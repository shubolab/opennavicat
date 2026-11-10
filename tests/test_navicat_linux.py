"""Synthetic Linux discovery boundaries; never read the host's Navicat data."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from opennavicat import navicat_linux as native
from opennavicat import navicat_profiles as profiles
from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.storage import Store


POSIX_READER = (
    os.name == "posix" and os.open in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
)
requires_posix = pytest.mark.skipif(not POSIX_READER, reason="Native nofollow/dirfd inspection requires POSIX")


@pytest.fixture
def owned_home(tmp_path, monkeypatch):
    # Resolve only this generated fixture: macOS /tmp and /var may be links,
    # while the product must reject links in an actual source path.
    home = tmp_path.resolve() / "generated-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def source(home, payload=b'{"password":"SYNTHETIC-DO-NOT-READ"}'):
    path = home / ".config/navicat/Common/connections.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return native.LinuxSourceCandidate("synthetic", path)


def forbidden(*_args, **_kwargs):
    pytest.fail("Discovery crossed its bounded metadata/prefix scope")


def test_candidate_roots_are_exact_nonrecursive_and_do_not_touch_files(owned_home, monkeypatch):
    xdg = owned_home.parent / "generated-xdg"
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    candidates, issues = native.linux_source_candidates(home=owned_home, environ={"XDG_CONFIG_HOME": str(xdg)})
    assert not issues
    assert [(item.namespace, item.path) for item in candidates] == [
        ("native-default", owned_home / ".config/navicat/Common/connections.json"),
        ("native-xdg", xdg / "navicat/Common/connections.json"),
        ("flatpak:com.navicat.premium.en", owned_home / ".var/app/com.navicat.premium.en/config/navicat/Common/connections.json"),
    ]
    assert all("generated-home" not in repr(item) for item in candidates)


def test_duplicate_exact_path_and_source_identity_do_not_depend_on_route(owned_home):
    candidates, issues = native.linux_source_candidates(home=owned_home, environ={"XDG_CONFIG_HOME": str(owned_home / ".config")})
    assert not issues and len(candidates) == 2
    first = candidates[0]
    assert native.LinuxSourceCandidate("different-route", first.path).source_id == first.source_id
    assert native.LinuxSourceCandidate(first.namespace, first.path.parent / "other.json").source_id != first.source_id
    again, _ = native.linux_source_candidates(home=owned_home, environ={})
    assert [item.source_id for item in candidates] == [item.source_id for item in again]


@pytest.mark.parametrize("setting", ["relative/config", "/generated/../outside", "/generated/with\0nul", "//server/share", 123])
def test_invalid_xdg_is_reported_without_expansion_or_extra_candidates(owned_home, setting):
    candidates, issues = native.linux_source_candidates(home=owned_home, environ={"XDG_CONFIG_HOME": setting})
    assert len(candidates) == 2
    assert {issue.code for issue in issues} == {"linux_config_location_review"}
    assert all(item.path.is_relative_to(owned_home) for item in candidates)


@pytest.mark.parametrize("home", ["relative", "/generated/../outside", "/generated/with\0nul", "//server/share", 123])
def test_invalid_home_cannot_authorize_an_xdg_scan(home):
    candidates, issues = native.linux_source_candidates(home=home, environ={"XDG_CONFIG_HOME": "/otherwise-valid"})
    assert not candidates and {issue.code for issue in issues} == {"linux_config_location_review"}


@requires_posix
@pytest.mark.parametrize("prefix,kind", [
    (b"{", "object"), (b"[", "array"), (b" \t\r\n{", "object"),
    (b"\xef\xbb\xbf \n[", "array"), (b'"', "other"), (b"\xefxx", "other"),
])
def test_prefix_read_stops_before_any_json_key_or_secret_value(owned_home, monkeypatch, prefix, kind):
    candidate = source(owned_home, prefix + b'"password":"SYNTHETIC-DO-NOT-READ"}')
    original = os.read
    chunks, requests = [], []
    def read(fd, size):
        requests.append(size)
        chunk = original(fd, size)
        chunks.append(chunk)
        return chunk
    monkeypatch.setattr(os, "read", read)
    inspected = native.inspect_linux_source(candidate)
    assert inspected.state == "requires-review" and inspected.exists is True
    assert inspected.container_prefix == kind and inspected.bytes_read == len(prefix)
    assert b"".join(chunks) == prefix and set(requests) <= {1, 2}
    assert b"password" not in b"".join(chunks) and "SYNTHETIC" not in repr(inspected)


@requires_posix
@pytest.mark.parametrize("payload,kind,consumed", [(b"", "empty", 0), (b"\xef\xbb", "other", 2), (b" \n", "empty", 2)])
def test_empty_and_truncated_prefixes_are_review_only(owned_home, payload, kind, consumed):
    inspected = native.inspect_linux_source(source(owned_home, payload))
    assert inspected.state == "requires-review" and inspected.container_prefix == kind
    assert inspected.bytes_read == consumed and inspected.exists is True


@requires_posix
@pytest.mark.parametrize("spaces,expected", [(31, "requires-review"), (32, "unreadable")])
def test_whitespace_prefix_budget_never_reads_past_the_bound(owned_home, monkeypatch, spaces, expected):
    candidate = source(owned_home, b" " * spaces + b'{"password":"UNREAD"}')
    monkeypatch.setattr(native, "_MAX_PREFIX", 32)
    chunks = []
    original = os.read
    def read(fd, size):
        data = original(fd, size);chunks.append(data);return data
    monkeypatch.setattr(os, "read", read)
    inspected = native.inspect_linux_source(candidate)
    assert inspected.state == expected
    assert len(b"".join(chunks)) <= 32 and b"password" not in b"".join(chunks)


@requires_posix
def test_only_known_files_are_inspected_and_unknown_flatpak_ids_are_ignored(owned_home, monkeypatch):
    valid = source(owned_home)
    for relative in (
        ".config/navicat/Common/ui_connections.json", ".config/navicat/Common/connections.log",
        ".config/navicat/nested/Common/connections.json",
        ".var/app/com.navicat.premium.zh/config/navicat/Common/connections.json",
        ".wine/drive_c/Navicat/Common/connections.json",
    ):
        path = owned_home / relative;path.parent.mkdir(parents=True, exist_ok=True);path.write_text("UNREAD")
    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    monkeypatch.setattr(os, "walk", forbidden)
    found = native.discover_linux_sources(home=owned_home, environ={})
    assert found.sources_found == 1
    assert [item.candidate.path for item in found.inspections if item.exists] == [valid.path]
    assert {issue.code for issue in found.issues} == {"linux_native_format_review"}


@requires_posix
@pytest.mark.parametrize("component", ["final", "parent"])
def test_final_and_parent_symlinks_are_never_followed(owned_home, monkeypatch, component):
    candidate = source(owned_home)
    unrelated = owned_home.parent / "unrelated";unrelated.mkdir()
    (unrelated / "connections.json").write_text('{"password":"UNREAD-OUTSIDE"}')
    if component == "final":
        candidate.path.unlink();candidate.path.symlink_to(unrelated / "connections.json")
    else:
        candidate.path.unlink();candidate.path.parent.rmdir();candidate.path.parent.symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setattr(native, "_container_prefix", forbidden)
    inspected = native.inspect_linux_source(candidate)
    assert inspected.state == "unreadable" and inspected.container_prefix == "unknown"
    found = native.discover_linux_sources(home=owned_home, environ={})
    assert "source_read_failed" in {issue.code for issue in found.issues}


@requires_posix
@pytest.mark.parametrize("kind", ["directory", "fifo", "oversize"])
def test_nonregular_or_oversize_file_is_rejected_before_any_read(owned_home, monkeypatch, kind):
    candidate = source(owned_home);candidate.path.unlink()
    if kind == "directory":candidate.path.mkdir()
    elif kind == "fifo":os.mkfifo(candidate.path)
    else:
        with candidate.path.open("wb") as handle:handle.truncate(native._MAX_SOURCE + 1)
    monkeypatch.setattr(native, "_container_prefix", forbidden)
    inspected = native.inspect_linux_source(candidate)
    assert inspected.state == "unreadable" and inspected.exists is True and inspected.bytes_read == 0


@requires_posix
def test_real_permission_failure_is_unreadable_not_an_empty_source(owned_home):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("Root bypasses POSIX read permission checks")
    candidate = source(owned_home);candidate.path.chmod(0)
    try:
        inspected = native.inspect_linux_source(candidate)
        assert inspected.state == "unreadable" and inspected.exists is True
    finally:candidate.path.chmod(0o600)


@requires_posix
@pytest.mark.parametrize("race", ["rewrite", "replace", "delete", "parent_replace"])
def test_file_and_parent_races_cannot_become_a_valid_or_empty_source(owned_home, monkeypatch, race):
    candidate = source(owned_home)
    original = native._container_prefix
    def changed(fd):
        result = original(fd)
        if race == "rewrite":candidate.path.write_bytes(b'{"different":"longer-generated-content"}')
        elif race == "replace":
            replacement = candidate.path.with_suffix(".new");replacement.write_bytes(b"{}");replacement.replace(candidate.path)
        elif race == "delete":candidate.path.unlink()
        else:
            parent = candidate.path.parent
            parent.rename(parent.with_name("Common-before"));parent.mkdir();candidate.path.write_bytes(b"{}")
        return result
    monkeypatch.setattr(native, "_container_prefix", changed)
    inspected = native.inspect_linux_source(candidate)
    assert inspected.state == "unreadable" and inspected.exists is True


@requires_posix
@pytest.mark.parametrize("replacement", ["regular", "symlink"])
def test_stat_to_open_replacement_is_rejected_before_prefix_read(owned_home, monkeypatch, replacement):
    candidate = source(owned_home)
    prepared = candidate.path.with_suffix(".replacement")
    prepared.write_bytes(b'{"password":"UNREAD-REPLACEMENT"}')
    original_stat = os.stat
    changed = False
    def stat_then_replace(path, *args, **kwargs):
        nonlocal changed
        info = original_stat(path, *args, **kwargs)
        if not changed and path == candidate.path.name and kwargs.get("dir_fd") is not None:
            changed = True
            if replacement == "regular":prepared.replace(candidate.path)
            else:
                candidate.path.unlink();candidate.path.symlink_to(prepared)
        return info
    monkeypatch.setattr(os, "stat", stat_then_replace)
    monkeypatch.setattr(native, "_container_prefix", forbidden)
    inspected = native.inspect_linux_source(candidate)
    assert changed and inspected.state == "unreadable" and inspected.exists is True


@requires_posix
def test_default_linux_route_never_parses_values_or_uses_macos_conversion(owned_home, monkeypatch):
    candidate = source(owned_home, b'{"PostgreSQL":{"looks-like-Mac":{"password":"UNREAD"}}}')
    monkeypatch.setattr(profiles, "_convert", forbidden)
    monkeypatch.setattr(profiles, "_records", forbidden)
    monkeypatch.setattr(profiles, "_load", forbidden)
    found = profiles.discover_navicat_profiles(platform="linux")
    assert not found.profiles and found.sources_found == 1
    assert {issue.code for issue in found.issues} == {"linux_native_format_review", "unsupported_source_structure"}
    assert all(issue.blocking for issue in found.issues)
    assert "UNREAD" not in repr(found) and str(candidate.path) not in repr(found)


@requires_posix
@pytest.mark.parametrize("failure", ["unknown", "unreadable"])
def test_unknown_or_failed_native_source_preserves_stored_config_groups_and_review(owned_home, tmp_path, monkeypatch, failure):
    candidate = source(owned_home)
    store = Store(tmp_path / "generated-workspace")
    config = ConnectionConfig("generated", "Generated connection", driver="postgresql", host="known.invalid", port=5432)
    imported = profiles.ImportedProfile("synthetic-known-source", config, ("Known group",), "synthetic-source",
        "linux-native", (), (), False, (("Known group",),), True, True)
    store.sync_navicat_profiles(profiles.NavicatImportReport((imported,), platform="linux"))
    identifier = store.connections()[0].id
    store.state["navicat_imports"][identifier].update(locally_edited=True, reviewed_at="synthetic-review", blocked=False)
    before = deepcopy(store.state)
    if failure == "unreadable":
        candidate.path.unlink();candidate.path.mkdir()
    monkeypatch.setattr(DatabaseSession, "connect", forbidden)
    monkeypatch.setattr(store, "hydrate", forbidden)
    monkeypatch.setattr(store, "secret", forbidden)
    monkeypatch.setattr(store, "save_connection", forbidden)
    found = profiles.discover_navicat_profiles(platform="linux")
    store.sync_navicat_profiles(found)
    assert store.state["connections"] == before["connections"]
    assert store.state["navicat_imports"] == before["navicat_imports"]
    assert store.state["navicat_imports"][identifier]["group_paths"] == [["Known group"]]
    assert not store.state["navicat_imports"][identifier]["source_missing"]


def test_explicit_legacy_linux_root_remains_blocked_and_cannot_replace_known_config(tmp_path):
    path = tmp_path / "legacy/Common/connections.json";path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"PostgreSQL":{"Generated":{
        "connection_uuid":"synthetic-legacy-id","host":"source.invalid","port":5432,
        "username":"generated","password":"SYNTHETIC-SECRET","usessl":False,"usetunnel":False,
    }}}))
    legacy = profiles.discover_navicat_profiles(path, platform="linux")
    assert len(legacy.profiles) == 1
    item = legacy.profiles[0]
    assert item.source_format == "linux-legacy-json" and item.blocked and not item.config_complete
    assert "linux_native_format" in item.unsupported_fields and item.config.password == ""
    store = Store(tmp_path / "generated-workspace")
    known = replace(item, config=replace(item.config,host="known.invalid"), blocked=False, config_complete=True,
                    group_path=("Known group",), group_paths=(("Known group",),), groups_complete=True)
    store.sync_navicat_profiles(profiles.NavicatImportReport((known,),platform="linux"))
    before = deepcopy(store.state)
    store.sync_navicat_profiles(legacy)
    assert store.state["connections"] == before["connections"]
    identifier = store.connections()[0].id
    assert store.state["navicat_imports"][identifier]["group_paths"] == [["Known group"]]
    assert "SYNTHETIC-SECRET" not in store.path.read_text()
