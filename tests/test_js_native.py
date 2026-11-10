"""Native helper qualification without a DB/Qt/SQLite fixture or user endpoint."""
from __future__ import annotations
import os
from pathlib import Path
import threading
import time

from bson import BSON, Binary, Decimal128, Int64
import pytest

from opennavicat.js_engine.codec import BSON_CODEC,JS_UNDEFINED
from opennavicat.js_engine.runtime import ExecutionLimits,JavaScriptError,NativeJavaScript


def test_native_bson_extension_is_loaded():
    import bson
    assert bson.has_c(),"Native qualification cannot silently use the pure Python BSON fallback"


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
    ('function again(){return again()+1;}again()','stack'),
    ('new Promise(()=>{})','pending'),
    ('Promise.reject(new Error("generated rejection"));42','generated rejection'),
    ('function again(){Promise.resolve().then(again)}again();42','job'),
    ('import("os")','load'),
    ('"x".repeat(300000)','output'),
])
def test_native_limits_errors_and_reaping(native,source,fragment):
    with pytest.raises(JavaScriptError) as raised:
        native.evaluate(source,limits=ExecutionLimits(timeout_ms=150,heap_bytes=4*1024*1024,max_jobs=50))
    result=raised.value.result
    assert fragment in str(raised.value).lower()
    assert result.child_reaped and result.elapsed_ms<2500 and result.stderr==""


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
