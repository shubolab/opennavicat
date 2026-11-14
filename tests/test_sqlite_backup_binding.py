"""Actual SQLite backup sources and the real window's queued backup entry."""
from contextlib import closing
from pathlib import Path
import sqlite3
import threading

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat.ui.window import QFileDialog
from test_ui import idle, window


MAIN = [(1, "main one"), (2, "main two")]
AUX = [(1, "aux one"), (2, "aux two")]
AUX_B = [(1, "other aux one"), (2, "other aux two")]


def rows(path):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return db.execute("SELECT id, value FROM things ORDER BY id").fetchall()


@pytest.fixture
def backup_source(tmp_path):
    paths = {name: tmp_path / (name + ".sqlite") for name in ("main", "aux", "aux_b")}
    for name, data in (("main", MAIN), ("aux", AUX), ("aux_b", AUX_B)):
        with closing(sqlite3.connect(paths[name])) as db, db:
            db.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, value TEXT)")
            db.executemany("INSERT INTO things VALUES (?, ?)", data)
    session = DatabaseSession(ConnectionConfig("owned-backup", "Owned backup", database=str(paths["main"])))
    session._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux"]),))
    try:
        yield session, paths
    finally:
        session.close()


@pytest.mark.parametrize("change_during_dialog", [False, True])
def test_window_selected_aux_backs_up_aux_and_freezes_gui_context(
        qtbot, window, backup_source, tmp_path, monkeypatch, change_during_dialog):
    session, paths = backup_source
    window.configs[session.config.id] = session.config
    window.sessions[session.config.id] = session
    window.active_id, window.active_schema = session.config.id, "aux"
    output = tmp_path / "selected-aux.sqlite"
    gui_thread, database_threads = threading.get_ident(), []
    query = session._query
    def checked_query(sql, *args, **kwargs):
        if sql == "PRAGMA database_list":
            database_threads.append(threading.get_ident())
            assert threading.get_ident() != gui_thread
        return query(sql, *args, **kwargs)
    monkeypatch.setattr(session, "_query", checked_query)
    def choose(*_args):
        assert threading.get_ident() == gui_thread
        if change_during_dialog:
            window.active_id, window.active_schema = "different-selection", "main"
        return str(output), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    window.backup_sqlite()
    idle(qtbot, window)
    assert rows(output) == AUX
    assert len(database_threads) >= 2
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX


def test_window_queued_backup_rejects_reconnected_owner(qtbot, window, backup_source, tmp_path, monkeypatch):
    session, paths = backup_source
    window.configs[session.config.id] = session.config
    window.sessions[session.config.id] = session
    window.active_id, window.active_schema = session.config.id, "aux"
    def reconnect_before_backup():
        session.close()
        session.connect()
        session._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux"]),))
    output = tmp_path / "stale-connection.sqlite"
    def choose(*_args):
        window.submit(reconnect_before_backup)
        return str(output), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    window.backup_sqlite()
    idle(qtbot, window)
    assert not output.exists()
    assert len(window.test_errors) == 1 and "connection changed" in window.test_errors.pop()
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX


def test_window_filepicker_alias_rebind_is_rejected_before_output_creation(qtbot, window, backup_source, tmp_path, monkeypatch):
    session, paths = backup_source
    window.configs[session.config.id] = session.config
    window.sessions[session.config.id] = session
    window.active_id, window.active_schema = session.config.id, "aux"
    original = session.connection
    output = tmp_path / "stale-alias.sqlite"
    def rebind():
        session.execute('DETACH DATABASE "aux"')
        session._query('ATTACH DATABASE ? AS "aux"', (str(paths["aux_b"]),))
    def choose(*_args):
        window.submit(rebind)
        return str(output), ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    window.backup_sqlite()
    idle(qtbot, window)
    assert session.connection is original and not output.exists()
    assert len(window.test_errors) == 1 and "database changed" in window.test_errors.pop()
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX
    assert rows(paths["aux_b"]) == AUX_B


def test_explicit_aux_backup_uses_actual_namespace(backup_source, tmp_path):
    session, paths = backup_source
    output = tmp_path / "explicit-aux.sqlite"
    session.backup_to(str(output), schema="aux")
    assert rows(output) == AUX
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX


