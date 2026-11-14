"""Complete-default collection cannot be filtered or substituted by a receipt."""
import importlib.util
from pathlib import Path
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
