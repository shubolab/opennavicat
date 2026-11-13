"""Tamper/failure contracts for the owned Oracle runner, without Docker I/O."""
import importlib.util
import json
from pathlib import Path
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
        return SimpleNamespace(stdout=json.dumps([{"Id": fixture.cid, "Image": support.IMAGE_CONFIG,
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
        return SimpleNamespace(stdout=json.dumps({"Architecture": "x86_64", "OSType": "linux"}))
    monkeypatch.setattr(fixture, "docker", docker)
    with pytest.raises(RuntimeError, match="native"):
        fixture.start()
    assert fixture.cid is None and [call[0] for call in calls] == ["info"]
