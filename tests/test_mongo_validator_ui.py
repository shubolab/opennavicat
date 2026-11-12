"""Actual detached widgets, independent mode buffers and lossless patches."""
from copy import deepcopy

from bson import BSON, Code, Decimal128, Int64
from PySide6.QtCore import Qt
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import BSON_CODEC
from opennavicat.ui.mongo_validator import BSON_TYPES, MongoValidatorEditor, BsonTypesDialog, ValidatorFragmentDialog


def raw(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


@pytest.fixture
def editor(qtbot):
    widget = MongoValidatorEditor()
    qtbot.addWidget(widget)
    widget.resize(1000, 700); widget.show()
    return widget


def add(editor, name='score', kind='int'):
    editor.add_property()
    row = editor.table.rowCount() - 1
    editor.table.item(row, 1).setText(name)
    if kind: editor.table.cellWidget(row, 2).setCurrentText(kind)
    editor.table.selectRow(row)
    return row


def test_initial_empty_schema_omits_validator_and_has_observed_headers_and_subpages(editor):
    assert editor.mode == 'schema' and editor.validator() is None
    assert [editor.mode_combo.itemText(i) for i in range(2)] == ['JSON Schema', 'Query']
    assert [editor.schema_tabs.tabText(i) for i in range(2)] == ['属性', '根模式']
    assert [editor.table.horizontalHeaderItem(i).text() for i in range(6)] == ['', '名称', 'BSON 类型', '必需', '标题', '描述']
    assert editor.can_add and not editor.can_delete
    assert [key for key in editor.root_fields if not key.endswith('_button')] == [
        'maxProperties', 'minProperties', 'additionalProperties', 'patternProperties', 'dependencies', 'title', 'description']


@pytest.mark.parametrize('kind', BSON_TYPES)
def test_all_22_types_have_their_actual_type_specific_constraints(editor, kind):
    add(editor, kind=kind)
    expected = {'enum'}
    if kind in {'decimal', 'double', 'int', 'long', 'number'}:
        expected |= {'multipleOf', 'maximum', 'exclusiveMaximum', 'minimum', 'exclusiveMinimum'}
    elif kind == 'array': expected |= {'items', 'additionalItems', 'maxItems', 'minItems', 'uniqueItems'}
    elif kind == 'object': expected |= {'maxProperties', 'minProperties', 'required', 'properties', 'additionalProperties', 'patternProperties', 'dependencies'}
    elif kind == 'string': expected |= {'maxLength', 'minLength', 'pattern'}
    assert {key for key in editor.constraint_fields if not key.endswith('_button')} == expected
    assert editor.validator()['$jsonSchema']['properties']['score']['bsonType'] == kind
    assert [editor.table.cellWidget(0, 2).itemText(i) for i in range(23)] == [*BSON_TYPES, '(Multiple)']


def test_score_required_title_description_and_new_numeric_double_constraints(editor):
    row = add(editor)
    editor.table.item(row, 3).setCheckState(Qt.CheckState.Checked)
    editor.table.item(row, 4).setText('Generated score')
    editor.table.item(row, 5).setText('Owned integer boundary')
    for key, value in [('multipleOf', '5'), ('maximum', '100'), ('minimum', '0')]:
        editor.constraint_fields[key].setText(value)
    schema = editor.validator()['$jsonSchema']
    assert schema['required'] == ['score']
    assert schema['properties']['score'] == {'bsonType': 'int', 'title': 'Generated score', 'description': 'Owned integer boundary',
                                            'multipleOf': 5.0, 'maximum': 100.0, 'minimum': 0.0}
    assert all(type(schema['properties']['score'][key]) is float for key in ('multipleOf', 'minimum', 'maximum'))
    assert 'exclusiveMaximum' not in schema['properties']['score']
    editor.constraint_fields['exclusiveMaximum'].setChecked(True)
    assert editor.validator()['$jsonSchema']['properties']['score']['exclusiveMaximum'] is True


def test_modes_preserve_separate_buffers_and_invalid_query_input(editor):
    add(editor); schema = raw(editor.validator())
    editor.mode_combo.setCurrentIndex(1)
    assert editor.query_editor.toPlainText() == '' and editor.validator() is None
    editor.query_editor.setPlainText('{score: {$gte: 20}}')
    assert type(editor.validator()['score']['$gte']) is float
    editor.mode_combo.setCurrentIndex(0)
    assert raw(editor.validator()) == schema
    editor.mode_combo.setCurrentIndex(1)
    assert editor.query_editor.toPlainText() == '{score: {$gte: 20}}'
    editor.query_editor.setPlainText('{broken')
    with pytest.raises(DatabaseError, match='line'): editor.validator()
    editor.mode_combo.setCurrentIndex(0)
    assert raw(editor.validator()) == schema
    editor.mode_combo.setCurrentIndex(1)
    assert editor.query_editor.toPlainText() == '{broken'
    with pytest.raises(DatabaseError): editor.validator()
    editor.query_editor.setPlainText('{score: {$gte: Int32(20)}}')
    assert type(editor.validator()['score']['$gte']) is int


def test_existing_unknown_rules_and_exact_bson_types_survive_title_edit(editor):
    document = {'$jsonSchema': {'future': {'typed': Int64(9007199254740993)}, 'required': ['score', 'unmapped'],
        'properties': {'score': {'bsonType': ['long', 'null'], 'minimum': Int64(9007199254740993),
            'enum': [Decimal128('1.00'), {'$date': 'literal'}], 'futureProperty': Code('owned data')}}}}
    editor.load_validator(document); editor.table.selectRow(0)
    assert raw(editor.validator()) == raw(document)
    editor.table.item(0, 4).setText('Changed title')
    expected = deepcopy(document); expected['$jsonSchema']['properties']['score']['title'] = 'Changed title'
    assert raw(editor.validator()) == raw(expected)
    editor.table.item(0, 1).setText('renamed')
    with pytest.raises(DatabaseError, match='unresolved'): editor.validator()
    editor.table.item(0, 1).setText('score')
    assert raw(editor.validator()) == raw(expected)


def test_unmapped_required_names_preserved_when_a_new_property_is_named(editor):
    editor.load_validator({'$jsonSchema': {'required': ['future', 'invisible']}})
    add(editor, 'future', 'string')
    assert editor.validator()['$jsonSchema']['required'] == ['future', 'invisible']
    assert editor.table.item(0, 3).checkState() == Qt.CheckState.Checked


def test_invalid_numeric_text_survives_selection_and_only_its_repair_unblocks(editor):
    add(editor, 'first', 'int'); editor.constraint_fields['minimum'].setText('1 + 2')
    add(editor, 'second', 'int'); editor.constraint_fields['maximum'].setText('100')
    with pytest.raises(DatabaseError): editor.validator()
    editor.table.selectRow(0)
    assert editor.constraint_fields['minimum'].text() == '1 + 2'
    editor.constraint_fields['minimum'].setText('3')
    assert editor.validator()['$jsonSchema']['properties']['first']['minimum'] == 3.0


def test_multiple_picker_and_untouched_original_type_array_order(qtbot, editor):
    add(editor, kind='(Multiple)'); editor.constraint_fields['bsonType_button'].click()
    dialog = editor.types_dialog
    assert isinstance(dialog, BsonTypesDialog) and dialog.windowTitle() == 'BSON 类型'
    for kind in ('int', 'null'):
        dialog.table.item(BSON_TYPES.index(kind), 0).setCheckState(Qt.CheckState.Checked)
    dialog.accept(); qtbot.waitUntil(lambda: editor.types_dialog is None)
    assert editor.validator()['$jsonSchema']['properties']['score']['bsonType'] == ['int', 'null']
    retained = BsonTypesDialog(['null', 'futureAlias', 'int'])
    qtbot.addWidget(retained); retained.accept()
    assert retained.selected == ['null', 'futureAlias', 'int']


def test_root_and_object_raw_fragment_apply_only_selected_keyword_and_cancel(qtbot, editor):
    original = {'$jsonSchema': {'dependencies': {'unrelated': ['kept']}, 'required': ['unmapped'],
        'properties': {'nested': {'bsonType': 'object', 'future': Int64(42)}}}}
    editor.load_validator(original); editor.table.selectRow(0)
    editor.constraint_fields['properties_button'].click(); dialog = editor.fragment_dialog
    assert isinstance(dialog, ValidatorFragmentDialog) and dialog.windowTitle() == 'Window'
    dialog.editor.setPlainText('{child: {bsonType:"string", minLength:2}}')
    dialog.reject(); qtbot.waitUntil(lambda: editor.fragment_dialog is None)
    assert raw(editor.validator()) == raw(original)
    editor.constraint_fields['properties_button'].click()
    editor.fragment_dialog.editor.setPlainText('{child: {bsonType:"string", minLength:2}}')
    editor.fragment_dialog.accept(); qtbot.waitUntil(lambda: editor.fragment_dialog is None)
    actual = editor.validator()['$jsonSchema']
    assert actual['properties']['nested']['properties'] == {'child': {'bsonType': 'string', 'minLength': 2.0}}
    assert type(actual['properties']['nested']['future']) is Int64
    assert actual['dependencies'] == original['$jsonSchema']['dependencies'] and actual['required'] == ['unmapped']
    editor.root_fields['additionalProperties_button'].click()
    editor.fragment_dialog.editor.setPlainText('false'); editor.fragment_dialog.accept()
    qtbot.waitUntil(lambda: editor.fragment_dialog is None)
    assert editor.validator()['$jsonSchema']['additionalProperties'] is False


def test_fragment_bad_syntax_and_wrong_shape_stay_open(qtbot, editor):
    add(editor, kind='array'); editor.constraint_fields['items_button'].click(); dialog = editor.fragment_dialog
    dialog.editor.setPlainText('{broken'); dialog.accept()
    assert editor.fragment_dialog is dialog and dialog.error.text()
    dialog.editor.setPlainText('123'); dialog.accept()
    assert editor.fragment_dialog is dialog and dialog.error.text()
    assert 'items' not in editor.validator()['$jsonSchema']['properties']['score']
    dialog.editor.setPlainText('{bsonType:"string"}'); dialog.accept()
    qtbot.waitUntil(lambda: editor.fragment_dialog is None)
    assert editor.validator()['$jsonSchema']['properties']['score']['items'] == {'bsonType': 'string'}


def test_readonly_widgets_emit_no_edits_and_preserve_bson(editor):
    add(editor); before = raw(editor.validator()); changes = []
    editor.changed.connect(lambda: changes.append(True)); editor.set_read_only(True)
    editor.add_property(); editor.delete_property(); editor.query_editor.setPlainText('{x:1}')
    assert not editor.can_add and not editor.can_delete and not changes and raw(editor.validator()) == before


def test_collection_toolbar_modes_and_parser_errors_block_sealed_save(qtbot):
    from opennavicat.mongodb_collections import CollectionDefinition, CollectionSnapshot
    from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
    page = MongoCollectionDesignerPage('generated', 'demo_catalog')
    qtbot.addWidget(page); page.load_snapshot(CollectionSnapshot('demo_catalog', '', CollectionDefinition(), exists=False))
    page.tabs.setCurrentWidget(page.validation)
    assert page.validator_add_action.isVisible() and page.validator_add_action.isEnabled()
    assert not page.validator_delete_action.isEnabled() and 'validator' not in page.draft().options
    page.validator_add_action.trigger()
    assert page.validator_delete_action.isEnabled() and page._invalid
    page.validator_widget.table.item(0, 1).setText('score')
    assert not page._invalid
    page.validator_widget.mode_combo.setCurrentIndex(1)
    assert not page.validator_add_action.isVisible() and not page.validator_delete_action.isVisible()
    page.validator_editor.setPlainText('{broken'); page.request_save()
    assert page._save_dialog is None and page._invalid and not page.save_action.isEnabled()
    page.validator_editor.setPlainText('{score:{$gte:20}}')
    assert not page._invalid and page.draft().options['validator'] == {'score': {'$gte': 20.0}}


def test_clearing_invalid_absent_numeric_rule_clears_parent_save_block(qtbot):
    from opennavicat.mongodb_collections import CollectionDefinition, CollectionSnapshot
    from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
    page = MongoCollectionDesignerPage('generated', 'demo_catalog')
    qtbot.addWidget(page); page.load_snapshot(CollectionSnapshot('demo_catalog', '', CollectionDefinition(), exists=False))
    editor = page.validator_widget
    add(editor)
    editor.constraint_fields['minimum'].setText('invalid')
    assert page._invalid and not page.save_action.isEnabled()
    editor.constraint_fields['minimum'].clear()
    assert not page._invalid and page.save_action.isEnabled()
    assert 'minimum' not in page.draft().options['validator']['$jsonSchema']['properties']['score']
    editor.root_fields['maxProperties'].setText('invalid')
    assert page._invalid
    editor.root_fields['maxProperties'].clear()
    assert not page._invalid and page.save_action.isEnabled()


def test_unprojectable_deep_existing_rule_is_retained_readonly_not_replaced_by_empty_schema(editor):
    value = {'leaf': Int64(9007199254740993)}
    for _ in range(70): value = {'nested': value}
    editor.load_validator(value); editor.set_read_only(False)
    assert editor.mode == 'query' and editor.query_editor.isReadOnly()
    assert raw(editor.validator()) == raw(value)
    assert '只读' in editor.message.text()
