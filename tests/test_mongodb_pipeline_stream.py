"""Finite change streams: bounded fake contracts and exact-owned replica sets."""
from collections import deque
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid

from bson import BSON, Binary, Decimal128, Int64, ObjectId, Timestamp
from bson.raw_bson import RawBSONDocument
from pymongo import MongoClient, monitoring, timeout
from pymongo.errors import NetworkTimeout, OperationFailure
import pytest

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, execute_pipeline
from opennavicat.mongodb_pipeline_stream import execute_finite_stream, StreamExecutionError
from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC, MongoSession, canonical_loads


IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"


def raw(document):
    return RawBSONDocument(BSON.encode(document, codec_options=BSON_CODEC), codec_options=RAW_CODEC)


class Cursor:
    def __init__(self, cached=(), batches=((),), *, on_more=None):
        self.cached, self.batches = deque(cached), deque(batches)
        self.on_more, self.getmores, self.closed = on_more, 0, False
        self.cursor_id = 91

    def batch_size(self, value):
        self.batch = value

    def try_next(self):
        if not self.cached:
            self.getmores += 1
            if self.on_more:
                self.on_more()
            if self.batches:
                self.cached.extend(self.batches.popleft())
        return self.cached.popleft() if self.cached else None

    def close(self):
        self.closed, self.cursor_id = True, 0


class MetadataCursor(list):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


def fake_session(cursor, *, read_only=True):
    commands, kills = [], []

    class Control:
        admin = property(lambda self: self)

        def __getitem__(self, _name):
            return self

        def aggregate(self, *_args, **_kwargs):
            return MetadataCursor()

        def command(self, command):
            kills.extend(command.get("cursors", []))
            return {"cursorsKilled": command.get("cursors", [])}

    class Endpoint:
        def get_database(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _name):
            return self

        def aggregate(self, pipeline, **options):
            commands.append((pipeline, options))
            return cursor

    @contextmanager
    def budget(value, **_kwargs):
        yield endpoint, value

    endpoint = Endpoint()
    session = SimpleNamespace(config=SimpleNamespace(read_only=read_only),
        _control=Control(), _operation=lambda *_args: nullcontext("owned-marker"),
        _validate_namespace=lambda *_args: None, _finite_stream_budget=budget,
        _cancel_requested=threading.Event())
    return session, commands, kills


def definition(**options):
    return PipelineDefinition(source="sales", stages=[PipelineStage("$changeStream", {})],
        reference_options=options)


def test_one_empty_batch_returns_and_closes_without_polling_again():
    cursor = Cursor()
    session, commands, kills = fake_session(cursor)
    result = execute_finite_stream(session, "generated", definition())
    assert result.completion == "empty_batch" and not result.events
    assert cursor.getmores == 1 and cursor.closed and kills == [91]
    assert commands[0][0] == [{"$changeStream": {}}]
    assert commands[0][1]["maxAwaitTimeMS"] == 1000


def test_cached_and_nonempty_batches_are_delivered_before_first_empty_batch():
    values = [raw({"_id": {"token": index}, "value": Int64(index)}) for index in range(5)]
    cursor = Cursor(values[:2], [values[2:4], values[4:], [], [raw({"later": True})]])
    session, _commands, _kills = fake_session(cursor)
    result = execute_finite_stream(session, "generated", definition())
    assert [event.raw_bson for event in result.events] == [event.raw for event in values]
    assert cursor.getmores == 3 and result.completion == "empty_batch" and cursor.closed


def test_row_bound_stops_without_extra_getmore_and_keeps_exact_bson():
    value = {"_id": {"token": Binary(b"\x00\xff", 128)}, "n": Int64(9007199254740993),
             "d": Decimal128("1.250"), "at": Timestamp(9, 7), "id": ObjectId()}
    cursor = Cursor([raw(value), raw(value)])
    session, _commands, _kills = fake_session(cursor)
    result = execute_finite_stream(session, "generated", definition(), limit=1)
    assert result.completion == "row_limit" and result.truncated and cursor.getmores == 0
    assert BSON.encode(canonical_loads(result.rows[0]), codec_options=BSON_CODEC) == result.events[0].raw_bson


