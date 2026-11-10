"""Real index shell commands on fresh fixture-owned MongoDB, never user servers."""
from datetime import datetime, timezone
import json
import re
import subprocess
import uuid

from bson import BSON, Binary, Int64, json_util
from pymongo.errors import AutoReconnect, OperationFailure
from pymongo.synchronous.database import Database
import pytest

from opennavicat.js_engine.codec import BSON_CODEC
from opennavicat.js_engine.presentation import JSON_OPTIONS, present_execution
from opennavicat.js_engine.runtime import ExecutionLimits, JavaScriptError
from test_js_engine import engine, assert_clean
from test_mongodb_session import mongodb_service, session


def test_real_index_helpers_return_names_specs_and_server_drop_documents(engine,session):
    result=engine.execute(session,"""
const c=db.docs;
const first=c.createIndex({a:1,b:-1},{name:'compound'});
const automatic=c.createIndex({c:1});
const repeated=c.createIndex({c:1});
const multiple=c.createIndexes([{d:1},{e:-1}]);
const alias=c.ensureIndex({f:1});
const before=c.getIndexes().map(x=>x.name);
const specs=c.getIndexSpecs().map(x=>x.name);
const removed=c.dropIndex('compound');
const pattern=c.dropIndex({c:1});
const many=c.dropIndexes(['d_1','e_-1']);
const remaining=c.getIndexes().map(x=>x.name);
c.dropIndexes();
({first,automatic,repeated,multiple,alias,before,specs,removed,pattern,many,remaining,final:c.getIndexes().map(x=>x.name)})
""")
    value=result.value
    assert value['first']=='compound' and value['automatic']==value['repeated']=='c_1'
    assert value['multiple']==['d_1','e_-1'] and value['alias']==['f_1']
    assert value['before']==value['specs']==['_id_','compound','c_1','d_1','e_-1','f_1']
    assert value['removed']=={'nIndexesWas':6,'ok':1.0}
    assert value['pattern']=={'nIndexesWas':5,'ok':1.0} and value['many']=={'nIndexesWas':4,'ok':1.0}
    assert value['remaining']==['_id_','f_1'] and value['final']==['_id_']
    assert all(row.affected is None for row in result.commands)
    assert_clean(result)


def test_real_helper_preview_keeps_typed_filter_options_and_compound_order(engine,session):
    result=engine.execute(session,"""
db.getCollection('docs').createIndex({a:Int32('1'),b:Int32('-1')},{name:'typed-preview',v:Int32('2'),hidden:true,
partialFilterExpression:{threshold:{$gte:Int32('1')},date:ISODate('2026-01-02T03:04:05.678Z'),payload:Binary.createFromBase64('AP8=',128),long:Long('9007199254740995'),limit:1},
collation:{locale:'en',strength:Int32('2')}})
""")
    assert result.value=='typed-preview'
    spec=next(x for x in session.connection[session.config.database].docs.list_indexes() if x['name']=='typed-preview')
    assert list(spec['key'].items())==[('a',1),('b',-1)] and spec['hidden']is True
    expected={'threshold':{'$gte':1},'date':datetime(2026,1,2,3,4,5,678000,timezone.utc),
        'payload':Binary(b'\0\xff',128),'long':Int64(9007199254740995),'limit':1.0}
    assert BSON.encode(spec['partialFilterExpression'],codec_options=BSON_CODEC)==BSON.encode(expected,codec_options=BSON_CODEC)
    assert spec['collation']['strength']==2 and type(spec['partialFilterExpression']['limit'])is float
    assert_clean(result)


@pytest.mark.parametrize('source',[
    "db.docs.createIndex({a:1})","db.docs.createIndexes([{a:1},{b:-1}])","db.docs.ensureIndex({a:1})",
    "db.docs.dropIndex('owned')","db.docs.dropIndex({a:1})","db.docs.dropIndexes()",
    "db.docs.dropIndexes(['owned'])",
])
def test_real_readonly_blocks_every_write_helper_before_driver(engine,session,monkeypatch,source):
    session.config.read_only=True
    monkeypatch.setattr(Database,'command',lambda *_a,**_k:pytest.fail('Readonly write reached driver'))
    with pytest.raises(JavaScriptError)as raised:engine.execute(session,source)
    assert len(raised.value.result.commands)==1 and raised.value.result.commands[0].outcome=='rejected'
    assert_clean(raised.value.result)


