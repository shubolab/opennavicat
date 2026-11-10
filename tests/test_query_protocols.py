"""Qt protocol switching and native Redis parsing, with fixture-owned replies.

The wire double supplies binary/nested RESP values; the real RedisSession parser
and execution loop run in the host's serial worker. Live Redis service/TLS/CAS
acceptance remains in the adapter's separate integration tests.
"""
from __future__ import annotations

from types import SimpleNamespace
import shutil
import socket
import subprocess
import threading

import pytest
from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTabWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from opennavicat.database import ConnectionConfig, QueryResult
from opennavicat.redis_session import RedisSession
from opennavicat.tasks import Task
from opennavicat.ui.editor import SqlEditor
from opennavicat.ui.query import QueryPage


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.configs = {
            "sqlite": ConnectionConfig("sqlite", "Owned SQLite", database=":memory:"),
            "postgresql": ConnectionConfig("postgresql", "Owned PostgreSQL", driver="postgresql", database="owned"),
            "sqlserver": ConnectionConfig("sqlserver", "Owned SQL Server", driver="sqlserver", database="master", ssl_mode="verify-full"),
            "redis": ConnectionConfig("redis", "Owned Redis", driver="redis", database="0", ssl_mode="disable"),
        }
        self.sessions = {
            "sqlite": SimpleNamespace(_selected_schema="main"),
            "postgresql": SimpleNamespace(_selected_schema="tenant"),
            "sqlserver": SimpleNamespace(_selected_schema="accounting"),
            "redis": SimpleNamespace(_selected_schema="3"),
        }
        self.active_id, self.active_schema = "sqlite", "main"
        self.tabs = QTabWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.tasks, self.errors, self.requests, self.explains = set(), [], [], []
        self.main_thread = threading.get_ident()

    def connection_item(self, connection_id):
        root = QTreeWidgetItem([connection_id])
        for schema in {"sqlite": ["main"], "postgresql": ["public", "tenant"],
                       "sqlserver": ["dbo", "accounting"], "redis": ["0", "3", "5"]}[connection_id]:
            item = QTreeWidgetItem([schema])
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "schema", "schema": schema})
            root.addChild(item)
        return root

    def set_active(self, connection_id, schema):
        self.active_id, self.active_schema = connection_id, schema

    def save_sql(self, **kwargs):
        return True

    def export_rows(self):
        pass

    def explain_query(self, page):
        self.explains.append(page.connection_id)

    def refresh_actions(self):
        pass

    def focus_workspace(self, *_args):
        pass

    def stop(self):
        pass

    def run_query(self, page):
        # Capture every widget value here, before entering the serial worker.
        text, schema, limit = page.editor.selected_sql(), page.schema, page.limit.value()
        session = self.sessions[page.connection_id]
        self.requests.append((page.connection_id, text, schema))
        page.running = True
        def work():
            assert threading.get_ident() != self.main_thread
            session.use_schema(schema)
            return session.execute(text, limit)
        task = Task(work)
        self.tasks.add(task)
        task.signals.succeeded.connect(page.show_results)
        task.signals.failed.connect(lambda error: self.errors.append(error) or page.show_error(error))
        def finish():
            self.tasks.discard(task)
            page.running = False
        task.signals.finished.connect(finish)
        self.pool.start(task)


@pytest.fixture
def host(qtbot):
    window = _Host()
    qtbot.addWidget(window)
    window.resize(1100, 760)
    window.show()
    yield window
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount())


def add_page(host, connection_id, text=""):
    page = QueryPage(host, connection_id, text)
    host.tabs.addTab(page, "Owned query")
    host.tabs.setCurrentWidget(page)
    return page


def switch(qtbot, page, connection_id):
    page.connection_picker.setFocus()
    wanted = page.connection_picker.findData(connection_id)
    while page.connection_picker.currentIndex() != wanted:
        key = Qt.Key.Key_Down if page.connection_picker.currentIndex() < wanted else Qt.Key.Key_Up
        qtbot.keyClick(page.connection_picker, key)


