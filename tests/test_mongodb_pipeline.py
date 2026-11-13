"""Aggregation pipeline model and real-server qualification on owned MongoDB."""
from __future__ import annotations

from bson import BSON, Binary, Code, Decimal128, Int64, Timestamp
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import threading
import time
import uuid
import pytest
from pymongo import monitoring, timeout
from pymongo.errors import InvalidOperation, OperationFailure

from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import (
    STAGE_NAMES, PipelineDefinition, PipelineStage, execute_pipeline,
    explain_pipeline, from_nagg, parse_query_pipeline, pipeline_script,
    parse_explain_reply, pipeline_aggregate_options, preview_pipeline,
    to_nagg, validate_pipeline,
)
from test_mongodb_session import mongodb_service, session  # noqa: F401 - pytest fixtures


class _PipelineBudgetCommands(monitoring.CommandListener):
    """Capture only non-sensitive budget fields through the public API."""

    def __init__(self, database):
        self.database = database
        self.commands = []
        self._lock = threading.Lock()

    def started(self, event):
        if event.database_name != self.database or event.command_name not in {"aggregate", "explain"}:
            return
        command = event.command
        inner = command.get("explain", {})
        with self._lock:
            self.commands.append({
                "kind": event.command_name, "maxTimeMS": command.get("maxTimeMS"),
                "innerMaxTimeMS": inner.get("maxTimeMS") if isinstance(inner, dict) else None,
            })

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@pytest.fixture
def pipeline_wire_session(mongodb_service, monkeypatch):
    from opennavicat.mongodb_session import MongoSession

    config = replace(mongodb_service, database="opennavicat_budget_" + uuid.uuid4().hex)
    value = MongoSession(config)
    listener = _PipelineBudgetCommands(config.database)
    original_options = value._connection_options
    # Instrument this owned session only; no global listener registration or
    # changes to PyMongo's command construction/timeout implementation.
    monkeypatch.setattr(value, "_connection_options", lambda: {
        **original_options(), "event_listeners": [listener]})
    value.connect()
    database = value.connection[config.database]
    database.docs.insert_many([{"_id": 1, "generated": True}, {"_id": 2, "generated": True}])
    database.command("profile", 2)
    try:
        yield value, listener
    finally:
        value.connection.drop_database(config.database)
        value.close()


@pytest.mark.parametrize("operation", [execute_pipeline, preview_pipeline, explain_pipeline])
@pytest.mark.parametrize("requested", [1, 10, 5000, 0, None])
def test_real_pipeline_max_time_wire_and_profiler(pipeline_wire_session, operation, requested):
    session, listener = pipeline_wire_session
    marker = "generated-budget-" + uuid.uuid4().hex
    options = {"comment": marker}
    if requested is not None:
        options["maxTimeMS"] = requested
    definition = PipelineDefinition(source="docs", sample_mode="all",
        stages=[PipelineStage("$match", {})], reference_options=options)
    try:
        # An ambient short CSOT must not convert a positive 1 ms server limit
        # to zero or replace it with a larger remaining client deadline.
        with timeout(2):
            operation(session, session.config.database, definition)
    except DatabaseError as error:
        # A genuine 1 ms server limit may expire even for this tiny workload.
        assert requested in (1, 10)
        assert isinstance(error.__cause__, OperationFailure) and error.__cause__.code == 50
    commands = listener.commands
    assert commands
    profiles = list(session.connection[session.config.database]["system.profile"].find({"$or": [
        {"command.comment.userComment": marker},
        {"command.explain.comment.userComment": marker}]}))
    assert profiles
    if requested is not None and requested > 0:
        assert all(type(item["maxTimeMS"]) is int and 0 < item["maxTimeMS"] <= requested for item in commands)
        assert all(item["innerMaxTimeMS"] is None for item in commands)
        assert all(0 < row["command"]["maxTimeMS"] <= requested for row in profiles)
        assert all("maxTimeMS" not in row["command"].get("explain", {}) for row in profiles)
    else:
        # No positive server budget retains the original client and existing
        # ambient/default CSOT behavior, rather than creating extra clients.
        assert not session._pipeline_clients
        assert all(0 < item["maxTimeMS"] <= 2000 for item in commands)
        if operation is explain_pipeline:
            assert all(item["innerMaxTimeMS"] == requested for item in commands)


