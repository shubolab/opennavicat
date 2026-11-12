"""Detached ordinary MongoDB collection designer tests without database I/O."""
from __future__ import annotations

from copy import deepcopy

import pytest

from opennavicat.database import DatabaseError
from bson import Binary, Int64

from opennavicat.mongodb_collections import (
    CollectionApplyError, CollectionDefinition, CollectionOutcome, CollectionPlan, CollectionSnapshot,
)
from opennavicat.mongodb_collection_copy import CollectionCopyPlan
from opennavicat.mongodb_collection_literals import MAX_STORAGE_TEXT_BYTES, parse_storage_option
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
    page.validator_widget.mode_combo.setCurrentIndex(1)
    page.validator_editor.setPlainText('{"amount": {"$gte": 0}}')
    page.level_combo.setCurrentIndex(page.level_combo.findData('strict'))
    page.action_combo.setCurrentIndex(page.action_combo.findData('error'))
    page.collation_editor.locale_combo.setCurrentText('en')
    page.collation_editor.combos['strength'].setCurrentIndex(
        page.collation_editor.combos['strength'].findData(2))

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
    assert [page.tabs.tabText(index) for index in range(page.tabs.count())] == [
        '索引', '验证', '存储引擎', '索引选项', '集合选项', '排序规则', '脚本预览']
    assert page.tabs.currentIndex() == 0
    assert page.name_edit.isHidden()
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


def test_reference_collection_option_controls_keep_defaults_implicit(qtbot, page):
    assert page.capped_check.text() == '固定集合'
    assert page.clustered_check.text() == '聚集集合'
    assert page.auto_index_id_check.isChecked()
    assert page.power2_check.isChecked()
    assert not page.no_padding_check.isChecked()
    assert page.draft().options == {}

    page.clustered_check.setChecked(True)
    page.cluster_name_edit.setText('cluster_main')
    page.cluster_expire_check.setChecked(True)
    page.cluster_expire_edit.setText('60')
    page.change_images_check.setChecked(True)
    assert page.draft().options == {
        'clusteredIndex': {'key': {'_id': 1}, 'unique': True, 'name': 'cluster_main'},
        'expireAfterSeconds': 60,
        'changeStreamPreAndPostImages': {'enabled': True},
    }

    page.capped_check.setChecked(True)
    assert not page.clustered_check.isChecked()
    assert page.draft().options == {
        'capped': True,
        'changeStreamPreAndPostImages': {'enabled': True},
    }
    page.change_images_check.setChecked(False)
    assert not page.cluster_name_edit.isEnabled()
    assert not page.cluster_expire_check.isEnabled()

    page.auto_index_id_check.setChecked(False)
    page.power2_check.setChecked(False)
    page.no_padding_check.setChecked(True)
    assert page.draft().options == {
        'capped': True,
        'autoIndexId': False,
        'usePowerOf2Sizes': False,
        'noPadding': True,
    }


def test_existing_collection_matches_reference_mutable_and_disabled_options(qtbot):
    definition = CollectionDefinition('existing', {
        'clusteredIndex': {'key': {'_id': 1}, 'name': 'cluster_main', 'unique': True},
        'expireAfterSeconds': 60,
        'changeStreamPreAndPostImages': {'enabled': True},
    })
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'existing')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot('demo_catalog', 'existing', definition, exists=True))
    assert page.clustered_check.isChecked() and not page.clustered_check.isEnabled()
    assert page.cluster_name_edit.text() == 'cluster_main' and not page.cluster_name_edit.isEnabled()
    assert page.cluster_expire_check.isChecked() and not page.cluster_expire_check.isEnabled()
    assert not page.auto_index_id_check.isEnabled()
    assert page.change_images_check.isChecked() and page.change_images_check.isEnabled()
    assert page.power2_check.isEnabled() and page.no_padding_check.isEnabled()
    page.change_images_check.setChecked(False)
    assert page.draft().options['changeStreamPreAndPostImages'] == {'enabled': False}


def test_invalid_document_is_dirty_but_never_emitted(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append)
    page.saveRequested.connect(saves.append)
    page.name_edit.setText('orders')
    page.validator_widget.mode_combo.setCurrentIndex(1)
    page.validator_editor.setPlainText('{broken')

    page.request_save()
    assert page._invalid and page.dirty
    assert not previews and not saves
    assert 'line' in page.message.text() and 'column' in page.message.text()


def test_unprojectable_json_schema_opens_exact_raw_query_and_preserves_typed_values(qtbot, page):
    value = {'$jsonSchema': {
        'title': 'External schema',
        'properties': [{'unexpected': Int64(7)}],
        'required': ['unmapped'],
    }}
    page.validator_widget.load_validator(value)
    widget = page.validator_widget
    assert widget.mode == 'query'
    assert 'Long("7")' in widget.query_editor.toPlainText()
    assert widget.validator() == value
    widget.mode_combo.setCurrentIndex(0)
    assert widget.table.rowCount() == 0
    assert widget.message.isVisible() or widget.message.text()
    widget.mode_combo.setCurrentIndex(1)
    assert widget.validator() == value


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
    assert page.raw_action.isVisible() and page.raw not in [page.tabs.widget(i) for i in range(page.tabs.count())]
    page.raw_action.trigger()
    assert page.raw.isVisible() and page.raw_editor.isReadOnly()
    page.raw.close()


