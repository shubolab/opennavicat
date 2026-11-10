"""Object workspace and inspector observed in the native reference application."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .icons import icon, tool_icon
from ..query_language import LEGACY

OBJECT_LABELS = {"table": "表", "view": "视图", "index": "索引", "trigger": "触发器",
    "redis_data": "数据", "query": "查询", "backup": "备份", "mongo_collection": "集合",
    "mongo_timeseries": "时间序列集合", "mongo_view": "视图", "mongo_index": "索引",
    "mongo_function": "函数", "mongo_mapreduce": "MapReduce", "mongo_pipeline": "聚合管道",
    "mongo_bucket": "GridFS 存储桶"}

def object_symbol(kind):
    return {"mongo_collection": "table", "mongo_timeseries": "table", "mongo_view": "view",
            "mongo_index": "index", "mongo_bucket": "database"}.get(kind, kind)


class ObjectBrowser(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.connection_id = None
        self.schema = "main"
        self.kind = "table"
        self.records = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setFixedHeight(36)
        self.toolbar.setStyleSheet("QToolButton { min-width: 26px; max-width: 30px; padding: 1px; }")
        self.open_action = self.add_action("打开表", "open", self.open_selected)
        self.design_action = self.add_action("设计表", "design", self.design_selected)
        self.new_action = self.add_action("新建表", "add", window.new_browser_object)
        self.delete_action = self.add_action("删除表", "delete", self.drop_selected)
        self.toolbar.addSeparator()
        self.import_action = self.add_action("导入向导", "import", self.import_selected)
        self.export_action = self.add_action("导出向导", "export", self.export_selected)
        self.toolbar.addSeparator()
        self.refresh_action = self.add_action("刷新", "refresh", self.refresh)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.details_action = self.add_action("详细信息", "list", lambda: self.table.show())
        self.er_action = self.add_action("ER 图表", "er", window.open_diagram)
        self.toolbar.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self.filter_rows)
        self.toolbar.addWidget(self.search)
        self.system_indexes_check = QCheckBox("显示系统索引")
        self.system_indexes_check.setObjectName("showSystemIndexes")
        self.system_indexes_check.toggled.connect(self.refresh)
        self.system_indexes_action = self.toolbar.addWidget(self.system_indexes_check)
        self.system_indexes_action.setVisible(False)
        layout.addWidget(self.toolbar)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("objectList")
        self.table.setHorizontalHeaderLabels(["名称", "根页面", "有索引", "有触发器", ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for index, width in enumerate([215, 120, 100, 120]):
            self.table.setColumnWidth(index, width)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.table.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.table)
        self.update_actions()

    def add_action(self, title, symbol, callback):
        action = QAction(tool_icon(symbol, 18), title, self)
        action.triggered.connect(callback)
        self.toolbar.addAction(action)
        return action

    def set_context(self, connection_id, schema, kind="table"):
        self.connection_id, self.schema, self.kind = connection_id, schema, kind
        label = OBJECT_LABELS.get(kind, "对象")
        self.open_action.setText("打开" + label)
        self.design_action.setText("设计" + label)
        self.new_action.setText("新建" + label)
        self.delete_action.setText("删除" + label)
        headers = {"index": ["名称", "唯一键", "自动索引", "表名称", "根页面"],
                   "trigger": ["名称", "类型", "表名称", "触发", ""]}.get(kind,
                   ["名称", "根页面", "有索引", "有触发器", ""])
        if kind == "mongo_collection":
            headers = ["名称", "文档数", "已固定", "内存中的总大小", "对象的平均大小", "存储大小", "索引数", "索引的总大小"]
        elif kind.startswith("mongo_"):
            headers = ["名称", "集合", "类型", "", ""] if kind == "mongo_index" else ["名称", "", "", "", ""]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.system_indexes_action.setVisible(kind == "index")
        if kind == "redis_data":
            self.table.setHorizontalHeaderLabels(["名称", "创建日期", "修改日期", "", ""])
        self.open_action.setVisible(kind not in {"index", "trigger"})
        self.refresh()

    def refresh(self):
        if self.window._closed or getattr(self.window, "_closing_mongo", False) or self.connection_id in getattr(self.window, "_closing_connections", ()):
            return
        session = self.window.sessions.get(self.connection_id)
        selected = self.selected()
        self._selection_hint = selected["name"] if selected and (selected["id"], selected["schema"], selected["kind"]) == (self.connection_id, self.schema, self.kind) else None
        self.table.setRowCount(0)
        self.records = []
        self.update_actions()
        if session is None:
            return
        schema, kind, connection_id = self.schema, self.kind, self.connection_id
        if kind == "redis_data":
            self.show_objects([dict(name="(所有数据)", kind=kind, schema=schema, id=connection_id)])
            return
        if kind == "query":
            items = [{"name": entry["name"], "kind": "query", "schema": schema, "sql": entry["sql"], "id": connection_id,
                      'query_language': entry.get('query_language') if entry.get('query_language') is not None else LEGACY}
                     for entry in self.window.store.state["favorites"] if entry.get("connection_id") == connection_id]
            self.show_objects(items)
            return
        if kind == "backup":
            items = [{"name": entry["name"], "kind": "backup", "schema": schema, "path": entry["path"], "id": connection_id} for entry in self.window.store.state.get("backups", []) if entry.get("connection_id") == connection_id]
            self.show_objects(items)
            return
        show_system = self.system_indexes_check.isChecked()
        def work():
            if session.config.driver == "mongodb":
                if kind == "mongo_index":
                    return [dict(obj, kind=kind, schema=schema, id=connection_id)
                            for obj in session.indexes(schema)], 0
                if kind in {"mongo_function", "mongo_mapreduce", "mongo_pipeline"}:
                    return [], 0
                all_objects = session.objects(schema)
                matching = {"mongo_collection", "mongo_timeseries"} if kind == "mongo_collection" else {kind}
                objects = [dict(obj, id=connection_id) for obj in all_objects
                           if obj["kind"] in matching and not obj.get("system")]
                hidden = sum(obj["kind"] in matching and bool(obj.get("system")) for obj in all_objects)
                return objects, hidden
            if session.config.driver == "sqlite" and kind in {"index", "trigger"}:
                from ..schema_objects import load_trigger
                rows = session._query(f"SELECT name,tbl_name,rootpage,sql FROM {session.quote_identifier(schema)}.sqlite_schema WHERE type=? ORDER BY name", (kind,))
                objects = []
                hidden = 0
                for name, owner, rootpage, ddl in rows:
                    obj = dict(name=name, table=owner, rootpage=rootpage, ddl=ddl or "", schema=schema, kind=kind, id=connection_id)
                    if kind == "index":
                        obj["automatic"] = ddl is None
                        if obj["automatic"] and not show_system:
                            hidden += 1
                            continue
                        metadata = session._query(f"PRAGMA {session.quote_identifier(schema)}.index_list({session.quote_identifier(owner)})")
                        match = next((row for row in metadata if row[1] == name), None)
                        obj["unique"] = bool(match and match[2])
                    else:
                        snapshot = load_trigger(session, schema, name)
                        obj["owner_type"] = snapshot.definition.owner_type
                        obj["event"] = snapshot.definition.event
                        obj["timing"] = snapshot.definition.timing
                    objects.append(obj)
                return objects, hidden
            objects = [dict(obj, id=connection_id) for obj in session.objects(schema) if obj["kind"] == kind]
            if session.config.driver == "sqlite":
                rootpages = dict(session._query(f"SELECT name,rootpage FROM {session.quote_identifier(schema)}.sqlite_schema WHERE type IN ('table','view')"))
                triggers = {row[0] for row in session._query(f"SELECT DISTINCT tbl_name FROM {session.quote_identifier(schema)}.sqlite_schema WHERE type='trigger'")}
                for obj in objects:
                    obj["rootpage"] = rootpages.get(obj["name"], "")
                    obj["indexed"] = bool(session._query(f"PRAGMA {session.quote_identifier(schema)}.index_list({session.quote_identifier(obj['name'])})")) if kind == "table" else False
                    obj["triggers"] = obj["name"] in triggers
            return objects, 0
        def done(result):
            objects, hidden = result
            if self.window.sessions.get(connection_id) is session and not getattr(self.window, "_closing_mongo", False) and connection_id not in getattr(self.window, "_closing_connections", ()) and (self.connection_id, self.schema, self.kind) == (connection_id, schema, kind):
                self.hidden_system_indexes = hidden
                self.show_objects(objects)
        self.window.submit(work, done, label="读取对象")

    def show_objects(self, objects):
        self.records = objects
        self.table.setRowCount(len(objects))
        for row, entry in enumerate(objects):
            values = [entry["name"], entry.get("rootpage", ""), "是" if entry.get("indexed") else "否", "是" if entry.get("triggers") else "否", ""]
            if self.kind == "index":
                values = [entry["name"], "是" if entry.get("unique") else "否", "是" if entry.get("automatic") else "否", entry["table"], entry.get("rootpage", "")]
            elif self.kind == "trigger":
                values = [entry["name"], "View" if entry.get("owner_type") == "view" else "Table", entry["table"], entry.get("timing", "") + " " + entry.get("event", ""), ""]
            elif self.kind == "mongo_collection":
                stats = entry.get("stats", {})
                values = [entry["name"], stats.get("count", ""),
                    ("是" if stats["capped"] else "否") if "capped" in stats else "",
                    stats.get("size", ""), stats.get("avgObjSize", ""), stats.get("storageSize", ""),
                    stats.get("nindexes", ""), stats.get("totalIndexSize", "")]
            elif self.kind == "mongo_index":
                values = [entry["name"], entry.get("collection", ""), str(entry.get("keys", "")), "", ""]
            elif self.kind.startswith("mongo_"):
                values = [entry["name"], "", "", "", ""]
            if self.kind in {"query", "backup", "redis_data"}:
                values = [entry["name"], "", "", "", ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setIcon(icon(object_symbol(entry["kind"]), 16))
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                self.table.setItem(row, column, item)
        self.filter_rows(self.search.text())
        if getattr(self, "_selection_hint", None):
            for row, entry in enumerate(objects):
                if entry["name"] == self._selection_hint:
                    self.table.setCurrentCell(row, 0)
                    break
        self.update_actions()
        self.window.status.setText(self.status_text())

    def status_text(self):
        hidden = getattr(self, "hidden_system_indexes", 0)
        suffix = f" · 隐藏 {hidden} 个系统项目" if hidden and self.kind.startswith("mongo_") else f" · 隐藏 {hidden} 个系统索引" if self.kind == "index" else ""
        return f"{len(self.records)} " + OBJECT_LABELS.get(self.kind, "对象") + suffix

    def selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selection_changed(self):
        self.update_actions()
        entry = self.selected()
        if entry:
            self.window.inspector.show_object(entry, self.window.configs.get(self.connection_id))
            session = self.window.sessions.get(self.connection_id)
            if self.window.inspector.isVisible() and session and entry["kind"] in {"table", "view"}:
                def done(ddl):
                    if self.selected() == entry and self.window.tabs.currentWidget() is self:
                        self.window.inspector.show_object(entry, self.window.configs.get(entry["id"]), ddl)
                self.window.submit(lambda: session.table_ddl(entry["schema"], entry["name"]), done, label="读取对象信息")

    def update_actions(self):
        selected = self.selected() is not None
        connected = self.connection_id in self.window.sessions and self.connection_id not in getattr(self.window, "_closing_connections", ()) and not getattr(self.window, "_closing_mongo", False)
        writable = connected and not self.window.configs[self.connection_id].read_only
        self.open_action.setEnabled(selected and self.kind != "mongo_index")
        self.design_action.setEnabled(selected and self.kind in {"table", "view", "index", "trigger"})
        self.new_action.setEnabled(writable and self.kind in {"table", "index", "trigger"})
        self.delete_action.setEnabled(writable and selected and self.kind in {"table", "view", "index", "trigger"} and not self.selected().get("automatic"))
        self.import_action.setEnabled(writable and selected and self.kind == "table")
        self.export_action.setEnabled(connected and selected and self.kind in {"table", "view"})
        self.er_action.setEnabled(connected and self.window.configs[self.connection_id].driver not in {"redis", "mongodb"})

    def filter_rows(self, text):
        text = text.casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            self.table.setRowHidden(row, bool(item and text not in item.text().casefold()))

    def open_selected(self):
        entry = self.selected()
        if not entry:
            return
        if entry["kind"] == "query":
            self.window.new_query(sql=entry["sql"], connection_id=entry["id"], query_language=entry.get('query_language') if entry.get('query_language') is not None else LEGACY)
        elif entry["kind"] == "backup":
            self.window.statusBar().showMessage(entry["path"], 10000)
        elif entry["kind"] == "redis_data":
            self.window.open_redis_browser(entry["id"], entry["schema"])
        elif entry["kind"] in {"index", "trigger"}:
            self.window.design_object(entry)
        else:
            self.window.open_table(dict(entry, table=entry["name"]))

    def design_selected(self):
        entry = self.selected()
        if entry:
            self.window.design_object(dict(entry, table=entry["name"]) if entry["kind"] not in {"index", "trigger"} else entry)

    def import_selected(self):
        self.open_selected()
        self.window.when_idle(self.window.import_rows)

    def export_selected(self):
        self.open_selected()
        self.window.when_idle(self.window.export_rows)

    def drop_selected(self):
        entry = self.selected()
        if entry:
            self.window.drop_object(entry)


class Inspector(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("inspector")
        self.setMinimumWidth(200)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        tabs = QWidget()
        row = QHBoxLayout(tabs)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addStretch()
        self.general = QToolButton()
        self.general.setIcon(icon("info", 18))
        self.general.setCheckable(True)
        self.general.setChecked(True)
        self.general.setToolTip("常规")
        row.addWidget(self.general)
        self.ddl_button = QToolButton()
        self.ddl_button.setText("DDL")
        self.ddl_button.setCheckable(True)
        self.ddl_button.setToolTip("DDL")
        row.addWidget(self.ddl_button)
        row.addStretch()
        tabs.setFixedHeight(30)
        layout.addWidget(tabs)
        self.stack = QStackedWidget()
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(22, 30, 18, 10)
        heading = QHBoxLayout()
        self.symbol = QLabel()
        self.symbol.setFixedSize(48, 48)
        heading.addWidget(self.symbol)
        labels = QVBoxLayout()
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight:600;font-size:14px;")
        labels.addWidget(self.title)
        self.kind = QLabel()
        labels.addWidget(self.kind)
        heading.addLayout(labels)
        box.addLayout(heading)
        box.addSpacing(22)
        self.connection = QLabel()
        self.connection.setWordWrap(True)
        self.schema = QLabel()
        box.addWidget(self.connection)
        box.addWidget(self.schema)
        box.addSpacing(22)
        self.details = QLabel()
        self.details.setWordWrap(True)
        box.addWidget(self.details)
        box.addStretch()
        self.stack.addWidget(body)
        self.ddl = QPlainTextEdit()
        self.ddl.setReadOnly(True)
        self.stack.addWidget(self.ddl)
        layout.addWidget(self.stack)
        self.buttons = QButtonGroup(self)
        self.buttons.setExclusive(True)
        self.buttons.addButton(self.general, 0)
        self.buttons.addButton(self.ddl_button, 1)
        self.buttons.idClicked.connect(self.stack.setCurrentIndex)

    def show_object(self, info, config=None, ddl=""):
        kind = info.get("kind", "query")
        self.symbol.setPixmap(icon(object_symbol(kind), 48).pixmap(48, 48))
        self.title.setText(info.get("name", info.get("table", "无标题")))
        self.kind.setText(OBJECT_LABELS.get(kind, kind))
        self.connection.setText(config.name if config else "")
        self.schema.setText(info.get("schema", ""))
        details = []
        if "rootpage" in info:
            details.extend(["根页面", str(info["rootpage"]), "", "有索引", "是" if info.get("indexed") else "否", "", "有触发器", "是" if info.get("triggers") else "否"])
        self.details.setText("\n".join(details))
        self.ddl.setPlainText(ddl)
