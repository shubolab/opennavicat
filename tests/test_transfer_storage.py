"""Data interchange fidelity and durable workspace behavior, without real secrets."""
import json
from contextlib import closing
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig, QueryResult
from opennavicat.storage import Store, create_sample
from opennavicat.transfer import export_result, import_data


def test_csv_distinguishes_null_empty_and_literal_null_marker(tmp_path):
    result = QueryResult(columns=["text", "nullable"], rows=[
        ["comma, quote \" and\nnewline", None],
        ["中文", ""],
        [r"C:\workspace\db", r"\N"],
        [r"\\N", "trailing\\"],
    ])
    target = tmp_path / "roundtrip.csv"
    export_result(result, target)
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    columns, rows = import_data(target)
    assert columns == result.columns
    assert rows == result.rows


def test_json_binary_null_and_unicode_roundtrip(tmp_path):
    result = QueryResult(columns=["id", "payload", "notes"],
                         rows=[[1, bytes(range(256)), None], [2, b"", "数据"]])
    target = tmp_path / "roundtrip.json"
    export_result(result, target)
    columns, rows = import_data(target)
    assert columns == result.columns
    assert rows == result.rows


def test_json_export_preserves_duplicate_sql_column_names(tmp_path):
    result = QueryResult(columns=["id", "id"], rows=[[1, 2]])
    target = tmp_path / "duplicate.json"
    export_result(result, target)
    assert json.loads(target.read_text("utf-8")) == {"columns": ["id", "id"], "rows": [[1, 2]]}
    with pytest.raises(ValueError, match="unique"):
        import_data(target)


@pytest.mark.parametrize("data", [
    {"columns": ["id", "name"], "rows": [[1]]},
    {"columns": [""], "rows": [[1]]},
    [{"id": 1}, {"name": "other columns"}],
])
def test_import_rejects_ambiguous_mapping(tmp_path, data):
    target = tmp_path / "invalid.json"
    target.write_text(json.dumps(data), "utf-8")
    with pytest.raises(ValueError):
        import_data(target)


def test_connection_password_is_only_in_mock_keychain(tmp_path, monkeypatch):
    vault = {}
    backend = SimpleNamespace(
        set_password=lambda service, key, value: vault.__setitem__((service, key), value),
        get_password=lambda service, key: vault.get((service, key)),
        delete_password=lambda service, key: vault.pop((service, key), None),
    )
    monkeypatch.setitem(sys.modules, "keyring", backend)
    config = ConnectionConfig("profile-1", "Local fixture", "postgresql", "fixture", username="test", password="test-secret")
    store = Store(tmp_path)
    store.save_connection(config, remember=True)
    assert "test-secret" not in store.path.read_text("utf-8")
    restarted = Store(tmp_path)
    assert restarted.connections()[0].password == ""
    assert restarted.secret(config) == "test-secret"
    restarted.delete_connection(config.id)
    assert restarted.connections() == []
    assert vault == {}


def test_keychain_failure_never_writes_plaintext(tmp_path, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("No keychain service")
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(set_password=unavailable))
    store = Store(tmp_path)
    config = ConnectionConfig("profile-2", "Remote", "mysql", username="test", password="test-secret")
    with pytest.raises(RuntimeError, match="keychain"):
        store.save_connection(config, remember=True)
    assert not store.path.exists()
    assert Store(tmp_path).connections() == []


def test_history_and_favorites_survive_restart(tmp_path):
    store = Store(tmp_path)
    config = ConnectionConfig("profile-3", "A connection")
    store.record(config, "SELECT 'persistent history'", 0.05, "success")
    store.add_favorite("Useful query", "SELECT 42", config.id)
    restarted = Store(tmp_path)
    assert restarted.history("persistent")[0]["sql"] == "SELECT 'persistent history'"
    assert restarted.history("unmatched") == []
    assert restarted.state["favorites"][0]["sql"] == "SELECT 42"


def test_sample_is_repeatable_and_relationally_consistent(tmp_path):
    path = create_sample(tmp_path)
    assert create_sample(tmp_path) == path
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM customers").fetchone()[0] == 6
        assert db.execute("SELECT count(*) FROM order_summary").fetchone()[0] == 36