@pytest.mark.parametrize("operation,ceiling", [(execute_pipeline, 120), (explain_pipeline, 30)])
def test_real_pipeline_server_timeout_closes_budget_client_and_session_recovers(
        pipeline_wire_session, operation, ceiling):
    session, listener = pipeline_wire_session
    with session._pipeline_budget(5000, ceiling_seconds=ceiling) as (old_client, _effective):
        old_client.admin.command("ping")
    # A finite read-only expansion of two generated documents supplies normal
    # executor interrupt points. Tiny JS-only pipelines can reach EOF without
    # an interrupt check, so elapsed JS time alone is not a timeout assertion.
    slow = PipelineDefinition(source="docs", sample_mode="all", stages=[
        PipelineStage("$project", {"generatedNumbers": {"$range": [0, 200000]}}),
        PipelineStage("$unwind", "$generatedNumbers"),
        PipelineStage("$group", {"_id": None, "total": {"$sum": "$generatedNumbers"}})],
        reference_options={"maxTimeMS": 10})
    started = time.monotonic()
    with pytest.raises(DatabaseError) as failure:
        operation(session, session.config.database, slow)
    assert isinstance(failure.value.__cause__, OperationFailure) and failure.value.__cause__.code == 50
    assert time.monotonic() - started < 5
    assert listener.commands[-1]["maxTimeMS"] == 10
    with pytest.raises(InvalidOperation):
        old_client.admin.command("ping")
    ordinary = PipelineDefinition(source="docs", stages=[PipelineStage("$match", {})],
        reference_options={"maxTimeMS": 5000})
    rows, truncated = execute_pipeline(session, session.config.database, ordinary)
    assert len(rows) == 2 and not truncated
    with session._pipeline_budget(5000, ceiling_seconds=ceiling) as (replacement, _effective):
        assert replacement is not old_client
        replacement.admin.command("ping")
    session.connection.admin.command("ping")


def test_submillisecond_pipeline_budget_cannot_dispatch_unlimited_zero(monkeypatch):
    import opennavicat.mongodb_pipeline as module

    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    with pytest.raises(DatabaseError, match="elapsed-time safety limit"):
        with module._pipeline_command_budget(object(), {"maxTimeMS": 1},
                ceiling_seconds=120, deadline=100.0004):
            pytest.fail("A rounded-zero server budget must never dispatch")


@pytest.mark.parametrize("operation", [execute_pipeline, explain_pipeline])
@pytest.mark.parametrize("gate_phase", ["construction", "warm_ping"])
def test_cold_budget_cancel_with_no_server_operation_prevents_dispatch(operation, gate_phase, monkeypatch):
    import opennavicat.mongodb_session as module

    preparing, release = threading.Event(), threading.Event()
    dispatched, failures, controls = [], [], []

    class EmptyCursor(list):
        def close(self):
            pass

    class Control:
        admin = property(lambda self: self)

        def aggregate(self, *_args, **_kwargs):
            controls.append("no active server operation")
            return EmptyCursor()

    class ColdClient:
        admin = property(lambda self: self)

        def __init__(self, **_kwargs):
            if gate_phase == "construction":
                preparing.set()
                assert release.wait(3), "The test must release cold preparation"

        def command(self, command):
            if command == "ping" and gate_phase == "warm_ping":
                preparing.set()
                assert release.wait(3), "The test must release cold authentication"
            elif command != "ping":
                dispatched.append("explain")
            return {"ok": 1}

        def get_database(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _name):
            return self

        def aggregate(self, *_args, **_kwargs):
            dispatched.append("aggregate")
            return EmptyCursor()

        def close(self):
            pass

    class Endpoint(ColdClient):
        def __init__(self):
            pass

        def command(self, command):
            dispatched.append("explain")
            return {"ok": 1}

    monkeypatch.setattr(module, "MongoClient", ColdClient)
    session = module.MongoSession(SimpleNamespace(database="generated"))
    session.config.read_only = False
    session.connection, session._control = Endpoint(), Control()
    session._connected_options = {"connectTimeoutMS": 1000, "serverSelectionTimeoutMS": 1000}
    definition = PipelineDefinition(source="docs", stages=[PipelineStage("$match", {})],
        reference_options={"maxTimeMS": 5000})

    def execute():
        try:
            operation(session, "generated", definition)
        except DatabaseError as error:
            failures.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert preparing.wait(2)
        marker = session.cancellation_marker()
        assert marker is not None
        session.cancel(expected_comment=marker)
        assert controls == ["no active server operation"]
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not dispatched, "A Stop accepted during cold preparation must prevent the later command"
    assert failures and "cancelled before dispatch" in str(failures[0])


