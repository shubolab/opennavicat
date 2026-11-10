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


def test_relational_facades_and_unaccepted_javascript_fail_explicitly():
    session=MongoSession(config_for())
    with pytest.raises(DatabaseError):
        session.execute('process.exit()')
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


@pytest.mark.parametrize('fraction', ['0010001', '0000001', '00000000000000001', '1234567'])
def test_date_fraction_source_cannot_be_truncated_before_validation(fraction):
    with pytest.raises(DatabaseError, match='millisecond'):
        canonical_loads('{"x":{"$date":"2026-01-01T00:00:00.'+fraction+'Z"}}')


def test_ssh_pool_context_keeps_original_identity_without_global_changes():
    from opennavicat.mongodb_session import _tunnel_pool_class
    from pymongo.pool_options import PoolOptions
    import ssl
    context = ssl.create_default_context()
    calls = []
    class Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True
        def wrap_socket(self, sock, **kwargs):
            calls.append((sock, kwargs)); return 'wrapped'
    original = Context()
    options = PoolOptions(ssl_context=original)
    pool_type = _tunnel_pool_class('database.example.invalid')
    pool = pool_type(('127.0.0.1', 19999), options)
    try:
        assert pool.opts._ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert pool.opts._ssl_context.check_hostname is True
        assert options._ssl_context is original
        assert context.check_hostname is True
        assert pool.opts._ssl_context.wrap_socket('socket', server_hostname='127.0.0.1', session='cache') == 'wrapped'
        assert calls == [('socket', {'server_hostname':'database.example.invalid', 'session':'cache'})]
    finally:
        pool.close()


def test_ssh_transport_customization_is_valid_in_pinned_real_client():
    from opennavicat.mongodb_session import _tunnel_pool_class
    client = MongoClient(host='127.0.0.1', port=19999, connect=False, tls=True,
                         _pool_class=_tunnel_pool_class('database.example.invalid'))
    try:
        assert client.options.pool_options._ssl_context.check_hostname
        # The actual PyMongo topology must use the same customization for both
        # operation and monitoring sockets, not a test-only substitute.
        operation = client._topology._create_pool_for_server(('127.0.0.1',19999))
        monitor = client._topology._create_pool_for_monitor(('127.0.0.1',19999))
        try:
            for pool in (operation, monitor):
                assert pool.opts._ssl_context._hostname == 'database.example.invalid'
                assert pool.opts._ssl_context.check_hostname
        finally:
            operation.close(); monitor.close()
    finally:
        client.close()


def test_real_gridfs_binary_roundtrip_metadata_cas_and_no_replace(session, tmp_path):
    from gridfs import GridFSBucket
    db=session.config.database; bucket='own_files'
    content=bytes(range(256))*3000+b'\0\xff'
    source=tmp_path/'sample.bin';source.write_bytes(content)
    ticks=[]
    uploaded=session.upload_file(db,bucket,source,metadata={'typed':Int64(1)},progress=lambda done,total:ticks.append((done,total)))
    assert not uploaded.editable and uploaded.document['length']==len(content)
    assert uploaded.document['md5']==__import__('hashlib').md5(content,usedforsecurity=False).hexdigest()
    assert ticks[0]==(0,len(content)) and ticks[-1]==(len(content),len(content))
    assert all(a[0]<=b[0] for a,b in zip(ticks,ticks[1:]))
    assert GridFSBucket(session.connection[db],bucket_name=bucket).open_download_stream(uploaded.document['_id']).read()==content
    listed=session.list_files(db,bucket,limit=1)
    assert len(listed.documents)==1 and not listed.has_more
    target=tmp_path/'download.bin'
    assert session.download_file(db,bucket,uploaded.document['_id'],target,expected=listed.documents[0])==len(content)
    assert target.read_bytes()==content
    target.write_bytes(b'keep-existing')
    with pytest.raises(DatabaseError,match='already exists'):
        session.download_file(db,bucket,uploaded.document['_id'],target,expected=uploaded)
    assert target.read_bytes()==b'keep-existing'
    assert session.download_file(db,bucket,uploaded.document['_id'],target,overwrite=True,expected=uploaded)==len(content)
    assert target.read_bytes()==content
    session.connection[db][bucket+'.files'].update_one({'_id':uploaded.document['_id']},{'$set':{'filename':'peer.bin'}})
    with pytest.raises(MongoConflictError):session.delete_file(db,bucket,expected=uploaded)
    assert session.connection[db][bucket+'.chunks'].count_documents({})==3
    fresh=session.list_files(db,bucket).documents[0]
    session.delete_file(db,bucket,expected=fresh)
    assert session.list_files(db,bucket).documents==[]
    assert session.connection[db][bucket+'.chunks'].count_documents({})==0


