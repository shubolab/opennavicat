"""Runtime credentials retain their original file/flag/owner provenance."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat import sqlite_database_config as codec
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, copy_connection_config
from opennavicat.storage import Store


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=lambda *args: None, get_password=lambda *args: None,
        delete_password=lambda *args: None))


def profile(identifier="owner"):
    return ConnectionConfig(identifier, "Owned fixture", database="generated-main.sqlite",
        sqlite_databases=(SQLiteDatabaseEntry("row", "aux", "file-A.sqlite", True),),
        sqlite_database_passwords={"row": "same-owned-secret"})


def changed(row, kind):
    return replace(row, **({"database": "file-B.sqlite"} if kind == "file" else {"encrypted": False}))


@pytest.mark.parametrize("kind", ["file", "flag"])
@pytest.mark.parametrize("operation", ["copy", "hydrate", "save"])
def test_hydrated_carrier_does_not_rebind_after_edit_without_new_input(tmp_path, kind, operation):
    store = Store(tmp_path)
    store.save_connection(profile())
    runtime = store.hydrate(store.connections()[0])
    assert runtime.sqlite_database_passwords == {"row": "same-owned-secret"}
    runtime.sqlite_databases = (changed(runtime.sqlite_databases[0], kind),)
    if operation == "copy":
        value = copy_connection_config(runtime)
    elif operation == "hydrate":
        value = store.hydrate(runtime)
    else:
        store.save_connection(runtime)
        value = store.hydrate(store.connections()[0])
    assert value.sqlite_database_passwords == {}
    assert "same-owned-secret" not in store.path.read_text()


@pytest.mark.parametrize("kind", ["file", "flag"])
def test_replace_and_deepcopy_preserve_original_binding_when_metadata_changes(tmp_path, kind):
    store = Store(tmp_path)
    store.save_connection(profile())
    runtime = store.hydrate(store.connections()[0])
    old_binding = deepcopy(runtime.sqlite_database_password_bindings)
    clone = deepcopy(runtime)
    assert clone.sqlite_database_password_bindings == old_binding
    assert clone.sqlite_database_password_bindings is not runtime.sqlite_database_password_bindings
    clone.sqlite_databases = (changed(clone.sqlite_databases[0], kind),)
    assert store.hydrate(clone).sqlite_database_passwords == {}
    replaced = replace(runtime, sqlite_databases=(changed(runtime.sqlite_databases[0], kind),))
    assert replaced.sqlite_database_passwords == {}
    assert runtime.sqlite_database_passwords == {"row": "same-owned-secret"}


@pytest.mark.parametrize("kind", ["file", "flag"])
def test_explicit_same_password_input_binds_the_new_metadata(tmp_path, kind):
    store = Store(tmp_path)
    store.save_connection(profile())
    runtime = store.hydrate(store.connections()[0])
    runtime.sqlite_databases = (changed(runtime.sqlite_databases[0], kind),)
    fresh = codec.with_sqlite_database_password(runtime, "row", "same-owned-secret")
    assert fresh is not runtime
    assert fresh.sqlite_database_password_bindings["row"] == (
        "owner", fresh.sqlite_databases[0].database, fresh.sqlite_databases[0].encrypted)
    assert store.hydrate(fresh).sqlite_database_passwords == {"row": "same-owned-secret"}
    store.save_connection(fresh)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"row": "same-owned-secret"}


def test_new_owner_requires_new_input_but_generated_id_is_a_legitimate_save_transition(tmp_path):
    store = Store(tmp_path)
    source = profile()
    source.id = "different-owner"
    assert copy_connection_config(source).sqlite_database_passwords == {}
    fresh = codec.with_sqlite_database_password(source, "row", "same-owned-secret")
    store.save_connection(fresh)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"row": "same-owned-secret"}
    unsaved = profile("")
    saved = store.save_connection(unsaved)
    assert saved.id and unsaved.id == saved.id
    assert saved.sqlite_database_password_bindings["row"][0] == saved.id
    assert store.hydrate(saved).sqlite_database_passwords == {"row": "same-owned-secret"}
    row = next(item for item in store.connections() if item.id == saved.id)
    assert store.hydrate(row).sqlite_database_passwords == {"row": "same-owned-secret"}


def test_rename_reorder_and_independent_copies_keep_the_correct_bound_secrets(tmp_path):
    store = Store(tmp_path)
    source = profile()
    second = SQLiteDatabaseEntry("second", "other", "file-A.sqlite", True)
    source.sqlite_databases += (second,)
    source = codec.with_sqlite_database_password(source, "second", "second-owned-secret")
    store.save_connection(source)
    runtime = store.hydrate(store.connections()[0])
    reordered = replace(runtime, sqlite_databases=(second, replace(runtime.sqlite_databases[0], name="renamed")))
    copied = copy_connection_config(reordered)
    assert copied.sqlite_database_passwords == {"row": "same-owned-secret", "second": "second-owned-secret"}
    assert copied.sqlite_database_password_bindings == reordered.sqlite_database_password_bindings
    copied.sqlite_database_password_bindings.clear()
    assert reordered.sqlite_database_password_bindings
    store.save_connection(reordered)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == reordered.sqlite_database_passwords


def test_delete_and_explicit_empty_input_cannot_resurrect_a_secret(tmp_path):
    store = Store(tmp_path)
    store.save_connection(profile())
    runtime = store.hydrate(store.connections()[0])
    cleared = codec.with_sqlite_database_password(runtime, "row", "")
    assert store.hydrate(cleared).sqlite_database_passwords == {"row": ""}
    store.save_connection(cleared)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {}
    assert not store._sqlite_database_passwords
    runtime.sqlite_databases = ()
    store.save_connection(runtime)
    assert not store._sqlite_database_passwords
    restored = replace(runtime, sqlite_databases=profile().sqlite_databases)
    assert store.hydrate(restored).sqlite_database_passwords == {}


def test_metadata_ignores_persisted_binding_and_password_carriers(tmp_path):
    source = profile()
    assert "same-owned-secret" not in repr(source)
    metadata = codec.connection_metadata(source)
    assert metadata["sqlite_database_passwords"] == {}
    assert metadata["sqlite_database_password_bindings"] == {}
    metadata["sqlite_database_passwords"] = {"row": "injected-durable-secret"}
    metadata["sqlite_database_password_bindings"] = {"row": ["owner", "file-A.sqlite", True]}
    loaded = codec.connection_from_metadata(metadata)
    assert loaded.sqlite_database_passwords == loaded.sqlite_database_password_bindings == {}
    (tmp_path / "workspace.json").write_text(json.dumps({"connections": [metadata]}))
    store = Store(tmp_path)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {}
    store.flush()
    assert "injected-durable-secret" not in store.path.read_text()


def test_process_json_snapshot_keeps_the_original_binding(tmp_path):
    store = Store(tmp_path)
    store.save_connection(profile())
    runtime = store.hydrate(store.connections()[0])
    restored = ConnectionConfig(**json.loads(json.dumps(asdict(runtime))))
    assert restored.sqlite_database_password_bindings == runtime.sqlite_database_password_bindings
    restored.sqlite_databases = (changed(restored.sqlite_databases[0], "file"),)
    assert store.hydrate(restored).sqlite_database_passwords == {}


@pytest.mark.parametrize("phase", ["flush", "replace"])
def test_failed_new_binding_save_preserves_old_file_state_and_bound_secret(tmp_path, monkeypatch, phase):
    store = Store(tmp_path)
    store.save_connection(profile())
    before = store.path.read_bytes(), deepcopy(store.state), store._sqlite_database_passwords.copy()
    runtime = store.hydrate(store.connections()[0])
    runtime.sqlite_databases = (changed(runtime.sqlite_databases[0], "file"),)
    candidate = codec.with_sqlite_database_password(runtime, "row", "same-owned-secret")
    if phase == "flush":
        monkeypatch.setattr(store, "flush", lambda: (_ for _ in ()).throw(OSError("controlled failure")))
    else:
        original = Path.replace
        def fail_replace(source, destination):
            if source == store.path.with_suffix(".tmp"):
                assert "same-owned-secret" not in source.read_text()
                raise OSError("controlled failure")
            return original(source, destination)
        monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="controlled"):
        store.save_connection(candidate)
    assert (store.path.read_bytes(), store.state, store._sqlite_database_passwords) == before
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords == {"row": "same-owned-secret"}
