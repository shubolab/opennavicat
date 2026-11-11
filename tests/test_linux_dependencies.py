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
            self.sources.append(Path(source).read_text())
            assert "Dir::Etc::sourceparts=-" in command
        if "update" in command:
            assert "APT::Update::Error-Mode=any" in command
            return SimpleNamespace(returncode=100 if self.mode == "refresh-failed" else 0)
        if self.mode in ("fallback", "refresh-failed", "install-failed") and not source:
            return SimpleNamespace(returncode=124)  # runner HTTP mirror timed out
        if self.mode == "install-failed" and source:
            return SimpleNamespace(returncode=100)
        packages = command[command.index("--no-install-recommends") + 1:]
        self.installed.update(package for package in packages
                              if self.mode != "missing" or package != "libegl1")
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


@pytest.mark.parametrize("mode,message", [
    ("refresh-failed", "package-index refresh failed"),
    ("install-failed", "dependency installation failed"),
    ("missing", "dependencies are still missing: libegl1"),
])
def test_required_package_gate_fails_closed(command_stub, mode, message):
    command_stub.mode = mode
    with pytest.raises(RuntimeError, match=message):
        helper.install("database-security")
    assert len(command_stub.cleanup) == 1
    assert not Path(command_stub.cleanup[0]).exists()


def test_profiles_select_glib_for_the_actual_ubuntu_release():
    assert "libglib2.0-0" in helper.packages_for("database-security", "jammy")
    assert "libglib2.0-0t64" in helper.packages_for("desktop", "noble")
    assert "fonts-noto-cjk" in helper.packages_for("desktop", "noble")
    assert "redis-server" in helper.packages_for("database-security", "jammy")


def test_unknown_fallback_architecture_is_rejected():
    with pytest.raises(RuntimeError, match="Unsupported native"):
        helper.official_sources("noble", "riscv64")


def test_non_ubuntu_or_invalid_codename_is_rejected(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID=debian\nVERSION_CODENAME=bookworm\n')
    with pytest.raises(RuntimeError, match="identified Ubuntu"):
        helper.ubuntu_codename(release)
    release.write_text('ID=ubuntu\nVERSION_CODENAME="noble bad"\n')
    with pytest.raises(RuntimeError, match="identified Ubuntu"):
        helper.ubuntu_codename(release)
