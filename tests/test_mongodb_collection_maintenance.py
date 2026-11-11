"""MongoDB collection validation and reindex against fresh owned services."""
from __future__ import annotations

from dataclasses import replace
import threading

import pytest

from opennavicat import mongodb_collection_maintenance as tools
from opennavicat import mongodb_collections as collections
from opennavicat import mongodb_timeseries as timeseries
from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoConflictError
from test_mongodb_session import mongodb_service, session


def ordinary(session):
    db = session.connection[session.config.database]
    db.drop_collection('maintain_docs')
    db.maintain_docs.insert_many([{'_id': i, 'n': i} for i in range(5)])
    db.maintain_docs.create_index([('n', 1)], name='n_1')
    return collections.load_collection(session, session.config.database, 'maintain_docs')


def ts(session):
    db = session.connection[session.config.database]
    db.drop_collection('maintain_ts')
    db.create_collection('maintain_ts', timeseries={'timeField': 'time', 'granularity': 'minutes'})
    return timeseries.load_time_series(session, session.config.database, 'maintain_ts')


@pytest.mark.parametrize('full', [False, True])
def test_real_validate_ordinary_collection_fast_and_full(session, full):
    snapshot = ordinary(session)
    result = tools.validate_collection(session, snapshot, full=full)
    assert result.operation == ('validate-full' if full else 'validate')
    assert result.report['valid'] is True
    assert result.document_count == 5 and result.index_count == 2
    assert result.report['uuid'] == snapshot.namespace_uuid
    assert result.duration_ms >= 0


def test_real_validate_timeseries_uses_bucket_uuid(session):
    snapshot = ts(session)
    result = tools.validate_collection(session, snapshot, full=False)
    assert result.report['valid'] is True
    assert result.source_uuid == snapshot.namespace_uuid
    assert result.report['uuid'] == snapshot.namespace_uuid


def test_real_reindex_preserves_data_indexes_and_uuid(session):
    snapshot = ordinary(session)
    db = session.connection[session.config.database]
    before = db.maintain_docs.index_information()
    result = tools.reindex_collection(session, snapshot)
    assert result.operation == 'reindex' and result.index_count == 2
    assert result.report['ok'] == 1.0
    assert db.maintain_docs.count_documents({}) == 5
    assert db.maintain_docs.index_information() == before
    after = collections.load_collection(session, session.config.database, 'maintain_docs')
    assert after.namespace_uuid == snapshot.namespace_uuid


def test_real_reindex_rejects_timeseries_and_cancelled_before_io(session):
    snapshot = ts(session)
    with pytest.raises(DatabaseError, match='ordinary collection'):
        tools.reindex_collection(session, snapshot)
    ordinary_snapshot = ordinary(session)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(DatabaseError, match='cancelled before'):
        tools.reindex_collection(session, ordinary_snapshot, cancelled=cancelled.is_set)


def test_real_validate_and_reindex_reject_stale_uuid(session):
    snapshot = ordinary(session)
    db = session.connection[session.config.database]
    db.drop_collection('maintain_docs'); db.maintain_docs.insert_one({'replacement': True})
    with pytest.raises(MongoConflictError):
        tools.validate_collection(session, snapshot)
    with pytest.raises(MongoConflictError):
        tools.reindex_collection(session, snapshot)


def test_real_maintenance_permission_denial_is_explicit(session):
    from opennavicat.mongodb_session import MongoSession
    ordinary(session)
    admin = session.connection.admin
    username = 'maintenance_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        snapshot = collections.load_collection(restricted, session.config.database, 'maintain_docs')
        with pytest.raises(DatabaseError, match='code 13'):
            tools.validate_collection(restricted, snapshot)
        with pytest.raises(DatabaseError, match='code 13'):
            tools.reindex_collection(restricted, snapshot)
    finally:
        restricted.close(); admin.command({'dropUser': username})


@pytest.mark.parametrize('force', [False, True])
def test_real_compact_ordinary_collection_preserves_identity_data_and_indexes(session, force):
    snapshot = ordinary(session)
    db = session.connection[session.config.database]
    before_indexes = db.maintain_docs.index_information()
    result = tools.compact_collection(session, snapshot, force=force)
    assert result.operation == ('compact-force' if force else 'compact')
    assert result.report['ok'] == 1.0 and 'bytesFreed' in result.report
    assert db.maintain_docs.count_documents({}) == 5
    assert db.maintain_docs.index_information() == before_indexes
    assert collections.load_collection(session, session.config.database, 'maintain_docs').namespace_uuid == snapshot.namespace_uuid


def test_real_compact_timeseries_and_reject_cancelled(session):
    snapshot = ts(session)
    result = tools.compact_collection(session, snapshot, force=False)
    assert result.operation == 'compact' and result.source_uuid == snapshot.namespace_uuid
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(DatabaseError, match='cancelled before'):
        tools.compact_collection(session, snapshot, cancelled=cancelled.is_set)


def test_real_compact_permission_denial_is_explicit(session):
    from opennavicat.mongodb_session import MongoSession
    ordinary(session)
    admin = session.connection.admin
    username = 'compact_reader'
    admin.command({'createUser': username, 'pwd': 'synthetic-reader-password',
        'roles': [{'role': 'read', 'db': session.config.database}]})
    restricted = MongoSession(replace(session.config, username=username, password='synthetic-reader-password'))
    try:
        restricted.connect()
        snapshot = collections.load_collection(restricted, session.config.database, 'maintain_docs')
        with pytest.raises(DatabaseError, match='code 13'):
            tools.compact_collection(restricted, snapshot)
    finally:
        restricted.close(); admin.command({'dropUser': username})
