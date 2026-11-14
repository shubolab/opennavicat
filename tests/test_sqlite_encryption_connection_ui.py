"""GUI credential capture and the asynchronous SQLite creation protocol."""
import json
import threading
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_encryption_config import bound_sqlite_encryption_password, with_sqlite_encryption_password
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, with_sqlite_database_password
from opennavicat.ui.dialogs import ConnectionDialog


def dialog(qtbot, tmp_path, *, remembered=False):
    path = tmp_path / "generated-owned.sqlite"
    path.touch()
    config = ConnectionConfig("generated-main", "Generated main", database=str(path), sqlite_open_mode="existing",
        sqlite_encryption_enabled=True, sqlite_cipher_name="aes256cbc", read_only=True, connect_timeout=37)
    if remembered:
        config = with_sqlite_encryption_password(config, "generated remembered key")
    widget = ConnectionDialog(config=config)
    qtbot.addWidget(widget)
    widget.show()
    return widget, config


def test_only_sqlite_uses_reference_advanced_tab_and_retains_runtime_options(qtbot, tmp_path):
    widget, config = dialog(qtbot, tmp_path)
    assert widget.tabs.widget(1) is widget.sqlite_advanced_tab
    assert widget.user_edit.isEnabled() and widget.remember_check.isEnabled()
    assert widget._collect_config().read_only and widget._collect_config().connect_timeout == 37
    widget.driver_combo.setCurrentIndex(widget.driver_combo.findData("postgresql"))
    assert widget.tabs.widget(1) is widget.advanced_tab
    widget.driver_combo.setCurrentIndex(widget.driver_combo.findData("sqlite"))
    assert widget.tabs.widget(1) is widget.sqlite_advanced_tab
    assert widget.read_only_check.isChecked() and widget.timeout_spin.value() == 37


def test_test_password_prompt_cancel_starts_nothing_and_does_not_save(qtbot, tmp_path, monkeypatch):
    widget, _ = dialog(qtbot, tmp_path)
    calls = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("generated canceled input", False))
    monkeypatch.setattr(widget, "_start_connection_test", calls.append)
    widget.test_connection()
    assert calls == [] and not widget._testing and widget.get_config() is None
    assert bound_sqlite_encryption_password(widget._collect_config()) == ""


def test_test_captures_password_on_gui_and_remember_off_never_copies_it_to_draft(qtbot, tmp_path, monkeypatch):
    widget, original = dialog(qtbot, tmp_path, remembered=True)
    widget.sqlite_advanced_tab.remember_check.setChecked(False)
    gui_thread = QThread.currentThread()
    prompts, snapshots = [], []
    def prompt(*args):
        assert QThread.currentThread() is gui_thread
        assert args[-1] == widget.password_edit.EchoMode.Password
        prompts.append(args)
        return "generated prompt key", True
    monkeypatch.setattr(QInputDialog, "getText", prompt)
    monkeypatch.setattr(widget, "_start_connection_test", snapshots.append)
    widget.test_connection(); widget.test_connection()
    assert len(prompts) == len(snapshots) == 2
    assert all(bound_sqlite_encryption_password(cfg) == "generated prompt key" for cfg in snapshots)
    widget.database_edit.setText(str(tmp_path / "generated-other.sqlite"))
    assert all(cfg.database == original.database for cfg in snapshots)
    assert widget.sqlite_advanced_tab.password_edit.text() == ""
    assert bound_sqlite_encryption_password(original) == "generated remembered key"


def test_missing_remembered_key_is_explicit_input_and_stale_modal_never_rebinds_it(qtbot, tmp_path, monkeypatch):
    widget, original = dialog(qtbot, tmp_path)
    snapshots = []
    def prompt(*args):
        widget.sqlite_advanced_tab.cipher_combo.setEditText("chacha20")
        return "generated captured key", True
    monkeypatch.setattr(QInputDialog, "getText", prompt)
    monkeypatch.setattr(widget, "_start_connection_test", snapshots.append)
    widget.test_connection()
    assert snapshots[0].sqlite_cipher_name == "aes256cbc"
    assert bound_sqlite_encryption_password(snapshots[0]) == "generated captured key"
    assert widget.sqlite_advanced_tab.cipher_combo.currentText() == "chacha20"
    assert widget.sqlite_advanced_tab.password_edit.text() == ""
    assert bound_sqlite_encryption_password(widget._collect_config()) == ""


