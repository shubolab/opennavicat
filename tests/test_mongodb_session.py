"""Exact BSON and real MongoDB behavior, using only fixture-owned Docker servers.

OPENNAVICAT_TEST_MONGODB=1 creates a new isolated server. An external endpoint or
an existing user's database is never accepted by this backend test fixture.
"""
from __future__ import annotations

import contextlib
import copy
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from bson import BSON, Binary, Code, DBRef, Decimal128, Int64, MaxKey, MinKey, ObjectId, Regex, Timestamp
from bson.datetime_ms import DatetimeMS
from pymongo import MongoClient

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_session import (
    BSON_CODEC, MongoConflictError, MongoDocument, MongoSession,
    canonical_dumps, canonical_loads,
)


RICH_VALUES = [ObjectId(), Int64(1), Int64(9007199254740993), Binary(bytes(range(256)), 128),
    Decimal128('1.00'), Decimal128('-0.00'), Decimal128('1E-6176'), DatetimeMS(2**63-1),
    Timestamp(123, 4), Regex('a.*b', 'ims'), Code('return 1'), DBRef('docs', Int64(5), database='example'),
    MinKey(), MaxKey(), datetime(2026, 1, 2, 3, 4, 5, 678000, timezone.utc), 'a\u2028b\u2029c\r\n中文',
    {'nested': [Int64(3), {'$field': Binary(b'\0\xff', 2)}]}, -0.0, float('inf')]


