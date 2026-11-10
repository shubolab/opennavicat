"""The IPC is tagged BSON data, never Python code or an EJSON second parse."""
from bson import BSON, Binary, Code, DBRef, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
import pytest

from opennavicat.mongodb_session import BSON_CODEC
from opennavicat.js_engine.codec import decode, encode


@pytest.mark.parametrize("value", [Int64(9007199254740995), Decimal128("12.5000"), Binary(b"\x00\xff", 128),
    ObjectId("00112233445566778899aabb"), DatetimeMS(2**63-1), Code("must stay data", {"long":Int64(3)}),
    DBRef("other", Int64(5), "owned"), Regex("x.*y", "im"), Timestamp(1, 2), MinKey(), MaxKey(),
    {"$date":"literal", "__proto__":{"safe":True}, "x":[None, True, -0.0, 2.0]}])
def test_structured_bson_roundtrip_is_exact(value):
    original = {"value":value}
    assert BSON.encode(decode(encode(original)), codec_options=BSON_CODEC) == BSON.encode(original, codec_options=BSON_CODEC)


@pytest.mark.parametrize("tree", [["document", [["a",["int32",1]],["a",["int32",2]]]],
    ["int64",str(2**63)], ["int32",True], ["binary","not base64",128], ["unknown","data"],
    ["js-bigint","9007199254740995000000000000"], ["timestamp",-1,0]])
def test_invalid_or_lossy_tree_is_rejected(tree):
    with pytest.raises((ValueError, TypeError)):
        decode(tree)
