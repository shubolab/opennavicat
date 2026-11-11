"""Actual MainWindow MongoDB routing with a fresh, disposable Docker service.

No existing Navicat profile or external endpoint participates in these tests.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import threading
import uuid

from bson import Binary, Decimal128, Int64
from pymongo import MongoClient
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.mongodb_session import MongoSession, canonical_dumps, canonical_loads
from opennavicat.storage import Store
from opennavicat.ui.mongo_browser import MongoCollectionPage
from opennavicat.ui.mongo_gridfs import MongoGridFSPage
from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
from opennavicat.ui.mongo_timeseries_designer import MongoTimeSeriesDesignerPage
from opennavicat.ui.mongo_maintenance import MongoMaintenanceConfirmDialog, MongoMaintenanceResultDialog
from opennavicat.ui.window import MainWindow, TablePage
from test_mongodb_session import mongodb_service  # Reuse the owned-container fixture contract.


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=20000)


def test_mongodb_factory_constructs_the_native_adapter_without_connecting(monkeypatch):
    import opennavicat.mongodb_session as module
    monkeypatch.setattr(module, "MongoClient", lambda **_kwargs: pytest.fail("Construction must not open a client"))
    session = DatabaseSession(ConnectionConfig("owned", "Generated", driver="mongodb", ssl_mode="disable"))
    assert isinstance(session, MongoSession) and session.connection is None
    assert session.in_transaction is False


@pytest.fixture
def mongo_window(qtbot, tmp_path, monkeypatch, mongodb_service):
    config = replace(mongodb_service, id="mongo-window", name="Generated MongoDB",
                     database="onv_window_" + uuid.uuid4().hex)
    client = MongoClient(host=config.host, port=config.port, username=config.username,
                         password=config.password, authSource="admin", directConnection=True)
    database = client[config.database]
    records = [{"_id": index, "name": f"sample-{index}", "long": Int64(9007199254740993 + index),
                "decimal": Decimal128("1.00"), "binary": Binary(b"\x00\xff\r\n", 128)} for index in range(6)]
    database.docs.insert_many(records)
    database.command({"create": "owned_view", "viewOn": "docs", "pipeline": [{"$match": {"_id": 0}}]})
    errors, io_threads = [], []
    monkeypatch.setattr(MainWindow, "error", lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok)
    for name in ("connect", "schemas", "objects", "fetch_documents", "insert_document", "replace_document", "delete_document", "close"):
        original = getattr(MongoSession, name)
        def traced(*args, _original=original, **kwargs):
            io_threads.append(threading.get_ident())
            assert threading.get_ident() != threading.main_thread().ident
            return _original(*args, **kwargs)
        monkeypatch.setattr(MongoSession, name, traced)
    store = Store(tmp_path / "generated-workspace")
    store.save_connection(config)
    store.save_connection(ConnectionConfig("sqlite", "Generated SQLite", database=":memory:"))
    window = MainWindow(store)
    def before_close(widget):
        monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
        if isValid(widget):
            idle(qtbot, widget)
            widget.close()
            qtbot.waitUntil(lambda: widget._closed, timeout=15000)
            idle(qtbot, widget)
    # pytest-qt otherwise forces deleteLater immediately after close(), even
    # when closeEvent correctly deferred native session shutdown to its owner.
    qtbot.addWidget(window, before_close_func=before_close); window.show()
    try:
        assert not window.sessions
        window.connect_id(config.id)
        qtbot.waitUntil(lambda: config.id in window.sessions or bool(errors), timeout=15000)
        idle(qtbot, window)
        assert not errors
        root = window.connection_item(config.id)
        schema = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == config.database)
        window.set_active(config.id, config.database)
        schema.setExpanded(True); window.tree_expand(schema); idle(qtbot, window)
        assert not errors
        yield window, config, database, schema, errors, io_threads
    finally:
        monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
        before_close(window)
        client.drop_database(config.database); client.close()
    assert not errors


def open_collection(qtbot, window, config, name="docs", kind="mongo_collection"):
    page = window.open_table(dict(id=config.id, schema=config.database, name=name, table=name, kind=kind))
    assert isinstance(page, MongoCollectionPage) and not isinstance(page, TablePage)
    qtbot.waitUntil(lambda: page.page is not None, timeout=15000); idle(qtbot, window)
    return page


def test_real_window_mongo_tree_statistics_typed_page_and_sqlite_switch(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    assert [schema.child(i).text(0) for i in range(schema.childCount())] == [
        "集合", "视图", "函数", "索引", "MapReduce", "聚合管道", "GridFS 存储桶", "查询"]
    assert window.category_actions["table"].text() == "集合"
    assert window.category_actions["other"].isVisible() and not window.category_actions["index"].isVisible()
    assert not window.category_actions["trigger"].isVisible()
    assert not window.category_actions["backup"].isVisible()
    assert not window.table_action.isEnabled() and not window.diagram_action.isEnabled()
    window.show_objects("table"); idle(qtbot, window)
    browser = window.object_page
    assert browser.table.columnCount() == 8
    assert [browser.table.horizontalHeaderItem(i).text() for i in range(8)] == [
        "名称", "文档数", "已固定", "内存中的总大小", "对象的平均大小", "存储大小", "索引数", "索引的总大小"]
    assert [record["name"] for record in browser.records] == ["docs"]
    page = open_collection(qtbot, window, config)
    assert page.model.rowCount() == 6
    assert page.page.documents[0].document["long"] == Int64(9007199254740993)
    assert page.page.documents[0].document["binary"] == Binary(b"\x00\xff\r\n", 128)
    assert page.current_result() is None and not window.export_action.isEnabled()
    assert window.open_table(dict(id=config.id, schema=config.database, name="docs", table="docs", kind="mongo_collection")) is page
    window.connect_id("sqlite"); qtbot.waitUntil(lambda: "sqlite" in window.sessions); idle(qtbot, window)
    window.set_active("sqlite", "main"); window.show_objects("table"); idle(qtbot, window)
    assert browser.table.columnCount() == 5 and window.category_actions["table"].text() == "表"
    assert window.category_actions["trigger"].isVisible() and not window.mongo_pipeline_action.isVisible()
    assert threads and not errors


def test_real_collection_designer_creates_and_collmods_with_worker_owned_io(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    page = window.open_mongo_collection_designer(config.id, config.database)
    assert isinstance(page, MongoCollectionDesignerPage)
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=15000)
    page.name_edit.setText("ui_created")
    page.validator_editor.setPlainText('{"score": {"$gte": 0}}')
    page.level_combo.setCurrentIndex(page.level_combo.findData("strict"))
    page.action_combo.setCurrentIndex(page.action_combo.findData("error"))
    page.request_save()
    dialog = page._save_dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    dialog.save_button.click()
    idle(qtbot, window)
    assert page.snapshot.exists and page.snapshot.name == "ui_created"
    assert {"docs", "owned_view", "ui_created"} <= set(database.list_collection_names())
    assert database.ui_created.find_one({"score": 1}) is None
    with pytest.raises(Exception):
        database.ui_created.insert_one({"score": -1})

    page.validator_editor.setPlainText('{"score": {"$gte": 1}}')
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    dialog.save_button.click()
    idle(qtbot, window)
    database.ui_created.insert_one({"score": 1})
    with pytest.raises(Exception):
        database.ui_created.insert_one({"score": 0})
    assert page.snapshot.exists and page.snapshot.definition.options["validator"] == {"score": {"$gte": 1}}

    window.set_active(config.id, config.database)
    window.show_objects("table")
    idle(qtbot, window)
    browser = window.object_page
    row = next(index for index, entry in enumerate(browser.records) if entry["name"] == "ui_created")
    browser.table.setCurrentCell(row, 0)
    browser.update_actions()
    assert browser.new_action.isEnabled() and browser.design_action.isEnabled()
    assert browser.delete_action.isEnabled() and browser.clear_action.isEnabled()
    entry = dict(browser.selected(), table="ui_created")
    assert window.design_object(entry) is page
    assert not errors and threads

def test_real_mongo_view_uses_typed_readonly_workspace(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    page = open_collection(qtbot, window, config, "owned_view", "mongo_view")
    assert page.read_only and page.model.rowCount() == 1
    assert not page.add_button.isEnabled() and not page.delete_button.isEnabled() and not page.edit_action.isEnabled()
    assert all(not record.editable for record in page.page.documents)
    assert page.add_document() is None
    assert not window.table_action.isEnabled() and not window.import_action.isEnabled()


@pytest.mark.parametrize("leaving", ["tab", "disconnect", "application"])
def test_actual_mainwindow_cancel_then_save_before_leaving_mongo_document(qtbot, mongo_window, monkeypatch, leaving):
    window, config, database, schema, errors, threads = mongo_window
    page = open_collection(qtbot, window, config)
    document = page.selected_document()
    dialog = page.edit_document()
    changed = deepcopy(document.document); changed["long"] = Int64(9007199254740999)
    dialog.editor.setPlainText(canonical_dumps(changed))
    def leave():
        if leaving == "tab": window.close_tab(window.tabs.indexOf(page))
        elif leaving == "disconnect": window.disconnect(config.id)
        else: window.close()
    leave()
    assert isValid(page) and page.dirty and config.id in window.sessions and not window._closed
    assert database.docs.find_one({"_id": document.document["_id"]})["long"] == document.document["long"]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Save)
    leave()
    if leaving == "application":
        qtbot.waitUntil(lambda: window._closed, timeout=15000)
    elif leaving == "tab":
        qtbot.waitUntil(lambda: not isValid(page), timeout=15000)
        assert config.id in window.sessions
    else:
        qtbot.waitUntil(lambda: config.id not in window.sessions, timeout=15000)
        qtbot.waitUntil(lambda: not isValid(page), timeout=15000)
    idle(qtbot, window)
    actual = database.docs.find_one({"_id": document.document["_id"]})
    assert isinstance(actual["long"], Int64) and actual["long"] == Int64(9007199254740999)
    assert not errors


def test_real_mongodb_query_page_runs_native_find_without_sql_formatting(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    text = 'db.getCollection("docs").find({}).sort({"_id": 1}).limit(2)'
    page = window.new_query(sql=text, connection_id=config.id, query_language='legacy')
    assert page.schema == config.database and page.editor.language == "text"
    assert not page.format_button.isEnabled() and page.format_sql() is False
    assert page.editor.toPlainText() == text and not page.supports_explain
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(window.store.history()), timeout=15000); idle(qtbot, window)
    assert window.store.history()[0]["status"] == "success", page.info.toPlainText()
    result = page.current_result()
    assert result is not None and len(result.rows) == 2
    first = canonical_loads(result.rows[0][0])
    assert first["long"] == Int64(9007199254740993) and isinstance(first["long"], Int64)
    window.transaction("BEGIN"); idle(qtbot, window)
    assert not window.sessions[config.id].in_transaction and not errors


def test_mongodb_document_export_is_exact_and_keeps_previous_file_on_failure(qtbot, mongo_window, tmp_path, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    page = open_collection(qtbot, window, config)
    destination = tmp_path / "generated-export.bson"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(destination), "BSON (*.bson)"))
    page.export_document(); idle(qtbot, window)
    expected = page.selected_document().raw_bson
    assert destination.read_bytes() == expected
    import opennavicat.ui.mongo_browser as module
    def fail_replace(*_args):
        raise OSError("Generated export replacement failure")
    destination.write_bytes(b"previous complete export")
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", fail_replace)
        page.export_document(); idle(qtbot, window)
    assert destination.read_bytes() == b"previous complete export"
    assert "replacement failure" in page.message.text()
    assert not list(tmp_path.glob(".opennavicat-export-*")) and not errors


def test_actual_mainwindow_gridfs_tree_routing_and_native_download(qtbot, mongo_window, tmp_path, monkeypatch):
    from gridfs import GridFSBucket
    window, config, database, schema, errors, threads = mongo_window
    bucket = GridFSBucket(database, bucket_name="generated_files")
    payload = b"generated GridFS bytes\x00\xff\r\n"
    bucket.upload_from_stream("generated.bin", payload)
    window.load_objects(schema, schema.data(0, Qt.ItemDataRole.UserRole)); idle(qtbot, window)
    gridfs_category = next(schema.child(i) for i in range(schema.childCount()) if schema.child(i).text(0) == "GridFS 存储桶")
    assert [gridfs_category.child(i).text(0) for i in range(gridfs_category.childCount())] == ["generated_files"]
    info = gridfs_category.child(0).data(0, Qt.ItemDataRole.UserRole)
    page = window.open_table(info)
    assert isinstance(page, MongoGridFSPage) and not isinstance(page, TablePage)
    qtbot.waitUntil(lambda: page.page is not None, timeout=15000); idle(qtbot, window)
    assert len(page.page.documents) == 1 and page.model.columnCount() == 9
    assert window.tabs.tabText(window.tabs.indexOf(page)).endswith(" - GridFS")
    assert not window.import_action.isEnabled() and not window.export_action.isEnabled()
    assert window.save_sql(page=page) is False
    assert window.open_table(info) is page
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(tmp_path))
    page.download_file(); idle(qtbot, window)
    assert (tmp_path / "generated.bin").read_bytes() == payload and not errors
    window.disconnect(config.id)
    qtbot.waitUntil(lambda: config.id not in window.sessions, timeout=15000)
    qtbot.waitUntil(lambda: not isValid(page), timeout=15000)


def test_mongo_query_ui_reports_actual_partial_writes_and_keeps_server_error(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    text = "db.runCommand({insert:'docs',ordered:false,documents:[{_id:901},{_id:0},{_id:902}]})"
    page = window.new_query(sql=text, connection_id=config.id, query_language='legacy')
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(window.store.history()), timeout=15000); idle(qtbot, window)
    assert window.store.history()[0]["status"] == "error"
    assert page._last_results[0].affected == 2 and page._last_results[0].partial is True
    assert page._last_results[0].truncated is False
    assert page.summary.item(0,1).text() == "部分完成"
    assert page.summary.item(0,2).text() == "2"
    assert "11000" in page.info.toPlainText()
    assert database.docs.count_documents({"_id":{"$in":[901,902]}}) == 2 and not errors


def test_refresh_keeps_mongodb_index_context_and_other_toolbar_label(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    window.show_objects("mongo_index"); idle(qtbot, window)
    assert window.object_page.kind == "mongo_index" and window.category_actions["other"].text() == "索引"
    window.refresh_tree(); idle(qtbot, window)
    assert window.object_page.kind == "mongo_index" and window.object_page.table.columnCount() == 9
    assert window.category_actions["other"].text() == "索引" and not errors


def test_closing_mongo_connection_cannot_queue_new_metadata_or_after_callback(qtbot, mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    window.show_objects("mongo_index"); idle(qtbot, window)
    session = window.sessions[config.id]
    close = session.close
    entered, release = threading.Event(), threading.Event()
    def slow_close():
        entered.set()
        assert release.wait(timeout=10)
        close()
    monkeypatch.setattr(session, "close", slow_close)
    try:
        window.disconnect(config.id)
        qtbot.waitUntil(entered.is_set, timeout=5000)
        count = len(window.tasks)
        assert not window.category_actions["table"].isEnabled()
        window.show_objects("table")
        window.object_page.refresh()
        window.load_schemas(config.id)
        window.load_objects(schema, schema.data(0, Qt.ItemDataRole.UserRole))
        window.connect_id(config.id, lambda: pytest.fail("Closing session cannot be reused"))
        assert len(window.tasks) == count and not errors
    finally:
        release.set()
    qtbot.waitUntil(lambda: config.id not in window.sessions, timeout=15000); idle(qtbot, window)
    assert not errors


def test_real_window_stop_inflight_collection_uses_cancel_pool_and_preserves_draft(qtbot, mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    page = window.open_mongo_collection_designer(config.id, config.database)
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=15000)
    page.name_edit.setText("blocked_collection")
    page.validator_editor.setPlainText('{"n": {"$gte": 0}}')
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
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
                {"$currentOp": {"allUsers": False}},
                {"$match": {"command.comment": marker, "command.create": "blocked_collection"}},
            ])
            return any(rows)
        qtbot.waitUntil(active_create, timeout=15000)
        marker = session.cancellation_marker()
        window.stop_action.trigger()
        qtbot.waitUntil(lambda: not window._mongo_collection_jobs and not window._cancelling, timeout=15000)
    finally:
        database.client.admin.command({"fsyncUnlock": 1})
        idle(qtbot, window)
    assert calls == [{"expected_comment": marker}]
    assert page.dirty and not page.loading and "取消" in page.message.text()
    assert "blocked_collection" not in database.list_collection_names()

    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert "blocked_collection" in database.list_collection_names() and not page.dirty


def test_real_window_clear_and_drop_collection_require_permanent_confirmation(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    window.set_active(config.id, config.database)
    window.show_objects("table")
    idle(qtbot, window)
    browser = window.object_page
    row = next(index for index, entry in enumerate(browser.records) if entry["name"] == "docs")
    browser.table.setCurrentCell(row, 0)
    browser.update_actions()
    assert browser.delete_action.isEnabled() and browser.clear_action.isEnabled()
    docs_info = dict(browser.selected(), table="docs")

    assert window.clear_mongo_collection(docs_info) is True
    qtbot.waitUntil(lambda: len(window._mongo_collection_operation_dialogs) == 1, timeout=15000)
    dialog = next(iter(window._mongo_collection_operation_dialogs))
    assert not dialog.confirm_button.isEnabled() and '"delete"' in dialog.plan.command_text
    dialog.permanent_check.setChecked(True)
    qtbot.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert database.docs.count_documents({}) == 0
    assert "_id_" in database.docs.index_information()

    database.drop_ui.insert_one({"_id": 1, "kept": True})
    drop_info = dict(docs_info, name="drop_ui", table="drop_ui")
    assert window.delete_mongo_collection(drop_info) is True
    qtbot.waitUntil(lambda: len(window._mongo_collection_operation_dialogs) == 1, timeout=15000)
    dialog = next(iter(window._mongo_collection_operation_dialogs))
    assert not dialog.confirm_button.isEnabled() and '"drop"' in dialog.plan.command_text
    dialog.permanent_check.setChecked(True)
    qtbot.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert "drop_ui" not in database.list_collection_names()
    assert not errors and threads


def test_real_window_rename_collection_preserves_data_indexes_and_uuid(qtbot, mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    database.rename_ui.insert_many([{"_id": 1, "n": 1}, {"_id": 2, "n": 2}])
    database.rename_ui.create_index([("n", -1)], name="rename_n_desc")
    before = database.command("listCollections", filter={"name": "rename_ui"})["cursor"]["firstBatch"][0]["info"]["uuid"]
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("renamed_ui", True))
    info = {"id": config.id, "schema": config.database, "name": "rename_ui", "table": "rename_ui",
            "kind": "mongo_collection"}
    assert window.rename_mongo_collection(info) is True
    qtbot.waitUntil(lambda: len(window._mongo_collection_operation_dialogs) == 1, timeout=15000)
    dialog = next(iter(window._mongo_collection_operation_dialogs))
    assert not dialog.confirm_button.isEnabled()
    assert '"renameCollection"' in dialog.plan.command_text and '"dropTarget": false' in dialog.plan.command_text
    dialog.permanent_check.setChecked(True)
    qtbot.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert "rename_ui" not in database.list_collection_names()
    assert database.renamed_ui.count_documents({}) == 2
    assert "rename_n_desc" in database.renamed_ui.index_information()
    after = database.command("listCollections", filter={"name": "renamed_ui"})["cursor"]["firstBatch"][0]["info"]["uuid"]
    assert after == before
    assert not errors and threads



def test_real_window_timeseries_designer_creates_and_updates_bucketing_and_expiry(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    page = window.open_mongo_timeseries_designer(config.id, config.database)
    assert isinstance(page, MongoTimeSeriesDesignerPage)
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=15000)
    page.name_edit.setText("ts_ui")
    page.time_field_edit.setText("time")
    page.meta_field_edit.setText("sensor")
    page.granularity_combo.setCurrentIndex(page.granularity_combo.findData("minutes"))
    page.expire_edit.setText("3600")
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert page.snapshot.exists and page.snapshot.name == "ts_ui"
    spec = database.command("listCollections", filter={"name": "ts_ui"})["cursor"]["firstBatch"][0]
    assert spec["type"] == "timeseries"
    assert spec["options"]["timeseries"]["granularity"] == "minutes"
    assert spec["options"]["expireAfterSeconds"] == 3600
    database.ts_ui.insert_one({"time": __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc),
                               "sensor": "a", "value": 1})
    assert database.ts_ui.count_documents({}) == 1

    page.granularity_combo.setCurrentIndex(page.granularity_combo.findData("hours"))
    page.expire_edit.setText("7200")
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    assert len(dialog.plan.commands) == 2 and "collectionUUID" not in dialog.plan.commands[0]
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    spec = database.command("listCollections", filter={"name": "ts_ui"})["cursor"]["firstBatch"][0]
    assert spec["options"]["timeseries"]["granularity"] == "hours"
    assert spec["options"]["expireAfterSeconds"] == 7200
    assert not errors and threads

    window.set_active(config.id, config.database)
    window.show_objects("table")
    idle(qtbot, window)
    browser = window.object_page
    assert browser.new_timeseries_action.isEnabled()
    row = next(index for index, entry in enumerate(browser.records) if entry["name"] == "ts_ui")
    browser.table.setCurrentCell(row, 0)
    browser.update_actions()
    assert browser.design_action.isEnabled()
    ts_entry = dict(browser.selected(), table="ts_ui")
    assert window.design_object(ts_entry) is page


def test_real_window_copy_collection_data_and_structure_with_staging(qtbot, mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    database.docs.create_index([("name", 1)], name="copy_name_idx")
    info = {"id": config.id, "schema": config.database, "name": "docs", "table": "docs", "kind": "mongo_collection"}
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("docs_copy_data", True))
    assert window.copy_mongo_collection(info, "data") is True
    qtbot.waitUntil(lambda: len(window._mongo_collection_copy_dialogs) == 1, timeout=15000)
    dialog = next(iter(window._mongo_collection_copy_dialogs))
    assert not dialog.copy_button.isEnabled()
    assert dialog.plan.staging_name.startswith("__opennavicat_copy_")
    dialog.ack_check.setChecked(True)
    qtbot.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert database.docs_copy_data.count_documents({}) == database.docs.count_documents({})
    assert "copy_name_idx" in database.docs_copy_data.index_information()
    assert dialog.plan.staging_name not in database.list_collection_names()

    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("docs_copy_structure", True))
    assert window.copy_mongo_collection(info, "structure") is True
    qtbot.waitUntil(lambda: len(window._mongo_collection_copy_dialogs) == 1, timeout=15000)
    dialog = next(iter(window._mongo_collection_copy_dialogs))
    dialog.ack_check.setChecked(True)
    qtbot.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    assert database.docs_copy_structure.count_documents({}) == 0
    assert "copy_name_idx" in database.docs_copy_structure.index_information()
    assert not errors and threads


def test_real_window_collection_maintenance_validate_full_and_reindex(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    info = {"id": config.id, "schema": config.database, "name": "docs", "table": "docs", "kind": "mongo_collection"}
    before_count = database.docs.count_documents({})
    before_indexes = database.docs.index_information()

    assert window.validate_mongo_collection(info, False) is True
    idle(qtbot, window)
    qtbot.waitUntil(lambda: any(isinstance(dialog, MongoMaintenanceResultDialog) for dialog in window._mongo_maintenance_dialogs), timeout=15000)
    result = next(dialog for dialog in window._mongo_maintenance_dialogs if isinstance(dialog, MongoMaintenanceResultDialog))
    assert result.windowTitle() == '集合验证结果' and 'valid' in result.report.toPlainText()
    result.reject(); idle(qtbot, window)

    assert window.validate_mongo_collection(info, True) is True
    qtbot.waitUntil(lambda: any(isinstance(dialog, MongoMaintenanceConfirmDialog) for dialog in window._mongo_maintenance_dialogs), timeout=15000)
    confirm = next(dialog for dialog in window._mongo_maintenance_dialogs if isinstance(dialog, MongoMaintenanceConfirmDialog))
    assert 'checkBSONConformance' in confirm.preview.toPlainText()
    confirm.ack.setChecked(True)
    qtbot.mouseClick(confirm.confirm, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    qtbot.waitUntil(lambda: any(isinstance(dialog, MongoMaintenanceResultDialog) for dialog in window._mongo_maintenance_dialogs), timeout=15000)
    result = next(dialog for dialog in window._mongo_maintenance_dialogs if isinstance(dialog, MongoMaintenanceResultDialog))
    assert result.windowTitle() == '完整集合验证结果'
    result.reject(); idle(qtbot, window)

    assert window.reindex_mongo_collection(info) is True
    qtbot.waitUntil(lambda: any(isinstance(dialog, MongoMaintenanceConfirmDialog) for dialog in window._mongo_maintenance_dialogs), timeout=15000)
    confirm = next(dialog for dialog in window._mongo_maintenance_dialogs if isinstance(dialog, MongoMaintenanceConfirmDialog))
    assert 'reIndex' in confirm.preview.toPlainText()
    confirm.ack.setChecked(True)
    qtbot.mouseClick(confirm.confirm, Qt.MouseButton.LeftButton)
    idle(qtbot, window)
    qtbot.waitUntil(lambda: any(isinstance(dialog, MongoMaintenanceResultDialog) for dialog in window._mongo_maintenance_dialogs), timeout=15000)
    result = next(dialog for dialog in window._mongo_maintenance_dialogs if isinstance(dialog, MongoMaintenanceResultDialog))
    assert result.windowTitle() == '重建集合索引结果'
    result.reject(); idle(qtbot, window)
    assert database.docs.count_documents({}) == before_count
    assert database.docs.index_information() == before_indexes
    assert not errors
