"""Mandatory owned-file execution and strict frozen/installed cipher receipts."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pytest

from opennavicat import sqlite_cipher_probe as probe


@pytest.fixture(scope="module")
def actual_report():
    # An absent provider is a failure, never a conditional skip.
    return probe.probe_sqlite_cipher_runtime()


def test_real_native_probe_reopens_two_encrypted_rows_and_cleans_owned_directory(actual_report):
    probe.validate_sqlite_cipher_runtime(actual_report)
    assert all(actual_report[flag] is True for flag in probe.SQLITE_CIPHER_RUNTIME_FLAGS)
    serialized = json.dumps(actual_report)
    assert "generated-cipher-smoke-key" not in serialized
    assert str(Path.home()) not in serialized and tempfile.gettempdir() not in serialized


def test_missing_provider_fails_before_any_temporary_file_is_created(monkeypatch):
    def absent():
        raise probe.provider.ProviderUnavailable("Generated unavailable provider")
    monkeypatch.setattr(probe.provider, "_provider", absent)
    monkeypatch.setattr(probe.tempfile, "TemporaryDirectory", lambda **kwargs: pytest.fail("Provider absence created a fixture"))
    with pytest.raises(probe.provider.ProviderUnavailable):
        probe.probe_sqlite_cipher_runtime()


@pytest.mark.parametrize("flag", probe.SQLITE_CIPHER_RUNTIME_FLAGS)
@pytest.mark.parametrize("value", [False, 1, None])
def test_cipher_runtime_receipt_requires_every_strict_boolean(actual_report, flag, value):
    changed = {**actual_report, flag: value}
    with pytest.raises(ValueError, match="Encrypted SQLite"):
        probe.validate_sqlite_cipher_runtime(changed)


@pytest.mark.parametrize("field,value", [
    ("sqlite_cipher_provider", "3.54.1.0"),
    ("sqlite_cipher_mc_version", "SQLite3 Multiple Ciphers 2.6.0"),
    ("sqlite_cipher_sqlite_version", "3.45.0"),
    ("sqlite_cipher_extension_sha256", "not-a-native-extension-hash"),
])
def test_cipher_runtime_receipt_requires_exact_versions_and_extension_hash(actual_report, field, value):
    with pytest.raises(ValueError, match="Encrypted SQLite"):
        probe.validate_sqlite_cipher_runtime({**actual_report, field: value})


@pytest.mark.parametrize("name", probe.SQLITE_CIPHER_LICENSE_HASHES)
def test_missing_or_modified_cipher_license_is_a_smoke_failure(tmp_path, monkeypatch, name):
    root = Path(probe.__file__).resolve().parents[1]
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    for filename in probe.SQLITE_CIPHER_LICENSE_HASHES:
        shutil.copyfile(root / "licenses" / filename, licenses / filename)
    monkeypatch.setattr(probe, "__file__", str(tmp_path / "opennavicat" / "sqlite_cipher_probe.py"))
    (licenses / name).write_bytes(b"generated altered license")
    with pytest.raises(RuntimeError, match="third-party license"):
        probe._verify_license_files()


def test_frozen_probe_cannot_use_build_host_licenses(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe.sys, "_MEIPASS", str(tmp_path), raising=False)
    with pytest.raises(RuntimeError, match="third-party license"):
        probe._verify_license_files()


def test_shipped_cipher_license_source_receipt_matches_all_retained_files():
    root = Path(probe.__file__).resolve().parents[1]
    receipt = json.loads((root / "docs/evidence/sqlite-cipher-license-sources-3.54.0.0.json").read_text())
    assert receipt["distribution"] == "apsw-sqlite3mc" and receipt["version"] == "3.54.0.0"
    assert set(receipt["files"]) == set(probe.SQLITE_CIPHER_LICENSE_HASHES)
    for name, value in receipt["files"].items():
        content = (root / "licenses" / name).read_bytes()
        assert value["sha256"] == hashlib.sha256(content).hexdigest() == probe.SQLITE_CIPHER_LICENSE_HASHES[name]
        assert value["bytes"] == len(content)
