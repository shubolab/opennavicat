"""Profile persistence failure never advances process or keychain credentials."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store


@pytest.fixture
def vault(monkeypatch):
    values = {}
    def get(service, key):
        return values.get((service, key))
    def put(service, key, value):
        values[service, key] = value
    def delete(service, key):
        values.pop((service, key), None)
    module = SimpleNamespace(get_password=get, set_password=put, delete_password=delete)
    monkeypatch.setitem(sys.modules, 'keyring', module)
    return module, values


def saved(tmp_path, vault):
    store = Store(tmp_path)
    config = ConnectionConfig('owned', 'Generated profile', driver='postgresql',
        password='owned-old-password', ssh_password='owned-old-ssh',
        ssh_key_passphrase='owned-old-key', ssl_key_password='owned-old-tls')
    store.save_connection(config, True, True, True, True)
    return store, config


@pytest.mark.parametrize('operation', ['replace-all', 'forget-all', 'mixed', 'delete'])
@pytest.mark.parametrize('failure', ['flush', 'replace'])
def test_failed_workspace_preserves_profile_caches_and_keychain(tmp_path, vault, monkeypatch, operation, failure):
    store, config = saved(tmp_path, vault)
    before = (deepcopy(store.state), store.passwords.copy(), store.secrets.copy(),
              store.path.read_bytes(), vault[1].copy())
    if failure == 'flush':
        monkeypatch.setattr(store, 'flush', lambda: (_ for _ in ()).throw(OSError('owned flush failure')))
    else:
        original = Path.replace
        def fail(path, target):
            if path == store.path.with_suffix('.tmp'):
                raise OSError('owned replace failure')
            return original(path, target)
        monkeypatch.setattr(Path, 'replace', fail)
    changed = replace(config, name='Changed', password='owned-new-password',
        ssh_password='owned-new-ssh', ssh_key_passphrase='owned-new-key', ssl_key_password='owned-new-tls')
    with pytest.raises(OSError, match='owned'):
        if operation == 'delete':
            store.delete_connection(config.id)
        else:
            flags = dict(remember=operation != 'forget-all', remember_ssh=operation == 'replace-all',
                         remember_ssl=operation != 'forget-all', remember_ssh_key=operation == 'replace-all')
            store.save_connection(changed, **flags)
    assert (store.state, store.passwords, store.secrets, store.path.read_bytes(), vault[1]) == before
    assert Store(tmp_path).connections()[0].name == config.name
    assert not any(value in store.path.read_text() for value in vault[1].values())


def test_mutating_keychain_failure_restores_even_the_current_write(tmp_path, vault, monkeypatch):
    store, config = saved(tmp_path, vault)
    before = deepcopy(store.state), store.path.read_bytes(), vault[1].copy()
    normal = vault[0].set_password
    def mutate_then_fail(service, key, value):
        normal(service, key, value)
        if value == 'owned-new-ssh':
            raise RuntimeError('owned vault write failure')
    monkeypatch.setattr(vault[0], 'set_password', mutate_then_fail)
    with pytest.raises(RuntimeError, match='owned vault'):
        store.save_connection(replace(config, password='owned-new-password', ssh_password='owned-new-ssh'),
                              remember=True, remember_ssh=True)
    assert (store.state, store.path.read_bytes(), vault[1]) == before
    assert store.hydrate(store.connections()[0]).password == config.password


def test_keychain_read_failure_does_not_write_or_erase_existing_secret(tmp_path, vault, monkeypatch):
    store, config = saved(tmp_path, vault)
    before = deepcopy(store.state), store.path.read_bytes(), vault[1].copy()
    monkeypatch.setattr(vault[0], 'get_password', lambda *_: (_ for _ in ()).throw(RuntimeError('owned vault read failure')))
    with pytest.raises(RuntimeError, match='owned vault read'):
        store.save_connection(replace(config, password='owned-new-password'), remember=True)
    assert (store.state, store.path.read_bytes(), vault[1]) == before


def test_successful_forget_commits_profile_before_removing_keychain(tmp_path, vault, monkeypatch):
    store, config = saved(tmp_path, vault)
    normal = vault[0].delete_password
    def delete(service, key):
        restarted = Store(tmp_path)
        assert not restarted.state['remembered_passwords']
        assert not restarted.state['remembered_secrets']
        assert restarted.connections()[0].name == 'Forgotten'
        normal(service, key)
    monkeypatch.setattr(vault[0], 'delete_password', delete)
    store.save_connection(replace(config, name='Forgotten'))
    assert not vault[1]
    assert store.hydrate(store.connections()[0]).password == config.password
