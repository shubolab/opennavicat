"""Ordinary collection DDL against only a fresh exact-owned MongoDB fixture."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import base64
import json
import struct
import threading
import time

from bson import BSON, Binary, Int64
from pymongo.database import Database
from pymongo.errors import AutoReconnect, OperationFailure
import pytest

from opennavicat import mongodb_collections as tools
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import BSON_CODEC, MongoConflictError
from test_mongodb_session import mongodb_service, session


def draft(session, name='created', **options):
    snapshot = tools.new_collection(session, session.config.database)
    return snapshot, tools.CollectionDefinition(name, options)


def validation(session, value=1):
    snapshot = tools.load_collection(session, session.config.database, 'docs')
    definition = deepcopy(snapshot.definition)
    definition.options['validator'] = {'n': {'$gte': value}}
    return snapshot, definition


@pytest.mark.parametrize('definition', [
    tools.CollectionDefinition(''), tools.CollectionDefinition('system.internal'),
    tools.CollectionDefinition('files.chunks'), tools.CollectionDefinition('invalid\0name'),
    tools.CollectionDefinition('ok', {'futureOption': True}),
    tools.CollectionDefinition('ok', {'size': True}), tools.CollectionDefinition('ok', {'capped': 1}),
    tools.CollectionDefinition('ok', {'capped': True}),
    tools.CollectionDefinition('ok', {'capped': True, 'size': 257}),
    tools.CollectionDefinition('ok', {'max': 20}),
    tools.CollectionDefinition('ok', {'validator': []}),
    tools.CollectionDefinition('ok', {'validationLevel': []}),
    tools.CollectionDefinition('ok', {'validationLevel': 'constraint'}),
    tools.CollectionDefinition('ok', {'validationAction': 'ignore'}),
    tools.CollectionDefinition('ok', {'collation': 'simple'}),
    tools.CollectionDefinition('ok', {'validator': {'d': datetime(2026, 1, 1, microsecond=1, tzinfo=timezone.utc)}}),
    tools.CollectionDefinition('ok', {'validator': {'invalid': object()}}),
])
def test_invalid_or_lossy_definitions_are_rejected_without_database_capability(definition):
    with pytest.raises(DatabaseError): tools.validate_definition(definition)


def test_detached_definition_equality_retains_bson_integer_type():
    assert tools.CollectionDefinition('a', {'validator': {'n': Int64(1)}}) != tools.CollectionDefinition('a', {'validator': {'n': 1}})


def test_literal_ejson_collision_preview_retains_exact_bson_in_explicit_nonexecuting_envelope():
    command = {'create': 'example', 'validator': {'$expr': {'$literal': {'$date': 'literal-data'}}}}
    plan = tools.CollectionPlan(tools.CollectionSnapshot('owned', '', tools.CollectionDefinition()),
        tools.CollectionDefinition('example'), [command])
    wrapper = json.loads(plan.command_text)
    assert wrapper['format'] == 'opennavicat-bson-command-v1'
    assert base64.b64decode(wrapper['bsonBase64']) == BSON.encode(command, codec_options=BSON_CODEC)
    assert '"$date": "literal-data"' in plan.command_script


def test_real_create_options_and_validator_enforce_actual_writes(session):
    snapshot, definition = draft(session, validator={'n': {'$gte': Int64(2)}}, validationLevel='strict', validationAction='error')
    plan = tools.plan_collection(session, snapshot, definition)
    assert plan.changed and plan.warnings and 'db.runCommand(' in plan.command_script
    result = tools.apply_plan(session, plan)
    assert result.outcome.status == 'acknowledged' and result.outcome.created_by_this_plan is None
    assert result.snapshot.exists and result.snapshot.namespace_uuid
    assert BSON.encode(result.snapshot.definition.options['validator']) == BSON.encode(definition.options['validator'])
    collection = session.connection[session.config.database].created
    collection.insert_one({'n': Int64(3)})
    with pytest.raises(OperationFailure) as error: collection.insert_one({'n': 1})
    assert error.value.code == 121
    assert collection.count_documents({}) == 1
    assert not tools.plan_collection(session, result.snapshot, result.snapshot.definition).changed


def test_real_existing_validation_change_preserves_uuid_documents_indexes_and_collation(session):
    db = session.connection[session.config.database]
    db.docs.drop(); db.create_collection('docs', collation={'locale': 'en', 'strength': 2})
    db.docs.insert_many([{'_id': 1, 'n': 0}, {'_id': 2, 'n': 4}]); db.docs.create_index('n', name='keep')
    before = [BSON.encode(d) for d in db.docs.find().sort('_id')]
    snapshot, definition = validation(session, Int64(2))
    definition.options.update(validationLevel='moderate', validationAction='error')
    plan = tools.plan_collection(session, snapshot, definition)
    assert set(plan.commands[0]) == {'collMod', 'collectionUUID', 'validator', 'validationLevel', 'validationAction', 'writeConcern'}
    saved = tools.apply_plan(session, plan).snapshot
    assert saved.namespace_uuid == snapshot.namespace_uuid
    assert saved.definition.options['collation'] == snapshot.definition.options['collation']
    assert [BSON.encode(d) for d in db.docs.find().sort('_id')] == before
    assert set(db.docs.index_information()) == {'_id_', 'keep'}
    db.docs.update_one({'_id': 1}, {'$set': {'n': -2}})  # existing invalid: moderate
    with pytest.raises(OperationFailure): db.docs.update_one({'_id': 2}, {'$set': {'n': -2}})


def test_real_capped_and_collation_create_and_immutable_changes_are_not_rebuilt(session):
    snapshot, definition = draft(session, capped=True, size=4096, max=Int64(12), collation={'locale': 'en', 'strength': 2})
    result = tools.apply_plan(session, tools.plan_collection(session, snapshot, definition))
    assert result.snapshot.definition.options['size'] == 4096
    assert result.snapshot.definition.options['max'] == 12
    changed = deepcopy(result.snapshot.definition); changed.options['size'] = 8192
    with pytest.raises(DatabaseError, match='immutable'):
        tools.plan_collection(session, result.snapshot, changed)
    assert tools.load_collection(session, result.snapshot.schema, 'created').namespace_uuid == result.snapshot.namespace_uuid


def test_real_explicit_clear_validation_does_not_rebuild_or_drop_documents(session):
    snapshot, definition = validation(session)
    saved = tools.apply_plan(session, tools.plan_collection(session, snapshot, definition)).snapshot
    session.connection[session.config.database].docs.insert_one({'n': 2})
    cleared = deepcopy(saved.definition); cleared.options.pop('validator')
    result = tools.apply_plan(session, tools.plan_collection(session, saved, cleared))
    assert result.snapshot.namespace_uuid == saved.namespace_uuid
    session.connection[session.config.database].docs.insert_one({'n': -10})
    assert session.connection[session.config.database].docs.count_documents({}) == 2


@pytest.mark.parametrize('level,action', [('off', 'error'), ('strict', 'warn')])
def test_real_validation_off_and_warn_allow_invalid_documents_as_configured(session, level, action):
    snapshot, definition = draft(session, validator={'n': {'$gte': 10}}, validationLevel=level, validationAction=action)
    saved = tools.apply_plan(session, tools.plan_collection(session, snapshot, definition)).snapshot
    session.connection[saved.schema].created.insert_one({'n': -1})
    assert session.connection[saved.schema].created.count_documents({}) == 1
    reset = deepcopy(saved.definition)
    reset.options.pop('validationLevel'); reset.options.pop('validationAction')
    strict = tools.apply_plan(session, tools.plan_collection(session, saved, reset)).snapshot
    assert strict.definition.options['validationLevel'] == 'strict' and strict.definition.options['validationAction'] == 'error'
    with pytest.raises(OperationFailure): session.connection[saved.schema].created.insert_one({'n': -2})


def test_real_successful_create_plan_cannot_be_replayed_as_an_owned_create(session):
    snapshot, definition = draft(session); plan = tools.plan_collection(session, snapshot, definition)
    saved = tools.apply_plan(session, plan).snapshot
    session.connection[saved.schema].created.insert_one({'keep': True})
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'not_started'
    assert session.connection[saved.schema].created.count_documents({'keep': True}) == 1


@pytest.mark.parametrize('target', ['snapshot', 'definition', 'commands', 'warnings', 'generation', 'endpoint', 'readonly'])
def test_real_tampering_and_lifecycle_changes_are_rejected_before_io(session, monkeypatch, target):
    snapshot, definition = draft(session)
    plan = tools.plan_collection(session, snapshot, definition)
    if target == 'snapshot': plan.snapshot.raw_spec = b'forged'
    elif target == 'definition': plan.definition.options['capped'] = True
    elif target == 'commands': plan.commands[0]['create'] = 'other'
    elif target == 'warnings': plan.warnings = []
    elif target == 'generation': session._generation = 'different-session'
    elif target == 'endpoint': session.config = replace(session.config, host='different.invalid')
    else: session.config = replace(session.config, read_only=True)
    monkeypatch.setattr(session, '_operation', lambda *_a, **_k: pytest.fail('Forged/stale plan reached I/O'))
    with pytest.raises(DatabaseError, match='plan|snapshot'): tools.apply_plan(session, plan)


def test_real_readonly_noop_is_readable_but_changed_plan_is_refused(session, monkeypatch):
    session.config = replace(session.config, read_only=True)
    snapshot = tools.load_collection(session, session.config.database, 'docs')
    assert tools.apply_plan(session, tools.plan_collection(session, snapshot, snapshot.definition)).outcome.status == 'no_change'
    definition = deepcopy(snapshot.definition); definition.options['validator'] = {}
    monkeypatch.setattr(Database, 'command', lambda *_a, **_k: pytest.fail('Read-only change reached a command'))
    with pytest.raises(DatabaseError, match='read-only'): tools.plan_collection(session, snapshot, definition)


def test_real_reordering_top_level_control_options_is_noop_not_ddl(session, monkeypatch):
    snapshot, definition = validation(session)
    definition.options['validationAction'] = 'error'
    saved = tools.apply_plan(session, tools.plan_collection(session, snapshot, definition)).snapshot
    reordered = deepcopy(saved.definition); reordered.options = dict(reversed(list(reordered.options.items())))
    monkeypatch.setattr(Database, 'command', lambda *_a, **_k: pytest.fail('Unchanged options sent DDL'))
    plan = tools.plan_collection(session, saved, reordered)
    assert not plan.changed and not plan.commands
    assert tools.apply_plan(session, plan).outcome.status == 'no_change'


def test_real_closed_and_reconnected_session_cannot_reuse_prior_plan(session, monkeypatch):
    snapshot, definition = draft(session); plan = tools.plan_collection(session, snapshot, definition)
    session.close(); session.connect()
    monkeypatch.setattr(Database, 'command', lambda *_a, **_k: pytest.fail('Old connection plan reached DDL'))
    with pytest.raises(DatabaseError, match='plan|snapshot'): tools.apply_plan(session, plan)


def test_real_existing_name_and_stale_options_refuse_before_saving(session):
    snapshot, definition = draft(session, 'docs')
    with pytest.raises(MongoConflictError): tools.plan_collection(session, snapshot, definition)
    snapshot, definition = validation(session)
    plan = tools.plan_collection(session, snapshot, definition)
    db = session.connection[session.config.database]
    db.command({'collMod': 'docs', 'validationAction': 'warn'})
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'not_started'
    assert tools.load_collection(session, snapshot.schema, 'docs').definition.options['validationAction'] == 'warn'


def test_real_uuid_guard_blocks_replacement_between_last_catalog_and_command(session, monkeypatch):
    snapshot, definition = validation(session)
    plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; fired = []
    def race(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('collMod') == 'docs' and 'validator' in command and not fired:
            fired.append(True); db.docs.drop(); db.create_collection('docs'); db.docs.insert_one({'marker': 'replacement'})
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', race)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'server_error' and error.value.outcome.reply['code'] == 361
    assert error.value.outcome.observed.namespace_uuid != snapshot.namespace_uuid
    assert session.connection[snapshot.schema].docs.find_one({'marker': 'replacement'})
    assert 'validator' not in error.value.outcome.observed.definition.options


def test_real_server_ignoring_guard_never_receives_desired_mutation(session, monkeypatch):
    snapshot, definition = validation(session)
    plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; guards = []
    def ignore(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('collMod'):
            assert 'validator' not in command
            guards.append(command); return {'ok': 1}
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', ignore)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'not_started' and len(guards) == 1
    assert 'validator' not in tools.load_collection(session, snapshot.schema, 'docs').definition.options


def test_real_same_option_race_is_explicitly_not_claimed_atomic_cas(session, monkeypatch):
    snapshot, definition = validation(session, 5)
    plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; fired = []
    def race(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('collMod') == 'docs' and 'validator' in command and not fired:
            fired.append(True)
            original(db, {'collMod': 'docs', 'validator': {'n': {'$gte': 99}}, 'validationAction': 'warn'})
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', race)
    result = tools.apply_plan(session, plan)
    # The explicitly edited key wins this race; an unrelated concurrent option
    # is preserved. No whole-options rewrite or false CAS guarantee is made.
    assert result.snapshot.definition.options['validator'] == {'n': {'$gte': 5}}
    assert result.snapshot.definition.options['validationAction'] == 'warn'
    assert result.outcome.status == 'acknowledged' and any('not an atomic' in text for text in plan.warnings)


@pytest.mark.parametrize('same_options', [True, False])
def test_real_create_race_never_claims_ownership_or_removes_peer_data(session, monkeypatch, same_options):
    snapshot, definition = draft(session)
    plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; fired = []
    def race(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('create') == 'created' and not fired:
            fired.append(True)
            peer = {'create': 'created'}
            if not same_options: peer['validationAction'] = 'warn'
            original(db, peer); db.created.insert_one({'peer': 'keep'})
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', race)
    if same_options:
        outcome = tools.apply_plan(session, plan).outcome
        assert outcome.status == 'acknowledged'
    else:
        with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
        outcome = error.value.outcome
        assert outcome.status == 'server_error'
    assert outcome.created_by_this_plan is None
    assert session.connection[snapshot.schema].created.find_one({'peer': 'keep'})


@pytest.mark.parametrize('concern', [False, True])
def test_injected_failure_after_real_create_preserves_uncertainty_and_never_rolls_back(session, monkeypatch, concern):
    snapshot, definition = draft(session)
    plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command
    def failure(db, command, *args, **kwargs):
        reply = original(db, command, *args, **kwargs)
        if isinstance(command, dict) and command.get('create') == 'created':
            if concern: raise OperationFailure('synthetic concern after actual creation', 64, {'ok': 0, 'code': 64, 'codeName': 'WriteConcernFailed'})
            raise AutoReconnect('synthetic lost acknowledgement after actual creation')
        return reply
    monkeypatch.setattr(Database, 'command', failure)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'unknown' and error.value.outcome.observed.exists
    assert session.connection[snapshot.schema].created.count_documents({}) == 0


def test_real_acknowledgement_survives_failed_readback(session, monkeypatch):
    snapshot, definition = draft(session)
    plan = tools.plan_collection(session, snapshot, definition)
    original = tools._catalog; actual = Database.command; wrote = []
    def track(db, command, *args, **kwargs):
        reply = actual(db, command, *args, **kwargs)
        if isinstance(command, dict) and command.get('create'): wrote.append(True)
        return reply
    def failed(*args):
        if wrote: raise DatabaseError('synthetic readback denial')
        return original(*args)
    monkeypatch.setattr(Database, 'command', track); monkeypatch.setattr(tools, '_catalog', failed)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'acknowledged' and error.value.outcome.observed is None
    assert error.value.outcome.observation_error == 'DatabaseError'
    assert 'created' in session.connection[session.config.database].list_collection_names()


def test_real_post_ack_connection_edit_does_not_retarget_recovery_metadata(session, monkeypatch):
    snapshot, definition = draft(session); plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; catalog = tools._catalog; dispatched = []
    def changed(db, command, *args, **kwargs):
        reply = original(db, command, *args, **kwargs)
        if isinstance(command, dict) and command.get('create') == 'created':
            dispatched.append(True); session.config = replace(session.config, host='not-the-reviewed-endpoint.invalid')
        return reply
    def protected(*args):
        if dispatched: pytest.fail('Recovery metadata was retargeted after a configuration change')
        return catalog(*args)
    monkeypatch.setattr(Database, 'command', changed); monkeypatch.setattr(tools, '_catalog', protected)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'acknowledged' and error.value.outcome.observed is None
    assert error.value.outcome.observation_error == 'DatabaseError'
    assert session.connection[snapshot.schema].created.count_documents({}) == 0


def test_real_queued_cancel_does_not_open_catalog_or_control_channel(session, monkeypatch):
    snapshot, definition = draft(session); plan = tools.plan_collection(session, snapshot, definition)
    monkeypatch.setattr(session, '_operation', lambda *_a, **_k: pytest.fail('Queued cancellation performed I/O'))
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan, cancelled=lambda: True)
    assert error.value.outcome.cancelled and error.value.outcome.status == 'not_started'
    assert error.value.outcome.observed is None


@pytest.mark.parametrize('after_actual', [False, True])
def test_real_cancel_before_mutation_or_after_ack_is_honest(session, monkeypatch, after_actual):
    snapshot, definition = validation(session)
    plan = tools.plan_collection(session, snapshot, definition)
    event = threading.Event(); original = Database.command
    def cancel(db, command, *args, **kwargs):
        try: return original(db, command, *args, **kwargs)
        finally:
            if isinstance(command, dict) and command.get('collMod') and ('validator' in command) == after_actual:
                event.set()
    monkeypatch.setattr(Database, 'command', cancel)
    with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(session, plan, cancelled=event.is_set)
    assert error.value.outcome.cancelled
    assert error.value.outcome.status == ('acknowledged' if after_actual else 'not_started')
    actual = tools.load_collection(session, snapshot.schema, 'docs')
    assert ('validator' in actual.definition.options) == after_actual


def test_synthetic_duplicate_or_future_catalog_stays_raw_readonly(session, monkeypatch):
    original = tools._catalog(session, session.config.database, 'docs')
    spec = BSON(original).decode(codec_options=BSON_CODEC)
    for raw in [
        BSON.encode({**spec, 'options': {'futureOption': {'typed': Int64(1)}}}, codec_options=BSON_CODEC),
        struct.pack('<i', len(original) + len(BSON.encode({'options': {}})) - 5) + original[4:-1] + BSON.encode({'options': {}})[4:],
    ]:
        monkeypatch.setattr(tools, '_catalog', lambda *_: raw)
        snapshot = tools.load_collection(session, session.config.database, 'docs')
        assert snapshot.read_only and snapshot.raw_spec == raw and snapshot.issues
        changed = deepcopy(snapshot.definition); changed.options['validator'] = {}
        with pytest.raises(DatabaseError, match='read-only'): tools.plan_collection(session, snapshot, changed)


def test_real_view_timeseries_and_gridfs_names_are_readonly_in_this_checkpoint(session):
    db = session.connection[session.config.database]
    db.create_collection('read_view', viewOn='docs', pipeline=[])
    db.create_collection('measurements', timeseries={'timeField': 'time'})
    db.create_collection('files.files')
    for name in ['read_view', 'measurements', 'files.files']:
        snapshot = tools.load_collection(session, session.config.database, name)
        assert snapshot.read_only and snapshot.raw_spec
        assert snapshot.collection_type == ('view' if name == 'read_view' else 'timeseries' if name == 'measurements' else 'collection')
        changed = deepcopy(snapshot.definition); changed.options['validator'] = {}
        with pytest.raises(DatabaseError): tools.plan_collection(session, snapshot, changed)


def test_real_native_inflight_cancel_uses_captured_marker_and_keeps_unknown_effects_honest(session, monkeypatch):
    snapshot, definition = validation(session); plan = tools.plan_collection(session, snapshot, definition)
    original = Database.command; locked = threading.Event(); errors = []
    def pause(db, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('collMod') == 'docs' and 'validator' in command:
            original(session.connection.admin, {'fsync': 1, 'lock': True}); locked.set()
        return original(db, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', pause)
    def apply():
        try: tools.apply_plan(session, plan)
        except Exception as error: errors.append(error)
    worker = threading.Thread(target=apply); worker.start()
    try:
        assert locked.wait(5)
        deadline = time.monotonic() + 5
        while True:
            marker = session.cancellation_marker()
            rows = list(session._control.admin.aggregate([{'$currentOp': {'allUsers': False}},
                {'$match': {'command.comment': marker, 'command.collMod': 'docs', 'command.validator': {'$exists': True}}}]))
            if rows: break
            assert time.monotonic() < deadline
            time.sleep(.02)
        session.cancel(expected_comment=marker)
        worker.join(5)
    finally:
        if locked.is_set(): original(session.connection.admin, {'fsyncUnlock': 1})
        worker.join(10)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], tools.CollectionApplyError)
    assert errors[0].outcome.cancelled and errors[0].outcome.status != 'acknowledged'
    actual = tools.load_collection(session, snapshot.schema, 'docs')
    assert 'validator' not in actual.definition.options
    session.connection[snapshot.schema].docs.insert_one({'n': -1})


def test_real_server_permission_denial_cannot_change_the_collection(session):
    from opennavicat.mongodb_session import MongoSession
    admin = session.connection.admin
    username = 'collection_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        snapshot, definition = draft(restricted)
        plan = tools.plan_collection(restricted, snapshot, definition)
        with pytest.raises(tools.CollectionApplyError) as error: tools.apply_plan(restricted, plan)
        assert error.value.outcome.status == 'server_error' and error.value.outcome.reply['code'] == 13
        existing, changed = validation(restricted)
        with pytest.raises(tools.CollectionApplyError) as denied:
            tools.apply_plan(restricted, tools.plan_collection(restricted, existing, changed))
        assert denied.value.outcome.status == 'not_started'
        assert 'created' not in session.connection[session.config.database].list_collection_names()
        assert 'validator' not in tools.load_collection(session, snapshot.schema, 'docs').definition.options
    finally:
        restricted.close(); admin.command({'dropUser': username})
