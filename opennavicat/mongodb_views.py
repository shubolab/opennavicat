"""Sealed ordinary MongoDB view definitions with no server-side UUID guard."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac

from bson import BSON
from pymongo import timeout

from .database import DatabaseError
from .js_engine.server_errors import server_failure
from .mongodb_collections import CollectionApplyError, CollectionOutcome, _catalog, _command_text
from .mongodb_index_script import protected_command_script
from .mongodb_session import BSON_CODEC, MongoConflictError, canonical_array_dumps


_MAX_VIEW_BYTES = 1024 * 1024
_ALLOWED_OPTIONS = {'viewOn', 'pipeline'}


@dataclass(eq=False)
class ViewDefinition:
    name: str = ''
    view_on: str = ''
    pipeline: list = field(default_factory=list)

    def __eq__(self, other):
        try:
            return isinstance(other, ViewDefinition) and _encoded(_definition(self)) == _encoded(_definition(other))
        except Exception:
            return False


@dataclass
class ViewSnapshot:
    schema: str
    name: str
    definition: ViewDefinition
    raw_spec: bytes = field(default=b'', repr=False)
    fingerprint: str = ''
    token: bytes = field(default=b'', repr=False)
    exists: bool = True
    read_only: bool = False
    issues: list[str] = field(default_factory=list)
    kind: str = 'mongo_view'


@dataclass
class ViewPlan:
    snapshot: ViewSnapshot
    definition: ViewDefinition
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
class ViewApplyResult:
    snapshot: ViewSnapshot
    outcome: CollectionOutcome


def _encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def _definition(value):
    return {'name': value.name, 'view_on': value.view_on, 'pipeline': value.pipeline}


def _snapshot_payload(value):
    return _encoded({'schema': value.schema, 'name': value.name, 'definition': _definition(value.definition),
        'raw': value.raw_spec, 'exists': value.exists, 'read_only': value.read_only,
        'issues': value.issues, 'kind': value.kind})


def _sign(session, snapshot, purpose, payload):
    return session._token(snapshot.schema, snapshot.name, purpose + b'\0' + payload, False, None)


def _seal_snapshot(session, snapshot):
    raw = _snapshot_payload(snapshot)
    snapshot.fingerprint = hashlib.sha256(raw).hexdigest()
    snapshot.token = _sign(session, snapshot, b'view-snapshot', raw)
    return snapshot


def _verify_snapshot(session, snapshot):
    try:
        if not isinstance(snapshot, ViewSnapshot): raise ValueError()
        raw = _snapshot_payload(snapshot)
        if not hmac.compare_digest(snapshot.token, _sign(session, snapshot, b'view-snapshot', raw)) or hashlib.sha256(raw).hexdigest() != snapshot.fingerprint:
            raise ValueError()
    except Exception:
        raise DatabaseError('The view snapshot is stale, forged or belongs to another session') from None


def _plan_payload(plan):
    return _encoded({'snapshot': _snapshot_payload(plan.snapshot), 'definition': _definition(plan.definition),
        'commands': plan.commands, 'changed': plan.changed, 'warnings': plan.warnings})


def _seal_plan(session, plan):
    plan.token = _sign(session, plan.snapshot, b'view-plan', _plan_payload(plan))
    return plan


def _verify_plan(session, plan):
    try:
        if not isinstance(plan, ViewPlan): raise ValueError()
        _verify_snapshot(session, plan.snapshot)
        if not hmac.compare_digest(plan.token, _sign(session, plan.snapshot, b'view-plan', _plan_payload(plan))):
            raise ValueError()
    except Exception:
        raise DatabaseError('The view plan is stale, forged or belongs to another session') from None


def _snapshot(session, schema, name, raw):
    if not raw:
        return _seal_snapshot(session, ViewSnapshot(schema, name, ViewDefinition(name), exists=False,
            read_only=session.config.read_only))
    spec = BSON(raw).decode(codec_options=BSON_CODEC)
    options = spec.get('options', {})
    issues = []
    if spec.get('type') != 'view': issues.append('Only a view can be edited here.')
    if set(options) - _ALLOWED_OPTIONS:
        issues.append('Additional view options have not completed designer qualification; original options are retained.')
    try:
        if BSON.encode(BSON(raw).decode(codec_options=BSON_CODEC), codec_options=BSON_CODEC) != raw:
            issues.append('Raw view BSON does not roundtrip exactly.')
    except Exception:
        issues.append('Raw view BSON cannot be decoded and re-encoded exactly.')
    definition = ViewDefinition(name, options.get('viewOn', ''), deepcopy(options.get('pipeline', [])))
    try:
        canonical_array_dumps(definition.pipeline)
    except Exception:
        issues.append('The view pipeline cannot roundtrip through Canonical Extended JSON; the raw catalog is read-only.')
    return _seal_snapshot(session, ViewSnapshot(schema, name, definition, raw, exists=True,
        read_only=bool(issues) or session.config.read_only, issues=issues))


def new_view(session, database, name=''):
    session._validate_namespace(database, name or None)
    session._check()
    return _seal_snapshot(session, ViewSnapshot(database, name, ViewDefinition(name), exists=False,
        read_only=session.config.read_only))


def load_view(session, database, name):
    raw = _catalog(session, database, name)
    if not raw: raise MongoConflictError('The view no longer exists')
    return _snapshot(session, database, name, raw)


def validate_definition(session, definition):
    if not isinstance(definition, ViewDefinition):
        raise DatabaseError('A view definition is required')
    name, source, pipeline = definition.name, definition.view_on, definition.pipeline
    if not isinstance(name, str) or not name or '\0' in name or '$' in name or name.startswith('system.') or name.endswith(('.files', '.chunks')):
        raise DatabaseError('Enter a nonempty, non-system view name')
    session._validate_namespace(definition.name, source)
    if source.startswith('system.') or source.endswith(('.files', '.chunks')):
        raise DatabaseError('The source namespace requires its dedicated tool')
    if not isinstance(pipeline, list) or any(not isinstance(stage, dict) for stage in pipeline):
        raise DatabaseError('A view pipeline must be an array of BSON documents')
    if len(pipeline) > 200:
        raise DatabaseError('The view pipeline exceeds the bounded designer limit')
    try:
        raw = _encoded(_definition(definition))
        if len(raw) > _MAX_VIEW_BYTES or _encoded(BSON(raw).decode(codec_options=BSON_CODEC)) != raw:
            raise ValueError()
    except Exception:
        raise DatabaseError('The view definition cannot be encoded as exact bounded BSON') from None
    return definition


def _current(session, snapshot, name=None):
    name = name or snapshot.name
    raw = _catalog(session, snapshot.schema, name)
    if snapshot.exists:
        if raw != snapshot.raw_spec:
            raise MongoConflictError('The view definition changed after loading; refresh before saving')
    elif raw:
        raise MongoConflictError('A collection with this name already exists; no view was created')


def plan_view(session, snapshot, definition):
    snapshot, definition = deepcopy(snapshot), deepcopy(definition)
    _verify_snapshot(session, snapshot)
    if snapshot.read_only or session.config.read_only:
        raise DatabaseError('This connection or view is read-only')
    validate_definition(session, definition)
    if snapshot.exists and definition.name != snapshot.name:
        raise DatabaseError('View rename requires its own reviewed workflow')
    _current(session, snapshot, definition.name)
    if snapshot.exists and definition == snapshot.definition:
        return _seal_plan(session, ViewPlan(snapshot, definition))
    if definition.view_on:
        source_raw = _catalog(session, snapshot.schema, definition.view_on)
        if not source_raw:
            raise MongoConflictError('The view source namespace does not exist')
    command = ({'create': definition.name, 'viewOn': definition.view_on, 'pipeline': deepcopy(definition.pipeline),
        'writeConcern': {'w': 1}} if not snapshot.exists else
        {'collMod': definition.name, 'viewOn': definition.view_on, 'pipeline': deepcopy(definition.pipeline),
         'writeConcern': {'w': 1}})
    warnings = ['MongoDB views have no collection UUID; the designer compares the complete raw view catalog before and after, but this is not an atomic compare-and-swap.']
    return _seal_plan(session, ViewPlan(snapshot, definition, [command], True, warnings))


def _matches(plan, observed):
    return observed is not None and observed.kind == 'mongo_view' and observed.definition == plan.definition


def _observe(session, plan, outcome):
    try:
        _verify_plan(session, plan)
        outcome.observed = load_view(session, plan.snapshot.schema, plan.definition.name)
        _verify_plan(session, plan)
    except Exception as error:
        outcome.observation_error = type(error).__name__


def apply_plan(session, plan, *, cancelled=None):
    plan = deepcopy(plan)
    _verify_plan(session, plan)
    outcome = CollectionOutcome('collMod' if plan.snapshot.exists else 'create',
        created_by_this_plan=False if plan.snapshot.exists else None)
    def stopped(): return bool(cancelled and cancelled()) or session._cancel_requested.is_set()
    if cancelled and cancelled():
        outcome.cancelled = True
        raise CollectionApplyError('View save cancelled before database access', outcome)
    if plan.changed and (session.config.read_only or plan.snapshot.read_only):
        raise DatabaseError('This connection or view is read-only')
    try:
        with session._operation() as comment:
            _current(session, plan.snapshot, plan.definition.name)
            if not plan.changed:
                outcome.status = 'no_change'; outcome.observed = plan.snapshot
                return ViewApplyResult(plan.snapshot, outcome)
            _verify_plan(session, plan)
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('View save stopped before its command', outcome)
            outcome.status = 'unknown'
            try:
                with timeout(120):
                    reply = session.connection[plan.snapshot.schema].command({**plan.commands[0], 'comment': comment})
                outcome.reply = deepcopy(dict(reply))
                if reply.get('writeConcernError') or reply.get('writeErrors'):
                    raise DatabaseError('The server did not acknowledge the view command completely')
                outcome.status = 'acknowledged'
            except Exception as error:
                failure = server_failure(error)
                if failure is not None:
                    outcome.reply = failure.document
                    if not failure.concern_uncertain: outcome.status = 'server_error'
                raise
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('The view command was acknowledged before cancellation; it is not rolled back', outcome)
            _observe(session, plan, outcome)
            if not _matches(plan, outcome.observed):
                raise CollectionApplyError('The command was acknowledged, but the final view definition could not be verified', outcome)
            return ViewApplyResult(outcome.observed, outcome)
    except Exception as error:
        outcome.cancelled = outcome.cancelled or stopped()
        if outcome.status != 'not_started' and outcome.observed is None:
            _observe(session, plan, outcome)
        if isinstance(error, CollectionApplyError): raise
        message = str(error) if isinstance(error, MongoConflictError) else (
            'View save did not execute; no view command was sent' if outcome.status == 'not_started'
            else 'View save failed; acknowledged changes are not rolled back')
        raise CollectionApplyError(message, outcome) from error
