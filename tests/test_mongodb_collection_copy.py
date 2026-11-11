"""Staged ordinary MongoDB collection copy against fresh exact-owned Mongo."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from bson import BSON
from pymongo.database import Database
from pymongo.errors import OperationFailure

from opennavicat import mongodb_collection_copy as tools
from opennavicat import mongodb_collections as collections
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoConflictError
from test_mongodb_session import mongodb_service, session


def source(session):
    db = session.connection[session.config.database]
    db.drop_collection('copy_source')
    db.create_collection('copy_source', validator={'n': {'$gte': 0}}, collation={'locale': 'en', 'strength': 2})
    db.copy_source.insert_many([{'_id': 1, 'n': 1}, {'_id': 2, 'n': 2}, {'_id': 3, 'n': 3}])
    db.copy_source.create_index([('n', -1)], name='n_desc', unique=False, sparse=False)
    db.copy_source.create_index([('n', 1), ('_id', 1)], name='n_id')
    return collections.load_collection(session, session.config.database, 'copy_source')


def test_real_copy_structure_only_preserves_options_and_indexes_without_documents(session):
    snapshot = source(session)
    db = session.connection[session.config.database]
    db.drop_collection('copy_structure')
    plan = tools.plan_copy(session, snapshot, 'copy_structure', 'structure')
    assert plan.mode == 'structure' and plan.target_name == 'copy_structure'
    assert plan.staging_name.startswith('__opennavicat_copy_')
    assert 'dropTarget' in plan.command_script and '"dropTarget": false' in plan.command_script
    result = tools.apply_copy(session, plan)
    assert result.outcome.status == 'acknowledged' and result.outcome.staging_name is None
    assert result.snapshot.definition.options == snapshot.definition.options
    assert result.snapshot.namespace_uuid != snapshot.namespace_uuid
    assert db.copy_structure.count_documents({}) == 0
    target_indexes = db.copy_structure.index_information()
    assert set(target_indexes) == {'_id_', 'n_desc', 'n_id'}


def test_real_copy_data_preserves_documents_options_indexes_and_validator(session):
    snapshot = source(session)
    db = session.connection[session.config.database]
    db.drop_collection('copy_data')
    plan = tools.plan_copy(session, snapshot, 'copy_data', 'data')
    assert any('$out' in str(command.get('pipeline', '')) for command in plan.preview_commands)
    result = tools.apply_copy(session, plan)
    assert result.outcome.copied_documents == 3
    assert result.snapshot.definition.options == snapshot.definition.options
    assert db.copy_data.find_one({'_id': 1}) == {'_id': 1, 'n': 1}
    assert set(db.copy_data.index_information()) == {'_id_', 'n_desc', 'n_id'}
    with pytest.raises(OperationFailure):
        db.copy_data.insert_one({'n': -1})


@pytest.mark.parametrize('mode,count', [('structure', 0), ('data', 2)])
def test_real_copy_preserves_collection_storage_index_defaults_and_override(session, mode, count):
    db = session.connection[session.config.database]
    options = {
        'storageEngine': {'wiredTiger': {'configString': 'block_compressor=zlib'}},
        'indexOptionDefaults': {'storageEngine': {'wiredTiger': {'configString': 'prefix_compression=false'}}},
    }
    db.create_collection('storage_source', **options)
    db.storage_source.insert_many([{'_id': 1, 'n': 1, 'm': 1}, {'_id': 2, 'n': 2, 'm': 2}])
    db.storage_source.create_index('n', name='default_storage')
    db.storage_source.create_index('m', name='override_storage',
        storageEngine={'wiredTiger': {'configString': 'prefix_compression=true'}})
    snapshot = collections.load_collection(session, session.config.database, 'storage_source')
    plan = tools.plan_copy(session, snapshot, 'storage_copy', mode)
    result = tools.apply_copy(session, plan)
    assert result.outcome.status == 'acknowledged' and result.outcome.staging_name is None
    assert result.snapshot.namespace_uuid != snapshot.namespace_uuid
    assert result.snapshot.definition.options == options
    assert db.storage_copy.count_documents({}) == count
    assert db.storage_source.count_documents({}) == 2
    stats = db.command({'collStats': 'storage_copy'})
    assert 'block_compressor=zlib' in stats['wiredTiger']['creationString']
    assert 'prefix_compression=false' in stats['indexDetails']['default_storage']['creationString']
    assert 'prefix_compression=true' in stats['indexDetails']['override_storage']['creationString']
    db.storage_copy.create_index('_id_new', name='subsequent_default')
    assert 'prefix_compression=false' in db.command({'collStats': 'storage_copy'})['indexDetails']['subsequent_default']['creationString']
    if mode == 'data':
        assert [BSON.encode(value) for value in db.storage_copy.find().sort('_id')] == [
            BSON.encode(value) for value in db.storage_source.find().sort('_id')]


def test_real_copy_rejects_existing_target_and_stale_source(session, monkeypatch):
    snapshot = source(session)
    db = session.connection[session.config.database]
    db.copy_target.insert_one({'target': True})
    with pytest.raises(MongoConflictError, match='target collection name already exists'):
        tools.plan_copy(session, snapshot, 'copy_target', 'structure')
    db.drop_collection('copy_target')

    plan = tools.plan_copy(session, snapshot, 'copy_target', 'structure')
    db.drop_collection('copy_source')
    db.copy_source.insert_one({'replacement': True})
    with pytest.raises(tools.CollectionApplyError) as error:
        tools.apply_copy(session, plan)
    assert error.value.outcome.status == 'not_started'
    assert 'copy_target' not in db.list_collection_names()


def test_real_copy_permission_denial_is_explicit(session):
    from opennavicat.mongodb_session import MongoSession
    source(session)
    admin = session.connection.admin
    username = 'copy_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        snapshot = collections.load_collection(restricted, session.config.database, 'copy_source')
        plan = tools.plan_copy(restricted, snapshot, 'copy_denied', 'structure')
        with pytest.raises(tools.CollectionApplyError) as error:
            tools.apply_copy(restricted, plan)
        assert error.value.outcome.status == 'server_error'
        assert error.value.outcome.reply['code'] == 13
        assert 'copy_denied' not in session.connection[session.config.database].list_collection_names()
    finally:
        restricted.close(); admin.command({'dropUser': username})


def test_real_copy_failure_after_staging_creation_retains_staging_and_never_target(session, monkeypatch):
    snapshot = source(session)
    db = session.connection[session.config.database]
    db.drop_collection('copy_fail')
    plan = tools.plan_copy(session, snapshot, 'copy_fail', 'structure')
    original = Database.command
    def fail_indexes(database, command, *args, **kwargs):
        if isinstance(command, dict) and command.get('createIndexes') == plan.staging_name:
            raise OperationFailure('synthetic index copy failure', code=999, details={'code': 999})
        return original(database, command, *args, **kwargs)
    monkeypatch.setattr(Database, 'command', fail_indexes)
    with pytest.raises(tools.CollectionApplyError) as error:
        tools.apply_copy(session, plan)
    assert error.value.outcome.staging_name == plan.staging_name
    assert plan.staging_name in db.list_collection_names()
    assert 'copy_fail' not in db.list_collection_names()
    assert 'staging' in str(error.value).lower()
