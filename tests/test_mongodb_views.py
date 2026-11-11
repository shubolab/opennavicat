"""MongoDB view DDL against fresh exact-owned standalone service."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import threading

import pytest

from opennavicat import mongodb_views as tools
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoConflictError
from test_mongodb_session import mongodb_service, session


@pytest.fixture
def source(session):
    db = session.connection[session.config.database]
    db.drop_collection('view_source')
    db.view_source.insert_many([{'_id': i, 'n': i} for i in range(5)])
    return db


def draft(session, name='view_one', source='view_source', pipeline=None):
    snapshot = tools.new_view(session, session.config.database)
    return snapshot, tools.ViewDefinition(name, source, pipeline or [])


@pytest.mark.parametrize('definition', [
    tools.ViewDefinition('', 'view_source', []),
    tools.ViewDefinition('system.v', 'view_source', []),
    tools.ViewDefinition('v', '', []),
    tools.ViewDefinition('v', 'view_source', {}),
    tools.ViewDefinition('v', 'view_source', [1]),
    tools.ViewDefinition('v', 'view_source', [{'$match': {'n': {'$gte': 0}}}] * 201),
])
def test_invalid_view_definitions_are_rejected(definition, session):
    with pytest.raises(DatabaseError):
        tools.validate_definition(session, definition)


def test_real_create_and_modify_view_pipeline(source, session):
    db = source
    db.drop_collection('view_one')
    snapshot, definition = draft(session, pipeline=[{'$match': {'n': {'$gte': 2}}}])
    plan = tools.plan_view(session, snapshot, definition)
    assert plan.commands[0]['create'] == 'view_one' and 'collectionUUID' not in plan.commands[0]
    saved = tools.apply_plan(session, plan).snapshot
    assert saved.exists and saved.definition == definition
    assert [row['_id'] for row in db.view_one.find().sort('_id', 1)] == [2, 3, 4]
    changed = deepcopy(saved.definition); changed.pipeline = [{'$match': {'n': 4}}]
    updated = tools.apply_plan(session, tools.plan_view(session, saved, changed)).snapshot
    assert updated.definition == changed
    assert [row['_id'] for row in db.view_one.find()] == [4]


def test_real_view_rejects_existing_target_and_stale_definition(source, session):
    db = source
    db.drop_collection('view_target')
    db.view_target.insert_one({'target': True})
    snapshot, definition = draft(session, 'view_target')
    with pytest.raises(MongoConflictError, match='already exists'):
        tools.plan_view(session, snapshot, definition)
    db.drop_collection('view_target')

    snapshot, definition = draft(session, 'view_stale', pipeline=[{'$match': {'n': 1}}])
    saved = tools.apply_plan(session, tools.plan_view(session, snapshot, definition)).snapshot
    changed = deepcopy(saved.definition); changed.pipeline = [{'$match': {'n': 2}}]
    plan = tools.plan_view(session, saved, changed)
    db.drop_collection('view_stale'); db.create_collection('view_stale', viewOn='view_source', pipeline=[{'$match': {'n': 0}}])
    with pytest.raises(tools.CollectionApplyError) as error:
        tools.apply_plan(session, plan)
    assert error.value.outcome.status == 'not_started'
    assert [row['_id'] for row in db.view_stale.find()] == [0]


def test_view_with_ambiguous_extended_json_pipeline_is_read_only(source, session):
    db = source
    db.command({'create': 'ambiguous_view', 'viewOn': 'view_source',
                'pipeline': [{'$replaceRoot': {'newRoot': {'$literal': {'$date': 'literal'}}}}]})
    snapshot = tools.load_view(session, session.config.database, 'ambiguous_view')
    assert snapshot.read_only
    assert any('Canonical Extended JSON' in issue for issue in snapshot.issues)


def test_real_view_missing_source_and_permission_denial(source, session):
    snapshot, definition = draft(session, 'view_missing', 'missing_source')
    with pytest.raises(MongoConflictError, match='source namespace'):
        tools.plan_view(session, snapshot, definition)
    from opennavicat.mongodb_session import MongoSession
    db = source
    db.drop_collection('view_owned')
    owner_snapshot, owner_definition = draft(session, 'view_owned', pipeline=[{'$match': {'n': 1}}])
    tools.apply_plan(session, tools.plan_view(session, owner_snapshot, owner_definition))
    admin = session.connection.admin
    username = 'view_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        saved = tools.load_view(restricted, session.config.database, 'view_owned')
        changed = deepcopy(saved.definition); changed.pipeline = []
        plan = tools.plan_view(restricted, saved, changed)
        with pytest.raises(tools.CollectionApplyError) as error:
            tools.apply_plan(restricted, plan)
        assert error.value.outcome.status == 'server_error' and error.value.outcome.reply['code'] == 13
    finally:
        restricted.close(); admin.command({'dropUser': username})


def test_view_cancel_before_io(source, session):
    snapshot, definition = draft(session, 'view_cancel')
    plan = tools.plan_view(session, snapshot, definition)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(tools.CollectionApplyError) as error:
        tools.apply_plan(session, plan, cancelled=cancelled.is_set)
    assert error.value.outcome.cancelled and error.value.outcome.status == 'not_started'
    assert 'view_cancel' not in source.list_collection_names()
