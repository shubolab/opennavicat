"""Behavioral database tests: mutations, transactions, bounds and SQL edge cases."""
from contextlib import closing
import sqlite3
import threading
import time

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession


@pytest.fixture
def session(tmp_path):
    s = DatabaseSession(ConnectionConfig("test", "Test", database=str(tmp_path / 'test.db')))
    s.connect()
    yield s
    s.close()


def test_script_bounded_results_and_partial_error(session):
    results = session.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT); INSERT INTO t VALUES (1, 'a;b'),(2, '100%'),(3, '_'); SELECT * FROM t;", limit=2)
    assert len(results) == 3
    assert results[-1].rows == [[1, "a;b"], [2, "100%"]]
    assert results[-1].truncated
    with pytest.raises(DatabaseError) as err:
        session.execute("INSERT INTO t VALUES (4,'ok'); SELECT no_such_column FROM t;")
    assert len(err.value.partial_results) == 1
    assert session.count_rows("main", "t") == 4  # SQL editor autocommit is explicit.


def test_schema_identifiers_search_and_paging(session):
    session.execute('CREATE TABLE "a""table" ("key" INTEGER PRIMARY KEY, "a""col" TEXT NOT NULL)')
    session.import_rows("main", 'a"table', ["key", 'a"col'], [[1, "100%"], [2, "x_y"], [3, "a'b"], [4, "x!y"]])
    assert session.schemas() == ["main"]
    assert session.objects("main") == [{"name": 'a"table', "kind": "table", "schema": "main"}]
    assert session.columns("main", 'a"table')[0]["pk"]
    assert "CREATE TABLE" in session.table_ddl("main", 'a"table')
    for search, expected in [("%", 1), ("_", 2), ("'", 3), ("!", 4)]:
        assert session.fetch_table("main", 'a"table', filter_text=search).rows[0][0] == expected
        assert session.count_rows("main", 'a"table', filter_text=search) == 1
    assert session.fetch_table("main", 'a"table', limit=2, offset=1, order_by="key", descending=True).rows == [[3, "a'b"], [2, "x_y"]]
    with pytest.raises(ValueError):
        session.fetch_table("main", 'a"table', order_by="key; DROP TABLE x")


def test_primary_key_mutations_are_atomic_and_optimistic(session):
    session.execute("CREATE TABLE t (a INTEGER, b INTEGER, text TEXT, PRIMARY KEY(a,b))")
    session.insert_row("main", "t", {"a": 1, "b": 2, "text": "before"})
    original = {"a": 1, "b": 2, "text": "before"}
    assert session.update_row("main", "t", original, {"text": None}) == 1
    with pytest.raises(DatabaseError, match="changed"):
        session.update_row("main", "t", original, {"text": "lost update"})
    with pytest.raises(DatabaseError, match="changed"):
        session.delete_row("main", "t", original)
    assert session.fetch_table("main", "t").rows == [[1, 2, None]]
    assert session.update_row("main", "t", {"a": 1, "b": 2, "text": None}, {"a": 5}) == 1
    assert session.delete_row("main", "t", {"a": 5, "b": 2, "text": None}) == 1
    session.execute("CREATE TABLE no_pk (x TEXT); INSERT INTO no_pk VALUES ('x')")
    with pytest.raises(DatabaseError, match="primary key"):
        session.delete_row("main", "no_pk", {"x": "x"})
    assert session.count_rows("main", "no_pk") == 1


def test_import_rollback_and_nested_transaction_preservation(session):
    session.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    with pytest.raises(DatabaseError):
        session.import_rows("main", "t", ["id", "value"], [[1, "first"], [2, None]])
    assert session.count_rows("main", "t") == 0
    session.execute("BEGIN; INSERT INTO t VALUES (3, 'outer')")
    assert session.in_transaction
    with pytest.raises(DatabaseError):
        session.import_rows("main", "t", ["id", "value"], [[4, "inner"], [3, "duplicate"]])
    assert session.in_transaction
    assert session.fetch_table("main", "t").rows == [[3, "outer"]]
    session.execute("ROLLBACK")
    assert session.count_rows("main", "t") == 0


