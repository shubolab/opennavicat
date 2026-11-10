"""Synthetic binary-stream tests; no application, profile file or credential I/O."""
from io import BytesIO
import json

import pytest

from opennavicat import navicat_linux_json as module
from opennavicat.navicat_linux_json import InvalidValue, ParseError, ProjectedObject, SkippedValue, project_json


def project(value, spec, **limits):
    return project_json(BytesIO(json.dumps(value, ensure_ascii=False).encode()), spec, **limits)


def test_nested_native_shaped_projection_keeps_missing_and_unknown_distinct():
    value = {"Users": [{"Projects": [{"Name": "Generated", "Servers": [
        {"Name": "Probe", "Host": "localhost", "Port": 5432, "Remember": False,
         "Password": "synthetic-secret", "SSL": {"private": ["secret"]}},
    ]}]}], "private-top-key": "secret"}
    spec = {"Users": [{"Projects": [{"Name": str, "Servers": [
        {"Name": str, "Host": str, "Port": int, "Remember": bool, "User": str, "SSL": None},
    ]}]}]}
    result = project(value, spec)
    server = result["Users"][0]["Projects"][0]["Servers"][0]
    assert isinstance(result, ProjectedObject) and result.unknown_fields
    assert server == {"Name": "Probe", "Host": "localhost", "Port": 5432,
                      "Remember": False, "SSL": SkippedValue("object", True, 1)}
    assert server.unknown_fields and "User" not in server
    assert "synthetic-secret" not in repr(result) and "Password" not in repr(result)
    assert "private-top-key" not in repr(result)


class ShortReads:
    def __init__(self, value, maximum=3):
        self.data = BytesIO(value)
        self.maximum = maximum
        self.requests = []

    def read(self, size):
        assert 0 < size <= 8192
        self.requests.append(size)
        return self.data.read(min(size, self.maximum))


def test_short_reads_cross_utf8_escape_numeric_and_bom_boundaries():
    raw = b'\xef\xbb\xbf {"Name":"' + "汉字😀".encode() + b'\\u0020\\ud83d\\ude00","Port":-123,"Remember":true}'
    stream = ShortReads(raw, 1)
    result = project_json(stream, {"Name": str, "Port": int, "Remember": bool})
    assert result == {"Name": "汉字😀 😀", "Port": -123, "Remember": True}
    assert len(stream.requests) > len(raw)


def test_only_selected_string_values_reach_json_decoder(monkeypatch):
    original = module.json.loads
    calls = []

    def decode(value, *args, **kwargs):
        calls.append(bytes(value))
        return original(value, *args, **kwargs)

    monkeypatch.setattr(module.json, "loads", decode)
    raw = (b'{"Name":"allowed", "Password":"' + b"secret" * 30000
           + b'", "SSH":{"Passphrase":"hidden","nested":[1e400,true,null,"opaque"]}}')
    result = project_json(ShortReads(raw, 31), {"Name": str, "SSH": None})
    assert result == {"Name": "allowed", "SSH": SkippedValue("object", True, 2)}
    assert result.unknown_fields
    assert calls == [b'"allowed"']
    assert "secret" not in repr(result) and "hidden" not in repr(result)


@pytest.mark.parametrize("raw", [
    b'{"Name":1,"Name":2}',
    b'{"Name":1,"\\u004eame":2}',
    b'{"unknown":{"a":1,"\\u0061":2}}',
    b'{"unknown":[{"x":1,"x":2}]}',
    '{"😀":1,"\\ud83d\\ude00":2}'.encode(),
    b'{"a/b":1,"a\\/b":2}',
    b'{"' + b"x" * 15000 + b'":1,"' + b"x" * 15000 + b'":2}',
])
def test_duplicate_keys_fail_even_in_skipped_or_long_unknown_branches(raw):
    with pytest.raises(ParseError, match="^Invalid or oversized JSON source.$"):
        project_json(ShortReads(raw, 13), {"Name": str})


def test_unicode_key_identity_does_not_normalize_distinct_code_points():
    result = project_json(BytesIO('{"é":1,"e\\u0301":2,"\\ud83d\\ude00":"ok"}'.encode()), {"😀": str})
    assert result == {"😀": "ok"} and result.unknown_fields


