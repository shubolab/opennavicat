"""Fresh-owned MongoDB index commands, typed plans and honest partial results."""
from copy import deepcopy
from dataclasses import replace
import threading
import time

from bson import BSON, Int64
from pymongo.database import Database
from pymongo.errors import AutoReconnect
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoConflictError
from opennavicat import mongodb_indexes as tools
from test_mongodb_session import mongodb_service, session


def draft(session, keys=None, **options):
    snapshot = tools.new_index(session, session.config.database, "docs")
    definition = deepcopy(snapshot.definition)
    definition.name = "owned_index"
    definition.keys = keys or [tools.IndexKey("a", 1), tools.IndexKey("b", -1)]
    definition.options = options
    return snapshot, definition


def test_bson_numeric_types_are_not_equal_in_detached_drafts():
    left = tools.IndexDefinition("idx", "docs", [tools.IndexKey("a")], {"x": Int64(1)})
    right = deepcopy(left)
    right.options["x"] = 1
    assert left != right


@pytest.mark.parametrize("changes", [
    {"name": "*"}, {"name": "_id_"}, {"name": ""}, {"keys": []},
    {"keys": [tools.IndexKey("a", True)]},
    {"keys": [tools.IndexKey("a"), tools.IndexKey("a")]},
    {"options": {"unique": 1}}, {"options": {"expireAfterSeconds": float("nan")}},
    {"options": {"futureOption": {"exact": Int64(1)}}},
    {"options": {"sparse": True, "partialFilterExpression": {"a": {"$exists": True}}}},
])
def test_invalid_definitions_are_rejected_before_commands(changes):
    definition = tools.IndexDefinition("owned", "docs", [tools.IndexKey("a")])
    for name, value in changes.items():
        setattr(definition, name, value)
    with pytest.raises(DatabaseError):
        tools.validate_definition(definition)


def test_real_ordered_unique_partial_and_collation_create_readback(session):
    snapshot, definition = draft(session, unique=True,
        partialFilterExpression={"a": {"$exists": True}}, collation={"locale": "en", "strength": 2})
    plan = tools.plan_index(session, snapshot, definition)
    assert plan.changed and not plan.destructive and "createIndex(" in plan.sql
    assert "createIndexes" in plan.command_script and "collectionUUID" in plan.command_text
    saved = tools.apply_plan(session, plan)
    spec = BSON(saved.raw_spec).decode()
    assert list(spec["key"].items()) == [("a", 1), ("b", -1)]
    assert spec["unique"] and spec["partialFilterExpression"] == definition.options["partialFilterExpression"]
    assert spec["collation"]["locale"] == "en" and spec["collation"]["strength"] == 2
    assert not tools.plan_index(session, saved, saved.definition).changed


def test_real_text_metadata_replay_preserves_internal_key_weights_and_suffix(session):
    keys = [tools.IndexKey("category"), tools.IndexKey("title", "text"),
            tools.IndexKey("body", "text"), tools.IndexKey("stamp", -1)]
    snapshot, definition = draft(session, keys, weights={"title": 5, "body": 2}, default_language="english")
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    assert not saved.read_only
    before = BSON(saved.raw_spec).decode()
    modified = deepcopy(saved.definition)
    modified.options["weights"]["body"] = 8
    plan = tools.plan_index(session, saved, modified)
    assert plan.destructive and len(plan.commands) == 2 and plan.warnings
    assert list(plan.commands[1]["indexes"][0]["key"].items()) == list(before["key"].items())
    updated = tools.apply_plan(session, plan)
    after = BSON(updated.raw_spec).decode()
    assert after["key"] == before["key"] and after["weights"] == {"title": 5, "body": 8}


def test_real_ttl_geo_hashed_storage_and_hidden_metadata_survive_edit(session):
    for name, keys, options in [
        ("ttl", [tools.IndexKey("created")], {"expireAfterSeconds": Int64(3600)}),
        ("geo", [tools.IndexKey("location", "2dsphere")], {"2dsphereIndexVersion": 3}),
        ("hashed", [tools.IndexKey("token", "hashed")], {"hidden": True}),
        ("storage", [tools.IndexKey("payload")], {"storageEngine": {"wiredTiger": {"configString": "block_compressor=zstd"}}}),
    ]:
        snapshot, definition = draft(session, keys, **options)
        definition.name = name
        saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
        assert not saved.read_only
        for option, value in options.items():
            assert saved.definition.options[option] == value


