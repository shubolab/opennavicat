"""MongoDB view designer with owner-queue catalog I/O."""
from __future__ import annotations

from copy import deepcopy
import base64

from bson import BSON
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import (
    BSON_CODEC, canonical_array_dumps, canonical_array_loads, canonical_dumps,
)

from .editor import SqlEditor
from .mongo_collection_designer import collection_error_text
from .schema_object_designer import _ObjectDesignerPage
from .table_designer import TableSaveDialog


class MongoViewSaveDialog(TableSaveDialog):
    def __init__(self, definition, is_new=False, parent=None):
        super().__init__(definition, is_new, parent)
        self.setWindowTitle('保存视图')
        for form in self.findChildren(QFormLayout):
            label = form.labelForField(self.name_edit)
            if label is not None: label.setText('视图名称')
        for label in self.findChildren(QLabel):
            if label.text() == 'SQL 预览': label.setText('脚本预览')
        self.preview_mode = QComboBox(); self.preview_mode.addItems(['执行脚本', 'BSON 命令'])
        self.preview_mode.currentIndexChanged.connect(self._render_preview)
        self.layout().insertWidget(self.layout().indexOf(self.preview), self.preview_mode)
        self.suppress_preview_check.setText('不再显示脚本预览')
        self.warning = QLabel(); self.warning.setWordWrap(True)
        self.layout().insertWidget(self.layout().count() - 1, self.warning)

    def _render_preview(self, *_):
        if self.plan is None: return
        try:
            text = self.plan.command_script if self.preview_mode.currentIndex() == 0 else self.plan.command_text
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.preview.setPlainText(text if self.plan.changed else '无更改')

    def show_plan(self, plan):
        if plan.definition != self.definition: return
        self.plan = plan
        self.save_button.setEnabled(bool(self.definition.name.strip() and plan.changed))
        self.warning.setText('\n'.join(plan.warnings))
        self._render_preview()

    def request_preview(self):
        self.plan = None; self.save_button.setEnabled(False)
        if not self.definition.name.strip():
            self.preview.setPlainText('请输入视图名称。'); return
        self.preview.setPlainText('正在生成视图命令…')
        self.previewRequested.emit(deepcopy(self.definition))


