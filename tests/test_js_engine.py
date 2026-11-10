"""Actual controlled-helper and fresh owned MongoDB protocol qualification.

No external/reference endpoint is accepted. Tests opt into the same existing
fresh-container fixture as the backend suite and preserve exact cleanup proof.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import os
import shutil
import threading
import time

from bson import BSON, Binary, Decimal128, Int64
from pymongo import monitoring
import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_session import BSON_CODEC, MongoSession
from opennavicat.js_engine.runtime import ExecutionLimits, JavaScriptError, NativeJavaScript
from opennavicat.js_engine.codec import JS_UNDEFINED
from test_mongodb_session import mongodb_service, session


@pytest.fixture(scope="module")
def engine():
    helper = Path(os.environ.get("OPENNAVICAT_TEST_JS_HELPER", Path(__file__).resolve().parents[1]/"artifacts/js-engine/opennavicat-js"))
    if not helper.exists():
        pytest.skip("Build the pinned controlled native helper with scripts/build-quickjs.py")
    return NativeJavaScript(helper)


@pytest.fixture
def offline():
    # VM-only cases cannot do network I/O: no client is created.
    session = MongoSession(ConnectionConfig("offline-js","Offline VM",driver="mongodb",database="owned",ssl_mode="disable"))
    session.connection = object()
    return session


def assert_clean(result):
    assert result.child_reaped and result.open_cursors == 0 and result.open_sessions == 0
    assert result.stderr == ""


def test_actual_native_bigint_variables_functions_loops_async(engine, offline):
    result = engine.execute(offline, "function plus(x){return x+2n;} var values=[];for(let i=0;i<3;i++)values.push(plus(9007199254740993n+BigInt(i)));(async()=>{await Promise.resolve();return values;})()")
    assert result.value == [9007199254740995,9007199254740996,9007199254740997]
    assert_clean(result)


@pytest.mark.parametrize("text,fragment", [("while(true){}",""),
    ("function recurse(){return recurse()+1;}recurse()","stack"),
    ("const a=[];for(;;)a.push('x'.repeat(4096))","memory"),
    ("Promise.reject(new Error('generated rejection'));42","generated rejection"),
    ("new Promise(()=>{})","pending"),
    ("import('os')","load"),
    ("'x'.repeat(300000)","output")])
def test_actual_native_failure_limits_and_cleanup(engine, offline, text, fragment):
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(offline,text,limits=ExecutionLimits(timeout_ms=100,heap_bytes=4*1024*1024))
    assert fragment in str(raised.value).lower()
    assert_clean(raised.value.result)
    assert raised.value.result.elapsed_ms < 2000


def test_actual_native_explicit_cancel_does_not_block_python(engine, offline):
    cancel = threading.Event()
    ticks=[]
    def pulse():
        for _ in range(10):
            time.sleep(.01);ticks.append(1)
        cancel.set()
    ticker=threading.Thread(target=pulse);ticker.start()
    try:
        with pytest.raises(JavaScriptError) as raised:
            engine.execute(offline,"while(true){}",cancel_event=cancel)
    finally:
        ticker.join()
    assert len(ticks)==10 and raised.value.result.cancelled and raised.value.result.hard_killed
    assert_clean(raised.value.result)


def test_already_cancelled_execution_never_launches_child_or_writes(engine,offline,monkeypatch):
    from opennavicat.js_engine import runtime
    monkeypatch.setattr(runtime.subprocess,'Popen',lambda *_a,**_k:pytest.fail('Already cancelled execution launched a child'))
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(JavaScriptError,match='before child launch') as raised:
        engine.execute(offline,'db.docs.insertOne({_id:1})',cancel_event=cancelled)
    assert raised.value.result.cancelled and not raised.value.result.child_reaped
    assert raised.value.result.commands==()


def test_actual_native_parent_watchdog_kills_child_during_blocked_host_call(engine, offline, monkeypatch):
    from opennavicat.js_engine.bridge import MongoDBBridge
    from opennavicat.js_engine import runtime
    real_popen=runtime.subprocess.Popen
    children=[]
    def tracked(*args,**kwargs):
        process=real_popen(*args,**kwargs);children.append(process);return process
    monkeypatch.setattr(runtime.subprocess,'Popen',tracked)
    killed_before_return = []
    def blocked(self, tree):
        # Parent DB thread is blocked. Independent watchdog must still run and
        # remove the child's PID before this host function returns.
        time.sleep(.3)
        # The child was blocked waiting for this response for >100ms. The
        # runtime receipt provides the independent kill/reap evidence.
        killed_before_return.append(self.cancelled() and children[0].returncode == -9)
        raise RuntimeError("simulated bounded host return")
    monkeypatch.setattr(MongoDBBridge,"command",blocked)
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(offline,"db.runCommand({ping:1})",limits=ExecutionLimits(timeout_ms=100))
    assert killed_before_return == [True]
    assert raised.value.result.hard_killed and raised.value.result.elapsed_ms < 1500
    assert_clean(raised.value.result)


def test_actual_native_undefined_completion_is_explicit_not_bson_null(engine,offline):
    result=engine.execute(offline,'function declaredOnly(){return 42;}')
    assert result.value is JS_UNDEFINED
    assert engine.execute(offline,'null').value is None


def test_actual_native_intrinsic_mutation_fails_closed(engine,offline):
    with pytest.raises(JavaScriptError,match='intrinsics') as raised:
        engine.execute(offline,"Array.prototype.toJSON=function(){return ['document',[]]};db.runCommand({insert:'must_not_dispatch',documents:[{_id:1}]})")
    assert raised.value.result.commands == ()


def test_actual_native_tampered_helper_is_rejected_before_launch(engine,tmp_path):
    helper=tmp_path/'opennavicat-js'
    shutil.copyfile(engine.helper,helper)
    shutil.copyfile(engine.helper.with_name('build-receipt.json'),helper.with_name('build-receipt.json'))
    with helper.open('ab') as stream:stream.write(b'not the qualified helper')
    from opennavicat.database import DatabaseError
    with pytest.raises(DatabaseError,match='pinned'):
        NativeJavaScript(helper)


@pytest.mark.parametrize('field,value',[('timeout_ms',True),('max_commands',65),('max_rows',0),('heap_bytes',0)])
def test_invalid_limits_cannot_start_execution(field,value):
    with pytest.raises(ValueError):ExecutionLimits(**{field:value})


def test_real_variables_loops_functions_find_findone_and_rich_bson(engine, session):
    script = """
