"""Synthetic source contracts: no real registry, credentials or database I/O."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json
import plistlib

import pytest

from opennavicat.navicat_profiles import _native_windows_report, discover_navicat_profiles
from opennavicat.navicat_windows import (
    FAMILIES, PROFILE_FIELDS, REG_DWORD, REG_QWORD, REG_SZ, NativeRegistry,
    ValueInfo, read_windows_navicat_registry,
)
from opennavicat.storage import Store


class Key:
    def __init__(self, values=None, *, children=(), stamp=10, unreadable=(), changing=False, identity=""):
        self.values = values or {}
        self.children = children
        self.last_write = stamp
        self.unreadable = set(unreadable)
        self.changing = changing
        self.queries = []
        self.physical_identity = identity

    def subkeys(self):
        yield from self.children

    def stamp(self):
        return self.last_write

    def identity(self):
        return self.physical_identity

    def value_info(self):
        return {name: ValueInfo(kind, len(value.encode("utf-16-le")) + 2 if isinstance(value, str) else
                               len(value) if isinstance(value, bytes) else 8 if kind == REG_QWORD else 4)
                for name, (value, kind) in self.values.items()}

    def query(self, name, expected_kind):
        assert name.casefold() in PROFILE_FIELDS | {"profilespath"}, "A secret or opaque field was queried"
        self.queries.append(name)
        if name in self.unreadable:
            raise OSError("SYNTHETIC-PRIVATE-ERROR")
        if self.changing:
            self.last_write += 1
        value, kind = self.values[name]
        assert kind == expected_kind
        return value


class Registry:
    root = r"Software\OpenNavicatTests\Synthetic"
    def __init__(self):
        self.keys = {}
        self.errors = set()
        self.opened = []

    @contextmanager
    def open(self, path, view):
        self.opened.append((path, view))
        if (path, view) in self.errors:
            raise PermissionError("SYNTHETIC-PRIVATE-PATH")
        yield self.keys.get((path, view))

    def profile(self, family="NavicatPG", name="Generated connection", *, view=64, values=None, stamp=10, **options):
        parent = family + r"\Servers"
        previous = self.keys.get((parent, view))
        children = list(previous.children) if previous else []
        children.append(name)
        self.keys[parent, view] = Key(children=children)
        options.setdefault("identity", family + "\\" + name)
        key = Key(values or {"Host": ("generated.invalid", REG_SZ), "Port": (5432, REG_DWORD),
                             "UserName": ("generated-user", REG_SZ)}, stamp=stamp, **options)
        self.keys[parent + "\\" + name, view] = key
        return key


def report(registry):
    return _native_windows_report(read_windows_navicat_registry(registry))


def test_native_discovery_is_wired_without_reading_the_host_on_other_platforms(monkeypatch):
    import opennavicat.navicat_windows as module
    fake = Registry()
    fake.profile()
    monkeypatch.setattr(module, "read_windows_navicat_registry", lambda: read_windows_navicat_registry(fake))
    found = discover_navicat_profiles(platform="win32")
    assert len(found.profiles) == 1 and found.profiles[0].blocked
    assert "windows_native_settings_review" in {issue.code for issue in found.issues}


@pytest.mark.parametrize("family,driver", FAMILIES)
def test_every_static_servers_family_is_visible_but_requires_manual_review(family, driver):
    fake = Registry()
    fake.profile(family, values={"Host": ("generated.invalid", REG_SZ), "DatabaseFile": ("generated.sqlite", REG_SZ)})
    found = report(fake)
    assert len(found.profiles) == 1
    imported = found.profiles[0]
    assert imported.config.driver == driver and imported.config.name == "Generated connection"
    assert imported.blocked and "windows_native_format" in imported.unsupported_fields
    assert all(getattr(imported.config, field) == "" for field in Store.SECRET_FIELDS)
    if driver == "sqlite":
        assert imported.config.database == "generated.sqlite"
    else:
        assert imported.config.ssl_mode == "verify-full"
    assert not imported.config.ssh_enabled


def test_only_plain_allowlist_values_are_queried_never_password_key_or_blob():
    fake = Registry()
    key = fake.profile(values={
        "Host": ("generated.invalid", REG_SZ), "Port": (27017, REG_DWORD), "UserName": ("generated", REG_SZ),
        "Password": ("SECRET-PASSWORD", REG_SZ), "Pwd": (b"SECRET-CIPHER", 3),
        "SSHPrivateKey": ("SECRET-KEY", REG_SZ), "ssl_param": ("SECRET-TLS", REG_SZ),
        "serverSettings": ("SECRET-OPAQUE", REG_SZ), "UseSSH": (1, REG_DWORD), "UseSSL": (0, REG_DWORD),
    })
    before = deepcopy(key.values)
    found = report(fake)
    assert set(key.queries) == {"Host", "Port", "UserName"}
    assert key.values == before
    assert found.profiles[0].config.host == "generated.invalid" and found.profiles[0].config.port == 27017
    assert "SECRET" not in repr(found) and "generated.invalid" not in repr(found)
    assert "Generated connection" not in repr(found) and "OpenNavicatTests" not in repr(found)
    assert found.profiles[0].config.ssl_mode == "verify-full" and not found.profiles[0].config.ssh_enabled


def test_binary_or_expandable_metadata_is_not_read_or_coerced():
    fake = Registry()
    key = fake.profile(values={"Host": (b"PRIVATE-BINARY", 3), "Port": ("5432", REG_SZ),
                               "UserName": ("%PRIVATE_ENV%", 2)})
    imported = report(fake).profiles[0]
    assert key.queries == ["Port"]
    assert imported.config.host == imported.config.username == "" and imported.config.port == 0
    assert imported.blocked and {"host", "port", "username"} <= set(imported.unsupported_fields)


def test_same_source_in_two_views_is_deduplicated_with_stable_store_identity(tmp_path):
    fake = Registry()
    fake.profile(view=32)
    first = report(fake)
    initial = first.profiles[0].source_id
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(first)
    identifier = store.connections()[0].id
    fake.profile(view=64)
    found = report(fake)
    assert len(found.profiles) == 1 and found.sources_found == 1
    # The report's preferred visible view can change; its explicit positive
    # alias proof lets the Store retain the original identity and references.
    assert initial in found.profiles[0].source_aliases
    store.sync_navicat_profiles(found)
    assert [c.id for c in store.connections()] == [identifier]
    assert store.state["navicat_imports"][identifier]["source_id"] == initial
    assert not store.state["navicat_imports"][identifier]["source_missing"]


@pytest.mark.parametrize("view", [32, 64])
@pytest.mark.parametrize("initial_identity", ["", "positive-key-identity"])
def test_native_identity_api_availability_does_not_change_logical_view_identity(tmp_path, view, initial_identity):
    fake = Registry()
    key = fake.profile(view=view, identity=initial_identity)
    first = report(fake)
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(first)
    identifier = store.connections()[0].id
    key.physical_identity = "" if initial_identity else "positive-key-identity"
    second = report(fake)
    assert second.profiles[0].source_id == first.profiles[0].source_id
    store.sync_navicat_profiles(second)
    assert [c.id for c in store.connections()] == [identifier]
    assert not store.state["navicat_imports"][identifier]["source_missing"]


@pytest.mark.parametrize("field", ["Host", "UserName", "Port", "DefaultDatabase"])
def test_incomplete_native_snapshot_preserves_existing_known_config(tmp_path, field):
    fake = Registry()
    key = fake.profile(values={"Host": ("generated.invalid", REG_SZ), "UserName": ("generated", REG_SZ),
                               "Port": (5432, REG_DWORD), "DefaultDatabase": ("generated_db", REG_SZ)})
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(report(fake))
    before = deepcopy(store.connections()[0])
    key.unreadable = {field}
    key.values["Host"] = ("different-snapshot.invalid", REG_SZ)
    store.sync_navicat_profiles(report(fake))
    assert store.connections() == [before]
    assert not store.state["navicat_imports"][before.id]["source_missing"]
    # A complete later snapshot may authoritatively update the source again.
    key.unreadable.clear()
    store.sync_navicat_profiles(report(fake))
    assert store.connections()[0].host == "different-snapshot.invalid"


def test_first_partial_native_snapshot_is_visible_blocked_and_later_recovers(tmp_path):
    fake = Registry()
    key = fake.profile(unreadable={"Host"})
    partial = report(fake)
    assert not partial.profiles[0].config_complete
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(partial)
    local = store.connections()[0]
    assert local.host == "" and local.username == "generated-user"
    assert store.state["navicat_imports"][local.id]["blocked"]
    key.unreadable.clear()
    store.sync_navicat_profiles(report(fake))
    assert store.connections()[0].id == local.id and store.connections()[0].host == "generated.invalid"


@pytest.mark.parametrize("initial_view", [32, 64])
@pytest.mark.parametrize("loss", ["no_proof", "conflict"])
def test_lost_alias_proof_keeps_reviewed_source_and_other_view(tmp_path, monkeypatch, initial_view, loss):
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *_: None)
    fake = Registry()
    first_key = fake.profile(view=initial_view)
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(report(fake))
    local = store.connections()[0]
    local.host, local.password = "reviewed.invalid", "LOCAL-REVIEW-SECRET"
    store.save_connection(local)
    store.state["favorites"] = [{"connection_id": local.id, "sql": "SELECT 1"}]
    other_key = fake.profile(view=64 if initial_view == 32 else 32)
    assert len(report(fake).profiles) == 1
    counts = store.sync_navicat_profiles(report(fake))
    assert counts["added"] == 0 and [c.id for c in store.connections()] == [local.id]
    store = Store(tmp_path / "workspace")
    if loss == "no_proof":
        first_key.physical_identity = other_key.physical_identity = ""
    else:
        other_key.values["Host"] = ("conflicting-view.invalid", REG_SZ)
    split = report(fake)
    assert len(split.profiles) == 2
    counts = store.sync_navicat_profiles(split)
    assert counts["added"] == 1 and counts["source_missing"] == 0
    assert len(store.connections()) == 2
    reviewed = next(c for c in store.connections() if c.id == local.id)
    assert reviewed.host == "reviewed.invalid"
    assert not store.state["navicat_imports"][local.id]["blocked"]
    assert store.state["favorites"][0]["connection_id"] == local.id
    # Hydration is not invoked by discovery; the separate process-secret cache
    # was intentionally discarded on restart, while its review still persists.
    assert "LOCAL-REVIEW-SECRET" not in store.path.read_text()
    assert store.sync_navicat_profiles(split)["added"] == 0


def test_two_reviewed_sources_later_proven_alias_are_not_merged_or_duplicated(tmp_path, monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *_: None)
    fake = Registry()
    keys = [fake.profile(view=view, identity="") for view in (64, 32)]
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(report(fake))
    ids = []
    for index, local in enumerate(store.connections()):
        local.host = f"reviewed-{index}.invalid"
        local.password = f"LOCAL-ONLY-{index}"
        store.save_connection(local)
        ids.append(local.id)
    before = deepcopy(store.state["connections"])
    secrets = deepcopy(store.passwords)
    store.state["favorites"] = [{"connection_id": identifier, "sql": "SELECT 1"} for identifier in ids]
    for key in keys:
        key.physical_identity = "positive-common-identity"
    positive = report(fake)
    assert len(positive.profiles) == 1
    for _ in range(2):
        counts = store.sync_navicat_profiles(positive)
        assert counts["added"] == counts["source_missing"] == 0
        assert counts["preserved"] == 2 and counts["total"] == 2
        assert store.state["connections"] == before and store.passwords == secrets
        assert all(not store.state["navicat_imports"][identifier]["blocked"] for identifier in ids)
        assert all("windows_alias_review" in store.state["navicat_imports"][identifier]["source_unsupported_fields"] for identifier in ids)
        assert {row["connection_id"] for row in store.state["favorites"]} == set(ids)
    assert all(secret not in store.path.read_text() for secret in secrets.values())


def test_matching_metadata_never_proves_aliasing_of_unread_opaque_values():
    fake = Registry()
    first = fake.profile(view=64, identity="")
    second = fake.profile(view=32, identity="")
    first.values["serverSettings"] = ("AAAA", REG_SZ)
    second.values["serverSettings"] = ("BBBB", REG_SZ)
    found = report(fake)
    assert len(found.profiles) == 2 and len({p.source_id for p in found.profiles}) == 2
    assert all(p.blocked for p in found.profiles)
    assert all("serverSettings" not in key.queries for key in (first, second))


@pytest.mark.parametrize("difference", ["value", "opaque_type", "last_write"])
def test_view_conflict_never_drops_a_profile_or_claims_the_views_are_equal(difference):
    fake = Registry()
    first = fake.profile(view=64)
    second = fake.profile(view=32)
    if difference == "value":
        second.values["Host"] = ("second.invalid", REG_SZ)
    elif difference == "opaque_type":
        first.values["serverSettings"] = ("UNREAD", REG_SZ)
        second.values["serverSettings"] = (b"UNREAD", 3)
    else:
        second.last_write += 1
    found = report(fake)
    assert len(found.profiles) == 2 and len({p.source_id for p in found.profiles}) == 2
    assert all(p.blocked for p in found.profiles)
    assert "windows_registry_view_conflict" in {i.code for i in found.issues}
    assert all("serverSettings" not in key.queries for key in (first, second))


def test_unicode_profile_names_are_not_collapsed_by_python_casefold():
    fake = Registry()
    fake.profile(name="Straße")
    fake.profile(name="STRASSE")
    found = report(fake)
    assert len(found.profiles) == 2 and len({p.source_id for p in found.profiles}) == 2


def test_profile_name_cannot_collide_with_the_view_discriminator():
    fake = Registry()
    fake.profile(name="Generated", view=64)
    fake.profile(name="Generated", view=32, values={"Host": ("second.invalid", REG_SZ)})
    fake.profile(name="Generated#view=64", view=64)
    found = report(fake)
    assert len(found.profiles) == 3 and len({p.source_id for p in found.profiles}) == 3


def test_oversized_plain_value_is_not_queried(monkeypatch):
    import opennavicat.navicat_windows as module
    fake = Registry()
    key = fake.profile(values={"Host": ("PRIVATE-TOO-LARGE", REG_SZ)})
    monkeypatch.setattr(module, "_MAX_VALUE_BYTES", 4)
    found = report(fake)
    assert not key.queries and found.profiles[0].config.host == ""
    assert found.profiles[0].blocked


def test_unicode_spoofed_metadata_name_is_not_queried():
    fake = Registry()
    key = fake.profile(values={"Hoſt": ("not-a-static-field.invalid", REG_SZ)})
    found = report(fake)
    assert not key.queries and found.profiles[0].config.host == ""


def test_partial_family_and_record_failures_preserve_visible_records_and_redact_errors():
    fake = Registry()
    fake.profile(name="Visible", unreadable={"Host"})
    fake.profile(name="Disappeared")
    del fake.keys[r"NavicatPG\Servers\Disappeared", 64]
    fake.errors.add((r"Navicat\Servers", 64))
    found = report(fake)
    assert {p.config.name for p in found.profiles} == {"Visible", "Disappeared"}
    assert all(p.blocked for p in found.profiles)
    assert "registry_read_failed" in {i.code for i in found.issues}
    assert "PRIVATE" not in repr(found)


def test_changed_snapshot_cannot_be_merged_across_views():
    fake = Registry()
    fake.profile(view=64, changing=True)
    fake.profile(view=32)
    found = report(fake)
    assert len(found.profiles) == 2
    assert "registry_read_failed" in {i.code for i in found.issues}


def groups_file(tmp_path, *, name="Generated connection"):
    location = tmp_path.resolve() / "configured-profiles"
    location.mkdir()
    path = location / "vgroup.json"
    path.write_text(json.dumps({"vgroups": [{"vgroup_name": "Generated group", "vgroup_type": "CONNECTION", "items": [
        {"name": name, "server_type": "PGSQL", "type": "CONNECTION"}]}]}))
    return location


def test_only_recognized_configured_profiles_location_is_read(tmp_path, monkeypatch):
    location = groups_file(tmp_path)
    fake = Registry()
    fake.profile()
    fake.keys["NavicatPremium", 64] = Key({"ProfilesPath": (str(location), REG_SZ), "serverSettings": ("SECRET", REG_SZ)})
    import opennavicat.navicat_profiles as module
    read = []
    loader = module._load
    def load(path, kind):
        read.append(path)
        return loader(path, kind)
    monkeypatch.setattr(module, "_load", load)
    found = report(fake)
    assert read == [location / "vgroup.json"]
    assert found.profiles[0].group_path == ("Generated group",) and found.profiles[0].blocked
    assert fake.keys["NavicatPremium", 64].queries == ["ProfilesPath"]


def test_absent_location_does_not_search_filesystem(tmp_path, monkeypatch):
    groups_file(tmp_path)
    fake = Registry()
    fake.profile()
    import opennavicat.navicat_profiles as module
    monkeypatch.setattr(module, "_load", lambda *_: pytest.fail("An unconfigured group path was read"))
    found = report(fake)
    assert found.profiles[0].group_path == ()
    assert "windows_groups_location_unavailable" in {i.code for i in found.issues}


@pytest.mark.parametrize("location", [r"\\server.invalid\share", "relative", "%APPDATA%/Profiles"])
def test_unc_relative_or_environment_locations_are_not_followed(location, monkeypatch):
    fake = Registry()
    fake.profile()
    fake.keys["NavicatPremium", 64] = Key({"ProfilesPath": (location, REG_SZ)})
    import opennavicat.navicat_profiles as module
    monkeypatch.setattr(module, "_load", lambda *_: pytest.fail("Unrecognized path was read"))
    assert report(fake).profiles[0].group_path == ()


def test_conflicting_locations_or_memberships_are_left_unresolved(tmp_path):
    location = groups_file(tmp_path)
    fake = Registry()
    fake.profile(view=64)
    fake.profile(view=32, values={"Host": ("different.invalid", REG_SZ)})
    fake.keys["NavicatPremium", 64] = Key({"ProfilesPath": (str(location), REG_SZ)})
    found = report(fake)
    assert len(found.profiles) == 2 and all(not p.group_path for p in found.profiles)
    assert "ambiguous_group_members" in {i.code for i in found.issues}
    fake.keys["NavicatPremium", 32] = Key({"ProfilesPath": (str(location.parent), REG_SZ)})
    found = report(fake)
    assert all(not p.group_path for p in found.profiles)
    assert "windows_groups_location_review" in {i.code for i in found.issues}


def test_group_symlink_is_not_followed(tmp_path):
    location = tmp_path.resolve() / "profiles"
    location.mkdir()
    target = tmp_path / "unrelated.json"
    target.write_text('{"private":"must-not-be-read"}')
    try:
        (location / "vgroup.json").symlink_to(target)
    except OSError:
        pytest.skip("This platform does not permit creating a test symlink")
    fake = Registry()
    fake.profile()
    fake.keys["NavicatPremium", 64] = Key({"ProfilesPath": (str(location), REG_SZ)})
    found = report(fake)
    assert not found.profiles[0].group_path and "groups_read_failed" in {i.code for i in found.issues}


def test_store_preserves_explicitly_reviewed_local_edits_and_never_connects(tmp_path, monkeypatch):
    from opennavicat.database import DatabaseSession
    monkeypatch.setattr(DatabaseSession, "connect", lambda *_: pytest.fail("Discovery contacted a database"))
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *_: None)
    fake = Registry()
    key = fake.profile()
    store = Store(tmp_path / "workspace")
    found = report(fake)
    counts = store.sync_navicat_profiles(found)
    assert counts["added"] == counts["blocked"] == 1
    local = store.connections()[0]
    local.host = "explicitly-reviewed.invalid"
    local.password = "LOCAL-ONLY-SECRET"
    store.save_connection(local)
    assert not store.state["navicat_imports"][local.id]["blocked"]
    key.values["Host"] = ("source-changed.invalid", REG_SZ)
    store.sync_navicat_profiles(report(fake))
    assert store.connections()[0].host == "explicitly-reviewed.invalid"
    assert not store.state["navicat_imports"][local.id]["blocked"]
    assert store.passwords[local.id] == "LOCAL-ONLY-SECRET"
    assert "LOCAL-ONLY-SECRET" not in store.path.read_text()


def test_native_backend_is_not_initialized_on_non_windows():
    import os
    if os.name == "nt":
        pytest.skip("This assertion covers the non-Windows import boundary")
    with pytest.raises(OSError):
        NativeRegistry()


def file_or_registry_source(tmp_path, platform):
    if platform == "win32":
        fake = Registry()
        fake.profile()
        location = groups_file(tmp_path)
        fake.keys["NavicatPremium", 64] = Key({"ProfilesPath": (str(location), REG_SZ)})
        return lambda: report(fake), location / "vgroup.json"
    base = tmp_path.resolve() / "source"
    (base / "Common").mkdir(parents=True)
    row = {"connection_uuid": "generated-native-id", "host": "generated.invalid", "port": 5432,
           "username": "generated", "usessl": False, "usetunnel": False}
    payload = {"PostgreSQL": {"Generated connection": row}}
    path = base / "Common" / ("conn.plist" if platform == "darwin" else "connections.json")
    path.write_bytes(plistlib.dumps(payload)) if platform == "darwin" else path.write_text(json.dumps(payload))
    group = base / ("Navicat Premium" if platform == "darwin" else "Premium") / "Profiles/vgroup.json"
    group.parent.mkdir(parents=True)
    group.write_text(json.dumps({"vgroups": [{"vgroup_name": "Generated group", "vgroup_type": "CONNECTION", "items": [
        {"name": "Generated connection", "server_type": "PGSQL", "type": "CONNECTION"}]}]}))
    return lambda: discover_navicat_profiles(base, platform=platform), group


def damage_group_file(path, damage):
    if damage == "missing":
        path.unlink()
    elif damage == "invalid_json":
        path.write_text("{synthetic invalid")
    elif damage == "unknown_structure":
        path.write_text('{"unrecognized":[]}')
    else:
        path.write_text(json.dumps({"vgroups": [{"vgroup_name": "Must not replace the old group", "vgroup_type": "CONNECTION", "items": [
            {"name": "Generated connection", "server_type": "PGSQL", "type": "CONNECTION"}, None]}]}))


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("damage", ["missing", "invalid_json", "unknown_structure", "invalid_members"])
@pytest.mark.parametrize("edited", [False, True])
def test_group_read_or_structure_failure_keeps_last_known_good_for_all_sources(tmp_path, monkeypatch, platform, damage, edited):
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *_: None)
    read, group = file_or_registry_source(tmp_path, platform)
    store = Store(tmp_path / "workspace")
    initial = read()
    assert initial.profiles[0].groups_complete
    store.sync_navicat_profiles(initial)
    local = store.connections()[0]
    if edited:
        local.host = "reviewed.invalid"
        local.password = "LOCAL-SECRET"
        store.save_connection(local)
    assert store.state["navicat_imports"][local.id]["group_paths"] == [["Generated group"]]
    damage_group_file(group, damage)
    incomplete = read()
    assert len(incomplete.profiles) == 1 and not incomplete.profiles[0].groups_complete
    store.sync_navicat_profiles(incomplete)
    metadata = store.state["navicat_imports"][local.id]
    assert metadata["group_paths"] == [["Generated group"]] and metadata["groups_complete"] is False
    # Consecutive partial reads cannot turn retained history into an
    # unauthoritative first import and replace it with a different grouping.
    mixed_group_file(group, title="Different partial group")
    store.sync_navicat_profiles(read())
    metadata = store.state["navicat_imports"][local.id]
    assert metadata["group_paths"] == [["Generated group"]]
    if edited:
        assert store.connections()[0].host == "reviewed.invalid" and not metadata["blocked"]
        assert store.passwords[local.id] == "LOCAL-SECRET"
        assert "LOCAL-SECRET" not in store.path.read_text()


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_first_import_is_visible_despite_unavailable_groups(tmp_path, platform):
    read, group = file_or_registry_source(tmp_path, platform)
    damage_group_file(group, "unknown_structure")
    store = Store(tmp_path / "workspace")
    counts = store.sync_navicat_profiles(read())
    assert counts["added"] == 1 and len(store.connections()) == 1
    assert store.state["navicat_imports"][store.connections()[0].id]["group_paths"] == []


def mixed_group_file(path, unsupported="member", title="Generated group"):
    valid = {"vgroup_name": title, "vgroup_type": "CONNECTION", "items": [
        {"name": "Generated connection", "server_type": "PGSQL", "type": "CONNECTION"}]}
    groups = [valid]
    if unsupported == "member":
        valid["items"].append(None)
    else:
        groups.append({"vgroup_name": "Unrecognized group", "vgroup_type": "UNSUPPORTED", "items": []})
    path.write_text(json.dumps({"vgroups": groups}))


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("unsupported", ["member", "group_type"])
def test_first_import_keeps_valid_memberships_from_incomplete_groups(tmp_path, platform, unsupported):
    read, group = file_or_registry_source(tmp_path, platform)
    mixed_group_file(group, unsupported)
    imported = read()
    assert not imported.profiles[0].groups_complete
    assert imported.profiles[0].group_paths == (("Generated group",),)
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(imported)
    metadata = store.state["navicat_imports"][store.connections()[0].id]
    assert metadata["group_paths"] == [["Generated group"]]


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_partial_memberships_accumulate_until_complete_source_is_available(tmp_path, platform):
    read, group = file_or_registry_source(tmp_path, platform)
    damage_group_file(group, "unknown_structure")
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(read())
    identifier = store.connections()[0].id
    mixed_group_file(group)
    store.sync_navicat_profiles(read())
    assert store.state["navicat_imports"][identifier]["group_paths"] == [["Generated group"]]
    store = Store(tmp_path / "workspace")
    mixed_group_file(group, title="Another known group")
    store.sync_navicat_profiles(read())
    assert store.state["navicat_imports"][identifier]["group_paths"] == [["Generated group"], ["Another known group"]]
    damage_group_file(group, "unknown_structure")
    store.sync_navicat_profiles(read())
    assert store.state["navicat_imports"][identifier]["group_paths"] == [["Generated group"], ["Another known group"]]
    # A fully valid empty source is authoritative, even after partial imports.
    group.write_text('{"vgroups":[]}')
    store.sync_navicat_profiles(read())
    assert store.state["navicat_imports"][identifier]["group_paths"] == []
    store = Store(tmp_path / "workspace")
    mixed_group_file(group)
    store.sync_navicat_profiles(read())
    assert store.state["navicat_imports"][identifier]["group_paths"] == []


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_valid_empty_group_source_clears_memberships_without_reverting_local_review(tmp_path, monkeypatch, platform):
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *_: None)
    read, group = file_or_registry_source(tmp_path, platform)
    store = Store(tmp_path / "workspace")
    store.sync_navicat_profiles(read())
    local = store.connections()[0]
    local.host, local.password = "reviewed.invalid", "LOCAL-SECRET"
    store.save_connection(local)
    group.write_text('{"vgroups":[]}')
    empty = read()
    assert empty.profiles[0].groups_complete and not empty.profiles[0].group_paths
    store.sync_navicat_profiles(empty)
    metadata = store.state["navicat_imports"][local.id]
    assert metadata["group_paths"] == [] and metadata["groups_complete"]
    assert not metadata["blocked"] and store.connections()[0].host == "reviewed.invalid"
    assert store.passwords[local.id] == "LOCAL-SECRET"
