#!/usr/bin/env python3
"""Qualify finite streams on a fresh owned native authenticated replica set."""
from __future__ import annotations

import argparse
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
TARGET = "tests/test_mongodb_pipeline_stream.py"
IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
SOURCE_PATHS = (TARGET, "opennavicat/mongodb_pipeline.py", "opennavicat/mongodb_pipeline_stream.py",
                "opennavicat/mongodb_session.py", "scripts/test-mongodb-stream.py",
                ".github/workflows/build.yml")
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([sys.argv[2],'--collect-only','-q'],plugins=[Collect()]))
'''


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError("Owned stream cleanup Docker command failed: " + args[0])
    return result


def absent(kind, identity):
    result = docker(kind, "inspect", identity, check=False)
    return result.returncode != 0 and "no such" in result.stderr.lower()


def cleanup_interrupted(directory, run_id):
    """Touch only this invocation's exact owned container and exclusive volumes."""
    removed = []
    for path in directory.glob("fixture-*.json"):
        receipt = json.loads(path.read_text())
        if receipt.get("run_id") != run_id:
            raise RuntimeError("Stream fixture receipt belongs to a different invocation")
        identity = receipt.get("container_id")
        if identity is None:
            continue
        owner, name = receipt.get("owner", ""), receipt.get("name", "")
        if (not re.fullmatch("[0-9a-f]{64}", identity) or not re.fullmatch("[0-9a-f]{32}", owner)
                or name != "opennavicat-stream-" + owner[:12]):
            raise RuntimeError("Stream fixture cleanup identity is invalid")
        if absent("container", identity):
            for volume in receipt.get("volumes", []):
                if not absent("volume", volume):
                    raise RuntimeError("Stream fixture has an unconfirmed remaining volume")
            continue
        info, = json.loads(docker("container", "inspect", identity).stdout)
        labels = info.get("Config", {}).get("Labels", {})
        host = info.get("HostConfig", {})
        bindings = host.get("PortBindings", {}).get("27017/tcp", [])
        if (info["Id"] != identity or info["Name"] != "/" + name
                or labels.get("opennavicat.owner") != owner
                or labels.get("opennavicat.test") != "true"
                or labels.get("opennavicat.task") != "finite-stream-tests"
                or labels.get("opennavicat.stream-run") != run_id
                or len(bindings) != 1 or bindings[0].get("HostIp") != "127.0.0.1"):
            raise RuntimeError("Refusing stream cleanup without exact ownership and loopback binding")
        # A user can explicitly name a volume with 64 hexadecimal characters.
        # Only this fixture's implicit image volumes are cleanup candidates;
        # reject explicit/inherited mounts before removing its container.
        if host.get("Binds") or host.get("Mounts") or host.get("VolumesFrom"):
            raise RuntimeError("Refusing stream cleanup with explicit or inherited mounts")
        volumes = [mount["Name"] for mount in info.get("Mounts", []) if mount["Type"] == "volume"]
        if any(not re.fullmatch("[0-9a-f]{64}", value) for value in volumes):
            raise RuntimeError("Refusing to remove a non-anonymous stream volume")
        if receipt.get("volumes") and set(receipt["volumes"]) != set(volumes):
            raise RuntimeError("Stream fixture volume identity differs from its receipt")
        for volume in volumes:
            users = docker("container", "ls", "--all", "--no-trunc", "--filter", "volume=" + volume,
                           "--format", "{{.ID}}").stdout.split()
            if users != [identity]:
                raise RuntimeError("Refusing to remove a stream volume shared by another container")
        docker("rm", "--force", "--volumes", identity)
        if not absent("container", identity) or any(not absent("volume", value) for value in volumes):
            raise RuntimeError("Stream emergency cleanup could not be confirmed")
        removed.append({"container_id": identity, "anonymous_volumes": volumes})
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    directory = args.evidence.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fixtures = directory / "fixtures"
    fixtures.mkdir()
    run_id = uuid.uuid4().hex
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_PATHS}
    summary = dict(verified=False, run_id=run_id, target=TARGET, reference_endpoint_used=False,
                   source_sha256=hashes, emergency_removed=[])
    environment = dict(os.environ, PYTHONPATH=str(ROOT), OPENNAVICAT_TEST_MONGODB="1",
        OPENNAVICAT_MONGODB_STREAM_REPORT_DIR=str(fixtures), OPENNAVICAT_MONGODB_STREAM_RUN_ID=run_id,
        QT_QPA_PLATFORM="offscreen")
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("OPENNAVICAT_TEST_MONGODB_ENDPOINT", None)
    process = None

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    try:
        required = directory / "required-cases.json"
        with (directory / "collection.log").open("w") as output:
            subprocess.run([sys.executable, "-c", COLLECT, str(required), TARGET], cwd=ROOT,
                env=environment, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
        expected = json.loads(required.read_text())
        if (not expected or len(set(expected)) != len(expected)
                or any(not node.startswith(TARGET + "::") for node in expected)):
            raise RuntimeError("Finite stream collection did not match its exact declared scope")
        with (directory / "pytest.log").open("w") as output:
            process = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-W", "error", TARGET,
                "--junitxml=" + str(directory / "results.xml")], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            try:
                status = process.wait(timeout=args.timeout)
            finally:
                if process.poll() is None:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGINT)
                    else:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
        cases = list(ET.parse(directory / "results.xml").getroot().iter("testcase"))
        if (status or len(cases) != len(expected)
                or {case.get("name") for case in cases} != {node.split("::", 1)[1] for node in expected}
                or any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped"))):
            raise RuntimeError("Finite stream qualification requires its full exact scope with zero skips")
        receipts = [json.loads(path.read_text()) for path in fixtures.glob("fixture-*.json")]
        if len(receipts) != 1:
            raise RuntimeError("Finite stream qualification requires one fresh owned replica set")
        receipt, = receipts
        canonical = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
        native_arch = canonical.get(receipt.get("native_image_architecture"))
        if (receipt.get("run_id") != run_id or receipt.get("image") != IMAGE
                or receipt.get("server_version") != "8.0.34"
                or native_arch is None or canonical.get(receipt.get("docker_architecture")) != native_arch
                or any(receipt.get(field) is not True for field in (
                    "cleanup_removed", "exact_container_and_volumes_absent"))):
            raise RuntimeError("Finite stream native replica-set identity/cleanup proof is incomplete")
        if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value for name, value in hashes.items()):
            raise RuntimeError("Finite stream source changed during qualification")
        summary.update(verified=True, passed=len(cases), failed=0, errors=0, skipped=0, fixtures=receipts)
    except BaseException as error:
        summary["error_type"] = type(error).__name__
        raise
    finally:
        try:
            summary["emergency_removed"] = cleanup_interrupted(fixtures, run_id)
        except Exception as error:
            summary.update(verified=False, cleanup_error_type=type(error).__name__)
            raise
        finally:
            (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"verified": True, "passed": summary["passed"], "skipped": 0,
                      "exact_owned_replica_set_cleaned": True}))


if __name__ == "__main__":
    main()