def test_loaded_key_is_used_only_for_unchanged_remembered_binding(qtbot, tmp_path, monkeypatch):
    widget, original = dialog(qtbot, tmp_path, remembered=True)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: (_ for _ in ()).throw(AssertionError("Unexpected prompt")))
    snapshots = []
    monkeypatch.setattr(widget, "_start_connection_test", snapshots.append)
    widget.test_connection()
    assert bound_sqlite_encryption_password(snapshots[0]) == "generated remembered key"
    assert widget.remember_sqlite_encryption_password


def test_attachment_passwords_keep_current_cipher_and_do_not_revive_after_revert(qtbot, tmp_path):
    path = tmp_path / "generated-main.sqlite"; path.touch()
    config = ConnectionConfig("generated-with-attachment", "Generated with attachment", database=str(path),
        sqlite_cipher_name="aes256cbc", sqlite_databases=(SQLiteDatabaseEntry(
            "generated-attachment", "generated_aux", str(tmp_path / "generated-aux.sqlite"), True),))
    config = with_sqlite_database_password(config, "generated-attachment", "generated auxiliary key")
    widget = ConnectionDialog(config=config)
    qtbot.addWidget(widget)
    assert widget._collect_config().sqlite_database_passwords == {"generated-attachment": "generated auxiliary key"}
    widget.sqlite_advanced_tab.cipher_combo.setEditText("chacha20")
    current = widget.sqlite_databases_page.snapshot()
    assert current.sqlite_cipher_name == "chacha20" and current.sqlite_database_passwords == {}
    widget.sqlite_advanced_tab.cipher_combo.setEditText("aes256cbc")
    assert widget._collect_config().sqlite_database_passwords == {}
    current = with_sqlite_database_password(widget.sqlite_databases_page.snapshot(), "generated-attachment", "generated new auxiliary key")
    widget.sqlite_databases_page._replace_draft(current)
    assert widget._collect_config().sqlite_database_passwords == {"generated-attachment": "generated new auxiliary key"}


def test_sqlite_general_auth_input_is_retained_visually_and_explicitly_rejected(qtbot, tmp_path, monkeypatch):
    widget, _ = dialog(qtbot, tmp_path)
    errors, work = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(widget, "_start_connection_test", work.append)
    widget.user_edit.setText("generated database user")
    widget.test_connection()
    assert not work and "验证尚未实现" in errors[0]
    assert widget.user_edit.text() == "generated database user"


def test_create_has_no_test_and_no_handler_cannot_accept_or_create(qtbot, tmp_path, monkeypatch):
    widget = ConnectionDialog()
    qtbot.addWidget(widget)
    path = tmp_path / "generated-new.sqlite"
    widget.name_edit.setText("Generated new")
    widget.database_edit.setText(str(path))
    widget.sqlite_mode_combo.setCurrentIndex(widget.sqlite_mode_combo.findData("create"))
    errors, work = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(widget, "_start_connection_test", work.append)
    assert not widget.test_button.isEnabled()
    widget.test_connection(); widget.accept()
    assert not path.exists() and not work and not widget._testing
    assert widget.get_config() is None and "后台创建队列" in errors[0]


