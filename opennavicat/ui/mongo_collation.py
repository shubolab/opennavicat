"""Structured MongoDB collection collation controls matching the reference page."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from opennavicat.database import DatabaseError


LOCALE_SUGGESTIONS = (
    'simple', 'root', 'af', 'am', 'ar', 'ar@collation=compat', 'en', 'en_US',
    'zh', 'zh_CN',
)
CASE_LEVEL = (('off', False), ('on', True))
CASE_FIRST = (('upper', 'upper'), ('lower', 'lower'), ('off', 'off'))
STRENGTH = (
    ('primary level', 1), ('secondary level', 2), ('tertiary level', 3),
    ('quaternary level', 4), ('identical level', 5),
)
BOOLEAN = (('off', False), ('on', True))
ALTERNATE = (('non-ignorable', 'non-ignorable'), ('shifted', 'shifted'))
MAX_VARIABLE = (('punct', 'punct'), ('space', 'space'))
FIELDS = (
    ('caseLevel', CASE_LEVEL), ('caseFirst', CASE_FIRST), ('strength', STRENGTH),
    ('numericOrdering', BOOLEAN), ('alternate', ALTERNATE),
    ('maxVariable', MAX_VARIABLE), ('backwards', BOOLEAN), ('normalization', BOOLEAN),
)


class MongoCollationEditor(QWidget):
    """Edit known collation fields while preserving unknown sibling fields."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._populating = False
        self._read_only = False
        self._unknown = {}
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.locale_combo = QComboBox()
        self.locale_combo.setObjectName('mongoCollationLocale')
        self.locale_combo.setEditable(True)
        self.locale_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.locale_combo.addItem('', None)
        for value in LOCALE_SUGGESTIONS:
            self.locale_combo.addItem(value, value)
        self.form.addRow('区域设置:', self.locale_combo)
        self.combos = {}
        for key, values in FIELDS:
            combo = QComboBox()
            combo.setObjectName('mongoCollation' + key[:1].upper() + key[1:])
            combo.addItem('', None)
            for label, value in values:
                combo.addItem(label, value)
            self.combos[key] = combo
            labels = {
                'caseLevel': '大小写级别:', 'caseFirst': '大小写优先:',
                'strength': '强度:', 'numericOrdering': '数值排序:',
                'alternate': '替代:', 'maxVariable': '最大变量:',
                'backwards': '逆向:', 'normalization': '规范化:',
            }
            self.form.addRow(labels[key], combo)
            combo.currentIndexChanged.connect(self._field_changed)
        self.locale_combo.currentTextChanged.connect(self._locale_changed)
        self.locale_combo.lineEdit().editingFinished.connect(self._locale_changed)
        self.set_read_only(False)
        self.load(None)

    def _locale(self):
        return self.locale_combo.currentText().strip()

    def _set_enabled(self):
        enabled = bool(self._locale()) and not self._read_only
        self.locale_combo.setEnabled(not self._read_only)
        for combo in self.combos.values():
            combo.setEnabled(enabled)

    def _locale_changed(self, *_):
        if self._populating:
            return
        self._set_enabled()
        self.changed.emit()

    def _field_changed(self, *_):
        if not self._populating:
            self.changed.emit()

    def set_read_only(self, value):
        self._read_only = bool(value)
        self._set_enabled()

    def load(self, value):
        self._populating = True
        try:
            value = deepcopy(value) if value is not None else None
            if value is not None and not isinstance(value, dict):
                raise DatabaseError('Collation must be a BSON document')
            value = value or {}
            known = {'locale', *(key for key, _ in FIELDS)}
            self._unknown = {key: deepcopy(item) for key, item in value.items() if key not in known}
            self.locale_combo.setCurrentText(str(value.get('locale', '')))
            for key, combo in self.combos.items():
                item = value.get(key)
                index = 0 if item is None else combo.findData(item)
                if index < 0:
                    combo.addItem(str(item), item)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
        finally:
            self._populating = False
        self._set_enabled()

    def collation(self):
        locale = self._locale()
        if not locale:
            return None
        result = deepcopy(self._unknown)
        result['locale'] = locale
        for key, combo in self.combos.items():
            if combo.currentIndex() == 0:
                result.pop(key, None)
            else:
                result[key] = deepcopy(combo.currentData())
        return result
