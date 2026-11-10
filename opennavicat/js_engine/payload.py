"""Explicit source/frozen helper lookup and reviewable PyInstaller inputs.

No compilation, downloads, PATH lookup, environment override or SDK fallback
occurs when a user runs a query. Frozen bundles must contain their own payload.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from ..database import DatabaseError
from .runtime import NativeJavaScript


def helper_name():
    return 'opennavicat-js.exe' if os.name == 'nt' else 'opennavicat-js'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_helper_payload(helper, directory):
    """Stage only verified runtime assets; callers choose independent output."""
    engine = NativeJavaScript(helper)
    if engine.helper.name != helper_name():
        raise DatabaseError('The helper payload requires its standard native executable name')
    directory = Path(directory).resolve()
    if directory == engine.helper.parent:
        raise DatabaseError('Stage the helper into a separate payload directory')
    directory.mkdir(parents=True, exist_ok=True)
    assets = {}
    for name in (helper_name(), 'build-receipt.json', 'LICENSE.quickjs-ng'):
        source = engine.helper.with_name(name)
        if not source.is_file():
            raise DatabaseError('The helper payload is missing its build receipt or license')
        target = directory / name
        shutil.copy2(source, target)
        assets[name] = _sha(target)
    manifest = {'format': 'opennavicat-js-payload-v1', 'assets': assets,
        'bootstrap_sha256': _sha(Path(__file__).with_name('bootstrap.js'))}
    (directory / 'payload-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return directory


def locate_helper(*, source_root=None):
    """Resolve the controlled helper; frozen applications never use source paths."""
    frozen = bool(getattr(sys, 'frozen', False))
    try:
        if frozen:
            root = Path(sys._MEIPASS).resolve(strict=True)
            directory = root / 'native' / 'js-engine'
            helper = (directory / helper_name()).resolve(strict=True)
            if not helper.is_relative_to(root):
                raise DatabaseError('The bundled JavaScript helper points outside the application')
            manifest_path = (directory / 'payload-manifest.json').resolve(strict=True)
            if not manifest_path.is_relative_to(root):
                raise DatabaseError('The bundled JavaScript manifest points outside the application')
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            expected = {helper_name(), 'build-receipt.json', 'LICENSE.quickjs-ng'}
            if manifest.get('format') != 'opennavicat-js-payload-v1' or set(manifest.get('assets', {})) != expected:
                raise DatabaseError('Invalid bundled JavaScript payload manifest')
            for name, digest in manifest['assets'].items():
                asset = (directory / name).resolve(strict=True)
                if not asset.is_relative_to(root) or _sha(asset) != digest:
                    raise DatabaseError('The bundled JavaScript payload does not match its manifest')
            if manifest.get('bootstrap_sha256') != _sha(Path(__file__).with_name('bootstrap.js')):
                raise DatabaseError('The bundled JavaScript bootstrap does not match its helper payload')
        else:
            root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[2]
            helper = root / 'artifacts' / 'js-engine' / helper_name()
        # Includes official revision/archive SHA, helper bytes, OS and machine.
        return NativeJavaScript(helper).helper
    except DatabaseError:
        raise
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        message = ('The application is missing its bundled native JavaScript helper'
            if frozen else 'Build the pinned native JavaScript helper with scripts/build-quickjs.py before running JavaScript')
        raise DatabaseError(message) from exc


def load_engine():
    return NativeJavaScript(locate_helper())
