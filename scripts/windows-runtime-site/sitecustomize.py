"""Build-process DLL search path for native Windows ARM64 wheel/hooks.

Only the Windows build helper adds this directory to PYTHONPATH. Packaged
application checks remove it and use the bundle's own runtime hook instead.
"""
import os
import sys

if sys.platform == "win32" and not getattr(sys, "frozen", False):
    native_directory = os.environ.get("OPENNAVICAT_LIBPQ_DIR")
    if native_directory:
        # This also runs in isolated wheel/hook build processes before the
        # editable package is installed, so load the tiny bootstrap by path.
        import importlib.util
        from pathlib import Path
        bootstrap_path = Path(__file__).resolve().parents[2] / "opennavicat/_native_runtime.py"
        bootstrap_spec = importlib.util.spec_from_file_location("_opennavicat_native_bootstrap", bootstrap_path)
        _native_bootstrap = importlib.util.module_from_spec(bootstrap_spec)
        bootstrap_spec.loader.exec_module(_native_bootstrap)
        _native_bootstrap.prepare_native_openssl(native_directory)
