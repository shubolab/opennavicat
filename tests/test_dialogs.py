"""Native connection editor layout and explicitly requested connection tests."""
import sqlite3
import threading
import base64

import pytest

from PySide6.QtCore import QPoint, Qt, QThread
from PySide6.QtWidgets import QDialog, QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.ui.dialogs import ConnectionDialog, confirm_ssh_host_key


def test_native_connection_tabs_profiles_and_config(qtbot):
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: (dialog.frameGeometry().width(), dialog.frameGeometry().height()) == (749, 668))
    assert dialog.width() <= dialog.frameGeometry().width()
    assert dialog.height() <= dialog.frameGeometry().height()
    assert dialog.profile_pane.isVisible()
    assert dialog.profile_pane.mapTo(dialog, QPoint()) == QPoint(20, 20)
    assert dialog.profile_pane.width() == 200
    assert dialog.settings_pane.mapTo(dialog, QPoint()) == QPoint(240, 20)
    assert dialog.footer.height() == 60
    assert dialog.driver_combo.isHidden()
    assert dialog.sqlite_mode_combo.isHidden()
    assert dialog.sqlite_existing_radio.isChecked()
    assert dialog.sqlite_create_radio.isEnabled()
    assert not dialog.sqlite_legacy_radio.isEnabled()
    assert "尚未实现" in dialog.sqlite_legacy_radio.toolTip()
    password_bottom = dialog.password_edit.mapTo(dialog, QPoint(0, dialog.password_edit.height())).y()
    assert dialog.remember_check.mapTo(dialog, QPoint()).y() > password_bottom
    assert dialog.remember_check.mapTo(dialog, QPoint()).x() == dialog.password_edit.mapTo(dialog, QPoint()).x()
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["常规", "高级", "数据库", "HTTP"]
    assert not dialog.user_edit.isEnabled()
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("postgresql"))
    qtbot.waitUntil(lambda: (dialog.frameGeometry().width(), dialog.frameGeometry().height()) == (574, 668))
    assert not dialog.profile_pane.isVisible()
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["常规", "高级", "数据库", "SSL", "SSH", "HTTP"]
    assert dialog.port_spin.value() == 5432
    assert dialog.user_edit.text() == dialog.database_edit.text() == "postgres"
    assert dialog.user_edit.isEnabled()
    qtbot.mouseClick(dialog.profile_toggle, Qt.MouseButton.LeftButton)
    assert dialog.profile_pane.isVisible()
    dialog.name_edit.setText("Local PostgreSQL")
    dialog.password_edit.setText("unsaved-only")
    dialog.ssl_enabled_check.setChecked(False)
    dialog.accept()
    config = dialog.get_config()
    assert config.driver == "postgresql"
    assert config.database == "postgres"
    assert config.ssl_mode == "disable"
    assert config.password == "unsaved-only"


def test_sqlite_existing_or_new_is_explicit_and_save_does_not_connect(qtbot, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    database = tmp_path / "new.sqlite3"
    dialog.name_edit.setText("New SQLite")
    dialog.database_edit.setText(str(database))
    dialog.accept()
    assert dialog.get_config() is None
    assert errors and "不存在" in errors[0]
    assert not database.exists()
    qtbot.mouseClick(dialog.sqlite_create_radio, Qt.MouseButton.LeftButton,
                     pos=QPoint(8, dialog.sqlite_create_radio.height() // 2))
    assert dialog.sqlite_mode_combo.currentData() == "create"
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.get_config().database == str(database)
    assert not database.exists()
    assert not dialog._test_pool.activeThreadCount()


def test_test_connection_reads_real_sqlite_and_closes_session(qtbot, tmp_path, monkeypatch):
    database = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE original (value TEXT)")
        db.execute("INSERT INTO original VALUES ('keep me')")
    closed = []
    original_close = DatabaseSession.close

    def close(session):
        original_close(session)
        closed.append(session.connection is None)

    monkeypatch.setattr(DatabaseSession, "close", close)
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.database_edit.setText(str(database))
    dialog.show()
    assert closed == []
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing)
    assert result.args == [True, "连接成功"]
    assert closed == [True]
    assert dialog.test_button.isEnabled()
    assert dialog.get_config() is None
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT * FROM original").fetchall() == [("keep me",)]
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_test_connection_rejects_corrupt_sqlite(qtbot, tmp_path):
    database = tmp_path / "corrupt.sqlite3"
    database.write_text("This is not a SQLite database.")
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.database_edit.setText(str(database))
    dialog.show()
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing)
    assert result.args[0] is False
    assert "database" in result.args[1].lower()
    assert dialog.test_status.text() == "连接失败"


def test_inflight_connection_test_blocks_closing_and_never_starts_implicitly(qtbot, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    class TestSession:
        def __init__(self, config):
            calls.append(("session", config.host))

        def connect(self):
            started.set()
            assert release.wait(5)

        def execute(self, *_args, **_kwargs):
            calls.append("select")

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("opennavicat.database.DatabaseSession", TestSession)
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("postgresql"))
    dialog.host_edit.setText("127.0.0.1")
    dialog.show()
    assert calls == []
    try:
        with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
            qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(started.is_set)
            assert not dialog.buttons.isEnabled()
            assert not dialog.test_button.isEnabled()
            dialog.accept()
            dialog.reject()
            dialog.close()
            assert dialog.isVisible()
            assert dialog.get_config() is None
            release.set()
        qtbot.waitUntil(lambda: not dialog._testing)
        assert result.args[0] is True
    finally:
        release.set()
        qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())
    assert calls == [("session", "127.0.0.1"), "select", "closed"]
    dialog.close()
    assert not dialog.isVisible()


