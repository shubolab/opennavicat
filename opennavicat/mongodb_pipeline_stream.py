"""Finite, BSON-preserving change-stream execution; no background watch/resume."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
from collections.abc import Mapping
import re
import time
import unicodedata
from urllib.parse import quote, quote_plus

from bson import BSON, Int64
from pymongo import timeout
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .mongodb_session import BSON_CODEC, RAW_CODEC, _MAX_PAGE_BYTES, canonical_dumps, canonical_loads


MAX_STREAM_ROWS = 1000
MAX_STREAM_SECONDS = 120
MAX_AWAIT_MS = 1000
CLEANUP_SECONDS = 3
MAX_STREAM_SERVER_MESSAGE_CHARS = 2048


def is_bounded_stream_message(value):
    """Accept the observed single-line server-message contract, without coercion.

    Multiline error formatting remains unqualified. Controls, bidi format
    characters and malformed Unicode cannot create extra UI status lines.
    """
    return (type(value) is str and 0 < len(value) <= MAX_STREAM_SERVER_MESSAGE_CHARS
            and not any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} for char in value)
            and len(value.encode('utf-8')) <= 8192)


def _server_message(error, session):
    if (not isinstance(error, OperationFailure) or not isinstance(error.details, Mapping)
            or type(error.code) is not int or not 0 <= error.code < 2**31
            or type(error.details.get('ok')) not in (int, float) or error.details['ok'] != 0):
        return None
    value = error.details.get('errmsg')
    if not is_bounded_stream_message(value):
        return None
    # A driver exception's display text and detailed response may contain
    # credentials. Read only errmsg and remove complete URI tokens plus this
    # owner profile's exact secrets and their URL-encoded representations.
    patterns = [r'(?i:\b[a-z][a-z0-9+.-]*://[^\s<>\"\x27]+)']
    secret_patterns = {}
    for name in ('password', 'ssh_password', 'ssh_key_passphrase', 'ssl_key_password'):
        secret = getattr(session.config, name, None)
        if type(secret) is str and 0 < len(secret) <= MAX_STREAM_SERVER_MESSAGE_CHARS:
            secret_patterns[re.escape(secret)] = len(secret)
            try:
                encoded_values = {quote(secret, safe=''), quote_plus(secret, safe='')}
            except UnicodeError:
                encoded_values = set()
            for encoded in encoded_values:
                if encoded != secret:
                    secret_patterns['(?i:' + re.escape(encoded) + ')'] = len(encoded)
    patterns.extend(sorted(secret_patterns, key=secret_patterns.get, reverse=True))
    safe = re.sub('|'.join(patterns), '[redacted]', value)
    return safe if is_bounded_stream_message(safe) else None


@dataclass(frozen=True)
class StreamEvent:
    raw_bson: bytes = field(repr=False)
    canonical_json: str = field(repr=False)


@dataclass(frozen=True)
class FiniteStreamResult:
    """A finite window; undelivered counts cover the known obtained document.

    A row/byte/time bound can leave further cached or server events unknown.
    These fields are not a total pending-event count or a pagination promise.
    """
    events: tuple[StreamEvent, ...] = field(repr=False)
    completion: str
    elapsed_seconds: float
    undelivered_event_count: int = 0
    undelivered_raw_bytes: int = 0
    undelivered_rendered_bytes: int = 0

    @property
    def rows(self):
        return [event.canonical_json for event in self.events]

    @property
    def truncated(self):
        return self.completion in {"row_limit", "byte_limit", "deadline"}

    def __iter__(self):
        # Existing execute callers unpack (rows, truncated). UI acceptance of
        # stream event presentation remains separate from this backend result.
        yield self.rows
        yield self.truncated


class StreamExecutionError(DatabaseError):
    def __init__(self, message, result, *, authentication_required=False,
                 server_message=None, error_phase=None, script_location=None):
        super().__init__(message, authentication_required=authentication_required)
        self.partial_result = result
        is_server_error = isinstance(result, FiniteStreamResult) and result.completion == 'error' and not authentication_required
        self.server_message = server_message if is_server_error and is_bounded_stream_message(server_message) else None
        self.error_phase = error_phase if is_server_error and type(error_phase) is str and error_phase in {'aggregate', 'get_more'} else None
        self.script_location = script_location if (
            is_server_error and type(script_location) is tuple and len(script_location) == 2
            and all(type(value) is int and 1 <= value < 2**31 for value in script_location)) else None


def _marked_operations(control, marker):
    # Bounded metadata only. The nonce targets this operation, including an
    # idle cursor whose initial response was lost to a network timeout.
    with control.admin.aggregate([
        {"$currentOp": {"allUsers": False, "idleCursors": True}},
        {"$match": {"$or": [
            {"command.comment": marker}, {"cursor.originatingCommand.comment": marker}]}},
        {"$limit": 16},
    ], batchSize=16) as cursor:
        return tuple(cursor)


def _cleanup(session, cursor, cursor_ids, database, collection, marker):
    """Owner-thread cleanup of this nonce's cursor, with a separate deadline."""
    deadline = time.monotonic() + CLEANUP_SECONDS
    close_error = None
    try:
        with timeout(CLEANUP_SECONDS):
            if cursor is not None:
                cursor.close()
    except Exception as error:
        close_error = error
    control = session._control
    if control is None:
        raise DatabaseError("Stream cursor cleanup could not use its independent control connection")
    pending = {(database, collection, identity) for identity in cursor_ids if identity}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= .01:
            raise DatabaseError("Stream cursor cleanup could not be confirmed within its deadline") from close_error
        with timeout(remaining):
            for dbname, name, identity in pending:
                reply = control[dbname].command({"killCursors": name, "cursors": [Int64(identity)]})
                confirmed = (*reply.get("cursorsKilled", ()), *reply.get("cursorsNotFound", ()))
                if (identity not in confirmed or reply.get("cursorsAlive")
                        or reply.get("cursorsUnknown")):
                    raise DatabaseError("The server did not confirm stream cursor cleanup")
            pending.clear()
            operations = _marked_operations(control, marker)
            if not operations:
                return
            for operation in operations:
                if operation.get("active") and "opid" in operation:
                    control.admin.command({"killOp": 1, "op": operation["opid"]})
                identity = operation.get("cursor", {}).get("cursorId")
                namespace = operation.get("ns", "")
                if identity and "." in namespace:
                    dbname, name = namespace.split(".", 1)
                    pending.add((dbname, name, identity))
        if not pending:
            time.sleep(min(.01, max(0, deadline - time.monotonic())))


