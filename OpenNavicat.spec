# -*- mode: python ; coding: utf-8 -*-
"""Native, directory-based builds retain Qt's dynamically linked libraries."""
from pathlib import Path
import sys
import tomllib
import os
from importlib.metadata import distributions, PackageNotFoundError

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules, copy_metadata
from PySide6.QtCore import QLibraryInfo

project = Path(SPECPATH)
project_version = tomllib.loads((project / "pyproject.toml").read_text("utf-8"))["project"]["version"]
qt_chinese = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"
if not qt_chinese.is_file():
    raise RuntimeError(f"Qt Chinese translation is missing from this build environment: {qt_chinese}")
qt_translations = [(str(qt_chinese), "PySide6/Qt/translations")]
# Importing psycopg_binary in isolation fails by design; declare its native
# modules instead of probing all submodules in a child interpreter.
try:
    binary_metadata = copy_metadata("psycopg-binary")
except PackageNotFoundError:
    binary_metadata = []
psycopg_binaries = collect_dynamic_libs("psycopg_binary") if binary_metadata else []
hiddenimports = collect_submodules("keyring.backends")
metadata = copy_metadata("keyring") + copy_metadata("psycopg") + binary_metadata
if binary_metadata:
    hiddenimports += ["psycopg_binary.pq", "psycopg_binary._psycopg"]
else:
    # Windows ARM64 has no psycopg-binary wheel. Bundle a native libpq build
    # alongside its DLL dependencies and use psycopg's ctypes implementation.
    libpq_directory = Path(os.environ.get("OPENNAVICAT_LIBPQ_DIR", ""))
    if sys.platform != "win32" or not (libpq_directory / "libpq.dll").is_file():
        raise RuntimeError("A native libpq.dll build is required through OPENNAVICAT_LIBPQ_DIR")
    psycopg_binaries += [(str(path), ".") for path in libpq_directory.glob("*.dll")]
    hiddenimports += ["psycopg.pq.pq_ctypes"]
license_files = [(str(path), "licenses") for path in (project / "licenses").glob("*.txt")]
if not binary_metadata:
    for notice in (libpq_directory.parent / "share").glob("*/copyright"):
        license_files.append((str(notice), f"licenses/third-party/vcpkg/{notice.parent.name}"))
for installed in distributions():
    for member in installed.files or []:
        parts = member.parts
        if not any(part.endswith(".dist-info") for part in parts) or "licenses" not in parts:
            continue
        source = Path(installed.locate_file(member))
        if source.is_file():
            relative = Path(*parts[parts.index("licenses") + 1:])
            destination = Path("licenses/third-party") / installed.metadata["Name"] / relative.parent
            license_files.append((str(source), str(destination)))

a = Analysis(
    [str(project / "scripts" / "desktop_entry.py")],
    pathex=[str(project)],
    binaries=psycopg_binaries,
    datas=metadata + license_files + qt_translations + [(str(project / "LICENSE"), "licenses"),
                                    (str(project / "THIRD_PARTY_NOTICES.md"), "licenses")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project / "scripts/runtime_libpq.py")],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.QtQuick", "PySide6.QtQml", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="OpenNavicat",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OpenNavicat")

if sys.platform == "darwin":
    app = BUNDLE(
        collection, name="OpenNavicat.app", icon=None,
        bundle_identifier="org.opennavicat.app",
        info_plist={
            "CFBundleName": "OpenNavicat",
            "CFBundleDisplayName": "OpenNavicat",
            "CFBundleShortVersionString": project_version,
            "CFBundleVersion": "2",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "OpenNavicat contributors — MIT License",
        },
    )
