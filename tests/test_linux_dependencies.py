"""Mandatory CI package gates and mirror fallback, without running host apt."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("linux_dependencies", ROOT / "scripts/install-linux-dependencies.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class Commands:
    def __init__(self, mode="success", arch="amd64"):
        self.mode, self.arch = mode, arch
        self.installed = set()
        self.apt_calls = []
        self.sources = []
        self.source_paths = []
        self.list_paths = []
        self.cleanup = []

    def __call__(self, command, **kwargs):
        if command[0] == "dpkg-query":
            present = command[-1] in self.installed
            return SimpleNamespace(returncode=0 if present else 1,
                                   stdout="install ok installed" if present else "")
        if command[0] == "dpkg":
            return SimpleNamespace(returncode=0, stdout=self.arch + "\n")
        assert command[0] == "sudo"
        if command[1] == "chown":
            self.cleanup.append(command[-1])
            return SimpleNamespace(returncode=0)
        assert command[1:3] == ["timeout", "--kill-after=10s"]
        assert command[3] in ("90s", "180s", "240s")
        assert command[4:7] == ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
        self.apt_calls.append(command)
        source = next((value.split("=", 1)[1] for value in command
                       if value.startswith("Dir::Etc::sourcelist=")), None)
        if source:
            content = Path(source).read_text()
            self.sources.append(content)
            self.source_paths.append(source)
            self.list_paths.append(next(value.split("=", 1)[1] for value in command
                                        if value.startswith("Dir::State::lists=")))
            assert "Dir::Etc::sourceparts=-" in command
        is_tuna = bool(source and "mirrors.tuna.tsinghua.edu.cn" in content)
        if "update" in command:
            assert "APT::Update::Error-Mode=any" in command
            failed = self.mode == "refresh-failed" or self.mode in ("tuna", "tuna-missing") and not is_tuna
            return SimpleNamespace(returncode=100 if failed else 0)
        if self.mode in ("fallback", "refresh-failed", "install-failed", "tuna", "tuna-missing", "official-install-failed") and not source:
            return SimpleNamespace(returncode=124)  # runner HTTP mirror timed out
        if source and (self.mode == "install-failed" or self.mode == "official-install-failed" and not is_tuna):
            return SimpleNamespace(returncode=100)
        packages = command[command.index("--no-install-recommends") + 1:]
        self.installed.update(package for package in packages
                              if self.mode not in ("missing", "tuna-missing") or package != "libegl1")
        return SimpleNamespace(returncode=0)


@pytest.fixture
def command_stub(monkeypatch):
    stub = Commands()
    monkeypatch.setattr(helper, "ubuntu_codename", lambda: "noble")
    monkeypatch.setattr(helper.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(helper.os, "getgid", lambda: 1000, raising=False)
    monkeypatch.setattr(helper.subprocess, "run", stub)
    return stub


def test_installed_dependencies_do_not_contact_apt(command_stub):
    command_stub.installed.update(helper.packages_for("desktop", "noble"))
    helper.install("desktop")
    assert command_stub.apt_calls == []


def test_cached_index_install_can_finish_without_source_refresh(command_stub):
    helper.install("desktop")
    assert len(command_stub.apt_calls) == 1
    assert command_stub.sources == []
    assert not helper.missing_packages(helper.packages_for("desktop", "noble"))


@pytest.mark.parametrize("arch,expected,excluded", [
    ("amd64", "https://archive.ubuntu.com/ubuntu", "ports.ubuntu.com"),
    ("arm64", "https://ports.ubuntu.com/ubuntu-ports", "archive.ubuntu.com"),
])
def test_http_timeout_uses_signed_native_https_fallback(command_stub, arch, expected, excluded):
    command_stub.mode, command_stub.arch = "fallback", arch
    helper.install("desktop")
    assert len(command_stub.apt_calls) == 3
    assert [command[3] for command in command_stub.apt_calls] == ["90s", "180s", "240s"]
    assert len(command_stub.sources) == 2
    for source in command_stub.sources:
        assert expected in source and excluded not in source
        assert "signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg" in source
        assert "noble-updates" in source and "noble-security" in source
        assert "http://" not in source and "azure" not in source
    assert len(command_stub.cleanup) == 1
    assert not Path(command_stub.cleanup[0]).exists()
    assert not helper.missing_packages(helper.packages_for("desktop", "noble"))


@pytest.mark.parametrize("codename,arch,profile,archive", [
    ("jammy", "amd64", "database-security", "ubuntu"),
    ("noble", "amd64", "desktop", "ubuntu"),
    ("noble", "arm64", "desktop", "ubuntu-ports"),
])
def test_canonical_dns_failure_uses_verified_transport_with_ubuntu_signature_key(
    command_stub, monkeypatch, codename, arch, profile, archive,
):
    monkeypatch.setattr(helper, "ubuntu_codename", lambda: codename)
    command_stub.mode, command_stub.arch = "tuna", arch
    helper.install(profile)
    assert len(command_stub.apt_calls) == 4
    assert [command[3] for command in command_stub.apt_calls] == ["90s", "180s", "180s", "240s"]
    assert len(command_stub.sources) == 3
    assert "mirrors.tuna.tsinghua.edu.cn" not in command_stub.sources[0]
    for source in command_stub.sources[1:]:
        assert len(source.splitlines()) == 3
        for line, suffix in zip(source.splitlines(), ("", "-updates", "-security"), strict=True):
            assert line == (
                "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
                f"https://mirrors.tuna.tsinghua.edu.cn/{archive} "
                f"{codename}{suffix} main restricted universe multiverse"
            )
    assert command_stub.source_paths[0] != command_stub.source_paths[1]
    assert command_stub.source_paths[1] == command_stub.source_paths[2]
    assert command_stub.list_paths[0] != command_stub.list_paths[1]
    assert command_stub.list_paths[1] == command_stub.list_paths[2]
    assert len(command_stub.cleanup) == 2
    assert all(not Path(path).exists() for path in command_stub.cleanup)
    assert not helper.missing_packages(helper.packages_for(profile, codename))
    assert not any("trusted=yes" in option or "AllowUnauthenticated=true" in option or
                   "AllowInsecureRepositories=true" in option or "Verify-Peer=false" in option
                   for command in command_stub.apt_calls for option in command)
    for command in command_stub.apt_calls:
        assert "APT::Get::AllowUnauthenticated=false" in command
        assert "Acquire::AllowInsecureRepositories=false" in command
        assert "Acquire::AllowDowngradeToInsecureRepositories=false" in command
        assert "Acquire::https::Verify-Peer=true" in command
        assert "Acquire::https::Verify-Host=true" in command


def test_canonical_install_failure_can_fall_back_to_tuna(command_stub):
    command_stub.mode = "official-install-failed"
    helper.install("desktop")
    assert len(command_stub.apt_calls) == 5
    assert len(command_stub.cleanup) == 2
    assert not helper.missing_packages(helper.packages_for("desktop", "noble"))


def test_tuna_success_reply_cannot_hide_missing_mandatory_library(command_stub):
    command_stub.mode = "tuna-missing"
    with pytest.raises(RuntimeError, match="dependencies are still missing: libegl1"):
        helper.install("desktop")
    assert "mirrors.tuna.tsinghua.edu.cn" in command_stub.sources[-1]
    assert len(command_stub.cleanup) == 2
    assert all(not Path(path).exists() for path in command_stub.cleanup)


@pytest.mark.parametrize("mode,message", [
    ("refresh-failed", "package-index refresh failed"),
    ("install-failed", "dependency installation failed"),
    ("missing", "dependencies are still missing: libegl1"),
])
def test_required_package_gate_fails_closed(command_stub, mode, message):
    command_stub.mode = mode
    with pytest.raises(RuntimeError, match=message):
        helper.install("database-security")
    assert len(command_stub.cleanup) == 2
    assert all(not Path(path).exists() for path in command_stub.cleanup)
    assert "mirrors.tuna.tsinghua.edu.cn" not in command_stub.sources[0]
    assert "mirrors.tuna.tsinghua.edu.cn" in command_stub.sources[-1]


def test_profiles_select_glib_for_the_actual_ubuntu_release():
    assert "libglib2.0-0" in helper.packages_for("database-security", "jammy")
    assert "libglib2.0-0t64" in helper.packages_for("desktop", "noble")
    assert "fonts-noto-cjk" in helper.packages_for("desktop", "noble")
    assert "redis-server" in helper.packages_for("database-security", "jammy")


@pytest.mark.parametrize("sources", [helper.official_sources, helper.tuna_sources])
def test_unknown_fallback_architecture_is_rejected(sources):
    with pytest.raises(RuntimeError, match="Unsupported native"):
        sources("noble", "riscv64")


def test_non_ubuntu_or_invalid_codename_is_rejected(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID=debian\nVERSION_CODENAME=bookworm\n')
    with pytest.raises(RuntimeError, match="identified Ubuntu"):
        helper.ubuntu_codename(release)
    release.write_text('ID=ubuntu\nVERSION_CODENAME="noble bad"\n')
    with pytest.raises(RuntimeError, match="identified Ubuntu"):
        helper.ubuntu_codename(release)
