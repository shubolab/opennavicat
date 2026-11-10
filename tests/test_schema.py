"""Real disposable SQLite designer saves, dependency retention, and rollback."""
from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.schema import (
    ColumnDefinition, ForeignKeyDefinition, IndexDefinition, IndexTerm, SchemaConflictError,
    SchemaError, TableDefinition, TableOptions, TriggerDefinition, UniqueKeyDefinition,
    apply_plan, load_table, new_table, plan_table,
)


@pytest.fixture
def session(tmp_path):
    value = DatabaseSession(ConnectionConfig("designer", "Designer", database=str(tmp_path / "design.db")))
    value.connect()
    yield value
    value.close()


def read_independently(session, sql):
    with closing(sqlite3.connect(session.config.database)) as connection:
        return connection.execute(sql).fetchall()


def edit(session, table="t", schema="main"):
    snapshot = load_table(session, schema, table)
    assert not snapshot.issues, snapshot.issues
    return snapshot, deepcopy(snapshot.definition)


def apply(session, snapshot, definition):
    plan = plan_table(session, snapshot, definition)
    return apply_plan(session, plan)


def test_new_table_all_editable_pages_and_actual_behavior(session):
    session.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE audit(message TEXT); INSERT INTO parent VALUES(1)")
    definition = TableDefinition('table "设计', columns=[
        ColumnDefinition("id", "INTEGER", nullable=False, pk_order=1, autoincrement=True),
        ColumnDefinition("parent", "INTEGER"),
        ColumnDefinition("name", "TEXT", nullable=False, default="'draft'", collation="NOCASE", notnull_conflict="FAIL"),
        ColumnDefinition("computed", "TEXT", generated_type="STORED", generated_expression="upper(name)"),
    ], indexes=[IndexDefinition("idx_name", ["name"], terms=[IndexTerm("name", "NOCASE", True)])],
        foreign_keys=[ForeignKeyDefinition("parent_fk", ["parent"], "parent", ["id"], on_delete="RESTRICT", deferrable=True, initially_deferred=True)],
        unique_keys=[UniqueKeyDefinition("unique_name", ["name"], "ABORT", terms=[IndexTerm("name")])],
        triggers=[TriggerDefinition("insert_log", "AFTER", "INSERT", "BEGIN INSERT INTO audit VALUES (NEW.name || '; saved'); END")],
        options=TableOptions(strict=True, autoincrement_seed=100))
    plan = plan_table(session, new_table(), definition)
    assert plan.mode == "create" and plan.changed
    assert "CREATE TRIGGER" in plan.ddl and "CREATE INDEX" in plan.ddl
    assert not any(x["name"] == definition.name for x in session.objects("main"))  # Preview is read-only.
    result = apply_plan(session, plan)
    assert not result.issues
    session.insert_row("main", definition.name, {"parent": 1, "name": "Alpha"})
    assert session.fetch_table("main", definition.name).rows == [[101, 1, "Alpha", "ALPHA"]]
    assert read_independently(session, "SELECT message FROM audit") == [("Alpha; saved",)]
    with pytest.raises(Exception):
        session.insert_row("main", definition.name, {"parent": 999, "name": "bad parent"})
    with pytest.raises(Exception):
        session.insert_row("main", definition.name, {"name": "alpha"})
    assert session._query("PRAGMA foreign_keys") == [(1,)]


