"""Keyboard activation reaches the real database and table workspace."""
import sqlite3
from contextlib import closing
import pytest

from PySide6.QtCore import Qt

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow, TablePage


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_opens_connection_then_real_table(qtbot, tmp_path, monkeypatch, key):
    database = tmp_path / "sample.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO products VALUES(1, 'Generated sample')")
        connection.commit()
    store = Store(tmp_path / "workspace")
    config = ConnectionConfig("keyboard-sample", "Generated keyboard sample", database=str(database))
    store.save_connection(config)
    window = MainWindow(store)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    window.show()
    def idle():
        return not window.tasks and not window.pool.activeThreadCount()
    try:
        root = window.connection_item(config.id)
        window.tree.setCurrentItem(root)
        window.tree.setFocus()
        qtbot.keyClick(window.tree, key)
        qtbot.waitUntil(lambda: config.id in window.sessions and idle(), timeout=10000)
        schema = window._schema_items(config.id, "main")[0]
        schema.setExpanded(True)
        qtbot.waitUntil(idle, timeout=10000)
        collection = next(schema.child(i) for i in range(schema.childCount())
            if schema.child(i).data(0, Qt.ItemDataRole.UserRole).get("kind") == "table")
        table = next(collection.child(i) for i in range(collection.childCount())
            if collection.child(i).data(0, Qt.ItemDataRole.UserRole).get("table") == "products")
        window.tree.setCurrentItem(table)
        qtbot.keyClick(window.tree, key)
        qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), TablePage)
            and window.tabs.currentWidget().model is not None and idle(), timeout=10000)
        page = window.tabs.currentWidget()
        assert page.model.rowCount() == 1
        assert page.model.data(page.model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "Generated sample"
        assert errors == []
    finally:
        qtbot.waitUntil(idle, timeout=10000)
        window.close()
