"""Release validation must reject missing, tampered or unverified installers."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).resolve().parents[1] / "scripts/publish-release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture
def assets(tmp_path):
    for system, arch in gate.TARGETS:
        installer = tmp_path / gate.asset_name("0.3.0", system, arch)
        installer.write_bytes(f"contract fixture for {system}/{arch}".encode())
        binary_format = {"macos": "Mach-O", "windows": "PE", "linux": "ELF"}[system]
        verification = {"status": "passed", "installed": True, "launched": True,
            "uninstalled": True, "runtime": {"smoke": "passed", "rows": 6,
                "columns": ["customer", "city", "orders", "revenue"], "runtime": {
                    "os": {"macos": "Darwin", "windows": "Windows", "linux": "Linux"}[system],
                    "version": "0.3.0", "machine": arch, "libpq": 180006,
                    "qt": "fixture", "psycopg": "fixture", "pymysql": "fixture",
                    "paramiko": "fixture", "cryptography": "fixture", "crypto_legacy_probe": True}}}
        manifest = {"name": "OpenNavicat", "version": "0.3.0", "os": system,
            "architecture": arch, "native_host_architecture": arch,
            "installer": installer.name, "bytes": installer.stat().st_size,
            "sha256": gate.digest(installer), "payload": {
                "main": {"format": binary_format, "architectures": [arch]},
                "native_images": [{"format": binary_format, "architectures": [arch]}],
                "native_image_count": 1}, "verification": verification}
        installer.with_suffix(".manifest.json").write_text(json.dumps(manifest), "utf-8")
        installer.with_suffix(".installed-smoke.json").write_text(json.dumps(verification), "utf-8")
    return tmp_path


def first_manifest(assets):
    path = next(assets.glob("*.manifest.json"))
    return path, json.loads(path.read_text("utf-8"))


def test_complete_set_is_verified_without_network_or_publication(assets, monkeypatch):
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: pytest.fail("Verification must not publish"))
    assert gate.publish("v0.3.0", assets, verify_only=True) == {"verified": 6, "version": "0.3.0"}
    assert len((assets / "SHA256SUMS").read_text().splitlines()) == 6


def test_missing_target_is_rejected(assets):
    next(assets.glob("*.deb")).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        gate.validate_assets(assets, "0.3.0")


def test_modified_installer_is_rejected_even_when_filename_and_size_match(assets):
    installer = next(assets.glob("*.exe"))
    data = installer.read_bytes()
    installer.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ValueError, match="checksum"):
        gate.validate_assets(assets, "0.3.0")


@pytest.mark.parametrize("change,expected", [
    (lambda m: m.update(native_host_architecture="x86"), "Non-native build host"),
    (lambda m: m["payload"]["native_images"][0].update(architectures=["x86"]), "Non-native library"),
    (lambda m: m["verification"].update(uninstalled=False), "not fully verified"),
    (lambda m: m["verification"]["runtime"]["runtime"].update(machine="x86"), "does not match"),
])
def test_relabeling_and_incomplete_verification_fail_closed(assets, change, expected):
    path, manifest = first_manifest(assets)
    change(manifest)
    path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match=expected):
        gate.validate_assets(assets, "0.3.0")


def test_tag_must_match_source_version_before_publication(assets):
    with pytest.raises(ValueError, match="Tag must exactly match"):
        gate.publish("v9.9.9", assets, verify_only=True)
