"""Controlled native child process; parent MongoDB operations remain authoritative.

Experimental native qualification branch. Not imported by the released UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import nullcontext
import json
import copy
import hashlib
import os
import platform
from pathlib import Path
import secrets
import queue
import struct
import subprocess
import tempfile
import threading
import time

from ..database import DatabaseError
from .records import CommandReceipt
from .codec import decode

MAX_FRAME = 1024*1024


def helper_machine(path):
    """Read the actual controlled executable, independently of its receipt."""
    data = Path(path).read_bytes()
    try:
        if data[:2] == b'MZ':
            offset = struct.unpack_from('<I', data, 0x3c)[0]
            if data[offset:offset+4] != b'PE\0\0': raise ValueError('PE signature')
            return {0x8664:'amd64', 0xaa64:'arm64'}[struct.unpack_from('<H', data, offset+4)[0]]
        if data[:6] == b'\x7fELF\x02\x01':
            return {62:'amd64',183:'arm64'}[struct.unpack_from('<H',data,18)[0]]
        if data[:4] == b'\xcf\xfa\xed\xfe':
            return {0x1000007:'amd64',0x100000c:'arm64'}[struct.unpack_from('<I',data,4)[0]]
    except (KeyError, ValueError, struct.error) as exc:
        raise DatabaseError('Invalid native JavaScript helper image') from exc
    raise DatabaseError('The JavaScript helper is not a supported native 64-bit executable')


class _NoDatabase:
    receipts = ()
    open_cursors = open_sessions = 0
    def command(self, tree):
        raise DatabaseError("This VM execution has no database capability")


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_ms: int = 5000
    heap_bytes: int = 32*1024*1024
    stack_bytes: int = 256*1024
    max_jobs: int = 1000
    max_commands: int = 64
    max_rows: int = 1000
    max_bson_bytes: int = 256*1024

    def __post_init__(self):
        bounds = {"timeout_ms":(10,120000),"heap_bytes":(1024*1024,256*1024*1024),
            "stack_bytes":(65536,1024*1024),"max_jobs":(1,100000),"max_commands":(1,64),
            "max_rows":(1,100000),"max_bson_bytes":(1024,256*1024)}
        for name,(low,high) in bounds.items():
            value = getattr(self,name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid execution limit: {name}")


@dataclass(frozen=True)
class JavaScriptResult:
    value: object = field(default=None, repr=False)
    commands: tuple[CommandReceipt,...] = ()
    elapsed_ms: float = 0
    cancelled: bool = False
    child_reaped: bool = False
    hard_killed: bool = False
    stderr: str = field(default="", repr=False)
    error: str = ""
    open_cursors: int = 0
    open_sessions: int = 0
    native_state: dict = field(default_factory=dict)
    resource_exhausted: bool = False
    # Retain JS-only type distinctions (e.g. BigInt(1) vs BSON Int32(1))
    # for presentation/export. The decoded Python value alone cannot do so.
    value_tree: list | None = field(default=None, repr=False)

    @property
    def uncertain_writes(self):
        return any(row.writes and row.outcome == "uncertain" for row in self.commands)

    @property
    def confirmed_write_count(self):
        """Sum only server-reported n; e.g. $out has no affected-row count."""
        return sum(row.affected or 0 for row in self.commands if row.writes and row.outcome in {"acknowledged","acknowledged_error"})

    @property
    def has_confirmed_writes(self):
        return any(row.writes and row.outcome in {"acknowledged","acknowledged_error"}for row in self.commands)


class JavaScriptError(DatabaseError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def _frame(payload):
    if len(payload) > MAX_FRAME:
        raise DatabaseError("JavaScript IPC frame limit exceeded")
    return struct.pack(">I",len(payload))+payload


class NativeJavaScript:
    def __init__(self, helper: str | Path):
        self.helper = Path(helper).resolve(strict=True)
        machine=lambda value:{"arm64":"arm64","aarch64":"arm64","amd64":"amd64","x86_64":"amd64"}.get(value.lower())
        if platform.system()not in {"Darwin","Linux","Windows"}or machine(platform.machine())is None:
            raise DatabaseError("Unsupported controlled native helper target")
        receipt = json.loads(self.helper.with_name("build-receipt.json").read_text())
        if (receipt.get("source_revision") != "d7215b0ad3fc2c562e8317fd9bca058fd4863804"
            or receipt.get("archive_sha256") != "a49defe70cbcae9226d9fac025af116e87ebae2440dcff1ca3c80b79e1cdef0b"
            or receipt.get("operating_system") != platform.system()
            or receipt.get("native_image",{}).get("machine") != machine(platform.machine())
            or helper_machine(self.helper) != machine(platform.machine())
            or receipt.get("helper_sha256") != hashlib.sha256(self.helper.read_bytes()).hexdigest()):
            raise DatabaseError("The controlled helper does not match its pinned official-source build receipt")

    def evaluate(self, source, *, limits=None, cancel_event=None):
        """VM-only qualification. No database client or transport is imported."""
        return self.execute(None,source,limits=limits,cancel_event=cancel_event)

    def execute(self, session, source, *, database=None, limits=None, cancel_event=None):
        limits = limits or ExecutionLimits()
        if not isinstance(source,str) or len(source.encode()) > MAX_FRAME:
            raise DatabaseError("JavaScript source exceeds its text byte limit")
        cancelled = threading.Event()
        resource_denied = threading.Event()
        finished = threading.Event()
        external = cancel_event or threading.Event()
        started = time.monotonic()
        deadline = started + limits.timeout_ms/1000
        if session is None:
            bridge = _NoDatabase()
        else:
            from .bridge import MongoDBBridge
            bridge = MongoDBBridge(session, database or session._selected_schema or session.config.database,
                deadline=deadline, cancelled=lambda:cancelled.is_set()or resource_denied.is_set(), max_rows=limits.max_rows, max_bson_bytes=limits.max_bson_bytes)
        token = secrets.token_hex(32)
        event = None
        failure = ""
        hard_killed = False
        process = None
        reader = None
        temporary = None
        incoming = queue.Queue(maxsize=32)
        cancellation_thread = None
        watchdog = None
        request_number = 0

        def stop_child():
            nonlocal hard_killed
            if process.poll() is None:
                try:
                    # The controlled helper exposes no child-spawn API. Kill
                    # exactly this Popen child on every platform; group lookup
                    # can race a naturally exiting POSIX child at the deadline.
                    process.kill()
                    hard_killed = True
                except ProcessLookupError:
                    pass

        def cancel_database(marker):
            # A delayed control task must not cancel a later operation, even
            # after the interrupted bridge has unwound its active comment.
            try:
                if session is not None:session.cancel(expected_comment=marker)
            except DatabaseError:
                pass  # The result still reports interruption/uncertainty.

        def watch():
            nonlocal cancellation_thread
            while not finished.wait(.005):
                if external.is_set() or time.monotonic() >= deadline:
                    cancelled.set()
                    marker = session.cancellation_marker() if session is not None else None
                    stop_child()  # independent of a blocked parent DB call
                    # Reap here too, even while execute() is still blocked in
                    # a host adapter call. Popen serializes concurrent waits.
                    try:
                        process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
                    cancellation_thread = threading.Thread(target=cancel_database, args=(marker,), name="MongoJS-cancel")
                    cancellation_thread.start()
                    return

        def read_output():
            # Windows pipe handles are not select()-able sockets. A dedicated
            # bounded reader works for native pipes on all three operating systems.
            try:
                while not finished.is_set():
                    chunk=os.read(process.stdout.fileno(),65536)
                    while not finished.is_set():
                        try:incoming.put(chunk,timeout=.05);break
                        except queue.Full:pass
                    if not chunk:return
            except OSError:
                if not finished.is_set():
                    try:incoming.put_nowait(b"")
                    except queue.Full:pass

        try:
            if external.is_set():
                cancelled.set()
                raise DatabaseError("JavaScript execution cancelled before child launch")
            temporary=tempfile.TemporaryDirectory(prefix="opennavicat-js-")
            with session._operation()if session is not None else nullcontext():
                options={"creationflags":subprocess.CREATE_NO_WINDOW}if os.name=="nt"else{"start_new_session":True}
                process = subprocess.Popen([str(self.helper),str(limits.timeout_ms),str(limits.heap_bytes),
                    str(limits.stack_bytes),str(limits.max_jobs),token],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,cwd=temporary.name,env={"LANG":"C.UTF-8",**({"SystemRoot":os.environ["SystemRoot"]}if os.name=="nt"else{})},close_fds=True,**options)
                watchdog = threading.Thread(target=watch,name="MongoJS-watchdog")
                watchdog.start()
                reader=threading.Thread(target=read_output,name="MongoJS-output");reader.start()
                bootstrap = Path(__file__).with_name("bootstrap.js").read_bytes()
                process.stdin.write(_frame(bootstrap)+_frame(source.encode()));process.stdin.flush()
                buffer = b""
                cumulative = 0
                while True:
                    if cancelled.is_set():
                        raise DatabaseError("JavaScript execution cancelled or timed out; committed writes are retained")
                    try:chunk=incoming.get(timeout=.01)
                    except queue.Empty:continue
                    if not chunk:
                        break
                    cumulative += len(chunk)
                    if cumulative > 8*1024*1024:
                        raise DatabaseError("JavaScript cumulative IPC output limit exceeded")
                    buffer += chunk
                    while len(buffer)>=4:
                        length = struct.unpack(">I",buffer[:4])[0]
                        if length > MAX_FRAME:
                            raise DatabaseError("Native JavaScript emitted an oversized frame")
                        if len(buffer)<length+4:
                            break
                        payload,buffer = buffer[4:length+4],buffer[length+4:]
                        message = json.loads(payload)
                        if message.get("kind")=="resource_limit":
                            if message.get("operation")!=token:
                                raise DatabaseError("Invalid JavaScript resource-limit binding")
                            resource_denied.set()
                            raise DatabaseError("JavaScript memory allocation limit exceeded; further database operations denied")
                        if message.get("kind") != "bridge":
                            if event is not None or message.get("kind") not in {"result","error"}:
                                raise DatabaseError("Invalid native JavaScript terminal frame")
                            event = message
                            continue
                        request_number += 1
                        if event is not None or message.get("operation") != token or message.get("request") != request_number or request_number > limits.max_commands:
                            raise DatabaseError("Invalid JavaScript operation binding/request limit")
                        try:
                            request = json.loads(message["payload"])
                            if set(request) != {"kind","tree"} or request["kind"] != "command":
                                raise DatabaseError("Only structured BSON command messages are permitted")
                            response = {"ok":True,"tree":bridge.command(request["tree"])}
                        except Exception as error:
                            response = {"ok":False,"error":str(error)}
                        if not cancelled.is_set():
                            process.stdin.write(_frame(json.dumps(response,ensure_ascii=True,separators=(",",":")).encode()));process.stdin.flush()
                if event is None:
                    raise DatabaseError("JavaScript child terminated without a result" + (" after cancellation" if cancelled.is_set() else ""))
                if event["kind"] == "error":
                    raise DatabaseError(event.get("message","JavaScript execution failed"))
                if external.is_set() or time.monotonic() >= deadline:
                    cancelled.set()
                    raise DatabaseError("JavaScript execution completed after cancellation/deadline")
                value = decode(event["tree"],allow_big=True)
        except Exception as error:
            failure = str(error)
            value = None
        finally:
            finished.set()
            if watchdog is not None:
                watchdog.join()
            if cancellation_thread is not None:
                cancellation_thread.join()
            if process is not None:
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    stop_child();process.wait()
                if process.returncode != 0 and not failure:
                    failure="Native JavaScript child exited unsuccessfully after emitting its result ("+str(process.returncode)+")"
                    value=None
                if reader is not None:reader.join()
                stderr = process.stderr.read(8193).decode(errors="replace")[:8192]
                for stream in (process.stdin,process.stdout,process.stderr):
                    stream.close()
            else:
                stderr = ""
            if temporary is not None:temporary.cleanup()
        result = JavaScriptResult(value,tuple(bridge.receipts),(time.monotonic()-started)*1000,
            cancelled.is_set(),process is not None and process.poll() is not None,hard_killed,stderr,failure,bridge.open_cursors,bridge.open_sessions,event.get("native",{})if event else {},resource_denied.is_set(),
            copy.deepcopy(event['tree']) if event and not failure else None)
        if failure:
            raise JavaScriptError(failure,result)
        return result
