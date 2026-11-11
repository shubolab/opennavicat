#!/usr/bin/env python3
"""Qualify MongoDB collection, view and MainWindow UI paths with fresh MongoDB.

This standalone checkpoint does not widen the released database-security job's
scope. Missing prerequisites/skips are failures, not protocol acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import bson
import pymongo


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    'tests/test_mongodb_collections.py',
    'tests/test_mongodb_timeseries.py',
    'tests/test_mongodb_collection_copy.py',
    'tests/test_mongodb_collection_maintenance.py',
    'tests/test_mongodb_views.py',
    'tests/test_mongodb_session.py::test_canonical_array_editor_preserves_bson_types_and_rejects_duplicate_fields',
    'tests/test_mongo_collection_designer_ui.py',
    'tests/test_mongo_timeseries_designer_ui.py',
    'tests/test_mongo_maintenance_ui.py',
    'tests/test_mongo_inspector_ui.py',
    'tests/test_mongo_view_designer_ui.py',
    'tests/test_schema_object_designer_ui.py',
    'tests/test_connection_lifecycle.py',
    'tests/test_mongo_window.py::test_real_window_mongo_tree_statistics_typed_page_and_sqlite_switch',
    'tests/test_mongo_window.py::test_real_collection_designer_creates_and_collmods_with_worker_owned_io',
    'tests/test_mongo_window.py::test_real_collection_designer_lists_indexes_and_opens_index_designer',
    'tests/test_mongo_window.py::test_real_window_collection_storage_creates_reopens_and_sheet_cancel_preserves_draft',
    'tests/test_mongo_window.py::test_real_window_collection_storage_server_error_keeps_text_and_can_retry',
    'tests/test_mongo_window.py::test_real_window_stop_inflight_collection_uses_cancel_pool_and_preserves_draft',
    'tests/test_mongo_window.py::test_real_window_clear_and_drop_collection_require_permanent_confirmation',
    'tests/test_mongo_window.py::test_real_window_rename_collection_preserves_data_indexes_and_uuid',
    'tests/test_mongo_window.py::test_real_window_timeseries_designer_creates_and_updates_bucketing_and_expiry',
    'tests/test_mongo_window.py::test_real_window_copy_collection_data_and_structure_with_staging',
    'tests/test_mongo_window.py::test_real_window_collection_maintenance_validate_reindex_and_compact',
    'tests/test_mongo_window.py::test_real_window_mongo_view_designer_creates_updates_and_routes_object_page',
    'tests/test_mongo_window.py::test_real_window_mongo_view_designer_refuses_missing_target_and_stale_definition',
    'tests/test_mongo_window.py::test_real_window_mongo_view_designer_creates_collated_view_and_preserves_collation',
    'tests/test_mongo_window.py::test_real_window_delete_mongo_view_requires_permanent_confirmation',
]
EXPECTED_FIXTURE_MODULES = {'test_mongodb_collections', 'test_mongodb_timeseries', 'test_mongodb_collection_copy', 'test_mongodb_collection_maintenance', 'test_mongodb_views', 'test_mongo_window'}
COLLECT = '''import json,sys,pytest
from pathlib import Path
class Collect:
 def pytest_collection_finish(self,session):
  Path(sys.argv[1]).write_text(json.dumps([item.nodeid for item in session.items],indent=2)+chr(10))
raise SystemExit(pytest.main([*sys.argv[2:],'--collect-only','-q'],plugins=[Collect()]))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    directory = args.evidence.resolve()
    if directory.exists(): raise RuntimeError('Use a new evidence directory for each qualification')
    directory.mkdir(parents=True)
    run_id = uuid.uuid4().hex
    environment = dict(os.environ, PYTHONPATH=str(ROOT), OPENNAVICAT_TEST_MONGODB='1', QT_QPA_PLATFORM='offscreen',
        OPENNAVICAT_MONGODB_PROTOCOL_RUN_ID=run_id,
        OPENNAVICAT_MONGODB_PROTOCOL_REPORT_DIR=str(directory / 'fixtures'))
    environment.pop('OPENNAVICAT_TEST_MONGODB_ENDPOINT', None)
    environment.pop('PYTEST_ADDOPTS', None)
    spec = importlib.util.spec_from_file_location('owned_mongodb_fixture_cleanup', ROOT / 'scripts/test-mongodb-protocol.py')
    cleanup = importlib.util.module_from_spec(spec); spec.loader.exec_module(cleanup)
    summary = {'verified': False, 'scope': TARGETS, 'run_id': run_id,
        'reference_endpoint_used': False, 'execution_scope': 'mongodb-collection-timeseries-copy-maintenance-view-inspector-backend-and-mainwindow', 'emergency_removed': [],
        'runtime': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
            'pymongo': pymongo.version, 'bson_c': bson.has_c()},
        'base_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in
            ('opennavicat/mongodb_collections.py', 'opennavicat/mongodb_timeseries.py',
             'opennavicat/mongodb_collection_copy.py', 'opennavicat/mongodb_collection_maintenance.py',
             'opennavicat/mongodb_views.py', 'opennavicat/mongodb_session.py', 'opennavicat/mongodb_collection_literals.py',
             'opennavicat/ui/mongo_collection_designer.py', 'opennavicat/ui/mongo_view_designer.py',
             'opennavicat/ui/mongo_timeseries_designer.py', 'opennavicat/ui/mongo_maintenance.py',
             'opennavicat/ui/window.py', 'opennavicat/ui/schema_object_designer.py',
             'opennavicat/ui/workspace.py', 'opennavicat/js_engine/server_errors.py',
             'tests/test_mongodb_collections.py', 'tests/test_mongodb_timeseries.py',
             'tests/test_mongodb_collection_copy.py', 'tests/test_mongodb_collection_maintenance.py',
             'tests/test_mongodb_views.py',
             'tests/test_mongodb_session.py', 'tests/test_mongo_collection_designer_ui.py',
             'tests/test_mongo_timeseries_designer_ui.py', 'tests/test_mongo_maintenance_ui.py',
             'tests/test_mongo_inspector_ui.py', 'tests/test_mongo_view_designer_ui.py',
             'tests/test_mongo_window.py', 'tests/test_schema_object_designer_ui.py', 'tests/test_connection_lifecycle.py',
             '.github/workflows/build.yml', 'scripts/test-mongodb-collections.py')}}
    process = None
    try:
        if summary['runtime']['bson_c'] is not True:
            raise RuntimeError('Native protocol qualification requires the actual BSON C extension')
        with (directory / 'collection.log').open('w') as output:
            subprocess.run([sys.executable, '-c', COLLECT, str(directory / 'required-cases.json'), *TARGETS],
                cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=60)
        expected = json.loads((directory / 'required-cases.json').read_text())
        if (not expected or any(not any(node == target or node.startswith(target + '::') for target in TARGETS) for node in expected)
                or len(set(expected)) != len(expected)):
            raise RuntimeError('Collection manifest did not identify the exact declared scope')
        with (directory / 'pytest.log').open('w') as output:
            process = subprocess.Popen([sys.executable, '-m', 'pytest', '-q', '-W', 'error', *TARGETS,
                '--junitxml=' + str(directory / 'results.xml')], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT)
            try:
                status = process.wait(timeout=args.timeout)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
        cases = ET.parse(directory / 'results.xml').getroot().findall('.//testcase')
        names = [case.attrib.get('name') for case in cases]
        summary.update(passed=sum(not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for case in cases),
            failed=sum(case.find('failure') is not None for case in cases),
            errors=sum(case.find('error') is not None for case in cases), skipped=sum(case.find('skipped') is not None for case in cases))
        if status or len(cases) != len(expected) or set(names) != {node.split('::', 1)[1] for node in expected} or any(summary[key] for key in ('failed', 'errors', 'skipped')):
            raise RuntimeError('Ordinary-collection qualification did not pass the entire exact scope without skips')
        fixtures = [json.loads(path.read_text()) for path in (directory / 'fixtures').glob('fixture-*.json')]
        fixtures = [value for value in fixtures if value.get('run_id') == run_id]
        if ({fixture.get('module') for fixture in fixtures} != EXPECTED_FIXTURE_MODULES
                or len(fixtures) != len(EXPECTED_FIXTURE_MODULES)
                or any(fixture.get(key) is not True for fixture in fixtures for key in (
                    'cleanup_removed', 'owner_token_verified', 'port_loopback_only',
                    'native_docker_architecture_matches', 'external_auth_readiness'))):
            raise RuntimeError('Required exact-owned native fixture/cleanup proof is missing')
        summary['fixtures'] = fixtures
        summary['verified'] = True
    except BaseException as error:
        summary['error_type'] = type(error).__name__
        raise
    finally:
        try:
            summary['emergency_removed'] = cleanup.cleanup_interrupted_fixtures(directory / 'fixtures', run_id)
        except Exception as error:
            summary['verified'] = False; summary['cleanup_error_type'] = type(error).__name__
            raise
        finally:
            (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__': main()