def test_rebuild_preserves_complex_unedited_sql_and_data_without_firing_triggers(session):
    session.execute("""CREATE TABLE parent(a INTEGER,b TEXT, PRIMARY KEY(a,b));
        INSERT INTO parent VALUES(1,'one');
        CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT COLLATE NOCASE CONSTRAINT nn NOT NULL ON CONFLICT FAIL DEFAULT 'draft'
            CONSTRAINT nonempty CHECK(length(name)>0),
          a INTEGER,b TEXT, generated TEXT AS (name || ',;') STORED,
          CONSTRAINT uq UNIQUE(name DESC) ON CONFLICT IGNORE,
          CONSTRAINT parent_fk FOREIGN KEY(a,b) REFERENCES parent(a,b)
            ON UPDATE CASCADE ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT invariant CHECK(a IS NULL OR a>0)) STRICT;
        CREATE INDEX expression_partial ON t(lower(name) COLLATE NOCASE DESC,id ASC) WHERE a IS NOT NULL;
        CREATE TABLE audit(message TEXT);
        CREATE TRIGGER changed AFTER UPDATE OF name ON t WHEN NEW.name <> OLD.name
          BEGIN INSERT INTO audit VALUES ('one; ' || NEW.name); INSERT INTO audit VALUES ('two'); END;
        INSERT INTO t(name,a,b) VALUES('Alpha',1,'one');
        CREATE VIEW v AS SELECT id,name,generated FROM t;
        CREATE VIEW chained AS SELECT name FROM v;
        CREATE TRIGGER view_write INSTEAD OF INSERT ON v BEGIN INSERT INTO t(name) VALUES(NEW.name); END;""")
    old, definition = edit(session)
    definition.columns.append(ColumnDefinition("notes", default="'new'"))
    plan = plan_table(session, old, definition)
    assert plan.mode == "rebuild" and "DROP VIEW" in plan.sql
    result = apply_plan(session, plan)
    assert not result.issues
    ddl = result.ddl
    for clause in ("CHECK(length(name)>0)", "CONSTRAINT invariant CHECK(a IS NULL OR a>0)",
                   "COLLATE NOCASE", "ON CONFLICT FAIL", "DEFERRABLE INITIALLY DEFERRED", "STORED", "STRICT"):
        assert clause in ddl
    assert read_independently(session, "SELECT * FROM t") == [(1, "Alpha", 1, "one", "Alpha,;", "new")]
    assert read_independently(session, "SELECT * FROM audit") == []
    assert read_independently(session, "SELECT * FROM chained") == [("Alpha",)]
    assert "lower(name) COLLATE NOCASE DESC,id ASC" in session._query("SELECT sql FROM sqlite_schema WHERE name='expression_partial'")[0][0]
    session.execute("UPDATE t SET name='Beta' WHERE id=1; INSERT INTO v(name) VALUES('Gamma')")
    assert read_independently(session, "SELECT * FROM audit") == [("one; Beta",), ("two",)]
    assert read_independently(session, "SELECT name FROM t ORDER BY id") == [("Beta",), ("Gamma",)]
    with pytest.raises(Exception):
        session.execute("INSERT INTO t(name) VALUES('')")
    assert session._query("PRAGMA foreign_key_check") == []
    assert session._query("PRAGMA foreign_keys") == [(1,)]


def test_inline_unique_fk_and_named_constraints_remain_in_original_order(session):
    session.execute("""CREATE TABLE parent(id INTEGER PRIMARY KEY); INSERT INTO parent VALUES(1);
      CREATE TABLE t(id INTEGER PRIMARY KEY,
       a TEXT CONSTRAINT a_unique UNIQUE ON CONFLICT FAIL,
       b TEXT UNIQUE ON CONFLICT IGNORE,
       parent_id INTEGER CONSTRAINT parent_fk REFERENCES parent(id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
       value TEXT CONSTRAINT vd DEFAULT 'old' CONSTRAINT vn NOT NULL CONSTRAINT ck CHECK(value <> ''));
      INSERT INTO t VALUES(1,'first','second',1,'original');""")
    snapshot, definition = edit(session)
    definition.columns[-1].default = "'new'"
    definition.columns.append(ColumnDefinition("added"))
    result = apply(session, snapshot, definition)
    assert 'CONSTRAINT a_unique UNIQUE ON CONFLICT FAIL' in result.ddl
    assert result.ddl.index("a_unique") < result.ddl.index('"b"')
    assert "CONSTRAINT parent_fk REFERENCES parent(id)" in result.ddl
    assert 'CONSTRAINT "vd" DEFAULT' in result.ddl
    assert "CONSTRAINT vn NOT NULL" in result.ddl and "CONSTRAINT ck CHECK(value <> '')" in result.ddl
    assert read_independently(session, "SELECT * FROM t") == [(1, "first", "second", 1, "original", None)]


