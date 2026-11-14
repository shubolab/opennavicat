"""Real SQLite3MC files through Good, Store, explicit Open and window workers.

Only vault/UI answers and an explicit failure-injection point are substituted;
the provider, database session, window queue and SQLite statements remain real.
"""
from contextlib import closing
import hashlib
import sqlite3
import sys
import threading
import traceback
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from opennavicat.database import DatabaseError, DatabaseSession
from opennavicat.sqlite_encryption_config import with_sqlite_encryption_password
from opennavicat.storage import Store
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.window import MainWindow


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
                   and not window.cancel_pool.activeThreadCount(), timeout=15000)


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    pytest.importorskip("apsw", reason="The exact optional encrypted provider is required")
    from opennavicat.sqlite_cipher_provider import _provider
    _provider()  # Reject ordinary/overlapping APSW and untested versions.
    gui, vault, errors, threads, gui_calls = threading.get_ident(), {}, [], [], []
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        get_password=lambda service, key: vault.get((service, key)),
        set_password=lambda service, key, value: vault.__setitem__((service, key), value),
        delete_password=lambda service, key: vault.pop((service, key), None)))
    monkeypatch.setattr("opennavicat.ui.window.discover_navicat_profiles", lambda: pytest.fail("No user profile discovery"))
    monkeypatch.setattr(MainWindow, "error", lambda self, error: errors.append(str(error)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_: pytest.fail("Unexpected password prompt"))
    create = DatabaseSession.create_sqlite_database
    def actual_create(config, cancellation_requested=None):
        threads.append(("create", threading.get_ident()))
        return create(config, cancellation_requested)
    monkeypatch.setattr(DatabaseSession, "create_sqlite_database", staticmethod(actual_create))
    for name in ("connect", "execute", "close"):
        original = getattr(DatabaseSession, name)
        def traced(self, *args, _original=original, _name=name, **kwargs):
            threads.append((_name, threading.get_ident()))
            if threading.get_ident() == gui:
                gui_calls.append((_name, traceback.format_stack(limit=7)))
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(DatabaseSession, name, traced)
    store = Store(tmp_path / "generated-workspace")
    window = MainWindow(store)
    qtbot.addWidget(window)
    window.show()
    yield SimpleNamespace(window=window, store=store, vault=vault, errors=errors, threads=threads,
                          gui=gui, gui_calls=gui_calls, root=tmp_path)
    # All calls below are real, including internal idempotent connect checks.
    idle(qtbot, window)
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=15000)
    idle(qtbot, window)


def create_through_good(qtbot, host, path, key, *, failed=False, monkeypatch=None):
    callbacks, dialogs = [], []
    def fill_and_accept():
        dialog = QApplication.activeModalWidget()
        try:
            assert threading.get_ident() == host.gui and isinstance(dialog, ConnectionDialog)
            dialogs.append(dialog)
            dialog.name_edit.setText("Generated real encrypted SQLite")
            dialog.database_edit.setText(str(path))
            dialog.sqlite_mode_combo.setCurrentIndex(dialog.sqlite_mode_combo.findData("create"))
            dialog.sqlite_advanced.encrypted_check.setChecked(True)
            dialog.sqlite_advanced.cipher_combo.setEditText("aes256cbc")
            dialog.sqlite_advanced.set_password(key)
            assert not dialog.test_button.isEnabled()
            dialog.accept()
        except BaseException as error:
            callbacks.append(error)
            if isinstance(dialog, ConnectionDialog): dialog.reject()
    if failed:
        def warning(parent, title, message):
            callbacks.append(str(message))
            assert not parent._creating and not parent._testing
            assert parent.sqlite_mode_combo.currentData() == "create"
            assert parent.buttons.isEnabled() and parent.content.isEnabled()
            QTimer.singleShot(0, parent.reject)
        monkeypatch.setattr(QMessageBox, "warning", warning)
    QTimer.singleShot(0, fill_and_accept)
    host.window.new_connection(driver="sqlite")
    idle(qtbot, host.window)
    if any(isinstance(value, BaseException) for value in callbacks):
        raise next(value for value in callbacks if isinstance(value, BaseException))
    assert len(dialogs) == 1
    return callbacks