def test_all_transport_secrets_have_independent_keychain_flags(tmp_path, monkeypatch):
    from dataclasses import replace
    vault = {}
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=lambda service, key, value: vault.__setitem__((service, key), value),
        get_password=lambda service, key: vault.get((service, key)),
        delete_password=lambda service, key: vault.pop((service, key), None),
    ))
    config = ConnectionConfig("transport-secrets", "SSH + TLS", "postgresql", username="fixture",
        password="db-secret", ssh_password="ssh-secret", ssh_key_passphrase="key-secret", ssl_key_password="tls-secret")
    store = Store(tmp_path)
    store.save_connection(config, remember=True, remember_ssh=True, remember_ssh_key=False, remember_ssl=True)
    serialized = store.path.read_text("utf-8")
    for value in ("db-secret", "ssh-secret", "key-secret", "tls-secret"):
        assert value not in serialized
    assert store.state["remembered_passwords"] == [config.id]
    assert set(store.state["remembered_secrets"][config.id]) == {"ssh_password", "ssl_key_password"}
    restored = Store(tmp_path)
    hydrated = restored.hydrate(restored.connections()[0])
    assert hydrated.password == "db-secret" and hydrated.ssh_password == "ssh-secret"
    assert hydrated.ssl_key_password == "tls-secret" and hydrated.ssh_key_passphrase == ""
    # Unremembered secrets remain available during this process only.
    assert store.hydrate(store.connections()[0]).ssh_key_passphrase == "key-secret"
    provided = replace(restored.connections()[0], ssh_key_passphrase="provided-key", password="provided-db")
    assert restored.hydrate(provided).ssh_key_passphrase == "provided-key"
    assert restored.hydrate(provided).password == "provided-db"
    # Different flags do not silently persist the private-key passphrase.
    assert ("OpenNavicat", f"{config.id}:ssh_key_passphrase") not in vault
    restored.delete_connection(config.id)
    assert not vault and not restored.state["remembered_secrets"]


def test_keychain_partial_failure_restores_previous_secrets_and_profile(tmp_path, monkeypatch):
    vault = {("OpenNavicat", "partial"): "old-db"}
    def put(service, key, value):
        if key.endswith(":ssh_password"):
            raise RuntimeError("keychain unavailable")
        vault[(service, key)] = value
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=put, get_password=lambda service, key: vault.get((service, key)),
        delete_password=lambda service, key: vault.pop((service, key), None)))
    store = Store(tmp_path)
    config = ConnectionConfig("partial", "Partial", password="new-db", ssh_password="new-ssh")
    with pytest.raises(RuntimeError, match="unavailable"):
        store.save_connection(config, remember=True, remember_ssh=True)
    assert vault[("OpenNavicat", "partial")] == "old-db"
    assert not store.path.exists() and store.connections() == []


def test_disabling_secret_remembering_never_resurrects_old_keychain_entries(tmp_path, monkeypatch):
    vault = {}
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(
        set_password=lambda service, key, value: vault.__setitem__((service, key), value),
        get_password=lambda service, key: vault.get((service, key)),
        delete_password=lambda service, key: vault.pop((service, key), None)))
    config = ConnectionConfig("forget", "Forget", password="db", ssh_password="ssh", ssh_key_passphrase="key", ssl_key_password="tls")
    store = Store(tmp_path)
    store.save_connection(config, remember=True, remember_ssh=True, remember_ssh_key=True, remember_ssl=True)
    assert len(vault) == 4
    store.save_connection(config)
    assert not vault and store.state["remembered_passwords"] == [] and store.state["remembered_secrets"] == {}
    restarted = Store(tmp_path)
    clean = restarted.hydrate(restarted.connections()[0])
    assert all(getattr(clean, field) == "" for field in Store.SECRET_FIELDS)
    with pytest.raises(ValueError, match="Unknown"):
        store.secret(config, "ssh_host_key")


def test_hydrate_unsaved_supplied_credentials_and_legacy_db_cache(tmp_path):
    store = Store(tmp_path)
    config = ConnectionConfig("unsaved", "Unsaved", password="input", ssh_password="ssh-input")
    hydrated = store.hydrate(config)
    assert hydrated is not config and hydrated.password == "input" and hydrated.ssh_password == "ssh-input"
    assert not store.path.exists()
    store.passwords["cached"] = "legacy-process-cache"
    assert store.hydrate(ConnectionConfig("cached", "Cached")).password == "legacy-process-cache"


def test_connection_repr_does_not_expose_any_secret():
    config = ConnectionConfig("redaction", "Public name", password="db-private",
        ssh_password="ssh-private", ssh_key_passphrase="ssh-key-private", ssl_key_password="tls-private")
    representation = repr(config)
    assert "Public name" in representation
    assert all(secret not in representation for secret in ("db-private", "ssh-private", "ssh-key-private", "tls-private"))