def test_edit_delete_inline_constraints_and_add_compound_sorted_unique(session):
    session.execute("""CREATE TABLE parent(id INTEGER PRIMARY KEY); INSERT INTO parent VALUES(1);
      CREATE TABLE t(id INTEGER PRIMARY KEY, a TEXT UNIQUE, parent_id INT REFERENCES parent(id));
      INSERT INTO t VALUES(1,'one',1)""")
    snapshot, definition = edit(session)
    definition.unique_keys.clear()
    definition.foreign_keys[0].on_delete = "CASCADE"
    definition.unique_keys.append(UniqueKeyDefinition("compound", ["a", "parent_id"], "FAIL",
        terms=[IndexTerm("a", "NOCASE", True), IndexTerm("parent_id")]))
    result = apply(session, snapshot, definition)
    assert len(result.definition.unique_keys) == 1
    assert result.definition.foreign_keys[0].on_delete == "CASCADE"
    session.execute("INSERT INTO t VALUES(2,'ONE',NULL); DELETE FROM parent")
    assert read_independently(session, "SELECT id FROM t") == [(2,)]


def test_auxiliary_indexes_triggers_are_transactional_without_table_rebuild(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,name TEXT); CREATE INDEX old_index ON t(name); INSERT INTO t VALUES(7,'before'); CREATE TABLE audit(value TEXT)")
    before_root = session._query("SELECT rootpage FROM sqlite_schema WHERE name='t'")
    snapshot, definition = edit(session)
    definition.indexes[0].name = "renamed_index"
    definition.indexes[0].unique = True
    definition.indexes[0].terms[0].descending = True
    definition.triggers.append(TriggerDefinition("tracked", "AFTER", "UPDATE", "BEGIN INSERT INTO audit VALUES(NEW.name); END"))
    plan = plan_table(session, snapshot, definition)
    assert plan.mode == "auxiliary" and "DROP TABLE" not in plan.sql
    result = apply_plan(session, plan)
    assert session._query("SELECT rootpage FROM sqlite_schema WHERE name='t'") == before_root
    assert result.definition.indexes[0].terms[0].descending
    session.execute("UPDATE t SET name='after' WHERE id=7")
    assert read_independently(session, "SELECT * FROM audit") == [("after",)]


def test_failure_in_late_auxiliary_statement_rolls_back_prior_index_drop(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,name TEXT); CREATE INDEX existing ON t(name)")
    snapshot, definition = edit(session)
    definition.indexes.clear()
    definition.triggers.append(TriggerDefinition("invalid_body", "AFTER", "INSERT", "BEGIN THIS IS NOT SQL; END"))
    plan = plan_table(session, snapshot, definition)
    with pytest.raises(SchemaError, match="rolled back"):
        apply_plan(session, plan)
    assert session.indexes("main", "t")[1]["name"] == "existing"
    assert session.table_ddl("main", "t") == snapshot.ddl
    assert not session.in_transaction


