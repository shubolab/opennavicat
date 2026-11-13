"""Real TLS/SSH transport for the finite stream's separate no-retry client.

These fresh owned TLS fixtures are standalone servers. The legal aggregate
qualifies transport and client lifetime; the actual $changeStream command is
expected to be rejected by that topology. Replica-set event semantics are
covered separately in test_mongodb_pipeline_stream.py.
"""
from __future__ import annotations

import contextlib
import os
import ssl

from pymongo.errors import OperationFailure
import pytest

from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage
from opennavicat.mongodb_pipeline_stream import StreamExecutionError, execute_finite_stream
from opennavicat.mongodb_session import MongoSession
from test_mongodb_security import (
    KEY_PASSWORD, _assert_pipeline_resources_closed, _docker, _observe_pipeline_clients,
    _report, _trace_pipeline_resource_order, client_config, endpoint_closed,
    isolated_tunnels, mongo_certificates as _certificate_fixture,
    mongo_ssh as _ssh_fixture, mtls_mongo as _mtls_fixture,
    ssh_config, tls_mongo as _tls_fixture, wait_for,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("OPENNAVICAT_TEST_MONGODB_SECURITY") != "1",
    reason="Opt in to fresh owned MongoDB stream TLS/SSH transports",
)


@pytest.fixture(scope="module")
def stream_certificates(tmp_path_factory):
    generator = _certificate_fixture.__wrapped__(tmp_path_factory)
    material = next(generator)
    try:
        yield material
    finally:
        with contextlib.suppress(StopIteration):
            next(generator)
        assert not material["directory"].exists()
        _report("stream-certificate-cleanup.json", {"private_certificate_directory_removed": True})


def _exact_fixture(generator, label):
    service = next(generator)
    info = service._owned()
    volumes = [mount["Name"] for mount in info["Mounts"] if mount["Type"] == "volume"]
    identity, name, token = service.ident, service.name, service.token
    evidence = {"container_id": identity, "container_name": name, "owner_token": token,
                "anonymous_volume_ids": volumes, "exact_container_and_volumes_absent": False}
    _report("stream-" + label + "-owned-cleanup.json", evidence)
    try:
        yield service
    finally:
        # The reused fixture performs its own exact-ID/name/token checked
        # deletion. Independently check all anonymous volumes afterwards.
        with contextlib.suppress(StopIteration):
            next(generator)
        for kind, value in [("container", identity), *[("volume", value) for value in volumes]]:
            response = _docker(kind, "inspect", value, check=False)
            assert response.returncode and "no such" in response.stderr.lower()
        # A desktop Docker forwarder can outlive the removed container briefly.
        wait_for(lambda: endpoint_closed(("127.0.0.1", service.port)), timeout=5)
        assert not service.environment.exists()
        evidence.update(exact_container_and_volumes_absent=True,
                        credential_environment_removed=True, loopback_endpoint_closed=True)
        _report("stream-" + label + "-owned-cleanup.json", evidence)


@pytest.fixture(scope="module")
def stream_tls_mongo(stream_certificates):
    yield from _exact_fixture(_tls_fixture.__wrapped__(stream_certificates), "tls")


@pytest.fixture(scope="module")
def stream_mtls_mongo(stream_certificates):
    yield from _exact_fixture(_mtls_fixture.__wrapped__(stream_certificates), "mtls")


@pytest.fixture
def stream_ssh(stream_tls_mongo, tmp_path):
    generator = _ssh_fixture.__wrapped__(stream_tls_mongo, tmp_path)
    server = next(generator)
    try:
        yield server
    finally:
        with contextlib.suppress(StopIteration):
            next(generator)
        assert endpoint_closed(("127.0.0.1", server.port))
        assert not server.private_key.exists() and not server.agent_key.exists()
        assert not server.thread.is_alive() and all(not thread.is_alive() for thread in server.threads)
        _report("stream-ssh-cleanup.json", {
            "loopback_listener_closed": True, "transports_closed": True,
            "worker_threads_stopped": True, "generated_private_keys_removed": True,
        })