def test_real_readonly_listing_keeps_managed_cursors_and_sessions_closed(engine,session):
    session.connection[session.config.database].docs.create_index([('a',1)],name='owned')
    session.config.read_only=True
    result=engine.execute(session,"({specs:db.docs.getIndexSpecs().map(x=>x.name),keys:db.docs.getIndexKeys(),indexes:db.docs.getIndexes().map(x=>x.name)})")
    assert result.value['specs']==result.value['indexes']==['_id_','owned']
    assert result.value['keys']==[{'_id':1.0},{'a':1.0}]
    assert all(not row.writes and row.verb=='listindexes' for row in result.commands)
    assert_clean(result)


@pytest.mark.parametrize('source',[
    "db.docs.createIndex({a:1},{unknownOption:true})","db.docs.createIndex({a:1},{name:undefined})",
    "db.docs.createIndex({})","db.docs.createIndex({a:0})","db.docs.createIndexes([])",
    "db.docs.createIndex({a:1},[])","db.docs.createIndex({a:1},{},1,2)",
    "db.docs.dropIndex('*')","db.docs.dropIndex(['a_1'])","db.docs.dropIndex()",
    "db.docs.dropIndexes([])","db.docs.getIndexes({batchSize:1})",
])
def test_real_invalid_helper_arguments_do_not_open_command_channel(engine,session,monkeypatch,source):
    monkeypatch.setattr(Database,'command',lambda *_a,**_k:pytest.fail('Invalid helper reached driver'))
    with pytest.raises(JavaScriptError)as raised:engine.execute(session,source)
    assert raised.value.result.commands==()
    assert_clean(raised.value.result)


def test_real_partial_ddl_retains_acknowledged_prefix_and_reports_known_error_without_row_count(engine,session):
    collection=session.connection[session.config.database].docs
    collection.insert_many([{'_id':1,'dup':'same'},{'_id':2,'dup':'same'}])
    before=[BSON.encode(x,codec_options=BSON_CODEC)for x in collection.find().sort('_id')]
    source="db.docs.createIndex({a:1},{name:'first-ok'});db.docs.createIndex({dup:1},{name:'unique-fails',unique:true});db.docs.createIndex({b:1},{name:'never-runs'})"
    with pytest.raises(JavaScriptError)as raised:engine.execute(session,source)
    result=raised.value.result
    assert [row.outcome for row in result.commands]==['acknowledged','acknowledged_error']
    assert all(row.affected is None for row in result.commands) and result.has_confirmed_writes
    assert result.confirmed_write_count==0 and not result.uncertain_writes
    assert set(collection.index_information())=={'_id_','first-ok'}
    assert before==[BSON.encode(x,codec_options=BSON_CODEC)for x in collection.find().sort('_id')]
    assert 'no rollback is implied' in present_execution(result,source).messages
    assert_clean(result)


def test_real_server_error_code_and_name_survive_adapter_wrapping(engine,session):
    result=engine.execute(session,"let result;try{db.docs.dropIndex('missing')}catch(e){result={code:e.code,codeName:e.codeName,name:e.name}};result")
    assert result.value=={'code':27.0,'codeName':'IndexNotFound','name':'MongoServerError'}
    assert result.commands[0].outcome=='acknowledged_error' and result.commands[0].affected is None
    assert not result.has_confirmed_writes and not result.uncertain_writes
    missing=engine.execute(session,"try{db.absent.getIndexes()}catch(e){({code:e.code,codeName:e.codeName})}")
    assert missing.value=={'code':26.0,'codeName':'NamespaceNotFound'}
    assert_clean(result);assert_clean(missing)


def test_real_lost_index_ack_is_uncertain_and_never_claims_rollback(engine,session,monkeypatch):
    original=Database.command
    def lose(self,command,*args,**kwargs):
        reply=original(self,command,*args,**kwargs)
        if isinstance(command,dict)and next(iter(command))=='createIndexes':
            raise AutoReconnect('synthetic lost acknowledgement after actual index creation')
        return reply
    monkeypatch.setattr(Database,'command',lose)
    with pytest.raises(JavaScriptError)as raised:engine.execute(session,"db.docs.createIndex({a:1},{name:'actually-created'})")
    result=raised.value.result
    assert result.uncertain_writes and not result.has_confirmed_writes
    assert result.commands[0].affected is None and result.commands[0].outcome=='uncertain'
    assert 'actually-created' in session.connection[session.config.database].docs.index_information()
    assert_clean(result)