def test_cancel_after_empty_getmore_is_error_and_owner_closes():
    cancelled = threading.Event()
    cursor = Cursor(on_more=cancelled.set)
    session, _commands, _kills = fake_session(cursor)
    with pytest.raises(StreamExecutionError) as error:
        execute_finite_stream(session, "generated", definition(), cancel_event=cancelled)
    assert error.value.partial_result.completion == "cancelled" and cursor.closed


def test_read_error_never_reopens_or_resumes_cursor():
    def fail():
        raise RuntimeError("generated transport failure")

    cursor = Cursor(on_more=fail)
    session, commands, _kills = fake_session(cursor)
    with pytest.raises(StreamExecutionError, match="Finite stream execution failed") as error:
        execute_finite_stream(session, "generated", definition())
    assert len(commands) == 1 and cursor.getmores == 1 and cursor.closed
    assert str(error.value.__cause__) == "generated transport failure"


def test_first_oversized_event_is_explicit_error_and_closed():
    cursor = Cursor([raw({"value": "x" * 1000})])
    session, _commands, _kills = fake_session(cursor)
    with pytest.raises(StreamExecutionError, match="exceeds the bounded"):
        execute_finite_stream(session, "generated", definition(), max_bytes=100)
    assert cursor.closed


def test_result_byte_bound_explicitly_counts_the_obtained_undelivered_event():
    first, second = raw({"value": "a" * 100}), raw({"value": "b" * 100})
    cursor = Cursor([first, second])
    session, _commands, _kills = fake_session(cursor)
    with pytest.raises(StreamExecutionError, match="one obtained event was not delivered") as failure:
        execute_finite_stream(session, "generated", definition(), max_bytes=200)
    result = failure.value.partial_result
    assert result.completion == "byte_limit" and len(result.events) == 1
    assert result.undelivered_event_count == 1 and result.undelivered_raw_bytes == len(second.raw)
    assert result.undelivered_rendered_bytes > 0 and cursor.closed


def test_driver_secrets_are_not_copied_to_the_ui_error():
    private = "mongodb://generated-owner:generated-secret@owned.invalid/private"

    def fail():
        raise RuntimeError(private)

    cursor = Cursor(on_more=fail)
    session, _commands, _kills = fake_session(cursor)
    with pytest.raises(StreamExecutionError) as error:
        execute_finite_stream(session, "generated", definition())
    assert "generated-secret" not in str(error.value) and "mongodb://" not in str(error.value)
    assert str(error.value.__cause__) == private


def test_auth_failure_keeps_safe_classification_and_cause():
    def fail():
        raise OperationFailure("mongodb://generated:private@owned.invalid", 18)

    session, _commands, _kills = fake_session(Cursor(on_more=fail))
    with pytest.raises(StreamExecutionError) as error:
        execute_finite_stream(session, "generated", definition())
    assert error.value.authentication_required and "server code 18" in str(error.value)
    assert "private" not in str(error.value) and error.value.partial_result.completion == "error"


def test_cleanup_failure_is_not_a_success_and_discards_only_stream_transport():
    cursor = Cursor()
    session, commands, _kills = fake_session(cursor)
    discarded = []

    def fail_metadata(*_args, **_kwargs):
        raise RuntimeError("generated private diagnostic")

    session._control.aggregate = fail_metadata
    session._discard_finite_stream_client = discarded.append
    with pytest.raises(StreamExecutionError, match="cleanup could not be confirmed") as error:
        execute_finite_stream(session, "generated", definition())
    assert error.value.partial_result.completion == "cleanup_failed" and discarded and commands
    assert cursor.closed and "private" not in str(error.value)


