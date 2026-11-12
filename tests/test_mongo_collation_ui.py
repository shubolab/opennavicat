"""Structured MongoDB collection collation controls without database I/O."""
from __future__ import annotations

import pytest

from opennavicat.mongodb_collections import CollectionDefinition, CollectionSnapshot
from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
from opennavicat.ui.mongo_collation import MongoCollationEditor


def test_new_collation_starts_blank_and_enables_after_locale(qtbot):
    editor = MongoCollationEditor()
    qtbot.addWidget(editor)
    assert editor.locale_combo.currentText() == ''
    assert editor.locale_combo.currentIndex() == -1
    assert editor.locale_combo.count() == 152
    assert all(not combo.isEnabled() for combo in editor.combos.values())
    editor.locale_combo.setCurrentText('en_US')
    assert all(combo.isEnabled() for combo in editor.combos.values())
    assert editor.collation() == {'locale': 'en_US'}


def test_reference_locale_catalog_preserves_order_and_variants(qtbot):
    from opennavicat.ui.mongo_collation import LOCALE_SUGGESTIONS

    editor = MongoCollationEditor()
    qtbot.addWidget(editor)
    assert len(LOCALE_SUGGESTIONS) == len(set(LOCALE_SUGGESTIONS)) == 152
    assert LOCALE_SUGGESTIONS[:8] == (
        'simple', 'af', 'am', 'ar', 'ar@collation=compat', 'as', 'az', 'az@collation=search')
    assert LOCALE_SUGGESTIONS[-8:] == (
        'yo', 'zh', 'zh@collation=big5han', 'zh@collation=gb2312han',
        'zh@collation=unihan', 'zh@collation=zhuyin', 'zh_Hant', 'zu')
    visible_values = [editor.locale_combo.itemText(index) for index in range(editor.locale_combo.count())]
    assert visible_values == list(LOCALE_SUGGESTIONS)
    editor.locale_combo.setCurrentText('generated_future_locale')
    assert editor.collation() == {'locale': 'generated_future_locale'}


def test_observed_structured_values_generate_exact_collation_and_preserve_unknown(qtbot):
    editor = MongoCollationEditor()
    qtbot.addWidget(editor)
    editor.load({
        'locale': 'en_US', 'caseLevel': False, 'caseFirst': 'off', 'strength': 3,
        'numericOrdering': False, 'alternate': 'non-ignorable', 'maxVariable': 'punct',
        'normalization': False, 'backwards': False, 'version': '57.1',
        'futureCollationField': {'keep': True},
    })
    assert editor.locale_combo.currentText() == 'en_US'
    assert editor.combos['strength'].currentData() == 3
    editor.combos['caseLevel'].setCurrentIndex(editor.combos['caseLevel'].findData(True))
    editor.combos['caseFirst'].setCurrentIndex(editor.combos['caseFirst'].findData('upper'))
    editor.combos['strength'].setCurrentIndex(editor.combos['strength'].findData(4))
    editor.combos['numericOrdering'].setCurrentIndex(editor.combos['numericOrdering'].findData(True))
    editor.combos['alternate'].setCurrentIndex(editor.combos['alternate'].findData('shifted'))
    editor.combos['maxVariable'].setCurrentIndex(editor.combos['maxVariable'].findData('space'))
    editor.combos['backwards'].setCurrentIndex(editor.combos['backwards'].findData(True))
    editor.combos['normalization'].setCurrentIndex(editor.combos['normalization'].findData(True))
    assert editor.collation() == {
        'locale': 'en_US', 'caseLevel': True, 'caseFirst': 'upper', 'strength': 4,
        'numericOrdering': True, 'alternate': 'shifted', 'maxVariable': 'space',
        'normalization': True, 'backwards': True, 'version': '57.1',
        'futureCollationField': {'keep': True},
    }
    editor.locale_combo.setCurrentText('')
    assert editor.collation() is None
    assert all(not combo.isEnabled() for combo in editor.combos.values())


def test_collection_page_updates_and_removes_structured_collation(qtbot):
    definition = CollectionDefinition('names', {})
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'names')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot('demo_catalog', 'names', definition, exists=False))
    editor = page.collation_editor
    editor.locale_combo.setCurrentText('en_US')
    editor.combos['strength'].setCurrentIndex(editor.combos['strength'].findData(2))
    assert page.draft().options['collation'] == {'locale': 'en_US', 'strength': 2}
    assert page.dirty
    editor.locale_combo.setCurrentText('')
    assert 'collation' not in page.draft().options


def test_existing_collation_controls_are_readonly_even_when_other_options_are_editable(qtbot):
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'locked')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot(
        'demo_catalog', 'locked',
        CollectionDefinition('locked', {'collation': {'locale': 'en_US', 'strength': 3, 'version': '57.1'}}),
        exists=True,
    ))
    assert page.collation_editor.locale_combo.currentText() == 'en_US'
    assert page.collation_editor.combos['strength'].currentData() == 3
    assert not page.collation_editor.locale_combo.isEnabled()
    assert all(not combo.isEnabled() for combo in page.collation_editor.combos.values())