def test_editor_language_switch_clears_sql_colors_and_preserves_text_cursor_undo_find(qtbot):
    editor = SqlEditor()
    qtbot.addWidget(editor)
    editor.show()
    editor.setPlainText("SELECT 7 FROM customers;")
    editor.highlighter.rehighlight()
    assert editor.document().firstBlock().layout().formats()
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.document().setModified(False)
    editor.set_language("redis")
    assert editor.toPlainText() == "SELECT 7 FROM customers;"
    assert editor.textCursor().selectedText() == "SELECT" and not editor.document().isModified()
    assert editor.document().firstBlock().layout().formats() == []
    words = editor._completion_model.stringList()
    assert "PING" in words and "HGETALL" in words and "FROM" not in words
    editor.set_completions(["FROM", "stale_sql_table"])
    assert "FROM" not in editor._completion_model.stringList()
    assert "stale_sql_table" not in editor._completion_model.stringList()
    editor.show_find()
    editor._find_input.setText("customers")
    editor._find()
    assert editor.textCursor().selectedText() == "customers"
    assert "Redis" in editor._find_dialog.windowTitle()
    editor._find_dialog.hide()
    editor.set_language("sql")
    assert "FROM" in editor._completion_model.stringList()
    assert "PING" not in editor._completion_model.stringList()
    assert editor.document().firstBlock().layout().formats()
    assert editor._find_dialog.windowTitle() == "Find in SQL"
    editor.moveCursor(QTextCursor.MoveOperation.End)
    prior = editor.toPlainText()
    qtbot.keyClicks(editor, " ")
    assert editor.document().isUndoAvailable()
    editor.set_language("redis")
    editor.undo()
    assert editor.toPlainText() == prior
    editor.redo()
    assert editor.toPlainText() == prior + " "


def test_real_connection_picker_switches_modes_clears_results_and_restores_sql_actions(qtbot, host):
    page = add_page(host, "sqlite", "select name from customers;")
    page.editor.set_completions(["old_table"])
    page.show_results([QueryResult(["value"], [[b"old\x00\xff"]])])
    assert page.current_result().rows == [[b"old\x00\xff"]]
    assert page.export_button.isEnabled()
    switch(qtbot, page, "redis")
    assert page.protocol_driver == "redis" and page.schema == "3"
    assert page.editor.language == "redis" and "CLI" in page.editor.placeholderText()
    assert not page.format_button.isEnabled() and not page.explain_button.isEnabled()
    assert not page.builder_button.isEnabled() and not page.supports_explain
    assert "old_table" not in page.editor._completion_model.stringList()
    assert page.results.count() == 0 and page.results.isHidden()
    assert page.current_result() is None and page._last_results == [] and not page.export_button.isEnabled()
    original = page.editor.toPlainText()
    assert page.format_sql() is False and page.editor.toPlainText() == original
    page._explain()
    page.running = True
    page.running = False
    assert not page.explain_button.isEnabled() and not host.explains
    switch(qtbot, page, "postgresql")
    assert page.schema == "tenant" and page.editor.language == "sql"
    assert page.format_button.isEnabled() and page.supports_explain
    assert "FROM" in page.editor._completion_model.stringList()
    assert page.editor.placeholderText() == ""
    assert page.format_sql() and "SELECT" in page.editor.toPlainText()
    page._explain()
    assert host.explains == ["postgresql"]


def test_sqlserver_uses_connected_default_schema_and_cannot_explain_even_after_running(qtbot, host):
    page = add_page(host, "sqlserver")
    assert page.schema == "accounting" and page.schema != host.configs["sqlserver"].database
    assert page.editor.language == "sql" and page.format_button.isEnabled()
    assert not page.supports_explain and "尚未实现" in page.explain_button.toolTip()
    page.running = True
    page.running = False
    page._explain()
    assert not page.explain_button.isEnabled() and not host.explains
    switch(qtbot, page, "redis")
    switch(qtbot, page, "sqlserver")
    assert page.schema == "accounting" and not page.explain_button.isEnabled()
    del host.sessions["sqlserver"]
    offline = add_page(host, "sqlserver")
    assert offline.schema == "dbo"


def redis_wire(host):
    session = RedisSession(host.configs["redis"])
    state = {"database": "0", "values": {}, "calls": []}
    session.connection = SimpleNamespace(db=0)
    session.connect = lambda: None
    session._check_command = lambda _arguments: None
    def raw(*arguments):
        assert threading.get_ident() != host.main_thread
        command = arguments[0].upper()
        state["calls"].append(arguments)
        if command == b"SELECT":
            state["database"] = arguments[1].decode() if isinstance(arguments[1], bytes) else str(arguments[1])
            return b"OK"
        if command == b"SET":
            state["values"][(state["database"], arguments[1])] = arguments[2]
            return b"OK"
        if command == b"GET":
            return state["values"].get((state["database"], arguments[1]))
        if command == b"ECHO":
            return arguments[1]
        if command == b"EVAL":
            return [b"\xff", [b"\x00", b"\r\n"], None]
        raise AssertionError("Unexpected fixture command")
    session._raw = raw
    host.sessions["redis"] = session
    return session, state


def type_lines(qtbot, editor, lines):
    editor.setFocus()
    for line in lines:
        qtbot.keyClicks(editor, line)
        qtbot.keyClick(editor, Qt.Key.Key_Return)


