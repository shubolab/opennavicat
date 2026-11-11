"""Query cells/export text must preserve BSON types and JS-only completions."""
import base64
import json
import pytest

from bson import BSON, Binary, Decimal128, Int64, json_util
from bson.datetime_ms import DatetimeMS

from opennavicat.js_engine.codec import BSON_CODEC, encode
from opennavicat.js_engine.presentation import JSON_OPTIONS, display_value, present_execution
from opennavicat.js_engine.records import CommandReceipt
from opennavicat.js_engine.runtime import JavaScriptResult


def test_rich_bson_cell_reverses_to_identical_bytes_and_escapes_editor_separators():
    value = {'long': Int64(1), 'int': 1, 'double': 1.0, 'minus': -0.0,
        'decimal': Decimal128('1.00'), 'binary': Binary(b'\0\xff', 128),
        'date': DatetimeMS(2**63-1), 'text': 'a\u2028b\u2029中文'}
    encoding, text = display_value(encode(value))
    assert encoding == 'Canonical Extended JSON'
    restored = json_util.loads(text, json_options=JSON_OPTIONS)
    assert BSON.encode(restored, codec_options=BSON_CODEC) == BSON.encode(value, codec_options=BSON_CODEC)
    assert '\u2028' not in text and '\u2029' not in text


@pytest.mark.parametrize('literal', [{'$date': '2026-01-01T00:00:00Z'}, {'$oid': 'literal-not-an-object-id'}])
def test_literal_ejson_collision_exports_exact_bson_wrapper_instead_of_changing_type(literal):
    value = {'literal': literal}
    encoding, text = display_value(encode(value))
    assert encoding == 'BSON wrapper bytes (base64)'
    envelope = json.loads(text)
    assert envelope['format'] == 'opennavicat-bson-value-v1'
    assert base64.b64decode(envelope['bsonBase64']) == BSON.encode({'value': value}, codec_options=BSON_CODEC)


def test_js_bigint_undefined_and_bson_null_are_distinct_without_fabricated_rows():
    integer = present_execution(JavaScriptResult(value=1, value_tree=['js-bigint', '1']), '1n')
    assert integer.result.rows[0][0] == 'JavaScript tagged value v1'
    assert json.loads(integer.result.rows[0][1])['tree'] == ['js-bigint', '1']
    undefined = present_execution(JavaScriptResult(value_tree=['js-undefined']), 'let a=1')
    assert undefined.result.rows == [] and 'undefined' in undefined.completion
    null = present_execution(JavaScriptResult(value_tree=['null']), 'null')
    assert null.result.rows == [['Canonical Extended JSON', 'null']]
    nested = display_value(['document', [['big', ['js-bigint', '9007199254740995']]]])
    assert json.loads(nested[1])['tree'][1][0][1] == ['js-bigint', '9007199254740995']


def test_failed_completion_keeps_acknowledged_and_uncertain_writes_without_fake_counts():
    execution = JavaScriptResult(commands=(
        CommandReceipt(1, 'insert', True, 'acknowledged_error', 2, 'Duplicate value'),
        CommandReceipt(2, 'aggregate', True, 'acknowledged', None),
        CommandReceipt(3, 'update', True, 'uncertain', None)), error='Network acknowledgement lost',
        value_tree=['double', '42'], child_reaped=True)
    presentation = present_execution(execution, 'synthetic source')
    assert presentation.result.rows == [] and presentation.result.partial
    assert presentation.result.affected == 2
    assert presentation.summary[1][4] == presentation.summary[2][4] == 'unknown'
    assert 'remain committed' in presentation.messages and 'uncertain' in presentation.messages


def test_cursor_completion_is_document_rows_and_empty_array_is_honest_empty_result():
    result = present_execution(JavaScriptResult(value_tree=encode([{'_id': Int64(1)}, {'_id': 2}])), 'db.docs.find()')
    assert len(result.result.rows) == 2
    assert json_util.loads(result.result.rows[0][1], json_options=JSON_OPTIONS) == {'_id': Int64(1)}
    empty = present_execution(JavaScriptResult(value_tree=['array', []]), '[]')
    assert empty.result.rows == [] and empty.completion == 'Array: 0 documents'


def test_known_server_write_error_with_unknown_effects_is_not_a_claimed_confirmed_write():
    execution=JavaScriptResult(commands=(CommandReceipt(1,'createindexes',True,'acknowledged_error',None,'DuplicateKey (11000)'),),error='Index build failed')
    assert not execution.has_confirmed_writes and not execution.uncertain_writes
    presentation=present_execution(execution,'db.docs.createIndex(...)')
    assert presentation.summary[0][4]=='unknown'
    assert 'no rollback is implied' in presentation.messages
    assert 'remain committed'not in presentation.messages