def test_real_getindexes_delivery_limit_is_explicit_and_closes_cursor(engine,session):
    collection=session.connection[session.config.database].docs
    for i in range(4):collection.create_index([(f'field{i}',1)])
    with pytest.raises(JavaScriptError,match='delivery limit')as raised:
        engine.execute(session,'db.docs.getIndexes()',limits=ExecutionLimits(max_rows=2))
    assert len(collection.index_information())==5
    assert_clean(raised.value.result)


def test_real_unacknowledged_concern_is_rejected_before_ddl(engine,session,monkeypatch):
    monkeypatch.setattr(Database,'command',lambda *_a,**_k:pytest.fail('Unacknowledged DDL reached driver'))
    with pytest.raises(JavaScriptError,match='acknowledged')as raised:
        engine.execute(session,"db.docs.createIndex({a:1},{writeConcern:{w:0}})")
    assert raised.value.result.commands[0].outcome=='rejected'
    assert_clean(raised.value.result)


def test_real_supported_helper_return_values_match_owned_mongosh(engine,session):
    # Resolve only the already verified fixture's exact loopback mapping. Do
    # not accept an external endpoint or inspect unrelated/user containers.
    ids=subprocess.check_output(['docker','container','ls','--no-trunc','--filter','label=opennavicat.test=true',
        '--filter','label=opennavicat.task=mongodb-adapter-tests','--format','{{.ID}}'],text=True).splitlines()
    matches=[]
    for identity in ids:
        info=json.loads(subprocess.check_output(['docker','inspect',identity]))[0]
        mappings=info['NetworkSettings']['Ports'].get('27017/tcp')or[]
        if not any(m.get('HostIp')=='127.0.0.1'and m.get('HostPort')==str(session.config.port)for m in mappings):continue
        labels=info['Config']['Labels'];token=labels.get('opennavicat.mongodb-protocol-fixture','')
        assert re.fullmatch('[0-9a-f]{32}',token) and info['Id']==identity and info['Name']=='/opennavicat-mongo-owned-'+token[:12]
        matches.append(identity)
    assert len(matches)==1
    identity=matches[0]
    version=subprocess.check_output(['docker','exec',identity,'mongosh','--version'],text=True).strip()
    assert re.fullmatch(r'\d+\.\d+\.\d+',version)
    source="""
const c=db.docs;const out={};
out.created=c.createIndex({a:1},{name:'named'});out.multiple=c.createIndexes([{b:1},{c:-1}]);
out.ensured=c.ensureIndex({d:1});out.names=c.getIndexes().map(x=>x.name);
out.specs=c.getIndexSpecs().map(x=>x.name);out.keys=c.getIndexKeys();
out.drop=c.dropIndex('named');out.all=c.dropIndexes();out.final=c.getIndexes().map(x=>x.name);out
"""
    native=engine.execute(session,source).value
    database='onv_mongosh_'+uuid.uuid4().hex
    oracle_source="(async()=>{const a=db.getSiblingDB('admin');await a.auth(process.env.ONV_OWN_USER,process.env.ONV_OWN_PASSWORD);db=db.getSiblingDB(process.env.ONV_OWN_DB);"+source.rstrip()[:-3]+";print(EJSON.stringify(out,{relaxed:false}));await db.dropDatabase();})()"
    # mongosh rewrites shell helper calls inside its actual execution engine;
    # this is not a substitute evaluator or mock of those methods.
    try:
        process=subprocess.run(['docker','exec','--env','ONV_OWN_USER='+session.config.username,
            '--env','ONV_OWN_PASSWORD='+session.config.password,'--env','ONV_OWN_DB='+database,
            identity,'mongosh','--quiet','--norc','--eval',oracle_source],capture_output=True,text=True,timeout=30)
    except subprocess.TimeoutExpired:
        raise AssertionError('Owned mongosh helper comparison timed out')from None
    assert process.returncode==0,process.stderr.replace(session.config.password,'<redacted>')[:2000]
    oracle=json_util.loads(process.stdout.strip(),json_options=JSON_OPTIONS)
    # Compare logical helper return shapes. mongosh EJSON re-encodes ordinary
    # control Numbers as Int32; Navicat's observed fresh Number is Double, so
    # this explicitly does not assert raw numeric BSON parity between shells.
    assert native==oracle