@pytest.mark.parametrize("failure", ["notnull", "unique", "generated", "view", "foreign_key"])
def test_failed_rebuild_rolls_back_all_schema_rows_and_dependencies(session, failure):
    session.execute("""CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT);
      INSERT INTO t VALUES(1,'same'),(2,'same'),(3,NULL);
      CREATE INDEX kept ON t(value);
      CREATE TABLE log(message TEXT);
      CREATE TRIGGER kept_trigger AFTER INSERT ON t BEGIN INSERT INTO log VALUES('trigger'); END;""")
    if failure == "view":
        session.execute("CREATE VIEW kept_view AS SELECT value FROM t")
    if failure == "foreign_key":
        session.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY); INSERT INTO parent VALUES(1)")
    snapshot, definition = edit(session)
    if failure == "notnull":
        definition.columns[1].nullable = False
    elif failure == "unique":
        definition.unique_keys.append(UniqueKeyDefinition("new_unique", ["value"]))
    elif failure == "generated":
        definition.columns.append(ColumnDefinition("broken", generated_type="STORED", generated_expression="absent + 1"))
    elif failure == "view":
        definition.columns.pop()
        definition.indexes.clear()
    else:
        definition.foreign_keys.append(ForeignKeyDefinition("new_fk", ["id"], "parent", ["id"]))
    plan = plan_table(session, snapshot, definition)
    original_catalog = session._query("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")
    with pytest.raises(SchemaError):
        apply_plan(session, plan)
    assert session._query("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name") == original_catalog
    assert read_independently(session, "SELECT * FROM t") == [(1, "same"), (2, "same"), (3, None)]
    assert not session.in_transaction
    assert session._query("PRAGMA foreign_keys") == [(1,)]
    assert not session._query("SELECT name FROM temp.sqlite_schema WHERE name LIKE '__opennavicat_%'")


def test_inbound_cascade_fk_does_not_delete_children_during_parent_rebuild(session):
    session.execute("""CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT); INSERT INTO t VALUES(1,'parent');
      CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES t(id) ON DELETE CASCADE);
      INSERT INTO child VALUES(1,1)""")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("new_column"))
    apply(session, snapshot, definition)
    assert read_independently(session, "SELECT * FROM child") == [(1, 1)]
    session.execute("DELETE FROM t WHERE id=1")
    assert read_independently(session, "SELECT * FROM child") == []


def test_native_column_and_table_rename_rewrites_dependencies_safely(session):
    session.execute("""CREATE TABLE t(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO t VALUES(1,'old');
      CREATE INDEX kept ON t(lower(name)) WHERE name IS NOT NULL;
      CREATE VIEW v AS SELECT name FROM t;
      CREATE TABLE child(pid INTEGER REFERENCES t(id)); INSERT INTO child VALUES(1);
      CREATE TABLE audit(value TEXT);
      CREATE TRIGGER tr AFTER UPDATE OF name ON t BEGIN INSERT INTO audit VALUES(NEW.name); END;""")
    snapshot, definition = edit(session)
    definition.name = 'new "name'
    definition.columns[1].name = "renamed"
    plan = plan_table(session, snapshot, definition)
    assert plan.mode == "rename" and "DROP TABLE" not in plan.sql
    assert '"renamed"' in plan.ddl and "lower(\"renamed\")" in plan.ddl
    result = apply_plan(session, plan)
    assert result.definition.columns[1].name == "renamed"
    assert read_independently(session, "SELECT * FROM v") == [("old",)]
    session.execute('UPDATE "new ""name" SET renamed=\'new\'')
    assert read_independently(session, "SELECT * FROM audit") == [("new",)]
    assert session.foreign_keys("main", "child")[0]["referenced_table"] == definition.name


def test_mixed_rename_and_other_changes_fail_closed_before_writing(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,name TEXT)")
    snapshot, definition = edit(session)
    definition.columns[1].name = "new_name"
    definition.columns[1].nullable = False
    with pytest.raises(SchemaError, match="renames separately"):
        plan_table(session, snapshot, definition)
    assert session.table_ddl("main", "t") == snapshot.ddl


@pytest.mark.parametrize("ddl", ["CREATE TABLE other(id INT)", "DROP TABLE t; CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)"])
def test_stale_catalog_even_recreated_same_ddl_rejected(session, ddl):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    session.execute(ddl)
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)
    assert [c["name"] for c in session.columns("main", "t")] == ["id", "value"]


def test_plan_cannot_be_retargeted_or_statements_changed(session):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    plan.steps.append(("DROP TABLE t", ()))
    with pytest.raises(SchemaConflictError, match="modified"):
        apply_plan(session, plan)
    plan = plan_table(session, snapshot, definition)
    session.close()
    session.connect()
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)


