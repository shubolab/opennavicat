"""Real MainWindow routing for six drivers, owned TDS and disposable Redis.

SQL Server tests opt in through OPENNAVICAT_TEST_SQLSERVER_ENDPOINT, or the
ignored local owned-fixture receipt. Redis tests start their own loopback server
and never use a user's service or the manual reference database.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import uuid

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.redis_session import RedisSession
from opennavicat.sqlserver_session import SqlServerSession
from opennavicat.storage import Store
from opennavicat.ui.dialogs import RowDialog
from opennavicat.ui.redis_browser import RedisBrowserPage
from opennavicat.ui.window import MainWindow, QueryPage, TablePage


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=20000)


@pytest.fixture
def window_factory(qtbot, tmp_path, monkeypatch):
    windows, errors = [], []
    monkeypatch.setattr(MainWindow, "error", lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    def unexpected(_parent, title, message, *_a, **_k):
        errors.append(f"{title}: {message}")
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "warning", unexpected)
    monkeypatch.setattr(QMessageBox, "critical", unexpected)
    def create(*configs):
        store = Store(tmp_path / str(len(windows)))
        for config in configs:
            store.save_connection(replace(config))
        window = MainWindow(store)
        window.test_errors = errors
        windows.append(window)
        qtbot.addWidget(window)
        window.resize(1200, 800)
        window.show()
        assert not window.sessions and not window.tasks
        return window
    yield create
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    for window in windows:
        idle(qtbot, window)
        for index in range(window.tabs.count()):
            page = window.tabs.widget(index)
            if isinstance(page, TablePage) and page.model:
                page.model.discard()
            if isinstance(page, RedisBrowserPage) and page.key_page:
                page.key_page.discard_changes()
        for session in window.sessions.values():
            if session.in_transaction:
                session.execute("ROLLBACK")
        window.close()
        assert window._closed
    assert errors == []


def connect(qtbot, window, config):
    window.connect_id(config.id)
    qtbot.waitUntil(lambda: config.id in window.sessions or bool(window.test_errors), timeout=20000)
    idle(qtbot, window)
    assert not window.test_errors
    assert config.id in window.sessions
    return window.sessions[config.id]


def schema_item(window, connection_id, schema):
    root = window.connection_item(connection_id)
    return next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == schema)


def expand(qtbot, window, item):
    item.setExpanded(True)
    qtbot.waitUntil(lambda: item.childCount() > 0 and item.child(0).data(0, Qt.ItemDataRole.UserRole) is not None,
                    timeout=20000)
    idle(qtbot, window)


def open_tree(qtbot, window, item):
    window.tree.scrollToItem(item)
    point = window.tree.visualItemRect(item).center()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    idle(qtbot, window)
    return window.tabs.currentWidget()


def run(qtbot, window, page):
    old = window.store.history()
    old_id = old[0]["id"] if old else None
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(window.store.history()) and window.store.history()[0]["id"] != old_id,
                    timeout=20000)
    idle(qtbot, window)
    assert window.store.history()[0]["status"] == "success", page.info.toPlainText()
    assert not page.running and not window.test_errors
    return page.current_result()


@pytest.fixture
def sql_owned():
    default = Path(__file__).resolve().parents[1] / "artifacts/sqlserver/endpoint.json"
    filename = Path(os.environ.get("OPENNAVICAT_TEST_SQLSERVER_ENDPOINT", str(default)))
    if not filename.is_file():
        pytest.skip("An owned SQL Server endpoint receipt is required for real MainWindow TDS tests")
    endpoint = json.loads(filename.read_text())
    ca = endpoint.get("ssl_ca") or endpoint.get("tls", {}).get("ca", "")
    config = ConnectionConfig(str(uuid.uuid4()), "Owned SQL Server window", driver="sqlserver",
        host="localhost" if ca else endpoint["host"], port=int(endpoint["port"]),
        database=endpoint["database"], username=endpoint["username"], password=endpoint["password"],
        ssl_mode="verify-full" if ca else endpoint.get("ssl_mode", "disable"), ssl_ca=ca, connect_timeout=6)
    admin = SqlServerSession(replace(config))
    admin.connect()
    schema = "onv_window_" + uuid.uuid4().hex[:12]
    admin.execute("CREATE SCHEMA " + admin.quote_identifier(schema))
    table = admin.qualified_name(schema, "records")
    admin.execute(f"CREATE TABLE {table}(id int IDENTITY PRIMARY KEY,label nvarchar(80) NOT NULL,payload varbinary(max),label_size AS LEN(label),revision rowversion)")
    admin.execute(f"WITH n AS (SELECT TOP (205) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) n FROM sys.all_objects) INSERT INTO {table}(label,payload) SELECT CONCAT('row-',RIGHT(CONCAT('000',n),3)),0x00FF FROM n")
    try:
        yield config, admin, schema, table
    finally:
        admin.execute("DROP TABLE " + table)
        admin.execute("DROP SCHEMA " + admin.quote_identifier(schema))
        admin.close()


@pytest.fixture
def redis_owned(qtbot, tmp_path):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("redis-server is required for the isolated real MainWindow Redis tests")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen([executable, "--bind", "127.0.0.1", "--port", str(port),
        "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def ready():
        assert process.poll() is None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return True
        except OSError:
            return False
    admin = None
    try:
        qtbot.waitUntil(ready, timeout=5000)
        config = ConnectionConfig(str(uuid.uuid4()), "Owned Redis window", driver="redis",
            host="127.0.0.1", port=port, database="0", ssl_mode="disable")
        admin = RedisSession(replace(config)); admin.connect()
        entries = [(b"binary\x00\xff", "string", b"before\r\n\x00\xff"),
                   (b"hash", "hash", [(b"field\xff", b"value\x00")]),
                   (b"list", "list", [b"item\xff"]), (b"set", "set", [b"member\x00"]),
                   (b"zset", "zset", [(b"member\xff", b"1.25")]),
                   (b"stream", "stream", [(b"field", b"value\xff")])]
        for key, kind, initial in entries:
            admin.create_key("0", key, kind, initial)
        yield config, admin, entries
    finally:
        if admin is not None:
            admin.close()
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)


def browser(qtbot, window, config):
    connect(qtbot, window, config)
    schema = schema_item(window, config.id, "0")
    expand(qtbot, window, schema)
    labels = [schema.child(i).text(0) for i in range(schema.childCount())]
    assert labels == ["数据", "查询", "备份"]
    data = schema.child(0)
    assert data.childCount() == 1 and data.child(0).text(0) == "(所有数据)"
    assert data.child(0).data(0, Qt.ItemDataRole.UserRole)["kind"] == "redis_data"
    data.setExpanded(True)
    page = open_tree(qtbot, window, data.child(0))
    assert isinstance(page, RedisBrowserPage)
    qtbot.waitUntil(lambda: not page.loading and len(page.entries) == 6, timeout=10000)
    idle(qtbot, window)
    return page


def key_editor(qtbot, window, page, key=b"binary\x00\xff"):
    row = next(i for i in range(page.keys.rowCount())
        if page.keys.item(i, 0).data(Qt.ItemDataRole.UserRole) == key)
    page.keys.scrollToItem(page.keys.item(row, 0))
    page.keys.selectRow(row)
    point = page.keys.visualItemRect(page.keys.item(row, 0)).center()
    qtbot.mouseClick(page.keys.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseDClick(page.keys.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qtbot.waitUntil(lambda: page.key_page is not None and page.key_page.value_page is not None
        and not page.key_page.loading, timeout=10000)
    idle(qtbot, window)
    return page.key_page


def test_six_driver_profiles_render_and_query_picker_never_connects_implicitly(qtbot, window_factory):
    configs = [ConnectionConfig(str(uuid.uuid4()), driver, driver=driver,
        database="0" if driver == "redis" else "master" if driver == "sqlserver" else ":memory:" if driver == "sqlite" else "owned",
        ssl_mode="disable") for driver in ("sqlite", "postgresql", "mysql", "mariadb", "sqlserver", "redis")]
    window = window_factory(*configs)
    assert window.tree.topLevelItemCount() == 6
    assert isinstance(DatabaseSession(configs[-2]), SqlServerSession)
    assert isinstance(DatabaseSession(configs[-1]), RedisSession)
    page = window.new_query(connection_id=configs[-2].id)
    assert page.schema == "dbo" and not page.supports_explain
    page.connection_picker.setCurrentIndex(page.connection_picker.findData(configs[-1].id))
    assert page.editor.language == "redis" and page.schema == "0"
    assert not window.sessions and not window.tasks
    assert all(window.connection_item(config.id).child(0).text(0) == "双击以打开连接" for config in configs)


def test_sqlserver_real_connect_schemas_tsql_results_and_default_query_schema(sql_owned, qtbot, window_factory):
    config, admin, schema, _ = sql_owned
    window = window_factory(config)
    session = connect(qtbot, window, config)
    assert isinstance(session, SqlServerSession)
    assert schema_item(window, config.id, schema) is not None
    page = window.new_query(connection_id=config.id,
        sql="DECLARE @n int=41; BEGIN SELECT @n+1 AS answer; SELECT SCHEMA_NAME() AS native_schema; END;")
    assert page.schema == session._selected_schema == "dbo" and page.schema != config.database
    result = run(qtbot, window, page)
    assert result.rows == [[42]]
    assert page.results.count() == 4
    page.results.setCurrentIndex(3)
    assert page.current_result().rows == [["dbo"]]
    assert not page.explain_button.isEnabled()
    window.transaction("BEGIN"); idle(qtbot, window)
    assert session.in_transaction
    window.transaction("ROLLBACK"); idle(qtbot, window)
    assert not session.in_transaction
    if config.ssl_ca:
        assert admin._query("SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id=@@SPID")[0][0] == "TRUE"
    assert window.disconnect(config.id) and config.id not in window.sessions


def test_sqlserver_table_metadata_pagination_and_gui_captured_crud(sql_owned, qtbot, window_factory, monkeypatch):
    config, admin, schema, table = sql_owned
    window = window_factory(config)
    connect(qtbot, window, config)
    owner = schema_item(window, config.id, schema); expand(qtbot, window, owner)
    tables = next(owner.child(i) for i in range(owner.childCount()) if owner.child(i).text(0) == "表")
    tables.setExpanded(True)
    record = next(tables.child(i) for i in range(tables.childCount()) if tables.child(i).text(0) == "records")
    page = open_tree(qtbot, window, record)
    assert isinstance(page, TablePage)
    qtbot.waitUntil(lambda: page.model is not None and not page.loading, timeout=10000)
    idle(qtbot, window)
    assert page.structure.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers and page.ddl.isReadOnly()
    assert page.structure.rowCount() == 5 and page.indexes.rowCount() >= 1
    columns = page.model.result.columns
    for name in ("id", "label_size", "revision"):
        assert not page.model.column_editable(columns.index(name))
    page.page_size.setCurrentText("100"); idle(qtbot, window)
    assert page.total == 205 and page.model.rowCount() == 100
    qtbot.mouseClick(page.next, Qt.MouseButton.LeftButton); idle(qtbot, window)
    assert page.offset == 100 and page.model.result.rows[0][0] == 101
    label = columns.index("label")
    assert page.model.setData(page.model.index(0, label), "changed-via-grid")
    qtbot.mouseClick(page.save_button, Qt.MouseButton.LeftButton); idle(qtbot, window)
    assert admin._query(f"SELECT label FROM {table} WHERE id=101") == [("changed-via-grid",)]
    assert not page.model.pending
    main_thread = threading.get_ident()
    real_values = RowDialog.values
    def gui_values(dialog):
        assert threading.get_ident() == main_thread, "RowDialog widgets were read on the DB worker"
        return real_values(dialog)
    monkeypatch.setattr(RowDialog, "values", gui_values)
    # Interact with the actual modal form when its nested event loop starts.
    from PySide6.QtCore import QTimer
    def fill_insert():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, RowDialog)
        for column, line, _null, default in dialog._fields:
            if column["name"] == "label":
                default.setChecked(False); line.setText("inserted-via-form")
        dialog.accept()
    QTimer.singleShot(0, fill_insert)
    qtbot.mouseClick(page.insert_button, Qt.MouseButton.LeftButton); idle(qtbot, window)
    assert admin._query(f"SELECT COUNT(*) FROM {table} WHERE label='inserted-via-form'") == [(1,)]
    page.filter.setText("inserted-via-form"); page.apply_filter(); idle(qtbot, window)
    assert page.total == 1
    page.grid.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(page.delete_button, Qt.MouseButton.LeftButton); idle(qtbot, window)
    assert admin._query(f"SELECT COUNT(*) FROM {table} WHERE label='inserted-via-form'") == [(0,)]


def test_redis_real_tree_single_browser_six_types_binary_key_and_save(redis_owned, qtbot, window_factory):
    config, admin, entries = redis_owned
    window = window_factory(config)
    page = browser(qtbot, window, config)
    assert {entry["type"] for entry in page.entries} == {entry[1] for entry in entries}
    assert {entry["key"] for entry in page.entries} == {entry[0] for entry in entries}
    assert window.open_redis_browser(config.id, "0") is page
    assert sum(isinstance(window.tabs.widget(i), RedisBrowserPage) for i in range(window.tabs.count())) == 1
    key = key_editor(qtbot, window, page)
    assert key.key == b"binary\x00\xff" and key.value_editor.mode.currentText() == "HEX"
    key.value_editor.editor.setPlainText("61 66 74 65 72 00 ff 0d 0a")
    assert page.dirty and key.save_button.isEnabled()
    qtbot.mouseClick(key.save_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not window.tasks and not key.loading and not key.dirty)
    idle(qtbot, window)
    assert admin.read_key("0", key.key).value == b"after\x00\xff\r\n"
    assert key.value_editor.bytes_value() == b"after\x00\xff\r\n"


@pytest.mark.parametrize("leave", ["close", "disconnect"])
def test_redis_dirty_cancel_then_save_retries_leave_without_deleted_qt_access(redis_owned, qtbot, window_factory, monkeypatch, leave):
    config, admin, _ = redis_owned
    window = window_factory(config)
    page = browser(qtbot, window, config)
    key = key_editor(qtbot, window, page)
    key.value_editor.editor.setPlainText("73 61 76 65 64 00 ff")
    assert page.dirty
    def attempt():
        if leave == "close":
            return window.close_tab(window.tabs.indexOf(page))
        return window.disconnect(config.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    attempt()
    assert window.tabs.indexOf(page) >= 0 and config.id in window.sessions and page.dirty
    assert key.value_editor.bytes_value() == b"saved\x00\xff"
    assert admin.read_key("0", key.key).value == b"before\r\n\x00\xff"
    # A save handoff must wait for worker completion, then retry the original leave.
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    attempt()
    assert config.id in window.sessions and window.tabs.indexOf(page) >= 0
    qtbot.waitUntil(lambda: window.tabs.indexOf(page) < 0 if isValid(page) else True, timeout=10000)
    idle(qtbot, window)
    qtbot.waitUntil(lambda: not isValid(page), timeout=10000)
    assert admin.read_key("0", b"binary\x00\xff").value == b"saved\x00\xff"
    assert (config.id in window.sessions) == (leave == "close")
    # Deliver queued refresh/retry callbacks after Qt has destroyed the old page.
    processed = []
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: processed.append(True))
    qtbot.waitUntil(lambda: bool(processed))
    assert not window.test_errors
