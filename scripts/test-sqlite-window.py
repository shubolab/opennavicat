#!/usr/bin/env python3
"""Zero-skip SQLite database-page qualification on the host's actual Qt QPA."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCOPES = (
    "tests/test_sqlite_connection_databases_ui.py",
    "tests/test_sqlite_connection_databases_font_ui.py",
    "tests/test_sqlite_connection_databases_native_contents.py",
    "tests/test_sqlite_small_visuals.py",
)
NATIVE = {"Windows": "windows", "Darwin": "cocoa", "Linux": "xcb"}
LIMIT = 4 * 1024 * 1024


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    path = Path(path)
    if path.stat().st_size > LIMIT:
        raise ValueError("Qualification metadata exceeds its read bound")
    return json.loads(path.read_text(encoding="utf-8"))


def case_key(node):
    address, bracket, parameters = node.partition("[")
    parts = address.split("::")
    if len(parts) < 2 or parts[0] not in SCOPES:
        raise ValueError("Qualification contains an undeclared test address")
    return ".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]]), parts[-1] + bracket + parameters


def manifest(nodes):
    if type(nodes) is not list or not nodes or any(type(node) is not str for node in nodes):
        raise ValueError("Missing collection manifest")
    keys = [case_key(node) for node in nodes]
    if len(set(nodes)) != len(nodes) or len(set(keys)) != len(keys):
        raise ValueError("Duplicate qualification case")
    if {node.split("::", 1)[0] for node in nodes} != set(SCOPES):
        raise ValueError("Every declared SQLite module must be collected")
    return nodes


def strict_junit(path, collected, executed):
    manifest(collected)
    if executed != collected:
        raise ValueError("Run collection differs from the complete collection sequence")
    path = Path(path)
    if path.stat().st_size > LIMIT:
        raise ValueError("JUnit exceeds its read bound")
    cases = list(ET.parse(path).iter("testcase"))
    expected = Counter(case_key(node) for node in collected)
    actual = Counter((case.get("classname"), case.get("name")) for case in cases)
    if actual != expected or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("JUnit requires the exact collected multiset and zero skips/failures/errors")
    return {"total": len(cases), "passed": len(cases), "skipped": 0, "failed": 0, "errors": 0}


def child_environment(qpa, owned):
    owned = Path(owned)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("OPENNAVICAT_", "PYTEST_"))}
    for key in ("PYTHONHOME", "PYTHONSTARTUP", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS"):
        env.pop(key, None)
    # Keep the source DLL/sitecustomize bootstrap used by Windows ARM64 libpq.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH", ""))))
    env.update(QT_QPA_PLATFORM=qpa, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONWARNINGS="error", PYTHONUTF8="1",
               OPENNAVICAT_DATA_DIR=str(owned / "store"), XDG_CONFIG_HOME=str(owned / "config"),
               XDG_DATA_HOME=str(owned / "data"), XDG_CACHE_HOME=str(owned / "cache"),
               APPDATA=str(owned / "appdata"), LOCALAPPDATA=str(owned / "localappdata"),
               TMPDIR=str(owned / "tmp"), TEMP=str(owned / "tmp"), TMP=str(owned / "tmp"))
    return env


def run_file_leader(argv, env, raw_log, *, timeout=180, grace=5, limit=LIMIT):
    """Windows-compatible file-backed owner; no process-tree cleanup claim.

    This declared child scope rejects Python subprocess/fork/exec attempts.
    The log-size limit is polled, so disk bytes can exceed it between polls;
    retained/read output is bounded. Only this Popen leader is signalled.
    """
    process = None
    timed_out = limited = False
    start = time.monotonic()
    with Path(raw_log).open("w+b") as log:
        try:
            process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            while process.poll() is None:
                limited = os.fstat(log.fileno()).st_size > limit
                timed_out = time.monotonic() - start >= timeout
                if limited or timed_out:
                    break
                try:
                    process.wait(timeout=.02)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process is not None and process.poll() is None:
                if os.name == "nt":
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    except OSError:
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process is not None:
                process.wait(timeout=5)
            log.flush()
            size = os.fstat(log.fileno()).st_size
            limited = limited or size > limit
            log.seek(0)
            output = log.read(limit).decode("utf-8", errors="replace")
    return {"returncode": process.returncode, "pid": process.pid, "reaped": process.returncode is not None,
            "timed_out": timed_out, "output_limited": limited, "output": output,
            "stdout_transport": "file-backed", "observed_log_bytes": size, "retained_output_bytes_limit": limit,
            "log_handle_closed": log.closed, "cleanup_scope": "owned-leader-only",
            "descendant_cleanup_claimed": False, "seconds": round(time.monotonic() - start, 3)}


def bounded_child(argv, env, raw_log, *, timeout):
    if os.name == "nt":
        return run_file_leader(argv, env, raw_log, timeout=timeout)
    spec = importlib.util.spec_from_file_location("sqlite_posix_child_owner", ROOT / "scripts/test-oracle-window.py")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    result = owner.run_bounded_child(lambda _fd: argv, env, timeout=timeout, grace=10, limit=LIMIT)
    result.pop("junit")
    result.update(stdout_transport="bounded-pipe", cleanup_scope="owned-posix-process-group")
    return result


def process_passed(result):
    if result.get("returncode") != 0 or result.get("timed_out") or result.get("output_limited") or not result.get("reaped"):
        return False
    if result.get("cleanup_scope") == "owned-leader-only":
        return result.get("log_handle_closed") is True and result.get("stdout_transport") == "file-backed"
    return all(result.get(key) for key in ("pipe_cleanup_complete", "readers_closed", "final_group_cleanup", "process_group_absent"))


def verify_runtime(value, *, qpa, phase, run_id, owned, nodes):
    if (value.get("phase") != phase or value.get("run_id") != run_id or value.get("exitstatus") != 0 or value.get("actual_qpa") != qpa
            or value.get("source_root") != str(ROOT) or value.get("data_directory") != str(Path(owned) / "store")
            or value.get("remaining_db_optins") or value.get("keyring_attempts") != 0
            or value.get("subprocess_attempts") != 0 or value.get("visible_windows_after_session") != 0
            or value.get("python_process_spawns_blocked") is not True):
        raise ValueError("Actual QPA/source/isolation/cleanup receipt is not qualified")
    imports = value.get("application_imports", {})
    if not imports or any(not Path(path).resolve().is_relative_to(ROOT / "opennavicat") for path in imports.values()):
        raise ValueError("Qualification imported a foreign application tree")
    if phase == "run":
        calls = value.get("calls", [])
        if [call.get("nodeid") for call in calls] != nodes or any(call.get("qpa") != qpa for call in calls):
            raise ValueError("Every collected case must execute on the requested actual QPA")


def source_snapshot():
    required = {*SCOPES, "scripts/test-sqlite-window.py", "scripts/test-oracle-window.py",
                "tests/test_sqlite_window_runner.py", ".github/workflows/build.yml"}
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    paths = sorted(required | {path for path in tracked if path.startswith("opennavicat/") and path.endswith(".py")})
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def git_snapshot(revision, paths):
    return {path: hashlib.sha256(subprocess.check_output(["git", "show", revision + ":" + path], cwd=ROOT)).hexdigest()
            for path in paths}


def child_main(phase, out, owned, qpa, run_id):
    os.environ.pop("PYTEST_ADDOPTS", None)
    import pytest
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QDialogButtonBox, QStyle, QStyleOptionFrame
    import keyring
    import opennavicat.storage as storage
    from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog

    out, owned = Path(out), Path(owned)
    attempts = {"keyring": 0, "subprocess": 0}
    def forbid_keyring(*_args, **_kwargs):
        attempts["keyring"] += 1
        raise RuntimeError("Qualification does not access the OS keyring")
    for name in ("get_password", "set_password", "delete_password"):
        setattr(keyring, name, forbid_keyring)
    def audit(event, _args):
        if event in {"subprocess.Popen", "os.fork", "os.posix_spawn", "os.exec", "os.system", "os.spawn"}:
            attempts["subprocess"] += 1
            raise RuntimeError("This qualification scope cannot spawn Python child processes")
    sys.addaudithook(audit)
    app = QApplication([])
    if app.platformName() != qpa:
        raise RuntimeError("The actual Qt platform differs from the requested native backend")
    store = storage.Store()
    if store.directory.resolve() != (owned / "store").resolve() or store.connections():
        raise RuntimeError("Qualification Store is not the new isolated owned Store")
    calls, geometry = [], []
    class Evidence:
        def pytest_collection_finish(self, session):
            write(out / (phase + "-cases.json"), [item.nodeid for item in session.items])
        def pytest_runtest_call(self, item):
            calls.append({"nodeid": item.nodeid, "qpa": app.platformName(), "style": app.style().objectName()})
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_teardown(self, item, nextitem):
            for widget in app.topLevelWidgets():
                if isinstance(widget, SQLiteDatabaseDialog):
                    edits = []
                    for edit in (widget.name_edit, widget.file_edit, widget.password_edit):
                        option = QStyleOptionFrame(); edit.initStyleOption(option)
                        content = edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, edit)
                        edits.append({"control": edit.objectName(), "font_pixels": edit.font().pixelSize(),
                            "glyph_height": edit.fontMetrics().height(), "height": edit.height(),
                            "style_contents": content.getRect(), "left": edit.mapTo(widget, QPoint()).x(),
                            "right": edit.mapTo(widget, QPoint(edit.width(), 0)).x()})
                    buttons = {name: {"width": button.width(), "height": button.height(),
                                     "right": button.mapTo(widget, QPoint(button.width(), 0)).x()}
                               for name, button in (("good", widget.buttons.button(QDialogButtonBox.StandardButton.Ok)),
                                                    ("cancel", widget.buttons.button(QDialogButtonBox.StandardButton.Cancel)),
                                                    ("browse", widget.browse_button))}
                    geometry.append({"nodeid": item.nodeid, "qpa": app.platformName(), "size": widget.size().toTuple(),
                                     "edit_geometry": edits, "button_geometry": buttons})
            yield
        def pytest_sessionfinish(self, session, exitstatus):
            app.processEvents()
            imports = {name: str(Path(module.__file__).resolve()) for name, module in tuple(sys.modules.items())
                       if name.startswith("opennavicat") and getattr(module, "__file__", None)}
            write(out / (phase + "-runtime.json"), {"phase": phase, "run_id": run_id,
                "exitstatus": int(exitstatus), "actual_qpa": app.platformName(), "source_root": str(ROOT),
                "application_imports": imports, "data_directory": str(store.directory.resolve()),
                "remaining_db_optins": sorted(key for key in os.environ if key.startswith("OPENNAVICAT_") and key != "OPENNAVICAT_DATA_DIR"),
                "keyring_attempts": attempts["keyring"], "subprocess_attempts": attempts["subprocess"],
                "python_process_spawns_blocked": True, "visible_windows_after_session": sum(widget.isVisible() for widget in app.topLevelWidgets()),
                "calls": calls, "geometry": geometry, "python": sys.version, "prefix": sys.prefix})
    args = ["-p", "pytestqt.plugin", "-o", "addopts=", "-q", "-W", "error", "--color=no",
            "--basetemp=" + str(owned / "pytest"), *SCOPES]
    args += ["--collect-only"] if phase == "collect" else ["--junitxml=" + str(out / "junit.xml")]
    return pytest.main(args, plugins=[Evidence()])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-platform", required=True, choices=tuple(NATIVE.values()))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if NATIVE.get(platform.system()) != args.qt_platform:
        parser.error("Use the host OS's actual native Qt platform")
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    receipt = {"passed": False, "run_id": run_id, "scope": list(SCOPES), "requested_qpa": args.qt_platform,
               "host_system": platform.system(), "host_architecture": platform.machine(), "human_reference_acceptance": False}
    isolated = None
    before = None
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt["source_revision"] = revision
    def interrupted(_number, _frame):
        raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    try:
        before = source_snapshot(); write(out / "source-before.json", before)
        blobs = git_snapshot(revision, before); write(out / "source-git.json", blobs)
        if before != blobs:
            raise RuntimeError("Qualification must use committed source bytes")
        with tempfile.TemporaryDirectory(prefix="opennavicat-sqlite-qualified-") as directory:
            isolated = Path(directory).resolve()
            for name in ("store", "config", "data", "cache", "appdata", "localappdata", "tmp"):
                (isolated / name).mkdir()
            env = child_environment(args.qt_platform, isolated)
            receipt["removed_environment_keys"] = sorted(key for key in os.environ if key.startswith(("OPENNAVICAT_", "PYTEST_")))
            for phase, timeout in (("collect", 90), ("run", 240)):
                receipt["phase"] = phase
                result = bounded_child([sys.executable, str(Path(__file__).resolve()), "--_child", phase,
                    str(out), str(isolated), args.qt_platform, run_id], env, isolated / (phase + ".raw"), timeout=timeout)
                (out / (phase + ".log")).write_text(result.pop("output"), encoding="utf-8")
                receipt[phase] = result; write(out / (phase + "-process.json"), result)
                if not process_passed(result):
                    raise RuntimeError("Qualification child did not complete normally")
                nodes = manifest(read_json(out / (phase + "-cases.json")))
                verify_runtime(read_json(out / (phase + "-runtime.json")), qpa=args.qt_platform,
                               phase=phase, run_id=run_id, owned=isolated, nodes=nodes)
            receipt["counts"] = strict_junit(out / "junit.xml", read_json(out / "collect-cases.json"), read_json(out / "run-cases.json"))
            receipt["actual_qpa"] = read_json(out / "run-runtime.json")["actual_qpa"]
            receipt["passed"] = True
    except (Exception, KeyboardInterrupt) as error:
        receipt.update(passed=False, error_class=type(error).__name__, error=str(error))
    finally:
        try:
            after = source_snapshot(); write(out / "source-after.json", after)
            receipt["source_equal_before_after_git"] = before is not None and before == after == git_snapshot(revision, after)
        except Exception as error:
            receipt.update(source_equal_before_after_git=False, source_error_class=type(error).__name__)
        receipt["same_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == revision
        receipt["owned_store_removed"] = isolated is not None and not isolated.exists()
        receipt["passed"] = bool(receipt["passed"] and receipt["source_equal_before_after_git"] and receipt["same_head"] and receipt["owned_store_removed"])
        write(out / "verification.json", receipt)
    print(json.dumps({key: receipt.get(key) for key in ("passed", "actual_qpa", "counts", "source_revision")}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--_child":
        raise SystemExit(child_main(*sys.argv[2:]))
    raise SystemExit(main())
