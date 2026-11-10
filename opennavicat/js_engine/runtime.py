"""Controlled native child process; parent MongoDB operations remain authoritative.

Experimental macOS ARM64 integration only. Not imported by the released UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import hashlib
import os
import platform
from pathlib import Path
import secrets
import selectors
import signal
import struct
import subprocess
import tempfile
import threading
import time

from ..database import DatabaseError
from .bridge import CommandReceipt, MongoDBBridge
from .codec import decode

MAX_FRAME = 1024*1024


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
            "stack_bytes":(65536,8*1024*1024),"max_jobs":(1,100000),"max_commands":(1,64),
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

    @property
    def uncertain_writes(self):
        return any(row.writes and row.outcome == "uncertain" for row in self.commands)

    @property
    def confirmed_write_count(self):
        return sum(row.affected for row in self.commands if row.writes and row.outcome in {"acknowledged","acknowledged_error"})


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
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise DatabaseError("This controlled native helper is qualified only on macOS ARM64")
        receipt = json.loads(self.helper.with_name("build-receipt.json").read_text())
        if (receipt.get("source_revision") != "d7215b0ad3fc2c562e8317fd9bca058fd4863804"
            or receipt.get("archive_sha256") != "a49defe70cbcae9226d9fac025af116e87ebae2440dcff1ca3c80b79e1cdef0b"
            or receipt.get("helper_sha256") != hashlib.sha256(self.helper.read_bytes()).hexdigest()):
            raise DatabaseError("The controlled helper does not match its pinned official-source build receipt")

    def execute(self, session, source, *, database=None, limits=None, cancel_event=None):
        limits = limits or ExecutionLimits()
        if not isinstance(source,str) or len(source.encode()) > MAX_FRAME:
            raise DatabaseError("JavaScript source exceeds its text byte limit")
        cancelled = threading.Event()
        finished = threading.Event()
        external = cancel_event or threading.Event()
        started = time.monotonic()
        deadline = started + limits.timeout_ms/1000
        bridge = MongoDBBridge(session, database or session._selected_schema or session.config.database,
            deadline=deadline, cancelled=cancelled.is_set, max_rows=limits.max_rows, max_bson_bytes=limits.max_bson_bytes)
        token = secrets.token_hex(32)
        event = None
        failure = ""
        hard_killed = False
        process = None
        selector = None
        cancellation_thread = None
        watchdog = None
        request_number = 0

        def stop_child():
            nonlocal hard_killed
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    hard_killed = True
                except ProcessLookupError:
                    pass

        def cancel_database():
            # The owning execute() waits for this thread before releasing the
            # outer session operation. Late cancellation cannot hit another run.
            try:
                session.cancel()
            except DatabaseError:
                pass  # The result still reports interruption/uncertainty.

        def watch():
            nonlocal cancellation_thread
            while not finished.wait(.005):
                if external.is_set() or time.monotonic() >= deadline:
                    cancelled.set()
                    stop_child()  # independent of a blocked parent DB call
                    # Reap here too, even while execute() is still blocked in
                    # a host adapter call. Popen serializes concurrent waits.
                    try:
                        process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        pass
                    cancellation_thread = threading.Thread(target=cancel_database, name="MongoJS-cancel")
                    cancellation_thread.start()
                    return

        try:
            if external.is_set():
                cancelled.set()
                raise DatabaseError("JavaScript execution cancelled before child launch")
            with session._operation(), tempfile.TemporaryDirectory(prefix="opennavicat-js-") as directory:
                process = subprocess.Popen([str(self.helper),str(limits.timeout_ms),str(limits.heap_bytes),
                    str(limits.stack_bytes),str(limits.max_jobs),token],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,cwd=directory,env={"LANG":"C.UTF-8"},close_fds=True,start_new_session=True)
                watchdog = threading.Thread(target=watch,name="MongoJS-watchdog")
                watchdog.start()
                bootstrap = Path(__file__).with_name("bootstrap.js").read_bytes()
                process.stdin.write(_frame(bootstrap)+_frame(source.encode()));process.stdin.flush()
                selector = selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
                buffer = b""
                cumulative = 0
                while True:
                    if cancelled.is_set():
                        raise DatabaseError("JavaScript execution cancelled or timed out; committed writes are retained")
                    ready = selector.select(.01)
                    if not ready:
                        if process.poll() is not None:
                            break
                        continue
                    chunk = os.read(process.stdout.fileno(),65536)
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
            if selector is not None:
                selector.close()
            if process is not None:
                try:
                    process.wait(timeout=.25)
                except subprocess.TimeoutExpired:
                    stop_child();process.wait()
                stderr = process.stderr.read(8193).decode(errors="replace")[:8192]
                for stream in (process.stdin,process.stdout,process.stderr):
                    stream.close()
            else:
                stderr = ""
        result = JavaScriptResult(value,tuple(bridge.receipts),(time.monotonic()-started)*1000,
            cancelled.is_set(),process is not None and process.poll() is not None,hard_killed,stderr,failure,bridge.open_cursors,bridge.open_sessions)
        if failure:
            raise JavaScriptError(failure,result)
        return result
