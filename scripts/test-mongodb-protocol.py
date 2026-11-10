#!/usr/bin/env python3
"""Run MongoDB backend, native query and MainWindow tests on fresh containers.

The zero-skip scope is exactly the three modules below. UI component tests that
need a manual reference endpoint are deliberately excluded. No endpoint receipt,
private certificate, key, environment file or URI is collected as evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


MODULES = ("test_mongodb_session", "test_mongodb_query", "test_mongo_window")
REQUIRED = {
    "test_mongodb_session": {
        "test_real_gridfs_binary_roundtrip_metadata_cas_and_no_replace",
        "test_real_cancel_targets_own_typed_comment_and_finishes_query",
    },
    "test_mongodb_query": {
        "test_real_find_native_bson_projection_chains_and_findone",
        "test_real_cursor_delivery_limit_peeks_getmore_and_closes_cursor",
        "test_native_write_errors_report_partial_outcomes_and_stop_later_commands",
    },
    "test_mongo_window": {
        "test_real_mongodb_query_page_runs_native_find_without_sql_formatting",
        "test_actual_mainwindow_gridfs_tree_routing_and_native_download",
    },
}


def check_junit(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases or any(case.find(kind) is not None for case in cases for kind in ("skipped", "failure", "error")):
        raise RuntimeError("MongoDB protocol requires executed successful cases with zero skips")
    grouped = {name: [] for name in MODULES}
    for case in cases:
        module = case.get("classname", "").rsplit(".", 1)[-1]
        if module not in grouped:
            raise RuntimeError("The protocol report contains tests outside its declared scope")
        grouped[module].append(case.get("name", ""))
    for module, required in REQUIRED.items():
        names = {name.split("[", 1)[0] for name in grouped[module]}
        if not required <= names:
            raise RuntimeError("Required real MongoDB protocol or MainWindow cases did not execute")
    return {"passed": len(cases), "failed": 0, "errors": 0, "skipped": 0,
            "modules": {name: len(values) for name, values in grouped.items()}}


def check_fixtures(directory, run_id):
    fixtures = [json.loads(path.read_text()) for path in directory.glob("fixture-*.json")]
    fixtures = [value for value in fixtures if value.get("run_id") == run_id]
    expected = set(MODULES)
    observed = {value.get("module", "").rsplit(".", 1)[-1] for value in fixtures}
    if observed != expected:
        raise RuntimeError("Each MongoDB protocol module must use a fresh owned fixture")
    required = ("owner_token_verified", "cleanup_removed", "port_loopback_only",
                "native_docker_architecture_matches", "external_auth_readiness")
    if any(value.get(key) is not True for value in fixtures for key in required):
        raise RuntimeError("Owned MongoDB fixture readiness or cleanup was not verified")
    return fixtures


def _docker(*arguments):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Owned MongoDB emergency cleanup Docker operation failed")
    return result.stdout.strip()


def cleanup_interrupted_fixtures(directory, run_id):
    """Remove only this run's exact, receipt-backed fixture identities.

    The pre-start receipt contains the token and name even if the child is
    killed before recording Docker's returned ID. Never scan by name prefix.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise RuntimeError("Invalid MongoDB protocol run identity")
    receipts = {}
    for path in directory.glob("fixture-*.json"):
        value = json.loads(path.read_text())
        if value.get("run_id") == run_id:
            receipts[value.get("owner_token")] = (path, value)
    identities = _docker("container", "ls", "--all", "--no-trunc", "--filter",
        "label=opennavicat.mongodb-protocol-run=" + run_id, "--format", "{{.ID}}").splitlines()
    cleaned = []
    for identity in identities:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("Invalid container identity in MongoDB cleanup")
        values = json.loads(_docker("inspect", identity))
        if len(values) != 1:
            raise RuntimeError("MongoDB cleanup did not resolve one exact container")
        info = values[0]
        labels = info.get("Config", {}).get("Labels", {}) or {}
        token = labels.get("opennavicat.mongodb-protocol-fixture", "")
        proof = receipts.get(token)
        name = "opennavicat-mongo-owned-" + token[:12]
        if (not re.fullmatch(r"[0-9a-f]{32}", token) or proof is None or
            labels.get("opennavicat.test") != "true" or
            labels.get("opennavicat.task") != "mongodb-adapter-tests" or
            labels.get("opennavicat.mongodb-protocol-run") != run_id or
            info.get("Id") != identity or info.get("Name") != "/" + name or
            proof[1].get("container_name") != name or
            proof[1].get("container_id", identity) != identity):
            raise RuntimeError("Refusing MongoDB emergency cleanup without exact run ownership")
        path, value = proof
        # Log collection is also restricted to the verified identity. Dummy
        # fixture credentials are redacted; no Docker inspect/env is published.
        try:
            logs = subprocess.run(["docker", "logs", "--tail", "60", identity],
                capture_output=True, text=True, timeout=15)
            value["emergency_log_available"] = logs.returncode == 0
            path.with_suffix(".emergency.log").write_text(
                (logs.stdout + logs.stderr).replace("fixture-password", "<redacted>"))
        except Exception as error:
            # Log retrieval is diagnostic; it must not strand a verified own
            # container if Docker's logging endpoint itself is unresponsive.
            value["emergency_log_error_type"] = type(error).__name__
        _docker("rm", "--force", "--volumes", identity)
        if _docker("container", "ls", "--all", "--no-trunc", "--filter",
                   "id=" + identity, "--format", "{{.ID}}"):
            raise RuntimeError("Emergency MongoDB fixture cleanup did not complete")
        value.update(container_id=identity, owner_token_verified=True,
                     cleanup_removed=True, emergency_cleanup=True)
        path.write_text(json.dumps(value, indent=2) + "\n")
        cleaned.append(identity)
    return cleaned


