"""Owned workspace tests for SQLite database metadata and memory-only secrets."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import (
    MAX_SQLITE_DATABASES, SQLiteDatabaseEntry, connection_from_metadata,
    connection_metadata, decode_sqlite_databases, encode_sqlite_databases, with_sqlite_database_password,
)
from opennavicat.storage import Store


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=lambda *args: calls.append(("set", args)),
        get_password=lambda *args: None,
        delete_password=lambda *args: calls.append(("delete", args)),
    ))
    return calls


def entry(identifier="aux-id", name='辅助 "数据库"', database="generated-aux.sqlite", encrypted=True):
    return SQLiteDatabaseEntry(identifier, name, database, encrypted)


def config(*rows, passwords=None, identifier="owned-profile"):
    return ConnectionConfig(identifier, "Generated owned profile", database="generated-main.sqlite",
                            sqlite_databases=rows, sqlite_database_passwords=passwords or {})


def test_legacy_profile_has_empty_independent_metadata_and_no_disk_secrets(tmp_path):
    (tmp_path / "workspace.json").write_text(json.dumps({"connections": [
        {"id": "legacy", "name": "Legacy", "database": "old.sqlite",
         "sqlite_database_passwords": {"aux-id": "injected-persisted-password"}},
    ]}))
    store = Store(tmp_path)
    old = store.connections()[0]
    assert old.sqlite_databases == () and old.sqlite_database_passwords == {}
    assert store.hydrate(old).sqlite_database_passwords == {}
    assert "injected-persisted-password" not in repr(store.state)
    store.flush()
    assert "injected-persisted-password" not in store.path.read_text()


def test_metadata_roundtrip_keeps_order_values_and_independent_frozen_entries():
    source = [{"id": "second", "name": "", "database": "", "encrypted": False},
              {"id": "first", "name": '名"字', "database": "relative/../aux.sqlite", "encrypted": True}]
    decoded = decode_sqlite_databases(source)
    assert tuple(row.id for row in decoded) == ("second", "first")
    assert encode_sqlite_databases(decoded) == source
    source[0]["name"] = "mutated"
    assert decoded[0].name == ""  # No guessed alias/path execution validation.
    with pytest.raises(FrozenInstanceError):
        decoded[0].name = "not allowed"
    copied = decode_sqlite_databases(decoded)
    assert copied == decoded and copied is not decoded and copied[0] is not decoded[0]


@pytest.mark.parametrize("value", [
    None, {}, "not a list", [None], [{"id": "one", "name": "name"}],
    [{"id": "one", "name": "name", "database": "file", "encrypted": 1}],
    [{"id": "one", "name": "name", "database": "file", "password": "private-nested"}],
    [{"id": "one", "name": "name", "database": "file", "future_security": "private-nested"}],
    [entry(), entry()], [entry(str(index)) for index in range(MAX_SQLITE_DATABASES + 1)],
])
def test_malformed_metadata_fails_closed_without_echoing_values(value):
    with pytest.raises(ValueError) as captured:
        decode_sqlite_databases(value)
    assert "private-nested" not in str(captured.value) and "private-nested" not in repr(captured.value)


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "id\0invalid"), ("id", "i" * 129),
    ("name", "n" * 4097), ("database", "f" * 32769),
    ("encrypted", "false"), ("name", 7),
])
def test_structural_metadata_bounds_do_not_coerce_values(field, value):
    row = dict(id="row", name="name", database="file", encrypted=False)
    row[field] = value
    with pytest.raises(ValueError):
        decode_sqlite_databases([row])


def test_config_copies_input_metadata_and_runtime_map_and_omits_secret_from_repr():
    rows = [dict(id="one", name="original", database="file", encrypted=True)]
    passwords = {"one": "owned-runtime-secret", "removed-id": "stale-runtime-secret"}
    value = ConnectionConfig("id", "name", sqlite_databases=rows, sqlite_database_passwords=passwords)
    rows[0]["name"] = "mutated"
    passwords["one"] = "mutated"
    assert value.sqlite_databases[0].name == "original"
    assert value.sqlite_database_passwords == {"one": "owned-runtime-secret"}
    assert "owned-runtime-secret" not in repr(value)
    metadata = connection_metadata(value)
    assert metadata["sqlite_database_passwords"] == {}
    assert "owned-runtime-secret" not in json.dumps(metadata)
    metadata["sqlite_database_passwords"] = {"one": "untrusted-persisted-secret"}
    restored = connection_from_metadata(metadata)
    assert restored.sqlite_database_passwords == {} and restored.sqlite_databases == value.sqlite_databases


def test_store_roundtrip_is_metadata_only_and_passwords_are_process_only(tmp_path, no_real_keyring):
    value = config(entry(), passwords={"aux-id": "only-in-this-process"})
    store = Store(tmp_path)
    returned = store.save_connection(value, remember=True, remember_ssh=True, remember_ssl=True)
    assert returned is not value and returned.sqlite_database_passwords is not value.sqlite_database_passwords
    assert store.connections()[0].sqlite_databases == value.sqlite_databases
    assert store.connections()[0].sqlite_database_passwords == {}
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"aux-id": "only-in-this-process"}
    assert "only-in-this-process" not in repr(store._sqlite_database_passwords)
    returned.sqlite_database_passwords["aux-id"] = "changed-return"
    value.sqlite_database_passwords["aux-id"] = "changed-input"
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords["aux-id"] == "only-in-this-process"
    restarted = Store(tmp_path)
    assert restarted.hydrate(restarted.connections()[0]).sqlite_database_passwords == {}
    assert not [call for call in no_real_keyring if call[0] == "set"]
    for path in tmp_path.iterdir():
        assert b"only-in-this-process" not in path.read_bytes()


def test_two_readers_and_hydrate_cannot_mutate_store_or_each_other(tmp_path):
    store = Store(tmp_path)
    value = config(entry(), passwords={"aux-id": "memory-secret"})
    value.oracle_custom_database_list = [{"active": True, "name": "retained", "auto_open": False}]
    store.save_connection(value)
    first, second = store.connections()[0], store.connections()[0]
    # Even bypassing frozen assignment cannot reach another reader's objects.
    object.__setattr__(first.sqlite_databases[0], "name", "private reader change")
    first.oracle_custom_database_list[0]["name"] = "private oracle change"
    assert second.sqlite_databases[0].name == entry().name
    assert store.connections()[0].oracle_custom_database_list[0]["name"] == "retained"
    hydrated = store.hydrate(second)
    hydrated.sqlite_database_passwords["aux-id"] = "not-store-secret"
    hydrated.oracle_custom_database_list[0]["name"] = "changed-hydrate"
    assert second.sqlite_database_passwords == {} and second.oracle_custom_database_list[0]["name"] == "retained"
    assert store.hydrate(second).sqlite_database_passwords["aux-id"] == "memory-secret"


def test_cache_uses_connection_and_stable_row_identity_not_alias_file_or_position(tmp_path):
    store = Store(tmp_path)
    first, second = entry("one", "same", "same.sqlite"), entry("two", "same", "same.sqlite")
    store.save_connection(config(first, second, passwords={"one": "secret-one", "two": "secret-two"}))
    store.save_connection(config(first, identifier="other-profile", passwords={"one": "other-secret"}))
    current = next(row for row in store.connections() if row.id == "owned-profile")
    renamed = replace(current, sqlite_databases=(second, replace(first, name="renamed")))
    store.save_connection(renamed)
    assert store.hydrate(renamed).sqlite_database_passwords == {"two": "secret-two", "one": "secret-one"}
    other = next(row for row in store.connections() if row.id == "other-profile")
    assert store.hydrate(other).sqlite_database_passwords == {"one": "other-secret"}


@pytest.mark.parametrize("changed", ["file", "encryption", "removed", "explicit-empty"])
def test_changed_binding_or_removed_row_cannot_resurrect_old_cached_password(tmp_path, changed):
    store = Store(tmp_path)
    store.save_connection(config(entry(), passwords={"aux-id": "old-bound-secret"}))
    current = store.connections()[0]
    if changed == "file":
        current.sqlite_databases = (replace(current.sqlite_databases[0], database="replacement.sqlite"),)
    elif changed == "encryption":
        current.sqlite_databases = (replace(current.sqlite_databases[0], encrypted=False),)
    elif changed == "removed":
        current.sqlite_databases = ()
    else:
        current = with_sqlite_database_password(current, "aux-id", "")
    if changed != "explicit-empty":
        assert store.hydrate(current).sqlite_database_passwords == {}
    store.save_connection(current)
    assert not store._sqlite_database_passwords
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {}
    # Restoring the prior metadata after a successful change cannot resurrect it.
    store.save_connection(config(entry()))
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {}


@pytest.mark.parametrize("phase", ["before-flush", "replace"])
def test_failed_save_keeps_old_durable_metadata_state_and_process_secret(tmp_path, monkeypatch, phase):
    store = Store(tmp_path)
    store.save_connection(config(entry(), passwords={"aux-id": "old-memory-secret"}))
    old_file, old_state = store.path.read_bytes(), deepcopy(store.state)
    old_cache = store._sqlite_database_passwords.copy()
    candidate = config(entry(name="uncommitted"), passwords={"aux-id": "new-uncommitted-secret"})
    if phase == "before-flush":
        monkeypatch.setattr(store, "flush", lambda: (_ for _ in ()).throw(OSError("controlled flush failure")))
    else:
        original = Path.replace
        def fail_replace(source, destination):
            if source == store.path.with_suffix(".tmp"):
                assert b"new-uncommitted-secret" not in source.read_bytes()
                raise OSError("controlled replace failure")
            return original(source, destination)
        monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="controlled"):
        store.save_connection(candidate)
    assert store.path.read_bytes() == old_file and store.state == old_state
    assert store._sqlite_database_passwords == old_cache
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"aux-id": "old-memory-secret"}
    assert all(b"new-uncommitted-secret" not in path.read_bytes() for path in tmp_path.iterdir())


def test_nested_password_injection_is_rejected_before_any_workspace_write(tmp_path):
    store = Store(tmp_path)
    store.save_connection(config(entry(), passwords={"aux-id": "old-memory-secret"}))
    before, state = store.path.read_bytes(), deepcopy(store.state)
    poisoned = config(entry())
    poisoned.sqlite_databases = [{"id": "aux-id", "name": "public", "database": "file",
                                  "encrypted": True, "password": "injected-nested-password"}]
    assert "injected-nested-password" not in repr(poisoned)
    with pytest.raises(ValueError) as captured:
        store.save_connection(poisoned)
    assert "injected-nested-password" not in repr(captured.value)
    assert store.path.read_bytes() == before and store.state == state
    assert not store.path.with_suffix(".tmp").exists()


def test_flush_never_serializes_injected_top_level_password_map_or_rewrites_sql(tmp_path):
    store = Store(tmp_path)
    store.save_connection(config(entry()))
    sql = "SELECT 'runtime-only-private-secret'; -- SQL stays verbatim\n"
    store.state["drafts"] = [{"sql": sql, "query_language": "sqlite"}]
    store.state["connections"][0]["sqlite_database_passwords"] = {"aux-id": "runtime-only-private-secret"}
    store.flush()
    disk = json.loads(store.path.read_text())
    assert disk["connections"][0]["sqlite_database_passwords"] == {}
    assert disk["drafts"][0]["sql"] == sql
    assert "runtime-only-private-secret" not in repr(store.state["connections"])


def test_synthetic_metadata_import_drops_runtime_attachment_passwords(tmp_path, no_real_keyring):
    from opennavicat.navicat_profiles import ImportedProfile, NavicatImportReport

    store = Store(tmp_path)
    value = config(entry(), passwords={"aux-id": "synthetic-import-secret"})
    original = deepcopy(value)
    profile = ImportedProfile("synthetic-owned-source", value, source_path="/synthetic/owned.plist",
                              source_format="macos-plist")
    report = NavicatImportReport((profile,), sources_found=1, platform="darwin")
    result = store.sync_navicat_profiles(report)
    assert result["added"] == 1 and value == original
    restored = store.connections()[0]
    assert restored.sqlite_databases == value.sqlite_databases
    assert restored.sqlite_database_passwords == {} and store.hydrate(restored).sqlite_database_passwords == {}
    assert not store._sqlite_database_passwords and not no_real_keyring
    assert b"synthetic-import-secret" not in store.path.read_bytes()


def test_flush_rejects_nested_injection_before_creating_a_temp_file(tmp_path):
    store = Store(tmp_path)
    store.save_connection(config(entry()))
    original = store.path.read_bytes()
    store.state["connections"][0]["sqlite_databases"][0]["password"] = "poisoned-workspace-secret"
    with pytest.raises(ValueError) as captured:
        store.flush()
    assert "poisoned-workspace-secret" not in repr(captured.value)
    assert store.path.read_bytes() == original and not store.path.with_suffix(".tmp").exists()


def test_flush_validates_cache_owner_before_committing_the_metadata_file(tmp_path):
    store = Store(tmp_path)
    store.save_connection(config(entry(), passwords={"aux-id": "memory-secret"}))
    original, cache = store.path.read_bytes(), store._sqlite_database_passwords.copy()
    store.state["connections"][0]["id"] = ["malformed owner"]
    with pytest.raises(ValueError, match="profile identity"):
        store.flush()
    assert store.path.read_bytes() == original and not store.path.with_suffix(".tmp").exists()
    assert store._sqlite_database_passwords == cache


@pytest.mark.parametrize("fail", [False, True])
def test_delete_commits_cache_pruning_only_with_profile_commit(tmp_path, monkeypatch, fail):
    store = Store(tmp_path)
    store.save_connection(config(entry(), passwords={"aux-id": "delete-owned-secret"}))
    before, state = store.path.read_bytes(), deepcopy(store.state)
    if fail:
        monkeypatch.setattr(store, "flush", lambda: (_ for _ in ()).throw(OSError("controlled delete failure")))
        with pytest.raises(OSError, match="controlled"):
            store.delete_connection("owned-profile")
        assert store.path.read_bytes() == before and store.state == state
        assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"aux-id": "delete-owned-secret"}
    else:
        store.delete_connection("owned-profile")
        assert not store.connections() and not store._sqlite_database_passwords


@pytest.mark.parametrize("bindings", [
    [], {"aux-id": "private-binding-payload"}, {"aux-id": ["owner"]},
    {"aux-id": ["owner", "file", "private-binding-payload"]},
])
def test_malformed_runtime_bindings_fail_without_echoing_values(bindings):
    with pytest.raises(ValueError) as captured:
        ConnectionConfig("owner", "Owned", sqlite_databases=(entry(),),
                         sqlite_database_passwords={"aux-id": "private-runtime-password"},
                         sqlite_database_password_bindings=bindings)
    assert "private-binding-payload" not in repr(captured.value)
    assert "private-runtime-password" not in repr(captured.value)


def test_missing_runtime_provenance_is_not_new_input():
    value = ConnectionConfig("owner", "Owned", sqlite_databases=(entry(),),
        sqlite_database_passwords={"aux-id": "private-runtime-password"}, sqlite_database_password_bindings={})
    assert value.sqlite_database_passwords == value.sqlite_database_password_bindings == {}


def test_failed_first_save_does_not_publish_generated_owner_or_move_the_carrier(tmp_path, monkeypatch):
    store = Store(tmp_path)
    value = config(entry(), passwords={"aux-id": "unsaved-memory-secret"}, identifier="")
    bindings, state = deepcopy(value.sqlite_database_password_bindings), deepcopy(store.state)
    monkeypatch.setattr(store, "flush", lambda: (_ for _ in ()).throw(OSError("controlled first save failure")))
    with pytest.raises(OSError, match="controlled"):
        store.save_connection(value)
    assert value.id == "" and value.sqlite_database_password_bindings == bindings
    assert not store.path.exists() and store.state == state and not store._sqlite_database_passwords
