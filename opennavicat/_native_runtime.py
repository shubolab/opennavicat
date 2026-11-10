"""Bootstrap one explicit Windows OpenSSL build before SSL/hash imports."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def prepare_native_openssl(directory):
    if sys.platform != "win32":
        return {}
    import ctypes
    root = Path(directory).resolve()
    handles = getattr(sys, "_opennavicat_native_library_handles", [])
    handles.append(os.add_dll_directory(str(root)))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint
    loaded = {}
    crypto = None
    for pattern in ("libcrypto*.dll", "libssl*.dll"):
        libraries = sorted(root.glob(pattern))
        if len(libraries) != 1:
            raise RuntimeError(f"Expected one native {pattern} in {root}")
        library = libraries[0]
        # Windows resolves a dependency by basename to an already loaded DLL.
        # Preload the intended build before CPython's _ssl/_hashlib can select
        # a same-name DLL from a different OpenSSL release.
        handle = ctypes.WinDLL(str(library))
        handles.append(handle)
        selected = kernel.GetModuleHandleW(library.name)
        buffer = ctypes.create_unicode_buffer(32768)
        if not selected or not kernel.GetModuleFileNameW(selected, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = Path(buffer.value).resolve()
        if not os.path.samefile(actual, library):
            raise RuntimeError(f"A different OpenSSL DLL was loaded before bootstrap: {actual}; expected {library}")
        loaded[library.name] = str(actual)
        if pattern.startswith("libcrypto"):
            crypto = handle
    crypto.OpenSSL_version.argtypes = [ctypes.c_int]
    crypto.OpenSSL_version.restype = ctypes.c_char_p
    result = {"version": crypto.OpenSSL_version(0).decode("utf-8"), "libraries": loaded}
    sys._opennavicat_native_library_handles = handles
    sys._opennavicat_native_openssl = result
    return result