def test_stream_transport_is_separate_no_retry_cached_and_closed_with_session(monkeypatch):
    from test_mongodb_session import _pipeline_budget_clients

    _module, session, clients, _events, options, _calls = _pipeline_budget_clients(monkeypatch, connect=False)
    options["retryReads"] = True
    session.connect()
    with session._pipeline_budget(5000) as (ordinary, _):
        assert ordinary.options["retryReads"]
    with session._finite_stream_budget(5000) as (stream, _):
        assert not stream.options["retryReads"] and stream.options["timeoutMS"] is None
        assert stream.options["socketTimeoutMS"] == 120000
    with session._finite_stream_budget(1) as (cached, effective):
        assert cached is stream and effective == 1 and stream.ping_count == 1
    assert session.connection.options["retryReads"] and len(clients) == 4
    with pytest.raises(RuntimeError, match="generated read failure"):
        with session._finite_stream_budget(5000):
            raise RuntimeError("generated read failure")
    assert stream.close_count == 1 and ordinary.close_count == 0
    with session._finite_stream_budget(5000) as (replacement, _):
        assert replacement is not stream
    session.close()
    assert session._finite_stream_client is None
    assert all(client.close_count == 1 for client in clients)


def test_stream_cold_auth_failure_closes_before_yield_and_can_retry(monkeypatch):
    from test_mongodb_session import _pipeline_budget_clients

    _module, session, clients, _events, _options, _calls = _pipeline_budget_clients(monkeypatch, fail_ping_at=2)
    try:
        with pytest.raises(RuntimeError, match="generated connection failure"):
            with session._finite_stream_budget(5000):
                pytest.fail("Cold authentication failed")
        assert clients[-1].close_count == 1 and session._finite_stream_client is None
        with session._finite_stream_budget(5000) as (retried, _):
            assert retried is clients[-1] and retried.close_count == 0
    finally:
        session.close()


def _docker(*args, check=True, env=None):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90, env=env)
    if check and result.returncode:
        raise RuntimeError("Owned stream Docker operation failed: " + args[0])
    return result