def test_outer_transaction_and_readonly_refused_without_committing_user_work(session):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    session.execute("BEGIN; INSERT INTO t VALUES(1,'user work')")
    with pytest.raises(SchemaError, match="current transaction"):
        apply_plan(session, plan)
    assert session.in_transaction
    session.execute("ROLLBACK")
    session.config.read_only = True
    with pytest.raises(Exception, match="read-only"):
        apply_plan(session, plan)
    assert read_independently(session, "SELECT * FROM t") == []


def test_unchanged_plan_has_no_mutation_or_format_churn(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT UNIQUE CHECK(length(value)>0))")
    snapshot, definition = edit(session)
    plan = plan_table(session, snapshot, definition)
    assert not plan.changed and plan.sql == "" and plan.ddl == snapshot.ddl
    assert apply_plan(session, plan).ddl == snapshot.ddl


def test_current_autoincrement_high_water_and_hidden_rowids_preserved(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT,value TEXT); INSERT INTO t VALUES(500,'deleted'); DELETE FROM t")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("new_column"))
    plan = plan_table(session, snapshot, definition)
    # Data changes between preview and apply are copied from the locked current
    # table. The current high-water mark must not revert to the preview's seed.
    session.execute("INSERT INTO t VALUES(900,'also deleted'); DELETE FROM t")
    apply_plan(session, plan)
    session.insert_row("main", "t", {"value": "next"})
    assert read_independently(session, "SELECT id FROM t") == [(901,)]
    session.execute("CREATE TABLE no_pk(value TEXT); INSERT INTO no_pk(rowid,value) VALUES(3,'three'),(999,'nine')")
    snapshot, definition = edit(session, "no_pk")
    definition.columns.append(ColumnDefinition("added"))
    apply(session, snapshot, definition)
    assert read_independently(session, "SELECT rowid,value FROM no_pk ORDER BY rowid") == [(3, "three"), (999, "nine")]


def test_reorder_without_rowid_composite_pk_and_data_types(session):
    session.execute("""CREATE TABLE t(a TEXT COLLATE NOCASE,b INTEGER,value BLOB,PRIMARY KEY(b DESC,a)) WITHOUT ROWID;
      INSERT INTO t VALUES('One',2,x'00ff'),('Two',3,NULL)""")
    snapshot, definition = edit(session)
    assert [(c.name, c.pk_order) for c in definition.columns] == [("a", 2), ("b", 1), ("value", 0)]
    definition.columns = [definition.columns[2], definition.columns[1], definition.columns[0]]
    result = apply(session, snapshot, definition)
    assert "PRIMARY KEY(b DESC,a)" in result.ddl and "WITHOUT ROWID" in result.ddl
    assert read_independently(session, "SELECT a,b,value FROM t ORDER BY b") == [("One", 2, b"\x00\xff"), ("Two", 3, None)]


def test_attached_database_targets_correct_namespace_and_restores_fk_off(session, tmp_path):
    session._query("ATTACH DATABASE ? AS attached", (str(tmp_path / "attached.db"),))
    session.execute("CREATE TABLE main.t(x TEXT); INSERT INTO main.t VALUES('main'); CREATE TABLE attached.t(x TEXT); INSERT INTO attached.t VALUES('attached'); PRAGMA foreign_keys=OFF")
    snapshot, definition = edit(session, schema="attached")
    definition.columns.append(ColumnDefinition("added", default="'new'"))
    definition.indexes.append(IndexDefinition("attached_idx", ["x"]))
    result = apply(session, snapshot, definition)
    assert result.schema == "attached"
    assert session.fetch_table("attached", "t").rows == [["attached", "new"]]
    assert session.fetch_table("main", "t").rows == [["main"]]
    assert session._query("PRAGMA foreign_keys") == [(0,)]


@pytest.mark.parametrize("expression", ["1); DROP TABLE t; --", "'unclosed", "(1", "1) , injected TEXT --"])
def test_expression_injection_and_broken_ddl_refused_before_apply(session, expression):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns[1].default = expression
    with pytest.raises(SchemaError):
        plan_table(session, snapshot, definition)
    assert session.table_ddl("main", "t") == snapshot.ddl


