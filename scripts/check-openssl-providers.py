"""Find provider modules belonging to cryptography's actual OpenSSL library.

The PyInstaller contrib hook checks only one adjacent directory. Homebrew and
vcpkg can use other compiled MODULESDIR locations, so query the linked libcrypto
itself rather than guessing a provider from another OpenSSL installation.
"""
from __future__ import annotations

import os
import sys

# This must precede hashlib/ssl or any package which can import them. The CI
# startup hook performs the same bootstrap for the builder's Python process.
if sys.platform == "win32" and os.environ.get("OPENNAVICAT_LIBPQ_DIR"):
    import opennavicat  # registers and full-path preloads the explicit SDK build

import argparse
import ctypes
import hashlib
from importlib.util import find_spec
import json
from pathlib import Path
import platform
import subprocess


def imports(path: Path) -> list[dict]:
    from PyInstaller.depend.bindepend import get_imports
    return [{"name": name, "resolved": str(resolved) if resolved else None}
            for name, resolved in sorted(get_imports(str(path)))]


def crypto_libraries(dependencies: list[dict]) -> list[Path]:
    result = []
    bootstrap = getattr(sys, "_opennavicat_native_openssl", {})
    actual_loaded = {name.casefold(): value for name, value in bootstrap.get("libraries", {}).items()}
    for dependency in dependencies:
        name = dependency["name"].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        resolved = actual_loaded.get(name) if sys.platform == "win32" else None
        resolved = resolved or dependency["resolved"]
        if "libcrypto" in name and resolved:
            path = Path(resolved).resolve()
            if path not in result:
                result.append(path)
    return result


def directory_candidates(library: Path, compiled_directory: str | None,
                         explicit_directory: str | None) -> list[Path]:
    values = []
    if explicit_directory:
        values.append(Path(explicit_directory))
    if compiled_directory:
        values.append(Path(compiled_directory))
    # Keep both the installed symlink prefix and its resolved Cellar target.
    for parent in (library.parent, library.resolve().parent):
        for base in (parent, parent.parent / "lib"):
            values.extend([base / "ossl-modules", base / "openssl-modules"])
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _openssl_details(path: Path) -> dict:
    library = ctypes.CDLL(str(path))
    library.OpenSSL_version.argtypes = [ctypes.c_int]
    library.OpenSSL_version.restype = ctypes.c_char_p
    version = library.OpenSSL_version(0).decode("utf-8", "replace")
    compiled = None
    if hasattr(library, "OPENSSL_info"):
        library.OPENSSL_info.argtypes = [ctypes.c_int]
        library.OPENSSL_info.restype = ctypes.c_char_p
        value = library.OPENSSL_info(1003)  # OPENSSL_INFO_MODULES_DIR, openssl/crypto.h
        compiled = value.decode("utf-8", "replace") if value else None
    return {"version": version, "compiled_modules_directory": compiled}


def provider_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file()
        and path.suffix.lower() in {".dll", ".dylib", ".so"})


