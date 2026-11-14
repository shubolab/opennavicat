"""Complete-default collection cannot be filtered or substituted by a receipt."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("application_qualification", ROOT / "scripts/test-application.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def junit(path, nodes, outcome=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for node in nodes:
        module, name = runner.case_key(node)
        case = ET.SubElement(suite, "testcase", classname=module, name=name)
        if outcome:
            ET.SubElement(case, outcome)
    ET.ElementTree(root).write(path)


@pytest.mark.parametrize("mutation", ["empty", "missing", "reordered", "duplicate", "foreign-junit", "failure", "error"])
def test_complete_collection_and_junit_cannot_be_filtered_or_replaced(tmp_path, mutation):
    nodes = ["tests/test_owned.py::test_one[with::delimiter]", "tests/test_owned.py::TestOwned::test_two"]
    collected, executed, reported = nodes[:], nodes[:], nodes[:]
    outcome = None
    if mutation == "empty": collected = executed = []
    elif mutation == "missing": executed.pop()
    elif mutation == "reordered": executed.reverse()
    elif mutation == "duplicate": collected = executed = [*nodes, nodes[0]]
    elif mutation == "foreign-junit": reported[0] = "tests/test_other.py::test_one"
    else: outcome = mutation
    path = tmp_path / "results.xml"
    junit(path, reported, outcome)
    with pytest.raises(ValueError):
        runner.verify_cases(collected, executed, path)


def test_default_conditional_skips_are_retained_with_exact_case_identity(tmp_path):
    nodes = ["tests/test_owned.py::TestOwned::test_one[a::b]"]
    path = tmp_path / "results.xml"
    junit(path, nodes, "skipped")
    assert runner.verify_cases(nodes, nodes, path) == {"total": 1, "passed": 0, "skipped": 1, "failed": 0, "errors": 0}


def test_default_environment_clears_selection_and_real_database_optins_without_losing_sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k selected")
    monkeypatch.setenv("OPENNAVICAT_TEST_POSTGRES_PORT", "generated-do-not-contact")
    monkeypatch.setenv("OPENNAVICAT_LIBPQ_DIR", "generated-sdk")
    env = runner.child_environment(tmp_path)
    assert "PYTEST_ADDOPTS" not in env and "OPENNAVICAT_TEST_POSTGRES_PORT" not in env
    assert env["OPENNAVICAT_LIBPQ_DIR"] == "generated-sdk"
    assert env["OPENNAVICAT_DATA_DIR"] == str(tmp_path / "store")
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["PYTEST_PLUGINS"] == "pytestqt.plugin"


def test_default_snapshot_includes_fixture_json_and_javascript_and_rejects_untracked_tests(tmp_path, monkeypatch):
    paths = ["tests/oracle_images.json", "opennavicat/js_engine/bootstrap.js", "native/quickjs/runner.c"]
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("generated-owned")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda args, **kw:
        b"" if "--others" in args else "\0".join(paths).encode())
    assert set(runner.source_snapshot()) == set(paths)
    monkeypatch.setattr(runner.subprocess, "check_output", lambda args, **kw: b"tests/test_untracked.py\0")
    with pytest.raises(RuntimeError, match="Untracked"):
        runner.source_snapshot()


def test_explicit_qt_plugin_is_available_to_an_inherited_isolated_pytest_process(tmp_path):
    target = tmp_path / "test_generated_plugin.py"
    target.write_text("def test_generated(qtbot):\n    assert qtbot is not None\n", encoding="utf-8")
    env = runner.child_environment(tmp_path)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(target)], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@pytest.mark.parametrize("phase", ["collect", "run"])
def test_child_uses_full_pyproject_scope_and_records_same_session_collection(tmp_path, monkeypatch, phase):
    calls = []
    def fake_main(args, plugins):
        calls.extend(args)
        plugin, = plugins
        session = types.SimpleNamespace(items=[types.SimpleNamespace(nodeid="tests/test_owned.py::test_one")])
        plugin.pytest_collection_finish(session)
        plugin.pytest_sessionfinish(session, 0)
        return 0
    monkeypatch.setattr(pytest, "main", fake_main)
    assert runner.child_main(phase, tmp_path, tmp_path / "owned", "generated-run") == 0
    assert not any(arg == "-k" or arg == "-m" or arg.startswith("tests/") for arg in calls)
    assert ("--collect-only" in calls) is (phase == "collect")
    assert "error" in calls and "pytestqt.plugin" in calls and "addopts=" in calls
    assert (tmp_path / (phase + "-cases.json")).read_text().strip() == '[\n  "tests/test_owned.py::test_one"\n]'


def helper_input(tmp_path, monkeypatch):
    import hashlib
    relative = 'artifacts/js-engine/opennavicat-js.exe'
    source = tmp_path / 'repo' / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b'generated-original-test-helper')
    monkeypatch.setattr(runner, 'ROOT', tmp_path / 'repo')
    content = source.read_bytes()
    hashes = {relative: {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}}
    out = tmp_path / 'result'
    out.mkdir()
    return relative, source, hashes, out


def test_frozen_helper_retains_test_bytes_after_later_package_rebuild(tmp_path, monkeypatch):
    relative, source, hashes, out = helper_input(tmp_path, monkeypatch)
    frozen = runner.freeze_helper_inputs(out, hashes)
    source.write_bytes(b'generated-later-package-helper')
    assert runner.verify_frozen_helper_inputs(out, hashes, frozen)
    assert (out / frozen[relative]['stored_path']).read_bytes() == b'generated-original-test-helper'
    with pytest.raises(RuntimeError, match='changed'):
        runner.freeze_helper_inputs(out, hashes)


@pytest.mark.parametrize('mutation', ['bytes', 'index', 'inventory', 'address', 'symlink'])
def test_retained_helper_tampering_is_rejected(tmp_path, monkeypatch, mutation):
    import json
    relative, source, hashes, out = helper_input(tmp_path, monkeypatch)
    frozen = runner.freeze_helper_inputs(out, hashes)
    target = out / frozen[relative]['stored_path']
    if mutation == 'bytes':
        target.write_bytes(b'generated-tampered')
    elif mutation == 'index':
        (out / 'helper-inputs.json').write_text('{}')
    elif mutation == 'inventory':
        hashes = {}
    elif mutation == 'address':
        frozen[relative]['stored_path'] = '../external'
        (out / 'helper-inputs.json').write_text(json.dumps(frozen))
    else:
        target.unlink()
        target.symlink_to(source)
    with pytest.raises(RuntimeError, match='Retained'):
        runner.verify_frozen_helper_inputs(out, hashes, frozen)


def test_freezing_helper_never_overwrites_an_existing_snapshot(tmp_path, monkeypatch):
    relative, source, hashes, out = helper_input(tmp_path, monkeypatch)
    frozen = runner.freeze_helper_inputs(out, hashes)
    target = out / frozen[relative]['stored_path']
    with pytest.raises(FileExistsError):
        runner.freeze_helper_inputs(out, hashes)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize('relative', ['/external/helper', '../helper', 'artifacts/other/helper'])
def test_helper_snapshot_address_cannot_escape_expected_inputs(tmp_path, monkeypatch, relative):
    with pytest.raises(RuntimeError, match='address'):
        runner.freeze_helper_inputs(tmp_path, {relative: {'bytes': 1, 'sha256': 'generated'}})
