"""Bounded JSON projection without decoding or retaining unselected values.

Only strings selected by ``str`` are passed to the standard JSON decoder.
Object keys are compared and deduplicated using hashes of their decoded Unicode
code points, without constructing unknown key strings. Skipped strings still
receive complete escape and UTF-8 validation. Bounds apply to the entire input,
including skipped branches; ``max_capture`` is a per-scalar encoded-token bound.
The reader requests at most 8 KiB at a time and reads at most max_bytes + 1
bytes, with the extra byte needed to distinguish EOF from an oversized source.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


_CHUNK = 8192
_INTEGER_DIGITS = 128
_ERROR = "Invalid or oversized JSON source."
_WS = (9, 10, 13, 32)
_MISSING = object()


class ParseError(ValueError):
    """A deliberately constant diagnostic, with no source path/key/value."""

    def __init__(self):
        super().__init__(_ERROR)


@dataclass(frozen=True)
class SkippedValue:
    kind: str
    nonempty: bool
    count: int | None = None


@dataclass(frozen=True)
class InvalidValue:
    kind: str
    reason: str


class ProjectedObject(dict):
    def __init__(self, *args, unknown_fields=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.unknown_fields = unknown_fields


@dataclass(frozen=True)
class _ObjectSpec:
    fields: dict


@dataclass(frozen=True)
class _ArraySpec:
    item: object


def _compile(spec, depth=0, ancestors=None):
    if spec is None or spec is str or spec is int or spec is bool:
        return spec
    if depth >= 32:
        raise ParseError()
    ancestors = set() if ancestors is None else ancestors
    if id(spec) in ancestors:
        raise ParseError()
    ancestors = ancestors | {id(spec)}
    if isinstance(spec, dict):
        fields = {}
        for key, child in spec.items():
            if not isinstance(key, str) or len(key.encode("utf-8")) > 256:
                raise ParseError()
            digest = hashlib.sha256(key.encode("utf-32-be")).digest()
            if digest in fields:
                raise ParseError()
            fields[digest] = (key, _compile(child, depth + 1, ancestors))
        return _ObjectSpec(fields)
    if isinstance(spec, list) and len(spec) == 1:
        return _ArraySpec(_compile(spec[0], depth + 1, ancestors))
    raise ParseError()


class _Reader:
    def __init__(self, stream, maximum):
        self.stream = stream
        self.maximum = maximum
        self.read_bytes = 0
        self.chunk = b""
        self.index = 0
        self.eof = False

    def peek(self):
        if self.index == len(self.chunk):
            if self.eof:
                return None
            size = min(_CHUNK, self.maximum - self.read_bytes + 1)
            block = self.stream.read(size)
            if not isinstance(block, bytes) or len(block) > size:
                raise ParseError()
            self.read_bytes += len(block)
            if self.read_bytes > self.maximum:
                raise ParseError()
            self.chunk, self.index = block, 0
            if not block:
                self.eof = True
                return None
        return self.chunk[self.index]

    def take(self):
        value = self.peek()
        if value is None:
            raise ParseError()
        self.index += 1
        return value

    def expect(self, byte):
        if self.take() != byte:
            raise ParseError()

    def whitespace(self):
        while self.peek() in _WS:
            self.take()


class _Parser:
    def __init__(self, stream, maximum, depth, items, capture):
        self.reader = _Reader(stream, maximum)
        self.max_depth = depth
        self.max_items = items
        self.max_capture = capture
        self.items = 0

    def string(self, *, capture=False, key=False):
        """Scan bytes; only an explicitly selected scalar gets a raw buffer."""
        reader = self.reader
        reader.expect(34)
        raw = bytearray(b'"') if capture and self.max_capture >= 1 else None
        over = capture and raw is None
        digest = hashlib.sha256() if key else None
        pending = None
        nonempty = False

        def take():
            nonlocal raw, over
            byte = reader.take()
            if raw is not None:
                if len(raw) >= self.max_capture:
                    raw, over = None, True
                else:
                    raw.append(byte)
            return byte

        def emit(point):
            nonlocal pending, nonempty
            nonempty = True
            if digest is None:
                return
            if pending is not None:
                if 0xDC00 <= point <= 0xDFFF:
                    point = 0x10000 + ((pending - 0xD800) << 10) + point - 0xDC00
                    pending = None
                else:
                    digest.update(pending.to_bytes(4, "big"))
                    pending = None
            if 0xD800 <= point <= 0xDBFF:
                pending = point
            else:
                digest.update(point.to_bytes(4, "big"))

        while True:
            byte = take()
            if byte == 34:
                if pending is not None:
                    digest.update(pending.to_bytes(4, "big"))
                return raw, over, nonempty, digest.digest() if digest is not None else None
            if byte == 92:
                escaped = take()
                if escaped == 117:
                    point = 0
                    for _ in range(4):
                        digit = take()
                        if 48 <= digit <= 57:
                            value = digit - 48
                        elif 65 <= digit <= 70:
                            value = digit - 55
                        elif 97 <= digit <= 102:
                            value = digit - 87
                        else:
                            raise ParseError()
                        point = (point << 4) | value
                    emit(point)
                else:
                    points = {34: 34, 92: 92, 47: 47, 98: 8, 102: 12, 110: 10, 114: 13, 116: 9}
                    if escaped not in points:
                        raise ParseError()
                    emit(points[escaped])
            elif byte < 32:
                raise ParseError()
            elif byte < 128:
                emit(byte)
            else:
                if 0xC2 <= byte <= 0xDF:
                    length, point, minimum = 1, byte & 0x1F, 0x80
                elif 0xE0 <= byte <= 0xEF:
                    length, point, minimum = 2, byte & 0x0F, 0x800
                elif 0xF0 <= byte <= 0xF4:
                    length, point, minimum = 3, byte & 0x07, 0x10000
                else:
                    raise ParseError()
                for _ in range(length):
                    next_byte = take()
                    if not 0x80 <= next_byte <= 0xBF:
                        raise ParseError()
                    point = (point << 6) | (next_byte & 0x3F)
                if point < minimum or point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
                    raise ParseError()
                emit(point)

    def number(self, capture):
        reader = self.reader
        raw = bytearray() if capture else None
        over = False
        digits = 0
        integer = True

        def take():
            nonlocal raw, over, digits
            byte = reader.take()
            digits += 48 <= byte <= 57
            if raw is not None:
                if len(raw) >= min(self.max_capture, _INTEGER_DIGITS + 1):
                    raw, over = None, True
                else:
                    raw.append(byte)

        if reader.peek() == 45:
            take()
        if reader.peek() == 48:
            take()
        elif reader.peek() is not None and 49 <= reader.peek() <= 57:
            while reader.peek() is not None and 48 <= reader.peek() <= 57:
                take()
        else:
            raise ParseError()
        if reader.peek() == 46:
            integer = False
            take()
            if reader.peek() is None or not 48 <= reader.peek() <= 57:
                raise ParseError()
            while reader.peek() is not None and 48 <= reader.peek() <= 57:
                take()
        if reader.peek() in (69, 101):
            integer = False
            take()
            if reader.peek() in (43, 45):
                take()
            if reader.peek() is None or not 48 <= reader.peek() <= 57:
                raise ParseError()
            while reader.peek() is not None and 48 <= reader.peek() <= 57:
                take()
        kind = "integer" if integer else "number"
        if not capture:
            return SkippedValue(kind, True)
        if not integer:
            return InvalidValue(kind, "type_mismatch")
        if digits > _INTEGER_DIGITS:
            return InvalidValue(kind, "integer_limit")
        if over:
            return InvalidValue(kind, "capture_limit")
        return int(raw)

    def value(self, spec, depth=0):
        self.items += 1
        if self.items > self.max_items:
            raise ParseError()
        reader = self.reader
        reader.whitespace()
        byte = reader.peek()
        if byte in (123, 91):
            if depth >= self.max_depth:
                raise ParseError()
            if byte == 123:
                result = self.object(spec if isinstance(spec, _ObjectSpec) else None, depth)
                matching, kind = isinstance(spec, _ObjectSpec), "object"
            else:
                result = self.array(spec if isinstance(spec, _ArraySpec) else None, depth)
                matching, kind = isinstance(spec, _ArraySpec), "array"
            return result if matching or spec is None else InvalidValue(kind, "type_mismatch")
        if byte == 34:
            raw, over, nonempty, _ = self.string(capture=spec is str)
            if spec is str:
                if over:
                    return InvalidValue("string", "capture_limit")
                value = json.loads(raw)
                # JSON's escape grammar permits an unpaired surrogate, but it
                # cannot become valid UTF-8 metadata. Keep that selected field
                # invalid instead of silently replacing characters or breaking
                # a later workspace write. Opaque branches remain undecoded.
                if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                    return InvalidValue("string", "unicode_scalar")
                return value
            return SkippedValue("string", nonempty) if spec is None else InvalidValue("string", "type_mismatch")
        if byte == 45 or (byte is not None and 48 <= byte <= 57):
            result = self.number(spec is int)
            return result if spec is int or spec is None else InvalidValue(result.kind, "type_mismatch")
        for token, value, kind in ((b"true", True, "boolean"), (b"false", False, "boolean"), (b"null", None, "null")):
            if byte == token[0]:
                for expected in token:
                    reader.expect(expected)
                if spec is bool and value is not None:
                    return value if len(token) <= self.max_capture else InvalidValue(kind, "capture_limit")
                return SkippedValue(kind, value is not None) if spec is None else InvalidValue(kind, "type_mismatch")
        raise ParseError()

    def object(self, spec, depth):
        reader = self.reader
        reader.expect(123)
        result = ProjectedObject() if spec is not None else None
        seen = set()
        count = 0
        reader.whitespace()
        if reader.peek() != 125:
            while True:
                _, _, _, digest = self.string(key=True)
                if digest in seen:
                    raise ParseError()
                seen.add(digest)
                reader.whitespace()
                reader.expect(58)
                selected = spec.fields.get(digest, _MISSING) if spec is not None else _MISSING
                child = self.value(selected[1] if selected is not _MISSING else None, depth + 1)
                count += 1
                if result is not None:
                    if selected is _MISSING:
                        result.unknown_fields = True
                    else:
                        result[selected[0]] = child
                reader.whitespace()
                if reader.peek() != 44:
                    break
                reader.take()
                reader.whitespace()
        reader.expect(125)
        return result if result is not None else SkippedValue("object", bool(count), count)

    def array(self, spec, depth):
        reader = self.reader
        reader.expect(91)
        result = [] if spec is not None else None
        count = 0
        reader.whitespace()
        if reader.peek() != 93:
            while True:
                child = self.value(spec.item if spec is not None else None, depth + 1)
                if result is not None:
                    result.append(child)
                count += 1
                reader.whitespace()
                if reader.peek() != 44:
                    break
                reader.take()
        reader.expect(93)
        return result if result is not None else SkippedValue("array", bool(count), count)


def project_json(binary_stream, spec, *, max_bytes=32 * 1024 * 1024,
                 max_depth=32, max_items=100000, max_capture=64 * 1024):
    """Project one complete JSON document; never return a partially read tree.

    ``spec`` is a dict of selected keys, a one-element list, ``str``, ``int``,
    ``bool``, or ``None`` (opaque skip). Missing selected keys remain missing;
    wrong types and oversized selected scalars become redacted InvalidValue.
    Integer capture is additionally limited to 128 decimal digits. A root value
    counts as one item; every member/array value, including skipped ones, counts
    toward max_items. max_depth counts nested containers, not scalar leaves.
    Unknown fields are omitted, with only their presence marked on the object.
    """
    try:
        for limit in (max_bytes, max_depth, max_items, max_capture):
            if type(limit) is not int or limit < 0:
                raise ParseError()
        compiled = _compile(spec)
        parser = _Parser(binary_stream, max_bytes, max_depth, max_items, max_capture)
        if parser.reader.peek() == 0xEF:
            for byte in b"\xef\xbb\xbf":
                parser.reader.expect(byte)
        result = parser.value(compiled)
        parser.reader.whitespace()
        if parser.reader.peek() is not None:
            raise ParseError()
        return result
    except Exception:
        pass
    # Raise outside the handler so __context__ cannot retain a stream error's
    # private path or a decoder exception's original document, even privately.
    raise ParseError()
