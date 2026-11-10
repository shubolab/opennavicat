"""Reconstructed component workflows, not a claim of complete 1:1 UI acceptance.

The data and actions below exercise the actual database-backed UI. Geometric
checks only protect functional density/navigation; visual parity still needs
the reference comparison documented in docs/parity-matrix.md.
"""
from __future__ import annotations

from contextlib import closing

import sqlite3
import threading

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QLabel, QToolButton

from opennavicat.ui.grid import DataGrid
from opennavicat.ui.window import QueryPage

# Reuse the native window fixture and real File-menu/query helpers, preserving
# their cleanup, background-idle and unexpected-error assertions.
from test_ui import idle, open_sample, run_query, window


def category(window, kind):
    root = window.connection_item(window.active_id)
    schema = next(root.child(i) for i in range(root.childCount())
                  if root.child(i).data(0, Qt.ItemDataRole.UserRole).get("schema") == "main")
    schema.setExpanded(True)
    return next(schema.child(i) for i in range(schema.childCount())
                if schema.child(i).data(0, Qt.ItemDataRole.UserRole).get("kind") == kind)


def select_category(qtbot, window, kind):
    item = category(window, kind)
    window.tree.scrollToItem(item)
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton,
                    pos=window.tree.visualItemRect(item).center())
    idle(qtbot, window)
    assert window.tabs.currentWidget() is window.object_page
    assert window.object_page.kind == kind


def select_object(qtbot, table, name):
    row = next(row for row in range(table.rowCount()) if table.item(row, 0).text() == name)
    table.scrollToItem(table.item(row, 0))
    qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton,
                    pos=table.visualItemRect(table.item(row, 0)).center())
    return row


def test_object_first_shell_and_sidebar_bottom_search_without_product_accounts(qtbot, window):
    assert window.tabs.count() == 1
    assert window.tabs.widget(0) is window.object_page
    assert window.tabs.tabText(0) == "对象"
    window.close_tab(0)
    assert window.tabs.count() == 1  # the object workspace is not a disposable SQL tab.
    assert window.tree_filter.isVisible()
    tree_bottom = window.tree.mapTo(window, window.tree.rect().bottomLeft()).y()
    search_top = window.tree_filter.mapTo(window, QPoint(0, 0)).y()
    assert search_top >= tree_bottom
    assert window.tree_filter.placeholderText() == "搜索"

    visible_text = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
    visible_text += [action.text() for action in window.main_toolbar.actions() if action.isVisible()]
    visible_text += [button.toolTip() for button in window.findChildren(QToolButton) if button.isVisible()]
    text = "\n".join(visible_text).casefold()
    # Database users/authentication remain in scope. Only product-account and
    # promotional controls are prohibited here, not a generic '用户' DB action.
    for excluded in ("open sample workspace", "local-first workspace", "welcome to",
                     "登录 navicat", "登录navicat", "navicat cloud", "订阅", "续费", "购买", "注册账号"):
        assert excluded not in text
    assert not any("avatar" in widget.objectName().casefold() for widget in window.findChildren(QToolButton))


def test_categories_and_object_list_use_real_rootpages_indexes_and_triggers(qtbot, window):
    page = open_sample(qtbot, window)
    path = window.configs[page.connection_id].database
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TRIGGER parity_products_updated AFTER UPDATE ON products BEGIN SELECT 1; END")
        rootpages = dict(db.execute("SELECT name,rootpage FROM sqlite_schema WHERE type='table'"))
        indexed = {name: bool(db.execute(f'PRAGMA index_list("{name}")').fetchall())
                   for name in ("customers", "orders", "products")}
    schema = window.connection_item(page.connection_id).child(0)
    assert [schema.child(i).text(0) for i in range(schema.childCount())] == ["表", "视图", "查询", "备份"]
    select_category(qtbot, window, "table")
    table = window.object_page.table
    assert [table.horizontalHeaderItem(i).text() for i in range(4)] == ["名", "根页面", "有索引", "有触发器"]
    assert table.rowCount() == 3
    for row in range(table.rowCount()):
        name = table.item(row, 0).text()
        assert int(table.item(row, 1).text()) == rootpages[name]
        assert table.item(row, 2).text() == ("是" if indexed[name] else "否")
        assert table.item(row, 3).text() == ("是" if name == "products" else "否")
    assert not window.object_page.open_action.isEnabled()
    row = select_object(qtbot, table, "products")
    assert {index.row() for index in table.selectionModel().selectedRows()} == {row}
    assert window.object_page.open_action.isEnabled()
    assert window.object_page.selected()["name"] == "products"

    select_category(qtbot, window, "view")
    assert table.rowCount() == 1 and table.item(0, 0).text() == "order_summary"
    assert not window.object_page.new_action.isEnabled()
    assert not window.object_page.import_action.isEnabled()
    select_category(qtbot, window, "query")
    assert table.rowCount() == 0
    assert not window.object_page.open_action.isEnabled()
    select_category(qtbot, window, "backup")
    assert table.rowCount() == 0
    assert not window.object_page.delete_action.isEnabled()


