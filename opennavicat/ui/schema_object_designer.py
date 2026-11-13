"""Independent SQLite index and trigger documents with queued owner-side DDL.

These widgets only edit detached definitions. Metadata, planning and application
belong to MainWindow's serial background queue, as in the table designer.
"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QTableWidget,
    QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.schema import IndexTerm

from .editor import SqlEditor
from .icons import tool_icon, toggle_icon
from .table_designer import COLLATIONS, TableSaveDialog


def _combo(values, value="", editable=False):
    combo = QComboBox()
    combo.setEditable(editable)
    combo.addItems(list(values))
    if value and value not in values:
        combo.addItem(value)
    combo.setCurrentText(value)
    return combo


class ObjectSaveDialog(TableSaveDialog):
    """Use the same confirmed-plan sheet with the object's actual name label."""

    def __init__(self, definition, kind, is_new=False, parent=None):
        super().__init__(definition, is_new, parent)
        label = "索引" if kind == "index" else "触发器"
        self.setWindowTitle("保存" + label)
        for form in self.findChildren(QFormLayout):
            name_label = form.labelForField(self.name_edit)
            if name_label is not None:
                name_label.setText(label + "名称")


class _ObjectDesignerPage(QWidget):
    loadRequested = Signal()
    previewRequested = Signal(object)
    saveRequested = Signal(object)
    dirtyChanged = Signal(bool)
    titleChanged = Signal(str)
    focusRequested = Signal(bool)
    savePreviewPreferenceChanged = Signal(bool)
    ownerChanged = Signal(str, str)  # owner_type ('table'/'view'), owner name

    def __init__(self, connection_id, schema="main", name="", parent=None):
        super().__init__(parent)
        self.connection_id, self.schema, self.object_name = connection_id, schema, name
        self.snapshot = self.definition = None
        self.dirty = self.loading = self.running = False
        self._populating = self._connection_read_only = self._read_only = False
        self._plan = self.confirmed_plan = self._save_dialog = None
        self._revision = 0
        self.suppress_save_preview = False
        self._tables, self._views, self._column_names = [], [], []
        self.setObjectName("schemaObjectDesignerPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setFixedHeight(36)
        self.save_action = self._action("保存", "save", self.request_save)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        layout.addWidget(self.toolbar)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("structureTabs")
        layout.addWidget(self.tabs, 1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setContentsMargins(8, 4, 8, 4)
        self.message.hide()
        layout.addWidget(self.message)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self.request_preview)

    @property
    def title(self):
        name = self.definition.name if self.definition else self.object_name
        return (name or "无标题") + (" — 已编辑" if self.dirty else "")

    @property
    def table(self):
        return self.definition.table if self.definition else ""

    @property
    def owner_type(self):
        return getattr(self.definition, "owner_type", "table")

    @property
    def modified(self):
        return self.dirty

    @property
    def revision(self):
        return self._revision

    def _action(self, label, symbol, callback):
        action = QAction(tool_icon(symbol, 18), label, self)
        if callback:
            action.triggered.connect(callback)
        self.toolbar.addAction(action)
        self.addAction(action)
        return action

    def _finish_ui(self):
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.focus_action = self._action("专注模式", "focus", None)
        self.focus_action.setIcon(toggle_icon("focus", 18))
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)
        preview = QWidget()
        box = QVBoxLayout(preview)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 4)
        self.preview_mode = _combo(["更改", "DDL"], "更改")
        self.preview_mode.setFixedWidth(120)
        row.addStretch()
        row.addWidget(self.preview_mode)
        row.addStretch()
        box.addLayout(row)
        self.sql_preview = SqlEditor()
        self.sql_preview.setReadOnly(True)
        box.addWidget(self.sql_preview, 1)
        self.tabs.addTab(preview, "SQL 预览")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.preview_mode.currentIndexChanged.connect(self._render_plan)
        self._render_plan()
        self._update_controls()

    def load_snapshot(self, snapshot):
        self.preview_timer.stop()
        previous_owner = (self.owner_type, self.table)
        self.snapshot = deepcopy(snapshot)
        self.definition = deepcopy(snapshot.definition)
        if previous_owner != (self.owner_type, self.table):
            self._column_names = []
        self.schema, self.object_name = snapshot.schema, snapshot.name
        self._read_only = self._connection_read_only or snapshot.read_only or bool(snapshot.issues)
        self._plan = self.confirmed_plan = None
        self._populating = True
        try:
            self._render_definition()
        finally:
            self._populating = False
        self.dirty = not snapshot.exists
        self.set_busy(False)
        self.dirtyChanged.emit(self.dirty)
        self.titleChanged.emit(self.title)
        self._render_plan()
        reasons = list(snapshot.issues)
        if self._connection_read_only:
            reasons.append("此连接为只读。")
        elif snapshot.read_only and not reasons:
            reasons.append("此对象由 SQLite 约束维护，不能在独立设计器中修改。")
        self.show_message("只读：" + "；".join(reasons) if reasons else "")
        self._notify_owner()

    def draft(self):
        return deepcopy(self.definition)

    def _edited(self):
        if self._populating or not self.definition or self._read_only:
            return
        self._revision += 1
        self._plan = self.confirmed_plan = None
        self.dirty = not self.snapshot.exists or self.definition != self.snapshot.definition
        self.dirtyChanged.emit(self.dirty)
        self.titleChanged.emit(self.title)
        self._update_controls()
        self._render_plan()
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.preview_timer.start()

    def set_owners(self, tables, views=None):
        self._tables = [item["name"] if isinstance(item, dict) else str(item) for item in tables]
        self._views = [item["name"] if isinstance(item, dict) else str(item) for item in views or []]
        before = self._populating
        self._populating = True
        try:
            self._render_owners()
        finally:
            self._populating = before

    def set_columns(self, table, columns, owner_type="table"):
        if self.definition is None or (self.owner_type, self.table) != (owner_type, table):
            return False
        self._column_names = [column["name"] if isinstance(column, dict) else str(column) for column in columns]
        before = self._populating
        self._populating = True
        try:
            self._render_columns()
        finally:
            self._populating = before
        return True

    def _render_owners(self):
        name = self.table
        values = self._views if self.owner_type == "view" else self._tables
        self.table_combo.clear()
        self.table_combo.addItems([""] + values)
        if name and name not in values:
            self.table_combo.addItem(name)
        self.table_combo.setCurrentText(name)

    def _owner_selected(self, name):
        if self._populating or not self.definition or self._read_only:
            return
        self.definition.table = name
        self._column_names = []
        self._populating = True
        self._render_columns()
        self._populating = False
        self._edited()
        self._notify_owner()

    def _notify_owner(self):
        if self.definition and self.table:
            self.ownerChanged.emit(self.owner_type, self.table)

    def _tab_changed(self, index):
        self._update_controls()
        if index == self.tabs.count() - 1:
            self.request_preview()

    def request_preview(self):
        if self.definition is not None and not self.loading:
            try:
                definition = self.draft()
            except DatabaseError as error:
                # Draft validation also runs from Qt tab/timer signals. Keep
                # the invalid input in its editor without dispatching a plan.
                self.show_error(error)
                return
            self.previewRequested.emit(definition)

    def request_save(self):
        if not self.definition or not self.dirty or self.loading or self._read_only:
            return
        self.preview_timer.stop()
        if self._save_dialog is not None:
            self._save_dialog.raise_()
            return
        if self.suppress_save_preview and self.snapshot.exists:
            self.saveRequested.emit(self.draft())
            return
        dialog = ObjectSaveDialog(self.definition, self.kind, not self.snapshot.exists, self)
        self._save_dialog = dialog
        dialog.previewRequested.connect(self.previewRequested)
        dialog.accepted.connect(self._save_confirmed)
        dialog.finished.connect(lambda *_: self._sheet_finished(dialog))
        dialog.open()
        QTimer.singleShot(0, dialog.request_preview)

    def _save_confirmed(self):
        dialog = self._save_dialog
        if dialog is None or dialog.plan is None:
            return
        if dialog.definition.name != self.definition.name:
            self.definition.name = dialog.definition.name
            self._edited()
        self.confirmed_plan = dialog.plan
        self.suppress_save_preview = dialog.suppress_preview_check.isChecked()
        self.savePreviewPreferenceChanged.emit(self.suppress_save_preview)
        self.saveRequested.emit(self.draft())

    def _sheet_finished(self, dialog):
        if self._save_dialog is dialog:
            self._save_dialog = None
        dialog.deleteLater()

    def show_plan(self, plan):
        if (not self.snapshot or plan.snapshot.kind != self.kind or
                plan.snapshot.schema != self.schema or plan.snapshot.name != self.snapshot.name or
                (self.snapshot.exists and plan.snapshot.fingerprint != self.snapshot.fingerprint)):
            return
        if self._save_dialog is not None:
            self._save_dialog.show_plan(plan)
        if self.definition == plan.definition:
            self._plan = plan
            self._render_plan()
            self.show_message("；".join(plan.warnings))

    def _render_plan(self, *_):
        if not hasattr(self, "sql_preview"):
            return
        if self.preview_mode.currentText() == "DDL":
            text = self._plan.ddl if self._plan else self.snapshot.ddl if self.snapshot and not self.dirty else "正在生成 DDL…"
        else:
            text = self._plan.sql if self._plan and self._plan.changed else "无更改" if not self.dirty else "正在生成更改…"
        self.sql_preview.setPlainText(text)

    def _update_controls(self):
        editable = self.definition is not None and not self.loading and not self._read_only
        self.save_action.setEnabled(editable and self.dirty)
        if hasattr(self, "general"):
            self.general.setEnabled(editable)
        self._update_specific_controls(editable)

    def set_busy(self, busy, message=""):
        self.loading = bool(busy)
        self.tabs.setEnabled(not busy)
        self._update_controls()
        if message:
            self.show_message(message)

    def set_read_only(self, value=True):
        self._connection_read_only = bool(value)
        self._read_only = bool(value or (self.snapshot and (self.snapshot.read_only or self.snapshot.issues)))
        self._update_controls()

    def show_message(self, message):
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def show_error(self, error):
        self.set_busy(False)
        self.show_message(str(error))
        if self._save_dialog is not None:
            self._save_dialog.show_error(error)
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.sql_preview.setPlainText("无法安全生成 SQL：\n" + str(error))

    def save_succeeded(self, snapshot):
        self.load_snapshot(snapshot)

    def current_result(self):
        return None


