"""Exercise the real native UI, background queue, driver and workspace together."""
from __future__ import annotations

import sqlite3
import json

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget, QMessageBox, QPushButton

from opennavicat.database import QueryResult
from opennavicat.storage import Store
from opennavicat.ui.dialogs import RowDialog, TableDesignerDialog
from opennavicat.ui.grid import DataModel
from opennavicat.ui.window import MainWindow, QueryPage, TablePage


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, exc: errors.append(str(exc)))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Discard)
    def unexpected_message(_parent, title, message, *_args, **_kwargs):
        errors.append(f"{title}: {message}")
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "warning", unexpected_message)
    monkeypatch.setattr(QMessageBox, "critical", unexpected_message)
    widget = MainWindow(Store(tmp_path))
    widget.test_errors = errors
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    idle(qtbot, widget)
    for index in range(widget.tabs.count()):
        page = widget.tabs.widget(index)
        if isinstance(page, TablePage) and page.model:
            page.model.discard()
    for session in widget.sessions.values():
        if session.in_transaction:
            session.execute("ROLLBACK")
    # The tests leave no dirty rows or open transactions, so closing must persist
    # drafts and release connections without an interactive confirmation.
    widget.close()
    assert errors == []


def open_sample(qtbot, window):
    file_menu = next(action.menu() for action in window.menuBar().actions()
                     if action.text() == "文件")
    action = next(action for action in file_menu.actions()
                  if action.text() == "打开示例数据库")
    action.trigger()
    qtbot.waitUntil(lambda: bool(window.sessions), timeout=10000)
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), QueryPage), timeout=10000)
    idle(qtbot, window)
    page = window.tabs.currentWidget()
    assert isinstance(page, QueryPage)
    assert page.connection_id == window.active_id
    return page


def run_query(qtbot, window, page):
    previous = window.store.history()
    previous_id = previous[0]["id"] if previous else None
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(window.store.history()) and
                   window.store.history()[0]["id"] != previous_id, timeout=10000)
    idle(qtbot, window)
    assert not page.running
    assert page.run_button.isEnabled()
    assert window.store.history()[0]["status"] == "success"
    assert page.results.tabText(0) == "信息"
    assert page.results.tabText(1) == "摘要"
    return page.current_result()


def customer_item(window):
    root = window.connection_item(window.active_id)
    schema = root.child(0)
    tables = next(schema.child(i) for i in range(schema.childCount())
                  if schema.child(i).text(0) == "表")
    tables.setExpanded(True)
    return next(tables.child(i) for i in range(tables.childCount())
                if tables.child(i).text(0) == "customers")


def open_customers(qtbot, window):
    item = customer_item(window)
    window.tree.scrollToItem(item)
    # Native item views need the initial press before the double-click event.
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                    pos=window.tree.visualItemRect(item).center())
    qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.tree.visualItemRect(item).center())
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), TablePage), timeout=5000)
    page = window.tabs.currentWidget()
    qtbot.waitUntil(lambda: page.model is not None, timeout=10000)
    idle(qtbot, window)
    assert page.model is not None
    return page


def test_sample_query_selection_history_and_disconnect(qtbot, window):
    page = open_sample(qtbot, window)
    result = run_query(qtbot, window, page)
    assert result.columns == ["customer", "city", "orders", "revenue"]
    assert len(result.rows) == 6
    assert all(row[2] == 6 for row in result.rows)
    assert window.export_action.isEnabled()
    assert window.store.history()[0]["status"] == "success"

    # A selected statement must be sent to the driver without surrounding SQL.
    page.editor.setPlainText("SELECT 'skip this';\nSELECT 42 AS selected_value;")
    cursor = page.editor.textCursor()
    cursor.setPosition(page.editor.toPlainText().index("SELECT 42"))
    cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
    page.editor.setTextCursor(cursor)
    assert run_query(qtbot, window, page).rows == [[42]]
    assert window.store.history()[0]["sql"] == "SELECT 42 AS selected_value;"

    connection_id = page.connection_id
    assert window.disconnect(connection_id)
    assert connection_id not in window.sessions
    assert not window.table_action.isEnabled()
    disconnected = window.connection_item(connection_id)
    assert disconnected.childCount() == 1
    assert disconnected.child(0).data(0, Qt.ItemDataRole.UserRole) is None
    assert page.connection_picker.currentData() == connection_id
    # SQL drafts remain usable after disconnect and reconnect on Run.
    assert page.editor.toPlainText().endswith("selected_value;")
    assert run_query(qtbot, window, page).rows == [[42]]
    assert connection_id in window.sessions