def probe_library(path: Path, modules: Path) -> dict:
    """Execute the public zero-key Blowfish vector through this native library."""
    library = ctypes.CDLL(str(path))
    def function(name, arguments, result):
        symbol = getattr(library, name)
        symbol.argtypes, symbol.restype = arguments, result
        return symbol
    pointer, integer = ctypes.c_void_p, ctypes.c_int
    set_path = function("OSSL_PROVIDER_set_default_search_path", [pointer, ctypes.c_char_p], integer)
    load = function("OSSL_PROVIDER_load", [pointer, ctypes.c_char_p], pointer)
    unload = function("OSSL_PROVIDER_unload", [pointer], integer)
    fetch = function("EVP_CIPHER_fetch", [pointer, ctypes.c_char_p, ctypes.c_char_p], pointer)
    cipher_free = function("EVP_CIPHER_free", [pointer], None)
    context_new = function("EVP_CIPHER_CTX_new", [], pointer)
    context_free = function("EVP_CIPHER_CTX_free", [pointer], None)
    initialize = function("EVP_EncryptInit_ex", [pointer, pointer, pointer, pointer, pointer], integer)
    padding = function("EVP_CIPHER_CTX_set_padding", [pointer, integer], integer)
    update = function("EVP_EncryptUpdate", [pointer, pointer, ctypes.POINTER(integer), pointer, integer], integer)
    finalize = function("EVP_EncryptFinal_ex", [pointer, pointer, ctypes.POINTER(integer)], integer)
    if set_path(None, os.fsencode(modules)) != 1:
        raise RuntimeError(f"OpenSSL refused its provider path: {modules}")
    default, legacy = load(None, b"default"), load(None, b"legacy")
    cipher, context = None, None
    try:
        if not legacy:
            raise RuntimeError(f"The native legacy provider failed to load from {modules}")
        cipher = fetch(None, b"BF-ECB", None)
        context = context_new()
        if not cipher or not context:
            raise RuntimeError("The native provider did not expose Blowfish ECB")
        key = ctypes.create_string_buffer(bytes(16), 16)
        data = ctypes.create_string_buffer(bytes(8), 8)
        output = ctypes.create_string_buffer(32)
        first, final = integer(), integer()
        if (initialize(context, cipher, None, key, None) != 1 or padding(context, 0) != 1
                or update(context, output, ctypes.byref(first), data, 8) != 1
                or finalize(context, ctypes.byref(output, first.value), ctypes.byref(final)) != 1):
            raise RuntimeError("The native OpenSSL legacy cipher probe failed")
        encrypted = bytes(output)[:first.value + final.value].hex()
        if encrypted != "4ef997456198dd78":
            raise RuntimeError(f"The native OpenSSL legacy cipher vector was wrong: {encrypted}")
        return {"passed": True, "algorithm": "BF-ECB", "zero_vector": encrypted}
    finally:
        if context:
            context_free(context)
        if cipher:
            cipher_free(cipher)
        if legacy:
            unload(legacy)
        if default:
            unload(default)


def inspect(override_library: Path | None = None, probe=False) -> dict:
    bindings = Path(find_spec("cryptography.hazmat.bindings._rust").origin)
    dependencies = imports(bindings)
    libraries = crypto_libraries(dependencies)
    if not libraries:
        for dependency in dependencies:
            if "libssl" in Path(dependency["name"]).name.lower() and dependency["resolved"]:
                libraries.extend(crypto_libraries(imports(Path(dependency["resolved"]))))
    if not libraries and sys.platform == "win32":
        bootstrap = getattr(sys, "_opennavicat_native_openssl", {})
        libraries = [Path(value).resolve() for name, value in bootstrap.get("libraries", {}).items()
                     if name.casefold().startswith("libcrypto")]
    if override_library:
        libraries = [override_library.resolve()]
    explicit = os.environ.get("OPENSSL_MODULES")
    report = {"status": "passed", "platform": sys.platform, "machine": platform.machine(),
        "bindings": str(bindings), "binding_imports": dependencies,
        "explicit_modules_directory": explicit, "libraries": [], "providers": [],
        "native_openssl_bootstrap": getattr(sys, "_opennavicat_native_openssl", None)}
    selected = []
    if not libraries and explicit:
        selected = provider_files(Path(explicit))
        if not any(path.stem.lower() == "legacy" for path in selected):
            raise RuntimeError(f"OPENSSL_MODULES does not contain its native legacy provider: {explicit}")
    for library in libraries:
        details = {"library": str(library), "resolved_library": str(library.resolve()),
                   **_openssl_details(library)}
        candidates = directory_candidates(library, details["compiled_modules_directory"], explicit)
        details["candidate_directories"] = [{"path": str(path), "exists": path.is_dir(),
            "resolved": str(path.resolve()), "files": [str(value) for value in provider_files(path)]}
            for path in candidates]
        report["libraries"].append(details)
        directory = next((path for path in candidates
            if any(value.stem.lower() == "legacy" for value in provider_files(path))), None)
        if directory is None:
            raise RuntimeError("No matching legacy provider was found for " + str(library) +
                "; inspected " + ", ".join(str(path) for path in candidates))
        details["selected_modules_directory"] = str(directory.resolve())
        if probe:
            details["native_probe"] = probe_library(library, directory.resolve())
        selected.extend(provider_files(directory))
    seen = set()
    for path in selected:
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        report["providers"].append({"source": str(real), "destination": "ossl-modules",
            "name": real.name, "sha256": hashlib.sha256(real.read_bytes()).hexdigest(),
            "imports": imports(real)})
    report["linkage"] = "dynamic" if libraries else "static-or-no-external-openssl"
    if probe:
        from cryptography.hazmat.backends.openssl.backend import backend
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        encryptor = Cipher(Blowfish(bytes(16)), modes.ECB()).encryptor()
        encrypted = (encryptor.update(bytes(8)) + encryptor.finalize()).hex()
        if encrypted != "4ef997456198dd78":
            raise RuntimeError("cryptography's actual OpenSSL instance failed the legacy cipher vector")
        report["cryptography_probe"] = {"passed": True, "zero_vector": encrypted,
                                        "openssl": backend.openssl_version_text()}
    return report