@pytest.fixture(scope="module")
def replica_service():
    if os.environ.get("OPENNAVICAT_TEST_MONGODB") != "1":
        pytest.skip("Enable new owned MongoDB services with OPENNAVICAT_TEST_MONGODB=1")
    if not shutil.which("docker"):
        pytest.skip("Owned replica-set tests require Docker")
    owner = uuid.uuid4().hex
    run_id = os.environ.get("OPENNAVICAT_MONGODB_STREAM_RUN_ID", "")
    name, replica = "opennavicat-stream-" + owner[:12], "generated_stream_rs"
    password, key = secrets.token_urlsafe(24), secrets.token_hex(48)
    evidence = {"owner": owner, "run_id": run_id, "name": name, "image": IMAGE, "cleanup_removed": False}
    directory = os.environ.get("OPENNAVICAT_MONGODB_STREAM_REPORT_DIR")
    receipt = Path(directory) / ("fixture-" + owner + ".json") if directory else None
    container, bootstrap, volumes = None, None, []

    def save():
        if receipt:
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(evidence, indent=2) + "\n")

    def owned():
        info = json.loads(_docker("inspect", container).stdout)[0]
        assert info["Id"] == container and info["Name"] == "/" + name
        labels = info["Config"]["Labels"]
        assert labels["opennavicat.owner"] == owner and labels["opennavicat.test"] == "true"
        assert labels["opennavicat.task"] == "finite-stream-tests"
        assert labels["opennavicat.stream-run"] == run_id
        bindings = info["HostConfig"]["PortBindings"]["27017/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        if info["State"]["Running"]:
            mapping = info["NetworkSettings"]["Ports"]["27017/tcp"]
            assert len(mapping) == 1 and mapping[0]["HostIp"] == "127.0.0.1"
        return info

    try:
        save()
        daemon = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        assert daemon["OSType"] == "linux"
        evidence["docker_architecture"] = daemon["Architecture"]
        env = dict(os.environ, MONGO_INITDB_ROOT_USERNAME="generated_owner",
                   MONGO_INITDB_ROOT_PASSWORD=password, OWNED_REPLICA_KEY=key)
        script = ('umask 077; printf "%s" "$OWNED_REPLICA_KEY" > /data/owned-repl.key; '
                  'chown mongodb:mongodb /data/owned-repl.key; '
                  'exec /usr/local/bin/docker-entrypoint.sh mongod --bind_ip_all '
                  '--replSet generated_stream_rs --keyFile /data/owned-repl.key '
                  '--setParameter enableTestCommands=1')
        container = _docker("run", "--detach", "--name", name, "--label", "opennavicat.test=true",
            "--label", "opennavicat.owner=" + owner, "--label", "opennavicat.task=finite-stream-tests",
            "--label", "opennavicat.stream-run=" + run_id,
            "--publish", "127.0.0.1::27017", "-e", "MONGO_INITDB_ROOT_USERNAME",
            "-e", "MONGO_INITDB_ROOT_PASSWORD", "-e", "OWNED_REPLICA_KEY",
            "--entrypoint", "/bin/bash", IMAGE, "-c", script, env=env).stdout.strip()
        evidence["container_id"] = container
        save()
        info = owned()
        volumes = [mount["Name"] for mount in info["Mounts"] if mount["Type"] == "volume"]
        port = int(info["NetworkSettings"]["Ports"]["27017/tcp"][0]["HostPort"])
        evidence.update(container_id=container, volumes=volumes, loopback_port=port)
        save()
        bootstrap = MongoClient("127.0.0.1", port, username="generated_owner", password=password,
            authSource="admin", directConnection=True, serverSelectionTimeoutMS=500, socketTimeoutMS=1000)
        deadline = time.monotonic() + 45
        initiated = False
        while time.monotonic() < deadline:
            owned()
            try:
                bootstrap.admin.command("ping")
                if not initiated:
                    bootstrap.admin.command({"replSetInitiate": {"_id": replica,
                        "members": [{"_id": 0, "host": "localhost:27017"}]}})
                    initiated = True
                if bootstrap.admin.command("hello").get("isWritablePrimary"):
                    break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError("Owned authenticated replica set did not become primary")
        evidence.update(server_version=bootstrap.server_info()["version"],
                        native_image_architecture=_docker("image", "inspect", info["Image"],
                            "--format", "{{.Architecture}}").stdout.strip())
        canonical = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
        assert evidence["server_version"] == "8.0.34"
        assert canonical.get(evidence["native_image_architecture"]) == canonical.get(daemon["Architecture"])
        assert canonical.get(daemon["Architecture"])
        evidence["native_architecture_matches"] = True
        save()
        yield ConnectionConfig("generated-stream", "Generated owned stream", driver="mongodb",
            host="127.0.0.1", port=port, database="generated", username="generated_owner",
            password=password, ssl_mode="disable", connect_timeout=3)
    finally:
        if bootstrap is not None:
            bootstrap.close()
        if container:
            info = owned()
            if not volumes:
                volumes = [m["Name"] for m in info["Mounts"] if m["Type"] == "volume"]
            assert sorted(volumes) == sorted(m["Name"] for m in info["Mounts"] if m["Type"] == "volume")
            for volume in volumes:
                assert _docker("ps", "-a", "--no-trunc", "--filter", "volume=" + volume,
                               "--format", "{{.ID}}").stdout.splitlines() == [container]
            _docker("rm", "-f", container)
            for volume in volumes:
                assert not _docker("ps", "-a", "--filter", "volume=" + volume,
                                   "--format", "{{.ID}}").stdout.strip()
                _docker("volume", "rm", volume)
            absent = _docker("inspect", container, check=False)
            assert absent.returncode and "no such object" in absent.stderr.lower()
            for volume in volumes:
                absent = _docker("volume", "inspect", volume, check=False)
                assert absent.returncode and "no such volume" in absent.stderr.lower()
            evidence.update(cleanup_removed=True, exact_container_and_volumes_absent=True)
        save()


class Wire(monitoring.CommandListener):
    def __init__(self, database):
        self.database, self.commands, self.getmore = database, [], threading.Event()

    def started(self, event):
        if event.database_name == self.database and event.command_name in {"aggregate", "getMore", "killCursors"}:
            self.commands.append(dict(event.command))
            if event.command_name == "getMore":
                self.getmore.set()

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@pytest.fixture
def stream_session(replica_service, monkeypatch):
    config = replace(replica_service, database="stream_" + uuid.uuid4().hex, read_only=True)
    session, wire = MongoSession(config), Wire(config.database)
    original = session._connection_options
    monkeypatch.setattr(session, "_connection_options", lambda: {**original(), "event_listeners": [wire]})
    try:
        session.connect()
        session.connection[config.database].sales.insert_one({"_id": 0, "generated": True})
        session.connection[config.database].command("profile", 2)
        yield session, wire
    finally:
        try:
            if session.connection is not None:
                session.connection.drop_database(config.database)
        finally:
            session.close()


def assert_no_owned_cursors(session):
    with session._control.admin.aggregate([
        {"$currentOp": {"allUsers": False, "idleCursors": True}},
        {"$match": {"$or": [{"ns": session.config.database + ".sales"},
                              {"command.$db": session.config.database}]}}, {"$limit": 16},
    ]) as cursor:
        assert not tuple(cursor)


def test_real_no_events_is_finite_one_empty_getmore_and_no_cursor_leak(stream_session):
    session, wire = stream_session
    started = time.monotonic()
    result = execute_finite_stream(session, session.config.database, definition(), await_ms=100)
    assert result.completion == "empty_batch" and not result.events and time.monotonic() - started < 3
    getmores = [command for command in wire.commands if "getMore" in command]
    assert len(getmores) == 1 and getmores[0]["maxTimeMS"] == 100
    assert_no_owned_cursors(session)


def test_real_two_events_arriving_during_run_are_preserved_then_finite(stream_session):
    session, wire = stream_session
    values = [{"_id": 1, "n": Int64(9007199254740993), "d": Decimal128("1.25"),
               "b": Binary(b"\x00\xff", 128), "at": Timestamp(9, 3)}, {"_id": 2, "generated": True}]

    def produce():
        assert wire.getmore.wait(3)
        session._control[session.config.database].sales.insert_many(values)

    worker = threading.Thread(target=produce)
    worker.start()
    try:
        result = execute_finite_stream(session, session.config.database, definition(), await_ms=1000)
    finally:
        worker.join(4)
        assert not worker.is_alive()
    events = [canonical_loads(row) for row in result.rows]
    assert [event["fullDocument"] for event in events] == values
    assert result.completion == "empty_batch"
    assert all(BSON.encode(canonical_loads(event.canonical_json), codec_options=BSON_CODEC) == event.raw_bson
               for event in result.events)
    assert_no_owned_cursors(session)


@pytest.mark.parametrize("requested", [1, 10, 5000])
def test_real_initial_server_limit_and_getmore_await_are_separate(stream_session, requested):
    session, wire = stream_session
    marker = "generated-stream-budget-" + uuid.uuid4().hex
    failed_initial = False
    try:
        execute_finite_stream(session, session.config.database,
            definition(maxTimeMS=requested, comment=marker), await_ms=50)
    except StreamExecutionError as error:
        assert requested in (1, 10)
        assert isinstance(error.__cause__, OperationFailure) and error.__cause__.code == 50
        failed_initial = True
    aggregates = [command for command in wire.commands if command.get("aggregate") == "sales"]
    assert len(aggregates) == 1 and 0 < aggregates[0]["maxTimeMS"] <= requested
    getmores = [command for command in wire.commands if "getMore" in command]
    assert all(command["maxTimeMS"] == 50 for command in getmores)
    if not failed_initial:
        assert len(getmores) == 1
    # Stream execution reads the oplog; its original aggregate is retained on
    # the owned database's getMore profiler entry as originatingCommand.
    profiles = tuple(session._control[session.config.database]["system.profile"].find({
        "originatingCommand.comment.userComment": marker, "command.getMore": {"$exists": True}}).limit(2))
    if failed_initial:
        assert not profiles
    else:
        assert len(profiles) == 1
        assert 0 < profiles[0]["originatingCommand"]["maxTimeMS"] <= requested
        assert profiles[0]["command"]["maxTimeMS"] == 50
    assert session._finite_stream_client is None or not session._finite_stream_client.options.retry_reads
    assert session.connection.options.retry_reads
    assert_no_owned_cursors(session)


def test_real_initial_batch_zero_then_available_events_are_drained(stream_session):
    session, wire = stream_session
    result = execute_finite_stream(session, session.config.database,
        definition(cursorBatchSize=0), await_ms=50)
    aggregates = [command for command in wire.commands if command.get("aggregate") == "sales"]
    assert aggregates[0]["cursor"]["batchSize"] == 0
    getmores = [command for command in wire.commands if "getMore" in command]
    assert len(getmores) == 1 and 0 < getmores[0]["batchSize"] <= 100
    assert result.completion == "empty_batch"
    assert_no_owned_cursors(session)


def test_real_manual_resume_update_lookup_and_fresh_run_do_not_share_tokens(stream_session):
    session, _wire = stream_session
    collection = session._control[session.config.database].sales
    with collection.watch(max_await_time_ms=100) as probe:
        collection.insert_one({"_id": 1, "amount": Int64(10)})
        first = probe.try_next()
        assert first is not None
        token = first["_id"]
    collection.insert_one({"_id": 2, "amount": Int64(20)})
    collection.update_one({"_id": 2}, {"$set": {"amount": Int64(30)}})
    draft = definition()
    draft.stages[0].body = {"resumeAfter": token, "fullDocument": "updateLookup"}
    draft.stages.append(PipelineStage("$addFields", {"generatedTag": "owned"}))
    result = execute_finite_stream(session, session.config.database, draft, await_ms=50)
    events = [canonical_loads(row) for row in result.rows]
    assert [item["operationType"] for item in events] == ["insert", "update"]
    assert [item["fullDocument"]["amount"] for item in events] == [20, 30]
    assert all(item["generatedTag"] == "owned" for item in events)
    fresh = execute_finite_stream(session, session.config.database, definition(), await_ms=50)
    assert not fresh.events
    repeated = execute_finite_stream(session, session.config.database, draft, await_ms=50)
    assert [item.raw_bson for item in repeated.events] == [item.raw_bson for item in result.events]
    assert_no_owned_cursors(session)


def test_real_stop_during_getmore_closes_only_its_cursor_and_session_recovers(stream_session):
    session, wire = stream_session
    foreign = session._control[session.config.database].sales.aggregate(
        [{"$changeStream": {}}], maxAwaitTimeMS=50, comment="generated-foreign-cursor")
    foreign_id = foreign.cursor_id
    errors, cancel = [], threading.Event()

    def run():
        try:
            execute_finite_stream(session, session.config.database, definition(), cancel_event=cancel)
        except StreamExecutionError as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert wire.getmore.wait(3)
        marker = session.cancellation_marker()
        assert marker is not None
        cancel.set()
        session.cancel(expected_comment=marker)
        worker.join(4)
        assert not worker.is_alive() and errors and errors[0].partial_result.completion == "cancelled"
        assert foreign.cursor_id == foreign_id and foreign.alive
        assert foreign.try_next() is None
    finally:
        foreign.close()
        worker.join(4)
    again = execute_pipeline(session, session.config.database, definition())
    assert not again.events and again.completion == "empty_batch"
    assert_no_owned_cursors(session)


def test_real_network_deadline_cleans_cursor_even_after_driver_marks_it_killed(stream_session):
    session, wire = stream_session
    with pytest.raises(StreamExecutionError) as failure:
        execute_finite_stream(session, session.config.database, definition(),
            await_ms=1000, timeout_seconds=.05)
    assert failure.value.partial_result.completion == "deadline"
    assert [command for command in wire.commands if "getMore" in command]
    assert_no_owned_cursors(session)


def test_real_initial_ambiguous_network_error_has_one_wire_attempt_and_no_reopen(stream_session):
    session, wire = stream_session
    session._control.admin.command({"configureFailPoint": "failCommand", "mode": {"times": 1},
        "data": {"failCommands": ["aggregate"], "closeConnection": True, "appName": "OpenNavicat"}})
    try:
        with pytest.raises(StreamExecutionError):
            execute_finite_stream(session, session.config.database, definition(), await_ms=50)
    finally:
        session._control.admin.command({"configureFailPoint": "failCommand", "mode": "off"})
    assert len([command for command in wire.commands if command.get("aggregate") == "sales"]) == 1
    assert_no_owned_cursors(session)


def test_real_lost_initial_cursor_response_is_cleaned_by_nonce_without_known_id(stream_session, monkeypatch):
    session, wire = stream_session
    lost = []
    with session._finite_stream_budget(5000) as (client, _):
        original_database = client.get_database

    class CollectionResponseGate:
        def __init__(self, collection):
            self.collection = collection

        def aggregate(self, *args, **kwargs):
            cursor = self.collection.aggregate(*args, **kwargs)
            assert cursor.cursor_id
            lost.append(cursor)
            # The real server has allocated a cursor, but the execution helper
            # receives no cursor object or ID, as with a lost initial response.
            raise NetworkTimeout("generated lost cursor response")

    class DatabaseResponseGate:
        def __init__(self, database):
            self.database = database

        def __getitem__(self, name):
            return CollectionResponseGate(self.database[name])

    def database_response_gate(name=None, *args, **kwargs):
        database = original_database(name, *args, **kwargs)
        return DatabaseResponseGate(database) if name == session.config.database else database

    # Scope fault injection to this exact owned client/public API, never a
    # global PyMongo method or a private cursor field.
    monkeypatch.setattr(client, "get_database", database_response_gate)
    try:
        with pytest.raises(StreamExecutionError):
            execute_finite_stream(session, session.config.database, definition(), await_ms=50)
        assert len(lost) == 1
        assert len([command for command in wire.commands if command.get("aggregate") == "sales"]) == 1
        assert_no_owned_cursors(session)
    finally:
        for cursor in lost:
            cursor.close()


def test_real_server_rejects_limit_without_appending_own_limit(stream_session):
    session, wire = stream_session
    draft = definition()
    draft.stages.append(PipelineStage("$limit", 1))
    with pytest.raises(StreamExecutionError) as failure:
        execute_finite_stream(session, session.config.database, draft, await_ms=50)
    assert isinstance(failure.value.__cause__, OperationFailure)
    aggregate = next(command for command in wire.commands if command.get("aggregate") == "sales")
    assert aggregate["pipeline"] == [{"$changeStream": {}}, {"$limit": 1}]
    assert_no_owned_cursors(session)


def test_real_row_and_byte_bounds_are_explicit_and_close_the_cursor(stream_session):
    session, _wire = stream_session
    collection = session._control[session.config.database].sales
    with collection.watch(max_await_time_ms=100) as probe:
        collection.insert_one({"_id": 1, "generated": True})
        token = probe.try_next()["_id"]
    collection.insert_many([{"_id": 2, "payload": "a" * 100}, {"_id": 3, "payload": "b" * 100}])
    draft = definition()
    draft.stages[0].body = {"resumeAfter": token}
    full = execute_finite_stream(session, session.config.database, draft, await_ms=50)
    assert len(full.events) == 2
    row_bound = execute_finite_stream(session, session.config.database, draft, await_ms=50, limit=1)
    assert row_bound.completion == "row_limit" and row_bound.truncated
    assert len(row_bound.events) == 1 and row_bound.undelivered_event_count == 0
    budget = max(len(event.canonical_json.encode()) for event in full.events) + 10
    with pytest.raises(StreamExecutionError) as failure:
        execute_finite_stream(session, session.config.database, draft, await_ms=50, max_bytes=budget)
    partial = failure.value.partial_result
    assert partial.completion == "byte_limit" and len(partial.events) == 1
    assert partial.undelivered_event_count == 1 and partial.undelivered_raw_bytes > 0
    assert_no_owned_cursors(session)


def test_real_database_read_role_can_stream_and_clean_its_own_cursor(stream_session):
    owner, _wire = stream_session
    password = secrets.token_urlsafe(24)
    owner._control.admin.command({"createUser": "generated_reader", "pwd": password,
        "roles": [{"role": "read", "db": owner.config.database}]})
    reader = MongoSession(replace(owner.config, username="generated_reader", password=password, read_only=True))
    try:
        reader.connect()
        result = execute_finite_stream(reader, reader.config.database, definition(), await_ms=50)
        assert not result.events and result.completion == "empty_batch"
        assert_no_owned_cursors(owner)
    finally:
        reader.close()
        owner._control.admin.command({"dropUser": "generated_reader"})
