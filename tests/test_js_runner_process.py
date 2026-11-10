"""The actual release CLI must not accept an environment-filtered VM subset."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_actual_vm_release_cli_ignores_ambient_pytest_subset(tmp_path):
    root=Path(__file__).resolve().parents[1]
    helper=root/'artifacts/js-engine'/('opennavicat-js.exe' if os.name=='nt' else 'opennavicat-js')
    if not helper.is_file():
        pytest.skip('Build the native helper before the actual release CLI regression')
    environment=dict(os.environ,PYTEST_ADDOPTS='-k test_source_qualification_child_keeps_early_bootstrap_before_hashlib')
    target=tmp_path/'qualification'
    result=subprocess.run([sys.executable,str(root/'scripts/test-js-engine.py'),'--mode','vm','--evidence',str(target)],
        cwd=root,env=environment,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    summary=json.loads((target/'summary.json').read_text())
    required=json.loads((target/'required-cases.json').read_text())
    assert summary['verified'] is True and summary['skipped']==0 and summary['passed']==len(required)
    assert 'tests/test_js_native.py::test_native_bson_extension_is_loaded' in required
    assert 'tests/test_js_native.py::test_native_binary_pipe_all_bytes_and_frame_boundaries[266]' in required
