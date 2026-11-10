"""Preview-first data comparison exercised through the native Qt workflow."""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QMessageBox, QPushButton

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store
from opennavicat.ui.compare import CompareTargetDialog, ComparisonPage
from opennavicat.ui.window import MainWindow, TablePage


SOURCE_ROWS = [(1, "new source value", 2.5), (2, "unchanged", None), (3, "new row", 1.0)]
TARGET_ROWS = [(1, "old target value", 2.5), (2, "unchanged", None), (4, "target only", 9.0)]


def rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT id, value, amount FROM things ORDER BY id").fetchall()


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def comparison_workspace(qtbot, tmp_path, monkeypatch):
    # The fixtures have no secrets; avoid invoking a desktop keychain backend.
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(delete_password=lambda *_: None))
    store = Store(tmp_path)
    paths = {}
    for connection_id, name, data in [("source", "Source fixture", SOURCE_ROWS),
                                       ("target", "Target fixture", TARGET_ROWS)]:
        path = tmp_path / f"{connection_id}.sqlite3"
        paths[connection_id] = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, value TEXT, amount REAL)")
            db.executemany("INSERT INTO things VALUES (?, ?, ?)", data)
        store.save_connection(ConnectionConfig(connection_id, name, database=str(path)))

    errors, confirmations = [], []
    monkeypatch.setattr(MainWindow, "error", lambda self, exc: errors.append(str(exc)))

    def confirm(_parent, title, message, *_args):
        confirmations.append((title, message))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(QMessageBox, "information", lambda *_: QMessageBox.StandardButton.Ok)
    def unexpected_message(_parent, title, message, *_args, **_kwargs):
        errors.append(f"{title}: {message}")
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "warning", unexpected_message)
    monkeypatch.setattr(QMessageBox, "critical", unexpected_message)
    window = MainWindow(store)
    qtbot.addWidget(window)
    window.show()
    for connection_id in ("source", "target"):
        window.set_active(connection_id)
        window.connect_active()
        qtbot.waitUntil(lambda: connection_id in window.sessions, timeout=10000)
        idle(qtbot, window)
    window.open_table({"id": "source", "schema": "main", "table": "things", "kind": "table"})
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), TablePage)
                   and window.tabs.currentWidget().model is not None, timeout=10000)
    idle(qtbot, window)
    yield window, paths, errors, confirmations
    idle(qtbot, window)
    window.close()


def compare_via_dialog(qtbot, window):
    def select_target():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, CompareTargetDialog)
            assert dialog.connection.count() == 2
            dialog.connection.setCurrentIndex(dialog.connection.findData("target"))
            assert dialog.schema.currentText() == "main"
            assert dialog.table.text() == "things"
            compare = next(button for button in dialog.findChildren(QPushButton)
                           if button.text() == "Compare")
            qtbot.mouseClick(compare, Qt.MouseButton.LeftButton)
        finally:
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, select_target)
    action = next(action for action in window.findChildren(QAction)
                  if action.text() == "数据同步…")
    action.trigger()
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), ComparisonPage), timeout=10000)
    idle(qtbot, window)
    return window.tabs.currentWidget()


@pytest.mark.parametrize("delete_extras", [False, True])
def test_compare_preview_reads_only_then_confirmed_apply(comparison_workspace, qtbot, delete_extras):
    window, paths, errors, confirmations = comparison_workspace
    page = compare_via_dialog(qtbot, window)
    assert rows(paths["source"]) == SOURCE_ROWS
    assert rows(paths["target"]) == TARGET_ROWS
    assert not confirmations
    assert page.grid.rowCount() == 3
    assert page.grid.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert page.detail.isReadOnly()
    assert len(page.plan.inserts) == len(page.plan.updates) == len(page.plan.deletes) == 1
    assert page.plan.unchanged == 1
    assert not page.deletes.isChecked()
    assert page.apply_button.isEnabled()

    if delete_extras:
        qtbot.mouseClick(page.deletes, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.apply_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert errors == []
    assert confirmations[0][0] == "Apply data changes"
    assert f"{1 if delete_extras else 0} deletes" in confirmations[0][1]
    assert "Target fixture" in confirmations[0][1]
    expected = SOURCE_ROWS if delete_extras else SOURCE_ROWS + [TARGET_ROWS[-1]]
    # Read with an independent SQLite connection: UI result models cannot make
    # this assertion pass unless the transaction really committed to disk.
    assert rows(paths["target"]) == expected
    assert rows(paths["source"]) == SOURCE_ROWS
    assert page.plan.applied
    assert "Applied successfully" in page.note.text()
    assert not page.apply_button.isEnabled()
    assert not page.deletes.isEnabled()


def test_stale_comparison_apply_rolls_back_without_clobbering_other_writer(comparison_workspace, qtbot):
    window, paths, errors, confirmations = comparison_workspace
    page = compare_via_dialog(qtbot, window)
    with sqlite3.connect(paths["target"]) as other_writer:
        other_writer.execute("UPDATE things SET value='concurrent writer' WHERE id=1")
    before = rows(paths["target"])
    qtbot.mouseClick(page.deletes, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.apply_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert len(confirmations) == 1
    assert len(errors) == 1
    assert "changed" in errors[0].lower()
    assert rows(paths["target"]) == before
    assert rows(paths["source"]) == SOURCE_ROWS
    assert not window.sessions["target"].in_transaction
    assert not page.plan.applied
    assert page.apply_button.isEnabled()
    assert page.deletes.isEnabled()