def test_unknown_virtual_declaration_protected_and_original_sql_immutable(session):
    session.execute("CREATE VIRTUAL TABLE t USING fts5(text)")
    snapshot = load_table(session, "main", "t")
    assert snapshot.issues
    definition = deepcopy(snapshot.definition)
    definition.columns.append(ColumnDefinition("unacceptable"))
    with pytest.raises(SchemaError, match="without loss"):
        plan_table(session, snapshot, definition)
    session.execute("CREATE TABLE ordinary(id INT CHECK(id>0))")
    snapshot, definition = edit(session, "ordinary")
    definition.columns[0].raw_sql = "id INT"
    with pytest.raises(SchemaError, match="original schema identity or SQL"):
        plan_table(session, snapshot, definition)


def test_changed_pragma_or_preview_binding_must_be_reviewed_again(session):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    session.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(SchemaConflictError, match="enforcement changed"):
        apply_plan(session, plan)
    assert session._query("PRAGMA foreign_keys") == [(0,)]


def test_cancel_rebuild_restores_original_schema_rows_and_pragma(session, monkeypatch):
    session.execute("CREATE TABLE t(id INT,value TEXT); INSERT INTO t VALUES(1,'retained')")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    actual_query = session._query
    def cancel_after_create(sql, params=None):
        result = actual_query(sql, params)
        if sql.startswith('CREATE TABLE "main"."__opennavicat_'):
            session._cancel_requested.set()
        return result
    monkeypatch.setattr(session, "_query", cancel_after_create)
    with pytest.raises(SchemaError, match="cancelled"):
        apply_plan(session, plan)
    assert session.table_ddl("main", "t") == snapshot.ddl
    assert read_independently(session, "SELECT * FROM t") == [(1, "retained")]
    assert not session.in_transaction and session._query("PRAGMA foreign_keys") == [(1,)]


@pytest.mark.parametrize("constraint", ["notnull_ignore", "unique_ignore"])
def test_conflict_ignore_never_silently_discards_copied_rows(session, constraint):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT); INSERT INTO t VALUES(1,'duplicate'),(2,'duplicate'),(3,NULL)")
    snapshot, definition = edit(session)
    if constraint == "notnull_ignore":
        definition.columns[1].nullable = False
        definition.columns[1].notnull_conflict = "IGNORE"
    else:
        definition.unique_keys.append(UniqueKeyDefinition("ignore_duplicates", ["value"], "IGNORE"))
    plan = plan_table(session, snapshot, definition)
    assert "COUNT(*)" in plan.sql
    with pytest.raises(SchemaError, match="discarded rows"):
        apply_plan(session, plan)
    assert read_independently(session, "SELECT * FROM t") == [(1, "duplicate"), (2, "duplicate"), (3, None)]
    assert session.table_ddl("main", "t") == snapshot.ddl
    assert not session.in_transaction and session._query("PRAGMA foreign_keys") == [(1,)]


def test_valid_sql_trigger_with_unresolved_body_rejected_before_commit(session):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.indexes.append(IndexDefinition("new_index", ["value"]))
    definition.triggers.append(TriggerDefinition("unknown_column", "AFTER", "INSERT", "BEGIN UPDATE t SET no_such_column=NEW.value; END"))
    plan = plan_table(session, snapshot, definition)
    with pytest.raises(SchemaError, match="no such column"):
        apply_plan(session, plan)
    assert not session.indexes("main", "t")
    assert not load_table(session, "main", "t").definition.triggers


