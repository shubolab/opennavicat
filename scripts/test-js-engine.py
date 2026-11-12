#!/usr/bin/env python3
"""Explicit zero-skip native VM / fresh-owned MongoDB release qualification.

The native installer workflow executes these declared scopes. Evidence contains native
metadata, JUnit and redacted own-fixture receipts, never config/keys/URIs.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
VM=("tests/test_js_codec.py","tests/test_js_native.py","tests/test_js_gate.py",
    "tests/test_js_presentation.py","tests/test_js_payload.py")
PROTOCOL=(*VM,"tests/test_js_engine.py","tests/test_js_host_reference.py","tests/test_js_index_helpers.py")
QT=("tests/test_mongo_native_query_ui.py",)
PROTOCOL_FIXTURES=frozenset({'test_js_engine','test_js_host_reference','test_js_index_helpers'})
EXPECTED_MONGODB_IMAGE="mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
EXPECTED_MONGODB_VERSION="8.0.34"
QT_PROTOCOL_FIXTURES=frozenset({'test_mongo_native_query_ui','test_js_window'})
COLLECT="""import json,sys,pytest
from pathlib import Path
class Capture:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([v.nodeid for v in session.items]),encoding='utf-8')
raise SystemExit(pytest.main(['--collect-only','-q','-W','error','-o','addopts=',*sys.argv[2:]],plugins=[Capture()]))
"""


def check_junit(path,required):
    expected={(Path(value.split('::',1)[0]).stem,value.split('::',1)[1])for value in required}
    cases=list(ET.parse(path).getroot().iter('testcase'))
    actual={(value.get('classname','').rsplit('.',1)[-1],value.get('name',''))for value in cases}
    if not expected or len(cases)!=len(expected)or actual!=expected:
        raise RuntimeError('Native engine qualification did not execute the complete collected scope')
    if any(value.find(kind)is not None for value in cases for kind in ('skipped','error','failure')):
        raise RuntimeError('Native engine qualification requires zero skipped/failed/error cases')
    return dict(passed=len(cases),failed=0,errors=0,skipped=0)


def protocol_module():
    spec=importlib.util.spec_from_file_location('owned_mongodb_protocol_runner',ROOT/'scripts/test-mongodb-protocol.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def source_environment():
    # Preserve native DLL bootstrap sitecustomize before pytest/hashlib runs.
    pythonpath=os.pathsep.join(value for value in (str(ROOT),os.environ.get('PYTHONPATH',''))if value)
    environment=dict(os.environ,PYTHONPATH=pythonpath,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    environment.pop('PYTEST_ADDOPTS',None)
    return environment


def check_fixtures(directory,run_id,required):
    fixtures=[json.loads(path.read_text())for path in Path(directory).glob('fixture-*.json')]
    fixtures=[value for value in fixtures if value.get('run_id')==run_id]
    if (len(fixtures)!=len(required)or {value.get('module')for value in fixtures}!=set(required)or
        any(value.get(field)is not True for value in fixtures for field in ('cleanup_removed','owner_token_verified',
            'port_loopback_only','native_docker_architecture_matches','external_auth_readiness'))or
        any(value.get('image')!=EXPECTED_MONGODB_IMAGE or value.get('server_version')!=EXPECTED_MONGODB_VERSION for value in fixtures)):
        raise RuntimeError('Native engine owned MongoDB readiness/cleanup not proven')
    return fixtures


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('vm','protocol','qt','qt-protocol'),required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--cleanup-interrupted',action='store_true',help='Verify and remove only this evidence run\'s exact receipt-backed MongoDB fixtures')
    args=parser.parse_args(argv)
    directory=args.evidence.resolve();directory.mkdir(parents=True,exist_ok=True)
    if args.cleanup_interrupted:
        receipt={'verified':False}
        try:
            path=directory/'summary.json'
            if path.exists():
                previous=json.loads(path.read_text())
                if previous.get('mode')not in {'protocol','qt-protocol'}:raise RuntimeError('Cleanup requires an exact protocol run')
                receipt.update(run_id=previous['run_id'],removed=protocol_module().cleanup_interrupted_fixtures(directory/'fixtures',previous['run_id']))
            else:receipt['no_protocol_run_started']=True
            receipt['verified']=True
            return 0
        except Exception as error:
            receipt['error_type']=type(error).__name__
            return 1
        finally:(directory/'cleanup-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    is_qt=args.mode in {'qt','qt-protocol'}
    is_protocol=args.mode in {'protocol','qt-protocol'}
    scope=(*QT,'tests/test_js_window.py') if args.mode=='qt-protocol' else QT if is_qt else VM if args.mode=='vm'else PROTOCOL
    test_arguments=[*scope]
    if is_qt:test_arguments=['-p','pytestqt.plugin',*test_arguments]
    if args.mode=='qt':test_arguments += ['-k','not fresh_mongo']
    run_id=uuid.uuid4().hex
    summary=dict(verified=False,mode=args.mode,scope=scope,run_id=run_id,reference_endpoint_used=False)
    summary['execution_scope']={
        'vm':'native-helper-typed-ipc-without-database-capability',
        'protocol':'native-helper-authoritative-fresh-mongodb',
        'qt':'real-qt-component-contracts-and-native-helper-vm-only-no-database-capability',
        'qt-protocol':'real-qt-component-native-helper-and-fresh-mongodb-production-chain',
    }[args.mode]
    # Preserve the source build's native DLL bootstrap sitecustomize (notably
    # Windows ARM64), which must run before pytest/hashlib loads CPython's SSL.
    # Frozen/installed smoke separately strips these developer/SDK paths.
    environment=source_environment()
    environment.pop('OPENNAVICAT_TEST_MONGODB_ENDPOINT',None)
    if is_qt:environment['QT_QPA_PLATFORM']='offscreen'
    cleanup_verified=not is_protocol
    if is_protocol:
        environment.update(OPENNAVICAT_TEST_MONGODB='1',OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID=run_id,
            OPENNAVICAT_MONGODB_PROTOCOL_REPORT_DIR=str(directory/'fixtures'))
    report=directory/'pytest-results.xml';required=directory/'required-cases.json'
    report.unlink(missing_ok=True);required.unlink(missing_ok=True)
    # Persist this run identity before a child can create any resources.
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    def interrupt(number,frame):raise KeyboardInterrupt
    for number in (signal.SIGINT,signal.SIGTERM):signal.signal(number,interrupt)
    process=None
    try:
        with (directory/'collection.log').open('w',encoding='utf-8')as log:
            subprocess.run([sys.executable,'-c',COLLECT,str(required),*test_arguments],cwd=ROOT,env=environment,
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        with (directory/'pytest.log').open('w',encoding='utf-8')as log:
            process=subprocess.Popen([sys.executable,'-m','pytest','-q','-W','error','-o','addopts=',*test_arguments,'--junitxml='+str(report)],
                cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,start_new_session=os.name=='posix')
            status=process.wait(timeout=180)
        if status:raise RuntimeError('Native engine pytest process failed')
        summary.update(check_junit(report,json.loads(required.read_text())))
        if is_protocol:
            expected_modules=QT_PROTOCOL_FIXTURES if is_qt else PROTOCOL_FIXTURES
            fixtures=check_fixtures(directory/'fixtures',run_id,expected_modules)
            summary['fixtures']=fixtures;cleanup_verified=True
        summary['verified']=True
        print(json.dumps({key:summary[key]for key in ('verified','mode','passed','skipped')}))
        return 0
    except (Exception,KeyboardInterrupt)as error:
        summary['error_type']=type(error).__name__
        print('Native JavaScript qualification failed; inspect its bounded evidence.',file=sys.stderr)
        return 1
    finally:
        if process is not None and process.poll()is None:
            if os.name=='posix':os.killpg(process.pid,signal.SIGINT)
            else:process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if not cleanup_verified:
            try:summary['emergency_cleanup']=protocol_module().cleanup_interrupted_fixtures(directory/'fixtures',run_id)
            except Exception as error:summary['emergency_cleanup_error_type']=type(error).__name__
        (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')


if __name__=='__main__':raise SystemExit(main())
