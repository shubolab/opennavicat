"""Editable table workspace; database reads and DDL run in the owner's queue.

The page owns a detached draft, never a live session.  Its request signals carry
copies, so a background planner cannot observe widgets or a changing draft.
"""
from __future__ import annotations

from copy import deepcopy
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QStyledItemDelegate, QTableView, QTableWidget,
    QTableWidgetItem, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from .editor import SqlEditor
from .icons import tool_icon, toggle_icon


CONFLICTS = ["", "ROLLBACK", "ABORT", "FAIL", "IGNORE", "REPLACE"]
TYPES = ["INTEGER", "TEXT", "REAL", "BLOB", "NUMERIC", "BOOLEAN", "DATE", "DATETIME", "VARCHAR", "DECIMAL"]
COLLATIONS = ["", "BINARY", "NOCASE", "RTRIM"]
REFERENTIAL_ACTIONS = ["NO ACTION", "RESTRICT", "SET NULL", "SET DEFAULT", "CASCADE"]


def _combo(values, value="", editable=False):
    result = QComboBox()
    result.setEditable(editable)
    result.addItems(values)
    if str(value) not in values and str(value):
        result.addItem(str(value))
    result.setCurrentText(str(value))
    return result


def _table(headers):
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    result.verticalHeader().setDefaultSectionSize(24)
    result.verticalHeader().hide()
    result.horizontalHeader().setDefaultSectionSize(130)
    result.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    result.horizontalHeader().setStretchLastSection(False)
    result.setAlternatingRowColors(True)
    return result


def _type_parts(value):
    match = re.fullmatch(r"\s*([^()]+?)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)\s*", value)
    return (match[1], match[2], match[3] or "") if match else (value, "", "")


class _ColumnModel(QAbstractTableModel):
    edited = Signal()
    HEADERS = ["名称", "类型", "大小", "比例", "不是 Null", "键"]

    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.read_only = False

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section] if orientation == Qt.Orientation.Horizontal else section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column, position = self.columns[index.row()], index.column()
        if position == 4 and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Unchecked if column.nullable else Qt.CheckState.Checked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            typ, size, scale = _type_parts(column.type)
            return [column.name, typ, size, scale, "", str(column.pk_order) if column.pk_order else ""][position]
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and not self.read_only:
            flags |= Qt.ItemFlag.ItemIsUserCheckable if index.column() == 4 else Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or self.read_only:
            return False
        column, position = self.columns[index.row()], index.column()
        if role == Qt.ItemDataRole.EditRole and str(value) == str(self.data(index, role)):
            return True
        if position == 4 and role == Qt.ItemDataRole.CheckStateRole:
            column.nullable = value not in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        elif role == Qt.ItemDataRole.EditRole:
            value = str(value)
            if position == 0:
                column.name = value
            elif position in (1, 2, 3):
                value = value.strip()
                typ, size, scale = _type_parts(column.type)
                if position == 1:
                    typ = value
                elif position == 2:
                    if value and not value.isdecimal():
                        return False
                    size = value
                    if not size:
                        scale = ""
                else:
                    if value and (not value.isdecimal() or not size):
                        return False
                    scale = value
                column.type = typ + (f"({size}{',' + scale if scale else ''})" if size else "")
            elif position == 5:
                value = value.strip()
                if value and (not value.isdecimal() or int(value) < 1):
                    return False
                column.pk_order = int(value) if value else 0
            else:
                return False
        else:
            return False
        self.dataChanged.emit(index, index)
        self.edited.emit()
        return True


