"""Actual Windows APIs, only beneath a unique owned HKCU test root.

Never writes PremiumSoft or reads existing user Navicat settings. Running these
tests proves our native read-only mechanism, not Navicat18 format acceptance.
"""
from __future__ import annotations

from contextlib import suppress
import os
import uuid

import pytest

from opennavicat.navicat_profiles import _native_windows_report
from opennavicat.navicat_windows import NativeRegistry, read_windows_navicat_registry

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Owned HKCU integration requires native Windows")


@pytest.fixture
def owned_registry(tmp_path):
    import winreg
    root = r"Software\OpenNavicatTests\NavicatDiscovery-" + uuid.uuid4().hex
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, root, 0, winreg.KEY_READ | winreg.KEY_WRITE) as created:
        assert created
    def remove(relative):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative, 0, winreg.KEY_READ) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                    index += 1
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
        for child in children:
            remove(relative + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, relative)
    try:
        yield root
    finally:
        assert root.startswith(r"Software\OpenNavicatTests\NavicatDiscovery-")
        remove(root)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, root)


def test_real_hkcu_names_and_types_do_not_read_secrets_and_keep_profiles_blocked(owned_registry, monkeypatch):
    import winreg
    relative = owned_registry + r"\NavicatPG\Servers\Generated native profile"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, relative, 0, winreg.KEY_WRITE) as key:
        for name, value in (("Host", "generated.invalid"), ("UserName", "generated-user"),
                            ("Password", "GENERATED-SECRET"), ("SSHPrivateKey", "GENERATED-KEY"),
                            ("serverSettings", "GENERATED-OPAQUE")):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "Port", 0, winreg.REG_DWORD, 5432)
    registry = NativeRegistry(root=owned_registry)
    query = registry.query_value
    enumeration = registry.enumerate_value
    queried, metadata = [], []
    def query_value(handle, name, *args):
        queried.append(name)
        return query(handle, name, *args)
    def enum_value(*args):
        assert args[6] is None, "RegEnumValueW must not receive a value-data buffer"
        metadata.append(True)
        return enumeration(*args)
    monkeypatch.setattr(registry, "query_value", query_value)
    monkeypatch.setattr(registry, "enumerate_value", enum_value)
    monkeypatch.setattr(winreg, "EnumValue", lambda *_: pytest.fail("EnumValue would read secret data"))
    found = _native_windows_report(read_windows_navicat_registry(registry))
    assert len(found.profiles) == 1
    profile = found.profiles[0]
    assert profile.config.host == "generated.invalid" and profile.config.port == 5432
    assert profile.blocked and "windows_native_format" in profile.unsupported_fields
    assert metadata and set(queried) == {"Host", "Port", "UserName"}
    assert "GENERATED-SECRET" not in repr(found) and "generated.invalid" not in repr(found)
    assert profile.config.password == profile.config.ssh_password == profile.config.ssl_key_password == profile.config.ssh_key_passphrase == ""
    # The source is still intact. Read only the known non-secret fixture value.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative) as key:
        assert winreg.QueryValueEx(key, "Host")[0] == "generated.invalid"


def test_real_hkcu_configured_group_file_and_bound_native_queries(owned_registry, tmp_path):
    import json
    import winreg
    location = tmp_path.resolve() / "generated-profiles"
    location.mkdir()
    (location / "vgroup.json").write_text(json.dumps({"vgroups": [{"vgroup_name": "Generated group", "vgroup_type": "CONNECTION", "items": [
        {"name": "Generated", "server_type": "SQLITE", "type": "CONNECTION"}]}]}))
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, owned_registry + r"\NavicatPremium", 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "ProfilesPath", 0, winreg.REG_SZ, str(location))
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, owned_registry + r"\NavicatSQLITE\Servers\Generated", 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "DatabaseFile", 0, winreg.REG_SZ, str(tmp_path / "not-opened.sqlite"))
    registry = NativeRegistry(root=owned_registry)
    found = _native_windows_report(read_windows_navicat_registry(registry))
    assert len(found.profiles) == 1 and found.profiles[0].group_path == ("Generated group",)
    assert found.profiles[0].blocked and not (tmp_path / "not-opened.sqlite").exists()
    with registry.open(r"NavicatSQLITE\Servers\Generated", 64) as key:
        with pytest.raises(ValueError, match="allowlist"):
            key.query("Password", winreg.REG_SZ)
