"""MongoDB's six-page index document; all metadata and DDL stay in its owner queue."""
from __future__ import annotations

from copy import deepcopy
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_indexes import IndexApplyError, IndexKey
from opennavicat.mongodb_session import canonical_dumps, canonical_loads

from .editor import SqlEditor
from .schema_object_designer import _ObjectDesignerPage
from .table_designer import TableSaveDialog


KEY_TYPES = [("ASC", 1), ("DESC", -1), ("2D", "2d"), ("2DSphere", "2dsphere"), ("Hashed", "hashed"), ("Text", "text")]


def index_error_text(error):
    if not isinstance(error, IndexApplyError):
        return str(error)
    labels = {"drop": "删除", "create": "创建", "not_started": "未执行",
              "acknowledged": "服务器已确认", "server_error": "服务器报错", "unknown": "确认未知"}
    steps = "\n".join(f"{labels[step.operation]} {step.collection}.{step.name}：{labels[step.status]}" for step in error.outcome.steps)
    states = []
    for collection, state in error.outcome.observation.items():
        if state.get("available"):
            names = ", ".join(row["name"] for row in state["indexes"]) or "无目标索引"
            values = {"collection": collection, "collectionUUID": state.get("namespace_uuid"),
                      "indexes": state["indexes"]}
            if state.get("lossless") is False:
                values["originalRawBSON"] = state.get("raw_indexes", [])
            states.append(collection + "：" + names + "\n" + canonical_dumps(values))
        else:
            states.append(collection + "：无法读取实际状态")
    return str(error) + "\n\n实际步骤：\n" + steps + "\n当前状态：\n" + "\n".join(states)


def _editor():
    editor = SqlEditor()
    editor.set_language("text")
    return editor


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(24)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.setColumnWidth(0, 240)
    table.setColumnWidth(1, 110)
    return table


class MongoIndexSaveDialog(TableSaveDialog):
    def __init__(self, definition, is_new=False, parent=None):
        super().__init__(definition, is_new, parent)
        self.setWindowTitle("保存索引")
        self.preview.set_language("text")
        for form in self.findChildren(QFormLayout):
            label = form.labelForField(self.name_edit)
            if label is not None:
                label.setText("索引名称")
        for label in self.findChildren(QLabel):
            if label.text() == "SQL 预览":
                label.setText("脚本预览")
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["辅助脚本（按名称寻址）", "实际执行脚本（集合 UUID）", "实际 BSON 命令"])
        self.preview_mode.currentIndexChanged.connect(self._render_preview)
        self.layout().insertWidget(self.layout().indexOf(self.preview), self.preview_mode)
        self.suppress_preview_check.setText("不再显示脚本预览")
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.layout().insertWidget(self.layout().count() - 1, self.warning)

    def show_plan(self, plan):
        if plan.definition != self.definition:
            return
        super().show_plan(plan)
        self.save_button.setEnabled(bool(self.definition.name and plan.changed))
        self.warning.setText("\n".join(plan.warnings))
        self._render_preview()

    def _render_preview(self, *_):
        if self.plan is None:
            return
        texts = [self.plan.sql, self.plan.command_script, self.plan.command_text]
        self.preview.setPlainText(texts[self.preview_mode.currentIndex()] or "无更改")

    def request_preview(self):
        self.plan = None
        self.save_button.setEnabled(False)
        if not self.definition.name:
            self.preview.setPlainText("请输入索引名称。")
            return
        self.preview.setPlainText("正在生成命令…")
        self.previewRequested.emit(deepcopy(self.definition))


class MongoIndexDeleteDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = deepcopy(plan)
        self.setWindowTitle("删除索引")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 430)
        layout = QVBoxLayout(self)
        label = QLabel("删除索引 " + plan.snapshot.collection + "." + plan.snapshot.name + "？")
        label.setWordWrap(True); layout.addWidget(label)
        self.preview = _editor(); self.preview.setReadOnly(True)
        self.preview.setPlainText(plan.command_script); layout.addWidget(self.preview, 1)
        self.permanent_check = QCheckBox("我了解此操作是永久性的且无法撤消")
        layout.addWidget(self.permanent_check)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.delete_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.delete_button.setText("删除")
        self.delete_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.permanent_check.toggled.connect(lambda checked: self.delete_button.setEnabled(checked and self.plan.delete))
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.plan.delete and self.permanent_check.isChecked():
            super().accept()


class MongoIndexDesignerPage(_ObjectDesignerPage):
    kind = "mongo_index"

    def __init__(self, connection_id, schema, collection="", name="", parent=None):
        super().__init__(connection_id, schema, name, parent)
        self._initial_collection = collection
        self._invalid = {}
        self._widgets = {}
        self.toolbar.addSeparator()
        self.add_action = self._action("添加字段", "add", self.add_field)
        self.delete_action = self._action("删除字段", "delete", self.delete_field)
        self.general = QWidget()
        layout = QVBoxLayout(self.general)
        layout.setContentsMargins(12, 12, 12, 12)
        form = self._form()
        self.table_combo = self.collection_combo = QComboBox()
        # Owners arrive after the page has already been shown. The default
        # first-show policy otherwise keeps the empty selector's cached width.
        self.collection_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.collection_combo.setMinimumContentsLength(12)
        self.collection_combo.setMaximumWidth(300)
        self.collection_combo.currentTextChanged.connect(self._owner_selected)
        form.addRow("集合名称", self.collection_combo)
        row = QWidget(); boxes = QHBoxLayout(row); boxes.setContentsMargins(0, 0, 0, 0)
        for option, label in (("unique", "唯一键"), ("background", "背景"), ("sparse", "稀疏")):
            widget = QCheckBox(label)
            self._widgets[option] = widget
            setattr(self, option + "_check", widget)
            widget.toggled.connect(lambda value, key=option: self._set_option(key, True if value else None))
            boxes.addWidget(widget)
        boxes.addStretch()
        form.addRow("", row)
        self.ttl_edit = self._line(form, "在此之后过期（秒）", "expireAfterSeconds", int)
        self.version_combo = self._choice(form, "索引版本", "v", ["", "1", "2"], int)
        layout.addLayout(form)
        self.key_tabs = QTabWidget()
        self.fields = _table(["字段", "类型"])
        self.fields.itemChanged.connect(self._fields_edited)
        self.fields.currentCellChanged.connect(lambda *_: self._update_controls())
        self.key_tabs.addTab(self.fields, "键")
        self.partial_editor = self._document_editor("partialFilterExpression")
        self.key_tabs.addTab(self.partial_editor, "部分筛选")
        layout.addWidget(self.key_tabs, 1)
        layout.addWidget(QLabel("文档选项使用 Canonical Extended JSON；原始 BSON 类型保留。"))
        self.tabs.addTab(self.general, "常规")

        self.storage = QWidget(); storage_box = QVBoxLayout(self.storage)
        self.storage_editor = self._document_editor("storageEngine")
        storage_box.addWidget(self.storage_editor)
        self.tabs.addTab(self.storage, "存储引擎")

        self.collation = QWidget(); collation_box = QVBoxLayout(self.collation)
        form = self._form()
        self.locale_edit = self._line(form, "区域设置", "collation.locale", str)
        for label, key in [("大小写级别", "caseLevel"), ("数值排序", "numericOrdering"),
                           ("逆向", "backwards"), ("规范化", "normalization")]:
            widget = QCheckBox()
            self._widgets["collation." + key] = widget
            widget.toggled.connect(lambda value, name=key: self._set_option("collation." + name, value))
            form.addRow(label, widget)
        for label, key, values, kind in [
            ("大小写优先", "caseFirst", ["", "off", "upper", "lower"], str),
            ("强度", "strength", ["", "1", "2", "3", "4", "5"], int),
            ("替代", "alternate", ["", "non-ignorable", "shifted"], str),
            ("最大变量", "maxVariable", ["", "punct", "space"], str),
        ]:
            self._choice(form, label, "collation." + key, values, kind)
        collation_box.addLayout(form); collation_box.addStretch()
        self.tabs.addTab(self.collation, "排序规则")

        self.text = QWidget(); text_box = QVBoxLayout(self.text)
        form = self._form()
        self._line(form, "默认语言", "default_language", str)
        self._line(form, "语言覆盖", "language_override", str)
        self._choice(form, "文本索引版本", "textIndexVersion", ["", "1", "2", "3"], int)
        text_box.addLayout(form)
        self.weights = _table(["字段", "权重"])
        self.weights.itemChanged.connect(self._weights_edited)
        text_box.addWidget(self.weights, 1)
        row = QHBoxLayout()
        self.add_weight_button = QPushButton("添加")
        self.remove_weight_button = QPushButton("移除")
        self.add_weight_button.clicked.connect(self.add_weight)
        self.remove_weight_button.clicked.connect(self.remove_weight)
        row.addWidget(self.add_weight_button); row.addWidget(self.remove_weight_button); row.addStretch()
        text_box.addLayout(row)
        self.tabs.addTab(self.text, "文本")

        self.geo = QWidget(); geo_box = QVBoxLayout(self.geo); form = self._form()
        self._line(form, "2D 位精度", "bits", int)
        self._line(form, "最大", "max", float)
        self._line(form, "最小", "min", float)
        self._choice(form, "2D 球体索引版本", "2dsphereIndexVersion", ["", "1", "2", "3"], int)
        self.bucket_size = QLineEdit()
        self.bucket_size.setReadOnly(True)
        self.bucket_size.setEnabled(False)
        self.bucket_size.setToolTip("GeoHaystack 已被 MongoDB 移除；现有未知定义保留为只读。")
        form.addRow("GeoHaystack 存储桶大小", self.bucket_size)
        geo_box.addLayout(form); geo_box.addStretch()
        self.tabs.addTab(self.geo, "地理空间")
        self._finish_ui()
        self.tabs.setTabText(5, "脚本预览")
        self.sql_preview.set_language("text")
        self.preview_mode.addItems(["执行脚本（集合 UUID）", "BSON 命令"])
        self.preview_mode.setMinimumWidth(0)
        self.preview_mode.setMaximumWidth(16777215)
        self.preview_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        return form

    @property
    def collection(self):
        return self.definition.collection if self.definition is not None else self._initial_collection

    @property
    def table(self):
        return self.collection

    def _line(self, form, label, name, kind):
        widget = QLineEdit()
        widget.setMaximumWidth(300)
        self._widgets[name] = widget
        widget.textChanged.connect(lambda value, key=name, parser=kind: self._scalar_edited(key, value, parser))
        form.addRow(label, widget)
        return widget

    def _choice(self, form, label, name, values, kind):
        widget = QComboBox()
        widget.addItems(values)
        widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        # A lone digit still needs room beside the native arrow and text inset.
        widget.setMinimumContentsLength(3)
        widget.setMaximumWidth(160)
        self._widgets[name] = widget
        widget.currentTextChanged.connect(lambda value, key=name, parser=kind: self._scalar_edited(key, value, parser))
        form.addRow(label, widget)
        return widget

    def _document_editor(self, name):
        editor = _editor()
        self._widgets[name] = editor
        editor.textChanged.connect(lambda key=name, widget=editor: self._document_edited(key, widget.toPlainText()))
        return editor

    def _set_option(self, name, value):
        if self._populating or self.definition is None or self._read_only:
            return
        self._invalid.pop(name, None)
        if name.startswith("collation."):
            key = name.split(".", 1)[1]
            collation = deepcopy(self.definition.options.get("collation", {}))
            if key == "locale" and value is None:
                self.definition.options.pop("collation", None)
            else:
                if value is None:
                    collation.pop(key, None)
                else:
                    collation[key] = value
                self.definition.options["collation"] = collation
        elif value is None:
            self.definition.options.pop(name, None)
        else:
            self.definition.options[name] = value
        self._edited()

    def _scalar_edited(self, name, value, kind):
        if self._populating or self.definition is None or self._read_only:
            return
        try:
            parsed = None if value == "" else kind(value)
            if isinstance(parsed, float) and not math.isfinite(parsed):
                raise ValueError()
            self._set_option(name, parsed)
        except (TypeError, ValueError):
            self._invalid[name] = "请输入有效的数值：" + name
            self._edited()

    def _document_edited(self, name, text):
        if self._populating or self.definition is None or self._read_only:
            return
        try:
            value = canonical_loads(text) if text.strip() else None
            self._set_option(name, value)
        except DatabaseError:
            self._invalid[name] = "请输入有效的 Canonical Extended JSON 文档：" + name
            self._edited()

    def _edited(self):
        super()._edited()
        if self._invalid:
            self.dirty = True
            self.dirtyChanged.emit(True); self.titleChanged.emit(self.title)
            self._update_controls()
            self.show_message("\n".join(self._invalid.values()))
        elif self.definition is not None and not self._read_only and not self._populating:
            self.show_message("")

    def draft(self):
        if self._invalid:
            raise DatabaseError("\n".join(self._invalid.values()))
        return super().draft()

    def set_collections(self, collections):
        self.set_owners(collections)

    def _render_definition(self):
        self._invalid.clear()
        self._render_owners()
        self._render_fields()
        for name, widget in self._widgets.items():
            value = self.definition.options.get(name)
            if name.startswith("collation."):
                value = self.definition.options.get("collation", {}).get(name.split(".", 1)[1])
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, SqlEditor):
                widget.setPlainText(canonical_dumps(value) if value is not None else "")
            elif isinstance(widget, QComboBox):
                text = "" if value is None else str(value)
                if widget.findText(text) < 0:
                    widget.addItem(text)
                widget.setCurrentText(text)
            else:
                widget.setText("" if value is None else str(value))
        self.bucket_size.setText(str(self.definition.options.get("bucketSize", "")))
        self._render_weights()

    def _render_columns(self):
        pass  # MongoDB index keys may name fields not present in sample documents.

    def _render_fields(self):
        self.fields.setRowCount(len(self.definition.keys))
        for row, key in enumerate(self.definition.keys):
            self.fields.setItem(row, 0, QTableWidgetItem(key.field))
            combo = QComboBox()
            for label, value in KEY_TYPES:
                combo.addItem(label, value)
            if combo.findData(key.order) < 0:
                combo.addItem(str(key.order), key.order)
            combo.setCurrentIndex(combo.findData(key.order))
            combo.currentIndexChanged.connect(lambda *_: self._fields_edited())
            self.fields.setCellWidget(row, 1, combo)

    def _fields_edited(self, *_):
        if self._populating or self.definition is None or self._read_only:
            return
        self.definition.keys = [IndexKey(self.fields.item(row, 0).text() if self.fields.item(row, 0) else "",
            self.fields.cellWidget(row, 1).currentData()) for row in range(self.fields.rowCount())]
        self._edited()

    def add_field(self):
        if self.definition is None or self._read_only or self.loading:
            return
        self.definition.keys.append(IndexKey())
        self._populating = True
        self._render_fields()
        self._populating = False
        self.fields.setCurrentCell(len(self.definition.keys) - 1, 0)
        self._edited()

    def delete_field(self):
        row = self.fields.currentRow()
        if self.definition is None or self._read_only or self.loading or row < 0:
            return
        del self.definition.keys[row]
        self._populating = True; self._render_fields(); self._populating = False
        self._edited()

    def _render_weights(self):
        values = self.definition.options.get("weights", {})
        self.weights.setRowCount(len(values))
        for row, (name, value) in enumerate(values.items()):
            self.weights.setItem(row, 0, QTableWidgetItem(name))
            self.weights.setItem(row, 1, QTableWidgetItem(str(value)))

    def _weights_edited(self, *_):
        if self._populating or self.definition is None or self._read_only:
            return
        try:
            values = {}
            for row in range(self.weights.rowCount()):
                name = self.weights.item(row, 0).text() if self.weights.item(row, 0) else ""
                value = self.weights.item(row, 1).text() if self.weights.item(row, 1) else ""
                if not name or name in values:
                    raise ValueError()
                values[name] = int(value)
            self._set_option("weights", values or None)
        except ValueError:
            self._invalid["weights"] = "文本权重需要不同的字段名称和整数。"
            self._edited()

    def add_weight(self):
        if self.definition is None or self._read_only or self.loading:
            return
        row = self.weights.rowCount()
        self._populating = True
        self.weights.insertRow(row)
        self.weights.setItem(row, 0, QTableWidgetItem(""))
        self.weights.setItem(row, 1, QTableWidgetItem("1"))
        self._populating = False
        self.weights.setCurrentCell(row, 0)
        self._weights_edited()

    def remove_weight(self):
        if self.definition is not None and not self._read_only and not self.loading and self.weights.currentRow() >= 0:
            self.weights.removeRow(self.weights.currentRow())
            self._weights_edited()

    def request_preview(self):
        if self.definition is not None and not self.loading:
            try:
                self.previewRequested.emit(self.draft())
            except DatabaseError as error:
                self.show_error(error)

    def request_save(self):
        if self.definition is None or not self.dirty or self.loading or self._read_only:
            return
        try:
            definition = self.draft()
        except DatabaseError as error:
            self.show_error(error)
            return
        self.preview_timer.stop()
        if self.suppress_save_preview and self.snapshot.exists:
            self.saveRequested.emit(definition)
            return
        if self._save_dialog is not None:
            self._save_dialog.raise_()
            return
        dialog = MongoIndexSaveDialog(definition, not self.snapshot.exists, self)
        dialog.suppress_preview_check.setChecked(self.suppress_save_preview)
        self._save_dialog = dialog
        dialog.previewRequested.connect(self.previewRequested)
        dialog.accepted.connect(self._save_confirmed)
        dialog.finished.connect(lambda *_: self._sheet_finished(dialog))
        dialog.open()
        QTimer.singleShot(0, dialog.request_preview)

    def show_plan(self, plan):
        if self._invalid or not self.snapshot or plan.snapshot.fingerprint != self.snapshot.fingerprint:
            return
        super().show_plan(plan)

    def _render_plan(self, *_):
        if not hasattr(self, "sql_preview"):
            return
        if self.preview_mode.currentIndex() in (2, 3):
            value = self._plan or (self.snapshot if not self.dirty else None)
            self.sql_preview.setPlainText((value.command_script if self.preview_mode.currentIndex() == 2 else value.command_text) if value is not None else "正在生成命令…")
            return
        super()._render_plan()

    def _update_specific_controls(self, editable):
        if not hasattr(self, "fields"):
            return
        self.add_action.setEnabled(editable and self.tabs.currentIndex() == 0)
        self.delete_action.setEnabled(editable and self.tabs.currentIndex() == 0 and self.fields.currentRow() >= 0)
        for name in ("storage", "collation", "text", "geo"):
            if hasattr(self, name):
                getattr(self, name).setEnabled(editable)
        has_locale = bool(self.definition and self.definition.options.get("collation", {}).get("locale"))
        for name, widget in self._widgets.items():
            if name.startswith("collation.") and name != "collation.locale":
                widget.setEnabled(editable and has_locale)
        self.save_action.setEnabled(editable and self.dirty and not self._invalid)

    def show_error(self, error):
        error = DatabaseError(index_error_text(error))
        super().show_error(error)
        if self.tabs.currentIndex() == 5:
            self.sql_preview.setPlainText("无法安全生成命令：\n" + str(error))