def test_tree_filter_grid_edit_apply_persists_and_discard(qtbot, window):
    open_sample(qtbot, window)
    page = open_customers(qtbot, window)
    assert page.model.rowCount() == 6
    assert page.structure.rowCount() == 5
    assert "CREATE TABLE" in page.ddl.toPlainText().upper()
    assert page.model.editable

    page.filter_action.setChecked(True)
    assert page.filter_bar.isVisible()
    qtbot.keyClicks(page.filter, "Lin Chen")
    qtbot.keyClick(page.filter, Qt.Key.Key_Return)
    idle(qtbot, window)
    assert page.model.rowCount() == 1
    assert page.total == 1
    name_col = page.model.result.columns.index("name")
    index = page.model.index(0, name_col)
    page.grid.setCurrentIndex(index)
    assert page.grid.edit(index) is None
    editor = page.grid.findChild(QLineEdit)
    assert editor is not None
    editor.selectAll()
    qtbot.keyClicks(editor, "Lin Updated")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: bool(page.model.pending), timeout=5000)
    assert page.model.pending
    assert page.save_button.isEnabled()

    # Applying changed rows goes through an actual SQL transaction. The active
    # filter no longer matches the new value, so the refreshed page is empty.
    qtbot.mouseClick(page.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert not page.model.pending
    assert page.total == 0
    db_path = window.configs[page.connection_id].database
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT name FROM customers WHERE id=2").fetchone() == ("Lin Updated",)

    page.filter.clear()
    qtbot.keyClick(page.filter, Qt.Key.Key_Return)
    idle(qtbot, window)
    assert page.total == 6
    index = page.model.index(0, name_col)
    original = page.model.data(index)
    assert page.model.setData(index, "Unsaved draft")
    qtbot.mouseClick(page.discard_button, Qt.MouseButton.LeftButton)
    assert not page.model.pending
    assert page.model.data(index) == original


def test_designer_review_execute_and_table_browser(qtbot, window):
    open_sample(qtbot, window)

    def fill_designer():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, TableDesignerDialog)
            dialog.name_edit.setText("designed_table")
            dialog.grid.cellWidget(1, 0).setText("description")
            assert '"main"."designed_table"' in dialog.preview.toPlainText()
            dialog.accept()
        finally:
            # A callback assertion must also exit the nested modal event loop.
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, fill_designer)
    window.new_table()
    page = window.tabs.currentWidget()
    assert isinstance(page, QueryPage)
    assert not page.running
    assert "CREATE TABLE" in page.editor.toPlainText()
    # The form produces a reviewable query; creation starts only on Run.
    with sqlite3.connect(window.configs[page.connection_id].database) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='designed_table'").fetchall()
    run_query(qtbot, window, page)
    window.refresh_tree()
    idle(qtbot, window)
    schema = window.connection_item(page.connection_id).child(0)
    tables = next(schema.child(i) for i in range(schema.childCount())
                  if schema.child(i).text(0) == "表")
    item = next(tables.child(i) for i in range(tables.childCount())
                if tables.child(i).text(0) == "designed_table")
    window.tree_open(item)
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), TablePage)
                   and window.tabs.currentWidget().model is not None, timeout=10000)
    idle(qtbot, window)
    table = window.tabs.currentWidget()
    assert isinstance(table, TablePage)
    assert table.total == 0
    assert table.model.result.columns == ["id", "description"]
    assert table.model.editable


def test_reopen_draft_and_history_in_native_query_library(qtbot, window, tmp_path):
    page = open_sample(qtbot, window)
    page.editor.setPlainText("SELECT 'durable draft' AS note;")
    run_query(qtbot, window, page)
    page.editor.appendPlainText("-- keep my unfinished work")
    assert page.modified
    connection_id = page.connection_id
    window.close()
    reopened = MainWindow(Store(tmp_path))
    qtbot.addWidget(reopened)
    reopened.show()
    restored = reopened.tabs.currentWidget()
    assert isinstance(restored, QueryPage)
    assert restored.connection_id == connection_id
    assert restored.modified
    assert restored.editor.toPlainText().endswith("-- keep my unfinished work")
    assert not reopened.sessions

    def select_history():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            assert dialog.windowTitle() == "查询历史"
            listing = dialog.findChild(QListWidget)
            assert listing.count() == 1
            listing.setCurrentRow(0)
            button = next(button for button in dialog.findChildren(QPushButton)
                          if button.text() == "打开查询")
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        finally:
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, select_history)
    reopened.show_history()
    history_page = reopened.tabs.currentWidget()
    assert history_page is not restored
    assert history_page.editor.toPlainText() == "SELECT 'durable draft' AS note;"
    assert run_query(qtbot, reopened, history_page).rows == [["durable draft"]]
    reopened.close()


