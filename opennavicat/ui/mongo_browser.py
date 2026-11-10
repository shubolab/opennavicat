"""BSON collection workspace with exact document writes through a serial owner.

Grid is the observed default; tree and page JSON are read-only presentations.
Document dialogs explicitly use Canonical Extended JSON. This does not claim
compatibility with the reference application's full JavaScript shell editor.
"""
from __future__ import annotations

from collections.abc import Mapping
from bisect import bisect_right
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import os
import tempfile

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QFileDialog,
    QApplication, QHeaderView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy, QSpinBox,
    QStackedWidget, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..mongodb_session import BSON_CODEC, MongoDocument, MongoPage, canonical_dumps, canonical_loads
from .editor import SqlEditor
from .grid import DataGrid
from .icons import tool_icon


_MISSING = object()


def _write_export(path, payload):
    """Replace a selected export only after all bytes reached its own volume."""
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".opennavicat-export-", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bson_type(value):
    if value is _MISSING:return "Missing"
    if value is None:return "Null"
    if isinstance(value, bool):return "Boolean"
    if isinstance(value, Int64):return "Int64"
    if isinstance(value, int):return "Int32"
    if isinstance(value, float):return "Double"
    if isinstance(value, Decimal128):return "Decimal128"
    if isinstance(value, ObjectId):return "ObjectId"
    if isinstance(value, (Binary, bytes)):return "Binary"
    if isinstance(value, (datetime, DatetimeMS)):return "Date"
    if isinstance(value, Timestamp):return "Timestamp"
    if isinstance(value, Regex):return "Regular expression"
    if isinstance(value, Code):return "Code with scope" if value.scope is not None else "Code"
    if isinstance(value, Mapping):return "Document"
    if isinstance(value, list):return "Array"
    if isinstance(value, str):return "String"
    return type(value).__name__


def document_key(document):
    """UI identity retains BSON types; Python equates int, Int64 and bool."""
    if "_id" in document.document:
        try:return b"id:"+BSON.encode({"_id":document.document["_id"]},codec_options=BSON_CODEC)
        except Exception:pass
    return b"raw:"+document.raw_bson


def display_bson(value):
    if value is _MISSING:return "（缺失）"
    if value is None:return "null"
    if isinstance(value, bool):return "true" if value else "false"
    if isinstance(value, Mapping):return f"(Document) {len(value)} Fields"
    if isinstance(value, list):return f"(Array) {len(value)} Items"
    if isinstance(value, (Binary, bytes)):return f"BLOB {len(value)} bytes"
    if isinstance(value, datetime):return value.isoformat()
    if isinstance(value, DatetimeMS):return f"Date({int(value)} ms)"
    if isinstance(value, Regex):return str(value.pattern)
    return str(value)


