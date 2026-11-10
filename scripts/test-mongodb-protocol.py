#!/usr/bin/env python3
"""Run MongoDB backend, query, index scripts/widgets and MainWindow protocols.

The zero-skip scope is exactly the declared modules below. A collection manifest
binds every executed parametrized case and distinguishes pure/offline cases from
real fixture dependencies; no minimum-count heuristic is used. Legacy components
that need a manual reference endpoint are excluded. No endpoint receipt,
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


MODULES = ("test_mongodb_session", "test_mongodb_query", "test_mongo_window",
    "test_mongodb_indexes", "test_mongo_index_designer_ui", "test_mongodb_index_script",
    "test_mongo_index_window")
FIXTURE_MODULES = MODULES
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
    "test_mongodb_indexes": {
        "test_real_namespace_race_at_drop_is_rejected_by_server_uuid",
        "test_real_drop_then_create_failure_reports_missing_original_without_restoring",
        "test_real_lost_acknowledgement_is_unknown_even_when_index_exists",
        "test_real_native_cancel_of_own_lock_wait_never_claims_completed_create",
        "test_real_server_permission_denial_is_not_mistaken_for_an_unsupported_server",
        "test_real_text_metadata_replay_preserves_internal_key_weights_and_suffix",
    },
    "test_mongo_index_designer_ui": {
        "test_real_serial_qt_save_sheet_ordered_keys_and_independent_wire",
        "test_real_rebuild_failure_keeps_dirty_draft_and_shows_actual_completed_steps",
        "test_real_delete_requires_permanent_checkbox_before_any_worker_write",
    },
    "test_mongodb_index_script": {
        "test_real_protected_preview_executes_and_retains_uuid_guard_and_index_options",
        "test_real_helper_preview_can_be_copied_into_native_mongosh",
        "test_real_raw_metadata_cursor_drains_getmore_without_losing_source_bson",
    },
    "test_mongo_index_window": {
        "test_real_window_index_columns_sizes_same_name_selection_and_draft_identity",
        "test_real_window_index_save_preserves_open_document_and_exact_draft",
        "test_real_window_failed_rebuild_shows_actual_steps_refreshes_list_preserves_draft",
        "test_real_window_index_delete_checkbox_cancel_and_keyboard_binding",
        "test_real_window_stop_queued_index_does_no_ddl_and_does_not_poison_next_operation",
        "test_real_window_stop_inflight_index_cancels_its_captured_native_command",
        "test_real_window_index_cancel_then_save_before_leaving",
        "test_real_window_index_inspector_never_queues_reads_after_disconnect_started",
        "test_real_window_preview_preference_only_accepted_and_existing_index_saves_without_sheet",
        "test_real_window_index_references_restore_disconnected_then_explicitly_load",
    },
}
NONFIXTURE_REQUIRED = {
    "test_mongodb_indexes": {"test_bson_numeric_types_are_not_equal_in_detached_drafts",
        "test_guard_qualification_is_command_specific_and_never_falls_back_to_uuid_free_write"},
    "test_mongodb_index_script": {
        "test_observed_constructor_literals_roundtrip_exact_bson_through_actual_command_parser",
        "test_unverified_or_lossy_script_values_are_explicitly_refused",
        "test_original_nonroundtrippable_bson_is_kept_readonly_without_a_lossy_script"},
    "test_mongo_index_designer_ui": {"test_stale_preview_cannot_replace_new_draft_or_invalid_input",
        "test_existing_suppression_emits_draft_for_fresh_worker_plan_without_reusing_confirmation"},
}


def _check_manifest(manifest, run_id=None):
    if (not isinstance(manifest, dict) or manifest.get("scope") != list(MODULES)
            or run_id is not None and manifest.get("run_id") != run_id):
        raise RuntimeError("The MongoDB collection manifest has a stale or undeclared scope")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("The MongoDB collection manifest is empty")
    expected = {}
    for item in items:
        if (not isinstance(item, dict) or item.get("module") not in MODULES
                or not isinstance(item.get("case"), str) or not item["case"]
                or type(item.get("requires_fixture")) is not bool):
            raise RuntimeError("The MongoDB collection manifest contains an invalid case")
        identity = item["module"], item["case"]
        if identity in expected:
            raise RuntimeError("The MongoDB collection manifest contains duplicate cases")
        expected[identity] = item["requires_fixture"]
    observed = {module for (module, _name), required in expected.items() if required}
    if observed != set(FIXTURE_MODULES):
        raise RuntimeError("Each declared MongoDB module must have a genuine fixture dependency")
    for required, fixture in ((REQUIRED, True), (NONFIXTURE_REQUIRED, False)):
        for module, names in required.items():
            for name in names:
                matches = [value for (mod, case), value in expected.items()
                           if mod == module and case.split("[", 1)[0] == name]
                if not matches or any(value is not fixture for value in matches):
                    raise RuntimeError("Required MongoDB cases have missing or incorrect fixture dependencies")
    return expected


def check_junit(path, manifest=None):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases or any(case.find(kind) is not None for case in cases for kind in ("skipped", "failure", "error")):
        raise RuntimeError("MongoDB protocol requires executed successful cases with zero skips")
    grouped = {name: [] for name in MODULES}
    for case in cases:
        module = case.get("classname", "").rsplit(".", 1)[-1]
        if module not in grouped:
            raise RuntimeError("The protocol report contains tests outside its declared scope")
        grouped[module].append(case.get("name", ""))
    for required in (REQUIRED, NONFIXTURE_REQUIRED):
        for module, values in required.items():
            names = {name.split("[", 1)[0] for name in grouped[module]}
            if not values <= names:
                raise RuntimeError("Required real or offline MongoDB protocol cases did not execute")
    scope = {}
    if manifest is not None:
        expected = _check_manifest(manifest)
        actual = [(module, name) for module, names in grouped.items() for name in names]
        if len(set(actual)) != len(actual) or set(actual) != set(expected):
            raise RuntimeError("The executed MongoDB cases do not exactly match the collection manifest")
        scope = {"collection_verified": True, "fixture_cases": sum(expected.values()),
                 "nonfixture_cases": sum(not value for value in expected.values())}
    return {"passed": len(cases), "failed": 0, "errors": 0, "skipped": 0, **scope,
            "modules": {name: len(values) for name, values in grouped.items()}}


def check_fixtures(directory, run_id):
    fixtures = [json.loads(path.read_text()) for path in directory.glob("fixture-*.json")]
    fixtures = [value for value in fixtures if value.get("run_id") == run_id]
    expected = set(FIXTURE_MODULES)
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


def _environment(root, directory, run_id):
    environment = dict(os.environ, OPENNAVICAT_TEST_MONGODB="1", QT_QPA_PLATFORM="offscreen",
        OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID=run_id,
        OPENNAVICAT_MONGODB_PROTOCOL_REPORT_DIR=str(directory / "fixtures"), PYTHONPATH=str(root))
    # There is no manual/reference endpoint path in this runner's contract.
    environment.pop("OPENNAVICAT_TEST_MONGODB_ENDPOINT", None)
    environment.pop("PYTEST_ADDOPTS", None)
    return environment


def collect_scope(root, directory, run_id, timeout):
    # Collection loads only the selected synthetic test modules and never starts
    # their fixtures. Persist exact parametrized identities and dependency
    # closure before executing, so a subset or missing variant cannot go green.
    path = directory / "collection.json"
    path.unlink(missing_ok=True)
    code = '''import json, os, pathlib, sys, pytest
class Manifest:
    def pytest_collection_finish(self, session):
        items = [{"module": item.path.stem, "case": item.name,
                  "requires_fixture": "mongodb_service" in item.fixturenames} for item in session.items]
        pathlib.Path(sys.argv[1]).write_text(json.dumps({
            "run_id": os.environ["OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID"],
            "scope": [pathlib.Path(value).stem for value in sys.argv[2:]], "items": items}, indent=2))
sys.exit(pytest.main(["--collect-only", "-q", "-W", "error", "-o", "addopts=", *sys.argv[2:]], plugins=[Manifest()]))
'''
    command = [sys.executable, "-c", code, str(path), *["tests/" + module + ".py" for module in MODULES]]
    result = subprocess.run(command, cwd=root, env=_environment(root, directory, run_id),
        capture_output=True, text=True, timeout=min(timeout, 60))
    (directory / "collection.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("MongoDB protocol test collection failed")
    manifest = json.loads(path.read_text())
    _check_manifest(manifest, run_id)
    return manifest


def run_tests(root, directory, run_id, timeout):
    environment = _environment(root, directory, run_id)
    command = [sys.executable, "-m", "pytest", "-q", "-W", "error", "-o", "addopts=",
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
    summary = {"passed": False, "run_id": run_id, "scope": list(MODULES), "fixture_scope": list(FIXTURE_MODULES),
        "manual_reference_endpoint_used": False}
    cleanup_verified = False
    for name in ("pytest-results.xml", "summary.json", "collection.json", "collection.log"):
        (directory / name).unlink(missing_ok=True)
    def interrupted(_signal, _frame): raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM): signal.signal(number, interrupted)
    try:
        manifest = collect_scope(root, directory, run_id, args.timeout)
        status = run_tests(root, directory, run_id, args.timeout)
        # Verify finalizer receipts even when assertions failed, and never let
        # previous-run evidence turn missing/failed/skipped cases green.
        summary["fixtures"] = check_fixtures(directory / "fixtures", run_id)
        cleanup_verified = True
        if status:
            summary["pytest_exit_code"] = status
            return status
        summary.update(check_junit(directory / "pytest-results.xml", manifest))
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
