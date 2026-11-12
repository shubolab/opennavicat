"""Embedded MongoDB index UI without opening a database."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from opennavicat.database import DatabaseError
from opennavicat.mongodb_collections import CollectionDefinition, CollectionSnapshot
from opennavicat.mongodb_indexes import InlineIndexDraft, IndexDefinition, IndexKey
from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
from opennavicat.ui.mongo_inline_index import InlineIndexKeyDialog, MongoInlineIndexSaveDialog


def page_with_index(qtbot):
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'orders')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot(
        'demo_catalog', 'orders', CollectionDefinition('orders', {}), exists=True))
    page.set_indexes([{
        'name': 'orders_status', 'keys': [('status', 1)], 'unique': False,
        'options': {'v': 2},
    }])
    return page


def test_key_dialog_commits_explicit_model_and_rejects_duplicates(qtbot):
    dialog = InlineIndexKeyDialog(IndexDefinition('idx', 'orders', [], {}))
    qtbot.addWidget(dialog)
    dialog.add_key()
    name = dialog.table.cellWidget(0, 0)
    kind = dialog.table.cellWidget(0, 1)
    assert isinstance(name, type(name)) and name.isEditable()
    name.setCurrentText('status')
    kind.setCurrentIndex(kind.findData(-1))
    dialog.add_key()
    dialog.table.cellWidget(1, 0).setCurrentText('status')
    dialog.table.cellWidget(1, 1).setCurrentIndex(dialog.table.cellWidget(1, 1).findData('hashed'))
    dialog.accept()
    assert 'twice' in dialog.error.text()
    dialog.table.cellWidget(1, 0).setCurrentText('created_at')
    dialog.accept()
    assert not dialog.isVisible()
    assert dialog.definition.keys == [IndexKey('status', -1), IndexKey('created_at', 'hashed')]


def test_inline_save_sheet_requires_preview_and_renders_exact_order(qtbot):
    drafts = [InlineIndexDraft(IndexDefinition(
        'orders_status_v2', 'orders', [IndexKey('status', -1)], {'unique': True}), 'orders_status')]
    dialog = MongoInlineIndexSaveDialog(drafts)
    qtbot.addWidget(dialog)
    plan = SimpleNamespace(
        changed=True,
        warnings=['rename is drop then create', 'not transactional'],
        command_script='drop\n\ncreate',
        command_text='BSON drop\n\nBSON create',
    )
    dialog.show_plan(plan)
    assert dialog.save_button.isEnabled()
    assert dialog.preview.toPlainText() == 'drop\n\ncreate'
    assert 'not transactional' in dialog.warning.text()
    dialog.preview_mode.setCurrentIndex(1)
    assert dialog.preview.toPlainText() == 'BSON drop\n\nBSON create'


def test_page_save_previews_then_emits_the_exact_confirmed_bundle(qtbot):
    page = page_with_index(qtbot)
    page.index_table.selectRow(0)
    name = page.index_table.cellWidget(0, 1)
    assert isinstance(name, QLineEdit)
    name.setText('orders_status_v2'); name.editingFinished.emit()
    page.unique_index_check.click()
    previews, saves = [], []
    page.inlineIndexPreviewRequested.connect(previews.append)
    page.inlineIndexSaveRequested.connect(saves.append)
    page.request_save()
    dialog = page._inline_index_save_dialog
    assert isinstance(dialog, MongoInlineIndexSaveDialog)
    dialog.request_preview()
    assert len(previews) == 1 and previews[0][0].definition.name == 'orders_status_v2'
    plan = SimpleNamespace(
        changed=True, drafts=previews[0], warnings=[], command_script='exact command', command_text='exact bson')
    # The page checks the current complete draft list, including deleted rows.
    page.show_inline_index_plan(plan)
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert len(saves) == 1 and saves[0].changed and saves[0].command_script == 'exact command'
    page.inline_indexes_saved()
    assert not page.dirty and not page._inline_index_dirty


def test_new_blank_index_blocks_save_and_delete_tracks_existing_deletion(qtbot):
    page = page_with_index(qtbot)
    page.new_index_button.click()
    assert page.index_table.rowCount() == 2
    assert page.dirty and not page.save_action.isEnabled()
    page.delete_index_button.click()
    assert page.index_table.rowCount() == 1
    page.index_table.selectRow(0)
    page.delete_index_button.click()
    assert page.index_table.rowCount() == 0
    assert page.inline_index_drafts()[0].deleted is True


def test_invalid_ttl_and_bucket_size_survive_focus_and_block_save(qtbot):
    page = page_with_index(qtbot)
    page.index_table.selectRow(0)
    page.ttl_index_check.click(); page.ttl_index_edit.setText('bad'); page.ttl_index_edit.editingFinished.emit()
    assert 'ttl' in page._inline_index_invalid and not page.save_action.isEnabled()
    page.ttl_index_edit.setText('30'); page.ttl_index_edit.editingFinished.emit()
    page.bucket_size_edit.setText('-1'); page.bucket_size_edit.editingFinished.emit()
    assert 'bucketSize' in page._inline_index_invalid and not page.save_action.isEnabled()
    page.bucket_size_edit.setText('2.5'); page.bucket_size_edit.editingFinished.emit()
    drafts = page.inline_index_drafts()
    assert drafts[0].definition.options['expireAfterSeconds'] == 30
    assert drafts[0].definition.options['bucketSize'] == 2.5
    assert page.save_action.isEnabled()


def test_inline_save_cancel_and_apply_error_preserve_draft(qtbot):
    from opennavicat.mongodb_indexes import InlineIndexApplyError, IndexDefinition

    page = page_with_index(qtbot)
    page.index_table.selectRow(0)
    name = page.index_table.cellWidget(0, 1)
    name.setText('orders_status_cancelled'); name.editingFinished.emit()
    page.request_save()
    dialog = page._inline_index_save_dialog
    plan = SimpleNamespace(
        changed=True, warnings=[], command_script='drop\ncreate', command_text='BSON')
    page.show_inline_index_plan(plan)
    dialog.reject()
    qtbot.waitUntil(lambda: page._inline_index_save_dialog is None)
    assert page.dirty and page.inline_index_drafts()[0].definition.name == 'orders_status_cancelled'

    error = InlineIndexApplyError(
        'stopped after 0 operations; no automatic rollback', [], 'orders_status',
        IndexDefinition('orders_status_cancelled', 'orders', [IndexKey('status', 1)]),
        DatabaseError('server rejected the replacement'),
    )
    page.request_save()
    page.show_error(error)
    current = page._inline_index_save_dialog
    assert 'server rejected the replacement' in current.error.text()
    assert page.inline_index_drafts()[0].definition.name == 'orders_status_cancelled'
    current.reject()