@pytest.mark.parametrize("ceiling", [30, 120])
@pytest.mark.parametrize("expired", [False, True])
def test_pipeline_budget_charges_cold_preparation_to_remaining_deadline(ceiling, expired, monkeypatch):
    import opennavicat.mongodb_pipeline as module
    from contextlib import contextmanager

    clock, endpoint = [0.0], object()
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    @contextmanager
    def prepared_client(requested, *, ceiling_seconds):
        assert requested == 5000 and ceiling_seconds == ceiling
        clock[0] = ceiling + .1 if expired else ceiling - .5
        yield endpoint, requested

    session = SimpleNamespace(_pipeline_budget=prepared_client)
    options = {"maxTimeMS": 5000}
    if expired:
        with pytest.raises(DatabaseError, match="elapsed-time safety limit"):
            with module._pipeline_command_budget(session, options, ceiling_seconds=ceiling, deadline=ceiling):
                pytest.fail("Expired preparation must not dispatch")
    else:
        with module._pipeline_command_budget(session, options, ceiling_seconds=ceiling, deadline=ceiling) as client:
            assert client is endpoint and options["maxTimeMS"] == 500


@pytest.mark.parametrize("requested", [0, None, 5000])
def test_explain_accepted_predispatch_cancel_never_sends_command(requested):
    from contextlib import contextmanager

    cancelled = threading.Event()

    @contextmanager
    def marked_operation():
        cancelled.set()
        yield "generated-marker"

    class Session:
        _cancel_requested = cancelled
        _operation = staticmethod(marked_operation)
        use_schema = staticmethod(lambda _database: None)

        @property
        def connection(self):
            pytest.fail("Cancelled Explain must not open a network endpoint")

    options = {} if requested is None else {"maxTimeMS": requested}
    with pytest.raises(DatabaseError, match="cancelled before dispatch"):
        explain_pipeline(Session(), "generated", PipelineDefinition(source="docs",
            stages=[PipelineStage("$match", {})], reference_options=options))


@pytest.mark.parametrize("operation", [execute_pipeline, preview_pipeline, explain_pipeline])
def test_job_cancel_before_operation_marker_survives_session_flag_reset(operation):
    from contextlib import contextmanager

    job_cancel, session_cancel = threading.Event(), threading.Event()
    job_cancel.set()

    @contextmanager
    def operation_marker():
        # MongoSession starts a new marker by resetting its own cooperative
        # flag. A Stop already accepted by the queued job remains authoritative.
        session_cancel.clear()
        yield "generated-marker"

    class Session:
        config = SimpleNamespace(read_only=False)
        _cancel_requested = session_cancel
        _operation = staticmethod(operation_marker)
        _validate_namespace = staticmethod(lambda *_args: None)
        use_schema = staticmethod(lambda _database: None)

        @property
        def connection(self):
            pytest.fail("A queued job cancellation must survive marker creation")

    with pytest.raises(DatabaseError, match="cancelled before dispatch"):
        operation(Session(), "generated", PipelineDefinition(source="docs",
            stages=[PipelineStage("$match", {})]), cancel_event=job_cancel)


def test_preview_job_cancel_survives_between_source_and_stage_prefixes():
    from bson.raw_bson import RawBSONDocument
    from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC

    job_cancel, calls = threading.Event(), []

    class Cursor:
        def __iter__(self):
            return iter([RawBSONDocument(BSON.encode({"generated": True}, codec_options=BSON_CODEC),
                codec_options=RAW_CODEC)])

        def close(self):
            job_cancel.set()

    class Endpoint:
        def get_database(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _name):
            return self

        def aggregate(self, pipeline, **_kwargs):
            calls.append(pipeline)
            return Cursor()

    session = SimpleNamespace(config=SimpleNamespace(read_only=False), connection=Endpoint(),
        _validate_namespace=lambda *_args: None, _operation=lambda *_args: nullcontext("generated-marker"))
    definition = PipelineDefinition(source="docs", sample_mode="all",
        stages=[PipelineStage("$match", {}), PipelineStage("$project", {"generated": 1})])
    with pytest.raises(DatabaseError, match="cancelled before dispatch"):
        preview_pipeline(session, "generated", definition, cancel_event=job_cancel)
    assert len(calls) == 1


