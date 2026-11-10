"""Execute MongoDB's BSON commands and the explicitly supported shell grammar.

All code runs on the session's serial owner. JavaScript is never evaluated on
the host. Returned rows contain typed Canonical EJSON or an explicit raw BSON
envelope; generic SQL renderers never convert BSON values with default=str.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from contextlib import nullcontext
from copy import deepcopy
import json
import time

from bson import BSON
from bson.raw_bson import RawBSONDocument
from pymongo import timeout
from pymongo.command_cursor import CommandCursor

from .database import DatabaseError, QueryResult
from .mongodb_session import BSON_CODEC, RAW_CODEC, _MAX_PAGE_BYTES, canonical_dumps, canonical_loads, parse_mongo_queries


_READ_COMMANDS = frozenset({"find", "aggregate", "count", "distinct", "ping", "hello", "ismaster",
    "buildinfo", "serverstatus", "dbstats", "collstats", "listcollections", "listindexes",
    "connectionstatus", "usersinfo", "rolesinfo", "getparameter", "getlog", "explain"})
_STREAM_STAGES = frozenset({"$changeStream", "$changeStreamSplitLargeEvent"})


def _check_pipeline_streams(pipeline):
    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise DatabaseError("Each aggregation stage must be a one-field document")
        operation, value = next(iter(stage.items()))
        if operation in _STREAM_STAGES:
            raise DatabaseError("Change streams require their dedicated streaming workspace")
        if operation == "$facet" and isinstance(value, dict):
            for child in value.values():
                _check_pipeline_streams(child)
        elif operation in {"$lookup", "$unionWith"} and isinstance(value, dict) and "pipeline" in value:
            _check_pipeline_streams(value["pipeline"])


def _validate_command(session, command):
    if not command or not isinstance(next(iter(command)), str):
        raise DatabaseError("A MongoDB command must have a command name")
    verb = next(iter(command)).lower()
    if verb in {"getmore", "killcursors", "saslstart", "saslcontinue", "authenticate", "logout"}:
        raise DatabaseError("Cursor and authentication state are managed by the MongoDB connection")
    if any(key in command for key in ("lsid", "txnNumber", "autocommit", "startTransaction", "$db", "$clusterTime", "$readPreference")):
        raise DatabaseError("MongoDB routing, sessions and transactions are managed by the connection")
    if any(command.get(key) for key in ("tailable", "awaitData", "exhaust", "noCursorTimeout")):
        raise DatabaseError("Streaming cursors require their dedicated workspace")
    if "writeConcern" in command:
        concern = command["writeConcern"]
        if not isinstance(concern, dict):
            raise DatabaseError("MongoDB writeConcern must be a document")
        acknowledgement = concern.get("w", 1)
        if (isinstance(acknowledgement, bool)
                or not isinstance(acknowledgement, (int, str))
                or isinstance(acknowledgement, int) and acknowledgement < 1
                or isinstance(acknowledgement, str) and not acknowledgement):
            raise DatabaseError("MongoDB query commands require acknowledged writeConcern (w >= 1 or a named mode)")
    if verb == "aggregate":
        pipeline = command.get("pipeline")
        if not isinstance(pipeline, list):
            raise DatabaseError("An aggregation pipeline must be an array")
        _check_pipeline_streams(pipeline)
    if session.config.read_only:
        if verb not in _READ_COMMANDS:
            raise DatabaseError("This MongoDB command has not been accepted for read-only connections")
        if verb == "aggregate":
            session._read_pipeline(command["pipeline"])
    if verb == "explain":
        nested = command[next(iter(command))]
        if not isinstance(nested, dict):
            raise DatabaseError("MongoDB explain must contain a BSON command")
        _validate_command(session, nested)
    return verb


def _render_document(document):
    raw = bytes(document.raw) if isinstance(document, RawBSONDocument) else BSON.encode(dict(document), codec_options=BSON_CODEC)
    try:
        value = BSON(raw).decode(codec_options=BSON_CODEC)
        text = canonical_dumps(value)
        if BSON.encode(canonical_loads(text), codec_options=BSON_CODEC) == raw and len(text.encode("utf-8")) <= 4 * _MAX_PAGE_BYTES:
            return text, len(raw)
    except Exception:
        pass
    # Duplicate fields, legacy BSON or unusual NaN payloads cannot be silently
    # normalized. This clearly named envelope retains their exact raw bytes.
    return json.dumps({"$opennavicatRawBSON": {"base64": base64.b64encode(raw).decode("ascii"),
        "reason": "This document cannot roundtrip exactly through Canonical Extended JSON"}}, ensure_ascii=True), len(raw)


def _bounded_command(command, verb, limit):
    bounded = deepcopy(command)
    def batch_size(value, maximum):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DatabaseError("The MongoDB batch size must be a nonnegative integer")
        return min(value, maximum)
    if verb == "find":
        requested = command.get("limit", 0)
        if not isinstance(requested, int) or isinstance(requested, bool) or requested < 0:
            raise DatabaseError("The MongoDB find command limit must be a nonnegative integer")
        if not requested or requested > limit:
            bounded["limit"] = limit + 1
        maximum = limit + 1 if command.get("singleBatch") else min(100, limit + 1)
        bounded["batchSize"] = batch_size(command.get("batchSize", maximum), maximum)
    elif verb == "aggregate":
        terminal_write = bool(bounded["pipeline"] and any(key in bounded["pipeline"][-1] for key in ("$out", "$merge")))
        if not terminal_write:
            bounded["pipeline"] = [*bounded["pipeline"], {"$limit": limit + 1}]
        cursor = bounded.get("cursor", {})
        if not isinstance(cursor, dict):
            raise DatabaseError("The MongoDB aggregation cursor must be a document")
        bounded["cursor"] = {**cursor, "batchSize": batch_size(cursor.get("batchSize", min(100, limit + 1)), min(100, limit + 1))}
    elif verb in {"listcollections", "listindexes"}:
        cursor = bounded.get("cursor", {})
        if not isinstance(cursor, dict):
            raise DatabaseError("The MongoDB cursor options must be a document")
        bounded["cursor"] = {**cursor, "batchSize": batch_size(cursor.get("batchSize", min(100, limit + 1)), min(100, limit + 1))}
    return bounded


def execute_queries(session, text, limit=1000):
    """Bound delivered rows/raw BSON, not arbitrary server work or driver RAM."""
    # Include parsing and validation in the script's cancellation lifetime.
    # Individual operations keep their own typed comments, while this outer
    # operation prevents a request being cleared before the first/next command.
    with session._operation():
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000:
            raise DatabaseError("The MongoDB result limit must be between 1 and 100000")
        commands = parse_mongo_queries(text)
        # Reject a later unsupported/readonly command before an earlier write runs.
        prepared = []
        for command in commands:
            verb = _validate_command(session, command)
            prepared.append((command, verb, _bounded_command(command, verb, limit)))
        return _execute_prepared(session, prepared, limit)


def _execute_prepared(session, prepared, limit):
    completed = []
    for original, verb, command in prepared:
        started = time.perf_counter()
        rows, raw_bytes, truncated = [], 0, False
        result = QueryResult(columns=["BSON / Extended JSON"])
        try:
            result.sql = canonical_dumps(original)
            database = session._selected_schema or session.config.database or "admin"
            session._validate_namespace(database)
            session._check()
            if session._cancel_requested.is_set():
                raise DatabaseError("MongoDB query cancelled")
            # Keep getMore bound to the command's logical session and serving
            # address. The official CommandCursor path handles CSOT correctly;
            # Database.command(getMore) incorrectly injects server maxTimeMS.
            supports_sessions = session.connection.topology_description.logical_session_timeout_minutes is not None
            logical = session.connection.start_session() if supports_sessions else nullcontext(None)
            operation = session._operation(user_comment=original["comment"]) if "comment" in original else session._operation()
            with logical as wire_session, timeout(120), operation as comment:
                if session._cancel_requested.is_set():
                    raise DatabaseError("MongoDB query cancelled")
                command["comment"] = comment
                target = session.connection.get_database(database, codec_options=RAW_CODEC)
                reply = target.command(command, session=wire_session, codec_options=RAW_CODEC)
                cursor = reply.get("cursor")
                if isinstance(cursor, Mapping) and isinstance(cursor.get("id"), int) and "firstBatch" in cursor:
                    namespace = cursor.get("ns", "")
                    if not isinstance(namespace, str) or "." not in namespace:
                        raise DatabaseError("MongoDB returned an invalid cursor namespace")
                    cursor_database, _, cursor_collection = namespace.partition(".")
                    session._validate_namespace(cursor_database, cursor_collection)
                    collection = session.connection.get_database(cursor_database, codec_options=RAW_CODEC).get_collection(cursor_collection)
                    batch_size = command.get("batchSize", command.get("cursor", {}).get("batchSize", min(100, limit + 1)))
                    native_cursor = CommandCursor(collection, cursor, session.connection.address,
                        batch_size=batch_size, session=wire_session, comment=comment)
                    try:
                        for document in native_cursor:
                            if session._cancel_requested.is_set():
                                raise DatabaseError("MongoDB query cancelled")
                            if len(rows) >= limit or (rows and isinstance(document, RawBSONDocument) and raw_bytes + len(document.raw) > _MAX_PAGE_BYTES):
                                truncated = True
                                break
                            rendered, size = _render_document(document)
                            if rows and raw_bytes + size > _MAX_PAGE_BYTES:
                                truncated = True
                                break
                            rows.append([rendered]); raw_bytes += size
                    finally:
                        with timeout(3):
                            native_cursor.close()
                else:
                    rendered, _size = _render_document(reply)
                    rows.append([rendered])
                    result.affected = int(reply.get("n", 0))
                    session._checked_write_result(reply)
            result.rows = rows
            result.affected = len(rows) if verb in {"find", "aggregate", "listcollections", "listindexes"} else int(reply.get("n", 0))
            result.truncated = truncated
            result.elapsed_ms = (time.perf_counter() - started) * 1000
            completed.append(result)
        except Exception as error:
            if rows:
                result.rows, result.truncated, result.partial = rows, truncated, True
                if verb in {"find", "aggregate", "listcollections", "listindexes"}:
                    result.affected = len(rows)
                result.elapsed_ms = (time.perf_counter() - started) * 1000
                completed.append(result)
            if isinstance(error, DatabaseError):
                raise DatabaseError(str(error), completed, authentication_required=error.authentication_required) from error
            wrapped = session._error(error)
            raise DatabaseError(str(wrapped), completed, authentication_required=wrapped.authentication_required) from error
    return completed
