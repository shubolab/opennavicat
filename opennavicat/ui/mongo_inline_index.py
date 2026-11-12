"""Reference-shaped embedded index rows, key modal and save sheet."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_indexes import InlineIndexApplyError, IndexDefinition, IndexKey

from .editor import SqlEditor
from .mongo_index_designer import KEY_TYPES, index_error_text


ORDER_LABELS = {value: label for label, value in KEY_TYPES}


def order_label(order):
    return ORDER_LABELS.get(order, str(order))


def key_summary(keys):
    return ", ".join(f"{key.field} {order_label(key.order)}" for key in keys)


class InlineIndexKeyDialog(QDialog):
    """Nested key editor whose committed model is independent of widget text."""

    def __init__(self, definition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Window")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(520, 320)
        self.definition = deepcopy(definition)
        self._populating = False
        box = QVBoxLayout(self)
        self.table = QTableWidget(0, 2)
        self.table.setObjectName("embeddedIndexKeys")
        self.table.setHorizontalHeaderLabels(["名称", "类型"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 300)
        box.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.add_button = QPushButton("添加")
        self.remove_button = QPushButton("移除")
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        box.addLayout(actions)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        box.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("好")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.add_button.clicked.connect(self.add_key)
        self.remove_button.clicked.connect(self.remove_key)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._render()
        self._update_buttons()

    def _render(self):
        self._populating = True
        try:
            self.table.setRowCount(0)
            for key in self.definition.keys:
                self.add_key(field=key.field, order=key.order, render=False)
        finally:
            self._populating = False

    def add_key(self, *, field="", order=1, render=True):
        if render:
            self._capture()
        row = self.table.rowCount()
        self.table.insertRow(row)
        name = QComboBox()
        name.setEditable(True)
        name.setObjectName("embeddedIndexKeyName")
        name.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for existing in sorted({key.field for key in self.definition.keys if key.field}
                               | {"_id", "label", "created_at", "sku", "value"}):
            name.addItem(existing)
        name.setCurrentText(field)
        kind = QComboBox()
        kind.setObjectName("embeddedIndexKeyType")
        for label, value in KEY_TYPES:
            kind.addItem(label, value)
        match = kind.findData(order)
        if match >= 0:
            kind.setCurrentIndex(match)
        self.table.setCellWidget(row, 0, name)
        self.table.setCellWidget(row, 1, kind)
        self.table.selectRow(row)
        self._update_buttons()

    def remove_key(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._update_buttons()

    def _capture(self):
        if self._populating:
            return
        keys = []
        for row in range(self.table.rowCount()):
            field = self.table.cellWidget(row, 0).currentText().strip()
            kind = self.table.cellWidget(row, 1).currentData()
            if not field:
                continue
            keys.append(IndexKey(field, kind))
        self.definition.keys = keys

    def _update_buttons(self):
        self.remove_button.setEnabled(self.table.currentRow() >= 0)

    def accept(self):
        self._capture()
        try:
            if not self.definition.keys:
                raise DatabaseError("Add at least one index field")
            names = [key.field for key in self.definition.keys]
            if len(names) != len(set(names)):
                raise DatabaseError("An index field cannot appear twice")
        except DatabaseError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.error.hide()
        super().accept()


class MongoInlineIndexSaveDialog(QDialog):
    """Sheet showing the exact ordered commands for embedded index drafts."""

    previewRequested = Signal(object)

    def __init__(self, drafts, parent=None):
        super().__init__(parent)
        self.drafts = deepcopy(list(drafts))
        self.plan = None
        self.setWindowTitle("保存")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(820, 620)
        box = QVBoxLayout(self)
        box.addWidget(QLabel("脚本预览:"))
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["执行脚本", "BSON 命令"])
        self.preview_mode.currentIndexChanged.connect(self._render_preview)
        box.addWidget(self.preview_mode)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        box.addWidget(self.warning)
        self.preview = SqlEditor()
        self.preview.setObjectName("embeddedIndexPreview")
        self.preview.set_language("text")
        self.preview.setReadOnly(True)
        box.addWidget(self.preview, 1)
        self.error = QLabel()
        self.error.setWordWrap(True)
        box.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.save_button.setText("保存")
        self.save_button.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def request_preview(self):
        self.plan = None
        self.save_button.setEnabled(False)
        self.error.hide()
        self.preview.setPlainText("正在生成索引命令…")
        self.previewRequested.emit(deepcopy(self.drafts))

    def show_plan(self, plan):
        self.plan = deepcopy(plan)
        self.save_button.setEnabled(bool(plan.changed))
        self.warning.setText("\n".join(plan.warnings))
        self._render_preview()

    def show_error(self, error):
        if isinstance(error, InlineIndexApplyError):
            message = str(error) + '\n\n' + index_error_text(error.cause)
        else:
            message = index_error_text(error)
        self.error.setText(message)
        self.error.show()
        self.preview.setPlainText(message)

    def _render_preview(self, *_):
        if self.plan is None:
            return
        try:
            text = self.plan.command_script if self.preview_mode.currentIndex() == 0 else self.plan.command_text
        except DatabaseError as error:
            text = "无法安全生成索引脚本：\n" + str(error)
        self.preview.setPlainText(text)