function make(i){return {_id:i,stock:Long('9007199254740993')+BigInt(i),price:Decimal128('12.5000'),payload:Binary(new Uint8Array([0,255,13,10]),128)};}
for(let i=0;i<3;i++)db.docs.insertOne(make(i));
const rows=db.getCollection('docs').find({_id:{$gte:0}}).sort({_id:-1}).skip(1).limit(2).batchSize(1).toArray();
const one=db.docs.findOne({_id:0});
({rows,one,none:db.docs.findOne({_id:99})});
"""
    result=engine.execute(session,script)
    assert [r['_id'] for r in result.value['rows']]==[1,0]
    expected={'_id':0.0,'stock':Int64(9007199254740993),'price':Decimal128('12.5000'),'payload':Binary(b'\x00\xff\r\n',128)}
    assert BSON.encode(result.value['one'],codec_options=BSON_CODEC)==BSON.encode(expected,codec_options=BSON_CODEC)
    assert result.value['none'] is None and result.confirmed_write_count==3
    assert all(c.outcome=='acknowledged' for c in result.commands)
    assert_clean(result)


def test_real_aggregate_cursor_iteration_and_runcommand(engine, session):
    session.connection[session.config.database].docs.insert_many([{'_id':i,'group':i%2,'amount':i}for i in range(8)])
    result=engine.execute(session,"""