class IndexDesignerPage(_ObjectDesignerPage):
    kind = "index"

    def __init__(self, connection_id, schema="main", name="", parent=None):
        super().__init__(connection_id, schema, name, parent)
        self.toolbar.addSeparator()
        self.add_action = self._action("添加字段", "add", self.add_field)
        self.delete_action = self._action("删除字段", "delete", self.delete_field)
        self.general = QWidget()
        layout = QVBoxLayout(self.general)
        layout.setContentsMargins(12, 12, 12, 12)
        self.table_combo = _combo([""])
        self.table_combo.setMaximumWidth(280)
        self.unique_check = QCheckBox("唯一键")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("表名称", self.table_combo)
        form.addRow("", self.unique_check)
        layout.addLayout(form)
        self.fields = QTableWidget(0, 3)
        self.fields.setHorizontalHeaderLabels(["名称", "排序规则", "排序"])
        self.fields.verticalHeader().hide()
        self.fields.verticalHeader().setDefaultSectionSize(24)
        self.fields.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.fields.horizontalHeader().setStretchLastSection(False)
        for index, width in enumerate((180, 150, 85)):
            self.fields.setColumnWidth(index, width)
        self.fields.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fields.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.fields, 1)
        self.tabs.addTab(self.general, "常规")
        self.table_combo.currentTextChanged.connect(self._owner_selected)
        self.unique_check.toggled.connect(self._unique_changed)
        self._finish_ui()

    def _render_definition(self):
        self._render_owners()
        self.unique_check.setChecked(self.definition.unique)
        self._render_columns()

    def _render_columns(self):
        if self.definition is None:
            return
        terms = self.definition.terms or [IndexTerm(column) for column in self.definition.columns]
        self.fields.setRowCount(len(terms))
        for row, term in enumerate(terms):
            field = _combo([""] + self._column_names, term.expression or term.column, editable=True)
            collation = _combo(COLLATIONS, term.collation, editable=True)
            order = _combo(["ASC", "DESC"], "DESC" if term.descending else "ASC")
            for column, widget in enumerate((field, collation, order)):
                self.fields.setCellWidget(row, column, widget)
                widget.currentTextChanged.connect(self._terms_changed)

    def _terms_changed(self, *_):
        if self._populating or not self.definition or self._read_only:
            return
        self.definition.terms = [IndexTerm(self.fields.cellWidget(row, 0).currentText(), self.fields.cellWidget(row, 1).currentText(), self.fields.cellWidget(row, 2).currentText() == "DESC") for row in range(self.fields.rowCount())]
        self.definition.columns = [term.column for term in self.definition.terms]
        self._edited()

    def _unique_changed(self, value):
        if not self._populating and self.definition and not self._read_only:
            self.definition.unique = value
            self._edited()

    def add_field(self):
        if self.loading or self._read_only or not self.definition:
            return
        self.definition.terms = list(self.definition.terms or [IndexTerm(column) for column in self.definition.columns]) + [IndexTerm()]
        self.definition.columns = [term.column or term.expression for term in self.definition.terms]
        self._populating = True
        self._render_columns()
        self._populating = False
        self.fields.setCurrentCell(self.fields.rowCount() - 1, 0)
        self._edited()

    def delete_field(self):
        if self.loading or self._read_only or not self.definition:
            return
        row = self.fields.currentRow()
        if row < 0:
            return
        terms = list(self.definition.terms or [IndexTerm(column) for column in self.definition.columns])
        terms.pop(row)
        self.definition.terms = terms
        self.definition.columns = [term.column or term.expression for term in terms]
        self._populating = True
        self._render_columns()
        self._populating = False
        self._edited()

    def _update_specific_controls(self, editable):
        if hasattr(self, "add_action"):
            self.add_action.setEnabled(editable and self.tabs.currentIndex() == 0)
            self.delete_action.setEnabled(editable and self.tabs.currentIndex() == 0)


