#!/usr/bin/env python3
"""Run fresh, real MongoDB TLS/SSH fixtures and require zero-skipped JUnit.

No existing endpoint or reference container is accepted. Requires Docker's
Linux MongoDB 8 image for the current architecture. Only the selected module is run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET


SCOPES = {
    "regular": ("tests/test_mongodb_security.py", 20),
    "stream": ("tests/test_mongodb_stream_security.py", 2),
}
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([sys.argv[2],'--collect-only','-q'],plugins=[Collect()]))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/mongodb-security"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--scope", choices=SCOPES, default="regular")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    target, minimum_cases = SCOPES[args.scope]
    junit = directory / "pytest-results.xml"
    environment = dict(os.environ, OPENNAVICAT_TEST_MONGODB_SECURITY="1",
                       OPENNAVICAT_MONGODB_SECURITY_REPORT_DIR=str(directory), PYTHONPATH=str(root))
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    required = directory / "required-cases.json"
    with (directory / "collection.log").open("w") as output:
        subprocess.run([sys.executable, "-c", COLLECT, str(required), target], cwd=root,
                       env=environment, stdout=output, stderr=subprocess.STDOUT,
                       check=True, timeout=60)
    expected = json.loads(required.read_text())
    if (len(expected) < minimum_cases or len(set(expected)) != len(expected)
            or any(not node.startswith(target + "::") for node in expected)):
        raise RuntimeError("MongoDB security collection did not match its declared scope")
    process = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-W", "error",
        "-o", "faulthandler_timeout=30", target, "--junitxml=" + str(junit)],
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
    if (len(cases) != len(expected)
            or {case.get("name") for case in cases} != {node.split("::", 1)[1] for node in expected}
            or any(case.find(kind) is not None for case in cases for kind in ["failure", "error", "skipped"])):
        raise RuntimeError("MongoDB security requires actual successful cases with zero skips")
    fixtures = [json.loads((directory / name).read_text()) for name in ["tls-fixture.json", "mtls-fixture.json"]]
    if not all(value.get("cleanup_removed") is True and value.get("credential_environment_removed") is True for value in fixtures):
        raise RuntimeError("Owned MongoDB container cleanup did not pass")
    if json.loads((directory / "certificate-cleanup.json").read_text()).get("private_certificate_directory_removed") is not True:
        raise RuntimeError("Private certificate cleanup did not pass")
    result = dict(passed=len(cases), failed=0, errors=0, skipped=0, containers=fixtures,
                  private_certificates_removed=True, existing_reference_containers_untouched=True,
                  scope=args.scope, target=target, exact_required_manifest_matched=True,
                  source_sha256={name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
                      target, "tests/test_mongodb_security.py", "opennavicat/mongodb_session.py",
                      "opennavicat/mongodb_pipeline.py", "opennavicat/mongodb_pipeline_stream.py",
                      "scripts/test-mongodb-security.py", ".github/workflows/build.yml")})
    if args.scope == "stream":
        owned = [json.loads((directory / name).read_text()) for name in (
            "stream-tls-owned-cleanup.json", "stream-mtls-owned-cleanup.json")]
        if any(value.get(field) is not True for value in owned for field in (
                "exact_container_and_volumes_absent", "credential_environment_removed", "loopback_endpoint_closed")):
            raise RuntimeError("Stream TLS/SSH exact container and volume cleanup did not pass")
        if {value["container_id"] for value in owned} != {value["container_id"] for value in fixtures}:
            raise RuntimeError("Stream transport and cleanup fixture identities differ")
        for name, fields in {
            "stream-certificate-cleanup.json": ("private_certificate_directory_removed",),
            "stream-ssh-cleanup.json": ("loopback_listener_closed", "transports_closed",
                                       "worker_threads_stopped", "generated_private_keys_removed"),
            "stream-client-ssh.json": ("verify_full_original_hostname", "encrypted_client_certificate_used",
                                       "finite_network_settings", "clients_closed_before_tunnel_and_pem", "stream_cache_cleared"),
            "stream-client-mtls.json": ("verify_full", "mutual_tls_required", "encrypted_client_key_used",
                                        "finite_network_settings", "clients_closed_before_pem", "stream_cache_cleared"),
        }.items():
            value = json.loads((directory / name).read_text())
            if any(value.get(field) is not True for field in fields):
                raise RuntimeError("Stream transport qualification is incomplete: " + name)
            if name.startswith("stream-client-") and (value.get("retry_reads") is not False
                    or value.get("real_standalone_stream_rejection") != 40573
                    or value.get("replica_set_event_acceptance") is not False):
                raise RuntimeError("Stream transport acceptance boundary differs: " + name)
        result.update(exact_stream_fixture_cleanup=owned, replica_set_event_acceptance=False)
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": len(cases), "skipped": 0, "owned_cleanup": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
