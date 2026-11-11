"""Time-series collection designer with owner-queue-only catalog I/O."""
from __future__ import annotations

from copy import deepcopy
import base64

from bson import BSON
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps

from .editor import SqlEditor
from .mongo_collection_designer import collection_error_text
from .schema_object_designer import _ObjectDesignerPage
from .table_designer import TableSaveDialog


class MongoTimeSeriesSaveDialog(TableSaveDialog):
    def __init__(self, definition, is_new=False, parent=None):
        super().__init__(definition, is_new, parent)
        self.setWindowTitle('保存时间序列集合')
        for form in self.findChildren(QFormLayout):
            label = form.labelForField(self.name_edit)
            if label is not None:
                label.setText('集合名称')
        for label in self.findChildren(QLabel):
            if label.text() == 'SQL 预览':
                label.setText('脚本预览')
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(['执行脚本', 'BSON 命令'])
        self.preview_mode.currentIndexChanged.connect(self._render_preview)
        self.layout().insertWidget(self.layout().indexOf(self.preview), self.preview_mode)
        self.suppress_preview_check.setText('不再显示脚本预览')
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.layout().insertWidget(self.layout().count() - 1, self.warning)

    def _render_preview(self, *_):
        if self.plan is None:
            return
        if not self.plan.changed:
            self.preview.setPlainText('无更改')
            return
        try:
            text = self.plan.command_script if self.preview_mode.currentIndex() == 0 else self.plan.command_text
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.preview.setPlainText(text)

    def show_plan(self, plan):
        if plan.definition != self.definition:
            return
        self.plan = plan
        self.save_button.setEnabled(bool(self.definition.name.strip() and plan.changed))
        self.warning.setText('\n'.join(plan.warnings))
        self._render_preview()

    def request_preview(self):
        self.plan = None
        self.save_button.setEnabled(False)
        if not self.definition.name.strip():
            self.preview.setPlainText('请输入集合名称。')
            return
        self.preview.setPlainText('正在生成时间序列命令…')
        self.previewRequested.emit(deepcopy(self.definition))


