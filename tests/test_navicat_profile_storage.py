"""Synthetic, additive Navicat metadata persistence; no user profile sources."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.navicat_profiles import ImportedProfile, ImportIssue, NavicatImportReport
from opennavicat.storage import Store


def imported(source_id="navicat:synthetic-1", *, driver="postgresql", name="Synthetic", **changes):
    config_changes = changes.pop("config", {})
    config = ConnectionConfig("untrusted-source-config-id", name, driver=driver,
        host="synthetic.invalid", database="0" if driver == "redis" else "owned", ssl_mode="disable")
    config = replace(config, **config_changes)
    options = dict(source_path="/synthetic/Common/conn.plist", source_format="macos-plist",
                   group_paths=(("Work", "Development"), ("Secondary",)),
                   unsupported_fields=(), requires_secrets=("password",), blocked=False)
    options.update(changes)
    return ImportedProfile(source_id, config, **options)


def report(*profiles, **options):
    return NavicatImportReport(tuple(profiles), sources_found=1, platform="darwin", **options)


@pytest.fixture
def vault(monkeypatch):
    values, calls = {}, []
    def get(service, key):
        calls.append(("get", service, key)); return values.get((service, key))
    def put(service, key, value):
        calls.append(("put", service, key)); values[service, key] = value
    def delete(service, key):
        calls.append(("delete", service, key)); values.pop((service, key), None)
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=get, set_password=put, delete_password=delete))
    return values, calls


def test_sync_is_metadata_only_strips_all_secrets_and_does_not_mutate_report(tmp_path, vault, monkeypatch):
    store = Store(tmp_path)
    secrets = {field: "SYNTHETIC-DO-NOT-PERSIST-" + field for field in Store.SECRET_FIELDS}
    source = imported(config=secrets)
    before = deepcopy(source.config)
    def forbidden(*_a, **_k):
        pytest.fail("Metadata sync called a database, keychain or credential API")
    monkeypatch.setattr(store, "hydrate", forbidden)
    monkeypatch.setattr(store, "secret", forbidden)
    monkeypatch.setattr(store, "save_connection", forbidden)
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(DatabaseSession, "connect", forbidden)
    counts = store.sync_navicat_profiles(report(source))
    assert counts == dict(added=1, updated=0, unchanged=0, preserved=0, source_missing=0, blocked=0, total=1)
    assert source.config == before
    assert not vault[1] and not store.passwords and not store.secrets
    assert all(getattr(store.connections()[0], field) == "" for field in Store.SECRET_FIELDS)
    text = store.path.read_text()
    assert not any(value in text for value in secrets.values())
    assert store.state["remembered_passwords"] == [] and store.state["remembered_secrets"] == {}


def test_all_nine_families_groups_and_explicit_review_blocks_round_trip(tmp_path, vault):
    drivers = ("mysql", "postgresql", "sqlserver", "oracle", "sqlite", "mariadb", "mongodb", "redis", "snowflake")
    profiles = [imported("synthetic:" + driver, driver=driver, name="Shared name", blocked=driver == "sqlserver",
                         unsupported_fields=("authenticationmode",) if driver == "sqlserver" else ()) for driver in drivers]
    store = Store(tmp_path)
    counts = store.sync_navicat_profiles(report(*profiles))
    assert counts["added"] == 9 and counts["blocked"] == 4
    restarted = Store(tmp_path)
    by_driver = {config.driver: config for config in restarted.connections()}
    assert set(by_driver) == set(drivers)
    for driver, config in by_driver.items():
        metadata = restarted.state["navicat_imports"][config.id]
        assert metadata["source_id"] == "synthetic:" + driver
        assert metadata["provenance"] == "/synthetic/Common/conn.plist"
        assert metadata["source_format"] == "macos-plist" and metadata["platform"] == "darwin"
        assert metadata["group_paths"] == [["Work", "Development"], ["Secondary"]]
        assert metadata["requires_secrets"] == ["password"]
        assert metadata["blocked"] == (driver in {"oracle", "mongodb", "snowflake", "sqlserver"})
        assert not metadata["locally_edited"] and not metadata["source_missing"] and metadata["last_seen"]
        if driver in {"oracle", "mongodb", "snowflake"}:
            assert "driver" in metadata["unsupported_fields"]
    assert not vault[1]


def test_source_identity_uuid5_survives_restart_rename_endpoint_and_source_move(tmp_path, vault):
    first = imported()
    store = Store(tmp_path)
    store.sync_navicat_profiles(report(first))
    identifier = store.connections()[0].id
    assert uuid.UUID(identifier).version == 5
    counts = store.sync_navicat_profiles(report(first))
    assert counts["unchanged"] == 1 and len(store.connections()) == 1
    moved = replace(first, config=replace(first.config, name="Renamed", host="changed.invalid"),
                    source_path="/synthetic/new/Common/conn.plist", group_paths=(("Moved",),))
    restarted = Store(tmp_path)
    counts = restarted.sync_navicat_profiles(report(moved))
    assert counts["updated"] == 1
    assert restarted.connections()[0].id == identifier
    assert restarted.connections()[0].name == "Renamed" and restarted.connections()[0].host == "changed.invalid"
    assert restarted.state["navicat_imports"][identifier]["group_paths"] == [["Moved"]]
    independent = Store(tmp_path / "independent")
    independent.sync_navicat_profiles(report(moved))
    assert independent.connections()[0].id == identifier


def test_same_named_manual_connection_and_even_colliding_id_are_never_overwritten(tmp_path, vault):
    source = imported(name="Shared")
    probe = Store(tmp_path / "probe"); probe.sync_navicat_profiles(report(source))
    collision = probe.connections()[0].id
    store = Store(tmp_path / "real")
    manual = ConnectionConfig(collision, "Shared", driver="mysql", host="manual.invalid", password="manual-process-secret")
    store.save_connection(manual)
    before = deepcopy(store.state["connections"][0])
    caches, calls = deepcopy(store.passwords), list(vault[1])
    counts = store.sync_navicat_profiles(report(source))
    assert counts["added"] == 1 and len(store.connections()) == 2
    assert store.state["connections"][0] == before
    imported_id = next(c.id for c in store.connections() if c.host == "synthetic.invalid")
    assert imported_id != collision and imported_id in store.state["navicat_imports"]
    assert store.passwords == caches and vault[1] == calls
    assert store.sync_navicat_profiles(report(source))["unchanged"] == 1
    assert len(store.connections()) == 2


def test_local_review_clears_unknown_block_and_refresh_preserves_config_all_secrets_and_keychain_choices(tmp_path, vault):
    store = Store(tmp_path)
    source = imported(blocked=True, unsupported_fields=("unknownjsonproperties",),
                      requires_secrets=("password", "ssh_password", "ssh_key_passphrase", "ssl_key_password"))
    store.sync_navicat_profiles(report(source))
    config = replace(store.connections()[0], name="Local name", host="approved.invalid", username="local-user",
                     password="local-db-secret", ssh_password="local-ssh-secret",
                     ssh_key_passphrase="local-key-secret", ssl_key_password="local-tls-secret")
    store.save_connection(config, remember=True, remember_ssh=True, remember_ssh_key=True, remember_ssl=True)
    metadata = store.state["navicat_imports"][config.id]
    assert metadata["locally_edited"] and not metadata["blocked"] and metadata["unsupported_fields"] == []
    assert metadata["requires_secrets"] == [] and metadata["source_blocked"]
    before = deepcopy(store.state["connections"])
    cache, secrets, flags, key_flags = deepcopy(store.passwords), deepcopy(store.secrets), deepcopy(store.state["remembered_passwords"]), deepcopy(store.state["remembered_secrets"])
    values, calls = deepcopy(vault[0]), list(vault[1])
    changed = replace(source, config=replace(source.config, host="source-changed.invalid"), group_paths=(("Source moved",),))
    counts = store.sync_navicat_profiles(report(changed))
    assert counts["preserved"] == 1 and counts["updated"] == 0
    assert store.state["connections"] == before
    assert store.passwords == cache and store.secrets == secrets
    assert store.state["remembered_passwords"] == flags and store.state["remembered_secrets"] == key_flags
    assert vault[0] == values and vault[1] == calls
    metadata = store.state["navicat_imports"][config.id]
    assert not metadata["blocked"] and metadata["source_blocked"] and metadata["reviewed_at"]
    assert metadata["group_paths"] == [["Source moved"]]
    assert metadata["source_requires_secrets"] == ["password", "ssh_key_passphrase", "ssh_password", "ssl_key_password"]
    assert not any(secret in store.path.read_text() for secret in cache.values())
    restarted = Store(tmp_path)
    assert restarted.state["navicat_imports"][config.id]["locally_edited"]
    assert restarted.connections()[0].host == "approved.invalid"


def test_unsupported_driver_cannot_be_unblocked_merely_by_resaving_metadata(tmp_path, vault):
    store = Store(tmp_path)
    store.sync_navicat_profiles(report(imported(driver="oracle")))
    config = store.connections()[0]
    store.save_connection(config)
    assert store.state["navicat_imports"][config.id]["blocked"]
    assert store.state["navicat_imports"][config.id]["unsupported_fields"] == ["driver"]
    config.driver = "postgresql"
    store.save_connection(config)
    assert not store.state["navicat_imports"][config.id]["blocked"]
    store.sync_navicat_profiles(report(imported(driver="oracle")))
    assert store.connections()[0].driver == "postgresql" and not store.state["navicat_imports"][config.id]["blocked"]


def test_removed_source_marks_missing_without_deleting_connections_or_local_data(tmp_path, vault):
    store = Store(tmp_path)
    a, b = imported("synthetic:a"), imported("synthetic:b", name="Second")
    store.sync_navicat_profiles(report(a, b))
    ids = {item["source_id"]: identifier for identifier, item in store.state["navicat_imports"].items()}
    store.state["favorites"] = [{"name": "Local favorite", "connection_id": ids[b.source_id], "sql": "SELECT 1"}]
    store.flush()
    counts = store.sync_navicat_profiles(report(a))
    assert counts["source_missing"] == 1 and len(store.connections()) == 2
    assert store.state["navicat_imports"][ids[b.source_id]]["source_missing"]
    assert store.state["favorites"][0]["connection_id"] == ids[b.source_id]
    store.sync_navicat_profiles(report(a, b))
    assert not any(m["source_missing"] for m in store.state["navicat_imports"].values())
    store.sync_navicat_profiles(NavicatImportReport(platform="linux"))
    assert not any(m["source_missing"] for m in store.state["navicat_imports"].values())


@pytest.mark.parametrize("code", ["source_read_failed", "registry_read_failed", "profile_read_failed", "unsupported_source_structure", "windows_discovery_pending"])
def test_incomplete_discovery_does_not_claim_existing_sources_were_deleted(tmp_path, vault, code):
    store = Store(tmp_path); store.sync_navicat_profiles(report(imported()))
    counts = store.sync_navicat_profiles(report(issues=(ImportIssue(code, "Synthetic failure"),)))
    assert counts["source_missing"] == 0 and len(store.connections()) == 1


def test_atomic_batch_invalid_second_entry_leaves_manual_and_import_state_untouched(tmp_path, vault):
    store = Store(tmp_path)
    store.save_connection(ConnectionConfig("manual", "Manual", password="process-secret"))
    before, disk, passwords, secrets = deepcopy(store.state), store.path.read_bytes(), deepcopy(store.passwords), deepcopy(store.secrets)
    invalid = replace(imported("synthetic:bad"), config=object())
    with pytest.raises(ValueError, match="Invalid Navicat"):
        store.sync_navicat_profiles(report(imported(), invalid))
    assert store.state == before and store.path.read_bytes() == disk
    assert store.passwords == passwords and store.secrets == secrets


def test_failed_atomic_replace_rolls_back_batch_in_memory_and_keeps_prior_workspace(tmp_path, vault, monkeypatch):
    store = Store(tmp_path); store.save_connection(ConnectionConfig("manual", "Manual"))
    before, disk, calls = deepcopy(store.state), store.path.read_bytes(), list(vault[1])
    real_replace = Path.replace
    def fail_replace(path, target):
        if path == store.path.with_suffix(".tmp"):
            raise OSError("Synthetic disk replacement failure")
        return real_replace(path, target)
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="Synthetic disk"):
        store.sync_navicat_profiles(report(imported("synthetic:a"), imported("synthetic:b")))
    assert store.state == before and store.path.read_bytes() == disk and vault[1] == calls
    assert Store(tmp_path).connections()[0].id == "manual"
    monkeypatch.setattr(Path, "replace", real_replace)
    assert store.sync_navicat_profiles(report(imported()))["added"] == 1


def test_duplicate_report_identity_fails_entire_batch_without_side_effects(tmp_path, vault):
    store = Store(tmp_path)
    before = deepcopy(store.state)
    with pytest.raises(ValueError, match="Repeated Navicat"):
        store.sync_navicat_profiles(report(imported(), imported()))
    assert store.state == before and not store.path.exists()


def test_old_workspace_remains_compatible_and_store_never_discovers_local_sources(tmp_path, monkeypatch, vault):
    import opennavicat.navicat_profiles as profiles
    monkeypatch.setattr(profiles, "discover_navicat_profiles", lambda *_a, **_k: pytest.fail("Store must not auto-discover a user's sources"))
    path = tmp_path / "workspace.json"
    config = ConnectionConfig("manual-old", "Original")
    from dataclasses import asdict
    path.write_text(json.dumps({"connections": [asdict(config)], "favorites": [{"name": "keep"}]}))
    store = Store(tmp_path)
    assert store.connections() == [config] and store.state["navicat_imports"] == {}
    assert store.state["favorites"] == [{"name": "keep"}]
    assert not vault[1]


def test_import_never_grants_ssh_trust_and_refresh_only_preserves_approved_same_endpoint_pin(tmp_path, vault):
    store = Store(tmp_path)
    source = imported(config=dict(ssh_enabled=True, ssh_host="ssh.synthetic.invalid", ssh_port=22,
                                  ssh_username="test", ssh_host_key="untrusted-source-pin"))
    store.sync_navicat_profiles(report(source))
    config = store.connections()[0]
    assert config.ssh_host_key == ""
    # This mirrors the explicit first-use trust UI's public metadata update;
    # it does not represent editing or importing a private credential.
    store.state["connections"][0]["ssh_host_key"] = "ssh-ed25519 approved-synthetic-public-pin"
    store.flush()
    counts = store.sync_navicat_profiles(report(source))
    assert counts["unchanged"] == 1
    assert store.connections()[0].ssh_host_key == "ssh-ed25519 approved-synthetic-public-pin"
    moved = replace(source, config=replace(source.config, ssh_port=2222))
    store.sync_navicat_profiles(report(moved))
    assert store.connections()[0].ssh_host_key == ""
    assert not vault[1]
