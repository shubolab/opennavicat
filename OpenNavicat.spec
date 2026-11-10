# -*- mode: python ; coding: utf-8 -*-
"""Native, directory-based builds retain Qt's dynamically linked libraries."""
from pathlib import Path
import sys
import tomllib
import os
import json
import subprocess
from importlib.metadata import distributions, PackageNotFoundError

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules, copy_metadata
from PySide6.QtCore import QLibraryInfo

project = Path(SPECPATH)
sys.path.insert(0, str(project))
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
metadata += copy_metadata("python-tds") + copy_metadata("redis") + copy_metadata("pyOpenSSL") + copy_metadata("certifi")
metadata += copy_metadata("pymongo") + copy_metadata("dnspython")
hiddenimports += ["opennavicat.sqlserver_session", "opennavicat.redis_session", "pytds", "redis", "OpenSSL", "certifi"]
hiddenimports += ["opennavicat.mongodb_session", "pymongo", "pymongo._cmessage", "bson", "bson._cbson"]
if binary_metadata:
    hiddenimports += ["psycopg_binary.pq", "psycopg_binary._psycopg"]
else:
    # Windows ARM64 has no psycopg-binary wheel. Bundle a native libpq build
    # alongside its DLL dependencies and use psycopg's ctypes implementation.
    libpq_directory = Path(os.environ.get("OPENNAVICAT_LIBPQ_DIR", ""))
    if sys.platform != "win32" or not (libpq_directory / "libpq.dll").is_file():
        raise RuntimeError("A native libpq.dll build is required through OPENNAVICAT_LIBPQ_DIR")
    psycopg_binaries += [(str(path), ".") for path in libpq_directory.glob("*.dll")]
    provider_directory = Path(os.environ.get("OPENSSL_MODULES", ""))
    if not (provider_directory / "legacy.dll").is_file():
        raise RuntimeError("The native OpenSSL provider directory is required through OPENSSL_MODULES")
    psycopg_binaries += [(str(path), "ossl-modules") for path in provider_directory.glob("*.dll")]
    hiddenimports += ["psycopg.pq.pq_ctypes"]
# Query the OpenSSL linked by cryptography, not whichever openssl happens to be
# first on PATH. Run in a child process so native libraries loaded for inspection
# cannot affect the bundler's own process. Use the same directory as the official
# PyInstaller OpenSSL runtime hook on every platform.
provider_report_path = project / "artifacts/openssl-providers-build.json"
subprocess.run([sys.executable, str(project / "scripts/check-openssl-providers.py"),
                "--probe", "--output", str(provider_report_path)], check=True)
provider_report = json.loads(provider_report_path.read_text("utf-8"))
psycopg_binaries += [(entry["source"], entry["destination"]) for entry in provider_report["providers"]]
psycopg_binaries = list(dict.fromkeys(psycopg_binaries))
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
    datas=metadata + license_files + qt_translations + [(str(project/'opennavicat/js_engine/bootstrap.js'), 'opennavicat/js_engine'),
                                    (str(project/'native/quickjs/LICENSE.quickjs-ng'), 'licenses/third-party/QuickJS-NG'),
                                    (str(project / "LICENSE"), "licenses"),
                                    (str(project / "THIRD_PARTY_NOTICES.md"), "licenses")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project / "scripts/runtime_libpq.py")],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.QtQuick", "PySide6.QtQml", "tkinter", "pytest"],
    noarchive=False,
)
if not binary_metadata:
    # CPython and the native SDK can ship the same DLL basename at different
    # OpenSSL patch/minor versions. Explicit SDK inputs must win normalization;
    # otherwise its provider can bind against an older Python copy and fail
    # with Windows ENTRY_POINT_NOT_FOUND. Keep each TOC dependency type intact.
    import runpy
    provider_tools = runpy.run_path(str(project / "scripts/check-openssl-providers.py"))
    a.binaries = provider_tools["prefer_sdk_openssl"](a.binaries, libpq_directory)
elif sys.platform == "darwin" and provider_report["libraries"]:
    # macOS also has same-basename collisions: Psycopg's bundled libcrypto.3
    # can replace the newer Homebrew build linked by cryptography and its
    # provider. Preserve that verified pair rather than mixing OpenSSL minors.
    import runpy
    provider_tools = runpy.run_path(str(project / "scripts/check-openssl-providers.py"))
    preferred = {}
    for library in provider_report["libraries"]:
        crypto = Path(library["resolved_library"])
        preferred[crypto.name] = str(crypto)
        ssl = crypto.parent / "libssl.3.dylib"
        if not ssl.is_file():
            raise RuntimeError(f"Missing matching libssl for provider-linked {crypto}")
        preferred[ssl.name] = str(ssl)
    a.binaries = provider_tools["prefer_openssl_sources"](a.binaries, preferred)
# Process the native helper once, then bind the final bytes in its receipt.
# Append DATA after Analysis classification so COLLECT/BUNDLE cannot rewrite it
# a second time. Executable mode is preserved; all payload images are still
# independently scanned and the actual frozen helper is executed by smoke.
from PyInstaller.building.utils import process_collected_binary
from opennavicat.js_engine.payload import helper_name, stage_helper_payload
import platform
source_helper = project/'artifacts/js-engine'/helper_name()
processed_helper = process_collected_binary(str(source_helper), 'native/js-engine/'+helper_name(),
    target_arch=platform.machine() if sys.platform=='darwin' else None, strict_arch_validation=True)
js_payload = stage_helper_payload(source_helper, project/'artifacts/js-engine/processed-payload', processed_helper=processed_helper)
for asset in (helper_name(), 'build-receipt.json', 'payload-manifest.json', 'LICENSE.quickjs-ng'):
    a.datas.append(('native/js-engine/'+asset, str(js_payload/asset), 'DATA'))
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
