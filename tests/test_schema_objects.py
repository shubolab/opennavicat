"""Owned SQLite objects: reviewed saves, native behavior, and exact rollback."""
from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.schema import IndexTerm, SchemaConflictError, SchemaError
from opennavicat.schema_objects import (
    IndexObjectDefinition, ObjectPlan, TriggerObjectDefinition, apply_plan,
    load_index, load_trigger, new_index, new_trigger, plan_index, plan_trigger,
)


@pytest.fixture
def session(tmp_path):
    result = DatabaseSession(ConnectionConfig("object-design", "Owned objects", database=str(tmp_path / "objects.db")))
    result.connect()
    result.execute("CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT,category TEXT); CREATE TABLE audit(message TEXT); INSERT INTO items VALUES(1,'alpha','same'),(2,'Beta','same')")
    yield result
    result.close()


def independent(session, sql):
    with closing(sqlite3.connect(session.config.database)) as connection:
        return connection.execute(sql).fetchall()


def catalog(session):
    return session._query("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")


def test_create_then_retarget_and_rename_compound_index_actual_sort_collation(session):
    definition = IndexObjectDefinition('idx "设计', ["name", "category"], terms=[IndexTerm("name", "NOCASE", True), IndexTerm("category")], table="items")
    original = deepcopy(definition)
    plan = plan_index(session, new_index(table="items"), definition)
    assert definition == original and plan.changed and "CREATE INDEX" in plan.sql
    assert not any(x[1] == definition.name for x in catalog(session))
    saved = apply_plan(session, plan)
    assert not saved.issues and not saved.read_only
    assert saved.definition.terms == definition.terms
    rows = independent(session, 'PRAGMA index_xinfo("idx ""设计")')
    assert rows[0][2:5] == ("name", 1, "NOCASE")
    session.execute("CREATE TABLE other(value TEXT)")
    snapshot = load_index(session, "main", definition.name)
    changed = deepcopy(snapshot.definition)
    changed.name, changed.table = "retargeted", "other"
    changed.columns, changed.terms = ["value"], [IndexTerm("value", "RTRIM")]
    result = apply_plan(session, plan_index(session, snapshot, changed))
    assert result.definition.table == "other"
    assert independent(session, "SELECT tbl_name FROM sqlite_schema WHERE name='retargeted'") == [("other",)]


def test_unique_failure_preserves_original_index_sql_and_all_data(session):
    session.execute("CREATE INDEX original ON items(category COLLATE NOCASE DESC)")
    snapshot = load_index(session, "main", "original")
    changed = deepcopy(snapshot.definition)
    changed.unique = True
    plan = plan_index(session, snapshot, changed)
    before = catalog(session)
    with pytest.raises(SchemaError, match="rolled back"):
        apply_plan(session, plan)
    assert catalog(session) == before
    assert independent(session, "SELECT * FROM items ORDER BY id") == [(1, "alpha", "same"), (2, "Beta", "same")]
    assert not session.in_transaction


@pytest.mark.parametrize("statement,name", [
    ("CREATE INDEX complex ON items(lower(name) COLLATE NOCASE DESC)", "complex"),
    ("CREATE INDEX partial ON items(name) WHERE id>1", "partial"),
    ("CREATE TABLE constrained(a TEXT UNIQUE)", "sqlite_autoindex_constrained_1"),
])
def test_complex_and_automatic_indexes_are_losslessly_readonly(session, statement, name):
    session.execute(statement)
    snapshot = load_index(session, "main", name)
    assert snapshot.read_only and snapshot.issues
    no_change = plan_index(session, snapshot, deepcopy(snapshot.definition))
    assert not no_change.changed and no_change.ddl == snapshot.ddl
    assert apply_plan(session, no_change).definition == snapshot.definition
    changed = deepcopy(snapshot.definition)
    changed.name = "disallowed"
    with pytest.raises(SchemaError, match="protected"):
        plan_index(session, snapshot, changed)
    assert load_index(session, "main", name).ddl == snapshot.ddl


