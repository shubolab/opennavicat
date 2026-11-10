"""Cross-driver sync against explicitly opted-in disposable local servers.

Set OPENNAVICAT_TEST_POSTGRES_PORT, OPENNAVICAT_TEST_MYSQL_PORT and
OPENNAVICAT_TEST_MARIADB_PORT to test-only containers; no saved profiles are read.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import itertools
import os
import uuid

import pytest

from opennavicat.compare import apply_plan, compare_tables
from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession

DRIVERS = ["sqlite", "postgresql", "mysql", "mariadb"]
PAIRS = list(itertools.permutations(DRIVERS, 2)) + [("mariadb", "mariadb")]
PORT_ENV = {"postgresql": "OPENNAVICAT_TEST_POSTGRES_PORT", "mysql": "OPENNAVICAT_TEST_MYSQL_PORT", "mariadb": "OPENNAVICAT_TEST_MARIADB_PORT"}


def make_session(driver, role, tmp_path):
    ident = f"compare-{role}-" + uuid.uuid4().hex
    if driver == "sqlite":
        session = DatabaseSession(ConnectionConfig(ident, role, database=str(tmp_path / f"{ident}.sqlite")))
        schema = "main"
    else:
        port = os.environ.get(PORT_ENV[driver])
        if not port:
            pytest.skip(f"Set {PORT_ENV[driver]} for disposable {driver} integration")
        session = DatabaseSession(ConnectionConfig(ident, role, driver, "opennavicat_test", "127.0.0.1", int(port),
            "postgres" if driver == "postgresql" else "root", "opennavicat-test-only", "disable"))
        schema = "onv_compare_" + uuid.uuid4().hex[:12]
        session.execute(f"CREATE SCHEMA {session.quote_identifier(schema)}")
    binary = "BYTEA" if driver == "postgresql" else "BLOB"
    session.execute(f"CREATE TABLE {session.qualified_name(schema, 'things')} (id INTEGER PRIMARY KEY, value TEXT, amount DECIMAL(12,2), doc JSON, payload {binary}, day DATE)")
    return session, schema


def close_session(session, schema):
    if session.in_transaction:
        session.execute("ROLLBACK")
    if session.config.driver != "sqlite":
        session.execute(f"DROP SCHEMA {session.quote_identifier(schema)}" + (" CASCADE" if session.config.driver == "postgresql" else ""))
    session.close()


@pytest.fixture(params=PAIRS, ids=lambda pair: "-to-".join(pair))
def typed_pair(request, tmp_path):
    # Check every external requirement before creating either side.
    for driver in request.param:
        if driver != "sqlite" and not os.environ.get(PORT_ENV[driver]):
            pytest.skip(f"Set {PORT_ENV[driver]} for disposable integration")
    created = []
    try:
        source = make_session(request.param[0], "source", tmp_path)
        created.append(source)
        target = make_session(request.param[1], "target", tmp_path)
        created.append(target)
        columns = ["id", "value", "amount", "doc", "payload", "day"]
        source[0].import_rows(source[1], "things", columns, [
            [1, "new", "12.25", {"owner": "source", "flags": [True, False]}, b"\x00\xff", "2026-11-10"],
            [2, "same", None, None, b"", None],
            [3, "insert", "4.50", ["x", 1], b"a", "2026-11-09"],
        ])
        target[0].import_rows(target[1], "things", columns, [
            [1, "old", "2.00", {"owner": "target"}, b"\x01", "2026-01-01"],
            [2, "same", None, None, b"", None],
            [4, "extra", "9.00", {"extra": True}, b"extra", "2026-11-08"],
        ])
        yield source, target
    finally:
        for session, schema in reversed(created):
            close_session(session, schema)


def normalized_rows(session, schema):
    rows = session.fetch_table(schema, "things").rows
    return [[r[0], r[1], Decimal(str(r[2])) if r[2] is not None else None, r[3], bytes(r[4]),
             r[5].isoformat() if isinstance(r[5], date) else r[5]] for r in rows]


def plan_for(pair):
    (source, source_schema), (target, target_schema) = pair
    return compare_tables(source, target, source_schema, "things", target_schema, "things")


def test_cross_driver_typed_sync_is_previewed_and_atomic(typed_pair):
    (source, source_schema), (target, target_schema) = typed_pair
    before = normalized_rows(target, target_schema)
    plan = plan_for(typed_pair)
    assert normalized_rows(target, target_schema) == before
    assert (len(plan.inserts), len(plan.updates), len(plan.deletes), plan.unchanged) == (1, 1, 1, 1)
    assert apply_plan(target, plan, include_deletes=True) == {"inserted": 1, "updated": 1, "deleted": 1}
    assert normalized_rows(target, target_schema) == normalized_rows(source, source_schema)
    assert plan.applied and not target.in_transaction
    repeated = plan_for(typed_pair)
    assert not repeated.inserts and not repeated.updates and not repeated.deletes
    assert repeated.unchanged == 3


def test_cross_driver_insert_conflict_rolls_back_prior_work(typed_pair):
    _, (target, schema) = typed_pair
    plan = plan_for(typed_pair)
    target.insert_row(schema, "things", {"id": 3, "value": "concurrent insert", "payload": b""})
    before = normalized_rows(target, schema)
    with pytest.raises((DatabaseError, ValueError)):
        apply_plan(target, plan, include_deletes=True)
    assert normalized_rows(target, schema) == before
    assert not plan.applied and not target.in_transaction


def test_cross_driver_schema_changed_after_preview_is_rejected(typed_pair):
    _, (target, schema) = typed_pair
    plan = plan_for(typed_pair)
    target.execute(f"ALTER TABLE {target.qualified_name(schema, 'things')} ADD COLUMN new_column TEXT")
    before = target.fetch_table(schema, "things").rows
    with pytest.raises((DatabaseError, ValueError)):
        apply_plan(target, plan)
    assert target.fetch_table(schema, "things").rows == before
    assert not plan.applied and not target.in_transaction


def test_mariadb_metadata_json_read_only_and_cancellation(tmp_path):
    import threading
    import time
    if not os.environ.get(PORT_ENV["mariadb"]):
        pytest.skip("Set OPENNAVICAT_TEST_MARIADB_PORT for disposable MariaDB integration")
    session, schema = make_session("mariadb", "MariaDB-native", tmp_path)
    try:
        parent = session.qualified_name(schema, "parent")
        child = session.qualified_name(schema, "child")
        session.execute(f'''CREATE TABLE {parent} (a INTEGER, b INTEGER, PRIMARY KEY(a,b));
            CREATE TABLE {child} (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER,
              CONSTRAINT child_parent_fk FOREIGN KEY(a,b) REFERENCES {parent}(a,b) ON UPDATE CASCADE ON DELETE SET NULL);
            CREATE UNIQUE INDEX child_unique ON {child}(a,b);''')
        assert schema in session.schemas()
        assert {o["name"] for o in session.objects(schema)} == {"things", "parent", "child"}
        json_column = next(c for c in session.columns(schema, "things") if c["name"] == "doc")
        assert json_column["type"] == "json" and json_column["storage_type"] == "longtext"
        values = [{"key": [1, True]}, ["x", 2], "plain string", True, 3.5]
        session.import_rows(schema, "things", ["id", "doc", "payload"], [[i, v, b""] for i, v in enumerate(values)])
        assert [r[3] for r in session.fetch_table(schema, "things").rows] == values
        indexes = session.indexes(schema, "child")
        assert next(i for i in indexes if i["primary"])["columns"] == ["id"]
        assert next(i for i in indexes if i["name"] == "child_unique")["columns"] == ["a", "b"]
        fk = session.foreign_keys(schema, "child")[0]
        assert fk["columns"] == ["a", "b"] and fk["referenced_columns"] == ["a", "b"]
        assert fk["on_update"] == "CASCADE" and fk["on_delete"] == "SET NULL"
        readonly = DatabaseSession(ConnectionConfig(**{**vars(session.config), "read_only": True}))
        try:
            assert readonly.count_rows(schema, "things") == 5
            with pytest.raises(DatabaseError):
                readonly.execute(f"INSERT INTO {child} (id) VALUES (99)")
        finally:
            readonly.close()
        errors = []
        def run():
            try:
                session.execute("SELECT SLEEP(15)")
            except DatabaseError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 2
        while not session._active and time.monotonic() < deadline:
            time.sleep(.001)
        time.sleep(.05)
        session.cancel()
        worker.join(3)
        assert not worker.is_alive() and errors
        assert session.execute("SELECT 42")[0].rows == [[42]]
    finally:
        close_session(session, schema)


@pytest.mark.parametrize("typed_pair", [("sqlite", "postgresql"), ("postgresql", "sqlite")],
                         indirect=True, ids=["sqlite-to-postgresql", "postgresql-to-sqlite"])
def test_cross_driver_date_primary_key_matches_then_syncs_idempotently(typed_pair):
    (source, source_schema), (target, target_schema) = typed_pair
    for session, schema in ((source, source_schema), (target, target_schema)):
        session.execute(f"CREATE TABLE {session.qualified_name(schema, 'dated')} (day DATE PRIMARY KEY, value TEXT)")
    source.insert_row(source_schema, "dated", {"day": "2026-11-10", "value": "new"})
    target.insert_row(target_schema, "dated", {"day": "2026-11-10", "value": "old"})
    plan = compare_tables(source, target, source_schema, "dated", target_schema, "dated")
    assert not plan.inserts and not plan.deletes
    assert len(plan.updates) == 1 and plan.updates[0][1] == {"value": "new"}
    assert apply_plan(target, plan) == {"inserted": 0, "updated": 1, "deleted": 0}
    repeated = compare_tables(source, target, source_schema, "dated", target_schema, "dated")
    assert not repeated.inserts and not repeated.updates and not repeated.deletes
    assert repeated.unchanged == 1