@pytest.mark.parametrize("raw", [
    b'', b' ', b'{', b'[1,]', b'{"a":1,}', b'{a:1}', b'{"a" 1}',
    b'{"a":+1}', b'{"a":01}', b'{"a":-}', b'{"a":1.}', b'{"a":.1}',
    b'{"a":1e}', b'{"a":1e+}', b'{"a":truefalse}', b'{"a":Null}',
    b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{} {}',
    b'{"a":"unterminated}', b'{"a":"\\q"}', b'{"a":"\\u00x0"}',
    b'{"a":"raw\x00control"}', b'{"a":"\xff"}', b'{"a":"\xc0\x80"}',
    b'{"a":"\xed\xa0\x80"}', b'{"a":"\xf4\x90\x80\x80"}', b'{"a":"\xe2\x82"}',
    b'{"a":"\x80"}', b'\xef\xbb{}', b'{}\xc2\xa0', b'{}\x00',
])
def test_complete_grammar_and_utf8_are_validated_in_opaque_values(raw):
    with pytest.raises(ParseError) as error:
        project_json(ShortReads(raw, 1), {})
    assert str(error.value) == "Invalid or oversized JSON source."


@pytest.mark.parametrize("raw,expected", [
    (b'""', SkippedValue("string", False)),
    (b'"secret"', SkippedValue("string", True)),
    (b'[]', SkippedValue("array", False, 0)),
    (b'[null,{},"secret"]', SkippedValue("array", True, 3)),
    (b'{}', SkippedValue("object", False, 0)),
    (b'{"private":1}', SkippedValue("object", True, 1)),
    (b'null', SkippedValue("null", False)),
    (b'false', SkippedValue("boolean", True)),
    (b'0', SkippedValue("integer", True)),
    (b'1e400', SkippedValue("number", True)),
])
def test_skip_sentinels_only_hold_safe_shape(raw, expected):
    result = project_json(BytesIO(raw), None)
    assert result == expected
    assert "secret" not in repr(result) and "private" not in repr(result)
    assert set(vars(result)) == {"kind", "nonempty", "count"}


@pytest.mark.parametrize("value,spec,kind", [
    (True, int, "boolean"), (1, bool, "integer"), ("1", int, "string"),
    (1.0, int, "number"), (None, str, "null"), ([], {}, "array"),
    ({}, [str], "object"), (["secret"], str, "array"),
])
def test_mismatched_types_are_redacted_without_coercion(value, spec, kind):
    assert project(value, spec) == InvalidValue(kind, "type_mismatch")


def test_nonfinite_exponent_is_never_converted_to_float():
    result = project_json(BytesIO(b'{"Port":1e400,"Unknown":1e999999}'), {"Port": int})
    assert result == {"Port": InvalidValue("number", "type_mismatch")}
    assert result.unknown_fields


def test_escaped_unpaired_surrogate_is_redacted_in_selected_metadata_only():
    result = project_json(BytesIO(b'{"Name":"\\ud800","Host":"ok","private":"\\udfff"}'), {"Name": str, "Host": str})
    assert result == {"Name": InvalidValue("string", "unicode_scalar"), "Host": "ok"}
    assert result.unknown_fields
    assert project_json(BytesIO(b'"\\ud800"'), None) == SkippedValue("string", True)


def test_capture_bound_applies_per_scalar_and_continues_projection():
    result = project_json(BytesIO(b'{"Name":"1234567","Host":"ok","Port":5432,"Remember":false}'),
                          {"Name": str, "Host": str, "Port": int, "Remember": bool}, max_capture=4)
    assert result == {"Name": InvalidValue("string", "capture_limit"), "Host": "ok", "Port": 5432,
                      "Remember": InvalidValue("boolean", "capture_limit")}
    assert project_json(BytesIO(b'12345'), int, max_capture=4) == InvalidValue("integer", "capture_limit")
    assert project_json(BytesIO(b'1' * 128), int) == int(b'1' * 128)
    assert project_json(BytesIO(b'1' * 129), int) == InvalidValue("integer", "integer_limit")


