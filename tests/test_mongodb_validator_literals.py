"""Validator data grammar, typed constructors and unchanged legacy defaults."""
import math
import struct

from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Timestamp
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import BSON_CODEC, _MongoLiteralParser, parse_mongo_queries
from opennavicat.mongodb_validator_literals import (
    MAX_LITERAL_BYTES, parse_numeric_constraint, parse_validator_literal, parse_validator_query,
)


def raw(value):
    return BSON.encode({'value': value}, codec_options=BSON_CODEC)


def test_validator_ordinary_numbers_are_double_at_every_data_depth():
    observed = parse_validator_query('{score: {$gte:20}, nested: [1, {v: 0x10}, -0]}')
    assert raw(observed) == raw({'score': {'$gte': 20.0}, 'nested': [1.0, {'v': 16.0}, -0.0]})
    assert struct.pack('>d', observed['nested'][-1]) == struct.pack('>d', -0.0)


def test_explicit_numeric_and_rich_bson_constructors_keep_types_and_control_arguments():
    observed = parse_validator_literal('''{
        i:Int32(1), j:NumberInt(2), l:Long("9007199254740993"), k:NumberLong(3),
        d:Decimal128("1.00"), t:Timestamp(7, 8), u:Timestamp({t:9,i:10}),
        binary:BinData(128,"AP8="), binary2:Binary.createFromBase64("AP8=",128),
        code:Code("return n",{n:1}), id:ObjectId("000000000000000000000001")
    }''')
    expected = {'i': 1, 'j': 2, 'l': Int64(9007199254740993), 'k': Int64(3),
        'd': Decimal128('1.00'), 't': Timestamp(7, 8), 'u': Timestamp(9, 10),
        'binary': Binary(b'\0\xff', 128), 'binary2': Binary(b'\0\xff', 128),
        'code': Code('return n', {'n': 1.0}), 'id': ObjectId('000000000000000000000001')}
    assert raw(observed) == raw(expected)


def test_literals_distinguish_null_false_empty_strings_and_blank_omission():
    assert parse_validator_literal('null') is None
    assert parse_validator_literal('false') is False
    assert parse_validator_literal("''") == ''
    assert parse_validator_literal('/* comment */ [1,2,] // tail') == [1.0, 2.0]
    assert parse_validator_query(' \n ') is None
    assert parse_numeric_constraint(' \n ') is None
    with pytest.raises(DatabaseError): parse_validator_literal(' \n ')
    with pytest.raises(DatabaseError): parse_validator_query('null')
    with pytest.raises(DatabaseError): parse_validator_query('[]')


@pytest.mark.parametrize('text,expected', [('0', 0.0), ('-0', -0.0), ('5', 5.0), ('2.5', 2.5),
    ('1e3', 1000.0), ('Int32(5)', 5.0), ('Long("5")', 5.0)])
def test_numeric_controls_produce_new_double_values(text, expected):
    observed = parse_numeric_constraint(text)
    assert type(observed) is float and raw(observed) == raw(expected)


@pytest.mark.parametrize('text', ['true', 'null', '"5"', '[]', '{}', 'NaN', 'Infinity', '-Infinity'])
def test_numeric_controls_reject_non_numbers_and_nonfinite_values(text):
    with pytest.raises(DatabaseError): parse_numeric_constraint(text)


@pytest.mark.parametrize('text', [
    '{broken', '{a:1,a:2}', '{a:{x:1,x:2}}', '{x:1}; db.dropDatabase()',
    '{x:(function(){return 1})()}', '{x:process.env}', '{x:require("fs")}',
    'db.runCommand({dropDatabase:1})', '1+2', '({x:1})',
    r'{x:"\ud800"}', '{' + 'x:{' * 70 + 'v:1' + '}' * 71,
])
def test_expressions_commands_duplicates_invalid_unicode_and_depth_are_rejected(text):
    with pytest.raises(DatabaseError): parse_validator_literal(text)


def test_literal_bson_field_names_do_not_become_extended_json_wrappers():
    observed = parse_validator_literal('{"$date":"literal", "$oid":"literal"}')
    assert observed == {'$date': 'literal', '$oid': 'literal'}
    assert raw(observed) == raw({'$date': 'literal', '$oid': 'literal'})


def test_byte_node_and_constructor_subtree_bounds_are_enforced():
    with pytest.raises(DatabaseError, match='1 MiB'):
        parse_validator_literal(' ' * (MAX_LITERAL_BYTES + 1))
    with pytest.raises(DatabaseError, match='bound'):
        parse_validator_literal('[' + ','.join('null' for _ in range(100001)) + ']')
    nested = '{"x":' * 70 + '1' + '}' * 70
    import json
    with pytest.raises(DatabaseError, match='bound'):
        parse_validator_literal('EJSON.parse(' + json.dumps(nested) + ')')


def test_syntax_error_has_safe_line_column_without_user_values_or_native_cause():
    secret = 'synthetic-private-text'
    with pytest.raises(DatabaseError) as captured:
        parse_validator_literal('{\nvalue: Decimal128("' + secret + '")\n}')
    assert 'line 2, column' in str(captured.value)
    assert secret not in str(captured.value) and captured.value.__suppress_context__ is True


def test_default_legacy_query_and_literal_number_types_are_unchanged():
    assert type(_MongoLiteralParser('{n:1}').value()['n']) is int
    command = parse_mongo_queries('db.docs.find({n:1})')[0]
    assert type(command['filter']['n']) is int
    assert type(parse_validator_query('{n:1}')['n']) is float
