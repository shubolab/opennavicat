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
from opennavicat.mongodb_collections import CollectionApplyError, CollectionPlan, validate_definition
from opennavicat.mongodb_collection_literals import parse_storage_option
from opennavicat.mongodb_indexes import (
    InlineIndexDraft, IndexDefinition, IndexKey, validate_definition as validate_index_definition,
)
from opennavicat.mongodb_index_script import bson_literal
from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps, canonical_loads

from .editor import SqlEditor
from .icons import tool_icon
from .schema_object_designer import _ObjectDesignerPage
from .table_designer import TableSaveDialog
from .mongo_validator import MongoValidatorEditor
from .mongo_inline_index import (
    InlineIndexKeyDialog, MongoInlineIndexSaveDialog, key_summary,
)


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
                label.setText('表名称:')
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
    inlineIndexPreviewRequested = Signal(object)
    inlineIndexSaveRequested = Signal(object)

    def __init__(self, connection_id, schema='', name='', parent=None):
        super().__init__(connection_id, schema, name, parent)
        self._initial_name = name
        self._invalid = {}
        self.index_rows = []
        self._inline_index_drafts = []
        self._inline_index_dirty = False
        self._inline_index_invalid = {}
        self._inline_index_plan = None
        self._inline_index_save_dialog = None
        self.toolbar.addSeparator()
        self.validator_add_action = self._action('添加属性', 'add', self._add_validator_property)
        self.validator_delete_action = self._action('删除属性', 'delete', self._delete_validator_property)

        self.general = QWidget()
        general = QVBoxLayout(self.general)
        general.setContentsMargins(12, 12, 12, 12)
        form = self._form()
        self.name_edit = QLineEdit(self)
        self.name_edit.hide()  # New names are entered in the observed save sheet.
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
            form, '验证级别', [('', None), ('off', 'off'), ('strict', 'strict'), ('moderate', 'moderate')])
        self.action_combo = self._choice(
            form, '验证动作', [('', None), ('error', 'error'), ('warn', 'warn')])
        validation.addLayout(form)
        self.validator_widget = MongoValidatorEditor()
        self.validator_editor = self.validator_widget.query_editor
        self.validator_widget.changed.connect(self._validator_changed)
        self.validator_widget.actionsChanged.connect(self._update_validator_actions)
        validation.addWidget(self.validator_widget, 1)
        self.tabs.addTab(self.validation, '验证')

        self.storage = QWidget()
        storage = QVBoxLayout(self.storage)
        storage.setContentsMargins(0, 0, 0, 0)
        self.storage_editor = self._document_editor()
        self.storage_editor.setObjectName('collectionStorageEngineEditor')
        self.storage_editor.textChanged.connect(
            lambda: self._storage_changed('storageEngine', self.storage_editor))
        storage.addWidget(self.storage_editor, 1)

        self.index_options = QWidget()
        index_options = QVBoxLayout(self.index_options)
        index_options.setContentsMargins(0, 0, 0, 0)
        self.index_options_editor = self._document_editor()
        self.index_options_editor.setObjectName('collectionIndexOptionDefaultsEditor')
        self.index_options_editor.textChanged.connect(
            lambda: self._storage_changed('indexOptionDefaults', self.index_options_editor))
        index_options.addWidget(self.index_options_editor, 1)

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
        self.delete_index_button = QPushButton('删除索引')
        index_bar.addWidget(self.new_index_button)
        index_bar.addWidget(self.delete_index_button)
        index_bar.addStretch(); indexes.addLayout(index_bar)
        self.index_table = QTableWidget(0, 3)
        self.index_table.setObjectName('mongoCollectionIndexes')
        self.index_table.setHorizontalHeaderLabels(['', '名称', '键'])
        self.index_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.index_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.index_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.index_table.verticalHeader().hide()
        self.index_table.horizontalHeader().setStretchLastSection(True)
        self.index_table.setColumnWidth(0, 26); self.index_table.setColumnWidth(1, 300)
        indexes.addWidget(self.index_table, 1)
        options_row = QHBoxLayout()
        self.unique_index_check = QCheckBox('唯一键')
        self.background_index_check = QCheckBox('背景')
        self.sparse_index_check = QCheckBox('稀疏')
        for widget in (self.unique_index_check, self.background_index_check, self.sparse_index_check):
            options_row.addWidget(widget)
        options_row.addStretch(); indexes.addLayout(options_row)
        ttl_row = QHBoxLayout()
        self.ttl_index_check = QCheckBox('在此之后过期（秒）:')
        self.ttl_index_edit = QLineEdit(); self.ttl_index_edit.setMaximumWidth(180)
        ttl_row.addWidget(self.ttl_index_check); ttl_row.addWidget(self.ttl_index_edit); ttl_row.addStretch()
        indexes.addLayout(ttl_row)
        bucket_row = QHBoxLayout()
        bucket_row.addWidget(QLabel('存储桶大小:'))
        self.bucket_size_edit = QLineEdit(); self.bucket_size_edit.setMaximumWidth(180)
        bucket_row.addWidget(self.bucket_size_edit); bucket_row.addStretch(); indexes.addLayout(bucket_row)
        self.index_message = QLabel(); self.index_message.setWordWrap(True); self.index_message.hide()
        indexes.addWidget(self.index_message)
        indexes.addWidget(QLabel('所有索引变更按表格顺序执行，整批不是事务；失败后不会自动回滚或重试。'))
        self.new_index_button.clicked.connect(self._new_inline_index)
        self.delete_index_button.clicked.connect(self._delete_inline_index)
        self.index_table.itemSelectionChanged.connect(self._index_selection_changed)
        self.unique_index_check.toggled.connect(lambda value: self._inline_index_option('unique', value))
        self.background_index_check.toggled.connect(lambda value: self._inline_index_option('background', value))
        self.sparse_index_check.toggled.connect(lambda value: self._inline_index_option('sparse', value))
        self.ttl_index_check.toggled.connect(self._inline_ttl_toggled)
        self.ttl_index_edit.editingFinished.connect(self._inline_ttl_changed)
        self.bucket_size_edit.editingFinished.connect(self._inline_bucket_changed)
        self.tabs.addTab(self.indexes, '索引')

        self.raw = QDialog(self)
        self.raw.setWindowTitle('原始 BSON（只读）')
        self.raw.resize(760, 540)
        raw = QVBoxLayout(self.raw)
        raw.setContentsMargins(12, 12, 12, 12)
        raw.addWidget(QLabel('服务器原始 catalog BSON（只读，仅用于保留未支持选项）'))
        self.raw_editor = QPlainTextEdit()
        self.raw_editor.setReadOnly(True)
        raw.addWidget(self.raw_editor, 1)
        raw_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        raw_buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        raw_buttons.rejected.connect(self.raw.reject)
        raw.addWidget(raw_buttons)
        self.raw_action = self._action('查看原始 BSON', 'query', self._show_raw)
        self.raw_action.setVisible(False)

        self._finish_ui()
        preview = self.tabs.widget(self.tabs.count() - 1)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for widget, label in (
            (self.indexes, '索引'), (self.validation, '验证'), (self.storage, '存储引擎'),
            (self.index_options, '索引选项'), (self.general, '集合选项'),
            (self.collation, '排序规则'), (preview, '脚本预览'),
        ):
            self.tabs.addTab(widget, label)
        self.tabs.setCurrentIndex(0)
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

    def load_snapshot(self, snapshot):
        if hasattr(self, '_inline_index_dirty'):
            self._inline_index_dirty = False
            self._inline_index_invalid.clear()
            self._inline_index_plan = None
            self._inline_index_save_dialog = None
        super().load_snapshot(snapshot)

    def set_indexes(self, rows):
        self.index_message.clear(); self.index_message.hide()
        self.index_rows = [deepcopy(row) for row in rows if row.get('name') != '_id_']
        if self._inline_index_dirty:
            self.index_message.setText('服务器索引已刷新；未保存的内嵌索引草稿已保留。')
            self.index_message.show()
            return
        collection = self.table
        drafts = []
        for row in self.index_rows:
            keys = row.get('keys') or [
                (field, -1 if direction == 'DESC' else direction) for field, direction in [(field, None) for field in row.get('columns', [])]
            ]
            parsed = []
            for field, order in keys:
                if order in (1, '1', 'ASC'):
                    order = 1
                elif order in (-1, '-1', 'DESC'):
                    order = -1
                parsed.append(IndexKey(str(field), order if order is not None else ''))
            options = deepcopy(row.get('options') or {})
            # session.indexes returns the complete listIndexes specification
            # plus normalized top-level columns. Structural identity is not an
            # editable index option.
            for structural in ('key', 'name', 'ns'):
                options.pop(structural, None)
            if row.get('unique') is True:
                options['unique'] = True
            definition = IndexDefinition(str(row.get('name', '')), collection, parsed, options)
            drafts.append(InlineIndexDraft(definition, str(row.get('name', '')), False))
        self._inline_index_drafts = drafts
        self._inline_index_dirty = False
        self._inline_index_invalid.clear()
        self._inline_index_plan = None
        self._render_inline_indexes()

    def set_index_error(self, error):
        self.index_message.setText('索引读取失败：' + str(error))
        self.index_message.show()
        self._update_index_buttons()

    def _visible_inline_drafts(self):
        return [draft for draft in self._inline_index_drafts if not draft.deleted]

    def _selected_inline_draft(self):
        row = self.index_table.currentRow()
        drafts = self._visible_inline_drafts()
        return drafts[row] if 0 <= row < len(drafts) else None

    def _render_inline_indexes(self):
        selected = self.index_table.currentRow()
        self._inline_index_populating = True
        try:
            self.index_table.setRowCount(0)
            for draft in self._visible_inline_drafts():
                row = self.index_table.rowCount()
                self.index_table.insertRow(row)
                icon = QTableWidgetItem()
                if draft.definition.keys:
                    icon.setIcon(tool_icon('index', 16))
                icon.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.index_table.setItem(row, 0, icon)
                name = QLineEdit(draft.definition.name)
                name.setObjectName('embeddedIndexName')
                name.editingFinished.connect(lambda draft=draft: self._inline_index_name_changed(draft))
                self.index_table.setCellWidget(row, 1, name)
                key_button = QPushButton(key_summary(draft.definition.keys) or '选择字段')
                key_button.setObjectName('embeddedIndexKeys')
                key_button.setFlat(True)
                key_button.setStyleSheet('text-align:left; padding:2px 6px;')
                key_button.clicked.connect(lambda _=False, draft=draft: self._open_inline_keys(draft))
                self.index_table.setCellWidget(row, 2, key_button)
            if self.index_table.rowCount():
                self.index_table.selectRow(min(max(selected, 0), self.index_table.rowCount() - 1))
        finally:
            self._inline_index_populating = False
        self._index_selection_changed()

    def _mark_inline_indexes_edited(self):
        self._inline_index_dirty = True
        self._inline_index_plan = None
        self._validate_inline_indexes()
        self.dirty = True
        self.dirtyChanged.emit(True)
        self.titleChanged.emit(self.title)
        self._update_controls()
        self._render_plan()
        if self._inline_index_invalid:
            self.show_message('\n'.join(self._inline_index_invalid.values()))
        else:
            self.show_message('')

    def _validate_inline_indexes(self):
        ui_errors = {key: value for key, value in self._inline_index_invalid.items()
                     if key in {'ttl', 'bucketSize'}}
        self._inline_index_invalid.clear()
        self._inline_index_invalid.update(ui_errors)
        names = []
        for draft in self._visible_inline_drafts():
            definition = draft.definition
            try:
                validate_index_definition(definition)
                names.append(definition.name)
            except DatabaseError as error:
                label = definition.name.strip() or '新索引'
                self._inline_index_invalid[label] = label + '：' + str(error)
        if len(names) != len(set(names)):
            self._inline_index_invalid['duplicate'] = '索引名称必须唯一。'
        return not self._inline_index_invalid

    def _new_inline_index(self):
        if not self.snapshot or not self.snapshot.exists or self._read_only or self.loading:
            return
        draft = InlineIndexDraft(IndexDefinition('', self.table, [], {}), '', False)
        self._inline_index_drafts.append(draft)
        self._render_inline_indexes()
        self.index_table.selectRow(self.index_table.rowCount() - 1)
        self._mark_inline_indexes_edited()

    def _delete_inline_index(self):
        draft = self._selected_inline_draft()
        if draft is None or self._read_only or self.loading:
            return
        if draft.original_name:
            draft.deleted = True
        else:
            self._inline_index_drafts.remove(draft)
        self._render_inline_indexes()
        self._mark_inline_indexes_edited()

    def _inline_index_name_changed(self, draft):
        if self._inline_index_populating or self._read_only:
            return
        row = self._visible_inline_drafts().index(draft)
        widget = self.index_table.cellWidget(row, 1)
        if widget is None:
            return
        name = widget.text().strip()
        if draft.definition.name != name:
            draft.definition.name = name
            self._mark_inline_indexes_edited()
            self.index_table.selectRow(row)

    def _open_inline_keys(self, draft):
        if self._read_only:
            return
        dialog = InlineIndexKeyDialog(draft.definition, self)
        self._inline_key_dialog = dialog
        def accepted():
            draft.definition.keys = deepcopy(dialog.definition.keys)
            self._render_inline_indexes()
            row = self._visible_inline_drafts().index(draft)
            self.index_table.selectRow(row)
            self._mark_inline_indexes_edited()
        dialog.accepted.connect(accepted)
        dialog.finished.connect(lambda *_: self._inline_key_finished(dialog))
        dialog.open()

    def _inline_key_finished(self, dialog):
        if getattr(self, '_inline_key_dialog', None) is dialog:
            self._inline_key_dialog = None
        dialog.deleteLater()

    def _index_selection_changed(self):
        if getattr(self, '_inline_index_populating', False):
            return
        draft = self._selected_inline_draft()
        editable = bool(draft and not self._read_only and not self.loading)
        options = draft.definition.options if draft else {}
        self._inline_index_populating = True
        try:
            self.unique_index_check.setChecked(bool(options.get('unique')))
            self.background_index_check.setChecked(bool(options.get('background')))
            self.sparse_index_check.setChecked(bool(options.get('sparse')))
            has_ttl = 'expireAfterSeconds' in options
            self.ttl_index_check.setChecked(has_ttl)
            self.ttl_index_edit.setText(str(options.get('expireAfterSeconds', '')))
            self.bucket_size_edit.setText(str(options.get('bucketSize', '')))
        finally:
            self._inline_index_populating = False
        for widget in (self.unique_index_check, self.background_index_check,
                       self.sparse_index_check, self.ttl_index_check):
            widget.setEnabled(editable)
        self.ttl_index_edit.setEnabled(editable and self.ttl_index_check.isChecked())
        self.bucket_size_edit.setEnabled(editable)
        self._update_index_buttons()

    def _inline_index_option(self, key, value):
        if getattr(self, '_inline_index_populating', False) or self._read_only:
            return
        draft = self._selected_inline_draft()
        if draft is None:
            return
        if value:
            draft.definition.options[key] = True
        else:
            draft.definition.options.pop(key, None)
        self._mark_inline_indexes_edited()

    def _inline_ttl_toggled(self, checked):
        if getattr(self, '_inline_index_populating', False) or self._read_only:
            return
        draft = self._selected_inline_draft()
        if draft is None:
            return
        self.ttl_index_edit.setEnabled(checked)
        if not checked:
            draft.definition.options.pop('expireAfterSeconds', None)
            self._mark_inline_indexes_edited()
        elif self.ttl_index_edit.text().strip():
            self._inline_ttl_changed()

    def _inline_ttl_changed(self):
        if getattr(self, '_inline_index_populating', False) or self._read_only:
            return
        draft = self._selected_inline_draft()
        if draft is None:
            return
        text = self.ttl_index_edit.text().strip()
        self._inline_index_invalid.pop('ttl', None)
        draft.definition.options.pop('expireAfterSeconds', None)
        if self.ttl_index_check.isChecked():
            try:
                if not text.isdecimal():
                    raise ValueError
                draft.definition.options['expireAfterSeconds'] = int(text)
            except ValueError:
                self._inline_index_invalid['ttl'] = '过期时间必须是 0–2147483647 的整数秒。'
        self._mark_inline_indexes_edited()

    def _inline_bucket_changed(self):
        if getattr(self, '_inline_index_populating', False) or self._read_only:
            return
        draft = self._selected_inline_draft()
        if draft is None:
            return
        text = self.bucket_size_edit.text().strip()
        self._inline_index_invalid.pop('bucketSize', None)
        draft.definition.options.pop('bucketSize', None)
        if text:
            try:
                value = float(text)
                if not value > 0 or value != value or value == float('inf'):
                    raise ValueError
                draft.definition.options['bucketSize'] = int(value) if value.is_integer() else value
            except ValueError:
                self._inline_index_invalid['bucketSize'] = '存储桶大小必须是正数。'
        self._mark_inline_indexes_edited()

    def inline_index_drafts(self):
        if not self._validate_inline_indexes():
            raise DatabaseError('\n'.join(self._inline_index_invalid.values()))
        return deepcopy(self._inline_index_drafts)

    def _inline_index_values_changed(self):
        return self._inline_index_dirty

    def _update_index_buttons(self):
        editable = bool(self.snapshot and self.snapshot.exists and not self._read_only and not self.loading)
        self.new_index_button.setEnabled(editable)
        self.delete_index_button.setEnabled(bool(editable and self._selected_inline_draft()))

    def show_inline_index_plan(self, bundle):
        if self._inline_index_save_dialog is None:
            return
        if self._inline_index_save_dialog.drafts != self._visible_inline_drafts() and self._inline_index_save_dialog.drafts != self._inline_index_drafts:
            return
        self._inline_index_plan = deepcopy(bundle)
        self._inline_index_save_dialog.show_plan(bundle)

    def _inline_index_sheet_finished(self, dialog):
        if self._inline_index_save_dialog is dialog:
            self._inline_index_save_dialog = None
        dialog.deleteLater()

    def _inline_indexes_confirmed(self):
        dialog = self._inline_index_save_dialog
        if dialog is None or self._inline_index_plan is None or not self._inline_index_plan.changed:
            return
        self.inlineIndexSaveRequested.emit(deepcopy(self._inline_index_plan))

    def inline_indexes_saved(self):
        self._inline_index_dirty = False
        self._inline_index_plan = None
        self._inline_index_invalid.clear()
        self.dirty = False
        self.dirtyChanged.emit(False)
        self._update_controls()

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

    def _storage_changed(self, key, editor):
        if (self._populating or self.definition is None or self._read_only
                or self.snapshot is None or self.snapshot.exists):
            return
        self._invalid.pop(key, None)
        try:
            value = parse_storage_option(editor.toPlainText(), key)
            if value is None:
                self.definition.options.pop(key, None)
            else:
                self.definition.options[key] = value
        except DatabaseError as error:
            self._invalid[key] = str(error)
        self._edited()

    def _validator_changed(self):
        if self._populating or self.definition is None or self._read_only:
            return
        self._invalid.pop('validator', None)
        try:
            value = self.validator_widget.validator()
            if value is None:
                self.definition.options.pop('validator', None)
            else:
                self.definition.options['validator'] = value
        except DatabaseError as error:
            self._invalid['validator'] = str(error)
        self._edited()

    def _update_validator_actions(self):
        if not hasattr(self, 'validator_widget'):
            return
        visible = self.tabs.currentWidget() is self.validation and self.validator_widget.mode == 'schema'
        self.validator_add_action.setVisible(visible)
        self.validator_delete_action.setVisible(visible)
        editable = self.definition is not None and not self.loading and not self._read_only
        self.validator_add_action.setEnabled(editable and self.validator_widget.can_add)
        self.validator_delete_action.setEnabled(editable and self.validator_widget.can_delete)

    def _add_validator_property(self):
        self.validator_widget.add_property()

    def _delete_validator_property(self):
        self.validator_widget.delete_property()

    def _show_raw(self):
        if self.raw_action.isVisible():
            self.raw.show()
            self.raw.raise_()

    def _set_option(self, key, value):
        if value is None:
            self.definition.options.pop(key, None)
        else:
            self.definition.options[key] = value
        self._invalid.pop(key, None)
        self._edited()

    def _edited(self):
        super()._edited()
        invalid = dict(self._invalid)
        invalid.update(self._inline_index_invalid)
        if invalid:
            self.dirty = True
            self.dirtyChanged.emit(True)
            self.titleChanged.emit(self.title)
            self._update_controls()
            self.show_message('\n'.join(invalid.values()))
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
        self.validator_widget.load_validator(options.get('validator'))
        for key, editor in (('storageEngine', self.storage_editor), ('indexOptionDefaults', self.index_options_editor)):
            try:
                editor.setPlainText(canonical_dumps(options[key]) if key in options else '')
            except Exception:
                editor.setPlainText('')
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
        self.validator_widget.set_read_only(not editable)
        self.name_edit.setReadOnly(bool(self.snapshot and self.snapshot.exists))
        new = bool(self.snapshot and not self.snapshot.exists)
        self.capped_check.setEnabled(editable and new)
        self.size_edit.setEnabled(editable and new and self.capped_check.isChecked())
        self.max_edit.setEnabled(editable and new and self.capped_check.isChecked())
        for editor in (self.storage_editor, self.index_options_editor):
            editor.setReadOnly(not editable or not new)
        self.raw_action.setVisible(bool(self.snapshot and self.snapshot.raw_spec and
                                       (self.snapshot.read_only or self.snapshot.issues or
                                        self.validator_widget._query_unrenderable)))
        if hasattr(self, 'save_action'):
            self.save_action.setEnabled(editable and self.dirty and not self._invalid and not self._inline_index_invalid)
        self._update_index_buttons()
        self._update_validator_actions()

    def _render_plan(self, *_):
        if not hasattr(self, 'sql_preview'):
            return
        if self._inline_index_dirty and not self._collection_options_changed():
            text = ('索引变更将按表格顺序分别生成 drop/create 命令；确认后由串行 worker 执行。'
                    if self.preview_mode.currentIndex() == 0 else
                    '索引 BSON 命令在保存脚本页中按表格顺序显示。')
            self.sql_preview.setPlainText(text)
            return
        try:
            plan = self._plan
            unnamed_script = None
            if self.definition is not None and self.snapshot is not None and not self.snapshot.exists and not self.definition.name:
                # The reference previews an unnamed draft as Untitled. This is
                # a text-only projection: it is never sealed, emitted to the
                # database queue, assigned to _plan or accepted for saving.
                definition = self.draft()
                definition.name = 'Untitled'
                validate_definition(definition)
                plan = CollectionPlan(self.snapshot, definition,
                    [{'create': definition.name, **deepcopy(definition.options)}], True)
                unnamed_script = 'db.createCollection(' + bson_literal(definition.name)
                if definition.options:
                    unnamed_script += ', ' + bson_literal(definition.options)
                unnamed_script += ')'
            if self.preview_mode.currentIndex() == 0:
                text = unnamed_script if unnamed_script is not None else plan.command_script if plan is not None and plan.changed else '无更改' if not self.dirty else '正在生成集合命令…'
            else:
                text = plan.command_text if plan is not None and plan.changed else '无更改' if not self.dirty else '正在生成集合命令…'
        except DatabaseError as error:
            text = '无法安全生成执行脚本：\n' + str(error)
        self.sql_preview.setPlainText(text)

    def _collection_options_changed(self):
        if self.snapshot is None:
            return True
        try:
            return self.draft() != self.snapshot.definition
        except DatabaseError:
            return True

    def request_preview(self):
        if self._inline_index_dirty and not self._collection_options_changed():
            self._render_plan()
            return
        if self.definition is not None and self.snapshot is not None and not self.snapshot.exists and not self.definition.name:
            self._render_plan()
            return
        super().request_preview()

    def request_save(self):
        if self.definition is None or not self.dirty or self.loading or self._read_only:
            return
        if self._inline_index_dirty and self._collection_options_changed():
            self.show_error(DatabaseError('请分别保存集合选项和嵌入式索引；当前实现不会把两类 DDL 合并成一个事务。'))
            return
        if self._inline_index_dirty:
            if not self.snapshot or not self.snapshot.exists:
                self.show_error(DatabaseError('请先创建集合并重新打开设计器，再保存索引。'))
                return
            try:
                drafts = self.inline_index_drafts()
            except DatabaseError as error:
                self.show_error(error)
                return
            if self._inline_index_save_dialog is not None:
                self._inline_index_save_dialog.raise_()
                return
            dialog = MongoInlineIndexSaveDialog(drafts, self)
            self._inline_index_save_dialog = dialog
            dialog.previewRequested.connect(self.inlineIndexPreviewRequested)
            dialog.accepted.connect(self._inline_indexes_confirmed)
            dialog.finished.connect(lambda *_: self._inline_index_sheet_finished(dialog))
            dialog.open()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, dialog.request_preview)
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
        if self._inline_index_save_dialog is not None:
            self._inline_index_save_dialog.show_error(error)
            return
        error = DatabaseError(collection_error_text(error))
        super().show_error(error)
        if self.tabs.currentIndex() == self.tabs.count() - 1:
            self.sql_preview.setPlainText('无法安全生成集合命令：\n' + str(error))
