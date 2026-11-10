"""Reject release relabeling, missing native dependencies and empty GUI smokes."""
import importlib.util
import json
from pathlib import Path
import struct
import sys

import pytest


script = Path(__file__).resolve().parents[1] / "scripts/package-installer.py"
specification = importlib.util.spec_from_file_location("opennavicat_installer", script)
installer = importlib.util.module_from_spec(specification)
sys.modules[specification.name] = installer
specification.loader.exec_module(installer)


def pe(machine):
    value = bytearray(128)
    value[:2] = b"MZ"
    struct.pack_into("<I", value, 0x3C, 80)
    value[80:84] = b"PE\0\0"
    struct.pack_into("<H", value, 84, machine)
    return bytes(value)


def elf(machine):
    value = bytearray(64)
    value[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", value, 18, machine)
    return bytes(value)


def mach(machine):
    return struct.pack("<IIIIIIII", 0xFEEDFACF, machine, 0, 2, 0, 0, 0, 0)


@pytest.mark.parametrize("data,format_name,architecture", [
    (pe(0xAA64), "PE", "arm64"), (pe(0x8664), "PE", "amd64"),
    (pe(0x14C), "PE", "x86"), (elf(183), "ELF", "arm64"),
    (elf(62), "ELF", "amd64"), (mach(0x0100000C), "Mach-O", "arm64"),
    (mach(0x01000007), "Mach-O", "amd64"),
])
def test_actual_binary_cpu_headers(tmp_path, data, format_name, architecture):
    path = tmp_path / "actual-image"
    path.write_bytes(data)
    assert installer.binary_identity(path) == {"format": format_name, "architectures": [architecture]}


def test_universal_macho_requires_a_native_slice(tmp_path):
    path = tmp_path / "universal"
    path.write_bytes(struct.pack(">II", 0xCAFEBABE, 2) +
        struct.pack(">IIIII", 0x01000007, 0, 48, 32, 0) +
        struct.pack(">IIIII", 0x0100000C, 0, 80, 32, 0) + bytes(64))
    assert installer.binary_identity(path)["architectures"] == ["amd64", "arm64"]
    installer.require_native(path, "macos", "arm64")
    with pytest.raises(installer.PackagingError, match="not a native"):
        installer.require_native(path, "windows", "arm64")


def test_arm64_application_cannot_hide_an_amd64_dependency(tmp_path):
    payload = tmp_path / "OpenNavicat"
    payload.mkdir()
    (payload / "OpenNavicat.exe").write_bytes(pe(0xAA64))
    (payload / "libpq.dll").write_bytes(pe(0x8664))
    with pytest.raises(installer.PackagingError, match="libpq.dll.*not a native"):
        installer.validate_payload(payload, "windows", "arm64")
    (payload / "libpq.dll").write_bytes(pe(0xAA64))
    manifest = installer.validate_payload(payload, "windows", "arm64")
    assert manifest["native_image_count"] == 2
    assert len(manifest["executable_sha256"]) == 64


def test_payload_never_follows_external_directory_or_file_symlinks(tmp_path):
    payload = tmp_path / "OpenNavicat"
    payload.mkdir()
    (payload / "OpenNavicat").write_bytes(elf(183))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "system.so").write_bytes(elf(183))
    try:
        (payload / "libraries").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not grant symlink creation privileges")
    with pytest.raises(installer.PackagingError, match="escapes"):
        installer.validate_payload(payload, "linux", "arm64")


@pytest.mark.parametrize("value", ['0.3.0\n[Files]', '0.3.0"', '../0.3.0', 'latest', 'v0.3.0'])
def test_release_version_cannot_inject_installer_source(value):
    with pytest.raises(installer.PackagingError, match="version"):
        installer.validate_version(value)


def report(architecture="arm64", version="0.3.0"):
    return {"smoke": "passed", "rows": 6, "columns": ["customer"],
        "runtime": {"version": version, "os": "Windows", "machine": architecture,
            "libpq": 180000, "qt": "6.12.0", "psycopg": "3.3.8", "pymysql": "1.2.4",
            "paramiko": "5.1.0", "cryptography": "46.0.0", "crypto_legacy_probe": True}}


def test_empty_windowed_process_stdout_is_not_an_installed_smoke(tmp_path):
    path = tmp_path / "report.json"
    with pytest.raises(installer.PackagingError, match="did not write"):
        installer.validate_smoke_report(path, "0.3.0", "arm64", "windows")
    path.write_text(json.dumps(report()))
    assert installer.validate_smoke_report(path, "0.3.0", "arm64", "windows")["rows"] == 6


@pytest.mark.parametrize("field,value,error", [
    ("machine", "AMD64", "wrong CPU"), ("version", "0.2.0", "differs"),
    ("libpq", 0, "native database runtime"), ("paramiko", "", "driver did not load"),
    ("crypto_legacy_probe", False, "legacy provider"),
])
def test_installed_report_must_prove_architecture_version_and_driver_loading(tmp_path, field, value, error):
    value_report = report()
    value_report["runtime"][field] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(value_report))
    with pytest.raises(installer.PackagingError, match=error):
        installer.validate_smoke_report(path, "0.3.0", "arm64", "windows")


def test_inno_native_architecture_and_verification_install_are_explicit(tmp_path):
    source = installer.render_inno(tmp_path / "Payload", tmp_path / "Output", "OpenNavicat-0.3.0-windows-arm64-setup", "0.3.0", "arm64")
    assert "ArchitecturesAllowed=arm64" in source
    assert "ArchitecturesInstallIn64BitMode=arm64" in source
    assert "org.opennavicat.verification." in source
    assert "AppId={code:ApplicationId}" in source
    assert "UsePreviousLanguage=no" in source
    assert "{param:VERIFY|0}" in source
    assert 'PrivilegesRequired=lowest' in source
    assert 'SetupArchitecture=arm64' not in source
    assert 'recursesubdirs createallsubdirs' in source
    amd = installer.render_inno(tmp_path / "Payload", tmp_path / "Output", "native-amd64", "0.3.0", "amd64")
    assert "ArchitecturesAllowed=x64os" in amd


def test_debian_native_metadata_and_working_desktop_entry():
    control = installer.debian_control("0.3.0", "arm64", 12345, "2.39")
    assert "Architecture: arm64\n" in control
    assert "Version: 0.3.0\n" in control
    assert "libc6 (>= 2.39)" in control
    assert "Installed-Size: 12345" in control
    desktop = (installer.RESOURCES / "opennavicat.desktop").read_text("utf-8")
    assert "Exec=/usr/bin/opennavicat\n" in desktop
    assert "%F" not in desktop


def test_cli_refuses_cross_architecture_before_creating_any_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "host_os", lambda: "linux")
    monkeypatch.setattr(installer, "host_arch", lambda: "arm64")
    with pytest.raises(installer.PackagingError, match="refusing a relabeled"):
        installer.main(["--output-dir", str(tmp_path), "--arch", "amd64"])
    assert list(tmp_path.iterdir()) == []