def test_other_complex_indexes_and_unrelated_schema_unchanged(session):
    session.execute("CREATE INDEX preserved ON items(lower(name)) WHERE id>0; CREATE INDEX edited ON items(category)")
    original_sql = load_index(session, "main", "preserved").ddl
    snapshot = load_index(session, "main", "edited")
    changed = deepcopy(snapshot.definition)
    changed.columns, changed.terms = ["name"], [IndexTerm("name")]
    apply_plan(session, plan_index(session, snapshot, changed))
    assert load_index(session, "main", "preserved").ddl == original_sql


@pytest.mark.parametrize("event,body,write,expected", [
    ("INSERT", "BEGIN INSERT INTO audit VALUES(NEW.name || '; insert'); END", "INSERT INTO items VALUES(3,'Gamma','other')", "Gamma; insert"),
    ("UPDATE", "BEGIN INSERT INTO audit VALUES(OLD.name || '→' || NEW.name); END", "UPDATE items SET name='changed' WHERE id=1", "alpha→changed"),
    ("DELETE", "BEGIN INSERT INTO audit VALUES(OLD.name); END", "DELETE FROM items WHERE id=1", "alpha"),
])
def test_table_trigger_events_are_compiled_without_writes_then_actually_fire(session, event, body, write, expected):
    definition = TriggerObjectDefinition('trigger "设计', timing="AFTER", event=event, body=body, table="items")
    if event == "UPDATE":
        definition.update_columns = ["NAME"]
        definition.when = "NEW.name <> OLD.name"
    saved = apply_plan(session, plan_trigger(session, new_trigger(table="items"), definition))
    assert saved.definition.table == "items" and not saved.read_only
    assert independent(session, "SELECT * FROM audit") == []
    session.execute(write)
    assert independent(session, "SELECT * FROM audit") == [(expected,)]


def test_retarget_trigger_from_table_to_view_with_instead_of_real_behavior(session):
    session.execute("CREATE VIEW item_view AS SELECT id,name,category FROM items; CREATE TRIGGER old_trigger AFTER INSERT ON items BEGIN INSERT INTO audit VALUES(NEW.name); END")
    snapshot = load_trigger(session, "main", "old_trigger")
    changed = deepcopy(snapshot.definition)
    changed.name, changed.table, changed.owner_type, changed.timing = "view_trigger", "item_view", "view", "INSTEAD OF"
    changed.body = "BEGIN INSERT INTO items(id,name,category) VALUES(NEW.id,NEW.name,NEW.category); END"
    saved = apply_plan(session, plan_trigger(session, snapshot, changed))
    assert saved.definition.owner_type == "view" and saved.definition.timing == "INSTEAD OF"
    session.execute("INSERT INTO item_view VALUES(3,'view insert','other')")
    assert independent(session, "SELECT name FROM items WHERE id=3") == [("view insert",)]
    assert independent(session, "SELECT * FROM audit") == []


@pytest.mark.parametrize("owner_type,timing", [("view", "AFTER"), ("table", "INSTEAD OF")])
def test_owner_type_controls_valid_trigger_timing(session, owner_type, timing):
    session.execute("CREATE VIEW item_view AS SELECT * FROM items")
    table = "item_view" if owner_type == "view" else "items"
    changed = TriggerObjectDefinition("wrong_timing", timing=timing, table=table, owner_type=owner_type, body="BEGIN SELECT 1; END")
    with pytest.raises(SchemaError, match="Views require INSTEAD OF"):
        plan_trigger(session, new_trigger(table=table, owner_type=owner_type), changed)


