"""Identity-checked MongoDB collection validation and index rebuild."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time

from bson import BSON, Binary
from pymongo import timeout
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .js_engine.server_errors import server_failure
from .mongodb_collections import CollectionSnapshot, _catalog
from .mongodb_session import BSON_CODEC, MongoConflictError, canonical_dumps
from .mongodb_timeseries import TimeSeriesSnapshot


_MAX_REPORT_BYTES = 1024 * 1024


@dataclass
class CollectionMaintenanceResult:
    operation: str
    source_uuid: object
    report: dict
    duration_ms: float
    document_count: int | None = None
    index_count: int | None = None


def _decode(raw):
    return BSON(raw).decode(codec_options=BSON_CODEC)


def _identity(session, snapshot):
    raw = _catalog(session, snapshot.schema, snapshot.name)
    if not raw:
        raise MongoConflictError('The collection no longer exists')
    if isinstance(snapshot, (CollectionSnapshot, TimeSeriesSnapshot)) and snapshot.exists and raw != snapshot.raw_spec:
        raise MongoConflictError('The collection UUID or options changed after loading')
    spec = _decode(raw)
    if spec.get('type') == 'timeseries':
        bucket_raw = _catalog(session, snapshot.schema, 'system.buckets.' + snapshot.name)
        if not bucket_raw:
            raise MongoConflictError('The time-series buckets collection no longer exists')
        namespace_uuid = (_decode(bucket_raw).get('info') or {}).get('uuid')
    else:
        namespace_uuid = (spec.get('info') or {}).get('uuid')
    if not isinstance(namespace_uuid, Binary) or namespace_uuid.subtype != 4 or len(namespace_uuid) != 16:
        raise MongoConflictError('A stable collection UUID is required for maintenance')
    return namespace_uuid, spec


def _checked_command(session, schema, command, seconds):
    try:
        with timeout(seconds):
            reply = session.connection[schema].command(command)
    except Exception as error:
        failure = server_failure(error)
        if failure is not None:
            raise DatabaseError('MongoDB rejected collection maintenance (code ' + str(failure.document.get('code', 'unknown')) + ')') from error
        raise
    return deepcopy(dict(reply))


def _bounded_report(reply):
    try:
        text = canonical_dumps(reply)
    except Exception:
        raise DatabaseError('The maintenance report cannot be safely represented as Canonical Extended JSON')
    if len(text.encode('utf-8')) > _MAX_REPORT_BYTES:
        raise DatabaseError('The maintenance report exceeded the 1 MiB delivery bound')
    return reply


def validate_collection(session, snapshot, *, full=False, cancelled=None):
    if not isinstance(snapshot, (CollectionSnapshot, TimeSeriesSnapshot)) or not snapshot.exists:
        raise DatabaseError('An existing collection snapshot is required for validation')
    if cancelled and cancelled():
        raise DatabaseError('Collection validation cancelled before database access')
    started = time.monotonic()
    with session._operation() as comment:
        before_uuid, _spec = _identity(session, snapshot)
        command = {'validate': snapshot.name, 'full': bool(full), 'comment': comment}
        if full:
            command['checkBSONConformance'] = True
        reply = _checked_command(session, snapshot.schema, command, 3600 if full else 300)
        after_uuid, _spec = _identity(session, snapshot)
    if before_uuid != after_uuid:
        raise MongoConflictError('The collection UUID changed during validation; the report remains unbound')
    reply = _bounded_report(reply)
    if reply.get('valid') is not True:
        raise DatabaseError('MongoDB validate returned valid=false; inspect the retained report')
    return CollectionMaintenanceResult(
        'validate-full' if full else 'validate', before_uuid, reply,
        round((time.monotonic() - started) * 1000, 3),
        int(reply['nrecords']) if isinstance(reply.get('nrecords'), (int, float)) else None,
        int(reply['nIndexes']) if isinstance(reply.get('nIndexes'), (int, float)) else None,
    )


def reindex_collection(session, snapshot, *, cancelled=None):
    if not isinstance(snapshot, CollectionSnapshot) or not snapshot.exists:
        raise DatabaseError('An existing ordinary collection snapshot is required for reIndex')
    if snapshot.kind != 'mongo_collection' or snapshot.collection_type != 'collection' or snapshot.read_only:
        raise DatabaseError('MongoDB reIndex is supported only for a fully editable ordinary collection')
    if session.config.read_only:
        raise DatabaseError('This connection is read-only')
    if cancelled and cancelled():
        raise DatabaseError('Index rebuild cancelled before database access')
    started = time.monotonic()
    with session._operation() as comment:
        before_uuid, _spec = _identity(session, snapshot)
        before_indexes = sorted(str(row.get('name')) for row in session.connection[snapshot.schema][snapshot.name].list_indexes())
        reply = _checked_command(session, snapshot.schema, {'reIndex': snapshot.name, 'comment': comment}, 3600)
        after_uuid, _spec = _identity(session, snapshot)
        after_indexes = sorted(str(row.get('name')) for row in session.connection[snapshot.schema][snapshot.name].list_indexes())
    if before_uuid != after_uuid:
        raise MongoConflictError('The collection UUID changed during index rebuild')
    if before_indexes != after_indexes:
        raise MongoConflictError('The index name set changed during rebuild; inspect the server report')
    reply = _bounded_report(reply)
    return CollectionMaintenanceResult(
        'reindex', before_uuid, reply,
        round((time.monotonic() - started) * 1000, 3),
        None,
        int(reply['nIndexes']) if isinstance(reply.get('nIndexes'), (int, float)) else len(after_indexes),
    )