@pytest.mark.parametrize('value', RICH_VALUES)
def test_canonical_editor_preserves_raw_bson_and_editor_unicode(value):
    document = {'_id': 1, 'value': value}
    encoded = BSON.encode(document, codec_options=BSON_CODEC)
    text = canonical_dumps(document)
    assert '\u2028' not in text and '\u2029' not in text
    assert BSON.encode(canonical_loads(text), codec_options=BSON_CODEC) == encoded


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}', '[]', '{"a":NaN}', '{bad'])
def test_invalid_or_duplicate_editor_fields_are_rejected_without_echoing_data(text):
    with pytest.raises(DatabaseError): canonical_loads(text)


def test_dates_cannot_silently_drop_submillisecond_precision():
    with pytest.raises(DatabaseError, match='millisecond'):
        canonical_loads('{"d":{"$date":"2026-01-01T00:00:00.123456Z"}}')


def config_for(port=27017, **values):
    config = ConnectionConfig('owned-test', 'Owned Mongo', driver='mongodb', host='127.0.0.1', port=port,
        database='opennavicat_test', username='fixture', password='fixture-password', ssl_mode='disable', connect_timeout=3)
    for key, value in values.items(): setattr(config, key, value)
    return config


@pytest.mark.parametrize('changes', [dict(mongo_connection_type='replica'), dict(mongo_srv=True),
    dict(mongo_replica_set='example'), dict(mongo_auth_mechanism='UNKNOWN'), dict(ssl_mode='prefer'),
    dict(host='mongodb://secret@host'),dict(connect_timeout=True),dict(port=-1)])
def test_unaccepted_protocol_configuration_is_rejected_before_network(changes, monkeypatch):
    import opennavicat.mongodb_session as module
    monkeypatch.setattr(module, 'MongoClient', lambda **kw: pytest.fail('Invalid protocol must not open client'))
    with pytest.raises(DatabaseError): MongoSession(config_for(**changes)).connect()


def test_document_repr_does_not_log_contents_or_token():
    value = MongoDocument({'private-value':'not-public'}, b'raw-private', b'token-private')
    assert all(s not in repr(value) for s in ['not-public','raw-private','token-private'])


def _docker(*args):
    return subprocess.run(['docker',*args],check=True,capture_output=True,text=True,timeout=120).stdout.strip()


@pytest.fixture(scope='module')
def mongodb_service():
    if os.environ.get('OPENNAVICAT_TEST_MONGODB') != '1':
        pytest.skip('Set OPENNAVICAT_TEST_MONGODB=1 for a new fixture-owned disposable MongoDB server')
    if not shutil.which('docker'):pytest.skip('Disposable MongoDB integration needs Docker')
    name='opennavicat-mongo-owned-'+uuid.uuid4().hex[:12]
    container=_docker('run','--detach','--rm','--name',name,'--label','opennavicat.test=true',
        '--label','opennavicat.task=mongodb-adapter-tests','--publish','127.0.0.1::27017',
        '-e','MONGO_INITDB_ROOT_USERNAME=fixture','-e','MONGO_INITDB_ROOT_PASSWORD=fixture-password','mongo:8.0','--bind_ip_all')
    try:
        port=int(_docker('port',container,'27017/tcp').rsplit(':',1)[1]);config=config_for(port)
        deadline=time.monotonic()+30
        while True:
            probe=MongoSession(config)
            try:probe.connect();break
            except DatabaseError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
            finally:probe.close()
        yield config
    finally:
        subprocess.run(['docker','rm','--force',container],capture_output=True,timeout=30)


@pytest.fixture
def session(mongodb_service):
    config=replace(mongodb_service,database='opennavicat_'+uuid.uuid4().hex)
    session=MongoSession(config);session.connect()
    session.connection[config.database].create_collection('docs')
    try:yield session
    finally:
        session.connection.drop_database(config.database)
        session.close()


def test_real_objects_page_raw_bson_typed_replace_delete_and_insert(session):
    database=session.config.database
    col=session.connection[database].docs
    originals=[{'_id':i,'name':f'row{i}','nested':{'types':[Int64(1),Decimal128('1.00'),Binary(b'\0\xff',128)]}} for i in range(4)]
    col.insert_many(originals)
    page=session.fetch_documents(database,'docs',sort=[('_id',1)],limit=2)
    assert page.has_more and page.next_offset==2 and page.total is None
    assert [d.document['_id'] for d in page.documents]==[0,1]
    assert page.documents[0].raw_bson==BSON.encode(originals[0],codec_options=BSON_CODEC)
    assert '<BSON'not in page.query_text and 'EJSON.parse' in page.query_text
    nextpage=session.fetch_documents(database,'docs',sort=[('_id',1)],offset=page.next_offset,limit=2)
    assert not nextpage.has_more and len(nextpage.documents)==2
    before=copy.deepcopy(page.documents[0].document)
    after=session.replace_document(database,'docs',{**before,'new':'saved'},expected=page.documents[0])
    assert after.document['new']=='saved' and page.documents[0].document==before
    session.delete_document(database,'docs',expected=after)
    assert col.find_one({'_id':0})is None
    draft={'name':'new','long':Int64(9007199254740995)}
    inserted=session.insert_document(database,'docs',draft)
    assert '_id'not in draft and isinstance(inserted.document['long'],Int64)
    assert inserted.document['long']==9007199254740995


@pytest.mark.parametrize('old,new',[(1,Int64(1)),(Int64(1),1),(1,1.0),
    (Decimal128('1.00'),Decimal128('1.0')),(-0.0,0.0),(0.0,-0.0),
    ({'a':Int64(1)},{'a':1}),([Int64(1)],[1]),({'x':1,'y':2},{'y':2,'x':1})])
def test_real_numeric_representation_nested_type_and_key_order_changes_conflict(session,old,new):
    db=session.config.database;col=session.connection[db].docs
    col.insert_one({'_id':1,'value':old});snapshot=session.fetch_documents(db,'docs').documents[0]
    assert snapshot.editable,snapshot.edit_reason
    col.replace_one({'_id':1},{'_id':1,'value':new})
    with pytest.raises(MongoConflictError):session.replace_document(db,'docs',{**snapshot.document,'edit':True},expected=snapshot)
    with pytest.raises(MongoConflictError):session.delete_document(db,'docs',expected=snapshot)
    actual=col.find_one({'_id':1})
    assert BSON.encode(actual,codec_options=BSON_CODEC)==BSON.encode({'_id':1,'value':new},codec_options=BSON_CODEC)


def test_real_all_supported_rich_types_safe_cas_even_with_dollar_and_dot_names(session):
    db=session.config.database
    for i,value in enumerate(RICH_VALUES):
        snapshot=session.insert_document(db,'docs',{'_id':i,'value':value,'$literal':{'a.b':[1,Int64(3)]}})
        assert snapshot.editable,(type(value),snapshot.edit_reason)
        updated=session.replace_document(db,'docs',{**snapshot.document,'saved':True},snapshot)
        assert updated.document['saved']
        session.delete_document(db,'docs',updated)


def test_real_projection_readonly_and_lost_ejson_wrapper_cannot_be_overwritten(session):
    db=session.config.database;col=session.connection[db].docs
    col.insert_one({'_id':1,'secret':2,'name':'keep'})
    page=session.fetch_documents(db,'docs',projection={'secret':0});doc=page.documents[0]
    assert not doc.editable and 'Projected'in doc.edit_reason
    with pytest.raises(DatabaseError):session.replace_document(db,'docs',doc.document,expected=doc)
    col.insert_one({'_id':2,'ambiguous':{'$date':'literal-user-field'}})
    page=session.fetch_documents(db,'docs',filter_doc={'_id':2});assert not page.documents[0].editable
    assert col.find_one({'_id':2})['ambiguous']=={'$date':'literal-user-field'}


def test_real_stale_collection_session_and_tampered_preimages_rejected(session):
    db=session.config.database;col=session.connection[db].docs
    col.insert_one({'_id':1,'x':1});snapshot=session.fetch_documents(db,'docs').documents[0]
    col.drop();session.connection[db].create_collection('docs');col.insert_one({'_id':1,'x':1})
    with pytest.raises(MongoConflictError):session.replace_document(db,'docs',{'_id':1,'x':2},snapshot)
    fresh=session.fetch_documents(db,'docs').documents[0]
    tampered=replace(fresh,raw_bson=BSON.encode({'_id':1,'x':999}))
    with pytest.raises(MongoConflictError):session.delete_document(db,'docs',tampered)
    session.close();session.connect()
    with pytest.raises(MongoConflictError):session.delete_document(db,'docs',fresh)
    assert session.connection[db].docs.find_one({'_id':1})['x']==1


def test_real_readonly_and_id_immutable(session):
    db=session.config.database;col=session.connection[db].docs
    col.insert_one({'_id':1,'x':1});snapshot=session.fetch_documents(db,'docs').documents[0]
    with pytest.raises(DatabaseError,match='_id'):session.replace_document(db,'docs',{'_id':Int64(1),'x':2},snapshot)
    session.config.read_only=True
    assert not session.fetch_documents(db,'docs').documents[0].editable
    with pytest.raises(DatabaseError,match='read-only'):session.delete_document(db,'docs',snapshot)
    with pytest.raises(DatabaseError,match='read-only'):session.insert_document(db,'docs',{'x':3})
    session.config.read_only=False


def test_real_authentication_failure_is_typed_and_cleanup_allows_reconnect(mongodb_service):
    bad=MongoSession(replace(mongodb_service,password='wrong-owned-password'))
    with pytest.raises(DatabaseError) as error:bad.connect()
    assert error.value.authentication_required and bad.connection is None
    bad.config.password=mongodb_service.password
    try:bad.connect();assert bad.schemas()
    finally:bad.close()


def test_relational_and_unaccepted_shell_facades_fail_explicitly():
    session=MongoSession(config_for())
    with pytest.raises(DatabaseError,match='Native command'):
        session.execute('db.docs.find({})')
    for name in ['columns','foreign_keys','fetch_table','update_row','delete_row','import_rows','backup_to']:
        with pytest.raises(DatabaseError,match='document APIs'):getattr(session,name)('ignored')


def test_duplicate_raw_bson_fields_are_readonly_not_silently_reserialized():
    import struct
    payload=BSON.encode({'_id':1,'x':1})[4:-1]+BSON.encode({'x':2})[4:-1]
    raw=struct.pack('<i',len(payload)+5)+payload+b'\0'
    session=MongoSession(config_for());session._server_major=8
    result=session._snapshot('example','docs',raw,namespace_uuid=Binary(bytes(16),4))
    assert result.raw_bson==raw and not result.editable
    assert 'losslessly'in result.edit_reason


@pytest.mark.parametrize('operation',['replace','delete','insert'])
def test_real_namespace_recreated_after_preflight_is_rejected_by_server_uuid(session,monkeypatch,operation):
    from pymongo.synchronous.database import Database
    db=session.config.database;col=session.connection[db].docs
    original={'_id':1,'x':1};col.insert_one(original)
    snapshot=session.fetch_documents(db,'docs').documents[0]
    real_command=Database.command;fired=False
    def race(self,command,*args,**kwargs):
        nonlocal fired
        if self.name==db and isinstance(command,dict) and set(command)&{'update','delete','insert'} and not fired:
            fired=True
            real_command(self,{'drop':'docs'})
            real_command(self,{'create':'docs'})
            real_command(self,{'insert':'docs','documents':[original]})
        return real_command(self,command,*args,**kwargs)
    monkeypatch.setattr(Database,'command',race)
    with pytest.raises(MongoConflictError,match='collection was replaced'):
        if operation=='replace':session.replace_document(db,'docs',{'_id':1,'x':2},snapshot)
        elif operation=='delete':session.delete_document(db,'docs',snapshot)
        else:session.insert_document(db,'docs',{'_id':2,'x':2})
    assert fired and list(session.connection[db].docs.find({}))==[original]


def test_real_mutating_display_dict_does_not_change_sealed_preimage(session):
    db=session.config.database;session.connection[db].docs.insert_one({'_id':1,'x':1})
    expected=session.fetch_documents(db,'docs').documents[0]
    expected.document['x']=999
    saved=session.replace_document(db,'docs',{'_id':1,'x':2},expected)
    assert saved.document['x']==2


def test_real_profile_endpoint_change_invalidates_document_token(session):
    db=session.config.database;session.connection[db].docs.insert_one({'_id':1,'x':1})
    expected=session.fetch_documents(db,'docs').documents[0]
    before=session.config.host;session.config.host='other.invalid'
    try:
        with pytest.raises(MongoConflictError):session.delete_document(db,'docs',expected)
    finally:session.config.host=before
    assert session.connection[db].docs.find_one({'_id':1})


def test_real_native_metadata_preserves_index_order_partial_collation_and_collection_options(session):
    db=session.config.database;col=session.connection[db].docs
    col.create_index([('x',-1),('name',1)],name='compound',unique=True,partialFilterExpression={'x':{'$exists':True}},collation={'locale':'en','strength':2})
    result={x['name']:x for x in session.indexes(db,'docs')}
    assert result['_id_']['primary'] and result['_id_']['unique']
    assert result['compound']['keys']==[('x',-1),('name',1)]
    assert result['compound']['options']['partialFilterExpression']=={'x':{'$exists':True}}
    assert result['compound']['options']['collation']['strength']==2
    assert session.collection_stats(db,'docs')['nindexes']==2
    assert session.collection_info(db,'docs')['info']['uuid']


def test_real_aggregation_is_typed_paged_readonly_and_out_merge_rejected_before_write(session):
    db=session.config.database;col=session.connection[db].docs
    col.insert_many([{'_id':i,'group':i%2,'value':Int64(9007199254740993+i)} for i in range(4)])
    pipeline=[{'$group':{'_id':'$group','maximum':{'$max':'$value'}}},{'$sort':{'_id':1}}]
    page=session.aggregate_documents(db,'docs',pipeline,limit=1)
    assert page.has_more and page.next_offset==1
    assert isinstance(page.documents[0].document['maximum'],Int64)
    assert not page.documents[0].editable and 'Aggregation' in page.documents[0].edit_reason
    assert not session.aggregate_documents(db,'docs',pipeline,offset=1,limit=1).has_more
    for bad in [[{'$out':'must_not_exist'}],[{'$merge':'must_not_exist'}],
                [{'$facet':{'nested':[{'$out':'must_not_exist'}]}}]]:
        with pytest.raises(DatabaseError,match='read-only'):session.aggregate_documents(db,'docs',bad)
    assert 'must_not_exist'not in session.connection[db].list_collection_names()


def test_real_page_byte_limit_keeps_large_values_whole_and_continuation_honest(session,monkeypatch):
    import opennavicat.mongodb_session as module
    db=session.config.database;col=session.connection[db].docs
    col.insert_many([{'_id':i,'large':'x'*10000} for i in range(3)])
    monkeypatch.setattr(module,'_MAX_PAGE_BYTES',15000)
    first=session.fetch_documents(db,'docs',sort=[('_id',1)],limit=10)
    assert len(first.documents)==1 and first.has_more and first.next_offset==1
    assert len(first.documents[0].document['large'])==10000
    nextpage=session.fetch_documents(db,'docs',sort=[('_id',1)],offset=first.next_offset,limit=10)
    assert nextpage.documents[0].document['_id']==1


@pytest.mark.parametrize('mechanism',['PLAIN','GSSAPI','MONGODB-X509'])
def test_unaccepted_advanced_auth_is_not_silently_fallback_or_attempted(mechanism,monkeypatch):
    import opennavicat.mongodb_session as module
    monkeypatch.setattr(module,'MongoClient',lambda **kw:pytest.fail('Unaccepted authentication must not open a client'))
    with pytest.raises(DatabaseError,match='authentication mechanism'):
        MongoSession(config_for(mongo_auth_mechanism=mechanism)).connect()


@pytest.mark.parametrize('mechanism',['DEFAULT','SCRAM-SHA-1','SCRAM-SHA-256'])
def test_password_auth_without_username_cannot_silently_become_unauthenticated(mechanism,monkeypatch):
    import opennavicat.mongodb_session as module
    monkeypatch.setattr(module,'MongoClient',lambda **kw:pytest.fail('Missing auth identity must fail before connect'))
    with pytest.raises(DatabaseError,match='username'):
        MongoSession(config_for(username='',mongo_auth_mechanism=mechanism)).connect()


def test_unaccepted_read_preference_is_not_ignored(monkeypatch):
    import opennavicat.mongodb_session as module
    monkeypatch.setattr(module,'MongoClient',lambda **kw:pytest.fail('Unsupported routing must not connect'))
    with pytest.raises(DatabaseError,match='read preferences'):
        MongoSession(config_for(mongo_read_preference='secondary')).connect()
