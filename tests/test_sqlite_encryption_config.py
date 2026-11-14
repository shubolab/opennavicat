"""Metadata and process-only provenance, without opening any database/keyring."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import (
    CONNECTION_SECRET_FIELDS, connection_from_metadata, connection_metadata, copy_connection_config,
    SQLiteDatabaseEntry, with_sqlite_database_password,
)
from opennavicat.sqlite_encryption_config import (
    SQLITE_CIPHER_NAMES, SQLITE_CIPHER_PAGE_SIZES, bound_sqlite_encryption_password,
    decode_sqlite_encryption_fields, resolve_sqlite_cipher,
    sqlite_encryption_password_binding, with_sqlite_encryption_password,
)


def config(**changes):
    values = dict(id="generated-owner", name="Generated encrypted example", database="example.sqlite",
                  sqlite_encryption_enabled=True, sqlite_cipher_name="aes256cbc",
                  sqlite_encryption_password="generated-model-secret")
    values.update(changes)
    return ConnectionConfig(**values)


def test_first_input_binds_and_metadata_never_contains_secret_or_carrier():
    source = config()
    assert bound_sqlite_encryption_password(source) == "generated-model-secret"
    assert source.sqlite_encryption_password_binding == ("generated-owner", "example.sqlite", True, "aes256cbc", 0)
    assert "generated-model-secret" not in repr(source)
    encoded = connection_metadata(source)
    assert "sqlite_encryption_password" not in encoded
    assert "sqlite_encryption_password_binding" not in encoded
    assert encoded["sqlite_encryption_enabled"] is True
    assert encoded["sqlite_cipher_name"] == "aes256cbc"
    assert encoded["sqlite_cipher_page_size"] == 0
    decoded = connection_from_metadata(json.loads(json.dumps(encoded)))
    assert decoded.sqlite_encryption_password == "" and decoded.sqlite_encryption_password_binding is None
    assert "sqlite_encryption_password" in CONNECTION_SECRET_FIELDS


@pytest.mark.parametrize("field,value", [
    ("id", "other-owner"), ("database", "./example.sqlite"),
    ("sqlite_encryption_enabled", False), ("sqlite_cipher_name", "chacha20"),
    ("sqlite_cipher_page_size", 8192),
])
@pytest.mark.parametrize("operation", ["copy", "replace", "deepcopy_then_copy"])
def test_metadata_change_never_rebinds_secret(field, value, operation):
    source = config(sqlite_cipher_name="aes256cbc legacy")
    if operation == "replace":
        result = replace(source, **{field: value})
    else:
        result = deepcopy(source) if operation == "deepcopy_then_copy" else source
        setattr(result, field, value)
        result = copy_connection_config(result)
    assert result.sqlite_encryption_password == ""
    assert result.sqlite_encryption_password_binding is None


def test_copy_preserves_matching_binding_but_does_not_bind_unmarked_assignment():
    source = config()
    copied = copy_connection_config(source)
    assert copied is not source
    assert copied.sqlite_encryption_password_binding == source.sqlite_encryption_password_binding
    source.sqlite_encryption_password_binding = None
    assert copy_connection_config(source).sqlite_encryption_password == ""


def test_explicit_new_input_and_clear_bind_to_current_metadata():
    source = config()
    source.database = "changed.sqlite"
    fresh = with_sqlite_encryption_password(source, "generated-model-secret")
    assert fresh is not source and bound_sqlite_encryption_password(fresh) == "generated-model-secret"
    assert fresh.sqlite_encryption_password_binding == sqlite_encryption_password_binding(fresh)
    clear = with_sqlite_encryption_password(fresh, "")
    assert clear.sqlite_encryption_password == ""
    assert copy_connection_config(clear).sqlite_encryption_password_binding == sqlite_encryption_password_binding(clear)


def test_driver_or_disabled_encryption_cannot_retain_encryption_secret():
    assert config(driver="postgresql").sqlite_encryption_password == ""
    disabled = config(sqlite_encryption_enabled=False)
    assert with_sqlite_encryption_password(disabled, "new-secret").sqlite_encryption_password == ""


def test_persisted_injected_key_or_binding_never_hydrates():
    value = connection_metadata(config())
    value.update(sqlite_encryption_password="injected-secret",
                 sqlite_encryption_password_binding=["generated-owner", "example.sqlite", True, "aes256cbc", 0])
    result = connection_from_metadata(value)
    assert result.sqlite_encryption_password == "" and result.sqlite_encryption_password_binding is None


@pytest.mark.parametrize("enabled,label,size", [
    (1, "", 0), (True, None, 0), (True, "AES256CBC", 0), (True, " aes256cbc", 0),
    (True, "unknown-generated-secret", 0), (True, "sqlcipher", 8192),
    (True, "aes128cbc legacy", True), (True, "aes128cbc legacy", 513),
])
def test_invalid_metadata_fails_closed_without_echoing(enabled, label, size):
    with pytest.raises(ValueError) as captured:
        decode_sqlite_encryption_fields(enabled, label, size)
    assert "unknown-generated-secret" not in str(captured.value)


@pytest.mark.parametrize("label", SQLITE_CIPHER_NAMES)
def test_labels_remain_verbatim_and_unsupported_ascon_legacy_is_explicit(label):
    source = config(sqlite_cipher_name=label)
    assert connection_metadata(source)["sqlite_cipher_name"] == label
    if label == "ascon128 legacy":
        with pytest.raises(ValueError, match="not supported"):
            resolve_sqlite_cipher(label)
    else:
        settings = resolve_sqlite_cipher(label)
        assert settings.cipher in {"aes128cbc", "aes256cbc", "chacha20", "sqlcipher", "rc4", "ascon128"}
        assert settings.legacy == (int(label[-1]) if " legacy v" in label else 1 if " legacy" in label else None)


def test_blank_metadata_resolves_only_at_execution_with_inference_marker():
    source = config(sqlite_cipher_name="")
    assert connection_metadata(source)["sqlite_cipher_name"] == ""
    settings = resolve_sqlite_cipher("")
    assert settings.cipher == "aes128cbc" and settings.legacy is None and settings.inferred_default


@pytest.mark.parametrize("size", SQLITE_CIPHER_PAGE_SIZES)
def test_legacy_page_sizes_are_structural_metadata_not_runtime_acceptance(size):
    assert resolve_sqlite_cipher("sqlcipher legacy v4", size).page_size == size


@pytest.mark.parametrize("binding", ["generated-private-binding", [], ["owner", "file", 1, "", 0]])
def test_malformed_runtime_binding_is_rejected_without_value_echo(binding):
    with pytest.raises(ValueError) as captured:
        config(sqlite_encryption_password_binding=binding)
    assert "generated-private-binding" not in str(captured.value)


def attached_config(**changes):
    return config(sqlite_databases=(SQLiteDatabaseEntry("aux", "aux", "aux.sqlite", True),),
                  sqlite_database_passwords={"aux": "generated-attached-secret"}, **changes)


@pytest.mark.parametrize("field,value", [("sqlite_cipher_name", "sqlcipher legacy v4"),
                                         ("sqlite_cipher_page_size", 8192)])
def test_encrypted_attachment_key_does_not_rebind_after_cipher_page_edit(field, value):
    source = attached_config(sqlite_cipher_name="aes256cbc legacy")
    assert source.sqlite_database_password_bindings["aux"] == (
        "generated-owner", "aux.sqlite", True, "aes256cbc legacy", 0)
    setattr(source, field, value)
    copied = copy_connection_config(source)
    assert copied.sqlite_database_passwords == copied.sqlite_database_password_bindings == {}
    explicit = with_sqlite_database_password(copied, "aux", "generated-attached-secret")
    assert copy_connection_config(explicit).sqlite_database_passwords == {"aux": "generated-attached-secret"}


def test_old_encrypted_attachment_three_part_carrier_fails_closed():
    source = attached_config(sqlite_database_password_bindings={"aux": ("generated-owner", "aux.sqlite", True)})
    assert source.sqlite_database_passwords == source.sqlite_database_password_bindings == {}


def test_plain_attachment_three_part_carrier_is_compatible_and_does_not_supply_main_key():
    source = config(sqlite_databases=(SQLiteDatabaseEntry("aux", "aux", "aux.sqlite", False),),
                    sqlite_database_passwords={"aux": "generated-plain-row-carrier"},
                    sqlite_database_password_bindings={"aux": ("generated-owner", "aux.sqlite", False)})
    assert source.sqlite_database_password_bindings["aux"] == ("generated-owner", "aux.sqlite", False)
    source.sqlite_encryption_password = ""
    assert copy_connection_config(source).sqlite_encryption_password == ""