def test_storage_object_literals_project_exact_options_and_blank_omits_them(qtbot, page):
    storage = "{ wiredTiger: { configString: 'block_compressor=zlib' } }"
    defaults = '{storageEngine: {wiredTiger: {configString: "prefix_compression=false"}}}'
    page.storage_editor.setPlainText(storage)
    page.index_options_editor.setPlainText(defaults)
    assert page.storage_editor.toPlainText() == storage
    assert page.draft().options == {
        'storageEngine': {'wiredTiger': {'configString': 'block_compressor=zlib'}},
        'indexOptionDefaults': {'storageEngine': {'wiredTiger': {'configString': 'prefix_compression=false'}}},
    }
    assert not page.storage_editor.isReadOnly() and not page.index_options_editor.isReadOnly()
    assert not page.raw_action.isVisible()
    page.storage_editor.clear()
    assert 'storageEngine' not in page.draft().options
    page.index_options_editor.setPlainText('  \n  ')
    assert page.draft().options == {}


def test_unnamed_storage_preview_uses_placeholder_without_emitting_database_request(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append)
    page.saveRequested.connect(saves.append)
    page.storage_editor.setPlainText('{wiredTiger:{configString:"block_compressor=zlib"}}')
    page.tabs.setCurrentIndex(page.tabs.count() - 1)
    assert page.sql_preview.toPlainText().startswith('db.createCollection("Untitled",')
    assert 'block_compressor=zlib' in page.sql_preview.toPlainText()
    assert not previews and not saves and page.definition.name == ''
    assert page._plan is None and page.confirmed_plan is None
    page.request_save()
    dialog = page._save_dialog
    assert dialog.name_edit.text() == '' and not dialog.save_button.isEnabled()
    dialog.reject()


def test_storage_invalid_literal_blocks_save_and_recovers_without_losing_text(qtbot, page):
    page.storage_editor.setPlainText('{wiredTiger: {configString: "block_compressor=zlib"}}')
    page.storage_editor.setPlainText('{wiredTiger: {configString: "safe"}}; db.dropDatabase()')
    assert page.dirty and not page.save_action.isEnabled()
    page.request_save()
    assert page._save_dialog is None
    assert 'db.dropDatabase()' in page.storage_editor.toPlainText()
    with pytest.raises(DatabaseError): page.draft()
    page.storage_editor.setPlainText('{wiredTiger: {configString: "block_compressor=zlib"}}')
    assert page.save_action.isEnabled() and not page._invalid


def test_existing_storage_editors_remain_selectable_readonly_without_dirty_mutation(qtbot):
    options = {
        'storageEngine': {'wiredTiger': {'configString': 'block_compressor=zlib'}},
        'indexOptionDefaults': {'storageEngine': {'wiredTiger': {'configString': 'prefix_compression=false'}}},
    }
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'stored')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot('demo_catalog', 'stored', CollectionDefinition('stored', options), exists=True))
    assert page.storage_editor.isReadOnly() and page.index_options_editor.isReadOnly()
    assert 'block_compressor=zlib' in page.storage_editor.toPlainText()
    assert 'prefix_compression=false' in page.index_options_editor.toPlainText()
    assert not page.dirty and not page.save_action.isEnabled()
    page.storage_editor.setPlainText('{wiredTiger: {configString: "block_compressor=snappy"}}')
    assert page.draft().options == options and not page.dirty
    assert not page.capped_check.isEnabled()


def test_storage_name_sheet_uses_observed_label_and_cancel_preserves_unnamed_draft(qtbot, page):
    from PySide6.QtWidgets import QFormLayout
    page.storage_editor.setPlainText('{wiredTiger: {configString: "block_compressor=zlib"}}')
    saved = []
    page.saveRequested.connect(saved.append)
    before = page.draft()
    page.request_save()
    dialog = page._save_dialog
    assert dialog.name_edit.text() == '' and not dialog.save_button.isEnabled()
    form = next(form for form in dialog.findChildren(QFormLayout) if form.labelForField(dialog.name_edit))
    assert form.labelForField(dialog.name_edit).text() == '表名称:'
    assert not dialog.suppress_preview_check.isChecked()
    dialog.name_edit.setText('cancelled_storage')
    dialog.reject()
    qtbot.waitUntil(lambda: page._save_dialog is None)
    assert page.draft() == before and page.draft().name == '' and not saved
    assert page.dirty and 'block_compressor=zlib' in page.storage_editor.toPlainText()