def wait_query(qtbot, host, page):
    qtbot.waitUntil(lambda: not page.running and not host.tasks and not host.pool.activeThreadCount())
    assert host.errors == []


def test_typed_redis_commands_execute_without_sqlparse_and_keep_bytes_and_nested_resp(qtbot, host, monkeypatch):
    session, wire = redis_wire(host)
    page = add_page(host, "redis")
    def no_sql(*_args, **_kwargs):
        pytest.fail("Redis command editor must not call SQL formatting or splitting")
    monkeypatch.setattr("sqlparse.split", no_sql)
    monkeypatch.setattr("sqlparse.format", no_sql)
    lines = ['SET "query;key" "a; b\\x00\\xff\\r\\n"', 'GET "query;key"']
    type_lines(qtbot, page.editor, lines)
    assert page.editor.toPlainText() == "\n".join(lines) + "\n"
    qtbot.keyClick(page.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    wait_query(qtbot, host, page)
    expected = b"a; b\x00\xff\r\n"
    assert wire["calls"] == [(b"SET", b"query;key", expected), (b"GET", b"query;key")]
    page.results.setCurrentIndex(3)
    result = page.current_result()
    assert result.rows == [[expected]]
    grid = page.results.currentWidget()
    assert grid.model().result.rows[0][0] == expected
    assert grid.model().data(grid.model().index(0, 0)) == "0x" + expected.hex()
    page.editor.setPlainText('EVAL "return {1, {2, 3}}" 0')
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    wait_query(qtbot, host, page)
    nested = page.current_result().rows
    assert nested == [[1, b"\xff"], [2, [b"\x00", b"\r\n"]], [3, None]]
    assert page.results.currentWidget().model().result.rows == nested
    assert page.editor.highlighter.enabled is False


def test_redis_select_updates_page_context_and_next_run_keeps_selected_database(qtbot, host):
    session, wire = redis_wire(host)
    page = add_page(host, "redis", 'SELECT 5\nSET "owned key" "db5"\nGET "owned key"')
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    wait_query(qtbot, host, page)
    assert session._selected_schema == page.schema == page.schema_picker.currentText() == "5"
    assert host.active_schema == "5"
    page.editor.setPlainText('GET "owned key"')
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    wait_query(qtbot, host, page)
    assert page.current_result().rows == [[b"db5"]]
    assert wire["database"] == "5"
    assert sum(arguments[0].upper() == b"SELECT" for arguments in wire["calls"]) == 1


@pytest.fixture
def native_redis(qtbot, tmp_path):
    """Own one local ephemeral server; never reuse a user's Redis service."""
    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("redis-server is unavailable for the isolated live Qt case")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen(
        [executable, "--bind", "127.0.0.1", "--port", str(port),
         "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    def ready():
        if process.poll() is not None:
            pytest.fail("The owned Redis fixture exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return True
        except OSError:
            return False
    try:
        qtbot.waitUntil(ready, timeout=5000)
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def test_live_redis_typed_commands_preserve_binary_nested_replies_and_selected_db(qtbot, host, native_redis, monkeypatch):
    config = host.configs["redis"]
    config.host, config.port = "127.0.0.1", native_redis
    session = RedisSession(config)
    host.sessions["redis"] = session
    page = add_page(host, "redis")
    def no_sql(*_args, **_kwargs):
        pytest.fail("Native Redis execution reached an SQL parser")
    monkeypatch.setattr("sqlparse.split", no_sql)
    monkeypatch.setattr("sqlparse.format", no_sql)
    lines = [
        'SELECT 5',
        'SET "query;key" "a; b\\x00\\xff\\r\\n"',
        'GET "query;key"',
        'EVAL "return {string.char(255), {string.char(0), string.char(13,10)}, false}" 0',
    ]
    try:
        type_lines(qtbot, page.editor, lines)
        qtbot.keyClick(page.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        wait_query(qtbot, host, page)
        page.results.setCurrentIndex(4)  # Message, summary, SELECT, SET, GET.
        assert page.current_result().rows == [[b"a; b\x00\xff\r\n"]]
        page.results.setCurrentIndex(5)
        assert page.current_result().rows == [[1, b"\xff"], [2, [b"\x00", b"\r\n"]], [3, None]]
        assert session._selected_schema == page.schema == host.active_schema == "5"
        page.editor.setPlainText('GET "query;key"')
        qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
        wait_query(qtbot, host, page)
        assert page.current_result().rows == [[b"a; b\x00\xff\r\n"]]
        assert page.editor.document().firstBlock().layout().formats() == []
    finally:
        # The socket belongs to this fixture. Do not close a user's connection.
        host.pool.waitForDone(5000)
        session.close()
