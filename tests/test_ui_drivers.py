"""Native Qt smoke through real disposable PostgreSQL and MySQL sessions.

Opt in with OPENNAVICAT_TEST_POSTGRES_PORT / OPENNAVICAT_TEST_MYSQL_PORT.
Servers must be disposable local fixtures with the test-only credentials below.
"""
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow, QueryPage, TablePage


@pytest.fixture(params=["postgresql", "mysql"])
def remote_window(request, tmp_path, monkeypatch, qtbot):
    driver = request.param
    name = "OPENNAVICAT_TEST_POSTGRES_PORT" if driver == "postgresql" else "OPENNAVICAT_TEST_MYSQL_PORT"
    port = os.environ.get(name)
    if not port:
        pytest.skip(f"Set {name} for isolated GUI driver integration")
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=lambda *a: None, get_password=lambda *a: None, delete_password=lambda *a: None))
    config = ConnectionConfig("ui-" + uuid.uuid4().hex, "Disposable " + driver, driver,
        "opennavicat_test", "127.0.0.1", int(port),
        "postgres" if driver == "postgresql" else "root", "opennavicat-test-only", "disable")
    schema = "onv_ui_" + uuid.uuid4().hex[:12]
    setup = DatabaseSession(config)
    setup.connect()
    setup.execute(f"CREATE SCHEMA {setup.quote_identifier(schema)}")
    setup.execute(f"CREATE TABLE {setup.qualified_name(schema, 'customers')} (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    setup.import_rows(schema, "customers", ["id", "name"], [[1, "before"], [2, "other"]])
    store = Store(tmp_path)
    store.save_connection(config)
    window = MainWindow(store)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    def unexpected_message(_parent, title, message, *_args, **_kwargs):
        errors.append(f"{title}: {message}")
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "warning", unexpected_message)
    monkeypatch.setattr(QMessageBox, "critical", unexpected_message)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Discard)
    window.show()
    window.connect_id(config.id)
    qtbot.waitUntil(lambda: config.id in window.sessions and not window.tasks, timeout=15000)
    assert not errors
    try:
        yield window, config, schema, setup, errors
    finally:
        qtbot.waitUntil(lambda: not window.tasks, timeout=15000)
        for i in range(window.tabs.count()):
            page = window.tabs.widget(i)
            if isinstance(page, TablePage) and page.model:
                page.model.discard()
        for session in window.sessions.values():
            if session.in_transaction:
                session.execute("ROLLBACK")
        window.close()
        setup.execute(f"DROP SCHEMA {setup.quote_identifier(schema)}" + (" CASCADE" if driver == "postgresql" else ""))
        setup.close()


def test_native_query_and_table_editor_on_real_drivers(remote_window, qtbot):
    window, config, schema, setup, errors = remote_window
    root = window.connection_item(config.id)
    schema_item = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == schema)
    schema_item.setExpanded(True)
    qtbot.waitUntil(lambda: not window.tasks and schema_item.childCount() == 4, timeout=10000)
    tables = next(schema_item.child(i) for i in range(schema_item.childCount())
                  if schema_item.child(i).text(0) == "表")
    assert tables.childCount() == 1 and tables.child(0).text(0) == "customers"
    window.set_active(config.id, schema)
    page = window.new_query(sql=f"SELECT * FROM {setup.qualified_name(schema, 'customers')} ORDER BY id", connection_id=config.id)
    assert isinstance(page, QueryPage)
    window.run_query(page)
    qtbot.waitUntil(lambda: not page.running and not window.tasks, timeout=10000)
    assert page.current_result().rows == [[1, "before"], [2, "other"]]
    assert window.store.history()[0]["status"] == "success"
    table = window.open_table({"id": config.id, "schema": schema, "table": "customers", "kind": "table"})
    assert isinstance(table, TablePage)
    qtbot.waitUntil(lambda: table.model is not None and not table.loading and not window.tasks, timeout=10000)
    assert table.model.result.rows == [[1, "before"], [2, "other"]]
    assert table.structure.rowCount() == 2
    assert "CREATE TABLE" in table.ddl.toPlainText()
    assert table.model.setData(table.model.index(0, 1), "after", Qt.ItemDataRole.EditRole)
    assert table.save_button.isEnabled()
    table.save_changes()
    qtbot.waitUntil(lambda: not table.loading and not window.tasks and not table.model.pending, timeout=10000)
    assert not errors
    assert setup.fetch_table(schema, "customers").rows == [[1, "after"], [2, "other"]]
    table.filter_action.setChecked(True)
    table.filter.setText("after")
    table.apply_filter()
    qtbot.waitUntil(lambda: not table.loading and not window.tasks, timeout=10000)
    assert table.model.result.rows == [[1, "after"]]
    assert table.total == 1


def test_native_query_transactions_partial_failure_and_cancel(remote_window, qtbot):
    window, config, schema, setup, errors = remote_window
    window.set_active(config.id, schema)
    page = window.new_query(sql="BEGIN; SELECT 42", connection_id=config.id)
    window.run_query(page)
    qtbot.waitUntil(lambda: not page.running and not window.tasks, timeout=10000)
    assert window.sessions[config.id].in_transaction
    page.results.setCurrentIndex(page.results.count() - 1)
    assert page.current_result().rows == [[42]]
    page.editor.setPlainText("ROLLBACK; SELECT no_such_column")
    window.run_query(page)
    qtbot.waitUntil(lambda: not page.running and not window.tasks, timeout=10000)
    assert "失败" in page.message.text()
    assert "no_such_column" in page.info.toPlainText()
    assert page.results.count() == 2  # Information + summary of completed ROLLBACK.
    assert page.summary.rowCount() == 1
    assert "ROLLBACK" in page.info.toPlainText()
    assert page.current_result() is None  # no stale SELECT42 grid after failure.
    assert not window.sessions[config.id].in_transaction
    page.editor.setPlainText("SELECT pg_sleep(15)" if config.driver == "postgresql" else "SELECT SLEEP(15)")
    window.run_query(page)
    qtbot.waitUntil(lambda: window.sessions[config.id]._active, timeout=10000)
    qtbot.wait(50)
    window.stop()
    qtbot.waitUntil(lambda: not page.running and not window.tasks, timeout=10000)
    assert "失败" in page.message.text()
    assert "[错误]" in page.info.toPlainText()
    assert not errors