def test_real_fields_named_like_text_internal_metadata_are_not_lost(session):
    snapshot, definition = draft(session, [tools.IndexKey("_ftsx"), tools.IndexKey("regular", -1)])
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    assert [key.field for key in saved.definition.keys] == ["_ftsx", "regular"]


def test_real_plan_is_session_bound_and_detects_tampering(session):
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    forged = deepcopy(plan)
    forged.commands[0]["indexes"][0]["unique"] = True
    with pytest.raises(DatabaseError, match="changed|another session"):
        tools.apply_plan(session, forged)
    assert "owned_index" not in session.connection[session.config.database].docs.index_information()
    session._generation = "changed"
    with pytest.raises(DatabaseError, match="another session"):
        tools.apply_plan(session, plan)


def test_real_stale_index_and_replaced_collection_reject_before_writes(session):
    snapshot, definition = draft(session)
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    definition = deepcopy(saved.definition); definition.options["sparse"] = True
    plan = tools.plan_index(session, saved, definition)
    db = session.connection[session.config.database]
    db.docs.drop_index("owned_index"); db.docs.create_index("replacement", name="owned_index")
    with pytest.raises(MongoConflictError):
        tools.apply_plan(session, plan)
    assert list(db.docs.index_information()["owned_index"]["key"]) == [("replacement", 1)]
    snapshot, definition = draft(session); definition.name = "another"
    plan = tools.plan_index(session, snapshot, definition)
    db.docs.drop(); db.create_collection("docs"); db.docs.insert_one({"marker": "replacement"})
    with pytest.raises(MongoConflictError):
        tools.apply_plan(session, plan)
    assert set(db.docs.index_information()) == {"_id_"} and db.docs.count_documents({"marker": "replacement"}) == 1


def test_real_namespace_race_at_drop_is_rejected_by_server_uuid(session, monkeypatch):
    snapshot, definition = draft(session)
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    definition = deepcopy(saved.definition); definition.options["sparse"] = True
    plan = tools.plan_index(session, saved, definition)
    original = Database.command
    fired = []
    def race(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get("dropIndexes") and command.get("index") and not fired:
            fired.append(True)
            db.docs.drop(); db.create_collection("docs")
            db.docs.create_index("replacement", name="owned_index")
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, "command", race)
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, plan)
    assert error.value.__cause__.code == 361
    assert [step.status for step in error.value.outcome.steps] == ["server_error", "not_started"]
    assert list(session.connection[session.config.database].docs.index_information()["owned_index"]["key"]) == [("replacement", 1)]


def test_real_drop_then_create_failure_reports_missing_original_without_restoring(session):
    db = session.connection[session.config.database]
    db.docs.insert_many([{"a": 1}, {"a": 1}])
    snapshot, definition = draft(session, [tools.IndexKey("a")])
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    modified = deepcopy(saved.definition); modified.options["unique"] = True
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, tools.plan_index(session, saved, modified))
    assert [step.status for step in error.value.outcome.steps] == ["acknowledged", "server_error"]
    assert error.value.outcome.observation["docs"]["indexes"] == []
    assert set(db.docs.index_information()) == {"_id_"} and db.docs.count_documents({}) == 2


def test_real_lost_acknowledgement_is_unknown_even_when_index_exists(session, monkeypatch):
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    original = Database.command
    writes = []
    def lost_reply(db, command, *args, **kwargs):
        reply = original(db, command, *args, **kwargs)
        if isinstance(command, dict) and "createIndexes" in command:
            writes.append(True)
            raise AutoReconnect("Synthetic lost acknowledgement")
        return reply
    monkeypatch.setattr(Database, "command", lost_reply)
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, plan)
    assert len(writes) == 1 and error.value.outcome.steps[0].status == "unknown"
    assert error.value.outcome.observation["docs"]["indexes"][0]["name"] == "owned_index"


