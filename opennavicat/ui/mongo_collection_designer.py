"""Ordinary MongoDB collection designer with owner-queue-only catalog I/O."""
from __future__ import annotations

from copy import deepcopy
import base64

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from bson import BSON

from opennavicat.database import DatabaseError
from opennavicat.mongodb_collections import CollectionApplyError
from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps, canonical_loads

from .editor import SqlEditor
from .schema_object_designer import _ObjectDesignerPage
from .table_designer import TableSaveDialog


def collection_error_text(error):
    """Retain acknowledgement and observation facts for collection failures."""
    if not isinstance(error, CollectionApplyError):
        return str(error)
    labels = {
        'not_started': '未执行', 'acknowledged': '服务器已确认',
        'server_error': '服务器报错', 'unknown': '确认未知', 'no_change': '无更改',
    }
    outcome = error.outcome
    lines = [str(error), f"操作：{outcome.operation}；状态：{labels.get(outcome.status, outcome.status)}"]
    if outcome.cancelled:
        lines.append('取消：已请求；已确认的数据库变更不会自动回滚。')
    if outcome.reply is not None:
        try:
            lines.append('服务器回执：\n' + canonical_dumps(outcome.reply))
        except Exception:
            lines.append('服务器回执无法安全显示为 Canonical Extended JSON。')
    if outcome.observation_error:
        lines.append('重新读取集合状态失败：' + outcome.observation_error)
    if outcome.collection_absent is not None:
        lines.append('集合已不存在：' + ('是' if outcome.collection_absent else '否'))
    if outcome.remaining_documents is not None:
        lines.append(f'重新读取的估算文档数：{outcome.remaining_documents}')
    if outcome.copied_documents is not None:
        lines.append(f'复制后的估算文档数：{outcome.copied_documents}')
    if outcome.staging_name:
        lines.append('可能保留的 staging 集合：' + outcome.staging_name)
    observed = outcome.observed
    if observed is not None:
        lines.append(f"观察到的集合：{observed.schema}.{observed.name}")
        if observed.issues:
            lines.append('只读原因：' + '；'.join(observed.issues))
    return '\n\n'.join(lines)


