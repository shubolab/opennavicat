"""Native encrypted backend workflows against newly generated owned files."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import threading

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat import sqlite_cipher_provider as provider
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, copy_connection_config, with_sqlite_database_password
from opennavicat.sqlite_encryption_config import SQLITE_CIPHER_NAMES, resolve_sqlite_cipher, with_sqlite_encryption_password


LABELS = tuple(label for label in SQLITE_CIPHER_NAMES if label != "ascon128 legacy")


@pytest.fixture(autouse=True)
def native():
    pytest.importorskip("apsw", reason="The optional encrypted SQLite provider is not installed")
    return provider._provider()


def options(label="aes256cbc", page_size=0):
    settings = resolve_sqlite_cipher(label, page_size)
    return dict(cipher=settings.cipher, legacy=settings.legacy, page_size=settings.page_size)


def make_file(path, label="aes256cbc", *, key="generated-backend-key", page_size=0, encrypted=True):
    connection = (provider.connect(path, key=key, create=True, **options(label, page_size)) if encrypted
                  else sqlite3.connect(path, isolation_level=None))
    try:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY,label TEXT NOT NULL)").close()
        connection.executemany("INSERT INTO items VALUES (?,?)", [(1, "generated one"), (2, "generated two")]).close()
    finally:
        connection.close()
    return path


def config(path, label="aes256cbc", **changes):
    value = dict(id="generated-owner", name="Generated encrypted profile", database=str(path), sqlite_open_mode="existing",
                 sqlite_encryption_enabled=True, sqlite_cipher_name=label, sqlite_encryption_password="generated-backend-key")
    value.update(changes)
    return ConnectionConfig(**value)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("label", LABELS)
def test_native_backend_create_close_readonly_reopen_wrongkey_and_standard_rejection(tmp_path, label):
    path = tmp_path / "generated-new.sqlite"
    new = config(path, label, sqlite_open_mode="create")
    DatabaseSession.create_sqlite_database(new)
    assert path.stat().st_size > 0 and path.read_bytes()[:16] != b"SQLite format 3\0"
    session = DatabaseSession(config(path, label))
    try:
        assert session.execute("CREATE TABLE items(id INTEGER PRIMARY KEY,label TEXT)")[0].affected == 0
        assert session.import_rows("main", "items", ["id", "label"], [[1, "generated one"], [2, "generated two"]]) == 2
    finally:
        session.close()
    before = fingerprint(path)
    read = DatabaseSession(config(path, label, read_only=True))
    try:
        assert read.fetch_table("main", "items").rows == [[1, "generated one"], [2, "generated two"]]
        assert read.execute("PRAGMA quick_check")[0].rows == [["ok"]]
        with pytest.raises(DatabaseError):
            read.execute("INSERT INTO items VALUES (3,'denied')")
    finally:
        read.close()
    wrong = DatabaseSession(config(path, label, read_only=True, sqlite_encryption_password="generated-wrong-key"))
    with pytest.raises(DatabaseError) as caught:
        wrong.connect()
    assert caught.value.authentication_required and wrong.connection is None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as standard:
        with pytest.raises(sqlite3.DatabaseError):
            standard.execute("SELECT * FROM sqlite_schema").fetchall()
    assert fingerprint(path) == before


@pytest.mark.parametrize("label", LABELS)
def test_plain_main_and_encrypted_attachment_use_explicit_provider_and_bound_key(tmp_path, label):
    main = make_file(tmp_path / "generated-plain.sqlite", encrypted=False)
    aux = make_file(tmp_path / "generated-encrypted.sqlite", label, key="generated-aux-key")
    row = SQLiteDatabaseEntry("aux", '辅助";literal', str(aux), True)
    source = config(main, label, sqlite_encryption_enabled=False, sqlite_databases=(row,),
                    sqlite_database_passwords={"aux": "generated-aux-key"})
    before = fingerprint(main), fingerprint(aux)
    session = DatabaseSession(source)
    try:
        session.connect()
        assert isinstance(session.connection, provider.CipherConnection)
        # Setting transient cipher PRAGMAs materializes SQLite's temp namespace.
        assert session.schemas() == ["main", "temp", row.name]
        assert session.fetch_table(row.name, "items").rows == [[1, "generated one"], [2, "generated two"]]
    finally:
        session.close()
    assert (fingerprint(main), fingerprint(aux)) == before


def test_plain_only_keeps_standard_connection_and_never_loads_provider(tmp_path, monkeypatch):
    path = make_file(tmp_path / "generated-plain.sqlite", encrypted=False)
    def forbidden():
        pytest.fail("Plain backend loaded its optional encrypted provider")
    monkeypatch.setattr(provider, "_provider", forbidden)
    session = DatabaseSession(config(path, sqlite_encryption_enabled=False))
    try:
        session.connect()
        assert isinstance(session.connection, sqlite3.Connection)
        assert session.fetch_table("main", "items").rows[0] == [1, "generated one"]
    finally:
        session.close()


def test_keyed_main_attaches_plain_with_empty_bound_key_and_encrypted_with_distinct_key(tmp_path, monkeypatch):
    main = make_file(tmp_path / "generated-main.sqlite")
    plain = make_file(tmp_path / "generated-plain.sqlite", encrypted=False)
    encrypted = make_file(tmp_path / "generated-aux.sqlite", key="generated-distinct-aux-key")
    rows = (SQLiteDatabaseEntry("plain", "plain_aux", str(plain), False), SQLiteDatabaseEntry("encrypted", "encrypted_aux", str(encrypted), True))
    session = DatabaseSession(config(main, sqlite_databases=rows, sqlite_database_passwords={"encrypted": "generated-distinct-aux-key"}))
    original, attaches = provider.CipherConnection.execute, []
    def execute(connection, sql, parameters=()):
        if sql.startswith("ATTACH"):
            assert session.connection is None
            attaches.append((sql, parameters))
        return original(connection, sql, parameters)
    monkeypatch.setattr(provider.CipherConnection, "execute", execute)
    try:
        session.connect()
        assert [parameters[1] for _sql, parameters in attaches] == ["", "generated-distinct-aux-key"]
        assert all(" KEY ?" in sql for sql, _parameters in attaches)
        assert session.fetch_table("plain_aux", "items").rows[0] == [1, "generated one"]
        assert session.fetch_table("encrypted_aux", "items").rows[0] == [1, "generated one"]
    finally:
        session.close()


def test_wrong_attachment_key_closes_unpublished_candidate_and_preserves_all_files(tmp_path, monkeypatch):
    main = make_file(tmp_path / "generated-main.sqlite")
    aux = make_file(tmp_path / "generated-aux.sqlite", key="generated-aux-key")
    row = SQLiteDatabaseEntry("aux", "aux", str(aux), True)
    session = DatabaseSession(config(main, sqlite_databases=(row,), sqlite_database_passwords={"aux": "generated-wrong-key"}))
    original, closed = provider.CipherConnection.close, []
    def close(connection):
        original(connection)
        closed.append(connection)
    monkeypatch.setattr(provider.CipherConnection, "close", close)
    before = fingerprint(main), fingerprint(aux)
    with pytest.raises(DatabaseError):
        session.connect()
    assert len(closed) == 1 and closed[0]._closed and session.connection is None
    assert session._selected_schema is None and session._sqlite_runtime_config is None
    assert (fingerprint(main), fingerprint(aux)) == before


@pytest.mark.parametrize("encrypted_main", [False, True])
def test_readonly_encrypted_attachment_blocks_raw_and_grid_writes(tmp_path, encrypted_main):
    main = make_file(tmp_path / "generated-main.sqlite", encrypted=encrypted_main)
    aux = make_file(tmp_path / "generated-aux.sqlite", key="generated-aux-key")
    row = SQLiteDatabaseEntry("aux", "aux", str(aux), True)
    session = DatabaseSession(config(main, sqlite_encryption_enabled=encrypted_main, read_only=True,
                                     sqlite_databases=(row,), sqlite_database_passwords={"aux": "generated-aux-key"}))
    before = fingerprint(main), fingerprint(aux)
    try:
        assert session.fetch_table("aux", "items").rows[0] == [1, "generated one"]
        for namespace in ("main", "aux"):
            with pytest.raises(DatabaseError):
                session.execute(f"INSERT INTO {namespace}.items VALUES (3,'denied')")
            with pytest.raises(DatabaseError, match="read-only"):
                session.insert_row(namespace, "items", {"id": 3, "label": "denied"})
    finally:
        session.close()
    assert (fingerprint(main), fingerprint(aux)) == before


def test_initialization_cancellation_closes_candidate_before_publish_and_retry_succeeds(tmp_path, monkeypatch):
    path = make_file(tmp_path / "generated-main.sqlite")
    session = DatabaseSession(config(path))
    original, opened = provider.connect, []
    def connect(*args, **kwargs):
        candidate = original(*args, **kwargs)
        opened.append(candidate)
        if len(opened) == 1:
            session.cancel()
        return candidate
    monkeypatch.setattr(provider, "connect", connect)
    with pytest.raises(DatabaseError):
        session.connect()
    assert opened[0]._closed and session.connection is None and not session._cancel_requested.is_set()
    try:
        session.connect()
        assert session.connection is opened[1]
    finally:
        session.close()


@pytest.mark.parametrize("changes", [dict(sqlite_encryption_password=""), dict(sqlite_cipher_name="ascon128 legacy"),
                                     dict(sqlite_cipher_name="sqlcipher legacy v4", sqlite_cipher_page_size=512)])
def test_unsupported_or_missing_key_rejects_before_path_access(tmp_path, monkeypatch, changes):
    session = DatabaseSession(config(tmp_path / "generated-missing.sqlite", **changes))
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected encryption metadata touched a path")
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(DatabaseError):
        session.connect()
    assert session.connection is None


def test_create_plain_is_materialized_closed_and_exclusive(tmp_path):
    path = tmp_path / "generated-plain.sqlite"
    draft = config(path, sqlite_encryption_enabled=False, sqlite_open_mode="create")
    DatabaseSession.create_sqlite_database(draft)
    before = path.read_bytes()
    assert before[:16] == b"SQLite format 3\0"
    with pytest.raises(FileExistsError):
        DatabaseSession.create_sqlite_database(draft)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as opened:
        assert opened.execute("PRAGMA quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("encrypted", [False, True])
def test_failed_creation_removes_only_its_new_owned_file(tmp_path, monkeypatch, encrypted):
    path = tmp_path / "generated-failed.sqlite"
    draft = config(path, sqlite_encryption_enabled=encrypted, sqlite_open_mode="create")
    def fail(*args):
        raise RuntimeError("Generated initializer failure")
    monkeypatch.setattr(DatabaseSession, "_sqlite_initialize_candidate", fail)
    with pytest.raises(RuntimeError):
        DatabaseSession.create_sqlite_database(draft)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("encrypted", [False, True])
def test_cancelled_new_creation_closes_and_removes_only_its_owned_file(tmp_path, encrypted):
    path = tmp_path / "generated-cancelled.sqlite"
    calls = [0]
    def cancelled():
        calls[0] += 1
        return calls[0] >= 3
    with pytest.raises((DatabaseError, sqlite3.OperationalError)):
        DatabaseSession.create_sqlite_database(config(path, sqlite_encryption_enabled=encrypted, sqlite_open_mode="create"),
                                               cancellation_requested=cancelled)
    assert not list(tmp_path.iterdir())


def test_provider_absence_fails_without_plain_backend_fallback(tmp_path, monkeypatch):
    path = make_file(tmp_path / "generated-main.sqlite")
    before = fingerprint(path)
    def missing():
        raise provider.ProviderUnavailable("Generated unavailable optional provider")
    monkeypatch.setattr(provider, "_provider", missing)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("Encrypted backend fell back to plain SQLite"))
    session = DatabaseSession(config(path))
    with pytest.raises(DatabaseError, match="unavailable"):
        session.connect()
    assert session.connection is None and fingerprint(path) == before


def test_backend_errors_do_not_contain_secret_sql_or_provider_notes(tmp_path):
    path = make_file(tmp_path / "generated-main.sqlite")
    session = DatabaseSession(config(path))
    try:
        with pytest.raises(DatabaseError) as captured:
            session.execute("SELECT generated_sensitive_sql_marker")
        error = captured.value
        chain = []
        while error is not None:
            chain.append(str(error) + repr(getattr(error, "__notes__", None)))
            error = error.__cause__
        assert all("generated_sensitive_sql_marker" not in text and "generated-backend-key" not in text for text in chain)
    finally:
        session.close()


def test_create_cancel_checks_callable_and_preserves_external_replacement(tmp_path):
    path = tmp_path / "generated-cancelled.sqlite"
    draft = config(path, sqlite_open_mode="create")
    calls = [0]
    def cancelled():
        calls[0] += 1
        if calls[0] == 2:
            replacement = tmp_path / "generated-replacement.sqlite"
            replacement.write_bytes(b"generated external replacement")
            replacement.replace(path)
            return True
        return False
    with pytest.raises(DatabaseError, match="cancelled"):
        DatabaseSession.create_sqlite_database(draft, cancellation_requested=cancelled)
    assert path.read_bytes() == b"generated external replacement"


@pytest.mark.parametrize("encrypted", [False, True])
def test_create_factory_time_replacement_is_opened_without_create_and_never_written_or_deleted(tmp_path, monkeypatch, encrypted):
    path, replacement = tmp_path / "generated-reserved.sqlite", tmp_path / "generated-external.sqlite"
    make_file(replacement, encrypted=encrypted)
    external = (provider.connect(replacement, key="generated-backend-key", **options()) if encrypted
                else sqlite3.connect(replacement, isolation_level=None))
    external.execute("PRAGMA user_version=73").close()
    external.close()
    before = fingerprint(replacement)
    original, returned = DatabaseSession._sqlite_new_connection, []
    def open_replacement(snapshot, database, uri):
        assert snapshot.sqlite_open_mode == "existing" and database.endswith("?mode=rw")
        replacement.replace(path)
        candidate = original(snapshot, database, uri)
        returned.append(candidate)
        assert fingerprint(path) == before
        return candidate
    monkeypatch.setattr(DatabaseSession, "_sqlite_new_connection", staticmethod(open_replacement))
    with pytest.raises(DatabaseError, match="changed during creation"):
        DatabaseSession.create_sqlite_database(config(path, sqlite_encryption_enabled=encrypted, sqlite_open_mode="create"))
    assert fingerprint(path) == before and len(returned) == 1
    if encrypted:
        assert returned[0]._closed
    opened = (provider.connect(path, key="generated-backend-key", read_only=True, **options()) if encrypted
              else sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    try:
        assert opened.execute("PRAGMA user_version").fetchone() == (73,)
        assert opened.execute("SELECT * FROM items ORDER BY id").fetchall() == [(1, "generated one"), (2, "generated two")]
    finally:
        opened.close()


def test_create_refuses_preexisting_sidecars_without_touching_them(tmp_path):
    path = tmp_path / "generated-sidecar.sqlite"
    sidecar = Path(str(path) + "-journal")
    sidecar.write_bytes(b"generated preexisting sidecar")
    with pytest.raises(DatabaseError, match="sidecar"):
        DatabaseSession.create_sqlite_database(config(path, sqlite_open_mode="create"))
    assert not path.exists() and sidecar.read_bytes() == b"generated preexisting sidecar"


def test_backend_pk_conflict_import_failure_and_namespace_writes_remain_atomic(tmp_path):
    main = make_file(tmp_path / "generated-main.sqlite")
    aux = make_file(tmp_path / "generated-aux.sqlite", key="generated-aux-key")
    row = SQLiteDatabaseEntry("aux", "aux", str(aux), True)
    session = DatabaseSession(config(main, sqlite_databases=(row,), sqlite_database_passwords={"aux": "generated-aux-key"}))
    try:
        assert session.update_row("aux", "items", {"id": 1, "label": "generated one"}, {"label": "generated changed"}) == 1
        with pytest.raises(DatabaseError, match="changed"):
            session.update_row("aux", "items", {"id": 1, "label": "generated one"}, {"label": "stale"})
        with pytest.raises(DatabaseError):
            session.import_rows("aux", "items", ["id", "label"], [[3, "generated three"], [2, "duplicate"]])
        assert session.fetch_table("aux", "items").rows == [[1, "generated changed"], [2, "generated two"]]
        assert session.fetch_table("main", "items").rows == [[1, "generated one"], [2, "generated two"]]
        session.execute("CREATE TABLE no_key(label TEXT)")
        session.insert_row("main", "no_key", {"label": "generated"})
        with pytest.raises(DatabaseError, match="primary key"):
            session.delete_row("main", "no_key", {"label": "generated"})
    finally:
        session.close()


def test_cancel_during_native_query_reuses_published_cipher_session(tmp_path, monkeypatch):
    path = make_file(tmp_path / "generated-cancel.sqlite")
    session = DatabaseSession(config(path))
    session.connect()
    entered, errors = threading.Event(), []
    original = provider.CipherCursor.execute
    def execute(cursor, sql, parameters=()):
        if "WITH RECURSIVE" in sql:
            entered.set()
        return original(cursor, sql, parameters)
    monkeypatch.setattr(provider.CipherCursor, "execute", execute)
    def query():
        try:
            session.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n")
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=query)
    worker.start()
    try:
        assert entered.wait(3)
        session.cancel()
        worker.join(3)
        assert not worker.is_alive() and len(errors) == 1 and isinstance(errors[0], DatabaseError)
        assert session.execute("SELECT 7")[0].rows == [[7]]
    finally:
        if worker.is_alive():
            session.cancel()
            worker.join(3)
        session.close()


@pytest.mark.parametrize("outer", [False, True])
def test_between_row_import_cancel_rolls_back_batch_and_keeps_only_existing_outer_changes(tmp_path, monkeypatch, outer):
    path = make_file(tmp_path / "generated-atomic-cancel.sqlite")
    session = DatabaseSession(config(path))
    if outer:
        session.execute("BEGIN; INSERT INTO items VALUES (99,'generated outer change')")
    original = provider.CipherCursor._execute
    def execute(cursor, sql, parameters, *, require_dml):
        result = original(cursor, sql, parameters, require_dml=require_dml)
        if require_dml and parameters[0] == 3:
            session.cancel()
        return result
    monkeypatch.setattr(provider.CipherCursor, "_execute", execute)
    try:
        with pytest.raises(DatabaseError):
            session.import_rows("main", "items", ["id", "label"], [[3, "generated batch one"], [4, "generated batch two"]])
        assert session.in_transaction is outer
        assert session.fetch_table("main", "items").rows == ([[1, "generated one"], [2, "generated two"], [99, "generated outer change"]]
                                                           if outer else [[1, "generated one"], [2, "generated two"]])
        assert not session._active and not session._cancel_requested.is_set()
        if outer:
            session.execute("ROLLBACK")
        assert session.execute("SELECT 7")[0].rows == [[7]]
    finally:
        session.close()


def test_inflight_native_dml_interrupt_reports_actual_outer_rollback_without_replay(tmp_path):
    path = make_file(tmp_path / "generated-native-dml-cancel.sqlite")
    session = DatabaseSession(config(path))
    session.execute("CREATE TABLE generated_audit(total INTEGER)")
    session.execute("CREATE TRIGGER generated_slow AFTER INSERT ON items WHEN new.id=3 BEGIN "
        "INSERT INTO generated_audit SELECT sum(x) FROM (WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL "
        "SELECT x+1 FROM n WHERE x<1000000000) SELECT x FROM n); END;")
    session.execute("BEGIN; INSERT INTO items VALUES (99,'generated outer change')")
    connection = session.connection
    entered, errors = threading.Event(), []
    def native_progress():
        entered.set()
        return connection._native_progress()
    connection._raw.set_progress_handler(native_progress, 1000)
    def mutate():
        try:
            session.import_rows("main", "items", ["id", "label"], [[3, "generated slow batch"]])
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        assert entered.wait(3)
        assert session._active
        session.cancel()
        worker.join(3)
        assert not worker.is_alive() and len(errors) == 1
        assert isinstance(errors[0], DatabaseError) and "entire transaction" in str(errors[0])
        assert not session.in_transaction and not session._active and not session._cancel_requested.is_set()
        assert session.fetch_table("main", "items").rows == [[1, "generated one"], [2, "generated two"]]
        assert session.execute("SELECT * FROM generated_audit")[0].rows == []
        independent = provider.connect(path, key="generated-backend-key", read_only=True, **options())
        try:
            assert independent.execute("SELECT * FROM items ORDER BY id").fetchall() == [(1, "generated one"), (2, "generated two")]
            assert independent.execute("SELECT * FROM generated_audit").fetchall() == []
        finally:
            independent.close()
    finally:
        if worker.is_alive():
            session.cancel()
            worker.join(3)
        session.close()
