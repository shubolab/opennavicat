"""Actual validation-design routes against only a fresh owned MongoDB service."""
from copy import deepcopy
import threading

from bson import BSON
from pymongo.errors import OperationFailure
import pytest
from PySide6.QtCore import Qt

import opennavicat.ui.window as window_module
from opennavicat.mongodb_session import BSON_CODEC
from test_mongodb_session import mongodb_service
from test_mongo_window import idle, mongo_window


SCORE_VALIDATOR = {'$jsonSchema': {
    'properties': {'score': {'bsonType': 'int', 'title': 'Generated score',
        'description': 'Owned integer boundary', 'multipleOf': 5.0, 'maximum': 100.0, 'minimum': 0.0}},
    'required': ['score'],
}}
OBSERVED_BSON_TYPES = (
    'array', 'binData', 'bool', 'date', 'dbPointer', 'decimal', 'double', 'int',
    'javascript', 'javascriptWithScope', 'long', 'maxKey', 'minKey', 'null',
    'number', 'object', 'objectId', 'regex', 'string', 'symbol', 'timestamp', 'undefined',
)


def catalog(database, name):
    with database.list_collections(filter={'name': name}) as cursor:
        values = list(cursor)
    assert len(values) == 1
    return values[0]


def configure_score(qtbot, window, config):
    page = window.open_mongo_collection_designer(config.id, config.database)
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=15000)
    idle(qtbot, window)
    page.tabs.setCurrentWidget(page.validation)
    editor = page.validator_widget
    assert editor.mode_combo.currentIndex() == 0 and editor.schema_tabs.currentIndex() == 0
    editor.add_property()
    # A pending visual row is a draft, never an implicit valid empty schema.
    page.request_save()
    assert page._save_dialog is None
    row = editor.table.rowCount() - 1
    editor.table.item(row, 1).setText('score')
    editor.table.cellWidget(row, 2).setCurrentText('int')
    editor.table.selectRow(row)
    editor.table.item(row, 3).setCheckState(Qt.CheckState.Checked)
    editor.table.item(row, 4).setText('Generated score')
    editor.table.item(row, 5).setText('Owned integer boundary')
    for key, value in [('multipleOf', '5'), ('maximum', '100'), ('minimum', '0')]:
        field = editor.constraint_fields[key]
        field.setText(value)
        field.editingFinished.emit()
    page.level_combo.setCurrentIndex(page.level_combo.findData('strict'))
    page.action_combo.setCurrentIndex(page.action_combo.findData('error'))
    assert BSON.encode(page.draft().options['validator'], codec_options=BSON_CODEC) == BSON.encode(SCORE_VALIDATOR, codec_options=BSON_CODEC)
    return page


def create_score(qtbot, window, config, name):
    page = configure_score(qtbot, window, config)
    page.request_save()
    dialog = page._save_dialog
    assert dialog is not None and not dialog.save_button.isEnabled()
    dialog.name_edit.setText(name)
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    assert dialog.plan.commands[0]['validator'] == SCORE_VALIDATOR
    dialog.save_button.click()
    idle(qtbot, window)
    assert page.snapshot.exists and page.snapshot.name == name and not page.dirty
    return page


def set_fragment(qtbot, editor, key, text):
    editor.constraint_fields[key + '_button'].click()
    dialog = editor.fragment_dialog
    assert dialog is not None and dialog.windowTitle() == 'Window'
    dialog.editor.setPlainText(text)
    dialog.accept()
    qtbot.waitUntil(lambda: editor.fragment_dialog is None)


def selected_property(editor, name):
    row = next(row for row in range(editor.table.rowCount()) if editor.table.item(row, 1).text() == name)
    editor.table.selectRow(row)
    return row


