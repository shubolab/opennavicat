"""BSON literals using constructors actually observed in Navicat 18's shell.

No EJSON helper or JavaScript execution is used to format data. A literal whose
type, bit pattern or property order cannot be expressed by the observed shell
is refused, rather than silently changing a copied index script's meaning.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import math
import struct

from bson import Binary, Code, Decimal128, Int64, ObjectId

from .database import DatabaseError


class UnsupportedScriptValue(DatabaseError):
    pass


def _quoted(value):
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise UnsupportedScriptValue("A shell string cannot contain an unpaired Unicode surrogate") from None
    return json.dumps(value, ensure_ascii=True)


def _property_order(keys):
    indexed, other = [], []
    for key in keys:
        if not isinstance(key, str):
            raise UnsupportedScriptValue("BSON object field names must be strings")
        if key == "__proto__":
            raise UnsupportedScriptValue("The observed JavaScript object-literal syntax cannot safely represent __proto__ fields")
        if len(key) <= 10 and key.isascii() and key.isdigit() and str(int(key)) == key and int(key) < 2**32 - 1:
            indexed.append((int(key), key))
        else:
            other.append(key)
    if [key for _, key in sorted(indexed)] + other != list(keys):
        raise UnsupportedScriptValue("JavaScript would reorder numeric object fields; the original ordered BSON is retained read-only")


def bson_literal(value, level=0):
    if level > 64:
        raise UnsupportedScriptValue("The BSON literal is too deeply nested")
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if isinstance(value, Code):
        raise UnsupportedScriptValue("The original Code BSON is retained; its shell constructor has not been verified")
    if isinstance(value, str):
        return _quoted(value)
    if isinstance(value, Int64):
        return "Long(" + _quoted(str(value)) + ")"
    if type(value) is int:
        if -(2**31) <= value < 2**31:
            return "Int32(" + _quoted(str(value)) + ")"
        if -(2**63) <= value < 2**63:
            return "Long(" + _quoted(str(value)) + ")"
        raise UnsupportedScriptValue("The integer does not fit a BSON integer")
    if type(value) is float:
        if math.isnan(value):
            if struct.pack(">d", value).hex() != "7ff8000000000000":
                raise UnsupportedScriptValue("The original noncanonical NaN bit pattern cannot be represented losslessly")
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return repr(value)
    if isinstance(value, Decimal128):
        text = str(value)
        if Decimal128(text).bid != value.bid:
            raise UnsupportedScriptValue("The original Decimal128 bit pattern cannot be represented losslessly")
        return "NumberDecimal(" + _quoted(text) + ")"
    if isinstance(value, ObjectId):
        return "ObjectId(" + _quoted(str(value)) + ")"
    if isinstance(value, bytes):
        subtype = value.subtype if isinstance(value, Binary) else 0
        return "Binary.createFromBase64(" + _quoted(base64.b64encode(value).decode("ascii")) + ", " + str(subtype) + ")"
    if isinstance(value, datetime):
        if value.microsecond % 1000:
            raise UnsupportedScriptValue("BSON dates require exact millisecond precision")
        try:
            date = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise UnsupportedScriptValue("This BSON date cannot be expressed by the observed ISODate constructor") from None
        return "ISODate(" + _quoted(date.isoformat(timespec="milliseconds").replace("+00:00", "Z")) + ")"
    if isinstance(value, dict):
        _property_order(list(value))
        if not value:
            return "{}"
        indent, child = "  " * level, "  " * (level + 1)
        return "{\n" + ",\n".join(child + _quoted(key) + ": " + bson_literal(item, level + 1) for key, item in value.items()) + "\n" + indent + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(bson_literal(item, level + 1) for item in value) + "]"
    raise UnsupportedScriptValue("The original " + type(value).__name__ + " BSON is retained; its shell constructor has not been verified")


def helper_script(schema, commands):
    lines = ["// database: " + _quoted(schema)]
    for command in commands:
        if "dropIndexes" in command:
            if len(command["index"]) != 1 or command["index"][0] == "*":
                raise UnsupportedScriptValue("Only one explicitly named index can be scripted for deletion")
            lines.append("db.getCollection(" + _quoted(command["dropIndexes"]) + ").dropIndex(" + _quoted(command["index"][0]) + ");")
        else:
            for spec in command["indexes"]:
                options = {key: value for key, value in spec.items() if key not in {"key", "ns"}}
                lines.append("db.getCollection(" + _quoted(command["createIndexes"]) + ").createIndex(" + bson_literal(spec["key"]) + ", " + bson_literal(options) + ");")
    return "\n\n".join(lines)


def protected_command_script(schema, commands):
    return "// database: " + _quoted(schema) + "\n\n" + "\n\n".join("db.runCommand(" + bson_literal(command) + ");" for command in commands)
