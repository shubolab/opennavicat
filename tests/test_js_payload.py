"""Frozen lookup may not silently borrow development payloads or external DLLs."""
import hashlib
import json
from pathlib import Path
import platform
import struct

import pytest

from opennavicat.database import DatabaseError
from opennavicat.js_engine import payload


def source_payload(tmp_path):
    # A synthetic byte/receipt fixture exercises lookup, never execution.
    directory = tmp_path / 'source' / 'artifacts' / 'js-engine'
    directory.mkdir(parents=True)
    helper = directory / payload.helper_name()
    architecture = {'aarch64':'arm64','arm64':'arm64','amd64':'amd64','x86_64':'amd64'}[platform.machine().lower()]
    data=bytearray(128)
    if platform.system()=='Darwin':
        data[:4]=b'\xcf\xfa\xed\xfe';struct.pack_into('<I',data,4,0x100000c if architecture=='arm64' else 0x1000007)
    elif platform.system()=='Windows':
        data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,64);data[64:68]=b'PE\0\0';struct.pack_into('<H',data,68,0xaa64 if architecture=='arm64'else 0x8664)
    else:
        data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,183 if architecture=='arm64'else 62)
    helper.write_bytes(data)  # Header contract only, never executed.
    receipt = {'source_revision': 'd7215b0ad3fc2c562e8317fd9bca058fd4863804',
        'archive_sha256': 'a49defe70cbcae9226d9fac025af116e87ebae2440dcff1ca3c80b79e1cdef0b',
        'operating_system': platform.system(), 'native_image': {'machine':
            {'aarch64': 'arm64', 'arm64': 'arm64', 'amd64': 'amd64', 'x86_64': 'amd64'}[platform.machine().lower()]},
        'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    helper.with_name('build-receipt.json').write_text(json.dumps(receipt))
    helper.with_name('LICENSE.quickjs-ng').write_text('Synthetic license fixture; no execution acceptance')
    return helper


def freeze(monkeypatch, root):
    monkeypatch.setattr(payload.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(payload.sys, '_MEIPASS', str(root), raising=False)


def test_frozen_lookup_requires_its_own_manifest_bootstrap_and_helper_bytes(tmp_path, monkeypatch):
    source = source_payload(tmp_path)
    root = tmp_path / 'bundle'
    staged = payload.stage_helper_payload(source, root / 'native/js-engine')
    freeze(monkeypatch, root)
    assert payload.locate_helper(source_root=tmp_path/'not-used') == staged/source.name
    (staged / source.name).write_bytes(b'tampered')
    with pytest.raises(DatabaseError, match='manifest'):
        payload.locate_helper()


def test_missing_frozen_helper_never_falls_back_to_available_source_or_env(tmp_path, monkeypatch):
    source = source_payload(tmp_path)
    root = tmp_path / 'empty-bundle'
    root.mkdir()
    freeze(monkeypatch, root)
    monkeypatch.setenv('OPENNAVICAT_TEST_JS_HELPER', str(source))
    monkeypatch.setenv('OPENNAVICAT_JS_HELPER', str(source))
    with pytest.raises(DatabaseError, match='bundled'):
        payload.locate_helper(source_root=source.parents[2])


def test_bootstrap_or_license_drift_rejects_frozen_payload(tmp_path, monkeypatch):
    source = source_payload(tmp_path)
    root = tmp_path / 'bundle'
    staged = payload.stage_helper_payload(source, root / 'native/js-engine')
    freeze(monkeypatch, root)
    manifest = staged / 'payload-manifest.json'
    value = json.loads(manifest.read_text())
    value['bootstrap_sha256'] = '0'*64
    manifest.write_text(json.dumps(value))
    with pytest.raises(DatabaseError, match='bootstrap'):
        payload.locate_helper()
    payload.stage_helper_payload(source, staged)
    (staged / 'LICENSE.quickjs-ng').unlink()
    with pytest.raises(DatabaseError, match='bundled'):
        payload.locate_helper()


def test_frozen_resolved_path_cannot_escape_to_a_development_helper(tmp_path, monkeypatch):
    source = source_payload(tmp_path)
    root = tmp_path / 'bundle'
    staged = payload.stage_helper_payload(source, root / 'native/js-engine')
    # Model a symlink/junction result portably without requiring the Windows
    # runner to have symlink privileges. This is a resolver contract test.
    original = Path.resolve
    def redirected(path, *args, **kwargs):
        return source if path == staged / source.name else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', redirected)
    freeze(monkeypatch, root)
    with pytest.raises(DatabaseError, match='outside'):
        payload.locate_helper()


def test_processed_payload_retains_source_hash_and_binds_final_bytes(tmp_path,monkeypatch):
    source=source_payload(tmp_path)
    original=json.loads(source.with_name('build-receipt.json').read_text())
    processed=tmp_path/'processed';processed.write_bytes(source.read_bytes()+b'owned simulated postprocessing')
    root=tmp_path/'bundle'
    staged=payload.stage_helper_payload(source,root/'native/js-engine',processed_helper=processed)
    receipt=json.loads((staged/'build-receipt.json').read_text())
    assert receipt['source_helper_sha256']==original['helper_sha256']
    assert receipt['helper_sha256']==hashlib.sha256(processed.read_bytes()).hexdigest()
    assert receipt['source_revision']==original['source_revision']
    freeze(monkeypatch,root)
    assert payload.locate_helper()==staged/source.name


def test_native_header_cannot_claim_the_receipts_architecture(tmp_path):
    source=source_payload(tmp_path)
    data=bytearray(source.read_bytes())
    # Keep the receipt/hash self-consistent but replace the actual header with
    # an unsupported CPU. Runtime must inspect bytes, not trust the assertion.
    if platform.system()=='Windows':struct.pack_into('<H',data,68,0x14c)
    elif platform.system()=='Darwin':struct.pack_into('<I',data,4,7)
    else:struct.pack_into('<H',data,18,3)
    source.write_bytes(data)
    path=source.with_name('build-receipt.json');receipt=json.loads(path.read_text())
    receipt['helper_sha256']=hashlib.sha256(data).hexdigest();path.write_text(json.dumps(receipt))
    with pytest.raises(DatabaseError,match='native'):
        payload.NativeJavaScript(source)