def transport_profile(tmp_path):
    files = {}
    for name in ("ssl_ca", "ssl_cert", "ssl_key", "ssl_crl", "ssh_key_file"):
        path = tmp_path / name
        path.write_text("Readable fixture; cryptographic parsing is owned by the connection worker.")
        files[name] = str(path)
    pin = "ssh-ed25519 " + base64.b64encode(b"fixture-public-host-key").decode("ascii")
    return ConnectionConfig("transport-fixture", "TLS and SSH", "postgresql", "fixture",
        host="database.invalid", port=5432, username="dbuser", ssl_mode="verify-ca", connect_timeout=31,
        ssl_key_password="TLS private key phrase", ssh_enabled=True, ssh_host="jump.invalid",
        ssh_port=2222, ssh_username="jumpuser", ssh_auth_method="password+key",
        ssh_password="SSH password", ssh_key_passphrase="SSH private key phrase",
        ssh_compression=True, ssh_host_key=pin, **files)


def test_ssl_ssh_values_prepopulate_and_host_pin_tracks_endpoint(qtbot, tmp_path):
    profile = transport_profile(tmp_path)
    dialog = ConnectionDialog(config=profile)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.timeout_spin.isEnabled()
    assert dialog.timeout_spin.value() == 31
    assert dialog.ssl_combo.currentData() == "verify-ca"
    assert dialog.ssl_verify_check.isChecked()
    assert dialog.ssl_ca_edit.isEnabled()
    assert dialog.ssh_auth_combo.currentData() == "password+key"
    assert not dialog.ssh_password_edit.isHidden()
    assert not dialog.ssh_key_field.isHidden()
    assert dialog.remember_ssh_password
    assert dialog.remember_ssh_key_passphrase
    assert dialog.remember_ssl_password
    for title, control in [("SSL", dialog.remember_ssl_check), ("SSH", dialog.ssh_compression_check)]:
        dialog.tabs.setCurrentIndex(next(index for index in range(dialog.tabs.count()) if dialog.tabs.tabText(index) == title))
        qtbot.wait(1)
        assert control.mapTo(dialog, QPoint(0, control.height())).y() < dialog.footer.y()
    collected = dialog._collect_config()
    assert collected == profile
    dialog.ssh_user_edit.setText("different-user-same-server")
    assert dialog._collect_config().ssh_host_key == profile.ssh_host_key
    dialog.ssh_host_edit.setText("JUMP.INVALID")
    assert dialog._collect_config().ssh_host_key == profile.ssh_host_key
    dialog.ssh_port_spin.setValue(22)
    assert dialog._collect_config().ssh_host_key == ""
    dialog.ssh_port_spin.setValue(2222)
    assert dialog._collect_config().ssh_host_key == ""


def test_ssl_file_validation_is_applicable_and_certificate_pair_is_required(qtbot, tmp_path):
    dialog = ConnectionDialog(config=transport_profile(tmp_path))
    qtbot.addWidget(dialog)
    dialog.ssl_cert_edit.clear()
    with pytest.raises(ValueError, match="同时设置"):
        dialog._collect_config()
    dialog.ssl_cert_edit.setText(str(tmp_path / "missing certificate"))
    with pytest.raises(ValueError, match="无法读取客户端证书"):
        dialog._collect_config()
    # Disabling custom authentication files must stop applying the stale path;
    # verify-ca still uses the platform trust store through the real backend.
    dialog.ssl_verify_check.setChecked(False)
    config = dialog._collect_config()
    assert config.ssl_mode == "verify-ca"
    assert (config.ssl_ca, config.ssl_cert, config.ssl_key, config.ssl_crl, config.ssl_key_password) == ("", "", "", "", "")
    assert not dialog.ssl_cert_edit.isEnabled()
    assert not dialog.remember_ssl_password
    dialog.ssl_enabled_check.setChecked(False)
    assert dialog._collect_config().ssl_mode == "disable"


def test_ssh_auth_rows_validation_and_independent_secret_preferences(qtbot, tmp_path):
    dialog = ConnectionDialog(config=transport_profile(tmp_path))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("password"))
    assert not dialog.ssh_password_edit.isHidden()
    assert dialog.ssh_key_field.isHidden()
    assert dialog.remember_ssh_password
    assert not dialog.remember_ssh_key_passphrase
    dialog.ssh_key_edit.setText(str(tmp_path / "missing key ignored for password authentication"))
    dialog._collect_config()
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("key"))
    assert dialog.ssh_password_edit.isHidden()
    assert not dialog.ssh_key_field.isHidden()
    assert not dialog.remember_ssh_password
    assert dialog.remember_ssh_key_passphrase
    with pytest.raises(ValueError, match="无法读取SSH 私钥"):
        dialog._collect_config()
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("agent"))
    assert dialog.ssh_password_edit.isHidden()
    assert dialog.ssh_key_field.isHidden()
    assert not dialog.remember_ssh_password
    assert not dialog.remember_ssh_key_passphrase
    dialog.ssh_host_edit.clear()
    with pytest.raises(ValueError, match="SSH 主机及用户名"):
        dialog._collect_config()
    dialog.ssh_enabled_check.setChecked(False)
    assert not dialog._collect_config().ssh_enabled


