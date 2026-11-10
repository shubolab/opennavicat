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
from PySide6.QtWidgets import QFileDialog, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.mongodb_session import MongoSession, canonical_dumps, canonical_loads
from opennavicat.storage import Store
from opennavicat.ui.mongo_browser import MongoCollectionPage
from opennavicat.ui.mongo_gridfs import MongoGridFSPage
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
    page = window.new_query(sql=text, connection_id=config.id)
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
    page = window.new_query(sql=text, connection_id=config.id)
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
