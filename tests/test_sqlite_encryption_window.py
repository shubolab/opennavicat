"""GUI-thread prompts and serial dispatch; actual encryption has separate tests."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, copy_connection_config
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow
import opennavicat.ui.window as window_module


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
                   and not window.cancel_pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    gui = threading.get_ident()
    values, attempts, constructed, errors = {}, [], [], []
    monkeypatch.setitem(sys.modules, 'keyring', SimpleNamespace(
        get_password=lambda service, key: values.get((service, key)),
        set_password=lambda service, key, value: values.__setitem__((service, key), value),
        delete_password=lambda service, key: values.pop((service, key), None)))
    class Session:
        def __init__(self, config):
            self.config = config
            self.in_transaction = False
            constructed.append(config)
        def connect(self):
            assert threading.get_ident() != gui
            attempts.append(copy_connection_config(self.config))
        def schemas(self): return []
        def objects(self, schema): return []
        def close(self): pass
    monkeypatch.setattr(window_module, 'DatabaseSession', Session)
    monkeypatch.setattr(MainWindow, 'error', lambda self, error: errors.append(str(error)))
    monkeypatch.setattr(window_module, 'discover_navicat_profiles', lambda: pytest.fail('No user discovery'))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_a, **_k: pytest.fail('Unexpected prompt'))
    monkeypatch.setattr(QMessageBox, 'information', lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    store = Store(tmp_path / 'workspace')
    config = ConnectionConfig('owned-cipher', 'Generated encryption example', database=str(tmp_path / 'not-opened.sqlite'),
        sqlite_open_mode='existing', sqlite_encryption_enabled=True, sqlite_cipher_name='aes256cbc',
        sqlite_encryption_password='owned-initial-secret')
    store.save_connection(config)
    window = MainWindow(store)
    qtbot.addWidget(window)
    window.show()
    value = SimpleNamespace(window=window, store=store, config=config, values=values, attempts=attempts,
                            constructed=constructed, errors=errors, gui=gui, session_type=Session)
    yield value
    idle(qtbot, window)
    window.close()
    qtbot.waitUntil(lambda: window._closed, timeout=10000)


def test_save_off_prompts_for_every_new_session_without_reusing_process_key(qtbot, host, monkeypatch):
    prompts = []
    def prompt(*args):
        assert threading.get_ident() == host.gui
        prompts.append(args)
        return 'owned-session-secret-' + str(len(prompts)), True
    monkeypatch.setattr(QInputDialog, 'getText', prompt)
    for index in (1, 2):
        host.window.connect_id(host.config.id)
        idle(qtbot, host.window)
        assert host.attempts[-1].sqlite_encryption_password == 'owned-session-secret-' + str(index)
        assert host.window.configs[host.config.id].sqlite_encryption_password == ''
        assert not host.store._sqlite_encryption_passwords and not host.values
        assert 'owned-session-secret' not in host.store.path.read_text()
        host.window.disconnect(host.config.id)
        idle(qtbot, host.window)
    assert len(prompts) == len(host.attempts) == 2 and not host.errors


def test_remembered_matching_key_avoids_prompt_but_stays_out_of_public_config(qtbot, host):
    host.store.save_connection(host.config, remember_sqlite_encryption=True)
    host.window.configs[host.config.id] = host.store.connections()[0]
    host.window.connect_id(host.config.id)
    idle(qtbot, host.window)
    assert host.attempts[0].sqlite_encryption_password == 'owned-initial-secret'
    assert host.window.configs[host.config.id].sqlite_encryption_password == ''
    assert not host.errors


@pytest.mark.parametrize('answer', [('owned-not-used', False), ('', True), ('x' * 65537, True)])
def test_cancel_empty_or_oversized_key_never_constructs_or_dispatches_session(qtbot, host, monkeypatch, answer):
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: answer)
    before = host.store.path.read_bytes()
    host.window.connect_id(host.config.id)
    idle(qtbot, host.window)
    assert not host.constructed and not host.attempts and not host.window.sessions
    assert not Path(host.config.database).exists()
    assert host.store.path.read_bytes() == before
    assert 'owned-not-used' not in ''.join(host.errors)


def test_nested_prompt_reentry_and_profile_mutation_cannot_dispatch_stale_config(qtbot, host, monkeypatch):
    calls = []
    def prompt(*args):
        calls.append(True)
        host.window.connect_id(host.config.id)
        host.window.configs[host.config.id].database += '.changed'
        return 'owned-stale-key', True
    monkeypatch.setattr(QInputDialog, 'getText', prompt)
    host.window.connect_id(host.config.id)
    idle(qtbot, host.window)
    assert calls == [True] and not host.constructed and not host.attempts
    assert not host.window._sqlite_password_prompts


def test_attachment_prompt_is_separate_bound_input_and_cancel_discards_whole_open(qtbot, host, monkeypatch):
    config = replace(host.config, sqlite_databases=(SQLiteDatabaseEntry('aux', 'aux', 'owned-aux.sqlite', True),))
    host.store.save_connection(config)
    host.window.configs[config.id] = host.store.connections()[0]
    answers = iter([('owned-main-key', True), ('owned-cancelled-aux', False)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: next(answers))
    host.window.connect_id(config.id)
    idle(qtbot, host.window)
    assert not host.constructed and not host.attempts
    answers = iter([('owned-main-key', True), ('owned-aux-key', True)])
    host.window.connect_id(config.id)
    idle(qtbot, host.window)
    runtime, = host.attempts
    assert runtime.sqlite_encryption_password == 'owned-main-key'
    assert runtime.sqlite_database_passwords == {'aux': 'owned-aux-key'}
    assert runtime.sqlite_database_password_bindings['aux'] == (config.id, 'owned-aux.sqlite', True, 'aes256cbc', 0)
    assert 'owned-main-key' not in host.store.path.read_text() and 'owned-aux-key' not in host.store.path.read_text()


class CreationDialog(QDialog):
    def __init__(self, gui):
        super().__init__()
        self.gui, self.calls = gui, []
    def finish_sqlite_creation(self, token, *, error=None):
        assert threading.get_ident() == self.gui
        self.calls.append((token, error))


@pytest.mark.parametrize('cancel', [False, True])
def test_create_dispatch_owns_snapshot_worker_and_cancel_token(qtbot, host, monkeypatch, cancel):
    entered, release = threading.Event(), threading.Event()
    worker_configs = []
    def create(config, cancellation_requested=None):
        assert threading.get_ident() != host.gui
        entered.set()
        assert release.wait(10)
        worker_configs.append(copy_connection_config(config))
        if cancellation_requested():
            raise DatabaseError('Owned creation cancelled')
    monkeypatch.setattr(host.session_type, 'create_sqlite_database', staticmethod(create), raising=False)
    dialog = CreationDialog(host.gui)
    qtbot.addWidget(dialog)
    token = object()
    config = replace(host.config, sqlite_open_mode='create')
    try:
        host.window._create_sqlite_connection(dialog, token, config)
        host.window._create_sqlite_connection(dialog, token, config)
        qtbot.waitUntil(entered.is_set)
        config.database += '.changed-after-dispatch'
        if cancel:
            host.window.stop()
    finally:
        release.set()
    idle(qtbot, host.window)
    assert len(worker_configs) == len(dialog.calls) == 1
    assert worker_configs[0].database == host.config.database
    assert dialog.calls[0][0] is token
    assert bool(dialog.calls[0][1]) is cancel
    assert not host.window._sqlite_creation_cancellations
    assert not Path(host.config.database).exists()  # routing fixture did not create any database


def test_wrong_cipher_key_does_not_open_a_database_account_password_dialog(qtbot, host, monkeypatch):
    prompts = []
    def prompt(*args):
        prompts.append(args[1])
        assert len(prompts) == 1
        return 'owned-wrong-key', True
    def fail_connect(session):
        error = DatabaseError('Owned SQLite file rejected the supplied encryption key')
        error.authentication_required = True
        raise error
    monkeypatch.setattr(QInputDialog, 'getText', prompt)
    monkeypatch.setattr(host.session_type, 'connect', fail_connect)
    host.window.connect_id(host.config.id)
    idle(qtbot, host.window)
    assert prompts == ['SQLite 加密密码']
    assert len(host.constructed) == len(host.errors) == 1
    assert not host.window.sessions and 'owned-wrong-key' not in host.errors[0]
