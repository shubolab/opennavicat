#!/usr/bin/env python3
"""Require exact, zero-skip stopped-container cleanup acceptance on fresh fixtures.

Only the declared test module may create resources. This checker independently
inspects its recorded exact IDs and volumes; it never discovers or deletes Docker
resources. An interrupted run remains failed even when recorded cleanup passes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_mongodb_stream_runner.py"
IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
SOURCE_PATHS = (TARGET, "scripts/test-mongodb-stream.py", "scripts/test-mongodb-stream-cleanup.py")
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([sys.argv[2],'--collect-only','-q'],plugins=[Collect()]))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}


def check_manifest(expected):
    require(isinstance(expected, list) and expected
            and all(isinstance(node, str) and node.startswith(TARGET + "::") for node in expected)
            and len(expected) == len(set(expected)),
            "Stopped cleanup collection does not match its declared exact scope")


def check_junit(path, expected):
    check_manifest(expected)
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    actual = [case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "") for case in cases]
    require(Counter(actual) == Counter(expected), "Stopped cleanup JUnit does not match the collected exact scope")
    counts = dict(passed=0, failed=0, errors=0, skipped=0)
    for case in cases:
        outcome = next((key for tag, key in (("failure", "failed"), ("error", "errors"), ("skipped", "skipped"))
                        if case.find(tag) is not None), "passed")
        counts[outcome] += 1
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    for attribute, actual_count in (("tests", len(cases)), ("failures", counts["failed"]),
                                    ("errors", counts["errors"]), ("skipped", counts["skipped"])):
        require(sum(int(suite.get(attribute, 0)) for suite in suites) == actual_count,
                "Stopped cleanup JUnit summary contradicts its cases")
    require(not counts["failed"] and not counts["errors"] and not counts["skipped"],
            "Stopped cleanup qualification requires its full scope with zero skips or errors")
    return counts


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)


def confirm_absent(kind, identity):
    result = docker(kind, "inspect", identity)
    try:
        empty = json.loads(result.stdout) == []
    except (ValueError, TypeError):
        empty = False
    require(result.returncode != 0 and empty and identity in result.stderr
            and any(phrase in result.stderr.lower() for phrase in
                    ("no such object", "no such container", "no such volume")),
            "Exact stopped cleanup resource absence could not be independently confirmed")


def check_ledgers(directory, expected, run_id, hashes, *, complete=True):
    check_manifest(expected)
    require(re.fullmatch(r"[0-9a-f]{32}", run_id) is not None, "Stopped cleanup qualification ID is invalid")
    records = [json.loads(path.read_text()) for path in sorted(directory.glob("cleanup-*.json"))]
    names = [record.get("case") for record in records]
    require(len(names) == len(set(names)) and set(names) <= set(expected), "Stopped cleanup ledgers contain duplicate or foreign cases")
    if complete:
        require(Counter(names) == Counter(expected), "Stopped cleanup ledgers do not cover every collected case")
    containers, volumes, owners, fixture_runs = set(), set(), set(), set()
    for record in records:
        owner, fixture_run = record.get("audit_token", ""), record.get("run_id", "")
        require(record.get("qualification_run_id") == run_id and record.get("image") == IMAGE
                and re.fullmatch(r"[0-9a-f]{32}", owner) is not None
                and re.fullmatch(r"[0-9a-f]{32}", fixture_run) is not None
                and owner not in owners and fixture_run not in fixture_runs,
                "Stopped cleanup ledger invocation or ownership differs")
        owners.add(owner)
        fixture_runs.add(fixture_run)
        require(record.get("runner_sha256") == hashes["scripts/test-mongodb-stream.py"]
                and record.get("tests_sha256") == hashes[TARGET]
                and record.get("runner_source_unchanged") is True
                and record.get("tests_source_unchanged") is True,
                "Stopped cleanup ledger source hashes differ")
        require(record.get("cleanup_completed") is True and record.get("all_created_resources_absent") is True
                and record.get("cleanup_errors") == [] and record.get("containers_started") == 0,
                "Stopped cleanup ledger did not finish cleanup without starting containers")
        created, storage = record.get("containers_created"), record.get("volumes_created")
        require(isinstance(created, list) and created and len(created) == len(set(created))
                and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in created),
                "Stopped cleanup ledger container identities are invalid")
        require(isinstance(storage, list) and storage and len(storage) == len(set(storage))
                and all(isinstance(value, str) and (re.fullmatch(r"[0-9a-f]{64}", value)
                    or value == "opennavicat-stream-cleanup-audit-" + owner) for value in storage),
                "Stopped cleanup ledger volume identities are invalid")
        require(not containers.intersection(created) and not volumes.intersection(storage),
                "Stopped cleanup resources are reused across case ledgers")
        containers.update(created)
        volumes.update(storage)
    # Validate every ledger before any Docker call, then inspect only its exact
    # resource identities. Docker daemon/transport failures never prove absence.
    for identity in sorted(containers):
        confirm_absent("container", identity)
    for identity in sorted(volumes):
        confirm_absent("volume", identity)
    return dict(cases=len(records), containers=len(containers), volumes=len(volumes),
                exact_ids_independently_absent=True, all_recorded_cases_cleaned=True)


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGINT)
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
    except ProcessLookupError:
        process.wait(timeout=30)
        return
    try:
        process.wait(timeout=30)  # Let pytest's owned fixture finalizers finish.
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=30)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 1800:
        parser.error("Use a qualification timeout between 1 and 1800 seconds")
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fixtures = directory / "fixtures"
    fixtures.mkdir()
    run_id, hashes = uuid.uuid4().hex, source_hashes()
    summary = dict(verified=False, qualification_run_id=run_id, target=TARGET,
                   source_sha256=hashes, containers_started=0, database_connections=0,
                   existing_container_or_volume_discovery=False)
    environment = dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM="offscreen",
        OPENNAVICAT_TEST_MONGODB_STREAM_CLEANUP="1",
        OPENNAVICAT_MONGODB_STREAM_CLEANUP_REPORT_DIR=str(fixtures),
        OPENNAVICAT_MONGODB_STREAM_CLEANUP_RUN_ID=run_id)
    environment.pop("PYTEST_ADDOPTS", None)
    expected, process, handlers = None, None, {}

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    for number in (signal.SIGINT, signal.SIGTERM):
        handlers[number] = signal.signal(number, interrupted)
    try:
        required = directory / "required-cases.json"
        with (directory / "collection.log").open("w") as output:
            subprocess.run([sys.executable, "-c", COLLECT, str(required), TARGET], cwd=ROOT,
                env=environment, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
        expected = json.loads(required.read_text())
        check_manifest(expected)
        with (directory / "pytest.log").open("w") as output:
            process = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-W", "error", TARGET,
                "--junitxml=" + str(directory / "results.xml")], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            try:
                status = process.wait(timeout=args.timeout)
            finally:
                stop_process(process)
        counts = check_junit(directory / "results.xml", expected)
        require(status == 0, "Stopped cleanup test process did not pass")
        summary["ledgers"] = check_ledgers(fixtures, expected, run_id, hashes)
        require(source_hashes() == hashes, "Stopped cleanup source changed during qualification")
        summary.update(verified=True, **counts)
    except BaseException as error:
        summary["error_type"] = type(error).__name__
        if expected is not None:
            try:
                summary["partial_ledger_audit"] = check_ledgers(fixtures, expected, run_id, hashes, complete=False)
            except Exception as cleanup_error:
                summary["partial_ledger_error_type"] = type(cleanup_error).__name__
    finally:
        try:
            stop_process(process)
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return 0 if summary["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
