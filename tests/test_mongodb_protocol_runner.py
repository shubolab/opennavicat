"""The CI protocol gate must reject skipped, stale or incomplete evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


spec = importlib.util.spec_from_file_location("mongodb_protocol_ci",
    Path(__file__).resolve().parents[1] / "scripts/test-mongodb-protocol.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def report(path):
    suite = ET.Element("testsuite")
    for module, names in runner.REQUIRED.items():
        for name in names:
            ET.SubElement(suite, "testcase", classname="tests." + module, name=name)
    ET.ElementTree(suite).write(path)
    return suite


@pytest.mark.parametrize("kind", ["skipped", "failure", "error"])
def test_protocol_gate_rejects_any_nonpassed_case(tmp_path, kind):
    path = tmp_path / "report.xml"
    suite = report(path)
    ET.SubElement(suite[0], kind)
    ET.ElementTree(suite).write(path)
    with pytest.raises(RuntimeError, match="zero skips"):
        runner.check_junit(path)


def test_protocol_gate_requires_fresh_mainwindow_gridfs_and_declared_scope(tmp_path):
    path = tmp_path / "report.xml"
    suite = report(path)
    receipt = runner.check_junit(path)
    assert receipt["passed"] == sum(len(names) for names in runner.REQUIRED.values())
    assert receipt["skipped"] == 0 and set(receipt["modules"]) == set(runner.MODULES)
    gridfs = next(case for case in suite if case.get("name") == "test_actual_mainwindow_gridfs_tree_routing_and_native_download")
    suite.remove(gridfs)
    ET.ElementTree(suite).write(path)
    with pytest.raises(RuntimeError, match="Required real"):
        runner.check_junit(path)
    suite.append(gridfs)
    ET.SubElement(suite, "testcase", classname="tests.test_mongo_gridfs_ui", name="manual_reference_case")
    ET.ElementTree(suite).write(path)
    with pytest.raises(RuntimeError, match="outside its declared scope"):
        runner.check_junit(path)


def fixtures(directory, run_id):
    for module in runner.MODULES:
        value = dict(module=module, run_id=run_id, owner_token_verified=True,
            cleanup_removed=True, port_loopback_only=True,
            native_docker_architecture_matches=True, external_auth_readiness=True)
        (directory / (module + "-fixture.json")).write_text(json.dumps(value))
        # The real fixture naming is independent of a module's display name.
        (directory / (module + "-fixture.json")).rename(directory / ("fixture-" + module + ".json"))


def test_protocol_gate_rejects_stale_fixture_receipts(tmp_path):
    fixtures(tmp_path, "previous-run")
    with pytest.raises(RuntimeError, match="fresh owned fixture"):
        runner.check_fixtures(tmp_path, "current-run")
    assert len(runner.check_fixtures(tmp_path, "previous-run")) == 3


@pytest.mark.parametrize("key", ["cleanup_removed", "owner_token_verified", "port_loopback_only",
    "native_docker_architecture_matches", "external_auth_readiness"])
def test_protocol_gate_requires_cleanup_ownership_and_native_ready_server(tmp_path, key):
    fixtures(tmp_path, "current-run")
    path = tmp_path / "fixture-test_mongo_window.json"
    value = json.loads(path.read_text()); value[key] = False
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="readiness or cleanup"):
        runner.check_fixtures(tmp_path, "current-run")


def test_runner_never_uses_manual_endpoint_or_claims_reference_component_cases(tmp_path, monkeypatch):
    captured = {}
    class Process:
        def __init__(self, command, **kwargs):
            captured.update(command=command, **kwargs)
        def wait(self, timeout): return 0
        def poll(self): return 0
    monkeypatch.setenv("OPENNAVICAT_TEST_MONGODB_ENDPOINT", "private-manual-reference.json")
    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    assert runner.run_tests(tmp_path, tmp_path, "generated-run", 5) == 0
    environment = captured["env"]
    assert "OPENNAVICAT_TEST_MONGODB_ENDPOINT" not in environment
    assert environment["OPENNAVICAT_TEST_MONGODB"] == "1"
    assert environment["OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID"] == "generated-run"
    assert set(item for item in captured["command"] if item.startswith("tests/")) == {
        "tests/test_mongodb_session.py", "tests/test_mongodb_query.py", "tests/test_mongo_window.py"}


def emergency_receipt(directory, run_id="a"*32):
    token, identity = "b"*32, "c"*64
    name = "opennavicat-mongo-owned-" + token[:12]
    value = dict(run_id=run_id, owner_token=token, container_name=name,
        container_id=identity, cleanup_removed=False)
    path = directory / "fixture-own.json"
    path.write_text(json.dumps(value))
    info = dict(Id=identity, Name="/"+name, Config={"Labels": {
        "opennavicat.test":"true", "opennavicat.task":"mongodb-adapter-tests",
        "opennavicat.mongodb-protocol-run":run_id,
        "opennavicat.mongodb-protocol-fixture":token}})
    return path, info


def test_emergency_cleanup_selects_only_exact_run_and_redacts_logs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path, info = emergency_receipt(tmp_path)
    calls = []
    def docker(*arguments):
        calls.append(arguments)
        if arguments[:2] == ("container", "ls"):
            assert arguments[-1] == "{{.ID}}"
            if "label=opennavicat.mongodb-protocol-run="+"a"*32 in arguments:
                return info["Id"]
            assert "id="+info["Id"] in arguments
            return ""
        if arguments[0] == "inspect":
            assert arguments[1] == info["Id"]
            return json.dumps([info])
        assert arguments == ("rm", "--force", "--volumes", info["Id"])
        return info["Id"]
    monkeypatch.setattr(runner, "_docker", docker)
    monkeypatch.setattr(runner.subprocess, "run", lambda arguments, **kwargs:
        SimpleNamespace(returncode=0, stdout="own fixture-password", stderr="diagnostic"))
    assert runner.cleanup_interrupted_fixtures(tmp_path, "a"*32) == [info["Id"]]
    assert json.loads(path.read_text())["cleanup_removed"] is True
    assert path.with_suffix(".emergency.log").read_text() == "own <redacted>diagnostic"
    assert all("prefix" not in str(arguments) for arguments in calls)


@pytest.mark.parametrize("mutation", ["test", "task", "run", "token", "id", "name", "receipt-id"])
def test_emergency_cleanup_never_deletes_ownership_mismatches(tmp_path, monkeypatch, mutation):
    path, info = emergency_receipt(tmp_path)
    labels = info["Config"]["Labels"]
    key = {"test":"opennavicat.test", "task":"opennavicat.task",
        "run":"opennavicat.mongodb-protocol-run", "token":"opennavicat.mongodb-protocol-fixture"}.get(mutation)
    if key: labels[key] = "other"
    elif mutation == "id": info["Id"] = "d"*64
    elif mutation == "name": info["Name"] = "/other-owned-container"
    else:
        value = json.loads(path.read_text()); value["container_id"] = "e"*64
        path.write_text(json.dumps(value))
    calls = []
    def docker(*arguments):
        calls.append(arguments)
        if arguments[0] == "container": return "c"*64
        if arguments[0] == "inspect": return json.dumps([info])
        pytest.fail("Mismatched ownership must not reach destructive cleanup")
    monkeypatch.setattr(runner, "_docker", docker)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs:pytest.fail("No unowned logs may be read"))
    with pytest.raises(RuntimeError, match="exact run ownership"):
        runner.cleanup_interrupted_fixtures(tmp_path, "a"*32)
    assert not any(arguments[0] == "rm" for arguments in calls)


def test_emergency_cleanup_does_not_touch_containers_from_other_runs(tmp_path, monkeypatch):
    emergency_receipt(tmp_path, "d"*32)
    calls = []
    def docker(*arguments):
        calls.append(arguments)
        assert arguments[0] == "container" and "label=opennavicat.mongodb-protocol-run="+"a"*32 in arguments
        return ""
    monkeypatch.setattr(runner, "_docker", docker)
    assert runner.cleanup_interrupted_fixtures(tmp_path, "a"*32) == []
    assert len(calls) == 1


def test_timeout_runs_cleanup_but_cannot_turn_failed_invocation_green(tmp_path, monkeypatch):
    def timeout(*_args): raise runner.subprocess.TimeoutExpired("owned child", 1)
    cleaned = []
    monkeypatch.setattr(runner, "run_tests", timeout)
    monkeypatch.setattr(runner.signal, "signal", lambda *_args:None)
    def emergency(directory, run_id):
        cleaned.append(run_id)
        return ["c"*64]
    monkeypatch.setattr(runner, "cleanup_interrupted_fixtures", emergency)
    assert runner.main(["--evidence", str(tmp_path)]) == 1
    receipt = json.loads((tmp_path / "summary.json").read_text())
    assert receipt["passed"] is False and receipt["error_type"] == "TimeoutExpired"
    assert receipt["emergency_cleanup"] == ["c"*64] and cleaned == [receipt["run_id"]]


def test_normal_finalizer_receipts_do_not_trigger_emergency_deletion(tmp_path, monkeypatch):
    def complete(root, directory, run_id, timeout):
        report(directory / "pytest-results.xml")
        target = directory / "fixtures"; target.mkdir()
        fixtures(target, run_id)
        return 0
    monkeypatch.setattr(runner, "run_tests", complete)
    monkeypatch.setattr(runner.signal, "signal", lambda *_args:None)
    monkeypatch.setattr(runner, "cleanup_interrupted_fixtures", lambda *_args:pytest.fail("Normal finalizers already cleaned"))
    assert runner.main(["--evidence", str(tmp_path)]) == 0
    receipt = json.loads((tmp_path / "summary.json").read_text())
    assert receipt["verified"] is True and "emergency_cleanup" not in receipt
