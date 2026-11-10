"""MainWindow index workflows against only a fresh fixture-owned MongoDB."""
from copy import deepcopy
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox
from shiboken6 import isValid

from opennavicat import mongodb_indexes as tools
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoSession, canonical_dumps
from opennavicat.ui.mongo_index_designer import MongoIndexDesignerPage
from opennavicat.ui.window import MainWindow
from test_mongo_index_designer_ui import add_keys
from test_mongo_window import idle, mongo_window, mongodb_service, open_collection


def settle(qtbot, window):
    # Successful/partial DDL schedules metadata refresh only after its task ends.
    for _ in range(3):
        idle(qtbot, window)
        qtbot.wait(35)
    idle(qtbot, window)


@pytest.fixture
def indexed(mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    database.other.insert_one({"_id": 0, "name": "other", "long": 1})
    database.docs.create_index([("name", 1)], name="same_name")
    database.other.create_index([("long", -1)], name="same_name")
    for name in ("new_index", "load_index", "plan_index", "plan_drop", "apply_plan"):
        original = getattr(tools, name)
        def traced(*args, _original=original, **kwargs):
            assert threading.get_ident() != threading.main_thread().ident
            threads.append(threading.get_ident())
            return _original(*args, **kwargs)
        monkeypatch.setattr(tools, name, traced)
    return mongo_window


def designer(qtbot, window, config, collection="docs", name="same_name"):
    page = window.open_mongo_index_designer(config.id, config.database, collection, name)
    assert isinstance(page, MongoIndexDesignerPage)
    qtbot.waitUntil(lambda: page.snapshot is not None, timeout=15000)
    settle(qtbot, window)
    return page


def browser(qtbot, window, config, collection="docs", name="same_name"):
    window.set_active(config.id, config.database)
    window.show_objects("index")
    settle(qtbot, window)
    page = window.object_page
    row = next(i for i, entry in enumerate(page.records)
               if (entry["name"], entry["collection"]) == (name, collection))
    page.table.setCurrentCell(row, 0)
    settle(qtbot, window)
    return page, row


def sheet(qtbot, window, page, name=None):
    page.request_save()
    dialog = page._save_dialog
    assert dialog is not None
    if name is not None:
        dialog.name_edit.setText(name)
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    settle(qtbot, window)
    return dialog


def accept_save(qtbot, window, page, name=None):
    dialog = sheet(qtbot, window, page, name)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    settle(qtbot, window)
    return dialog


def test_real_window_index_columns_sizes_same_name_selection_and_draft_identity(qtbot, indexed):
    window, config, database, schema, errors, threads = indexed
    objects, _ = browser(qtbot, window, config, "other")
    assert [objects.table.horizontalHeaderItem(i).text() for i in range(9)] == [
        "名称", "索引类型", "集合名称", "唯一键", "背景", "稀疏", "在此之后过期（秒）", "版本", "大小"]
    assert not objects.open_action.isVisible() and objects.design_action.isEnabled()
    row = objects.table.currentRow()
    assert objects.table.item(row, 1).text() == "常规"
    assert objects.table.item(row, 8).text() != "—"
    assert database.command("collStats", "other")["indexSizes"]["same_name"] > 0
    objects.refresh(); settle(qtbot, window)
    assert objects.selected()["collection"] == "other"
    # Exercise the same double-click route as the actual object table.
    item = objects.table.item(objects.table.currentRow(), 0)
    objects.table.scrollToItem(item)
    qtbot.mouseClick(objects.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=objects.table.visualItemRect(item).center())
    qtbot.mouseDClick(objects.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=objects.table.visualItemRect(item).center())
    settle(qtbot, window)
    other = window.tabs.currentWidget()
    assert isinstance(other, MongoIndexDesignerPage) and other.snapshot.collection == "other"
    docs = designer(qtbot, window, config)
    assert docs is not other
    docs.collection_combo.setCurrentText("other")
    assert docs.dirty and docs.collection == "other"
    assert designer(qtbot, window, config, "other") is other
    assert designer(qtbot, window, config) is docs
    # Discard/reload reads the original identity, not the edited target.
    window.load_mongo_index_designer(docs); settle(qtbot, window)
    assert docs.snapshot.collection == "docs" and docs.collection == "docs" and not docs.dirty
    assert list(database.docs.index_information()["same_name"]["key"]) == [("name", 1)]
    assert threads and not errors


def test_real_window_new_toolbar_context_cancel_then_ordered_index_save(qtbot, indexed):
    window, config, database, *_ = indexed
    objects, _ = browser(qtbot, window, config)
    window.connect_id("sqlite"); settle(qtbot, window)
    window.set_active("sqlite", "main")
    # The object browser carries an explicit target even while another profile
    # owns the connection picker (for example, a retained document context).
    objects.set_context(config.id, config.database, "mongo_index")
    settle(qtbot, window)
    assert window.active_id == "sqlite" and objects.connection_id == config.id
    objects.new_action.trigger(); settle(qtbot, window)
    page = window.tabs.currentWidget()
    assert isinstance(page, MongoIndexDesignerPage) and page.connection_id == config.id
    page.collection_combo.setCurrentText("docs")
    add_keys(qtbot, page, [("long", -1), ("name", 1)])
    dialog = sheet(qtbot, window, page, "new_ordered")
    assert "createIndex(" in dialog.preview.toPlainText()
    qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    settle(qtbot, window)
    assert page.dirty and page.definition.name == "" and "new_ordered" not in database.docs.index_information()
    accept_save(qtbot, window, page, "new_ordered")
    assert not page.dirty and page.snapshot.name == "new_ordered"
    assert list(database.docs.index_information()["new_ordered"]["key"]) == [("long", -1), ("name", 1)]


def test_real_window_index_save_preserves_open_document_and_exact_draft(qtbot, indexed):
    window, config, database, *_ = indexed
    data = open_collection(qtbot, window, config)
    edit = data.edit_document()
    changed = deepcopy(data.selected_document().document)
    changed["name"] = "unsaved local document"
    text = canonical_dumps(changed)
    edit.editor.setPlainText(text)
    assert data.dirty
    page = designer(qtbot, window, config)
    assert "字段\nname\n" in window.inspector.details.text()
    assert "版本\n2\n" in window.inspector.details.text()
    page.sparse_check.setChecked(True)
    assert "稀疏\n是\n" in window.inspector.details.text()
    accept_save(qtbot, window, page)
    assert database.docs.index_information()["same_name"]["sparse"] is True
    assert data.dirty and data._dialog is edit and edit.editor.toPlainText() == text
    assert database.docs.find_one({"_id": changed["_id"]})["name"] != changed["name"]


def test_real_window_failed_rebuild_shows_actual_steps_refreshes_list_preserves_draft(qtbot, indexed):
    window, config, database, *_ = indexed
    database.docs.update_many({}, {"$set": {"duplicated": "same"}})
    database.docs.create_index([("duplicated", 1)], name="cannot_be_unique")
    objects, _ = browser(qtbot, window, config, name="cannot_be_unique")
    page = designer(qtbot, window, config, name="cannot_be_unique")
    page.unique_check.setChecked(True)
    accept_save(qtbot, window, page)
    assert page.dirty and not page.loading and page.definition.options["unique"] is True
    assert "服务器已确认" in page.message.text() and "服务器报错" in page.message.text()
    assert "当前状态" in page.message.text()
    assert "cannot_be_unique" not in database.docs.index_information()
    assert all(row["name"] != "cannot_be_unique" for row in objects.records)
    assert "same_name" in database.other.index_information()


def test_real_window_index_delete_checkbox_cancel_and_keyboard_binding(qtbot, indexed):
    window, config, database, *_ = indexed
    objects, row = browser(qtbot, window, config)
    objects.table.setFocus()
    qtbot.keyClick(objects.table, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: bool(window._mongo_index_delete_dialogs), timeout=15000)
    settle(qtbot, window)
    dialog = next(iter(window._mongo_index_delete_dialogs))
    assert not dialog.delete_button.isEnabled()
    dialog.accept()
    assert dialog.isVisible() and "same_name" in database.docs.index_information()
    dialog.reject(); settle(qtbot, window)
    assert not window._mongo_index_delete_dialogs and "same_name" in database.docs.index_information()
    # Editing the browser filter must never invoke the table's delete shortcut.
    objects.search.setText("same_name"); objects.search.setFocus()
    qtbot.keyClick(objects.search, Qt.Key.Key_Backspace)
    settle(qtbot, window)
    assert not window._mongo_index_delete_dialogs
    objects.search.clear(); objects.table.setCurrentCell(row, 0)
    window.activateWindow(); objects.table.setFocus(); qtbot.wait(25)
    assert objects.delete_action.isEnabled() and objects.table.hasFocus()
    qtbot.keyClick(objects.table, Qt.Key.Key_Backspace)
    qtbot.waitUntil(lambda: bool(window._mongo_index_delete_dialogs), timeout=15000)
    settle(qtbot, window)
    dialog = next(iter(window._mongo_index_delete_dialogs))
    assert "collectionUUID" in dialog.preview.toPlainText()
    dialog.permanent_check.setChecked(True)
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    settle(qtbot, window)
    assert "same_name" not in database.docs.index_information()
    assert "same_name" in database.other.index_information()
    assert not window._mongo_index_delete_dialogs


def test_real_window_stop_queued_index_does_no_ddl_and_does_not_poison_next_operation(qtbot, indexed, monkeypatch):
    window, config, database, *_ = indexed
    page = designer(qtbot, window, config)
    page.sparse_check.setChecked(True)
    dialog = sheet(qtbot, window, page)
    entered, release = threading.Event(), threading.Event()
    cancelled_calls = []
    original = MongoSession.cancel
    def cancel(self, *args, **kwargs):
        cancelled_calls.append(kwargs)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(MongoSession, "cancel", cancel)
    def block():
        entered.set()
        assert release.wait(15)
    window.submit(block)
    qtbot.waitUntil(entered.is_set)
    try:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        assert window._mongo_index_jobs and window.stop_action.isEnabled()
        window.stop_action.trigger()
        assert all(event.is_set() and not started.is_set() for _, event, started in window._mongo_index_jobs.values())
    finally:
        release.set()
    settle(qtbot, window)
    assert not window._mongo_index_jobs and not cancelled_calls
    assert page.dirty and not page.loading and "取消" in page.message.text()
    assert not database.docs.index_information()["same_name"].get("sparse")
    results = []
    session = window.sessions[config.id]
    window.submit(lambda: session.execute('db.docs.find({"_id":0})'), results.extend)
    settle(qtbot, window)
    assert results and results[0].rows
    accept_save(qtbot, window, page)
    assert database.docs.index_information()["same_name"]["sparse"] is True


def test_real_window_stale_sheet_name_error_does_not_replace_new_plan(qtbot, indexed, monkeypatch):
    window, config, database, *_ = indexed
    page = designer(qtbot, window, config, name="")
    add_keys(qtbot, page, [("long", 1)])
    entered, release = threading.Event(), threading.Event()
    original = tools.plan_index
    def plan(session, snapshot, definition):
        if definition.name == "old_name":
            entered.set()
            assert release.wait(15)
            raise DatabaseError("stale old-name preview error")
        return original(session, snapshot, definition)
    monkeypatch.setattr(tools, "plan_index", plan)
    page.request_save(); dialog = page._save_dialog
    dialog.name_edit.setText("old_name")
    qtbot.waitUntil(entered.is_set, timeout=10000)
    try:
        dialog.name_edit.setText("current_name")
    finally:
        release.set()
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    settle(qtbot, window)
    assert dialog.plan.definition.name == "current_name"
    assert "stale old-name" not in page.message.text() + dialog.preview.toPlainText()
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton); settle(qtbot, window)
    assert "current_name" in database.docs.index_information() and "old_name" not in database.docs.index_information()


