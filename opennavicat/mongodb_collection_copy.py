"""Safe-ish staged ordinary MongoDB collection copy without target overwrite."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import uuid

from bson import BSON, Binary
from pymongo import timeout
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .mongodb_collections import (
    CollectionApplyError, CollectionOutcome, CollectionSnapshot, _catalog, _command_text,
)
from .js_engine.server_errors import server_failure
from .mongodb_index_script import protected_command_script
from .mongodb_session import BSON_CODEC, MongoConflictError


_MAX_INDEX_BYTES = 1024 * 1024
_MAX_INDEXES = 64


@dataclass
class CollectionCopyPlan:
    snapshot: CollectionSnapshot
    target_name: str
    mode: str
    staging_name: str
    indexes: list[dict] = field(default_factory=list, repr=False)
    preview_commands: list[dict] = field(default_factory=list, repr=False)
    warnings: list[str] = field(default_factory=list)
    token: bytes = field(default=b'', repr=False)
    kind: str = 'mongo_collection_copy'

    @property
    def command_text(self):
        values = []
        for command in self.preview_commands:
            if 'renameCollection' in command:
                values.append(json.dumps(command, default=str, indent=2) + '\n// collectionUUID is resolved from the staging collection at execution')
            else:
                values.append(_command_text(command))
        return '\n\n'.join(values)

    @property
    def command_script(self):
        lines = ['// Staged copy; no target overwrite and no automatic staging cleanup']
        for command in self.preview_commands:
            if 'renameCollection' in command:
                command = deepcopy(command)
                command['collectionUUID'] = '<resolved staging UUID>'
            lines.append(protected_command_script(self.snapshot.schema, [command]))
        return '\n\n'.join(lines)


@dataclass
class CollectionCopyResult:
    snapshot: CollectionSnapshot
    outcome: CollectionOutcome


def _encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def _snapshot_payload(snapshot):
    return _encoded({
        'schema': snapshot.schema, 'name': snapshot.name,
        'definition': {'name': snapshot.definition.name, 'options': snapshot.definition.options},
        'raw': Binary(snapshot.raw_spec), 'uuid': snapshot.namespace_uuid,
        'exists': snapshot.exists, 'read_only': snapshot.read_only,
        'issues': snapshot.issues, 'collection_type': snapshot.collection_type, 'kind': snapshot.kind,
    })


def _plan_payload(plan):
    return _encoded({
        'snapshot': _snapshot_payload(plan.snapshot), 'target': plan.target_name,
        'mode': plan.mode, 'staging': plan.staging_name, 'indexes': plan.indexes,
        'preview': plan.preview_commands, 'warnings': plan.warnings,
    })


def _sign(session, snapshot, purpose, payload):
    return session._token(snapshot.schema, snapshot.name, purpose + b'\0' + payload, False, snapshot.namespace_uuid)


def _seal(session, plan):
    plan.token = _sign(session, plan.snapshot, b'collection-copy-plan', _plan_payload(plan))
    return plan


def _verify(session, plan):
    try:
        if not isinstance(plan, CollectionCopyPlan):
            raise ValueError()
        expected = _sign(session, plan.snapshot, b'collection-copy-plan', _plan_payload(plan))
        if not hmac.compare_digest(plan.token, expected):
            raise ValueError()
    except Exception:
        raise DatabaseError('The collection copy plan is stale, forged or belongs to another session') from None


def _current_source(session, snapshot):
    raw = _catalog(session, snapshot.schema, snapshot.name)
    if raw != snapshot.raw_spec:
        raise MongoConflictError('The source collection UUID or options changed; refresh before copying')


def _uuid_from_raw(raw, name):
    spec = BSON(raw).decode(codec_options=BSON_CODEC)
    namespace_uuid = (spec.get('info') or {}).get('uuid')
    if not isinstance(namespace_uuid, Binary) or namespace_uuid.subtype != 4 or len(namespace_uuid) != 16:
        raise MongoConflictError('A stable collection UUID is required for the staged copy')
    return namespace_uuid


def _index_specs(session, schema, name):
    values = []
    total = 0
    for spec in session.connection[schema][name].list_indexes():
        value = BSON(BSON.encode(dict(spec), codec_options=BSON_CODEC)).decode(codec_options=BSON_CODEC)
        if value.get('name') == '_id_':
            continue
        value.pop('ns', None)
        value.pop('v', None)
        raw = _encoded(value)
        total += len(raw)
        if len(values) >= _MAX_INDEXES or total > _MAX_INDEX_BYTES:
            raise DatabaseError('The source has too many or oversized indexes for a bounded copy preview')
        values.append(value)
    return values


def plan_copy(session, snapshot, target_name, mode):
    snapshot = deepcopy(snapshot)
    if mode not in {'data', 'structure'}:
        raise DatabaseError('Copy mode must be data or structure')
    if not snapshot.exists or snapshot.kind != 'mongo_collection' or snapshot.collection_type != 'collection' or snapshot.read_only:
        raise DatabaseError('Only a fully editable ordinary collection can be copied here')
    if session.config.read_only:
        raise DatabaseError('This connection is read-only')
    if not isinstance(target_name, str) or not target_name or target_name == snapshot.name or '\0' in target_name or '$' in target_name or target_name.startswith('system.') or target_name.endswith(('.files', '.chunks')):
        raise DatabaseError('Enter a different nonempty ordinary target collection name')
    session._validate_namespace(snapshot.schema, target_name)
    _current_source(session, snapshot)
    if _catalog(session, snapshot.schema, target_name):
        raise MongoConflictError('The target collection name already exists; nothing was copied')
    staging = '__opennavicat_copy_' + uuid.uuid4().hex
    if _catalog(session, snapshot.schema, staging):
        raise MongoConflictError('A staging collection name collision occurred; retry the copy')
    indexes = _index_specs(session, snapshot.schema, snapshot.name)
    create = {'create': staging, **deepcopy(snapshot.definition.options), 'writeConcern': {'w': 1}}
    preview = [create]
    if indexes:
        preview.append({'createIndexes': staging, 'indexes': deepcopy(indexes), 'writeConcern': {'w': 1}})
    if mode == 'data':
        preview.append({'aggregate': snapshot.name,
            'pipeline': [{'$match': {}}, {'$out': staging}], 'cursor': {}})
    preview.append({'renameCollection': snapshot.schema + '.' + staging,
        'to': snapshot.schema + '.' + target_name, 'dropTarget': False,
        'collectionUUID': '<resolved staging UUID>', 'writeConcern': {'w': 1}})
    warnings = [
        'Copy uses a random staging collection and the final rename has dropTarget=false, so an existing target is never intentionally overwritten.',
        'The source can change while data is copied. This is not a point-in-time snapshot and not a transaction.',
        'If a step fails after staging creation, the staging collection may remain. It is not automatically dropped.',
    ]
    return _seal(session, CollectionCopyPlan(snapshot, target_name, mode, staging, indexes, preview, warnings))


def _reply_ok(reply):
    if reply.get('writeConcernError') or reply.get('writeErrors'):
        raise DatabaseError('The server did not acknowledge the copy step completely')


def apply_copy(session, plan, *, cancelled=None):
    plan = deepcopy(plan)
    _verify(session, plan)
    outcome = CollectionOutcome('copy')
    def stopped():
        return bool(cancelled and cancelled()) or session._cancel_requested.is_set()
    if cancelled and cancelled():
        outcome.cancelled = True
        raise CollectionApplyError('Collection copy cancelled before database access', outcome)
    staging_uuid = None
    def run(command, database, seconds=120):
        outcome.status = 'unknown'
        try:
            with timeout(seconds):
                reply = database.command(command)
            outcome.reply = deepcopy(dict(reply))
            _reply_ok(reply)
            outcome.status = 'acknowledged'
            return reply
        except Exception as error:
            failure = server_failure(error)
            if failure is not None:
                outcome.reply = failure.document
                if not failure.concern_uncertain:
                    outcome.status = 'server_error'
            raise
    try:
        with session._operation() as comment:
            _current_source(session, plan.snapshot)
            if _catalog(session, plan.snapshot.schema, plan.target_name):
                raise MongoConflictError('The target collection appeared before copy; no target was modified')
            if _catalog(session, plan.snapshot.schema, plan.staging_name):
                raise MongoConflictError('The staging collection appeared before copy; no target was modified')
            create = {'create': plan.staging_name, **deepcopy(plan.snapshot.definition.options),
                'writeConcern': {'w': 1}, 'comment': comment}
            run(create, session.connection[plan.snapshot.schema])
            outcome.staging_name = plan.staging_name
            staging_raw = _catalog(session, plan.snapshot.schema, plan.staging_name)
            if not staging_raw: raise DatabaseError('The staging collection was not observable after create')
            staging_uuid = _uuid_from_raw(staging_raw, plan.staging_name)
            if plan.indexes:
                if stopped():
                    outcome.cancelled = True
                    raise CollectionApplyError('Collection copy stopped before creating staging indexes', outcome)
                command = {'createIndexes': plan.staging_name, 'indexes': deepcopy(plan.indexes),
                    'writeConcern': {'w': 1}, 'comment': comment}
                run(command, session.connection[plan.snapshot.schema])
            if plan.mode == 'data':
                if stopped():
                    outcome.cancelled = True
                    raise CollectionApplyError('Collection copy stopped before data copy', outcome)
                command = {'aggregate': plan.snapshot.name,
                    'pipeline': [{'$match': {}}, {'$out': plan.staging_name}],
                    'cursor': {}, 'comment': comment}
                run(command, session.connection[plan.snapshot.schema], seconds=3600)
                staging_raw = _catalog(session, plan.snapshot.schema, plan.staging_name)
                if not staging_raw: raise DatabaseError('The staging collection was not observable after data copy')
                staging_uuid = _uuid_from_raw(staging_raw, plan.staging_name)
            if _catalog(session, plan.snapshot.schema, plan.target_name):
                raise MongoConflictError('The target collection appeared before final rename; staging was retained')
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('Collection copy stopped before final rename; staging was retained', outcome)
            rename = {'renameCollection': plan.snapshot.schema + '.' + plan.staging_name,
                'to': plan.snapshot.schema + '.' + plan.target_name, 'dropTarget': False,
                'collectionUUID': staging_uuid, 'writeConcern': {'w': 1}, 'comment': comment}
            run(rename, session.connection.admin)
            target_raw = _catalog(session, plan.snapshot.schema, plan.target_name)
            if not target_raw: raise DatabaseError('The target collection was not observable after rename')
            from .mongodb_collections import load_collection
            target = load_collection(session, plan.snapshot.schema, plan.target_name)
            if target.namespace_uuid != staging_uuid:
                raise DatabaseError('The final target UUID does not match the staged collection')
            outcome.observed = target
            outcome.staging_name = None
            outcome.copied_documents = session.connection[plan.snapshot.schema][plan.target_name].estimated_document_count()
            return CollectionCopyResult(target, outcome)
    except Exception as error:
        outcome.cancelled = outcome.cancelled or stopped()
        if staging_uuid is not None and outcome.staging_name:
            try:
                outcome.observed = __import__('opennavicat.mongodb_collections', fromlist=['load_collection']).load_collection(
                    session, plan.snapshot.schema, plan.staging_name)
            except Exception as observation_error:
                outcome.observation_error = type(observation_error).__name__
        if isinstance(error, CollectionApplyError): raise
        detail = f'; staging {plan.snapshot.schema}.{plan.staging_name} may remain' if outcome.staging_name else ''
        raise CollectionApplyError('Collection copy failed; no target overwrite was attempted' + detail, outcome) from error