def test_real_gridfs_cancel_upload_removes_only_own_chunks_and_download_keeps_destination(session,tmp_path):
    db=session.config.database;bucket='cancel_files'
    source=tmp_path/'source.txt';source.write_bytes(b'a'*(255*1024*3))
    good=session.upload_file(db,bucket,source)
    cancelled=threading.Event()
    def stop(done,total):
        if done:cancelled.set()
    with pytest.raises(DatabaseError,match='cancelled'):
        session.upload_file(db,bucket,source,progress=stop,cancelled=cancelled.is_set)
    assert session.connection[db][bucket+'.files'].count_documents({})==1
    assert session.connection[db][bucket+'.chunks'].count_documents({})==3
    cancelled.clear()
    target=tmp_path/'keep.txt';target.write_bytes(b'original')
    with pytest.raises(DatabaseError,match='cancelled'):
        session.download_file(db,bucket,good.document['_id'],target,overwrite=True,expected=good,
                              progress=stop,cancelled=cancelled.is_set)
    assert target.read_bytes()==b'original'
    assert list(tmp_path.glob('.opennavicat-download-*'))==[]
    # The cancelled callback is consulted before bucket creation or source opens.
    with pytest.raises(DatabaseError,match='cancelled'):
        session.upload_file(db,'never_created',tmp_path/'missing',cancelled=lambda:True)
    assert 'never_created.files'not in session.connection[db].list_collection_names()


def test_real_gridfs_download_failure_or_target_race_never_truncates_existing_file(session,tmp_path):
    db=session.config.database;bucket='race_files'
    source=tmp_path/'source.bin';source.write_bytes(b'b'*(255*1024*2))
    good=session.upload_file(db,bucket,source)
    target=tmp_path/'race.bin'
    def create_target(done,total):
        if done and not target.exists():target.write_bytes(b'other-process')
    with pytest.raises(DatabaseError,match='already exists'):
        session.download_file(db,bucket,good.document['_id'],target,expected=good,progress=create_target)
    assert target.read_bytes()==b'other-process'
    assert list(tmp_path.glob('.opennavicat-download-*'))==[]
    # A corrupted chunk is diagnosed by the true uploaded MD5, before commit.
    session.connection[db][bucket+'.chunks'].update_one({'files_id':good.document['_id'],'n':0},{'$set':{'data':Binary(b'c'*(255*1024))}})
    with pytest.raises(DatabaseError,match='checksum'):
        session.download_file(db,bucket,good.document['_id'],target,overwrite=True,expected=good)
    assert target.read_bytes()==b'other-process'


def test_real_gridfs_read_only_and_stale_namespace_do_not_mutate(session,tmp_path):
    db=session.config.database;bucket='readonly_files'
    source=tmp_path/'a';source.write_bytes(b'one')
    value=session.upload_file(db,bucket,source)
    session.config.read_only=True
    try:
        with pytest.raises(DatabaseError,match='read-only'):session.upload_file(db,bucket,source)
        with pytest.raises(DatabaseError,match='read-only'):session.delete_file(db,bucket,value)
        readonly_snapshot=session.list_files(db,bucket).documents[0]
        target=tmp_path/'read';session.download_file(db,bucket,value.document['_id'],target,expected=readonly_snapshot)
        assert target.read_bytes()==b'one'
    finally:session.config.read_only=False
    session.connection[db][bucket+'.files'].drop();session.connection[db].create_collection(bucket+'.files')
    with pytest.raises(MongoConflictError):session.delete_file(db,bucket,value)


def test_safe_shell_literal_parser_preserves_bson_constructors_and_query_order():
    from opennavicat.mongodb_session import parse_mongo_queries
    commands=parse_mongo_queries('''// own fixture query
        db.getCollection('docs').find({n:Long('9007199254740995'), money:Decimal128('1.00'),
            binary:BinData(128,'AP8='), at:ISODate('2026-01-01T00:00:00.0010000Z'),
            nested:{flag:true, missing:null}, name:/a.*b/im, key:ObjectId('000000000000000000000001'),
            unicode:'\\uD83D\\uDE80'}, {n:1}).sort({n:-1}).limit(4).skip(2);
        db.docs.aggregate([{$match:{status:'ready'}},{$group:{_id:'$status',n:{$sum:1}}}]).limit(3).toArray()
    ''')
    first, second=commands
    assert first['find']=='docs' and first['projection']=={'n':1}
    assert first['sort']=={'n':-1} and first['skip']==2 and first['limit']==4
    values=first['filter']
    assert type(values['n']) is Int64 and values['n']==9007199254740995
    assert values['money']==Decimal128('1.00')
    assert values['binary']==Binary(b'\0\xff',128)
    assert values['at'].microsecond==1000 and values['unicode']=='🚀'
    assert values['key']==ObjectId('000000000000000000000001')
    assert values['name'].flags==Regex('a.*b','im').flags
    assert second['pipeline'][-1]=={'$limit':3}