def test_creation_token_failure_retry_and_success_use_captured_snapshot(qtbot, tmp_path, monkeypatch):
    widget = ConnectionDialog()
    qtbot.addWidget(widget)
    path = tmp_path / "generated-create.sqlite"
    widget.name_edit.setText("Generated create")
    widget.database_edit.setText(str(path))
    widget.sqlite_mode_combo.setCurrentIndex(widget.sqlite_mode_combo.findData("create"))
    requests, errors = [], []
    widget.create_requested.connect(lambda token, config: requests.append((token, config)))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    widget.show(); widget.accept()
    first, signaled = requests[0]
    assert widget._creating and widget._testing and widget.get_config() is None
    assert not widget.buttons.isEnabled() and not widget.content.isEnabled()
    widget.accept(); widget.reject(); widget.close()
    assert widget.isVisible() and len(requests) == 1
    # Signal consumers cannot mutate the dialog's private success snapshot.
    signaled.database = "generated-mutated-worker-copy.sqlite"
    widget.finish_sqlite_creation(object(), error=ValueError("stale"))
    assert widget._creating
    widget.finish_sqlite_creation(first, error=ValueError("generated creation failed"))
    assert errors == ["generated creation failed"]
    assert widget.isVisible() and not widget._creating and not widget._testing
    assert widget.sqlite_mode_combo.currentData() == "create" and not widget.test_button.isEnabled()
    widget.accept()
    second, captured = requests[1]
    widget.finish_sqlite_creation(first)
    assert widget._creating and widget.get_config() is None
    widget.finish_sqlite_creation(second)
    assert widget.result() == QDialog.DialogCode.Accepted
    saved = widget.get_config()
    assert saved.database == str(path) and saved.sqlite_open_mode == "existing"
    assert not path.exists()  # This is a signal/GUI protocol test, not a database worker.


def test_encrypted_test_worker_failure_closes_and_redacts_its_captured_key(qtbot, tmp_path, monkeypatch):
    widget, _ = dialog(qtbot, tmp_path)
    secret = "generated ' key\\line 密码"
    widget.sqlite_advanced_tab.set_password(secret)
    gui_thread, workers = threading.get_ident(), []
    class FailingSession:
        def __init__(self, config):
            self.config = config
        def connect(self):
            workers.append(("connect", threading.get_ident()))
            assert bound_sqlite_encryption_password(self.config) == secret
            raise RuntimeError("generated worker error " + secret + " " + repr(secret) + " " + json.dumps(secret))
        def close(self):
            workers.append(("close", threading.get_ident()))
    monkeypatch.setattr("opennavicat.database.DatabaseSession", FailingSession)
    with qtbot.waitSignal(widget.test_finished, timeout=5000) as result:
        widget.test_connection()
    qtbot.waitUntil(lambda: not widget._test_pool.activeThreadCount())
    assert result.args[0] is False and "[已隐藏密码]" in result.args[1]
    for spelling in (secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]):
        assert spelling not in result.args[1] and spelling not in widget.test_status.toolTip()
    assert [kind for kind, _ in workers] == ["connect", "close"]
    assert all(thread != gui_thread for _, thread in workers)
    assert not widget._testing and widget.content.isEnabled() and widget.buttons.isEnabled()
    assert widget._test_sqlite_secrets == () and widget._test_error is None
    widget.close()
    assert not widget.isVisible()


def test_creation_error_redacts_prompted_key_and_restores_the_draft(qtbot, tmp_path, monkeypatch):
    widget = ConnectionDialog()
    qtbot.addWidget(widget)
    widget.name_edit.setText("Generated encrypted create")
    widget.database_edit.setText(str(tmp_path / "generated-secret-create.sqlite"))
    widget.sqlite_mode_combo.setCurrentIndex(widget.sqlite_mode_combo.findData("create"))
    widget.sqlite_advanced_tab.encrypted_check.setChecked(True)
    widget.set_remember_sqlite_encryption(False)
    secret = "generated prompt only key"
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: (secret, True))
    requests, messages = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    widget.create_requested.connect(lambda token, snapshot: requests.append((token, snapshot)))
    widget.show(); widget.accept()
    token, captured = requests[0]
    assert bound_sqlite_encryption_password(captured) == secret
    assert widget.sqlite_advanced_tab.password_edit.text() == ""
    widget.finish_sqlite_creation(token, error=ValueError("generated creation failure " + secret))
    assert secret not in messages[0] and "[已隐藏密码]" in messages[0]
    assert not widget._creating and not widget._testing and widget.isVisible()
    assert widget.buttons.isEnabled() and widget.content.isEnabled()
    assert widget.get_config() is None and not widget.test_button.isEnabled()
