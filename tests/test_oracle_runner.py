"""Tamper/failure contracts for the owned Oracle runner, without Docker I/O."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load(ROOT / "scripts/test-oracle.py")
support = load(ROOT / "tests/oracle_owned_fixture.py")


@pytest.mark.parametrize("invalid_code, wrong_owner", [(False, False), (True, False), (False, True)],
                         ids=["owned-state", "unsafe-driver-code", "unverified-state"])
def test_readiness_failure_persists_safe_phase_and_owned_state_without_extending_budget(
        tmp_path, monkeypatch, invalid_code, wrong_owner):
    """Run the real startup state machine with no Docker/network and no sleep."""
    fixture = support.OwnedOracle(tmp_path / "new-startup")
    clock = SimpleNamespace(now=0.0, failed=False)
    cid = "1" * 64
    pin = support.PINS["arm64"]
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "info":
            value = {"Architecture": "aarch64", "OSType": "linux"}
        elif args[:2] == ("image", "inspect"):
            value = [{"Id": pin["config"], "Architecture": "arm64", "Os": "linux", "Config": {}}]
        elif args[0] == "create":
            return SimpleNamespace(stdout=cid)
        elif args[0] == "inspect":
            assert args[1] == cid
            labels = dict(fixture.labels)
            if clock.failed and wrong_owner: labels["owner"] = "not-our-container"
            value = [{"Id": cid, "Image": pin["config"], "Config": {"Labels": labels},
                "HostConfig": {"Binds": None, "VolumesFrom": None,
                    "Tmpfs": {support.SECRET_DIR: "rw"}, "Memory": 4 * 1024**3,
                    "NanoCpus": 2_000_000_000, "ShmSize": 1024**3},
                "Mounts": [{"Type": "tmpfs", "Destination": support.SECRET_DIR}],
                "NetworkSettings": {"Ports": {"1521/tcp": [{"HostIp": "127.0.0.1", "HostPort": "31415"}]}},
                "State": {"Status": "exited", "Running": False, "OOMKilled": True, "ExitCode": 137,
                    "Error": fixture.admin_password,
                    "Health": {"Log": [{"Output": fixture.app_password}]}}}]
        else:
            assert args[0] in {"start", "exec"}
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout=json.dumps(value))
    def unavailable(*_args, **_kwargs):
        clock.now, clock.failed = 361.0, True
        code = fixture.app_password if invalid_code else "DPY-6005"
        raise support.oracledb.OperationalError(SimpleNamespace(
            full_code=code, message=fixture.admin_password + fixture.denied_password))
    monkeypatch.setattr(fixture, "docker", docker)
    monkeypatch.setattr(fixture, "connect", unavailable)
    monkeypatch.setattr(support, "_bounded_command_output", lambda *args, **kwargs:
                        (b"ORA-01034: generated detail\nSP2-0640: generated detail", "captured"))
    with monkeypatch.context() as patch:
        patch.setattr(support.time, "monotonic", lambda: clock.now)
        patch.setattr(support.time, "sleep", lambda *_: pytest.fail("No wait or retry after deadline"))
        with pytest.raises(RuntimeError, match="readiness deadline"):
            fixture.start()
    body = (fixture.out / "fixture.json").read_text()
    assert all(secret not in body for secret in
               (fixture.admin_password, fixture.app_password, fixture.denied_password))
    diagnostic = json.loads(body)["startup"]
    assert diagnostic["phase"] == "readiness" and diagnostic["status"] == "failed"
    assert diagnostic["reason"] == "readiness_deadline" and diagnostic["error_class"] == "RuntimeError"
    assert diagnostic["readiness_budget_seconds"] == 360 and diagnostic["readiness_attempts"] == 1
    assert diagnostic["elapsed_ms"] == 361000
    if invalid_code: assert "last_driver_code" not in diagnostic
    else: assert diagnostic["last_driver_code"] == "DPY-6005"
    if wrong_owner:
        assert diagnostic["container_state_unavailable"] is True
        assert "container_state" not in diagnostic
    else:
        assert diagnostic["container_state"] == {
            "status": "exited", "running": False, "oom_killed": True, "exit_code": 137}
        assert diagnostic["server_log"]["codes"] == [
            {"code": "ORA-01034", "count": 1}, {"code": "SP2-0640", "count": 1}]
    assert support.startup_diagnostic(fixture.receipt) == diagnostic
    assert not any(call[0] in {"logs", "ps", "stop", "rm"} for call in calls)


def test_startup_diagnostic_export_rejects_unknown_or_secret_bearing_metadata():
    secret = "generated-sensitive-diagnostic-value"
    assert support.startup_diagnostic({"startup": {
        "phase": "readiness", "status": "failed", "reason": secret,
        "error_class": secret, "elapsed_ms": True, "readiness_attempts": -1,
        "last_driver_code": secret, "unknown": secret,
        "container_state": {"status": secret, "running": "false", "oom_killed": secret,
                            "exit_code": secret, "Config": secret}}}) == {
                                "phase": "readiness", "status": "failed"}


def junit(tmp_path, entries):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for name, tag in entries:
        case = ET.SubElement(suite, "testcase", classname="tests.test_oracle_integration", name=name)
        if tag:
            ET.SubElement(case, tag)
    path = tmp_path / "results.xml"
    ET.ElementTree(root).write(path)
    return path


def test_junit_requires_exact_cases_and_zero_skip(tmp_path):
    nodes = ["tests/test_oracle_integration.py::test_first", "tests/test_oracle_integration.py::test_second"]
    path = junit(tmp_path, [("test_first", None), ("test_second", None)])
    assert runner.strict_junit(path, nodes)["passed"] == 2


@pytest.mark.parametrize("entries", [[], [("test_first", None)],
    [("test_first", None), ("test_first", None)],
    [("test_first", None), ("test_unknown", None)],
    [("test_first", "skipped"), ("test_second", None)],
    [("test_first", "failure"), ("test_second", None)],
    [("test_first", "error"), ("test_second", None)]])
def test_junit_missing_duplicate_unknown_skipped_or_failed_cases_cannot_qualify(tmp_path, entries):
    nodes = ["tests/test_oracle_integration.py::test_first", "tests/test_oracle_integration.py::test_second"]
    with pytest.raises(RuntimeError):
        runner.strict_junit(junit(tmp_path, entries), nodes)


def test_credential_redaction_does_not_depend_on_driver_error_format():
    text = "assert password == 'generated-secret'\nURI='user:generated-secret@host'"
    assert runner.redact_output(text, ["generated-secret", ""]) == "assert password == '[REDACTED]'\nURI='user:[REDACTED]@host'"


@pytest.mark.parametrize("stop_timeout", [False, True])
def test_failed_session_observation_still_removes_exact_owned_container(tmp_path, monkeypatch, stop_timeout):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    fixture.cid = "a" * 64
    class Admin:
        def cursor(self):
            raise RuntimeError("untrusted server detail")
    fixture.admin = Admin()
    calls = []
    monkeypatch.setattr(fixture, "inspect", lambda: calls.append("verify ownership"))
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "stop" and stop_timeout:
            raise TimeoutError("graceful stop deadline")
        return SimpleNamespace(returncode=1, stderr="No such object")
    monkeypatch.setattr(fixture, "docker", docker)
    with pytest.raises(RuntimeError, match="still removed"):
        fixture.close()
    assert calls == ["verify ownership", ("stop", "--time", "30", fixture.cid),
                     ("rm", "--force", fixture.cid), ("inspect", fixture.cid)]
    receipt = json.loads((fixture.out / "fixture.json").read_text())
    assert receipt["exact_id_absent"] and receipt["application_session_check_failed"]


def test_wrong_owner_never_reaches_container_removal(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    fixture.cid = "b" * 64
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        fixture.image_config = support.PINS["arm64"]["config"]
        return SimpleNamespace(stdout=json.dumps([{"Id": fixture.cid, "Image": fixture.image_config,
            "Config": {"Labels": {"owner": "someone-else"}}}]))
    monkeypatch.setattr(fixture, "docker", docker)
    with pytest.raises(AssertionError):
        fixture.close()
    assert calls == [("inspect", fixture.cid)]


def test_non_native_engine_is_rejected_before_container_creation(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"Architecture": "riscv64", "OSType": "linux"}))
    monkeypatch.setattr(fixture, "docker", docker)
    with pytest.raises(RuntimeError, match="native"):
        fixture.start()
    assert fixture.cid is None and [call[0] for call in calls] == ["info"]


@pytest.mark.parametrize("reported,expected", [("x86_64", "amd64"), ("amd64", "amd64"),
                                             ("aarch64", "arm64"), ("arm64", "arm64")])
def test_native_engine_architecture_selects_its_own_exact_child(reported, expected):
    assert support.native_architecture({"Architecture": reported, "OSType": "linux"}) == expected
    pin = support.PINS[expected]
    assert pin["image"].startswith("docker.io/gvenzl/oracle-free@sha256:")
    assert len(pin["config"].removeprefix("sha256:")) == 64


@pytest.mark.parametrize("engine", [{"Architecture": "amd64", "OSType": "windows"},
    {"Architecture": "ppc64le", "OSType": "linux"}, {"Architecture": None, "OSType": "linux"}])
def test_unsupported_os_or_architecture_has_no_emulation_fallback(engine):
    with pytest.raises(RuntimeError, match="native"):
        support.native_architecture(engine)


def cleanup_ledger(tmp_path, *, provisional=False):
    out = tmp_path / "evidence"
    fixture = support.OwnedOracle(out / "owned-fixture")
    pin = support.PINS["amd64"]
    fixture.cid = "c" * 64
    fixture.receipt.update(image=pin["image"], image_config=pin["config"], platform="linux/amd64")
    if not provisional:
        fixture.receipt["container_id"] = fixture.cid
    fixture.save()
    container = {"Id": fixture.cid, "Image": pin["config"], "Name": "/" + fixture.name,
        "Config": {"Labels": dict(fixture.labels)}, "HostConfig": {"Binds": None, "VolumesFrom": None},
        "Mounts": [{"Type": "tmpfs", "Destination": support.SECRET_DIR}]}
    return out, fixture, container


@pytest.mark.parametrize("provisional", [False, True])
def test_cleanup_only_exact_ledger_removes_without_db_or_start_and_preserves_receipt(tmp_path, monkeypatch, provisional):
    out, fixture, container = cleanup_ledger(tmp_path, provisional=provisional)
    ledger = fixture.out / "fixture.json"
    before = ledger.read_bytes()
    calls = []
    def run(command, **kwargs):
        args = command[command.index(str(out / "cleanup-docker-anonymous")) + 1:]
        calls.append(args)
        if args[0] == "inspect" and len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=json.dumps([container]))
        if args[0] == "inspect":
            return SimpleNamespace(returncode=1, stderr="No such object")
        assert args[0] in {"stop", "rm"}
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(support.subprocess, "run", run)
    monkeypatch.setattr(support.oracledb, "connect", lambda **kwargs: pytest.fail("cleanup must never connect"))
    result = support.cleanup_interrupted(out)
    assert result["passed"] and result["no_start"] and result["no_database_connection"]
    assert calls == [["inspect", fixture.name if provisional else fixture.cid],
        ["stop", "--time", "30", fixture.cid], ["rm", "--force", fixture.cid], ["inspect", fixture.cid]]
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("tamper", ["owner", "image", "name", "bind", "volume", "id"])
def test_cleanup_only_mismatch_never_stops_or_removes(tmp_path, monkeypatch, tamper):
    out, fixture, container = cleanup_ledger(tmp_path)
    if tamper == "owner":
        container["Config"]["Labels"]["run"] = "d" * 32
    elif tamper == "image":
        container["Image"] = "sha256:" + "e" * 64
    elif tamper == "name":
        container["Name"] = "/unrelated"
    elif tamper == "bind":
        container["HostConfig"]["Binds"] = ["/existing:/data"]
    elif tamper == "volume":
        container["Mounts"] = [{"Type": "volume", "Destination": "/data"}]
    elif tamper == "id":
        container["Id"] = "f" * 64
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[-2] == "inspect"
        return SimpleNamespace(returncode=0, stdout=json.dumps([container]))
    monkeypatch.setattr(support.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="refused"):
        support.cleanup_interrupted(out)
    assert len(calls) == 1


def test_cleanup_only_missing_ledger_cannot_start_or_connect(tmp_path, monkeypatch):
    monkeypatch.setattr(support.subprocess, "run", lambda *args, **kwargs: pytest.fail("no resources known"))
    monkeypatch.setattr(support.oracledb, "connect", lambda **kwargs: pytest.fail("no DB"))
    result = support.cleanup_interrupted(tmp_path / "new")
    assert result["passed"] and result["no_owned_ledger"] and not result["ledger_found"]


def test_atomic_receipt_retains_prior_exact_ledger_if_replace_is_interrupted(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    before = (fixture.out / "fixture.json").read_bytes()
    fixture.receipt["container_id"] = "d" * 64
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(Path, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        fixture.save()
    assert (fixture.out / "fixture.json").read_bytes() == before


def test_failed_docker_diagnostic_retains_no_arbitrary_stderr_or_endpoint(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    text = "x" * 9000 + " ".join([fixture.admin_password, fixture.app_password, fixture.denied_password,
        "postgresql://synthetic-user:foreign-secret@private-endpoint.invalid:5432/private-db"])
    monkeypatch.setattr(support.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=1, stderr=text))
    with pytest.raises(RuntimeError, match="Docker operation failed") as caught:
        fixture.docker("info")
    body = (fixture.out / "fixture.json").read_text()
    assert all(secret not in body for secret in (fixture.admin_password, fixture.app_password, fixture.denied_password))
    failure = json.loads(body)["docker_failure"]
    assert failure == {"operation": "info", "exit_code": 1}
    assert all(value not in body + str(caught.value) for value in
               ("private-endpoint.invalid", "foreign-secret", "synthetic-user", "private-db"))


@pytest.mark.parametrize("value", [[], None, "terminal output", ["tests/other.py::test_unknown"],
    ["tests/test_oracle_integration.py::test_a", "tests/test_oracle_integration.py::test_a"]])
def test_required_case_manifest_rejects_empty_foreign_duplicate_or_malformed_scope(tmp_path, value):
    path = tmp_path / "required-cases.json"
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="manifest"):
        runner.read_required(path)


def test_required_manifest_preserves_nodeids_without_terminal_reporter_parsing(tmp_path):
    expected = ["tests/test_oracle_integration.py::test_a[param::value]",
                "tests/test_oracle_integration.py::Group::test_b"]
    path = tmp_path / "required-cases.json"
    path.write_text(json.dumps(expected))
    assert runner.read_required(path) == expected


def test_exact_junit_accepts_nested_class_and_parameter_nodeids(tmp_path):
    suite = ET.Element("testsuite")
    ET.SubElement(suite, "testcase", classname="tests.test_oracle_integration.Group", name="test_b")
    ET.SubElement(suite, "testcase", classname="tests.test_oracle_integration", name="test_a[param::value]")
    path = tmp_path / "class-results.xml"
    ET.ElementTree(suite).write(path)
    expected = ["tests/test_oracle_integration.py::Group::test_b", "tests/test_oracle_integration.py::test_a[param::value]"]
    assert runner.strict_junit(path, expected)["passed"] == 2


def owned_startup_docker(fixture, calls, *, state, tamper=None):
    """A full create/start/endpoint path, with adversarial inspect/log data."""
    cid = "2" * 64
    pin = support.PINS["arm64"]
    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "info":
            value = {"Architecture": "aarch64", "OSType": "linux", "ServerVersion": "29.0.1",
                     "ServerErrors": None}
        elif args[:2] == ("image", "inspect"):
            value = [{"Id": pin["config"], "Architecture": "arm64", "Os": "linux", "Config": {}}]
        elif args[0] == "create":
            return SimpleNamespace(stdout=cid)
        elif args[0] == "inspect":
            assert args[1] == cid
            data = {"Id": cid, "Name": "/" + fixture.name, "Image": pin["config"],
                "Config": {"Labels": dict(fixture.labels)},
                "HostConfig": {"Binds": None, "VolumesFrom": None, "Tmpfs": {support.SECRET_DIR: "rw"},
                    "Memory": 4 * 1024**3, "NanoCpus": 2_000_000_000, "ShmSize": 1024**3},
                "Mounts": [{"Type": "tmpfs", "Destination": support.SECRET_DIR}],
                "NetworkSettings": {"Ports": {"1521/tcp": [{"HostIp": "127.0.0.1", "HostPort": "31415"}]}},
                "State": {**state, "Error": "foreign-secret private-endpoint.invalid",
                          "Health": {"Log": [{"Output": fixture.admin_password + " private-endpoint.invalid"}]}}}
            if fixture.receipt["startup"]["phase"] == "readiness" and tamper:
                if tamper == "owner": data["Config"]["Labels"]["run"] = "unowned"
                if tamper == "image": data["Image"] = "sha256:" + "3" * 64
                if tamper == "id": data["Id"] = "3" * 64
                if tamper == "bind": data["HostConfig"]["Binds"] = ["/private-existing:/data"]
                if tamper == "mount": data["Mounts"] = [{"Type": "volume", "Destination": "/data"}]
            value = [data]
        else:
            assert args[0] in {"start", "exec", "stop", "rm"}
            return SimpleNamespace(stdout="", returncode=0)
        return SimpleNamespace(stdout=json.dumps(value))
    return docker


@pytest.mark.parametrize("state", [
    {"Status": "exited", "Running": False, "ExitCode": 24, "OOMKilled": False},
    {"Status": "dead", "Running": False, "ExitCode": 137, "OOMKilled": True}], ids=["exit24", "dead137"])
def test_exited_exact_owned_container_fails_before_readiness_budget_and_exports_only_codes(tmp_path, monkeypatch, state):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    calls, logs = [], []
    monkeypatch.setattr(fixture, "docker", owned_startup_docker(fixture, calls, state=state))
    private = "postgresql://synthetic-user:foreign-secret@private-endpoint.invalid:5432/private-db"
    raw = ("ORA-01034: " + private + " " + fixture.admin_password + "\n"
           "SP2-0640: " + fixture.app_password + "\nORA-01034: " + fixture.denied_password + "\n"
           "password ORA-123456 invalid XORA-12345 ORA-12345suffix\n").encode()
    def bounded(command):
        assert calls[-1][0] == ("inspect", fixture.cid)
        assert command[-4:] == ["logs", "--tail", "200", fixture.cid]
        logs.append(command)
        return raw, "captured"
    monkeypatch.setattr(support, "_bounded_command_output", bounded, raising=False)
    def unavailable(*args, **kwargs):
        raise support.oracledb.OperationalError(SimpleNamespace(full_code="DPY-6005", message=private))
    monkeypatch.setattr(fixture, "connect", unavailable)
    monkeypatch.setattr(support.time, "sleep", lambda *_: pytest.fail("Exited container must not retry/sleep"))
    with pytest.raises(RuntimeError, match="container exited before readiness") as caught:
        fixture.start()
    diagnostic = support.startup_diagnostic(fixture.receipt)
    assert diagnostic["reason"] == "container_exited" and diagnostic["readiness_attempts"] == 1
    assert diagnostic["elapsed_ms"] < 360000 and diagnostic["readiness_budget_seconds"] == 360
    assert diagnostic["server_log"] == {"status": "captured", "bytes_read": len(raw),
        "codes": [{"code": "ORA-01034", "count": 2}, {"code": "SP2-0640", "count": 1}]}
    assert len(logs) == 1 and not any(args[0] in {"stop", "rm"} for args, _ in calls)
    assert diagnostic["container_state"]["exit_code"] == state["ExitCode"]
    persisted = "".join(p.read_text() for p in fixture.out.rglob("*") if p.is_file()) + str(caught.value)
    for value in [private, "private-endpoint.invalid", "foreign-secret", "synthetic-user", "private-db",
                  fixture.admin_password, fixture.app_password, fixture.denied_password]:
        assert value not in persisted


@pytest.mark.parametrize("tamper", ["owner", "image", "id", "bind", "mount"])
def test_readiness_rejects_owner_image_or_mount_changes_before_log_access(tmp_path, monkeypatch, tamper):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    calls = []
    monkeypatch.setattr(fixture, "docker", owned_startup_docker(fixture, calls,
        state={"Status": "exited", "Running": False, "ExitCode": 24}, tamper=tamper))
    monkeypatch.setattr(support, "_bounded_command_output", lambda *args, **kwargs: pytest.fail("No unverified logs"))
    monkeypatch.setattr(fixture, "connect", lambda *args, **kwargs:
        (_ for _ in ()).throw(support.oracledb.OperationalError("synthetic untrusted error")))
    monkeypatch.setattr(support.time, "sleep", lambda *_: pytest.fail("Contract change must fail immediately"))
    with pytest.raises(AssertionError):
        fixture.start()
    diagnostic = support.startup_diagnostic(fixture.receipt)
    assert diagnostic["reason"] == "fixture_contract" and diagnostic["container_state_unavailable"] is True
    assert "server_log" not in diagnostic
    assert not any(args[0] in {"stop", "rm"} for args, _ in calls)


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_docker_exception_does_not_export_command_environment_or_output(tmp_path, monkeypatch, failure):
    fixture = support.OwnedOracle(tmp_path / "new-fixture", docker_host="unix:///private-endpoint.invalid/socket")
    secret = fixture.admin_password + " foreign-secret private-endpoint.invalid"
    def run(*args, **kwargs):
        if failure == "timeout": raise subprocess.TimeoutExpired([secret], 10, output=secret, stderr=secret)
        raise OSError(secret)
    monkeypatch.setattr(support.subprocess, "run", run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)) as caught:
        fixture.docker("info", timeout=10)
    assert all(value not in str(caught.value) for value in [fixture.admin_password, "foreign-secret", "private-endpoint.invalid"])
    assert not isinstance(caught.value, subprocess.TimeoutExpired) or caught.value.output is None
    assert all(secret not in p.read_text() for p in fixture.out.rglob("*") if p.is_file())


@pytest.mark.parametrize("status", ["deadline", "output_limit", "command_failed"])
def test_log_capture_non_success_preserves_only_finite_status_and_codes(tmp_path, monkeypatch, status):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    fixture.cid = "4" * 64
    raw = b"SP2-0640: foreign-secret at private-endpoint.invalid\n"
    monkeypatch.setattr(support, "_bounded_command_output", lambda command: (raw, status))
    assert fixture._server_log_summary() == {"status": status, "bytes_read": len(raw),
        "codes": [{"code": "SP2-0640", "count": 1}]}


def test_log_summary_has_finite_codes_and_rejects_forged_metadata(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    fixture.cid = "4" * 64
    raw = " ".join("ORA-" + str(i).zfill(5) for i in range(100)).encode()
    monkeypatch.setattr(support, "_bounded_command_output", lambda command: (raw, "captured"))
    result = fixture._server_log_summary()
    assert len(result["codes"]) == support.LOG_CODES and result["code_limit_reached"] is True
    assert support.startup_diagnostic({"startup": {"server_log": {"status": ["secret"],
        "bytes_read": True, "codes": [{"code": "ORA-00001 secret", "count": 1},
        {"code": "SP2-0640", "count": 2, "raw": "private-endpoint.invalid"}], "raw": "secret"}}}) == {
            "server_log": {"codes": [{"code": "SP2-0640", "count": 2}]}}
    def poisoned(command):
        raise RuntimeError("foreign-secret private-endpoint.invalid")
    monkeypatch.setattr(support, "_bounded_command_output", poisoned)
    assert fixture._server_log_summary() == {"status": "unavailable"}


@pytest.mark.skipif(os.name != "posix", reason="Owned Oracle Docker fixture log collector requires POSIX")
@pytest.mark.parametrize("mode", ["success", "overflow", "timeout", "closed-pipe", "nonzero"])
def test_real_bounded_log_command_reaps_child_and_never_writes_raw_output(tmp_path, monkeypatch, mode):
    code = ("import os,sys,time;os.write(1,b'ORA-01034 foreign-secret private-endpoint.invalid\\n');" +
        {"success": "", "overflow": "os.write(1,b'x'*1000000);time.sleep(30)",
         "timeout": "time.sleep(30)", "closed-pipe": "os.close(1);os.close(2);time.sleep(30)",
         "nonzero": "sys.exit(7)"}[mode])
    before = list(tmp_path.iterdir())
    children = []
    original = subprocess.Popen
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(support.subprocess, "Popen", spawn)
    started = time.monotonic()
    raw, status = support._bounded_command_output([sys.executable, "-c", code], timeout=.3, limit=128)
    assert status == {"success": "captured", "overflow": "output_limit", "timeout": "deadline", "closed-pipe": "deadline", "nonzero": "command_failed"}[mode]
    assert len(raw) <= 128 and b"ORA-01034" in raw and time.monotonic() - started < 5
    assert list(tmp_path.iterdir()) == before
    assert len(children) == 1 and children[0].poll() is not None and children[0].stdout.closed


@pytest.mark.parametrize("mode", ["near-deadline", "late-success"])
def test_readiness_deadline_never_starts_an_extra_connection_or_seed(tmp_path, monkeypatch, mode):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    calls, attempts, sleeps = [], [], []
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(fixture, "docker", owned_startup_docker(fixture, calls,
        state={"Status": "running", "Running": True, "ExitCode": 0}))
    monkeypatch.setattr(support, "_bounded_command_output", lambda command: (b"", "captured"))
    monkeypatch.setattr(fixture, "_seed", lambda: pytest.fail("Expired readiness must not seed"))
    def connect(*args, **kwargs):
        attempts.append(args)
        if mode == "late-success":
            clock.now = 361
            return SimpleNamespace(version="23.26.3")
        clock.now = 359.75
        raise support.oracledb.OperationalError("synthetic unavailable")
    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds
    monkeypatch.setattr(fixture, "connect", connect)
    monkeypatch.setattr(support.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(support.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="readiness deadline"):
        fixture.start()
    assert len(attempts) == 1 and sleeps == ([.25] if mode == "near-deadline" else [])
    assert fixture.receipt["startup"]["readiness_budget_seconds"] == 360
    assert fixture.receipt["startup"]["reason"] == "readiness_deadline"
    readiness_timeouts = [kwargs["timeout"] for args, kwargs in calls if args[0] == "inspect" and kwargs["timeout"] < 1]
    assert readiness_timeouts == ([.25] if mode == "near-deadline" else [])


def test_running_then_exited_container_stops_after_one_retry_with_exact_revalidation(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    calls, attempts, sleeps = [], [], []
    state = {"Status": "running", "Running": True, "ExitCode": 0, "OOMKilled": False}
    monkeypatch.setattr(fixture, "docker", owned_startup_docker(fixture, calls, state=state))
    monkeypatch.setattr(support, "_bounded_command_output", lambda command: (b"ORA-01034 safe", "captured"))
    def connect(*args, **kwargs):
        attempts.append(args)
        if len(attempts) == 2:
            state.update(Status="exited", Running=False, ExitCode=24)
        raise support.oracledb.OperationalError("unavailable")
    monkeypatch.setattr(fixture, "connect", connect)
    monkeypatch.setattr(support.time, "sleep", lambda seconds: sleeps.append(seconds))
    with pytest.raises(RuntimeError, match="container exited"):
        fixture.start()
    assert len(attempts) == 2 and sleeps == [1]
    assert fixture.receipt["startup"]["readiness_attempts"] == 2
    assert len([args for args, _ in calls if args[0] == "inspect"]) == 5


def test_successful_start_and_keyboard_interrupt_never_capture_server_logs(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "success")
    calls = []
    docker = owned_startup_docker(fixture, calls, state={"Status": "running", "Running": True})
    def successful_docker(*args, **kwargs):
        if args[0] == "exec" and args[-2:] == ("uname", "-m"):
            calls.append((args, kwargs))
            return SimpleNamespace(stdout="aarch64")
        return docker(*args, **kwargs)
    monkeypatch.setattr(fixture, "docker", successful_docker)
    monkeypatch.setattr(fixture, "connect", lambda *args, **kwargs: SimpleNamespace(version="23.26.3"))
    monkeypatch.setattr(fixture, "_seed", lambda: None)
    monkeypatch.setattr(support, "_bounded_command_output", lambda *args, **kwargs: pytest.fail("No logs on success/interrupt"))
    fixture.start()
    assert fixture.receipt["startup"]["status"] == "ready" and fixture.receipt["startup"]["phase"] == "complete"
    assert "server_log" not in fixture.receipt["startup"]
    interrupted = support.OwnedOracle(tmp_path / "interrupted")
    interrupted.cid = "5" * 64
    monkeypatch.setattr(interrupted, "inspect", lambda **kwargs: pytest.fail("Interrupt must not start diagnosis"))
    monkeypatch.setattr(interrupted, "_start", lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        interrupted.start()
    assert interrupted.receipt["startup"]["reason"] == "interrupted"


def test_engine_probe_rejects_arbitrary_server_errors_and_version_strings(tmp_path, monkeypatch):
    fixture = support.OwnedOracle(tmp_path / "new-fixture")
    secret = "foreign-secret private-endpoint.invalid /private/path"
    monkeypatch.setattr(fixture, "docker", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({
        "Architecture": "riscv64", "OSType": "linux", "ServerVersion": secret, "ServerErrors": [secret]})))
    with pytest.raises(RuntimeError, match="native"):
        fixture.start()
    body = (fixture.out / "fixture.json").read_text()
    assert secret not in body
    assert fixture.receipt["engine_probe"] == {"Architecture": None, "OSType": "linux",
        "ServerVersion": None, "ServerErrors": ["reported"]}


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "fork"), reason="POSIX owned process group contract")
@pytest.mark.parametrize("pipe_mode", ["inherited", "closed"])
def test_real_log_capture_removes_grandchild_even_after_leader_exits(tmp_path, monkeypatch, pipe_mode):
    # Every descendant self-expires quickly if a regression prevents cleanup.
    # Tests never signal a nonwaitable descendant by PID.
    code = """import os,time
child=os.fork()
if child==0:
 grand=os.fork()
 if grand==0:
  if MODE=='closed':
   os.close(1);os.close(2)
  time.sleep(.8)
  os._exit(0)
 os.write(1,(str(grand)+'\\n').encode())
 os._exit(0)
os._exit(0)
""".replace("MODE", repr(pipe_mode))
    original = subprocess.Popen
    leaders = []
    sessions = []
    def spawn(*args, **kwargs):
        sessions.append(kwargs.get("start_new_session"))
        leader = original(*args, **kwargs)
        leaders.append(leader)
        return leader
    monkeypatch.setattr(support.subprocess, "Popen", spawn)
    before = list(tmp_path.iterdir())
    raw, status = support._bounded_command_output([sys.executable, "-c", code], timeout=.1)
    descendant = int(raw.strip())
    assert status == ("deadline" if pipe_mode == "inherited" else "captured")
    assert len(leaders) == 1 and leaders[0].returncode == 0 and leaders[0].stdout.closed
    with pytest.raises(ProcessLookupError):
        os.kill(descendant, 0)
    assert sessions == [True]
    with pytest.raises(ProcessLookupError):
        os.killpg(leaders[0].pid, 0)
    assert list(tmp_path.iterdir()) == before