def test_triggers_multi_statement_and_generated_columns(session):
    session.execute("""CREATE TABLE t (id INTEGER PRIMARY KEY, x INT, doubled INT GENERATED ALWAYS AS (x*2) VIRTUAL);
    CREATE TABLE audit (message TEXT);
    CREATE TRIGGER logged AFTER INSERT ON t BEGIN
      INSERT INTO audit VALUES ('first; entry');
      INSERT INTO audit VALUES ('second');
    END;
    INSERT INTO t (x) VALUES (3);""")
    assert session.count_rows("main", "audit") == 2
    assert session.fetch_table("main", "t").rows == [[1, 3, 6]]
    with pytest.raises(ValueError, match="Generated"):
        session.update_row("main", "t", {"id": 1, "x": 3, "doubled": 6}, {"doubled": 10})


def test_read_only_mode_and_filename_uri(tmp_path):
    import os
    path = tmp_path / ("strange # 空格.sqlite" if os.name == "nt" else "strange?#.sqlite")
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    s = DatabaseSession(ConnectionConfig("ro", "Read only", database=str(path), read_only=True))
    assert s.count_rows("main", "t") == 0
    with pytest.raises(DatabaseError, match="read-only|readonly"):
        s.insert_row("main", "t", {"id": 1})
    with pytest.raises(DatabaseError, match="readonly|read-only"):
        s.execute("INSERT INTO t VALUES (1)")
    s.close()


def test_cancel_long_query_and_reuse_session(session):
    errors = []
    def execute():
        try:
            session.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n")
        except DatabaseError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=execute)
    worker.start()
    deadline = time.monotonic() + 2
    while not session._active and time.monotonic() < deadline:
        time.sleep(.001)
    time.sleep(.02)
    session.cancel()
    worker.join(2)
    assert not worker.is_alive()
    assert errors
    assert session.execute("SELECT 42")[0].rows == [[42]]


def test_backup_is_consistent_and_rejects_overwrite(session, tmp_path):
    session.execute("CREATE TABLE t (id INTEGER PRIMARY KEY); INSERT INTO t VALUES (10)")
    path = tmp_path / "snapshot.sqlite"
    session.backup_to(str(path))
    with closing(sqlite3.connect(path)) as c, c:
        assert c.execute("SELECT id FROM t").fetchall() == [(10,)]
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(FileExistsError):
        session.backup_to(str(path))
    session.execute("BEGIN; INSERT INTO t VALUES (20)")
    with pytest.raises(DatabaseError, match="transaction"):
        session.backup_to(str(tmp_path / "unfinished.sqlite"))
    session.execute("ROLLBACK")


@pytest.fixture(params=["postgresql", "mysql"])
def external_session(request):
    """Opt in only against disposable local test servers, never user profiles."""
    import os
    import uuid
    env = "OPENNAVICAT_TEST_POSTGRES_PORT" if request.param == "postgresql" else "OPENNAVICAT_TEST_MYSQL_PORT"
    port = os.environ.get(env)
    if not port:
        pytest.skip(f"Set {env} for isolated driver integration")
    driver = request.param
    s = DatabaseSession(ConnectionConfig("integration", "Disposable integration", driver,
        "opennavicat_test", "127.0.0.1", int(port),
        "postgres" if driver == "postgresql" else "root", "opennavicat-test-only", "disable"))
    schema = "onv_test_" + uuid.uuid4().hex[:12]
    s.connect()
    s.execute(f"CREATE SCHEMA {s.quote_identifier(schema)}")
    try:
        yield s, schema
    finally:
        if s.in_transaction:
            s.execute("ROLLBACK")
        s.execute(f"DROP SCHEMA {s.quote_identifier(schema)}" + (" CASCADE" if driver == "postgresql" else ""))
        s.close()


