"""Native helper qualification without a DB/Qt/SQLite fixture or user endpoint."""
from __future__ import annotations
import os
from pathlib import Path
import threading
import time
import struct
import subprocess

from bson import BSON, Binary, Decimal128, Int64
import pytest

from opennavicat.js_engine.codec import BSON_CODEC,JS_UNDEFINED,decode,encode
from opennavicat.js_engine.runtime import ExecutionLimits,JavaScriptError,NativeJavaScript


def test_native_bson_extension_is_loaded():
    import bson
    assert bson.has_c(),"Native qualification cannot silently use the pure Python BSON fallback"


@pytest.mark.parametrize('length',[0,10,26,266,282,65546,65562,1024*1024])
def test_native_binary_pipe_all_bytes_and_frame_boundaries(native,length):
    data=bytes(index%256 for index in range(length))
    frame=struct.pack('>I',length)+data
    options={'creationflags':subprocess.CREATE_NO_WINDOW}if os.name=='nt'else{}
    reply=subprocess.run([str(native.helper),'--probe-framing','1'],input=frame,capture_output=True,timeout=5,**options)
    assert reply.returncode==0 and reply.stdout==frame and reply.stderr==b''


@pytest.mark.parametrize('frame',[struct.pack('>I',1024*1024+1),struct.pack('>I',26)+b'incomplete'])
def test_native_binary_pipe_rejects_oversized_and_incomplete_frames(native,frame):
    options={'creationflags':subprocess.CREATE_NO_WINDOW}if os.name=='nt'else{}
    reply=subprocess.run([str(native.helper),'--probe-framing','1'],input=frame,capture_output=True,timeout=5,**options)
    assert reply.returncode==65 and reply.stdout==b''and reply.stderr==b''


@pytest.fixture(scope="module")
def native():
    name="opennavicat-js.exe"if os.name=="nt"else"opennavicat-js"
    helper=Path(os.environ.get("OPENNAVICAT_TEST_JS_HELPER",Path(__file__).resolve().parents[1]/"artifacts/js-engine"/name))
    if not helper.exists():pytest.skip("Build the pinned native JavaScript helper before qualification")
    return NativeJavaScript(helper)


def test_native_bigint_variables_functions_loops_and_async(native):
    result=native.evaluate("function plus(n){return n+2n;}let a=[];for(let i=0;i<3;i++)a.push(plus(9007199254740993n+BigInt(i)));(async()=>{await Promise.resolve();return a;})()")
    assert result.value==[9007199254740995,9007199254740996,9007199254740997]
    assert result.child_reaped and result.stderr=="" and result.commands==()
    assert result.native_state['pointer_bits']==64
    if os.name=="nt":assert result.native_state['windows_no_console']is True


def test_native_binary_framing_unicode_crlf_nul_and_bson_types(native):
    result=native.evaluate("({text:'中文\\r\\n\\u0000\\u2028',long:Long('9007199254740995'),decimal:Decimal128('12.5000'),binary:Binary(new Uint8Array([0,255,13,10]),128),minusZero:-0})")
    expected={'text':'中文\r\n\x00\u2028','long':Int64(9007199254740995),'decimal':Decimal128('12.5000'),'binary':Binary(b'\0\xff\r\n',128),'minusZero':-0.0}
    assert BSON.encode(result.value,codec_options=BSON_CODEC)==BSON.encode(expected,codec_options=BSON_CODEC)
    assert result.child_reaped


def test_native_no_host_or_database_capabilities(native):
    assert native.evaluate('[typeof std,typeof os,typeof require,typeof process,typeof fetch,typeof __native]').value==['undefined']*6
    with pytest.raises(JavaScriptError,match='no database capability'):
        native.evaluate('db.runCommand({ping:1})')
    assert native.evaluate('[typeof Double,typeof DateMS,String(Long("9007199254740995"))]').value==['undefined','undefined','Long("9007199254740995")']


@pytest.mark.parametrize('source,fragment',[
    ('while(true){}',''),
    ("const a=[];for(;;)a.push('x'.repeat(4096))",'memory'),
    ("try{const a=[];for(;;)a.push('x'.repeat(4096))}catch(e){}42",'memory'),
    ('function again(){return again()+1;}again()','stack'),
    ('new Promise(()=>{})','pending'),
    ('Promise.reject(new Error("generated rejection"));42','generated rejection'),
    ('function again(){Promise.resolve().then(again)}again();42','job'),
    ('import("os")','load'),
    ('"x".repeat(300000)','output'),
    ('throw null','null'),
])
def test_native_limits_errors_and_reaping(native,source,fragment):
    with pytest.raises(JavaScriptError) as raised:
        native.evaluate(source,limits=ExecutionLimits(timeout_ms=1500 if fragment in {'memory','output','null'} else 150,heap_bytes=4*1024*1024,max_jobs=50))
    result=raised.value.result
    assert fragment in str(raised.value).lower()
    assert result.child_reaped and result.elapsed_ms<2500 and result.stderr==""
    if fragment=='memory':assert result.resource_exhausted


