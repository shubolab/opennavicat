"""Dense native SQL and Redis workspaces using the observed query layout."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QMenu, QPlainTextEdit, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .editor import SqlEditor, pretty_sql
from .grid import DataGrid, DataModel
from .icons import CONNECTION_ICON_DRIVERS, connection_icon, database_icon, toggle_icon, tool_icon


def _execution_icon(stop=False):
    """Use the shared scalable execution artwork and disabled-state rendering."""
    return tool_icon("stop" if stop else "run", 16)


class _QueryGrid(DataGrid):
    """Continue real column rules through unused viewport space, without fake rows."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if getattr(self, "paints_unused_grid", False):
            return
        if not self.model():
            return
        count = self.model().rowCount()
        top = self.rowViewportPosition(count - 1) + self.rowHeight(count - 1) if count else 0
        top = max(0, top)
        if top >= self.viewport().height():
            return
        painter = QPainter(self.viewport())
        color = self.palette().mid().color()
        color.setAlpha(80)
        painter.setPen(color)
        for column in range(self.model().columnCount()):
            x = self.columnViewportPosition(column) + self.columnWidth(column) - 1
            painter.drawLine(x, top, x, self.viewport().height())
        painter.end()


class QueryPage(QWidget):
    def __init__(self, window, connection_id, sql=""):
        super().__init__()
        self.window = window
        self.connection_id = connection_id
        self.schema = self._initial_schema(connection_id)
        self.filename = None
        self.modified = False
        self._running = False
        self._has_results = False
        self._last_results = []
        self.setObjectName("queryPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.toolbar = QFrame()
        self.toolbar.setObjectName("queryToolbar")
        self.toolbar.setFixedHeight(36)
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(7, 3, 7, 3)
        tools.setSpacing(4)
        self.save_button = self._tool("保存查询", "save", lambda: window.save_sql(page=self))
        tools.addWidget(self.save_button)
        tools.addWidget(self._separator())
        self.builder_button = self._tool("查询创建工具（尚未实现）", "builder", available=False)
        tools.addWidget(self.builder_button)
        self.format_button = self._tool("格式化 SQL", "format", self.format_sql)
        tools.addWidget(self.format_button)
        self.snippets_button = self._tool("代码片段库（尚未实现）", "snippets", available=False)
        tools.addWidget(self.snippets_button)
        self.ai_button = self._tool("询问 AI（尚未实现）", "ai", available=False)
        tools.addWidget(self.ai_button)
        tools.addWidget(self._separator())
        self.export_button = self._tool("导出结果", "export", window.export_rows)
        self.export_button.setEnabled(False)
        tools.addWidget(self.export_button)
        tools.addStretch()
        self.results_below_button = self._tool("在编辑器下方显示结果", "result_below", self.results_below)
        self.results_beside_button = self._tool("在编辑器旁边显示结果", "result_beside", self.results_beside)
        for button, symbol in ((self.results_below_button, "result_below"),
                               (self.results_beside_button, "result_beside")):
            button.setCheckable(True)
            button.setIcon(toggle_icon(symbol, 16))
        self.results_below_button.setChecked(True)
        tools.addWidget(self.results_below_button)
        tools.addWidget(self.results_beside_button)
        tools.addWidget(self._separator())
        self.focus_action = self._tool("专注模式", "focus")
        self.focus_action.setIcon(toggle_icon("focus", 16))
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(lambda enabled: window.focus_workspace(self, enabled))
        tools.addWidget(self.focus_action)
        layout.addWidget(self.toolbar)

        self.execution_bar = QFrame()
        self.execution_bar.setObjectName("queryExecutionBar")
        self.execution_bar.setFixedHeight(34)
        execution = QHBoxLayout(self.execution_bar)
        execution.setContentsMargins(7, 3, 7, 3)
        execution.setSpacing(8)
        self.connection_picker = QComboBox()
        self.connection_picker.setIconSize(QSize(14, 14))
        self.connection_picker.setFixedWidth(150)
        self.connection_picker.setToolTip("连接")
        self.schema_picker = QComboBox()
        self.schema_picker.setIconSize(QSize(14, 14))
        self.schema_picker.setFixedWidth(150)
        self.schema_picker.setToolTip("数据库 / 模式")
        execution.addWidget(self.connection_picker)
        execution.addWidget(self.schema_picker)
        self.run_button = QToolButton()
        self.run_button.setObjectName("queryRun")
        self.run_button.setIcon(_execution_icon())
        self.run_button.setIconSize(QSize(16, 16))
        self.run_button.setText("运行")
        self.run_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.run_button.setFixedSize(68, 26)
        self.run_button.setAutoRaise(True)
        self.run_button.setToolTip("运行（⌘ / Ctrl + Enter）")
        self.run_button.clicked.connect(lambda: window.run_query(self))
        self.run_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.run_button.customContextMenuRequested.connect(self._run_menu)
        execution.addWidget(self.run_button)
        self.stop_button = QToolButton()
        self.stop_button.setObjectName("queryStop")
        self.stop_button.setIcon(_execution_icon(True))
        self.stop_button.setIconSize(QSize(16, 16))
        self.stop_button.setFixedSize(24, 24)
        self.stop_button.setAutoRaise(True)
        self.stop_button.setToolTip("停止运行")
        self.stop_button.clicked.connect(window.stop)
        self.stop_button.setEnabled(False)
        execution.addWidget(self.stop_button)
        self.explain_button = self._tool("解释 SQL / 执行计划", "explain", self._explain)
        self.explain_button.setFixedSize(24, 24)
        if not callable(getattr(window, "explain_query", None)):
            self.explain_button.setEnabled(False)
            self.explain_button.setToolTip("执行计划功能尚未实现。")
        execution.addWidget(self.explain_button)
        execution.addStretch()
        layout.addWidget(self.execution_bar)

        # Retain the public compatibility attributes without adding a banner or
        # a visible result-limit spinner to the observed compact query layout.
        self.connection_label = QLabel(self)
        self.connection_label.hide()
        self.limit = QSpinBox(self)
        self.limit.setRange(1, 100000)
        self.limit.setValue(1000)
        self.limit.hide()

        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.setObjectName("querySplitter")
        self.split.setHandleWidth(3)
        self.editor = SqlEditor()
        self.editor.setPlaceholderText("")
        font = self.editor.font()
        font.setPixelSize(12)
        self.editor.setFont(font)
        self.editor.setPlainText(sql)
        self.editor.document().setModified(False)
        self.editor.textChanged.connect(self.mark_modified)
        self.editor.execute_requested.connect(lambda: window.run_query(self))
        self.split.addWidget(self.editor)
        self.results = QTabWidget()
        self.results.setObjectName("queryResults")
        self.results.setDocumentMode(False)
        self.results.tabBar().setExpanding(False)
        self.results.setMinimumHeight(120)
        self.results.currentChanged.connect(self._result_changed)
        self.split.addWidget(self.results)
        self.results.hide()
        self.split.setStretchFactor(0, 55)
        self.split.setStretchFactor(1, 45)
        layout.addWidget(self.split, 1)

        self.footer = QFrame()
        self.footer.setObjectName("queryFooter")
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(6, 0, 6, 0)
        footer.setSpacing(10)
        self.message = QLabel()
        self.message.setObjectName("queryStatus")
        self.message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.message.setWordWrap(False)
        footer.addWidget(self.message, 1)
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("queryElapsed")
        footer.addWidget(self.elapsed_label)
        self.footer.setFixedHeight(22)
        layout.addWidget(self.footer)
        self.connection_picker.currentIndexChanged.connect(self._connection_changed)
        self.schema_picker.currentTextChanged.connect(self._schema_changed)
        self.refresh_label()

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(20)
        return line

    def _tool(self, tooltip, name, callback=None, available=True):
        button = QToolButton()
        button.setAutoRaise(True)
        button.setIcon(tool_icon(name, 16))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(26, 26)
        button.setToolTip(tooltip)
        button.setEnabled(available)
        if callback:
            button.clicked.connect(callback)
        return button

    @property
    def running(self):
        return self._running

    @running.setter
    def running(self, value):
        self._running = bool(value)
        if hasattr(self, "stop_button"):
            self.stop_button.setEnabled(self._running)
            self.run_button.setEnabled(not self._running)
            self.connection_picker.setEnabled(not self._running)
            self.schema_picker.setEnabled(not self._running)
            self._update_protocol_actions()

    @property
    def protocol_driver(self):
        config = self.window.configs.get(self.connection_id)
        return config.driver if config else "sqlite"

    @property
    def supports_explain(self):
        return self.protocol_driver in {"sqlite", "postgresql", "mysql", "mariadb"} and callable(getattr(self.window, "explain_query", None))

    def _initial_schema(self, connection_id):
        config = self.window.configs[connection_id]
        session = getattr(self.window, "sessions", {}).get(connection_id)
        selected = getattr(session, "_selected_schema", None)
        if selected is not None and selected != "":
            return str(selected)
        if config.driver == "sqlserver":
            return "dbo"
        if config.driver == "redis":
            return config.database or "0"
        if config.driver == "mongodb":
            return config.database or "admin"
        if config.driver == "sqlite":
            return "main"
        active = getattr(self.window, "active_schema", None)
        if active and active != "main" and getattr(self.window, "active_id", connection_id) == connection_id:
            return active
        return "public" if config.driver == "postgresql" else config.database

    def _apply_protocol_mode(self):
        native = self.protocol_driver == "redis"
        mongo = self.protocol_driver == "mongodb"
        self.editor.set_language("text" if mongo else "redis" if native else "sql")
        self.editor.setPlaceholderText('MongoDB：Canonical Extended JSON 命令，或 db.getCollection(...).find(...)/aggregate(...)。完整 mongosh JavaScript 仍待实现。' if mongo else 'Redis 原生命令，每行一条；参数支持 CLI 引号和 \\xNN 字节转义。' if native else "")
        self.schema_picker.setToolTip("MongoDB 数据库" if mongo else "Redis 数据库编号" if native else "数据库 / 模式")
        self._update_protocol_actions()

    def _update_protocol_actions(self):
        native = self.protocol_driver in {"redis", "mongodb"}
        self.format_button.setEnabled(not native)
        self.format_button.setToolTip("此原生查询不使用 SQL 格式化。" if native else "格式化 SQL")
        self.builder_button.setEnabled(False)
        self.builder_button.setToolTip("MongoDB 可视化聚合管道设计器尚未实现。" if self.protocol_driver == "mongodb" else "Redis 使用原生命令，不使用 SQL 查询创建工具。" if native else "查询创建工具（尚未实现）")
        self.explain_button.setEnabled(not self.running and self.supports_explain)
        if native:
            self.explain_button.setToolTip("MongoDB explain 专用工具尚未实现。" if self.protocol_driver == "mongodb" else "Redis 原生命令不支持 SQL EXPLAIN。")
        elif self.protocol_driver == "sqlserver":
            self.explain_button.setToolTip("SQL Server 执行计划尚未实现。")
        else:
            self.explain_button.setToolTip("解释 SQL / 执行计划" if self.supports_explain else "执行计划功能尚未实现。")

    def refresh_label(self):
        self.connection_picker.blockSignals(True)
        self.connection_picker.clear()
        for connection_id, config in self.window.configs.items():
            self.connection_picker.addItem(connection_icon(config.driver, 14, connected=connection_id in getattr(self.window, "sessions", {})) if config.driver in CONNECTION_ICON_DRIVERS else QIcon(), config.name, connection_id)
        self.connection_picker.setCurrentIndex(self.connection_picker.findData(self.connection_id))
        self.connection_picker.blockSignals(False)
        self.schema_picker.blockSignals(True)
        self.schema_picker.clear()
        root = self.window.connection_item(self.connection_id)
        if root:
            for index in range(root.childCount()):
                info = root.child(index).data(0, Qt.ItemDataRole.UserRole)
                if info and info["type"] == "schema":
                    self.schema_picker.addItem(database_icon(14, opened=bool(info.get("loaded"))), info["schema"])
        if self.schema and self.schema_picker.findText(self.schema) < 0:
            self.schema_picker.addItem(database_icon(14, opened=self.connection_id in getattr(self.window, "sessions", {})), self.schema)
        self.schema_picker.setCurrentIndex(self.schema_picker.findText(self.schema or ""))
        self.schema_picker.blockSignals(False)
        config = self.window.configs.get(self.connection_id)
        self.connection_label.setText(f"{config.name if config else ''} / {self.schema or ''}")
        self._apply_protocol_mode()
        self.refresh_context_icons()

    def refresh_context_icons(self):
        """Refresh badge state without rebuilding selectors or emitting changes."""
        sessions = getattr(self.window, "sessions", {})
        for row in range(self.connection_picker.count()):
            identifier = self.connection_picker.itemData(row)
            config = self.window.configs.get(identifier)
            if config:
                state = [config.driver, identifier in sessions]
                if self.connection_picker.itemData(row, Qt.ItemDataRole.UserRole + 18) != state:
                    badge = connection_icon(config.driver, 14, connected=identifier in sessions) if config.driver in CONNECTION_ICON_DRIVERS else QIcon()
                    self.connection_picker.setItemIcon(row, badge)
                    self.connection_picker.setItemData(row, state, Qt.ItemDataRole.UserRole + 18)
        root = self.window.connection_item(self.connection_id)
        schemas = {}
        if root and self.connection_id in sessions:
            for index in range(root.childCount()):
                child = root.child(index)
                info = child.data(0, Qt.ItemDataRole.UserRole) or {}
                if info.get("type") == "schema":
                    schemas[info["schema"]] = child.icon(0)
        for row in range(self.schema_picker.count()):
            current = schemas.get(self.schema_picker.itemText(row))
            # Preserve the native icon's identity between ordinary action refreshes.
            key = current.cacheKey() if current and not current.isNull() else 0
            if self.schema_picker.itemData(row, Qt.ItemDataRole.UserRole + 18) != key:
                self.schema_picker.setItemIcon(row, current if key else database_icon(14, opened=False))
                self.schema_picker.setItemData(row, key, Qt.ItemDataRole.UserRole + 18)


    def _connection_changed(self, index):
        if self.running:
            return
        connection_id = self.connection_picker.itemData(index)
        if connection_id not in self.window.configs or connection_id == self.connection_id:
            return
        self.connection_id = connection_id
        self.schema = self._initial_schema(connection_id)
        self.editor.set_completions([])
        self.refresh_label()
        self.window.set_active(connection_id, self.schema)
        self._clear_result_tabs()
        for attribute in ("info", "summary"):
            if hasattr(self, attribute):
                delattr(self, attribute)
        self.results.hide()
        self._has_results = False
        self._last_results = []
        self.message.clear()
        self.elapsed_label.clear()
        self.export_button.setEnabled(False)
        if callable(getattr(self.window, "refresh_actions", None)):
            self.window.refresh_actions()

    def _schema_changed(self, text):
        if text and not self.running:
            self.schema = text
            self.window.set_active(self.connection_id, text)

    def mark_modified(self):
        self.modified = True
        index = self.window.tabs.indexOf(self)
        if index >= 0:
            name = Path(self.filename).name if self.filename else self.window.tabs.tabText(index).rstrip(" •*")
            self.window.tabs.setTabText(index, f"{name} *")

    def format_sql(self):
        if self.protocol_driver in {"redis", "mongodb"}:
            return False
        self.editor.setPlainText(pretty_sql(self.editor.toPlainText()))
        return True

    def results_below(self):
        self.split.setOrientation(Qt.Orientation.Vertical)
        self.results_below_button.setChecked(True)
        self.results_beside_button.setChecked(False)
        self.split.setSizes([55, 45])

    def results_beside(self):
        self.split.setOrientation(Qt.Orientation.Horizontal)
        self.results_below_button.setChecked(False)
        self.results_beside_button.setChecked(True)
        self.split.setSizes([55, 45])

    def _run_menu(self, position):
        menu = QMenu(self)
        limit = menu.addAction(f"限制返回记录…（当前 {self.limit.value():,}）")
        limit.triggered.connect(self._choose_limit)
        menu.exec(self.run_button.mapToGlobal(position))

    def _choose_limit(self):
        value, accepted = QInputDialog.getInt(self, "限制返回记录", "每个结果集最多返回记录数", self.limit.value(), 1, 100000, 1000)
        if accepted:
            self.limit.setValue(value)

    def _explain(self):
        if not self.supports_explain or self.running:
            return
        explain = getattr(self.window, "explain_query", None)
        if callable(explain):
            explain(self)

    def _result_changed(self, *_):
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(self.current_result() is not None)
        if hasattr(self.window, "refresh_actions"):
            self.window.refresh_actions()

    def _clear_result_tabs(self):
        while self.results.count():
            widget = self.results.widget(0)
            self.results.removeTab(0)
            widget.deleteLater()

    def show_results(self, results):
        if self.protocol_driver in {"redis", "mongodb"}:
            session = getattr(self.window, "sessions", {}).get(self.connection_id)
            selected = getattr(session, "_selected_schema", None)
            if selected is not None and str(selected) != self.schema:
                self.schema = str(selected)
                self.refresh_label()
                self.window.set_active(self.connection_id, self.schema)
        first_execution = not self._has_results
        self._last_results = list(results)
        self._clear_result_tabs()
        self.info = QPlainTextEdit()
        self.info.setObjectName("queryInformation")
        self.info.setReadOnly(True)
        info = []
        for number, result in enumerate(results, 1):
            info.extend([f"[{number}] {result.sql.strip()}",
                         "此命令只取得部分结果；请结合下方错误检查实际完成的操作。" if result.partial else "",
                         f"受影响的行数：{result.affected:,}    执行时间：{result.elapsed_ms:.3f} ms",
                         "已达到返回记录限制。" if result.truncated else "", ""])
        self.info.setPlainText("\n".join(info))
        self.results.addTab(self.info, "消息")
        self.summary = QTableWidget(len(results), 4)
        self.summary.setObjectName("querySummary")
        self.summary.setHorizontalHeaderLabels(["命令" if self.protocol_driver == "redis" else "查询", "状态", "受影响的行数", "执行时间"])
        self.summary.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary.verticalHeader().setDefaultSectionSize(20)
        self.summary.horizontalHeader().setStretchLastSection(True)
        for row, result in enumerate(results):
            for column, value in enumerate([row + 1, "部分完成" if result.partial else "成功", result.affected, f"{result.elapsed_ms:.3f} ms"]):
                self.summary.setItem(row, column, QTableWidgetItem(str(value)))
        self.results.addTab(self.summary, "摘要")
        first_grid = None
        result_number = 0
        for result in results:
            if not result.columns:
                continue
            result_number += 1
            grid = _QueryGrid()
            grid.setObjectName("queryResultGrid")
            grid.setModel(DataModel(result, parent=grid))
            grid.verticalHeader().setDefaultSectionSize(20)
            grid.verticalHeader().setMinimumSectionSize(20)
            grid.horizontalHeader().setStretchLastSection(False)
            grid.horizontalHeader().setFixedHeight(22)
            grid.setAlternatingRowColors(True)
            index = self.results.addTab(grid, f"结果 {result_number}")
            if first_grid is None:
                first_grid = index
        self.results.setCurrentIndex(first_grid if first_grid is not None else 0)
        self.results.show()
        self._has_results = True
        if first_execution:
            height = max(300, self.split.height())
            self.split.setSizes([round(height * 0.55), round(height * 0.45)])
        elapsed = sum(result.elapsed_ms for result in results)
        self.elapsed_label.setText(f"执行时间：{elapsed / 1000:.3f} s")
        latest_sql = results[-1].sql.strip().replace("\n", " ") if results else ""
        self.message.setText(latest_sql)
        self.message.setToolTip(latest_sql)
        self._result_changed()

    def current_result(self):
        widget = self.results.currentWidget()
        return widget.model().result if isinstance(widget, DataGrid) else None

    def show_error(self, error, partial_results=None, sql=None):
        """Show an actual execution failure in Information, retaining partial sets."""
        self.show_results(partial_results or [])
        sql = self.editor.selected_sql().strip() if sql is None else sql
        prefix = self.info.toPlainText().rstrip()
        details = f"{sql}\n\n[错误] {error}"
        self.info.setPlainText((prefix + "\n\n" if prefix else "") + details)
        self.results.setCurrentIndex(0)
        self.message.setText(f"{'命令' if self.protocol_driver == 'redis' else '查询'}失败：{error}")
        self.message.setToolTip(str(error))
        self._result_changed()
