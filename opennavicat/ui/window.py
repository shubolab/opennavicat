from __future__ import annotations

import time
import uuid
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QStyle, QTabWidget, QTableWidget,
    QTableWidgetItem, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..database import ConnectionConfig, DatabaseSession, SUPPORTED_DRIVERS
from ..navicat_profiles import discover_navicat_profiles
from .. import __version__
from ..compare import compare_tables
from ..storage import Store, create_sample
from ..query_language import LEGACY, MONGO_JS, query_language as resolve_query_language
from ..tasks import Task
from ..transfer import export_result, import_data
from ..transport import SSHAuthenticationError, SSHHostKeyError, TLSKeyPasswordError
from .. import schema as schema_tools
from .. import schema_objects as object_schema_tools
from .connection_manager import ConnectionManagerDialog
from .dialogs import ConnectionDialog, RowDialog, TableDesignerDialog, confirm_ssh_host_key
from .editor import SqlEditor, pretty_sql
from .grid import DataGrid, DataModel
from .compare import CompareTargetDialog, ComparisonPage
from .diagram import SchemaDiagram
from .workspace import ObjectBrowser, Inspector, OBJECT_LABELS, object_symbol
from .icons import icon as workbench_icon, tool_icon
from .query import QueryPage
from .mongo_native_query import NativeMongoQueryPage
from .table_designer import TableDesignerPage
from .schema_object_designer import IndexDesignerPage, TriggerDesignerPage
from .redis_browser import RedisBrowserPage, RedisKeyPage
from .mongo_browser import MongoCollectionPage
from .mongo_gridfs import MongoGridFSPage

DESIGNER_PAGES = (TableDesignerPage, IndexDesignerPage, TriggerDesignerPage)
MONGO_PAGES = (MongoCollectionPage, MongoGridFSPage)

SAMPLE_QUERY = """-- Explore your sample workspace. Select SQL to run only that selection.
SELECT
    c.name AS customer,
    c.city,
    COUNT(o.id) AS orders,
    ROUND(SUM(p.price * o.quantity), 2) AS revenue
FROM customers c
JOIN orders o ON o.customer_id = c.id
JOIN products p ON p.id = o.product_id
GROUP BY c.id, c.name, c.city
ORDER BY revenue DESC;"""


