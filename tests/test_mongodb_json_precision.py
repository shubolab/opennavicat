"""JSON overflow must not silently turn an editor value into BSON infinity."""
import json
import math

import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import canonical_loads, parse_mongo_queries


@pytest.mark.parametrize("literal", ["1e400", "-1e400", "9.99e9999"])
def test_editor_rejects_nested_json_double_overflow(literal):
    with pytest.raises(DatabaseError, match="double range"):
        canonical_loads('{"nested":[{"value":' + literal + '}]}')


@pytest.mark.parametrize("literal", ["1e400", "-1e400"])
def test_ejson_parse_rejects_plain_json_double_overflow(literal):
    source = '{"value":' + literal + '}'
    with pytest.raises(DatabaseError):
        parse_mongo_queries('db.docs.find(EJSON.parse(' + json.dumps(source) + '))')


@pytest.mark.parametrize("literal", ["1e308", "-1e308", "2.2250738585072014e-308"])
def test_editor_accepts_representable_json_double(literal):
    assert canonical_loads('{"value":' + literal + '}')["value"] == float(literal)


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
def test_explicit_extended_json_nonfinite_values_remain_available(literal):
    result = canonical_loads(json.dumps({"value": {"$numberDouble": literal}}))["value"]
    assert math.isnan(result) if literal == "NaN" else result == float(literal)