def test_information_pane_toggle_follows_selected_real_object(qtbot, window):
    open_sample(qtbot, window)
    select_category(qtbot, window, "table")
    assert not window.inspector.isVisible()
    window.info_action.trigger()
    assert window.inspector.isVisible()
    assert window.info_action.isChecked()
    select_object(qtbot, window.object_page.table, "customers")
    assert window.inspector.title.text() == "customers"
    assert window.inspector.kind.text() == "表"
    assert window.inspector.schema.text() == "main"
    assert "根页面" in window.inspector.details.text()
    assert window.inspector.connection.text() == window.configs[window.active_id].name
    window.info_action.trigger()
    assert not window.inspector.isVisible()
    assert window.object_page.selected()["name"] == "customers"


def test_query_information_summary_real_grids_and_failure_remove_stale_results(qtbot, window):
    page = open_sample(qtbot, window)
    assert isinstance(page, QueryPage)
    assert page.results.isHidden()
    page.editor.setPlainText("SELECT 17 AS previous_success;")
    assert run_query(qtbot, window, page).rows == [[17]]
    assert [page.results.tabText(i) for i in range(page.results.count())] == ["信息", "摘要", "结果 1"]
    assert page.summary.rowCount() == 1
    assert "previous_success" in page.info.toPlainText()
    assert page.export_button.isEnabled()

    before = window.store.history()[0]["id"]
    page.editor.setPlainText("SELECT no_such_parity_column;")
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.store.history()[0]["id"] != before, timeout=10000)
    idle(qtbot, window)
    assert window.store.history()[0]["status"] == "error"
    assert "失败" in page.message.text()
    assert "no_such_parity_column" in page.info.toPlainText()
    assert "[错误]" in page.info.toPlainText()
    assert page.results.currentIndex() == 0
    assert page.results.count() == 2
    assert not any(isinstance(page.results.widget(i), DataGrid) for i in range(page.results.count()))
    assert page.current_result() is None
    assert not page.export_button.isEnabled()
    assert not window.export_action.isEnabled()

    page.editor.setPlainText("SELECT 3 AS first_value; SELECT 4 AS second_value;")
    assert run_query(qtbot, window, page).rows == [[3]]
    assert page.summary.rowCount() == 2
    assert page.results.count() == 4
    page.results.setCurrentIndex(3)
    assert page.current_result().rows == [[4]]
    page.results.setCurrentIndex(0)
    assert not page.export_button.isEnabled()
    page.results.setCurrentIndex(2)
    assert page.export_button.isEnabled()


def test_query_busy_controls_keep_event_loop_responsive(qtbot, window):
    page = open_sample(qtbot, window)
    entered, release = threading.Event(), threading.Event()
    def controlled_query():
        entered.set()
        release.wait(5)
        return 42
    window.sessions[page.connection_id].connection.create_function("parity_controlled_query", 0, controlled_query)
    page.editor.setPlainText("SELECT parity_controlled_query() AS value;")
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert page.running
        assert not page.run_button.isEnabled()
        assert page.stop_button.isEnabled()
        assert not page.connection_picker.isEnabled()
        assert not page.schema_picker.isEnabled()
        assert window.stop_action.isEnabled()
        processed = []
        QTimer.singleShot(0, lambda: processed.append(True))
        qtbot.waitUntil(lambda: bool(processed), timeout=2000)
    finally:
        release.set()
    idle(qtbot, window)
    assert page.current_result().rows == [[42]]
    assert not page.running
    assert page.run_button.isEnabled() and not page.stop_button.isEnabled()
    assert page.connection_picker.isEnabled() and page.schema_picker.isEnabled()


def test_dense_result_viewport_selection_has_only_actual_rows(qtbot, window):
    page = open_sample(qtbot, window)
    assert len(run_query(qtbot, window, page).rows) == 6
    grid = page.results.currentWidget()
    assert isinstance(grid, DataGrid)
    assert grid.model().rowCount() == 6 and grid.model().columnCount() == 4
    assert grid.rowHeight(0) <= 24
    index = grid.model().index(1, 1)
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=grid.visualRect(index).center())
    assert grid.currentIndex() == index
    assert {(i.row(), i.column()) for i in grid.selectedIndexes()} == {(1, 1)}
    header = grid.verticalHeader()
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                    pos=QPoint(header.width() // 2, header.sectionViewportPosition(0) + header.sectionSize(0) // 2))
    assert grid.selectionModel().isRowSelected(0)
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton,
                    pos=QPoint(header.width() // 2, header.sectionViewportPosition(0) + header.sectionSize(0) // 2))
    assert not grid.selectionModel().isRowSelected(0)
    unused_y = grid.rowViewportPosition(5) + grid.rowHeight(5) + 30
    assert unused_y < grid.viewport().height()
    assert not grid.indexAt(QPoint(10, unused_y)).isValid()
    before = grid.model().rowCount()
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(10, unused_y))
    assert grid.model().rowCount() == before
    assert all(index.row() < 6 for index in grid.selectedIndexes())
