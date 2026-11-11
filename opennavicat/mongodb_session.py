"""MongoDB's BSON-native document adapter; no SQL emulation.

Document browsing/editor APIs preserve raw BSON preimages and their types.
Canonical Extended JSON is an explicit editing format, not a claim of complete
mongosh JavaScript compatibility. The full native shell remains a separate gate.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import math
import mimetypes
import os
import stat
import re
import ipaddress
import secrets
import tempfile
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

from bson import BSON, json_util, Binary, Code, DBRef, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
from datetime import datetime
from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions, DatetimeConversion
from bson.raw_bson import RawBSONDocument

from .database import ConnectionConfig, DatabaseError, DatabaseSession, QueryResult, _tls_context
from pymongo import MongoClient, ReturnDocument, timeout as mongo_timeout
from pymongo.collation import Collation
from pymongo.errors import OperationFailure, PyMongoError
from .transport import SSHTunnel, SSHHostKeyError, SSHAuthenticationError, TLSKeyPasswordError

JSON_OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(
    uuid_representation=UuidRepresentation.UNSPECIFIED,
    tz_aware=True, tzinfo=timezone.utc,
    datetime_conversion=DatetimeConversion.DATETIME_AUTO,
)
BSON_CODEC = CodecOptions(
    uuid_representation=UuidRepresentation.UNSPECIFIED,
    tz_aware=True, tzinfo=timezone.utc,
    datetime_conversion=DatetimeConversion.DATETIME_AUTO,
)
RAW_CODEC = BSON_CODEC.with_options(document_class=RawBSONDocument)


@dataclass(frozen=True)
class MongoDocument:
    document: dict[str, Any] = field(repr=False)
    raw_bson: bytes = field(repr=False)
    cas_token: bytes = field(repr=False)
    editable: bool = True
    edit_reason: str = ""


@dataclass(frozen=True)
class MongoPage:
    documents: list[MongoDocument]
    offset: int
    limit: int
    has_more: bool
    total: int | None = None
    query_text: str = ""
    next_offset: int = 0


def canonical_dumps(document: dict[str, Any]) -> str:
    """BSON types + ASCII escapes make the editor's Unicode roundtrip explicit."""
    if not isinstance(document, dict):
        raise DatabaseError("A document must be an object")
    return json_util.dumps(document, json_options=JSON_OPTIONS, ensure_ascii=True, indent=2)


def canonical_array_dumps(values: list[Any]) -> str:
    """Serialize a BSON array without weakening document-editor validation."""
    if not isinstance(values, list):
        raise DatabaseError("An array value is required")
    try:
        raw = BSON.encode({"value": values}, codec_options=BSON_CODEC)
        if BSON.encode(BSON(raw).decode(codec_options=BSON_CODEC), codec_options=BSON_CODEC) != raw:
            raise ValueError()
        text = json_util.dumps(values, json_options=JSON_OPTIONS, ensure_ascii=True, indent=2)
        if BSON.encode({"value": json_util.loads(text, json_options=JSON_OPTIONS)}, codec_options=BSON_CODEC) != raw:
            raise ValueError()
        return text
    except Exception as exc:
        raise DatabaseError("The array cannot be serialized as exact BSON") from exc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatabaseError("Duplicate document field names are not accepted")
        result[key] = value
    return result


def _finite_json_float(token):
    value = float(token)
    if not math.isfinite(value):
        raise DatabaseError("A JSON number exceeds the BSON double range; use an explicit Extended JSON nonfinite value")
    return value


def _validate_date_precision(value, depth=0):
    if depth > _MAX_DEPTH:
        raise DatabaseError("The BSON document is too deeply nested")
    if isinstance(value, datetime) and value.microsecond % 1000:
        raise DatabaseError("BSON dates support millisecond precision; finer input must be rounded explicitly")
    if isinstance(value, Code) and value.scope is not None:
        _validate_date_precision(value.scope, depth + 1)
    elif isinstance(value, DBRef):
        _validate_date_precision(dict(value.as_doc()), depth + 1)
    elif isinstance(value, dict):
        for child in value.values(): _validate_date_precision(child, depth + 1)
    elif isinstance(value, list):
        for child in value: _validate_date_precision(child, depth + 1)


def _date_string_precision(text):
    # json_util/fromisoformat can already discard the seventh fractional digit.
    # Check the source spelling before either parser rounds it to microseconds.
    for match in re.finditer(r"[.,]([0-9]+)", text):
        if any(digit != "0" for digit in match.group(1)[3:]):
            raise DatabaseError("BSON dates support millisecond precision; finer input must be rounded explicitly")


def _validate_json_date_literals(value, depth=0):
    if depth > _MAX_DEPTH: raise DatabaseError("The BSON document is too deeply nested")
    if isinstance(value, dict):
        if isinstance(value.get("$date"), str): _date_string_precision(value["$date"])
        for child in value.values(): _validate_json_date_literals(child, depth + 1)
    elif isinstance(value, list):
        for child in value: _validate_json_date_literals(child, depth + 1)


