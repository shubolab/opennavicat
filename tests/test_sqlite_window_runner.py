"""Reject incomplete/tampered receipts and exercise real owned leader failures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sqlite_window_qualification", ROOT / "scripts/test-sqlite-window.py")
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def nodes():
    return [module + "::test_owned[parameter::with-delimiter]" for module in runner.SCOPES]


def report(path, addresses, tag=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for node in addresses:
        module, name = runner.case_key(node)
        case = ET.SubElement(suite, "testcase", classname=module, name=name)
        if tag:
            ET.SubElement(case, tag)
    ET.ElementTree(root).write(path)


def test_junit_requires_exact_parametrized_multiset_without_deduplicating(tmp_path):
    path = tmp_path / "results.xml"
    report(path, nodes())
    assert runner.strict_junit(path, nodes(), nodes())["passed"] == len(nodes())
    report(path, [*nodes(), nodes()[0]])
    with pytest.raises(ValueError, match="multiset"):
        runner.strict_junit(path, nodes(), nodes())


@pytest.mark.parametrize("tag", ["skipped", "failure", "error"])
def test_no_skip_or_failure_can_become_a_qualification(tmp_path, tag):
    path = tmp_path / "results.xml"; report(path, nodes(), tag)
    with pytest.raises(ValueError, match="zero skips"):
        runner.strict_junit(path, nodes(), nodes())


@pytest.mark.parametrize("change", ["empty", "missing-module", "duplicate", "foreign", "reordered-run", "missing-run"])
def test_collection_and_run_scope_cannot_be_filtered_or_substituted(tmp_path, change):
    collected, executed = nodes(), nodes()
    if change == "empty": collected = []
    elif change == "missing-module": collected = collected[:-1]
    elif change == "duplicate": collected = [*collected, collected[0]]
    elif change == "foreign": collected[0] = "tests/test_other.py::test_owned"
    elif change == "reordered-run": executed.reverse()
    else: executed.pop()
    path = tmp_path / "results.xml"; report(path, nodes())
    with pytest.raises(ValueError): runner.strict_junit(path, collected, executed)


def runtime(owned):
    return {"phase": "run", "run_id": "owned-run", "exitstatus": 0, "actual_qpa": "cocoa", "source_root": str(ROOT),
        "data_directory": str(owned / "store"), "remaining_db_optins": [], "keyring_attempts": 0,
        "subprocess_attempts": 0, "visible_windows_after_session": 0, "python_process_spawns_blocked": True,
        "application_imports": {"opennavicat": str(ROOT / "opennavicat/__init__.py")},
        "calls": [{"nodeid": node, "qpa": "cocoa"} for node in nodes()]}


@pytest.mark.parametrize("mutation", ["offscreen", "foreign-import", "wrong-store", "db-optin", "keyring", "spawn", "live-window", "missing-call", "stale-run", "nonzero-exit"])
def test_runtime_requires_actual_native_qpa_owned_source_and_cleanup(tmp_path, mutation):
    value = runtime(tmp_path)
    runner.verify_runtime(value, qpa="cocoa", phase="run", run_id="owned-run", owned=tmp_path, nodes=nodes())
    if mutation == "offscreen": value["actual_qpa"] = "offscreen"
    elif mutation == "foreign-import": value["application_imports"] = {"opennavicat": str(tmp_path / "foreign.py")}
    elif mutation == "wrong-store": value["data_directory"] = str(tmp_path / "unowned")
    elif mutation == "db-optin": value["remaining_db_optins"] = ["OPENNAVICAT_TEST_POSTGRES_PORT"]
    elif mutation == "keyring": value["keyring_attempts"] = 1
    elif mutation == "spawn": value["subprocess_attempts"] = 1
    elif mutation == "live-window": value["visible_windows_after_session"] = 1
    elif mutation == "missing-call": value["calls"].pop()
    elif mutation == "stale-run": value["run_id"] = "previous-run"
    else: value["exitstatus"] = 1
    with pytest.raises(ValueError):
        runner.verify_runtime(value, qpa="cocoa", phase="run", run_id="owned-run", owned=tmp_path, nodes=nodes())


def test_child_environment_owns_store_and_clears_subset_and_endpoint_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNAVICAT_TEST_POSTGRES_PORT", "generated-do-not-contact")
    monkeypatch.setenv("OPENNAVICAT_DATA_DIR", "generated-unowned")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k one-test-only")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PYTHONPATH", "generated-native-dll-bootstrap")
    env = runner.child_environment("cocoa", tmp_path)
    assert env["QT_QPA_PLATFORM"] == "cocoa"
    assert env["OPENNAVICAT_DATA_DIR"] == str(tmp_path / "store")
    assert "OPENNAVICAT_TEST_POSTGRES_PORT" not in env and "PYTEST_ADDOPTS" not in env
    assert env["PYTHONPATH"].split(os.pathsep) == [str(ROOT), "generated-native-dll-bootstrap"]
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env.get("HOME") == os.environ.get("HOME")


@pytest.mark.parametrize("mode", ["normal", "failure", "timeout", "overflow"])
def test_real_file_backed_leader_wait_output_bound_and_failure_cleanup(tmp_path, mode):
    code = {"normal": "print('owned-complete')", "failure": "raise SystemExit(3)",
            "timeout": "import time;time.sleep(10)", "overflow": "print('x'*100000)"}[mode]
    path = tmp_path / "owned.log"
    result = runner.run_file_leader([sys.executable, "-c", code], dict(os.environ), path,
        timeout=.15 if mode == "timeout" else 10, grace=.1, limit=1024)
    assert result["reaped"] and result["log_handle_closed"]
    assert result["stdout_transport"] == "file-backed" and result["descendant_cleanup_claimed"] is False
    assert len(result["output"].encode()) <= 1024
    assert runner.process_passed(result) is (mode == "normal")
    assert result["timed_out"] is (mode == "timeout")
    assert result["output_limited"] is (mode == "overflow")
    path.unlink()  # Also proves the file owner is closed on Windows.


def test_actual_cli_rejects_offscreen_as_native_without_creating_evidence(tmp_path):
    target = tmp_path / "not-native"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/test-sqlite-window.py"),
        "--qt-platform", "offscreen", "--output", str(target)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and not target.exists()


def test_actual_offscreen_application_cannot_be_reported_as_cocoa(tmp_path):
    owned, out = tmp_path / "owned", tmp_path / "out"
    owned.mkdir(); out.mkdir()
    for name in ("store", "config", "data", "cache", "appdata", "localappdata", "tmp"):
        (owned / name).mkdir()
    env = runner.child_environment("offscreen", owned)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/test-sqlite-window.py"), "--_child",
        "collect", str(out), str(owned), "cocoa", "owned-negative"], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and "actual Qt platform differs" in result.stderr
    assert not (out / "run-runtime.json").exists()


def test_actual_diagnostic_child_collects_all_modules_and_records_its_offscreen_qpa(tmp_path):
    owned, out = tmp_path / "owned", tmp_path / "out"
    owned.mkdir(); out.mkdir()
    for name in ("store", "config", "data", "cache", "appdata", "localappdata", "tmp"):
        (owned / name).mkdir()
    env = runner.child_environment("offscreen", owned)
    env["PYTEST_ADDOPTS"] = "-k must-not-filter-owned-scope"  # The child also owns ambient-filter removal.
    result = subprocess.run([sys.executable, str(ROOT / "scripts/test-sqlite-window.py"), "--_child",
        "collect", str(out), str(owned), "offscreen", "owned-diagnostic"], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    required = runner.manifest(json.loads((out / "collect-cases.json").read_text()))
    assert {node.split("::", 1)[0] for node in required} == set(runner.SCOPES)
    receipt = json.loads((out / "collect-runtime.json").read_text())
    assert receipt["actual_qpa"] == "offscreen" and receipt["subprocess_attempts"] == receipt["keyring_attempts"] == 0
    assert receipt["data_directory"] == str(owned / "store")
    assert not (out / "verification.json").exists()  # A child receipt is not native qualification.


def test_workflow_keeps_six_targets_and_adds_native_gate_in_same_validation_artifact():
    text = (ROOT / ".github/workflows/build.yml").read_text()
    for target in ("windows-amd64", "windows-arm64", "macos-amd64", "macos-arm64", "linux-amd64", "linux-arm64"):
        assert "id: " + target in text
    assert "xvfb-run -a python scripts/test-sqlite-window.py --qt-platform xcb" in text
    assert "artifacts/sqlite-window-native/" in text
    assert "name: validation-${{ matrix.id }}" in text
    assert "needs: [desktop, database-security, oracle-native]" in text
    assert "python scripts/package-installer.py" in text
