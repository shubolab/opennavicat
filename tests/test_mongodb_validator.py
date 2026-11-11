"""Exact BSON and atomic draft edits; no database, UI or Extended JSON parser."""
from copy import deepcopy
from datetime import datetime, timezone
import struct

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import BSON_CODEC
from opennavicat.mongodb_validator import (
    AddProperty, RemoveKeyword, RemoveProperty, RenameProperty, SetKeyword,
    UnresolvedSchemaDependencyError, ValidatorDraft, ValidatorLimits,
)


SCHEMA = ('$jsonSchema',)


def encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def sample():
    return {'$jsonSchema': {
        'bsonType': ['object', 'null'],
        'title': 'Generated schema',
        'properties': {
            'first': {'bsonType': ['long', 'null'], 'minimum': Int64(2),
                      'enum': [Int64(3), Decimal128('1.00'), Binary(b'\x00\xff', 128)]},
            'second': {'bsonType': 'object', 'properties': {
                '$date': {'bsonType': 'string', 'description': 'literal property'},
                '$oid': {'bsonType': 'string'},
            }},
        },
        'required': ['unmapped-before', 'first', 'unmapped-after'],
        'description': 'Preserve me',
    }}


@pytest.mark.parametrize('value,visual', [
    ({'$jsonSchema': {'properties': {}}}, True),
    ({'$jsonSchema': {}}, True),
    ({'$jsonSchema': {}, 'status': {'$eq': 'active'}}, False),
    ({'$and': [{'$jsonSchema': {}}]}, False),
    ({'$jsonSchema': []}, False),
    ({'literal': {'$date': 'not a date', '$oid': 'not an object id'}}, False),
])
def test_only_exact_schema_projects_and_projection_never_mutates_query(value, visual):
    raw = encoded(value)
    draft = ValidatorDraft(raw)
    assert (draft.visual_schema is not None) is visual
    assert draft.document == value and draft.original_raw == draft.raw_bson == raw
    if visual:
        projected = draft.visual_schema
        projected['invented'] = True
        assert 'invented' not in draft.visual_schema
    else:
        with pytest.raises(DatabaseError, match='exact single'):
            draft.apply([AddProperty(SCHEMA, 'new', {})])
    assert draft.raw_bson == raw and not draft.changed


def test_set_remove_preserves_order_unknown_siblings_and_all_typed_bson():
    original = sample()
    original['$jsonSchema']['future'] = {
        '$date': 'literal data', '$oid': 'also literal data', 'long': Int64(1),
        'decimal': Decimal128('9.000'), 'binary': Binary(b'\x00\xff', 200),
        'date': datetime(2026, 11, 12, tzinfo=timezone.utc), 'oid': ObjectId(),
    }
    raw = encoded(original)
    draft = ValidatorDraft(original)
    original['$jsonSchema']['future']['long'] = 4
    result = draft.apply([
        SetKeyword((*SCHEMA, 'properties', 'first', 'title'), 'Named'),
        SetKeyword((*SCHEMA, 'properties', 'first', 'maximum'), 12.5),
        RemoveKeyword((*SCHEMA, 'description')),
    ])
    expected = BSON(raw).decode(codec_options=BSON_CODEC)
    expected['$jsonSchema']['properties']['first']['title'] = 'Named'
    expected['$jsonSchema']['properties']['first']['maximum'] = 12.5
    del expected['$jsonSchema']['description']
    assert draft.raw_bson == encoded(expected) and draft.original_raw == raw and draft.changed
    assert type(result['$jsonSchema']['properties']['first']['minimum']) is Int64
    assert type(result['$jsonSchema']['properties']['first']['maximum']) is float
    assert encoded(result['$jsonSchema']['future']) == encoded(expected['$jsonSchema']['future'])
    result['$jsonSchema']['properties'].clear()
    assert draft.raw_bson == encoded(expected)