def inspect_bundle(directory: Path) -> dict:
    """Reject bundled OpenSSL images still referring to a Homebrew build path."""
    directory = directory.resolve()
    images, seen = [], set()
    report = {"status": "passed", "platform": sys.platform, "machine": platform.machine(),
              "bundle": str(directory), "images": images}
    legacy = next((path for path in directory.rglob("legacy.*")
                   if path.is_file() and "ossl-modules" in str(path)), None)
    for path in directory.rglob("*"):
        if not path.is_file() or path.resolve() in seen:
            continue
        name = path.name.lower()
        if not ("libcrypto" in name or "libssl" in name or name.startswith("legacy.")
                or (name.startswith("_rust") and path.suffix in {".so", ".pyd"})):
            continue
        seen.add(path.resolve())
        dependencies = imports(path)
        image = {"path": str(path.relative_to(directory)), "imports": dependencies}
        images.append(image)
        if sys.platform == "darwin":
            for dependency in dependencies:
                value = dependency["name"]
                if value.startswith(("/usr/local/", "/opt/homebrew/", "/Library/Frameworks/Python.framework/")):
                    raise BundleInspectionError(f"Bundled OpenSSL image retains a development dependency: {path}: {value}", report)
            if name.startswith("libcrypto"):
                image["openssl"] = _openssl_details(path.resolve())
                if legacy:
                    try:
                        image["native_probe"] = probe_library(path.resolve(), legacy.resolve().parent)
                    except Exception as exc:
                        image["native_probe_error"] = str(exc)
                        raise BundleInspectionError(str(exc), report) from exc
    return report


class BundleInspectionError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def prefer_sdk_openssl(binary_toc, directory: Path):
    """Retain TOC types while replacing same-name CPython OpenSSL DLL sources."""
    sdk = {}
    for pattern in ("libcrypto*.dll", "libssl*.dll"):
        files = list(directory.glob(pattern))
        if len(files) != 1:
            raise RuntimeError(f"Expected one SDK {pattern} in {directory}")
        sdk[files[0].name.casefold()] = str(files[0].resolve())
    return prefer_openssl_sources(binary_toc, sdk)


def prefer_openssl_sources(binary_toc, preferred):
    """Do not collapse a provider's OpenSSL version into another same-name copy."""
    sdk = {name.casefold(): str(Path(source).resolve()) for name, source in preferred.items()}
    output, present = [], set()
    for destination, source, kind in binary_toc:
        basename = destination.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if basename in sdk:
            source = sdk[basename]
            present.add(basename)
        output.append((destination, source, kind))
    for basename in sdk.keys() - present:
        path = Path(sdk[basename])
        output.append((path.name, str(path), "BINARY"))
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write build diagnostics as JSON")
    parser.add_argument("--crypto-library", type=Path,
                        help="Inspect one explicit native libcrypto for build diagnostics")
    parser.add_argument("--probe", action="store_true", help="Execute a native legacy-provider cipher vector")
    parser.add_argument("--bundle", type=Path, help="Check frozen OpenSSL dependencies for leaked development paths")
    args = parser.parse_args(argv)
    try:
        report = inspect_bundle(args.bundle) if args.bundle else inspect(args.crypto_library, args.probe)
    except Exception as exc:
        report = getattr(exc, "report", {"platform": sys.platform, "machine": platform.machine()})
        report.update(status="failed", error=str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