def test_real_good_store_open_rows_readonly_and_wrong_key(qtbot, host, monkeypatch):
    path = host.root / "generated-aes256.sqlite"
    key = "generated-window-only-key"
    create_through_good(qtbot, host, path, key)
    saved, = host.store.connections()
    assert saved.sqlite_open_mode == "existing" and saved.sqlite_encryption_enabled
    assert not host.window.sessions and saved.id not in host.window._connecting
    assert path.read_bytes()[:16] != b"SQLite format 3\0"
    assert saved.sqlite_encryption_password == "" and key not in host.store.path.read_text()
    assert key in host.vault.values() and not host.errors
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as standard:
        with pytest.raises(sqlite3.DatabaseError): standard.execute("SELECT * FROM sqlite_schema").fetchall()
    host.window.connect_id(saved.id)
    idle(qtbot, host.window)
    session = host.window.sessions[saved.id]
    results = []
    def write_rows():
        session.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT NOT NULL)")
        session.import_rows("main", "generated_facts", ["id", "label"], [[1, "generated one"], [2, "generated two"]])
        return session.execute("SELECT id,label FROM generated_facts ORDER BY id")
    host.window.submit(write_rows, results.extend)
    idle(qtbot, host.window)
    assert results[0].rows == [[1, "generated one"], [2, "generated two"]]
    host.window.disconnect(saved.id); idle(qtbot, host.window)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    readonly = host.store.hydrate(saved); readonly.read_only = True
    host.store.save_connection(readonly, remember_sqlite_encryption=True)
    host.window.configs[saved.id] = host.store.connections()[0]
    host.window.connect_id(saved.id); idle(qtbot, host.window)
    session = host.window.sessions[saved.id]
    rows, failures = [], []
    host.window.submit(lambda: session.execute("SELECT id,label FROM generated_facts ORDER BY id"), rows.extend)
    idle(qtbot, host.window)
    assert rows[0].rows == [[1, "generated one"], [2, "generated two"]]
    host.window.submit(lambda: session.execute("DELETE FROM generated_facts"), lambda _: pytest.fail("Read-only write succeeded"), failures.append)
    idle(qtbot, host.window)
    assert len(failures) == 1 and isinstance(failures[0], DatabaseError)
    host.window.disconnect(saved.id); idle(qtbot, host.window)
    host.store.save_connection(with_sqlite_encryption_password(readonly, ""), remember_sqlite_encryption=False)
    host.window.configs[saved.id] = host.store.connections()[0]
    prompts = []
    def wrong_key(*args):
        assert threading.get_ident() == host.gui
        prompts.append(args[1]); return "generated-wrong-window-key", True
    monkeypatch.setattr(QInputDialog, "getText", wrong_key)
    metadata = host.store.path.read_bytes()
    host.window.connect_id(saved.id); idle(qtbot, host.window)
    assert len(prompts) == len(host.errors) == 1
    assert not host.window.sessions and not host.window._connecting
    assert "generated-wrong-window-key" not in host.errors[0] and key not in host.errors[0]
    assert host.store.path.read_bytes() == metadata and not host.vault
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert all(thread != host.gui for _, thread in host.threads), host.gui_calls
    assert {name for name, _ in host.threads} >= {"create", "connect", "execute", "close"}
    assert not any((host.root / (path.name + suffix)).exists() for suffix in ("-wal", "-shm", "-journal"))


def test_real_create_failure_after_native_initialization_cleans_only_owned_file(qtbot, host, monkeypatch):
    path = host.root / "generated-failed-aes256.sqlite"
    protected = host.root / "generated-unrelated.txt"; protected.write_bytes(b"generated keep")
    actual = DatabaseSession._sqlite_initialize_candidate
    calls = []
    def fail_after_native(self, connection, config, attachments, cancelled):
        actual(self, connection, config, attachments, cancelled)
        assert path.is_file() and threading.get_ident() != host.gui
        calls.append(type(connection).__name__)
        raise DatabaseError("generated failure after actual native initialization")
    monkeypatch.setattr(DatabaseSession, "_sqlite_initialize_candidate", fail_after_native)
    messages = create_through_good(qtbot, host, path, "generated-create-failure-key", failed=True, monkeypatch=monkeypatch)
    assert calls == ["CipherConnection"] and len(messages) == 1
    assert not path.exists() and protected.read_bytes() == b"generated keep"
    assert not host.store.connections() and not host.vault and not host.window.sessions
    assert not host.window._sqlite_creation_cancellations and not host.errors