def test_grid_json_values_and_database_generated_columns(qtbot):
    metadata = [
        {"name": "id", "type": "bigint", "pk": True, "identity_generation": "ALWAYS"},
        {"name": "settings", "type": "jsonb"},
        {"name": "computed", "type": "text", "generated": True},
        {"name": "optional", "type": "json"},
    ]
    original = {"enabled": True, "labels": ["中文", "test"]}
    result = QueryResult(columns=["id", "settings", "computed", "optional"],
                         rows=[[7, original, "derived", None]])
    model = DataModel(result, editable=True, metadata=metadata)
    settings = model.index(0, 1)
    assert json.loads(model.data(settings)) == original
    assert json.loads(model.data(settings, Qt.ItemDataRole.EditRole)) == original
    with qtbot.waitSignal(model.dirty_changed):
        assert model.setData(settings, '{"enabled": false, "labels": ["edited"]}')
    assert model.changes()[0][1] == {"settings": {"enabled": False, "labels": ["edited"]}}
    assert not model.setData(settings, "{invalid JSON}")
    assert json.loads(model.data(settings))["labels"] == ["edited"]
    assert model.setData(model.index(0, 3), '["formerly null"]')
    assert model.changes()[0][1]["optional"] == ["formerly null"]
    for column in (0, 2):
        index = model.index(0, column)
        assert not model.flags(index) & Qt.ItemFlag.ItemIsEditable
        assert not model.setData(index, "cannot overwrite a generated value")
    assert model.changes()[0][0]["computed"] == "derived"
    assert "computed" not in model.changes()[0][1]
    assert "id" not in model.changes()[0][1]


def test_row_dialog_json_and_generated_fields(qtbot, monkeypatch):
    metadata = [
        {"name": "id", "type": "bigint", "pk": True, "identity_generation": "ALWAYS"},
        {"name": "settings", "type": "jsonb", "nullable": True},
        {"name": "computed", "type": "text", "generated": True},
    ]
    original = {"enabled": True, "labels": ["中文"]}
    dialog = RowDialog(metadata, {"id": 7, "settings": original, "computed": "derived"})
    qtbot.addWidget(dialog)
    assert json.loads(dialog._fields[1][1].text()) == original
    assert not dialog._fields[0][1].isEnabled()
    assert not dialog._fields[2][1].isEnabled()
    dialog._fields[1][1].setText('{"enabled": false, "labels": ["new"]}')
    dialog.accept()
    assert dialog.values() == {"settings": {"enabled": False, "labels": ["new"]}}

    created = RowDialog(metadata)
    qtbot.addWidget(created)
    for index in (0, 2):
        assert not created._fields[index][1].isEnabled()
        assert not created._fields[index][3].isEnabled()
    created._fields[1][3].setChecked(False)
    created._fields[1][1].setText('[true, {"amount": 2.5}]')
    created.accept()
    assert created.values() == {"settings": [True, {"amount": 2.5}]}

    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    invalid = RowDialog(metadata)
    qtbot.addWidget(invalid)
    invalid._fields[1][3].setChecked(False)
    invalid._fields[1][1].setText("malformed JSON")
    invalid.accept()
    assert invalid.result() != QDialog.DialogCode.Accepted
    assert errors and "settings" in errors[0]


def test_cancel_long_sql_keeps_ui_and_session_usable(qtbot, window):
    page = open_sample(qtbot, window)
    page.editor.setPlainText(
        "WITH RECURSIVE sequence(n) AS (VALUES(1) UNION ALL "
        "SELECT n+1 FROM sequence WHERE n < 1000000000) "
        "SELECT SUM(n) FROM sequence;"
    )
    session = window.sessions[page.connection_id]
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: session._active, timeout=5000)
    assert page.running
    assert window.stop_action.isEnabled()
    count = window.tabs.count()
    window.close_tab(window.tabs.indexOf(page))
    assert window.tabs.count() == count
    assert window.tabs.indexOf(page) >= 0
    try:
        window.stop_action.trigger()
        qtbot.waitUntil(lambda: not page.running, timeout=5000)
        idle(qtbot, window)
    finally:
        if session._active:
            session.cancel()
    assert page.run_button.isEnabled()
    assert not window.stop_action.isEnabled()
    assert "失败" in page.message.text()
    assert any(word in page.info.toPlainText().lower() for word in ("interrupt", "cancel"))
    assert window.store.history()[0]["status"] == "error"
    page.editor.setPlainText("SELECT 42 AS reusable_after_cancel;")
    assert run_query(qtbot, window, page).rows == [[42]]