def run_tests(root, directory, run_id, timeout):
    environment = dict(os.environ, OPENNAVICAT_TEST_MONGODB="1", QT_QPA_PLATFORM="offscreen",
        OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID=run_id,
        OPENNAVICAT_MONGODB_PROTOCOL_REPORT_DIR=str(directory / "fixtures"), PYTHONPATH=str(root))
    # There is no manual/reference endpoint path in this runner's contract.
    environment.pop("OPENNAVICAT_TEST_MONGODB_ENDPOINT", None)
    command = [sys.executable, "-m", "pytest", "-q", "-W", "error",
        *["tests/" + module + ".py" for module in MODULES],
        "--junitxml=" + str(directory / "pytest-results.xml")]
    process = subprocess.Popen(command, cwd=root, env=environment,
        start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    try:
        return process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            if os.name == "posix": os.killpg(process.pid, signal.SIGINT)
            else: process.send_signal(signal.CTRL_BREAK_EVENT)
            try: process.wait(timeout=30)  # Let actual pytest finalizers clean up.
            except subprocess.TimeoutExpired:
                if os.name == "posix": os.killpg(process.pid, signal.SIGKILL)
                else: process.kill()
                process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/security-integration/mongodb-protocol"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    summary = {"passed": False, "run_id": run_id, "scope": list(MODULES),
        "manual_reference_endpoint_used": False}
    cleanup_verified = False
    for name in ("pytest-results.xml", "summary.json"):
        (directory / name).unlink(missing_ok=True)
    def interrupted(_signal, _frame): raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM): signal.signal(number, interrupted)
    try:
        status = run_tests(root, directory, run_id, args.timeout)
        # Verify finalizer receipts even when assertions failed, and never let
        # previous-run evidence turn missing/failed/skipped cases green.
        summary["fixtures"] = check_fixtures(directory / "fixtures", run_id)
        cleanup_verified = True
        if status:
            summary["pytest_exit_code"] = status
            return status
        summary.update(check_junit(directory / "pytest-results.xml"))
        summary["verified"] = True
        print(json.dumps({"passed": summary["passed"], "skipped": 0, "owned_cleanup": True}))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        summary["error_type"] = type(error).__name__
        print("MongoDB protocol integration failed; see redacted evidence.", file=sys.stderr)
        return 1
    finally:
        if not cleanup_verified:
            try:
                summary["emergency_cleanup"] = cleanup_interrupted_fixtures(directory / "fixtures", run_id)
            except Exception as error:
                summary["emergency_cleanup_error_type"] = type(error).__name__
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
