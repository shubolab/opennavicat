"""Offline rejection checks and a real interrupt/finalizer for the strict gate."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("strict_stream_cleanup_runner",
    ROOT / "scripts/test-mongodb-stream-cleanup.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
EXPECTED = [runner.TARGET + "::test_generated_first", runner.TARGET + "::test_generated_second"]


def junit(path, names, *, skipped=False, failed=False):
    suite = ET.Element("testsuite", tests=str(len(names)), skipped=str(int(skipped)),
                       failures=str(int(failed)), errors="0")
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", classname="tests.test_mongodb_stream_runner", name=name)
        if index == 0 and skipped:
            ET.SubElement(case, "skipped")
        if index == 0 and failed:
            ET.SubElement(case, "failure")
    ET.ElementTree(suite).write(path)


def test_gate_accepts_exact_successful_junit(tmp_path):
    path = tmp_path / "results.xml"
    junit(path, [node.split("::")[1] for node in EXPECTED])
    assert runner.check_junit(path, EXPECTED) == dict(passed=2, failed=0, errors=0, skipped=0)


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "skipped", "failed"])
def test_gate_rejects_partial_duplicate_or_nonexecuted_junit(tmp_path, invalid):
    names = [node.split("::")[1] for node in EXPECTED]
    if invalid == "missing":
        names.pop()
    elif invalid == "duplicate":
        names[1] = names[0]
    path = tmp_path / "results.xml"
    junit(path, names, skipped=invalid == "skipped", failed=invalid == "failed")
    with pytest.raises(RuntimeError):
        runner.check_junit(path, EXPECTED)


@pytest.mark.parametrize("invalid", ["foreign_invocation", "unconfirmed_cleanup", "source_hash", "duplicate_case"])
def test_invalid_ledger_is_rejected_before_any_docker_inspection(tmp_path, monkeypatch, invalid):
    hashes = {name: "a" * 64 for name in runner.SOURCE_PATHS}
    record = dict(case=EXPECTED[0], audit_token="b" * 32, run_id="c" * 32,
                  qualification_run_id="d" * 32, image=runner.IMAGE,
                  runner_sha256=hashes["scripts/test-mongodb-stream.py"], tests_sha256=hashes[runner.TARGET],
                  runner_source_unchanged=True, tests_source_unchanged=True,
                  cleanup_completed=True, all_created_resources_absent=True, cleanup_errors=[], containers_started=0,
                  containers_created=["e" * 64], volumes_created=["f" * 64])
    if invalid == "foreign_invocation":
        record["qualification_run_id"] = "0" * 32
    elif invalid == "unconfirmed_cleanup":
        record["cleanup_completed"] = False
    elif invalid == "source_hash":
        record["runner_sha256"] = "0" * 64
    (tmp_path / "cleanup-first.json").write_text(json.dumps(record))
    if invalid == "duplicate_case":
        (tmp_path / "cleanup-duplicate.json").write_text(json.dumps(record))
    monkeypatch.setattr(runner, "docker", lambda *_args: pytest.fail("Invalid ledgers must not inspect Docker resources"))
    with pytest.raises(RuntimeError):
        runner.check_ledgers(tmp_path, [EXPECTED[0]], "d" * 32, hashes)


@pytest.mark.skipif(os.name != "posix", reason="Exercises the POSIX process-group SIGINT/finalizer contract")
def test_real_slow_child_receives_interrupt_finishes_finalizer_and_is_reaped(tmp_path):
    ready, finalized = tmp_path / "ready", tmp_path / "finalized"
    script = ("from pathlib import Path\nimport time\n"
              "try:\n"
              f" Path({str(ready)!r}).write_text('ready')\n"
              " time.sleep(60)\n"
              f"finally:\n Path({str(finalized)!r}).write_text('finalized')\n")
    process = subprocess.Popen([sys.executable, "-c", script], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.01)
        assert ready.exists() and process.poll() is None
        runner.stop_process(process)
        assert finalized.read_text() == "finalized" and process.poll() is not None
    finally:
        runner.stop_process(process)