def test_real_pipeline_budget_preserves_read_only_and_independent_cancel(pipeline_wire_session):
    session, listener = pipeline_wire_session
    session.config.read_only = True
    ordinary = PipelineDefinition(source="docs", stages=[PipelineStage("$match", {})],
        reference_options={"maxTimeMS": 5000})
    rows, _truncated = execute_pipeline(session, session.config.database, ordinary)
    assert len(rows) == 2
    before = len(listener.commands)
    with pytest.raises(DatabaseError, match="writable session"):
        execute_pipeline(session, session.config.database, PipelineDefinition(source="docs",
            stages=[PipelineStage("$out", "generated_output")], reference_options={"maxTimeMS": 5000}))
    assert len(listener.commands) == before

    slow = PipelineDefinition(source="docs", stages=[PipelineStage("$addFields", {
        "generatedHold": {"$function": {
            "body": "function(v) { const end = Date.now() + 1000; while (Date.now() < end) {} return v; }",
            "args": ["$_id"], "lang": "js"}}})],
        reference_options={"maxTimeMS": 5000, "comment": "generated-budget-cancel"})
    errors = []

    def execute():
        try:
            execute_pipeline(session, session.config.database, slow)
        except DatabaseError as error:
            errors.append(error)

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        visible, marker = [], None
        while time.monotonic() < deadline and worker.is_alive():
            marker = session.cancellation_marker()
            if marker is not None:
                with timeout(1):
                    visible = list(session._control.admin.aggregate([
                        {"$currentOp": {"allUsers": False}}, {"$match": {"command.comment": marker}}]))
                if visible:
                    break
            time.sleep(.01)
        assert visible, "Owned budget query must be visible to its independent cancellation channel"
        session.cancel(expected_comment=marker)
        worker.join(3)
        assert not worker.is_alive() and errors
        assert session.cancellation_marker() is None
        rows, _truncated = execute_pipeline(session, session.config.database, ordinary)
        assert len(rows) == 2
    finally:
        worker.join(8)
        assert not worker.is_alive()


def test_stage_catalog_matches_reference_order():
    assert len(STAGE_NAMES) == 42
    assert STAGE_NAMES[:6] == (
        "$addFields", "$bucket", "$bucketAuto", "$changeStream",
        "$changeStreamSplitLargeEvent", "$collStats")
    assert STAGE_NAMES[-8:] == (
        "$setWindowFields", "$sharedDataDistribution", "$skip", "$sort",
        "$sortByCount", "$unionWith", "$unset", "$unwind")


@pytest.mark.parametrize('operation', [preview_pipeline, execute_pipeline])
def test_cancel_accepted_after_operation_marker_prevents_aggregate_dispatch(operation):
    from contextlib import contextmanager
    import threading

    cancelled = threading.Event()
    @contextmanager
    def marked_operation():
        cancelled.set()
        yield 'generated-marker'
    class Session:
        config = SimpleNamespace(read_only=False)
        _cancel_requested = cancelled
        _validate_namespace = staticmethod(lambda *_args: None)
        _operation = staticmethod(marked_operation)
        @property
        def connection(self):
            pytest.fail('An accepted pre-dispatch cancellation must not send aggregate')
    with pytest.raises(DatabaseError, match='before dispatch'):
        operation(Session(), 'generated', PipelineDefinition(source='docs', stages=[PipelineStage()]))


def test_nagg_roundtrip_preserves_bson_and_disabled_stages():
    definition = PipelineDefinition(
        name="generated_pipeline", source="docs", sample_mode="first", sample_size=25,
        stages=[
            PipelineStage("$match", {"_id": Int64(5)}, True, "stage-one"),
            PipelineStage("$project", {"amount": Decimal128("1.25")}, False, "stage-two"),
        ], selected_stage=1)
    text = to_nagg(definition)
    restored = from_nagg(text)
    assert restored.source == definition.source
    assert restored.stages == definition.stages
    assert restored.sample_mode == definition.sample_mode
    assert restored.sample_size == definition.sample_size
    assert restored.name == "" and restored.selected_stage == 0
    assert restored.stages[0].body["_id"] == Int64(5)
    assert restored.stages[1].body["amount"] == Decimal128("1.25")
    assert restored.stages[1].enabled is False
    document = json.loads(text)
    assert list(document) == [
        "Version", "Collection", "PreviewSourceOption", "Stages", "Option"]
    assert document["Version"] == "0.0"
    assert document["Collection"] == "docs"
    assert document["PreviewSourceOption"] == {"SampleMode": 1, "SampleSize": 25}
    assert document["Stages"][0]["MQL"].startswith('{"_id":{"$numberLong":"5"}}')
    assert 'Long("5")' in pipeline_script(definition)