def test_native_explicit_cancel_and_new_run(native):
    cancelled=threading.Event();ticks=[]
    def pulse():
        for _ in range(8):time.sleep(.01);ticks.append(1)
        cancelled.set()
    thread=threading.Thread(target=pulse);thread.start()
    try:
        with pytest.raises(JavaScriptError)as raised:
            native.evaluate('while(true){}',cancel_event=cancelled)
    finally:thread.join()
    assert ticks and raised.value.result.cancelled and raised.value.result.hard_killed and raised.value.result.child_reaped
    assert native.evaluate('42').value==42


def test_native_watchdog_reaps_while_parent_host_function_is_still_blocked(native,monkeypatch):
    from opennavicat.js_engine import runtime
    children=[];observations=[];real=runtime.subprocess.Popen
    def popen(*args,**kwargs):
        process=real(*args,**kwargs);children.append(process);return process
    def blocked(self,tree):
        time.sleep(.35)
        observations.append(children[0].returncode is not None)
        raise RuntimeError('bounded synthetic host return')
    monkeypatch.setattr(runtime.subprocess,'Popen',popen)
    monkeypatch.setattr(runtime._NoDatabase,'command',blocked)
    with pytest.raises(JavaScriptError)as raised:
        native.evaluate('db.runCommand({ping:1})',limits=ExecutionLimits(timeout_ms=150))
    assert observations==[True]and raised.value.result.hard_killed and raised.value.result.child_reaped


def test_native_undefined_null_and_prototype_boundary(native):
    assert native.evaluate('function onlyDeclare(){}').value is JS_UNDEFINED
    assert native.evaluate('null').value is None
    with pytest.raises(JavaScriptError,match='intrinsics'):
        native.evaluate('Array.prototype.toJSON=function(){return 42};({x:1})')


def test_native_completion_retains_js_bigint_and_bson_numeric_types_for_display(native):
    result = native.evaluate('({big:1n,long:Long("1"),int:Int32(1),number:1})')
    tags = {name: tree[0] for name, tree in result.value_tree[1]}
    assert tags == {'big': 'js-bigint', 'long': 'int64', 'int': 'int32', 'number': 'double'}
    with pytest.raises(JavaScriptError) as raised:
        native.evaluate('throw new Error("synthetic failure")')
    assert raised.value.result.value_tree is None


def test_actual_helper_probe_reports_source_and_executed_bytes_hash(native,monkeypatch):
    import hashlib,json
    from opennavicat.js_engine import probe
    monkeypatch.setattr(probe,'load_engine',lambda:native)
    report=probe.probe_native_javascript()
    receipt=json.loads(native.helper.with_name('build-receipt.json').read_text())
    assert report['native_js_helper_sha256']==hashlib.sha256(native.helper.read_bytes()).hexdigest()
    assert report['native_js_source_helper_sha256']==receipt.get('source_helper_sha256',receipt['helper_sha256'])
    assert report['native_js_source_revision']=='d7215b0ad3fc2c562e8317fd9bca058fd4863804'
    assert all(report[field]is True for field in probe.PROBE_FIELDS)


@pytest.mark.parametrize('source',[
    'BigInt.prototype.toString=function(){return "123"};Long("1")',
    'Object.is=function(){return false};-0',
    'Array.prototype.push=function(){return 0};({x:1})',
])
def test_native_intrinsics_cannot_silently_change_bson_values(native,source):
    with pytest.raises(JavaScriptError,match='intrinsics'):
        native.evaluate(source)


def test_native_array_accessors_are_not_executed_during_data_encoding(native):
    with pytest.raises(JavaScriptError,match='accessors'):
        native.evaluate('const a=[1];Object.defineProperty(a,0,{enumerable:true,get(){throw new Error("must not call getter")}});a')


def test_native_late_failure_status_cannot_turn_a_terminal_frame_into_success(native,monkeypatch):
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
        native.evaluate('42')
    assert raised.value.result.child_reaped and raised.value.result.value is None


