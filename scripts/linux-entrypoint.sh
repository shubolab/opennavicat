#!/bin/sh
set -eu

# /src is a readonly host mount. All installs and build mutations are confined
# to this disposable container; /out is the only writable host mount.
tar -C /src \
  --exclude='./.venv' --exclude='./.git' --exclude='./build' --exclude='./dist' \
  --exclude='./artifacts' --exclude='./docs/evidence' --exclude='./.pytest_cache' \
  --exclude='__pycache__' --exclude='*.egg-info' --exclude='*.pyc' \
  -cf - . | tar -C /work -xf -

python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, platform
files = {}
for path in sorted(Path('/work').rglob('*')):
    if path.is_file():
        files[str(path.relative_to('/work'))] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {'captured_at': datetime.now(timezone.utc).isoformat(),
            'system': platform.platform(), 'architecture': platform.machine(),
            'python': platform.python_version(), 'os_release': Path('/etc/os-release').read_text(),
            'files': files}
Path('/out/source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('SOURCE SNAPSHOT CAPTURED:', manifest['captured_at'], manifest['architecture'], flush=True)
PY

python -m pip install -e '.[dev]'
python -m pytest -q --junitxml=/out/pytest-results.xml
python -m opennavicat --theme dark --smoke-test --screenshot /out/source-workbench.png
python scripts/build.py --clean --archive
dist/OpenNavicat/OpenNavicat --theme dark --smoke-test --screenshot /out/packaged-workbench.png
cp /work/artifacts/*.zip /out/

python - <<'PY'
from pathlib import Path
import hashlib, json, os, platform, stat, subprocess, zipfile
from PySide6 import __version__ as qt_version
from opennavicat import __version__
root = Path('/work/dist/OpenNavicat')
files = list(root.rglob('*'))
archive = Path('/out') / f'OpenNavicat-{__version__}-linux-{platform.machine().lower()}.zip'
translations = [p for p in files if p.name == 'qtbase_zh_CN.qm']
assert translations, 'Packaged Chinese Qt translation is missing'
with zipfile.ZipFile(archive) as z:
    mode = z.getinfo('OpenNavicat/OpenNavicat').external_attr >> 16
    assert mode & stat.S_IXUSR, 'Archive dropped executable mode'
metrics = {
    'platform': platform.platform(), 'architecture': platform.machine(),
    'python': platform.python_version(), 'qt': qt_version, 'version': __version__,
    'app_regular_bytes': sum(p.stat().st_size for p in files if p.is_file() and not p.is_symlink()),
    'archive': archive.name, 'archive_bytes': archive.stat().st_size,
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'archive_executable_mode': oct(mode),
    'webengine_paths': [str(p.relative_to(root)) for p in files if 'webengine' in p.name.lower()],
    'qt_chinese_translations': [str(p.relative_to(root)) for p in translations],
    'system_chinese_font': subprocess.check_output(['fc-match', '--format', '%{family}', ':lang=zh-cn'], text=True),
    'bundled_font_files': [str(p.relative_to(root)) for p in files if p.suffix.lower() in {'.ttf', '.ttc', '.otf'}],
    'source_smoke': 'passed', 'packaged_smoke': 'passed',
}
assert not metrics['webengine_paths'], 'Unexpected WebEngine runtime in native build'
Path('/out/build-metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
print(json.dumps(metrics, indent=2))
PY
