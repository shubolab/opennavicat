"""Connection deletion commits only after an owned close succeeds; no DB used."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import uuid

import keyring
import pytest
from PySide6.QtWidgets import QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow
import opennavicat.ui.window as window_module


class CloseFixture:
    in_transaction = False

    def __init__(self, config, *, fail=False, gated=True):
        self.config = config
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.close_threads = []
        if not gated:
            self.release.set()

    def close(self):
        self.close_threads.append(threading.get_ident())
        self.started.set()
        assert self.release.wait(5), "The owned close gate was not released"
        if self.fail:
            raise DatabaseError("Generated close failure")
        self.closed = True


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
                   and not window.cancel_pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def make_host(qtbot, tmp_path, monkeypatch):
    vault = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, key: vault.get((service, key)))
    monkeypatch.setattr(keyring, "set_password", lambda service, key, value: vault.__setitem__((service, key), value))
    monkeypatch.setattr(keyring, "delete_password", lambda service, key: vault.pop((service, key), None))
    monkeypatch.setattr(window_module, "discover_navicat_profiles", lambda: pytest.fail("No existing profiles"))
    monkeypatch.setattr(window_module, "DatabaseSession", lambda *_: pytest.fail("No database construction"))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda _window, error: errors.append(str(error)))

    def make(driver, *, fail=False, connected=True):
        config = ConnectionConfig("generated-delete-" + uuid.uuid4().hex, "Generated deletion", driver=driver,
            host="owned.invalid", port=1521, username="GENERATED", database="", ssl_mode="disable",
            oracle_service_identifier="GENERATED_SERVICE", password="generated-db-only",
            ssh_password="generated-ssh-only", ssh_key_passphrase="generated-key-only",
            ssl_key_password="generated-tls-only")
        store = Store(tmp_path / (driver + "-workspace"))
        store.state["navicat_auto_discover"] = False
        store.save_connection(config, True, remember_ssh=True, remember_ssh_key=True, remember_ssl=True)
        window = MainWindow(store)
        window.configs[config.id] = config
        sessions = []
        session = None
        if connected:
            session = CloseFixture(config, fail=fail, gated=driver in {"oracle", "mongodb"})
            sessions.append(session)
            window.sessions[config.id] = session
        window.set_active(config.id)
        window.show()
        cleaned = False

        def finish(_widget=None):
            nonlocal cleaned
            if cleaned:
                return
            for item in sessions:
                item.fail = False
                item.release.set()
            idle(qtbot, window)
            window.close()
            qtbot.waitUntil(lambda: window._closed, timeout=10000)
            idle(qtbot, window)
            cleaned = True

        qtbot.addWidget(window, before_close_func=finish)
        return SimpleNamespace(window=window, store=store, config=config, session=session,
            sessions=sessions, vault=vault, errors=errors, finish=finish)

    return make


@pytest.mark.parametrize("driver", ["oracle", "mongodb"])
def test_async_delete_keeps_profile_and_credentials_until_close_succeeds(qtbot, make_host, driver):
    h = make_host(driver)
    identifier = h.config.id
    saved, credentials = h.store.path.read_bytes(), dict(h.vault)
    try:
        h.window.remove_connection(identifier)
        qtbot.waitUntil(h.session.started.is_set)
        assert h.window.configs[identifier] is h.config
        assert h.store.path.read_bytes() == saved and h.vault == credentials
        assert h.window.sessions[identifier] is h.session and not h.session.closed
    finally:
        h.session.release.set()
    idle(qtbot, h.window)
    qtbot.waitUntil(lambda: identifier not in h.window.configs)
    assert h.session.closed and len(h.session.close_threads) == 1
    assert h.session.close_threads[0] != threading.get_ident()
    assert identifier not in h.window.sessions
    assert h.store.connections() == [] and h.vault == {}
    assert identifier not in h.store.state["remembered_passwords"]
    assert identifier not in h.store.state["remembered_secrets"]
    assert not h.errors


@pytest.mark.parametrize("driver", ["oracle", "mongodb"])
def test_async_close_failure_preserves_profile_store_and_credential_choices(qtbot, make_host, driver):
    h = make_host(driver, fail=True)
    identifier = h.config.id
    saved, credentials = h.store.path.read_bytes(), dict(h.vault)
    try:
        h.window.remove_connection(identifier)
        qtbot.waitUntil(h.session.started.is_set)
        assert h.window.configs[identifier] is h.config and h.store.path.read_bytes() == saved
    finally:
        h.session.release.set()
    idle(qtbot, h.window)
    assert h.errors == ["Generated close failure"]
    assert h.window.configs[identifier] is h.config
    assert h.window.sessions[identifier] is h.session
    assert h.store.path.read_bytes() == saved and h.vault == credentials
    assert identifier in h.store.state["remembered_passwords"]
    assert set(h.store.state["remembered_secrets"][identifier]) == {"ssh_password", "ssh_key_passphrase", "ssl_key_password"}


@pytest.mark.parametrize("change", ["config_identity", "config_value", "saved_profile", "remember_choice", "session"])
def test_stale_async_delete_cannot_remove_replacement_binding(qtbot, make_host, change):
    h = make_host("oracle")
    identifier = h.config.id
    try:
        h.window.remove_connection(identifier)
        qtbot.waitUntil(h.session.started.is_set)
        if change == "config_identity":
            h.window.configs[identifier] = replace(h.config)
        elif change == "config_value":
            h.config.name = "Generated changed in place"
        elif change == "saved_profile":
            h.store.save_connection(replace(h.config, name="Generated saved replacement"), True,
                remember_ssh=True, remember_ssh_key=True, remember_ssl=True)
        elif change == "remember_choice":
            h.store.state["remembered_secrets"][identifier] = ["ssh_password"]
            h.store.flush()
        else:
            replacement = CloseFixture(h.config, gated=False)
            h.sessions.append(replacement)
            h.window.sessions[identifier] = replacement
        expected_config = h.window.configs[identifier]
        expected_store, expected_vault = h.store.path.read_bytes(), dict(h.vault)
    finally:
        h.session.release.set()
    idle(qtbot, h.window)
    # The continuation is deliberately delivered only once the owner's job exits.
    qtbot.waitUntil(lambda: "此次连接没有删除" in h.window.statusBar().currentMessage())
    assert h.window.configs[identifier] is expected_config
    assert h.store.path.read_bytes() == expected_store and h.vault == expected_vault
    assert h.session.closed and not h.errors
    if change == "session":
        assert h.window.sessions[identifier] is replacement and not replacement.closed


@pytest.mark.parametrize("fail", [False, True])
def test_synchronous_delete_commits_only_after_success(make_host, fail):
    h = make_host("sqlite", fail=fail)
    saved, credentials = h.store.path.read_bytes(), dict(h.vault)
    if fail:
        with pytest.raises(DatabaseError, match="Generated close failure"):
            h.window.remove_connection(h.config.id)
        assert h.window.configs[h.config.id] is h.config
        assert h.store.path.read_bytes() == saved and h.vault == credentials
    else:
        h.window.remove_connection(h.config.id)
        assert h.session.closed and h.config.id not in h.window.sessions
        assert h.config.id not in h.window.configs and h.store.connections() == []
        assert h.vault == {}
    assert len(h.session.close_threads) == 1


def test_disconnected_profile_delete_and_user_cancel_do_not_construct_session(make_host, monkeypatch):
    h = make_host("oracle", connected=False)
    saved, credentials = h.store.path.read_bytes(), dict(h.vault)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    h.window.remove_connection(h.config.id)
    assert h.store.path.read_bytes() == saved and h.vault == credentials
    assert h.config.id in h.window.configs and not h.window.tasks
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    h.window.remove_connection(h.config.id)
    assert h.config.id not in h.window.configs and h.store.connections() == [] and h.vault == {}
    assert not h.window.tasks and not h.errors
