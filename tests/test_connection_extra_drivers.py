"""SQL Server/Redis profile collection and explicitly requested worker probes."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from opennavicat.database import ConnectionConfig
from opennavicat.ui.dialogs import ConnectionDialog


def dialog_for(qtbot, driver, config=None):
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    if config is None:
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
        dialog.name_edit.setText("Owned profile")
    dialog.show()
    return dialog


def test_sqlserver_defaults_sql_authentication_and_save_without_connecting(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr("opennavicat.database.DatabaseSession.connect", lambda *_args: calls.append("network"))
    dialog = dialog_for(qtbot, "sqlserver")
    assert dialog.port_spin.value() == 1433
    assert dialog.database_edit.text() == dialog.remote_database_edit.text() == "master"
    assert dialog.user_edit.text() == ""
    assert dialog.sqlserver_auth_combo.isVisible() and not dialog.redis_mode_combo.isVisible()
    assert dialog.sqlserver_auth_combo.currentData() == "sql"
    assert not dialog.sqlserver_auth_combo.model().item(1).isEnabled()
    assert not dialog.read_only_check.isEnabled()
    assert dialog.ssl_enabled_check.isChecked() and dialog.ssl_combo.currentData() == "verify-full"
    with pytest.raises(ValueError, match="登录名"):
        dialog._collect_config()
    dialog.user_edit.setText("fixture_login")
    dialog.password_edit.setText("process-only-secret")
    dialog.remember_check.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    config = dialog.get_config()
    assert (config.driver, config.database, config.port, config.username, config.password) == (
        "sqlserver", "master", 1433, "fixture_login", "process-only-secret")
    assert dialog.remember_password and not config.read_only
    assert not calls


def test_sqlserver_tls_supported_modes_and_custom_ca_only(qtbot, tmp_path):
    ca = tmp_path / "owned-ca.pem"
    ca.write_text("Readable CA fixture; actual TLS parsing occurs in the worker.")
    dialog = dialog_for(qtbot, "sqlserver")
    dialog.user_edit.setText("fixture_login")
    assert [dialog.ssl_combo.itemData(i) for i in range(dialog.ssl_combo.count())] == ["require", "verify-ca", "verify-full"]
    dialog.ssl_verify_check.setChecked(True)
    assert dialog.ssl_ca_edit.isEnabled()
    assert not dialog.ssl_cert_edit.isEnabled() and not dialog.ssl_key_edit.isEnabled()
    assert not dialog.ssl_crl_edit.isEnabled() and not dialog.ssl_key_password_edit.isEnabled()
    assert not dialog.ssl_remember_check.isEnabled() and not dialog.remember_ssl_password
    dialog.ssl_ca_edit.setText(str(ca))
    for mode in ("require", "verify-ca", "verify-full"):
        dialog.ssl_combo.setCurrentIndex(dialog.ssl_combo.findData(mode))
        config = dialog._collect_config()
        assert config.ssl_mode == mode and config.ssl_ca == str(ca)
        assert not config.ssl_cert and not config.ssl_key and not config.ssl_crl and not config.ssl_key_password
    assert "验证证书链" in dialog.ssl_combo.itemData(dialog.ssl_combo.findData("require"), Qt.ItemDataRole.ToolTipRole)
    dialog.ssl_enabled_check.setChecked(False)
    assert dialog._collect_config().ssl_mode == "disable"


@pytest.mark.parametrize("field", ["ssl_cert", "ssl_key", "ssl_crl", "ssl_key_password"])
def test_sqlserver_rejects_unsupported_tls_prefill_even_when_tls_is_disabled(qtbot, field):
    profile = ConnectionConfig("sql-unsupported", "Unsupported TLS", driver="sqlserver", database="master",
        username="fixture_login", ssl_mode="disable", **{field: "unsupported-value"})
    dialog = dialog_for(qtbot, "sqlserver", profile)
    assert not dialog.ssl_enabled_check.isChecked()
    with pytest.raises(ValueError, match="不支持客户端"):
        dialog._collect_config()


@pytest.mark.parametrize("mode", ["allow", "prefer"])
def test_sqlserver_saved_unsupported_tls_mode_requires_explicit_change(qtbot, mode):
    profile = ConnectionConfig("sql-mode", "Unsupported mode", driver="sqlserver", database="master",
        username="fixture_login", ssl_mode=mode)
    dialog = dialog_for(qtbot, "sqlserver", profile)
    assert dialog.ssl_combo.currentData() == mode
    with pytest.raises(ValueError, match="SSL 模式"):
        dialog._collect_config()
    dialog.ssl_combo.setCurrentIndex(dialog.ssl_combo.findData("verify-full"))
    assert dialog._collect_config().ssl_mode == "verify-full"


def test_sqlserver_unsupported_auth_and_readonly_are_explicitly_rejected(qtbot):
    profile = ConnectionConfig("original-id", "Read protection", driver="sqlserver", database="master",
        username="fixture_login", ssl_mode="disable", read_only=True)
    dialog = dialog_for(qtbot, "sqlserver", profile)
    assert dialog.read_only_check.isChecked() and dialog.read_only_check.isEnabled()
    with pytest.raises(ValueError, match="只读"):
        dialog._collect_config()
    dialog.read_only_check.setChecked(False)
    assert not dialog.read_only_check.isEnabled()
    dialog.sqlserver_auth_combo.setCurrentIndex(1)
    with pytest.raises(ValueError, match="集成身份验证"):
        dialog._collect_config()
    dialog.sqlserver_auth_combo.setCurrentIndex(0)
    assert dialog._collect_config().id == "original-id"


def test_redis_defaults_acl_blank_username_and_driver_switches(qtbot):
    dialog = dialog_for(qtbot, "postgresql")
    assert dialog.user_edit.text() == dialog.database_edit.text() == "postgres"
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("redis"))
    assert dialog.port_spin.value() == 6379 and dialog.database_edit.text() == "0"
    assert dialog.user_edit.text() == ""
    assert dialog.remote_database_label.text() == "数据库编号"
    assert dialog.redis_mode_combo.isVisible() and not dialog.sqlserver_auth_combo.isVisible()
    assert dialog.redis_mode_combo.currentData() == "standalone"
    assert dialog.user_edit.isEnabled() and not dialog.sqlite_type_widget.isVisible()
    assert not dialog.ssl_enabled_check.isChecked()
    assert [dialog.ssl_combo.itemData(i) for i in range(dialog.ssl_combo.count())] == ["require", "verify-ca", "verify-full"]
    config = dialog._collect_config()
    assert (config.driver, config.port, config.database, config.username, config.password, config.ssl_mode) == ("redis", 6379, "0", "", "", "disable")
    dialog.database_edit.setText("0007")
    assert dialog._collect_config().database == "7"
    dialog.user_edit.setText("fixture-acl")
    dialog.password_edit.setText("acl-secret")
    dialog.remember_check.setChecked(True)
    assert dialog._collect_config().username == "fixture-acl" and dialog.remember_password


@pytest.mark.parametrize("database", ["", "-1", "1.5", "example", "１", "1;FLUSHALL"])
def test_redis_database_rejects_non_ascii_nonnegative_integer(qtbot, database):
    dialog = dialog_for(qtbot, "redis")
    dialog.remote_database_edit.setText(database)
    with pytest.raises(ValueError, match="数据库编号"):
        dialog._collect_config()


@pytest.mark.parametrize("mode", ["cluster", "sentinel"])
def test_redis_cluster_and_sentinel_are_visible_disabled_and_fail_closed(qtbot, mode):
    dialog = dialog_for(qtbot, "redis")
    index = dialog.redis_mode_combo.findData(mode)
    assert index >= 0 and not dialog.redis_mode_combo.model().item(index).isEnabled()
    dialog.redis_mode_combo.setCurrentIndex(index)
    with pytest.raises(ValueError, match="尚未实现"):
        dialog._collect_config()


def test_redis_tls_and_ssh_flags_roundtrip_without_implicit_network(qtbot, tmp_path):
    files = {}
    for name in ("ssl_ca", "ssl_cert", "ssl_key", "ssl_crl", "ssh_key_file"):
        path = tmp_path / name
        path.write_text("Readable configuration fixture; worker validates cryptography.")
        files[name] = str(path)
    profile = ConnectionConfig("redis-profile", "Owned Redis", driver="redis", host="redis.example.invalid",
        port=6380, database="3", username="fixture-acl", password="db-secret", ssl_mode="verify-full",
        ssl_key_password="tls-secret", ssh_enabled=True, ssh_host="ssh.example.invalid", ssh_username="fixture-ssh",
        ssh_auth_method="password+key", ssh_password="ssh-secret", ssh_key_passphrase="key-secret", **files)
    dialog = dialog_for(qtbot, "redis", profile)
    assert dialog.port_spin.value() == 6380 and dialog.database_edit.text() == "3"
    assert dialog.ssl_enabled_check.isChecked() and dialog.ssl_combo.currentData() == "verify-full"
    assert dialog.ssl_verify_check.isChecked()
    config = dialog._collect_config()
    assert config.driver == "redis" and config.ssl_key_password == "tls-secret"
    assert config.ssh_auth_method == "password+key" and config.ssh_key_passphrase == "key-secret"
    assert all(getattr(config, name) == value for name, value in files.items())
    assert dialog.remember_password and dialog.remember_ssl_password
    assert dialog.remember_ssh_password and dialog.remember_ssh_key_passphrase
    assert not dialog._testing and not dialog._test_pool.activeThreadCount()


def test_redis_does_not_silently_accept_saved_opportunistic_tls_mode(qtbot):
    profile = ConnectionConfig("redis-old", "Invalid saved mode", driver="redis", database="0", ssl_mode="prefer")
    dialog = dialog_for(qtbot, "redis", profile)
    assert dialog.ssl_enabled_check.isChecked()
    assert dialog.ssl_combo.currentData() == "prefer"
    assert not dialog.ssl_combo.model().item(dialog.ssl_combo.currentIndex()).isEnabled()
    with pytest.raises(ValueError, match="SSL 模式"):
        dialog._collect_config()
    dialog.ssl_combo.setCurrentIndex(dialog.ssl_combo.findData("require"))
    assert dialog._collect_config().ssl_mode == "require"


@pytest.mark.parametrize("driver,command", [("redis", "PING"), ("sqlserver", "SELECT 1")])
def test_extra_driver_connection_probe_only_runs_on_explicit_click_in_worker(qtbot, monkeypatch, driver, command):
    calls = []
    class Session:
        def __init__(self, config):
            self.config = config
            calls.append(("created", QThread.currentThread() is QApplication.instance().thread()))
        def connect(self):
            calls.append("connected")
        def execute(self, text, limit):
            calls.append((text, limit))
        def close(self):
            calls.append("closed")
    monkeypatch.setattr("opennavicat.database.DatabaseSession", Session)
    dialog = dialog_for(qtbot, driver)
    if driver == "sqlserver":
        dialog.user_edit.setText("fixture_login")
    assert calls == []
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing)
    assert result.args == [True, "连接成功"]
    assert calls == [("created", False), "connected", (command, 1), "closed"]
    assert dialog.get_config() is None
