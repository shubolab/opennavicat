"""OpenNavicat native database workbench."""
import os
import sys

__version__ = "0.8.1"

# Source builds on Windows ARM64 use native libpq and OpenSSL from an explicit
# directory. Frozen builds register only their bundled directory in a runtime
# hook, so a developer's vcpkg installation cannot mask a broken distribution.
if sys.platform == "win32" and not getattr(sys, "frozen", False):
    _native_directory = os.environ.get("OPENNAVICAT_LIBPQ_DIR")
    if _native_directory:
        from ._native_runtime import prepare_native_openssl
        prepare_native_openssl(_native_directory)