@pytest.mark.parametrize("body", ["BEGIN UPDATE items SET absent=NEW.name; END", "BEGIN SELECT NEW.missing; END", "BEGIN THIS IS NOT SQL; END"])
def test_trigger_save_failure_rolls_back_original_definition(session, body):
    session.execute("CREATE TRIGGER original AFTER INSERT ON items BEGIN INSERT INTO audit VALUES(NEW.name); END")
    snapshot = load_trigger(session, "main", "original")
    changed = deepcopy(snapshot.definition)
    changed.body = body
    plan = plan_trigger(session, snapshot, changed)
    before = catalog(session)
    with pytest.raises(SchemaError, match="rolled back"):
        apply_plan(session, plan)
    assert catalog(session) == before and not session.in_transaction
    session.execute("INSERT INTO items VALUES(3,'retained','other')")
    assert independent(session, "SELECT * FROM audit") == [("retained",)]


def test_trigger_body_cannot_smuggle_extra_top_level_ddl_or_lose_comments(session):
    source = new_trigger(table="items")
    malicious = TriggerObjectDefinition("escaped", timing="AFTER", table="items", body="BEGIN SELECT 1; END; DROP TABLE items; BEGIN SELECT 1; END")
    with pytest.raises(SchemaError, match="top-level"):
        plan_trigger(session, source, malicious)
    valid = TriggerObjectDefinition("commented", timing="AFTER", table="items", body="BEGIN SELECT CASE WHEN NEW.id>0 THEN 'END; -- text' ELSE 'x' END; END -- final comment")
    plan = plan_trigger(session, source, valid)
    assert "-- final comment\n;" in plan.ddl
    saved = apply_plan(session, plan)
    assert "END; -- text" in saved.ddl


def test_update_of_unknown_generated_columns_and_multiple_events_rejected(session):
    session.execute("ALTER TABLE items ADD COLUMN computed TEXT AS (upper(name)) VIRTUAL")
    source = new_trigger(table="items")
    for column in ["missing", "computed"]:
        changed = TriggerObjectDefinition("invalid", timing="AFTER", event="UPDATE", update_columns=[column], table="items", body="BEGIN SELECT 1; END")
        with pytest.raises(SchemaError, match="existing writable"):
            plan_trigger(session, source, changed)
    changed.event, changed.update_columns = "INSERT OR UPDATE", []
    with pytest.raises(SchemaError, match="one INSERT"):
        plan_trigger(session, source, changed)


@pytest.mark.parametrize("foreign_keys", ["ON", "OFF"])
def test_changing_parent_unique_index_cannot_break_empty_child_fk(session, foreign_keys):
    session.execute(f"PRAGMA foreign_keys={foreign_keys}; CREATE TABLE parent(code TEXT); CREATE UNIQUE INDEX parent_unique ON parent(code); CREATE TABLE child(code TEXT REFERENCES parent(code))")
    snapshot = load_index(session, "main", "parent_unique")
    changed = deepcopy(snapshot.definition)
    changed.unique = False
    before = catalog(session)
    with pytest.raises(SchemaError, match="foreign key mismatch"):
        apply_plan(session, plan_index(session, snapshot, changed))
    assert catalog(session) == before
    assert session._query("PRAGMA foreign_keys") == [(foreign_keys == "ON",)]
    session.execute("PRAGMA foreign_keys=ON; INSERT INTO parent VALUES('valid'); INSERT INTO child VALUES('valid')")


def test_index_rename_cannot_break_view_or_trigger_indexed_by_dependency(session):
    session.execute("CREATE INDEX required_index ON items(name); CREATE VIEW dependent AS SELECT * FROM items INDEXED BY required_index")
    snapshot = load_index(session, "main", "required_index")
    changed = deepcopy(snapshot.definition)
    changed.name = "renamed"
    with pytest.raises(SchemaError, match="no such index"):
        apply_plan(session, plan_index(session, snapshot, changed))
    assert independent(session, "SELECT count(*) FROM dependent") == [(2,)]


def test_schema_drift_reconnect_legacy_and_forged_plan_are_rejected(session):
    source = new_index(table="items")
    definition = IndexObjectDefinition("index_name", ["name"], table="items")
    plan = plan_index(session, source, definition)
    plan.steps.append(("DROP TABLE items", ()))
    with pytest.raises(SchemaConflictError, match="modified"):
        apply_plan(session, plan)
    with pytest.raises(SchemaConflictError, match="not produced"):
        apply_plan(session, ObjectPlan(source, definition, ["DROP TABLE items"], "", True))
    plan = plan_index(session, source, definition)
    session.execute("PRAGMA legacy_alter_table=ON")
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)
    plan = plan_index(session, source, definition)
    session.close(); session.connect()
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)