def test_reference_version_0_nagg_imports_observed_lookup_and_sample_modes():
    document = {
        "Version": "0.0",
        "Collection": "sales",
        "PreviewSourceOption": {"SampleMode": 0, "SampleSize": 1000},
        "Stages": [
            {
                "Op": "$lookup",
                "MQL": '{"from":"customers","localField":"customer","foreignField":"customer","as":"customer_docs"}',
                "Enabled": True,
            },
            {"Op": "$limit", "MQL": "2", "Enabled": True},
        ],
        "Option": {
            "allowDiskUse": 0,
            "cursorBatchSize": -1,
            "maxTimeMS": 1200,
            "bypassDocumentValidation": 0,
            "readConcernLevel": 0,
            "locale": "zh",
            "caseLevel": 0,
            "caseFirst": 0,
            "strength": 3,
            "numericOrdering": 0,
            "alternate": 0,
            "maxVariable": 0,
            "backwards": 0,
            "normalization": 0,
            "hint": "",
            "comment": "generated reference",
            "enableWriteConcern": False,
            "w": "",
            "j": 0,
            "wtimeout": 0,
            "let": "",
        },
        "GeneratedExtension": {"preserved": True},
    }
    definition = from_nagg(json.dumps(document, separators=(",", ":")))
    assert definition.source == "sales"
    assert definition.sample_mode == "all" and definition.sample_size == 1000
    assert definition.stages[0].operator == "$lookup"
    assert definition.stages[0].body == {
        "from": "customers", "localField": "customer",
        "foreignField": "customer", "as": "customer_docs"}
    assert definition.stages[1].body == 2
    assert definition.reference_options == {
        "maxTimeMS": 1200, "locale": "zh", "strength": 3, "comment": "generated reference"}
    assert definition.reference_extra == {"GeneratedExtension": {"preserved": True}}
    exported = json.loads(to_nagg(definition))
    assert exported["PreviewSourceOption"]["SampleMode"] == 0
    assert exported["Option"]["maxTimeMS"] == 1200
    assert exported["Option"]["comment"] == "generated reference"
    assert exported["GeneratedExtension"] == {"preserved": True}


def test_reference_nagg_export_matches_observed_lookup_bytes():
    definition = PipelineDefinition(
        source="sales", sample_mode="all", sample_size=1000,
        stages=[PipelineStage("$lookup", {
            "from": "customers", "localField": "customer",
            "foreignField": "customer", "as": "customer_docs"})])
    encoded = to_nagg(definition).encode("utf-8")
    assert len(encoded) == 588
    assert hashlib.sha256(encoded).hexdigest() == (
        "bde5e0bc931dcdab5d922fa5cf0f19f4da148639d23001c52fb414838900dd0a")


def test_reference_options_map_to_aggregate_arguments_and_script():
    definition = PipelineDefinition(
        source="sales", stages=[PipelineStage("$match", {"active": True})],
        reference_options={
            "allowDiskUse": -1,
            "cursorBatchSize": 25,
            "maxTimeMS": 1200,
            "bypassDocumentValidation": 1,
            "readConcernLevel": 3,
            "locale": "zh",
            "caseLevel": 1,
            "caseFirst": 2,
            "strength": 3,
            "numericOrdering": 1,
            "alternate": 2,
            "maxVariable": 2,
            "backwards": 1,
            "normalization": 1,
            "hint": "active_1",
            "comment": "generated_options",
            "enableWriteConcern": True,
            "w": "1",
            "j": 1,
            "wtimeout": 500,
            "let": '{"wanted":true}',
        })
    options = pipeline_aggregate_options(definition)
    assert options["allowDiskUse"] is False
    assert options["bypassDocumentValidation"] is True
    assert options["batchSize"] == 25 and options["maxTimeMS"] == 1200
    assert options["readConcern"] == {"level": "majority"}
    assert options["collation"] == {
        "locale": "zh", "caseLevel": True, "caseFirst": "lower", "alternate": "shifted",
        "maxVariable": "space", "numericOrdering": True, "backwards": True,
        "normalization": True, "strength": 3}
    assert options["let"] == {"wanted": True}
    assert options["writeConcern"] == {"w": 1, "j": True, "wtimeout": 500}
    script = pipeline_script(definition)
    assert '"allowDiskUse": false' in script
    assert '"readConcern": {' in script and '"majority"' in script
    assert '"hint": "active_1"' in script
    assert '"wtimeout": Int32("500")' in script


def test_reference_options_reject_unsupported_enum_values():
    definition = PipelineDefinition(
        source="sales", stages=[PipelineStage("$match", {})],
        reference_options={"readConcernLevel": 99})
    with pytest.raises(DatabaseError, match="readConcernLevel"):
        validate_pipeline(definition)


def test_pipeline_script_with_options_and_no_stages_is_valid():
    definition = PipelineDefinition(
        source="sales", stages=[], selected_stage=None,
        reference_options={"comment": "generated_empty"})
    script = pipeline_script(definition)
    assert "db.getCollection(\"sales\").aggregate([]," in script
    assert '"comment": "generated_empty"' in script


@pytest.fixture(scope="module")
def pipeline_native():
    from opennavicat.js_engine.runtime import NativeJavaScript

    name = "opennavicat-js.exe" if os.name == "nt" else "opennavicat-js"
    helper = Path(os.environ.get("OPENNAVICAT_TEST_JS_HELPER",
        Path(__file__).resolve().parents[1] / "artifacts/js-engine" / name))
    if not helper.exists():
        pytest.skip("Build the pinned native JavaScript helper before script qualification")
    return NativeJavaScript(helper)