def rule_names(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'operatorName' and isinstance(child, str):
                yield child
            yield from rule_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from rule_names(child)


def test_real_window_visual_score_schema_cancel_create_and_enforce_exact_boundaries(qtbot, mongo_window, monkeypatch):
    window, config, database, schema, errors, threads = mongo_window
    catalog_threads = []
    original = window_module.mongo_collection_tools._catalog
    def worker_catalog(*args, **kwargs):
        catalog_threads.append(threading.get_ident())
        assert threading.get_ident() != threading.main_thread().ident
        return original(*args, **kwargs)
    monkeypatch.setattr(window_module.mongo_collection_tools, '_catalog', worker_catalog)
    page = configure_score(qtbot, window, config)
    before = deepcopy(page.draft())
    page.request_save()
    cancelled = page._save_dialog
    cancelled.name_edit.setText('cancelled_score_schema')
    qtbot.waitUntil(cancelled.save_button.isEnabled, timeout=15000)
    assert cancelled.plan.commands[0]['validator'] == SCORE_VALIDATOR
    assert '$jsonSchema' in cancelled.preview.toPlainText() and 'Generated score' in cancelled.preview.toPlainText()
    cancelled.reject()
    qtbot.waitUntil(lambda: page._save_dialog is None)
    idle(qtbot, window)
    assert 'cancelled_score_schema' not in database.list_collection_names()
    assert page.draft() == before and page.dirty

    page.request_save()
    dialog = page._save_dialog
    dialog.name_edit.setText('score_schema_ui')
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    dialog.save_button.click()
    idle(qtbot, window)
    assert page.snapshot.exists and not page.dirty
    actual = catalog(database, 'score_schema_ui')
    assert BSON.encode(actual['options']['validator'], codec_options=BSON_CODEC) == BSON.encode(SCORE_VALIDATOR, codec_options=BSON_CODEC)
    assert actual['options']['validationLevel'] == 'strict' and actual['options']['validationAction'] == 'error'
    rule = actual['options']['validator']['$jsonSchema']['properties']['score']
    assert all(type(rule[key]) is float for key in ('multipleOf', 'maximum', 'minimum'))
    collection = database.score_schema_ui
    for value in (0, 5, 100):
        collection.insert_one({'score': value})
    for invalid in ({}, {'score': '5'}, {'score': -5}, {'score': 105}, {'score': 3}):
        with pytest.raises(OperationFailure) as failure:
            collection.insert_one(invalid)
        assert failure.value.code == 121
    assert collection.count_documents({}) == 3
    assert catalog_threads and threads and not errors


def test_real_window_query_validation_errors_cancel_collmod_and_reopen_preserve_uuid_and_documents(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    page = create_score(qtbot, window, config, 'query_validation_ui')
    collection = database.query_validation_ui
    collection.insert_many([{'score': value} for value in (0, 5, 100)])
    before = catalog(database, 'query_validation_ui')
    namespace_uuid = before['info']['uuid']
    original_validator = BSON.encode(before['options']['validator'], codec_options=BSON_CODEC)
    original_documents = [BSON.encode(value, codec_options=BSON_CODEC) for value in collection.find().sort('_id')]
    editor = page.validator_widget
    editor.mode_combo.setCurrentIndex(1)
    assert page.validator_editor.toPlainText() == ''
    editor.mode_combo.setCurrentIndex(0)
    assert page.draft().options['validator'] == SCORE_VALIDATOR
    editor.mode_combo.setCurrentIndex(1)

    page.validator_editor.setPlainText('{broken')
    page.request_save()
    assert page._save_dialog is None and page.dirty
    assert page.validator_editor.toPlainText() == '{broken'
    assert 'line' in page.message.text() or '行' in page.message.text()
    assert BSON.encode(catalog(database, 'query_validation_ui')['options']['validator'], codec_options=BSON_CODEC) == original_validator

    unknown_text = '{score: {$unknown: 1}}'
    page.validator_editor.setPlainText(unknown_text)
    page.request_save()
    dialog = page._save_dialog
    assert dialog is not None and dialog.name_edit.isHidden()
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    assert dialog.plan.commands[0]['validator'] == {'score': {'$unknown': 1.0}}
    assert dialog.plan.commands[0]['collMod'] == 'query_validation_ui'
    dialog.save_button.click()
    idle(qtbot, window)
    assert page.dirty and not page.loading and page.validator_editor.toPlainText() == unknown_text
    assert '服务器报错' in page.message.text() and '"code"' in page.message.text()
    failed = catalog(database, 'query_validation_ui')
    assert failed['info']['uuid'] == namespace_uuid
    assert BSON.encode(failed['options']['validator'], codec_options=BSON_CODEC) == original_validator
    assert [BSON.encode(value, codec_options=BSON_CODEC) for value in collection.find().sort('_id')] == original_documents

    page.validator_editor.setPlainText('{score: {$gte: 20}}')
    page.request_save()
    cancelled = page._save_dialog
    qtbot.waitUntil(cancelled.save_button.isEnabled, timeout=15000)
    assert cancelled.plan.commands[0]['validator'] == {'score': {'$gte': 20.0}}
    cancelled.reject()
    qtbot.waitUntil(lambda: page._save_dialog is None)
    idle(qtbot, window)
    assert BSON.encode(catalog(database, 'query_validation_ui')['options']['validator'], codec_options=BSON_CODEC) == original_validator
    assert page.dirty and page.validator_editor.toPlainText() == '{score: {$gte: 20}}'

    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    dialog.save_button.click()
    idle(qtbot, window)
    saved = catalog(database, 'query_validation_ui')
    assert saved['info']['uuid'] == namespace_uuid
    assert saved['options']['validator'] == {'score': {'$gte': 20.0}}
    assert type(saved['options']['validator']['score']['$gte']) is float
    assert saved['options']['validationLevel'] == 'strict' and saved['options']['validationAction'] == 'error'
    assert [BSON.encode(value, codec_options=BSON_CODEC) for value in collection.find().sort('_id')] == original_documents
    collection.insert_one({'score': 20})
    with pytest.raises(OperationFailure) as failure:
        collection.insert_one({'score': 19})
    assert failure.value.code == 121 and collection.count_documents({}) == 4

    window.close_tab(window.tabs.indexOf(page))
    reopened = window.open_mongo_collection_designer(config.id, config.database, 'query_validation_ui')
    qtbot.waitUntil(lambda: reopened.snapshot is not None and not reopened.loading, timeout=15000)
    idle(qtbot, window)
    assert reopened is not page and reopened.tabs.currentIndex() == 0
    assert reopened.validator_widget.mode_combo.currentIndex() == 1
    assert reopened.snapshot.namespace_uuid == namespace_uuid and not reopened.dirty
    assert 'score' in reopened.validator_editor.toPlainText() and '20' in reopened.validator_editor.toPlainText()
    assert reopened.level_combo.currentData() == 'strict' and reopened.action_combo.currentData() == 'error'
    assert threads and not errors


def test_real_window_all_types_nested_fragments_multiple_and_isolated_string_boundaries(qtbot, mongo_window):
    window, config, database, schema, errors, threads = mongo_window
    page = window.open_mongo_collection_designer(config.id, config.database)
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=15000)
    idle(qtbot, window)
    page.tabs.setCurrentWidget(page.validation)
    editor = page.validator_widget
    expected_properties = {}
    for kind in OBSERVED_BSON_TYPES:
        editor.add_property()
        row = editor.table.rowCount() - 1
        name = 'p_' + kind
        editor.table.item(row, 1).setText(name)
        combo = editor.table.cellWidget(row, 2)
        assert [combo.itemText(i) for i in range(combo.count())] == [*OBSERVED_BSON_TYPES, '(Multiple)']
        combo.setCurrentText(kind)
        expected_properties[name] = {'bsonType': kind}
    assert editor.table.rowCount() == 22

    selected_property(editor, 'p_array')
    set_fragment(qtbot, editor, 'items', '{bsonType:"object",required:["code"],properties:{code:{bsonType:"string"}}}')
    set_fragment(qtbot, editor, 'additionalItems', 'false')
    editor.constraint_fields['maxItems'].setText('4')
    editor.constraint_fields['minItems'].setText('1')
    editor.constraint_fields['uniqueItems'].setChecked(True)
    expected_properties['p_array'].update(
        items={'bsonType': 'object', 'required': ['code'], 'properties': {'code': {'bsonType': 'string'}}},
        additionalItems=False, maxItems=4.0, minItems=1.0, uniqueItems=True)

    selected_property(editor, 'p_object')
    set_fragment(qtbot, editor, 'properties', '{child:{bsonType:"string",minLength:2}}')
    expected_properties['p_object']['properties'] = {'child': {'bsonType': 'string', 'minLength': 2.0}}

    selected_property(editor, 'p_string')
    set_fragment(qtbot, editor, 'enum', '["AA","BB"]')
    editor.constraint_fields['maxLength'].setText('4')
    editor.constraint_fields['minLength'].setText('2')
    editor.constraint_fields['pattern'].setText('^[A-Z]+$')
    expected_properties['p_string'].update(enum=['AA', 'BB'], maxLength=4.0, minLength=2.0, pattern='^[A-Z]+$')

    row = selected_property(editor, 'p_undefined')
    editor.table.cellWidget(row, 2).setCurrentText('(Multiple)')
    editor.constraint_fields['bsonType_button'].click()
    types_dialog = editor.types_dialog
    assert types_dialog is not None and types_dialog.windowTitle() == 'BSON 类型'
    assert types_dialog.table.rowCount() == 22
    assert all(types_dialog.table.item(i, 0).checkState() == Qt.CheckState.Unchecked for i in range(22))
    for kind in ('int', 'null'):
        types_dialog.table.item(OBSERVED_BSON_TYPES.index(kind), 0).setCheckState(Qt.CheckState.Checked)
    types_dialog.accept()
    qtbot.waitUntil(lambda: editor.types_dialog is None)
    expected_properties['p_undefined']['bsonType'] = ['int', 'null']
    expected = {'$jsonSchema': {'properties': expected_properties}}
    assert BSON.encode(editor.validator(), codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
    page.level_combo.setCurrentIndex(page.level_combo.findData('strict'))
    page.action_combo.setCurrentIndex(page.action_combo.findData('error'))
    page.request_save()
    dialog = page._save_dialog
    dialog.name_edit.setText('nested_schema_ui')
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    assert BSON.encode(dialog.plan.commands[0]['validator'], codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
    dialog.save_button.click()
    idle(qtbot, window)
    assert page.snapshot.exists and not page.dirty
    actual = catalog(database, 'nested_schema_ui')
    namespace_uuid = actual['info']['uuid']
    assert BSON.encode(actual['options']['validator'], codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
    collection = database.nested_schema_ui
    collection.insert_many([
        {'p_array': [{'code': 'generated'}], 'p_object': {'child': 'AB'}, 'p_string': 'AA', 'p_undefined': None},
        {'p_undefined': 5},
    ])
    invalid_documents = [
        {'p_array': []},
        {'p_array': [{'code': name} for name in ('A', 'B', 'C', 'D', 'E')]},
        {'p_array': [{'code': 'A'}, {'code': 'A'}]},
        {'p_array': [{}]},
        {'p_array': [{'code': 2}]},
        {'p_object': {'child': 'A'}},
        {'p_object': {'child': 2}},
        {'p_string': 'CC'}, {'p_string': 'A'}, {'p_string': 'AAAAA'}, {'p_string': 'aa'},
        {'p_undefined': 'generated'},
    ]
    assert len(invalid_documents) == 12
    for document in invalid_documents:
        with pytest.raises(OperationFailure) as failure:
            collection.insert_one(document)
        assert failure.value.code == 121
    assert collection.count_documents({}) == 2

    window.close_tab(window.tabs.indexOf(page))
    reopened = window.open_mongo_collection_designer(config.id, config.database, 'nested_schema_ui')
    qtbot.waitUntil(lambda: reopened.snapshot is not None and not reopened.loading, timeout=15000)
    idle(qtbot, window)
    assert reopened is not page and reopened.tabs.currentIndex() == 0
    reopened.tabs.setCurrentWidget(reopened.validation)
    editor = reopened.validator_widget
    assert editor.mode_combo.currentIndex() == 0 and editor.table.rowCount() == 22
    assert BSON.encode(editor.validator(), codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
    selected_property(editor, 'p_array')
    assert float(editor.constraint_fields['maxItems'].text()) == 4.0
    assert float(editor.constraint_fields['minItems'].text()) == 1.0
    assert editor.constraint_fields['uniqueItems'].isChecked()
    selected_property(editor, 'p_object')
    assert 'child' in editor.constraint_fields['properties'].text()
    row = selected_property(editor, 'p_undefined')
    assert editor.table.cellWidget(row, 2).currentText() == '(Multiple)'
    selected_property(editor, 'p_string')
    assert editor.constraint_fields['pattern'].text() == '^[A-Z]+$'
    editor.constraint_fields['enum_button'].click()
    fragment = editor.fragment_dialog
    assert 'AA' in fragment.editor.toPlainText() and 'BB' in fragment.editor.toPlainText()
    fragment.editor.clear()
    fragment.accept()
    qtbot.waitUntil(lambda: editor.fragment_dialog is None)
    expected_properties['p_string'].pop('enum')
    assert reopened.dirty
    reopened.request_save()
    dialog = reopened._save_dialog
    qtbot.waitUntil(dialog.save_button.isEnabled, timeout=15000)
    assert dialog.plan.commands[0]['collMod'] == 'nested_schema_ui'
    assert set(dialog.plan.commands[0]) == {'collMod', 'collectionUUID', 'validator', 'writeConcern'}
    dialog.save_button.click()
    idle(qtbot, window)
    assert not reopened.dirty
    saved = catalog(database, 'nested_schema_ui')
    assert saved['info']['uuid'] == namespace_uuid
    assert BSON.encode(saved['options']['validator'], codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
    assert collection.count_documents({}) == 2
    collection.insert_many([{'p_string': 'CC'}, {'p_string': 'ABCD'}])
    for value, rule in [('A', 'minLength'), ('AAAAA', 'maxLength'), ('aa', 'pattern')]:
        with pytest.raises(OperationFailure) as failure:
            collection.insert_one({'p_string': value})
        assert failure.value.code == 121
        observed = set(rule_names(failure.value.details)) & {'minLength', 'maxLength', 'pattern', 'enum'}
        assert observed == {rule}
    assert collection.count_documents({}) == 4
    assert threads and not errors