let ids=[];const cursor=db.docs.find({}).sort({_id:1}).batchSize(1);
while(cursor.hasNext()){const row=cursor.next();ids.push(row._id);}
const grouped=db.docs.aggregate([{$group:{_id:'$group',total:{$sum:'$amount'}}},{$sort:{_id:1}}]).batchSize(1).toArray();
({ids,grouped,count:db.runCommand({count:'docs',query:{}}).n,pong:db.runCommand({ping:1}).ok});
""")
    assert result.value=={'ids':list(range(8)),'grouped':[{'_id':0,'total':12},{'_id':1,'total':16}],'count':8,'pong':1.0}
    assert_clean(result)


def test_real_final_find_and_aggregate_cursors_execute_without_toarray(engine,session):
    session.connection[session.config.database].docs.insert_many([{'_id':i}for i in range(4)])
    result=engine.execute(session,'db.docs.find({_id:{$gte:1}}).sort({_id:1}).limit(2).batchSize(1)')
    assert result.value==[{'_id':1},{'_id':2}]
    assert_clean(result)
    grouped=engine.execute(session,'db.docs.aggregate([{$group:{_id:null,n:{$sum:1}}}])')
    assert grouped.value==[{'_id':None,'n':4.0}]
    assert_clean(grouped)


def test_real_confirmed_write_remains_when_native_child_final_status_fails(engine,session,monkeypatch):
    from opennavicat.js_engine import runtime
    original=runtime.subprocess.Popen
    def failed_status(*args,**kwargs):
        process=original(*args,**kwargs);wait=process.wait
        def later(*a,**k):
            status=wait(*a,**k)
            if status==0:process.returncode=73;return 73
            return status
        process.wait=later;return process
    monkeypatch.setattr(runtime.subprocess,'Popen',failed_status)
    with pytest.raises(JavaScriptError,match='exited unsuccessfully')as raised:
        engine.execute(session,'db.docs.insertOne({_id:1});42')
    assert raised.value.result.confirmed_write_count==1 and raised.value.result.has_confirmed_writes
    assert session.connection[session.config.database].docs.find_one({'_id':1})is not None
    assert_clean(raised.value.result)


def test_real_update_delete_and_typed_command_comment(engine, session):
    result=engine.execute(session,"""
db.docs.insertMany([{_id:1,n:1},{_id:2,n:2},{_id:3,n:3}]);
const changed=db.docs.updateOne({_id:1},{$set:{long:Long('9007199254740995')}});
db.docs.updateMany({n:{$gte:2}},{$inc:{n:10}});
const deleted=db.docs.deleteOne({_id:2});
db.docs.deleteMany({_id:3});
const reply=db.runCommand({find:'docs',comment:{long:Long('9007199254740995')}});
({changed,deleted,doc:reply.cursor.firstBatch[0]});
""")
    assert result.value['doc']=={'_id':1,'n':1,'long':Int64(9007199254740995)}
    assert result.value['changed']['modifiedCount']==1 and result.value['deleted']['deletedCount']==1
    assert session.connection[session.config.database].docs.count_documents({})==1
    assert_clean(result)


def test_real_reference_number_wire_policy_is_distinct_from_integer_controls(engine,session):
    engine.execute(session,"""
db.docs.insertOne({_id:Int32(1),one:1,large:2147483648,rounded:9007199254740995,negativeZero:-0,
 explicit:Int32(1),exact:Long('9007199254740995'),data:{limit:1,batchSize:2,maxTimeMS:3,writeConcern:{w:1}}});