def _exercise_no_retry_transport(session, created, commands):
    original_options = dict(session._connected_options)
    assert original_options["retryReads"] is True
    with session._finite_stream_budget(5000) as (client, effective):
        assert client is session._finite_stream_client
        assert client is not session.connection and client is not session._control
        assert effective == 5000 and client.options.retry_reads is False
        assert client.options.timeout is None
        assert client.options.pool_options.socket_timeout == 120
        assert 0 < client.options.pool_options.connect_timeout <= session.config.connect_timeout
        assert 0 < client.options.server_selection_timeout <= session.config.connect_timeout
        assert client.admin.command("ping")["ok"] == 1
        with client.security_fixture.samples.aggregate([
                {"$match": {"_id": 1}}], maxTimeMS=effective) as cursor:
            assert list(cursor) == [{"_id": 1, "verified": "real-tls"}]
    with session._finite_stream_budget(1000) as (again, effective):
        assert again is client and effective == 1000
        assert again.admin.command("ping")["ok"] == 1
    assert session._connected_options == original_options
    assert not session._pipeline_clients and len(created) == 3
    assert session.connection.options.retry_reads and session._control.options.retry_reads
    assert commands == [{"command": "aggregate", "max_time_ms": 5000}]

    # Exercise the real production dispatcher on this verified transport too.
    # A standalone rejection is useful wire evidence, not successful event
    # delivery. The failure must evict/close only the stream budget client.
    stream = PipelineDefinition(source="samples", stages=[PipelineStage("$changeStream", {})],
                                reference_options={"maxTimeMS": 5000})
    with pytest.raises(StreamExecutionError) as failure:
        execute_finite_stream(session, "security_fixture", stream, await_ms=100)
    assert isinstance(failure.value.__cause__, OperationFailure)
    assert failure.value.__cause__.code == 40573
    assert failure.value.partial_result.completion == "error"
    assert not failure.value.partial_result.events
    assert session._finite_stream_client is None
    assert session.connection.admin.command("ping")["ok"] == 1
    assert session._control.admin.command("ping")["ok"] == 1
    assert len(created) == 3
    assert [command["command"] for command in commands] == ["aggregate", "aggregate", "aggregate"]
    # Third aggregate is the exact-nonce cleanup metadata scan.
    assert all(type(command["max_time_ms"]) is int and 0 < command["max_time_ms"] <= 5000
               for command in commands[1:])


def test_stream_client_verify_full_over_ssh_keeps_original_hostname_and_owner_cleanup(
        stream_tls_mongo, stream_ssh, isolated_tunnels, monkeypatch):
    import opennavicat.mongodb_session as module

    holder, handshakes = {}, []
    created, closed, commands, events = _observe_pipeline_clients(monkeypatch, holder)
    original_init = module._TLSHostnameContext.__init__

    class ObservedContext:
        def __init__(self, context):
            self.context = context

        def __getattr__(self, name):
            return getattr(self.context, name)

        def wrap_socket(self, *args, **kwargs):
            handshakes.append((kwargs.get("server_hostname") == "db.test",
                self.context.check_hostname, self.context.verify_mode == ssl.CERT_REQUIRED))
            return self.context.wrap_socket(*args, **kwargs)

    def context(self, actual, hostname):
        original_init(self, actual, hostname)
        self._context = ObservedContext(actual)

    monkeypatch.setattr(module._TLSHostnameContext, "__init__", context)
    session = MongoSession(ssh_config(stream_tls_mongo, stream_ssh,
        ssl_cert=str(stream_tls_mongo.material["client"]),
        ssl_key=str(stream_tls_mongo.material["encrypted-client-key"]), ssl_key_password=KEY_PASSWORD))
    try:
        session.connect()
        _trace_pipeline_resource_order(monkeypatch, session, holder, created, closed, events)
        before = len(handshakes)
        tunnel, pem = session._tunnel, session._pem_dir
        _exercise_no_retry_transport(session, created, commands)
        assert len(handshakes) > before and all(all(value) for value in handshakes)
        assert session._tunnel is tunnel and session._pem_dir is pem
        assert stream_ssh.destinations and all(host == "db.test" and port == stream_tls_mongo.port
                                              for host, port in stream_ssh.destinations)
    finally:
        session.close()
    _assert_pipeline_resources_closed(session, holder, created, closed, events)
    assert session._finite_stream_client is None
    assert len(isolated_tunnels) == 1 and all(endpoint_closed(value) for value in isolated_tunnels)
    _report("stream-client-ssh.json", {
        "verify_full_original_hostname": True, "encrypted_client_certificate_used": True,
        "retry_reads": False, "finite_network_settings": True, "commands": commands,
        "real_standalone_stream_rejection": 40573, "replica_set_event_acceptance": False,
        "clients_closed_before_tunnel_and_pem": True, "stream_cache_cleared": True,
    })


def test_stream_client_direct_mutual_tls_keeps_certificate_and_owner_cleanup(
        stream_mtls_mongo, monkeypatch):
    holder = {}
    created, closed, commands, events = _observe_pipeline_clients(monkeypatch, holder)
    session = MongoSession(client_config(stream_mtls_mongo))
    try:
        session.connect()
        _trace_pipeline_resource_order(monkeypatch, session, holder, created, closed, events)
        pem = session._pem_dir
        _exercise_no_retry_transport(session, created, commands)
        assert session._pem_dir is pem and session._tunnel is None and holder["pem"].exists()
    finally:
        session.close()
    _assert_pipeline_resources_closed(session, holder, created, closed, events)
    assert session._finite_stream_client is None
    _report("stream-client-mtls.json", {
        "verify_full": True, "mutual_tls_required": True, "encrypted_client_key_used": True,
        "retry_reads": False, "finite_network_settings": True, "commands": commands,
        "real_standalone_stream_rejection": 40573, "replica_set_event_acceptance": False,
        "clients_closed_before_pem": True, "stream_cache_cleared": True,
    })
