#!/usr/bin/env python3
"""Qualify the actual Oracle Qt window on a fresh publisher-pinned native fixture.

Uses offscreen Qt; does not open Navicat or an existing profile. The actual
window class is the target, not the backend's tests or fake-driver GUI tests.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import datetime as dt
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MODULE = "tests/test_oracle_window_integration.py"
TARGET = MODULE + "::TestOwnedOracleWindow"
UI_SOURCE_PATHS = (
    "opennavicat/ui/oracle_connection.py", "opennavicat/ui/dialogs.py",
    "opennavicat/ui/window.py", "opennavicat/ui/workspace.py",
    "opennavicat/ui/connection_manager.py", "opennavicat/ui/grid.py",
    "opennavicat/ui/theme.py", "opennavicat/storage.py", "opennavicat/tasks.py",
    MODULE, "scripts/test-oracle-window.py",
)
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([sys.argv[2],'--collect-only','-q','--color=no'],plugins=[Collect()]))
'''


def backend_source_paths(source=None):
    """Resolve the existing backend runner's literal scope without executing it."""
    tree = ast.parse(source if source is not None else (ROOT / "scripts/test-oracle.py").read_text())
    constants = {}
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.Name) and node.id in constants:
            return constants[node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(value(item) for item in node.elts)
        raise ValueError("Backend source scope must use explicit literal paths")
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in {"TARGET", "SOURCE_PATHS"}:
                    constants[target.id] = value(statement.value)
    paths = constants.get("SOURCE_PATHS")
    if not isinstance(paths, tuple) or not paths or len(set(paths)) != len(paths):
        raise ValueError("Backend source scope is missing or duplicated")
    for path in paths:
        if type(path) is not str or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("Backend source scope contains an unsafe path")
    return paths


def hashes():
    scopes = {"backend": backend_source_paths(), "window": UI_SOURCE_PATHS}
    return {name: {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                   for path in paths} for name, paths in scopes.items()}


def redact_output(text, secrets):
    for secret in secrets:
        if type(secret) is str and secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def child_environment(qt_platform):
    env = {key: value for key, value in os.environ.items() if not key.startswith('OPENNAVICAT_TEST_ORACLE_')}
    # This runner owns the complete declared class; ambient -k/--lf filters may
    # not turn a dynamically collected subset into a supposedly complete gate.
    env.pop('PYTEST_ADDOPTS', None)
    env['QT_QPA_PLATFORM'] = qt_platform
    return env


def strict_junit(data, nodeids):
    cases = ET.fromstring(data).findall(".//testcase")
    expected = Counter()
    for node in nodeids:
        parts = node.split("::")
        if len(parts) < 2 or parts[0] != MODULE:
            raise ValueError("Unexpected Oracle window node")
        expected[(".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]]), parts[-1])] += 1
    actual = Counter((case.get("classname"), case.get("name")) for case in cases)
    if not cases or actual != expected or len(set(nodeids)) != len(nodeids):
        raise ValueError("Oracle window JUnit differs from exact collected scope")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "error", "failure")):
        raise ValueError("Every actual Oracle window case must pass with zero skips")
    return {"passed": len(cases), "skipped": 0, "failed": 0, "exact_cases": nodeids}


def read_required(path):
    nodes = json.loads(Path(path).read_text())
    if (type(nodes) is not list or not nodes or any(type(node) is not str or not node.startswith(TARGET + '::') for node in nodes)
            or len(set(nodes)) != len(nodes)):
        raise ValueError('Actual Oracle window collection manifest is invalid')
    return nodes


def run_bounded_child(argv_factory, env, *, timeout=300, grace=20, limit=2 * 1024 * 1024):
    """Capture stdout/JUnit only in memory; allow pytest finalizers before kill.

    POSIX is the current fixture runner contract (macOS and native Linux CI).
    JUnit's inherited pipe prevents a traceback from writing a credential to a
    temporary XML file before its parent has had a chance to redact it.
    """
    if os.name != "posix" or not all(hasattr(os, name) for name in ('waitid', 'WNOWAIT', 'WEXITED', 'P_PID')):
        raise RuntimeError("Oracle window fixture runner currently requires POSIX")
    read_fd, write_fd = os.pipe()
    process = None
    reader_fds = []
    selector = selectors.DefaultSelector()
    buffers = {'output': bytearray(), 'junit': bytearray()}
    overflow, group_cleanup, timed_out, inherited_pipe_timeout = False, False, False, False
    pipe_cleanup_complete, final_group_cleanup, final_signal_errno = False, False, None
    def leader_done():
        # Do not reap before cleaning the owned group. Keeping the leader's PID
        # reserved prevents a reused PID/PGID from becoming a cleanup target.
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    def signal_group(value):
        done = leader_done()  # Verifies that this is still our waitable child.
        if not done:
            try:
                if os.getpgid(process.pid) != process.pid:
                    raise RuntimeError('Owned child process group identity changed')
            except ProcessLookupError:
                if not leader_done(): raise
        # Darwin stops exposing getpgid for an exited leader. WNOWAIT still
        # reserves our child's PID; start_new_session fixed its original group
        # to that PID, which therefore cannot have been reused by another job.
        try: os.killpg(process.pid, value)
        except ProcessLookupError: pass
    def pump(wait):
        nonlocal overflow
        for key, _events in selector.select(max(0, wait)):
            try: chunk = os.read(key.fd, 65536)
            except BlockingIOError: continue
            if not chunk:
                selector.unregister(key.fd)
                continue
            buffer = buffers[key.data]
            room = max(0, limit - len(buffer))
            buffer.extend(chunk[:room])
            if len(chunk) > room: overflow = True
    def drain_until(deadline):
        while time.monotonic() < deadline:
            if leader_done() and not selector.get_map(): return
            pump(min(.02, deadline - time.monotonic()))
    try:
        process = subprocess.Popen(argv_factory(write_fd), cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd); write_fd = None
        for fd, name in ((process.stdout.fileno(), 'output'), (read_fd, 'junit')):
            reader_fds.append(fd)
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        deadline, drain_deadline = time.monotonic() + timeout, None
        while True:
            done = leader_done()
            if done and not selector.get_map(): break
            now = time.monotonic()
            if done and drain_deadline is None:
                drain_deadline = min(deadline, now + min(.25, grace))
            remaining = min(deadline, drain_deadline if drain_deadline is not None else deadline) - now
            if remaining <= 0:
                timed_out, inherited_pipe_timeout = True, done and bool(selector.get_map())
                break
            pump(min(.02, remaining))
    finally:
        try:
            if process is not None:
                if not leader_done() or selector.get_map():
                    group_cleanup = True
                    signal_group(signal.SIGINT)
                    drain_until(time.monotonic() + grace)
                    if not leader_done() or selector.get_map():
                        signal_group(signal.SIGKILL)
                        drain_until(time.monotonic() + 5)
                pipe_cleanup_complete = not selector.get_map()
                # EOF is not evidence that the group is empty: a child may
                # close its diagnostic fds and keep running. The unreaped
                # leader still reserves the identity of our original group.
                try:
                    signal_group(signal.SIGKILL)
                except PermissionError as error:
                    # An all-zombie Darwin group can report EPERM. This is
                    # only pending evidence, never proof of absence: require
                    # the waitable leader to have exited, every pipe EOF, then
                    # reap and verify a real ESRCH below without more signals.
                    if not leader_done() or selector.get_map(): raise
                    final_signal_errno = error.errno
                final_group_cleanup = True
                process.wait(timeout=5)
        finally:
            selector.close()
            if process is not None and process.stdout is not None: process.stdout.close()
            if write_fd is not None: os.close(write_fd)
            if read_fd is not None: os.close(read_fd)
    readers_closed = True
    for fd in reader_fds:
        try: os.fstat(fd)
        except OSError as error: readers_closed = readers_closed and error.errno == errno.EBADF
        else: readers_closed = False
    group_absent, post_reap_group_errno = False, None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        # Only a read-only existence check is permitted after reaping. Never
        # send a destructive signal after the reserved leader PID is released.
        try: os.killpg(process.pid, 0)
        except ProcessLookupError:
            group_absent = True
            post_reap_group_errno = errno.ESRCH
            break
        except PermissionError as error: post_reap_group_errno = error.errno
        else: post_reap_group_errno = 0
        time.sleep(.01)
    return {"returncode": process.returncode, "pid": process.pid, "reaped": process.poll() is not None,
            "timed_out": timed_out, "output_limited": bool(overflow),
            "inherited_pipe_timeout": inherited_pipe_timeout, "group_cleanup_requested": group_cleanup,
            "pipe_cleanup_complete": pipe_cleanup_complete, "reader_threads": 0, "readers_closed": readers_closed,
            "final_group_cleanup": final_group_cleanup, "process_group_absent": group_absent,
            "final_signal_errno": final_signal_errno, "post_reap_group_errno": post_reap_group_errno,
            "output": buffers['output'].decode("utf-8", errors="replace"),
            "junit": buffers['junit'].decode("utf-8", errors="replace")}


def _load_fixture():
    spec = importlib.util.spec_from_file_location("oracle_owned_fixture", ROOT / "tests/oracle_owned_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-host")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pull-image", action="store_true")
    parser.add_argument("--cleanup-interrupted", action="store_true")
    parser.add_argument("--qt-platform", choices=("offscreen", "cocoa"), default="offscreen")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("This actual-fixture runner currently requires macOS/Linux")
    if args.qt_platform == 'cocoa' and platform.system() != 'Darwin':
        parser.error('The Cocoa platform is accepted only on macOS')
    if args.cleanup_interrupted and (args.output is None or args.pull_image):
        parser.error('Cleanup requires --output and cannot pull images')
    suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = (args.output or ROOT / "artifacts/oracle-window-actual" / ("actual-" + suffix)).resolve()
    if args.cleanup_interrupted:
        out.mkdir(parents=True, exist_ok=True)
        try:
            cleanup = _load_fixture().cleanup_interrupted(out, docker_host=args.docker_host)
        except BaseException as error:
            cleanup = {'passed': False, 'cleanup_only': True, 'no_start': True,
                       'no_database_connection': True, 'error_class': type(error).__name__}
        (out / 'cleanup-only.json').write_text(json.dumps(cleanup, indent=2) + '\n')
        print(json.dumps(cleanup))
        return 0 if cleanup['passed'] else 1
    out.mkdir(parents=True, exist_ok=False)
    before = hashes()
    (out / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    receipt = {"base_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_before": before, "python": platform.python_version(), "host_architecture": platform.machine(),
        "target": TARGET, "requested_qt_platform": args.qt_platform,
        "native_cocoa": args.qt_platform == 'cocoa', "human_ui_acceptance": False,
        "reference_ui_acceptance": False, "passed": False}
    fixture = None
    secrets = ()
    try:
        receipt['phase'] = 'collection'
        env = child_environment(args.qt_platform)
        required = out / 'required-cases.json'
        collect = subprocess.run([sys.executable, "-c", COLLECT, str(required), TARGET],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        (out / "collect.log").write_text(collect.stdout + collect.stderr)
        receipt['collection_exit_code'] = collect.returncode
        if collect.returncode or not required.is_file():
            raise RuntimeError("Actual Oracle window scope collection failed")
        nodes = read_required(required)
        receipt["expected_cases"] = nodes
        fixture = _load_fixture().OwnedOracle(out / "owned-fixture", docker_host=args.docker_host)
        # Keep the shared fixture's exact cleanup identity. This additional
        # caller scope distinguishes the window qualification from backend tests.
        fixture.receipt["caller_scope"] = TARGET
        fixture.save()
        secrets = (fixture.admin_password, fixture.app_password, fixture.denied_password)
        receipt['phase'] = 'fixture_start_and_seed'
        fixture.start(pull=args.pull_image)
        receipt['phase'] = 'window_fixture_setup'
        slow_view = "ONVW_COMPUTE_" + fixture.run_id[:12].upper()
        assert re.fullmatch(r"ONVW_COMPUTE_[A-F0-9]{12}", slow_view)
        # Add one generated fixture-only view. Existing data and server settings
        # are not changed; normal application sessions never execute this DDL.
        with fixture.app.cursor() as cursor:
            cursor.execute('CREATE VIEW "' + slow_view + '" AS SELECT SUM(SQRT(A.N*B.N)) AS TOTAL FROM '
                '(SELECT LEVEL N FROM DUAL CONNECT BY LEVEL<=100000) A CROSS JOIN '
                '(SELECT LEVEL N FROM DUAL CONNECT BY LEVEL<=100000) B')
            cursor.execute("SELECT OBJECT_NAME,OBJECT_TYPE,STATUS FROM USER_OBJECTS WHERE OBJECT_NAME=:name", {"name": slow_view})
            assert cursor.fetchall() == [(slow_view, "VIEW", "VALID")]
        fixture.receipt["window_compute_view"] = slow_view
        fixture.save()
        env.update(fixture.environment())
        case_dir = out / "cases"; case_dir.mkdir()
        env.update(OPENNAVICAT_TEST_ORACLE_WINDOW="1", OPENNAVICAT_TEST_ORACLE_WINDOW_REPORT_DIR=str(case_dir),
                   OPENNAVICAT_TEST_ORACLE_WINDOW_COMPUTE_VIEW=slow_view,
                   OPENNAVICAT_TEST_ORACLE_WINDOW_QT_PLATFORM=args.qt_platform)
        receipt['phase'] = 'actual_window_tests'
        result = run_bounded_child(lambda fd: [sys.executable, "-m", "pytest", "-q", "-W", "error", TARGET,
            "--junitxml=/dev/fd/" + str(fd)], env)
        (out / "pytest.log").write_text(redact_output(result.pop("output"), secrets))
        junit = redact_output(result.pop("junit"), secrets)
        (out / "results.xml").write_text(junit)
        receipt["test_process"] = result
        if (result["returncode"] or result["timed_out"] or result["output_limited"] or not result["reaped"]
                or result['group_cleanup_requested'] or not result['pipe_cleanup_complete'] or not result['readers_closed']
                or not result['final_group_cleanup'] or not result['process_group_absent']):
            raise RuntimeError("Actual Oracle window process did not complete successfully")
        receipt["tests"] = strict_junit(junit, nodes)
        ledgers = sorted(case_dir.glob("*.json"))
        if len(ledgers) != len(nodes):
            raise RuntimeError("Every actual window case must produce a ledger")
        cases = [json.loads(path.read_text()) for path in ledgers]
        if (Counter(case["nodeid"] for case in cases) != Counter(nodes)
                or not all(case.get("passed") and case.get("application_sessions_after_close") == 0
                           and case.get('qt_platform') == args.qt_platform for case in cases)):
            raise RuntimeError("Oracle window case ledgers do not prove completion and session cleanup")
        receipt.update(case_ledgers=[path.name for path in ledgers],
                       actual_qt_platforms=sorted({case['qt_platform'] for case in cases}), passed=True)
    except BaseException as error:
        receipt["error_class"] = type(error).__name__
        detail = error.args[0] if getattr(error, 'args', ()) else None
        full = getattr(detail, 'full_code', None)
        codes = [full] if type(full) is str and re.fullmatch(r'(?:ORA|DPY|DPI)-[0-9]{4,5}', full) else []
        message = getattr(detail, 'message', None)
        if type(message) is str:
            codes.extend(code for code in re.findall(r'(?:ORA|DPY|DPI)-[0-9]{4,5}', message[:4096]) if code not in codes)
        receipt['safe_driver_codes'] = codes[:12]
    finally:
        if fixture is not None:
            try:
                fixture.close()
                fixture.scan_secrets()
                check = fixture.docker("inspect", fixture.cid, check=False) if fixture.cid else None
                receipt["independent_exact_id_absent"] = bool(check is not None and check.returncode == 1
                    and "no such" in check.stderr.lower())
            except BaseException as error:
                receipt.update(passed=False, cleanup_error_class=type(error).__name__)
            receipt.update(exact_container_id=fixture.cid, cleanup=fixture.receipt.get("exact_id_absent") is True,
                application_sessions_after_test_process_exit=fixture.receipt.get("application_sessions_after_test_process_exit"))
        else:
            receipt.update(cleanup=True, independent_exact_id_absent=True)
        after = hashes()
        (out / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
        receipt.update(source_after=after, source_unchanged=before == after)
        leaked = False
        for path in out.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                for secret in secrets:
                    if secret.encode() in data:
                        leaked = True
                        data = data.replace(secret.encode(), b"[REDACTED]")
                if leaked and data != path.read_bytes(): path.write_bytes(data)
        receipt["secret_scan_passed"] = not leaked
        receipt["passed"] = bool(receipt["passed"] and receipt["source_unchanged"] and receipt["cleanup"]
            and receipt["independent_exact_id_absent"] and not leaked)
        (out / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "verification": str(out / "verification.json")}))
    return 0 if receipt["passed"] else 1


def interrupted(*_args):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
