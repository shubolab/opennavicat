"""MongoDB's BSON-native document adapter; no SQL emulation.

Document browsing/editor APIs preserve raw BSON preimages and their types.
Canonical Extended JSON is an explicit editing format, not a claim of complete
mongosh JavaScript compatibility. The full native shell remains a separate gate.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import math
import ipaddress
import secrets
import socket
import socketserver
import select
import struct
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
from pymongo import MongoClient, ReturnDocument
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


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatabaseError("Duplicate document field names are not accepted")
        result[key] = value
    return result


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


def canonical_loads(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise DatabaseError("The document editor accepts at most 16 MiB of Extended JSON")
    try:
        # The JSON library's object_pairs_hook suppresses object_hook, so validate
        # duplicates separately before json_util performs BSON wrapper decoding.
        json.loads(text, object_pairs_hook=_unique_pairs,
                   parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Use Extended JSON for nonfinite numbers")))
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



class MongoConflictError(DatabaseError):
    """The exact typed document or the session binding no longer matches."""


_MAX_PAGE_BYTES = 16 * 1024 * 1024
_MAX_EDIT_BYTES = 1024 * 1024
_MAX_DEPTH = 80


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


class _SocksProxy:
    """Documented PyMongo SOCKS transport keeps the real TLS hostname intact.

    Only one configured database endpoint is allowed; this is not an open proxy.
    SSH identity/authentication and deterministic channel cleanup remain owned by
    the existing SSHTunnel. Replica/SRV discovery is not silently routed elsewhere.
    """
    def __init__(self, tunnel, hostname, port, timeout):
        self.tunnel, self.hostname, self.port = tunnel, hostname, port
        self.timeout = timeout
        self.sockets = set()
        self.lock = threading.Lock()
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                client, channel = self.request, None
                with owner.lock: owner.sockets.add(client)
                def read(n):
                    result = b""
                    while len(result) < n:
                        data = client.recv(n - len(result))
                        if not data: raise EOFError
                        result += data
                    return result
                try:
                    client.settimeout(owner.timeout)
                    version, count = read(2)
                    methods = read(count)
                    if version != 5 or 0 not in methods:
                        client.sendall(b"\x05\xff"); return
                    client.sendall(b"\x05\x00")
                    version, command, _, address_type = read(4)
                    if version != 5 or command != 1: raise ValueError("Unsupported SOCKS request")
                    if address_type == 3: hostname = read(read(1)[0]).decode("ascii")
                    elif address_type == 1: hostname = socket.inet_ntop(socket.AF_INET, read(4))
                    elif address_type == 4: hostname = socket.inet_ntop(socket.AF_INET6, read(16))
                    else: raise ValueError("Unsupported SOCKS address")
                    port = struct.unpack("!H", read(2))[0]
                    if hostname.lower() != owner.hostname.lower() or port != owner.port:
                        client.sendall(b"\x05\x02\x00\x01" + b"\0" * 6); return
                    channel = owner.tunnel.transport.open_channel("direct-tcpip", (owner.hostname, owner.port), self.client_address, timeout=owner.timeout)
                    with owner.lock: owner.sockets.add(channel)
                    client.sendall(b"\x05\x00\x00\x01" + b"\0" * 6)
                    client.settimeout(None)
                    while True:
                        ready, _, _ = select.select([client, channel], [], [], .25)
                        for src in ready:
                            data = src.recv(65536)
                            if not data: return
                            (channel if src is client else client).sendall(data)
                except Exception:
                    pass  # Query/handshake callers get their bounded network error.
                finally:
                    with owner.lock:
                        owner.sockets.discard(client)
                        owner.sockets.discard(channel)
                    for conn in (channel, client):
                        if conn is not None:
                            with contextlib.suppress(Exception): conn.close()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = False
            daemon_threads = True
            block_on_close = False
        self.server = Server(("127.0.0.1", 0), Handler)
        self.endpoint = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        with self.lock: sockets = list(self.sockets)
        for conn in sockets:
            with contextlib.suppress(Exception): conn.close()
        self.thread.join(timeout=2)


class MongoSession(DatabaseSession):
    """BSON-native standalone adapter; owner-thread work, separate cancel channel."""

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.connection = None
        self._control = None
        self._tunnel = self._proxy = self._pem_dir = None
        self._selected_schema = config.database or "admin"
        self._seal_key = secrets.token_bytes(32)
        self._generation = secrets.token_hex(16)
        self._active_lock = threading.Lock()
        self._active_comment = None
        self._cancel_requested = threading.Event()
        self._server_major = 0

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
        _tls_context(c)
        host = c.host or "localhost"
        if not isinstance(host, str) or any(char in host for char in "/@,?\0"):
            raise ValueError("MongoDB hostname must be a plain host, not a connection URI")
        raw_host = host.strip("[]")
        try:
            parsed_ip = ipaddress.ip_address(raw_host)
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
                    stream.write(Path(c.ssl_key).expanduser().read_bytes())
                    stream.write(b"\n")
                    stream.write(Path(c.ssl_cert).expanduser().read_bytes())
                kwargs.update(tlsCertificateKeyFile=str(pem), tlsCertificateKeyFilePassword=c.ssl_key_password or "")
        if c.ssh_enabled:
            self._tunnel = SSHTunnel(c, raw_host, port).start()
            self._proxy = _SocksProxy(self._tunnel, raw_host, port, c.connect_timeout)
            kwargs.update(proxyHost=self._proxy.endpoint[0], proxyPort=self._proxy.endpoint[1])
        return kwargs

    def connect(self):
        if self.connection is not None:
            return
        try:
            kwargs = self._connection_options()
            self.connection = MongoClient(**kwargs)
            self.connection.admin.command("ping")
            version = self.connection.server_info()["versionArray"]
            self._server_major = version[0]
            self._control = MongoClient(**kwargs)
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
        if self._proxy is not None:
            self._proxy.close(); self._proxy = None
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
    def _operation(self):
        self._check()
        self._cancel_requested.clear()
        comment = "OpenNavicat:" + secrets.token_hex(16)
        with self._active_lock: self._active_comment = comment
        try:
            yield comment
        except DatabaseError:
            raise
        except Exception as exc:
            raise self._error(exc) from exc
        finally:
            with self._active_lock:
                if self._active_comment == comment: self._active_comment = None

    def cancel(self):
        with self._active_lock: comment = self._active_comment
        if not comment or self._control is None:
            return
        self._cancel_requested.set()
        try:
            rows = self._control.admin.aggregate([{"$currentOp": {"allUsers": False}}, {"$match": {"command.comment": comment}}], maxTimeMS=3000)
            for row in rows:
                self._control.admin.command({"killOp": 1, "op": row["opid"]}, maxTimeMS=3000)
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
            result = []
            names = {s["name"] for s in specs}
            buckets = {n[:-6] for n in names if n.endswith(".files") and n[:-6] + ".chunks" in names}
            for spec in specs:
                name = spec["name"]
                kind = "mongo_view" if spec["type"] == "view" else "mongo_timeseries" if spec["type"] == "timeseries" else "mongo_collection"
                result.append(dict(name=name, schema=database, kind=kind, options=spec.get("options", {}),
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

    def execute(self, command, limit=1000):
        raise DatabaseError("MongoDB uses BSON document APIs. Native command and full JavaScript execution have not completed adapter acceptance")

    def _unsupported_relational(self, *args, **kwargs):
        raise DatabaseError("This MongoDB session uses document APIs, not SQL table operations")

    columns = foreign_keys = fetch_table = update_row = delete_row = import_rows = backup_to = _unsupported_relational

    def indexes(self, database, collection):
        self._validate_namespace(database, collection)
        with self._operation() as comment:
            rows = list(self.connection[database][collection].list_indexes(comment=comment))
            return [{"name": row["name"], "columns": list(row["key"]), "keys": list(row["key"].items()),
                     "unique": bool(row.get("unique", row["name"] == "_id_")), "primary": row["name"] == "_id_",
                     "options": dict(row), "definition": canonical_dumps(dict(row))} for row in rows]

    def collection_stats(self, database, collection):
        self._validate_namespace(database, collection)
        with self._operation() as comment:
            return self.connection[database].command({"collStats": collection, "comment": comment})

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
