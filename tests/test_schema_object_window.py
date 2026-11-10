"""Real MainWindow/SQLite workflows for global index and trigger documents."""
from contextlib import closing
import sqlite3

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store
from opennavicat.ui.schema_object_designer import IndexDesignerPage, TriggerDesignerPage
from opennavicat.ui.window import MainWindow, TablePage


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=10000)
    # when_idle schedules follow-up metadata work after the save task finishes.
    qtbot.wait(35)
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch, request):
    path = tmp_path / "owned-objects.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE owner_a(id INTEGER PRIMARY KEY,value TEXT);
            CREATE TABLE owner_b(id INTEGER PRIMARY KEY,value TEXT);
            CREATE TABLE audit(value TEXT);
            CREATE TABLE constrained(value TEXT UNIQUE);
            CREATE VIEW owned_view AS SELECT id,value FROM owner_a;
            INSERT INTO owner_a VALUES(1,'original A');
            INSERT INTO owner_b VALUES(2,'unrelated B');
            CREATE INDEX retarget_me ON owner_a(value);
            CREATE TRIGGER audit_a AFTER INSERT ON owner_a
                BEGIN INSERT INTO audit VALUES(NEW.value); END;
        """)
    read_only = getattr(request, "param", False)
    config = ConnectionConfig("owned-object-window", "Owned objects", database=str(path), read_only=read_only)
    store = Store(tmp_path / "workspace")
    store.save_connection(config)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(str(a[2])))
    widget = MainWindow(store)
    widget.show()
    widget.connect_id(config.id)
    idle(qtbot, widget)
    assert config.id in widget.sessions
    yield widget
    idle(qtbot, widget)
    # A failed assertion must not strand a native confirmation sheet at teardown.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    for index in range(widget.tabs.count()):
        page = widget.tabs.widget(index)
        if isinstance(page, TablePage) and page.model:
            page.model.discard()
        dialog = getattr(page, "_save_dialog", None)
        if dialog is not None:
            dialog.reject()
    for session in widget.sessions.values():
        if session.in_transaction:
            session.execute("ROLLBACK")
    widget.close()
    assert not widget.isVisible()
    widget.deleteLater()
    assert errors == []


def independent(window, sql, parameters=()):
    config = window.configs["owned-object-window"]
    with closing(sqlite3.connect(config.database)) as db, db:
        return db.execute(sql, parameters).fetchall()


def open_data(qtbot, window, table):
    page = window.open_table({"id": "owned-object-window", "schema": "main", "table": table, "kind": "table"})
    idle(qtbot, window)
    return page


def open_from_browser(qtbot, window, kind, name):
    window.category_actions[kind].trigger()
    idle(qtbot, window)
    browser = window.object_page
    row = next(row for row in range(browser.table.rowCount()) if browser.table.item(row, 0).text() == name)
    browser.table.setCurrentCell(row, 0)
    browser.design_action.trigger()
    idle(qtbot, window)
    page = window.tabs.currentWidget()
    assert isinstance(page, IndexDesignerPage if kind == "index" else TriggerDesignerPage)
    return page


def confirm_save(qtbot, window, page):
    page.save_action.trigger()
    dialog = page._save_dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=10000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)


def test_index_retarget_saves_database_without_retargeting_open_data_pages(qtbot, window):
    data_a = open_data(qtbot, window, "owner_a")
    data_b = open_data(qtbot, window, "owner_b")
    designer = open_from_browser(qtbot, window, "index", "retarget_me")
    designer.table_combo.setCurrentText("owner_b")
    idle(qtbot, window)
    assert independent(window, "SELECT tbl_name FROM sqlite_schema WHERE name='retarget_me'") == [("owner_a",)]
    confirm_save(qtbot, window, designer)
    assert independent(window, "SELECT tbl_name FROM sqlite_schema WHERE name='retarget_me'") == [("owner_b",)]
    assert (data_a.table, data_b.table) == ("owner_a", "owner_b")
    assert data_a.current_result().rows == [[1, "original A"]]
    assert data_b.current_result().rows == [[2, "unrelated B"]]
    assert not designer.dirty


@pytest.mark.parametrize("pending_table", ["owner_a", "owner_b"])
def test_index_retarget_rejects_pending_edits_on_either_affected_table(qtbot, window, pending_table):
    data_a = open_data(qtbot, window, "owner_a")
    data_b = open_data(qtbot, window, "owner_b")
    pending = data_a if pending_table == "owner_a" else data_b
    assert pending.model.setData(pending.model.index(0, 1), "keep my uncommitted edit", Qt.ItemDataRole.EditRole)
    designer = open_from_browser(qtbot, window, "index", "retarget_me")
    designer.table_combo.setCurrentText("owner_b")
    idle(qtbot, window)
    confirm_save(qtbot, window, designer)
    assert designer.dirty and not designer.loading
    assert "未保存" in designer.message.text()
    assert bool(pending.model.pending)
    assert pending.model.data(pending.model.index(0, 1), Qt.ItemDataRole.EditRole) == "keep my uncommitted edit"
    assert independent(window, "SELECT tbl_name FROM sqlite_schema WHERE name='retarget_me'") == [("owner_a",)]
    assert independent(window, "SELECT value FROM owner_b") == [("unrelated B",)]


def test_trigger_table_to_view_retarget_uses_instead_of_and_really_fires(qtbot, window):
    designer = open_from_browser(qtbot, window, "trigger", "audit_a")
    designer.owner_type_combo.setCurrentText("View")
    designer.table_combo.setCurrentText("owned_view")
    designer.event_checks["INSERT"].setChecked(True)
    designer.body_editor.setPlainText("BEGIN INSERT INTO owner_a(id,value) VALUES(NEW.id,NEW.value); END")
    idle(qtbot, window)
    assert designer.timing_combo.currentText() == "INSTEAD OF"
    confirm_save(qtbot, window, designer)
    assert not designer.dirty
    assert independent(window, "SELECT tbl_name FROM sqlite_schema WHERE name='audit_a'") == [("owned_view",)]
    independent(window, "INSERT INTO owned_view(id,value) VALUES(7,'created through view')")
    assert independent(window, "SELECT value FROM owner_a WHERE id=7") == [("created through view",)]
    assert independent(window, "SELECT * FROM audit") == []


@pytest.mark.parametrize("kind,name", [("index", "retarget_me"), ("trigger", "audit_a")])
def test_close_cancel_and_discard_preserve_original_object(qtbot, window, monkeypatch, kind, name):
    before = independent(window, "SELECT sql FROM sqlite_schema WHERE name=?", (name,))
    designer = open_from_browser(qtbot, window, kind, name)
    if kind == "index":
        designer.unique_check.setChecked(True)
    else:
        designer.body_editor.setPlainText("BEGIN INSERT INTO audit VALUES('uncommitted draft'); END")
    assert designer.dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    window.close_tab(window.tabs.indexOf(designer))
    assert window.tabs.indexOf(designer) >= 0 and designer.dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    window.close_tab(window.tabs.indexOf(designer))
    assert not isValid(designer) or window.tabs.indexOf(designer) < 0
    idle(qtbot, window)
    assert independent(window, "SELECT sql FROM sqlite_schema WHERE name=?", (name,)) == before


def test_close_save_preview_cancel_keeps_draft_then_save_disconnect_succeeds(qtbot, window, monkeypatch):
    designer = open_from_browser(qtbot, window, "index", "retarget_me")
    designer.unique_check.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    assert window.disconnect("owned-object-window") is False
    dialog = designer._save_dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=10000)
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert window.tabs.indexOf(designer) >= 0 and designer.dirty
    assert "owned-object-window" in window.sessions
    assert window.disconnect("owned-object-window") is False
    dialog = designer._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=10000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "owned-object-window" not in window.sessions, timeout=10000)
    idle(qtbot, window)
    assert not isValid(designer) or window.tabs.indexOf(designer) < 0
    sql = independent(window, "SELECT sql FROM sqlite_schema WHERE name='retarget_me'")[0][0]
    assert "CREATE UNIQUE INDEX" in sql
    assert independent(window, "SELECT * FROM owner_a") == [(1, "original A")]


def test_system_indexes_hidden_until_requested_and_automatic_index_is_readonly(qtbot, window):
    window.category_actions["index"].trigger()
    idle(qtbot, window)
    browser = window.object_page
    assert browser.system_indexes_check.isVisible()
    assert not any(entry.get("automatic") for entry in browser.records)
    browser.system_indexes_check.setChecked(True)
    idle(qtbot, window)
    automatic = next(entry for entry in browser.records if entry.get("automatic"))
    row = next(row for row, entry in enumerate(browser.records) if entry["name"] == automatic["name"])
    browser.table.setCurrentCell(row, 0)
    assert not browser.delete_action.isEnabled()
    browser.design_action.trigger()
    idle(qtbot, window)
    designer = window.tabs.currentWidget()
    assert isinstance(designer, IndexDesignerPage)
    assert designer.snapshot.read_only
    assert not designer.save_action.isEnabled()
    assert "只读" in designer.message.text()
    assert independent(window, "SELECT sql FROM sqlite_schema WHERE name=?", (automatic["name"],)) == [(None,)]


@pytest.mark.parametrize("window", [True], indirect=True)
def test_readonly_connection_disables_new_and_existing_object_mutation(qtbot, window):
    window.category_actions["index"].trigger()
    idle(qtbot, window)
    assert not window.object_page.new_action.isEnabled()
    assert not window.object_page.delete_action.isEnabled()
    designer = open_from_browser(qtbot, window, "index", "retarget_me")
    assert not designer.save_action.isEnabled() and not designer.general.isEnabled()
    assert "只读" in designer.message.text()
    assert independent(window, "SELECT tbl_name FROM sqlite_schema WHERE name='retarget_me'") == [("owner_a",)]
