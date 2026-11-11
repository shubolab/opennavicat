"""Detached ordinary MongoDB collection designer tests without database I/O."""
from __future__ import annotations

from copy import deepcopy

import pytest

from opennavicat.database import DatabaseError
from bson import Binary

from opennavicat.mongodb_collections import (
    CollectionApplyError, CollectionDefinition, CollectionOutcome, CollectionPlan, CollectionSnapshot,
)
from opennavicat.mongodb_collection_copy import CollectionCopyPlan
from opennavicat.ui.mongo_collection_designer import (
    MongoCollectionCopyDialog, MongoCollectionDesignerPage, MongoCollectionOperationDialog,
    MongoCollectionSaveDialog, collection_error_text,
)


def new_snapshot(options=None):
    return CollectionSnapshot(
        'demo_catalog', '', CollectionDefinition('', deepcopy(options or {})), exists=False)


@pytest.fixture
def page(qtbot):
    widget = MongoCollectionDesignerPage('generated', 'demo_catalog')
    qtbot.addWidget(widget)
    widget.load_snapshot(new_snapshot())
    return widget


def test_collection_page_edits_exact_supported_options_and_caps_size_units(qtbot, page):
    page.name_edit.setText('events')
    page.capped_check.setChecked(True)
    page.size_edit.setText('4096')
    page.max_edit.setText('250')
    page.validator_editor.setPlainText('{"amount": {"$gte": 0}}')
    page.level_combo.setCurrentIndex(page.level_combo.findData('strict'))
    page.action_combo.setCurrentIndex(page.action_combo.findData('error'))
    page.collation_editor.setPlainText('{"locale": "en", "strength": 2}')

    draft = page.draft()
    assert draft.name == 'events'
    assert draft.options == {
        'capped': True,
        'size': 4096,
        'max': 250,
        'validator': {'amount': {'$gte': 0}},
        'validationLevel': 'strict',
        'validationAction': 'error',
        'collation': {'locale': 'en', 'strength': 2},
    }
    assert page.tabs.count() == 6
    assert page.raw_editor.isReadOnly()
    assert page.sql_preview.isReadOnly()

    page.size_edit.setText('4097')
    assert '256' in '\n'.join(page._invalid.values())
    assert not page.save_action.isEnabled()
    with pytest.raises(DatabaseError, match='256'):
        page.draft()

    page.size_edit.setText('4352')
    assert not page._invalid and page.save_action.isEnabled()
    page.capped_check.setChecked(False)
    assert all(key not in page.draft().options for key in ('capped', 'size', 'max'))
    assert not page.size_edit.isEnabled() and not page.max_edit.isEnabled()


def test_invalid_document_is_dirty_but_never_emitted(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append)
    page.saveRequested.connect(saves.append)
    page.name_edit.setText('orders')
    page.validator_editor.setPlainText('{broken')

    page.request_save()
    assert page._invalid and page.dirty
    assert not previews and not saves
    assert 'Canonical Extended JSON' in page.message.text()


def test_name_sheet_previews_exact_plan_and_confirms_save(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append)
    page.saveRequested.connect(saves.append)
    page.name_edit.setText('orders')
    page.capped_check.setChecked(True)
    page.size_edit.setText('4096')

    page.request_save()
    dialog = page._save_dialog
    assert isinstance(dialog, MongoCollectionSaveDialog)
    qtbot.waitUntil(lambda: bool(previews))
    assert previews[-1].name == 'orders'
    assert not dialog.save_button.isEnabled()

    plan = CollectionPlan(
        page.snapshot,
        deepcopy(page.draft()),
        commands=[{'create': 'orders', 'capped': True, 'size': 4096}],
        changed=True,
    )
    page.show_plan(plan)
    assert dialog.save_button.isEnabled()
    assert 'db.runCommand' in dialog.preview.toPlainText()
    dialog.preview_mode.setCurrentIndex(1)
    assert '"create"' in dialog.preview.toPlainText()
    dialog.save_button.click()
    assert saves == [page.draft()]

    dialog.reject()
    qtbot.waitUntil(lambda: page._save_dialog is None)


def test_existing_unknown_options_remain_raw_readonly(qtbot):
    definition = CollectionDefinition('locked', {'futureOption': {'typed': 1}})
    snapshot = CollectionSnapshot(
        'demo_catalog', 'locked', definition,
        raw_spec=b'\x10\x00\x00\x00\x10x\x00\x01\x00\x00\x00\x00',
        exists=True, read_only=True, issues=['Additional collection options are retained.'],
    )
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'locked')
    qtbot.addWidget(page)
    page.load_snapshot(snapshot)
    assert page.name_edit.isReadOnly()
    assert page._read_only and not page.save_action.isEnabled()
    assert 'Additional collection options' in page.message.text()
    assert page.raw_editor.toPlainText()
    assert not page.validation.isEnabled() and not page.collation.isEnabled()