def test_missing_namespace_is_rejected_before_creating_destination(backup_source, tmp_path):
    session, _paths = backup_source
    session.execute('DETACH DATABASE "aux"')
    output = tmp_path / "removed-alias.sqlite"
    with pytest.raises(DatabaseError, match="database.*attached"):
        session.backup_to(str(output), schema="aux")
    assert not output.exists()


def test_captured_closed_connection_does_not_reopen_or_recreate_source(backup_source, tmp_path):
    session, paths = backup_source
    original = session.backup_source("main")
    session.close()
    paths["main"].unlink()
    output = tmp_path / "closed-owner.sqlite"
    with pytest.raises(DatabaseError, match="connection changed"):
        session.backup_to(str(output), source=original)
    assert session.connection is None and not paths["main"].exists() and not output.exists()
    assert rows(paths["aux"]) == AUX


@pytest.mark.parametrize("destination", ["main", "aux"])
def test_aux_backup_never_overwrites_any_attached_file(backup_source, destination):
    session, paths = backup_source
    with pytest.raises((DatabaseError, FileExistsError)):
        session.backup_to(str(paths[destination]), schema="aux")
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX


def test_legacy_unconnected_default_still_opens_and_backs_up_main(backup_source, tmp_path):
    original, paths = backup_source
    session = DatabaseSession(original.config)
    output = tmp_path / "legacy-main.sqlite"
    try:
        assert session.connection is None
        session.backup_to(str(output))
        assert rows(output) == MAIN
    finally:
        session.close()


def test_busy_refresh_does_not_read_a_native_connection_mid_close(qtbot, window, backup_source, monkeypatch):
    original, paths = backup_source
    entered, release, block = threading.Event(), threading.Event(), threading.Event()
    native_connect = sqlite3.connect
    class ClosingConnection(sqlite3.Connection):
        def close(self):
            super().close()
            if block.is_set():
                block.clear()
                entered.set()
                assert release.wait(5)
    def connect(*args, **kwargs):
        return native_connect(*args, **kwargs, factory=ClosingConnection)
    monkeypatch.setattr(sqlite3, "connect", connect)
    session = DatabaseSession(original.config)
    session.execute("BEGIN")
    window.configs[session.config.id] = session.config
    window.sessions[session.config.id] = session
    window.active_id = session.config.id
    window.refresh_actions()
    assert window.transaction_status.text() == "事务已开始"
    assert all(action.isEnabled() for action in window.transaction_actions)
    block.set()
    window.submit(session.close)
    try:
        qtbot.waitUntil(entered.is_set)
        # The native handle is genuinely closed while DatabaseSession still
        # retains it. GUI refresh must not dereference its transaction state.
        window.refresh_actions()
        assert window.transaction_status.text() == "事务已开始"
        assert not any(action.isEnabled() for action in window.transaction_actions)
    finally:
        release.set()
        idle(qtbot, window)
    assert session.connection is None
    assert window.transaction_status.text() == ""
    assert all(action.isEnabled() for action in window.transaction_actions)
    assert rows(paths["main"]) == MAIN


@pytest.mark.parametrize("finish_action", [1, 2], ids=["commit", "rollback"])
def test_transaction_actions_disable_while_running_and_restore_when_idle(qtbot, window, backup_source, finish_action):
    session, paths = backup_source
    window.configs[session.config.id] = session.config
    window.sessions[session.config.id] = session
    window.active_id, window.active_schema = session.config.id, "main"
    window.refresh_actions()
    window.transaction_actions[0].trigger()
    assert not any(action.isEnabled() for action in window.transaction_actions)
    idle(qtbot, window)
    assert session.in_transaction and window.transaction_status.text() == "事务已开始"
    assert all(action.isEnabled() for action in window.transaction_actions)
    window.transaction_actions[finish_action].trigger()
    assert not any(action.isEnabled() for action in window.transaction_actions)
    assert window.transaction_status.text() == "事务已开始"
    idle(qtbot, window)
    assert not session.in_transaction and window.transaction_status.text() == ""
    assert all(action.isEnabled() for action in window.transaction_actions)
    assert rows(paths["main"]) == MAIN and rows(paths["aux"]) == AUX
