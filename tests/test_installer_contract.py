"""Reject release relabeling, missing native dependencies and empty GUI smokes."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

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
            "paramiko": "5.1.0", "cryptography": "46.0.0", "crypto_legacy_probe": True,
            "python_tds": "1.17.1", "redis": "8.1.0", "pyopenssl": "26.4.0",
            "certifi": "2026.10.23", "sqlserver_default_ca_present": True, "extra_adapters_constructed": True, "sqlserver_tls_context_ready": True,
            "pymongo": "4.19.0", "mongodb_adapter_constructed": True,
            "mongodb_bson_probe": True, "mongodb_tls_context_ready": True,
            "oracledb": "26.1.0", "oracle_adapter_constructed": True,
            "oracle_native_extensions_loaded": True, "oracle_parameters_ready": True,
            "oracle_thin_mode_ready": True,
            **dict.fromkeys(('native_js_helper_ready','native_js_bigint_bson_probe','native_js_cancel_probe','native_js_child_reaped','native_js_no_console','native_js_bundled'),True)}}


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


@pytest.mark.parametrize("field", ["mongodb_adapter_constructed", "mongodb_bson_probe", "mongodb_tls_context_ready"])
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_installed_mongodb_probe_cannot_be_missing_or_truthy_text(tmp_path, field, value):
    payload = report()
    if value is None:
        payload["runtime"].pop(field)
    else:
        payload["runtime"][field] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(installer.PackagingError, match="MongoDB"):
        installer.validate_smoke_report(path, "0.3.0", "arm64", "windows")


@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_new_windows_package_requires_real_registry_api_probe(tmp_path, value):
    payload = report(version="0.7.0")
    if value is not None:
        payload["runtime"]["navicat_windows_api_ready"] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(installer.PackagingError, match="Windows registry"):
        installer.validate_smoke_report(path, "0.7.0", "arm64", "windows")
    payload["runtime"]["navicat_windows_api_ready"] = True
    path.write_text(json.dumps(payload))
    assert installer.validate_smoke_report(path, "0.7.0", "arm64", "windows")["smoke"] == "passed"


def test_old_windows_release_does_not_claim_a_later_registry_probe(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report(version="0.6.1")))
    assert installer.validate_smoke_report(path, "0.6.1", "arm64", "windows")["smoke"] == "passed"


@pytest.mark.parametrize("field", ["oracle_adapter_constructed", "oracle_native_extensions_loaded",
    "oracle_parameters_ready", "oracle_thin_mode_ready"])
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_new_installed_oracle_probes_require_actual_true(tmp_path, field, value):
    payload = report(version="0.13.0")
    payload["runtime"]["navicat_windows_api_ready"] = True
    payload["runtime"][field] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(installer.PackagingError, match="Oracle"):
        installer.validate_smoke_report(path, "0.13.0", "arm64", "windows")


def test_oracle_probe_is_not_retroactively_required_by_published_012(tmp_path):
    payload = report(version="0.12.0")
    payload["runtime"]["navicat_windows_api_ready"] = True
    for field in tuple(payload["runtime"]):
        if field.startswith("oracle"):
            payload["runtime"].pop(field)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload))
    assert installer.validate_smoke_report(path, "0.12.0", "arm64", "windows")["smoke"] == "passed"


def test_new_installed_oracle_requires_qualified_driver_version(tmp_path):
    payload = report(version="0.13.0")
    payload["runtime"]["oracledb"] = "3.3.0"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(installer.PackagingError, match="Oracle"):
        installer.validate_smoke_report(path, "0.13.0", "arm64", "windows")
@pytest.mark.parametrize('field', ['native_js_helper_ready','native_js_bigint_bson_probe','native_js_cancel_probe','native_js_child_reaped','native_js_no_console','native_js_bundled'])
@pytest.mark.parametrize('value', [None, False, 'true', 1])
def test_installed_native_javascript_requires_executed_true_probes(tmp_path,field,value):
    value_report=report()
    if value is None:value_report['runtime'].pop(field)
    else:value_report['runtime'][field]=value
    path=tmp_path/'report.json';path.write_text(json.dumps(value_report))
    with pytest.raises(installer.PackagingError,match='JavaScript'):
        installer.validate_smoke_report(path,'0.3.0','arm64','windows')


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


def test_macos_owned_readonly_mount_retries_resource_busy_without_forcing_normal_success(tmp_path, monkeypatch):
    calls = []

    def detach(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(16, command, stderr="Resource busy")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(installer, "run", detach)
    monkeypatch.setattr(installer.time, "sleep", lambda _: None)
    volume = tmp_path / "our-verification-volume"
    installer.detach_verification_volume(volume)
    assert len(calls) == 2
    assert all("-force" not in command for command in calls)
    assert all(command[-1] == volume for command in calls)


def test_macos_persistent_busy_forces_only_its_owned_readonly_verification_mount(tmp_path, monkeypatch):
    calls = []

    def detach(command, **_kwargs):
        calls.append(command)
        if "-force" not in command:
            raise subprocess.CalledProcessError(16, command, stderr="Resource busy")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(installer, "run", detach)
    monkeypatch.setattr(installer.time, "sleep", lambda _: None)
    volume = tmp_path / "our-verification-volume"
    installer.detach_verification_volume(volume)
    assert len(calls) == 5
    assert calls[-1] == ["/usr/bin/hdiutil", "detach", "-force", volume]


def test_macos_detach_does_not_retry_or_force_unrelated_errors(tmp_path, monkeypatch):
    calls = []

    def detach(command, **_kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="Permission denied")

    monkeypatch.setattr(installer, "run", detach)
    with pytest.raises(subprocess.CalledProcessError):
        installer.detach_verification_volume(tmp_path / "our-verification-volume")
    assert len(calls) == 1
    assert "-force" not in calls[0]


@pytest.fixture
def macos_dmg_case(tmp_path, monkeypatch):
    payload, output = tmp_path / 'Payload.app', tmp_path / 'release-assets'
    payload.mkdir(); output.mkdir()
    (payload / 'owned-marker').write_bytes(b'generated application input')
    case = SimpleNamespace(payload=payload, output=output, images=[], delays=[], create=None,
        target=output / 'OpenNavicat-0.12.0-macos-arm64.dmg')
    case.target.write_bytes(b'previous verified installer')
    monkeypatch.setattr(installer.time, 'sleep', case.delays.append)

    def applications_link(path, destination, **_kwargs):
        # This portable unit test isolates image creation. The real macOS
        # symlink and ditto behavior are covered by native install acceptance.
        assert path.name == 'Applications' and destination == '/Applications'
        path.write_text('generated Applications-link stand-in')

    monkeypatch.setattr(installer.Path, 'symlink_to', applications_link)

    def run(command, **kwargs):
        if command[0] == '/usr/bin/ditto':
            destination = Path(command[-1]); destination.mkdir()
            (destination / 'owned-marker').write_bytes((payload / 'owned-marker').read_bytes())
            return subprocess.CompletedProcess(command, 0)
        assert command[:2] == ['/usr/bin/hdiutil', 'create']
        assert command[command.index('-format') + 1] == 'UDZO'
        assert command[command.index('-fs') + 1] == 'HFS+' and kwargs['timeout'] == 600
        stage = Path(command[command.index('-srcfolder') + 1])
        assert (stage / 'OpenNavicat.app/owned-marker').read_bytes() == b'generated application input'
        image = Path(command[-1]); case.images.append(image)
        return case.create(command, image)

    monkeypatch.setattr(installer, 'run', run)
    return case


def dmg_attempts(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]


def test_macos_busy_create_retries_isolated_partial_images_and_only_promotes_success(macos_dmg_case, capsys):
    case = macos_dmg_case

    def create(command, image):
        assert case.target.read_bytes() == b'previous verified installer'
        assert all(not old.exists() for old in case.images[:-1])
        if len(case.images) < 3:
            image.write_bytes(b'partial image must never replace the target')
            raise subprocess.CalledProcessError(1, command, output='owned busy output',
                stderr='hdiutil: create failed - Resource busy\n')
        image.write_bytes(b'complete generated DMG')
        return subprocess.CompletedProcess(command, 0, stdout='created generated DMG', stderr='')

    case.create = create
    result = installer.build_macos(case.payload, case.output, '0.12.0', 'arm64')
    assert result == case.target and result.read_bytes() == b'complete generated DMG'
    assert len(set(case.images)) == 3 and all(image != case.target for image in case.images)
    assert all(image.parent.parent == case.output for image in case.images)
    assert all(not image.exists() for image in case.images)
    assert list(case.output.iterdir()) == [case.target] and case.delays == [1, 2]
    records = dmg_attempts(capsys)
    assert [record['status'] for record in records] == ['busy', 'busy', 'success']
    assert [record['attempt'] for record in records] == [1, 2, 3]
    assert all(record['stage'] == 'hdiutil-create' for record in records)
    assert records[0]['stdout'] == 'owned busy output'
    assert records[0]['stderr'] == 'hdiutil: create failed - Resource busy\n'
    assert records[-1]['stdout'] == 'created generated DMG' and records[-1]['returncode'] == 0


def test_macos_busy_create_exhaustion_preserves_existing_target_and_cleans_partials(macos_dmg_case, capsys):
    case = macos_dmg_case

    def create(command, image):
        assert case.target.read_bytes() == b'previous verified installer'
        image.write_bytes(b'partial busy image')
        raise subprocess.CalledProcessError(1, command, stderr='hdiutil: create failed - Resource busy\n')

    case.create = create
    with pytest.raises(subprocess.CalledProcessError):
        installer.build_macos(case.payload, case.output, '0.12.0', 'arm64')
    assert len(set(case.images)) == 3 and case.delays == [1, 2]
    assert case.target.read_bytes() == b'previous verified installer'
    assert list(case.output.iterdir()) == [case.target] and all(not image.exists() for image in case.images)
    records = dmg_attempts(capsys)
    assert len(records) == 3 and records[-1]['status'] == 'busy' and not records[-1]['will_retry']


@pytest.mark.parametrize('stderr', ['hdiutil: create failed - Permission denied\n', 'Resource busy\n',
    'warning: hdiutil: create failed - Resource busy\n', 'hdiutil: create failed - Resource busy elsewhere\n', ''])
def test_macos_create_unrelated_error_or_stdout_busy_is_not_retried(macos_dmg_case, capsys, stderr):
    case = macos_dmg_case

    def create(command, image):
        image.write_bytes(b'partial unrelated-error image')
        raise subprocess.CalledProcessError(1, command,
            output='hdiutil: create failed - Resource busy\n', stderr=stderr)

    case.create = create
    with pytest.raises(subprocess.CalledProcessError):
        installer.build_macos(case.payload, case.output, '0.12.0', 'arm64')
    assert len(case.images) == 1 and case.delays == []
    assert case.target.read_bytes() == b'previous verified installer' and list(case.output.iterdir()) == [case.target]
    assert dmg_attempts(capsys)[0]['status'] == 'failed'


def test_macos_create_timeout_is_not_retried_even_with_busy_stderr(macos_dmg_case, capsys):
    case = macos_dmg_case

    def create(command, image):
        image.write_bytes(b'partial timed-out image')
        raise subprocess.TimeoutExpired(command, 600, output=b'owned timeout output \xff',
            stderr=b'hdiutil: create failed - Resource busy\n')

    case.create = create
    with pytest.raises(subprocess.TimeoutExpired):
        installer.build_macos(case.payload, case.output, '0.12.0', 'arm64')
    assert len(case.images) == 1 and case.delays == []
    assert case.target.read_bytes() == b'previous verified installer' and list(case.output.iterdir()) == [case.target]
    record = dmg_attempts(capsys)[0]
    assert record['status'] == 'timeout' and record['returncode'] is None
    assert record['stdout'] == 'owned timeout output \ufffd'


@pytest.mark.parametrize('image_type', ['missing', 'empty', 'directory'])
def test_macos_zero_exit_without_nonempty_regular_image_is_not_promoted(macos_dmg_case, capsys, image_type):
    case = macos_dmg_case

    def create(command, image):
        if image_type == 'empty':
            image.write_bytes(b'')
        elif image_type == 'directory':
            image.mkdir()
        return subprocess.CompletedProcess(command, 0, stdout='not a complete image', stderr='')

    case.create = create
    with pytest.raises(installer.PackagingError, match='nonempty regular'):
        installer.build_macos(case.payload, case.output, '0.12.0', 'arm64')
    assert len(case.images) == 1 and case.delays == []
    assert case.target.read_bytes() == b'previous verified installer' and list(case.output.iterdir()) == [case.target]
    assert dmg_attempts(capsys)[0]['status'] == 'invalid-output'
