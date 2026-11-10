"""Qualification cannot turn missing or skipped native cases into acceptance."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
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


def test_source_qualification_child_keeps_early_bootstrap_before_hashlib(tmp_path,monkeypatch):
    # A real owned Python startup hook models the CI's native DLL preload.
    # No SDK or DLL is replaced; Windows hosted CI validates its actual DLLs.
    (tmp_path/'sitecustomize.py').write_text('import os\nos.environ["OPENNAVICAT_OWN_BOOTSTRAP_PROBE"]="early"\n')
    monkeypatch.setenv('PYTHONPATH',str(tmp_path))
    environment=runner().source_environment()
    environment.pop('OPENNAVICAT_OWN_BOOTSTRAP_PROBE',None)
    result=subprocess.run([sys.executable,'-c','import hashlib,os;print(os.environ.get("OPENNAVICAT_OWN_BOOTSTRAP_PROBE","missing"))'],
        env=environment,cwd=tmp_path,capture_output=True,text=True,check=True,timeout=10)
    assert result.stdout.strip()=='early'
    assert str(tmp_path)in environment['PYTHONPATH'].split(os.pathsep)
