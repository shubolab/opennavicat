"""Release validation must reject missing, tampered or unverified installers."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from opennavicat import __version__ as VERSION

spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).resolve().parents[1] / "scripts/publish-release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture
def assets(tmp_path):
    for system, arch in gate.TARGETS:
        installer = tmp_path / gate.asset_name(VERSION, system, arch)
        installer.write_bytes(f"contract fixture for {system}/{arch}".encode())
        binary_format = {"macos": "Mach-O", "windows": "PE", "linux": "ELF"}[system]
        verification = {"status": "passed", "installed": True, "launched": True,
            "uninstalled": True, "runtime": {"smoke": "passed", "rows": 6,
                "columns": ["customer", "city", "orders", "revenue"], "runtime": {
                    "os": {"macos": "Darwin", "windows": "Windows", "linux": "Linux"}[system],
                    "version": VERSION, "machine": arch, "libpq": 180006,
                    "qt": "fixture", "psycopg": "fixture", "pymysql": "fixture",
                    "paramiko": "fixture", "cryptography": "fixture", "crypto_legacy_probe": True,
                    "python_tds": "1.17.1", "redis": "8.1.0", "pyopenssl": "26.4.0",
                    "certifi": "2026.10.23", "sqlserver_default_ca_present": True,
                    "extra_adapters_constructed": True, "sqlserver_tls_context_ready": True,
                    "pymongo": "4.19.0", "mongodb_adapter_constructed": True,
                    "mongodb_bson_probe": True, "mongodb_tls_context_ready": True,
                    "navicat_windows_api_ready": True if system == "windows" else None,
                    **dict.fromkeys(('native_js_helper_ready','native_js_bigint_bson_probe','native_js_cancel_probe','native_js_child_reaped','native_js_no_console','native_js_bundled'),True)}}}
        manifest = {"name": "OpenNavicat", "version": VERSION, "os": system,
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
    assert gate.publish(f"v{VERSION}", assets, verify_only=True) == {"verified": 6, "version": VERSION}
    assert len((assets / "SHA256SUMS").read_text().splitlines()) == 6


def test_missing_target_is_rejected(assets):
    next(assets.glob("*.deb")).unlink()
    with pytest.raises(ValueError, match="incomplete"):
        gate.validate_assets(assets, VERSION)


def test_modified_installer_is_rejected_even_when_filename_and_size_match(assets):
    installer = next(assets.glob("*.exe"))
    data = installer.read_bytes()
    installer.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(ValueError, match="checksum"):
        gate.validate_assets(assets, VERSION)


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
        gate.validate_assets(assets, VERSION)


def test_tag_must_match_source_version_before_publication(assets):
    with pytest.raises(ValueError, match="Tag must exactly match"):
        gate.publish("v9.9.9", assets, verify_only=True)


@pytest.mark.parametrize("field", ["python_tds", "redis", "pyopenssl", "certifi", "sqlserver_default_ca_present", "pymongo"])
def test_missing_new_driver_runtime_is_rejected(assets, field):
    path, manifest = first_manifest(assets)
    manifest["verification"]["runtime"]["runtime"].pop(field)
    path.write_text(json.dumps(manifest), "utf-8")
    installer = assets / manifest["installer"]
    installer.with_suffix(".installed-smoke.json").write_text(json.dumps(manifest["verification"]), "utf-8")
    with pytest.raises(ValueError, match="dependencies are incomplete"):
        gate.validate_assets(assets, VERSION)


@pytest.mark.parametrize("field", ["extra_adapters_constructed", "sqlserver_tls_context_ready", "mongodb_adapter_constructed", "mongodb_bson_probe", "mongodb_tls_context_ready", 'native_js_helper_ready','native_js_bigint_bson_probe','native_js_cancel_probe','native_js_child_reaped','native_js_no_console','native_js_bundled'])
@pytest.mark.parametrize("reported", [None, False, "true", 1])
def test_adapter_and_tls_probe_must_be_actual_true_boolean(assets, field, reported):
    path, manifest = first_manifest(assets)
    runtime = manifest["verification"]["runtime"]["runtime"]
    if reported is None:
        runtime.pop(field)
    else:
        runtime[field] = reported
    path.write_text(json.dumps(manifest), "utf-8")
    installer = assets / manifest["installer"]
    installer.with_suffix(".installed-smoke.json").write_text(json.dumps(manifest["verification"]), "utf-8")
    with pytest.raises(ValueError, match="probes did not pass"):
        gate.validate_assets(assets, VERSION)


@pytest.mark.parametrize("reported", [None, False, "true", 1])
def test_windows_registry_probe_must_be_actual_true_boolean(assets, reported):
    path = assets / gate.asset_name(VERSION, "windows", "arm64")
    manifest_path = path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    runtime = manifest["verification"]["runtime"]["runtime"]
    if reported is None:
        runtime.pop("navicat_windows_api_ready")
    else:
        runtime["navicat_windows_api_ready"] = reported
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    path.with_suffix(".installed-smoke.json").write_text(json.dumps(manifest["verification"]), "utf-8")
    with pytest.raises(ValueError, match="Windows registry"):
        gate.validate_assets(assets, VERSION)


class MockGitHubRelease:
    """A draft with gh --clobber semantics; no external command is executed."""

    def __init__(self, *, exists=True, initial_assets=(), asset_change=None, fail_upload=False):
        self.exists = exists
        self.is_draft = True
        self.asset_change = asset_change
        self.fail_upload = fail_upload
        self.assets = [dict(asset) for asset in initial_assets]
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append(command)
        assert command[:2] == ["gh", "release"]
        action = command[2]
        if action == "view" and command[-2:] == ["--json", "isDraft"]:
            return subprocess.CompletedProcess(command, 0 if self.exists else 1,
                                               stdout=json.dumps({"isDraft": self.is_draft}))
        if action == "create":
            assert "--draft" in command and "--verify-tag" in command
            self.exists = True
            return subprocess.CompletedProcess(command, 0)
        if action == "upload":
            assert self.is_draft and kwargs.get("check") is True
            if self.fail_upload:
                raise subprocess.CalledProcessError(1, command)
            paths = [Path(value) for value in command[command.index("--clobber") + 1:]]
            # gh --clobber replaces matching names while keeping unrelated
            # assets already attached to a reused draft.
            remote = {asset["name"]: asset for asset in self.assets}
            remote.update({path.name: {"name": path.name, "size": path.stat().st_size,
                                      "state": "uploaded", "digest": "sha256:" + gate.digest(path)} for path in paths})
            self.assets = list(remote.values())
            if self.asset_change:
                self.asset_change(self.assets)
            return subprocess.CompletedProcess(command, 0)
        if action == "view" and command[-2:] == ["--json", "assets"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"assets": self.assets}))
        if action == "edit":
            assert command[-1] == "--draft=false"
            self.is_draft = False
            return subprocess.CompletedProcess(command, 0)
        pytest.fail(f"Unexpected mocked release command: {command}")


@pytest.fixture
def mock_release(monkeypatch, tmp_path):
    project = tmp_path / "source"
    notes = project / "docs/releases" / f"v{VERSION}.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Synthetic milestone notes; never published.\n", "utf-8")
    (project / "pyproject.toml").write_text(f'[project]\nversion = "{VERSION}"\n', "utf-8")
    monkeypatch.setattr(gate, "ROOT", project)
    def install(**kwargs):
        service = MockGitHubRelease(**kwargs)
        monkeypatch.setattr(gate.subprocess, "run", service.run)
        return service
    return install


@pytest.mark.parametrize("exists", [True, False])
def test_exact_eight_uploaded_bytes_publish_only_after_verification(assets, mock_release, exists):
    service = mock_release(exists=exists)
    result = gate.publish(f"v{VERSION}", assets)
    assert result == {"published": f"v{VERSION}", "url": f"https://github.com/shubolab/opennavicat/releases/tag/v{VERSION}"}
    assert service.is_draft is False
    assert len(service.assets) == 8
    assert {asset["name"] for asset in service.assets} == {
        *(gate.asset_name(VERSION, system, arch) for system, arch in gate.TARGETS),
        "SHA256SUMS", "build-manifests.json",
    }
    assert [call[2] for call in service.calls] == (["view", "upload", "view", "edit"] if exists
                                                   else ["view", "create", "upload", "view", "edit"])


@pytest.mark.parametrize("change,expected", [
    (lambda assets: assets.append({"name": "unexpected-stale.zip", "size": 123, "state": "uploaded", "digest": "sha256:" + "0" * 64}), "asset set"),
    (lambda assets: assets.pop(), "asset set"),
    (lambda assets: assets.append(dict(assets[0])), "asset set"),
    (lambda assets: assets[0].update(size=assets[0]["size"] + 1), "size differs"),
    (lambda assets: assets[0].update(digest="sha256:" + "0" * 64), "checksum differs"),
    (lambda assets: assets[0].pop("digest"), "checksum differs"),
    (lambda assets: assets[0].update(state="starter"), "not complete"),
    (lambda assets: assets[0].pop("state"), "not complete"),
    (lambda assets: assets[-2].update(digest="sha256:" + "0" * 64), "checksum differs"),
    (lambda assets: assets[-1].update(digest="sha256:" + "0" * 64), "checksum differs"),
])
def test_reused_draft_is_never_published_with_unverified_remote_assets(assets, mock_release, change, expected):
    service = mock_release(asset_change=change)
    with pytest.raises(ValueError, match=expected):
        gate.publish(f"v{VERSION}", assets)
    assert service.is_draft is True
    assert not any(call[2] == "edit" for call in service.calls)


def test_stale_extra_asset_survives_clobber_but_cannot_be_published(assets, mock_release):
    stale = {"name": "previous-attempt.zip", "size": 123, "state": "uploaded", "digest": "sha256:" + "0" * 64}
    service = mock_release(initial_assets=[stale])
    with pytest.raises(ValueError, match="extra=.*previous-attempt.zip"):
        gate.publish(f"v{VERSION}", assets)
    assert len(service.assets) == 9 and stale in service.assets
    assert service.is_draft is True
    assert not any(call[2] == "edit" for call in service.calls)


def test_public_release_is_never_uploaded_or_republished(assets, mock_release):
    service = mock_release()
    service.is_draft = False
    with pytest.raises(ValueError, match="already public release is immutable"):
        gate.publish(f"v{VERSION}", assets)
    assert service.is_draft is False
    assert [call[2] for call in service.calls] == ["view"]


@pytest.mark.parametrize("exists", [True, False])
def test_failed_upload_keeps_release_draft_private(assets, mock_release, exists):
    service = mock_release(exists=exists, fail_upload=True)
    with pytest.raises(subprocess.CalledProcessError):
        gate.publish(f"v{VERSION}", assets)
    assert service.is_draft is True
    assert [call[2] for call in service.calls] == (["view", "upload"] if exists else ["view", "create", "upload"])