def canonical_loads(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise DatabaseError("The document editor accepts at most 16 MiB of Extended JSON")
    try:
        # The JSON library's object_pairs_hook suppresses object_hook, so validate
        # duplicates separately before json_util performs BSON wrapper decoding.
        raw = json.loads(text, object_pairs_hook=_unique_pairs, parse_float=_finite_json_float,
                   parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Use Extended JSON for nonfinite numbers")))
        _validate_json_date_literals(raw)
        result = json_util.loads(text, json_options=JSON_OPTIONS)
        if not isinstance(result, dict):
            raise DatabaseError("A document must be an object")
        _validate_date_precision(result)
        BSON.encode(result, codec_options=BSON_CODEC)
        return result
    except DatabaseError:
        raise
    except Exception as exc:
        # Parser reprs can include document values. Keep the boundary generic.
        raise DatabaseError("Invalid canonical Extended JSON document") from exc


def canonical_array_loads(text: str) -> list[Any]:
    """Parse one canonical EJSON array using the document editor's safeguards."""
    if not isinstance(text, str):
        raise DatabaseError("Invalid canonical Extended JSON array")
    try:
        value = canonical_loads('{"value":' + text + '}')["value"]
        if not isinstance(value, list):
            raise DatabaseError("An array value is required")
        return value
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError("Invalid canonical Extended JSON array") from exc



class MongoConflictError(DatabaseError):
    """The exact typed document or the session binding no longer matches."""


_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_EDIT_BYTES = 1024 * 1024
_MAX_DEPTH = 80
_NO_COMMENT = object()


def _literal(value):
    return {"$literal": value}


def _cas_terms(value, expression="$$ROOT", depth=0):
    """Exact value/type/key-order guard, using MongoDB 5+'s literal getField.

    MongoDB's ordinary numeric equality collapses BSON number types and decimal
    scale. Explicit type and decimal text checks keep an edit from overwriting
    a concurrent type/representation change. Unsupported values fail closed.
    """
    if depth > _MAX_DEPTH:
        raise DatabaseError("The BSON document is too deeply nested for safe editing")
    typ = None
    if isinstance(value, DBRef):
        value = dict(value.as_doc())
    if isinstance(value, Code):
        if value.scope is not None:
            raise DatabaseError("Code with scope is read-only until exact nested CAS is supported")
        typ = "javascript"
    elif isinstance(value, dict):
        typ = "object"
    elif isinstance(value, list):
        typ = "array"
    elif value is None:
        typ = "null"
    elif isinstance(value, bool):
        typ = "bool"
    elif isinstance(value, Int64):
        typ = "long"
    elif isinstance(value, int):
        typ = "int" if -(2**31) <= value < 2**31 else "long"
    elif isinstance(value, float):
        if math.isnan(value):
            raise DatabaseError("NaN BSON values are read-only until payload-exact CAS is supported")
        typ = "double"
    elif isinstance(value, Decimal128):
        if value.to_decimal().is_nan():
            raise DatabaseError("Decimal NaN values are read-only until payload-exact CAS is supported")
        typ = "decimal"
    elif isinstance(value, str):
        typ = "string"
    elif isinstance(value, (bytes, Binary)):
        typ = "binData"
    elif isinstance(value, ObjectId):
        typ = "objectId"
    elif isinstance(value, (datetime, DatetimeMS)):
        typ = "date"
    elif isinstance(value, Regex):
        typ = "regex"
    elif isinstance(value, Timestamp):
        typ = "timestamp"
    elif isinstance(value, MinKey):
        typ = "minKey"
    elif isinstance(value, MaxKey):
        typ = "maxKey"
    if typ is None:
        raise DatabaseError("This BSON type is read-only until exact CAS is supported")
    terms = [{"$eq": [{"$type": expression}, typ]}]
    if isinstance(value, dict):
        terms.append({"$eq": [{"$map": {"input": {"$objectToArray": expression}, "as": "pair", "in": "$$pair.k"}}, _literal(list(value))]})
        for key, child in value.items():
            child_expr = {"$getField": {"field": _literal(key), "input": expression}}
            terms.extend(_cas_terms(child, child_expr, depth + 1))
    elif isinstance(value, list):
        terms.append({"$eq": [{"$size": expression}, len(value)]})
        for index, child in enumerate(value):
            terms.extend(_cas_terms(child, {"$arrayElemAt": [expression, index]}, depth + 1))
    elif isinstance(value, Decimal128):
        terms.append({"$eq": [{"$toString": expression}, str(value)]})
    elif isinstance(value, float) and value == 0:
        terms.append({"$eq": [{"$toString": expression}, "-0" if math.copysign(1, value) < 0 else "0"]})
    return terms


def _exact_filter(original):
    if "_id" not in original:
        raise DatabaseError("Only full documents with _id can be edited")
    terms = [{"$eq": ["$$ROOT", _literal(original)]}, *_cas_terms(original)]
    # $eq binds a value even when an embedded _id contains operator-like keys.
    return {"_id": {"$eq": original["_id"]}, "$expr": {"$and": terms}}


class _TLSHostnameContext:
    """Per-client TLS delegate: the tunnel changes TCP routing, never identity."""
    def __init__(self, context, hostname):
        self._context, self._hostname = context, hostname

    def __getattr__(self, name):
        return getattr(self._context, name)

    def wrap_socket(self, sock, *args, **kwargs):
        # PyMongo passes this by keyword, including its native TLS session cache.
        # Keep the underlying context's CA/CRL/client certificate/check_hostname.
        kwargs['server_hostname'] = self._hostname
        return self._context.wrap_socket(sock, *args, **kwargs)


class _TunnelPoolOptions:
    def __init__(self, options, hostname):
        self._options = options
        context = options._ssl_context
        self._ssl_context = _TLSHostnameContext(context, hostname) if context is not None else None

    def __getattr__(self, name):
        return getattr(self._options, name)


def _tunnel_pool_class(hostname):
    """Narrow, version-guarded PyMongo 4.19 deep-customization interface.

    MongoClient's private _pool_class is explicitly intended for deep driver
    customization (also used by Motor). It applies to application and monitor
    pools. No public SOCKS option exists in 4.19. This delegate leaves socket
    creation, handshakes, credentials, pooling and all TLS checks to PyMongo.
    Reaccept the private interface with real security tests before upgrading.
    """
    import pymongo
    from pymongo.synchronous.pool import Pool
    from pymongo.pool_options import PoolOptions
    if pymongo.version_tuple[:3] != (4, 19, 0) or not hasattr(PoolOptions, '_ssl_context'):
        raise DatabaseError('MongoDB SSH requires the security-accepted PyMongo 4.19.0 transport interface')
    class TunnelPool(Pool):
        def __init__(self, address, options, *args, **kwargs):
            super().__init__(address, _TunnelPoolOptions(options, hostname), *args, **kwargs)
    return TunnelPool


class _MongoLiteralParser:
    """A non-evaluating BSON literal/query grammar, never a JavaScript runtime."""
    def __init__(self, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > 16 * 1024 * 1024:
            raise DatabaseError('MongoDB query text is limited to 16 MiB')
        self.text, self.pos, self.nodes = text, 0, 0

    def error(self):
        raise DatabaseError(f'Unsupported MongoDB query syntax at character {self.pos + 1}; only the documented BSON query subset is accepted')

    def skip(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace(): self.pos += 1
            elif self.text.startswith('//', self.pos):
                end = self.text.find('\n', self.pos + 2)
                self.pos = len(self.text) if end < 0 else end + 1
            elif self.text.startswith('/*', self.pos):
                end = self.text.find('*/', self.pos + 2)
                if end < 0: self.error()
                self.pos = end + 2
            else: break

    def peek(self):
        self.skip()
        return self.text[self.pos:self.pos + 1]

    def take(self, token):
        self.skip()
        if not self.text.startswith(token, self.pos): self.error()
        self.pos += len(token)

    def identifier(self):
        self.skip(); start = self.pos
        if start >= len(self.text) or not (self.text[start].isalpha() or self.text[start] in '_$'): self.error()
        self.pos += 1
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in '_$'):
            self.pos += 1
        return self.text[start:self.pos]

    def string(self):
        self.skip(); quote = self.peek()
        if quote not in ('"', "'"): self.error()
        self.pos += 1; chars = []
        escapes = {'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','0':'\0'}
        while self.pos < len(self.text):
            c = self.text[self.pos]; self.pos += 1
            if c == quote: return ''.join(chars)
            if c in '\r\n': self.error()
            if c != '\\': chars.append(c); continue
            if self.pos >= len(self.text): self.error()
            c = self.text[self.pos]; self.pos += 1
            if c in '\r\n':
                if c == '\r' and self.text[self.pos:self.pos+1] == '\n': self.pos += 1
                continue
            if c in '123456789' or (c == '0' and self.text[self.pos:self.pos+1].isdigit()):
                raise DatabaseError('Legacy numeric string escapes are not supported; use explicit Unicode or hexadecimal escapes')
            if c in ('u','x'):
                count = 4 if c == 'u' else 2
                digits = self.text[self.pos:self.pos+count]
                if len(digits) != count or any(a not in '0123456789abcdefABCDEF' for a in digits): self.error()
                self.pos += count; code = int(digits, 16)
                if 0xD800 <= code <= 0xDBFF:
                    if self.text[self.pos:self.pos+2] != '\\u': self.error()
                    tail = self.text[self.pos+2:self.pos+6]
                    try: low = int(tail, 16)
                    except ValueError: self.error()
                    if len(tail) != 4 or not 0xDC00 <= low <= 0xDFFF: self.error()
                    self.pos += 6; code = 0x10000 + ((code-0xD800)<<10) + low-0xDC00
                elif 0xDC00 <= code <= 0xDFFF: self.error()
                chars.append(chr(code))
            else: chars.append(escapes.get(c,c))
        self.error()

    def arguments(self, depth):
        self.take('('); args = []
        if self.peek() == ')': self.take(')'); return args
        while True:
            args.append(self.value(depth + 1))
            if self.peek() == ')': self.take(')'); return args
            self.take(',')
            if self.peek() == ')': self.take(')'); return args

    def value(self, depth=0):
        self.nodes += 1
        if depth > 80 or self.nodes > 200000: raise DatabaseError('The BSON query literal is too complex')
        c = self.peek()
        if c in ('"', "'"): return self.string()
        if c == '{':
            self.take('{'); result = {}
            if self.peek() == '}': self.take('}'); return result
            while True:
                key = self.string() if self.peek() in ('"', "'") else self.identifier()
                if key in result: raise DatabaseError('Duplicate BSON document field names are not accepted')
                self.take(':'); result[key] = self.value(depth+1)
                if self.peek() == '}': self.take('}'); return result
                self.take(',')
                if self.peek() == '}': self.take('}'); return result
        if c == '[':
            self.take('['); result = []
            if self.peek() == ']': self.take(']'); return result
            while True:
                result.append(self.value(depth+1))
                if self.peek() == ']': self.take(']'); return result
                self.take(',')
                if self.peek() == ']': self.take(']'); return result
        if c == '/':
            self.take('/'); pattern = []; escaped = False; in_class = False
            while self.pos < len(self.text):
                char = self.text[self.pos]; self.pos += 1
                if char in '\r\n': self.error()
                if escaped: pattern.extend(['\\',char]); escaped = False; continue
                if char == '\\': escaped = True; continue
                if char == '[': in_class = True
                if char == ']': in_class = False
                if char == '/' and not in_class: break
                pattern.append(char)
            else: self.error()
            start = self.pos
            while self.pos < len(self.text) and self.text[self.pos].isalpha(): self.pos += 1
            flags = self.text[start:self.pos]
            if any(f not in 'imsxlu' for f in flags): raise DatabaseError('This JavaScript regular-expression flag is not a BSON regex flag')
            return Regex(''.join(pattern), flags)
        if self.text.startswith('-Infinity',self.pos): self.pos += 9; return float('-inf')
        if c and (c.isdigit() or c in '+-.'):
            import re
            match = re.match(r'[+-]?(?:0[xX][0-9a-fA-F]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)',self.text[self.pos:])
            if not match: self.error()
            token = match.group(); self.pos += len(token)
            if token in ('-0','-0.0'): return -0.0
            return float(token) if any(x in token for x in '.eE') and 'x' not in token.lower() else int(token,16) if 'x' in token.lower() else int(token)
        name = self.identifier()
        if name in {'true','false','null','NaN','Infinity'}:
            return {'true':True,'false':False,'null':None,'NaN':float('nan'),'Infinity':float('inf')}[name]
        is_new = name == 'new'
        if is_new: name = self.identifier()
        if self.peek() == '.': self.take('.'); name += '.' + self.identifier()
        args = self.arguments(depth)
        try:
            if name == 'ObjectId' and len(args) in (0,1): return ObjectId(*args)
            if name in {'Int32','NumberInt','Long','NumberLong'} and len(args) == 1:
                if isinstance(args[0],bool) or not isinstance(args[0],(str,int)): raise ValueError
                value = int(args[0]); bits = 64 if name in {'Long','NumberLong'} else 32
                if not -(2**(bits-1)) <= value < 2**(bits-1): raise ValueError
                return Int64(value) if bits == 64 else value
            if name in {'Decimal128','NumberDecimal'} and len(args) == 1 and isinstance(args[0],str): return Decimal128(args[0])
            if name in {'Double','NumberDouble'} and len(args) == 1 and isinstance(args[0],(str,int,float)): return float(args[0])
            if name == 'ISODate' and len(args) == 1 and isinstance(args[0],str):
                _date_string_precision(args[0])
                value = datetime.fromisoformat(args[0].replace('Z','+00:00'))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if name == 'Date' and is_new and len(args) == 1:
                if isinstance(args[0],str):
                    _date_string_precision(args[0])
                    value = datetime.fromisoformat(args[0].replace('Z','+00:00'))
                    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                if isinstance(args[0],int) and not isinstance(args[0],bool): return DatetimeMS(args[0])
            if name == 'Timestamp':
                if len(args) == 1 and isinstance(args[0],dict): return Timestamp(args[0]['t'],args[0]['i'])
                if len(args) == 2: return Timestamp(*args)
            if name in {'Binary.createFromBase64','BinData'}:
                import base64
                if name == 'BinData' and len(args) == 2: subtype, data = args
                elif name == 'Binary.createFromBase64' and len(args) in (1,2): data, subtype = args[0],args[1] if len(args)==2 else 0
                else: raise ValueError
                if not isinstance(subtype,int) or isinstance(subtype,bool) or not 0<=subtype<=255 or not isinstance(data,str):raise ValueError
                return Binary(base64.b64decode(data,validate=True),subtype)
            if name == 'UUID' and len(args) == 1:
                import uuid
                return Binary.from_uuid(uuid.UUID(args[0]),UuidRepresentation.STANDARD)
            if name == 'MinKey' and not args: return MinKey()
            if name == 'MaxKey' and not args: return MaxKey()
            if name == 'Code' and len(args) in (1,2): return Code(*args)
            if name == 'DBRef' and len(args) in (2,3): return DBRef(*args)
            if name == 'RegExp' and len(args) in (1,2):
                flags = args[1] if len(args)==2 else ''
                if not isinstance(flags,str) or any(x not in 'imsxlu' for x in flags):raise ValueError
                return Regex(args[0],flags)
            if name == 'EJSON.parse' and len(args)==1 and isinstance(args[0],str):
                parsed=json.loads(args[0],object_pairs_hook=_unique_pairs,parse_float=_finite_json_float,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Use Extended JSON for nonfinite numbers')))
                _validate_json_date_literals(parsed)
                return json_util.loads(args[0],json_options=JSON_OPTIONS)
        except Exception as exc:
            raise DatabaseError('Invalid BSON literal constructor') from exc
        raise DatabaseError('Only the documented BSON data constructors are supported; JavaScript is not evaluated')

    def commands(self):
        result = []
        while self.peek():
            if self.peek() == ';': self.take(';'); continue
            if self.identifier() != 'db': self.error()
            self.take('.'); member = self.identifier()
            if member == 'runCommand':
                args = self.arguments(0)
                if len(args)!=1 or not isinstance(args[0],dict):self.error()
                result.append(args[0])
            else:
                if member == 'getCollection':
                    args = self.arguments(0)
                    if len(args)!=1 or not isinstance(args[0],str):self.error()
                    collection = args[0]
                else: collection = member
                self.take('.'); verb = self.identifier(); args = self.arguments(0)
                if verb in {'find','findOne'}:
                    if len(args)>2 or any(not isinstance(a,dict) for a in args):self.error()
                    command = {'find':collection,'filter':args[0] if args else {}}
                    if len(args)==2:command['projection']=args[1]
                    if verb=='findOne':command['limit']=1;command['singleBatch']=True
                elif verb=='aggregate':
                    if len(args)not in (1,2) or not isinstance(args[0],list) or (len(args)==2 and not isinstance(args[1],dict)):self.error()
                    command={'aggregate':collection,'pipeline':args[0],'cursor':{}}
                    if len(args)==2:
                        for key,value in args[1].items():
                            if key not in {'allowDiskUse','maxTimeMS','hint','collation','comment','let','batchSize'}:self.error()
                            if key=='batchSize':command['cursor']['batchSize']=value
                            else:command[key]=value
                else:raise DatabaseError('This MongoDB shell method is not implemented; use an explicit BSON command')
                ended = verb=='findOne'
                while self.peek()=='.':
                    self.take('.'); method=self.identifier(); values=self.arguments(0)
                    if ended:self.error()
                    if method=='toArray' and not values:ended=True;continue
                    if method not in {'sort','skip','limit'} or len(values)!=1:self.error()
                    value=values[0]
                    if method=='sort':
                        if not isinstance(value,dict):self.error()
                    elif not isinstance(value,int) or isinstance(value,bool) or (method=='skip' and value<0) or (method=='limit' and verb=='aggregate' and value==0):self.error()
                    if verb=='aggregate':
                        if method=='limit' and value<0:self.error()
                        command['pipeline'].append({'$'+method:value})
                    elif method=='sort':command['sort']=value
                    elif method=='skip':command['skip']=value
                    else:
                        command['limit']=abs(value)
                        if value<0:command['singleBatch']=True
                result.append(command)
            if self.peek()==';':self.take(';')
            elif self.peek() and not self.text.startswith('db.',self.pos):self.error()
        return result


def parse_mongo_queries(text):
    """Canonical EJSON command or a documented non-evaluating BSON query subset."""
    if not isinstance(text,str): raise DatabaseError('MongoDB query text must be text')
    if text.lstrip().startswith('{'):
        commands=[canonical_loads(text)]
    else:
        commands=_MongoLiteralParser(text).commands()
    if not commands:raise DatabaseError('The MongoDB query is empty')
    for command in commands:
        _validate_date_precision(command)
        try:BSON.encode(command,codec_options=BSON_CODEC)
        except Exception as exc:raise DatabaseError('The MongoDB command contains invalid BSON') from exc
    return commands


class MongoSession(DatabaseSession):
    """BSON-native standalone adapter; owner-thread work, separate cancel channel."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.connection = None
        self._control = None
        self._tunnel = self._pem_dir = None
        self._selected_schema = config.database or "admin"
        self._seal_key = secrets.token_bytes(32)
        self._generation = secrets.token_hex(16)
        self._active_lock = threading.Lock()
        self._active_comment = None
        self._operation_depth = 0
        self._cancel_requested = threading.Event()
        self._server_major = 0
        self.metadata_issues = []

    @property
    def in_transaction(self):
        return False

    def _error(self, exc):
        return DatabaseError(str(exc), authentication_required=isinstance(exc, OperationFailure) and exc.code == 18)

    def _connection_options(self):
        c = self.config
        if not isinstance(c.connect_timeout, int) or isinstance(c.connect_timeout, bool) or not 1 <= c.connect_timeout <= 31536000:
            raise ValueError("Connection timeout must be positive")
        kind = getattr(c, "mongo_connection_type", "standalone").lower()
        if kind != "standalone" or getattr(c, "mongo_srv", False) or getattr(c, "mongo_replica_set", "") or getattr(c, "mongo_members", ""):
            raise ValueError("Replica sets, sharded clusters and SRV discovery have not completed native adapter acceptance")
        if getattr(c, "mongo_read_preference", "primary") != "primary":
            raise ValueError("Secondary read preferences have not completed adapter acceptance")
        mode = c.ssl_mode or "disable"
        if mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("MongoDB requires an explicit TLS mode; opportunistic TLS is not supported")
        if mode == "require" and (c.ssl_ca or c.ssl_crl):
            raise ValueError("MongoDB CA/CRL files require a certificate-verification mode")
        _tls_context(c)
        host = c.host or "localhost"
        if not isinstance(host, str) or any(char in host for char in "/@,?\0"):
            raise ValueError("MongoDB hostname must be a plain host, not a connection URI")
        raw_host = host.strip("[]")
        try:
            parsed_ip = ipaddress.ip_address(raw_host)
            raw_host = str(parsed_ip)
            host = "[" + raw_host + "]" if parsed_ip.version == 6 else raw_host
        except ValueError:
            if ":" in raw_host: raise ValueError("Invalid MongoDB hostname")
            raw_host = raw_host.encode("idna").decode("ascii")
            host = raw_host
        port = c.port or 27017
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("MongoDB port must be between 1 and 65535")
        kwargs = dict(host=host, port=port,
                      directConnection=True, appname="OpenNavicat", connect=False,
                      connectTimeoutMS=c.connect_timeout * 1000, serverSelectionTimeoutMS=c.connect_timeout * 1000,
                      socketTimeoutMS=None, timeoutMS=120000,
                      retryReads=bool(getattr(c, "mongo_retry_reads", True)),
                      retryWrites=bool(getattr(c, "mongo_retry_writes", True)),
                      tls=mode != "disable")
        mechanism = getattr(c, "mongo_auth_mechanism", "DEFAULT").upper()
        if mechanism not in {"NONE", "DEFAULT", "SCRAM-SHA-1", "SCRAM-SHA-256"}:
            raise ValueError("This MongoDB authentication mechanism has not completed adapter acceptance")
        if mechanism != "NONE" and not c.username and (c.password or mechanism != "DEFAULT"):
            raise ValueError("A MongoDB username is required for password authentication")
        if mechanism != "NONE" and c.username:
            if c.username: kwargs["username"] = c.username
            kwargs["password"] = c.password
            kwargs["authSource"] = getattr(c, "mongo_auth_source", "admin") or "admin"
            if mechanism != "DEFAULT": kwargs["authMechanism"] = mechanism
        if mode != "disable":
            kwargs.update(tlsAllowInvalidCertificates=mode == "require", tlsAllowInvalidHostnames=mode != "verify-full")
            if mode in {"verify-ca", "verify-full"}:
                if c.ssl_ca: kwargs["tlsCAFile"] = str(Path(c.ssl_ca).expanduser())
                if c.ssl_crl: kwargs["tlsCRLFile"] = str(Path(c.ssl_crl).expanduser())
            if c.ssl_cert:
                self._pem_dir = tempfile.TemporaryDirectory(prefix="opennavicat-mongo-tls-")
                pem = Path(self._pem_dir.name) / "client.pem"
                with pem.open("xb") as stream:
                    pem.chmod(0o600)
                    key_path = Path(c.ssl_key).expanduser().resolve()
                    cert_path = Path(c.ssl_cert).expanduser().resolve()
                    stream.write(key_path.read_bytes())
                    if key_path != cert_path:
                        stream.write(b"\n")
                        stream.write(cert_path.read_bytes())
                kwargs.update(tlsCertificateKeyFile=str(pem), tlsCertificateKeyFilePassword=c.ssl_key_password or "")
        if c.ssh_enabled:
            self._tunnel = SSHTunnel(c, raw_host, port).start()
            kwargs.update(host=self._tunnel.local_host, port=self._tunnel.local_port,
                          _pool_class=_tunnel_pool_class(raw_host))
        return kwargs

    def connect(self):
        if self.connection is not None:
            return
        try:
            kwargs = self._connection_options()
            self.connection = MongoClient(**kwargs)
            # CSOT timeoutMS overrides serverSelectionTimeoutMS: explicitly bound
            # initial selection/handshake/authentication by the configured timeout.
            with mongo_timeout(self.config.connect_timeout):
                self.connection.admin.command("ping")
                version = self.connection.server_info()["versionArray"]
                self._server_major = version[0]
                self._control = MongoClient(**kwargs)
                self._control.admin.command("ping")
        except Exception as exc:
            self.close()
            if isinstance(exc, (SSHHostKeyError, SSHAuthenticationError, TLSKeyPasswordError)):
                raise
            raise self._error(exc) from exc

    def close(self):
        for client in (self.connection, self._control):
            if client is not None:
                with contextlib.suppress(Exception): client.close()
        self.connection = self._control = None
        if self._tunnel is not None:
            self._tunnel.close(); self._tunnel = None
        if self._pem_dir is not None:
            self._pem_dir.cleanup(); self._pem_dir = None
        self._generation = secrets.token_hex(16)
        self._active_comment = None

    def _check(self):
        if self.connection is None:
            raise DatabaseError("MongoDB is not connected")

    @contextlib.contextmanager
    def _operation(self, user_comment=_NO_COMMENT):
        self._check()
        comment = "OpenNavicat:" + secrets.token_hex(16)
        if user_comment is not _NO_COMMENT:
            # Copy typed BSON so caller mutations cannot change cancellation
            # identity after dispatch. The user comment remains visible intact.
            copied = BSON(BSON.encode({"value": user_comment}, codec_options=BSON_CODEC)).decode(codec_options=BSON_CODEC)["value"]
            comment = {"OpenNavicat": comment, "userComment": copied}
        with self._active_lock:
            # Clearing the cooperative flag and replacing the cancellation
            # identity must be atomic with cancel's identity check. Otherwise
            # a delayed request for the previous operation can set the new
            # operation's flag between these two actions.
            if self._operation_depth == 0: self._cancel_requested.clear()
            self._operation_depth += 1
            previous = self._active_comment
            self._active_comment = comment
        try:
            yield comment
        except DatabaseError:
            raise
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            with self._active_lock:
                if self._active_comment == comment: self._active_comment = previous
                self._operation_depth -= 1

    def cancellation_marker(self):
        """Snapshot the current operation's public cancellation identity.

        Capture this when scheduling a cancellation, then pass it to cancel.
        A None snapshot intentionally cannot cancel a later operation. The
        returned typed comment is detached from this session's active value.
        """
        with self._active_lock:
            return copy.deepcopy(self._active_comment)

    def cancel(self, expected_comment=_NO_COMMENT):
        """Cancel the current operation, optionally requiring a captured marker.

        No argument preserves the immediate-query cancellation contract. A
        supplied stale marker (including None) does not set the cooperative
        flag or open the control channel. Once accepted, network cancellation
        targets only that captured nonce even if the owner starts new work.
        """
        with self._active_lock:
            comment = self._active_comment
            if expected_comment is not _NO_COMMENT:
                if expected_comment is None or comment is None:
                    return
                # BSON equality preserves typed comments, NaN representations
                # and field order; ordinary Python equality can conflate types.
                try:
                    if BSON.encode({'comment': expected_comment}, codec_options=BSON_CODEC) != BSON.encode({'comment': comment}, codec_options=BSON_CODEC):
                        return
                except Exception:
                    return
            self._cancel_requested.set()
            comment = copy.deepcopy(comment)
        if not comment or self._control is None:
            return
        try:
            with mongo_timeout(3):
                rows = self._control.admin.aggregate([{"$currentOp": {"allUsers": False}}, {"$match": {"command.comment": comment}}], maxTimeMS=3000)
                try:
                    for row in rows:
                        self._control.admin.command({"killOp": 1, "op": row["opid"]}, maxTimeMS=3000)
                finally:
                    rows.close()
        except PyMongoError as exc:
            raise DatabaseError("The server did not allow cancellation of this operation") from exc

    def schemas(self):
        with self._operation() as comment:
            return self.connection.list_database_names(comment=comment)

    def use_schema(self, database):
        self._validate_namespace(database)
        self._selected_schema = database

    @staticmethod
    def _validate_namespace(database, collection=None):
        if not isinstance(database, str) or not database or any(c in database for c in '/\\. "$*<>:|?\0'):
            raise DatabaseError("Invalid MongoDB database name")
        if collection is not None and (not isinstance(collection, str) or not collection or "\0" in collection):
            raise DatabaseError("Invalid MongoDB collection name")

    def collection_info(self, database, collection):
        self._validate_namespace(database, collection)
        with self._operation() as comment:
            result = list(self.connection[database].list_collections(filter={"name": collection}, comment=comment))
            if not result: raise DatabaseError("The MongoDB collection no longer exists")
            return result[0]

    def objects(self, database):
        self._validate_namespace(database)
        with self._operation() as comment:
            specs = list(self.connection[database].list_collections(comment=comment))
            self.metadata_issues = []
            result = []
            names = {s["name"] for s in specs}
            buckets = {n[:-6] for n in names if n.endswith(".files") and n[:-6] + ".chunks" in names}
            for spec in specs:
                name = spec["name"]
                kind = "mongo_view" if spec["type"] == "view" else "mongo_timeseries" if spec["type"] == "timeseries" else "mongo_collection"
                stats = self.collection_stats(database, name) if spec["type"] != "view" else {}
                result.append(dict(name=name, schema=database, kind=kind, options=spec.get("options", {}), stats=stats,
                    system=name.startswith("system.") or any(name in {bucket + ".files", bucket + ".chunks"} for bucket in buckets)))
            result.extend(dict(name=n, schema=database, kind="mongo_bucket", system=False) for n in sorted(buckets))
            return sorted(result, key=lambda row: (row["kind"], row["name"]))

    @staticmethod
    def _collection_uuid(info):
        return info.get("info", {}).get("uuid")

    def _token(self, database, collection, raw, editable, namespace_uuid=None):
        purpose = b"full" if editable else b"readonly"
        config = {k: v for k, v in vars(self.config).items() if "password" not in k and "passphrase" not in k}
        context = json.dumps([config, self._generation, database, collection], sort_keys=True, default=str, separators=(",", ":")).encode()
        context += BSON.encode({"uuid": namespace_uuid}, codec_options=BSON_CODEC)
        return purpose + b":" + hmac.digest(self._seal_key, context + b"\0" + purpose + b"\0" + raw, "sha256")

    def _snapshot(self, database, collection, raw, readonly_reason="", namespace_uuid=None):
        data = BSON(raw).decode(codec_options=BSON_CODEC)
        reason = readonly_reason
        if not reason:
            try:
                if self.config.read_only: raise DatabaseError("This connection is read-only")
                if self._server_major < 5: raise DatabaseError("Exact document CAS requires MongoDB 5 or newer")
                if namespace_uuid is None: raise DatabaseError("A stable collection UUID is required for safe editing")
                if len(raw) > _MAX_EDIT_BYTES: raise DatabaseError("Documents larger than 1 MiB are read-only in this editor")
                if "_id" not in data: raise DatabaseError("A full document with _id is required for editing")
                if BSON.encode(canonical_loads(canonical_dumps(data)), codec_options=BSON_CODEC) != raw:
                    raise DatabaseError("This BSON document cannot roundtrip losslessly through canonical Extended JSON")
                _exact_filter(data)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, DatabaseError) else "This BSON document requires a raw BSON editor"
        editable = not bool(reason)
        return MongoDocument(data, bytes(raw), self._token(database, collection, raw, editable, namespace_uuid), editable, reason)

    def fetch_documents(self, database, collection, filter_doc=None, sort=None, projection=None, offset=0, limit=1000):
        self._validate_namespace(database, collection)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10000:
            raise DatabaseError("Document page offset/limit is invalid")
        if filter_doc is not None and not isinstance(filter_doc, dict): raise DatabaseError("A MongoDB filter must be a document")
        info = self.collection_info(database, collection)
        reason = "Projected documents must be refetched in full before editing" if projection is not None else ""
        if info["type"] != "collection": reason = "Views and time-series documents are read-only in this editor"
        if collection.startswith("system.") or collection.endswith((".files", ".chunks")):
            reason = "Internal/GridFS documents require their dedicated tool"
        with self._operation() as comment:
            cursor = self.connection.get_database(database, codec_options=RAW_CODEC)[collection].find(
                filter_doc or {}, projection=projection, sort=sort, skip=offset, limit=limit + 1, batch_size=min(limit + 1, 100), comment=comment)
            documents, size, has_more = [], 0, False
            try:
                for rawdoc in cursor:
                    raw = rawdoc.raw
                    if len(documents) >= limit or (documents and size + len(raw) > _MAX_PAGE_BYTES):
                        has_more = True; break
                    documents.append(self._snapshot(database, collection, raw, reason, self._collection_uuid(info))); size += len(raw)
            finally:
                cursor.close()
            def literal(value):
                return "EJSON.parse(" + json.dumps(canonical_dumps(value), ensure_ascii=True) + ")"
            text = f"db.getCollection({json.dumps(collection)}).find({literal(filter_doc or {})}"
            if projection is not None: text += ", " + literal(projection)
            text += ")"
            if sort: text += ".sort(" + literal(dict(sort)) + ")"
            text += f".limit({limit}).skip({offset})"
            return MongoPage(documents, offset, limit, has_more, None, text, offset + len(documents))

    def _verify_expected(self, database, collection, expected):
        self._validate_namespace(database, collection)
        self._check()
        if self.config.read_only: raise DatabaseError("This connection is read-only")
        if not isinstance(expected, MongoDocument) or not expected.editable:
            raise DatabaseError("This document is not editable")
        info = self.collection_info(database, collection)
        namespace_uuid = self._collection_uuid(info)
        wanted = self._token(database, collection, expected.raw_bson, True, namespace_uuid)
        if not hmac.compare_digest(expected.cas_token, wanted):
            raise MongoConflictError("The document belongs to a different or stale session/namespace")
        proof = self._snapshot(database, collection, expected.raw_bson, namespace_uuid=namespace_uuid)
        if not proof.editable: raise DatabaseError(proof.edit_reason)
        return proof.document, namespace_uuid

    @staticmethod
    def _checked_write_result(result):
        if result.get("writeErrors"):
            error = result["writeErrors"][0]
            if error.get("code") == 361:
                raise MongoConflictError("The collection was replaced; refresh before writing")
            raise DatabaseError("MongoDB rejected the document write (code " + str(error.get("code", "unknown")) + ")", authentication_required=error.get("code") == 18) from OperationFailure(error.get("errmsg", "Write failed"), code=error.get("code"), details=error)
        if result.get("writeConcernError"):
            raise DatabaseError("Write acknowledgement is uncertain; refresh before retrying")
        return result

    def replace_document(self, database, collection, document, expected):
        original, namespace_uuid = self._verify_expected(database, collection, expected)
        if not isinstance(document, dict) or "_id" not in document or BSON.encode({"_id": document["_id"]}, codec_options=BSON_CODEC) != BSON.encode({"_id": original["_id"]}, codec_options=BSON_CODEC):
            raise DatabaseError("The BSON _id value and type cannot be changed")
        _validate_date_precision(document)
        encoded = BSON.encode(document, codec_options=BSON_CODEC)
        if len(encoded) > _MAX_EDIT_BYTES: raise DatabaseError("The document editor accepts at most 1 MiB of BSON")
        # Validate new editor data as well; no silent conversion of ambiguous wrappers.
        if BSON.encode(canonical_loads(canonical_dumps(document)), codec_options=BSON_CODEC) != encoded:
            raise DatabaseError("The edited document cannot roundtrip losslessly through Extended JSON")
        with self._operation() as comment:
            replacement = BSON(encoded).decode(codec_options=BSON_CODEC)
            result = self._checked_write_result(self.connection[database].command({
                "update": collection, "updates": [{"q": _exact_filter(original), "u": [{"$replaceWith": {"$literal": replacement}}],
                    "multi": False, "upsert": False, "collation": {"locale": "simple"}}],
                "ordered": True, "collectionUUID": namespace_uuid, "comment": comment}))
            if result.get("n") != 1: raise MongoConflictError("The document changed or was deleted; refresh before saving")
            return self._snapshot(database, collection, encoded, namespace_uuid=namespace_uuid)

    def delete_document(self, database, collection, expected):
        original, namespace_uuid = self._verify_expected(database, collection, expected)
        with self._operation() as comment:
            result = self._checked_write_result(self.connection[database].command({
                "delete": collection, "deletes": [{"q": _exact_filter(original), "limit": 1,
                    "collation": {"locale": "simple"}}], "ordered": True,
                "collectionUUID": namespace_uuid, "comment": comment}))
            if result.get("n") != 1: raise MongoConflictError("The document changed or was deleted; refresh before deleting")

    def insert_document(self, database, collection, document):
        self._validate_namespace(database, collection)
        if self.config.read_only: raise DatabaseError("This connection is read-only")
        if not isinstance(document, dict): raise DatabaseError("A document must be an object")
        _validate_date_precision(document)
        encoded = BSON.encode(document, codec_options=BSON_CODEC)
        if len(encoded) > _MAX_EDIT_BYTES: raise DatabaseError("The document editor accepts at most 1 MiB of BSON")
        if BSON.encode(canonical_loads(canonical_dumps(document)), codec_options=BSON_CODEC) != encoded:
            raise DatabaseError("The edited document cannot roundtrip losslessly through Extended JSON")
        info = self.collection_info(database, collection)
        if info["type"] == "view" or collection.startswith("system.") or collection.endswith((".files", ".chunks")):
            raise DatabaseError("This namespace requires its dedicated tool")
        with self._operation() as comment:
            # Decode a copy: PyMongo may add _id, but must not mutate a GUI draft.
            value = BSON(encoded).decode(codec_options=BSON_CODEC)
            if "_id" not in value: value = {"_id": ObjectId(), **value}
            namespace_uuid = self._collection_uuid(info)
            if namespace_uuid is None: raise DatabaseError("A stable collection UUID is required for safe inserts")
            result = self._checked_write_result(self.connection[database].command({
                "insert": collection, "documents": [value], "ordered": True,
                "collectionUUID": namespace_uuid, "comment": comment}))
            if result.get("n") != 1: raise DatabaseError("The insert was not acknowledged")
            raw = BSON.encode(value, codec_options=BSON_CODEC)
            return self._snapshot(database, collection, raw,
                "Time-series document replacement is not supported" if info["type"] == "timeseries" else "",
                namespace_uuid)

    @staticmethod
    def _validate_bucket(database, bucket):
        MongoSession._validate_namespace(database, bucket)
        if bucket.startswith('system.') or bucket.endswith(('.files', '.chunks')):
            raise DatabaseError('Use a GridFS bucket name, not an internal collection')

    def _transfer_cancelled(self, cancelled=None):
        if self._cancel_requested.is_set() or (cancelled is not None and cancelled()):
            raise DatabaseError('The GridFS transfer was cancelled')

    def _gridfs_collections(self, database, bucket, create=False, comment=None):
        self._validate_bucket(database, bucket)
        result = {}
        for suffix in ('files', 'chunks'):
            name = bucket + '.' + suffix
            rows = list(self.connection[database].list_collections(filter={'name': name}, comment=comment))
            if not rows and create:
                try: self.connection[database].create_collection(name, comment=comment)
                except OperationFailure as exc:
                    if exc.code != 48: raise
                rows = list(self.connection[database].list_collections(filter={'name': name}, comment=comment))
            if not rows:
                result[suffix] = None
            elif rows[0]['type'] != 'collection' or self._collection_uuid(rows[0]) is None:
                raise DatabaseError('GridFS requires ordinary collections with stable UUIDs')
            else:
                result[suffix] = self._collection_uuid(rows[0])
        return result

    def _gridfs_snapshot(self, database, bucket, raw, namespace_uuid):
        return self._snapshot(database, bucket + '.files', raw,
                              'GridFS metadata uses its dedicated file operations', namespace_uuid)

    def list_files(self, database, bucket, filter_doc=None, offset=0, limit=100):
        self._validate_bucket(database, bucket)
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or
            not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10000):
            raise DatabaseError('GridFS page offset/limit is invalid')
        if filter_doc is not None and not isinstance(filter_doc, dict):
            raise DatabaseError('The GridFS filter must be a BSON document')
        with self._operation() as comment:
            ids = self._gridfs_collections(database, bucket, comment=comment)
            docs, size, more = [], 0, False
            if ids['files'] is not None:
                collection = self.connection.get_database(database, codec_options=RAW_CODEC)[bucket + '.files']
                cursor = collection.find(filter_doc or {}, comment=comment).sort([('uploadDate', -1), ('_id', 1)]).skip(offset).limit(limit + 1).batch_size(min(limit + 1, 100))
                try:
                    for item in cursor:
                        raw = item.raw
                        if len(docs) >= limit or (docs and size + len(raw) > _MAX_PAGE_BYTES):
                            more = True; break
                        docs.append(self._gridfs_snapshot(database, bucket, raw, ids['files']))
                        size += len(raw)
                finally: cursor.close()
            return MongoPage(docs, offset, limit, more, None,
                             'GridFS file metadata (native files collection)', offset + len(docs))

    def _verify_file(self, database, bucket, expected, namespace_uuid, check_current=True):
        if not isinstance(expected, MongoDocument):
            raise DatabaseError('A complete GridFS file snapshot is required')
        wanted = self._token(database, bucket + '.files', expected.raw_bson, False, namespace_uuid)
        if not hmac.compare_digest(expected.cas_token, wanted):
            raise MongoConflictError('The GridFS file belongs to a stale session or namespace')
        original = BSON(expected.raw_bson).decode(codec_options=BSON_CODEC)
        if not all(key in original for key in ('_id', 'length', 'chunkSize', 'uploadDate')):
            raise DatabaseError('The GridFS metadata is incomplete')
        if check_current:
            current = self.connection.get_database(database, codec_options=RAW_CODEC)[bucket + '.files'].find_one({'_id': {'$eq': original['_id']}})
            if current is None or current.raw != expected.raw_bson:
                raise MongoConflictError('The GridFS file changed or was deleted; refresh before retrying')
        return original

    def upload_file(self, database, bucket, source_path, filename=None, metadata=None, progress=None, cancelled=None):
        """Publish a new GridFS file only after all acknowledged chunks exist.

        Standalone GridFS is a multi-document protocol, not a transaction. Failure
        removes only this upload's new ObjectId; a cleanup failure is explicit.
        """
        self._validate_bucket(database, bucket)
        if self.config.read_only: raise DatabaseError('This connection is read-only')
        source = Path(source_path).expanduser()
        if filename is None: filename = source.name
        if not isinstance(filename, str) or not filename or '\0' in filename:
            raise DatabaseError('A nonempty GridFS filename is required')
        if metadata is not None:
            if not isinstance(metadata, dict): raise DatabaseError('GridFS metadata must be a document')
            _validate_date_precision(metadata)
            metadata = BSON(BSON.encode(metadata, codec_options=BSON_CODEC)).decode(codec_options=BSON_CODEC)
        file_id, chunk_size = ObjectId(), 255 * 1024
        ids = None
        with self._operation() as comment:
            self._transfer_cancelled(cancelled)
            with source.open('rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode): raise DatabaseError('Upload requires a regular file')
                total = before.st_size
                if progress: progress(0, total)
                self._transfer_cancelled(cancelled)
                ids = self._gridfs_collections(database, bucket, create=True, comment=comment)
                from pymongo import IndexModel
                self.connection[database][bucket + '.files'].create_indexes([
                    IndexModel([('filename', 1), ('uploadDate', 1)])], comment=comment)
                self.connection[database][bucket + '.chunks'].create_indexes([
                    IndexModel([('files_id', 1), ('n', 1)], unique=True)], comment=comment)
                sent, index = 0, 0
                publication_attempted = False
                digest = hashlib.md5(usedforsecurity=False)
                try:
                    while True:
                        self._transfer_cancelled(cancelled)
                        chunk = stream.read(chunk_size)
                        if not chunk: break
                        result = self._checked_write_result(self.connection[database].command({
                            'insert': bucket + '.chunks', 'documents': [{'_id': ObjectId(), 'files_id': file_id,
                                'n': index, 'data': Binary(chunk)}], 'ordered': True,
                            'collectionUUID': ids['chunks'], 'comment': comment}))
                        if result.get('n') != 1: raise DatabaseError('The GridFS chunk was not acknowledged')
                        digest.update(chunk); sent += len(chunk); index += 1
                        if progress: progress(sent, total)
                    after = os.fstat(stream.fileno())
                    if sent != total or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise DatabaseError('The upload source changed while being read')
                    self._transfer_cancelled(cancelled)
                    if self._gridfs_collections(database, bucket, comment=comment) != ids:
                        raise MongoConflictError('The GridFS bucket changed during the upload')
                    document = {'_id': file_id, 'length': Int64(sent), 'chunkSize': chunk_size,
                                'uploadDate': datetime.now(timezone.utc), 'filename': filename, 'md5': digest.hexdigest()}
                    mime = mimetypes.guess_type(filename)[0]
                    if mime: document['contentType'] = mime
                    if metadata is not None: document['metadata'] = metadata
                    raw = BSON.encode(document, codec_options=BSON_CODEC)
                    publication_attempted = True
                    result = self._checked_write_result(self.connection[database].command({
                        'insert': bucket + '.files', 'documents': [document], 'ordered': True,
                        'collectionUUID': ids['files'], 'comment': comment}))
                    if result.get('n') != 1: raise DatabaseError('The GridFS file was not acknowledged')
                    return self._gridfs_snapshot(database, bucket, raw, ids['files'])
                except BaseException as exc:
                    # Before publication, only own chunks can exist. After a
                    # publication attempt, first confirm metadata removal; if
                    # that fails, retain chunks rather than corrupt a possibly
                    # complete visible upload whose acknowledgement was lost.
                    try:
                        cleanups = []
                        if publication_attempted: cleanups.append(('files', {'_id': {'$eq': file_id}}))
                        cleanups.append(('chunks', {'files_id': {'$eq': file_id}}))
                        for suffix, query in cleanups:
                            self._checked_write_result(self.connection[database].command({
                                'delete': bucket + '.' + suffix, 'deletes': [{'q': query, 'limit': 0}],
                                'collectionUUID': ids[suffix], 'comment': comment}))
                    except Exception as cleanup:
                        raise DatabaseError('The upload failed and cleanup was not confirmed; refresh the bucket before retrying') from cleanup
                    raise exc

    def download_file(self, database, bucket, file_id, target_path, overwrite=False, expected=None, progress=None, cancelled=None):
        """Download into a sibling temporary file, then commit atomically.

        Default no-replace uses a filesystem hard link, including the race with
        another process creating the destination. No existing file is truncated.
        """
        self._validate_bucket(database, bucket)
        target = Path(target_path).expanduser()
        parent = target.parent.resolve(strict=True)
        target = parent / target.name
        if target.is_symlink() or (target.exists() and (not overwrite or not target.is_file())):
            raise DatabaseError('The download destination already exists or is unsafe')
        temporary = None
        with self._operation() as comment:
            self._transfer_cancelled(cancelled)
            ids = self._gridfs_collections(database, bucket, comment=comment)
            if ids['files'] is None or ids['chunks'] is None: raise DatabaseError('The GridFS bucket no longer exists')
            if expected is None:
                rawdoc = self.connection.get_database(database, codec_options=RAW_CODEC)[bucket + '.files'].find_one({'_id': {'$eq': file_id}}, comment=comment)
                if rawdoc is None: raise DatabaseError('The GridFS file no longer exists')
                expected = self._gridfs_snapshot(database, bucket, rawdoc.raw, ids['files'])
            original = self._verify_file(database, bucket, expected, ids['files'])
            if BSON.encode({'_id': file_id}, codec_options=BSON_CODEC) != BSON.encode({'_id': original['_id']}, codec_options=BSON_CODEC):
                raise DatabaseError('The GridFS file ID does not match its snapshot')
            from gridfs import GridOut
            sent, total = 0, original['length']
            digest = hashlib.md5(usedforsecurity=False)
            try:
                descriptor, name = tempfile.mkstemp(prefix='.opennavicat-download-', dir=parent)
                temporary = Path(name)
                with os.fdopen(descriptor, 'wb') as stream, GridOut(self.connection[database][bucket], file_id=file_id, file_document=original) as download:
                    if progress: progress(0, total)
                    while True:
                        self._transfer_cancelled(cancelled)
                        chunk = download.readchunk()
                        if not chunk: break
                        stream.write(chunk); digest.update(chunk); sent += len(chunk)
                        if progress: progress(sent, total)
                    if sent != total: raise DatabaseError('The GridFS file length does not match its metadata')
                    if original.get('md5') and original['md5'].lower() != digest.hexdigest():
                        raise DatabaseError('The GridFS file checksum does not match its metadata')
                    stream.flush(); os.fsync(stream.fileno())
                self._transfer_cancelled(cancelled)
                after = self._gridfs_collections(database, bucket, comment=comment)
                if after != ids: raise MongoConflictError('The GridFS bucket changed during the download')
                self._verify_file(database, bucket, expected, ids['files'])
                if overwrite:
                    os.replace(temporary, target)
                else:
                    try: os.link(temporary, target)
                    except FileExistsError as exc: raise DatabaseError('The download destination already exists') from exc
                    temporary.unlink()
                temporary = None
                return sent
            finally:
                if temporary is not None:
                    with contextlib.suppress(FileNotFoundError): temporary.unlink()

    def delete_file(self, database, bucket, expected):
        self._validate_bucket(database, bucket)
        if self.config.read_only: raise DatabaseError('This connection is read-only')
        with self._operation() as comment:
            ids = self._gridfs_collections(database, bucket, comment=comment)
            if ids['files'] is None or ids['chunks'] is None: raise MongoConflictError('The GridFS bucket no longer exists')
            original = self._verify_file(database, bucket, expected, ids['files'], check_current=False)
            result = self._checked_write_result(self.connection[database].command({
                'delete': bucket + '.files', 'deletes': [{'q': _exact_filter(original), 'limit': 1,
                    'collation': {'locale': 'simple'}}], 'collectionUUID': ids['files'], 'comment': comment}))
            if result.get('n') != 1: raise MongoConflictError('The GridFS file changed or was deleted; refresh before deleting')
            try:
                self._checked_write_result(self.connection[database].command({
                    'delete': bucket + '.chunks', 'deletes': [{'q': {'files_id': {'$eq': original['_id']}}, 'limit': 0}],
                    'collectionUUID': ids['chunks'], 'comment': comment}))
            except Exception as exc:
                raise DatabaseError('File metadata was deleted, but orphan chunk cleanup was not confirmed; refresh before retrying') from exc

    def execute(self, command, limit=1000):
        from .mongodb_query import execute_queries
        return execute_queries(self, command, limit)

    def _unsupported_relational(self, *args, **kwargs):
        raise DatabaseError("This MongoDB session uses document APIs, not SQL table operations")

    columns = foreign_keys = fetch_table = update_row = delete_row = import_rows = backup_to = _unsupported_relational

    def indexes(self, database, collection=None):
        self._validate_namespace(database, collection)
        if collection is None:
            with self._operation() as comment:
                specs = list(self.connection[database].list_collections(comment=comment))
            result = []
            self.metadata_issues = []
            for spec in specs:
                if spec["type"] == "view": continue
                try:
                    result.extend(self.indexes(database, spec["name"]))
                except DatabaseError:
                    self.metadata_issues.append({"operation": "listIndexes", "database": database,
                                                 "collection": spec["name"], "available": False})
            return result
        with self._operation() as comment:
            rows = list(self.connection[database][collection].list_indexes(comment=comment))
            return [{"name": row["name"], "collection": collection, "schema": database,
                     "columns": list(row["key"]), "keys": list(row["key"].items()),
                     "unique": bool(row.get("unique", row["name"] == "_id_")), "primary": row["name"] == "_id_",
                     "system": collection.startswith("system.") or collection.endswith((".files", ".chunks")) or row["name"] == "_id_",
                     "options": dict(row), "definition": canonical_dumps(dict(row))} for row in rows]

    def collection_stats(self, database, collection):
        self._validate_namespace(database, collection)
        self._check()
        try:
            with mongo_timeout(3), self._operation() as comment:
                return self.connection[database].command({"collStats": collection, "comment": comment, "maxTimeMS": 3000})
        except DatabaseError:
            # Collection stats are optional metadata. An unavailable command or
            # scoped role cannot turn unknown values into zeros or hide names.
            self.metadata_issues.append({"operation": "collStats", "database": database,
                                         "collection": collection, "available": False})
            return {}

    @staticmethod
    def _read_pipeline(pipeline):
        if not isinstance(pipeline, list): raise DatabaseError("An aggregation pipeline must be an array")
        allowed = {"$addFields", "$bucket", "$bucketAuto", "$collStats", "$count", "$densify", "$documents", "$facet",
                   "$fill", "$geoNear", "$graphLookup", "$group", "$indexStats", "$limit", "$lookup", "$match",
                   "$project", "$redact", "$replaceRoot", "$replaceWith", "$sample", "$set", "$setWindowFields",
                   "$skip", "$sort", "$sortByCount", "$unionWith", "$unset", "$unwind"}
        for stage in pipeline:
            if not isinstance(stage, dict) or len(stage) != 1:
                raise DatabaseError("Each aggregation stage must be a one-field document")
            op, value = next(iter(stage.items()))
            if op not in allowed:
                raise DatabaseError("This aggregation stage has not completed read-only adapter acceptance")
            if op == "$facet" and isinstance(value, dict):
                for child in value.values(): MongoSession._read_pipeline(child)
            elif op in {"$lookup", "$unionWith"} and isinstance(value, dict) and "pipeline" in value:
                MongoSession._read_pipeline(value["pipeline"])
        return pipeline

    def aggregate_documents(self, database, collection, pipeline, offset=0, limit=1000):
        self._validate_namespace(database, collection)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10000:
            raise DatabaseError("Aggregation page offset/limit is invalid")
        self._read_pipeline(pipeline)
        info = self.collection_info(database, collection)
        bounded = [*pipeline, {"$skip": offset}, {"$limit": limit + 1}]
        with self._operation() as comment:
            cursor = self.connection.get_database(database, codec_options=RAW_CODEC)[collection].aggregate(
                bounded, batchSize=min(limit + 1, 100), comment=comment)
            documents, size, has_more = [], 0, False
            try:
                for rawdoc in cursor:
                    raw = rawdoc.raw
                    if len(documents) >= limit or (documents and size + len(raw) > _MAX_PAGE_BYTES):
                        has_more = True; break
                    documents.append(self._snapshot(database, collection, raw,
                        "Aggregation results must be refetched from their collection before editing",
                        self._collection_uuid(info)))
                    size += len(raw)
            finally:
                cursor.close()
            rendered = json_util.dumps([*pipeline, {"$skip": offset}, {"$limit": limit}], json_options=JSON_OPTIONS, ensure_ascii=True)
            text = f"db.getCollection({json.dumps(collection)}).aggregate(EJSON.parse({json.dumps(rendered)}))"
            return MongoPage(documents, offset, limit, has_more, None, text, offset + len(documents))
