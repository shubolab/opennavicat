"""Actual SQLite attachment initialization against new owned local files only."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry


def make_database(path, label, *, connector=None):
    with closing((connector or sqlite3.connect)(path)) as connection, connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
        connection.executemany("INSERT INTO items VALUES (?, ?)", [(1, label + "-1"), (2, label + "-2")])
    return path


@pytest.fixture
def files(tmp_path):
    return tuple(make_database(tmp_path / (name + ".sqlite"), name) for name in ("main", "aux", "second"))


def config(main, *attachments, **changes):
    return ConnectionConfig("owned", "Owned plain attachments", database=str(main), sqlite_open_mode="existing",
                            sqlite_databases=attachments, **changes)


def attached(path, name="aux", identifier="aux-row", encrypted=False):
    return SQLiteDatabaseEntry(identifier, name, str(path), encrypted)


def hashes(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


def track_candidates(monkeypatch, session, *, limit=None, before_connect=None, before_execute=None, before_close=None):
    original = sqlite3.connect
    candidates, calls = [], []
    class Candidate(sqlite3.Connection):
        closed = False
        def execute(self, sql, parameters=()):
            assert session.connection is None or session.connection is self
            calls.append((sql, parameters, session.connection is None))
            if before_execute:
                before_execute(self, sql, parameters)
            return super().execute(sql, parameters)
        def close(self):
            if before_close:
                before_close(self)
            super().close()
            self.closed = True
    def connect(*args, **kwargs):
        if before_connect:
            before_connect(args, kwargs)
        value = original(*args, **kwargs, factory=Candidate)
        if limit is not None:
            value.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, limit)
        candidates.append(value)
        return value
    monkeypatch.setattr(sqlite3, "connect", connect)
    return candidates, calls


def test_real_attachments_metadata_queries_and_mutations_route_to_the_selected_alias(files):
    main, aux, _second = files
    before_main = main.read_bytes()
    session = DatabaseSession(config(main, attached(aux)))
    try:
        session.connect()
        assert session._selected_schema == "main" and session.schemas() == ["main", "aux"]
        assert session.objects("aux") == [{"name": "items", "kind": "table", "schema": "aux"}]
        assert [row["name"] for row in session.columns("aux", "items")] == ["id", "label"]
        assert "CREATE TABLE" in session.table_ddl("aux", "items")
        result = session.execute("SELECT 'main', id, label FROM main.items UNION ALL SELECT 'aux', id, label FROM aux.items")
        assert result[0].rows == [["main", 1, "main-1"], ["main", 2, "main-2"],
                                 ["aux", 1, "aux-1"], ["aux", 2, "aux-2"]]
        session.use_schema("aux")
        assert session._selected_schema == "aux"
        original = {"id": 1, "label": "aux-1"}
        assert session.update_row("aux", "items", original, {"label": "aux-updated"}) == 1
        assert session.fetch_table("main", "items").rows[0] == [1, "main-1"]
        assert session.fetch_table("aux", "items").rows[0] == [1, "aux-updated"]
    finally:
        session.close()
    assert main.read_bytes() == before_main
    with closing(sqlite3.connect(aux)) as independent:
        assert independent.execute("SELECT label FROM items ORDER BY id").fetchall() == [("aux-updated",), ("aux-2",)]


def test_candidate_is_published_only_after_bounded_catalog_checks(files, monkeypatch):
    main, aux, second = files
    session = DatabaseSession(config(main, attached(aux), attached(second, "second", "second-row")))
    candidates, calls = track_candidates(monkeypatch, session)
    try:
        session.connect()
        assert session.connection is candidates[0]
        assert all(unpublished for _sql, _params, unpublished in calls)
        catalogs = [sql for sql, _params, _ in calls if "sqlite_schema" in sql]
        assert len(catalogs) == 3 and all(sql.startswith("SELECT 1 FROM ") and sql.endswith(" LIMIT 1") for sql in catalogs)
        attaches = [(sql, params) for sql, params, _ in calls if sql.startswith("ATTACH")]
        assert len(attaches) == 2 and all("ATTACH DATABASE ? AS " in sql and len(params) == 1 for sql, params in attaches)
    finally:
        session.close()


def test_unicode_and_sql_injection_shaped_aliases_are_literal_identifiers(tmp_path):
    main = make_database(tmp_path / "main.sqlite", "main")
    filename = "strange # % 空格.sqlite" if os.name == "nt" else "strange ? # % 空格.sqlite"
    aux = make_database(tmp_path / filename, "aux")
    alias = '辅助"; CREATE TABLE injected(value); --'
    before = hashes(tmp_path)
    session = DatabaseSession(config(main, attached(aux, alias)))
    try:
        session.connect()
        assert alias in session.schemas()
        assert session.count_rows(alias, "items") == 2
        assert session.fetch_table(alias, "items").rows[0] == [1, "aux-1"]
        assert not session._query("SELECT name FROM main.sqlite_schema WHERE name='injected'")
    finally:
        session.close()
    assert hashes(tmp_path) == before


def test_ascii_only_alias_comparison_does_not_fold_distinct_unicode_names(files):
    main, aux, second = files
    session = DatabaseSession(config(main, attached(aux, "Ä", "upper"), attached(second, "ä", "lower")))
    try:
        assert session.fetch_table("Ä", "items").rows[0][1] == "aux-1"
        assert session.fetch_table("ä", "items").rows[0][1] == "second-1"
    finally:
        session.close()


@pytest.mark.parametrize("names", [("main",), ("MAIN",), ("temp",), ("Temp",), ("",), ("bad\0alias",), ("AUX", "aux")])
def test_invalid_or_reserved_aliases_are_rejected_before_database_open(files, monkeypatch, names):
    main, aux, second = files
    rows = tuple(attached(path, name, str(index)) for index, (path, name) in enumerate(zip((aux, second), names)))
    session = DatabaseSession(config(main, *rows))
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid alias opened a SQLite database")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(DatabaseError, match="alias"):
        session.connect()
    assert session.connection is None and session._selected_schema is None


def test_encrypted_metadata_rejects_before_any_database_path_access(tmp_path, monkeypatch):
    session = DatabaseSession(config(tmp_path / "missing-main.sqlite", attached(tmp_path / "missing-aux.sqlite", encrypted=True)))
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported encryption touched a database path")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "is_file", forbidden)
    with pytest.raises(DatabaseError, match="encryption provider"):
        session.connect()
    assert session.connection is None and not list(tmp_path.iterdir())


@pytest.mark.parametrize("field", ["username", "password"])
@pytest.mark.parametrize("mode,with_attachment", [("existing", False), ("legacy", True)])
def test_unimplemented_authentication_is_not_silently_reported_success(files, monkeypatch, field, mode, with_attachment):
    main, aux, _second = files
    value = config(main, *((attached(aux),) if with_attachment else ()), **{field: "owned-auth-value"})
    value.sqlite_open_mode = mode
    session = DatabaseSession(value)
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported authentication opened a SQLite database")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(DatabaseError, match="authentication requires"):
        session.connect()
    assert session.connection is None


def test_legacy_creation_and_memory_behavior_remain_explicit(tmp_path):
    path = tmp_path / "legacy-created.sqlite"
    session = DatabaseSession(ConnectionConfig("legacy", "Legacy", database=str(path), username="legacy", password="legacy"))
    try:
        session.execute("CREATE TABLE legacy(id INTEGER)")
        assert path.exists() and session._selected_schema == "main"
    finally:
        session.close()
    for mode in ("legacy", "existing", "create"):
        memory = DatabaseSession(ConnectionConfig("memory", "Memory", database=":memory:", sqlite_open_mode=mode))
        try:
            assert memory.execute("SELECT 1")[0].rows == [[1]]
        finally:
            memory.close()


def test_existing_mode_never_creates_a_missing_main_even_if_it_disappears_at_open(files, monkeypatch):
    main, _aux, _second = files
    session = DatabaseSession(config(main))
    def disappear(_args, _kwargs):
        main.unlink()
    candidates, _calls = track_candidates(monkeypatch, session, before_connect=disappear)
    with pytest.raises(DatabaseError, match="open database"):
        session.connect()
    assert not main.exists() and not candidates and session.connection is None


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_corrupt_second_attachment_never_publishes_a_partial_session(files, monkeypatch, missing):
    main, aux, second = files
    native_connect = sqlite3.connect
    if missing:
        second.unlink()
    else:
        second.write_bytes(b"owned deliberately corrupt SQLite fixture")
    before = hashes(main.parent)
    session = DatabaseSession(config(main, attached(aux), attached(second, "second", "second-row")))
    candidates, _calls = track_candidates(monkeypatch, session)
    with pytest.raises(DatabaseError):
        session.connect()
    assert session.connection is None and session._selected_schema is None and hashes(main.parent) == before
    for candidate in candidates:
        assert candidate.closed
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            candidate.execute("SELECT 1")
    if missing:
        assert not second.exists()
    else:
        assert second.read_bytes() == b"owned deliberately corrupt SQLite fixture"
        second.unlink()
    make_database(second, "repaired", connector=native_connect)
    try:
        session.connect()
        assert session._selected_schema == "main" and session.count_rows("second", "items") == 2
        assert session.fetch_table("second", "items").rows[0][1] == "repaired-1"
        assert all(candidate.closed for candidate in candidates[:-1])
    finally:
        session.close()


def test_real_engine_attachment_limit_is_checked_before_any_attach(files, monkeypatch):
    main, aux, second = files
    before = hashes(main.parent)
    session = DatabaseSession(config(main, attached(aux), attached(second, "second", "second-row")))
    candidates, calls = track_candidates(monkeypatch, session, limit=1)
    with pytest.raises(DatabaseError, match="attachment limit"):
        session.connect()
    assert len(candidates) == 1 and candidates[0].closed
    assert not any(sql.startswith("ATTACH") for sql, _params, _unpublished in calls)
    assert session.connection is None and hashes(main.parent) == before


def test_readonly_applies_to_main_and_every_attachment_even_without_query_only(files):
    main, aux, _second = files
    before = hashes(main.parent)
    session = DatabaseSession(config(main, attached(aux), read_only=True))
    try:
        session.connect()
        assert session.fetch_table("aux", "items").rows[0] == [1, "aux-1"]
        for schema in ("main", "aux"):
            with pytest.raises(DatabaseError, match="read-only"):
                session.update_row(schema, "items", {"id": 1, "label": schema + "-1"}, {"label": "blocked"})
        with closing(session.connection.execute("PRAGMA query_only = OFF")):
            pass
        for schema in ("main", "aux"):
            with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
                session.connection.execute(f"UPDATE {session.quote_identifier(schema)}.items SET label=?", ("blocked",))
    finally:
        session.close()
    assert hashes(main.parent) == before


def test_readonly_create_can_open_an_existing_file_but_cannot_create_a_missing_one(files):
    main, _aux, _second = files
    value = config(main, read_only=True)
    value.sqlite_open_mode = "create"
    session = DatabaseSession(value)
    try:
        assert session.count_rows("main", "items") == 2
    finally:
        session.close()
    value.database = str(main.parent / "read-only-missing.sqlite")
    with pytest.raises(DatabaseError, match="cannot create"):
        DatabaseSession(value).connect()
    assert not Path(value.database).exists()


def test_explicit_create_failure_retains_the_requested_new_main_file(tmp_path):
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"owned corrupt database")
    main = tmp_path / "explicit-new.sqlite"
    value = config(main, attached(corrupt))
    value.sqlite_open_mode = "create"
    session = DatabaseSession(value)
    with pytest.raises(DatabaseError):
        session.connect()
    assert session.connection is None and session._selected_schema is None
    assert main.exists()  # Explicit creation is not a cross-file transaction.
    assert corrupt.read_bytes() == b"owned corrupt database"


def test_old_persistent_metadata_migrates_to_existing_without_filesystem_guessing(tmp_path, monkeypatch):
    from opennavicat.sqlite_database_config import connection_from_metadata
    from opennavicat.storage import Store
    import json

    missing = tmp_path / "lost-original.sqlite"
    record = {"id": "old", "name": "Old owned profile", "database": str(missing)}
    with monkeypatch.context() as guard:
        guard.setattr(Path, "is_file", lambda *args: pytest.fail("Metadata decoder inspected the filesystem"))
        guard.setattr(Path, "exists", lambda *args: pytest.fail("Metadata decoder inspected the filesystem"))
        guard.setattr(Path, "resolve", lambda *args, **kwargs: pytest.fail("Metadata decoder inspected the filesystem"))
        assert connection_from_metadata(record).sqlite_open_mode == "existing"
    workspace = tmp_path / "workspace.json"
    workspace.write_text(json.dumps({"connections": [record]}))
    saved = workspace.read_bytes()
    store = Store(tmp_path)
    old = store.connections()[0]
    assert old.sqlite_open_mode == "existing" and workspace.read_bytes() == saved
    with pytest.raises(DatabaseError, match="open database"):
        DatabaseSession(old).connect()
    assert not missing.exists()
    for mode in ("legacy", "create"):
        restored = connection_from_metadata({**record, "sqlite_open_mode": mode})
        assert restored.sqlite_open_mode == mode
    assert ConnectionConfig("direct", "Direct API", database=str(missing)).sqlite_open_mode == "legacy"


def test_missing_attachment_at_attach_time_is_not_recreated(files, monkeypatch):
    main, aux, second = files
    before_main, before_aux = main.read_bytes(), aux.read_bytes()
    removed = False
    def disappear(_candidate, sql, parameters):
        nonlocal removed
        if sql.startswith("ATTACH") and 'AS "second"' in sql and not removed:
            second.unlink()
            removed = True
    session = DatabaseSession(config(main, attached(aux), attached(second, "second", "second-row")))
    candidates, calls = track_candidates(monkeypatch, session, before_execute=disappear)
    with pytest.raises(DatabaseError, match="open database"):
        session.connect()
    assert removed and not second.exists()
    assert len(candidates) == 1 and candidates[0].closed
    assert session.connection is None and session._selected_schema is None
    assert main.read_bytes() == before_main and aux.read_bytes() == before_aux
    assert len([sql for sql, _params, _ in calls if sql.startswith("ATTACH")]) == 2


def test_pending_initialization_cancel_does_not_open_or_create_a_database(tmp_path, monkeypatch):
    path = tmp_path / "cancelled-create.sqlite"
    value = config(path)
    value.sqlite_open_mode = "create"
    session = DatabaseSession(value)
    session.cancel()
    session.close()  # There was no published handle whose cancel could be retired.
    assert session._cancel_requested.is_set()
    with monkeypatch.context() as guard:
        guard.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("Cancelled request opened a database"))
        with pytest.raises(DatabaseError, match="cancelled"):
            session.connect()
    assert not path.exists() and session.connection is None
    try:
        session.connect()
        assert path.exists() and session._selected_schema == "main"
    finally:
        session.close()


def test_cancel_during_unpublished_candidate_initialization_closes_it_and_allows_retry(files, monkeypatch):
    main, aux, _second = files
    session = DatabaseSession(config(main, attached(aux)))
    cancelled = False
    def cancel_before_attach(_candidate, sql, _parameters):
        nonlocal cancelled
        if sql.startswith("ATTACH") and not cancelled:
            assert session.connection is None
            session.cancel()
            cancelled = True
    candidates, _calls = track_candidates(monkeypatch, session, before_execute=cancel_before_attach)
    with pytest.raises(DatabaseError, match="cancelled"):
        session.connect()
    assert cancelled and candidates[0].closed and session.connection is None
    try:
        session.connect()
        assert session.count_rows("aux", "items") == 2
    finally:
        session.close()


@pytest.mark.parametrize("failure", ["cancel", "catalog"])
def test_new_cancel_during_candidate_cleanup_is_not_consumed_with_the_failed_request(files, monkeypatch, failure):
    main, aux, _second = files
    if failure == "catalog":
        aux.write_bytes(b"owned corrupt candidate cleanup fixture")
    session = DatabaseSession(config(main, attached(aux)))
    initialized_cancel = False
    cleanup_cancel = False
    def cancel_during_initialization(_candidate, sql, _parameters):
        nonlocal initialized_cancel
        if failure == "cancel" and sql.startswith("ATTACH") and not initialized_cancel:
            session.cancel()
            initialized_cancel = True
    def cancel_during_cleanup(_candidate):
        nonlocal cleanup_cancel
        if not cleanup_cancel:
            session.cancel()
            cleanup_cancel = True
    candidates, _calls = track_candidates(monkeypatch, session, before_execute=cancel_during_initialization,
                                          before_close=cancel_during_cleanup)
    with pytest.raises(DatabaseError):
        session.connect()
    assert cleanup_cancel and candidates[0].closed and session._cancel_requested.is_set()
    assert session.connection is None and session._selected_schema is None
    count = len(candidates)
    with pytest.raises(DatabaseError, match="cancelled"):
        session.connect()
    assert len(candidates) == count and not session._cancel_requested.is_set()
    if failure == "cancel":
        try:
            session.connect()
            assert session.count_rows("aux", "items") == 2
        finally:
            session.close()


def test_new_cancel_while_closing_a_published_handle_stays_pending(files, monkeypatch):
    main, aux, _second = files
    session = DatabaseSession(config(main, attached(aux)))
    cleanup_cancel = False
    def cancel_during_cleanup(_candidate):
        nonlocal cleanup_cancel
        if not cleanup_cancel:
            session.cancel()
            cleanup_cancel = True
    candidates, _calls = track_candidates(monkeypatch, session, before_close=cancel_during_cleanup)
    session.connect()
    session.cancel()  # Existing handle's old, idle cancellation.
    session.close()
    assert candidates[0].closed and cleanup_cancel and session._cancel_requested.is_set()
    with pytest.raises(DatabaseError, match="cancelled"):
        session.connect()
    assert len(candidates) == 1 and not session._cancel_requested.is_set()
    try:
        session.connect()
        assert session.count_rows("aux", "items") == 2
    finally:
        session.close()


def test_cancelled_query_then_close_and_reconnect_does_not_keep_old_interrupt_state(files):
    main, aux, _second = files
    before = hashes(main.parent)
    session = DatabaseSession(config(main, attached(aux)))
    session.connect()
    old_connection, errors = session.connection, []
    def query():
        try:
            session.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n")
        except DatabaseError as exc:
            errors.append(exc)
    worker = threading.Thread(target=query)
    try:
        worker.start()
        deadline = time.monotonic() + 2
        while not session._active and time.monotonic() < deadline:
            time.sleep(.001)
        assert session._active
        session.cancel()
        worker.join(2)
        assert not worker.is_alive() and errors
        session.cancel()  # A late old-query cancellation while its handle is idle.
        assert session._cancel_requested.is_set()
        session.close()
        assert not session._cancel_requested.is_set()
        session.connect()
        assert session.connection is not old_connection and session._selected_schema == "main"
        assert session.fetch_table("aux", "items").rows[0][1] == "aux-1"
    finally:
        if worker.is_alive():
            session.cancel()
            worker.join(2)
        session.close()
    assert not worker.is_alive() and hashes(main.parent) == before


def test_modern_main_treats_uri_characters_as_filename_text(tmp_path):
    filename = "main # % 空格.sqlite" if os.name == "nt" else "main?mode=ro#% 空格.sqlite"
    main = make_database(tmp_path / filename, "literal")
    session = DatabaseSession(config(main))
    try:
        session.insert_row("main", "items", {"id": 3, "label": "literal-path-write"})
    finally:
        session.close()
    assert list(tmp_path.iterdir()) == [main]
    with closing(sqlite3.connect(main)) as independent:
        assert independent.execute("SELECT label FROM items WHERE id=3").fetchone() == ("literal-path-write",)
    victim = tmp_path / "must-not-be-created.sqlite"
    injected = config("file:" + str(victim) + "?mode=rwc")
    with pytest.raises(DatabaseError):
        DatabaseSession(injected).connect()
    assert not victim.exists() and list(tmp_path.iterdir()) == [main]


def test_snapshot_validation_rejects_postconstruction_metadata_injection_before_open(files, monkeypatch):
    main, aux, _second = files
    value = config(main)
    value.sqlite_databases = [{"id": "row", "name": "aux", "database": str(aux),
                              "encrypted": False, "password": "private-injected-value"}]
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("Invalid metadata opened a database"))
    with pytest.raises(DatabaseError) as captured:
        DatabaseSession(value).connect()
    assert "private-injected-value" not in repr(captured.value)


def test_execution_uses_the_captured_attachment_plan_when_the_source_config_changes(files, monkeypatch):
    main, aux, second = files
    value = config(main, attached(aux))
    session = DatabaseSession(value)
    def mutate_source(_args, _kwargs):
        value.sqlite_databases = (attached(second, "changed-after-snapshot", "other-row"),)
    _candidates, _calls = track_candidates(monkeypatch, session, before_connect=mutate_source)
    try:
        session.connect()
        assert session.schemas() == ["main", "aux"]
        assert session.fetch_table("aux", "items").rows[0][1] == "aux-1"
    finally:
        session.close()


def test_explicit_create_with_a_valid_attachment_initializes_both_namespaces(tmp_path):
    aux = make_database(tmp_path / "aux.sqlite", "aux")
    main = tmp_path / "new-main.sqlite"
    value = config(main, attached(aux))
    value.sqlite_open_mode = "create"
    session = DatabaseSession(value)
    try:
        session.execute("CREATE TABLE main.created(id INTEGER PRIMARY KEY); INSERT INTO main.created VALUES(7)")
        assert session.count_rows("aux", "items") == 2 and session.count_rows("main", "created") == 1
    finally:
        session.close()
    with closing(sqlite3.connect(main)) as independent:
        assert independent.execute("SELECT id FROM created").fetchall() == [(7,)]


def test_explicit_memory_main_can_attach_an_existing_file(files):
    _main, aux, _second = files
    before = aux.read_bytes()
    session = DatabaseSession(config(":memory:", attached(aux)))
    try:
        session.execute("CREATE TABLE main.ephemeral(id INTEGER)")
        assert session.schemas() == ["main", "aux"] and session.count_rows("aux", "items") == 2
        assert session._query("PRAGMA database_list")[0][2] == ""
    finally:
        session.close()
    assert aux.read_bytes() == before


def test_corrupt_main_catalog_cannot_publish_an_unusable_connection(tmp_path, monkeypatch):
    main = tmp_path / "corrupt-main.sqlite"
    main.write_bytes(b"owned corrupt main SQLite fixture")
    before = main.read_bytes()
    session = DatabaseSession(config(main))
    candidates, _calls = track_candidates(monkeypatch, session)
    with pytest.raises(DatabaseError):
        session.connect()
    assert len(candidates) == 1 and candidates[0].closed
    assert session.connection is None and session._selected_schema is None and main.read_bytes() == before


def test_empty_modern_filename_is_not_silently_changed_to_memory():
    for mode in ("existing", "create"):
        value = config("")
        value.sqlite_open_mode = mode
        session = DatabaseSession(value)
        with pytest.raises(DatabaseError, match="filename is required"):
            session.connect()
        assert session.connection is None


@pytest.mark.parametrize("new_cancel_during_cleanup", [False, True])
def test_cancel_after_final_candidate_checkpoint_cannot_publish(files, monkeypatch, new_cancel_during_cleanup):
    main, aux, _second = files
    before = hashes(main.parent)
    session = DatabaseSession(config(main, attached(aux)))
    candidate_ready = threading.Event()
    release_publication = threading.Event()
    cleanup_cancel = False
    native_aux_rows, errors = [], []
    original_initialize = session._sqlite_initialize_candidate
    def pause_after_final_checkpoint(connection, *args):
        original_initialize(connection, *args)
        assert isinstance(connection, sqlite3.Connection)
        with closing(connection.execute("SELECT id, label FROM aux.items ORDER BY id")) as cursor:
            native_aux_rows.extend(cursor.fetchall())
        candidate_ready.set()
        assert release_publication.wait(3), "Owned candidate publication barrier was not released"
    def cancel_during_cleanup(_candidate):
        nonlocal cleanup_cancel
        if new_cancel_during_cleanup and not cleanup_cancel:
            session.cancel()
            cleanup_cancel = True
    monkeypatch.setattr(session, "_sqlite_initialize_candidate", pause_after_final_checkpoint)
    candidates, _calls = track_candidates(monkeypatch, session, before_close=cancel_during_cleanup)
    def connect():
        try:
            session.connect()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=connect)
    try:
        worker.start()
        assert candidate_ready.wait(3)
        assert native_aux_rows == [(1, "aux-1"), (2, "aux-2")]
        assert session.connection is None and session._selected_schema is None
        session.cancel()  # Arrives after the helper's final checkpoint, before publication.
        cancelled_generation = session._cancel_generation
        release_publication.set()
        worker.join(3)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], DatabaseError) and "cancelled" in str(errors[0])
        assert candidates[0].closed and session.connection is None and session._selected_schema is None
        assert session._cancel_requested.is_set() is new_cancel_during_cleanup
        assert session._cancel_generation == cancelled_generation + int(new_cancel_during_cleanup)
        if new_cancel_during_cleanup:
            with pytest.raises(DatabaseError, match="cancelled"):
                session.execute("SELECT id, label FROM aux.items ORDER BY id")
            assert len(candidates) == 1 and not session._cancel_requested.is_set()
        monkeypatch.setattr(session, "_sqlite_initialize_candidate", original_initialize)
        assert session.execute("SELECT id, label FROM aux.items ORDER BY id")[0].rows == [[1, "aux-1"], [2, "aux-2"]]
    finally:
        release_publication.set()
        worker.join(3)
        session.close()
    assert not worker.is_alive() and hashes(main.parent) == before