class HostKeyFixtureError(RuntimeError):
    def __init__(self, *, expected=None):
        super().__init__("SSH host key mismatch" if expected else "SSH host key is not trusted")
        self.hostname = "127.0.0.1"
        self.port = 22022
        self.key_type = "ssh-ed25519"
        self.key_base64 = base64.b64encode(b"fixture-public-key").decode("ascii")
        self.fingerprint = "SHA256:the_actual_fixture_fingerprint"
        self.expected = expected

    @property
    def pin(self):
        return self.key_type + " " + self.key_base64


def test_host_key_prompt_displays_actual_identity_defaults_no_and_rejects_mismatch(qtbot, monkeypatch):
    questions, errors = [], []

    def question(*args):
        questions.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    unknown = HostKeyFixtureError()
    assert not confirm_ssh_host_key(dialog, unknown)
    assert "127.0.0.1:22022" in questions[0][2]
    assert unknown.key_type in questions[0][2]
    assert unknown.fingerprint in questions[0][2]
    assert questions[0][-1] == QMessageBox.StandardButton.No
    assert not confirm_ssh_host_key(dialog, HostKeyFixtureError(expected=unknown.pin))
    assert len(questions) == 1
    assert errors and "不匹配" in errors[0][1]


@pytest.mark.parametrize("approval", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_async_ssh_trust_occurs_on_gui_after_close_and_retries_only_explicit_yes(qtbot, monkeypatch, approval):
    import opennavicat.transport as transport
    monkeypatch.setattr(transport, "SSHHostKeyError", HostKeyFixtureError)
    calls, questions, closed = [], [], []

    class Session:
        def __init__(self, config):
            self.config = config

        def connect(self):
            assert threading.current_thread() is not threading.main_thread()
            calls.append(self.config.ssh_host_key)
            if not self.config.ssh_host_key:
                raise HostKeyFixtureError()

        def execute(self, *_args, **_kwargs):
            return []

        def close(self):
            closed.append(True)

    def question(*args):
        assert QThread.currentThread() == dialog.thread()
        assert len(closed) == 1  # The failed attempt has released its transport.
        assert dialog._testing
        assert not dialog.buttons.isEnabled()
        questions.append(args)
        return approval

    monkeypatch.setattr("opennavicat.database.DatabaseSession", Session)
    monkeypatch.setattr(QMessageBox, "question", question)
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("postgresql"))
    dialog.ssh_enabled_check.setChecked(True)
    dialog.ssh_host_edit.setText("127.0.0.1")
    dialog.ssh_port_spin.setValue(22022)
    dialog.ssh_user_edit.setText("fixture")
    dialog.show()
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    assert len(questions) == 1
    if approval == QMessageBox.StandardButton.Yes:
        assert result.args[0] is True
        assert calls == ["", HostKeyFixtureError().pin]
        assert len(closed) == 2
        assert dialog._collect_config(testing=True).ssh_host_key == HostKeyFixtureError().pin
    else:
        assert result.args[0] is False
        assert calls == [""]
        assert len(closed) == 1
        assert dialog._collect_config(testing=True).ssh_host_key == ""
    assert not dialog._testing


def test_async_ssh_mismatch_never_offers_acceptance_or_retries(qtbot, monkeypatch):
    import opennavicat.transport as transport
    monkeypatch.setattr(transport, "SSHHostKeyError", HostKeyFixtureError)
    saved_pin = "ssh-ed25519 " + base64.b64encode(b"previously-trusted-key").decode("ascii")
    config = ConnectionConfig("mismatch", "Local key mismatch", "postgresql", "postgres",
        port=5432, ssl_mode="disable", ssh_enabled=True, ssh_host="127.0.0.1",
        ssh_port=22022, ssh_username="fixture", ssh_host_key=saved_pin)
    attempts, closed, alerts = [], [], []

    class Session:
        def __init__(self, profile):
            attempts.append(profile.ssh_host_key)

        def connect(self):
            raise HostKeyFixtureError(expected=saved_pin)

        def close(self):
            closed.append(True)

    def question(*_args):
        raise AssertionError("A mismatched host key must never offer trust acceptance")

    monkeypatch.setattr("opennavicat.database.DatabaseSession", Session)
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: alerts.append(args))
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    dialog.show()
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    assert not result.args[0]
    assert attempts == [saved_pin]
    assert closed == [True]
    assert len(alerts) == 1
    assert dialog._collect_config().ssh_host_key == saved_pin
    assert not dialog._testing