class MongoViewDropDialog(QDialog):
    """Permanent view-drop confirmation with the exact reviewed command."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = deepcopy(plan)
        self.setWindowTitle('删除视图')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        subject = plan.snapshot.schema + '.' + plan.snapshot.name
        label = QLabel(f'永久删除视图 {subject}？')
        label.setWordWrap(True); layout.addWidget(label)
        self.warning = QLabel('\n'.join(plan.warnings)); self.warning.setWordWrap(True); layout.addWidget(self.warning)
        self.preview = SqlEditor(); self.preview.set_language('text'); self.preview.setReadOnly(True)
        try: self.preview.setPlainText(plan.command_script)
        except DatabaseError as error: self.preview.setPlainText('无法生成执行脚本：\n' + str(error) + '\n\n' + plan.command_text)
        layout.addWidget(self.preview, 1)
        self.permanent_check = QCheckBox('我了解此操作是永久性的、不会自动回滚，并且视图没有 UUID；最终检查后由其他客户端替换的同名视图也可能被删除')
        layout.addWidget(self.permanent_check)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm_button.setText('删除'); self.confirm_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.permanent_check.toggled.connect(self.confirm_button.setEnabled)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.plan is not None and self.permanent_check.isChecked(): super().accept()


class MongoViewDesignerPage(_ObjectDesignerPage):
    kind = 'mongo_view'

    def __init__(self, connection_id, schema='', name='', parent=None):
        super().__init__(connection_id, schema, name, parent)
        self._initial_name = name
        self._invalid = {}
        self.toolbar.addSeparator()
        self.general = QWidget(); layout = QVBoxLayout(self.general); layout.setContentsMargins(12, 12, 12, 12)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_edit = QLineEdit(); self.name_edit.setMaximumWidth(360)
        self.source_combo = QComboBox(); self.source_combo.setEditable(True); self.source_combo.setMaximumWidth(360)
        form.addRow('视图名称', self.name_edit)
        form.addRow('源集合/视图', self.source_combo)
        layout.addLayout(form)
        layout.addWidget(QLabel('Pipeline（Canonical Extended JSON 文档数组）'))
        self.pipeline_editor = SqlEditor(); self.pipeline_editor.set_language('text')
        layout.addWidget(self.pipeline_editor, 1)
        self.tabs.addTab(self.general, '常规')
        self.raw = QWidget(); raw = QVBoxLayout(self.raw); raw.setContentsMargins(12, 12, 12, 12)
        raw.addWidget(QLabel('服务器原始 catalog BSON（只读）'))
        self.raw_editor = QPlainTextEdit(); self.raw_editor.setReadOnly(True); raw.addWidget(self.raw_editor, 1)
        self.tabs.addTab(self.raw, '原始定义')
        self._finish_ui(); self.tabs.setTabText(self.tabs.count() - 1, '脚本预览')
        self.sql_preview.set_language('text')
        self.preview_mode.clear(); self.preview_mode.addItems(['执行脚本', 'BSON 命令'])
        self.preview_mode.setMinimumWidth(0); self.preview_mode.setMaximumWidth(16777215)
        self.preview_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.name_edit.textChanged.connect(self._name_changed)
        self.source_combo.currentTextChanged.connect(self._source_changed)
        self.pipeline_editor.textChanged.connect(self._pipeline_changed)

    @property
    def table(self): return self.definition.name if self.definition is not None else self._initial_name
    @property
    def collection(self): return self.table

    def set_sources(self, values):
        current = self.source_combo.currentText()
        self._populating = True
        self.source_combo.clear()
        self.source_combo.addItems([''] + sorted(set(str(value) for value in values if value)))
        self.source_combo.setCurrentText(current)
        self._populating = False

    def _name_changed(self, value):
        if self._populating or self.definition is None or self._read_only or self.snapshot.exists: return
        self.definition.name = value
        self._invalid.pop('name', None)
        if not value.strip(): self._invalid['name'] = '请输入视图名称。'
        self._edited()

    def _source_changed(self, value):
        if self._populating or self.definition is None or self._read_only: return
        self.definition.view_on = value
        self._invalid.pop('source', None)
        if not value.strip(): self._invalid['source'] = '请选择源集合或视图。'
        self._edited()

    def _pipeline_changed(self):
        if self._populating or self.definition is None or self._read_only: return
        text = self.pipeline_editor.toPlainText()
        self._invalid.pop('pipeline', None)
        if not text.strip():
            self.definition.pipeline = []
        else:
            try:
                value = canonical_array_loads(text)
                if any(not isinstance(stage, dict) for stage in value):
                    raise ValueError()
                self.definition.pipeline = value
            except Exception:
                self._invalid['pipeline'] = 'Pipeline 必须是 Canonical Extended JSON 文档数组。'
        self._edited()

    def _edited(self):
        super()._edited()
        if self._invalid:
            self.dirty = True; self.dirtyChanged.emit(True); self.titleChanged.emit(self.title)
            self._update_controls(); self.show_message('\n'.join(self._invalid.values()))
        elif self.definition is not None and not self._read_only and not self._populating:
            self.show_message('')

    def draft(self):
        if self._invalid: raise DatabaseError('\n'.join(self._invalid.values()))
        return super().draft()

    def _render_definition(self):
        self._invalid.clear(); value = self.definition
        if not value.name.strip(): self._invalid['name'] = '请输入视图名称。'
        if not value.view_on.strip(): self._invalid['source'] = '请选择源集合或视图。'
        self.name_edit.setText(value.name); self.source_combo.setCurrentText(value.view_on)
        try:
            self.pipeline_editor.setPlainText(canonical_array_dumps(value.pipeline))
        except DatabaseError:
            self._invalid['pipeline'] = 'Pipeline 无法无损转换为 Canonical Extended JSON；请查看原始 catalog BSON。'
            self.pipeline_editor.setPlainText('')
        if self.snapshot.raw_spec:
            try: self.raw_editor.setPlainText(canonical_dumps(BSON(self.snapshot.raw_spec).decode(codec_options=BSON_CODEC)))
            except Exception: self.raw_editor.setPlainText('原始 BSON 无法解码；base64：\n' + base64.b64encode(self.snapshot.raw_spec).decode('ascii'))
        else: self.raw_editor.setPlainText('新视图尚无服务器 catalog BSON。')

    def _render_columns(self): pass

    def _update_specific_controls(self, editable):
        self.name_edit.setReadOnly(bool(self.snapshot and self.snapshot.exists))
        self.source_combo.setEnabled(editable)
        self.pipeline_editor.setReadOnly(not editable)
        if hasattr(self, 'save_action'): self.save_action.setEnabled(editable and self.dirty and not self._invalid)

    def _render_plan(self, *_):
        if not hasattr(self, 'sql_preview'): return
        try:
            if self.preview_mode.currentIndex() == 0:
                text = self._plan.command_script if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成视图命令…'
            else:
                text = self._plan.command_text if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成视图命令…'
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.sql_preview.setPlainText(text)

    def request_save(self):
        if self.definition is None or not self.dirty or self.loading or self._read_only: return
        try: definition = self.draft()
        except DatabaseError as error: self.show_error(error); return
        self.preview_timer.stop()
        if self.suppress_save_preview and self.snapshot.exists:
            self.saveRequested.emit(definition); return
        if self._save_dialog is not None: self._save_dialog.raise_(); return
        dialog = MongoViewSaveDialog(definition, not self.snapshot.exists, self)
        dialog.suppress_preview_check.setChecked(self.suppress_save_preview)
        self._save_dialog = dialog
        dialog.previewRequested.connect(self.previewRequested); dialog.accepted.connect(self._save_confirmed)
        dialog.finished.connect(lambda *_: self._sheet_finished(dialog)); dialog.open()
        QTimer.singleShot(0, dialog.request_preview)

    def show_error(self, error):
        error = DatabaseError(collection_error_text(error))
        super().show_error(error)
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.sql_preview.setPlainText('无法安全生成视图命令：\n' + str(error))
