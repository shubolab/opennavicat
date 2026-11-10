"""Main-window authentication routing; protocol behavior has separate tests."""
from dataclasses import replace
import json
import threading

import paramiko
import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.storage import Store
from opennavicat.transport import SSHAuthenticationError, SSHHostKeyError, TLSKeyPasswordError
import opennavicat.ui.window as window_module


@pytest.fixture(scope="module")
def host_key():
    return paramiko.RSAKey.generate(2048)


@pytest.fixture
def setup_connection(qtbot, tmp_path, monkeypatch):
    errors, attempts, closed, prompts = [], [], [], []
    scenario = [lambda config: None]
    gui_thread = threading.get_ident()

    class ControlledSession:
        def __init__(self, config):
            self.config = config
            self.in_transaction = False

        def connect(self):
            assert threading.get_ident() != gui_thread
            attempts.append(replace(self.config))
            scenario[0](self.config)

        def schemas(self):
            return []

        def objects(self, schema):
            return []

        def close(self):
            closed.append(self.config.id)

    monkeypatch.setattr(window_module, "DatabaseSession", ControlledSession)
    monkeypatch.setattr(window_module.MainWindow, "error", lambda self, error: errors.append(error))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: pytest.fail("Unexpected credential prompt"))
    store = Store(tmp_path)
    config = ConnectionConfig("security", "Owned security test", "postgresql", "postgres",
        ssh_enabled=True, ssh_host="owned.invalid", ssh_username="test")
    store.save_connection(config)
    window = window_module.MainWindow(store)
    qtbot.addWidget(window)
    window.show()
    yield window, store, scenario, attempts, errors, closed, prompts, gui_thread
    qtbot.waitUntil(lambda: not window.tasks and not window._connecting and not window.pool.activeThreadCount(), timeout=5000)
    window.close()


def test_main_unknown_host_requires_gui_approval_and_preserves_public_pin(qtbot, setup_connection, monkeypatch, host_key):
    window, store, scenario, attempts, errors, closed, prompts, gui_thread = setup_connection
    expected = SSHHostKeyError("owned.invalid", 22, host_key)
    def connect(config):
        if not config.ssh_host_key:
            raise expected
        assert config.ssh_host_key == expected.pin
    scenario[0] = connect
    def approve(*args):
        assert threading.get_ident() == gui_thread
        assert not window.tasks and not window._connecting
        assert closed == ["security"]  # failed transport closed before approval
        prompts.append(args)
        assert args[-1] == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", approve)
    completed = []
    window.connect_id("security", lambda: completed.append(True))
    qtbot.waitUntil(lambda: bool(completed), timeout=5000)
    assert len(attempts) == 2 and len(prompts) == 1 and not errors
    saved = json.loads(store.path.read_text("utf-8"))["connections"][0]
    assert saved["ssh_host_key"] == expected.pin
    for field in Store.SECRET_FIELDS:
        assert saved[field] == ""
    assert "security" in window.sessions


def test_declining_host_key_does_not_retry_or_persist(qtbot, setup_connection, monkeypatch, host_key):
    window, store, scenario, attempts, errors, closed, prompts, _ = setup_connection
    def connect(config):
        raise SSHHostKeyError(config.ssh_host, config.ssh_port, host_key)
    scenario[0] = connect
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args) or QMessageBox.StandardButton.No)
    window.connect_id("security")
    qtbot.waitUntil(lambda: bool(prompts), timeout=5000)
    assert len(attempts) == 1 and not window.sessions and not errors
    assert store.state["connections"][0]["ssh_host_key"] == ""


def test_changed_key_has_no_acceptance_path(qtbot, setup_connection, monkeypatch, host_key):
    window, store, scenario, attempts, errors, closed, prompts, _ = setup_connection
    previous = paramiko.RSAKey.generate(2048)
    expected = f"{previous.get_name()} {previous.get_base64()}"
    def connect(config):
        raise SSHHostKeyError(config.ssh_host, config.ssh_port, host_key, expected)
    scenario[0] = connect
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: prompts.append(args) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("A changed key must not be accepted"))
    window.connect_id("security")
    qtbot.waitUntil(lambda: bool(prompts), timeout=5000)
    assert len(attempts) == 1 and not window.sessions and not errors
    assert store.state["connections"][0]["ssh_host_key"] == ""


@pytest.mark.parametrize("field,failure", [
    ("password", DatabaseError("Password required", authentication_required=True)),
    ("ssh_password", SSHAuthenticationError("password")),
    ("ssh_key_passphrase", SSHAuthenticationError("key", needs_passphrase=True)),
    ("ssl_key_password", TLSKeyPasswordError()),
])
def test_unremembered_credentials_prompt_only_after_real_rejection(qtbot, setup_connection, monkeypatch, field, failure):
    window, store, scenario, attempts, errors, closed, prompts, gui_thread = setup_connection
    def connect(config):
        if not getattr(config, field):
            raise failure
    scenario[0] = connect
    def enter(*args):
        assert threading.get_ident() == gui_thread
        assert len(attempts) == 1 and not window.tasks and not window._connecting
        prompts.append(args)
        return "owned-test-secret", True
    monkeypatch.setattr(QInputDialog, "getText", enter)
    completed = []
    window.connect_id("security", lambda: completed.append(True))
    qtbot.waitUntil(lambda: bool(completed), timeout=5000)
    assert len(attempts) == 2 and len(prompts) == 1 and not errors
    assert getattr(attempts[1], field) == "owned-test-secret"
    assert "owned-test-secret" not in store.path.read_text("utf-8")
    assert store.secret(store.connections()[0], field) == "owned-test-secret"


def test_intentionally_empty_database_password_connects_without_prompt(qtbot, setup_connection):
    window, store, scenario, attempts, errors, closed, prompts, _ = setup_connection
    completed = []
    window.connect_id("security", lambda: completed.append(True))
    qtbot.waitUntil(lambda: bool(completed), timeout=5000)
    assert len(attempts) == 1 and attempts[0].password == "" and not errors


def test_network_failure_never_requests_database_password(qtbot, setup_connection, monkeypatch):
    window, store, scenario, attempts, errors, closed, prompts, _ = setup_connection
    def connect(config):
        raise DatabaseError("Connection refused")
    scenario[0] = connect
    window.connect_id("security")
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    assert len(attempts) == 1 and not window.sessions
    assert not store.state["remembered_passwords"]