class MongoTimeSeriesDesignerPage(_ObjectDesignerPage):
    kind = 'mongo_timeseries'

    def __init__(self, connection_id, schema='', name='', parent=None):
        super().__init__(connection_id, schema, name, parent)
        self._initial_name = name
        self._invalid = {}
        self.toolbar.addSeparator()
        self.general = QWidget()
        layout = QVBoxLayout(self.general)
        layout.setContentsMargins(12, 12, 12, 12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.name_edit = QLineEdit(); self.name_edit.setMaximumWidth(360)
        self.time_field_edit = QLineEdit(); self.time_field_edit.setMaximumWidth(360)
        self.meta_field_edit = QLineEdit(); self.meta_field_edit.setMaximumWidth(360)
        self.granularity_combo = QComboBox(); self.granularity_combo.setMaximumWidth(260)
        for label, value in [('秒', 'seconds'), ('分钟', 'minutes'), ('小时', 'hours'), ('自定义桶', '')]:
            self.granularity_combo.addItem(label, value)
        self.rounding_edit = QLineEdit(); self.rounding_edit.setMaximumWidth(260)
        self.max_span_edit = QLineEdit(); self.max_span_edit.setMaximumWidth(260)
        self.expire_edit = QLineEdit(); self.expire_edit.setMaximumWidth(260)
        form.addRow('集合名称', self.name_edit)
        form.addRow('时间字段', self.time_field_edit)
        form.addRow('元数据字段', self.meta_field_edit)
        form.addRow('粒度', self.granularity_combo)
        form.addRow('桶舍入秒数', self.rounding_edit)
        form.addRow('最大桶跨度秒数', self.max_span_edit)
        form.addRow('过期秒数', self.expire_edit)
        layout.addLayout(form)
        note = QLabel('自定义 bucketing 必须同时填写且两个值相等；timeField/metaField 创建后不可修改。')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.tabs.addTab(self.general, '常规')

        self.raw = QWidget(); raw = QVBoxLayout(self.raw); raw.setContentsMargins(12, 12, 12, 12)
        raw.addWidget(QLabel('服务器原始 catalog BSON（只读）'))
        self.raw_editor = QPlainTextEdit(); self.raw_editor.setReadOnly(True)
        raw.addWidget(self.raw_editor, 1)
        self.tabs.addTab(self.raw, '原始定义')
        self._finish_ui()
        self.tabs.setTabText(self.tabs.count() - 1, '脚本预览')
        self.sql_preview.set_language('text')
        self.preview_mode.clear()
        self.preview_mode.addItems(['执行脚本', 'BSON 命令'])
        self.preview_mode.setMinimumWidth(0)
        self.preview_mode.setMaximumWidth(16777215)
        self.preview_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.name_edit.textChanged.connect(self._name_changed)
        self.time_field_edit.textChanged.connect(lambda value: self._field_changed('time_field', value))
        self.meta_field_edit.textChanged.connect(lambda value: self._field_changed('meta_field', value))
        self.granularity_combo.currentIndexChanged.connect(self._granularity_changed)
        self.rounding_edit.textChanged.connect(lambda value: self._integer_changed('bucket_rounding_seconds', value))
        self.max_span_edit.textChanged.connect(lambda value: self._integer_changed('bucket_max_span_seconds', value))
        self.expire_edit.textChanged.connect(lambda value: self._integer_changed('expire_after_seconds', value))

    @property
    def table(self):
        return self.definition.name if self.definition is not None else self._initial_name

    @property
    def collection(self):
        return self.table

    def _name_changed(self, value):
        if self._populating or self.definition is None or self._read_only or self.snapshot.exists:
            return
        self.definition.name = value
        self._invalid.pop('name', None)
        if not value:
            self._invalid['name'] = '请输入集合名称。'
        self._edited()

    def _field_changed(self, key, value):
        if self._populating or self.definition is None or self._read_only or self.snapshot.exists:
            return
        setattr(self.definition, key, value)
        self._invalid.pop(key, None)
        if key == 'time_field' and not value:
            self._invalid[key] = '时间字段不能为空。'
        if value == '_id':
            self._invalid[key] = '字段名不能是 _id。'
        if self.definition.meta_field and self.definition.meta_field == self.definition.time_field:
            self._invalid['meta_field'] = 'metaField 不能等于 timeField。'
        elif self.definition.meta_field != '':
            self._invalid.pop('meta_field', None)
        self._edited()

    def _granularity_changed(self, *_):
        if self._populating or self.definition is None or self._read_only:
            return
        value = self.granularity_combo.currentData()
        self.definition.granularity = value
        if value:
            self.definition.bucket_rounding_seconds = None
            self.definition.bucket_max_span_seconds = None
            self._populating = True
            self.rounding_edit.clear(); self.max_span_edit.clear()
            self._populating = False
        self._invalid.pop('granularity', None)
        if value:
            self._invalid.pop('bucket', None)
        self._edited()

    def _integer_changed(self, key, value):
        if self._populating or self.definition is None or self._read_only:
            return
        text = value.strip()
        self._invalid.pop(key, None)
        if not text:
            setattr(self.definition, key, None)
        elif not text.isdecimal() or int(text) < 1:
            self._invalid[key] = '该值必须是正整数。'
        else:
            setattr(self.definition, key, int(text))
        if not self.definition.granularity:
            if (self.definition.bucket_rounding_seconds is None) != (self.definition.bucket_max_span_seconds is None):
                self._invalid['bucket'] = '自定义 bucketing 必须同时填写两个值。'
            elif self.definition.bucket_rounding_seconds is not None and self.definition.bucket_rounding_seconds != self.definition.bucket_max_span_seconds:
                self._invalid['bucket'] = 'bucketRoundingSeconds 必须等于 bucketMaxSpanSeconds。'
            else:
                self._invalid.pop('bucket', None)
        self._edited()

    def _edited(self):
        super()._edited()
        if self._invalid:
            self.dirty = True
            self.dirtyChanged.emit(True); self.titleChanged.emit(self.title)
            self._update_controls()
            self.show_message('\n'.join(value for value in self._invalid.values() if value))
        elif self.definition is not None and not self._read_only and not self._populating:
            self.show_message('')

    def draft(self):
        if self._invalid:
            raise DatabaseError('\n'.join(value for value in self._invalid.values() if value))
        return super().draft()

    def _render_definition(self):
        self._invalid.clear()
        value = self.definition
        if not self.snapshot.exists and not value.time_field:
            self._invalid['time_field'] = '时间字段不能为空。'
        self.name_edit.setText(value.name)
        self.time_field_edit.setText(value.time_field)
        self.meta_field_edit.setText(value.meta_field)
        index = self.granularity_combo.findData(value.granularity)
        self.granularity_combo.setCurrentIndex(index if index >= 0 else self.granularity_combo.findData(''))
        self.rounding_edit.setText('' if value.bucket_rounding_seconds is None else str(value.bucket_rounding_seconds))
        self.max_span_edit.setText('' if value.bucket_max_span_seconds is None else str(value.bucket_max_span_seconds))
        self.expire_edit.setText('' if value.expire_after_seconds is None else str(value.expire_after_seconds))
        if self.snapshot.raw_spec:
            try:
                self.raw_editor.setPlainText(canonical_dumps(BSON(self.snapshot.raw_spec).decode(codec_options=BSON_CODEC)))
            except Exception:
                self.raw_editor.setPlainText('原始 BSON 无法解码；base64：\n' + base64.b64encode(self.snapshot.raw_spec).decode('ascii'))
        else:
            self.raw_editor.setPlainText('新集合尚无服务器 catalog BSON。')

    def _render_columns(self):
        pass

    def _update_specific_controls(self, editable):
        self.granularity_combo.setEnabled(editable)
        custom = self.granularity_combo.currentData() == ''
        self.rounding_edit.setEnabled(editable and custom)
        self.max_span_edit.setEnabled(editable and custom)
        self.expire_edit.setEnabled(editable)
        for widget in (self.name_edit, self.time_field_edit, self.meta_field_edit):
            widget.setReadOnly(bool(self.snapshot and self.snapshot.exists))
        if hasattr(self, 'save_action'):
            self.save_action.setEnabled(editable and self.dirty and not self._invalid)

    def _render_plan(self, *_):
        if not hasattr(self, 'sql_preview'):
            return
        try:
            if self.preview_mode.currentIndex() == 0:
                text = self._plan.command_script if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成时间序列命令…'
            else:
                text = self._plan.command_text if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成时间序列命令…'
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.sql_preview.setPlainText(text)

    def request_save(self):
        if self.definition is None or not self.dirty or self.loading or self._read_only:
            return
        try:
            definition = self.draft()
        except DatabaseError as error:
            self.show_error(error); return
        self.preview_timer.stop()
        if self.suppress_save_preview and self.snapshot.exists:
            self.saveRequested.emit(definition); return
        if self._save_dialog is not None:
            self._save_dialog.raise_(); return
        dialog = MongoTimeSeriesSaveDialog(definition, not self.snapshot.exists, self)
        dialog.suppress_preview_check.setChecked(self.suppress_save_preview)
        self._save_dialog = dialog
        dialog.previewRequested.connect(self.previewRequested)
        dialog.accepted.connect(self._save_confirmed)
        dialog.finished.connect(lambda *_: self._sheet_finished(dialog))
        dialog.open()
        QTimer.singleShot(0, dialog.request_preview)

    def show_error(self, error):
        error = DatabaseError(collection_error_text(error))
        super().show_error(error)
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.sql_preview.setPlainText('无法安全生成时间序列命令：\n' + str(error))