@pytest.mark.parametrize("case", ["stage", "empty_with_options", "typed_let"])
def test_generated_script_executes_in_native_vm_and_passes_exact_aggregate_arguments(pipeline_native, case):
    from opennavicat.mongodb_session import BSON_CODEC

    source = 'generated_"collection'
    stages = [PipelineStage("$match", {"long": Int64(9007199254740995),
                                          "decimal": Decimal128("12.5000"), "int": 7})]
    expected_stages = [{"$match": {"long": Int64(9007199254740995),
                                    "decimal": Decimal128("12.5000"), "int": 7}}]
    options, expected_options = {}, None
    if case == "empty_with_options":
        stages, expected_stages = [], []
        options = {"comment": "generated_empty"}
        expected_options = {"comment": "generated_empty"}
    elif case == "typed_let":
        options = {"maxTimeMS": 1200, "let": (
            '{"long":{"$numberLong":"9007199254740995"},'
            '"decimal":{"$numberDecimal":"12.5000"},'
            '"int":{"$numberInt":"7"},'
            '"binary":{"$binary":{"base64":"AP8=","subType":"80"}}}')}
        expected_options = {"maxTimeMS": 1200, "let": {
            "long": Int64(9007199254740995), "decimal": Decimal128("12.5000"),
            "int": 7, "binary": Binary(b"\0\xff", 128)}}
    definition = PipelineDefinition(source=source, stages=stages,
        selected_stage=0 if stages else None, reference_options=options)
    # A lexical synthetic db captures the generated call without granting a
    # database capability or claiming unimplemented host aggregate options.
    script = """const captured=[];
(function(db){
""" + pipeline_script(definition) + """
})({getCollection(name){return {aggregate(...args){captured.push({name,args});}};}});
captured;
"""
    result = pipeline_native.evaluate(script)
    expected_args = [expected_stages]
    if expected_options is not None:
        expected_args.append(expected_options)
    expected = [{"name": source, "args": expected_args}]
    assert BSON.encode({"captured": result.value}, codec_options=BSON_CODEC) == BSON.encode(
        {"captured": expected}, codec_options=BSON_CODEC)
    assert result.child_reaped and result.commands == ()


@pytest.mark.parametrize("value", [Code("return 1"), Timestamp(1, 2), {"__proto__": 1}])
@pytest.mark.parametrize("placement", ["stage", "let"])
def test_script_refuses_unqualified_shell_bson_without_losing_the_draft(value, placement):
    from opennavicat.mongodb_session import canonical_dumps

    definition = PipelineDefinition(source="generated", stages=[PipelineStage("$match", {"value": value})])
    if placement == "let":
        definition.stages = [PipelineStage("$match", {})]
        definition.reference_options = {"let": canonical_dumps({"value": value})}
    original = to_nagg(definition)
    with pytest.raises(DatabaseError, match="retained|cannot safely"):
        pipeline_script(definition)
    assert to_nagg(definition) == original


@pytest.mark.parametrize("operator", ["$changeStream", "$changeStreamSplitLargeEvent"])
@pytest.mark.parametrize("mode", ["all", "first", "random"])
@pytest.mark.parametrize("read_only", [False, True])
def test_stream_preview_is_rejected_before_any_source_or_stage_io(operator, mode, read_only):
    class NoIO:
        config = SimpleNamespace(read_only=read_only)

        def _validate_namespace(self, *_args):
            pytest.fail("A stream preview must be refused before source dispatch")

    definition = PipelineDefinition(source="generated", sample_mode=mode,
        stages=[PipelineStage(operator, {})])
    with pytest.raises(DatabaseError, match="Preview does not execute change-stream"):
        preview_pipeline(NoIO(), "generated", definition)


@pytest.mark.parametrize("operation", [preview_pipeline, execute_pipeline])
@pytest.mark.parametrize("read_only", [False, True])
def test_disabled_stream_stage_does_not_block_ordinary_pipeline(operation, read_only):
    from bson.raw_bson import RawBSONDocument
    from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC

    calls, closed = [], []

    class Cursor:
        def __iter__(self):
            return iter([RawBSONDocument(BSON.encode({"generated": True}, codec_options=BSON_CODEC),
                                        codec_options=RAW_CODEC)])

        def close(self):
            closed.append(True)

    class Endpoint:
        def get_database(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _name):
            return self

        def aggregate(self, pipeline, **_kwargs):
            calls.append(pipeline)
            return Cursor()

    session = SimpleNamespace(config=SimpleNamespace(read_only=read_only), connection=Endpoint(),
        _validate_namespace=lambda *_args: None, _operation=lambda *_args: nullcontext("owned-marker"))
    definition = PipelineDefinition(source="generated", sample_mode="all", stages=[
        PipelineStage("$match", {}), PipelineStage("$changeStream", {}, enabled=False),
        PipelineStage("$changeStreamSplitLargeEvent", {}, enabled=False)])
    result = operation(session, "generated", definition)
    assert result is not None and calls and len(closed) == len(calls)
    assert all(not any(set(stage) & {"$changeStream", "$changeStreamSplitLargeEvent"}
                       for stage in pipeline) for pipeline in calls)