class TriggerDesignerPage(_ObjectDesignerPage):
    kind = "trigger"

    def __init__(self, connection_id, schema="main", name="", parent=None):
        super().__init__(connection_id, schema, name, parent)
        self.general = QWidget()
        row = QHBoxLayout(self.general)
        row.setContentsMargins(16, 16, 16, 16)
        form_widget = QWidget()
        form_widget.setMaximumWidth(610)
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.owner_type_combo = _combo(["Table", "View"], "Table")
        self.table_combo = _combo([""])
        self.timing_combo = _combo(["BEFORE", "AFTER"], "BEFORE")
        self.when_edit = QLineEdit()
        form.addRow("触发器类型", self.owner_type_combo)
        form.addRow("表名称", self.table_combo)
        form.addRow("触发", self.timing_combo)
        events = QWidget()
        event_row = QHBoxLayout(events)
        event_row.setContentsMargins(0, 0, 0, 0)
        self.event_checks = {}
        for label, event in (("插入", "INSERT"), ("删除", "DELETE"), ("更新", "UPDATE")):
            check = QCheckBox(label)
            check.setToolTip("SQLite 每个触发器只支持一种事件。")
            self.event_checks[event] = check
            event_row.addWidget(check)
            check.toggled.connect(lambda checked, e=event: self._event_changed(e, checked))
        event_row.addStretch()
        form.addRow("事件", events)
        self.update_columns = QListWidget()
        self.update_columns.setMaximumHeight(180)
        self.update_columns.itemChanged.connect(self._update_columns_changed)
        form.addRow("UPDATE OF", self.update_columns)
        selection = QWidget()
        selection_row = QHBoxLayout(selection)
        selection_row.setContentsMargins(0, 0, 0, 0)
        self.select_all_button = QPushButton("全选")
        self.deselect_all_button = QPushButton("取消全选")
        self.select_all_button.clicked.connect(lambda: self._select_update_columns(True))
        self.deselect_all_button.clicked.connect(lambda: self._select_update_columns(False))
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.deselect_all_button)
        selection_row.addStretch()
        form.addRow("", selection)
        form.addRow("当子句", self.when_edit)
        row.addWidget(form_widget, 1)
        row.addStretch()
        self.tabs.addTab(self.general, "常规")
        self.body_editor = SqlEditor()
        self.tabs.addTab(self.body_editor, "定义")
        self.owner_type_combo.currentTextChanged.connect(self._owner_type_changed)
        self.table_combo.currentTextChanged.connect(self._owner_selected)
        self.timing_combo.currentTextChanged.connect(self._timing_changed)
        self.when_edit.textChanged.connect(self._when_changed)
        self.body_editor.textChanged.connect(self._body_changed)
        self._finish_ui()

    def _render_definition(self):
        self.owner_type_combo.setCurrentText("View" if self.owner_type == "view" else "Table")
        self._render_owners()
        self._render_timings()
        for event, check in self.event_checks.items():
            check.setChecked(event == self.definition.event)
        self.when_edit.setText(self.definition.when)
        self.body_editor.setPlainText(self.definition.body)
        self._render_columns()

    def _render_timings(self):
        values = ["INSTEAD OF"] if self.owner_type == "view" else ["BEFORE", "AFTER"]
        self.timing_combo.clear()
        self.timing_combo.addItems(values)
        if self.definition.timing not in values:
            self.timing_combo.addItem(self.definition.timing)
        self.timing_combo.setCurrentText(self.definition.timing)

    def _render_columns(self):
        if not self.definition:
            return
        selected = self.definition.update_columns
        names = list(dict.fromkeys(self._column_names + selected))
        self.update_columns.clear()
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)
            self.update_columns.addItem(item)

    def _owner_type_changed(self, value):
        if self._populating or not self.definition or self._read_only:
            return
        self.definition.owner_type = "view" if value == "View" else "table"
        self.definition.table = ""
        self.definition.timing = "INSTEAD OF" if self.owner_type == "view" else "BEFORE"
        self._column_names = []
        self.definition.update_columns = []
        self._populating = True
        self._render_owners()
        self._render_timings()
        self._render_columns()
        self._populating = False
        self._edited()

    def _timing_changed(self, value):
        if not self._populating and self.definition and not self._read_only:
            self.definition.timing = value
            self._edited()

    def _event_changed(self, event, checked):
        if self._populating or not self.definition or self._read_only:
            return
        self._populating = True
        if checked:
            self.definition.event = event
            for other, check in self.event_checks.items():
                check.setChecked(other == event)
            if event != "UPDATE":
                self.definition.update_columns = []
                self._render_columns()
        else:
            self.event_checks[self.definition.event].setChecked(True)
        self._populating = False
        self._edited()

    def _update_columns_changed(self, *_):
        if not self._populating and self.definition and not self._read_only:
            self.definition.update_columns = [self.update_columns.item(row).text() for row in range(self.update_columns.count()) if self.update_columns.item(row).checkState() == Qt.CheckState.Checked]
            self._edited()

    def _select_update_columns(self, selected):
        if not self.definition or self.definition.event != "UPDATE" or self._read_only or self.loading:
            return
        self._populating = True
        for row in range(self.update_columns.count()):
            self.update_columns.item(row).setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self._populating = False
        self._update_columns_changed()

    def _when_changed(self, value):
        if not self._populating and self.definition and not self._read_only:
            self.definition.when = value
            self._edited()

    def _body_changed(self):
        if not self._populating and self.definition and not self._read_only:
            self.definition.body = self.body_editor.toPlainText()
            self._edited()

    def _update_specific_controls(self, editable):
        if hasattr(self, "body_editor"):
            self.body_editor.setReadOnly(not editable)
            update = bool(editable and self.definition.event == "UPDATE")
            for widget in (self.update_columns, self.select_all_button, self.deselect_all_button):
                widget.setEnabled(update)
