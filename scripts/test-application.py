#!/usr/bin/env python3
"""Run the complete default suite with independent collection and source receipts."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Windows ARM64 must select its explicit native OpenSSL build before hashlib/pytest.
import opennavicat

import argparse
import ast
from collections import Counter
import hashlib
import importlib.util
import json
import platform
import shutil
import signal
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET

SOURCE_PREFIXES = ("opennavicat/", "tests/", "scripts/", "native/", "packaging/", ".github/workflows/")
ROOT_INPUTS = ("pyproject.toml", "uv.lock", ".gitattributes", ".gitignore", "OpenNavicat.spec")


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def case_key(node):
    address, bracket, parameters = node.partition("[")
    parts = address.split("::")
    if len(parts) < 2 or not parts[0].startswith("tests/") or not parts[0].endswith(".py"):
        raise ValueError("Default collection contains an unexpected test address")
    return ".".join([parts[0][:-3].replace("/", "."), *parts[1:-1]]), parts[-1] + bracket + parameters


def verify_cases(collected, executed, junit):
    if not collected or executed != collected or len(set(collected)) != len(collected):
        raise ValueError("Default collection must be nonempty, unique and identical during execution")
    expected = Counter(case_key(node) for node in collected)
    cases = list(ET.parse(junit).iter("testcase"))
    if Counter((case.get("classname"), case.get("name")) for case in cases) != expected:
        raise ValueError("Default JUnit differs from the complete collected multiset")
    counts = {"total": len(cases), "passed": 0, "skipped": 0, "failed": 0, "errors": 0}
    for case in cases:
        outcome = next((label for tag, label in (("failure", "failed"), ("error", "errors"), ("skipped", "skipped"))
                        if case.find(tag) is not None), "passed")
        counts[outcome] += 1
    if counts["failed"] or counts["errors"]:
        raise ValueError("Default JUnit contains failures or errors")
    return counts


def source_snapshot():
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    if any(path and (path.startswith(SOURCE_PREFIXES) or path in ROOT_INPUTS) for path in untracked):
        raise RuntimeError("Untracked default-suite input must be committed before verification")
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    selected = sorted(path for path in paths if path and (path.startswith(SOURCE_PREFIXES) or path in ROOT_INPUTS))
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in selected}


def helper_snapshot(*, required=False):
    directory = ROOT / "artifacts/js-engine"
    name = "opennavicat-js.exe" if os.name == "nt" else "opennavicat-js"
    raw_paths = [directory / item for item in (name, "build-receipt.json", "LICENSE.quickjs-ng")]
    if not any(path.exists() for path in raw_paths) and not required:
        return {}
    paths = raw_paths[:]
    processed = directory / "processed-payload"
    if processed.exists():
        paths += [processed / item for item in (name, "build-receipt.json", "payload-manifest.json", "LICENSE.quickjs-ng")]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise RuntimeError("Native helper inputs must be complete regular files")
    hashes = {path.relative_to(ROOT).as_posix(): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "bytes": path.stat().st_size} for path in paths}
    constants = {}
    for node in ast.parse((ROOT / "scripts/build-quickjs.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("REVISION", "ARCHIVE_SHA256"):
                    constants[target.id] = ast.literal_eval(node.value)
    runner_hash = hashlib.sha256((ROOT / "native/quickjs/runner.c").read_bytes()).hexdigest()
    def digest(path):
        return hashes[path.relative_to(ROOT).as_posix()]["sha256"]
    for folder in (directory, processed) if processed.exists() else (directory,):
        receipt = json.loads((folder / "build-receipt.json").read_text(encoding="utf-8"))
        if (receipt.get("source_revision") != constants["REVISION"]
                or receipt.get("archive_sha256") != constants["ARCHIVE_SHA256"]
                or receipt.get("runner_source_sha256") != runner_hash or receipt.get("helper_sha256") != digest(folder / name)):
            raise RuntimeError("Native helper differs from source or its build receipt")
        if folder == processed:
            manifest = json.loads((processed / "payload-manifest.json").read_text(encoding="utf-8"))
            if (receipt.get("source_helper_sha256") != digest(directory / name)
                    or manifest.get("format") != "opennavicat-js-payload-v1"
                    or manifest.get("bootstrap_sha256") != hashlib.sha256((ROOT / "opennavicat/js_engine/bootstrap.js").read_bytes()).hexdigest()
                    or manifest.get("assets") != {item: digest(processed / item) for item in (name, "build-receipt.json", "LICENSE.quickjs-ng")}):
                raise RuntimeError("Processed helper does not bind to the current raw helper and bootstrap")
    return hashes


def freeze_helper_inputs(out, hashes):
    """Retain the actual test inputs before a later package build replaces them."""
    frozen = {}
    for relative, expected in hashes.items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("artifacts", "js-engine"):
            raise RuntimeError("Invalid native helper input address")
        source = ROOT / path
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("Native helper input must remain a regular file")
        content = source.read_bytes()
        if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise RuntimeError("Native helper changed before its input bytes were retained")
        stored = Path("helper-inputs") / path
        target = out / stored
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
        frozen[relative] = {**expected, "stored_path": stored.as_posix()}
    write(out / "helper-inputs.json", frozen)
    return frozen


def verify_frozen_helper_inputs(out, hashes, frozen):
    if set(hashes) != set(frozen) or json.loads((out / "helper-inputs.json").read_text(encoding="utf-8")) != frozen:
        raise RuntimeError("Retained native helper input inventory changed")
    for relative, expected in hashes.items():
        stored = Path("helper-inputs") / relative
        record = {**expected, "stored_path": stored.as_posix()}
        target = out / stored
        if frozen[relative] != record or not target.is_file() or target.is_symlink():
            raise RuntimeError("Retained native helper input address changed")
        content = target.read_bytes()
        if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise RuntimeError("Retained native helper input bytes changed")
    return True


def git_snapshot(revision, paths):
    return {path: hashlib.sha256(subprocess.check_output(["git", "show", revision + ":" + path], cwd=ROOT)).hexdigest()
            for path in paths}


def child_environment(owned):
    env = {key: value for key, value in os.environ.items()
           if key == "OPENNAVICAT_LIBPQ_DIR" or not key.startswith(("OPENNAVICAT_", "PYTEST_"))}
    # Explicit plugins also reach existing isolated subprocess tests, whose
    # pytest invocations do not inherit this runner's command-line -p option.
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_PLUGINS="pytestqt.plugin", PYTHONUTF8="1",
               OPENNAVICAT_DATA_DIR=str(owned / "store"))
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH", ""))))
    return env


def child_main(phase, out, owned, run_id):
    import pytest
    from PySide6.QtWidgets import QStyleFactory
    out, owned = Path(out), Path(owned)
    class Evidence:
        def pytest_collection_finish(self, session):
            write(out / (phase + "-cases.json"), [item.nodeid for item in session.items])
        def pytest_sessionfinish(self, session, exitstatus):
            write(out / (phase + "-runtime.json"), {
                "phase": phase, "run_id": run_id, "exitstatus": int(exitstatus),
                "source_root": str(ROOT), "python": sys.version, "platform": platform.system(),
                "architecture": platform.machine(), "collection_only": phase == "collect",
                "qt_style_names": QStyleFactory.keys(),
                "human_reference_acceptance": False})
    args = ["-p", "pytestqt.plugin", "-q", "-W", "error", "--color=no", "-o", "addopts=",
            "-o", "cache_dir=" + str(owned / "cache"), "--basetemp=" + str(owned / phase)]
    args += ["--collect-only"] if phase == "collect" else ["--junitxml=" + str(out / "junit.xml")]
    # No test path, -k, -m or deselection is accepted: use pyproject's full testpaths.
    return pytest.main(args, plugins=[Evidence()])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit-output", type=Path, help="Also retain an identical JUnit file for existing consumers")
    parser.add_argument("--require-native-helper", action="store_true")
    args = parser.parse_args(argv)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    receipt = {"passed": False, "run_id": uuid.uuid4().hex, "source_revision": revision,
               "human_reference_acceptance": False, "conditional_skips_allowed": True}
    before, helper_before, frozen_helpers, owned = None, None, None, None
    def interrupted(_number, _frame):
        raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    try:
        before = source_snapshot()
        write(out / "source-before.json", before)
        blobs = git_snapshot(revision, before)
        write(out / "source-git.json", blobs)
        if before != blobs:
            raise RuntimeError("Default verification requires committed source bytes")
        helper_before = helper_snapshot(required=args.require_native_helper)
        write(out / "helper-before.json", helper_before)
        frozen_helpers = freeze_helper_inputs(out, helper_before)
        owner_file = "windows_child_owner.py" if os.name == "nt" else "test-sqlite-window.py"
        spec = importlib.util.spec_from_file_location("default_child_owner", ROOT / "scripts" / owner_file)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        with tempfile.TemporaryDirectory(prefix="opennavicat-default-") as directory:
            owned = Path(directory).resolve()
            env = child_environment(owned)
            for phase, timeout in (("collect", 180), ("run", 1200)):
                command = [sys.executable, str(Path(__file__).resolve()), "--_child", phase,
                           str(out), str(owned), receipt["run_id"]]
                if os.name == "nt":
                    result = owner.run_owned_child(command, env, owned / (phase + ".raw"), cwd=ROOT, timeout=timeout)
                else:
                    result = owner.bounded_child(command, env, owned / (phase + ".raw"), timeout=timeout)
                (out / (phase + ".log")).write_text(result.pop("output"), encoding="utf-8")
                receipt[phase] = result
                write(out / (phase + "-process.json"), result)
                if not owner.process_passed(result):
                    raise RuntimeError("Default verification child failed: " + phase)
                runtime = json.loads((out / (phase + "-runtime.json")).read_text(encoding="utf-8"))
                if (runtime.get("run_id") != receipt["run_id"] or runtime.get("exitstatus") != 0
                        or runtime.get("source_root") != str(ROOT) or runtime.get("phase") != phase):
                    raise ValueError("Default verification runtime identity differs")
            collected = json.loads((out / "collect-cases.json").read_text(encoding="utf-8"))
            executed = json.loads((out / "run-cases.json").read_text(encoding="utf-8"))
            receipt["counts"] = verify_cases(collected, executed, out / "junit.xml")
            receipt["skip_reasons"] = dict(Counter(case.find("skipped").get("message", "")
                for case in ET.parse(out / "junit.xml").iter("testcase") if case.find("skipped") is not None))
            receipt["passed"] = True
    except (Exception, KeyboardInterrupt) as error:
        receipt.update(passed=False, error_class=type(error).__name__, error=str(error))
    finally:
        try:
            after = source_snapshot()
            write(out / "source-after.json", after)
            receipt["source_equal_before_after_git"] = before is not None and before == after == git_snapshot(revision, after)
        except Exception as error:
            receipt.update(source_equal_before_after_git=False, source_error_class=type(error).__name__)
        try:
            helper_after = helper_snapshot(required=args.require_native_helper)
            write(out / "helper-after.json", helper_after)
            receipt["helper_equal_before_after"] = helper_before is not None and helper_before == helper_after
            receipt["helper_inputs_frozen"] = verify_frozen_helper_inputs(out, helper_before, frozen_helpers)
        except Exception as error:
            receipt.update(helper_equal_before_after=False, helper_inputs_frozen=False, helper_error_class=type(error).__name__)
        receipt["same_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == revision
        receipt["owned_store_removed"] = owned is not None and not owned.exists()
        receipt["passed"] = bool(receipt["passed"] and receipt["source_equal_before_after_git"]
                                 and receipt["helper_equal_before_after"] and receipt["helper_inputs_frozen"]
                                 and receipt["same_head"] and receipt["owned_store_removed"])
        if args.junit_output and (out / "junit.xml").exists():
            args.junit_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / "junit.xml", args.junit_output)
        write(out / "verification.json", receipt)
    print(json.dumps(receipt))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--_child":
        raise SystemExit(child_main(*sys.argv[2:]))
    raise SystemExit(main())
