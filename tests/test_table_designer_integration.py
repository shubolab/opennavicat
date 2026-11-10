"""MainWindow routing, serial DDL and document lifecycle with owned SQLite."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import sqlite3
import threading

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMessageBox

from opennavicat import schema as schema_tools
from opennavicat.storage import Store
from opennavicat.ui.table_designer import TableDesignerPage
from opennavicat.ui.window import MainWindow, TablePage

from test_ui import customer_item, idle, open_customers, open_sample


@pytest.fixture
def workspace(qtbot, tmp_path, monkeypatch):
    errors, answers = [], []
    monkeypatch.setattr(MainWindow, "error", lambda self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: answers.pop(0) if answers else QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, message, *_a, **_k: errors.append(f"{title}: {message}"))
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, message, *_a, **_k: errors.append(f"{title}: {message}"))
    window = MainWindow(Store(tmp_path))
    window.test_errors, window.test_answers = errors, answers
    # This fixture drains follow-up serial work before deleting its own window;
    # pytest-qt's automatic close/delete hook would run before this finalizer.
    window.show()
    query = open_sample(qtbot, window)
    window.fixture_id = query.connection_id
    window.fixture_path = window.configs[query.connection_id].database
    yield window
    idle(qtbot, window)
    # Follow-up refreshes are deliberately scheduled after the serial task's
    # finished signal; let those owned 25ms callbacks enter the queue too.
    qtbot.wait(35)
    idle(qtbot, window)
    answers.clear()
    for index in range(window.tabs.count()):
        page = window.tabs.widget(index)
        if isinstance(page, TableDesignerPage) and page._save_dialog:
            page._save_dialog.reject()
        if isinstance(page, TablePage) and page.model:
            page.model.discard()
    window.close()
    assert errors == []
    window.deleteLater()


def designer(qtbot, window):
    page = window.open_designer(window.fixture_id, "main", "customers")
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=10000)
    idle(qtbot, window)
    return page


def add_note(page, name="designer_note"):
    page.tabs.setCurrentIndex(0)
    page.add_record()
    row = page.field_model.rowCount() - 1
    page.field_model.setData(page.field_model.index(row, 0), name)
    page.fields.setCurrentIndex(page.field_model.index(row, 0))
    page.default_edit.setCurrentText("'saved from designer'")
    assert page.dirty


def names(path, table="customers"):
    with closing(sqlite3.connect(path)) as connection:
        return [row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')]


def leave(window, page, operation):
    if operation == "tab":
        window.close_tab(window.tabs.indexOf(page))
    elif operation == "disconnect":
        window.disconnect(window.fixture_id)
    else:
        window.close()


def has_designer(window):
    return any(isinstance(window.tabs.widget(index), TableDesignerPage) for index in range(window.tabs.count()))


def left(window, operation):
    return not has_designer(window) if operation == "tab" else window.fixture_id not in window.sessions if operation == "disconnect" else not window.isVisible()


def test_data_and_designer_are_separate_documents_and_reopen_their_own_state(qtbot, workspace):
    window = workspace
    data = open_customers(qtbot, window)
    data_index = window.tabs.indexOf(data)
    window.info_action.trigger()
    page = window.design_object(customer_item(window).data(0, Qt.ItemDataRole.UserRole))
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading)
    idle(qtbot, window)
    assert isinstance(page, TableDesignerPage) and page is not data
    assert window.tabs.indexOf(data) == data_index
    assert data.subtabs.isTabVisible(0)
    assert window.inspector.isVisible()
    add_note(page)
    info = customer_item(window).data(0, Qt.ItemDataRole.UserRole)
    assert window.open_table(info) is data
    assert data.subtabs.currentIndex() == 0
    assert window.open_designer(window.fixture_id, "main", "customers") is page
    assert page.dirty and page.definition.columns[-1].name == "designer_note"
    assert "已编辑" in window.tabs.tabText(window.tabs.indexOf(page))


def test_new_table_save_sheet_uses_real_queue_then_refreshes_tree_and_objects(qtbot, workspace):
    window = workspace
    page = window.new_table()
    assert isinstance(page, TableDesignerPage)
    assert page.title.startswith("无标题")
    assert page.fields.currentIndex().row() == 0
    page.field_model.setData(page.field_model.index(0, 0), "id")
    page.field_model.setData(page.field_model.index(0, 1), "INTEGER")
    page.toggle_primary_key()
    page.request_save()
    dialog = page._save_dialog
    assert not dialog.save_button.isEnabled()
    dialog.name_edit.setText("ui_created_table")
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    assert "CREATE TABLE" in dialog.preview.toPlainText()
    assert names(window.fixture_path, "ui_created_table") == []
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.table == "ui_created_table" and not page.dirty and not page.loading, timeout=10000)
    idle(qtbot, window)
    assert names(window.fixture_path, "ui_created_table") == ["id"]
    assert "ui_created_table@main" in window.tabs.tabText(window.tabs.indexOf(page))
    window.show_objects("table")
    qtbot.waitUntil(lambda: any(record["name"] == "ui_created_table" for record in window.object_page.records))
    def tree_has_created():
        root = window.connection_item(window.fixture_id)
        if root is None or not root.childCount():
            return False
        schema = root.child(0)
        for index in range(schema.childCount()):
            category = schema.child(index)
            if (category.data(0, Qt.ItemDataRole.UserRole) or {}).get("kind") == "table":
                return any(category.child(child).text(0) == "ui_created_table" for child in range(category.childCount()))
        return False
    qtbot.waitUntil(tree_has_created, timeout=10000)


@pytest.mark.parametrize("operation", ["tab", "disconnect", "exit"])
@pytest.mark.parametrize("answer", ["Save", "Discard", "Cancel"])
def test_dirty_document_lifecycle_real_save_discard_cancel(qtbot, workspace, operation, answer):
    window = workspace
    page = designer(qtbot, window)
    add_note(page)
    window.test_answers.append(getattr(QMessageBox.StandardButton, answer))
    leave(window, page, operation)
    if answer == "Cancel":
        assert not left(window, operation)
        assert page.dirty and "designer_note" not in names(window.fixture_path)
        return
    if answer == "Save":
        dialog = page._save_dialog
        assert dialog is not None
        qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
        assert not left(window, operation)
        assert "designer_note" not in names(window.fixture_path)
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: left(window, operation), timeout=10000)
        assert "designer_note" in names(window.fixture_path)
    else:
        assert left(window, operation)
        assert "designer_note" not in names(window.fixture_path)


@pytest.mark.parametrize("operation", ["tab", "disconnect", "exit"])
def test_cancel_save_sheet_clears_pending_lifecycle_retry(qtbot, workspace, operation):
    window = workspace
    page = designer(qtbot, window)
    add_note(page)
    window.test_answers.append(QMessageBox.StandardButton.Save)
    leave(window, page, operation)
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    dialog.reject()
    idle(qtbot, window)
    assert not left(window, operation)
    assert page.dirty and getattr(page, "_after_save", None) is None
    # A later explicit save must not unexpectedly close a tab/window/connection.
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000)
    idle(qtbot, window)
    assert not left(window, operation)
    assert window.fixture_id in window.sessions and window.isVisible()


def test_read_only_profile_keeps_designer_visible_and_rejects_mutations(qtbot, workspace):
    window = workspace
    # Reconnect the owned profile in actual SQLite read-only mode.
    assert window.disconnect(window.fixture_id)
    window.configs[window.fixture_id].read_only = True
    window.connect_id(window.fixture_id)
    qtbot.waitUntil(lambda: window.fixture_id in window.sessions, timeout=10000)
    idle(qtbot, window)
    page = designer(qtbot, window)
    assert page.field_model.rowCount() > 0
    assert not page.save_action.isEnabled() and not page.add_action.isEnabled()
    before = page.draft()
    page.add_record()
    assert not page.field_model.setData(page.field_model.index(0, 0), "bad")
    page.request_save()
    assert page._save_dialog is None and page.definition == before
    assert not page.dirty


def test_pending_grid_values_reject_structure_save_and_keep_both_drafts(qtbot, workspace):
    window = workspace
    data = open_customers(qtbot, window)
    index = data.model.result.columns.index("name")
    assert data.model.setData(data.model.index(0, index), "unsaved row value")
    page = designer(qtbot, window)
    add_note(page)
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "未保存记录" in page.message.text())
    assert page.dirty and data.model.pending
    assert "designer_note" not in names(window.fixture_path)
    with closing(sqlite3.connect(window.fixture_path)) as connection:
        assert connection.execute("SELECT name FROM customers ORDER BY id LIMIT 1").fetchone()[0] != "unsaved row value"


def test_old_failed_preview_does_not_replace_current_success_or_enable_wrong_save(qtbot, workspace, monkeypatch):
    window = workspace
    page = window.new_table()
    page.field_model.setData(page.field_model.index(0, 0), "id")
    page.request_save()
    dialog = page._save_dialog
    entered, release = threading.Event(), threading.Event()
    original = schema_tools.plan_table
    old_failures = []
    original_submit = window.submit
    def capture_preview_failure(function, success=None, failure=None, *args, **kwargs):
        if kwargs.get("label") == "生成结构变更预览":
            old_failures.append(failure)
        return original_submit(function, success, failure, *args, **kwargs)
    monkeypatch.setattr(window, "submit", capture_preview_failure)
    def controlled_plan(session, source, definition):
        if definition.name == "old_invalid":
            entered.set()
            release.wait(5)
            raise ValueError("stale invalid preview")
        return original(session, source, definition)
    monkeypatch.setattr(schema_tools, "plan_table", controlled_plan)
    dialog.name_edit.setText("old_invalid")
    qtbot.waitUntil(entered.is_set)
    dialog.name_edit.setText("current_valid")
    release.set()
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    idle(qtbot, window)
    assert dialog.definition.name == "current_valid"
    assert "current_valid" in dialog.preview.toPlainText()
    assert "stale invalid" not in dialog.preview.toPlainText()
    assert "stale invalid" not in page.message.text()
    assert old_failures
    # Deliver the actual old-request root callback after the current successful
    # plan. This exercises gating, rather than relying on a later success to
    # erase the stale error again.
    old_failures[0](ValueError("late older callback"))
    assert dialog.save_button.isEnabled()
    assert "current_valid" in dialog.preview.toPlainText()
    assert "late older" not in page.message.text()
    dialog.reject()


def test_suppress_preview_preference_still_plans_and_saves_later_edits(qtbot, workspace):
    window = workspace
    page = designer(qtbot, window)
    add_note(page, "first_note")
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    dialog.suppress_preview_check.setChecked(True)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000)
    idle(qtbot, window)
    assert window.store.state["suppress_schema_save_preview"] is True
    add_note(page, "second_note")
    assert page.confirmed_plan is None
    page.request_save()
    assert page._save_dialog is None
    qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000)
    assert "second_note" in names(window.fixture_path)


@pytest.mark.parametrize("operation", ["disconnect", "exit"])
def test_queued_post_save_reload_after_connection_close_is_a_noop(qtbot, workspace, monkeypatch, operation):
    window = workspace
    page = designer(qtbot, window)
    add_note(page)
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    # Capture the real post-save callback, then choose a deterministic ordering
    # where the document/connection is closed before that callback is delivered.
    deferred = []
    monkeypatch.setattr(window, "when_idle", deferred.append)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000)
    idle(qtbot, window)
    assert deferred and "designer_note" in names(window.fixture_path)
    session = window.sessions[window.fixture_id]
    leave(window, page, operation)
    assert left(window, operation)
    assert session.connection is None
    assert window.fixture_id not in window.sessions if operation == "disconnect" else window._closed
    invoked = []
    for callback in deferred:
        def deliver(cb=callback):
            cb()
            invoked.append(True)
        QTimer.singleShot(0, deliver)
    qtbot.waitUntil(lambda: len(invoked) == len(deferred))
    assert not window.tasks and not window.pool.activeThreadCount()
    assert session.connection is None  # an exit must not reopen its old session.
    assert not window.test_errors


def test_schema_response_from_replaced_session_cannot_fill_reconnected_tree(qtbot, workspace, monkeypatch):
    window = workspace
    old_session = window.sessions[window.fixture_id]
    original_submit = window.submit
    delayed = []
    def hold_old_response(function, success=None, failure=None, *args, **kwargs):
        if kwargs.get("label") == "Loading schemas" and getattr(function, "__self__", None) is old_session:
            def hold(result):
                delayed.append((success, result))
            return original_submit(function, hold, failure, *args, **kwargs)
        return original_submit(function, success, failure, *args, **kwargs)
    monkeypatch.setattr(window, "submit", hold_old_response)
    window.load_schemas(window.fixture_id)
    qtbot.waitUntil(lambda: bool(delayed))
    idle(qtbot, window)
    assert delayed[0][1]  # the old session really read schema names successfully.
    assert window.disconnect(window.fixture_id)
    window.connect_id(window.fixture_id)
    qtbot.waitUntil(lambda: window.fixture_id in window.sessions and window.sessions[window.fixture_id] is not old_session)
    idle(qtbot, window)
    new_session = window.sessions[window.fixture_id]
    filled = []
    original_fill = window.fill_schemas
    def observe_fill(connection_id, schemas):
        filled.append((connection_id, schemas))
        return original_fill(connection_id, schemas)
    monkeypatch.setattr(window, "fill_schemas", observe_fill)
    callback, result = delayed[0]
    callback(result)
    assert filled == []
    assert window.sessions[window.fixture_id] is new_session
    assert old_session.connection is None and new_session.connection is not None
    assert not window.test_errors