def test_external_crud_search_types_and_nested_transactions(external_session):
    from datetime import date
    from decimal import Decimal
    s, schema = external_session
    table = 'table%s"`'
    q = s.qualified_name(schema, table)
    column = 'value%s"`'
    qc = s.quote_identifier(column)
    binary = "BYTEA" if s.config.driver == "postgresql" else "BLOB"
    s.execute(f"CREATE TABLE {q} (id INTEGER PRIMARY KEY, {qc} TEXT NOT NULL, amount DECIMAL(12,2), day DATE, payload {binary})")
    assert schema in s.schemas()
    assert s.objects(schema) == [{"name": table, "kind": "table", "schema": schema}]
    values = {"id": 1, column: "100%_!'quoted", "amount": Decimal("12.30"), "day": date(2026, 11, 10), "payload": b"\x00\xff"}
    assert s.insert_row(schema, table, values) == 1
    fetched = s.fetch_table(schema, table, filter_text="%_!")
    assert fetched.rows[0][0:4] == [1, values[column], Decimal("12.30"), date(2026, 11, 10)]
    assert bytes(fetched.rows[0][4]) == b"\x00\xff"
    assert s.count_rows(schema, table, filter_text="'quoted") == 1
    original = dict(zip(fetched.columns, fetched.rows[0]))
    assert s.update_row(schema, table, original, {column: "after"}) == 1
    with pytest.raises(DatabaseError, match="changed"):
        s.delete_row(schema, table, original)
    s.execute("BEGIN")
    with pytest.raises(DatabaseError):
        s.import_rows(schema, table, ["id", column], [[2, "first"], [1, "conflict"]])
    assert s.in_transaction
    assert s.count_rows(schema, table) == 1
    assert s.insert_row(schema, table, {"id": 3, column: "inside"}) == 1
    s.execute("ROLLBACK")
    assert s.count_rows(schema, table) == 1
    current = dict(zip(fetched.columns, s.fetch_table(schema, table).rows[0]))
    assert s.delete_row(schema, table, current) == 1
    assert s.count_rows(schema, table) == 0
    assert "CREATE TABLE" in s.table_ddl(schema, table)


def test_external_result_limit_and_cancellation(external_session):
    s, schema = external_session
    s.execute(f"CREATE TABLE {s.qualified_name(schema, 't')} (id INTEGER PRIMARY KEY)")
    s.import_rows(schema, "t", ["id"], [[i] for i in range(5)])
    result = s.execute(f"SELECT id FROM {s.qualified_name(schema, 't')} ORDER BY id", limit=2)[0]
    assert result.rows == [[0], [1]] and result.truncated
    errors = []
    def execute():
        try:
            s.execute("SELECT pg_sleep(15)" if s.config.driver == "postgresql" else "SELECT SLEEP(15)")
        except DatabaseError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=execute)
    worker.start()
    deadline = time.monotonic() + 2
    while not s._active and time.monotonic() < deadline:
        time.sleep(.001)
    time.sleep(.05)
    s.cancel()
    worker.join(3)
    assert not worker.is_alive()
    assert errors
    assert s.execute("SELECT 42")[0].rows == [[42]]


def test_external_json_array_and_read_only(external_session):
    s, schema = external_session
    q = s.qualified_name(schema, "json_data")
    s.execute(f"CREATE TABLE {q} (id INTEGER PRIMARY KEY, doc JSON)")
    assert s.insert_row(schema, "json_data", {"id": 1, "doc": {"hello": [1, None, "world"]}}) == 1
    fetched = s.fetch_table(schema, "json_data")
    original = dict(zip(fetched.columns, fetched.rows[0]))
    assert s.update_row(schema, "json_data", original, {"doc": {"updated": True}}) == 1
    assert s.import_rows(schema, "json_data", ["id", "doc"], [[2, [1, 2]], [3, None]]) == 2
    config = ConnectionConfig(**{**vars(s.config), "read_only": True})
    read_only = DatabaseSession(config)
    try:
        assert read_only.count_rows(schema, "json_data") == 3
        with pytest.raises(DatabaseError):
            read_only.execute(f"INSERT INTO {q} VALUES (99,NULL)")
    finally:
        read_only.close()


def test_external_mysql_tls_require_and_certificate_verification(external_session):
    s, schema = external_session
    if s.config.driver != "mysql":
        pytest.skip("This disposable PostgreSQL server has TLS disabled")
    tls = DatabaseSession(ConnectionConfig(**{**vars(s.config), "ssl_mode": "require"}))
    try:
        tls.connect()
        assert tls.connection._secure
        assert tls.execute("SELECT 42")[0].rows == [[42]]
    finally:
        tls.close()
    verified = DatabaseSession(ConnectionConfig(**{**vars(s.config), "ssl_mode": "verify-full"}))
    with pytest.raises(DatabaseError, match="CERTIFICATE_VERIFY_FAILED|certificate verify failed"):
        verified.connect()