def test_real_window_stop_inflight_index_cancels_its_captured_native_command(qtbot, indexed, monkeypatch):
    window, config, database, *_ = indexed
    page = designer(qtbot, window, config, name="")
    add_keys(qtbot, page, [("long", 1)])
    dialog = sheet(qtbot, window, page, "blocked_create")
    session = window.sessions[config.id]
    calls = []
    original = MongoSession.cancel
    def cancel(self, *args, **kwargs):
        assert threading.get_ident() != threading.main_thread().ident
        calls.append(deepcopy(kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(MongoSession, "cancel", cancel)
    database.client.admin.command({"fsync": 1, "lock": True})
    try:
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        def active_create():
            marker = session.cancellation_marker()
            if marker is None:
                return False
            rows = database.client.admin.aggregate([
                {"$currentOp": {"allUsers": False}}, {"$match": {"command.comment": marker}}])
            return any(row.get("command", {}).get("createIndexes") and row["command"].get("indexes") for row in rows)
        qtbot.waitUntil(active_create, timeout=15000)
        marker = session.cancellation_marker()
        window.stop_action.trigger()
        qtbot.waitUntil(lambda: not window._mongo_index_jobs and not window._cancelling, timeout=15000)
    finally:
        database.client.admin.command({"fsyncUnlock": 1})
        settle(qtbot, window)
    assert calls == [{"expected_comment": marker}]
    assert page.dirty and not page.loading and "实际步骤" in page.message.text()
    assert "blocked_create" not in database.docs.index_information()
    accept_save(qtbot, window, page)
    assert "blocked_create" in database.docs.index_information() and not page.dirty


@pytest.mark.parametrize("leaving", ["tab", "disconnect", "application"])
def test_real_window_index_cancel_then_save_before_leaving(qtbot, indexed, monkeypatch, leaving):
    window, config, database, *_ = indexed
    page = designer(qtbot, window, config)
    page.sparse_check.setChecked(True)
    def leave():
        if leaving == "tab":
            window.close_tab(window.tabs.indexOf(page))
        elif leaving == "disconnect":
            window.disconnect(config.id)
        else:
            window.close()
    leave(); settle(qtbot, window)
    assert window.tabs.indexOf(page) >= 0 and config.id in window.sessions and not window._closed
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    leave()
    qtbot.waitUntil(lambda: page._save_dialog is not None, timeout=15000)
    page._save_dialog.reject(); settle(qtbot, window)
    assert page.dirty and config.id in window.sessions and not window._closed
    leave()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    settle(qtbot, window)
    assert database.docs.index_information()["same_name"]["sparse"] is True
    if leaving == "application":
        assert window._closed
    elif leaving == "disconnect":
        assert config.id not in window.sessions and (not isValid(page) or window.tabs.indexOf(page) < 0)
    else:
        assert not isValid(page) or window.tabs.indexOf(page) < 0


def test_real_window_index_builtin_and_readonly_are_viewable_without_mutation(qtbot, indexed):
    window, config, database, *_ = indexed
    objects, _ = browser(qtbot, window, config, name="_id_")
    assert objects.design_action.isEnabled() and not objects.delete_action.isEnabled()
    builtin = designer(qtbot, window, config, name="_id_")
    assert builtin._read_only and not builtin.add_action.isEnabled()
    assert builtin.snapshot.raw_spec and not builtin.dirty
    window.configs[config.id].read_only = True
    window.sessions[config.id].config.read_only = True
    objects, _ = browser(qtbot, window, config)
    assert objects.design_action.isEnabled() and not objects.new_action.isEnabled() and not objects.delete_action.isEnabled()
    page = designer(qtbot, window, config)
    assert page._read_only and not page.save_action.isEnabled()
    page.add_field(); page.request_save()
    assert not page.dirty and page._save_dialog is None
    assert list(database.docs.index_information()["same_name"]["key"]) == [("name", 1)]


def test_real_window_index_inspector_never_queues_reads_after_disconnect_started(qtbot, indexed, monkeypatch):
    window, config, database, *_ = indexed
    objects, _ = browser(qtbot, window, config)
    window.inspector.show()
    entered, release = threading.Event(), threading.Event()
    original = MongoSession.close
    def close(self):
        if self is window.sessions.get(config.id):
            entered.set()
            assert release.wait(15)
        return original(self)
    monkeypatch.setattr(MongoSession, "close", close)
    assert window.disconnect(config.id)
    qtbot.waitUntil(entered.is_set, timeout=10000)
    try:
        pending = set(window.tasks)
        row = next(i for i, entry in enumerate(objects.records)
                   if (entry["collection"], entry["name"]) == ("other", "same_name"))
        objects.table.setCurrentCell(row, 0)
        assert set(window.tasks) == pending
        assert config.id in window._closing_connections
    finally:
        release.set()
    settle(qtbot, window)
    assert config.id not in window.sessions and not window._closing_connections


def test_real_window_preview_preference_only_accepted_and_existing_index_saves_without_sheet(qtbot, indexed):
    window, config, database, *_ = indexed
    other = designer(qtbot, window, config, "other")
    page = designer(qtbot, window, config)
    page.sparse_check.setChecked(True)
    dialog = sheet(qtbot, window, page)
    dialog.suppress_preview_check.setChecked(True)
    dialog.reject(); settle(qtbot, window)
    assert window.store.state.get("suppress_schema_save_preview") is not True
    assert not page.suppress_save_preview and not other.suppress_save_preview
    dialog = sheet(qtbot, window, page)
    dialog.suppress_preview_check.setChecked(True)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton); settle(qtbot, window)
    assert window.store.state["suppress_schema_save_preview"] is True
    assert page.suppress_save_preview and other.suppress_save_preview
    page.sparse_check.setChecked(False)
    page.request_save()
    assert page._save_dialog is None
    settle(qtbot, window)
    assert not page.dirty and not database.docs.index_information()["same_name"].get("sparse")
    window.set_schema_preview_suppressed(False)
    assert not page.suppress_save_preview and not other.suppress_save_preview


def test_real_window_index_references_restore_disconnected_then_explicitly_load(qtbot, indexed, monkeypatch):
    window, config, database, *_ = indexed
    first = designer(qtbot, window, config)
    designer(qtbot, window, config, "other")
    first.collection_combo.setCurrentText("other")
    # Unsaved retargeting is deliberately not a persisted database identity.
    refs = window.mongo_index_references()
    assert [(r["collection"], r["name"]) for r in refs] == [("docs", "same_name"), ("other", "same_name")]
    assert all(set(r) == {"connection_id", "schema", "collection", "name"} for r in refs)
    store = window.store
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    window.close(); qtbot.waitUntil(lambda: window._closed, timeout=15000)
    assert store.state["mongo_index_designers"] == refs
    restored = MainWindow(store)
    def before_close(widget):
        if isValid(widget) and not widget._closed:
            settle(qtbot, widget); widget.close()
            qtbot.waitUntil(lambda: widget._closed, timeout=15000); idle(qtbot, widget)
    qtbot.addWidget(restored, before_close_func=before_close)
    try:
        restored.show(); settle(qtbot, restored)
        pages = [restored.tabs.widget(i) for i in range(restored.tabs.count())
                 if isinstance(restored.tabs.widget(i), MongoIndexDesignerPage)]
        assert len(pages) == 2 and not restored.sessions
        assert all(page.snapshot is None and page._pending_connection for page in pages)
        restored.connect_id(config.id); settle(qtbot, restored)
        assert [page.snapshot.collection for page in pages] == ["docs", "other"]
        assert all(not page._pending_connection and not page.dirty for page in pages)
        assert list(pages[0].snapshot.definition.keys)[0].field == "name"
        assert list(pages[1].snapshot.definition.keys)[0].field == "long"
    finally:
        before_close(restored)
