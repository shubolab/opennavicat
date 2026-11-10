"""Strict source/frozen/installed helper execution, without any database IO."""
import sys
import threading

from bson import BSON, Binary, Decimal128, Int64

from .codec import BSON_CODEC
from .payload import load_engine
from .runtime import ExecutionLimits, JavaScriptError

PROBE_FIELDS = ('native_js_helper_ready', 'native_js_bigint_bson_probe',
                'native_js_cancel_probe', 'native_js_child_reaped', 'native_js_no_console')


def probe_native_javascript():
    engine = load_engine()
    result = engine.evaluate("({big:9007199254740995n,long:Long('9007199254740995'),decimal:Decimal128('1.00'),binary:Binary(new Uint8Array([0,255,13,10]),128)})")
    value = dict(result.value)
    big = value.pop('big')
    expected = {'long': Int64(9007199254740995), 'decimal': Decimal128('1.00'), 'binary': Binary(b'\0\xff\r\n',128)}
    typed = (big == 9007199254740995 and result.value_tree[1][0][1] == ['js-bigint','9007199254740995']
        and BSON.encode(value,codec_options=BSON_CODEC) == BSON.encode(expected,codec_options=BSON_CODEC))
    cancelled = threading.Event()
    timer = threading.Timer(.15, cancelled.set)
    timer.start()
    try:
        try:
            engine.evaluate('while(true){}', limits=ExecutionLimits(timeout_ms=2000), cancel_event=cancelled)
        except JavaScriptError as error:
            interruption = error.result
        else:
            raise RuntimeError('Native JavaScript infinite-loop interruption unexpectedly succeeded')
    finally:
        timer.cancel(); timer.join()
    checks = dict(native_js_helper_ready=True, native_js_bigint_bson_probe=bool(typed),
        native_js_cancel_probe=bool(interruption.cancelled and interruption.hard_killed),
        native_js_child_reaped=bool(result.child_reaped and interruption.child_reaped),
        native_js_no_console=sys.platform!='win32' or result.native_state.get('windows_no_console') is True,
        native_js_bundled=bool(getattr(sys,'frozen',False)))
    if any(checks[key] is not True for key in PROBE_FIELDS):
        raise RuntimeError('The actual native JavaScript helper did not pass its strict execution probes')
    return checks
