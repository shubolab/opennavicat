"""Expose bundled Windows libpq and native OpenSSL providers on every OS."""
import os
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    for name in ("ossl-modules", "openssl-modules"):
        providers = Path(sys._MEIPASS) / name
        if any((providers / ("legacy" + suffix)).is_file() for suffix in (".dll", ".dylib", ".so")):
            os.environ["OPENSSL_MODULES"] = str(providers)
            break

if sys.platform == "win32" and getattr(sys, "frozen", False):
    directory = Path(sys._MEIPASS)
    if (directory / "libpq.dll").is_file():
        # Run before _hashlib/_ssl or third-party hooks can select the same-name
        # CPython DLL. SDK binaries are explicitly preferred in the build TOC.
        from opennavicat._native_runtime import prepare_native_openssl
        prepare_native_openssl(directory)
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
