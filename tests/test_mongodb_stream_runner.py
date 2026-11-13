"""Opt-in destructive fallback checks using only newly created stopped resources.

No container is started and no database endpoint is used. The separate cleanup
ledger always retains the exact IDs created here, including deliberately invalid
runner metadata, and never discovers containers by a broad label/name search.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts/test-mongodb-stream.py"
RUNNER_SHA256 = hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest()
TESTS_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location("owned_stream_runner_cleanup", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

pytestmark = pytest.mark.skipif(
    os.environ.get("OPENNAVICAT_TEST_MONGODB_STREAM_CLEANUP") != "1",
    reason="Fresh stopped Docker cleanup fixtures require explicit opt-in",
)


def docker(*args, check=True):
    value = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    if check and value.returncode:
        raise RuntimeError("Owned cleanup test Docker operation failed: " + args[0])
    return value


def inspect(kind, identity):
    result = docker(kind, "inspect", identity, check=False)
    if result.returncode:
        if (json.loads(result.stdout or "null") == [] and identity in result.stderr
                and ("no such object" in result.stderr.lower()
                     or "no such container" in result.stderr.lower()
                     or "no such volume" in result.stderr.lower())):
            return None
        raise RuntimeError("Could not establish exact owned resource state")
    info, = json.loads(result.stdout)
    return info


class OwnedStoppedResources:
    def __init__(self):
        self.audit_token = uuid.uuid4().hex
        self.run_id = uuid.uuid4().hex
        self.containers = {}
        self.names = set()
        self.volumes = set()
        self.named_volumes = set()
        self.observations = {}
        self.on_change = lambda: None

    def _remember(self, info):
        assert info["Config"]["Labels"]["opennavicat.stream-cleanup-audit"] == self.audit_token
        assert info["Config"]["Image"] == runner.IMAGE
        assert info["Name"].removeprefix("/") in self.names
        assert info["State"]["Running"] is False
        identity = info["Id"]
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        self.containers[identity] = info["Name"]
        self.volumes.update(mount["Name"] for mount in info["Mounts"] if mount["Type"] == "volume")
        self.on_change()
        return identity

    def create(self, *, owner=None, labels=None, name_suffix="", host_ip="127.0.0.1", mount=None):
        owner = owner or uuid.uuid4().hex
        name = "opennavicat-stream-" + owner[:12] + name_suffix
        self.names.add(name)
        self.on_change()
        values = {"opennavicat.owner": owner, "opennavicat.test": "true",
                  "opennavicat.task": "finite-stream-tests", "opennavicat.stream-run": self.run_id,
                  **(labels or {}), "opennavicat.stream-cleanup-audit": self.audit_token}
        command = ["container", "create", "--pull=never", "--name", name,
                   "--publish", host_ip + "::27017"]
        for key, value in values.items():
            command += ["--label", key + "=" + value]
        if mount:
            command += ["--mount", "type=volume,source=" + mount[0] + ",target=" + mount[1] + ",volume-nocopy"]
        command.append(runner.IMAGE)
        identity = docker(*command).stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        info = inspect("container", identity)
        assert info is not None
        self._remember(info)
        return {"run_id": self.run_id, "container_id": identity, "owner": owner,
                "name": "opennavicat-stream-" + owner[:12],
                "volumes": [mount["Name"] for mount in info["Mounts"] if mount["Type"] == "volume"]}

    def create_named_volume(self, *, hexadecimal=False):
        name = (uuid.uuid4().hex + uuid.uuid4().hex if hexadecimal
                else "opennavicat-stream-cleanup-audit-" + self.audit_token)
        self.named_volumes.add(name)
        self.volumes.add(name)
        self.on_change()
        docker("volume", "create", "--label", "opennavicat.stream-cleanup-audit=" + self.audit_token, name)
        info = inspect("volume", name)
        assert info["Labels"]["opennavicat.stream-cleanup-audit"] == self.audit_token
        self.on_change()
        return name

    def assert_retained(self):
        for identity in self.containers:
            info = inspect("container", identity)
            assert info is not None and info["Id"] == identity and info["State"]["Running"] is False
        for name in self.volumes:
            assert inspect("volume", name) is not None
        self.observations["refused_resources_retained"] = True

    def cleanup(self):
        # A create timeout can occur after Docker created the object; inspect
        # only the exact unique names allocated by this test and verify its token.
        for name in self.names:
            info = inspect("container", name)
            if info is not None:
                self._remember(info)
        errors = []
        for identity, name in reversed(list(self.containers.items())):
            try:
                info = inspect("container", identity)
                if info is not None:
                    assert info["Name"] == name
                    self._remember(info)
                    docker("rm", "--force", "--volumes", identity)
                assert inspect("container", identity) is None
            except Exception as error:
                errors.append(type(error).__name__)
        for name in self.volumes:
            try:
                info = inspect("volume", name)
                if info is not None:
                    if name in self.named_volumes:
                        assert info["Labels"]["opennavicat.stream-cleanup-audit"] == self.audit_token
                    users = docker("container", "ls", "--all", "--no-trunc", "--filter",
                                   "volume=" + name, "--format", "{{.ID}}").stdout.split()
                    assert not users, "Never remove an owned volume used by any remaining container"
                    docker("volume", "rm", name)
                assert inspect("volume", name) is None
            except Exception as error:
                errors.append(type(error).__name__)
        return errors


@pytest.fixture
def owned_stopped(request):
    # A local image is required; --pull=never never fetches an image implicitly.
    image, = json.loads(docker("image", "inspect", runner.IMAGE).stdout)
    assert image["Os"] == "linux"
    owned = OwnedStoppedResources()
    directory = Path(os.environ.get("OPENNAVICAT_MONGODB_STREAM_CLEANUP_REPORT_DIR",
                                    ROOT / "artifacts/stream-cleanup-audit"))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("cleanup-" + owned.audit_token + ".json")

    def save(*, completed=False, errors=()):
        value = {"case": request.node.nodeid, "audit_token": owned.audit_token,
                 "qualification_run_id": os.environ.get("OPENNAVICAT_MONGODB_STREAM_CLEANUP_RUN_ID", ""),
                 "run_id": owned.run_id, "image": runner.IMAGE,
                 "runner_sha256": RUNNER_SHA256, "tests_sha256": TESTS_SHA256,
                 "runner_source_unchanged": hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest() == RUNNER_SHA256,
                 "tests_source_unchanged": hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == TESTS_SHA256,
                 "containers_created": list(owned.containers), "volumes_created": sorted(owned.volumes),
                 "allocated_container_names": sorted(owned.names),
                 "cleanup_completed": completed, "all_created_resources_absent": completed and not errors,
                 "cleanup_errors": list(errors), "containers_started": 0, **owned.observations}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)

    owned.on_change = save
    save()
    try:
        yield owned
    finally:
        errors = owned.cleanup()
        unchanged = hashlib.sha256(RUNNER_PATH.read_bytes()).hexdigest() == RUNNER_SHA256
        save(completed=True, errors=errors)
        assert not errors, "Exact owned fixture cleanup could not be established"
        assert unchanged, "Runner source changed during cleanup qualification"
        assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == TESTS_SHA256


def receipt(directory, value):
    (directory / "fixture-owned.json").write_text(json.dumps(value) + "\n")


def test_exact_stopped_container_and_exclusive_anonymous_volumes_are_removed(owned_stopped, tmp_path):
    value = owned_stopped.create()
    assert len(value["volumes"]) == 2 and all(re.fullmatch(r"[0-9a-f]{64}", name) for name in value["volumes"])
    receipt(tmp_path, value)
    removed = runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    assert removed == [{"container_id": value["container_id"], "anonymous_volumes": value["volumes"]}]
    assert inspect("container", value["container_id"]) is None
    assert all(inspect("volume", name) is None for name in value["volumes"])
    assert runner.cleanup_interrupted(tmp_path, owned_stopped.run_id) == []
    owned_stopped.observations["fallback_removed_only_exact_owned_container_and_exclusive_volumes"] = True


@pytest.mark.parametrize("label", ["opennavicat.owner", "opennavicat.stream-run",
                                  "opennavicat.task", "opennavicat.test"])
def test_mismatched_real_container_ownership_labels_refuse_and_retain(owned_stopped, tmp_path, label):
    value = owned_stopped.create(labels={label: uuid.uuid4().hex})
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="exact ownership"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()


@pytest.mark.parametrize("mismatch", ["receipt_run", "container_name", "non_loopback_binding"])
def test_mismatched_run_name_or_binding_refuses_and_retains(owned_stopped, tmp_path, mismatch):
    value = owned_stopped.create(name_suffix="-different" if mismatch == "container_name" else "",
                                 host_ip="0.0.0.0" if mismatch == "non_loopback_binding" else "127.0.0.1")
    if mismatch == "receipt_run":
        value["run_id"] = uuid.uuid4().hex
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="different invocation|exact ownership"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()


def test_cid_of_another_new_owned_container_cannot_rebind_a_receipt(owned_stopped, tmp_path):
    value, other = owned_stopped.create(), owned_stopped.create()
    value["container_id"] = other["container_id"]
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="exact ownership"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()


def test_anonymous_volume_shared_by_another_stopped_container_refuses_and_retains(owned_stopped, tmp_path):
    value = owned_stopped.create()
    shared = value["volumes"][0]
    other = owned_stopped.create(mount=(shared, "/generated-shared"))
    assert shared in other["volumes"]
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="shared by another container"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()
    owned_stopped.observations["shared_anonymous_volume_refused"] = True


def test_incomplete_volume_receipt_refuses_and_retains_actual_volumes(owned_stopped, tmp_path):
    value = owned_stopped.create()
    value["volumes"] = value["volumes"][:1]
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="volume identity differs"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()


def test_named_volume_is_never_treated_as_disposable_anonymous_storage(owned_stopped, tmp_path):
    named = owned_stopped.create_named_volume()
    value = owned_stopped.create(mount=(named, "/data/db"))
    receipt(tmp_path, value)
    with pytest.raises(RuntimeError, match="explicit or inherited mounts"):
        runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    owned_stopped.assert_retained()


def test_explicitly_named_hexadecimal_volume_refuses_before_container_removal(owned_stopped, tmp_path):
    named = owned_stopped.create_named_volume(hexadecimal=True)
    value = owned_stopped.create(mount=(named, "/data/db"))
    info = inspect("container", value["container_id"])
    assert any(mount.get("Source") == named for mount in info["HostConfig"]["Mounts"])
    owned_stopped.observations["explicitly_named_hexadecimal_volume"] = named
    receipt(tmp_path, value)
    try:
        with pytest.raises(RuntimeError, match="explicit or inherited mounts"):
            runner.cleanup_interrupted(tmp_path, owned_stopped.run_id)
    finally:
        owned_stopped.observations["container_retained_after_refusal"] = inspect("container", value["container_id"]) is not None
        owned_stopped.observations["named_volume_retained_after_refusal"] = inspect("volume", named) is not None
    owned_stopped.assert_retained()