def test_sqlite_indexes_and_composite_implicit_foreign_keys(session):
    session.execute('''CREATE TABLE parent (a INTEGER, b TEXT, PRIMARY KEY(a,b));
        CREATE TABLE child (id INTEGER PRIMARY KEY, a INTEGER, b TEXT,
          FOREIGN KEY(a,b) REFERENCES parent ON UPDATE CASCADE ON DELETE SET NULL);
        CREATE UNIQUE INDEX child_unique ON child(a,b);
        CREATE INDEX child_expression ON child(lower(b)) WHERE a IS NOT NULL;''')
    indexes = session.indexes("main", "child")
    primary = next(i for i in indexes if i["primary"])
    assert primary["columns"] == ["id"] and primary["unique"]
    unique = next(i for i in indexes if i["name"] == "child_unique")
    assert unique["columns"] == ["a", "b"] and unique["unique"] and not unique["primary"]
    expression = next(i for i in indexes if i["name"] == "child_expression")
    assert "lower(b)" in expression["definition"]
    fk = session.foreign_keys("main", "child")[0]
    assert fk["columns"] == ["a", "b"]
    assert fk["referenced_table"] == "parent"
    assert fk["referenced_columns"] == ["a", "b"]
    assert fk["on_update"] == "CASCADE" and fk["on_delete"] == "SET NULL"


def test_external_indexes_foreign_keys_and_schema_binding(external_session):
    s, schema = external_session
    parent = s.qualified_name(schema, "parent")
    child = s.qualified_name(schema, "child")
    s.execute(f'''CREATE TABLE {parent} (a INTEGER, b INTEGER, PRIMARY KEY(a,b));
        CREATE TABLE {child} (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER,
          CONSTRAINT child_parent_fk FOREIGN KEY(a,b) REFERENCES {parent}(a,b) ON UPDATE CASCADE ON DELETE SET NULL);
        CREATE UNIQUE INDEX child_unique ON {child}(a,b);''')
    indexes = s.indexes(schema, "child")
    primary = next(i for i in indexes if i["primary"])
    assert primary["columns"] == ["id"] and primary["unique"]
    unique = next(i for i in indexes if i["name"] == "child_unique")
    assert unique["columns"] == ["a", "b"] and unique["unique"]
    fk = s.foreign_keys(schema, "child")[0]
    assert fk["name"] == "child_parent_fk" and fk["columns"] == ["a", "b"]
    assert fk["referenced_schema"] == schema and fk["referenced_table"] == "parent"
    assert fk["referenced_columns"] == ["a", "b"]
    assert fk["on_update"] == "CASCADE" and fk["on_delete"] == "SET NULL"
    s.use_schema(schema)
    assert s.execute("SELECT COUNT(*) FROM child")[0].rows == [[0]]
    s.execute("BEGIN")
    s.use_schema(schema)  # no metadata roundtrip while in a transaction
    with pytest.raises(DatabaseError, match="transaction"):
        s.use_schema("public" if s.config.driver == "postgresql" else "information_schema")
    s.execute("ROLLBACK")
    assert s.execute("SELECT COUNT(*) FROM child")[0].rows == [[0]]


def test_sqlite_declared_json_preserves_python_json_values(session):
    session.execute("CREATE TABLE json_values (id INTEGER PRIMARY KEY, doc JSON)")
    values = [{"key": [1, True]}, ["x", 2], "plain string", True, 3.5]
    session.import_rows("main", "json_values", ["id", "doc"], [[i, value] for i, value in enumerate(values)])
    result = session.fetch_table("main", "json_values")
    assert [r[1] for r in result.rows] == values
    original = dict(zip(result.columns, result.rows[0]))
    session.update_row("main", "json_values", original, {"doc": "edited scalar"})
    assert session.fetch_table("main", "json_values").rows[0][1] == "edited scalar"


def test_external_json_scalars_are_json_not_sql_strings(external_session):
    s, schema = external_session
    s.execute(f"CREATE TABLE {s.qualified_name(schema, 'json_values')} (id INTEGER PRIMARY KEY, doc JSON)")
    values = [{"key": [1, True]}, ["x", 2], "plain string", True, 3.5]
    s.import_rows(schema, "json_values", ["id", "doc"], [[i, value] for i, value in enumerate(values)])
    result = s.fetch_table(schema, "json_values")
    assert [r[1] for r in result.rows] == values
    original = dict(zip(result.columns, result.rows[0]))
    s.update_row(schema, "json_values", original, {"doc": "edited scalar"})
    assert s.fetch_table(schema, "json_values").rows[0][1] == "edited scalar"