class ConnectionTree(QTreeWidget):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() in (
                Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
            item = self.currentItem()
            if item is not None:
                self.itemActivated.emit(item, self.currentColumn())
            event.accept()
            return
        super().keyPressEvent(event)


class TablePage(QWidget):
    def __init__(self, window, connection_id, schema, table, kind="table"):
        super().__init__()
        self.window, self.connection_id, self.schema, self.table, self.kind = window, connection_id, schema, table, kind
        self.offset = 0
        self.total = 0
        self.metadata = []
        self.order_by = None
        self.descending = False
        self.loading = False
        self.filter_value = ""
        self.model = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setFixedHeight(36)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setStyleSheet("QToolButton {min-width:26px; max-width:30px; padding:1px;}")
        def tool(title, symbol, callback=None, enabled=True):
            action = QAction(tool_icon(symbol, 18), title, self)
            action.setEnabled(enabled)
            if callback:
                action.triggered.connect(callback)
            self.toolbar.addAction(action)
            return action
        tool("开始事务", "transaction_begin", lambda: window.transaction("BEGIN"))
        tool("提交", "commit", lambda: window.transaction("COMMIT"))
        tool("回滚", "rollback", lambda: window.transaction("ROLLBACK"))
        self.toolbar.addSeparator()
        tool("编辑器", "design", self.edit_record)
        self.filter_action = tool("筛选", "filter")
        self.filter_action.setCheckable(True)
        tool("排序", "sort", self.open_sort)
        tool("列", "table", self.open_columns)
        self.toolbar.addSeparator()
        self.toolbar.addAction(window.import_action)
        self.toolbar.addAction(window.export_action)
        self.toolbar.addSeparator()
        tool("数据生成（尚未实现）", "database", enabled=False)
        tool("创建图表（尚未实现）", "chart", enabled=False)
        layout.addWidget(self.toolbar)
        self.filter_bar = QWidget()
        filter_layout = QHBoxLayout(self.filter_bar)
        filter_layout.setContentsMargins(8, 3, 8, 3)
        filter_layout.addWidget(QLabel("筛选"))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("输入记录值")
        self.filter.setClearButtonEnabled(True)
        self.filter.returnPressed.connect(self.apply_filter)
        filter_layout.addWidget(self.filter, 1)
        find = QPushButton("应用")
        find.clicked.connect(self.apply_filter)
        filter_layout.addWidget(find)
        close_filter = QToolButton()
        close_filter.setText("×")
        close_filter.clicked.connect(lambda: self.filter_action.setChecked(False))
        filter_layout.addWidget(close_filter)
        self.filter_bar.hide()
        self.filter_action.toggled.connect(self.filter_bar.setVisible)
        layout.addWidget(self.filter_bar)
        self.label = QLabel(table, self)
        self.label.hide()
        self.subtabs = QTabWidget()
        self.subtabs.setObjectName("structureTabs")
        self.subtabs.setDocumentMode(True)
        self.grid = DataGrid()
        self.grid.horizontalHeader().sectionClicked.connect(self.sort)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.context_menu)
        self.subtabs.addTab(self.grid, "数据")
        self.structure = QTableWidget(0, 5)
        self.structure.setHorizontalHeaderLabels(["名", "类型", "不是 null", "默认值", "键"])
        self.structure.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.structure.horizontalHeader().setStretchLastSection(False)
        self.structure.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.structure.verticalHeader().hide()
        self.structure.verticalHeader().setDefaultSectionSize(23)
        self.subtabs.addTab(self.structure, "字段")
        self.indexes = QTableWidget(0, 4)
        self.indexes.setHorizontalHeaderLabels(["名", "字段 / 表达式", "唯一", "主键"])
        self.indexes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.indexes.horizontalHeader().setStretchLastSection(True)
        self.indexes.verticalHeader().hide()
        self.subtabs.addTab(self.indexes, "索引")
        self.foreign_keys = QTableWidget(0, 6)
        self.foreign_keys.setHorizontalHeaderLabels(["名", "字段", "参考表", "参考字段", "更新时", "删除时"])
        self.foreign_keys.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.foreign_keys.horizontalHeader().setStretchLastSection(True)
        self.foreign_keys.verticalHeader().hide()
        self.subtabs.addTab(self.foreign_keys, "外键")
        self.ddl = SqlEditor()
        self.ddl.setReadOnly(True)
        self.subtabs.addTab(self.ddl, "SQL 预览")
        self.subtabs.tabBar().hide()
        layout.addWidget(self.subtabs, 1)
        self.footer = QWidget()
        self.footer.setObjectName("tableFooter")
        self.footer.setFixedHeight(28)
        bar = QHBoxLayout(self.footer)
        bar.setContentsMargins(6, 1, 6, 1)
        bar.setSpacing(3)
        def button(title, symbol, callback):
            result = QToolButton()
            result.setToolTip(title)
            result.setIcon(tool_icon(symbol, 16))
            result.setIconSize(QSize(16, 16))
            result.setAutoRaise(True)
            result.setFixedSize(24, 24)
            result.clicked.connect(callback)
            bar.addWidget(result)
            return result
        self.insert_button = button("添加记录", "record_add", self.insert_row)
        self.delete_button = button("删除记录", "record_delete", self.delete_row)
        self.save_button = button("应用修改", "record_apply", self.save_changes)
        self.save_button.setEnabled(False)
        self.discard_button = button("取消修改", "record_cancel", lambda: self.model and self.model.discard())
        self.discard_button.setEnabled(False)
        button("刷新", "refresh", self.refresh)
        bar.addSpacing(12)
        self.sql_label = QLabel()
        self.sql_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sql_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.sql_label, 1)
        self.count = QLabel(self)
        self.count.hide()
        first = button("第一页", "first", lambda: self.go_page(0))
        self.previous = button("上一页", "previous", lambda: self.change_page(-1))
        self.page_number = QSpinBox()
        self.page_number.setRange(1, 1)
        self.page_number.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_number.setFixedWidth(42)
        self.page_number.editingFinished.connect(lambda: self.go_page(self.page_number.value() - 1))
        bar.addWidget(self.page_number)
        self.next = button("下一页", "next", lambda: self.change_page(1))
        button("最后一页", "last", lambda: self.go_page(max(0, (self.total - 1) // int(self.page_size.currentText()))))
        self.page_size = QComboBox(self)
        self.page_size.addItems(["100", "200", "500", "1000"])
        self.page_size.setCurrentText("1000")
        self.page_size.currentTextChanged.connect(self.apply_filter)
        self.page_size.hide()
        settings = button("设置", "other", self.page_settings)
        bar.addSpacing(10)
        button("网格视图", "table", lambda: self.subtabs.setCurrentIndex(0))
        button("表单视图", "list", self.edit_record)
        layout.addWidget(self.footer)
        self.note = QLabel(self)
        self.note.hide()
        QTimer.singleShot(0, self.refresh)

    def go_page(self, page):
        if self.loading or not self.can_discard():
            return
        self.offset = max(0, page) * int(self.page_size.currentText())
        self.refresh(check=False)

    def page_settings(self):
        value, ok = QInputDialog.getItem(self, "记录设置", "每页记录数", ["100", "200", "500", "1000"], ["100", "200", "500", "1000"].index(self.page_size.currentText()), False)
        if ok:
            self.page_size.setCurrentText(value)

    def open_sort(self):
        if not self.model:
            return
        menu = QMenu(self)
        for column, name in enumerate(self.model.result.columns):
            action = menu.addAction(name + (" ↓" if name == self.order_by and self.descending else " ↑" if name == self.order_by else ""))
            action.triggered.connect(lambda checked=False, c=column: self.sort(c))
        menu.exec(self.toolbar.mapToGlobal(self.toolbar.rect().bottomLeft()))

    def open_columns(self):
        if not self.model:
            return
        menu = QMenu(self)
        for column, name in enumerate(self.model.result.columns):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.grid.isColumnHidden(column))
            action.toggled.connect(lambda checked, c=column: self.grid.setColumnHidden(c, not checked))
        menu.exec(self.toolbar.mapToGlobal(self.toolbar.rect().bottomLeft()))

    def edit_record(self):
        if not self.model or not self.model.editable or not self.model.result.rows:
            return
        row = self.grid.currentIndex().row()
        row = max(0, row)
        values = dict(zip(self.model.result.columns, self.model.result.rows[row]))
        for (r, c), value in self.model.pending.items():
            if r == row:
                values[self.model.result.columns[c]] = value
        dialog = RowDialog(self.metadata, values, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for name, value in dialog.values().items():
                column = self.model.result.columns.index(name)
                self.model.setData(self.model.index(row, column), value)

    def can_discard(self):
        if self.model and self.model.pending:
            return QMessageBox.question(self, "未保存的修改", "放弃尚未应用的记录修改？", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard
        return True

    def apply_filter(self, *_):
        if self.loading or not self.can_discard():
            return
        self.filter_value = self.filter.text()
        self.offset = 0
        self.refresh(check=False)

    def sort(self, column):
        if self.loading or not self.model or not self.can_discard():
            return
        name = self.model.result.columns[column]
        self.descending = not self.descending if self.order_by == name else False
        self.order_by = name
        self.offset = 0
        self.grid.horizontalHeader().setSortIndicator(column, Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder)
        self.grid.horizontalHeader().setSortIndicatorShown(True)
        self.refresh(check=False)

    def change_page(self, delta):
        if self.loading or not self.can_discard():
            return
        self.offset = max(0, self.offset + delta * int(self.page_size.currentText()))
        self.refresh(check=False)

    def refresh(self, checked=False, check=True):
        if self.loading or (check and not self.can_discard()):
            return
        session = self.window.sessions.get(self.connection_id)
        if session is None:
            return
        self.loading = True
        self.setEnabled(False)
        size = int(self.page_size.currentText())
        def work():
            metadata = session.columns(self.schema, self.table)
            ddl = session.table_ddl(self.schema, self.table)
            indexes = session.indexes(self.schema, self.table)
            foreign_keys = session.foreign_keys(self.schema, self.table)
            result = session.fetch_table(self.schema, self.table, self.offset, size, self.order_by, self.descending, self.filter_value)
            total = session.count_rows(self.schema, self.table, self.filter_value)
            return metadata, ddl, result, total, indexes, foreign_keys
        def done(data):
            self.metadata, ddl, result, self.total, indexes, foreign_keys = data
            writable = self.kind == "table" and not session.config.read_only
            editable = writable and any(c["pk"] for c in self.metadata)
            self.model = DataModel(result, editable, self.grid, self.metadata)
            self.model.dirty_changed.connect(self.update_dirty)
            self.grid.setModel(self.model)
            self.structure.setRowCount(len(self.metadata))
            for i, col in enumerate(self.metadata):
                for j, value in enumerate([col["name"], col["type"], "否" if col["nullable"] else "是", col["default"] if col["default"] is not None else "", "主键" if col["pk"] else ""]):
                    self.structure.setItem(i, j, QTableWidgetItem(str(value)))
            self.structure.resizeColumnsToContents()
            self.indexes.setRowCount(len(indexes))
            for i, entry in enumerate(indexes):
                for j, value in enumerate([entry["name"], ", ".join(entry["columns"]) or entry.get("definition", ""), "是" if entry["unique"] else "否", "是" if entry["primary"] else "否"]):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(entry.get("definition", ""))
                    self.indexes.setItem(i, j, item)
            self.indexes.resizeColumnsToContents()
            self.foreign_keys.setRowCount(len(foreign_keys))
            for i, entry in enumerate(foreign_keys):
                for j, value in enumerate([entry["name"], ", ".join(entry["columns"]), entry["referenced_table"], ", ".join(entry["referenced_columns"]), entry["on_update"], entry["on_delete"]]):
                    self.foreign_keys.setItem(i, j, QTableWidgetItem(str(value)))
            self.foreign_keys.resizeColumnsToContents()
            self.ddl.setPlainText(ddl)
            self.sql_label.setText(result.sql)
            self.sql_label.setToolTip(result.sql)
            self.page_number.blockSignals(True)
            self.page_number.setRange(1, max(1, (self.total + size - 1) // size))
            self.page_number.setValue(self.offset // size + 1)
            self.page_number.blockSignals(False)
            self.window.status.setText(f"{self.total} 条记录在第 {self.offset // size + 1} 页")
            if self.window.tabs.currentWidget() is self:
                self.window.inspector.show_object({"name": self.table, "schema": self.schema, "kind": self.kind}, session.config, ddl)
            self.insert_button.setEnabled(writable)
            self.delete_button.setEnabled(editable)
            self.update_dirty()
            self.count.setText(f"{self.offset + 1 if self.total else 0}–{self.offset + len(result.rows)} of {self.total:,}")
            self.previous.setEnabled(self.offset > 0)
            self.next.setEnabled(self.offset + len(result.rows) < self.total)
            self.note.setText("Double-click a cell to edit · Changes are saved only when applied" if editable else "Read-only grid · Editing requires a writable table with a primary key")
        def complete():
            self.loading = False
            self.setEnabled(True)
        self.window.submit(work, done, label=f"Reading {self.table}", finished=complete)

    def update_dirty(self):
        dirty = bool(self.model and self.model.pending)
        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)

    def save_changes(self):
        if not self.model or not self.model.pending or self.loading:
            return
        session = self.window.sessions[self.connection_id]
        changes = self.model.changes()
        self.loading = True
        self.setEnabled(False)
        def work():
            if session.in_transaction:
                raise ValueError("Commit or roll back the SQL transaction before applying grid changes.")
            session.execute("BEGIN TRANSACTION" if session.config.driver == "sqlserver" else "BEGIN")
            try:
                for original, changed in changes:
                    session.update_row(self.schema, self.table, original, changed)
                session.execute("COMMIT")
            except Exception:
                session.execute("ROLLBACK")
                raise
        def done(_):
            self.model.discard()
            self.loading = False
            self.setEnabled(True)
            self.refresh(check=False)
        def failed(exc):
            self.loading = False
            self.setEnabled(True)
            self.window.error(exc)
        self.window.submit(work, done, failed, f"Saving {len(changes)} row(s)")

    def insert_row(self):
        if not self.can_discard():
            return
        dialog = RowDialog(self.metadata, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        session = self.window.sessions[self.connection_id]
        values, schema, table = dialog.values(), self.schema, self.table
        self.window.submit(lambda: session.insert_row(schema, table, values), lambda _: self.refresh(check=False), label="Inserting row")

    def delete_row(self):
        if not self.model or not self.can_discard():
            return
        rows = sorted({i.row() for i in self.grid.selectedIndexes()})
        if not rows:
            self.window.statusBar().showMessage("请选择要删除的记录", 4000)
            return
        if QMessageBox.question(self, "删除记录", f"Permanently delete {len(rows)} selected row(s) from {self.table}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        session = self.window.sessions[self.connection_id]
        originals = [dict(zip(self.model.result.columns, self.model.result.rows[row])) for row in rows]
        def work():
            if session.in_transaction:
                raise ValueError("Commit or roll back the SQL transaction before deleting rows.")
            session.execute("BEGIN TRANSACTION" if session.config.driver == "sqlserver" else "BEGIN")
            try:
                for original in originals:
                    session.delete_row(self.schema, self.table, original)
                session.execute("COMMIT")
            except Exception:
                session.execute("ROLLBACK")
                raise
        self.window.submit(work, lambda _: self.refresh(check=False), label="Deleting rows")

    def context_menu(self, pos):
        menu = QMenu(self)
        action = menu.addAction("设为 NULL")
        index = self.grid.indexAt(pos)
        action.setEnabled(bool(self.model and index.isValid() and self.model.column_editable(index.column())))
        action.triggered.connect(lambda: self.model.setData(index, None))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def current_result(self):
        return self.model.result if self.model else None


class MainWindow(QMainWindow):
    navicat_discovery_finished = Signal(dict)

    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.configs = {c.id: c for c in self.store.connections()}
        self.sessions = {}
        self.tasks = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.cancel_pool = QThreadPool(self)
        self.cancel_pool.setMaxThreadCount(1)
        self._cancelling = False
        self.active_id = None
        self.active_schema = "main"
        self.query_number = 0
        self._connecting = set()
        self._closing_mongo = False
        self._closing_connections = set()
        self._closed = False
        self._navicat_discovering = False
        self._navicat_pending_configs = set()
        self.last_navicat_counts = None
        self._tree_generation = 0
        self._loading_object_nodes = set()
        self._focus_page = None
        self.setWindowTitle("OpenNavicat")
        self.resize(1512, 920)
        self.setMinimumSize(950, 620)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
        self.build_ui()
        self.build_menu()
        self.populate_connections()
        self.restore_drafts()
        self.info_action.setChecked(bool(self.store.state.get("info_visible", False)))
        self.inspector.setVisible(self.info_action.isChecked())
        self.navigation_action.setChecked(bool(self.store.state.get("navigation_visible", True)))
        self.sidebar.setVisible(self.navigation_action.isChecked())
        if self.store.state.get("ui_revision") == 2 and self.store.state.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(self.store.state["geometry"].encode("ascii")))
        sizes = self.store.state.get("splitter_sizes")
        if self.store.state.get("ui_revision") == 2 and isinstance(sizes, list) and len(sizes) == 3:
            self.splitter.setSizes(sizes)
        self.refresh_actions()

    def action(self, text, slot, shortcut=None, icon=None):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        if icon:
            action.setIcon(workbench_icon(icon) if isinstance(icon, str) else self.style().standardIcon(icon))
        return action

    def build_ui(self):
        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(28, 28))
        toolbar.setFixedHeight(56)
        self.main_toolbar = toolbar
        self.addToolBar(toolbar)
        self.new_connection_action = self.action("连接", self.new_connection, "Ctrl+Shift+N", "connection")
        self.query_action = self.action("新建查询", self.new_query, "Ctrl+N", "new_query")
        self.refresh_action = self.action("刷新", self.refresh_tree, "F5", "refresh")
        self.table_action = self.action("新建表", self.new_table, icon="add")
        self.diagram_action = self.action("ER 图表", self.open_diagram, icon="er")
        self.run_action = self.action("运行", self.run_current, "Ctrl+Return", "run")
        self.stop_action = self.action("停止", self.stop, icon="stop")
        self.import_action = self.action("导入", self.import_rows, icon="import")
        self.export_action = self.action("导出", self.export_rows, icon="export")
        self.import_action.setIcon(tool_icon("import", 18))
        self.export_action.setIcon(tool_icon("export", 18))
        toolbar.addAction(self.new_connection_action)
        toolbar.addAction(self.query_action)
        toolbar.addSeparator()
        self.category_actions = {}
        for label, kind, symbol, supported in [("表", "table", "table", True), ("视图", "view", "view", True), ("函数", "function", "function", False), ("索引", "index", "index", True), ("触发器", "trigger", "trigger", True), ("其它", "other", "other", True), ("用户", "user", "user", False), ("查询", "query", "query", True), ("备份", "backup", "backup", True), ("自动运行", "automation", "automation", False), ("模型", "model", "model", True), ("BI", "chart", "bi", False)]:
            if kind == "other":
                action = self.action(label, self.show_other_tools, icon=symbol)
            elif kind == "model":
                action = self.action(label, self.open_diagram, icon=symbol)
                action.setToolTip("当前可查看真实结构关系图；完整模型编辑仍待实现")
            else:
                action = self.action(label, lambda checked=False, k=kind: self.show_objects(k), icon=symbol)
            action.setCheckable(kind in {"table", "view", "index", "trigger", "query", "backup"})
            if not supported:
                action.setEnabled(False)
                action.setToolTip("此项仍待完整实现")
            toolbar.addAction(action)
            self.category_actions[kind] = action
        self.category_actions["table"].setChecked(True)
        self.mongo_pipeline_action = self.action("管道", lambda: self.show_objects("mongo_pipeline"), icon="query")
        self.mongo_pipeline_action.setEnabled(False)
        self.mongo_pipeline_action.setToolTip("MongoDB 可视化聚合管道设计器尚未实现")
        self.mongo_pipeline_action.setVisible(False)
        toolbar.insertAction(self.category_actions["query"], self.mongo_pipeline_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.navigation_action = self.action("导航", self.toggle_navigation, icon="navigation")
        self.navigation_action.setCheckable(True)
        self.navigation_action.setChecked(True)
        self.navigation_action.setToolTip("隐藏或显示导航窗格")
        self.info_action = self.action("信息", self.toggle_inspector, icon="info")
        self.info_action.setCheckable(True)
        self.info_action.setToolTip("隐藏或显示信息窗格")
        self.ai_panel_action = self.action("AI 会话", lambda: None, icon="ai")
        self.ai_panel_action.setEnabled(False)
        self.ai_panel_action.setToolTip("AI 会话面板尚未实现")
        self.navigation_action.setIcon(tool_icon("navigation", 18))
        self.info_action.setIcon(tool_icon("info", 18))
        self.view_controls = QWidget()
        view_layout = QVBoxLayout(self.view_controls)
        view_layout.setContentsMargins(4, 0, 4, 0)
        view_layout.setSpacing(0)
        view_buttons = QHBoxLayout()
        view_buttons.setContentsMargins(0, 0, 0, 0)
        view_buttons.setSpacing(9)
        for action in (self.info_action, self.ai_panel_action):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(28, 30)
            button.setAutoRaise(True)
            view_buttons.addWidget(button)
        view_layout.addLayout(view_buttons)
        view_label = QLabel("查看")
        view_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view_label.setStyleSheet("font-size:11px;")
        view_layout.addWidget(view_label)
        toolbar.addWidget(self.view_controls)
        # The active context belongs to the navigation tree or each SQL page.
        self.connection_picker = QComboBox(self)
        self.connection_picker.hide()
        self.connection_picker.currentIndexChanged.connect(self.select_connection)
        self.schema_picker = QComboBox(self)
        self.schema_picker.hide()
        self.schema_picker.currentTextChanged.connect(self.select_schema)
        self.splitter = QSplitter()
        self.setCentralWidget(self.splitter)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMinimumWidth(180)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(8, 0, 8, 6)
        side.setSpacing(0)
        self.tree = ConnectionTree()
        self.tree.setObjectName("connectionTree")
        self.tree.setAccessibleName("数据库连接和对象")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setAnimated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.itemActivated.connect(self.tree_open)
        self.tree.itemExpanded.connect(self.tree_expand)
        self.tree.itemClicked.connect(self.tree_selected)
        side.addWidget(self.tree, 1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 3, 0, 0)
        bottom.setSpacing(4)
        self.active_only = QToolButton()
        self.active_only.setIcon(workbench_icon("connection", 16))
        self.active_only.setFixedSize(20, 24)
        self.active_only.setCheckable(True)
        self.active_only.setToolTip("仅显示活跃对象")
        self.active_only.toggled.connect(lambda _: self.filter_tree(self.tree_filter.text()))
        bottom.addWidget(self.active_only)
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("搜索")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self.filter_tree)
        bottom.addWidget(self.tree_filter, 1)
        side.addLayout(bottom)
        self.splitter.addWidget(self.sidebar)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("workspaceTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(20, 20))
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(False)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.splitter.addWidget(self.tabs)
        self.inspector = Inspector()
        self.splitter.addWidget(self.inspector)
        self.inspector.hide()
        self.splitter.setSizes([240, 1072, 200])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.object_page = ObjectBrowser(self)
        self.tabs.addTab(self.object_page, "对象")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.LeftSide, None)
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setFixedHeight(22)
        self.statusBar().addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedSize(80, 6)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.transaction_status = QLabel("")
        self.statusBar().addPermanentWidget(self.transaction_status)

    def build_menu(self):
        file = self.menuBar().addMenu("文件")
        file.addAction(self.new_connection_action)
        file.addAction(self.query_action)
        file.addAction(self.action("打开查询…", self.open_sql, QKeySequence.StandardKey.Open))
        file.addAction(self.action("保存查询…", self.save_sql, QKeySequence.StandardKey.Save))
        file.addSeparator()
        file.addAction(self.action("打开示例数据库", self.sample_workspace))
        file.addSeparator()
        file.addAction(self.action("退出 OpenNavicat", self.close, QKeySequence.StandardKey.Quit))
        edit = self.menuBar().addMenu("编辑")
        edit.addAction(self.action("查找…", self.find_current, QKeySequence.StandardKey.Find))
        view = self.menuBar().addMenu("查看")
        view.addAction(self.navigation_action)
        view.addAction(self.info_action)
        view.addAction(self.refresh_action)
        connection = self.menuBar().addMenu("连接")
        connection.addAction(self.action("打开连接", self.connect_active))
        connection.addAction(self.action("关闭连接", self.disconnect_active))
        connection.addAction(self.action("编辑连接…", self.edit_connection))
        query = self.menuBar().addMenu("查询")
        query.addAction(self.run_action)
        query.addAction(self.stop_action)
        query.addAction(self.action("解释", self.explain_current))
        query.addSeparator()
        query.addAction(self.action("开始事务", lambda: self.transaction("BEGIN")))
        query.addAction(self.action("提交", lambda: self.transaction("COMMIT")))
        query.addAction(self.action("回滚", lambda: self.transaction("ROLLBACK")))
        query.addSeparator()
        query.addAction(self.action("美化 SQL", self.format_sql, "Ctrl+Shift+F"))
        favorites = self.menuBar().addMenu("收藏夹")
        favorites.addAction(self.action("添加到收藏夹…", self.add_favorite, "Ctrl+Shift+S"))
        favorites.addAction(self.action("查询历史…", self.show_history, "Ctrl+H"))
        favorites.addAction(self.action("已保存的查询…", self.show_favorites))
        tools = self.menuBar().addMenu("工具")
        tools.addAction(self.table_action)
        tools.addAction(self.diagram_action)
        tools.addAction(self.import_action)
        tools.addAction(self.export_action)
        tools.addAction(self.action("数据同步…", self.compare_data))
        tools.addSeparator()
        tools.addAction(self.action("备份数据库…", self.backup_sqlite))
        tools.addSeparator()
        self.navicat_reload_action = self.action("从 Navicat 重新加载连接", self.start_navicat_discovery)
        tools.addAction(self.navicat_reload_action)
        self.navicat_auto_action = QAction("启动时自动加载 Navicat 连接", self)
        self.navicat_auto_action.setCheckable(True)
        self.navicat_auto_action.setChecked(bool(self.store.state.get("navicat_auto_discover", True)))
        self.navicat_auto_action.toggled.connect(self._set_navicat_auto_discover)
        tools.addAction(self.navicat_auto_action)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self.action("键盘快捷键", self.shortcuts))
        help_menu.addAction(self.action("关于 OpenNavicat", self.about))

    def toggle_navigation(self, checked):
        self.sidebar.setVisible(checked)

    def toggle_inspector(self, checked):
        self.inspector.setVisible(checked)
        if checked:
            width = self.width()
            self.splitter.setSizes([240 if self.sidebar.isVisible() else 0, max(400, width - 440), 200])
            if self.tabs.currentWidget() is self.object_page:
                self.object_page.selection_changed()

    def show_objects(self, kind="table"):
        if self.active_id not in self.sessions or self.active_id in self._closing_connections or self._closing_mongo:
            return
        if self.configs[self.active_id].driver == "redis" and kind == "table":
            kind = "redis_data"
        if self.configs[self.active_id].driver == "mongodb":
            kind = {"table": "mongo_collection", "view": "mongo_view", "index": "mongo_index",
                    "function": "mongo_function"}.get(kind, kind)
            other = self.category_actions["other"]
            selected = kind in {"mongo_index", "mongo_mapreduce", "mongo_bucket"}
            other.setText(OBJECT_LABELS[kind] if selected else "其它")
            other.setIcon(tool_icon(object_symbol(kind) if selected else "other"))
        for key, action in self.category_actions.items():
            if action.isCheckable():
                action.setChecked(key == kind or {"table": "mongo_collection", "view": "mongo_view",
                    "index": "mongo_index"}.get(key) == kind)
        self.tabs.setCurrentWidget(self.object_page)
        self.object_page.set_context(self.active_id, self.active_schema, kind)

    def show_other_tools(self):
        menu = QMenu(self)
        if self.configs.get(self.active_id) and self.configs[self.active_id].driver == "mongodb":
            for title, kind, available in [("索引", "mongo_index", True), ("MapReduce", "mongo_mapreduce", False),
                                           ("GridFS 存储桶", "mongo_bucket", True)]:
                action = menu.addAction(title, lambda checked=False, kind=kind: self.show_objects(kind))
                action.setEnabled(available and self.active_id in self.sessions)
                if not available:
                    action.setToolTip("此专用工具仍待实现。")
            menu.exec(self.mapToGlobal(self.rect().center()))
            return
        menu.addAction("数据同步…", self.compare_data)
        menu.addAction("ER 图表", self.open_diagram)
        menu.addAction("查询历史…", self.show_history)
        menu.exec(self.mapToGlobal(self.rect().center()))

    def find_current(self):
        page = self.tabs.currentWidget()
        if isinstance(page, QueryPage):
            page.editor.show_find()

    def explain_current(self):
        page = self.tabs.currentWidget()
        if isinstance(page, QueryPage):
            self.explain_query(page)

    def explain_query(self, page):
        if not getattr(page, "supports_explain", True):
            self.statusBar().showMessage("此数据库的执行计划尚待实现。", 5000)
            return
        sql = page.editor.selected_sql().strip()
        if not sql:
            return
        config = self.configs[page.connection_id]
        prefix = "EXPLAIN QUERY PLAN " if config.driver == "sqlite" else "EXPLAIN "
        target = self.new_query(sql=prefix + sql, connection_id=page.connection_id)
        target.schema = page.schema
        target.refresh_label()
        self.when_idle(lambda: self.run_query(target))

    def open_redis_browser(self, connection_id, schema):
        if connection_id not in self.sessions or self.configs[connection_id].driver != "redis":
            return None
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, RedisBrowserPage) and (page.connection_id, page.schema) == (connection_id, schema):
                self.tabs.setCurrentWidget(page)
                return page
        page = RedisBrowserPage(self, connection_id, schema)
        page.titleChanged.connect(lambda *_: self.update_redis_title(page))
        page.focusRequested.connect(lambda enabled: self.focus_workspace(page, enabled))
        self.tabs.addTab(page, workbench_icon("table", 20), "(所有数据)")
        self.tabs.setCurrentWidget(page)
        self.update_redis_title(page)
        return page

    def open_redis_key(self, connection_id, schema, key):
        page = self.open_redis_browser(connection_id, schema)
        return page.show_key(key) if page else None

    def update_redis_title(self, page):
        index = self.tabs.indexOf(page)
        if index >= 0:
            config = self.configs.get(page.connection_id)
            title = f"{page.title} ({config.name if config else ''})"
            self.tabs.setTabText(index, title)
            if self.tabs.currentWidget() is page:
                self.setWindowTitle(title)

    def open_mongo_collection(self, info):
        connection_id, database = info["id"], info["schema"]
        if connection_id not in self.sessions or connection_id in self._closing_connections or self._closing_mongo or self.configs[connection_id].driver != "mongodb":
            return None
        kind = info.get("kind", "mongo_collection")
        if kind == "mongo_bucket":
            return self.open_mongo_bucket(info)
        if kind not in {"mongo_collection", "mongo_timeseries", "mongo_view"}:
            self.statusBar().showMessage(f"{OBJECT_LABELS.get(kind, '此对象')}的专用工具仍待实现。", 5000)
            return None
        collection = info.get("name", info.get("table"))
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, MongoCollectionPage) and (page.connection_id, page.database, page.collection) == (connection_id, database, collection):
                self.tabs.setCurrentWidget(page)
                return page
        page = MongoCollectionPage(self, connection_id, database, collection,
            kind="view" if kind == "mongo_view" else kind)
        page.titleChanged.connect(lambda *_: self.update_mongo_title(page))
        page.focusRequested.connect(lambda enabled: self.focus_workspace(page, enabled))
        self.tabs.addTab(page, workbench_icon(object_symbol(kind), 20), collection)
        self.tabs.setCurrentWidget(page)
        self.update_mongo_title(page)
        return page

    def update_mongo_title(self, page):
        index = self.tabs.indexOf(page)
        config = self.configs.get(page.connection_id)
        if index >= 0 and config is not None:
            name = page.bucket if isinstance(page, MongoGridFSPage) else page.collection
            title = f"{name}@{page.database} ({config.name})" + (" *" if page.dirty else "")
            if isinstance(page, MongoGridFSPage):
                title += " - GridFS"
            self.tabs.setTabText(index, title)
            if self.tabs.currentWidget() is page:
                self.setWindowTitle(title)
                self.inspector.show_object({"name": name, "schema": page.database,
                    "kind": "mongo_bucket" if isinstance(page, MongoGridFSPage) else "mongo_view" if page.kind == "view" else page.kind}, config)

    def open_mongo_bucket(self, info):
        connection_id, database = info["id"], info["schema"]
        config = self.configs.get(connection_id)
        if connection_id not in self.sessions or not config or config.driver != "mongodb" or connection_id in self._closing_connections or self._closing_mongo:
            return None
        bucket = info.get("name", info.get("table"))
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, MongoGridFSPage) and (page.connection_id, page.database, page.bucket) == (connection_id, database, bucket):
                self.tabs.setCurrentWidget(page)
                return page
        page = MongoGridFSPage(self, connection_id, database, bucket)
        page.focusRequested.connect(lambda enabled: self.focus_workspace(page, enabled))
        page.titleChanged.connect(lambda *_: self.update_mongo_title(page))
        self.tabs.addTab(page, workbench_icon("database", 20), bucket)
        self.tabs.setCurrentWidget(page)
        self.update_mongo_title(page)
        return page

    def design_object(self, info):
        config = self.configs.get(info["id"])
        if config and config.driver == "mongodb":
            self.statusBar().showMessage("MongoDB 的集合及索引专用设计器仍待实现。", 5000)
            return None
        if config and config.driver == "sqlite":
            if info["kind"] in {"index", "trigger"}:
                return self.open_object_designer(info["id"], info["schema"], info["kind"], info.get("name", ""))
            if info["kind"] == "table":
                return self.open_designer(info["id"], info["schema"], info.get("name", info.get("table", "")))
        # Other dialects still expose read-only metadata in their own document;
        # opening a design must never hide the data tab of an existing document.
        page = TablePage(self, info["id"], info["schema"], info.get("name", info["table"]), info["kind"])
        page._structure_only = True
        page.subtabs.setTabVisible(0, False)
        page.subtabs.tabBar().show()
        page.subtabs.setCurrentIndex(1)
        self.tabs.setCurrentIndex(self.tabs.addTab(page, workbench_icon("design", 20),
            f"{page.table}@{page.schema} ({config.name})"))
        self.tabs.setTabToolTip(self.tabs.indexOf(page), "只读结构；此数据库的可编辑对象设计器仍待实现")
        return page

    def open_designer(self, connection_id, schema, table=""):
        session = self.sessions.get(connection_id)
        if session is None or self.configs[connection_id].driver != "sqlite":
            return None
        if table:
            for index in range(self.tabs.count()):
                page = self.tabs.widget(index)
                if isinstance(page, TableDesignerPage) and (page.connection_id, page.schema, page.table) == (connection_id, schema, table):
                    self.tabs.setCurrentWidget(page)
                    return page
        page = TableDesignerPage(connection_id, schema, table)
        # The window's File/Save shortcut routes to the current document; avoid
        # an ambiguous duplicate with the standalone page's shortcut.
        page.save_action.setShortcut(QKeySequence())
        page.set_read_only(self.configs[connection_id].read_only)
        page.suppress_save_preview = bool(self.store.state.get("suppress_schema_save_preview", False))
        page.loadRequested.connect(lambda: self.load_designer(page))
        page.previewRequested.connect(lambda definition: self.preview_designer(page, definition))
        page.saveRequested.connect(lambda definition: self.save_designer(page, definition))
        page.titleChanged.connect(lambda _: self.update_designer_title(page))
        page.focusRequested.connect(lambda enabled: self.focus_workspace(page, enabled))
        page.savePreviewPreferenceChanged.connect(lambda value: self.store.state.update(suppress_schema_save_preview=value))
        self.tabs.addTab(page, workbench_icon("design", 20), "无标题")
        self.tabs.setCurrentWidget(page)
        self.update_designer_title(page)
        if table:
            self.load_designer(page)
        else:
            page.load_snapshot(schema_tools.new_table(schema))
        return page

    def open_object_designer(self, connection_id, schema, kind, name=""):
        session = self.sessions.get(connection_id)
        if session is None or self.configs[connection_id].driver != "sqlite" or kind not in {"index", "trigger"}:
            return None
        if name:
            for index in range(self.tabs.count()):
                page = self.tabs.widget(index)
                if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) and (page.connection_id, page.schema, page.kind, page.object_name) == (connection_id, schema, kind, name):
                    self.tabs.setCurrentWidget(page)
                    return page
        page = (IndexDesignerPage if kind == "index" else TriggerDesignerPage)(connection_id, schema, name)
        page.save_action.setShortcut(QKeySequence())
        page.set_read_only(self.configs[connection_id].read_only)
        page.suppress_save_preview = bool(self.store.state.get("suppress_schema_save_preview", False))
        page.loadRequested.connect(lambda: self.load_designer(page))
        page.previewRequested.connect(lambda definition: self.preview_designer(page, definition))
        page.saveRequested.connect(lambda definition: self.save_designer(page, definition))
        page.titleChanged.connect(lambda _: self.update_designer_title(page))
        page.focusRequested.connect(lambda enabled: self.focus_workspace(page, enabled))
        page.savePreviewPreferenceChanged.connect(lambda value: self.store.state.update(suppress_schema_save_preview=value))
        page.ownerChanged.connect(lambda owner_type, table: self.load_object_columns(page, owner_type, table))
        self.tabs.addTab(page, workbench_icon(kind, 20), "无标题")
        self.tabs.setCurrentWidget(page)
        if name:
            self.load_designer(page)
        else:
            page.load_snapshot((object_schema_tools.new_index if kind == "index" else object_schema_tools.new_trigger)(schema))
        def owners_done(objects):
            if self.tabs.indexOf(page) >= 0 and self.sessions.get(connection_id) is session:
                page.set_owners([obj["name"] for obj in objects if obj["kind"] == "table"],
                    [obj["name"] for obj in objects if obj["kind"] == "view"])
        self.submit(lambda: session.objects(schema), owners_done, page.show_error, label="读取所属对象")
        self.update_designer_title(page)
        return page

    def load_object_columns(self, page, owner_type, table):
        session = self.sessions.get(page.connection_id)
        if self._closed or session is None or not table or self.tabs.indexOf(page) < 0:
            return
        def done(columns):
            if self.tabs.indexOf(page) >= 0 and self.sessions.get(page.connection_id) is session:
                page.set_columns(table, columns, owner_type)
        self.submit(lambda: session.columns(page.schema, table), done, page.show_error, label="读取字段")

    @staticmethod
    def _designer_api(page):
        if isinstance(page, IndexDesignerPage):
            return object_schema_tools.load_index, object_schema_tools.plan_index, object_schema_tools.apply_plan
        if isinstance(page, TriggerDesignerPage):
            return object_schema_tools.load_trigger, object_schema_tools.plan_trigger, object_schema_tools.apply_plan
        return schema_tools.load_table, schema_tools.plan_table, schema_tools.apply_plan

    def new_browser_object(self):
        if self.object_page.kind in {"index", "trigger"}:
            return self.open_object_designer(self.active_id, self.active_schema, self.object_page.kind)
        return self.new_table()

    def update_designer_title(self, page):
        index = self.tabs.indexOf(page)
        if index < 0:
            return
        config = self.configs.get(page.connection_id)
        name = page.definition.name if page.definition else page.table
        title = f"{name or '无标题'}@{page.schema} ({config.name if config else ''})"
        if page.dirty:
            title += " — 已编辑"
        self.tabs.setTabText(index, title)
        self.tabs.setTabToolTip(index, title)
        if self.tabs.currentWidget() is page:
            self.setWindowTitle(title)
            self.inspector.show_object({"name": name or "无标题", "schema": page.schema,
                "kind": page.kind if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) else "table"}, config, page.snapshot.ddl if page.snapshot else "")

    def load_designer(self, page):
        session = self.sessions.get(page.connection_id)
        if self._closed or session is None or page.loading or self.tabs.indexOf(page) < 0:
            return
        schema = page.schema
        table = page.object_name if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) else page.table
        loader, _, _ = self._designer_api(page)
        page.set_busy(True, "正在读取对象结构…")
        def done(snapshot):
            page.load_snapshot(snapshot)
            self.update_designer_title(page)
        self.submit(lambda: loader(session, schema, table), done,
            page.show_error, label="读取表设计", finished=lambda: page.set_busy(False))

    def preview_designer(self, page, definition):
        session = self.sessions.get(page.connection_id)
        if self._closed or session is None or page.snapshot is None or page.loading or self.tabs.indexOf(page) < 0:
            return
        snapshot, draft = deepcopy(page.snapshot), deepcopy(definition)
        def failed(error):
            if self.tabs.indexOf(page) < 0 or page.loading:
                return
            dialog = page._save_dialog
            current = dialog.definition if dialog is not None else page.definition
            if current == draft and page.snapshot.fingerprint == snapshot.fingerprint:
                page.show_error(error)
        _, planner, _ = self._designer_api(page)
        self.submit(lambda: planner(session, snapshot, draft),
            page.show_plan, failed, label="生成结构变更预览")

    def save_designer(self, page, definition):
        session = self.sessions.get(page.connection_id)
        if self._closed or session is None or page.snapshot is None or page.loading or self.tabs.indexOf(page) < 0:
            return
        original_table = page.snapshot.definition.table if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) else page.snapshot.table
        _, planner, apply = self._designer_api(page)
        affected_tables = {original_table, definition.table} if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) else {original_table}
        for index in range(self.tabs.count()):
            document = self.tabs.widget(index)
            if isinstance(document, TablePage) and (document.connection_id, document.schema) == (page.connection_id, page.schema) and document.table in affected_tables and document.model is not None and document.model.pending:
                page._after_save = None
                page.show_error(ValueError("请先提交或取消该表数据页中的未保存记录，再保存结构变更。"))
                return
        snapshot, draft = deepcopy(page.snapshot), deepcopy(definition)
        confirmed = deepcopy(page.confirmed_plan)
        page.set_busy(True, "正在保存表结构…")
        def work():
            plan = confirmed if confirmed is not None and confirmed.definition == draft else planner(session, snapshot, draft)
            return apply(session, plan)
        def done(updated):
            page.save_succeeded(updated)
            self.update_designer_title(page)
            # Refresh live data documents after the DDL, without silently
            # replacing another designer's unsaved draft.
            for index in range(self.tabs.count()):
                document = self.tabs.widget(index)
                affected_tables = {original_table, updated.definition.table} if isinstance(page, (IndexDesignerPage, TriggerDesignerPage)) else {original_table}
                if isinstance(document, TablePage) and (document.connection_id, document.schema) == (page.connection_id, page.schema) and document.table in affected_tables:
                    if isinstance(page, TableDesignerPage):
                        document.table = updated.table
                    self.when_idle(lambda p=document: p.refresh(check=False) if self.tabs.indexOf(p) >= 0 else None)
            self.when_idle(lambda: self.load_schemas(page.connection_id))
            if self.object_page.connection_id == page.connection_id and self.object_page.schema == page.schema:
                self.when_idle(self.object_page.refresh)
            retry = getattr(page, "_after_save", None)
            page._after_save = None
            if retry is not None:
                self.when_idle(retry)
        def failed(error):
            page._after_save = None
            page.show_error(error)
        self.submit(work, done, failed, label="保存表设计", finished=lambda: page.set_busy(False))

    def focus_workspace(self, page, enabled):
        if enabled:
            if self.tabs.currentWidget() is not page:
                return
            if self._focus_page is None:
                self._focus_visibility = (self.sidebar.isVisible(), self.inspector.isVisible(), self.main_toolbar.isVisible())
            self._focus_page = page
            self.sidebar.hide()
            self.inspector.hide()
            self.main_toolbar.hide()
        elif self._focus_page is not None:
            previous = self._focus_page
            self._focus_page = None
            for widget, visible in zip((self.sidebar, self.inspector, self.main_toolbar), self._focus_visibility):
                widget.setVisible(visible)
            if hasattr(previous, "focus_action"):
                previous.focus_action.setChecked(False)

    def can_leave_designer(self, page, retry):
        if not page.dirty:
            return True
        answer = QMessageBox.question(self, "是否保存更改？", "如果你不保存更改，你的更改将会丢失。",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            # Closing and disconnecting resume only after a successful save;
            # cancelling the preview leaves both draft and connection intact.
            page._after_save = retry
            page.request_save()
            if page._save_dialog is not None:
                page._save_dialog.rejected.connect(lambda: setattr(page, "_after_save", None))
            return False
        return True

    def drop_object(self, info):
        name, ok = QInputDialog.getText(self, "删除对象", f"输入对象名称 {info['name']} 以确认删除：")
        if not ok or name != info["name"]:
            return
        session = self.sessions[info["id"]]
        keyword = {"table": "TABLE", "view": "VIEW", "index": "INDEX", "trigger": "TRIGGER"}.get(info["kind"])
        if keyword is None or info.get("automatic") or session.config.read_only:
            return
        sql = f"DROP {keyword} {session.qualified_name(info['schema'], info['name'])}"
        if session.config.driver == "sqlserver" and keyword == "VIEW":
            inner = f"DROP VIEW {session.quote_identifier(info['schema'])}.{session.quote_identifier(info['name'])}"
            sql = f"EXEC {session.quote_identifier(session.config.database or 'master')}.sys.sp_executesql N'" + inner.replace("'", "''") + "'"
        def done(_):
            self.refresh_tree()
            self.object_page.refresh()
        self.submit(lambda: session.execute(sql), done, label="删除对象")

    def submit(self, function, success=None, failure=None, label="Working", finished=None, pool=None):
        if self._closed or not isValid(self):
            return
        task = Task(function)
        self.tasks.add(task)
        self.status.setText(label + "…")
        self.progress.show()
        def deliver(callback, *args):
            if not self._closed and isValid(self):
                callback(*args)
        if success:
            task.signals.succeeded.connect(lambda result: deliver(success, result))
        task.signals.failed.connect(lambda error: deliver(failure or self.error, error))
        def cleanup():
            self.tasks.discard(task)
            if self._closed or not isValid(self):
                return
            if finished:
                finished()
            if not self.tasks:
                self.progress.hide()
                self.status.setText("就绪")
            self.refresh_actions()
        task.signals.finished.connect(cleanup)
        self.refresh_actions()
        (pool or self.pool).start(task)

    def error(self, exc):
        QMessageBox.critical(self, "操作失败", str(exc))

    def _set_navicat_auto_discover(self, enabled):
        had_value = "navicat_auto_discover" in self.store.state
        previous = self.store.state.get("navicat_auto_discover", True)
        self.store.state["navicat_auto_discover"] = bool(enabled)
        try:
            self.store.flush()
        except Exception as error:
            if had_value:
                self.store.state["navicat_auto_discover"] = previous
            else:
                self.store.state.pop("navicat_auto_discover", None)
            self.navicat_auto_action.blockSignals(True)
            self.navicat_auto_action.setChecked(bool(previous))
            self.navicat_auto_action.blockSignals(False)
            self.error(error)

    def start_navicat_discovery(self, checked=False):
        """Explicit metadata-only startup/manual refresh, with no implicit login."""
        if self._closed or self._navicat_discovering:
            return False
        self._navicat_discovering = True
        self.navicat_reload_action.setEnabled(False)
        def failed(_error):
            if self._closed:
                return
            self.last_navicat_counts = {"failed": 1}
            self.statusBar().showMessage("无法重新加载 Navicat 连接；现有连接和分组保持不变。", 8000)
            self.navicat_discovery_finished.emit(self.last_navicat_counts)
        def done(report):
            if self._closed:
                return
            unavailable = {"source_read_failed", "registry_read_failed", "unsupported_source_structure",
                           "windows_discovery_pending", "unsupported_platform"}
            if not report.profiles and any(issue.code in unavailable for issue in report.issues):
                failed(None)
                return
            try:
                counts = self.store.sync_navicat_profiles(report)
                configs = {config.id: config for config in self.store.connections()}
            except Exception:
                failed(None)
                return
            pending = set()
            for identifier, session in self.sessions.items():
                stored = configs.get(identifier)
                current = replace(session.config, **{field: "" for field in self.store.SECRET_FIELDS})
                if stored is not None and stored != current and identifier in self.store.state.get("navicat_imports", {}):
                    pending.add(identifier)
                # The executing session and every UI operation retain exactly
                # the same endpoint/config until an explicit disconnect.
                configs[identifier] = session.config
            self._navicat_pending_configs = pending
            self.configs = configs
            self.populate_connections()
            self.update_schema_picker()
            self.refresh_actions()
            self.last_navicat_counts = dict(counts, pending=len(pending))
            message = (f"已加载 {counts['total']} 个 Navicat 连接：新增 {counts['added']}，更新 {counts['updated']}，"
                       f"保留本地编辑 {counts['preserved']}，待复核 {counts['blocked']}。")
            if pending:
                message += f" {len(pending)} 个已连接配置将在关闭连接后更新。"
            self.statusBar().showMessage(message, 10000)
            self.navicat_discovery_finished.emit(self.last_navicat_counts)
        def finished():
            self._navicat_discovering = False
            if not self._closed:
                self.navicat_reload_action.setEnabled(True)
        self.submit(discover_navicat_profiles, done, failed, "加载 Navicat 连接元数据", finished)
        return True

    def _connection_tooltip(self, config):
        lines = [f"{config.driver} · {config.database}"]
        imported = self.store.state.get("navicat_imports", {}).get(config.id)
        if imported:
            if imported.get("source_missing"):
                lines.append("来源已移除；保留本地连接")
            if config.driver not in SUPPORTED_DRIVERS:
                lines.append("未实现驱动；仅保留连接元数据")
            elif imported.get("blocked"):
                fields = imported.get("unsupported_fields", [])
                lines.append("待复核：请先编辑并确认连接设置" + ("（" + "、".join(fields) + "）" if fields else ""))
            if imported.get("requires_secrets"):
                lines.append("需补充凭据；不会读取 Navicat 已保存的密码")
            if config.id in self._navicat_pending_configs:
                lines.append("来源配置已更新；关闭当前连接后生效")
        lines.append("双击以打开连接")
        return "\n".join(lines)

    def populate_connections(self):
        snapshots = {}
        def capture(item):
            # PySide ties QTreeWidgetItem.clone() wrappers to the source tree;
            # even detached clones can die with tree.clear(). Keep plain values.
            info = deepcopy(item.data(0, Qt.ItemDataRole.UserRole))
            if info and info.get("type") == "schema":
                pending_key = (info["id"], info["schema"], self._tree_generation, id(self.sessions.get(info["id"])))
                if pending_key in self._loading_object_nodes:
                    # Its old callback will be discarded after the rebuild.
                    # Expanded replacement nodes must request a fresh snapshot.
                    info["loaded"] = False
            return {"text": item.text(0), "data": info,
                    "icon": QIcon(item.icon(0)), "tooltip": item.toolTip(0), "expanded": item.isExpanded(),
                    "children": [capture(item.child(index)) for index in range(item.childCount())]}
        def restore(parent, records):
            for record in records:
                child = QTreeWidgetItem([record["text"]])
                child.setData(0, Qt.ItemDataRole.UserRole, deepcopy(record["data"]))
                child.setIcon(0, record["icon"])
                child.setToolTip(0, record["tooltip"])
                parent.addChild(child)
                restore(child, record["children"])
                child.setExpanded(record["expanded"])
        for identifier in self.sessions:
            old = self.connection_item(identifier)
            if old:
                snapshots[identifier] = [capture(old.child(index)) for index in range(old.childCount())]
        self._tree_generation += 1
        self.tree.clear()
        previous = self.active_id
        self.connection_picker.blockSignals(True)
        self.connection_picker.clear()
        self.connection_picker.addItem("选择连接…", None)
        imports = self.store.state.get("navicat_imports", {})
        imported_root, folders = None, {}
        for config in self.configs.values():
            self.connection_picker.addItem(config.name, config.id)
            parents = [None]
            if config.id in imports:
                if imported_root is None:
                    imported_root = QTreeWidgetItem(["Navicat 连接"])
                    imported_root.setData(0, Qt.ItemDataRole.UserRole, {"type": "navicat_root"})
                    imported_root.setIcon(0, workbench_icon("folder", 16))
                    self.tree.addTopLevelItem(imported_root)
                    imported_root.setExpanded(True)
                parents = []
                paths = imports[config.id].get("group_paths") or [[]]
                for path in paths:
                    parent = imported_root
                    for depth, label in enumerate(path):
                        key = tuple(path[:depth+1])
                        if key not in folders:
                            folder = QTreeWidgetItem([label])
                            folder.setData(0, Qt.ItemDataRole.UserRole, {"type": "navicat_group", "group_path": key})
                            folder.setIcon(0, workbench_icon("folder", 16))
                            parent.addChild(folder)
                            folder.setExpanded(True)
                            folders[key] = folder
                        parent = folders[key]
                    if parent not in parents:
                        parents.append(parent)
            for parent in parents:
                item = QTreeWidgetItem([config.name])
                item.setData(0, Qt.ItemDataRole.UserRole, {"type": "connection", "id": config.id})
                item.setToolTip(0, self._connection_tooltip(config))
                item.setIcon(0, workbench_icon(config.driver, 16))
                if parent is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                if config.id in snapshots:
                    restore(item, snapshots[config.id])
                    item.setExpanded(True)
                else:
                    item.addChild(QTreeWidgetItem(["双击以打开连接"]))
        # Manual profiles keep their original positions before the import root.
        if imported_root is not None:
            position = self.tree.indexOfTopLevelItem(imported_root)
            self.tree.takeTopLevelItem(position)
            self.tree.addTopLevelItem(imported_root)
            imported_root.setExpanded(True)
            for folder in folders.values():
                folder.setExpanded(True)
        self.connection_picker.setCurrentIndex(max(0, self.connection_picker.findData(previous)))
        self.connection_picker.blockSignals(False)
        self.filter_tree(self.tree_filter.text())

    def selected_info(self):
        item = self.tree.currentItem()
        info = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return info if info and "id" in info else None

    def select_connection(self, index):
        self.active_id = self.connection_picker.itemData(index)
        self.update_schema_picker()
        self.refresh_actions()

    def select_schema(self, text):
        if text:
            self.active_schema = text
            page = self.tabs.currentWidget()
            if isinstance(page, QueryPage) and page.connection_id == self.active_id and not page.running:
                page.schema = text
                page.refresh_label()
        self.refresh_actions()

    def update_schema_picker(self):
        self.schema_picker.blockSignals(True)
        self.schema_picker.clear()
        root = self.connection_item(self.active_id)
        if root and self.active_id in self.sessions:
            for i in range(root.childCount()):
                info = root.child(i).data(0, Qt.ItemDataRole.UserRole)
                if info and info["type"] == "schema":
                    self.schema_picker.addItem(info["schema"])
        found = self.schema_picker.findText(self.active_schema)
        self.schema_picker.setCurrentIndex(max(0, found))
        self.schema_picker.blockSignals(False)
        self.active_schema = self.schema_picker.currentText() or "main"

    def set_active(self, connection_id, schema=None):
        self.active_id = connection_id
        self.connection_picker.blockSignals(True)
        self.connection_picker.setCurrentIndex(max(0, self.connection_picker.findData(connection_id)))
        self.connection_picker.blockSignals(False)
        if schema:
            self.active_schema = schema
        self.update_schema_picker()
        self.refresh_actions()

    def connection_items(self, connection_id):
        result = []
        def visit(item):
            info = item.data(0, Qt.ItemDataRole.UserRole)
            if info and info.get("type") == "connection":
                if info.get("id") == connection_id:
                    result.append(item)
                return
            for index in range(item.childCount()):
                visit(item.child(index))
        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
        return result

    def connection_item(self, connection_id):
        selected = self.tree.currentItem()
        while selected is not None:
            info = selected.data(0, Qt.ItemDataRole.UserRole)
            if info and info.get("type") == "connection" and info.get("id") == connection_id:
                return selected
            selected = selected.parent()
        return next(iter(self.connection_items(connection_id)), None)

    def _schema_items(self, connection_id, schema):
        return [root.child(index) for root in self.connection_items(connection_id)
                for index in range(root.childCount())
                if (root.child(index).data(0, Qt.ItemDataRole.UserRole) or {}).get("schema") == schema]

    def new_connection(self, checked=False, config=None, driver=None):
        selected_driver = config.driver if config is not None else driver
        if selected_driver is not None and selected_driver not in SUPPORTED_DRIVERS:
            QMessageBox.information(self, "未实现驱动", "此连接驱动尚未实现；原始元数据会保留，无法在其他驱动的设置中编辑。")
            return
        if self.tasks:
            self.statusBar().showMessage("Wait for the current operation before editing connections", 5000)
            return
        if config is None and driver is None:
            manager = ConnectionManagerDialog(self)
            self._connection_manager = manager
            manager.selectionRequested.connect(self.connection_type_selected)
            manager.open()
            return manager
        dialog = ConnectionDialog(self, config)
        if driver:
            dialog.driver_combo.setCurrentIndex(max(0, dialog.driver_combo.findData(driver)))
        if config:
            dialog.remember_check.setChecked(config.id in self.store.state["remembered_passwords"])
            remembered = self.store.state.get("remembered_secrets", {}).get(config.id, [])
            dialog.ssh_remember_check.setChecked("ssh_password" in remembered)
            dialog.ssh_key_remember_check.setChecked("ssh_key_passphrase" in remembered)
            dialog.ssl_remember_check.setChecked("ssl_key_password" in remembered)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_config()
        if result is None:
            return
        if result.id in self.sessions:
            if not self.disconnect(result.id):
                return
        try:
            self.store.save_connection(result, dialog.remember_password,
                remember_ssh=dialog.remember_ssh_password,
                remember_ssh_key=dialog.remember_ssh_key_passphrase,
                remember_ssl=dialog.remember_ssl_password)
        except Exception as exc:
            self.error(f"The operating system keychain could not save the password. The connection was not saved.\n\n{exc}")
            return
        self.configs[result.id] = result
        self.active_id = result.id
        self.populate_connections()
        self.set_active(result.id)
        if result.id not in self.store.state.get("navicat_imports", {}):
            self.connect_id(result.id)

    def connection_type_selected(self, driver, preset):
        # A selected URI remains in memory until the actual settings are saved.
        # The settings dialog owns the explicit OS-keychain choices.
        config = ConnectionConfig(id=str(uuid.uuid4()), **preset)
        self.new_connection(config=config)

    def edit_connection(self):
        config = self.configs.get(self.active_id)
        if config:
            if config.driver not in SUPPORTED_DRIVERS:
                QMessageBox.information(self, "未实现驱动", "此连接驱动尚未实现；连接元数据已保留，当前无法编辑。")
                return
            self.new_connection(config=self.store.hydrate(config))

    def connect_active(self):
        if self.active_id:
            self.connect_id(self.active_id)

    def connect_id(self, connection_id, after=None):
        if connection_id in self._closing_connections or self._closing_mongo:
            self.statusBar().showMessage("正在关闭连接，请等待操作完成。", 3000)
            return
        if connection_id in self.sessions:
            if after:
                after()
            return
        if connection_id in self._connecting or connection_id not in self.configs:
            return
        original = self.configs[connection_id]
        imported = self.store.state.get("navicat_imports", {}).get(connection_id, {})
        if original.driver not in SUPPORTED_DRIVERS:
            QMessageBox.information(self, "未实现驱动", "此连接驱动尚未实现；已保留连接元数据，尚不能打开连接。")
            return
        if imported.get("blocked"):
            QMessageBox.information(self, "待复核连接", "此 Navicat 连接包含尚未确认的选项。请先编辑、复核并保存连接设置，再手动打开连接。")
            return
        # Empty passwords can be intentional (certificate, local authentication,
        # or an account with an empty password). Try the configured method first.
        config = self.store.hydrate(self.configs[connection_id])
        self.configs[connection_id] = config
        self._connecting.add(connection_id)
        session = DatabaseSession(config)
        def work():
            try:
                session.connect()
                return session.schemas()
            except Exception:
                session.close()
                raise
        def done(schemas):
            self.sessions[connection_id] = session
            self.fill_schemas(connection_id, schemas)
            self.set_active(connection_id)
            if after:
                self.when_idle(after)
        def failed(error):
            if isinstance(error, SSHHostKeyError):
                self.when_idle(lambda: self._confirm_connection_host_key(config, error, after))
            elif isinstance(error, TLSKeyPasswordError) and not config.ssl_key_password:
                self.when_idle(lambda: self._request_transport_secret(config, "ssl_key_password", after))
            elif isinstance(error, SSHAuthenticationError):
                field = "ssh_key_passphrase" if error.needs_passphrase else "ssh_password"
                password_method = config.ssh_auth_method in {"password", "password+key"}
                if not getattr(config, field) and (error.needs_passphrase or password_method):
                    self.when_idle(lambda: self._request_transport_secret(config, field, after))
                else:
                    self.error(error)
            elif getattr(error, "authentication_required", False) and not config.password:
                self.when_idle(lambda: self._request_connection_password(config, after))
            else:
                self.error(error)
        self.submit(work, done, failed, label=f"连接 {config.name}",
                    finished=lambda: self._connecting.discard(connection_id))

    def _confirm_connection_host_key(self, config, error, after=None):
        # A queued response must never approve a different profile/endpoint.
        if self.configs.get(config.id) is not config:
            return
        if (config.ssh_host, config.ssh_port) != (error.hostname, error.port):
            return
        if not confirm_ssh_host_key(self, error):
            return
        config.ssh_host_key = error.pin
        # This public pin is metadata; updating it must not change keyring choices.
        saved = next((item for item in self.store.state["connections"]
                      if item["id"] == config.id), None)
        if saved and (saved.get("ssh_host"), saved.get("ssh_port", 22)) == (config.ssh_host, config.ssh_port):
            previous_pin = saved.get("ssh_host_key", "")
            saved["ssh_host_key"] = error.pin
            try:
                self.store.flush()
            except Exception as exc:
                saved["ssh_host_key"] = previous_pin
                config.ssh_host_key = previous_pin
                self.error(exc)
                return
        self.connect_id(config.id, after)

    def _request_connection_password(self, config, after=None):
        if self.configs.get(config.id) is not config:
            return
        password, ok = QInputDialog.getText(self, "连接密码",
            f"{config.username}@{config.host} 的数据库密码", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if not password:
            self.error("服务器已拒绝空密码，请输入数据库密码后重试。")
            return
        config.password = password
        self.store.passwords[config.id] = password
        self.connect_id(config.id, after)

    def _request_transport_secret(self, config, field, after=None):
        if self.configs.get(config.id) is not config:
            return
        title = {"ssh_password": "SSH 密码", "ssh_key_passphrase": "SSH 私钥通行短语",
                 "ssl_key_password": "SSL 客户端密钥通行短语"}[field]
        endpoint = config.host if field == "ssl_key_password" else f"{config.ssh_username}@{config.ssh_host}"
        secret, ok = QInputDialog.getText(self, title, f"{endpoint} 的{title}", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if not secret:
            self.error(f"请输入{title}后重试。")
            return
        setattr(config, field, secret)
        self.store.secrets[(config.id, field)] = secret
        self.connect_id(config.id, after)

    def when_idle(self, callback):
        if self._closed or not isValid(self):
            return
        if self.tasks:
            QTimer.singleShot(25, self, lambda: self.when_idle(callback))
        else:
            callback()

    def load_schemas(self, connection_id):
        session = self.sessions.get(connection_id)
        if session is None or self._closed or self._closing_mongo or connection_id in self._closing_connections:
            return
        def done(schemas):
            if self.sessions.get(connection_id) is session:
                self.fill_schemas(connection_id, schemas)
        self.submit(session.schemas, done, label="Loading schemas")

    def fill_schemas(self, connection_id, schemas):
        items = self.connection_items(connection_id)
        if not items:
            return
        schemas = list(schemas)
        for item in items:
            expanded = {item.child(index).text(0) for index in range(item.childCount())
                        if item.child(index).isExpanded()}
            if self.active_id == connection_id:
                expanded.add(self.active_schema)
            item.takeChildren()
            children = []
            for schema in schemas:
                child = QTreeWidgetItem([schema])
                child.setData(0, Qt.ItemDataRole.UserRole, {"type": "schema", "id": connection_id, "schema": schema, "loaded": False})
                child.setIcon(0, workbench_icon("database", 16))
                child.addChild(QTreeWidgetItem([""]))
                item.addChild(child)
                children.append(child)
            item.setExpanded(True)
            for child in children:
                if child.text(0) in expanded or len(schemas) == 1:
                    child.setExpanded(True)
                    self.tree_expand(child)
        self.update_schema_picker()

    def tree_expand(self, item):
        info = item.data(0, Qt.ItemDataRole.UserRole)
        if info and info["type"] == "schema" and not info.get("loaded"):
            self.load_objects(item, info)

    def load_objects(self, item, info):
        session = self.sessions.get(info["id"])
        if session is None or self._closing_mongo or info["id"] in self._closing_connections:
            return
        info["loaded"] = True
        item.setData(0, Qt.ItemDataRole.UserRole, info)
        generation = self._tree_generation
        key = (info["id"], info["schema"], generation, id(session))
        if key in self._loading_object_nodes:
            return
        self._loading_object_nodes.add(key)
        def current():
            return not self._closed and not self._closing_mongo and info["id"] not in self._closing_connections and self._tree_generation == generation and self.sessions.get(info["id"]) is session
        def render(item, objects):
            expanded = {item.child(i).text(0) for i in range(item.childCount()) if item.child(i).isExpanded()}
            item.takeChildren()
            completions = []
            categories = [("数据", "redis_data", "table"), ("查询", "query", "query"), ("备份", "backup", "backup")] if session.config.driver == "redis" else [("表", "table", "table"), ("视图", "view", "view"), ("查询", "query", "query"), ("备份", "backup", "backup")]
            if session.config.driver == "mongodb":
                categories = [("集合", "mongo_collection", "table"), ("视图", "mongo_view", "view"),
                    ("函数", "mongo_function", "function"), ("索引", "mongo_index", "index"),
                    ("MapReduce", "mongo_mapreduce", "query"), ("聚合管道", "mongo_pipeline", "query"),
                    ("GridFS 存储桶", "mongo_bucket", "database"), ("查询", "query", "query")]
            for label, kind, symbol in categories:
                category = QTreeWidgetItem([label])
                category.setIcon(0, workbench_icon(symbol, 16))
                category.setData(0, Qt.ItemDataRole.UserRole, {"type": "category", "id": info["id"], "schema": info["schema"], "kind": kind})
                item.addChild(category)
                category.setExpanded(label in expanded)
                if kind in {"mongo_function", "mongo_mapreduce", "mongo_pipeline"}:
                    category.setDisabled(True)
                    category.setToolTip(0, "此专用工具尚未实现，仍在完整对齐范围内。")
                if kind == "redis_data":
                    child = QTreeWidgetItem(["(所有数据)"])
                    child.setIcon(0, workbench_icon("table", 16))
                    child.setData(0, Qt.ItemDataRole.UserRole, {"type": "object", "id": info["id"], "schema": info["schema"], "table": "(所有数据)", "name": "(所有数据)", "kind": "redis_data"})
                    category.addChild(child)
                if kind in {"table", "view", "mongo_collection", "mongo_view", "mongo_bucket", "mongo_index"}:
                    for obj in objects:
                        matching = obj["kind"] == kind or kind == "mongo_collection" and obj["kind"] == "mongo_timeseries"
                        if not matching or obj.get("system"):
                            continue
                        child = QTreeWidgetItem([obj["name"]])
                        child.setData(0, Qt.ItemDataRole.UserRole, dict(obj, type="object", id=info["id"], schema=info["schema"], table=obj["name"]))
                        child.setIcon(0, workbench_icon(object_symbol(obj["kind"]), 16))
                        category.addChild(child)
                        completions.append(obj["name"])
            return completions
        def done(objects):
            self._loading_object_nodes.discard(key)
            if not current():
                return
            completions = []
            for target in self._schema_items(info["id"], info["schema"]):
                target_info = target.data(0, Qt.ItemDataRole.UserRole)
                target_info["loaded"] = True
                target.setData(0, Qt.ItemDataRole.UserRole, target_info)
                completions = render(target, objects)
            for index in range(self.tabs.count()):
                page = self.tabs.widget(index)
                if isinstance(page, QueryPage) and (page.connection_id, page.schema) == (info["id"], info["schema"]):
                    page.editor.set_completions(completions)
            self.filter_tree(self.tree_filter.text())
            if self.tabs.currentWidget() is self.object_page and (self.active_id, self.active_schema) == (info["id"], info["schema"]):
                kind = self.object_page.kind if (self.object_page.connection_id, self.object_page.schema) == (info["id"], info["schema"]) else "table"
                self.show_objects(kind)
        def failed(error):
            self._loading_object_nodes.discard(key)
            if not current():
                return
            for target in self._schema_items(info["id"], info["schema"]):
                target_info = target.data(0, Qt.ItemDataRole.UserRole)
                target_info["loaded"] = False
                target.setData(0, Qt.ItemDataRole.UserRole, target_info)
            self.error(error)
        def work():
            objects = [] if session.config.driver == "redis" else session.objects(info["schema"])
            if session.config.driver == "mongodb":
                objects += [dict(obj, kind="mongo_index") for obj in session.indexes(info["schema"])]
            return objects
        self.submit(work, done, failed, "读取对象")

    def tree_selected(self, item, column=0):
        info = item.data(0, Qt.ItemDataRole.UserRole)
        if not info or "id" not in info:
            return
        self.set_active(info["id"], info.get("schema"))
        if info["type"] == "category":
            self.show_objects(info["kind"])
        elif info["type"] == "schema" and self.tabs.currentWidget() is self.object_page:
            self.show_objects("table")
        elif info["type"] == "object":
            self.inspector.show_object(info, self.configs.get(info["id"]))

    def tree_open(self, item, column=0):
        info = item.data(0, Qt.ItemDataRole.UserRole)
        if not info:
            parent = item.parent()
            info = parent.data(0, Qt.ItemDataRole.UserRole) if parent else None
        if not info or "id" not in info:
            return
        self.set_active(info["id"], info.get("schema"))
        if info["type"] == "connection":
            self.connect_id(info["id"])
        elif info["type"] == "object":
            self.open_table(info)
        elif info["type"] in {"schema", "category"}:
            self.show_objects(info.get("kind", "table"))

    def open_table(self, info):
        if info["kind"] == "redis_data":
            return self.open_redis_browser(info["id"], info["schema"])
        if self.configs[info["id"]].driver == "mongodb":
            return self.open_mongo_collection(info)
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if isinstance(page, TablePage) and not getattr(page, "_structure_only", False) and (page.connection_id, page.schema, page.table) == (info["id"], info["schema"], info["table"]):
                self.tabs.setCurrentIndex(i)
                return page
        page = TablePage(self, info["id"], info["schema"], info["table"], info["kind"])
        self.tabs.setCurrentIndex(self.tabs.addTab(page, workbench_icon(info.get("kind", "table"), 20), f"{info['table']}@{info['schema']} ({self.configs[info['id']].name})"))
        return page

    def refresh_tree(self):
        if self.tasks:
            self.statusBar().showMessage("An operation is already in progress", 4000)
            return
        if self.active_id in self.sessions:
            self.load_schemas(self.active_id)
        page = self.tabs.currentWidget()
        if isinstance(page, MONGO_PAGES):
            page.refresh()
        elif isinstance(page, TablePage):
            page.refresh()
        elif isinstance(page, RedisBrowserPage):
            page.refresh()
        elif isinstance(page, DESIGNER_PAGES):
            if page.dirty:
                self.statusBar().showMessage("表设计中有未保存的更改，请先保存或关闭此草稿。", 5000)
            elif page.snapshot and page.snapshot.exists:
                self.load_designer(page)

    def filter_tree(self, text):
        text = text.casefold()
        active_only = self.active_only.isChecked()
        def apply(item, inherited=False):
            info = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if info.get("type") == "connection" and active_only and info.get("id") not in self.sessions:
                item.setHidden(True)
                return False
            match = text in item.text(0).casefold()
            child_match = False
            for index in range(item.childCount()):
                child_match = apply(item.child(index), inherited or match) or child_match
            visible = match or inherited or child_match
            if info.get("type") in {"navicat_root", "navicat_group"} and active_only:
                visible = child_match
            item.setHidden(not visible)
            return visible
        for index in range(self.tree.topLevelItemCount()):
            apply(self.tree.topLevelItem(index))

    def tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)
            self.tree_selected(item)
        info = self.selected_info()
        menu = QMenu(self)
        if info:
            if info["type"] == "connection":
                menu.addAction("打开连接", lambda: self.connect_id(info["id"]))
                menu.addAction("关闭连接", lambda: self.disconnect(info["id"]))
                menu.addAction("编辑连接…", self.edit_connection)
                menu.addAction("删除连接", lambda: self.remove_connection(info["id"]))
            if info["id"] in self.sessions:
                menu.addAction("新建查询", self.new_query)
                menu.addAction("刷新", self.refresh_tree)
            if info["type"] == "object":
                redis = self.configs[info["id"]].driver == "redis"
                mongo = self.configs[info["id"]].driver == "mongodb"
                menu.addAction("打开数据" if redis else "打开集合" if mongo else "打开表", lambda: self.open_table(info))
                if redis:
                    menu.addAction("新建 Redis 命令", lambda: self.new_query(connection_id=info["id"]))
                elif mongo:
                    def mongo_query():
                        name = json.dumps(info["table"], ensure_ascii=True)
                        page = self.new_query(sql=f"db.getCollection({name}).find({{}}).limit(1000)", connection_id=info["id"])
                        if page:
                            page.schema = info["schema"]
                            page.refresh_label()
                    menu.addAction("新建 MongoDB 查询", mongo_query)
                else:
                    def select_query():
                        session = self.sessions[info["id"]]
                        table = session.qualified_name(info["schema"], info["table"])
                        sql = f"SELECT TOP (1000) * FROM {table};" if session.config.driver == "sqlserver" else f"SELECT * FROM {table} LIMIT 1000;"
                        self.new_query(sql=sql, connection_id=info["id"])
                    menu.addAction("新建 SELECT 查询", select_query)
                menu.addAction("复制名称", lambda: QApplication.clipboard().setText(info["table"]))
        menu.addSeparator()
        menu.addAction(self.new_connection_action)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def new_query(self, checked=False, sql="", connection_id=None, query_language=None):
        connection_id = connection_id or self.active_id
        if connection_id not in self.configs:
            QMessageBox.information(self, "选择连接", "请先创建或选择一个连接。")
            return
        try:
            language = resolve_query_language(query_language, driver=self.configs[connection_id].driver, new=True)
        except ValueError as exc:
            self.error(exc)
            return
        page = NativeMongoQueryPage(self, connection_id, sql) if language == MONGO_JS else QueryPage(self, connection_id, sql)
        page.query_language = language
        root = self.connection_item(connection_id)
        completions = []
        if root:
            for i in range(root.childCount()):
                schema_item = root.child(i)
                if schema_item.text(0) == page.schema:
                    def collect(item):
                        info = item.data(0, Qt.ItemDataRole.UserRole)
                        if info and info.get("type") == "object":
                            completions.append(info["table"])
                        for j in range(item.childCount()):
                            collect(item.child(j))
                    collect(schema_item)
        page.editor.set_completions(completions)
        self.query_number += 1
        self.tabs.setCurrentIndex(self.tabs.addTab(page, workbench_icon("query", 20), f"无标题@{page.schema} ({self.configs[connection_id].name})"))
        page.editor.setFocus()
        return page

    def run_current(self):
        page = self.tabs.currentWidget()
        if isinstance(page, QueryPage):
            self.run_query(page)

    def run_query(self, page):
        if isinstance(page, NativeMongoQueryPage):
            return page.run_native()
        if page.running:
            return
        if self.tasks:
            self.statusBar().showMessage("Wait for the current operation to finish", 4000)
            return
        if page.connection_id not in self.sessions:
            self.connect_id(page.connection_id, lambda: self.run_query(page))
            return
        sql = page.editor.selected_sql().strip()
        if not sql:
            return
        session = self.sessions[page.connection_id]
        page.running = True
        page.run_button.setEnabled(False)
        page.message.setStyleSheet("")
        page.message.setText("正在运行…")
        started = time.perf_counter()
        limit = page.limit.value()
        schema = page.schema
        def done(results):
            page.show_results(results)
            self.store.record(session.config, sql, (time.perf_counter() - started) * 1000, "success")
        def failed(exc):
            partial = getattr(exc, "partial_results", [])
            page.show_error(exc, partial, sql=sql)
            self.store.record(session.config, sql, (time.perf_counter() - started) * 1000, "error")
        def finish():
            page.running = False
            page.run_button.setEnabled(True)
            self.refresh_actions()
        def work():
            if schema:
                session.use_schema(schema)
            return session.execute(sql, limit)
        label = "执行 MongoDB 查询" if session.config.driver == "mongodb" else "执行 Redis 命令" if session.config.driver == "redis" else "执行 SQL"
        self.submit(work, done, failed, label, finish)

    def stop(self):
        # Cancellation targets the executing connection, not an arbitrary profile.
        if self._cancelling:
            return
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if isinstance(page, QueryPage) and page.running:
                if isinstance(page, NativeMongoQueryPage):
                    page.request_cancel()
                    continue
                session = self.sessions.get(page.connection_id)
                if session:
                    self._cancelling = True
                    def finish():
                        self._cancelling = False
                    if session.config.driver == 'mongodb':
                        marker = session.cancellation_marker()
                        cancel = lambda session=session, marker=marker: session.cancel(expected_comment=marker)
                    else:
                        cancel = session.cancel
                    self.submit(cancel, label="Cancelling query", finished=finish, pool=self.cancel_pool)

    def transaction(self, sql):
        session = self.sessions.get(self.active_id)
        if session:
            if session.config.driver in {"redis", "mongodb"}:
                self.statusBar().showMessage("此驱动不使用 SQL 事务命令；专用事务工具仍待实现。", 5000)
                return
            statement = "BEGIN TRANSACTION" if sql == "BEGIN" and session.config.driver == "sqlserver" else sql
            self.submit(lambda: session.execute(statement), label=sql.title())

    def tab_changed(self, index):
        page = self.tabs.widget(index)
        if self._focus_page is not None and page is not self._focus_page:
            self.focus_workspace(self._focus_page, False)
        if isinstance(page, DESIGNER_PAGES):
            self.set_active(page.connection_id, page.schema)
            self.update_designer_title(page)
        if isinstance(page, RedisBrowserPage):
            self.set_active(page.connection_id, page.schema)
            self.update_redis_title(page)
        if isinstance(page, MONGO_PAGES):
            self.set_active(page.connection_id, page.database)
            self.update_mongo_title(page)
        if isinstance(page, (QueryPage, TablePage)):
            self.set_active(page.connection_id, getattr(page, "schema", None))
            config = self.configs.get(page.connection_id)
            if config:
                name = page.table if isinstance(page, TablePage) else Path(page.filename).name if page.filename else "无标题"
                self.setWindowTitle(f"{name}@{page.schema} ({config.name})")
                self.inspector.show_object({"name": name, "schema": page.schema, "kind": page.kind if isinstance(page, TablePage) else "query"}, config, page.ddl.toPlainText() if isinstance(page, TablePage) else "")
        elif page is self.object_page:
            self.setWindowTitle("OpenNavicat")
        self.refresh_actions()

    def refresh_actions(self):
        if not hasattr(self, "run_action") or not hasattr(self, "status"):
            return
        page = self.tabs.currentWidget()
        connected = self.active_id in self.sessions and self.active_id not in self._closing_connections and not self._closing_mongo
        if hasattr(self, "category_actions"):
            config = self.configs.get(self.active_id)
            sqlite = bool(config and config.driver == "sqlite")
            redis = bool(config and config.driver == "redis")
            mongo = bool(config and config.driver == "mongodb")
            self.category_actions["table"].setText("集合" if mongo else "数据" if redis else "表")
            for key in ("view", "function", "other", "index", "trigger"):
                self.category_actions[key].setVisible(not redis)
            self.category_actions["function"].setVisible(not sqlite and not redis)
            self.category_actions["other"].setVisible(not sqlite and not redis)
            self.category_actions["index"].setVisible(sqlite)
            if not mongo:
                self.category_actions["other"].setText("其它")
                self.category_actions["other"].setIcon(tool_icon("other"))
            self.category_actions["trigger"].setVisible(sqlite)
            self.category_actions["backup"].setVisible(not mongo)
            self.mongo_pipeline_action.setVisible(mongo)
            for key in ("table", "view", "index", "trigger", "query", "backup", "model", "other"):
                self.category_actions[key].setEnabled(connected)
            other = self.category_actions["other"]
            user = self.category_actions["user"]
            self.main_toolbar.removeAction(other)
            self.main_toolbar.removeAction(user)
            before = self.category_actions["query"]
            if mongo:
                self.main_toolbar.insertAction(self.category_actions["index"], user)
                self.main_toolbar.insertAction(self.mongo_pipeline_action, other)
            else:
                for action in ([user, other] if sqlite else [other, user]):
                    self.main_toolbar.insertAction(before, action)
        self.query_action.setEnabled(self.active_id in self.configs and self.configs[self.active_id].driver in SUPPORTED_DRIVERS)
        relational = connected and self.configs[self.active_id].driver not in {"redis", "mongodb"}
        self.table_action.setEnabled(relational and not self.configs[self.active_id].read_only)
        self.diagram_action.setEnabled(relational)
        self.category_actions["model"].setEnabled(relational)
        if hasattr(self, "object_page"):
            self.object_page.update_actions()
        self.run_action.setEnabled(isinstance(page, QueryPage) and not page.running)
        self.stop_action.setEnabled(not self._cancelling and any(isinstance(self.tabs.widget(i), QueryPage) and self.tabs.widget(i).running for i in range(self.tabs.count())))
        self.import_action.setEnabled(isinstance(page, TablePage) and page.kind == "table" and not self.configs[page.connection_id].read_only)
        self.export_action.setEnabled(isinstance(page, (QueryPage, TablePage)) and page.current_result() is not None)
        session = self.sessions.get(self.active_id)
        if hasattr(self, "transaction_status"):
            self.transaction_status.setText("事务已开始" if session and session.in_transaction else "")
        if isinstance(page, QueryPage) and page.current_result() is not None and not page.running:
            self.status.setText(f"{len(page.current_result().rows)} 条记录")
        elif isinstance(page, TablePage) and page.model is not None and not page.loading:
            self.status.setText(f"{page.total} 条记录在第 {page.offset // int(page.page_size.currentText()) + 1} 页")
        elif isinstance(page, MONGO_PAGES) and page.page is not None and not page.loading:
            unit = "文件" if isinstance(page, MongoGridFSPage) else "文档"
            self.status.setText(f"本页 {len(page.page.documents)} 个{unit}" + (" · 已编辑" if page.dirty else ""))
        elif isinstance(page, DESIGNER_PAGES) and page.definition is not None and not page.loading:
            self.status.setText((f"字段数: {len(page.definition.columns)}" if hasattr(page.definition, "columns") else "触发器") + (" · 已编辑" if page.dirty else ""))
        elif page is self.object_page and not self.tasks:
            self.status.setText(self.object_page.status_text())

    def disconnect_active(self):
        if self.active_id:
            self.disconnect(self.active_id)

    def disconnect(self, connection_id):
        if connection_id in self._closing_connections:
            return False
        if self.tasks:
            QMessageBox.information(self, "操作进行中", "请等待当前操作完成后再关闭连接。")
            return False
        pages = [(i, self.tabs.widget(i)) for i in range(self.tabs.count()) if getattr(self.tabs.widget(i), "connection_id", None) == connection_id]
        for _, page in pages:
            if isinstance(page, TablePage) and not page.can_discard():
                return False
            if isinstance(page, (RedisBrowserPage, *MONGO_PAGES)) and not page.can_discard(lambda: self.disconnect(connection_id)):
                return False
            if isinstance(page, DESIGNER_PAGES) and not self.can_leave_designer(page, lambda: self.disconnect(connection_id)):
                return False
        session = self.sessions.get(connection_id)
        if session and session.in_transaction:
            if QMessageBox.question(self, "事务尚未结束", "回滚尚未提交的事务并关闭连接？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return False
        if session and session.config.driver == "mongodb":
            # MongoClient.close can drain cursors and send commands. Keep that
            # work on the same serial owner as reads/writes, then change widgets.
            self._closing_connections.add(connection_id)
            for _, page in pages:
                if isinstance(page, MONGO_PAGES):
                    page._closed = True
            def failed(error):
                self._closing_connections.discard(connection_id)
                for _, page in pages:
                    if isinstance(page, MONGO_PAGES):
                        page._closed = False
                self.error(error)
            self.submit(session.close, lambda _value: self._finish_disconnect(connection_id, session, pages),
                        failed, label="关闭 MongoDB 连接")
            return True
        if session:
            session.close()
        return self._finish_disconnect(connection_id, session, pages)

    def _finish_disconnect(self, connection_id, session, pages):
        self._closing_connections.discard(connection_id)
        if self.sessions.get(connection_id) is session:
            self.sessions.pop(connection_id, None)
        if self.object_page.connection_id == connection_id:
            self.object_page.refresh()
        for index, page in reversed(pages):
            if isinstance(page, (TablePage, RedisBrowserPage, *MONGO_PAGES, *DESIGNER_PAGES)):
                if isinstance(page, MONGO_PAGES):
                    page._closed = True
                self.tabs.removeTab(index)
                page.deleteLater()
        if connection_id in self._navicat_pending_configs:
            saved = next((config for config in self.store.connections() if config.id == connection_id), None)
            if saved is not None:
                self.configs[connection_id] = saved
                index = self.connection_picker.findData(connection_id)
                if index >= 0:
                    self.connection_picker.setItemText(index, saved.name)
                for tab in range(self.tabs.count()):
                    page = self.tabs.widget(tab)
                    if isinstance(page, QueryPage) and page.connection_id == connection_id:
                        page.refresh_label()
            self._navicat_pending_configs.discard(connection_id)
        for item in self.connection_items(connection_id):
            item.takeChildren()
            item.addChild(QTreeWidgetItem(["双击以打开连接"]))
            config = self.configs.get(connection_id)
            if config:
                item.setText(0, config.name)
                item.setToolTip(0, self._connection_tooltip(config))
        self.filter_tree(self.tree_filter.text())
        self.update_schema_picker()
        self.refresh_actions()
        return True

    def remove_connection(self, connection_id):
        if QMessageBox.question(self, "删除连接", "删除这个已保存的连接？数据库文件和服务器数据不受影响。") != QMessageBox.StandardButton.Yes:
            return
        if not self.disconnect(connection_id):
            return
        self.store.delete_connection(connection_id)
        del self.configs[connection_id]
        self.populate_connections()
        self.set_active(None)

    def close_tab(self, index):
        page = self.tabs.widget(index)
        if index == 0 or self.tasks or getattr(page, "running", False) or getattr(page, "loading", False):
            return
        if isinstance(page, TablePage) and not page.can_discard():
            return
        if isinstance(page, DESIGNER_PAGES) and not self.can_leave_designer(page, lambda: self.close_tab(self.tabs.indexOf(page))):
            return
        if isinstance(page, (RedisBrowserPage, *MONGO_PAGES)) and not page.can_discard(lambda: self.close_tab(self.tabs.indexOf(page))):
            return
        if isinstance(page, QueryPage) and page.modified:
            answer = QMessageBox.question(self, "保存查询", "关闭前保存此查询？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save and not self.save_sql(page=page):
                return
        self.tabs.removeTab(index)
        if isinstance(page, MONGO_PAGES):
            page._closed = True
        page.deleteLater()

    def open_sql(self):
        filename, _ = QFileDialog.getOpenFileName(self, "打开查询", "", "Queries (*.sql *.js *.json *.redis);;All files (*)")
        if not filename:
            return
        try:
            sql = Path(filename).read_text("utf-8-sig")
            language = self.store.saved_file_language(filename, sql)
            if self.configs.get(self.active_id) and self.configs[self.active_id].driver == 'mongodb' and language is None:
                choice, accepted = QInputDialog.getItem(self, '查询语言', '文件没有执行语言记录，请选择其原有语义：',
                    ['旧版 MongoDB 命令 / Canonical EJSON（保留原整数语义）', 'MongoDB JavaScript（普通 Number 保存为 Double）'], 0, False)
                if not accepted: return
                language = MONGO_JS if choice.startswith('MongoDB JavaScript') else LEGACY
            page = self.new_query(sql=sql, query_language=language or LEGACY)
            if page:
                self.store.remember_file_language(filename, sql, page.query_language)
                page.filename = filename
                page.modified = False
                self.tabs.setTabText(self.tabs.indexOf(page), Path(filename).name)
        except Exception as exc:
            self.error(exc)

    def save_sql(self, checked=False, page=None):
        page = page or self.tabs.currentWidget()
        if isinstance(page, MongoGridFSPage):
            return False
        if isinstance(page, (RedisBrowserPage, MongoCollectionPage, *DESIGNER_PAGES)):
            page.request_save()
            return False  # schema writes are asynchronous, never a synchronous SQL-file save.
        if not isinstance(page, QueryPage):
            return False
        filename = page.filename
        if not filename:
            redis = self.configs[page.connection_id].driver == "redis"
            mongo = self.configs[page.connection_id].driver == "mongodb"
            filename, _ = QFileDialog.getSaveFileName(self, "保存查询", "query.js" if mongo else "commands.redis" if redis else "query.sql", "MongoDB queries (*.js *.json)" if mongo else "Redis commands (*.redis *.txt)" if redis else "SQL files (*.sql)")
        if not filename:
            return False
        try:
            Path(filename).write_text(page.editor.toPlainText(), "utf-8")
            self.store.remember_file_language(filename, page.editor.toPlainText(), getattr(page, 'query_language', LEGACY))
            page.filename, page.modified = filename, False
            self.tabs.setTabText(self.tabs.indexOf(page), Path(filename).name)
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def format_sql(self):
        page = self.tabs.currentWidget()
        if isinstance(page, QueryPage):
            page.format_sql()

    def new_table(self):
        session = self.sessions.get(self.active_id)
        if not session:
            return
        if session.config.driver == "sqlite":
            return self.open_designer(self.active_id, self.active_schema)
        dialog = TableDesignerDialog(self, session, self.active_schema)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        sql = dialog.sql()
        if sql:
            page = self.new_query(sql=sql)
            page.message.setText("请检查 SQL 预览，运行后创建表。")

    def import_rows(self):
        page = self.tabs.currentWidget()
        if not isinstance(page, TablePage) or not page.can_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import into " + page.table, "", "Data files (*.csv *.json)")
        if not path:
            return
        try:
            columns, rows = import_data(path)
            known = {c["name"] for c in page.metadata}
            if not set(columns).issubset(known):
                raise ValueError("Import columns do not match this table: " + ", ".join(set(columns) - known))
        except Exception as exc:
            self.error(exc)
            return
        if QMessageBox.question(self, "Import preview", f"Insert {len(rows):,} rows into {page.schema}.{page.table}?\n\nColumns: {', '.join(columns)}\n\nThe batch is atomic. CSV uses \\N for NULL and doubles literal backslashes.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        session = self.sessions[page.connection_id]
        def done(count):
            self.statusBar().showMessage(f"Imported {count:,} rows", 7000)
            page.refresh(check=False)
        self.submit(lambda: session.import_rows(page.schema, page.table, columns, rows), done, label="Importing data")

    def export_rows(self):
        page = self.tabs.currentWidget()
        result = page.current_result() if isinstance(page, (QueryPage, TablePage)) else None
        if result is None:
            return
        path, selected_filter = QFileDialog.getSaveFileName(self, "Export loaded rows", "result.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".json" if "JSON" in selected_filter else ".csv"
        try:
            export_result(result, path)
            self.statusBar().showMessage(f"Exported {len(result.rows):,} loaded rows to {path}", 9000)
        except Exception as exc:
            self.error(exc)

    def compare_data(self):
        page = self.tabs.currentWidget()
        if not isinstance(page, TablePage):
            QMessageBox.information(self, "数据同步", "请先打开源表，再选择已连接的目标表。")
            return
        if self.tasks or not page.can_discard():
            return
        dialog = CompareTargetDialog(self, page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target_id, schema, table = dialog.target()
        source = self.sessions[page.connection_id]
        target = self.sessions[target_id]
        def done(plan):
            preview = ComparisonPage(self, plan)
            self.tabs.setCurrentIndex(self.tabs.addTab(preview, "Data comparison"))
        self.submit(lambda: compare_tables(source, target, page.schema, page.table, schema, table), done, label="Comparing table data")

    def open_diagram(self):
        if self.tasks or self.active_id not in self.sessions:
            return
        session = self.sessions[self.active_id]
        schema = self.active_schema
        connection_id = self.active_id
        connection_name = session.config.name
        def work():
            objects = [obj for obj in session.objects(schema) if obj["kind"] == "table"]
            if len(objects) > 150:
                raise ValueError("The relationship viewer supports up to 150 tables per schema. Choose a smaller schema for a readable diagram.")
            return [{"name": obj["name"], "columns": session.columns(schema, obj["name"]), "foreign_keys": session.foreign_keys(schema, obj["name"])} for obj in objects]
        def done(tables):
            diagram = SchemaDiagram(connection_name, schema, tables)
            diagram.connection_id = connection_id
            self.tabs.setCurrentIndex(self.tabs.addTab(diagram, f"Diagram · {schema}"))
        self.submit(work, done, label="Reading schema relationships")

    def add_favorite(self):
        page = self.tabs.currentWidget()
        if not isinstance(page, QueryPage):
            return
        name, ok = QInputDialog.getText(self, "保存查询", "查询名称")
        if ok and name.strip():
            self.store.add_favorite(name.strip(), page.editor.selected_sql(), page.connection_id, query_language=getattr(page, 'query_language', LEGACY))
            self.statusBar().showMessage("Query saved to favorites", 5000)

    def show_history(self):
        self.query_library(False)

    def show_favorites(self):
        self.query_library(True)

    def query_library(self, favorites):
        dialog = QDialog(self)
        dialog.setWindowTitle("已保存的查询" if favorites else "查询历史")
        dialog.resize(900, 590)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("搜索查询…")
        layout.addWidget(search)
        splitter = QSplitter()
        listing = QListWidget()
        preview = SqlEditor()
        preview.setReadOnly(True)
        splitter.addWidget(listing)
        splitter.addWidget(preview)
        splitter.setSizes([300, 600])
        layout.addWidget(splitter)
        def load():
            listing.clear()
            items = self.store.state["favorites"] if favorites else self.store.history(search.text())
            for entry in items:
                if favorites and search.text().casefold() not in (entry["name"] + entry["sql"]).casefold():
                    continue
                label = entry["name"] if favorites else f"{entry['connection_name']} · {entry['status']}\n{entry['created']}\n{entry['sql'][:90]}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, entry)
                listing.addItem(item)
        search.textChanged.connect(load)
        listing.currentItemChanged.connect(lambda item, _: preview.setPlainText(item.data(Qt.ItemDataRole.UserRole)["sql"] if item else ""))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_button = buttons.addButton("打开查询", QDialogButtonBox.ButtonRole.AcceptRole)
        def open_entry():
            item = listing.currentItem()
            if item:
                entry = item.data(Qt.ItemDataRole.UserRole)
                connection_id = entry.get("connection_id")
                if connection_id not in self.configs:
                    connection_id = self.active_id
                if self.new_query(sql=entry["sql"], connection_id=connection_id, query_language=entry.get('query_language') if entry.get('query_language') is not None else LEGACY):
                    dialog.accept()
        open_button.clicked.connect(open_entry)
        listing.itemDoubleClicked.connect(lambda _: open_entry())
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        load()
        dialog.exec()

    def backup_sqlite(self):
        session = self.sessions.get(self.active_id)
        if not session or session.config.driver != "sqlite":
            QMessageBox.information(self, "SQLite 备份", "请选择一个已打开的 SQLite 连接。")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Back up SQLite database", "backup.sqlite3", "SQLite (*.sqlite3 *.db)")
        if filename:
            self.submit(lambda: session.backup_to(filename), lambda _: self.statusBar().showMessage("Database backup completed", 7000), label="Backing up database")

    def sample_workspace(self):
        if self.tasks:
            self.statusBar().showMessage("Wait for the current operation to finish", 4000)
            return
        try:
            path = create_sample(self.store.directory)
            config = next((c for c in self.configs.values() if c.driver == "sqlite" and c.database == str(path)), None)
            if config is None:
                config = ConnectionConfig(id=str(uuid.uuid4()), name="Sample workspace", database=str(path), driver="sqlite")
                self.store.save_connection(config)
                self.configs[config.id] = config
                self.active_id = config.id
                self.populate_connections()
            self.set_active(config.id)
            self.connect_id(config.id, lambda: self.new_query(sql=SAMPLE_QUERY, connection_id=config.id))
        except Exception as exc:
            self.error(exc)

    def restore_drafts(self):
        unrestored = []
        for draft in self.store.state.get("drafts", []):
            if draft.get("connection_id") in self.configs:
                page = self.new_query(sql=draft.get("sql", ""), connection_id=draft["connection_id"], query_language=draft.get('query_language') if draft.get('query_language') is not None else LEGACY)
                if page:
                    page.filename = draft.get("filename")
                    page.schema = draft.get("schema", page.schema)
                    page.refresh_label()
                    page.modified = bool(draft.get("modified", False))
                    if page.filename:
                        self.tabs.setTabText(self.tabs.indexOf(page), Path(page.filename).name + (" •" if page.modified else ""))
                else:
                    unrestored.append(deepcopy(draft))
            else:
                unrestored.append(deepcopy(draft))
        self._unrestored_query_drafts = unrestored

    def shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", "Use Command on macOS and Control on Windows/Linux.\n\nNew connection: Ctrl+Shift+N\nNew query: Ctrl+N\nOpen SQL: Ctrl+O\nSave SQL: Ctrl+S\nRun selection / script: Ctrl+Enter\nFind in SQL: Ctrl+F\nFormat SQL: Ctrl+Shift+F\nQuery history: Ctrl+H\nRefresh: F5\nCopy grid selection: Ctrl+C")

    def about(self):
        QMessageBox.about(self, "关于 OpenNavicat", f"<h2>OpenNavicat {__version__}</h2><p>原生桌面数据库工具。</p><p>SQLite · PostgreSQL · MySQL · MariaDB · SQL Server · Redis · MongoDB<br>Qt 6 / Python · MIT License</p><p>完整界面及工具功能还原进行中。</p>")

    def closeEvent(self, event):
        if self._closing_mongo:
            event.ignore()
            return
        if self.tasks:
            self.stop()
            QMessageBox.information(self, "操作进行中", "已请求停止正在执行的查询。请等待操作完成后再关闭窗口。")
            event.ignore()
            return
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if isinstance(page, DESIGNER_PAGES) and not self.can_leave_designer(page, self.close):
                event.ignore()
                return
            if isinstance(page, TablePage) and not page.can_discard():
                event.ignore()
                return
            if isinstance(page, (RedisBrowserPage, *MONGO_PAGES)) and not page.can_discard(self.close):
                event.ignore()
                return
        mongo_sessions = [(identifier, session) for identifier, session in self.sessions.items()
                          if session.config.driver == "mongodb"]
        if mongo_sessions:
            self._closing_mongo = True
            mongo_pages = [self.tabs.widget(index) for index in range(self.tabs.count())
                           if isinstance(self.tabs.widget(index), MONGO_PAGES)]
            for page in mongo_pages:
                page._closed = True
            def close_mongo():
                for _, session in mongo_sessions:
                    session.close()
            def closed(_value):
                for identifier, session in mongo_sessions:
                    if self.sessions.get(identifier) is session:
                        self.sessions.pop(identifier)
                self._closing_mongo = False
                self.when_idle(self.close)
            def failed(error):
                self._closing_mongo = False
                for page in mongo_pages:
                    page._closed = False
                self.error(error)
            self.submit(close_mongo, closed, failed, label="关闭 MongoDB 连接")
            event.ignore()
            return
        if any(session.in_transaction for session in self.sessions.values()):
            if QMessageBox.question(self, "事务尚未结束", "回滚尚未提交的事务并退出？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        drafts = deepcopy(getattr(self, '_unrestored_query_drafts', []))
        for i in range(self.tabs.count()):
            page = self.tabs.widget(i)
            if isinstance(page, QueryPage):
                drafts.append({"connection_id": page.connection_id, "schema": page.schema, "sql": page.editor.toPlainText(), "filename": page.filename, "modified": page.modified, 'query_language': getattr(page, 'query_language', LEGACY)})
        self.store.state["drafts"] = drafts
        self.store.state["ui_revision"] = 2
        self.store.state["info_visible"] = self.info_action.isChecked()
        self.store.state["navigation_visible"] = self.navigation_action.isChecked()
        self.store.state["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.store.state["splitter_sizes"] = self.splitter.sizes()
        try:
            self.store.flush()
            for session in self.sessions.values():
                session.close()
        except Exception as exc:
            self.error(exc)
            event.ignore()
            return
        self._closed = True
        event.accept()
