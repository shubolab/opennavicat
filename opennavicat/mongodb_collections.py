"""Sealed ordinary-collection drafts and single-command native MongoDB DDL.

No UI contract is inferred from MongoDB's command reference. UUID protects the
namespace, not an options compare-and-swap. Create success does not prove who
created an identically configured collection. No recovery path drops data.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import json
import uuid

from bson import BSON, Binary
from pymongo import timeout
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .mongodb_session import BSON_CODEC, RAW_CODEC, MongoConflictError, _validate_date_precision, canonical_dumps, canonical_loads
from .mongodb_index_script import protected_command_script
from .js_engine.server_errors import server_failure


_CREATE = frozenset({'capped', 'size', 'max', 'validator', 'validationLevel', 'validationAction', 'collation'})
_MUTABLE = frozenset({'validator', 'validationLevel', 'validationAction'})
_DEFAULT = {'validator': {}, 'validationLevel': 'strict', 'validationAction': 'error'}
_MAX_CATALOG_BYTES = 1024 * 1024


@dataclass(eq=False)
class CollectionDefinition:
    name: str = ''
    options: dict = field(default_factory=dict, repr=False)

    def __eq__(self, other):
        try:
            return isinstance(other, CollectionDefinition) and _encoded(_definition(self)) == _encoded(_definition(other))
        except Exception:
            return False


@dataclass
class CollectionSnapshot:
    schema: str
    name: str
    definition: CollectionDefinition
    raw_spec: bytes = field(default=b'', repr=False)
    namespace_uuid: object = field(default=None, repr=False)
    fingerprint: str = ''
    token: bytes = field(default=b'', repr=False)
    exists: bool = True
    read_only: bool = False
    issues: list[str] = field(default_factory=list)
    collection_type: str = 'collection'
    kind: str = 'mongo_collection'


@dataclass
class CollectionPlan:
    snapshot: CollectionSnapshot
    definition: CollectionDefinition
    commands: list[dict] = field(default_factory=list, repr=False)
    changed: bool = False
    warnings: list[str] = field(default_factory=list)
    token: bytes = field(default=b'', repr=False)

    @property
    def command_text(self):
        return '\n\n'.join(_command_text(command) for command in self.commands)

    @property
    def command_script(self):
        return protected_command_script(self.snapshot.schema, self.commands)


@dataclass
class CollectionOutcome:
    operation: str
    status: str = 'not_started'  # not_started / acknowledged / server_error / unknown / no_change
    reply: dict | None = field(default=None, repr=False)
    observed: CollectionSnapshot | None = field(default=None, repr=False)
    observation_error: str = ''
    cancelled: bool = False
    # MongoDB create may return success for an identical existing collection.
    # This remains unknown even after a successful absent->create->readback.
    created_by_this_plan: bool | None = None
    remaining_documents: int | None = None
    collection_absent: bool | None = None
    staging_name: str | None = None
    copied_documents: int | None = None


@dataclass
class CollectionApplyResult:
    snapshot: CollectionSnapshot
    outcome: CollectionOutcome


@dataclass
class CollectionOperationResult:
    snapshot: CollectionSnapshot | None
    outcome: CollectionOutcome


class CollectionApplyError(DatabaseError):
    def __init__(self, message, outcome):
        super().__init__(message)
        self.outcome = outcome


def _encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def _definition(value):
    return {'name': value.name, 'options': value.options}


def _command_text(command):
    raw = _encoded(command)
    try:
        text = canonical_dumps(command)
        if _encoded(canonical_loads(text)) == raw: return text
    except Exception:
        pass
    # Literal $date/$oid-style fields may collide with EJSON wrappers. The
    # envelope is deliberately not an executable command; retain exact BSON.
    return json.dumps({'format': 'opennavicat-bson-command-v1', 'bsonBase64': base64.b64encode(raw).decode('ascii'),
        'reason': 'This command cannot roundtrip exactly through Canonical Extended JSON'}, separators=(',', ':'))


def _snapshot_payload(value):
    return _encoded({'schema': value.schema, 'name': value.name, 'definition': _definition(value.definition),
        'raw': Binary(value.raw_spec), 'uuid': value.namespace_uuid, 'exists': value.exists,
        'read_only': value.read_only, 'issues': value.issues, 'collection_type': value.collection_type, 'kind': value.kind})


def _sign(session, snapshot, purpose, payload):
    return session._token(snapshot.schema, snapshot.name, purpose + b'\0' + payload, False, snapshot.namespace_uuid)


def _seal_snapshot(session, snapshot):
    raw = _snapshot_payload(snapshot)
    snapshot.fingerprint = hashlib.sha256(raw).hexdigest()
    snapshot.token = _sign(session, snapshot, b'collection-snapshot', raw)
    return snapshot


def _verify_snapshot(session, snapshot):
    try:
        if not isinstance(snapshot, CollectionSnapshot): raise ValueError()
        raw = _snapshot_payload(snapshot)
        if (snapshot.kind != 'mongo_collection' or snapshot.fingerprint != hashlib.sha256(raw).hexdigest()
                or not hmac.compare_digest(snapshot.token, _sign(session, snapshot, b'collection-snapshot', raw))):
            raise ValueError()
    except Exception:
        raise DatabaseError('The collection snapshot changed or belongs to another session/configuration') from None


def _plan_payload(plan):
    return _encoded({'snapshot': Binary(_snapshot_payload(plan.snapshot)), 'definition': _definition(plan.definition),
        'commands': plan.commands, 'changed': plan.changed, 'warnings': plan.warnings})


def _seal_plan(session, plan):
    plan.token = _sign(session, plan.snapshot, b'collection-plan', _plan_payload(plan))
    return plan


def _verify_plan(session, plan):
    try:
        _verify_snapshot(session, plan.snapshot)
        if not hmac.compare_digest(plan.token, _sign(session, plan.snapshot, b'collection-plan', _plan_payload(plan))):
            raise ValueError()
    except Exception:
        raise DatabaseError('The reviewed collection plan changed or belongs to another session/configuration') from None


def _catalog(session, schema, name):
    """Read only this exact catalog row; retain raw BSON and close its cursor."""
    session._validate_namespace(schema, name)
    with timeout(3), session._operation() as comment:
        database = session.connection.get_database(schema, codec_options=RAW_CODEC)
        # list_collections uses the driver's default command codec for its
        # initial batch. cursor_command has an explicit public raw codec and
        # keeps subsequent batches pinned to the actual serving connection.
        with database.cursor_command({'listCollections': 1, 'filter': {'name': name},
                'cursor': {'batchSize': 1}, 'comment': comment}, codec_options=RAW_CODEC, comment=comment) as cursor:
            rows = []
            for row in cursor:
                raw = bytes(row.raw)
                if len(raw) > _MAX_CATALOG_BYTES or rows:
                    raise DatabaseError('The collection catalog exceeds its bound or is ambiguous')
                rows.append(raw)
            return rows[0] if rows else b''


def _snapshot(session, schema, name, raw):
    spec = BSON(raw).decode(codec_options=BSON_CODEC) if raw else {}
    options = deepcopy(spec.get('options', {}))
    info = spec.get('info', {})
    namespace_uuid = info.get('uuid') if isinstance(info, dict) else None
    issues = []
    if not isinstance(options, dict):
        options = {}; issues.append('Collection options are not an editable BSON document.')
    if raw and _encoded(spec) != raw:
        issues.append('Raw catalog BSON cannot be decoded and re-encoded exactly; original bytes are retained.')
    if raw and (spec.get('name') != name or spec.get('type') != 'collection'
            or not isinstance(namespace_uuid, Binary) or namespace_uuid.subtype != 4 or len(namespace_uuid) != 16):
        issues.append('Only UUID-bearing ordinary collections can be edited in this checkpoint.')
    if set(options) - _CREATE:
        issues.append('Additional collection options have not completed designer qualification; original options are retained.')
    if name.startswith('system.') or name.endswith(('.files', '.chunks')) or schema in {'admin', 'config', 'local'}:
        issues.append('Internal namespaces and GridFS-managed collections cannot be modified here.')
    result = CollectionSnapshot(schema, name, CollectionDefinition(name, options), raw,
        namespace_uuid, exists=bool(raw), read_only=bool(issues) or session.config.read_only,
        issues=issues, collection_type=spec.get('type', 'collection'))
    return _seal_snapshot(session, result)


def new_collection(session, database, name=''):
    session._validate_namespace(database, name or None)
    session._check()
    # No catalog or network call is needed until a draft has a final name.
    return _snapshot(session, database, name, b'')


def load_collection(session, database, name):
    raw = _catalog(session, database, name)
    if not raw: raise MongoConflictError('The collection no longer exists')
    return _snapshot(session, database, name, raw)


def validate_definition(definition):
    if not isinstance(definition, CollectionDefinition):
        raise DatabaseError('An ordinary-collection definition is required')
    name, options = definition.name, definition.options
    if not isinstance(name, str) or not name or '\0' in name or '$' in name or name.startswith('system.') or name.endswith(('.files', '.chunks')):
        raise DatabaseError('Enter a nonempty, non-system ordinary-collection name')
    if not isinstance(options, dict) or set(options) - _CREATE:
        raise DatabaseError('Unsupported collection options cannot be edited lossily')
    if 'capped' in options and type(options['capped']) is not bool:
        raise DatabaseError('capped must be a boolean')
    for key in {'size', 'max'} & options.keys():
        if isinstance(options[key], bool) or not isinstance(options[key], int) or not 1 <= options[key] < 2**63:
            raise DatabaseError(key + ' must be a positive BSON integer')
        if not options.get('capped'): raise DatabaseError(key + ' requires capped=true')
    if options.get('capped') and 'size' not in options:
        raise DatabaseError('A capped collection requires size')
    # A bounded first checkpoint avoids pretending engine-normalized sizes are
    # exact user values. MongoDB rounds capped size to a 256-byte multiple.
    if 'size' in options and options['size'] % 256:
        raise DatabaseError('This checkpoint requires a capped size in whole 256-byte units')
    for key in {'validator', 'collation'} & options.keys():
        if not isinstance(options[key], dict): raise DatabaseError(key + ' must be a BSON document')
    if 'validationLevel' in options and (not isinstance(options['validationLevel'], str) or options['validationLevel'] not in {'off', 'strict', 'moderate'}):
        raise DatabaseError('Unsupported validationLevel for this checkpoint')
    if 'validationAction' in options and (not isinstance(options['validationAction'], str) or options['validationAction'] not in {'warn', 'error'}):
        raise DatabaseError('Unsupported validationAction for this checkpoint')
    try:
        _validate_date_precision(options)
        raw = _encoded(_definition(definition))
        if len(raw) > _MAX_CATALOG_BYTES or _encoded(BSON(raw).decode(codec_options=BSON_CODEC)) != raw:
            raise ValueError()
    except Exception:
        raise DatabaseError('The definition cannot be encoded as exact bounded BSON') from None


def _current(session, snapshot, name=None):
    name = name or snapshot.name
    raw = _catalog(session, snapshot.schema, name)
    if snapshot.exists:
        if raw != snapshot.raw_spec:
            raise MongoConflictError('The collection UUID or options changed after loading; refresh before saving')
    elif raw:
        raise MongoConflictError('A collection with this name already exists; no collection was modified')


def plan_collection(session, snapshot, definition):
    snapshot, definition = deepcopy(snapshot), deepcopy(definition)
    _verify_snapshot(session, snapshot)
    if snapshot.exists and definition == snapshot.definition:
        _current(session, snapshot)
        return _seal_plan(session, CollectionPlan(snapshot, definition))
    if session.config.read_only or snapshot.read_only:
        raise DatabaseError('This connection or collection is read-only')
    validate_definition(definition)
    session._validate_namespace(snapshot.schema, definition.name)
    _current(session, snapshot, definition.name)
    if snapshot.exists:
        if definition.name != snapshot.name:
            raise DatabaseError('Collection rename requires its own reviewed workflow; no rebuild is performed')
        old, new = snapshot.definition.options, definition.options
        changed = {key for key in old.keys() | new.keys()
            if _encoded({'value': old.get(key)}) != _encoded({'value': new.get(key)}) or (key in old) != (key in new)}
        if changed - _MUTABLE:
            raise DatabaseError('Only validation rules/level/action can be changed in place; immutable options are preserved')
        if not changed:
            return _seal_plan(session, CollectionPlan(snapshot, definition))
        command = {'collMod': snapshot.name, 'collectionUUID': snapshot.namespace_uuid,
            **{key: deepcopy(new.get(key, _DEFAULT[key])) for key in sorted(changed)}}
        warnings = ['UUID protects the collection identity, not an atomic comparison of its options. Another client can change the same option between the final check and collMod.']
    else:
        command = {'create': definition.name, **deepcopy(definition.options)}
        warnings = ['Create can acknowledge an identically configured collection created by another client after the final check. Ownership is not proven; no automatic delete or rollback will run.']
    command['writeConcern'] = {'w': 1}
    return _seal_plan(session, CollectionPlan(snapshot, definition, [command], True, warnings))


def _uuid_guard(session, schema, name, comment):
    """An empty collMod probe changes no option even if UUID is ignored."""
    try:
        with timeout(3):
            session.connection[schema].command({'collMod': name,
                'collectionUUID': Binary(uuid.uuid4().bytes, 4), 'comment': comment})
    except OperationFailure as error:
        if error.code == 361 and (error.details or {}).get('codeName') == 'CollectionUUIDMismatch': return
        raise DatabaseError('The server did not verify the required collection UUID guard; nothing was saved') from error
    raise DatabaseError('The server ignored the required collection UUID guard; nothing was saved')


def plan_drop(session, snapshot):
    """Seal a UUID-guarded drop without claiming that deletion is reversible."""
    snapshot = deepcopy(snapshot)
    _verify_snapshot(session, snapshot)
    if not snapshot.exists or snapshot.kind != 'mongo_collection' or snapshot.collection_type != 'collection':
        raise DatabaseError('Only an existing ordinary collection can be dropped here')
    if session.config.read_only or snapshot.read_only:
        raise DatabaseError('This connection or collection is read-only')
    _current(session, snapshot)
    command = {'drop': snapshot.name, 'collectionUUID': snapshot.namespace_uuid,
        'writeConcern': {'w': 1}}
    warnings = ['Drop is permanent. MongoDB protects the namespace UUID, but a peer that recreates the same name after the command is not deleted.']
    return _seal_plan(session, CollectionPlan(snapshot, deepcopy(snapshot.definition), [command], True, warnings))


def plan_clear(session, snapshot):
    """Seal deleteMany({}) while retaining collections, indexes and options."""
    snapshot = deepcopy(snapshot)
    _verify_snapshot(session, snapshot)
    if not snapshot.exists or snapshot.kind != 'mongo_collection' or snapshot.collection_type != 'collection':
        raise DatabaseError('Only an existing ordinary collection can be cleared here')
    if session.config.read_only or snapshot.read_only:
        raise DatabaseError('This connection or collection is read-only')
    _current(session, snapshot)
    estimated = session.connection[snapshot.schema][snapshot.name].estimated_document_count()
    command = {'delete': snapshot.name, 'deletes': [{'q': {}, 'limit': 0,
        'collation': {'locale': 'simple'}}], 'ordered': True,
        'collectionUUID': snapshot.namespace_uuid, 'writeConcern': {'w': 1}}
    warnings = [
        f'Clear permanently deletes all documents in {snapshot.schema}.{snapshot.name}. The estimated count before planning is {estimated}; indexes and collection options are retained.',
        'The UUID protects collection identity, not a data snapshot. Documents written by another client after the final check are also deleted.',
        'Clear is not transactional and is not automatically rolled back.',
    ]
    return _seal_plan(session, CollectionPlan(snapshot, deepcopy(snapshot.definition), [command], True, warnings))


def plan_rename(session, snapshot, new_name):
    """Seal a UUID-guarded rename that never drops an existing target."""
    snapshot = deepcopy(snapshot)
    _verify_snapshot(session, snapshot)
    if not snapshot.exists or snapshot.kind != 'mongo_collection' or snapshot.collection_type != 'collection':
        raise DatabaseError('Only an existing ordinary collection can be renamed here')
    if session.config.read_only or snapshot.read_only:
        raise DatabaseError('This connection or collection is read-only')
    if not isinstance(new_name, str) or not new_name or new_name == snapshot.name:
        raise DatabaseError('Enter a different nonempty collection name')
    session._validate_namespace(snapshot.schema, new_name)
    _current(session, snapshot)
    if _catalog(session, snapshot.schema, new_name):
        raise MongoConflictError('The target collection name already exists; no collection was renamed')
    source = snapshot.schema + '.' + snapshot.name
    target = snapshot.schema + '.' + new_name
    definition = CollectionDefinition(new_name, deepcopy(snapshot.definition.options))
    command = {'renameCollection': source, 'to': target, 'dropTarget': False,
        'collectionUUID': snapshot.namespace_uuid, 'writeConcern': {'w': 1}}
    warnings = ['Rename checks that the target name is absent before dispatch. A peer can create the target in the final race window; dropTarget is always false, so no existing target is intentionally deleted.']
    return _seal_plan(session, CollectionPlan(snapshot, definition, [command], True, warnings))


def _observe(session, plan, outcome):
    try:
        # A disconnect/reconnect or connection edit after dispatch must not
        # retarget recovery reads or mint a new snapshot for the old request.
        _verify_plan(session, plan)
        observed = load_collection(session, plan.snapshot.schema, plan.definition.name)
        _verify_plan(session, plan)
        outcome.observed = observed
    except Exception as error:
        outcome.observation_error = type(error).__name__


def _matches(plan, observed):
    if observed.kind != 'mongo_collection' or observed.read_only:
        return False
    actual = observed.definition.options
    expected = plan.definition.options
    keys = expected.keys() if not plan.snapshot.exists else plan.commands[0].keys() & _MUTABLE
    for key in keys:
        wanted, got = expected.get(key, _DEFAULT.get(key)), actual.get(key, _DEFAULT.get(key))
        if key == 'capped': got = actual.get(key, False)
        if key == 'collation':
            got = actual.get(key, {'locale': 'simple'})
            if not all(got.get(k) == v for k, v in wanted.items()): return False
        elif key in {'size', 'max'}:
            if got != wanted: return False  # server catalog uses numeric controls
        elif _encoded({'v': wanted}) != _encoded({'v': got}): return False
    return not plan.snapshot.exists or observed.namespace_uuid == plan.snapshot.namespace_uuid


def apply_plan(session, plan, *, cancelled=None):
    plan = deepcopy(plan)
    _verify_plan(session, plan)
    outcome = CollectionOutcome('collMod' if plan.snapshot.exists else 'create',
        created_by_this_plan=False if plan.snapshot.exists else None)
    def stopped():
        return bool(cancelled and cancelled()) or session._cancel_requested.is_set()
    # A queued cancellation performs no catalog/control/network operation.
    if cancelled and cancelled():
        outcome.cancelled = True
        raise CollectionApplyError('Collection save cancelled before database access', outcome)
    if plan.changed and (session.config.read_only or plan.snapshot.read_only):
        raise DatabaseError('This connection or collection is read-only')
    try:
        with session._operation() as comment:
            _current(session, plan.snapshot, plan.definition.name)
            if not plan.changed:
                outcome.status = 'no_change'; outcome.observed = plan.snapshot
                return CollectionApplyResult(plan.snapshot, outcome)
            if plan.snapshot.exists:
                _uuid_guard(session, plan.snapshot.schema, plan.snapshot.name, comment)
                _current(session, plan.snapshot)
            _verify_plan(session, plan)  # connection/config cannot retarget after preview
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('Collection save stopped before its command', outcome)
            outcome.status = 'unknown'
            try:
                with timeout(120):
                    reply = session.connection[plan.snapshot.schema].command({**plan.commands[0], 'comment': comment})
                outcome.reply = deepcopy(dict(reply))
                if reply.get('writeConcernError') or reply.get('writeErrors'):
                    raise DatabaseError('The server did not acknowledge this collection command completely')
                outcome.status = 'acknowledged'
            except Exception as error:
                failure = server_failure(error)
                if failure is not None:
                    outcome.reply = failure.document
                    if not failure.concern_uncertain: outcome.status = 'server_error'
                raise
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('The command was acknowledged before cancellation; it is not rolled back', outcome)
            _observe(session, plan, outcome)
            if outcome.observed is None or not _matches(plan, outcome.observed):
                raise CollectionApplyError('The command was acknowledged, but its final collection state could not be verified', outcome)
            return CollectionApplyResult(outcome.observed, outcome)
    except Exception as error:
        outcome.cancelled = outcome.cancelled or stopped()
        if outcome.status != 'not_started' and outcome.observed is None:
            _observe(session, plan, outcome)
        if isinstance(error, CollectionApplyError): raise
        raise CollectionApplyError('Collection save failed; acknowledged changes are not rolled back and no retry/delete was attempted', outcome) from error


def _observe_operation(session, plan, outcome):
    try:
        _verify_plan(session, plan)
        command = plan.commands[0]
        if 'drop' in command:
            raw = _catalog(session, plan.snapshot.schema, plan.snapshot.name)
            outcome.collection_absent = not raw
            outcome.observed = None
        elif 'renameCollection' in command:
            outcome.collection_absent = not _catalog(session, plan.snapshot.schema, plan.snapshot.name)
            target = command['to'].split('.', 1)[1]
            outcome.observed = load_collection(session, plan.snapshot.schema, target)
        else:
            observed = load_collection(session, plan.snapshot.schema, plan.snapshot.name)
            outcome.observed = observed
            outcome.remaining_documents = session.connection[plan.snapshot.schema][plan.snapshot.name].estimated_document_count()
        _verify_plan(session, plan)
    except Exception as error:
        outcome.observation_error = type(error).__name__


def _operation_matches(plan, outcome):
    if 'drop' in plan.commands[0]:
        return outcome.collection_absent is True
    if 'renameCollection' in plan.commands[0]:
        return (outcome.collection_absent is True and outcome.observed is not None
            and outcome.observed.namespace_uuid == plan.snapshot.namespace_uuid)
    observed = outcome.observed
    return (observed is not None and observed.namespace_uuid == plan.snapshot.namespace_uuid
        and outcome.remaining_documents == 0)


def apply_operation(session, plan, *, cancelled=None):
    """Apply a sealed drop or clear; never retry or claim a rollback."""
    plan = deepcopy(plan)
    _verify_plan(session, plan)
    command = plan.commands[0]
    if not plan.changed or sum(key in command for key in ('drop', 'delete', 'renameCollection')) != 1:
        raise DatabaseError('A drop, clear or rename operation plan is required')
    outcome = CollectionOutcome('drop' if 'drop' in command else 'rename' if 'renameCollection' in command else 'clear')
    def stopped():
        return bool(cancelled and cancelled()) or session._cancel_requested.is_set()
    if cancelled and cancelled():
        outcome.cancelled = True
        raise CollectionApplyError('Collection operation cancelled before database access', outcome)
    if session.config.read_only or plan.snapshot.read_only:
        raise DatabaseError('This connection or collection is read-only')
    try:
        with session._operation() as comment:
            _current(session, plan.snapshot)
            _verify_plan(session, plan)
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('Collection operation stopped before its command', outcome)
            outcome.status = 'unknown'
            try:
                with timeout(120):
                    database = session.connection.admin if 'renameCollection' in command else session.connection[plan.snapshot.schema]
                    reply = database.command({**command, 'comment': comment})
                outcome.reply = deepcopy(dict(reply))
                if reply.get('writeConcernError') or reply.get('writeErrors'):
                    raise DatabaseError('The server did not acknowledge this collection operation completely')
                outcome.status = 'acknowledged'
            except Exception as error:
                failure = server_failure(error)
                if failure is not None:
                    outcome.reply = failure.document
                    if not failure.concern_uncertain: outcome.status = 'server_error'
                raise
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('The command was acknowledged before cancellation; it is not rolled back', outcome)
            _observe_operation(session, plan, outcome)
            if not _operation_matches(plan, outcome):
                raise CollectionApplyError('The command was acknowledged, but its final collection state could not be verified', outcome)
            return CollectionOperationResult(outcome.observed, outcome)
    except Exception as error:
        outcome.cancelled = outcome.cancelled or stopped()
        if outcome.status != 'not_started' and outcome.observed is None and outcome.collection_absent is None:
            _observe_operation(session, plan, outcome)
        if isinstance(error, CollectionApplyError): raise
        raise CollectionApplyError('Collection operation failed; acknowledged effects are not rolled back', outcome) from error