def test_ordered_property_add_rename_delete_preserves_types_and_unmapped_required_names():
    source = sample()
    draft = ValidatorDraft(source)
    draft.apply([AddProperty(SCHEMA, 'middle', {'bsonType': 'decimal', 'minimum': Decimal128('1.0')},
                             before='second', required=True)])
    assert list(draft.visual_schema['properties']) == ['first', 'middle', 'second']
    assert draft.visual_schema['required'] == ['unmapped-before', 'first', 'unmapped-after', 'middle']
    draft.apply([RenameProperty(SCHEMA, 'first', 'renamed'), RemoveProperty(SCHEMA, 'middle')])
    observed = draft.visual_schema
    assert list(observed['properties']) == ['renamed', 'second']
    assert observed['required'] == ['unmapped-before', 'renamed', 'unmapped-after']
    assert encoded(observed['properties']['renamed']) == encoded(source['$jsonSchema']['properties']['first'])
    assert observed['properties']['renamed']['bsonType'] == ['long', 'null']
    assert observed['properties']['second']['bsonType'] == 'object'


@pytest.mark.parametrize('required', [None, [], ['unmapped'], ['old'], ['old', 'new', 'unmapped']])
def test_required_rename_and_delete_preserve_presence_and_unmapped_members(required):
    schema = {'properties': {'old': {}}}
    if required is not None:
        schema['required'] = required
    draft = ValidatorDraft({'$jsonSchema': schema})
    draft.apply([RenameProperty(SCHEMA, 'old', 'new')])
    after = draft.visual_schema
    assert ('required' in after) is (required is not None)
    if required is not None:
        expected = [name for name in required if name != 'old'] if 'new' in required else [
            'new' if name == 'old' else name for name in required]
        assert after['required'] == expected
    draft.apply([RemoveProperty(SCHEMA, 'new')])
    assert ('required' in draft.visual_schema) is (required is not None)
    if required is not None:
        assert draft.visual_schema['required'] == [name for name in required if name not in {'old', 'new'}]


def test_add_required_membership_is_explicit_and_keeps_preexisting_unmapped_names():
    draft = ValidatorDraft({'$jsonSchema': {'required': ['future', 'keep']}})
    draft.apply([AddProperty(SCHEMA, 'future', {'bsonType': 'string'})])
    assert draft.visual_schema['required'] == ['future', 'keep']
    draft.apply([AddProperty(SCHEMA, 'keep', {}, required=False)])
    assert draft.visual_schema['required'] == ['future']
    omitted = ValidatorDraft({'$jsonSchema': {}})
    omitted.apply([AddProperty(SCHEMA, 'optional', {}, required=False)])
    assert 'required' not in omitted.visual_schema


def test_literal_ejson_property_names_and_nested_required_are_not_interpreted():
    draft = ValidatorDraft(sample())
    nested = (*SCHEMA, 'properties', 'second')
    draft.apply([SetKeyword((*nested, 'required'), ['$date', 'unmapped']),
                 RenameProperty(nested, '$date', '$oid-renamed')])
    observed = draft.visual_schema['properties']['second']
    assert list(observed['properties']) == ['$oid-renamed', '$oid']
    assert observed['required'] == ['$oid-renamed', 'unmapped']
    assert observed['properties']['$oid-renamed']['description'] == 'literal property'


@pytest.mark.parametrize('keyword,value', [
    ('dependencies', {'first': ['second']}),
    ('dependentRequired', {'first': ['second']}),
    ('dependentSchemas', {'first': {'required': ['second']}}),
    ('patternProperties', {'^first$': {'bsonType': 'long'}}),
    ('allOf', [{'required': ['first']}]),
    ('$ref', '#/definitions/first'),
    ('futureNameRule', {'first': Binary(b'unknown', 128)}),
    ('enum', [{'first': Int64(1)}]),
])
@pytest.mark.parametrize('operation', [RenameProperty(SCHEMA, 'first', 'new'), RemoveProperty(SCHEMA, 'first')])
def test_unresolved_name_dependencies_refuse_atomic_rewrites_and_keep_full_raw(keyword, value, operation):
    document = sample()
    document['$jsonSchema'][keyword] = value
    draft = ValidatorDraft(document)
    before = draft.raw_bson
    with pytest.raises(UnresolvedSchemaDependencyError, match=keyword.replace('$', r'\$')):
        draft.apply([SetKeyword((*SCHEMA, 'title'), 'must roll back'), operation])
    assert draft.raw_bson == draft.original_raw == before and not draft.changed
    assert encoded({'value': draft.document['$jsonSchema'][keyword]}) == encoded({'value': value})