def test_reference_nagg_preserves_stage_extensions_and_exact_bson():
    definition = PipelineDefinition(
        source="sales", sample_mode="random", sample_size=50,
        stages=[PipelineStage(
            "$match", {"_id": {"$gt": Int64(7)}, "amount": Decimal128("1.25")},
            reference_extra={"StageExtension": {"future": True}})])
    restored = from_nagg(to_nagg(definition))
    assert restored == definition
    assert restored.stages[0].reference_extra == {"StageExtension": {"future": True}}
    assert restored.stages[0].body["_id"]["$gt"] == Int64(7)
    assert restored.stages[0].body["amount"] == Decimal128("1.25")


def test_reference_nagg_preserves_mql_comments_and_parses_them_safely():
    mql = '''{
/* Filter out documents based on given criteria. *
 * query: criteria to filter the documents.      *
 { <query> }
 */
}'''
    document = {
        "Version": "0.0",
        "Collection": "sales",
        "PreviewSourceOption": {"SampleMode": 2, "SampleSize": 1000},
        "Stages": [{"Op": "$match", "MQL": mql, "Enabled": True}],
        "Option": {},
    }
    definition = from_nagg(json.dumps(document, separators=(",", ":")))
    assert definition.stages[0].body == {}
    assert definition.stages[0].reference_mql == mql
    exported = json.loads(to_nagg(definition))
    assert exported["Stages"][0]["MQL"] == mql

    stage = PipelineStage(
        "$match", {"url": "https://generated.example/*not-a-comment*/", "active": True},
        reference_mql='{"url":"https://generated.example/*not-a-comment*/","active":true}')
    restored = from_nagg(to_nagg(PipelineDefinition(
        source="sales", stages=[stage])))
    assert restored.stages[0].body["url"] == "https://generated.example/*not-a-comment*/"


def test_legacy_opennavicat_nagg_remains_importable():
    legacy = json.dumps({
        "format": "opennavicat-nagg-v1",
        "name": "legacy_pipeline",
        "source": "sales",
        "sampleMode": "first",
        "sampleSize": 12,
        "selectedStage": 0,
        "stages": [{
            "operator": "$limit",
            "body": 12,
            "enabled": True,
            "stageId": "legacy-stage",
        }],
    })
    restored = from_nagg(legacy)
    assert restored.name == "legacy_pipeline"
    assert restored.sample_mode == "first" and restored.sample_size == 12
    assert restored.stages[0].body == 12


def test_nagg_and_query_import_reject_partial_or_ambiguous_stages():
    with pytest.raises(DatabaseError, match="aggregation pipeline"):
        from_nagg('{"format":"other","stages":[]}')
    with pytest.raises(DatabaseError, match="one-field"):
        parse_query_pipeline("docs", '[{"$match":{},"$limit":1}]')
    with pytest.raises(DatabaseError, match="between"):
        validate_pipeline(PipelineDefinition(source="docs", sample_size=0,
            stages=[PipelineStage("$match", {})]))


def test_explain_projection_builds_nested_plan_and_preserves_unknown_fields():
    reply = {
        "explainVersion": "1",
        "queryPlanner": {"namespace": "demo.sales", "winningPlan": {"stage": "COLLSCAN"}},
        "executionStats": {
            "executionTimeMillis": 17,
            "executionStages": {
                "stage": "GROUP",
                "nReturned": 1,
                "executionTimeMillisEstimate": 12,
                "customNodeMetric": {"$numberLong": 7},
                "inputStage": {
                    "stage": "COLLSCAN",
                    "nReturned": 3,
                    "docsExamined": 4,
                    "executionTimeMillisEstimate": 10,
                    "unknownNodeFlag": True,
                },
            },
        },
        "unknownTopLevel": {"preserved": True},
        "ok": 1,
    }
    explain = parse_explain_reply(reply)
    assert explain.root is not None and explain.root.stage == "GROUP"
    assert [node.stage for node in explain.nodes()] == ["GROUP", "COLLSCAN"]
    assert explain.root.children[0].docs_examined == 4
    assert explain.raw["unknownTopLevel"] == {"preserved": True}
    assert explain.root.raw["customNodeMetric"] == Int64(7)
    assert "unknownNodeFlag" in explain.root.children[0].raw
    assert explain.stats_rows()[1]["stage"] == "COLLSCAN"
    assert explain.stats_rows()[1]["elapsed_percent"] > 80
    assert '"unknownTopLevel"' in explain.raw_text


