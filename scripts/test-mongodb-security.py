#!/usr/bin/env python3
"""Run fresh, real MongoDB TLS/SSH fixtures and require zero-skipped JUnit.

No existing endpoint or reference container is accepted. Requires Docker's
Linux MongoDB 8 image for the current architecture. Only this test module is run.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/mongodb-security"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    junit = directory / "pytest-results.xml"
    # Old evidence must not make a failed or empty new invocation appear green.
    for name in ["pytest-results.xml", "tls-fixture.json", "mtls-fixture.json", "certificate-cleanup.json", "summary.json"]:
        (directory / name).unlink(missing_ok=True)
    environment = dict(os.environ, OPENNAVICAT_TEST_MONGODB_SECURITY="1",
                       OPENNAVICAT_MONGODB_SECURITY_REPORT_DIR=str(directory), PYTHONPATH=str(root))
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    process = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-W", "error",
        "-o", "faulthandler_timeout=30", "tests/test_mongodb_security.py", "--junitxml=" + str(junit)],
        cwd=root, env=environment, start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    try:
        status = process.wait(timeout=args.timeout)
    except (KeyboardInterrupt, subprocess.TimeoutExpired):
        if os.name == "posix": os.killpg(process.pid, signal.SIGINT)
        else: process.send_signal(signal.CTRL_BREAK_EVENT)
        try:
            process.wait(timeout=30)  # Give pytest fixture finalizers time to run.
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        raise
    if status:
        return status
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    if len(cases) < 20 or any(case.find(kind) is not None for case in cases for kind in ["failure", "error", "skipped"]):
        raise RuntimeError("MongoDB security requires actual successful cases with zero skips")
    fixtures = [json.loads((directory / name).read_text()) for name in ["tls-fixture.json", "mtls-fixture.json"]]
    if not all(value.get("cleanup_removed") is True and value.get("credential_environment_removed") is True for value in fixtures):
        raise RuntimeError("Owned MongoDB container cleanup did not pass")
    if json.loads((directory / "certificate-cleanup.json").read_text()).get("private_certificate_directory_removed") is not True:
        raise RuntimeError("Private certificate cleanup did not pass")
    result = dict(passed=len(cases), failed=0, errors=0, skipped=0, containers=fixtures,
                  private_certificates_removed=True, existing_reference_containers_untouched=True)
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": len(cases), "skipped": 0, "owned_cleanup": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
