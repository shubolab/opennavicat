#!/usr/bin/env python3
"""Run Oracle adapter tests against a new native amd64/arm64 pinned container.

Example (Docker engine endpoint is explicit when using a named context):
  .venv/bin/python scripts/test-oracle.py --docker-host unix:///path/docker.sock
The public gvenzl image is selected from the native engine architecture only.
Use --pull-image for an anonymous exact-digest pull into that engine's cache. No
Oracle account, system Instant Client, existing TNS/wallet or profile is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_oracle_integration.py"
SOURCE_PATHS = ("opennavicat/database.py", "opennavicat/oracle_session.py", "pyproject.toml", "uv.lock",
                "tests/test_oracle_session.py", "tests/test_oracle_runner.py", TARGET,
                "tests/oracle_owned_fixture.py", "tests/oracle_images.json", "scripts/test-oracle.py",
                "scripts/install-linux-dependencies.py", ".github/workflows/build.yml", ".github/workflows/oracle-native.yml")
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([sys.argv[2],'--collect-only','-q','--color=no'],plugins=[Collect()]))
'''


def hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_PATHS}


def redact_output(text, secrets):
    for secret in secrets:
        if type(secret) is str and secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def strict_junit(path, nodeids):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    module = TARGET[:-3].replace("/", ".")
    actual = []
    for case in cases:
        classname = case.get("classname", "")
        if classname != module and not classname.startswith(module + "."):
            raise RuntimeError("Oracle JUnit contains another module")
        suffix = classname[len(module):].lstrip(".").replace(".", "::")
        actual.append(TARGET + "::" + (suffix + "::" if suffix else "") + case.get("name", ""))
    if Counter(nodeids) != Counter(actual) or not cases or len(set(nodeids)) != len(nodeids):
        raise RuntimeError("Oracle JUnit does not match the exact collected cases")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise RuntimeError("Oracle actual-server scope requires every case passed with zero skips")
    return {"passed": len(cases), "skipped": 0, "failed": 0, "exact_cases": nodeids}


def read_required(path):
    expected = json.loads(Path(path).read_text())
    if (type(expected) is not list or not expected or any(type(n) is not str or not n.startswith(TARGET + "::") for n in expected)
            or len(set(expected)) != len(expected)):
        raise RuntimeError("Oracle required-case manifest is invalid")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-host")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pull-image", action="store_true")
    parser.add_argument("--cleanup-interrupted", action="store_true")
    args = parser.parse_args()
    if args.cleanup_interrupted and (args.output is None or args.pull_image):
        parser.error("cleanup requires --output and cannot pull an image")
    suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = (args.output or ROOT / "artifacts" / "oracle-basic-backend" / ("actual-" + suffix)).resolve()
    spec = importlib.util.spec_from_file_location("oracle_owned_fixture", ROOT / "tests/oracle_owned_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if args.cleanup_interrupted:
        out.mkdir(parents=True, exist_ok=True)
        try:
            cleanup = module.cleanup_interrupted(out, docker_host=args.docker_host)
        except BaseException as error:
            cleanup = {"passed": False, "cleanup_only": True, "no_start": True,
                       "no_database_connection": True, "error_class": type(error).__name__}
        (out / "cleanup-only.json").write_text(json.dumps(cleanup, indent=2) + "\n")
        print(json.dumps(cleanup))
        return 0 if cleanup["passed"] else 1
    out.mkdir(parents=True, exist_ok=False)
    fixture = module.OwnedOracle(out / "owned-fixture", docker_host=args.docker_host)
    before = hashes()
    (out / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt = {"base_revision": revision, "source_before": before, "python": platform.python_version(),
               "platform": platform.platform(), "module": TARGET, "passed": False}
    try:
        receipt["phase"] = "collect"
        required = out / "required-cases.json"
        collect = subprocess.run([sys.executable, "-c", COLLECT, str(required), TARGET],
                                 cwd=ROOT, capture_output=True, text=True, timeout=30)
        receipt["collection_exit_code"] = collect.returncode
        (out / "collect.log").write_text(redact_output(collect.stdout + collect.stderr,
            (fixture.admin_password, fixture.app_password, fixture.denied_password))[-32768:])
        if collect.returncode or not required.is_file():
            raise RuntimeError("Oracle scope collection failed")
        nodeids = read_required(required)
        receipt["expected_cases"] = nodeids
        receipt["phase"] = "fixture_start"
        fixture.start(pull=args.pull_image)
        env = {key: value for key, value in os.environ.items() if not key.startswith("OPENNAVICAT_TEST_ORACLE_")}
        env.update(fixture.environment())
        env["QT_QPA_PLATFORM"] = "offscreen"
        receipt["phase"] = "actual_tests"
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", TARGET,
            "--junitxml=" + str(out / "results.xml")], cwd=ROOT, env=env, text=True,
            capture_output=True, timeout=300)
        secrets = (fixture.admin_password, fixture.app_password, fixture.denied_password)
        (out / "pytest.log").write_text(redact_output(completed.stdout + completed.stderr, secrets))
        # Failure tracebacks/assertion rendering must not turn a real ephemeral
        # credential into a persisted diagnostic, even if a regression leaks it.
        junit = out / "results.xml"
        if junit.exists():
            junit.write_text(redact_output(junit.read_text(), secrets))
        receipt["test_process_reaped"] = True
        receipt["test_process_returncode"] = completed.returncode
        if completed.returncode:
            raise RuntimeError("Oracle actual-server tests failed; see sanitized pytest.log")
        receipt["tests"] = strict_junit(out / "results.xml", nodeids)
        receipt["passed"] = True
    except BaseException as error:
        receipt["error_class"] = type(error).__name__
        startup = module.startup_diagnostic(fixture.receipt)
        if startup:
            receipt["fixture_startup"] = startup
        detail = error.args[0] if getattr(error, "args", ()) else None
        code = getattr(detail, "full_code", None)
        if type(code) is str and __import__("re").fullmatch(r"(?:ORA|DPY|DPI)-[0-9]{4,5}", code):
            receipt["safe_driver_code"] = code
    finally:
        try:
            fixture.close()
            fixture.scan_secrets()
        except BaseException as error:
            receipt.update(passed=False, cleanup_error_class=type(error).__name__)
        after = hashes()
        receipt.update(source_after=after, source_unchanged=before == after,
                       exact_container_id=fixture.cid,
                       cleanup=fixture.receipt.get("exact_id_absent") is True)
        receipt["passed"] = bool(receipt["passed"] and receipt["source_unchanged"] and receipt["cleanup"])
        (out / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "verification": str(out / "verification.json")}))
    return 0 if receipt["passed"] else 1


def interrupted(*_args):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