def test_snapshot_identity_and_raw_sql_cannot_bypass_structured_validation(session):
    session.execute("CREATE INDEX normal ON items(name)")
    snapshot = load_index(session, "main", "normal")
    changed = deepcopy(snapshot.definition)
    changed.raw_sql = "DROP TABLE items"
    with pytest.raises(SchemaError, match="original object identity or SQL"):
        plan_index(session, snapshot, changed)
    forged = deepcopy(snapshot)
    forged.definition.raw_sql = "CREATE INDEX normal ON items(category)"
    with pytest.raises(SchemaConflictError, match="snapshot was modified"):
        plan_index(session, forged, forged.definition)


def test_write_lock_schema_race_preserves_external_change_and_reports_conflict(session, monkeypatch):
    plan = plan_index(session, new_index(table="items"), IndexObjectDefinition("desired", ["name"], table="items"))
    query = session._query
    def racing(sql, params=None):
        if sql == "BEGIN IMMEDIATE":
            with closing(sqlite3.connect(session.config.database)) as other, other:
                other.execute("CREATE INDEX external ON items(category)")
        return query(sql, params)
    monkeypatch.setattr(session, "_query", racing)
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)
    assert any(row[1] == "external" for row in catalog(session))
    assert not any(row[1] == "desired" for row in catalog(session))
    assert not session.in_transaction


def test_cancel_between_drop_and_create_restores_original_index(session, monkeypatch):
    session.execute("CREATE INDEX original ON items(name)")
    snapshot = load_index(session, "main", "original")
    changed = deepcopy(snapshot.definition)
    changed.name = "new_name"
    plan = plan_index(session, snapshot, changed)
    query = session._query
    def cancel(sql, params=None):
        rows = query(sql, params)
        if sql.startswith("DROP INDEX"):
            session._cancel_requested.set()
        return rows
    monkeypatch.setattr(session, "_query", cancel)
    with pytest.raises(SchemaError, match="cancelled"):
        apply_plan(session, plan)
    assert load_index(session, "main", "original").ddl == snapshot.ddl
    assert not session.in_transaction


def test_attached_namespace_and_read_only_outer_transaction_guards(session, tmp_path):
    session._query("ATTACH DATABASE ? AS attached", (str(tmp_path / "attached.db"),))
    session.execute("CREATE TABLE attached.items(value TEXT); INSERT INTO attached.items VALUES('attached')")
    definition = IndexObjectDefinition("idx", ["value"], table="items")
    plan = plan_index(session, new_index("attached", "items"), definition)
    session.execute("BEGIN; INSERT INTO items VALUES(3,'outer','other')")
    with pytest.raises(SchemaError, match="current transaction"):
        apply_plan(session, plan)
    assert session.in_transaction
    session.execute("ROLLBACK")
    result = apply_plan(session, plan)
    assert result.schema == "attached" and result.definition.columns == ["value"]
    assert not any(row[1] == "idx" for row in catalog(session))
    snapshot = load_index(session, "attached", "idx")
    changed = deepcopy(snapshot.definition); changed.unique = True
    plan = plan_index(session, snapshot, changed)
    session.config.read_only = True
    with pytest.raises(Exception, match="read-only"):
        apply_plan(session, plan)


def test_temp_trigger_dependencies_are_explicitly_protected(session):
    session.execute("CREATE TEMP TRIGGER temporary AFTER INSERT ON main.items BEGIN SELECT 1; END")
    source = load_trigger(session, "temp", "temporary")
    assert source.read_only and source.issues
    with pytest.raises(SchemaError, match="Temporary triggers"):
        plan_index(session, new_index(table="items"), IndexObjectDefinition("idx", ["name"], table="items"))
