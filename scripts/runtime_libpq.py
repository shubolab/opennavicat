"""Expose bundled native libpq to psycopg's Windows ctypes loader."""
import os
from pathlib import Path
import sys

if sys.platform == "win32" and getattr(sys, "frozen", False):
    directory = Path(sys._MEIPASS)
    if (directory / "libpq.dll").is_file():
        # ctypes.util.find_library searches PATH; add_dll_directory handles its
        # dependent OpenSSL DLLs on modern Python. Keep the handle alive.
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        sys._opennavicat_libpq_directory = os.add_dll_directory(str(directory))