def test_ancestor_dependency_blocks_nested_rename_without_changing_any_subtree():
    document = sample()
    document['$jsonSchema']['allOf'] = [{'properties': {'second': {'required': ['$date']}}}]
    draft = ValidatorDraft(document)
    with pytest.raises(UnresolvedSchemaDependencyError, match='allOf'):
        draft.apply([RenameProperty((*SCHEMA, 'properties', 'second'), '$date', 'replacement')])
    assert draft.raw_bson == encoded(document)


@pytest.mark.parametrize('name', ['properties', 'items', '$jsonSchema'])
def test_schema_keyword_spelling_in_a_property_name_does_not_bypass_dependencies(name):
    document = {'$jsonSchema': {'properties': {name: {
        'properties': {'old': {}}, 'dependencies': {'old': ['unmapped']},
    }}}}
    draft = ValidatorDraft(document)
    with pytest.raises(UnresolvedSchemaDependencyError, match='dependencies'):
        draft.apply([RenameProperty((*SCHEMA, 'properties', name), 'old', 'new')])
    assert draft.raw_bson == encoded(document)


def test_nested_property_named_properties_and_scalar_annotations_remain_editable():
    parent = (*SCHEMA, 'properties', 'properties')
    document = {'$jsonSchema': {'title': 'Root', '$comment': 'Keep annotation', 'properties': {
        'properties': {'description': 'Nested', 'properties': {'old': {'bsonType': 'int'}}},
    }}}
    draft = ValidatorDraft(document)
    draft.apply([AddProperty(parent, 'new', {'bsonType': 'string'}),
                 RenameProperty(parent, 'old', 'renamed')])
    expected = deepcopy(document)
    expected['$jsonSchema']['properties']['properties']['properties'] = {
        'renamed': {'bsonType': 'int'}, 'new': {'bsonType': 'string'},
    }
    assert draft.raw_bson == encoded(expected)


@pytest.mark.parametrize('bad_patch', [
    SetKeyword(('$jsonSchema', 'missing', 'title'), 'bad parent'),
    SetKeyword(['$jsonSchema', 'title'], 'list path'),
    SetKeyword(('$jsonSchema', 'enum', True), 1),
    SetKeyword(('$jsonSchema', 'title'), object()),
    SetKeyword(('$jsonSchema', 'title'), 2**64),
    SetKeyword(('$jsonSchema', 'title'), (1, 2)),
    SetKeyword(('$jsonSchema', 'title'), datetime(2026, 11, 12, microsecond=1)),
    AddProperty(SCHEMA, 'first', {}),
    AddProperty(SCHEMA, 'new', {}, before='missing'),
    AddProperty(SCHEMA, 'new', {}, before=[]),
    AddProperty((*SCHEMA, 'properties'), 'misplaced', {}),
    RenameProperty(SCHEMA, 'first', 'second'),
    RemoveProperty(SCHEMA, 'missing'),
    RemoveKeyword(('$jsonSchema', 'missing')),
    RemoveKeyword((*SCHEMA, 'properties', 'first')),
    SetKeyword((*SCHEMA, 'properties', 'first'), {}),
    SetKeyword((*SCHEMA, 'properties', 'new'), {}),
    RemoveKeyword((*SCHEMA, 'properties')),
    SetKeyword((*SCHEMA, 'properties'), {}),
    SetKeyword(SCHEMA, {}),
    object(),
])
def test_invalid_patch_rolls_back_the_entire_batch(bad_patch):
    draft = ValidatorDraft(sample())
    before = draft.raw_bson
    with pytest.raises(DatabaseError):
        draft.apply([SetKeyword((*SCHEMA, 'title'), 'must roll back'), bad_patch])
    assert draft.raw_bson == before and not draft.changed


