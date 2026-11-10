"""Native BSON command/cursor tests, with only a fresh owned MongoDB service."""
from __future__ import annotations

import base64
import json
import struct
from contextlib import contextmanager

from bson import BSON, Binary, Decimal128, Int64, ObjectId
from bson.raw_bson import RawBSONDocument
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_query import _render_document, execute_queries
from opennavicat.mongodb_session import BSON_CODEC, canonical_loads
from test_mongodb_session import mongodb_service, session


def seed(session, count=6):
    documents = [{"_id": index, "name": f"generated-{index}", "long": Int64(9007199254740993+index),
                  "decimal": Decimal128("1.00"), "binary": Binary(b"\x00\xff\r\n", 128)} for index in range(count)]
    session.connection[session.config.database].docs.insert_many(documents)
    return documents


def values(result):
    return [canonical_loads(row[0]) for row in result.rows]


def test_raw_bson_duplicate_fields_are_explicit_and_byte_exact_in_query_results():
    first = BSON.encode({"_id": 1, "duplicate": 1})
    repeated = BSON.encode({"duplicate": 2})[4:-1]
    raw = struct.pack("<i", len(first)+len(repeated)) + first[4:-1] + repeated + b"\0"
    text, size = _render_document(RawBSONDocument(raw))
    payload = json.loads(text)["$opennavicatRawBSON"]
    assert base64.b64decode(payload["base64"]) == raw and size == len(raw)
    assert "cannot roundtrip" in payload["reason"]


def test_real_find_native_bson_projection_chains_and_findone(session):
    seed(session)
    result = execute_queries(session, "db.getCollection('docs').find({_id: {$gte: 1}}).sort({_id:-1}).skip(1).limit(2).toArray()", 100)[0]
    data = values(result)
    assert [doc["_id"] for doc in data] == [4, 3] and not result.truncated
    assert isinstance(data[0]["long"], Int64) and str(data[0]["decimal"]) == "1.00"
    assert data[0]["binary"] == Binary(b"\x00\xff\r\n", 128)
    one = execute_queries(session, 'db.docs.findOne({_id:0}, {long:1})')[0]
    assert values(one)[0] == {"_id": 0, "long": Int64(9007199254740993)}


def test_real_cursor_delivery_limit_peeks_getmore_and_closes_cursor(session):
    seed(session, 150)
    server = session.connection.admin
    baseline = server.command("serverStatus")["metrics"]["cursor"]["open"]["total"]
    result = execute_queries(session, '{"find":"docs","sort":{"_id":1},"batchSize":2}', 3)[0]
    assert [doc["_id"] for doc in values(result)] == [0,1,2] and result.truncated
    assert server.command("serverStatus")["metrics"]["cursor"]["open"]["total"] == baseline
    # An exact two-document result with batchSize two needs an empty getMore
    # before the client knows whether the GUI delivery cap truncated anything.
    exact = execute_queries(session, '{"find":"docs","filter":{"_id":{"$lt":2}},"batchSize":2}', 2)[0]
    assert len(exact.rows) == 2 and exact.truncated is False
    assert server.command("serverStatus")["metrics"]["cursor"]["open"]["total"] == baseline


def test_real_aggregate_read_and_terminal_out_keep_actual_write_semantics(session):
    seed(session)
    query = "db.docs.aggregate([{$group:{_id:null, count:{$sum:1}}}])"
    result = execute_queries(session, query, 1)[0]
    assert values(result) == [{"_id": None, "count": 6}] and not result.truncated
    # A GUI result cap must never be inserted before $out or after its terminal
    # position: the user's explicit write still has to receive all six docs.
    execute_queries(session, "db.docs.aggregate([{$match:{}}, {$out:'generated_out'}])", 1)
    assert session.connection[session.config.database].generated_out.count_documents({}) == 6


def test_native_write_errors_report_partial_outcomes_and_stop_later_commands(session):
    script = """db.runCommand({insert:'docs',documents:[{_id:1,long:Long('9007199254740993')}]});
db.runCommand({insert:'docs',documents:[{_id:1,name:'duplicate'}]});
db.runCommand({insert:'docs',documents:[{_id:2,name:'must-not-run'}]});"""
    with pytest.raises(DatabaseError, match="11000") as raised:
        execute_queries(session, script)
    assert len(raised.value.partial_results) == 2
    assert session.connection[session.config.database].docs.count_documents({}) == 1
    assert session.connection[session.config.database].docs.find_one({"_id":2}) is None
    assert isinstance(session.connection[session.config.database].docs.find_one({"_id":1})["long"], Int64)


@pytest.mark.parametrize("text", [
    "db.docs.aggregate([{$out:'blocked'}])",
    "db.docs.aggregate([{$lookup:{from:'docs',pipeline:[{$merge:'blocked'}],as:'values'}}])",
    "db.runCommand({explain:{aggregate:'docs',pipeline:[{$out:'blocked'}],cursor:{}}})",
    "db.runCommand({insert:'docs',documents:[{_id:99}]})",
])
def test_readonly_native_commands_reject_writes_before_driver_io(session, monkeypatch, text):
    session.config.read_only = True
    from pymongo.synchronous.database import Database
    monkeypatch.setattr(Database, "command", lambda *_args, **_kwargs: pytest.fail("Rejected command reached the driver"))
    with pytest.raises(DatabaseError):
        execute_queries(session, text)