def test_existing_collection_index_table_exposes_full_index_designer_routes(qtbot):
    definition = CollectionDefinition('orders', {})
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'orders')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot('demo_catalog', 'orders', definition, exists=True))
    requests = {'new': 0, 'edit': [], 'delete': [], 'refresh': 0}
    page.newIndexRequested.connect(lambda: requests.__setitem__('new', requests['new'] + 1))
    page.editIndexRequested.connect(requests['edit'].append)
    page.deleteIndexRequested.connect(requests['delete'].append)
    page.indexesRequested.connect(lambda: requests.__setitem__('refresh', requests['refresh'] + 1))
    page.set_indexes([
        {'name': '_id_', 'keys': [('_id', 1)], 'unique': True, 'system': True, 'options': {'v': 2}},
        {'name': 'orders_status', 'keys': [('status', 1)], 'unique': False,
         'options': {'background': True, 'sparse': True, 'expireAfterSeconds': 60, 'v': 2}},
    ])
    assert page.index_table.rowCount() == 2
    assert page.index_table.item(1, 0).text() == 'orders_status'
    assert page.index_table.item(1, 1).text() == 'status: 1'
    assert page.index_table.item(1, 5).text() == '60'
    assert page.new_index_button.isEnabled()
    page.new_index_button.click()
    page.index_table.selectRow(0)
    assert not page.edit_index_button.isEnabled() and not page.delete_index_button.isEnabled()
    page.index_table.selectRow(1)
    page.edit_index_button.click(); page.delete_index_button.click(); page.refresh_index_button.click()
    assert requests['new'] == 1 and requests['edit'] == ['orders_status']
    assert requests['delete'][0]['name'] == 'orders_status' and requests['refresh'] == 1


def test_apply_error_formatter_keeps_status_reply_and_observation():
    observed = CollectionSnapshot(
        'demo_catalog', 'orders', CollectionDefinition('orders', {'validator': {}}),
        exists=True, read_only=True, issues=['readback became read-only'],
    )
    outcome = CollectionOutcome(
        'collMod', status='unknown', reply={'ok': 0, 'code': 91}, observed=observed,
        observation_error='MongoConflictError', cancelled=True,
    )
    text = collection_error_text(CollectionApplyError('save failed', outcome))
    assert '服务器报错' not in text
    assert '确认未知' in text
    assert '"code"' in text
    assert 'MongoConflictError' in text
    assert 'readback became read-only' in text
    assert '不会自动回滚' in text



@pytest.mark.parametrize(
    "operation,command,button_text",
    [("drop", {"drop": "locked", "collectionUUID": Binary(b"\0" * 16, 4), "writeConcern": {"w": 1}}, "删除"),
     ("clear", {"delete": "locked", "deletes": [{"q": {}, "limit": 0}],
                "collectionUUID": Binary(b"\0" * 16, 4), "writeConcern": {"w": 1}}, "清空"),
     ("rename", {"renameCollection": "demo_catalog.locked", "to": "demo_catalog.renamed",
                 "dropTarget": False, "collectionUUID": Binary(b"\0" * 16, 4),
                 "writeConcern": {"w": 1}}, "重命名")],
)
def test_permanent_collection_operation_requires_explicit_checkbox(qtbot, operation, command, button_text):
    snapshot = CollectionSnapshot(
        "demo_catalog", "locked", CollectionDefinition("locked"), exists=True)
    definition = CollectionDefinition("renamed") if operation == "rename" else snapshot.definition
    plan = CollectionPlan(snapshot, definition, [command], True, ["permanent warning"])
    dialog = MongoCollectionOperationDialog(plan, operation)
    qtbot.addWidget(dialog)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    assert dialog.confirm_button.text() == button_text
    assert not dialog.confirm_button.isEnabled()
    assert "db.runCommand" in dialog.preview.toPlainText()
    dialog.permanent_check.setChecked(True)
    assert dialog.confirm_button.isEnabled()
    dialog.accept()
    assert accepted == [True]



@pytest.mark.parametrize("mode", ["data", "structure"])
def test_copy_dialog_requires_staging_acknowledgement(qtbot, mode):
    snapshot = CollectionSnapshot("demo_catalog", "source", CollectionDefinition("source"), exists=True)
    plan = CollectionCopyPlan(
        snapshot, "target", mode, "__opennavicat_copy_test", [],
        [{"create": "__opennavicat_copy_test"}, {"renameCollection": "demo_catalog.__opennavicat_copy_test",
          "to": "demo_catalog.target", "dropTarget": False, "collectionUUID": "<resolved staging UUID>"}],
        ["staging may remain", "not a transaction"],
    )
    dialog = MongoCollectionCopyDialog(plan)
    qtbot.addWidget(dialog)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    assert not dialog.copy_button.isEnabled()
    assert "staging" in dialog.preview.toPlainText().lower()
    dialog.ack_check.setChecked(True)
    assert dialog.copy_button.isEnabled()
    dialog.accept()
    assert accepted == [True]
