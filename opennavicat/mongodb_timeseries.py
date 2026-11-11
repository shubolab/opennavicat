"""Sealed ordinary time-series collection DDL with native MongoDB I/O."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json

from bson import BSON, Binary
from pymongo import timeout
from pymongo.errors import OperationFailure

from .database import DatabaseError
from .mongodb_collections import CollectionApplyError, CollectionOutcome, _catalog, _command_text
from .mongodb_session import BSON_CODEC, MongoConflictError, _validate_date_precision
from .mongodb_index_script import protected_command_script


_MAX_CATALOG_BYTES = 1024 * 1024
_GRANULARITIES = {'seconds', 'minutes', 'hours'}
_ALLOWED_OPTIONS = {'timeseries', 'expireAfterSeconds'}


@dataclass(eq=False)
class TimeSeriesDefinition:
    name: str = ''
    time_field: str = ''
    meta_field: str = ''
    granularity: str = 'seconds'
    bucket_rounding_seconds: int | None = None
    bucket_max_span_seconds: int | None = None
    expire_after_seconds: int | None = None

    def __eq__(self, other):
        try:
            return isinstance(other, TimeSeriesDefinition) and _encoded(_comparison_definition(self)) == _encoded(_comparison_definition(other))
        except Exception:
            return False


@dataclass
class TimeSeriesSnapshot:
    schema: str
    name: str
    definition: TimeSeriesDefinition
    raw_spec: bytes = field(default=b'', repr=False)
    namespace_uuid: object = field(default=None, repr=False)
    fingerprint: str = ''
    token: bytes = field(default=b'', repr=False)
    exists: bool = True
    read_only: bool = False
    issues: list[str] = field(default_factory=list)
    kind: str = 'mongo_timeseries'


@dataclass
class TimeSeriesPlan:
    snapshot: TimeSeriesSnapshot
    definition: TimeSeriesDefinition
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
class TimeSeriesApplyResult:
    snapshot: TimeSeriesSnapshot
    outcome: CollectionOutcome


def _encoded(value):
    return BSON.encode(value, codec_options=BSON_CODEC)


def _definition(value):
    return {
        'name': value.name,
        'time_field': value.time_field,
        'meta_field': value.meta_field,
        'granularity': value.granularity,
        'bucket_rounding_seconds': value.bucket_rounding_seconds,
        'bucket_max_span_seconds': value.bucket_max_span_seconds,
        'expire_after_seconds': value.expire_after_seconds,
    }


def _comparison_definition(value):
    def integer(item):
        return None if item is None else int(item)
    return {
        'name': value.name,
        'time_field': value.time_field,
        'meta_field': value.meta_field,
        'granularity': value.granularity,
        'bucket_rounding_seconds': integer(value.bucket_rounding_seconds),
        'bucket_max_span_seconds': integer(value.bucket_max_span_seconds),
        'expire_after_seconds': integer(value.expire_after_seconds),
    }


def _snapshot_payload(value):
    return _encoded({
        'schema': value.schema,
        'name': value.name,
        'definition': _definition(value.definition),
        'raw': Binary(value.raw_spec),
        'uuid': value.namespace_uuid,
        'exists': value.exists,
        'read_only': value.read_only,
        'issues': value.issues,
        'kind': value.kind,
    })


def _sign(session, snapshot, purpose, payload):
    return session._token(snapshot.schema, snapshot.name, purpose + b'\0' + payload, False, snapshot.namespace_uuid)


def _seal_snapshot(session, snapshot):
    raw = _snapshot_payload(snapshot)
    snapshot.fingerprint = hashlib.sha256(raw).hexdigest()
    snapshot.token = _sign(session, snapshot, b'timeseries-snapshot', raw)
    return snapshot


def _verify_snapshot(session, snapshot):
    try:
        if not isinstance(snapshot, TimeSeriesSnapshot):
            raise ValueError()
        raw = _snapshot_payload(snapshot)
        expected = _sign(session, snapshot, b'timeseries-snapshot', raw)
        if not hmac.compare_digest(snapshot.token, expected) or hashlib.sha256(raw).hexdigest() != snapshot.fingerprint:
            raise ValueError()
    except Exception:
        raise DatabaseError('The time-series snapshot is stale, forged or belongs to another session') from None


def _plan_payload(plan):
    return _encoded({
        'snapshot': _snapshot_payload(plan.snapshot),
        'definition': _definition(plan.definition),
        'commands': plan.commands,
        'changed': plan.changed,
        'warnings': plan.warnings,
    })


def _seal_plan(session, plan):
    raw = _plan_payload(plan)
    plan.token = _sign(session, plan.snapshot, b'timeseries-plan', raw)
    return plan


def _verify_plan(session, plan):
    try:
        if not isinstance(plan, TimeSeriesPlan):
            raise ValueError()
        _verify_snapshot(session, plan.snapshot)
        expected = _sign(session, plan.snapshot, b'timeseries-plan', _plan_payload(plan))
        if not hmac.compare_digest(plan.token, expected):
            raise ValueError()
    except Exception:
        raise DatabaseError('The time-series plan is stale, forged or belongs to another session') from None


def _raw_issues(spec, namespace_uuid, raw):
    issues = []
    options = spec.get('options', {})
    if spec.get('type') != 'timeseries':
        issues.append('Only a time-series collection can be edited here.')
    if set(options) - _ALLOWED_OPTIONS:
        issues.append('Additional time-series options have not completed designer qualification; original options are retained.')
    try:
        if BSON(raw).decode(codec_options=BSON_CODEC).get('options', {}) != options:
            issues.append('Raw catalog BSON did not preserve its decoded options exactly.')
    except Exception:
        issues.append('Raw catalog BSON cannot be decoded and re-encoded exactly; original bytes are retained.')
    if raw and (spec.get('name') is None or spec.get('type') != 'timeseries'
                or not isinstance(namespace_uuid, Binary) or namespace_uuid.subtype != 4 or len(namespace_uuid) != 16):
        issues.append('A stable collection UUID is required for safe time-series editing.')
    return issues


def _snapshot(session, schema, name, raw):
    if not raw:
        return _seal_snapshot(session, TimeSeriesSnapshot(schema, name, TimeSeriesDefinition(name), exists=False,
            read_only=session.config.read_only))
    spec = BSON(raw).decode(codec_options=BSON_CODEC)
    bucket_raw = _catalog(session, schema, 'system.buckets.' + name)
    bucket_spec = BSON(bucket_raw).decode(codec_options=BSON_CODEC) if bucket_raw else {}
    namespace_uuid = (bucket_spec.get('info') or {}).get('uuid')
    options = spec.get('options', {})
    ts = options.get('timeseries', {})
    granularity = ts.get('granularity', '')
    if granularity:
        rounding = max_span = None
    else:
        rounding = ts.get('bucketRoundingSeconds')
        max_span = ts.get('bucketMaxSpanSeconds')
    definition = TimeSeriesDefinition(
        name=name,
        time_field=ts.get('timeField', ''),
        meta_field=ts.get('metaField', ''),
        granularity=granularity,
        bucket_rounding_seconds=rounding,
        bucket_max_span_seconds=max_span,
        expire_after_seconds=(int(options['expireAfterSeconds']) if options.get('expireAfterSeconds') is not None else None),
    )
    issues = _raw_issues(spec, namespace_uuid, raw)
    return _seal_snapshot(session, TimeSeriesSnapshot(schema, name, definition, raw, namespace_uuid,
        exists=True, read_only=bool(issues) or session.config.read_only, issues=issues))


def new_time_series(session, database, name=''):
    session._validate_namespace(database, name or None)
    session._check()
    definition = TimeSeriesDefinition(name=name)
    return _seal_snapshot(session, TimeSeriesSnapshot(database, name, definition, exists=False,
        read_only=session.config.read_only))


def load_time_series(session, database, name):
    raw = _catalog(session, database, name)
    if not raw:
        raise MongoConflictError('The time-series collection no longer exists')
    return _snapshot(session, database, name, raw)


def _field_name(value, label):
    if not isinstance(value, str) or not value or '\0' in value or value.startswith('$') or '.' in value:
        raise DatabaseError(label + ' must be a nonempty top-level field name')
    if value == '_id':
        raise DatabaseError(label + ' cannot be _id')


def validate_definition(definition):
    if not isinstance(definition, TimeSeriesDefinition):
        raise DatabaseError('A time-series collection definition is required')
    name = definition.name
    if not isinstance(name, str) or not name or '\0' in name or '$' in name or name.startswith('system.') or name.endswith(('.files', '.chunks')):
        raise DatabaseError('Enter a nonempty, non-system time-series collection name')
    _field_name(definition.time_field, 'timeField')
    if definition.meta_field:
        _field_name(definition.meta_field, 'metaField')
    if definition.meta_field and definition.meta_field == definition.time_field:
        raise DatabaseError('metaField must differ from timeField')
    granularity = definition.granularity
    if granularity and granularity not in _GRANULARITIES:
        raise DatabaseError('granularity must be seconds, minutes or hours')
    rounding, span = definition.bucket_rounding_seconds, definition.bucket_max_span_seconds
    for value, label in ((rounding, 'bucketRoundingSeconds'), (span, 'bucketMaxSpanSeconds')):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31536000):
            raise DatabaseError(label + ' must be an integer from 1 through 31536000')
    if granularity and (rounding is not None or span is not None):
        raise DatabaseError('custom bucketing cannot be combined with granularity')
    if not granularity:
        if rounding is None or span is None:
            raise DatabaseError('Custom bucketing requires both bucketRoundingSeconds and bucketMaxSpanSeconds')
        if rounding != span:
            raise DatabaseError('bucketRoundingSeconds must equal bucketMaxSpanSeconds')
    expire = definition.expire_after_seconds
    if expire is not None and (isinstance(expire, bool) or not isinstance(expire, int) or expire < 1 or expire >= 2 ** 63):
        raise DatabaseError('expireAfterSeconds must be a positive BSON integer')
    try:
        _validate_date_precision(definition)
        raw = _encoded(_definition(definition))
        if len(raw) > _MAX_CATALOG_BYTES or _encoded(BSON(raw).decode(codec_options=BSON_CODEC)) != raw:
            raise ValueError()
    except Exception:
        raise DatabaseError('The time-series definition cannot be encoded as exact bounded BSON') from None
    return definition


def _timeseries_options(definition):
    result = {'timeField': definition.time_field}
    if definition.meta_field:
        result['metaField'] = definition.meta_field
    if definition.granularity:
        result['granularity'] = definition.granularity
    else:
        result['bucketMaxSpanSeconds'] = definition.bucket_max_span_seconds
        result['bucketRoundingSeconds'] = definition.bucket_rounding_seconds
    return result


def _current(session, snapshot, name=None):
    name = name or snapshot.name
    raw = _catalog(session, snapshot.schema, name)
    if snapshot.exists:
        if raw != snapshot.raw_spec:
            raise MongoConflictError('The time-series options changed after loading; refresh before saving')
        bucket_raw = _catalog(session, snapshot.schema, 'system.buckets.' + name)
        bucket_spec = BSON(bucket_raw).decode(codec_options=BSON_CODEC) if bucket_raw else {}
        bucket_uuid = (bucket_spec.get('info') or {}).get('uuid')
        if bucket_uuid != snapshot.namespace_uuid:
            raise MongoConflictError('The time-series bucket UUID changed after loading; refresh before saving')
    elif raw:
        raise MongoConflictError('A collection with this name already exists; no collection was modified')


def plan_time_series(session, snapshot, definition):
    snapshot, definition = deepcopy(snapshot), deepcopy(definition)
    _verify_snapshot(session, snapshot)
    if snapshot.read_only or session.config.read_only:
        raise DatabaseError('This connection or time-series collection is read-only')
    validate_definition(definition)
    session._validate_namespace(snapshot.schema, definition.name)
    if snapshot.exists and definition.name != snapshot.name:
        raise DatabaseError('Time-series rename requires its own reviewed workflow')
    _current(session, snapshot, definition.name)
    if snapshot.exists and definition == snapshot.definition:
        return _seal_plan(session, TimeSeriesPlan(snapshot, definition))
    if not snapshot.exists:
        command = {'create': definition.name, 'timeseries': _timeseries_options(definition), 'writeConcern': {'w': 1}}
        if definition.expire_after_seconds is not None:
            command['expireAfterSeconds'] = definition.expire_after_seconds
        warnings = ['Create can acknowledge an identically configured time-series collection created by another client after the final check. Ownership is not proven; no automatic delete or rollback will run.']
        return _seal_plan(session, TimeSeriesPlan(snapshot, definition, [command], True, warnings))

    old = snapshot.definition
    if (definition.time_field, definition.meta_field) != (old.time_field, old.meta_field):
        raise DatabaseError('timeField and metaField are immutable in this time-series designer')
    commands = []
    bucket_old = (old.granularity, old.bucket_max_span_seconds, old.bucket_rounding_seconds)
    bucket_new = (definition.granularity, definition.bucket_max_span_seconds, definition.bucket_rounding_seconds)
    if bucket_new != bucket_old:
        options = ({'granularity': definition.granularity} if definition.granularity else {
            'bucketMaxSpanSeconds': definition.bucket_max_span_seconds,
            'bucketRoundingSeconds': definition.bucket_rounding_seconds,
        })
        commands.append({'collMod': definition.name, 'timeseries': options, 'writeConcern': {'w': 1}})
    if definition.expire_after_seconds != old.expire_after_seconds:
        value = 'off' if definition.expire_after_seconds is None else definition.expire_after_seconds
        commands.append({'collMod': definition.name, 'expireAfterSeconds': value,
            'writeConcern': {'w': 1}})
    warnings = []
    if len(commands) > 1:
        warnings.append('Time-series bucketing and expiry are sent as separate collMod commands. They are not transactional; an acknowledged earlier command is not rolled back if a later command fails.')
    warnings.append('The designer verifies the underlying buckets UUID before and after collMod, but MongoDB rejects collectionUUID on a time-series collMod. This is not an atomic compare-and-swap: a peer can replace or modify the time-series collection between the final check and command.')
    return _seal_plan(session, TimeSeriesPlan(snapshot, definition, commands, bool(commands), warnings))


def _matches(plan, observed):
    if observed is None or observed.kind != 'mongo_timeseries':
        return False
    if plan.snapshot.exists and observed.namespace_uuid != plan.snapshot.namespace_uuid:
        return False
    wanted, actual = plan.definition, observed.definition
    if not plan.snapshot.exists:
        return actual == wanted
    checks = []
    if (wanted.granularity, wanted.bucket_max_span_seconds, wanted.bucket_rounding_seconds) != (
            plan.snapshot.definition.granularity, plan.snapshot.definition.bucket_max_span_seconds,
            plan.snapshot.definition.bucket_rounding_seconds):
        # The server canonicalizes granularity with a derived maximum span.
        checks.append(actual.granularity == wanted.granularity and actual.bucket_rounding_seconds == wanted.bucket_rounding_seconds)
    if wanted.expire_after_seconds != plan.snapshot.definition.expire_after_seconds:
        checks.append(actual.expire_after_seconds == wanted.expire_after_seconds)
    return all(checks) if checks else True


def _observe(session, plan, outcome):
    try:
        _verify_plan(session, plan)
        outcome.observed = load_time_series(session, plan.snapshot.schema, plan.definition.name)
        _verify_plan(session, plan)
    except Exception as error:
        outcome.observation_error = type(error).__name__


def apply_plan(session, plan, *, cancelled=None):
    plan = deepcopy(plan)
    _verify_plan(session, plan)
    outcome = CollectionOutcome('collMod' if plan.snapshot.exists else 'create',
        created_by_this_plan=False if plan.snapshot.exists else None)
    def stopped():
        return bool(cancelled and cancelled()) or session._cancel_requested.is_set()
    if cancelled and cancelled():
        outcome.cancelled = True
        raise CollectionApplyError('Time-series save cancelled before database access', outcome)
    if plan.changed and (session.config.read_only or plan.snapshot.read_only):
        raise DatabaseError('This connection or time-series collection is read-only')
    try:
        with session._operation() as comment:
            _current(session, plan.snapshot, plan.definition.name)
            if not plan.changed:
                outcome.status = 'no_change'
                outcome.observed = plan.snapshot
                return TimeSeriesApplyResult(plan.snapshot, outcome)
            _verify_plan(session, plan)
            for command in plan.commands:
                if stopped():
                    outcome.cancelled = True
                    raise CollectionApplyError('Time-series save stopped before its next command', outcome)
                outcome.status = 'unknown'
                try:
                    with timeout(120):
                        reply = session.connection[plan.snapshot.schema].command({**command, 'comment': comment})
                    outcome.reply = deepcopy(dict(reply))
                    if reply.get('writeConcernError') or reply.get('writeErrors'):
                        raise DatabaseError('The server did not acknowledge this time-series command completely')
                    outcome.status = 'acknowledged'
                except Exception as error:
                    from .js_engine.server_errors import server_failure
                    failure = server_failure(error)
                    if failure is not None:
                        outcome.reply = failure.document
                        if not failure.concern_uncertain:
                            outcome.status = 'server_error'
                    raise
            if stopped():
                outcome.cancelled = True
                raise CollectionApplyError('The commands were acknowledged before cancellation; they are not rolled back', outcome)
            _observe(session, plan, outcome)
            if outcome.observed is None or not _matches(plan, outcome.observed):
                raise CollectionApplyError('The command was acknowledged, but its final time-series state could not be verified', outcome)
            return TimeSeriesApplyResult(outcome.observed, outcome)
    except Exception as error:
        outcome.cancelled = outcome.cancelled or stopped()
        if outcome.status != 'not_started' and outcome.observed is None:
            _observe(session, plan, outcome)
        if isinstance(error, CollectionApplyError):
            raise
        raise CollectionApplyError('Time-series save failed; acknowledged commands are not rolled back', outcome) from error
