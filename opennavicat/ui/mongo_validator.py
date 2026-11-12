"""Detached MongoDB validation pages backed by lossless BSON patches.

No widget opens a database or executes JavaScript. Schema and Query are separate
drafts. Raw fragment dialogs edit only their explicitly selected keyword.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from bson import BSON
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from opennavicat.database import DatabaseError
from opennavicat.mongodb_index_script import bson_literal
from opennavicat.mongodb_session import BSON_CODEC
from opennavicat.mongodb_validator import (
    AddProperty, RemoveKeyword, RemoveProperty, RenameProperty,
    ReplaceSchemaFragment, SetKeyword, ValidatorDraft,
)
from opennavicat.mongodb_validator_literals import (
    parse_numeric_constraint, parse_validator_literal, parse_validator_query,
)

from .editor import SqlEditor
from .icons import tool_icon


BSON_TYPES = (
    'array', 'binData', 'bool', 'date', 'dbPointer', 'decimal', 'double', 'int',
    'javascript', 'javascriptWithScope', 'long', 'maxKey', 'minKey', 'null',
    'number', 'object', 'objectId', 'regex', 'string', 'symbol', 'timestamp', 'undefined',
)
SCHEMA = ('$jsonSchema',)
MAX_PROPERTY_ROWS = 1000
NUMERIC_TYPES = {'decimal', 'double', 'int', 'long', 'number'}
ROOT_FIELDS = (
    ('maxProperties', '最大属性', 'number'), ('minProperties', '最小属性', 'number'),
    ('additionalProperties', '附加属性', 'fragment'), ('patternProperties', '模式属性', 'fragment'),
    ('dependencies', '依赖性', 'fragment'), ('title', '标题', 'fragment'), ('description', '描述', 'fragment'),
)


def _literal(value):
    try:
        return bson_literal(value)
    except DatabaseError:
        return None


def _bounded(value, limit=512):
    value = str(value)
    return value if len(value) <= limit else value[:limit - 1] + '…'


class ValidatorFragmentDialog(QDialog):
    def __init__(self, value=None, *, present=False, validate=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Window')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(680, 420)
        self.value, self.omitted = deepcopy(value), not present
        self.validate = validate
        box = QVBoxLayout(self)
        self.editor = SqlEditor(); self.editor.set_language('text')
        text = _literal(value) if present else ''
        self.editor.setPlainText(text if text is not None else '')
        self.editor.setReadOnly(text is None)
        box.addWidget(self.editor, 1)
        self.error = QLabel(); self.error.setWordWrap(True); box.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('好')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        box.addWidget(self.buttons)
        if text is None:
            self.error.setText('原始 BSON 值无法表示为已支持的脚本；该值保留只读。')
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def accept(self):
        if self.editor.isReadOnly():
            return
        try:
            text = self.editor.toPlainText()
            self.omitted = not text.strip()
            self.value = None if self.omitted else parse_validator_literal(text)
            if self.validate is not None:
                self.validate(self.value, self.omitted)
        except DatabaseError as error:
            self.error.setText(_bounded(error)); return
        super().accept()


class BsonTypesDialog(QDialog):
    def __init__(self, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('BSON 类型')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(360, 600)
        self.original = deepcopy(list(selected))
        self.selected = deepcopy(self.original)
        box = QVBoxLayout(self)
        self.table = QTableWidget(len(BSON_TYPES), 2)
        self.table.setHorizontalHeaderLabels(['', 'BSON 类型'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, name in enumerate(BSON_TYPES):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, item); self.table.setItem(row, 1, QTableWidgetItem(name))
        box.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.select_all = QPushButton('全选'); self.select_none = QPushButton('取消全选')
        row.addWidget(self.select_all); row.addWidget(self.select_none); row.addStretch(); box.addLayout(row)
        self.select_all.clicked.connect(lambda: self._check_all(True))
        self.select_none.clicked.connect(lambda: self._check_all(False))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('好')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); box.addWidget(self.buttons)

    def _check_all(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def accept(self):
        checked = [name for row, name in enumerate(BSON_TYPES)
                   if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]
        # Untouched existing order and unknown type aliases are retained. Opening
        # and accepting a picker cannot silently rewrite the original BSON array.
        self.selected = [name for name in self.original if name not in BSON_TYPES or name in checked]
        self.selected += [name for name in checked if name not in self.selected]
        super().accept()


@dataclass
class _Row:
    key: str | None
    schema: dict = field(default_factory=dict)
    required: bool = False
    inputs: dict = field(default_factory=dict)
    required_edited: bool = False


class MongoValidatorEditor(QWidget):
    changed = Signal()
    actionsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._populating = self._read_only = False
        self._schema_touched = False
        self._schema = ValidatorDraft({'$jsonSchema': {}})
        self._query = None
        self._invalid = {}
        self._root_inputs = {}
        self._visual_editable = True
        self._rows = []
        self._loaded = False
        self.fragment_dialog = self.types_dialog = None
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.mode_combo = QComboBox(); self.mode_combo.addItems(['JSON Schema', 'Query'])
        self.mode_combo.setMaximumWidth(260); form.addRow('验证符类型', self.mode_combo); box.addLayout(form)
        self.stack = QStackedWidget(); box.addWidget(self.stack, 1)
        self.schema_tabs = QTabWidget(); self.stack.addWidget(self.schema_tabs)
        self.properties_page = QWidget(); properties = QVBoxLayout(self.properties_page)
        properties.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName('mongoValidatorProperties')
        self.table.setHorizontalHeaderLabels(['', '名称', 'BSON 类型', '必需', '标题', '描述'])
        self.table.verticalHeader().hide(); self.table.setColumnWidth(0, 26)
        self.table.setColumnWidth(1, 180); self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 54); self.table.setColumnWidth(4, 180)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        split.addWidget(self.table)
        self.constraints = QWidget(); self.constraints_form = QFormLayout(self.constraints)
        split.addWidget(self.constraints); split.setStretchFactor(0, 2); split.setStretchFactor(1, 1)
        properties.addWidget(split, 1)
        self.schema_tabs.addTab(self.properties_page, '属性')
        self.root_page = QWidget(); self.root_form = QFormLayout(self.root_page)
        self.schema_tabs.addTab(self.root_page, '根模式')
        self.query_editor = SqlEditor(); self.query_editor.set_language('text'); self.stack.addWidget(self.query_editor)
        self.message = QLabel(); self.message.setWordWrap(True); self.message.hide(); box.addWidget(self.message)
        self.constraint_fields, self.root_fields = {}, {}
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.query_editor.textChanged.connect(self._query_changed)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.schema_tabs.currentChanged.connect(lambda *_: self.actionsChanged.emit())
        self.load_validator(None)

    @property
    def mode(self):
        return 'schema' if self.mode_combo.currentIndex() == 0 else 'query'

    @property
    def can_add(self):
        return not self._read_only and self._visual_editable and self.mode == 'schema' and self.schema_tabs.currentIndex() == 0 and len(self._rows) < MAX_PROPERTY_ROWS

    @property
    def can_delete(self):
        return not self._read_only and self.mode == 'schema' and self.schema_tabs.currentIndex() == 0 and 0 <= self.table.currentRow() < len(self._rows)

    def validator(self):
        active_errors = [value for key, value in self._invalid.items() if key.startswith(self.mode + ':')]
        if self.mode == 'schema' and any(row.key is None for row in self._rows):
            active_errors.append('请输入新属性的名称。')
        if active_errors:
            raise DatabaseError('\n'.join(active_errors))
        if self.mode == 'query':
            return deepcopy(self._query)
        return self._schema.document if self._schema_touched else None

    def load_validator(self, value):
        keep = False
        if self._loaded:
            try:
                keep = BSON.encode({'v': self.validator()}, codec_options=BSON_CODEC) == BSON.encode({'v': value}, codec_options=BSON_CODEC)
            except Exception:
                pass
        self._populating = True
        try:
            if not keep:
                try:
                    draft = ValidatorDraft(value if value is not None else {'$jsonSchema': {}})
                except DatabaseError:
                    # Existing valid-server rules can exceed the visual model's
                    # editing limits. Keep the entire original Query buffer;
                    # never turn a failed projection into an empty validator.
                    draft = None
                pure = draft is not None and draft.visual_schema is not None
                self._schema = draft if pure else ValidatorDraft({'$jsonSchema': {}})
                self._schema_touched = value is not None and pure
                self._query = None if pure else deepcopy(value)
                self.mode_combo.setCurrentIndex(0 if pure else 1)
            elif self.mode == 'schema':
                self._schema = ValidatorDraft(value if value is not None else {'$jsonSchema': {}})
                self._schema_touched = value is not None
            else:
                self._query = deepcopy(value)
            self._invalid.clear()
            self._root_inputs.clear()
            self._rows_from_model()
            self.query_editor.setPlainText(_literal(self._query) if self._query is not None and _literal(self._query) is not None else '')
            self._query_unrenderable = self._query is not None and _literal(self._query) is None
            self._populate_table()
            self._render_root()
            self.stack.setCurrentIndex(self.mode_combo.currentIndex())
            self._loaded = True
        finally:
            self._populating = False
        self._selection_changed(); self._refresh_message(); self.actionsChanged.emit()

    def set_read_only(self, value):
        self._read_only = bool(value)
        self.mode_combo.setEnabled(not value)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers if value else
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        before = self._populating; self._populating = True
        try:
            for row in range(self.table.rowCount()):
                combo = self.table.cellWidget(row, 2)
                if combo is not None: combo.setEnabled(not value)
                item = self.table.item(row, 3)
                if item is not None:
                    flags = item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable if value else item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                    if flags != item.flags(): item.setFlags(flags)
        finally:
            self._populating = before
        self.query_editor.setReadOnly(bool(value or self._query_unrenderable))
        self.root_page.setEnabled(not value); self.constraints.setEnabled(not value)
        self.actionsChanged.emit()

    def _rows_from_model(self):
        schema = self._schema.visual_schema or {}
        values = schema.get('properties', {})
        self._rows = []
        self._visual_editable = True
        if not isinstance(values, dict) or len(values) > MAX_PROPERTY_ROWS or any(not isinstance(v, dict) for v in values.values()):
            self._invalid['schema:projection'] = '属性映射超出可视编辑范围，原始规则已保留。'
            self._visual_editable = False
            return
        required = schema.get('required', [])
        for name, value in values.items():
            self._rows.append(_Row(name, deepcopy(value), isinstance(required, list) and name in required))

    def _populate_table(self, selected=-1):
        before = self._populating; self._populating = True
        try:
            self.table.setRowCount(0)
            for number, row in enumerate(self._rows):
                self.table.insertRow(number)
                icon = QTableWidgetItem(tool_icon('table', 14), '')
                icon.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable); self.table.setItem(number, 0, icon)
                for column, text in ((1, row.key or ''), (4, row.schema.get('title', '')), (5, row.schema.get('description', ''))):
                    item = QTableWidgetItem(text if type(text) is str else _bounded(text))
                    if type(text) is not str: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(number, column, item)
                required = QTableWidgetItem(); required.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                required.setCheckState(Qt.CheckState.Checked if row.required else Qt.CheckState.Unchecked)
                self.table.setItem(number, 3, required)
                combo = QComboBox(); combo.addItems([*BSON_TYPES, '(Multiple)'])
                value = row.schema.get('bsonType')
                combo.setCurrentIndex(len(BSON_TYPES) if isinstance(value, list) else combo.findText(value) if type(value) is str else -1)
                combo.setEnabled(not self._read_only)
                combo.currentIndexChanged.connect(lambda _, number=number, combo=combo: self._type_changed(number, combo.currentText()))
                self.table.setCellWidget(number, 2, combo)
            if 0 <= selected < len(self._rows): self.table.selectRow(selected)
        finally:
            self._populating = before

    def add_property(self):
        if not self.can_add: return
        self._rows.append(_Row(None))
        self._populate_table(len(self._rows) - 1); self._selection_changed(); self._notify()
        self.table.editItem(self.table.item(len(self._rows) - 1, 1))

    def delete_property(self):
        if not self.can_delete: return
        index = self.table.currentRow(); row = self._rows[index]
        if row.key is not None and not self._apply([RemoveProperty(SCHEMA, row.key)], notify=False): return
        del self._rows[index]
        self._invalid = {key: value for key, value in self._invalid.items() if ':' + str(id(row)) + ':' not in key}
        self._populate_table(min(index, len(self._rows) - 1)); self._selection_changed(); self._notify()

    def _apply(self, patches, *, notify=True, error_key='schema:operation'):
        try:
            self._schema.apply(patches)
        except DatabaseError as error:
            self._invalid[error_key] = _bounded(error); self._notify(); return False
        self._invalid.pop(error_key, None)
        self._schema_touched = True
        values = (self._schema.visual_schema or {}).get('properties', {})
        for row in self._rows:
            if row.key in values: row.schema = deepcopy(values[row.key])
        if notify: self._notify()
        return True

    def _notify(self):
        self._refresh_message(); self.changed.emit(); self.actionsChanged.emit()

    def _refresh_message(self):
        try:
            self.validator(); text = '原始 Query 无法表示为已支持的 BSON 脚本，原值已保留只读。' if self.mode == 'query' and getattr(self, '_query_unrenderable', False) else ''
        except DatabaseError as error:
            text = _bounded(error)
        self.message.setText(text); self.message.setVisible(bool(text))

    def _mode_changed(self, index):
        if self._populating: return
        self.stack.setCurrentIndex(index); self._notify()

    def _query_changed(self):
        if self._populating or self._read_only: return
        try:
            value = parse_validator_query(self.query_editor.toPlainText())
        except DatabaseError as error:
            self._invalid['query:literal'] = _bounded(error)
        else:
            self._query = value; self._invalid.pop('query:literal', None)
        self._notify()

    def _item_changed(self, item):
        if self._populating or self._read_only: return
        number, column = item.row(), item.column()
        if not 0 <= number < len(self._rows): return
        row = self._rows[number]
        if column == 1:
            value = item.text()
            error_key = 'schema:' + str(id(row)) + ':name'
            try:
                patch = AddProperty(SCHEMA, value, row.schema, required=row.required if row.required_edited else None) if row.key is None else RenameProperty(SCHEMA, row.key, value)
                if not value: raise DatabaseError('请输入属性名称。')
                if self._apply([patch], notify=False, error_key=error_key):
                    row.key = value; self._invalid.pop(error_key, None)
                    required = (self._schema.visual_schema or {}).get('required', [])
                    row.required = isinstance(required, list) and value in required
                    self._populating = True
                    self.table.item(number, 3).setCheckState(Qt.CheckState.Checked if row.required else Qt.CheckState.Unchecked)
                    self._populating = False
            except DatabaseError as error:
                self._invalid[error_key] = _bounded(error)
        elif column == 3:
            required = item.checkState() == Qt.CheckState.Checked
            if row.key is None: row.required = required; row.required_edited = True
            else:
                schema = self._schema.visual_schema
                names = schema.get('required', [])
                if not isinstance(names, list) or any(type(name) is not str for name in names):
                    self._invalid['schema:required'] = '现有必需规则不是字符串数组，原值已保留。'
                else:
                    names = list(names)
                    if required and row.key not in names: names.append(row.key)
                    if not required: names = [name for name in names if name != row.key]
                    if self._apply([SetKeyword((*SCHEMA, 'required'), names)], notify=False): row.required = required
        elif column in (4, 5):
            self._set_row_keyword(number, 'title' if column == 4 else 'description', item.text(), omit=not item.text(), notify=False)
        self._selection_changed(); self._notify()

    def _type_changed(self, number, value):
        if self._populating or self._read_only or not 0 <= number < len(self._rows): return
        row = self._rows[number]
        types = row.schema.get('bsonType')
        self._set_row_keyword(number, 'bsonType', types if isinstance(types, list) and value == '(Multiple)' else [] if value == '(Multiple)' else value)
        self.table.selectRow(number); self._selection_changed()

    def _set_row_keyword(self, number, key, value, *, omit=False, notify=True, fragment=False):
        row = self._rows[number]
        if row.key is None:
            if omit: row.schema.pop(key, None)
            else: row.schema[key] = deepcopy(value)
            if notify: self._notify()
            return True
        path = (*SCHEMA, 'properties', row.key)
        if omit and key not in row.schema:
            if notify: self._notify()
            return True
        patch = RemoveKeyword((*path, key)) if omit else ReplaceSchemaFragment(path, key, value) if fragment else SetKeyword((*path, key), value)
        return self._apply([patch], notify=notify, error_key='schema:' + str(id(row)) + ':' + key)

    def _selection_changed(self):
        if self._populating: return
        self._clear_form(self.constraints_form); self.constraint_fields = {}
        number = self.table.currentRow()
        if 0 <= number < len(self._rows):
            row = self._rows[number]; kind = row.schema.get('bsonType')
            fields = [('enum', '枚举', 'fragment')]
            if isinstance(kind, list): fields = [('bsonType', 'BSON 类型', 'types'), *fields]
            elif type(kind) is str and kind in NUMERIC_TYPES:
                fields += [('multipleOf', '多个', 'number'), ('maximum', '最大', 'number'), ('exclusiveMaximum', '独占最大值', 'check'),
                           ('minimum', '最小', 'number'), ('exclusiveMinimum', '独占最小值', 'check')]
            elif kind == 'array':
                fields += [('items', '项目', 'fragment'), ('additionalItems', '附加项目', 'fragment'),
                           ('maxItems', '最大项目', 'number'), ('minItems', '最小项目', 'number'), ('uniqueItems', '唯一项目', 'check')]
            elif kind == 'object':
                fields += [('maxProperties', '最大属性', 'number'), ('minProperties', '最小属性', 'number'),
                           ('required', '必需', 'fragment'), ('properties', '属性', 'fragment'), ('additionalProperties', '附加属性', 'fragment'),
                           ('patternProperties', '模式属性', 'fragment'), ('dependencies', '依赖性', 'fragment')]
            elif kind == 'string':
                fields += [('maxLength', '最大长度', 'number'), ('minLength', '最小长度', 'number'), ('pattern', '模式', 'text')]
            self._build_fields(self.constraints_form, fields, row.schema, self.constraint_fields, number)
        self.constraints.setEnabled(not self._read_only); self.actionsChanged.emit()

    @staticmethod
    def _clear_form(form):
        while form.count():
            item = form.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout():
                layout = item.layout()
                while layout.count():
                    child = layout.takeAt(0)
                    if child.widget(): child.widget().deleteLater()

    def _render_root(self):
        self._clear_form(self.root_form); self.root_fields = {}
        self._build_fields(self.root_form, ROOT_FIELDS, self._schema.visual_schema or {}, self.root_fields, None)

    def _build_fields(self, form, fields, values, outputs, number):
        inputs = self._root_inputs if number is None else self._rows[number].inputs
        for key, label, kind in fields:
            if kind == 'check':
                control = QCheckBox(); control.setChecked(values.get(key) is True)
                control.toggled.connect(lambda value, key=key, number=number: self._field_changed(number, key, value))
                form.addRow(label, control); outputs[key] = control
                continue
            control = QLineEdit()
            value = values.get(key)
            text = _literal(value) if key in values else ''
            if kind in {'fragment', 'types'}:
                control.setReadOnly(True); control.setText(_bounded(text, 180) if text is not None else '保留的 BSON 值')
                row = QHBoxLayout(); row.addWidget(control)
                button = QPushButton('…'); button.setFixedWidth(30); row.addWidget(button)
                button.clicked.connect(lambda _, key=key, number=number, kind=kind: self._open_types(number) if kind == 'types' else self._open_fragment(number, key))
                form.addRow(label, row); outputs[key] = control; outputs[key + '_button'] = button
            else:
                control.setText(inputs.get(key, str(value) if key in values else ''))
                control.textChanged.connect(lambda value, key=key, number=number, kind=kind: self._text_field_changed(number, key, kind, value))
                form.addRow(label, control); outputs[key] = control

    def _text_field_changed(self, number, key, kind, text):
        if self._populating or self._read_only: return
        inputs = self._root_inputs if number is None else self._rows[number].inputs
        inputs[key] = text
        identity = 'schema:' + ('root' if number is None else str(id(self._rows[number]))) + ':' + key
        try:
            value = parse_numeric_constraint(text) if kind == 'number' else text
            self._invalid.pop(identity, None)
            self._field_changed(number, key, value, omit=not text.strip())
        except DatabaseError as error:
            self._invalid[identity] = _bounded(error); self._notify()

    def _field_changed(self, number, key, value, *, omit=False, fragment=False):
        if self._populating or self._read_only: return
        if number is not None:
            self._set_row_keyword(number, key, value, omit=omit, fragment=fragment)
        else:
            schema = self._schema.visual_schema or {}
            if omit and key not in schema:
                self._notify(); return
            patch = RemoveKeyword((*SCHEMA, key)) if omit else ReplaceSchemaFragment(SCHEMA, key, value) if fragment else SetKeyword((*SCHEMA, key), value)
            self._apply([patch])

    def _open_fragment(self, number, key):
        if self._read_only: return
        values = (self._schema.visual_schema or {}) if number is None else self._rows[number].schema
        def validate(value, omitted):
            if number is None:
                path = SCHEMA; document = self._schema.raw_bson
            else:
                row = self._rows[number]
                path = (*SCHEMA, 'properties', row.key if row.key is not None else 'generated_draft')
                document = self._schema.raw_bson if row.key is not None else {'$jsonSchema': {'properties': {'generated_draft': deepcopy(row.schema)}}}
            if omitted and key not in values: return
            patch = RemoveKeyword((*path, key)) if omitted else ReplaceSchemaFragment(path, key, value)
            ValidatorDraft(document).apply([patch])
        dialog = ValidatorFragmentDialog(values.get(key), present=key in values, validate=validate, parent=self)
        self.fragment_dialog = dialog
        def accepted():
            self._field_changed(number, key, dialog.value, omit=dialog.omitted, fragment=True)
            if number is None: self._render_root()
            else: self._selection_changed()
        dialog.accepted.connect(accepted)
        dialog.finished.connect(lambda *_: self._fragment_finished(dialog)); dialog.open()

    def _fragment_finished(self, dialog):
        if self.fragment_dialog is dialog: self.fragment_dialog = None
        dialog.deleteLater()

    def _open_types(self, number):
        if self._read_only or number is None: return
        selected = self._rows[number].schema.get('bsonType', [])
        dialog = BsonTypesDialog(selected if isinstance(selected, list) else [], self)
        self.types_dialog = dialog
        dialog.accepted.connect(lambda: self._types_accepted(number, dialog))
        dialog.finished.connect(lambda *_: self._types_finished(dialog)); dialog.open()

    def _types_accepted(self, number, dialog):
        self._set_row_keyword(number, 'bsonType', dialog.selected); self._selection_changed()

    def _types_finished(self, dialog):
        if self.types_dialog is dialog: self.types_dialog = None
        dialog.deleteLater()
