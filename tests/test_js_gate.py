"""Qualification cannot turn missing or skipped native cases into acceptance."""
from __future__ import annotations
import importlib.util
import json
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


def test_source_qualification_cannot_inherit_pytest_selection(monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS','-k test_source_qualification_child_keeps_early_bootstrap_before_hashlib')
    environment=runner().source_environment()
    assert 'PYTEST_ADDOPTS' not in environment


def test_real_collector_overrides_ini_test_filters(tmp_path):
    import json
    (tmp_path/'pytest.ini').write_text('[pytest]\naddopts = -k only_selected\n')
    source=tmp_path/'test_owned_selection.py'
    source.write_text('def test_only_selected(): pass\ndef test_other_required(): pass\n')
    target=tmp_path/'collection.json'
    result=subprocess.run([sys.executable,'-c',runner().COLLECT,str(target),'-c',str(tmp_path/'pytest.ini'),str(source)],
        env=runner().source_environment(),cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert {value.split('::')[-1] for value in json.loads(target.read_text())}=={'test_only_selected','test_other_required'}


def test_protocol_explicitly_executes_index_helpers_and_requires_three_owned_modules():
    module=runner()
    assert 'tests/test_js_index_helpers.py' in module.PROTOCOL
    assert module.PROTOCOL_FIXTURES=={'test_js_engine','test_js_host_reference','test_js_index_helpers'}
    assert module.QT_PROTOCOL_FIXTURES=={'test_mongo_native_query_ui','test_js_window'}


@pytest.mark.parametrize('fault',[None,'missing','duplicate','extra','stale','cleanup','readiness','architecture'])
def test_fixture_gate_rejects_missing_helper_receipt_duplicates_and_incomplete_cleanup(tmp_path,fault):
    module=runner();run_id='synthetic-current-run'
    def receipt(name):
        return dict(run_id=run_id,module=name,cleanup_removed=True,owner_token_verified=True,
            port_loopback_only=True,native_docker_architecture_matches=True,external_auth_readiness=True)
    rows=[receipt(name)for name in sorted(module.PROTOCOL_FIXTURES)]
    if fault=='missing':rows=[row for row in rows if row['module']!='test_js_index_helpers']
    elif fault=='duplicate':rows.append(dict(rows[0]))
    elif fault=='extra':rows.append(receipt('unexpected-owned-module'))
    elif fault=='stale':rows[0]['run_id']='synthetic-older-run'
    elif fault=='cleanup':rows[0]['cleanup_removed']=False
    elif fault=='readiness':rows[0]['external_auth_readiness']=False
    elif fault=='architecture':rows[0]['native_docker_architecture_matches']=False
    for index,row in enumerate(rows):(tmp_path/f'fixture-{index}.json').write_text(json.dumps(row))
    if fault:
        with pytest.raises(RuntimeError,match='readiness/cleanup not proven'):
            module.check_fixtures(tmp_path,run_id,module.PROTOCOL_FIXTURES)
    else:
        # Stale evidence cannot stand in for a required module, but retaining a
        # previous run's explicitly separate receipt is harmless.
        old=receipt('ignored-old-module');old['run_id']='synthetic-older-run';old['cleanup_removed']=False
        (tmp_path/'fixture-old.json').write_text(json.dumps(old))
        assert len(module.check_fixtures(tmp_path,run_id,module.PROTOCOL_FIXTURES))==3