@pytest.mark.parametrize("metadata", [{"$db":"other"}, {"lsid":{}}, {"$clusterTime":{}}, {"$readPreference":{"mode":"secondary"}}, {"tailable":True}])
def test_cursor_and_routing_state_cannot_be_changed_by_an_opaque_command(session, metadata):
    command = {"find":"docs", **metadata}
    with pytest.raises(DatabaseError):
        execute_queries(session, json.dumps(command))


def test_real_command_cursor_listindexes_and_multiple_native_results(session):
    seed(session)
    session.connection[session.config.database].docs.create_index("name", name="generated_name")
    results = execute_queries(session, "db.runCommand({listIndexes:'docs',cursor:{batchSize:1}}); db.docs.find({_id:0});")
    assert len(results) == 2 and {doc["name"] for doc in values(results[0])} == {"_id_", "generated_name"}
    assert values(results[1])[0]["_id"] == 0


def test_parse_error_in_later_native_statement_does_not_partly_execute_script(session):
    with pytest.raises(DatabaseError):
        execute_queries(session, "db.runCommand({insert:'docs',documents:[{_id:99}]}); process.exit(1)")
    assert session.connection[session.config.database].docs.count_documents({}) == 0


def test_later_invalid_result_bound_is_validated_before_any_native_write(session):
    script = "db.runCommand({insert:'docs',documents:[{_id:901}]}); db.runCommand({find:'docs',limit:'bad'})"
    with pytest.raises(DatabaseError):
        execute_queries(session, script)
    assert session.connection[session.config.database].docs.count_documents({}) == 0


def test_unordered_bulk_error_preserves_actual_write_count_and_partial_status(session):
    seed(session, 1)
    script = "db.runCommand({insert:'docs',ordered:false,documents:[{_id:902},{_id:0},{_id:903}]})"
    with pytest.raises(DatabaseError) as raised:
        execute_queries(session, script)
    partial = raised.value.partial_results[0]
    assert partial.affected == 2 and partial.partial is True and partial.truncated is False
    assert session.connection[session.config.database].docs.count_documents({}) == 3
    assert "writeErrors" in partial.rows[0][0]


def test_cancel_between_real_statements_preserves_first_result_and_stops_next_write(session, monkeypatch):
    original_operation = session._operation
    cancelled = False

    @contextmanager
    def operation_with_between_statement_cancel(*args, **kwargs):
        nonlocal cancelled
        with original_operation(*args, **kwargs) as comment:
            yield comment
        # The first real server command has completed, but its successor has
        # not started. Keep this deterministic instead of racing a timer.
        if not cancelled:
            cancelled = True
            session.cancel()

    monkeypatch.setattr(session, "_operation", operation_with_between_statement_cancel)
    script = "db.runCommand({insert:'docs',documents:[{_id:911}]}); db.runCommand({insert:'docs',documents:[{_id:912}]})"
    with pytest.raises(DatabaseError, match="cancelled") as raised:
        execute_queries(session, script)
    assert [doc["_id"] for doc in session.connection[session.config.database].docs.find().sort("_id", 1)] == [911]
    assert len(raised.value.partial_results) == 1
    first = raised.value.partial_results[0]
    assert first.affected == 1 and first.partial is False and first.truncated is False
    # Cancellation applies to this script, and a subsequent fresh execution
    # must still work without reconnecting or altering the prior outcome.
    monkeypatch.setattr(session, "_operation", original_operation)
    result = execute_queries(session, "db.docs.find({_id:911})")[0]
    assert values(result) == [{"_id":911}] and not session._cancel_requested.is_set()


@pytest.mark.parametrize("concern", ["{w:0}", "{w:Long('0')}", "{w:0,j:true}", "{w:false}", "{w:0.0}", "{w:-1}", "{w:''}", "0"])
@pytest.mark.parametrize("nested", [False, True])
def test_unacknowledged_concern_is_rejected_before_any_statement_or_driver_io(session, monkeypatch, concern, nested):
    command = "{insert:'docs',documents:[{_id:922}],writeConcern:" + concern + "}"
    if nested:
        command = "{explain:" + command + "}"
    script = "db.runCommand({insert:'docs',documents:[{_id:921}]}); db.runCommand(" + command + ")"
    from pymongo.synchronous.database import Database
    with monkeypatch.context() as patch:
        patch.setattr(Database, "command", lambda *_args, **_kwargs: pytest.fail("Rejected script reached the driver"))
        with pytest.raises(DatabaseError, match="writeConcern"):
            execute_queries(session, script)
    assert session.connection[session.config.database].docs.count_documents({}) == 0


def test_acknowledged_write_accepts_same_named_fields_inside_user_documents(session):
    result = execute_queries(session, "db.runCommand({insert:'docs',documents:[{_id:931,writeConcern:{w:0},nested:{writeConcern:{w:0}}}],writeConcern:{w:1}})")[0]
    assert result.affected == 1 and not result.partial and not result.truncated
    assert session.connection[session.config.database].docs.find_one({"_id":931}) == {
        "_id":931, "writeConcern":{"w":0}, "nested":{"writeConcern":{"w":0}}}