@pytest.mark.parametrize('operation',['host-unchanged','same-value','fresh-components'])
def test_native_host_metadata_typed_ipc_with_synthetic_parent(native,monkeypatch,operation):
    """Offline native protocol qualification; actual server writes run elsewhere."""
    from opennavicat.js_engine import runtime
    seed={'_id':'seed','i32':1,'nested':{'i32':1},'arr':[1,1.0,Int64(1)]}
    captured=[]
    def response(self,tree):
        command=decode(tree)
        if 'find'in command:
            return encode({'ok':1.0,'cursor':{'id':Int64(0),'ns':'owned.docs','firstBatch':[seed]}})
        assert 'insert'in command
        captured.append(command['documents'][0])
        return encode({'ok':1.0,'n':1})
    monkeypatch.setattr(runtime._NoDatabase,'command',response)
    change={'host-unchanged':"const copy=d;copy._id='copy';",
        'same-value':"const copy=d;copy._id='copy';copy.i32=1;copy.nested.i32=1;copy.arr[0]=1;",
        'fresh-components':"const copy={_id:'copy',i32:d.i32,nested:d.nested,arr:[...d.arr]};"}[operation]
    result=native.evaluate("const d=db.docs.findOne({_id:'seed'});"+change+"db.docs.insertOne(copy);[typeof d.i32,d.i32===1,typeof d.arr[2]]")
    assert result.value==['number',True,'object']and result.child_reaped
    assert len(captured)==1
    expected={'_id':'copy','i32':1,'nested':{'i32':1},'arr':[1,1.0,Int64(1)]}
    if operation!='host-unchanged':
        expected['i32']=1.0;expected['nested']['i32']=1.0;expected['arr'][0]=1.0
    assert BSON.encode(captured[0],codec_options=BSON_CODEC)==BSON.encode(expected,codec_options=BSON_CODEC)


def test_native_preview_constructors_preserve_int32_limits_uuid_subtype_and_exact_dates(native):
    from datetime import datetime, timezone
    result=native.evaluate("({min:Int32('-2147483648'),max:NumberInt('2147483647'),uuid:Binary.createFromBase64('AAECAwQFBgcICQoLDA0ODw==',4),date:ISODate('2026-01-02T03:04:05.678Z'),offset:ISODate('2026-01-02T11:04:05.678000+08:00')})")
    date=datetime(2026,1,2,3,4,5,678000,timezone.utc)
    expected={'min':-2147483648,'max':2147483647,'uuid':Binary(bytes(range(16)),4),'date':date,'offset':date}
    assert BSON.encode(result.value,codec_options=BSON_CODEC)==BSON.encode(expected,codec_options=BSON_CODEC)
    assert result.child_reaped and result.commands==()


@pytest.mark.parametrize('expression',[
    "Int32('2147483648')","Int32('-2147483649')","Int32('1.5')","Int32('1e2')","Int32('')","Int32(true)",
    "Binary.createFromBase64('not base64',4)","Binary.createFromBase64('AA==',256)",
    "ISODate('not a date')","ISODate('2026-02-30T00:00:00.000Z')",
    "ISODate('2026-01-02T03:04:05.6780001Z')","ISODate('2026-01-02T03:04:05')",
])
def test_invalid_constructor_never_reaches_database_capability(native,expression):
    with pytest.raises(JavaScriptError)as raised:
        native.evaluate('db.runCommand({insert:"never",documents:[{_id:1,value:'+expression+'}]})')
    assert raised.value.result.child_reaped and raised.value.result.commands==()
    assert 'no database capability'not in str(raised.value)


def test_native_index_helpers_send_control_keys_and_keep_data_filter_numbers(native,monkeypatch):
    from opennavicat.js_engine import runtime
    captured=[]
    def command(self,tree):
        captured.append(decode(tree))
        return encode({'ok':1.0,'numIndexesBefore':1,'numIndexesAfter':2})
    monkeypatch.setattr(runtime._NoDatabase,'command',command)
    result=native.evaluate("db.docs.createIndex({a:1,b:-1},{name:'typed',partialFilterExpression:{limit:1},collation:{locale:'en',strength:Int32('2')},maxTimeMS:12,writeConcern:{w:1},comment:{purpose:'owned'},commitQuorum:'majority'})")
    assert result.value=='typed' and len(captured)==1
    command=captured[0];spec=command['indexes'][0]
    assert spec['key']=={'a':1,'b':-1} and all(type(x)is int for x in spec['key'].values())
    assert type(spec['partialFilterExpression']['limit'])is float
    assert spec['collation']['strength']==2 and type(spec['collation']['strength'])is int
    assert set(command)=={'createIndexes','indexes','maxTimeMS','writeConcern','comment','commitQuorum'}
    assert not any(key in spec for key in ('maxTimeMS','writeConcern','comment','commitQuorum'))
    assert result.child_reaped


def test_native_index_options_do_not_execute_accessors_before_validation(native):
    source="const opts={};Object.defineProperty(opts,'name',{enumerable:true,get(){db.runCommand({insert:'must-not-run',documents:[{_id:1}]});return 'owned'}});db.docs.createIndex({a:1},opts)"
    with pytest.raises(JavaScriptError,match='data fields')as raised:native.evaluate(source)
    assert raised.value.result.commands==() and raised.value.result.child_reaped


def test_native_index_pattern_array_does_not_execute_accessors_before_validation(native):
    source="const patterns=[{}];Object.defineProperty(patterns,0,{enumerable:true,get(){db.runCommand({insert:'must-not-run',documents:[{_id:1}]});return {a:1}}});db.docs.createIndexes(patterns)"
    with pytest.raises(JavaScriptError,match='data fields')as raised:native.evaluate(source)
    assert raised.value.result.commands==() and raised.value.result.child_reaped