class _TypeDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        return self._editor(parent)

    @staticmethod
    def _editor(parent):
        editor = _combo(TYPES, editable=True)
        editor.setParent(parent)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data(Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class IndexFieldsDialog(QDialog):
    """Ordered compound-index terms, including collation and sort direction."""

    def __init__(self, columns, terms=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("字段")
        self.resize(510, 300)
        self.column_names = list(columns)
        layout = QVBoxLayout(self)
        self.table = _table(["字段", "排序规则", "排序顺序"])
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("添加")
        self.remove_button = QPushButton("移除")
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch()
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("好")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.add_button.clicked.connect(lambda: self.add_term())
        self.remove_button.clicked.connect(self.remove_term)
        for term in terms or []:
            self.add_term(term)

    def add_term(self, term=None):
        term = term or {}
        row = self.table.rowCount()
        self.table.insertRow(row)
        field = _combo(self.column_names, term.get("column", ""), editable=True)
        collation = _combo(COLLATIONS, term.get("collation", ""), editable=True)
        order = _combo(["ASC", "DESC"], "DESC" if term.get("descending") else "ASC")
        for column, widget in enumerate((field, collation, order)):
            self.table.setCellWidget(row, column, widget)
        self.table.setCurrentCell(row, 0)

    def remove_term(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def terms(self):
        return [{"column": self.table.cellWidget(row, 0).currentText(),
                 "collation": self.table.cellWidget(row, 1).currentText(),
                 "descending": self.table.cellWidget(row, 2).currentText() == "DESC"}
                for row in range(self.table.rowCount())]

    def accept(self):
        if not self.terms() or any(not term["column"].strip() for term in self.terms()):
            QMessageBox.warning(self, "字段", "请至少选择一个字段，字段名称不能为空。")
            return
        super().accept()


class ForeignKeyFieldsDialog(QDialog):
    """Pairs remain structured identifiers rather than comma-split SQL text."""

    def __init__(self, columns, local=None, referenced=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("外键字段")
        self.resize(470, 290)
        self.column_names = list(columns)
        layout = QVBoxLayout(self)
        self.table = _table(["本地字段", "被引用的字段"])
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        add = QPushButton("添加")
        remove = QPushButton("移除")
        controls.addWidget(add)
        controls.addWidget(remove)
        controls.addStretch()
        layout.addLayout(controls)
        add.clicked.connect(lambda: self.add_pair())
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("好")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for left, right in zip(local or [], referenced or []):
            self.add_pair(left, right)

    def add_pair(self, local="", referenced=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setCellWidget(row, 0, _combo(self.column_names, local, editable=True))
        self.table.setCellWidget(row, 1, _combo([], referenced, editable=True))
        self.table.setCurrentCell(row, 0)

    def values(self):
        return ([self.table.cellWidget(row, 0).currentText() for row in range(self.table.rowCount())],
                [self.table.cellWidget(row, 1).currentText() for row in range(self.table.rowCount())])

    def accept(self):
        local, referenced = self.values()
        if not local or any(not value.strip() for value in local + referenced):
            QMessageBox.warning(self, "外键字段", "请为每一对字段填写本地名称和被引用的名称。")
            return
        super().accept()


class TableSaveDialog(QDialog):
    """The observed save sheet, with asynchronous, current-definition SQL."""

    previewRequested = Signal(object)

    def __init__(self, definition, is_new=False, parent=None):
        super().__init__(parent)
        self.definition = deepcopy(definition)
        self.plan = None
        self.setWindowTitle("保存表")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 440)
        layout = QVBoxLayout(self)
        self.name_edit = QLineEdit(self.definition.name)
        if is_new:
            form = QFormLayout()
            form.addRow("表名称", self.name_edit)
            layout.addLayout(form)
        else:
            self.name_edit.hide()
        layout.addWidget(QLabel("SQL 预览"))
        self.preview = SqlEditor()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        self.suppress_preview_check = QCheckBox("不再显示 SQL 预览")
        layout.addWidget(self.suppress_preview_check)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("保存")
        self.save_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name_edit.textChanged.connect(self._name_changed)

    def request_preview(self):
        self.plan = None
        self.save_button.setEnabled(False)
        if not self.definition.name.strip():
            self.preview.setPlainText("请输入表名称。")
            return
        self.preview.setPlainText("正在生成 SQL…")
        self.previewRequested.emit(deepcopy(self.definition))

    def _name_changed(self, value):
        self.definition.name = value
        self.request_preview()

    def show_plan(self, plan):
        if plan.definition != self.definition:
            return
        self.plan = plan
        self.preview.setPlainText(plan.sql or "无更改")
        self.save_button.setEnabled(bool(self.definition.name.strip() and plan.changed))

    def show_error(self, error):
        self.plan = None
        self.preview.setPlainText("无法安全保存：\n" + str(error))
        self.save_button.setEnabled(False)

    def accept(self):
        if self.plan is not None and self.plan.definition == self.definition and self.save_button.isEnabled():
            super().accept()


class TableDesignerPage(QWidget):
    """SQLite table editor embedded as a main-workspace tab.

    Owner contract: connect signals before calling ``load_snapshot``.  Perform
    ``schema.load_table/plan_table/apply_plan`` in the serial database queue and
    deliver results with ``load_snapshot/show_plan/save_succeeded/show_error``.
    A definition emitted by either request is a deepcopy of the current draft.
    """

    loadRequested = Signal()
    previewRequested = Signal(object)
    saveRequested = Signal(object)
    dirtyChanged = Signal(bool)
    titleChanged = Signal(str)
    focusRequested = Signal(bool)
    savePreviewPreferenceChanged = Signal(bool)

    def __init__(self, connection_id, schema="main", table="", parent=None):
        super().__init__(parent)
        self.connection_id, self.schema, self.table = connection_id, schema, table
        self.kind = "table_design"
        self.snapshot = None
        self.definition = None
        self.dirty = False
        self.loading = False
        self.running = False
        self._populating = False
        self._read_only = False
        self._connection_read_only = False
        self._plan = None
        self.confirmed_plan = None
        self._save_dialog = None
        self.suppress_save_preview = False
        self._revision = 0
        self.setObjectName("tableDesignerPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setFixedHeight(36)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.save_action = self._action("保存", "save", self.request_save)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.toolbar.addSeparator()
        self.add_action = self._action("添加字段", "add", lambda: self.add_record())
        self.insert_action = self._action("插入字段", "add", lambda: self.add_record(insert=True))
        self.delete_action = self._action("删除字段", "delete", self.delete_record)
        self.toolbar.addSeparator()
        self.pk_action = self._action("主键属性", "key", self.toggle_primary_key)
        self.toolbar.addSeparator()
        self.ai_action = self._action("询问 AI", "ai", None)
        self.ai_action.setEnabled(False)
        self.ai_action.setToolTip("此功能尚未实现。")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.focus_action = self._action("专注模式", "focus", None)
        self.focus_action.setIcon(toggle_icon("focus", 18))
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)
        layout.addWidget(self.toolbar)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("structureTabs")
        self.tabs.setDocumentMode(False)
        layout.addWidget(self.tabs, 1)
        self._build_fields()
        self.indexes = _table(["名称", "字段", "唯一键"])
        self.foreign_keys = _table(["名称", "被引用的表", "被引用的字段", "本地字段", "删除时", "更新时", "延迟"])
        self.unique_keys = _table(["名称", "字段", "ON CONFLICT"])
        self.tabs.addTab(self.indexes, "索引")
        self.tabs.addTab(self.foreign_keys, "外键")
        self.tabs.addTab(self.unique_keys, "唯一键")
        self._build_triggers()
        self._build_options()
        self._build_preview()
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setContentsMargins(8, 4, 8, 4)
        self.message.hide()
        layout.addWidget(self.message)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(220)
        self.preview_timer.timeout.connect(self.request_preview)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.indexes.itemChanged.connect(lambda item: self._record_item_changed("indexes", item))
        self.foreign_keys.itemChanged.connect(lambda item: self._record_item_changed("foreign_keys", item))
        self.unique_keys.itemChanged.connect(lambda item: self._record_item_changed("unique_keys", item))
        self.triggers.itemChanged.connect(lambda item: self._record_item_changed("triggers", item))
        self.triggers.itemSelectionChanged.connect(self._trigger_selected)
        self._update_actions()

    @property
    def title(self):
        name = self.definition.name if self.definition else self.table
        return (name or "无标题") + (" — 已编辑" if self.dirty else "")

    @property
    def revision(self):
        return self._revision

    @property
    def modified(self):
        return self.dirty

    def _action(self, text, symbol, callback):
        action = QAction(tool_icon(symbol, 18), text, self)
        if callback:
            action.triggered.connect(callback)
        self.toolbar.addAction(action)
        self.addAction(action)
        return action

    def _build_fields(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.field_splitter = QSplitter(Qt.Orientation.Vertical)
        self.fields = QTableView()
        self.fields.setAlternatingRowColors(True)
        self.fields.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fields.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.fields.verticalHeader().setDefaultSectionSize(24)
        self.fields.verticalHeader().hide()
        self.fields.horizontalHeader().setDefaultSectionSize(130)
        self.fields.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.fields.horizontalHeader().setStretchLastSection(False)
        self.fields.setItemDelegateForColumn(1, _TypeDelegate(self.fields))
        self.field_splitter.addWidget(self.fields)
        self.field_properties = QWidget()
        property_row = QHBoxLayout(self.field_properties)
        property_row.setContentsMargins(0, 0, 0, 0)
        property_row.addStretch()
        property_box = QWidget()
        property_box.setMaximumWidth(580)
        form = QFormLayout(property_box)
        form.setContentsMargins(12, 10, 12, 10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        property_row.addWidget(property_box)
        property_row.addStretch()
        self.default_edit = _combo(["", "NULL", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"], editable=True)
        self.default_edit.setToolTip("SQL 表达式；字符串常量需要单引号。空白表示没有 DEFAULT 子句。")
        self.collation_combo = _combo(COLLATIONS, editable=True)
        self.notnull_conflict_combo = _combo(CONFLICTS)
        self.generated_type_combo = _combo(["", "VIRTUAL", "STORED"])
        self.generated_expression_edit = QLineEdit()
        for label, widget in [("默认值", self.default_edit), ("排序规则", self.collation_combo),
                              ("不是 Null ON CONFLICT", self.notnull_conflict_combo),
                              ("生成类型", self.generated_type_combo), ("生成值", self.generated_expression_edit)]:
            widget.setFixedWidth(250)
            form.addRow(label, widget)
        self.field_splitter.addWidget(self.field_properties)
        self.field_splitter.setStretchFactor(0, 1)
        self.field_splitter.setStretchFactor(1, 0)
        self.field_properties.hide()
        layout.addWidget(self.field_splitter)
        self.tabs.addTab(page, "字段")
        self.default_edit.currentTextChanged.connect(lambda text: self._field_property("default", text or None))
        self.collation_combo.currentTextChanged.connect(lambda text: self._field_property("collation", text))
        self.notnull_conflict_combo.currentTextChanged.connect(lambda text: self._field_property("notnull_conflict", text))
        self.generated_type_combo.currentTextChanged.connect(lambda text: self._field_property("generated_type", text))
        self.generated_expression_edit.textChanged.connect(lambda text: self._field_property("generated_expression", text))

    def _build_triggers(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.trigger_splitter = QSplitter(Qt.Orientation.Vertical)
        self.triggers = _table(["名称", "触发时机", "insert", "update", "delete"])
        self.trigger_splitter.addWidget(self.triggers)
        self.trigger_properties = QTabWidget()
        self.trigger_body = SqlEditor()
        self.trigger_properties.addTab(self.trigger_body, "定义")
        advanced = QWidget()
        form = QFormLayout(advanced)
        self.trigger_when = QLineEdit()
        self.trigger_update_columns = QLineEdit()
        self.trigger_update_columns.setReadOnly(True)
        self.trigger_update_columns.setToolTip("已有 UPDATE OF 字段原样保留；字段选择编辑器仍待实现。")
        form.addRow("WHEN", self.trigger_when)
        form.addRow("UPDATE OF", self.trigger_update_columns)
        self.trigger_properties.addTab(advanced, "高级")
        self.trigger_splitter.addWidget(self.trigger_properties)
        self.trigger_properties.hide()
        layout.addWidget(self.trigger_splitter)
        self.tabs.addTab(page, "触发器")
        self.trigger_body.textChanged.connect(self._trigger_body_changed)
        self.trigger_when.textChanged.connect(self._trigger_when_changed)

    def _build_options(self):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(24, 24, 24, 24)
        self.pk_conflict_combo = _combo(CONFLICTS)
        self.autoincrement_seed = QLineEdit("-1")
        self.autoincrement_seed.setEnabled(False)
        self.autoincrement_seed.setToolTip("本轮不修改 sqlite_sequence；现有自动递增值将保留。")
        self.without_rowid_check = QCheckBox("没有 RowID")
        form.addRow("主键 ON CONFLICT", self.pk_conflict_combo)
        form.addRow("自动递增", self.autoincrement_seed)
        form.addRow("", self.without_rowid_check)
        self.tabs.addTab(page, "选项")
        self.pk_conflict_combo.currentTextChanged.connect(lambda text: self._option_changed("pk_conflict", text))
        self.without_rowid_check.toggled.connect(lambda value: self._option_changed("without_rowid", value))

    def _build_preview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 4)
        self.preview_mode = _combo(["更改", "DDL"])
        self.preview_mode.setFixedWidth(120)
        controls.addStretch()
        controls.addWidget(self.preview_mode)
        controls.addStretch()
        layout.addLayout(controls)
        self.sql_preview = SqlEditor()
        self.sql_preview.setReadOnly(True)
        self.sql_preview.setPlainText("无更改")
        layout.addWidget(self.sql_preview, 1)
        self.tabs.addTab(page, "SQL 预览")
        self.preview_mode.currentIndexChanged.connect(self._render_plan)

    def load_snapshot(self, snapshot):
        self.preview_timer.stop()
        self.snapshot = deepcopy(snapshot)
        self.definition = deepcopy(snapshot.definition)
        if not snapshot.exists and not self.definition.columns:
            from opennavicat.schema import ColumnDefinition
            self.definition.columns.append(ColumnDefinition(""))
        self.schema, self.table = snapshot.schema, snapshot.table
        self._read_only = self._connection_read_only or bool(snapshot.issues)
        self._plan = None
        self._populating = True
        try:
            old_model = self.fields.model()
            self.field_model = _ColumnModel(self.definition.columns, self)
            self.field_model.read_only = self._read_only
            self.field_model.edited.connect(self._edited)
            self.fields.setModel(self.field_model)
            if old_model is not None:
                old_model.deleteLater()
            self.fields.selectionModel().currentRowChanged.connect(lambda *_: self._field_selected())
            for index, width in enumerate((110, 105, 36, 36, 60, 60)):
                self.fields.setColumnWidth(index, width)
            self._populate_records()
            self.pk_conflict_combo.setCurrentText(self.definition.options.pk_conflict)
            self.pk_conflict_combo.setEnabled(not self._read_only)
            seed = self.definition.options.autoincrement_seed
            self.autoincrement_seed.setText(str(seed if seed is not None else -1))
            self.without_rowid_check.setChecked(self.definition.options.without_rowid)
            self.without_rowid_check.setEnabled(not self._read_only)
            self._field_selected()
            self._trigger_selected()
        finally:
            self._populating = False
        self._set_dirty(not snapshot.exists)
        self.set_busy(False)
        self.show_message("只读：" + "；".join(snapshot.issues) if snapshot.issues else "此连接为只读。" if self._connection_read_only else "")
        self._render_plan()
        self.titleChanged.emit(self.title)
        if not snapshot.exists and self.definition.columns:
            self.fields.setCurrentIndex(self.field_model.index(0, 0))

    def _populate_records(self):
        for name in ("indexes", "foreign_keys", "unique_keys", "triggers"):
            table = getattr(self, name)
            table.setRowCount(0)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers if self._read_only else QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
            for record in getattr(self.definition, name):
                row = table.rowCount()
                table.insertRow(row)
                self._populate_record(name, row, record)

    def _item(self, table, row, column, value, *, checked=None, editable=True):
        item = QTableWidgetItem(str(value))
        flags = item.flags()
        if checked is not None:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            flags &= ~Qt.ItemFlag.ItemIsEditable
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        if not editable or self._read_only:
            flags &= ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setFlags(flags)
        table.setItem(row, column, item)
        return item

    @staticmethod
    def _index_is_complex(record):
        return bool(getattr(record, "where", "") or any(getattr(term, "expression", "") for term in getattr(record, "terms", [])))

    @staticmethod
    def _terms(record):
        terms = getattr(record, "terms", [])
        if terms:
            return [{"column": term.column, "collation": term.collation, "descending": term.descending} for term in terms]
        return [{"column": column, "collation": "", "descending": False} for column in record.columns]

    def _term_summary(self, record):
        if self._index_is_complex(record):
            return record.raw_sql or "表达式 / 部分索引（原样保留）"
        return ", ".join(term["column"] + (" COLLATE " + term["collation"] if term["collation"] else "") + (" DESC" if term["descending"] else " ASC") for term in self._terms(record))

    def _fields_cell(self, table, row, record, callback):
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)
        text = QLineEdit(self._term_summary(record))
        text.setReadOnly(True)
        text.setFrame(False)
        text.setToolTip(text.text())
        button = QToolButton()
        button.setText("…")
        button.setFixedWidth(24)
        button.setToolTip("选择字段、排序规则和排序顺序")
        complex_index = self._index_is_complex(record)
        button.setEnabled(not self._read_only and not complex_index)
        if complex_index:
            text.setToolTip("表达式或部分索引原样保留；当前字段编辑器不支持修改此索引。\n" + text.text())
        button.clicked.connect(callback)
        layout.addWidget(text, 1)
        layout.addWidget(button)
        table.setCellWidget(row, 1, holder)

    def _populate_record(self, name, row, record):
        table = getattr(self, name)
        complex_index = name == "indexes" and self._index_is_complex(record)
        self._item(table, row, 0, record.name, editable=not complex_index)
        if name in {"indexes", "unique_keys"}:
            self._fields_cell(table, row, record, lambda checked=False, rec=record, n=name: self.edit_index_fields(n, rec))
            if name == "indexes":
                self._item(table, row, 2, "", checked=record.unique, editable=not complex_index)
            else:
                combo = _combo(CONFLICTS, record.on_conflict)
                combo.setEnabled(not self._read_only)
                combo.currentTextChanged.connect(lambda value, rec=record: self._record_property(rec, "on_conflict", value))
                table.setCellWidget(row, 2, combo)
        elif name == "foreign_keys":
            self._item(table, row, 1, record.referenced_table)
            for column, values in ((2, record.referenced_columns), (3, record.columns)):
                holder = QWidget()
                layout = QHBoxLayout(holder)
                layout.setContentsMargins(2, 0, 2, 0)
                text = QLineEdit(", ".join(values))
                text.setReadOnly(True)
                text.setFrame(False)
                button = QToolButton()
                button.setText("…")
                button.setFixedWidth(24)
                button.setEnabled(not self._read_only)
                button.clicked.connect(lambda checked=False, rec=record: self.edit_foreign_key_fields(rec))
                layout.addWidget(text, 1)
                layout.addWidget(button)
                table.setCellWidget(row, column, holder)
            for column, attr in ((4, "on_delete"), (5, "on_update")):
                combo = _combo(REFERENTIAL_ACTIONS, getattr(record, attr))
                combo.setEnabled(not self._read_only)
                combo.currentTextChanged.connect(lambda value, rec=record, a=attr: self._record_property(rec, a, value))
                table.setCellWidget(row, column, combo)
            self._item(table, row, 6, "", checked=record.initially_deferred)
        elif name == "triggers":
            combo = _combo(["BEFORE", "AFTER", "INSTEAD OF"], record.timing)
            combo.setEnabled(not self._read_only)
            combo.currentTextChanged.connect(lambda value, rec=record: self._record_property(rec, "timing", value))
            table.setCellWidget(row, 1, combo)
            for column, event in enumerate(("INSERT", "UPDATE", "DELETE"), 2):
                self._item(table, row, column, "", checked=record.event == event)

    def edit_index_fields(self, name, record):
        if self._read_only or self._index_is_complex(record):
            return
        dialog = IndexFieldsDialog([column.name for column in self.definition.columns], self._terms(record), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from opennavicat.schema import IndexTerm
        record.terms = [IndexTerm(**term) for term in dialog.terms()]
        record.columns = [term.column for term in record.terms]
        self._populating = True
        try:
            self._populate_records()
        finally:
            self._populating = False
        self._edited()

    def edit_foreign_key_fields(self, record):
        if self._read_only:
            return
        dialog = ForeignKeyFieldsDialog([column.name for column in self.definition.columns], record.columns, record.referenced_columns, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        record.columns, record.referenced_columns = dialog.values()
        self._populating = True
        try:
            self._populate_records()
        finally:
            self._populating = False
        self._edited()

    def _record_item_changed(self, name, item):
        if self._populating or self.definition is None or self._read_only:
            return
        record = getattr(self.definition, name)[item.row()]
        if item.column() == 0:
            record.name = item.text()
        elif name == "indexes" and item.column() == 2:
            record.unique = item.checkState() == Qt.CheckState.Checked
        elif name == "foreign_keys":
            if item.column() == 1:
                record.referenced_table = item.text()
            elif item.column() == 6:
                record.deferrable = item.checkState() == Qt.CheckState.Checked
                record.initially_deferred = record.deferrable
        elif name == "triggers" and item.column() >= 2:
            if item.checkState() != Qt.CheckState.Checked:
                # SQLite has exactly one event per trigger; keep one selected.
                self._populating = True
                item.setCheckState(Qt.CheckState.Checked)
                self._populating = False
                return
            record.event = ("INSERT", "UPDATE", "DELETE")[item.column() - 2]
            if record.event != "UPDATE":
                record.update_columns = []
            self._populating = True
            for column in range(2, 5):
                self.triggers.item(item.row(), column).setCheckState(Qt.CheckState.Checked if column == item.column() else Qt.CheckState.Unchecked)
            self._populating = False
            self._trigger_selected()
        self._edited()

    def _record_property(self, record, attr, value):
        if not self._populating and not self._read_only:
            setattr(record, attr, value)
            self._edited()

    def _field_selected(self):
        row = self.fields.currentIndex().row()
        column = self.definition.columns[row] if self.definition and 0 <= row < len(self.definition.columns) else None
        self.field_properties.setVisible(column is not None)
        if column is None:
            return
        before = self._populating
        self._populating = True
        try:
            self.default_edit.setCurrentText(column.default if column.default is not None else "")
            self.collation_combo.setCurrentText(column.collation)
            self.notnull_conflict_combo.setCurrentText(column.notnull_conflict)
            self.generated_type_combo.setCurrentText(column.generated_type)
            self.generated_expression_edit.setText(column.generated_expression)
            self.field_properties.setEnabled(not self._read_only and not self.loading)
            self.generated_expression_edit.setEnabled(bool(column.generated_type) and not self._read_only)
            self.notnull_conflict_combo.setEnabled(not column.nullable and not self._read_only)
        finally:
            self._populating = before

    def _field_property(self, attr, value):
        row = self.fields.currentIndex().row()
        if self._populating or self._read_only or not self.definition or row < 0:
            return
        setattr(self.definition.columns[row], attr, value)
        if attr == "generated_type":
            if not value:
                self.definition.columns[row].generated_expression = ""
                before = self._populating
                self._populating = True
                self.generated_expression_edit.clear()
                self._populating = before
            self.generated_expression_edit.setEnabled(bool(value))
        self._edited()

    def _trigger_selected(self):
        row = self.triggers.currentRow()
        record = self.definition.triggers[row] if self.definition and 0 <= row < len(self.definition.triggers) else None
        self.trigger_properties.setVisible(record is not None)
        if record:
            before = self._populating
            self._populating = True
            self.trigger_body.setPlainText(record.body)
            self.trigger_when.setText(record.when)
            self.trigger_update_columns.setText(", ".join(record.update_columns))
            self.trigger_properties.setEnabled(not self._read_only and not self.loading)
            self._populating = before

    def _trigger_body_changed(self):
        row = self.triggers.currentRow()
        if not self._populating and not self._read_only and self.definition and row >= 0:
            self.definition.triggers[row].body = self.trigger_body.toPlainText()
            self._edited()

    def _trigger_when_changed(self, value):
        row = self.triggers.currentRow()
        if not self._populating and not self._read_only and self.definition and row >= 0:
            self.definition.triggers[row].when = value
            self._edited()

    def _option_changed(self, attr, value):
        if not self._populating and not self._read_only and self.definition:
            setattr(self.definition.options, attr, value)
            self._edited()

    def _current_collection(self):
        return {0: "columns", 1: "indexes", 2: "foreign_keys", 3: "unique_keys", 4: "triggers"}.get(self.tabs.currentIndex())

    def _current_table(self):
        return self.fields if self.tabs.currentIndex() == 0 else getattr(self, self._current_collection(), None)

    def add_record(self, insert=False):
        if self.loading or self._read_only or not self.definition:
            return
        from opennavicat.schema import ColumnDefinition, ForeignKeyDefinition, IndexDefinition, TriggerDefinition, UniqueKeyDefinition
        name = self._current_collection()
        if not name:
            return
        constructor = {"columns": lambda: ColumnDefinition(""), "indexes": lambda: IndexDefinition("", []),
                       "foreign_keys": lambda: ForeignKeyDefinition("", [], "", []),
                       "unique_keys": lambda: UniqueKeyDefinition("", []), "triggers": lambda: TriggerDefinition("")}[name]
        records = getattr(self.definition, name)
        table = self._current_table()
        current = table.currentIndex().row() if name == "columns" else table.currentRow()
        row = current if insert and current >= 0 else len(records)
        if name == "columns":
            self.field_model.beginInsertRows(QModelIndex(), row, row)
        records.insert(row, constructor())
        if name == "columns":
            self.field_model.endInsertRows()
            self.fields.setCurrentIndex(self.field_model.index(row, 0))
            self.fields.edit(self.field_model.index(row, 0))
        else:
            self._populating = True
            self._populate_records()
            self._populating = False
            table.setCurrentCell(row, 0)
            table.editItem(table.item(row, 0))
        self._edited()

    def delete_record(self):
        if self.loading or self._read_only or not self.definition:
            return
        name = self._current_collection()
        if not name:
            return
        table = self._current_table()
        row = table.currentIndex().row() if name == "columns" else table.currentRow()
        records = getattr(self.definition, name)
        if not 0 <= row < len(records):
            return
        if name == "columns":
            self.field_model.beginRemoveRows(QModelIndex(), row, row)
            records.pop(row)
            self.field_model.endRemoveRows()
            self._field_selected()
        else:
            records.pop(row)
            self._populating = True
            self._populate_records()
            self._populating = False
            self._trigger_selected()
        self._edited()

    def toggle_primary_key(self):
        if self.loading or self._read_only or not self.definition or self.tabs.currentIndex() != 0:
            return
        row = self.fields.currentIndex().row()
        if row < 0:
            return
        column = self.definition.columns[row]
        column.pk_order = 0 if column.pk_order else max((col.pk_order for col in self.definition.columns), default=0) + 1
        for number, col in enumerate(sorted((col for col in self.definition.columns if col.pk_order), key=lambda col: col.pk_order), 1):
            col.pk_order = number
        self.field_model.dataChanged.emit(self.field_model.index(0, 5), self.field_model.index(len(self.definition.columns) - 1, 5))
        self._edited()

    def set_table_name(self, name):
        if self.definition and not self.loading and not self._read_only:
            self.definition.name = name
            self._edited()

    def _set_dirty(self, value):
        changed = self.dirty != bool(value)
        self.dirty = bool(value)
        if changed:
            self.dirtyChanged.emit(self.dirty)
        self.titleChanged.emit(self.title)
        self._update_actions()

    def _edited(self):
        if self._populating:
            return
        self._revision += 1
        self._plan = None
        self.confirmed_plan = None
        self._set_dirty(not self.snapshot.exists or self.definition != self.snapshot.definition)
        self._field_selected()
        self._render_plan()
        if self.tabs.currentIndex() == 6:
            self.preview_timer.start()

    def draft(self):
        return deepcopy(self.definition)

    def _commit_editor(self):
        focused = self.focusWidget()
        for view in (self.fields, self.indexes, self.foreign_keys, self.unique_keys, self.triggers):
            editor = focused
            while editor is not None and editor.parentWidget() is not view.viewport():
                editor = editor.parentWidget()
            if editor is not None and isinstance(editor, (QLineEdit, QComboBox)):
                view.commitData(editor)
                focused.clearFocus()
                return

    def request_preview(self):
        if self.definition is not None and not self.loading:
            self.previewRequested.emit(self.draft())

    def request_save(self):
        # Commit any active native cell delegate before capturing the request.
        self._commit_editor()
        if self.definition is not None and self.dirty and not self.loading and not self._read_only:
            self.preview_timer.stop()
            if self._save_dialog is not None:
                self._save_dialog.raise_()
                return
            if self.suppress_save_preview and self.snapshot.exists:
                self.saveRequested.emit(self.draft())
                return
            dialog = TableSaveDialog(self.definition, is_new=not self.snapshot.exists, parent=self)
            self._save_dialog = dialog
            dialog.previewRequested.connect(self.previewRequested)
            dialog.accepted.connect(self._save_confirmed)
            dialog.finished.connect(lambda *_: self._save_dialog_finished(dialog))
            dialog.open()
            QTimer.singleShot(0, dialog.request_preview)

    def _save_confirmed(self):
        dialog = self._save_dialog
        if dialog is None or dialog.plan is None:
            return
        if dialog.definition != self.definition:
            # Only the name is editable in the sheet. Keep the field model's
            # list bound to the current draft if application subsequently fails.
            self.definition.name = dialog.definition.name
            self._edited()
        self.confirmed_plan = dialog.plan
        self.suppress_save_preview = dialog.suppress_preview_check.isChecked()
        self.savePreviewPreferenceChanged.emit(self.suppress_save_preview)
        self.saveRequested.emit(self.draft())

    def _save_dialog_finished(self, dialog):
        if self._save_dialog is dialog:
            self._save_dialog = None
        dialog.deleteLater()

    def show_plan(self, plan):
        if (self.snapshot is None or
                (self.snapshot.exists and plan.snapshot.fingerprint != self.snapshot.fingerprint) or
                plan.snapshot.schema != self.snapshot.schema or plan.snapshot.table != self.snapshot.table):
            return
        if self._save_dialog is not None:
            self._save_dialog.show_plan(plan)
        # A late preview must never replace a newer edit's SQL.
        if self.definition is not None and plan.definition == self.definition:
            self._plan = plan
            self._render_plan()
            self.show_message("；".join(plan.warnings))

    def _render_plan(self, *_):
        if not hasattr(self, "sql_preview"):
            return
        if self.preview_mode.currentIndex() == 1:
            text = self._plan.ddl if self._plan else self.snapshot.ddl if self.snapshot and not self.dirty else "正在生成 DDL…"
        else:
            text = self._plan.sql if self._plan and self._plan.changed else "无更改" if not self.dirty else "正在生成更改…"
        self.sql_preview.setPlainText(text)

    def _tab_changed(self, index):
        self._update_actions()
        if index == 6 and self.definition is not None:
            self.request_preview()

    def _update_actions(self):
        available = self.definition is not None and not self.loading and not self._read_only
        tab = self.tabs.currentIndex()
        label = {0: "字段", 1: "索引", 2: "外键", 3: "唯一键", 4: "触发器"}.get(tab, "字段")
        self.save_action.setEnabled(available and self.dirty)
        self.add_action.setText("添加" + label)
        self.insert_action.setText("插入" + label)
        self.delete_action.setText("删除" + label)
        for action in (self.add_action, self.insert_action, self.delete_action):
            action.setEnabled(available and tab < 5)
        self.pk_action.setEnabled(available and tab == 0)

    def set_busy(self, busy, message=""):
        self.loading = bool(busy)
        self.tabs.setEnabled(not busy)
        self._update_actions()
        if message:
            self.show_message(message)

    def set_read_only(self, read_only=True):
        self._connection_read_only = bool(read_only)
        if self.snapshot is not None:
            self._read_only = self._connection_read_only or bool(self.snapshot.issues)
            self.field_model.read_only = self._read_only
            self._populating = True
            try:
                self._populate_records()
            finally:
                self._populating = False
            self.pk_conflict_combo.setEnabled(not self._read_only)
            self.without_rowid_check.setEnabled(not self._read_only)
            self._field_selected()
        self._update_actions()

    def show_message(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def show_error(self, error):
        self.set_busy(False)
        if self._save_dialog is not None:
            self._save_dialog.show_error(error)
        self.show_message(str(error))
        if self.tabs.currentIndex() == 6:
            self.sql_preview.setPlainText("无法安全生成 SQL：\n" + str(error))

    def save_succeeded(self, snapshot):
        self.load_snapshot(snapshot)

    def current_result(self):
        return None