def test_constraint_conflict_edit_preserves_integer_primary_key_desc_rowid_exception(session):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY DESC ON CONFLICT FAIL,value TEXT); INSERT INTO t(rowid,id,value) VALUES(20,5,'preserve')")
    snapshot, definition = edit(session)
    definition.options.pk_conflict = "REPLACE"
    result = apply(session, snapshot, definition)
    assert "PRIMARY KEY DESC ON CONFLICT REPLACE" in result.ddl
    assert read_independently(session, "SELECT rowid,id,value FROM t") == [(20, 5, "preserve")]
    session.execute("INSERT INTO t(id,value) VALUES(NULL,'null pk')")
    assert read_independently(session, "SELECT id FROM t WHERE value='null pk'") == [(None,)]


def test_named_composite_primary_key_sort_collation_survives_conflict_edit(session):
    session.execute("CREATE TABLE t(a TEXT,b INT,CONSTRAINT composite PRIMARY KEY(a COLLATE NOCASE DESC,b ASC) ON CONFLICT FAIL) WITHOUT ROWID; INSERT INTO t VALUES('one',1)")
    snapshot, definition = edit(session)
    definition.options.pk_conflict = "REPLACE"
    result = apply(session, snapshot, definition)
    assert 'CONSTRAINT "composite" PRIMARY KEY ("a" COLLATE "NOCASE" DESC, "b") ON CONFLICT REPLACE' in result.ddl
    assert read_independently(session, "SELECT * FROM t") == [("one", 1)]


def test_schema_changed_while_acquiring_write_lock_is_rejected(session, monkeypatch):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    actual_query = session._query
    def race_before_begin(sql, params=None):
        if sql == "BEGIN IMMEDIATE":
            with closing(sqlite3.connect(session.config.database)) as other, other:
                other.execute("CREATE INDEX external_change ON t(value)")
        return actual_query(sql, params)
    monkeypatch.setattr(session, "_query", race_before_begin)
    with pytest.raises(SchemaConflictError, match="before the write lock"):
        apply_plan(session, plan)
    assert [c["name"] for c in session.columns("main", "t")] == ["id", "value"]
    assert [x["name"] for x in session.indexes("main", "t")] == ["external_change"]
    assert session._query("PRAGMA foreign_keys") == [(1,)] and not session.in_transaction


def test_commit_failure_still_rolls_back_schema_and_data(session):
    session.execute("CREATE TABLE t(id INT,value TEXT); INSERT INTO t VALUES(1,'retained')")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    def forbid_commit(action, first, second, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK
    session.connection.set_authorizer(forbid_commit)
    try:
        with pytest.raises(SchemaError, match="rolled back"):
            apply_plan(session, plan)
    finally:
        session.connection.set_authorizer(None)
    assert session.table_ddl("main", "t") == snapshot.ddl
    assert read_independently(session, "SELECT * FROM t") == [(1, "retained")]
    assert not session.in_transaction and session._query("PRAGMA foreign_keys") == [(1,)]


def test_temporary_dependency_blocks_rebuild_and_new_dependency_invalidates_plan(session):
    session.execute("CREATE TABLE t(id INT,value TEXT)")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    plan = plan_table(session, snapshot, definition)
    session.execute("CREATE TEMP VIEW dependent AS SELECT * FROM main.t")
    with pytest.raises(SchemaConflictError):
        apply_plan(session, plan)
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    with pytest.raises(SchemaError, match="Temporary views or triggers"):
        plan_table(session, snapshot, definition)


def test_comments_quoted_commas_and_keywords_in_sql_are_not_reparsed_as_constraints(session):
    session.execute('''CREATE TABLE t("default" TEXT DEFAULT 'NOT NULL, CHECK(1); -- text' CHECK(length("default")>0),
      id INTEGER, /* comment , CHECK(x) */ payload TEXT DEFAULT ('it''s,a,b') -- final comment
    ); INSERT INTO t(id) VALUES(1)''')
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("more", "TEXT", default="'DEFAULT ;, text'"))
    result = apply(session, snapshot, definition)
    assert not result.issues
    assert read_independently(session, 'SELECT "default",payload,more FROM t') == [("NOT NULL, CHECK(1); -- text", "it's,a,b", "DEFAULT ;, text")]