@pytest.mark.parametrize('text',[
    "db.docs.find({x:process.exit()})", "db.docs.find({x:require('fs')})",
    "db.docs.find({x:(function(){return 1})()})", "db.docs.find({x:1,x:2})",
    "db.docs.find({x:1}); db.docs.find({x:unknown})", "db.docs.find({x:/a/g})",
    "db.docs.find({x:Long('9223372036854775808')})", "db.docs.find({x:Binary.createFromBase64('!',128)})",
    "db.docs.find({x:ISODate('2026-01-01T00:00:00.0010001Z')})",
    "db.docs.find({x:new Date('2026-01-01T00:00:00.0000001Z')})",
    'db.docs.find(EJSON.parse(\'{"x":{"$date":"2026-01-01T00:00:00.00000000000000001Z"}}\'))',
    "db.docs.find({x:'\\uD800'})", "db.docs.find({}).where('arbitrary code')",
])
def test_safe_shell_parser_rejects_code_ambiguous_data_and_lossy_dates(text):
    from opennavicat.mongodb_session import parse_mongo_queries
    with pytest.raises(DatabaseError):parse_mongo_queries(text)


def test_safe_shell_parser_canonical_command_and_negative_single_batch_limit():
    from opennavicat.mongodb_session import parse_mongo_queries
    assert parse_mongo_queries('{"find":"docs","filter":{"x":{"$numberLong":"9007199254740993"}}}')[0]['filter']['x']==Int64(9007199254740993)
    assert parse_mongo_queries('db.docs.find({}).limit(-3)')[0]['singleBatch'] is True
    assert parse_mongo_queries('db.runCommand({ping:1});')[0]=={'ping':1}


def test_real_gridfs_failed_chunk_write_cleans_new_upload_only(session,tmp_path,monkeypatch):
    from pymongo.synchronous.database import Database
    db=session.config.database;bucket='failed_files'
    source=tmp_path/'source';source.write_bytes(b'a'*(255*1024*3))
    original=session.upload_file(db,bucket,source)
    command=Database.command
    fired=[]
    def failing(database,value,*args,**kwargs):
        if (database.name==db and isinstance(value,dict) and value.get('insert')==bucket+'.chunks'
            and value['documents'][0]['n']==1 and not fired):
            fired.append(True)
            raise OSError('owned simulated network interruption')
        return command(database,value,*args,**kwargs)
    monkeypatch.setattr(Database,'command',failing)
    with pytest.raises(DatabaseError,match='network interruption'):
        session.upload_file(db,bucket,source)
    assert fired and session.connection[db][bucket+'.files'].count_documents({})==1
    assert session.connection[db][bucket+'.chunks'].count_documents({})==3
    assert session.list_files(db,bucket).documents[0].raw_bson==original.raw_bson


def test_real_gridfs_empty_file_and_progress_callback_error_cleanup(session,tmp_path):
    db=session.config.database;bucket='empty_files'
    source=tmp_path/'empty';source.write_bytes(b'')
    original=session.upload_file(db,bucket,source)
    assert original.document['length']==0
    target=tmp_path/'out'
    assert session.download_file(db,bucket,original.document['_id'],target,expected=original)==0
    assert target.read_bytes()==b''
    source.write_bytes(b'a'*(255*1024*2))
    def fail(done,total):
        if done:raise RuntimeError('owned callback failure')
    with pytest.raises(DatabaseError,match='callback failure'):session.upload_file(db,bucket,source,progress=fail)
    assert session.connection[db][bucket+'.files'].count_documents({})==1
    assert session.connection[db][bucket+'.chunks'].count_documents({})==0


def test_operation_comment_preserves_typed_user_value_and_nested_lifecycle():
    value=MongoSession(config_for());value.connection=object()
    user={'n':Int64(1),'binary':Binary(b'\0\xff',128),'text':'own'}
    with value._operation(user_comment=user) as comment:
        assert comment['userComment']==user and type(comment['userComment']['n']) is Int64
        user['text']='changed-after-dispatch'
        assert comment['userComment']['text']=='own'
        with value._operation() as nested:
            assert isinstance(nested,str) and value._active_comment==nested
        assert value._active_comment==comment and value._operation_depth==1
    assert value._active_comment is None and value._operation_depth==0
    with pytest.raises(Exception):
        with value._operation(user_comment=object()):pass
    assert value._operation_depth==0 and value._active_comment is None


