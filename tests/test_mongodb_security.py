"""Opt-in real MongoDB TLS/SSH; only freshly owned loopback Docker peers.

Run scripts/test-mongodb-security.py for a checked JUnit + cleanup receipt.
No external endpoint, Navicat profile, system known_hosts or user key is read.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import platform
import tempfile
import time
import uuid

import paramiko
from pymongo import MongoClient
from pymongo.errors import InvalidOperation
from pymongo.monitoring import CommandListener
import pytest

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_session import MongoSession
from opennavicat.transport import SSHAuthenticationError, SSHHostKeyError, TLSKeyPasswordError
from test_connection_security_integration import (
    certificates as _base_certificates, OwnedSSHServer, isolated_tunnels,
    KEY_PASSWORD, SSH_PASSWORD, endpoint_closed, wait_for,
)

pytestmark = pytest.mark.skipif(os.environ.get("OPENNAVICAT_TEST_MONGODB_SECURITY") != "1",
    reason="Opt in to fresh owned MongoDB TLS/SSH with OPENNAVICAT_TEST_MONGODB_SECURITY=1")
IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
OWNER_LABEL = "opennavicat.mongodb-security-fixture"
REPORT = Path(os.environ.get("OPENNAVICAT_MONGODB_SECURITY_REPORT_DIR", "artifacts/mongodb-security"))


def _report(name, value):
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, indent=2) + "\n")


def _docker(*arguments, check=True, timeout=90):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("Owned MongoDB Docker operation failed; see redacted fixture evidence")
    return result


@pytest.fixture(scope="module")
def mongo_certificates(tmp_path_factory):
    # Reuse the existing generator directly so this fixture can verify its
    # finalizer removed the entire private CA/key directory before reporting.
    generator = _base_certificates.__wrapped__(tmp_path_factory)
    material = next(generator)
    directory = material["directory"]
    for prefix in ["server", "client"]:
        path = directory / (prefix + "-combined.pem")
        path.write_bytes(material[prefix + "-key"].read_bytes() + b"\n" + material[prefix].read_bytes())
        path.chmod(0o600)
        material[prefix + "-combined"] = path
    try:
        yield material
    finally:
        with contextlib.suppress(StopIteration):
            next(generator)
        assert not directory.exists(), "Private MongoDB certificate material remains"
        _report("certificate-cleanup.json", {"private_certificate_directory_removed": True})


class OwnedMongoSecurity:
    def __init__(self, material, mutual):
        self.material, self.mutual = material, mutual
        self.token = uuid.uuid4().hex
        self.name = "opennavicat-mongo-security-" + self.token[:14]
        self.ident = None
        self.port = None
        self.password = secrets.token_urlsafe(28)
        self.environment = material["directory"] / (self.token + ".env")
        self.evidence = dict(image=IMAGE, mutual_tls_required=mutual,
                             owner_token_verified=False, cleanup_removed=False)

    def _owned(self):
        data = json.loads(_docker("inspect", self.ident).stdout)[0]
        assert data["Id"] == self.ident and data["Name"] == "/" + self.name
        assert data["Config"]["Labels"].get(OWNER_LABEL) == self.token
        assert data["Config"]["Labels"].get("opennavicat.test") == "true"
        return data

    def start(self):
        daemon = _docker("info", "--format", "{{.OSType}}/{{.Architecture}}").stdout.strip()
        assert daemon.startswith("linux/"), "The fixture requires a Linux Docker daemon"
        kernel=_docker('info', '--format', '{{.KernelVersion}}').stdout.strip()
        self.evidence.update(docker_os_architecture=daemon, docker_kernel_version=kernel, client_os=platform.system(),
                             client_architecture=platform.machine())
        self.environment.write_text("OWN_MONGO_USER=fixture\nOWN_MONGO_PASSWORD=" + self.password + "\n")
        self.environment.chmod(0o600)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::add-mask::" + self.password, flush=True)
        arguments = ["run", "--detach", "--name", self.name,
            "--label", "opennavicat.test=true", "--label", OWNER_LABEL + "=" + self.token,
            "--publish", "127.0.0.1::27017", "--user", "0:0", "--entrypoint", "mongod",
            "--env-file", str(self.environment), "--mount",
            f"type=bind,source={self.material['directory']},target=/owned-security,readonly", IMAGE,
            "--bind_ip_all", "--auth", "--tlsMode", "requireTLS",
            "--tlsCertificateKeyFile", "/owned-security/server-combined.pem",
            "--tlsCAFile", "/owned-security/ca.pem", "--tlsCRLFile", "/owned-security/client-crl.pem"]
        if not self.mutual:
            arguments.append("--tlsAllowConnectionsWithoutCertificates")
        self.ident = _docker(*arguments).stdout.strip()
        info = self._owned()
        bindings = info["HostConfig"]["PortBindings"]["27017/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        live = info["NetworkSettings"]["Ports"]["27017/tcp"]
        assert len(live) == 1 and live[0]["HostIp"] == "127.0.0.1"
        self.port = int(live[0]["HostPort"])
        self.evidence.update(owner_token_verified=True, port_loopback_only=True,
            container_id=self.ident, container_name=self.name, image_id=info["Image"], require_tls_server=True)
        image_arch = _docker("image", "inspect", info["Image"], "--format", "{{.Architecture}}").stdout.strip()
        canonical = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
        assert canonical.get(daemon.split("/")[1]) == canonical.get(image_arch) and canonical.get(image_arch)
        self.evidence.update(image_architecture=image_arch, native_docker_architecture_matches=True)
        # Localhost exception is used only inside this new empty container.
        initialize = ("db.getSiblingDB('admin').createUser({user:process.env.OWN_MONGO_USER,"
            "pwd:process.env.OWN_MONGO_PASSWORD,roles:[{role:'root',db:'admin'}]}); print('owned-user-created')")
        deadline = time.monotonic() + 60
        while True:
            result = _docker("exec", self.ident, "mongosh", "--quiet", "--host", "localhost",
                "--tls", "--tlsCAFile", "/owned-security/ca.pem", "--tlsCertificateKeyFile",
                "/owned-security/client-combined.pem", "--eval", initialize, check=False, timeout=8)
            try:
                client = self.client()
                try:
                    self.version = client.server_info()["version"]
                    client.security_fixture.samples.insert_one({"_id": 1, "verified": "real-tls"})
                finally:
                    client.close()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Owned MongoDB failed real verified TLS/auth readiness") from None
                if self._owned()["State"]["Running"] is not True:
                    raise RuntimeError("Owned MongoDB stopped before verified TLS readiness") from None
                time.sleep(.2)
        self.evidence.update(server_version=self.version, external_tls_auth_readiness=True)
        return self

    def client(self):
        return MongoClient("localhost", self.port, username="fixture", password=self.password,
            authSource="admin", tls=True, tlsCAFile=str(self.material["ca"]),
            tlsCertificateKeyFile=str(self.material["client-combined"]),
            serverSelectionTimeoutMS=1500, connectTimeoutMS=1500)

    def config(self, **overrides):
        config = ConnectionConfig("owned-mongo-security", "Owned MongoDB security", driver="mongodb",
            host="localhost", port=self.port, username="fixture", password=self.password,
            database="security_fixture", mongo_auth_source="admin", ssl_mode="verify-full",
            ssl_ca=str(self.material["ca"]), connect_timeout=2)
        return replace(config, **overrides)

    def close(self):
        try:
            if self.ident:
                self._owned()  # Exact ID/name/token check before removal.
                logs = _docker("logs", "--tail", "60", self.ident, check=False, timeout=10)
                REPORT.mkdir(parents=True, exist_ok=True)
                label = "mtls" if self.mutual else "tls"
                (REPORT / (label + "-server.log")).write_text((logs.stdout + logs.stderr).replace(self.password, "<redacted>"))
                _docker("rm", "--force", "--volumes", self.ident, timeout=30)
                remaining = _docker("container", "ls", "--all", "--no-trunc", "--filter", "id=" + self.ident,
                    "--format", "{{.ID}}", timeout=15).stdout.strip()
                assert not remaining, "Owned MongoDB container remains"
                self.evidence["cleanup_removed"] = True
        finally:
            self.environment.unlink(missing_ok=True)
            self.evidence["credential_environment_removed"] = not self.environment.exists()
            _report(("mtls" if self.mutual else "tls") + "-fixture.json", self.evidence)


@pytest.fixture(scope="module")
def tls_mongo(mongo_certificates):
    service = OwnedMongoSecurity(mongo_certificates, False)
    try:
        yield service.start()
    finally:
        service.close()


@pytest.fixture(scope="module")
def mtls_mongo(mongo_certificates):
    service = OwnedMongoSecurity(mongo_certificates, True)
    try:
        yield service.start()
    finally:
        service.close()


@pytest.fixture(autouse=True)
def actual_resource_cleanup(monkeypatch):
    """Observe real resources without stubbing crypto, sockets or SSH traffic."""
    import opennavicat.mongodb_session as module
    proxy_class = getattr(module, "_SocksProxy", None)
    original_temp, original_session = tempfile.TemporaryDirectory, MongoSession.__init__
    original_proxy = proxy_class.__init__ if proxy_class is not None else None
    directories, proxies, sessions = [], [], []
    def directory(*args, **kwargs):
        result = original_temp(*args, **kwargs)
        if kwargs.get("prefix") == "opennavicat-mongo-tls-":
            directories.append(Path(result.name))
        return result
    def proxy(self, *args, **kwargs):
        original_proxy(self, *args, **kwargs)
        proxies.append(self)
    def session(self, *args, **kwargs):
        original_session(self, *args, **kwargs)
        sessions.append(self)
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", directory)
    if proxy_class is not None:
        monkeypatch.setattr(proxy_class, "__init__", proxy)
    monkeypatch.setattr(MongoSession, "__init__", session)
    try:
        yield
    finally:
        # Also drain clients after an interrupted or unexpectedly failing test;
        # normal-failure assertions still check adapter cleanup before this.
        for item in sessions:
            item.close()
        assert all(not path.exists() for path in directories)
        assert all(endpoint_closed(item.endpoint) and not item.thread.is_alive() for item in proxies)


def assert_query(session):
    page = session.fetch_documents("security_fixture", "samples", {"_id": 1})
    assert len(page.documents) == 1 and page.documents[0].document["verified"] == "real-tls"


def assert_drained(session):
    assert session.connection is session._control is None
    assert all(getattr(session, name, None) is None for name in ["_proxy", "_tunnel", "_pem_dir"])


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_real_tls_modes_and_query(tls_mongo, mode):
    session = MongoSession(tls_mongo.config(ssl_mode=mode, ssl_ca="" if mode == "require" else str(tls_mongo.material["ca"])))
    try:
        session.connect(); assert_query(session)
    finally:
        session.close()
    assert_drained(session)


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_wrong_ca_is_rejected(tls_mongo, mode):
    session = MongoSession(tls_mongo.config(ssl_mode=mode, ssl_ca=str(tls_mongo.material["wrong-ca"])))
    started = time.monotonic()
    with pytest.raises(DatabaseError) as error:
        session.connect()
    assert time.monotonic() - started < 5, "Query CSOT must not override the two-second connect deadline"
    assert not error.value.authentication_required
    assert_drained(session)


def test_untrusted_default_ca_and_plaintext_fail(tls_mongo):
    for changes in [dict(ssl_ca=""), dict(ssl_mode="disable", ssl_ca="")]:
        session = MongoSession(tls_mongo.config(**changes))
        with pytest.raises(DatabaseError) as error:
            session.connect()
        assert not error.value.authentication_required
        assert_drained(session)


@pytest.mark.parametrize("mode,accepted", [("verify-ca", True), ("verify-full", False)])
def test_ip_san_mismatch_is_distinct_from_ca_validation(tls_mongo, mode, accepted):
    # The real cert has DNS localhost/db.test but deliberately no IP SAN.
    session = MongoSession(tls_mongo.config(host="127.0.0.1", ssl_mode=mode))
    try:
        if accepted:
            session.connect(); assert_query(session)
        else:
            with pytest.raises(DatabaseError) as error:
                session.connect()
            assert not error.value.authentication_required
    finally:
        session.close()
    assert_drained(session)


def client_config(service, **changes):
    values = dict(ssl_cert=str(service.material["client"]), ssl_key=str(service.material["encrypted-client-key"]),
                  ssl_key_password=KEY_PASSWORD)
    values.update(changes)
    return service.config(**values)


def test_mutual_tls_encrypted_private_key_and_temp_cleanup(mtls_mongo):
    session = MongoSession(client_config(mtls_mongo))
    try:
        session.connect(); assert_query(session)
        temporary = Path(session._pem_dir.name)
        assert temporary.exists() and (temporary / "client.pem").stat().st_mode & 0o777 == 0o600
    finally:
        session.close()
    assert not temporary.exists(); assert_drained(session)


@pytest.mark.parametrize("revoked", [False, True])
def test_mutual_tls_rejects_missing_or_revoked_client(mtls_mongo, revoked):
    config = mtls_mongo.config() if not revoked else client_config(mtls_mongo,
        ssl_cert=str(mtls_mongo.material["revoked-client"]), ssl_key=str(mtls_mongo.material["revoked-client-key"]), ssl_key_password="")
    session = MongoSession(config)
    with pytest.raises(DatabaseError) as error:
        session.connect()
    assert not error.value.authentication_required
    assert_drained(session)


def test_client_crl_checks_actual_server_certificate(tls_mongo):
    good = MongoSession(tls_mongo.config(ssl_crl=str(tls_mongo.material["empty-crl"])))
    try:
        good.connect(); assert_query(good)
    finally:
        good.close()
    revoked = MongoSession(tls_mongo.config(ssl_crl=str(tls_mongo.material["server-crl"])))
    with pytest.raises(DatabaseError) as error:
        revoked.connect()
    assert not error.value.authentication_required
    assert_drained(revoked)


@pytest.mark.parametrize("password", ["", "wrong-fixture-key-password"])
def test_encrypted_client_key_failure_is_typed(mtls_mongo, password):
    session = MongoSession(client_config(mtls_mongo, ssl_key_password=password))
    with pytest.raises(TLSKeyPasswordError) as error:
        session.connect()
    assert error.value.needs_passphrase
    assert_drained(session)


@pytest.fixture
def mongo_ssh(tls_mongo, tmp_path):
    server = OwnedSSHServer(tls_mongo, tmp_path)
    try:
        yield server
    finally:
        server.close()
        assert endpoint_closed(("127.0.0.1", server.port))
        assert all(not transport.is_active() for transport in server.transports)


def ssh_config(service, server, **changes):
    values = dict(host="db.test", ssh_enabled=True, ssh_host="127.0.0.1", ssh_port=server.port,
        ssh_username="security_ssh", ssh_password=SSH_PASSWORD, ssh_auth_method="password", ssh_host_key=server.pin)
    values.update(changes)
    return service.config(**values)


@pytest.mark.parametrize("method", ["password", "key"])
def test_real_ssh_tls_original_hostname_and_authentication(tls_mongo, mongo_ssh, isolated_tunnels, method):
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh, ssh_auth_method=method,
        ssh_key_file=str(mongo_ssh.private_key), ssh_key_passphrase=KEY_PASSWORD, ssh_compression=True))
    try:
        session.connect(); assert_query(session)
        assert mongo_ssh.destinations and all(host == "db.test" and port == tls_mongo.port for host, port in mongo_ssh.destinations)
        assert ("password" if method == "password" else "publickey") in mongo_ssh.authentication
    finally:
        session.close()
    assert_drained(session)
    assert isolated_tunnels and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_unknown_pin_never_authenticates_then_approved_pin_connects(tls_mongo, mongo_ssh, isolated_tunnels):
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh, ssh_host_key=""))
    try:
        with pytest.raises(SSHHostKeyError) as error:
            session.connect()
        assert error.value.pin == mongo_ssh.pin and error.value.expected is None
        assert mongo_ssh.auth_attempts == 0
        assert_drained(session)
        session.config = replace(session.config, ssh_host_key=error.value.pin)
        session.connect(); assert_query(session)
    finally:
        session.close()
    assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_mismatched_pin_is_rejected_before_credentials(tls_mongo, mongo_ssh, isolated_tunnels):
    wrong = paramiko.RSAKey.generate(2048)
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh, ssh_host_key=wrong.get_name() + " " + wrong.get_base64()))
    with pytest.raises(SSHHostKeyError) as error:
        session.connect()
    assert error.value.expected and mongo_ssh.auth_attempts == 0 and not isolated_tunnels
    assert_drained(session)


def test_actual_peer_host_key_rotation_is_rejected_before_reauthentication(tls_mongo, mongo_ssh, isolated_tunnels):
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh))
    try:
        session.connect(); assert_query(session); session.close()
        authentications = mongo_ssh.auth_attempts
        mongo_ssh.host_key = paramiko.RSAKey.generate(2048)
        with pytest.raises(SSHHostKeyError) as error:
            session.connect()
        assert error.value.expected == session.config.ssh_host_key
        assert mongo_ssh.auth_attempts == authentications
        assert_drained(session)
    finally:
        session.close()
    assert len(isolated_tunnels) == 1 and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_wrong_ssh_password_and_refused_socket_cleanup(tls_mongo, mongo_ssh, isolated_tunnels):
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh, ssh_password="wrong-fixture-password"))
    with pytest.raises(SSHAuthenticationError):
        session.connect()
    assert_drained(session)
    wait_for(lambda: all(not transport.is_active() for transport in mongo_ssh.transports))
    mongo_ssh.close()
    refused = MongoSession(ssh_config(tls_mongo, mongo_ssh, connect_timeout=1))
    with pytest.raises(DatabaseError):
        refused.connect()
    assert_drained(refused)
    assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_ssh_preserves_wrong_hostname_and_cleans_tls_failure(tls_mongo, mongo_ssh, isolated_tunnels):
    session = MongoSession(ssh_config(tls_mongo, mongo_ssh, host="wrong.test"))
    with pytest.raises(DatabaseError) as error:
        session.connect()
    assert not error.value.authentication_required
    assert mongo_ssh.destinations and all(host == "wrong.test" for host, _ in mongo_ssh.destinations)
    assert_drained(session)
    assert isolated_tunnels and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_real_ssh_mutual_tls_and_reconnect(mtls_mongo, tmp_path, isolated_tunnels):
    server = OwnedSSHServer(mtls_mongo, tmp_path)
    session = MongoSession(replace(client_config(mtls_mongo), **{
        key: value for key, value in vars(ssh_config(mtls_mongo, server)).items()
        if key.startswith("ssh_") or key == "host"}))
    try:
        for _ in range(2):
            session.connect(); assert_query(session); session.close(); assert_drained(session)
        assert server.destinations and all(host == "db.test" for host, _ in server.destinations)
    finally:
        session.close(); server.close()
    assert len(isolated_tunnels) == 2 and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def _observe_pipeline_clients(monkeypatch, holder):
    """Observe real clients/commands without storing options or command bodies."""
    import opennavicat.mongodb_session as module

    created, closed, commands, events = [], set(), [], []
    original_client = module.MongoClient

    class Commands(CommandListener):
        def started(self, event):
            if event.command_name in {'aggregate', 'explain'}:
                commands.append({'command': event.command_name, 'max_time_ms': event.command.get('maxTimeMS')})

        def succeeded(self, _event):
            pass

        def failed(self, _event):
            pass

    listener = Commands()
    def client(*args, **kwargs):
        options = dict(kwargs)
        options['event_listeners'] = [*options.get('event_listeners', []), listener]
        value = original_client(*args, **options)
        original_close = value.close
        def close():
            pem = holder.get('pem')
            tunnel = holder.get('tunnel')
            resources_live = (pem is None or pem.exists()) and (tunnel is None or tunnel.active)
            try:
                original_close()
            finally:
                closed.add(id(value))
                events.append(('client_closed', id(value), resources_live))
        monkeypatch.setattr(value, 'close', close)
        created.append(value)
        return value
    monkeypatch.setattr(module, 'MongoClient', client)
    return created, closed, commands, events


def _trace_pipeline_resource_order(monkeypatch, session, holder, created, closed, events):
    holder['pem'] = Path(session._pem_dir.name)
    holder['tunnel'] = tunnel = session._tunnel
    tunnel_endpoint = tunnel.endpoint if tunnel is not None else None
    temporary = session._pem_dir
    original_cleanup = temporary.cleanup
    def cleanup():
        events.append(('pem_cleanup', all(id(value) in closed for value in created),
                       tunnel_endpoint is None or endpoint_closed(tunnel_endpoint)))
        original_cleanup()
    monkeypatch.setattr(temporary, 'cleanup', cleanup)
    if tunnel is not None:
        original_close = tunnel.close
        def close_tunnel():
            events.append(('tunnel_close', all(id(value) in closed for value in created)))
            original_close()
        monkeypatch.setattr(tunnel, 'close', close_tunnel)


def _exercise_verified_pipeline_budgets(session, created, commands):
    from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, execute_pipeline, explain_pipeline
    from opennavicat.mongodb_session import canonical_loads

    definition = PipelineDefinition(source='samples', stages=[PipelineStage('$match', {'_id': 1})],
        reference_options={'maxTimeMS': 5000})
    rows, truncated = execute_pipeline(session, 'security_fixture', definition)
    assert not truncated and len(rows) == 1 and canonical_loads(rows[0])['verified'] == 'real-tls'
    explained = explain_pipeline(session, 'security_fixture', definition)
    assert explained.raw.get('ok') == 1
    budget_clients = tuple(session._pipeline_clients.values())
    assert len(budget_clients) == 2 and all(value is not session.connection and value is not session._control
                                           for value in budget_clients)
    assert all(value.options.timeout is None and value.options.pool_options.socket_timeout in (30, 120)
               for value in budget_clients)
    assert len(created) == 4  # Main/control plus both real, independently connected budget clients.
    assert {value['command'] for value in commands} == {'aggregate', 'explain'}
    assert all(type(value['max_time_ms']) is int and 0 < value['max_time_ms'] <= 5000 for value in commands)


def _assert_pipeline_resources_closed(session, holder, created, closed, events):
    assert closed == {id(value) for value in created}
    assert all(event[2] for event in events if event[0] == 'client_closed')
    assert all(event[1] for event in events if event[0] == 'tunnel_close')
    assert all(event[1] and event[2] for event in events if event[0] == 'pem_cleanup')
    assert [event[0] for event in events][-1] == 'pem_cleanup'
    assert not holder['pem'].exists()
    cache_empty = not session._pipeline_clients
    options_cleared = session._connected_options is None
    assert cache_empty and options_cleared
    assert_drained(session)
    for client in created:
        # Public operation after close cannot silently create a replacement pool.
        with pytest.raises(InvalidOperation):
            client.admin.command('ping')


def test_pipeline_budget_verify_full_over_ssh_keeps_hostname_and_resource_order(
        tls_mongo, mongo_ssh, isolated_tunnels, monkeypatch):
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
            # The cert has DNS db.test/localhost and no IP SAN. This delegates
            # to real crypto while witnessing the identity actually verified.
            handshakes.append((kwargs.get('server_hostname') == 'db.test',
                self.context.check_hostname, self.context.verify_mode == ssl.CERT_REQUIRED))
            return self.context.wrap_socket(*args, **kwargs)

    def context(self, actual, hostname):
        original_init(self, actual, hostname)
        self._context = ObservedContext(actual)
    monkeypatch.setattr(module._TLSHostnameContext, '__init__', context)
    session = holder['session'] = MongoSession(ssh_config(tls_mongo, mongo_ssh,
        ssl_cert=str(tls_mongo.material['client']), ssl_key=str(tls_mongo.material['encrypted-client-key']),
        ssl_key_password=KEY_PASSWORD))
    try:
        session.connect()
        _trace_pipeline_resource_order(monkeypatch, session, holder, created, closed, events)
        before = len(handshakes)
        original_tunnel, original_pem = session._tunnel, session._pem_dir
        _exercise_verified_pipeline_budgets(session, created, commands)
        assert len(handshakes) > before and all(all(value) for value in handshakes)
        assert session._tunnel is original_tunnel and session._pem_dir is original_pem
        assert mongo_ssh.destinations and all(host == 'db.test' and port == tls_mongo.port
                                            for host, port in mongo_ssh.destinations)
    finally:
        session.close()
    _assert_pipeline_resources_closed(session, holder, created, closed, events)
    assert len(isolated_tunnels) == 1 and all(endpoint_closed(value) for value in isolated_tunnels)
    _report('pipeline-budget-ssh.json', dict(verified=True, original_hostname_verified=True,
        verify_full=True, client_certificate_used=True, commands=commands, budget_clients=2,
        clients_closed=True, clients_closed_before_tunnel_and_pem=True))


def test_pipeline_budget_direct_mutual_tls_keeps_client_certificate_and_resource_order(mtls_mongo, monkeypatch):
    holder = {}
    created, closed, commands, events = _observe_pipeline_clients(monkeypatch, holder)
    session = holder['session'] = MongoSession(client_config(mtls_mongo))
    try:
        session.connect()
        _trace_pipeline_resource_order(monkeypatch, session, holder, created, closed, events)
        original_pem = session._pem_dir
        _exercise_verified_pipeline_budgets(session, created, commands)
        assert session._pem_dir is original_pem and session._tunnel is None
        assert holder['pem'].exists()
    finally:
        session.close()
    _assert_pipeline_resources_closed(session, holder, created, closed, events)
    _report('pipeline-budget-mtls.json', dict(verified=True, mutual_tls_required=True,
        verify_full=True, encrypted_client_key_used=True, commands=commands, budget_clients=2,
        clients_closed=True, clients_closed_before_pem=True))
