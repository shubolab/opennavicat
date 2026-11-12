"""Structured MongoDB collection collation controls matching the reference page."""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from opennavicat.database import DatabaseError


# Ordered non-empty values enumerated from the Navicat 18.0.5 edit combo.
LOCALE_SUGGESTIONS = tuple('''
simple
af
am
ar
ar@collation=compat
as
az
az@collation=search
be
bg
bn
bn@collation=traditional
bo
bs
bs@collation=search
bs_Cyrl
ca
ca@collation=search
chr
cs
cs@collation=search
cy
da
da@collation=search
de
de@collation=eor
de@collation=phonebook
de@collation=search
de_AT
de_AT@collation=phonebook
dsb
dz
ee
el
en
en_US
en_US_POSIX
eo
es
es@collation=search
es@collation=traditional
et
fa
fa_AF
fi
fi@collation=search
fi@collation=traditional
fil
fo
fo@collation=search
fr
fr_CA
ga
gl
gl@collation=search
gu
ha
haw
he
he@collation=search
hi
hr
hr@collation=search
hsb
hu
hy
id
ig
is
is@collation=search
it
ja
ja@collation=unihan
ka
kk
kl
kl@collation=search
km
kn
kn@collation=traditional
ko
ko@collation=search
ko@collation=searchjl
ko@collation=unihan
kok
ky
lb
lkt
ln
ln@collation=phonetic
lo
lt
lv
mk
ml
mn
mr
ms
mt
my
nb
nb@collation=search
ne
nl
nn
nn@collation=search
om
or
pa
pl
ps
pt
ro
ru
se
se@collation=search
si
si@collation=dictionary
sk
sk@collation=search
sl
smn
smn@collation=search
sq
sr
sr_Latn
sr_Latn@collation=search
sv
sv@collation=search
sw
ta
te
th
to
tr
tr@collation=search
ug
uk
ur
vi
vi@collation=traditional
wae
yi
yi@collation=search
yo
zh
zh@collation=big5han
zh@collation=gb2312han
zh@collation=unihan
zh@collation=zhuyin
zh_Hant
zu
'''.split())
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
            locale = str(value.get('locale', ''))
            if locale:
                self.locale_combo.setCurrentText(locale)
            else:
                self.locale_combo.setCurrentIndex(-1)
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
