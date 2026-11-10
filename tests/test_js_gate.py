"""Qualification cannot turn missing or skipped native cases into acceptance."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest


def runner():
    path=Path(__file__).resolve().parents[1]/'scripts/test-js-engine.py'
    spec=importlib.util.spec_from_file_location('js_qualification',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('case',['','<skipped/>','<failure/>','<error/>'])
def test_junit_scope_is_complete_executed_and_zero_skip(tmp_path,case):
    path=tmp_path/'report.xml'
    path.write_text('<testsuite><testcase classname="tests.test_js_native" name="test_example">'+case+'</testcase></testsuite>')
    required=['tests/test_js_native.py::test_example']
    if case:
        with pytest.raises(RuntimeError):runner().check_junit(path,required)
    else:assert runner().check_junit(path,required)['passed']==1
    with pytest.raises(RuntimeError):runner().check_junit(path,required+['tests/test_js_native.py::test_missing'])