def test_real_pipeline_preview_execute_and_explain(session):
    database = session.connection[session.config.database]
    database.pipeline_source.insert_many([
        {"group": "north", "amount": Int64(10)},
        {"group": "north", "amount": Int64(20)},
        {"group": "south", "amount": Int64(30)},
    ])
    definition = PipelineDefinition(
        name="generated_pipeline", source="pipeline_source", sample_mode="first", sample_size=100,
        stages=[
            PipelineStage("$match", {"group": "north"}),
            PipelineStage("$group", {"_id": "$group", "total": {"$sum": "$amount"}}),
        ])
    preview = preview_pipeline(session, session.config.database, definition, limit=20)
    assert len(preview.source_rows) == 3
    assert len(preview.stages[0]["rows"]) == 2
    assert len(preview.final_rows) == 1
    rows, truncated = execute_pipeline(session, session.config.database, definition)
    assert rows == preview.final_rows and not truncated
    all_preview = preview_pipeline(session, session.config.database, PipelineDefinition(
        source="pipeline_source", sample_mode="all", sample_size=1,
        stages=[PipelineStage("$match", {"group": "north"})]), limit=20)
    assert len(all_preview.source_rows) == 3
    assert all_preview.stages[-1]["rows"] == preview.stages[0]["rows"]
    plan = explain_pipeline(session, session.config.database, definition)
    assert plan.root is not None
    assert "executionStats" in plan.raw
    assert any(node.stage for node in plan.nodes())


def test_real_pipeline_options_enter_preview_execute_and_explain(session):
    database = session.connection[session.config.database]
    database.pipeline_source.insert_many([
        {"group": "north", "amount": 10},
        {"group": "south", "amount": 20},
    ])
    definition = PipelineDefinition(
        source="pipeline_source", stages=[PipelineStage(
            "$match", {"$expr": {"$eq": ["$group", "$$wanted"]}})],
        reference_options={
            "allowDiskUse": 1,
            "cursorBatchSize": 2,
            "maxTimeMS": 5000,
            "readConcernLevel": 1,
            "comment": "generated_options",
            "let": '{"wanted":"north"}',
        })
    preview = preview_pipeline(session, session.config.database, definition, limit=20)
    assert len(preview.final_rows) == 1
    rows, truncated = execute_pipeline(session, session.config.database, definition)
    assert rows == preview.final_rows and not truncated
    explain = explain_pipeline(session, session.config.database, definition)
    assert explain.command["let"] == {"wanted": "north"}
    assert explain.command["cursor"]["batchSize"] == 2


def test_preview_refuses_out_but_execute_allows_it_only_on_writable_session(session):
    database = session.connection[session.config.database]
    database.pipeline_source.insert_one({"group": "north", "amount": 1})
    definition = PipelineDefinition(
        name="write_pipeline", source="pipeline_source",
        stages=[PipelineStage("$out", "pipeline_output")],
        reference_options={
            "enableWriteConcern": True, "w": "1", "j": 1, "wtimeout": 500,
            "comment": "generated_write_options"})
    with pytest.raises(DatabaseError, match="Preview does not execute"):
        preview_pipeline(session, session.config.database, definition)
    rows, _truncated = execute_pipeline(session, session.config.database, definition)
    assert rows == []
    assert database.pipeline_output.count_documents({}) == 1


def test_store_persists_pipeline_per_connection_and_schema(tmp_path):
    from opennavicat.storage import Store

    definition = PipelineDefinition(
        name="generated_pipeline", source="docs",
        stages=[PipelineStage("$match", {"generated": True})])
    store = Store(tmp_path / "workspace")
    entry = store.save_mongo_pipeline("connection-a", "catalog", "generated_pipeline", to_nagg(definition))
    assert store.mongo_pipeline(entry["id"])["source"] == "docs"
    assert store.mongo_pipelines("connection-a", "catalog")[0]["name"] == "generated_pipeline"
    assert store.mongo_pipelines("connection-b", "catalog") == []

    definition.name = "renamed_pipeline"
    updated = store.save_mongo_pipeline(
        "connection-a", "catalog", definition.name, to_nagg(definition), pipeline_id=entry["id"])
    assert updated["id"] == entry["id"] and updated["created"] == entry["created"]
    restarted = Store(tmp_path / "workspace")
    restored = restarted.mongo_pipeline(entry["id"])
    assert restored["name"] == "renamed_pipeline"
    assert from_nagg(restored["nagg"]).source == "docs"
    restarted.delete_mongo_pipeline(entry["id"])
    assert Store(tmp_path / "workspace").mongo_pipeline(entry["id"]) is None
