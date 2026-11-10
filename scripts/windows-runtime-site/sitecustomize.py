"""Build-process DLL search path for native Windows ARM64 wheel/hooks.

Only the Windows build helper adds this directory to PYTHONPATH. Packaged
application checks remove it and use the bundle's own runtime hook instead.
"""
import os
import sys

if sys.platform == "win32" and not getattr(sys, "frozen", False):
    native_directory = os.environ.get("OPENNAVICAT_LIBPQ_DIR")
    if native_directory:
        _native_dll_directory = os.add_dll_directory(native_directory)
