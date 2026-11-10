"""OpenNavicat native database workbench."""
import os
from pathlib import Path
import sys

__version__ = "0.3.0"

# Source builds on Windows ARM64 use native libpq and OpenSSL from an explicit
# directory. Frozen builds register only their bundled directory in a runtime
# hook, so a developer's vcpkg installation cannot mask a broken distribution.
if sys.platform == "win32" and not getattr(sys, "frozen", False):
    _native_directory = os.environ.get("OPENNAVICAT_LIBPQ_DIR")
    if _native_directory:
        _native_dll_directory = os.add_dll_directory(str(Path(_native_directory).resolve()))