@pytest.mark.parametrize("owner", ["t", "other_table"])
def test_trigger_update_of_column_names_follow_sqlite_case_semantics(session, owner):
    session.execute("CREATE TABLE t(id INTEGER PRIMARY KEY,value TEXT); CREATE TABLE other_table(value TEXT); CREATE TABLE audit(value TEXT)")
    session.execute(f"CREATE TRIGGER tracked AFTER UPDATE OF VALUE ON {owner} BEGIN INSERT INTO audit VALUES(NEW.value); END")
    snapshot, definition = edit(session)
    definition.columns.append(ColumnDefinition("added"))
    apply(session, snapshot, definition)
    session.execute(f"INSERT INTO {owner}(value) VALUES('before'); UPDATE {owner} SET value='after'")
    assert read_independently(session, "SELECT * FROM audit") == [("after",)]


def test_legacy_rename_mode_cannot_silently_break_an_empty_inbound_foreign_key(session):
    session.execute("PRAGMA foreign_keys=OFF; CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(pid INTEGER REFERENCES parent(id))")
    snapshot, definition = edit(session, "parent")
    definition.name = "renamed_parent"
    plan = plan_table(session, snapshot, definition)
    session.execute("PRAGMA legacy_alter_table=ON")
    with pytest.raises(SchemaConflictError, match="schema or connection changed"):
        apply_plan(session, plan)
    assert session.foreign_keys("main", "child")[0]["referenced_table"] == "parent"
    assert session._query("PRAGMA legacy_alter_table") == [(1,)]
    snapshot, definition = edit(session, "parent")
    definition.name = "renamed_parent"
    with pytest.raises(SchemaError, match="Disable legacy_alter_table"):
        plan_table(session, snapshot, definition)
    session.execute("PRAGMA legacy_alter_table=OFF")
    snapshot, definition = edit(session, "parent")
    definition.name = "renamed_parent"
    apply(session, snapshot, definition)
    assert session.foreign_keys("main", "child")[0]["referenced_table"] == "renamed_parent"
    session.execute("PRAGMA foreign_keys=ON; INSERT INTO renamed_parent VALUES(1); INSERT INTO child VALUES(1)")
    assert read_independently(session, "SELECT * FROM child") == [(1,)]


def test_inline_fk_set_null_default_are_actions_not_extra_column_constraints(session):
    session.execute("""CREATE TABLE parent(id INTEGER PRIMARY KEY); INSERT INTO parent VALUES(1);
      CREATE TABLE t(id INTEGER PRIMARY KEY,pid INTEGER DEFAULT NULL REFERENCES parent(id) ON DELETE SET NULL ON UPDATE SET DEFAULT);
      INSERT INTO t VALUES(1,1)""")
    snapshot, definition = edit(session)
    assert definition.columns[1].default == "NULL"
    assert definition.foreign_keys[0].on_delete == "SET NULL"
    assert definition.foreign_keys[0].on_update == "SET DEFAULT"
    definition.columns.append(ColumnDefinition("added"))
    apply(session, snapshot, definition)
    session.execute("UPDATE parent SET id=2 WHERE id=1")
    assert read_independently(session, "SELECT pid FROM t") == [(None,)]
    session.execute("UPDATE t SET pid=2; DELETE FROM parent WHERE id=2")
    assert read_independently(session, "SELECT pid FROM t") == [(None,)]


def test_named_single_table_primary_key_is_not_replaced_by_an_unnamed_inline_key(session):
    session.execute("CREATE TABLE t(id INTEGER,value TEXT,CONSTRAINT named_pk PRIMARY KEY(id DESC) ON CONFLICT FAIL); INSERT INTO t VALUES(7,'row')")
    snapshot, definition = edit(session)
    definition.options.pk_conflict = "REPLACE"
    result = apply(session, snapshot, definition)
    assert 'CONSTRAINT "named_pk" PRIMARY KEY ("id" DESC) ON CONFLICT REPLACE' in result.ddl
    assert read_independently(session, "SELECT rowid,id,value FROM t") == [(7, 7, "row")]
