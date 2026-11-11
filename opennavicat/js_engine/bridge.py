"""Parent-only MongoDB authority for the experimental native JS process.

The child never receives a client/config object. Every requested command crosses
this validator again, even when it did not originate from our JS shell helpers.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
import time
import math

from bson import BSON, Int64
from bson.raw_bson import RawBSONDocument
from pymongo import timeout
from pymongo.command_cursor import CommandCursor

from ..database import DatabaseError
from ..mongodb_query import _READ_COMMANDS, _bounded_command, _validate_command
from ..mongodb_session import BSON_CODEC, RAW_CODEC
from .codec import decode, encode
from .records import CommandReceipt
from .server_errors import server_failure




class MongoDBBridge:
    def __init__(self, session, database, *, deadline, cancelled, max_rows=1000, max_bson_bytes=256*1024):
        session._validate_namespace(database)
        self.session, self.database = session, database
        self.deadline, self.cancelled = deadline, cancelled
        self.max_rows, self.max_bson_bytes = max_rows, max_bson_bytes
        self.receipts = []
        self.open_cursors = 0
        self.open_sessions = 0

    def check(self):
        if self.cancelled() or time.monotonic() >= self.deadline:
            raise DatabaseError("JavaScript execution cancelled or timed out")
        self.session._check()

    @staticmethod
    def _contains_write(value):
        if isinstance(value, Mapping):
            return any(key in {"$out", "$merge"} or MongoDBBridge._contains_write(child) for key, child in value.items())
        return isinstance(value, list) and any(MongoDBBridge._contains_write(child) for child in value)

    @staticmethod
    def _lossless(document):
        raw = bytes(document.raw) if isinstance(document, RawBSONDocument) else BSON.encode(document, codec_options=BSON_CODEC)
        value = BSON(raw).decode(codec_options=BSON_CODEC)
        if BSON.encode(value, codec_options=BSON_CODEC) != raw or BSON.encode(decode(encode(value)), codec_options=BSON_CODEC) != raw:
            raise DatabaseError("This raw BSON representation cannot cross the JavaScript bridge exactly")
        return value, len(raw)

    def command(self, tree):
        self.check()
        original = decode(tree)
        if not isinstance(original, dict):
            raise DatabaseError("A BSON command document is required")
        verb, writes, dispatched = "unknown", False, False
        sequence = len(self.receipts) + 1
        try:
            # Command-control values have integer semantics, unlike JS Number
            # values inside user documents/filters/pipelines. Coerce only this
            # explicit protocol context; never recursively rewrite data fields.
            def integer(value):
                if isinstance(value,float)and math.isfinite(value)and value.is_integer()and abs(value)<=2**53-1:
                    return int(value)
                return value
            requested_verb=next(iter(original),'').lower()
            controls=['maxTimeMS']
            if requested_verb in {'find','count'}:controls.extend(('limit','skip'))
            if requested_verb=='find':controls.append('batchSize')
            for name in controls:
                if name in original:original[name]=integer(original[name])
            if requested_verb in {'aggregate','listcollections','listindexes'}and isinstance(original.get('cursor'),dict):
                if 'batchSize'in original['cursor']:original['cursor']['batchSize']=integer(original['cursor']['batchSize'])
            if isinstance(original.get('writeConcern'),dict):
                for name in ('w','wtimeout','wtimeoutMS'):
                    if name in original['writeConcern']:original['writeConcern'][name]=integer(original['writeConcern'][name])
            verb = _validate_command(self.session, original)
            writes = verb not in _READ_COMMANDS or verb == "aggregate" and self._contains_write(original.get("pipeline"))
            # Copy/encode before any I/O; no unsupported value can be dropped.
            raw = BSON.encode(original, codec_options=BSON_CODEC)
            if len(raw) > self.max_bson_bytes:
                raise DatabaseError("The JavaScript command exceeds its BSON byte limit")
            command = _bounded_command(original, verb, self.max_rows)
            supports_sessions = self.session.connection.topology_description.logical_session_timeout_minutes is not None
            logical = self.session.connection.start_session() if supports_sessions else nullcontext(None)
            operation = self.session._operation(user_comment=original["comment"]) if "comment" in original else self.session._operation()
            with logical as wire_session, timeout(max(.001, self.deadline-time.monotonic())), operation as comment:
                self.open_sessions += bool(wire_session)
                try:
                    self.check()
                    command["comment"] = comment
                    target = self.session.connection.get_database(self.database, codec_options=RAW_CODEC)
                    dispatched = True
                    reply = target.command(command, session=wire_session, codec_options=RAW_CODEC)
                    cursor = reply.get("cursor")
                    if isinstance(cursor, Mapping) and isinstance(cursor.get("id"), int) and "firstBatch" in cursor:
                        namespace = cursor.get("ns", "")
                        db, separator, name = namespace.partition(".")
                        if not separator or db != self.database:
                            raise DatabaseError("MongoDB returned a cursor outside the selected database")
                        self.session._validate_namespace(db, name)
                        collection = target.get_collection(name)
                        batch = command.get("batchSize", command.get("cursor", {}).get("batchSize", 100))
                        native = CommandCursor(collection, cursor, self.session.connection.address,
                            batch_size=batch, session=wire_session, comment=comment)
                        self.open_cursors += 1
                        documents, size = [], 0
                        try:
                            for document in native:
                                self.check()
                                value, length = self._lossless(document)
                                if len(documents) >= self.max_rows or size+length > self.max_bson_bytes:
                                    raise DatabaseError("JavaScript cursor result exceeds its delivery limit; add a narrower filter or explicit limit")
                                documents.append(value); size += length
                        finally:
                            try:
                                with timeout(3):
                                    native.close()
                            finally:
                                self.open_cursors -= 1
                        # This is an explicitly eager managed command cursor.
                        # It is exhausted/closed on the wire before returning;
                        # no raw live cursor ID or logical session reaches JS.
                        output = {"ok": reply.get("ok", 1), "cursor": {"id": Int64(0), "ns": namespace, "firstBatch": documents}}
                    else:
                        output, _ = self._lossless(reply)
                    affected = int(reply["n"]) if "n" in reply else None
                    uncertain = bool(reply.get("writeConcernError"))
                    failed = bool(reply.get("writeErrors"))
                    receipt = CommandReceipt(sequence, verb, writes,
                        "uncertain" if uncertain else "acknowledged_error" if failed else "acknowledged", affected,
                        "Write acknowledgement is uncertain" if uncertain else "Server reported write errors" if failed else "", deepcopy(output))
                    self.receipts.append(receipt)
                    self.session._checked_write_result(reply)
                    self.check()
                    return encode(output)
                finally:
                    self.open_sessions -= bool(wire_session)
        except Exception as error:
            if not self.receipts or self.receipts[-1].sequence != sequence:
                failure=server_failure(error) if dispatched else None
                affected=failure.document.get('n') if failure is not None else None
                if type(affected)is not int:affected=None
                self.receipts.append(CommandReceipt(sequence, verb, writes,
                    'uncertain' if failure is not None and writes and failure.concern_uncertain else 'acknowledged_error' if failure is not None else "uncertain" if dispatched and writes else "rejected",
                    affected=affected,error=(failure.code_name or 'MongoServerError')+f' ({failure.code})' if failure is not None else type(error).__name__,
                    reply=failure.document if failure is not None else None))
            raise