def test_oversized_allowed_string_still_validates_its_suffix_and_following_values():
    with pytest.raises(ParseError):
        project_json(BytesIO(b'{"Name":"' + b'x' * 10000 + b'\\qad"}'), {"Name": str}, max_capture=8)
    with pytest.raises(ParseError):
        project_json(BytesIO(b'{"Name":"' + b'x' * 10000 + b'","Other":NaN}'), {"Name": str}, max_capture=8)


def test_byte_bound_includes_unknown_values_and_trailing_whitespace():
    data = b'{"private":"secret"}'
    assert project_json(BytesIO(data), {}, max_bytes=len(data)) == {}
    for raw in (data, data + b' '):
        with pytest.raises(ParseError):
            project_json(ShortReads(raw, 2), {}, max_bytes=len(data) - 1)
    with pytest.raises(ParseError):
        project_json(BytesIO(data + b' '), {}, max_bytes=len(data))


def test_depth_and_item_limits_cover_skipped_branches():
    assert project_json(BytesIO(b'{"x":[null]}'), {}, max_depth=2, max_items=3) == {}
    for limits in ({"max_depth": 1}, {"max_items": 2}):
        with pytest.raises(ParseError):
            project_json(BytesIO(b'{"x":[null]}'), {}, **limits)
    with pytest.raises(ParseError):
        project_json(BytesIO(b'[' * 33 + b'0' + b']' * 33), None)


def test_stream_failure_never_returns_partial_results_or_original_diagnostic():
    class ThrowingStream(ShortReads):
        def read(self, size):
            if self.data.tell() >= 16:
                raise OSError("synthetic-secret-path-and-value")
            return super().read(size)
    with pytest.raises(ParseError) as error:
        project_json(ThrowingStream(b'{"Name":"ok","private":"secret"}', 1), {"Name": str})
    assert str(error.value) == "Invalid or oversized JSON source."
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_invalid_limits_and_spec_fail_before_reading():
    class NeverRead:
        def read(self, size):
            pytest.fail("Invalid policy must not read source")
    for limits in ({"max_bytes": -1}, {"max_capture": True}, {"max_items": 1.5}):
        with pytest.raises(ParseError):
            project_json(NeverRead(), {}, **limits)
    for spec in (float, [], [str, int], {"Name": "string"}, {"x" * 257: str}):
        with pytest.raises(ParseError):
            project_json(NeverRead(), spec)
    recursive = {}
    recursive["child"] = recursive
    with pytest.raises(ParseError):
        project_json(NeverRead(), recursive)


def test_stream_must_honor_binary_and_bounded_read_contract():
    for value in ("{}", None, b"x" * 9000):
        class InvalidStream:
            def read(self, size):
                return value
        with pytest.raises(ParseError):
            project_json(InvalidStream(), {})


def test_generated_valid_documents_and_selected_unicode_match_standard_decoder():
    # Independent valid-input oracle, with multiple chunk boundaries. The
    # production parser may only decode the one selected field, not this tree.
    import random
    rng = random.Random(472013)
    atoms = [None, True, False, 0, -1, 1.25, "", "\\\"\n\t\0", "漢字😀é", "\U0010ffff"]

    def value(depth):
        if depth == 0 or rng.randrange(3) == 0:
            return rng.choice(atoms)
        if rng.randrange(2):
            return [value(depth - 1) for _ in range(rng.randrange(5))]
        return {"field" + str(i): value(depth - 1) for i in range(rng.randrange(5))}

    for _ in range(120):
        data = {"Name": rng.choice(["\U0010ffff", "漢字😀é", "\"\0\n"]), "opaque": value(4)}
        for ascii_only in (False, True):
            raw = json.dumps(data, ensure_ascii=ascii_only).encode()
            for chunk in (1, 7, 8192):
                result = project_json(ShortReads(raw, chunk), {"Name": str})
                assert result == {"Name": json.loads(raw)["Name"]}
                assert result.unknown_fields


def test_literal_nul_at_every_byte_boundary_is_rejected_in_skipped_source():
    raw = b'{"opaque":{"nested":[1,true,"escaped\\n"]}}'
    for position in range(len(raw) + 1):
        with pytest.raises(ParseError):
            project_json(ShortReads(raw[:position] + b'\0' + raw[position:], 2), {})