class MongoCollectionCopyDialog(QDialog):
    """Staged copy confirmation with explicit non-atomic and staging warnings."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = deepcopy(plan)
        self.setWindowTitle('复制集合')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        mode = '结构和数据' if plan.mode == 'data' else '仅结构'
        label = QLabel(f"复制集合 {plan.snapshot.schema}.{plan.snapshot.name} 到 {plan.snapshot.schema}.{plan.target_name}（{mode}）？")
        label.setWordWrap(True); layout.addWidget(label)
        warnings = QLabel('\n'.join(plan.warnings)); warnings.setWordWrap(True); layout.addWidget(warnings)
        self.preview = SqlEditor(); self.preview.set_language('text'); self.preview.setReadOnly(True)
        try: self.preview.setPlainText(plan.command_script)
        except DatabaseError as error: self.preview.setPlainText('无法安全生成执行脚本：\n' + str(error) + '\n\n' + plan.command_text)
        layout.addWidget(self.preview, 1)
        self.ack_check = QCheckBox('我了解复制不是快照或事务；失败后可能保留 staging 集合，且不会覆盖已存在的目标')
        layout.addWidget(self.ack_check)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.copy_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.copy_button.setText('复制'); self.copy_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.ack_check.toggled.connect(self.copy_button.setEnabled)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.ack_check.isChecked(): super().accept()


class MongoCollectionOperationDialog(QDialog):
    """Permanent drop/clear confirmation with the exact sealed command."""

    def __init__(self, plan, operation, parent=None):
        super().__init__(parent)
        self.plan = deepcopy(plan)
        self.operation = operation
        drop = operation == 'drop'
        clear = operation == 'clear'
        rename = operation == 'rename'
        self.setWindowTitle('删除集合' if drop else '清空集合' if clear else '重命名集合')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 500)
        layout = QVBoxLayout(self)
        subject = plan.snapshot.schema + '.' + plan.snapshot.name
        if rename:
            label_text = '将 ' + subject + ' 重命名为 ' + plan.snapshot.schema + '.' + plan.definition.name + '？'
        else:
            label_text = ('永久删除集合 ' if drop else '永久清空集合 ') + subject + '？'
        label = QLabel(label_text)
        label.setWordWrap(True)
        layout.addWidget(label)
        warnings = QLabel('\n'.join(plan.warnings))
        warnings.setWordWrap(True)
        layout.addWidget(warnings)
        self.preview = SqlEditor()
        self.preview.set_language('text')
        self.preview.setReadOnly(True)
        try:
            self.preview.setPlainText(plan.command_script)
        except DatabaseError as error:
            self.preview.setPlainText('无法生成执行脚本：\n' + str(error) + '\n\n' + plan.command_text)
        layout.addWidget(self.preview, 1)
        self.permanent_check = QCheckBox(
            '我了解此操作是永久性的、不会自动回滚，并且不会删除操作后由其他客户端重新创建的同名集合'
            if drop else
            '我了解此操作会永久删除集合中的所有文档，并可能删除最终检查后由其他客户端写入的文档；不会自动回滚'
            if clear else
            '我确认目标名称当前不存在；重命名后旧名称不再可用，dropTarget 始终为 false')
        layout.addWidget(self.permanent_check)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm_button.setText('删除' if drop else '清空' if clear else '重命名')
        self.confirm_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.permanent_check.toggled.connect(self.confirm_button.setEnabled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.plan is not None and self.permanent_check.isChecked():
            super().accept()


class MongoCollectionSaveDialog(TableSaveDialog):
    """The collection save sheet uses exact command preview, not SQL."""

    def __init__(self, definition, is_new=False, parent=None):
        super().__init__(definition, is_new, parent)
        self.setWindowTitle('保存集合')
        for form in self.findChildren(QFormLayout):
            label = form.labelForField(self.name_edit)
            if label is not None:
                label.setText('集合名称')
        for label in self.findChildren(QLabel):
            if label.text() == 'SQL 预览':
                label.setText('脚本预览')
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(['执行脚本（集合 UUID）', 'BSON 命令'])
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
        self.preview.setPlainText('正在生成集合命令…')
        self.previewRequested.emit(deepcopy(self.definition))


class MongoCollectionDesignerPage(_ObjectDesignerPage):
    """Detached ordinary-collection draft bound to a MainWindow owner queue."""

    kind = 'mongo_collection'
    indexesRequested = Signal()
    newIndexRequested = Signal()
    editIndexRequested = Signal(str)
    deleteIndexRequested = Signal(object)

    def __init__(self, connection_id, schema='', name='', parent=None):
        super().__init__(connection_id, schema, name, parent)
        self._initial_name = name
        self._invalid = {}
        self.index_rows = []
        self.toolbar.addSeparator()

        self.general = QWidget()
        general = QVBoxLayout(self.general)
        general.setContentsMargins(12, 12, 12, 12)
        form = self._form()
        self.name_edit = QLineEdit()
        self.name_edit.setMaximumWidth(360)
        self.name_edit.textChanged.connect(self._name_changed)
        self.capped_check = QCheckBox('固定大小集合（Capped）')
        self.capped_check.setObjectName('cappedCollectionCheckbox')
        self.capped_check.toggled.connect(self._capped_changed)
        self.size_edit = QLineEdit()
        self.size_edit.setMaximumWidth(260)
        self.size_edit.textChanged.connect(lambda value: self._integer_changed('size', value))
        self.max_edit = QLineEdit()
        self.max_edit.setMaximumWidth(260)
        self.max_edit.textChanged.connect(lambda value: self._integer_changed('max', value))
        form.addRow('集合名称', self.name_edit)
        form.addRow('', self.capped_check)
        form.addRow('最大大小（字节）', self.size_edit)
        form.addRow('最大文档数', self.max_edit)
        general.addLayout(form)
        note = QLabel('Capped 大小按 256 字节整数倍编辑；MongoDB 会按服务端规则处理该值。')
        note.setWordWrap(True)
        general.addWidget(note)
        general.addStretch()
        self.tabs.addTab(self.general, '集合选项')

        self.validation = QWidget()
        validation = QVBoxLayout(self.validation)
        validation.setContentsMargins(12, 12, 12, 12)
        form = self._form()
        self.level_combo = self._choice(
            form, '验证级别', [('服务器默认', None), ('关闭（off）', 'off'),
                           ('严格（strict）', 'strict'), ('适度（moderate）', 'moderate')])
        self.action_combo = self._choice(
            form, '验证动作', [('服务器默认', None), ('警告（warn）', 'warn'), ('错误（error）', 'error')])
        validation.addLayout(form)
        validation.addWidget(QLabel('验证器（Canonical Extended JSON 文档）'))
        self.validator_editor = self._document_editor()
        self.validator_editor.textChanged.connect(
            lambda: self._document_changed('validator', self.validator_editor))
        validation.addWidget(self.validator_editor, 1)
        self.tabs.addTab(self.validation, '验证')

        self.collation = QWidget()
        collation = QVBoxLayout(self.collation)
        collation.setContentsMargins(12, 12, 12, 12)
        collation.addWidget(QLabel('排序规则（Canonical Extended JSON 文档）'))
        self.collation_editor = self._document_editor()
        self.collation_editor.textChanged.connect(
            lambda: self._document_changed('collation', self.collation_editor))
        collation.addWidget(self.collation_editor, 1)
        self.tabs.addTab(self.collation, '排序规则')

        self.indexes = QWidget()
        indexes = QVBoxLayout(self.indexes)
        indexes.setContentsMargins(12, 12, 12, 12)
        index_bar = QHBoxLayout()
        self.new_index_button = QPushButton('新建索引')
        self.edit_index_button = QPushButton('设计索引')
        self.delete_index_button = QPushButton('删除索引')
        self.refresh_index_button = QPushButton('刷新')
        index_bar.addWidget(self.new_index_button); index_bar.addWidget(self.edit_index_button)
        index_bar.addWidget(self.delete_index_button); index_bar.addWidget(self.refresh_index_button)
        index_bar.addStretch(); indexes.addLayout(index_bar)
        self.index_table = QTableWidget(0, 8)
        self.index_table.setObjectName('mongoCollectionIndexes')
        self.index_table.setHorizontalHeaderLabels(
            ['名称', '字段', '唯一键', '背景', '稀疏', 'TTL', 'Bucket Size', '版本'])
        self.index_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.index_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.index_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.index_table.verticalHeader().hide()
        self.index_table.horizontalHeader().setStretchLastSection(True)
        indexes.addWidget(self.index_table, 1)
        self.index_message = QLabel(); self.index_message.setWordWrap(True); self.index_message.hide()
        indexes.addWidget(self.index_message)
        indexes.addWidget(QLabel('索引通过独立索引设计器保存；集合选项保存不会修改索引。'))
        self.new_index_button.clicked.connect(lambda: self.newIndexRequested.emit())
        self.edit_index_button.clicked.connect(self._edit_index)
        self.delete_index_button.clicked.connect(self._delete_index)
        self.refresh_index_button.clicked.connect(lambda: self.indexesRequested.emit())
        self.index_table.itemSelectionChanged.connect(self._update_index_buttons)
        self.tabs.addTab(self.indexes, '索引')

        self.raw = QWidget()
        raw = QVBoxLayout(self.raw)
        raw.setContentsMargins(12, 12, 12, 12)
        raw.addWidget(QLabel('服务器原始 catalog BSON（只读，仅用于保留未支持选项）'))
        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setReadOnly(True)
        raw.addWidget(self.raw_editor, 1)
        self.tabs.addTab(self.raw, '原始定义')

        self._finish_ui()
        self.tabs.setTabText(self.tabs.count() - 1, '脚本预览')
        self.sql_preview.set_language('text')
        self.preview_mode.clear()
        self.preview_mode.addItems(['执行脚本（集合 UUID）', 'BSON 命令'])
        self.preview_mode.setMinimumWidth(0)
        self.preview_mode.setMaximumWidth(16777215)
        self.preview_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        return form

    @staticmethod
    def _document_editor():
        editor = SqlEditor()
        editor.set_language('text')
        editor.setMinimumHeight(150)
        return editor

    def _choice(self, form, label, values):
        combo = QComboBox()
        combo.setMaximumWidth(260)
        for text, value in values:
            combo.addItem(text, value)
        form.addRow(label, combo)
        combo.currentIndexChanged.connect(lambda *_, c=combo: self._choice_changed(c))
        return combo

    @property
    def table(self):
        return self.definition.name if self.definition is not None else self._initial_name

    @property
    def collection(self):
        return self.table

    def set_indexes(self, rows):
        self.index_message.clear(); self.index_message.hide()
        self.index_rows = [deepcopy(row) for row in rows]
        self.index_table.setRowCount(0)
        for row in self.index_rows:
            values = [row.get('name', '')]
            keys = row.get('keys') or [(key, None) for key in row.get('columns', [])]
            values.append(', '.join(f'{key}: {direction}' for key, direction in keys))
            options = row.get('options') or {}
            values.extend([
                '是' if row.get('unique') else '否',
                '是' if options.get('background') else '否',
                '是' if options.get('sparse') else '否',
                options.get('expireAfterSeconds', ''),
                options.get('bucketSize', ''),
                options.get('v', ''),
            ])
            table_row = self.index_table.rowCount()
            self.index_table.insertRow(table_row)
            for column, value in enumerate(values):
                self.index_table.setItem(table_row, column, QTableWidgetItem(str(value)))
            self.index_table.item(table_row, 0).setData(Qt.ItemDataRole.UserRole, deepcopy(row))
        self._update_index_buttons()

    def set_index_error(self, error):
        self.index_message.setText('索引读取失败：' + str(error))
        self.index_message.show()
        self._update_index_buttons()

    def _selected_index(self):
        row = self.index_table.currentRow()
        item = self.index_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _edit_index(self):
        row = self._selected_index()
        if row is not None and not self._read_only and not row.get('system'):
            self.editIndexRequested.emit(row.get('name', ''))

    def _delete_index(self):
        row = self._selected_index()
        if row is not None and not self._read_only and not row.get('system'):
            self.deleteIndexRequested.emit(deepcopy(row))

    def _update_index_buttons(self):
        selected = self._selected_index()
        editable = bool(self.snapshot and self.snapshot.exists and not self._read_only and not self.loading)
        self.new_index_button.setEnabled(editable)
        self.edit_index_button.setEnabled(bool(editable and selected and not selected.get('system')))
        self.delete_index_button.setEnabled(bool(editable and selected and not selected.get('system')))
        self.refresh_index_button.setEnabled(bool(self.snapshot and self.snapshot.exists and not self.loading))

    def _choice_changed(self, combo):
        if self._populating or self.definition is None or self._read_only:
            return
        if combo is self.level_combo:
            self._set_option('validationLevel', combo.currentData())
        elif combo is self.action_combo:
            self._set_option('validationAction', combo.currentData())

    def _name_changed(self, value):
        if self._populating or self.definition is None or self._read_only or self.snapshot is None or self.snapshot.exists:
            return
        self.definition.name = value
        self._invalid.pop('name', None)
        if not value:
            self._invalid['name'] = '请输入集合名称。'
        self._edited()

    def _capped_changed(self, checked):
        if self._populating or self.definition is None or self._read_only:
            return
        if checked:
            self.definition.options['capped'] = True
        else:
            for key in ('capped', 'size', 'max'):
                self.definition.options.pop(key, None)
            self._populating = True
            self.size_edit.clear()
            self.max_edit.clear()
            self._populating = False
        self._invalid.pop('capped', None)
        self._edited()

    def _integer_changed(self, key, value):
        if self._populating or self.definition is None or self._read_only:
            return
        text = value.strip()
        self._invalid.pop(key, None)
        if not text:
            self.definition.options.pop(key, None)
        elif not text.isdecimal() or int(text) < 1 or int(text) >= 2 ** 63:
            self._invalid[key] = key + ' 必须是正的 BSON 整数。'
        elif key == 'size' and int(text) % 256:
            self._invalid[key] = 'Capped 大小必须是 256 字节的整数倍。'
        else:
            self.definition.options[key] = int(text)
        self._edited()

    def _document_changed(self, key, editor):
        if self._populating or self.definition is None or self._read_only:
            return
        text = editor.toPlainText()
        self._invalid.pop(key, None)
        if not text.strip():
            self.definition.options.pop(key, None)
        else:
            try:
                value = canonical_loads(text)
                if not isinstance(value, dict):
                    raise DatabaseError('需要 BSON 文档')
                self.definition.options[key] = value
            except Exception:
                self._invalid[key] = '请输入有效的 Canonical Extended JSON 文档：' + key
        self._edited()

    def _set_option(self, key, value):
        if value is None:
            self.definition.options.pop(key, None)
        else:
            self.definition.options[key] = value
        self._invalid.pop(key, None)
        self._edited()

    def _edited(self):
        super()._edited()
        if self._invalid:
            self.dirty = True
            self.dirtyChanged.emit(True)
            self.titleChanged.emit(self.title)
            self._update_controls()
            self.show_message('\n'.join(self._invalid.values()))
        elif self.definition is not None and not self._read_only and not self._populating:
            self.show_message('')

    def draft(self):
        if self._invalid:
            raise DatabaseError('\n'.join(self._invalid.values()))
        return super().draft()

    def _render_definition(self):
        self._invalid.clear()
        options = self.definition.options
        self.name_edit.setText(self.definition.name)
        self.capped_check.setChecked(bool(options.get('capped')))
        self.size_edit.setText(str(options.get('size', '')))
        self.max_edit.setText(str(options.get('max', '')))
        try:
            self.collation_editor.setPlainText(canonical_dumps(options['collation']) if 'collation' in options else '')
        except Exception:
            self.collation_editor.setPlainText('')
        try:
            self.validator_editor.setPlainText(canonical_dumps(options['validator']) if 'validator' in options else '')
        except Exception:
            self.validator_editor.setPlainText('')
        for combo, key in ((self.level_combo, 'validationLevel'), (self.action_combo, 'validationAction')):
            index = combo.findData(options.get(key))
            combo.setCurrentIndex(index if index >= 0 else 0)
        raw = self.snapshot.raw_spec if self.snapshot is not None else b''
        if not raw:
            self.raw_editor.setPlainText('新集合尚无服务器 catalog BSON。')
        else:
            try:
                self.raw_editor.setPlainText(canonical_dumps(BSON(raw).decode(codec_options=BSON_CODEC)))
            except Exception:
                self.raw_editor.setPlainText(
                    '原始 BSON 无法解码；以下 base64 仅用于只读保留：\n'
                    + base64.b64encode(raw).decode('ascii'))

    def _render_columns(self):
        pass

    def _update_specific_controls(self, editable):
        for widget in (self.validation, self.collation):
            widget.setEnabled(editable)
        self.name_edit.setReadOnly(bool(self.snapshot and self.snapshot.exists))
        self.size_edit.setEnabled(editable and self.capped_check.isChecked())
        self.max_edit.setEnabled(editable and self.capped_check.isChecked())
        if hasattr(self, 'save_action'):
            self.save_action.setEnabled(editable and self.dirty and not self._invalid)
        self._update_index_buttons()

    def _render_plan(self, *_):
        if not hasattr(self, 'sql_preview'):
            return
        try:
            if self.preview_mode.currentIndex() == 0:
                text = self._plan.command_script if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成集合命令…'
            else:
                text = self._plan.command_text if self._plan is not None and self._plan.changed else '无更改' if not self.dirty else '正在生成集合命令…'
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.sql_preview.setPlainText(text)

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
        dialog = MongoCollectionSaveDialog(definition, not self.snapshot.exists, self)
        dialog.suppress_preview_check.setChecked(self.suppress_save_preview)
        self._save_dialog = dialog
        dialog.previewRequested.connect(self.previewRequested)
        dialog.accepted.connect(self._save_confirmed)
        dialog.finished.connect(lambda *_: self._sheet_finished(dialog))
        dialog.open()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, dialog.request_preview)

    def show_error(self, error):
        error = DatabaseError(collection_error_text(error))
        super().show_error(error)
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.sql_preview.setPlainText('无法安全生成集合命令：\n' + str(error))
