"""Run the actual platform bundle through an isolated end-to-end SQL query."""
from pathlib import Path
import os
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
if sys.platform == "darwin":
    executable = root / "dist/OpenNavicat.app/Contents/MacOS/OpenNavicat"
elif sys.platform == "win32":
    executable = root / "dist/OpenNavicat/OpenNavicat.exe"
else:
    executable = root / "dist/OpenNavicat/OpenNavicat"
environment = os.environ.copy()
environment.setdefault("QT_QPA_PLATFORM", "offscreen")
subprocess.run([str(executable), "--smoke-test", "--smoke-report", str(root / "artifacts/packaged-smoke.json"),
               "--screenshot", str(root / "artifacts/packaged-workbench.png")],
               cwd=root, env=environment, check=True, timeout=45)
import json
report = json.loads((root / "artifacts/packaged-smoke.json").read_text("utf-8"))
assert report["smoke"] == "passed" and report["rows"] == 6
assert report["runtime"]["libpq"] > 0