def test_real_cancel_targets_own_typed_comment_and_finishes_query(session):
    from pymongo import timeout
    db=session.config.database
    session.connection[db].docs.insert_one({'_id':1})
    failures=[];started=threading.Event()
    def query():
        try:
            with session._operation(user_comment={'purpose':'owned-cancel','n':Int64(1)}) as comment:
                started.set()
                list(session.connection[db].docs.find({'$where':'sleep(4000); return true;'},comment=comment,max_time_ms=6000))
        except DatabaseError as exc:failures.append(exc)
    worker=threading.Thread(target=query)
    worker.start();assert started.wait(2)
    try:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            with timeout(1):
                rows=list(session._control.admin.aggregate([{'$currentOp':{'allUsers':False}},
                    {'$match':{'command.comment':session._active_comment}}]))
            if rows:break
            time.sleep(.02)
        assert rows,'Owned long query was not visible to its cancellation channel'
        when=time.monotonic();session.cancel();worker.join(3)
        assert not worker.is_alive() and failures and time.monotonic()-when<3
        assert session._active_comment is None
    finally:
        worker.join(8)
        assert not worker.is_alive()


def test_real_gridfs_bucket_replacement_during_upload_does_not_publish_file(session,tmp_path):
    db=session.config.database;bucket='changed_files'
    source=tmp_path/'source';source.write_bytes(b'own file')
    changed=[]
    def replace_chunks(done,total):
        if done and not changed:
            changed.append(True)
            session.connection[db][bucket+'.chunks'].drop()
            session.connection[db].create_collection(bucket+'.chunks')
            session.connection[db][bucket+'.chunks'].insert_one({'_id':'keep-peer','marker':'new namespace'})
    with pytest.raises(DatabaseError):
        session.upload_file(db,bucket,source,progress=replace_chunks)
    assert changed and session.connection[db][bucket+'.files'].count_documents({})==0
    assert session.connection[db][bucket+'.chunks'].find_one({'_id':'keep-peer'})['marker']=='new namespace'


def test_real_gridfs_lost_publication_ack_cleans_only_new_file_or_retains_complete_uncertain_upload(session,tmp_path,monkeypatch):
    from pymongo.synchronous.database import Database
    db=session.config.database;bucket='ack_files'
    source=tmp_path/'source';source.write_bytes(b'a'*(255*1024*2))
    original=session.upload_file(db,bucket,source)
    command=Database.command
    state={'lost':False,'deny_cleanup':False}
    def uncertain(database,value,*args,**kwargs):
        if database.name==db and isinstance(value,dict):
            if value.get('insert')==bucket+'.files' and not state['lost']:
                result=command(database,value,*args,**kwargs)
                state['lost']=True
                raise OSError('owned lost publication acknowledgement')
            if value.get('delete')==bucket+'.files' and state['deny_cleanup']:
                raise OperationFailure('owned metadata cleanup denied',code=13)
        return command(database,value,*args,**kwargs)
    from pymongo.errors import OperationFailure
    monkeypatch.setattr(Database,'command',uncertain)
    with pytest.raises(DatabaseError,match='lost publication acknowledgement'):
        session.upload_file(db,bucket,source)
    assert session.connection[db][bucket+'.files'].count_documents({})==1
    assert session.connection[db][bucket+'.chunks'].count_documents({})==2
    state.update(lost=False,deny_cleanup=True)
    with pytest.raises(DatabaseError,match='cleanup was not confirmed'):
        session.upload_file(db,bucket,source)
    # Cleanup permission failure must not delete chunks beneath a complete
    # published file whose wire acknowledgement was lost.
    assert session.connection[db][bucket+'.files'].count_documents({})==2
    assert session.connection[db][bucket+'.chunks'].count_documents({})==4
    from gridfs import GridFSBucket
    for row in session.list_files(db,bucket).documents:
        assert GridFSBucket(session.connection[db],bucket_name=bucket).open_download_stream(row.document['_id']).read()==source.read_bytes()
    assert session.connection[db][bucket+'.files'].find_one({'_id':original.document['_id']})


@pytest.mark.parametrize('token',['NaN','Infinity','-Infinity'])
def test_ejson_parse_constructor_obeys_json_nonfinite_syntax(token):
    from opennavicat.mongodb_session import parse_mongo_queries
    with pytest.raises(DatabaseError):
        parse_mongo_queries("db.docs.find(EJSON.parse('{\"x\":"+token+"}'))")


def test_real_gridfs_metadata_dates_reject_precision_loss_before_bucket_creation(session,tmp_path):
    db=session.config.database;bucket='metadata_dates'
    source=tmp_path/'source';source.write_bytes(b'own bytes')
    with pytest.raises(DatabaseError,match='millisecond'):
        session.upload_file(db,bucket,source,metadata={'date':datetime(2026,1,2,3,4,5,123456,timezone.utc)})
    assert bucket+'.files'not in session.connection[db].list_collection_names()
    exact=datetime(2026,1,2,3,4,5,123000,timezone.utc)
    saved=session.upload_file(db,bucket,source,metadata={'date':exact})
    assert saved.document['metadata']['date']==exact