@contextmanager
def _stream_command_budget(session, options, deadline, cancel_event):
    from .mongodb_pipeline import _check_pipeline_dispatch, _pipeline_remaining_ms

    _check_pipeline_dispatch(session, deadline, cancel_event)
    _pipeline_remaining_ms(deadline)
    with session._finite_stream_budget(options["maxTimeMS"]) as (client, effective):
        _check_pipeline_dispatch(session, deadline, cancel_event)
        options["maxTimeMS"] = min(effective, _pipeline_remaining_ms(deadline))
        yield client


def execute_finite_stream(session, database, definition, *, limit=MAX_STREAM_ROWS,
                          await_ms=MAX_AWAIT_MS, timeout_seconds=MAX_STREAM_SECONDS,
                          max_bytes=_MAX_PAGE_BYTES, cancel_event=None):
    """Drain one raw cursor until its first empty batch or an explicit bound.

    maxTimeMS is the aggregate option. maxAwaitTimeMS separately bounds each
    getMore wait; neither is advertised as a preemptive CPU/wall-clock limit.
    No resume token is injected, saved or reused between invocations.
    """
    from .mongodb_pipeline import (
        WRITE_STAGES, _check_pipeline_dispatch, _pipeline_cancelled,
        pipeline_aggregate_options, pipeline_documents,
        validate_pipeline,
    )

    validate_pipeline(definition)
    for value, maximum, label in ((limit, MAX_STREAM_ROWS, "row limit"),
                                  (await_ms, MAX_AWAIT_MS, "await time"),
                                  (max_bytes, _MAX_PAGE_BYTES, "byte limit")):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise DatabaseError("Stream " + label + " is invalid")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= MAX_STREAM_SECONDS):
        raise DatabaseError("Stream elapsed-time budget is invalid")
    documents = pipeline_documents(definition)
    if not any("$changeStream" in stage for stage in documents):
        raise DatabaseError("A finite stream requires a $changeStream stage")
    if any(next(iter(stage)) in WRITE_STAGES for stage in documents):
        raise DatabaseError("A change-stream pipeline does not execute write stages")
    session._validate_namespace(database, definition.source)
    options = pipeline_aggregate_options(definition)
    user_comment = options.pop("comment", None)
    requested = options.get("maxTimeMS", 0)
    # A finite application ceiling also bounds an unspecified/unlimited initial
    # aggregate. A user's positive server limit is never increased.
    initial_ms = max(1, int(timeout_seconds * 1000))
    options["maxTimeMS"] = min(requested, initial_ms) if requested > 0 else initial_ms
    options["maxAwaitTimeMS"] = await_ms
    requested_batch = options.get("batchSize")
    options["batchSize"] = 0 if requested_batch == 0 else min(requested_batch or 100, limit, 100)
    started = time.monotonic()
    deadline = started + timeout_seconds
    events, raw_size, rendered_size = [], 0, 0
    undelivered_count, undelivered_raw, undelivered_rendered = 0, 0, 0
    completion, cursor, cursor_ids, dispatched, client = "empty_batch", None, set(), False, None
    phase = None

    def result():
        return FiniteStreamResult(tuple(events), completion, time.monotonic() - started,
                                  undelivered_count, undelivered_raw, undelivered_rendered)

    operation = session._operation(user_comment) if user_comment is not None else session._operation()
    with operation as marker:
        options["comment"] = marker
        try:
            with _stream_command_budget(session, options, deadline, cancel_event) as client:
                _check_pipeline_dispatch(session, deadline, cancel_event)
                dispatched = True
                phase = 'aggregate'
                cursor = client.get_database(database, codec_options=RAW_CODEC)[definition.source].aggregate(
                    documents, **options)
                phase = None
                cursor.batch_size(min(requested_batch or 100, limit, 100))
                while True:
                    if _pipeline_cancelled(session, cancel_event):
                        completion = "cancelled"
                        raise StreamExecutionError("Stream execution was cancelled", result())
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        completion = "deadline"
                        break
                    if len(events) >= limit:
                        completion = "row_limit"
                        break
                    if cursor.cursor_id:
                        cursor_ids.add(cursor.cursor_id)
                    # getMore keeps its own maxAwaitTimeMS field. CSOT here
                    # bounds network wait without rewriting that field.
                    with timeout(remaining):
                        phase = 'get_more'
                        document = cursor.try_next()
                        phase = None
                    if document is not None:
                        undelivered_count = 1
                        undelivered_raw = len(document.raw)
                    if _pipeline_cancelled(session, cancel_event):
                        completion = "cancelled"
                        raise StreamExecutionError("Stream execution was cancelled", result())
                    if document is None:
                        completion = "deadline" if time.monotonic() >= deadline else "empty_batch"
                        break
                    raw = bytes(document.raw)
                    value = BSON(raw).decode(codec_options=BSON_CODEC)
                    rendered = canonical_dumps(value)
                    if BSON.encode(canonical_loads(rendered), codec_options=BSON_CODEC) != raw:
                        completion = "error"
                        raise StreamExecutionError(
                            "A stream event cannot roundtrip as exact Canonical Extended JSON; it was not delivered", result())
                    text_size = len(rendered.encode("utf-8"))
                    undelivered_rendered = text_size
                    if len(raw) > max_bytes or text_size > max_bytes:
                        completion = "error"
                        raise StreamExecutionError("A stream event exceeds the bounded result size; one event was not delivered", result())
                    if raw_size + len(raw) > max_bytes or rendered_size + text_size > max_bytes:
                        completion = "byte_limit"
                        raise StreamExecutionError(
                            "The finite stream byte limit was reached; one obtained event was not delivered", result())
                    events.append(StreamEvent(raw, rendered))
                    undelivered_count, undelivered_raw, undelivered_rendered = 0, 0, 0
                    raw_size += len(raw)
                    rendered_size += text_size
        except Exception as error:
            if _pipeline_cancelled(session, cancel_event):
                completion = "cancelled"
            elif time.monotonic() >= deadline:
                completion = "deadline"
            elif isinstance(error, StreamExecutionError):
                completion = error.partial_result.completion
            elif completion != "deadline":
                completion = "error"
            if isinstance(error, StreamExecutionError):
                error.partial_result = result()
                raise
            # Driver errors can include a complete URI, authentication data or
            # server-echoed document values. Preserve their cause, not their text
            # in a DatabaseError that the UI will display directly.
            if completion == "cancelled":
                message = "Stream execution was cancelled"
            elif completion == "deadline":
                message = "The finite stream elapsed-time budget was reached"
            elif isinstance(error, OperationFailure) and type(error.code) is int and 0 <= error.code < 2**31:
                message = "Finite stream execution failed (server code " + str(error.code) + ")"
            else:
                message = "Finite stream execution failed"
            raise StreamExecutionError(message, result(), authentication_required=(
                isinstance(error, OperationFailure) and error.code == 18),
                server_message=_server_message(error, session) if completion == 'error' else None,
                error_phase=phase if isinstance(error, OperationFailure) else None) from error
        finally:
            try:
                if dispatched:
                    _cleanup(session, cursor, cursor_ids, database, definition.source, marker)
            except Exception as error:
                completion = "cleanup_failed"
                session._discard_finite_stream_client(client)
                raise StreamExecutionError("Stream cursor cleanup could not be confirmed", result()) from error
    return result()
