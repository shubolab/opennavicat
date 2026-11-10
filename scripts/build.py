"""Build a native desktop distribution on the current operating system.

Run with the project's Python environment: python scripts/build.py
PyInstaller bundles for its host platform; run separately on each target OS.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="store_true", help="Also create a ZIP in artifacts/.")
    parser.add_argument("--clean", action="store_true", help="Clean PyInstaller's cache before building.")
    parser.add_argument('--distpath', type=Path, help='Independent build output directory')
    parser.add_argument('--workpath', type=Path, help='Independent PyInstaller working directory')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root/'scripts/build-quickjs.py'), '--allow-unqualified-platform'], cwd=root, check=True)
    dist = args.distpath.resolve() if args.distpath else root/'dist'
    work = args.workpath.resolve() if args.workpath else root/'build/pyinstaller'
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(dist),
               "--workpath", str(work)]
    if args.clean:
        command.append("--clean")
    command.append(str(root / "OpenNavicat.spec"))
    subprocess.run(command, cwd=root, check=True)
    target = dist / ("OpenNavicat.app" if sys.platform == "darwin" else "OpenNavicat")
    if not target.is_dir():
        raise RuntimeError(f"Expected build output was not created: {target}")
    print(f"Native desktop build: {target}")
    if args.archive:
        sys.path.insert(0, str(root))
        from opennavicat import __version__
        artifacts = root / "artifacts"
        artifacts.mkdir(exist_ok=True)
        name = f"OpenNavicat-{__version__}-{platform.system().lower()}-{platform.machine().lower()}"
        if sys.platform == "darwin":
            # Qt frameworks contain symlinks. ditto retains them and executable
            # modes rather than copying their targets repeatedly into the ZIP.
            archive = str(artifacts / f"{name}.zip")
            subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(target), archive], check=True)
        else:
            archive = shutil.make_archive(str(artifacts / name), "zip", root_dir=target.parent, base_dir=target.name)
        print(f"Distribution archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
