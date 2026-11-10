"""Provider discovery, frozen paths and SDK-vs-CPython DLL precedence."""
import importlib.util
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("openssl_provider_check", ROOT / "scripts/check-openssl-providers.py")
helper = importlib.util.module_from_spec(specification)
specification.loader.exec_module(helper)


def test_compiled_modules_directory_precedes_adjacent_guess(tmp_path):
    library = tmp_path / "lib/libcrypto.3.dylib"
    compiled = tmp_path / "Cellar/openssl/3.6/lib/openssl-modules"
    candidates = helper.directory_candidates(library, str(compiled), None)
    assert candidates[0] == compiled
    assert library.parent / "ossl-modules" in candidates
    assert library.parent / "openssl-modules" in candidates


def test_dynamic_crypto_collects_actual_compiled_provider_location(tmp_path, monkeypatch):
    library = tmp_path / "lib/libcrypto.3.dylib"
    library.parent.mkdir()
    library.touch()
    modules = tmp_path / "private-build/provider-modules"
    modules.mkdir(parents=True)
    provider = modules / "legacy.dylib"
    provider.write_bytes(b"native provider fixture")
    binding = tmp_path / "_rust.abi3.so"
    binding.touch()
    monkeypatch.delenv("OPENSSL_MODULES", raising=False)
    monkeypatch.setattr(helper, "find_spec", lambda _: SimpleNamespace(origin=str(binding)))
    monkeypatch.setattr(helper, "imports", lambda path: [{"name": str(library), "resolved": str(library)}] if path == binding else [])
    monkeypatch.setattr(helper, "_openssl_details", lambda _: {"version": "OpenSSL 3.6.5", "compiled_modules_directory": str(modules)})
    report = helper.inspect()
    assert report["linkage"] == "dynamic"
    assert report["libraries"][0]["selected_modules_directory"] == str(modules)
    assert report["providers"][0]["source"] == str(provider)
    assert report["providers"][0]["destination"] == "ossl-modules"
    assert len(report["providers"][0]["sha256"]) == 64


def test_missing_dynamic_legacy_provider_fails_build_instead_of_using_host_fallback(tmp_path, monkeypatch):
    library = tmp_path / "libcrypto.3.dylib"
    library.touch()
    binding = tmp_path / "_rust.abi3.so"
    binding.touch()
    monkeypatch.delenv("OPENSSL_MODULES", raising=False)
    monkeypatch.setattr(helper, "find_spec", lambda _: SimpleNamespace(origin=str(binding)))
    monkeypatch.setattr(helper, "imports", lambda _: [{"name": str(library), "resolved": str(library)}])
    monkeypatch.setattr(helper, "_openssl_details", lambda _: {"version": "OpenSSL 3.6.5", "compiled_modules_directory": str(tmp_path / "missing")})
    with pytest.raises(RuntimeError, match="No matching legacy provider"):
        helper.inspect()


def test_sdk_openssl_overrides_cpython_duplicate_without_changing_toc_type(tmp_path):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    crypto, ssl = sdk / "libcrypto-3-arm64.dll", sdk / "libssl-3-arm64.dll"
    crypto.touch()
    ssl.touch()
    toc = [(crypto.name, "Python/DLLs/" + crypto.name, "BINARY"),
           ("nested/" + ssl.name, "Python/DLLs/" + ssl.name, "BINARY"),
           ("_ssl.pyd", "Python/DLLs/_ssl.pyd", "EXTENSION")]
    actual = helper.prefer_sdk_openssl(toc, sdk)
    assert actual[0] == (crypto.name, str(crypto), "BINARY")
    assert actual[1] == ("nested/" + ssl.name, str(ssl), "BINARY")
    assert actual[2] == toc[2]


def test_windows_loaded_sdk_identity_overrides_static_python_search_heuristics(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk/libcrypto-3-arm64.dll"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "_opennavicat_native_openssl",
        {"version": "OpenSSL 3.6.5", "libraries": {sdk.name: str(sdk)}}, raising=False)
    paths = helper.crypto_libraries([{"name": sdk.name,
        "resolved": str(tmp_path / "Python/DLLs" / sdk.name)}])
    assert paths == [sdk]


def test_frozen_provider_path_is_bundled_on_macos(tmp_path, monkeypatch):
    modules = tmp_path / "ossl-modules"
    modules.mkdir()
    (modules / "legacy.dylib").touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("OPENSSL_MODULES", "/external/development/modules")
    runpy.run_path(str(ROOT / "scripts/runtime_libpq.py"))
    assert os.environ["OPENSSL_MODULES"] == str(modules)


def test_windows_sdk_is_preloaded_after_provider_path_but_before_later_hooks(tmp_path, monkeypatch):
    import opennavicat._native_runtime as bootstrap
    (tmp_path / "libpq.dll").touch()
    modules = tmp_path / "ossl-modules"
    modules.mkdir()
    (modules / "legacy.dll").touch()
    called = []

    def preload(directory):
        assert os.environ["OPENSSL_MODULES"] == str(modules)
        called.append(directory)

    monkeypatch.setattr(bootstrap, "prepare_native_openssl", preload)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", "original")
    monkeypatch.delenv("OPENSSL_MODULES", raising=False)
    runpy.run_path(str(ROOT / "scripts/runtime_libpq.py"))
    assert called == [tmp_path]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path)


def test_macos_bundle_cannot_keep_an_absolute_homebrew_crypto_dependency(tmp_path, monkeypatch):
    library = tmp_path / "Contents/Frameworks/legacy.dylib"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(helper, "imports", lambda _: [{"name": "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib", "resolved": None}])
    with pytest.raises(RuntimeError, match="development dependency"):
        helper.inspect_bundle(tmp_path)
