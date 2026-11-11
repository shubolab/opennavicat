"""Time-series DDL against only a fresh exact-owned MongoDB fixture."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pymongo.errors import OperationFailure

from opennavicat import mongodb_timeseries as tools
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoConflictError
from test_mongodb_session import mongodb_service, session


def draft(session, name='measurements', **values):
    snapshot = tools.new_time_series(session, session.config.database)
    definition = tools.TimeSeriesDefinition(name=name, time_field='time', **values)
    return snapshot, definition


@pytest.mark.parametrize('definition', [
    tools.TimeSeriesDefinition('', 'time'),
    tools.TimeSeriesDefinition('system.ts', 'time'),
    tools.TimeSeriesDefinition('ts', ''),
    tools.TimeSeriesDefinition('ts', '_id'),
    tools.TimeSeriesDefinition('ts', 'time', 'time'),
    tools.TimeSeriesDefinition('ts', 'time', 'meta', granularity='days'),
    tools.TimeSeriesDefinition('ts', 'time', granularity='minutes', bucket_rounding_seconds=60),
    tools.TimeSeriesDefinition('ts', 'time', granularity='', bucket_rounding_seconds=60),
    tools.TimeSeriesDefinition('ts', 'time', granularity='', bucket_max_span_seconds=60),
    tools.TimeSeriesDefinition('ts', 'time', granularity='', bucket_rounding_seconds=60, bucket_max_span_seconds=120),
    tools.TimeSeriesDefinition('ts', 'time', expire_after_seconds=0),
])
def test_invalid_timeseries_definitions_are_rejected_without_io(definition):
    with pytest.raises(DatabaseError):
        tools.validate_definition(definition)


def test_real_create_granularity_timeseries_and_expiry(session):
    db = session.connection[session.config.database]
    db.drop_collection('ts_granularity')
    snapshot, definition = draft(session, 'ts_granularity', meta_field='meta', granularity='minutes', expire_after_seconds=3600)
    plan = tools.plan_time_series(session, snapshot, definition)
    assert plan.changed and plan.commands[0]['create'] == 'ts_granularity'
    assert plan.commands[0]['timeseries'] == {
        'timeField': 'time', 'metaField': 'meta', 'granularity': 'minutes'}
    result = tools.apply_plan(session, plan)
    assert result.outcome.status == 'acknowledged' and result.outcome.created_by_this_plan is None
    assert result.snapshot.kind == 'mongo_timeseries'
    assert result.snapshot.definition == definition
    db.ts_granularity.insert_one({'time': datetime(2026, 1, 1, tzinfo=timezone.utc), 'meta': 'a', 'value': 1})
    assert db.ts_granularity.count_documents({}) == 1
    spec = db.command('listCollections', filter={'name': 'ts_granularity'})['cursor']['firstBatch'][0]
    assert spec['type'] == 'timeseries'
    assert spec['options']['timeseries']['granularity'] == 'minutes'
    assert spec['options']['expireAfterSeconds'] == 3600


def test_real_create_custom_bucketing_and_validate_positive_equal_seconds(session):
    db = session.connection[session.config.database]
    db.drop_collection('ts_custom')
    snapshot, definition = draft(session, 'ts_custom', granularity='',
        bucket_rounding_seconds=120, bucket_max_span_seconds=120, expire_after_seconds=60)
    result = tools.apply_plan(session, tools.plan_time_series(session, snapshot, definition))
    assert result.snapshot.definition == definition
    spec = db.command('listCollections', filter={'name': 'ts_custom'})['cursor']['firstBatch'][0]
    assert spec['options']['timeseries']['bucketRoundingSeconds'] == 120
    assert spec['options']['timeseries']['bucketMaxSpanSeconds'] == 120
    assert 'granularity' not in spec['options']['timeseries']


def test_real_existing_collmod_splits_bucketing_and_expire_commands(session):
    db = session.connection[session.config.database]
    db.drop_collection('ts_modify')
    snapshot, definition = draft(session, 'ts_modify', granularity='minutes', expire_after_seconds=3600)
    saved = tools.apply_plan(session, tools.plan_time_series(session, snapshot, definition)).snapshot
    changed = deepcopy(saved.definition)
    changed.granularity = 'hours'
    changed.expire_after_seconds = 7200
    plan = tools.plan_time_series(session, saved, changed)
    assert len(plan.commands) == 2
    assert plan.commands[0]['timeseries'] == {'granularity': 'hours'}
    assert 'expireAfterSeconds' not in plan.commands[0]
    assert plan.commands[1]['expireAfterSeconds'] == 7200 and 'timeseries' not in plan.commands[1]
    assert any('not transactional' in warning for warning in plan.warnings)
    updated = tools.apply_plan(session, plan).snapshot
    assert updated.definition.granularity == 'hours'
    assert updated.definition.expire_after_seconds == 7200
    off = deepcopy(updated.definition); off.expire_after_seconds = None
    off_plan = tools.plan_time_series(session, updated, off)
    assert off_plan.commands == [{'collMod': 'ts_modify',
        'expireAfterSeconds': 'off', 'writeConcern': {'w': 1}}]
    final = tools.apply_plan(session, off_plan).snapshot
    assert final.definition.expire_after_seconds is None


def test_real_timeseries_rejects_immutable_fields_and_stale_uuid(session):
    db = session.connection[session.config.database]
    db.drop_collection('ts_stale')
    snapshot, definition = draft(session, 'ts_stale', granularity='seconds')
    saved = tools.apply_plan(session, tools.plan_time_series(session, snapshot, definition)).snapshot
    immutable = deepcopy(saved.definition); immutable.time_field = 'other_time'
    with pytest.raises(DatabaseError, match='immutable'):
        tools.plan_time_series(session, saved, immutable)
    changed = deepcopy(saved.definition); changed.expire_after_seconds = 120
    plan = tools.plan_time_series(session, saved, changed)
    db.drop_collection('ts_stale')
    db.create_collection('ts_stale', timeseries={'timeField': 'time', 'granularity': 'seconds'})
    with pytest.raises(tools.CollectionApplyError) as error:
        tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'not_started'


def test_real_timeseries_permission_denial_is_explicit(session):
    from opennavicat.mongodb_session import MongoSession
    admin = session.connection.admin
    username = 'timeseries_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        # Create a readable source with the owning session, then attempt collMod with read permission only.
        db = session.connection[session.config.database]
        db.drop_collection('ts_permission')
        owner_snapshot, owner_definition = draft(session, 'ts_permission', granularity='minutes')
        owner_snapshot = tools.apply_plan(session, tools.plan_time_series(session, owner_snapshot, owner_definition)).snapshot
        read_snapshot = tools.load_time_series(restricted, session.config.database, 'ts_permission')
        changed = deepcopy(read_snapshot.definition); changed.expire_after_seconds = 600
        plan = tools.plan_time_series(restricted, read_snapshot, changed)
        with pytest.raises(tools.CollectionApplyError) as error:
            tools.apply_plan(restricted, plan)
        assert error.value.outcome.status == 'server_error'
        assert error.value.outcome.reply['code'] == 13
        assert tools.load_time_series(session, session.config.database, 'ts_permission').definition.expire_after_seconds is None
    finally:
        restricted.close(); admin.command({'dropUser': username})
