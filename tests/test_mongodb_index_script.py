"""Observed shell constructors, exact BSON roundtrips and executable previews."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import struct
import subprocess

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Regex
from bson.datetime_ms import DatetimeMS
import pytest

from opennavicat.mongodb_session import BSON_CODEC, parse_mongo_queries
from opennavicat.mongodb_index_script import UnsupportedScriptValue, bson_literal
from opennavicat import mongodb_indexes as tools
from test_mongodb_session import mongodb_service, session


@pytest.mark.parametrize("value", [
    None, True, False, 1, -2147483648, 2147483647, 2147483648, Int64(1), Int64(9007199254740995),
    1.0, -0.0, 1.5, 1.2345678901234567, float("nan"), float("inf"), float("-inf"),
    Decimal128("1.00"), Decimal128("-0.00"), Decimal128("1E-6176"), ObjectId(),
    Binary(b"\0\xff\r\n", 128), Binary(b"", 0), b"plain-binary", "\0\r\n\u2028\u2029汉字😀",
    datetime(2026, 1, 2, 3, 4, 5, 678000, timezone.utc),
    {"nested": [Int64(1), {"x": Decimal128("1.00")}], "$literal": "escaped\"\\text"},
])
def test_observed_constructor_literals_roundtrip_exact_bson_through_actual_command_parser(value):
    script = "db.runCommand({payload:" + bson_literal(value) + "});"
    result = parse_mongo_queries(script)[0]["payload"]
    assert BSON.encode({"payload": result}, codec_options=BSON_CODEC) == BSON.encode({"payload": value}, codec_options=BSON_CODEC)
    assert "EJSON" not in script and "NumberDouble" not in script


@pytest.mark.parametrize("value", [
    Code("return 1"), Code("return x", {"x": 1}), Regex("a.*", "ims"), DatetimeMS(2**63 - 1),
    {"2": 1, "1": 2}, {"a": 1, "1": 2}, {"__proto__": {"x": 1}},
    datetime(2026, 1, 2, microsecond=123456, tzinfo=timezone.utc),
    struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[0], "\ud800",
])
def test_unverified_or_lossy_script_values_are_explicitly_refused(value):
    with pytest.raises(UnsupportedScriptValue):
        bson_literal(value)


def test_numeric_looking_keys_that_javascript_preserves_and_escaped_injection_remain_data():
    value = {"1": "one", "2": "two", "a": "'); db.dropDatabase(); //", "00": "not-array-index", "9" * 5000: "long-string-key"}
    output = bson_literal(value)
    decoded = parse_mongo_queries("db.runCommand({payload:" + output + "});")[0]["payload"]
    assert decoded == value and list(decoded) == list(value)


def test_original_nonroundtrippable_bson_is_kept_readonly_without_a_lossy_script():
    from opennavicat.database import ConnectionConfig
    from opennavicat.mongodb_session import MongoSession
    session = MongoSession(ConnectionConfig("offline", "Generated", driver="mongodb"))
    spec = {"name": "symbol", "key": {"a": 1}, "partialFilterExpression": {"kind": "legacy-symbol"}}
    raw = BSON.encode(spec).replace(b'\x02kind\0', b'\x0ekind\0', 1)
    assert raw != BSON.encode(BSON(raw).decode())
    row = tools._IndexRow(BSON(raw).decode(codec_options=BSON_CODEC)); row.raw_bson = raw
    snapshot = tools._snapshot(session, "generated", "docs", "symbol", row, Binary(b'x' * 16, 4))
    assert snapshot.raw_spec == raw and snapshot.read_only
    assert "不能" in snapshot.ddl and "legacy-symbol" not in snapshot.ddl
    assert "originalRawBSON" in snapshot.command_text


def test_real_protected_preview_executes_and_retains_uuid_guard_and_index_options(session):
    snapshot = tools.new_index(session, session.config.database, "docs")
    definition = deepcopy(snapshot.definition)
    definition.name = "copied_protected"
    definition.keys = [tools.IndexKey("quantity", -1), tools.IndexKey("name", 1)]
    definition.options = {"partialFilterExpression": {"quantity": {"$gte": Int64(1)}}, "sparse": False}
    plan = tools.plan_index(session, snapshot, definition)
    assert "createIndex(" in plan.sql and "Int32(" in plan.sql
    commands = parse_mongo_queries(plan.command_script)
    assert BSON.encode(commands[0], codec_options=BSON_CODEC) == BSON.encode(plan.commands[0], codec_options=BSON_CODEC)
    session.execute(plan.command_script)
    saved = tools.load_index(session, session.config.database, "docs", definition.name)
    assert [(key.field, key.order) for key in saved.definition.keys] == [("quantity", -1), ("name", 1)]
    assert saved.definition.options["partialFilterExpression"] == definition.options["partialFilterExpression"]


def _owned_container(config):
    ids = subprocess.check_output(['docker', 'container', 'ls', '--no-trunc', '--filter',
        'label=opennavicat.task=mongodb-adapter-tests', '--format', '{{.ID}}'], text=True).splitlines()
    matches = []
    for identifier in ids:
        info = json.loads(subprocess.check_output(['docker', 'inspect', identifier], text=True))[0]
        labels = info['Config'].get('Labels', {})
        mapping = info['NetworkSettings']['Ports'].get('27017/tcp', [])
        if mapping == [{'HostIp': '127.0.0.1', 'HostPort': str(config.port)}]:
            assert info['Id'] == identifier and info['Name'].startswith('/opennavicat-mongo-owned-')
            assert labels.get('opennavicat.test') == 'true' and len(labels.get('opennavicat.mongodb-protocol-fixture', '')) == 32
            matches.append(identifier)
    assert len(matches) == 1, 'The fixture must have exactly one positively owned loopback container'
    return matches[0]


def test_real_helper_preview_can_be_copied_into_native_mongosh(session):
    snapshot = tools.new_index(session, session.config.database, "docs")
    definition = deepcopy(snapshot.definition)
    definition.name = "copied_helper"
    definition.keys = [tools.IndexKey("quantity", -1), tools.IndexKey("name", 1)]
    definition.options = {"unique": True, "collation": {"locale": "en", "strength": 2}}
    plan = tools.plan_index(session, snapshot, definition)
    container = _owned_container(session.config)
    auth = 'db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME,process.env.MONGO_INITDB_ROOT_PASSWORD);\n'
    result = subprocess.run(['docker', 'exec', container, 'mongosh', '--quiet', session.config.database,
        '--eval', auth + plan.sql], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    saved = tools.load_index(session, session.config.database, "docs", definition.name)
    assert saved.definition.options['unique'] and saved.definition.options['collation']['strength'] == 2
    assert [(key.field, key.order) for key in saved.definition.keys] == [("quantity", -1), ("name", 1)]


def test_real_raw_metadata_cursor_drains_getmore_without_losing_source_bson(session, monkeypatch):
    from pymongo.database import Database
    original = Database.command
    for number in range(3):
        session.connection[session.config.database].docs.create_index('a' + str(number), name='idx' + str(number))
    def small_batch(db, command, *args, **kwargs):
        if isinstance(command, dict) and 'listIndexes' in command:
            command = {**command, 'cursor': {'batchSize': 1}}
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', small_batch)
    rows = tools._rows(session, session.config.database, 'docs')
    assert len(rows) == 4
    assert all(row.raw_bson == BSON.encode(dict(row), codec_options=BSON_CODEC) for row in rows)
