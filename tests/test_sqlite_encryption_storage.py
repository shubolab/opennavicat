"""Owned fake-vault tests for exact SQLite file/cipher credential provenance."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, connection_metadata, with_sqlite_database_password
from opennavicat.sqlite_encryption_config import with_sqlite_encryption_password
from opennavicat.storage import Store


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    values, reads = {}, []
    def get(service, key):
        reads.append(key)
        return values.get((service, key))
    def put(service, key, value):
        values[service, key] = value
    def delete(service, key):
        values.pop((service, key), None)
    monkeypatch.setitem(sys.modules, 'keyring', SimpleNamespace(get_password=get, set_password=put, delete_password=delete))
    return values, reads


def profile(**changes):
    values = dict(id='owned', name='Generated encryption example', database='generated.sqlite',
        sqlite_encryption_enabled=True, sqlite_cipher_name='aes256cbc legacy',
        sqlite_encryption_password='owned-cipher-secret')
    values.update(changes)
    return ConnectionConfig(**values)


def test_remember_uses_keyring_and_nonsecret_binding_but_no_plaintext_or_carrier(tmp_path, vault):
    store = Store(tmp_path)
    source = profile()
    store.save_connection(source, remember_sqlite_encryption=True)
    text = store.path.read_text()
    assert 'owned-cipher-secret' not in text
    row = store.state['connections'][0]
    assert 'sqlite_encryption_password' not in row and 'sqlite_encryption_password_binding' not in row
    assert store.state['remembered_sqlite_encryption_bindings']['owned'] == ['owned', 'generated.sqlite', True, 'aes256cbc legacy', 0]
    restarted = Store(tmp_path)
    hydrated = restarted.hydrate(restarted.connections()[0])
    assert hydrated.sqlite_encryption_password == 'owned-cipher-secret'
    assert hydrated.sqlite_encryption_password_binding == source.sqlite_encryption_password_binding
    assert vault[0]['OpenNavicat', 'owned:sqlite_encryption_password'] == 'owned-cipher-secret'
    assert ('owned', 'sqlite_encryption_password') not in store.secrets


def test_unremembered_explicit_carrier_is_session_only_and_new_hydrate_requires_input(tmp_path, vault):
    store = Store(tmp_path)
    source = profile()
    saved = store.save_connection(source)
    assert saved.sqlite_encryption_password == source.sqlite_encryption_password
    assert not store._sqlite_encryption_passwords and not vault[0]
    assert store.hydrate(store.connections()[0]).sqlite_encryption_password == ''
    assert Store(tmp_path).hydrate(store.connections()[0]).sqlite_encryption_password == ''
    assert store.hydrate(saved).sqlite_encryption_password == source.sqlite_encryption_password


@pytest.mark.parametrize('field,value', [('id', 'other'), ('database', './generated.sqlite'),
    ('sqlite_encryption_enabled', False), ('sqlite_cipher_name', 'chacha20 legacy'), ('sqlite_cipher_page_size', 8192)])
@pytest.mark.parametrize('source', ['metadata', 'hydrated'])
def test_metadata_change_never_reuses_keyring_or_process_secret(tmp_path, vault, field, value, source):
    store = Store(tmp_path)
    store.save_connection(profile(), remember_sqlite_encryption=True)
    config = store.connections()[0]
    if source == 'hydrated':
        config = store.hydrate(config)
    setattr(config, field, value)
    vault[1].clear()
    assert store.hydrate(config).sqlite_encryption_password == ''
    assert not vault[1]


def test_explicit_clear_does_not_refill_or_resurrect_key(tmp_path, vault):
    store = Store(tmp_path)
    store.save_connection(profile(), remember_sqlite_encryption=True)
    config = with_sqlite_encryption_password(store.hydrate(store.connections()[0]), '')
    assert store.hydrate(config).sqlite_encryption_password == ''
    store.save_connection(config, remember_sqlite_encryption=True)
    assert not vault[0] and not store._sqlite_encryption_passwords
    assert not store.state['remembered_sqlite_encryption_bindings']
    assert Store(tmp_path).hydrate(store.connections()[0]).sqlite_encryption_password == ''


def test_generated_identity_migrates_only_successful_new_draft_binding(tmp_path):
    store = Store(tmp_path)
    config = profile(id='')
    saved = store.save_connection(config, remember_sqlite_encryption=True)
    assert config.id and saved.sqlite_encryption_password_binding[0] == config.id
    assert Store(tmp_path).hydrate(store.connections()[0]).sqlite_encryption_password == 'owned-cipher-secret'


@pytest.mark.parametrize('operation', ['update', 'forget', 'delete', 'new'])
def test_failed_commit_restores_binding_cache_workspace_and_keyring(tmp_path, vault, monkeypatch, operation):
    store = Store(tmp_path)
    store.save_connection(profile(), remember_sqlite_encryption=True)
    before = deepcopy(store.state), store._sqlite_encryption_passwords.copy(), store.path.read_bytes(), vault[0].copy()
    monkeypatch.setattr(store, 'flush', lambda: (_ for _ in ()).throw(OSError('owned failure')))
    config = profile(id='' if operation == 'new' else 'owned', sqlite_encryption_password='owned-new-cipher-secret')
    with pytest.raises(OSError, match='owned failure'):
        if operation == 'delete':
            store.delete_connection('owned')
        else:
            store.save_connection(config, remember_sqlite_encryption=operation != 'forget')
    assert (store.state, store._sqlite_encryption_passwords, store.path.read_bytes(), vault[0]) == before
    if operation == 'new':
        assert config.id == '' and config.sqlite_encryption_password_binding[0] == ''


def test_external_metadata_change_and_restore_cannot_resurrect_key(tmp_path, vault):
    store = Store(tmp_path)
    store.save_connection(profile(), remember_sqlite_encryption=True)
    original = deepcopy(store.state['connections'][0])
    store.state['connections'][0]['database'] = 'changed.sqlite'
    store.flush()
    assert not store._sqlite_encryption_passwords
    assert not store.state['remembered_sqlite_encryption_bindings']
    store.state['connections'][0] = original
    store.flush()
    vault[1].clear()
    assert store.hydrate(store.connections()[0]).sqlite_encryption_password == ''
    assert not vault[1]


@pytest.mark.parametrize('bad_binding', [None, [], ['owned', 'generated.sqlite', 1, 'aes256cbc legacy', 0],
    ['owned', 'generated.sqlite', True, 'aes256cbc legacy', False]])
def test_malformed_or_missing_persistent_binding_never_reads_keyring(tmp_path, vault, bad_binding):
    store = Store(tmp_path)
    store.save_connection(profile(), remember_sqlite_encryption=True)
    store._sqlite_encryption_passwords.clear()
    store.state['remembered_sqlite_encryption_bindings']['owned'] = bad_binding
    vault[1].clear()
    assert store.hydrate(store.connections()[0]).sqlite_encryption_password == ''
    assert not vault[1]


@pytest.mark.parametrize('field,value', [('sqlite_cipher_name', 'chacha20 legacy'), ('sqlite_cipher_page_size', 8192)])
def test_encrypted_attachment_cache_also_binds_cipher_and_page(tmp_path, field, value):
    store = Store(tmp_path)
    config = profile(sqlite_databases=(SQLiteDatabaseEntry('row', 'aux', 'aux.sqlite', True),),
        sqlite_database_passwords={'row': 'owned-attachment-secret'})
    store.save_connection(config, remember_sqlite_encryption=True)
    current = store.hydrate(store.connections()[0])
    assert current.sqlite_database_passwords['row'] == 'owned-attachment-secret'
    setattr(current, field, value)
    assert store.hydrate(current).sqlite_database_passwords == {}
    fresh = with_sqlite_database_password(current, 'row', 'owned-attachment-secret')
    store.save_connection(fresh)
    assert store.hydrate(store.connections()[0]).sqlite_database_passwords['row'] == 'owned-attachment-secret'
    assert 'owned-attachment-secret' not in store.path.read_text()