def test_array_tuple_paths_preserve_other_query_branches_and_numeric_types():
    query = {'$and': [{'score': {'$gte': Int64(2)}}, {'literal': {'$oid': 'not an ObjectId'}}]}
    draft = ValidatorDraft(query)
    draft.apply([SetKeyword(('$and', 0, 'score', '$lte'), 9.5)])
    expected = deepcopy(query)
    expected['$and'][0]['score']['$lte'] = 9.5
    assert draft.visual_schema is None and draft.raw_bson == encoded(expected)
    assert type(draft.document['$and'][0]['score']['$gte']) is Int64


@pytest.mark.parametrize('value,limits', [
    ({'x': 'too long'}, ValidatorLimits(max_bytes=8)),
    ({'x': {'y': {}}}, ValidatorLimits(max_depth=1)),
    ({'x': [1, 2, 3]}, ValidatorLimits(max_nodes=3)),
])
def test_original_bson_is_bounded(value, limits):
    with pytest.raises(DatabaseError, match='bound'):
        ValidatorDraft(value, limits=limits)


def test_mutation_and_batch_bounds_are_atomic_and_cycles_never_recurse_unbounded():
    draft = ValidatorDraft({'x': 1}, limits=ValidatorLimits(max_bytes=80, max_patches=1))
    with pytest.raises(DatabaseError, match='batch'):
        draft.apply([SetKeyword(('x',), 2), SetKeyword(('x',), 3)])
    assert draft.raw_bson == encoded({'x': 1})
    with pytest.raises(DatabaseError, match='byte bound'):
        draft.apply([SetKeyword(('x',), 'x' * 100)])
    cyclic = {}; cyclic['self'] = cyclic
    with pytest.raises(DatabaseError, match='depth bound'):
        draft.apply([SetKeyword(('x',), cyclic)])
    assert draft.raw_bson == draft.original_raw


def test_raw_duplicate_keys_are_retained_readonly_instead_of_silently_reencoded():
    element = b'\x10x\x00' + struct.pack('<i', 1)
    raw = struct.pack('<i', 5 + 2 * len(element)) + element + element + b'\0'
    draft = ValidatorDraft(raw)
    assert draft.read_only and draft.issues and draft.visual_schema is None
    assert draft.raw_bson == draft.original_raw == raw and not draft.changed
    with pytest.raises(DatabaseError, match='read-only'):
        draft.apply([SetKeyword(('x',), 2)])
    with pytest.raises(DatabaseError, match='read-only'):
        _ = draft.document


def test_scoped_code_and_nested_typed_values_remain_raw_bson_when_unmodified():
    value = {'query': {'$expr': Code('literal only, never evaluated', {'long': Int64(7)})}}
    draft = ValidatorDraft(value)
    assert draft.raw_bson == encoded(value)
    draft.apply([SetKeyword(('annotation',), Binary(b'\x00', 128))])
    assert encoded(draft.document['query']) == encoded(value['query'])


@pytest.mark.parametrize('limits', [False, {}, 'unbounded'])
def test_invalid_limits_do_not_silently_select_a_different_limit_set(limits):
    with pytest.raises(DatabaseError, match='ValidatorLimits'):
        ValidatorDraft({}, limits=limits)


def test_unresolved_keyword_labels_bound_count_individual_names_and_total_text():
    document = sample()
    long_name = 'future_' + 'x' * 10000
    document['$jsonSchema'][long_name] = Binary(b'preserve complete rule', 128)
    document['$jsonSchema'].update({f'unknown_{index}': Int64(index) for index in range(40)})
    draft = ValidatorDraft(document)
    before = draft.raw_bson
    with pytest.raises(UnresolvedSchemaDependencyError) as failure:
        draft.apply([RemoveProperty(SCHEMA, 'first')])
    text = str(failure.value)
    assert len(text) <= 512 and long_name not in text and '…' in text
    assert '35 more' in text and '41 total' in text
    assert draft.raw_bson == before and encoded(draft.document) == before
    assert long_name in draft.visual_schema
