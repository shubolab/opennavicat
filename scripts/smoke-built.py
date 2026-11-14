"""Run the actual platform bundle through an isolated end-to-end SQL query."""
from pathlib import Path
import argparse
import os
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
parser.add_argument("--output-dir", type=Path, default=root / "artifacts")
options = parser.parse_args()
distribution = options.dist_dir.resolve()
output = options.output_dir.resolve()
output.mkdir(parents=True, exist_ok=True)
if sys.platform == "darwin":
    executable = distribution / "OpenNavicat.app/Contents/MacOS/OpenNavicat"
elif sys.platform == "win32":
    executable = distribution / "OpenNavicat/OpenNavicat.exe"
else:
    executable = distribution / "OpenNavicat/OpenNavicat"
environment = os.environ.copy()
environment.setdefault("QT_QPA_PLATFORM", "offscreen")
environment.pop("PYTHONPATH", None)
environment.pop("OPENSSL_MODULES", None)
for variable in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_FALLBACK_FRAMEWORK_PATH"):
    environment.pop(variable, None)
native_build_directory = environment.pop("OPENNAVICAT_LIBPQ_DIR", "")
if native_build_directory:
    environment["PATH"] = os.pathsep.join(path for path in environment.get("PATH", "").split(os.pathsep)
        if os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(native_build_directory)))
subprocess.run([str(executable), "--smoke-test", "--smoke-report", str(output / "packaged-smoke.json"),
               "--screenshot", str(output / "packaged-workbench.png")],
               cwd=root, env=environment, check=True, timeout=45)
import json
report = json.loads((output / "packaged-smoke.json").read_text("utf-8"))
assert report["smoke"] == "passed" and report["rows"] == 6
assert report["runtime"]["libpq"] >= 170000, "Secure PostgreSQL cancellation requires libpq 17+"
assert report["runtime"]["crypto_legacy_probe"] is True
sys.path.insert(0, str(root))
from opennavicat.sqlite_cipher_probe import validate_sqlite_cipher_runtime
validate_sqlite_cipher_runtime(report["runtime"])
for field in ('native_js_helper_ready','native_js_bigint_bson_probe','native_js_cancel_probe',
              'native_js_child_reaped','native_js_no_console','native_js_bundled'):
    assert report['runtime'].get(field) is True, f'Missing compiled native JavaScript execution proof: {field}'