def test_real_readonly_builtin_and_queued_cancel_write_nothing(session, monkeypatch):
    builtin = tools.load_index(session, session.config.database, "docs", "_id_")
    assert builtin.read_only and builtin.issues
    with pytest.raises(DatabaseError):
        tools.plan_drop(session, builtin)
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    with monkeypatch.context() as patch:
        patch.setattr(session, "_operation", lambda *_: pytest.fail("Queued cancel must not perform DB I/O"))
        with pytest.raises(tools.IndexApplyError) as error:
            tools.apply_plan(session, plan, cancelled=lambda: True)
    assert error.value.outcome.steps[0].status == "not_started" and not error.value.outcome.observation
    session.config = replace(session.config, read_only=True)
    with pytest.raises(DatabaseError, match="another session"):
        tools.apply_plan(session, plan)
    readonly = tools.new_index(session, session.config.database, "docs")
    with pytest.raises(DatabaseError, match="read-only"):
        tools.plan_index(session, readonly, definition)


def test_real_cancel_between_drop_and_create_keeps_actual_partial_state(session):
    snapshot, definition = draft(session)
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    modified = deepcopy(saved.definition); modified.options["sparse"] = True
    calls = []
    def cancelled():
        calls.append(True)
        return len(calls) >= 3
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, tools.plan_index(session, saved, modified), cancelled=cancelled)
    assert [step.status for step in error.value.outcome.steps] == ["acknowledged", "not_started"]
    assert set(session.connection[session.config.database].docs.index_information()) == {"_id_"}


def test_real_native_cancel_of_own_lock_wait_never_claims_completed_create(session):
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    session.connection.admin.command({"fsync": 1, "lock": True})
    errors = []
    worker = threading.Thread(target=lambda: _capture_apply(session, plan, errors))
    try:
        worker.start()
        deadline = time.monotonic() + 10
        while True:
            with session._active_lock:
                comment = session._active_comment
            rows = list(session._control.admin.aggregate([{"$currentOp": {"allUsers": False}}, {"$match": {"command.comment": comment}}])) if comment else []
            if any(row.get("command", {}).get("createIndexes") and row.get("command", {}).get("indexes") for row in rows):
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        session.cancel()
        worker.join(5)
    finally:
        session.connection.admin.command({"fsyncUnlock": 1})
        worker.join(10)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], tools.IndexApplyError)
    assert errors[0].outcome.steps[0].status != "acknowledged"
    assert "owned_index" not in session.connection[session.config.database].docs.index_information()


def _capture_apply(session, plan, errors):
    try:
        tools.apply_plan(session, plan)
    except Exception as error:
        errors.append(error)


@pytest.mark.parametrize("operation,code,version,accepted", [
    ("drop", 361, "unqueried", True), ("create", 361, "unqueried", True),
    ("create", 2, "8.0.34", True), ("create", 2, "7.0.45", True),
    ("create", 2, "8.0.35", False), ("drop", 2, "8.0.34", False),
    ("drop", None, "unqueried", False), ("create", 13, "unqueried", False),
])
def test_guard_qualification_is_command_specific_and_never_falls_back_to_uuid_free_write(monkeypatch, operation, code, version, accepted):
    from contextlib import contextmanager
    from pymongo.errors import OperationFailure
    captured = []
    class Client:
        def __getitem__(self, name):
            assert name == "generated"
            return self
        def command(self, command):
            captured.append(command)
            if code is not None:
                raise OperationFailure("synthetic", code, {"codeName": "CollectionUUIDMismatch" if code == 361 else "BadValue"})
            return {"ok": 1}
    class Session:
        connection = Client()
        @contextmanager
        def _operation(self):
            yield "generated-probe"
    def build_info(_session):
        assert version != "unqueried"
        return version
    monkeypatch.setattr(tools, "_server_version", build_info)
    if accepted:
        tools._require_command_guard(Session(), "generated", "docs", operation)
    else:
        with pytest.raises(DatabaseError):
            tools._require_command_guard(Session(), "generated", "docs", operation)
    assert len(captured) == 1
    command = captured[0]
    assert command["indexes" if operation == "create" else "index"] == []
    assert command["collectionUUID"].subtype == 4 and len(command["collectionUUID"]) == 16