db.docs.find({}).limit(1).skip(0).batchSize(1).toArray();
""")
    doc=session.connection.get_database(session.config.database,codec_options=BSON_CODEC).docs.find_one({'_id':1})
    assert type(doc['one'])is float and type(doc['large'])is float and doc['rounded']==9007199254740996.0
    import struct
    assert struct.pack('<d',doc['negativeZero'])==b'\0\0\0\0\0\0\0\x80'
    assert type(doc['explicit'])is int and isinstance(doc['exact'],Int64)
    assert all(type(doc['data'][name])is float for name in ('limit','batchSize','maxTimeMS'))
    assert type(doc['data']['writeConcern']['w'])is float


def test_real_javascript_numeric_keys_follow_actual_reference_object_order(engine,session):
    from bson import SON
    session.connection[session.config.database].docs.insert_one({'_id':1,'numericKeys':SON([('2','two'),('1','one'),('a','letter')])})
    result=engine.execute(session,"const doc=db.docs.findOne({_id:1});db.docs.updateOne({_id:1},{$set:{numericKeys:doc.numericKeys}});Object.keys(doc.numericKeys)")
    assert result.value==['1','2','a']
    assert list(session.connection[session.config.database].docs.find_one({'_id':1})['numericKeys'])==['1','2','a']


def test_real_partial_writes_are_preserved_and_later_command_not_run(engine, session):
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(session,"""
db.docs.insertOne({_id:1,typed:Long('9007199254740995')});
db.docs.insertMany([{_id:2},{_id:1},{_id:3}],{ordered:false});
db.docs.insertOne({_id:4});
""")
    result=raised.value.result
    assert result.confirmed_write_count==3
    assert [r.outcome for r in result.commands]==['acknowledged','acknowledged_error']
    assert session.connection[session.config.database].docs.count_documents({})==3
    assert session.connection[session.config.database].docs.find_one({'_id':4})is None
    assert not result.uncertain_writes
    assert_clean(result)


@pytest.mark.parametrize("text",["db.docs.insertOne({_id:1})", "db.docs.updateOne({},{$set:{x:1}})",
    "db.docs.deleteMany({})", "db.docs.aggregate([{$out:'blocked'}]).toArray()",
    "db.runCommand({aggregate:'docs',pipeline:[{$facet:{x:[{$merge:'blocked'}]}}],cursor:{}})"])
def test_real_readonly_rejects_before_any_driver_command(engine, session, monkeypatch, text):
    session.config.read_only=True
    from pymongo.synchronous.database import Database
    monkeypatch.setattr(Database,'command',lambda *_a,**_k:pytest.fail('Readonly rejected command reached driver'))
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(session,text)
    assert all(c.outcome=='rejected' for c in raised.value.result.commands)
    assert_clean(raised.value.result)


def test_real_delivery_limit_fails_explicitly_and_closes_native_cursor(engine, session):
    col=session.connection[session.config.database].docs
    col.insert_many([{'_id':i}for i in range(10)])
    baseline=session.connection.admin.command('serverStatus')['metrics']['cursor']['open']['total']
    with pytest.raises(JavaScriptError,match='delivery limit') as raised:
        engine.execute(session,'db.docs.find({}).batchSize(1).toArray()',limits=ExecutionLimits(max_rows=3))
    assert_clean(raised.value.result)
    assert session.connection.admin.command('serverStatus')['metrics']['cursor']['open']['total']==baseline
    exact=engine.execute(session,'db.docs.find({}).limit(3).batchSize(1).toArray()',limits=ExecutionLimits(max_rows=3))
    assert len(exact.value)==3


def test_real_terminal_out_is_not_rewritten_by_delivery_limit(engine, session):
    session.connection[session.config.database].docs.insert_many([{'_id':i}for i in range(8)])
    result=engine.execute(session,"db.docs.aggregate([{$out:'out_generated'}]).toArray()",limits=ExecutionLimits(max_rows=1))
    assert result.value==[]
    assert session.connection[session.config.database].out_generated.count_documents({})==8
    assert result.commands[0].writes and result.commands[0].outcome=='acknowledged'
    assert result.commands[0].affected is None and result.has_confirmed_writes
    assert_clean(result)


def test_real_cancel_after_confirmed_write_keeps_write_and_session_reusable(engine, session):
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(session,"db.docs.insertOne({_id:1});while(true){}",limits=ExecutionLimits(timeout_ms=250))
    assert raised.value.result.confirmed_write_count==1
    assert session.connection[session.config.database].docs.find_one({'_id':1})is not None
    assert_clean(raised.value.result)
    assert engine.execute(session,'db.docs.findOne({_id:1})').value=={'_id':1}


def test_real_memory_exhaustion_cannot_be_caught_to_dispatch_a_later_write(engine,session):
    with pytest.raises(JavaScriptError,match='memory')as raised:
        engine.execute(session,"try{const a=[];for(;;)a.push('x'.repeat(4096))}catch(e){}db.docs.insertOne({_id:1})",
            limits=ExecutionLimits(heap_bytes=4*1024*1024))
    assert raised.value.result.commands==()
    assert raised.value.result.resource_exhausted
    assert session.connection[session.config.database].docs.find_one({'_id':1})is None
    assert_clean(raised.value.result)


def test_real_memory_limit_keeps_confirmed_write_but_denies_later_read_or_write(engine,session):
    with pytest.raises(JavaScriptError,match='memory')as raised:
        engine.execute(session,"db.docs.insertOne({_id:1});try{const a=[];for(;;)a.push('x'.repeat(4096))}catch(e){}try{db.docs.findOne({_id:1})}catch(e){}db.docs.insertOne({_id:2})",
            limits=ExecutionLimits(heap_bytes=4*1024*1024))
    result=raised.value.result
    assert result.resource_exhausted and result.confirmed_write_count==1
    assert len(result.commands)==1 and result.commands[0].verb=='insert'
    assert session.connection[session.config.database].docs.count_documents({})==1
    assert_clean(result)


def test_real_network_acknowledgement_failure_is_uncertain_not_rollback(engine, session, monkeypatch):
    from pymongo.errors import AutoReconnect
    from pymongo.synchronous.database import Database
    real=Database.command
    def lose_ack(self, command, *args, **kwargs):
        reply=real(self,command,*args,**kwargs)
        if isinstance(command,dict) and next(iter(command))=='insert':
            raise AutoReconnect('synthetic lost acknowledgement after actual server write')
        return reply
    monkeypatch.setattr(Database,'command',lose_ack)
    with pytest.raises(JavaScriptError) as raised:
        engine.execute(session,'db.docs.insertOne({_id:1})')
    assert raised.value.result.uncertain_writes and raised.value.result.confirmed_write_count==0
    assert session.connection[session.config.database].docs.find_one({'_id':1})is not None
    assert_clean(raised.value.result)


def test_real_find_getmore_killcursor_share_lsid_and_typed_nonce_comment(engine,session,monkeypatch):
    import opennavicat.mongodb_session as module
    class Events(monitoring.CommandListener):
        def __init__(self):self.events=[]
        def started(self,event):
            if event.command_name.lower() in {'find','getmore','killcursors'}:
                self.events.append((event.command_name.lower(),dict(event.command)))
        def succeeded(self,event):pass
        def failed(self,event):pass
    events=Events();original=module.MongoClient
    def client(**kwargs):
        return original(**kwargs,event_listeners=[events])
    monkeypatch.setattr(module,'MongoClient',client)
    observed=MongoSession(replace(session.config));observed.connect()
    logical_sessions=[]
    start_session=observed.connection.start_session
    def tracked_session(*args,**kwargs):
        value=start_session(*args,**kwargs);logical_sessions.append(value);return value
    monkeypatch.setattr(observed.connection,'start_session',tracked_session)
    session.connection[session.config.database].docs.insert_many([{'_id':i,'text':'x'*600}for i in range(10)])
    try:
        with pytest.raises(JavaScriptError,match='delivery limit') as raised:
            engine.execute(observed,"db.runCommand({find:'docs',batchSize:1,comment:{n:Long('9007199254740995')}})",limits=ExecutionLimits(max_bson_bytes=1024))
        assert_clean(raised.value.result)
        assert {'find','getmore','killcursors'} <= {name for name,_ in events.events}
        lsid=events.events[0][1]['lsid']
        comment=events.events[0][1]['comment']
        assert comment['userComment']=={'n':Int64(9007199254740995)}
        assert comment['OpenNavicat'].startswith('OpenNavicat:')
        for _,command in events.events:
            assert command['lsid']==lsid
            assert command.get('comment',comment)==comment
        assert logical_sessions and all(value.has_ended for value in logical_sessions)
    finally:
        observed.close()


def test_real_cancel_while_server_call_blocks_stops_only_own_query_and_recovers(engine,session):
    session.connection[session.config.database].docs.insert_one({'_id':1})
    cancel=threading.Event();failures=[]
    def wait_for_query():
        deadline=time.monotonic()+3
        try:
            while time.monotonic()<deadline:
                with session._active_lock:
                    comment=session._active_comment
                rows=list(session._control.admin.aggregate([{'$currentOp':{'allUsers':False}},
                    {'$match':{'command.comment':comment,'command.find':'docs'}}],maxTimeMS=1000)) if comment else []
                if rows:
                    cancel.set();return
                time.sleep(.01)
            failures.append('Own slow MongoDB query did not reach the server')
            cancel.set()
        except Exception as error:
            failures.append(type(error).__name__);cancel.set()
    worker=threading.Thread(target=wait_for_query);worker.start()
    try:
        with pytest.raises(JavaScriptError) as raised:
            engine.execute(session,"db.docs.find({$where:Code('function(){sleep(10000);return true;}')}).toArray()",
                cancel_event=cancel,limits=ExecutionLimits(timeout_ms=5000))
    finally:
        worker.join(5)
    assert not worker.is_alive() and not failures
    assert raised.value.result.cancelled and raised.value.result.hard_killed
    assert raised.value.result.elapsed_ms < 4500
    assert_clean(raised.value.result)
    assert engine.execute(session,'db.docs.findOne({_id:1})').value=={'_id':1}
