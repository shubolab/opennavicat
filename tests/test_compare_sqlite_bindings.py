"""Stale previews must never follow a SQLite alias onto another owned file."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

import pytest

from opennavicat.compare import apply_plan, compare_tables
from opennavicat.database import ConnectionConfig, DatabaseSession, SQLiteDatabaseEntry


OLD = [(1, "old"), (2, "extra")]
NEW = [(1, "new"), (3, "insert")]
MAIN = [(90, "main guard")]


def make_file(path, rows):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, value TEXT)")
        db.executemany("INSERT INTO things VALUES (?, ?)", rows)


def read_file(path):
    # A separate read-only connection proves the file result, and cannot create
    # a missing database while checking a rejected preview.
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT id, value FROM things ORDER BY id").fetchall()


@pytest.fixture
def databases(tmp_path):
    paths = {name: tmp_path / (name + ".sqlite")
             for name in ("source", "main", "aux_a", "aux_b")}
    for name, rows in (("source", NEW), ("main", MAIN), ("aux_a", OLD), ("aux_b", OLD)):
        make_file(paths[name], rows)
    source = DatabaseSession(ConnectionConfig("source", "Source", database=str(paths["source"])))
    target = DatabaseSession(ConnectionConfig("target", "Target", database=str(paths["main"])))
    target._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux_a"]),))
    try:
        yield SimpleNamespace(source=source, target=target, paths=paths)
    finally:
        target.close()
        source.close()


def preview(databases):
    return compare_tables(databases.source, databases.target, "main", "things", "aux", "things")


def attempt_apply(target, plan):
    statements = []
    target.connection.set_trace_callback(statements.append)
    error = None
    try:
        apply_plan(target, plan, include_deletes=True)
    except ValueError as caught:
        error = caught
    finally:
        target.connection.set_trace_callback(None)
    return error, statements


def assert_rejected_before_writes(target, plan, error, statements):
    assert isinstance(error, ValueError) and "Compare again" in str(error)
    assert not any(re.match(r"\s*(BEGIN|INSERT|UPDATE|DELETE)\b", sql, re.I)
                   for sql in statements), statements
    assert not target.in_transaction and not plan.applied


@pytest.mark.parametrize('change', ['name', 'database', 'encrypted', 'id', 'order'])
def test_pending_attached_profile_change_invalidates_preview_before_writes(databases, change):
    target, paths = databases.target, databases.paths
    target._query('ATTACH DATABASE ? AS "other"', (str(paths['aux_b']),))
    first = SQLiteDatabaseEntry('first', 'aux', str(paths['aux_a']))
    second = SQLiteDatabaseEntry('second', 'other', str(paths['aux_b']))
    target.config.sqlite_databases = (first, second)
    target.config.sqlite_database_passwords = {'first': 'NEVER-FINGERPRINT-PASSWORD'}
    plan = preview(databases)
    assert 'NEVER-FINGERPRINT-PASSWORD' not in plan.target_fingerprint
    if change == 'order':
        target.config.sqlite_databases = (second, first)
    else:
        changed = {'name': 'renamed', 'database': str(paths['aux_b']),
                   'encrypted': True, 'id': 'replacement'}[change]
        target.config.sqlite_databases = (replace(first, **{change: changed}), second)
    error, statements = attempt_apply(target, plan)
    assert_rejected_before_writes(target, plan, error, statements)
    assert read_file(paths['aux_a']) == OLD and read_file(paths['aux_b']) == OLD


def test_same_session_alias_retarget_cannot_apply_old_preview_to_identical_file(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    original_connection = target.connection
    before_columns = target.columns("aux", "things")
    target.execute('DETACH DATABASE "aux"')
    target._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux_b"]),))
    assert target.connection is original_connection
    assert target.columns("aux", "things") == before_columns
    assert read_file(paths["aux_a"]) == read_file(paths["aux_b"]) == OLD
    error, statements = attempt_apply(target, plan)
    # On the old implementation B is changed to NEW, although the preview read
    # A. Check actual files before the rejection assertion to retain that proof.
    assert read_file(paths["aux_b"]) == OLD
    assert read_file(paths["aux_a"]) == OLD
    assert read_file(paths["main"]) == MAIN
    assert_rejected_before_writes(target, plan, error, statements)


def test_same_address_reconnect_with_same_alias_file_cannot_apply_old_preview(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    before_mapping = target._query("PRAGMA database_list")
    original_connection = target.connection
    target.close()
    target.connect()
    target._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux_a"]),))
    assert target.connection is not original_connection
    assert target._query("PRAGMA database_list") == before_mapping
    error, statements = attempt_apply(target, plan)
    assert read_file(paths["aux_a"]) == OLD
    assert read_file(paths["aux_b"]) == OLD
    assert read_file(paths["main"]) == MAIN
    assert_rejected_before_writes(target, plan, error, statements)


def test_removed_alias_is_rejected_before_transaction_or_metadata_read(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    target.execute('DETACH DATABASE "aux"')
    error, statements = attempt_apply(target, plan)
    assert read_file(paths["aux_a"]) == read_file(paths["aux_b"]) == OLD
    assert read_file(paths["main"]) == MAIN
    assert_rejected_before_writes(target, plan, error, statements)


def test_missing_attached_file_is_rejected_before_transaction(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    try:
        paths["aux_a"].unlink()
    except PermissionError:
        pytest.skip("This OS does not permit removing an open owned SQLite file")
    error, statements = attempt_apply(target, plan)
    assert not paths["aux_a"].exists()
    assert read_file(paths["aux_b"]) == OLD and read_file(paths["main"]) == MAIN
    assert_rejected_before_writes(target, plan, error, statements)


def test_closed_target_rejection_does_not_reopen_or_recreate_main_file(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    target.close()
    paths["main"].unlink()
    with pytest.raises(ValueError, match="Compare again"):
        apply_plan(target, plan, include_deletes=True)
    assert target.connection is None and not paths["main"].exists()
    assert read_file(paths["aux_a"]) == read_file(paths["aux_b"]) == OLD
    assert not plan.applied


def test_unchanged_attached_binding_applies_only_to_previewed_file(databases):
    target, paths = databases.target, databases.paths
    plan = preview(databases)
    assert apply_plan(target, plan, include_deletes=True) == {
        "inserted": 1, "updated": 1, "deleted": 1,
    }
    assert read_file(paths["aux_a"]) == NEW
    assert read_file(paths["aux_b"]) == OLD and read_file(paths["main"]) == MAIN