class MongoDocumentModel(QAbstractTableModel):
    """Read-only field grid; raw BSON and CAS tokens never enter QVariant cells."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.documents, self.columns = [], []
        self.type_colors = False

    def set_documents(self, documents):
        self.beginResetModel()
        self.documents = list(documents)
        self.columns = list(dict.fromkeys(key for document in self.documents for key in document.document))
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):return 0 if parent.isValid() else len(self.documents)
    def columnCount(self, parent=QModelIndex()):return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        value = self.documents[index.row()].document.get(self.columns[index.column()], _MISSING)
        if role == Qt.ItemDataRole.DisplayRole:return display_bson(value)
        if role == Qt.ItemDataRole.ToolTipRole:
            if value is _MISSING:return "字段不存在；它与 BSON null 不同。"
            try:text = canonical_dumps({"value": value})
            except Exception:text = "此 BSON 值无法转换为 Extended JSON。"
            return bson_type(value) + "\n" + text[:8000]
        if role == Qt.ItemDataRole.ForegroundRole:
            palette = self.parent().palette() if self.parent() is not None else QApplication.palette()
            if value is None or value is _MISSING:return palette.color(QPalette.ColorRole.PlaceholderText)
            if self.type_colors and not isinstance(value, str):return palette.color(QPalette.ColorRole.Link)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section] if orientation == Qt.Orientation.Horizontal else str(section+1)

    def flags(self, index):
        return super().flags(index) & ~Qt.ItemFlag.ItemIsEditable


class MongoDocumentDialog(QDialog):
    writeRequested = Signal(object)
    modifiedChanged = Signal()

    def __init__(self, document=None, parent=None):
        super().__init__(parent)
        self.is_new = document is None
        self.busy = False
        self.setWindowTitle(("添加文档" if self.is_new else "编辑文档") + " · Extended JSON")
        self.resize(740, 570)
        layout = QVBoxLayout(self)
        self.editor = SqlEditor(); self.editor.set_language("text")
        self.initial_text = canonical_dumps({} if document is None else document)
        self.editor.setPlainText(self.initial_text)
        self.editor.document().setModified(False)
        self.editor.textChanged.connect(self.modifiedChanged)
        layout.addWidget(self.editor, 1)
        self.error = QLabel(); self.error.setTextFormat(Qt.TextFormat.PlainText); self.error.setWordWrap(True)
        layout.addWidget(self.error)
        controls = QHBoxLayout()
        self.validate_button = QPushButton("验证")
        self.validate_button.clicked.connect(self.validate_document)
        controls.addWidget(self.validate_button); controls.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("添加" if self.is_new else "保存")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        controls.addWidget(self.buttons); layout.addLayout(controls)

    @property
    def modified(self):return self.editor.toPlainText() != self.initial_text

    def validate_document(self):
        try:
            document = canonical_loads(self.editor.toPlainText())
        except Exception as error:
            self.error.setText(str(error)); return None
        self.error.setText("文档有效。")
        return document

    def accept(self):
        if self.busy:return False
        document = self.validate_document()
        if document is None:return False
        self.writeRequested.emit(document)
        return True

    def set_busy(self, value):
        self.busy = bool(value)
        self.editor.setReadOnly(value); self.buttons.setEnabled(not value); self.validate_button.setEnabled(not value)

    def show_error(self, error):
        self.set_busy(False); self.error.setText(str(error))

    def reject(self):
        if not self.busy:super().reject()


class MongoCollectionPage(QWidget):
    dirtyChanged = Signal(bool)
    titleChanged = Signal(str)
    focusRequested = Signal(bool)

    def __init__(self, window, connection_id, database, collection, *, kind="collection"):
        super().__init__()
        self.window, self.connection_id = window, connection_id
        self.database = self.schema = database
        self.collection = self.table = collection
        self.kind = kind
        self.loading = self.running = self._closed = False
        self.page = None
        self.offset, self._request, self._selected_row = 0, 0, 0
        self._dialog = self._after_save = None
        self._last_dirty = False
        self._filter, self._sort, self._projection = {}, None, None
        self._json_pending = True
        self._json_offsets = []
        self._setting_json = False
        self._setting_selection = False
        self._tree_document_key = None
        self._pending_row = None
        self._page_history = []
        self._history_identity = None
        self._tree_values = {}
        self.destroyed.connect(lambda: setattr(self, "_closed", True))
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.toolbar = QToolBar(); self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setFixedHeight(36); self.toolbar.setIconSize(QSize(18,18)); layout.addWidget(self.toolbar)
        self.profile_action = self._action("集合配置文件（尚未实现）", "query", enabled=False)
        self.edit_action = self._action("单元格编辑器 · Extended JSON", "design", self.edit_document)
        self.filter_action = self._action("筛选和排序", "filter")
        self.filter_action.setCheckable(True)
        self.columns_action = self._action("列", "table", self.choose_columns)
        self._action("历史（尚未实现）", "history", enabled=False)
        self.collapse_action = self._action("全部折叠", "list", lambda:self.tree.collapseAll())
        self.color_action = self._action("类型颜色", "format")
        self.color_action.setCheckable(True); self.color_action.toggled.connect(self._set_type_colors)
        self._action("分析（尚未实现）", "chart", enabled=False)
        self.toolbar.addSeparator()
        self._action("导入文档（尚未实现）", "import", enabled=False)
        self.export_action = self._action("导出选中文档", "export", self.export_document)
        self._action("生成数据（尚未实现）", "database", enabled=False)
        self._action("数据脱敏（尚未实现）", "user", enabled=False)
        stretch = QWidget(); stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(stretch)
        self.focus_action = self._action("专注模式", "design")
        self.focus_action.setCheckable(True); self.focus_action.toggled.connect(self.focusRequested)
        self.filter_bar = QWidget(); filters = QHBoxLayout(self.filter_bar); filters.setContentsMargins(8,3,8,3)
        self.filter_edit, self.sort_edit, self.projection_edit = QLineEdit("{}"), QLineEdit("{}"), QLineEdit("{}")
        for label, edit in (("筛选 · Extended JSON",self.filter_edit),("排序",self.sort_edit),("字段投影",self.projection_edit)):
            filters.addWidget(QLabel(label)); filters.addWidget(edit,1); edit.returnPressed.connect(self.apply_filter)
        apply = QPushButton("应用"); apply.clicked.connect(self.apply_filter); filters.addWidget(apply)
        self.filter_bar.hide(); self.filter_action.toggled.connect(self.filter_bar.setVisible); layout.addWidget(self.filter_bar)
        self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True)
        self.message.hide(); layout.addWidget(self.message)
        self.views = QStackedWidget()
        self.grid = DataGrid(row_selectors=False); self.model = MongoDocumentModel(self.grid); self.grid.setModel(self.model)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.doubleClicked.connect(lambda _index:self.edit_document())
        self.grid.selectionModel().currentRowChanged.connect(self._grid_selected)
        self.grid.horizontalHeader().sectionClicked.connect(self.sort_column)
        self.views.addWidget(self.grid)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["名称","值","类型"])
        self.tree.setHeaderHidden(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0,160); self.tree.setColumnWidth(2,140)
        self.tree.setToolTip("当前文档的字段、值与 BSON 类型。双击字段使用 Extended JSON 编辑当前文档。")
        self.tree.itemExpanded.connect(self._expand_value)
        self.tree.currentItemChanged.connect(self._tree_selected)
        self.tree.itemDoubleClicked.connect(lambda *_args:self.edit_document())
        self.views.addWidget(self.tree)
        self.json_view = SqlEditor(); self.json_view.set_language("text"); self.json_view.setReadOnly(True)
        self.json_view.setToolTip("当前页的 Canonical Extended JSON，只读。通过网格或树选择文档后打开文档编辑器。")
        self.json_view.cursorPositionChanged.connect(self._json_selected)
        self.views.addWidget(self.json_view); layout.addWidget(self.views,1)
        footer = QHBoxLayout(); footer.setContentsMargins(6,1,6,1); footer.setSpacing(3)
        def button(tooltip,symbol,slot):
            item=QToolButton(); item.setIcon(tool_icon(symbol,16)); item.setIconSize(QSize(16,16))
            item.setAutoRaise(True); item.setToolTip(tooltip); item.clicked.connect(slot); footer.addWidget(item)
            return item
        self.delete_button=button("删除选中文档","record_delete",self.delete_document)
        self.refresh_button=button("刷新","refresh",self.refresh)
        self.add_button=button("添加文档 · Extended JSON","record_add",self.add_document)
        self.query_label=QLabel(); self.query_label.setTextFormat(Qt.TextFormat.PlainText); self.query_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.query_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        footer.addWidget(self.query_label,1)
        self.first_button=button("第一页","first",lambda:self.navigate("first"))
        self.previous_button=button("上一页","previous",lambda:self.navigate("previous"))
        self.next_button=button("下一页","next",lambda:self.navigate("next"))
        self.last_button=button("最后一页","last",lambda:self.navigate("last"))
        self.record_position=QLabel()
        self.record_position.setToolTip("位置基于当前返回顺序；并发变更可能改变分页。")
        footer.addWidget(self.record_position)
        self.page_number=QSpinBox(); self.page_number.setMinimum(1); self.page_number.setMaximum(1)
        self.page_number.setFixedWidth(55); self.page_number.editingFinished.connect(self.choose_page)
        self.page_number.setToolTip("当前访问链的页码，按实际返回边界导航；刷新后重新计数。")
        footer.addWidget(self.page_number)
        self.page_size=QSpinBox(self); self.page_size.setRange(1,1000); self.page_size.setValue(1000)
        self.page_size.setFixedWidth(70); self.page_size.setToolTip("每页文档数")
        self.page_size.editingFinished.connect(lambda:self.go_page(0)); self.page_size.hide()
        self.settings_button=button("设置","other",self.page_settings)
        self.view_buttons=QButtonGroup(self); self.view_buttons.setExclusive(True)
        for index,label in enumerate(("网格","树","Extended JSON")):
            item=QToolButton(); item.setText(label); item.setCheckable(True); item.setChecked(index==0)
            item.clicked.connect(lambda _checked=False,index=index:self.set_view(index))
            self.view_buttons.addButton(item,index); footer.addWidget(item)
        layout.addLayout(footer)
        self.status=QLabel("正在读取…"); layout.addWidget(self.status)
        self._update_controls()
        QTimer.singleShot(0,self,lambda:self.refresh(check=False))

    def _action(self,title,symbol,slot=None,enabled=True):
        action=QAction(tool_icon(symbol,18),title,self); action.setEnabled(enabled)
        if slot:action.triggered.connect(slot)
        self.toolbar.addAction(action); return action

    @property
    def title(self):return f"{self.collection} @ {self.database}"+(" — 已编辑" if self.dirty else "")
    @property
    def dirty(self):return bool(self._dialog and self._dialog.modified)
    @property
    def read_only(self):
        session=self.window.sessions.get(self.connection_id)
        return session is None or bool(session.config.read_only) or self.kind == "view"

    def current_result(self):
        # Generic SQL CSV/JSON exports are not a lossless BSON serialization.
        return None

    def selected_document(self):
        if self.page and 0<=self._selected_row<len(self.page.documents):return self.page.documents[self._selected_row]
        return None

    def _grid_selected(self,current,_previous):
        if self._setting_selection or not current.isValid():return
        target=current.row()
        if target!=self._selected_row:
            self._restore_grid_selection()
            self.select_record(target)
        else:self._update_controls()

    def _restore_grid_selection(self):
        self._setting_selection=True
        try:
            if self.model.columns and self.page and self.page.documents:
                self.grid.setCurrentIndex(self.model.index(self._selected_row,0))
        finally:self._setting_selection=False

    def _tree_selected(self,current,_previous):
        # The tree only contains the current document, never a list of records.
        self._update_controls()

    def _json_selected(self):
        if self._setting_json or self.views.currentWidget() is not self.json_view or not self._json_offsets:return
        target=max(0,bisect_right(self._json_offsets,self.json_view.textCursor().position())-1)
        if target!=self._selected_row and not self.select_record(target):self._restore_json_selection()

    def _restore_json_selection(self):
        if self._selected_row<len(self._json_offsets):
            self._setting_json=True
            try:
                cursor=self.json_view.textCursor();cursor.setPosition(self._json_offsets[self._selected_row]);self.json_view.setTextCursor(cursor)
            finally:self._setting_json=False

    def select_record(self,row,*,check=True):
        if self.loading or self._closed or not self.page or not 0<=row<len(self.page.documents):return False
        if row!=self._selected_row and check and not self.can_discard(lambda:self.select_record(row)):return False
        self._selected_row=row
        self._restore_grid_selection()
        self._render_tree()
        if self.views.currentWidget() is self.json_view:self._restore_json_selection()
        self._update_controls()
        return True

    def _render_tree(self):
        document=self.selected_document()
        key=(document_key(document),document.raw_bson) if document else None
        if key==self._tree_document_key:return
        self._tree_document_key=key
        self.tree.clear();self._tree_values.clear()
        if document is not None:
            for name,value in document.document.items():self._new_tree_item(None,name,value,self._selected_row)
            if self.tree.topLevelItemCount():self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _new_tree_item(self,parent,label,value,row):
        item=QTreeWidgetItem([str(label),display_bson(value),bson_type(value)])
        token=len(self._tree_values)+1; self._tree_values[token]=(row,value)
        item.setData(0,Qt.ItemDataRole.UserRole,token)
        if isinstance(value,(Mapping,list)) and value:item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        if parent is None:self.tree.addTopLevelItem(item)
        else:parent.addChild(item)
        return item

    def _expand_value(self,item):
        if item.childCount():return
        row,value=self._tree_values[item.data(0,Qt.ItemDataRole.UserRole)]
        pairs=value.items() if isinstance(value,Mapping) else enumerate(value) if isinstance(value,list) else []
        for label,child in pairs:self._new_tree_item(item,label,child,row)

    def set_view(self,view_index):
        selected_row=self._selected_row
        if view_index==2 and self._json_pending and self.page:
            rows = []; self._json_offsets=[]; offset=0
            for row_index, document in enumerate(self.page.documents):
                try:
                    text = canonical_dumps(document.document)
                    if not document.editable:
                        try:
                            lossless = BSON.encode(canonical_loads(text)) == document.raw_bson
                        except Exception:
                            lossless = False
                        if not lossless:
                            text = "// 此显示无法保留全部原始 BSON 类型；请使用原始 BSON 导出。\n" + text
                except Exception:text = "// 无法无损转换；请导出原始 BSON 文档。"
                block=f"// {self.offset+row_index+1}\n" + text
                self._json_offsets.append(offset);offset+=len(block)+2
                rows.append(block)
            self._setting_json=True
            self.json_view.setPlainText("\n\n".join(rows))
            self._setting_json=False
            self._json_pending=False
        self.views.setCurrentIndex(view_index)
        self._selected_row=selected_row
        self.view_buttons.button(view_index).setChecked(True)
        if self.page and self.page.documents:
            if view_index==0 and self.model.columns:self.grid.setCurrentIndex(self.model.index(self._selected_row,0))
            elif view_index==1:
                self._render_tree()
            elif view_index==2 and self._selected_row<len(self._json_offsets):
                self._restore_json_selection()
        self._update_controls()

    def _set_type_colors(self,value):
        self.model.type_colors=bool(value)
        if self.model.rowCount() and self.model.columnCount():
            self.model.dataChanged.emit(self.model.index(0,0),self.model.index(self.model.rowCount()-1,self.model.columnCount()-1))

    def show_error(self,error):
        self.message.setText(str(error)); self.message.show()

    def _dirty_changed(self):
        dirty=self.dirty
        if dirty!=self._last_dirty:
            self._last_dirty=dirty; self.dirtyChanged.emit(dirty); self.titleChanged.emit(self.title)
        self._update_controls()

    def _submit(self,function,done,label,failed=None):
        if self.loading or self._closed:return False
        session=self.window.sessions.get(self.connection_id)
        if session is None:self.show_error("连接已断开。");return False
        self._request+=1; request=self._request; binding=(self.database,self.collection)
        self.loading=self.running=True; self._update_controls()
        def alive():return isValid(self) and not self._closed and request==self._request
        def current():return alive() and self.window.sessions.get(self.connection_id) is session and binding==(self.database,self.collection)
        def stale():
            if alive():
                self.loading=self.running=False; self._after_save=None
                self._pending_row=None
                if self.page:self.offset=self.page.offset
                if self._dialog:self._dialog.set_busy(False)
                self.show_error("连接已改变，请刷新后继续。"); self._update_controls()
        def success(value):
            if current():self.loading=self.running=False;done(value);self._update_controls()
            else:stale()
        def failure(error):
            if current():
                self.loading=self.running=False;self._after_save=None;self.show_error(error)
                self._pending_row=None
                if self.page:self.offset=self.page.offset
                if failed:failed(error)
                self._update_controls()
            else:stale()
        self.window.submit(lambda:function(session),success,failure,label=label)
        return True

    def _idle(self,callback):
        self.loading=self.running=True;self._update_controls()
        def resume():
            if isValid(self) and not self._closed:
                self.loading=self.running=False
                callback()
                if isValid(self) and not self._closed:self._update_controls()
        self.window.when_idle(resume)

    def load_page(self,page):
        selected=self.selected_document()
        previous_key=document_key(selected) if selected is not None else None
        self.message.clear();self.message.hide()
        self.page=page;self.offset=page.offset
        self._setting_selection=True
        self.model.set_documents(page.documents)
        if self._pending_row is not None:
            target=len(page.documents)-1 if self._pending_row<0 else self._pending_row
            self._pending_row=None
        else:target=next((row for row,record in enumerate(page.documents) if document_key(record)==previous_key),self._selected_row)
        self._selected_row=max(0,min(target,len(page.documents)-1))
        if page.documents and self.model.columns:self.grid.setCurrentIndex(self.model.index(self._selected_row,0))
        self._setting_selection=False
        self._tree_document_key=None
        self._render_tree()
        self._json_pending=True
        self.set_view(self.views.currentIndex())
        self.query_label.setText(page.query_text);self.query_label.setToolTip(page.query_text)
        count=f"共 {page.total:,} 个文档" if page.total is not None else "总数未知"
        readonly = sum(not document.editable for document in page.documents)
        suffix = " · 只读" if self.read_only else f" · {readonly} 个文档只读" if readonly else ""
        self.status.setText(f"本页 {len(page.documents):,} 个 · {count}"+(" · 还有下一页" if page.has_more else "")+suffix)
        self.status.setToolTip("\n".join(sorted({document.edit_reason for document in page.documents if document.edit_reason})))
        history=self._known_history()
        self.page_number.setMaximum(max(1,len(history)+int(page.has_more)))
        self.page_number.setValue(max(1,len(history)))
        self._update_controls()

    def _query_identity(self):
        # BSON bytes retain typed filter values and ordered sort terms. Session
        # identity prevents a reconnect under the same profile reusing offsets.
        query=BSON.encode({"filter":self._filter,"sort":self._sort,"projection":self._projection},codec_options=BSON_CODEC)
        return (self.window.sessions.get(self.connection_id),self.database,self.collection,self.page_size.value(),query)

    def _known_history(self):
        if self.page and self._history_identity==self._query_identity() and self._page_history and self._page_history[-1]==self.page.offset:
            return self._page_history
        return []

    def refresh(self,checked=False,*,check=True,_history=None):
        if self.loading or (check and not self.can_discard(lambda:self.refresh(check=False))):return
        database,collection,offset,limit=self.database,self.collection,self.offset,self.page_size.value()
        filter_doc,sort,projection=deepcopy(self._filter),deepcopy(self._sort),deepcopy(self._projection)
        identity=self._query_identity()
        history=list(_history) if _history is not None else [offset]
        def done(page):
            # Only successful reads enter history. Failed next/back requests
            # leave the last usable chain intact.
            self._page_history=history if page.offset==offset else [page.offset]
            self._history_identity=identity
            self.load_page(page)
        self._submit(lambda session:session.fetch_documents(database,collection,filter_doc=filter_doc,sort=sort,projection=projection,offset=offset,limit=limit),done,"读取 MongoDB 文档")

    def apply_filter(self):
        if self.loading or not self.can_discard(self.apply_filter):return
        try:
            filter_doc=canonical_loads(self.filter_edit.text())
            ordering=canonical_loads(self.sort_edit.text())
            projection=canonical_loads(self.projection_edit.text())
            if any(not isinstance(value,int) or isinstance(value,bool) or value not in (1,-1) for value in ordering.values()):raise ValueError("排序方向只能为 1 或 -1。")
        except Exception as error:self.show_error(error);return
        self._filter,self._sort,self._projection=filter_doc,list(ordering.items()) or None,projection or None
        self._pending_row=0;self.offset=0;self.refresh(check=False)

    def sort_column(self,column):
        if not 0<=column<len(self.model.columns):return
        field=self.model.columns[column]
        direction=-1 if self._sort==[(field,1)] else 1
        self.sort_edit.setText(canonical_dumps({field:direction}));self.apply_filter()

    def go_page(self,offset,*,selected_row=0):
        if self.loading or not self.can_discard(lambda:self.go_page(offset,selected_row=selected_row)):return
        offset=max(0,offset)
        history=self._known_history()
        if offset in history:history=history[:history.index(offset)+1]
        elif history and self.page.has_more and offset==self.page.next_offset:history=[*history,offset]
        else:history=[offset]
        self._pending_row=selected_row;self.offset=offset;self.refresh(check=False,_history=history)

    def choose_page(self):
        history=self._known_history();target=self.page_number.value()-1
        if 0<=target<len(history):self.go_page(history[target])
        elif history and target==len(history) and self.page.has_more:self.go_page(self.page.next_offset)

    def navigate(self,direction):
        if self.loading or not self.page:return
        tree=self.views.currentWidget() is self.tree
        history=self._known_history()
        if not tree:
            if direction=="first":self.go_page(0)
            elif direction=="previous" and len(history)>1:self.go_page(history[-2])
            elif direction=="next" and history and self.page.has_more:self.go_page(self.page.next_offset)
            elif direction=="last":self.last_page()
            return
        if direction=="first":
            if self.offset:self.go_page(0)
            else:self.select_record(0)
        elif direction=="previous":
            if self._selected_row:self.select_record(self._selected_row-1)
            elif len(history)>1:self.go_page(history[-2],selected_row=-1)
        elif direction=="next":
            if self._selected_row+1<len(self.page.documents):self.select_record(self._selected_row+1)
            elif history and self.page.has_more:self.go_page(self.page.next_offset)
        elif direction=="last" and history and self.page.total:
            last=self.page.total-1
            if self.offset<=last<self.offset+len(self.page.documents):self.select_record(last-self.offset)
            else:self.go_page(last)

    def page_settings(self):
        if self.loading or not self.can_discard(self.page_settings):return
        value,accepted=QInputDialog.getInt(self,"记录设置","每页文档数",self.page_size.value(),1,1000)
        if accepted:self.page_size.setValue(value);self.go_page(0)

    def last_page(self):
        # A known count does not reveal byte-budget page boundaries. Locate
        # the actual final record, rather than inventing a full last page.
        if self._known_history() and self.page.total:self.go_page(self.page.total-1)

    def choose_columns(self):
        menu=QMenu(self)
        for column,name in enumerate(self.model.columns):
            action=menu.addAction(name);action.setCheckable(True);action.setChecked(not self.grid.isColumnHidden(column))
            action.toggled.connect(lambda checked,column=column:self.grid.setColumnHidden(column,not checked))
        menu.exec(self.toolbar.mapToGlobal(self.toolbar.rect().bottomLeft()))

    def _open_editor(self,expected=None):
        if self.read_only or self.loading:return None
        if self._dialog:self._dialog.raise_();return self._dialog
        if expected is not None and not expected.editable:self.show_error(expected.edit_reason);return None
        dialog=self._dialog=MongoDocumentDialog(expected.document if expected else None,self)
        if expected is not None:dialog.setWindowTitle(f"编辑文档 {self.offset+self._selected_row+1} · Extended JSON")
        dialog.editor.textChanged.connect(self._dirty_changed)
        def finished():
            if self._dialog is dialog:self._dialog=None
            self._dirty_changed();dialog.deleteLater()
        dialog.rejected.connect(finished);dialog.accepted.connect(finished)
        def write(document):
            captured=deepcopy(document);database,collection=self.database,self.collection
            dialog.set_busy(True)
            def work(session):
                return session.replace_document(database,collection,captured,expected=expected) if expected else session.insert_document(database,collection,captured)
            def done(saved):
                records=list(self.page.documents) if self.page else []
                if expected is not None:
                    records=[saved if record.cas_token==expected.cas_token else record for record in records]
                elif self.page and len(records)<self.page.limit:records.append(saved)
                if self.page:self.load_page(replace(self.page,documents=records))
                QDialog.accept(dialog)
                retry,self._after_save=self._after_save,None
                self._idle(retry or (lambda:self.refresh(check=False)))
            if not self._submit(work,done,"保存 MongoDB 文档",dialog.show_error):dialog.set_busy(False)
        dialog.writeRequested.connect(write);dialog.open();self._update_controls()
        return dialog

    def add_document(self):return self._open_editor()
    def edit_document(self):
        document=self.selected_document()
        return self._open_editor(document) if document is not None else None

    def request_save(self):
        if self._dialog and not self._dialog.accept():self._after_save=None
    save_changes=request_save

    def discard_changes(self):
        if not self.loading and self._dialog:self._dialog.reject()

    def can_discard(self,retry=None):
        if self.loading:return False
        if not self._dialog:return True
        if not self.dirty:self._dialog.reject();return True
        answer=QMessageBox.question(self,"是否保存更改？","当前文档有尚未保存的更改。",
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Discard:self.discard_changes();return True
        if answer==QMessageBox.StandardButton.Save:
            self._after_save=retry;self.request_save()
        return False

    def delete_document(self):
        if self.read_only or self.loading or not self.can_discard(self.delete_document):return
        document=self.selected_document()
        if document is None:return
        if not document.editable:self.show_error(document.edit_reason);return
        if QMessageBox.question(self,"删除文档","确认永久删除选中的一个文档？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Yes:return
        database,collection=self.database,self.collection
        self._submit(lambda session:session.delete_document(database,collection,expected=document),lambda _value:self._idle(lambda:self.refresh(check=False)),"删除 MongoDB 文档")

    def export_document(self):
        document=self.selected_document()
        if document is None or self.loading:return
        filename,selected=QFileDialog.getSaveFileName(self,"导出选中文档","document.bson","BSON 原始文档 (*.bson);;Canonical Extended JSON (*.json)")
        if not filename:return
        if selected.startswith("Canonical"):
            try:
                text=canonical_dumps(document.document)
                if BSON.encode(canonical_loads(text))!=document.raw_bson:raise ValueError()
            except Exception:
                self.show_error("此文档不能无损转换为 Extended JSON；请导出原始 BSON。");return
            payload=text.encode("utf-8")
        else:payload=bytes(document.raw_bson)
        self._submit(lambda _session:_write_export(filename, payload),lambda _value:None,"导出 MongoDB 文档")

    def _update_controls(self):
        if not hasattr(self,"add_button"):return
        document=self.selected_document()
        writable=not self.loading and not self.read_only
        editable=writable and document is not None and document.editable
        self.add_button.setEnabled(writable and self._dialog is None)
        self.edit_action.setEnabled(editable);self.delete_button.setEnabled(editable)
        self.export_action.setEnabled(document is not None and not self.loading)
        self.refresh_button.setEnabled(not self.loading)
        tree=self.views.currentWidget() is self.tree
        json=self.views.currentWidget() is self.json_view
        self.edit_action.setVisible(not json);self.columns_action.setVisible(not tree and not json)
        self.collapse_action.setVisible(not json);self.color_action.setVisible(not tree and not json)
        self.collapse_action.setEnabled(tree)
        history=self._known_history()
        has_previous=len(history)>1 or (tree and self._selected_row>0)
        has_next=bool(self.page and ((bool(history) and self.page.has_more) or (tree and self._selected_row+1<len(self.page.documents))))
        self.first_button.setEnabled(not self.loading and (self.offset>0 or (tree and self._selected_row>0)))
        self.previous_button.setEnabled(not self.loading and has_previous)
        self.next_button.setEnabled(not self.loading and has_next)
        has_last=bool(history and self.page.total is not None and (has_next if tree else self.page.has_more))
        self.last_button.setEnabled(not self.loading and has_last)
        for control,name in ((self.first_button,"第一"),(self.previous_button,"上一"),(self.next_button,"下一"),(self.last_button,"最后一")):
            control.setToolTip(name+("条记录" if tree else "页"))
        if self.offset and len(history)<2 and not (tree and self._selected_row):
            self.previous_button.setToolTip("此前页边界未知；请从第一页重新导航。")
        if self.page:
            self.page_number.setMaximum(max(1,len(history)+int(bool(history) and self.page.has_more)))
            self.page_number.setValue(max(1,len(history)))
        if tree and self.page and self.page.total is None:self.last_button.setToolTip("文档总数未知，无法定位最后一条记录。")
        elif not tree and self.page and self.page.total is not None:
            self.last_button.setToolTip("定位最后一条记录（最后一页的实际边界未知）。")
        self.page_number.setVisible(not tree);self.record_position.setVisible(tree)
        self.record_position.setText(f"第 {self.offset+self._selected_row+1:,} 条记录" if document else "没有文档")
        self.settings_button.setEnabled(not self.loading)
        self.page_size.setEnabled(not self.loading);self.page_number.setEnabled(not self.loading)
        if document is not None and not document.editable:self.edit_action.setToolTip(document.edit_reason)
        else:self.edit_action.setToolTip("编辑选中文档 · Canonical Extended JSON")

    def closeEvent(self,event):
        if not self.can_discard(self.close):event.ignore();return
        self._closed=True;super().closeEvent(event)