def test_real_text_default_weight_and_removed_override_keep_all_text_fields(session):
    snapshot, definition = draft(session, [tools.IndexKey("title", "text"), tools.IndexKey("body", "text")], weights={"title": 5})
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    assert saved.definition.options["weights"] == {"body": 1, "title": 5}
    modified = deepcopy(saved.definition); modified.options["weights"] = {"title": 2}
    saved = tools.apply_plan(session, tools.plan_index(session, saved, modified))
    assert saved.definition.options["weights"] == {"body": 1, "title": 2}
    assert {key.field for key in saved.definition.keys} == {"body", "title"}


def test_real_successful_delete_is_name_targeted_and_preserves_other_indexes(session):
    snapshot, definition = draft(session)
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    session.connection[session.config.database].docs.create_index("other", name="another_index")
    deleted = tools.apply_plan(session, tools.plan_drop(session, saved))
    assert not deleted.exists
    assert set(session.connection[session.config.database].docs.index_information()) == {"_id_", "another_index"}


def test_real_replaced_target_collection_is_detected_before_dropping_source_index(session):
    snapshot, definition = draft(session)
    saved = tools.apply_plan(session, tools.plan_index(session, snapshot, definition))
    db = session.connection[session.config.database]
    db.create_collection("other")
    moved = deepcopy(saved.definition); moved.collection = "other"
    plan = tools.plan_index(session, saved, moved)
    db.other.drop(); db.create_collection("other")
    with pytest.raises(MongoConflictError):
        tools.apply_plan(session, plan)
    assert "owned_index" in db.docs.index_information() and set(db.other.index_information()) == {"_id_"}


def test_real_write_concern_failure_keeps_unknown_ack_and_actual_index(session, monkeypatch):
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    original = Database.command
    def incomplete_ack(db, command, *args, **kwargs):
        reply = original(db, command, *args, **kwargs)
        if isinstance(command, dict) and command.get("indexes"):
            reply["writeConcernError"] = {"code": 64, "errmsg": "synthetic durability acknowledgement failure"}
        return reply
    monkeypatch.setattr(Database, "command", incomplete_ack)
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, plan)
    assert error.value.outcome.steps[0].status == "unknown"
    assert error.value.outcome.observation["docs"]["indexes"][0]["name"] == "owned_index"


def test_real_server_permission_denial_is_not_mistaken_for_an_unsupported_server(session):
    from opennavicat.mongodb_session import MongoSession
    import uuid
    name = "index_reader_" + uuid.uuid4().hex
    db = session.connection[session.config.database]
    db.command({"createUser": name, "pwd": "synthetic-fixture-only", "roles": [{"role": "read", "db": session.config.database}]})
    restricted = MongoSession(replace(session.config, username=name, password="synthetic-fixture-only", mongo_auth_source=session.config.database))
    try:
        restricted.connect()
        snapshot, definition = draft(restricted)
        with pytest.raises(DatabaseError, match="permission") as error:
            tools.plan_index(restricted, snapshot, definition)
        assert error.value.__cause__.code == 13
        assert set(db.docs.index_information()) == {"_id_"}
    finally:
        restricted.close()


def test_real_post_ack_replacement_is_reported_instead_of_returning_a_false_success(session, monkeypatch):
    snapshot, definition = draft(session)
    plan = tools.plan_index(session, snapshot, definition)
    original = Database.command
    replaced = []
    def replace_after_ack(db, command, *args, **kwargs):
        reply = original(db, command, *args, **kwargs)
        if isinstance(command, dict) and command.get("indexes") and not replaced:
            replaced.append(True)
            db.docs.drop_index("owned_index")
            db.docs.create_index("replacement", name="owned_index")
        return reply
    monkeypatch.setattr(Database, "command", replace_after_ack)
    with pytest.raises(tools.IndexApplyError) as error:
        tools.apply_plan(session, plan)
    assert error.value.outcome.steps[0].status == "acknowledged"
    assert list(error.value.outcome.observation["docs"]["indexes"][0]["key"].items()) == [("replacement", 1)]