def test_real_drop_default_id_and_missing_array_do_not_claim_success(engine,session):
    collection=session.connection[session.config.database].docs
    collection.insert_one({'_id':'retained','value':1})
    collection.create_index([('value',1)],name='retained-index')
    for source in ("db.docs.dropIndex('_id_')","db.docs.dropIndexes(['retained-index','missing'])"):
        with pytest.raises(JavaScriptError)as raised:engine.execute(session,source)
        result=raised.value.result
        assert result.commands[0].outcome=='acknowledged_error' and not result.uncertain_writes
        assert not result.has_confirmed_writes and result.commands[0].affected is None
        assert set(collection.index_information())=={'_id_','retained-index'}
        assert collection.find_one({'_id':'retained'})['value']==1
        assert_clean(result)


def test_real_quorum_standalone_error_keeps_typed_comment_and_exact_control_options(engine,session,monkeypatch):
    original=Database.command;captured=[]
    def capture(self,command,*args,**kwargs):
        if isinstance(command,dict)and next(iter(command))=='createIndexes':captured.append(command)
        return original(self,command,*args,**kwargs)
    monkeypatch.setattr(Database,'command',capture)
    result=engine.execute(session,"try{db.docs.createIndex({a:1},{name:'quorum',maxTimeMS:1000,comment:{n:Long('1')}},1)}catch(e){({code:e.code,codeName:e.codeName})}")
    assert result.value=={'code':2.0,'codeName':'BadValue'}
    assert len(captured)==1
    assert type(captured[0]['maxTimeMS'])is int and type(captured[0]['commitQuorum'])is int
    assert type(captured[0]['comment']['userComment']['n'])is Int64
    assert captured[0]['comment']['OpenNavicat'].startswith('OpenNavicat:')
    assert 'quorum'not in session.connection[session.config.database].docs.index_information()
    assert result.commands[0].outcome=='acknowledged_error' and not result.has_confirmed_writes
    assert_clean(result)


def test_real_standalone_unavailable_write_concern_is_known_rejection(engine,session):
    with pytest.raises(JavaScriptError)as raised:
        engine.execute(session,"db.docs.createIndex({a:1},{name:'concern-uncertain',writeConcern:{w:2,wtimeout:1000}})")
    result=raised.value.result
    row=result.commands[0]
    assert not result.uncertain_writes and row.outcome=='acknowledged_error'
    assert row.reply['code']==2 and row.reply['codeName']=='BadValue'
    assert not result.has_confirmed_writes and result.commands[0].affected is None
    assert 'concern-uncertain'not in session.connection[session.config.database].docs.index_information()
    assert_clean(result)


def test_injected_concern_reply_after_actual_index_write_remains_uncertain(engine,session,monkeypatch):
    original=Database.command
    def concern(self,command,*args,**kwargs):
        reply=original(self,command,*args,**kwargs)
        if isinstance(command,dict)and next(iter(command))=='createIndexes':
            raise OperationFailure('synthetic concern reply after actual index creation',64,
                {'ok':0.0,'code':64,'codeName':'WriteConcernFailed'})
        return reply
    monkeypatch.setattr(Database,'command',concern)
    with pytest.raises(JavaScriptError)as raised:engine.execute(session,"db.docs.createIndex({a:1},{name:'actual-index'})")
    result=raised.value.result
    assert result.uncertain_writes and not result.has_confirmed_writes
    assert result.commands[0].outcome=='uncertain' and result.commands[0].affected is None
    assert 'actual-index'in session.connection[session.config.database].docs.index_information()
    assert_clean(result)


@pytest.mark.parametrize('keys,options,field,value',[
    ("{a:1}","{name:'common',v:2,collation:{locale:'en',strength:2}}",'v',2),
    ("{date:1}","{name:'common',expireAfterSeconds:10}",'expireAfterSeconds',10),
    ("{a:'text'}","{name:'common',weights:{a:5},default_language:'english',textIndexVersion:3}",'textIndexVersion',3),
    ("{loc:'2dsphere'}","{name:'common','2dsphereIndexVersion':3}",'2dsphereIndexVersion',3),
])
def test_real_common_index_option_numbers_remain_valid_control_values(engine,session,keys,options,field,value):
    result=engine.execute(session,'db.docs.createIndex('+keys+','+options+')')
    assert result.value=='common'
    spec=next(x for x in session.connection[session.config.database].docs.list_indexes() if x['name']=='common')
    assert spec[field]==value
    assert_clean(result)