@pytest.mark.parametrize('source', [
    '{wiredTiger:{configString:"block_compressor=zlib"}}',
    "/* generated */ { wiredTiger: { configString: 'block_compressor=zlib', }, } // end",
])
def test_storage_literal_parser_accepts_observed_object_grammar_without_evaluation(source):
    assert parse_storage_option(source, 'storageEngine') == {'wiredTiger': {'configString': 'block_compressor=zlib'}}


@pytest.mark.parametrize('source', [
    'db.runCommand({dropDatabase:1})',
    '({wiredTiger:{configString:"block_compressor=zlib"}})',
    '{wiredTiger:{configString:"x"}}; db.dropDatabase()',
    '{wiredTiger:{configString:(function(){return "x"})()}}',
    '{wiredTiger:{configString:"x",configString:"y"}}',
    '{wiredTiger:{configString:Code("not executable")}}',
    '{wiredTiger:{configString:123}}',
    '{wiredTiger:{configString:"x",future:true}}',
    '{futureEngine:{configString:"x"}}',
    '[{wiredTiger:{configString:"x"}}]',
    '{' + 'a:{' * 90 + 'x:1' + '}' * 91,
])
def test_storage_literal_parser_rejects_expressions_extra_statements_and_unqualified_shapes(source):
    with pytest.raises(DatabaseError): parse_storage_option(source, 'storageEngine')


def test_storage_literal_parser_bounds_utf8_and_preserves_string_escapes():
    assert parse_storage_option(' \n ', 'storageEngine') is None
    assert parse_storage_option(r'{wiredTiger:{configString:"block_compressor=\u007alib"}}', 'storageEngine') == {
        'wiredTiger': {'configString': 'block_compressor=zlib'}}
    with pytest.raises(DatabaseError, match='1 MiB'):
        parse_storage_option(' ' * (MAX_STORAGE_TEXT_BYTES + 1), 'storageEngine')
    with pytest.raises(DatabaseError): parse_storage_option('{wiredTiger:{configString:"\ud800"}}', 'storageEngine')


def test_existing_collection_index_table_uses_reference_inline_controls(qtbot):
    definition = CollectionDefinition('orders', {})
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'orders')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot('demo_catalog', 'orders', definition, exists=True))
    page.set_indexes([
        {'name': '_id_', 'keys': [('_id', 1)], 'unique': True, 'system': True, 'options': {'v': 2}},
        {'name': 'orders_status', 'keys': [('status', 1)], 'unique': False,
         'options': {'background': True, 'sparse': True, 'expireAfterSeconds': 60, 'v': 2}},
    ])
    assert page.index_table.rowCount() == 1
    assert page.index_table.item(0, 0).text() == '' and not page.index_table.item(0, 0).icon().isNull()
    assert page.index_table.cellWidget(0, 1).text() == 'orders_status'
    assert page.index_table.cellWidget(0, 2).text() == 'status ASC'
    page.index_table.selectRow(0)
    assert not page.unique_index_check.isChecked()
    assert page.background_index_check.isChecked()
    assert page.sparse_index_check.isChecked()
    assert page.ttl_index_check.isChecked() and page.ttl_index_edit.text() == '60'
    assert page.new_index_button.isEnabled()
    page.new_index_button.click()
    assert page.index_table.rowCount() == 2 and page.dirty
    assert not page.save_action.isEnabled()
    page.delete_index_button.click()
    assert page.index_table.rowCount() == 1
    page.set_index_error('permission denied')
    assert 'permission denied' in page.index_message.text() and not page.index_message.isHidden()


def test_inline_index_rename_options_delete_and_key_summary_are_exact(qtbot):
    page = MongoCollectionDesignerPage('generated', 'demo_catalog', 'orders')
    qtbot.addWidget(page)
    page.load_snapshot(CollectionSnapshot(
        'demo_catalog', 'orders', CollectionDefinition('orders', {}), exists=True))
    page.set_indexes([{
        'name': 'orders_status', 'keys': [('status', 1)],
        'unique': False, 'options': {'v': 2},
    }])
    page.index_table.selectRow(0)
    name = page.index_table.cellWidget(0, 1)
    name.setText('orders_status_v2'); name.editingFinished.emit()
    page.unique_index_check.click()
    page.background_index_check.click()
    page.sparse_index_check.click()
    page.ttl_index_check.click(); page.ttl_index_edit.setText('120'); page.ttl_index_edit.editingFinished.emit()
    page.bucket_size_edit.setText('4.5'); page.bucket_size_edit.editingFinished.emit()
    drafts = page.inline_index_drafts()
    assert len(drafts) == 1 and drafts[0].original_name == 'orders_status'
    assert drafts[0].definition.name == 'orders_status_v2'
    assert drafts[0].definition.keys[0].field == 'status'
    assert drafts[0].definition.options == {
        'v': 2, 'unique': True, 'background': True, 'sparse': True,
        'expireAfterSeconds': 120, 'bucketSize': 4.5,
    }
    page.delete_index_button.click()
    assert page.inline_index_drafts()[0].deleted is True
    assert page.index_table.rowCount() == 0


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
