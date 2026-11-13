"""Provider discovery, frozen paths and SDK-vs-CPython DLL precedence."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
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


def test_macos_provider_linked_openssl_pair_wins_psycopg_same_basename(tmp_path):
    crypto = tmp_path / "Homebrew3.6/libcrypto.3.dylib"
    ssl = tmp_path / "Homebrew3.6/libssl.3.dylib"
    toc = [("libcrypto.3.dylib", "Psycopg3.5/libcrypto.3.dylib", "BINARY"),
           ("libssl.3.dylib", "Psycopg3.5/libssl.3.dylib", "BINARY"),
           ("cryptography/_rust.abi3.so", "cryptography/_rust.abi3.so", "EXTENSION")]
    actual = helper.prefer_openssl_sources(toc, {crypto.name: crypto, ssl.name: ssl})
    assert actual[:2] == [(crypto.name, str(crypto), "BINARY"), (ssl.name, str(ssl), "BINARY")]
    assert actual[2] == toc[2]


def test_bundle_probe_failure_records_actual_openssl_version_in_diagnostic(tmp_path, monkeypatch):
    import json
    bundle = tmp_path / "bundle"
    modules = bundle / "ossl-modules"
    modules.mkdir(parents=True)
    (modules / "legacy.dylib").touch()
    (bundle / "libcrypto.3.dylib").touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(helper, "imports", lambda _: [])
    monkeypatch.setattr(helper, "_openssl_details", lambda _: {"version": "OpenSSL 3.5.8"})

    def missing_entrypoint(*_args):
        raise RuntimeError("The native legacy provider failed to load")

    monkeypatch.setattr(helper, "probe_library", missing_entrypoint)
    output = tmp_path / "diagnostic.json"
    assert helper.main(["--bundle", str(bundle), "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    crypto = next(image for image in report["images"] if image["path"] == "libcrypto.3.dylib")
    assert crypto["openssl"]["version"] == "OpenSSL 3.5.8"
    assert "failed to load" in crypto["native_probe_error"]


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
    # Register even an originally absent key before the real hook writes it;
    # delenv on an absent key alone would leave that direct assignment behind.
    monkeypatch.setenv("OPENSSL_MODULES", "")
    runpy.run_path(str(ROOT / "scripts/runtime_libpq.py"))
    assert called == [tmp_path]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path)


@pytest.mark.parametrize("initially_present", [False, True], ids=["absent", "present"])
def test_provider_hook_test_restores_environment_in_real_child_pytest(tmp_path, initially_present):
    """The real hook must not leave a mock provider path for later processes."""
    original = tmp_path / "owned-original-modules"
    original.mkdir()
    evidence = tmp_path / "environment.json"
    program = r'''import json,os,pathlib,sys
import opennavicat,pytest
expected=None
class Probe:
 def pytest_collection_finish(self,session):
  global expected
  # Finish module/driver discovery before isolating this test's environment;
  # do not make cryptography load from an intentionally empty mock directory.
  if sys.argv[1]=='present':os.environ['OPENSSL_MODULES']=sys.argv[2]
  else:os.environ.pop('OPENSSL_MODULES',None)
  expected=os.environ.get('OPENSSL_MODULES')
code=pytest.main(['-q','-W','error','tests/test_openssl_packaging.py::test_windows_sdk_is_preloaded_after_provider_path_but_before_later_hooks','--basetemp='+sys.argv[4]],plugins=[Probe()])
actual=os.environ.get('OPENSSL_MODULES')
data=dict(pytest_exit_code=int(code),initially_present=sys.argv[1]=='present',after_present='OPENSSL_MODULES' in os.environ,restored=actual==expected)
pathlib.Path(sys.argv[3]).write_text(json.dumps(data,indent=2)+'\n')
raise SystemExit(0 if int(code)==0 and data['restored'] else 1)
'''
    env = {key: value for key, value in os.environ.items()
           if key != "PYTEST_ADDOPTS" and not key.startswith(
               ("OPENNAVICAT_TEST", "OPENNAVICAT_MONGODB", "OPENNAVICAT_ORACLE"))}
    process = subprocess.run(
        [sys.executable, "-c", program, "present" if initially_present else "absent",
         str(original), str(evidence), str(tmp_path / "child")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert evidence.is_file(), process.stdout + process.stderr
    result = json.loads(evidence.read_text())
    assert process.returncode == 0, json.dumps(result) + "\n" + process.stdout + process.stderr
    assert result == {"pytest_exit_code": 0, "initially_present": initially_present,
                      "after_present": initially_present, "restored": True}


def test_macos_bundle_cannot_keep_an_absolute_homebrew_crypto_dependency(tmp_path, monkeypatch):
    library = tmp_path / "Contents/Frameworks/legacy.dylib"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(helper, "imports", lambda _: [{"name": "/usr/local/opt/openssl@3/lib/libcrypto.3.dylib", "resolved": None}])
    with pytest.raises(RuntimeError, match="development dependency"):
        helper.inspect_bundle(tmp_path)
