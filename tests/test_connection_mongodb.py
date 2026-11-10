"""Mongo settings and native URI handoff, with no discovery or implicit I/O."""
from dataclasses import fields
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog, QLineEdit

from opennavicat import database
from opennavicat.database import ConnectionConfig
from opennavicat.ui.connection_manager import (
    ConnectionManagerDialog, ConnectionURIError, parse_connection_uri,
)
from opennavicat.ui.dialogs import ConnectionDialog


def settings(qtbot, config=None):
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    if config is None:
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("mongodb"))
        dialog.name_edit.setText("Synthetic Mongo")
    dialog.show()
    return dialog


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_mongo_general_none_defaults_and_password_toggle_do_not_connect(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(database.DatabaseSession, "connect", lambda *_: calls.append("connect"))
    dialog = settings(qtbot)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["常规", "高级", "数据库", "SSL", "SSH", "备注"]
    assert dialog.port_spin.value() == 27017 and not dialog.ssl_enabled_check.isChecked()
    assert dialog.mongo_auth_combo.isVisible() and dialog.mongo_topology_combo.isVisible()
    assert dialog.mongo_auth_combo.currentData() == "NONE"
    assert dialog.mongo_topology_combo.currentData() == "standalone"
    assert not dialog.mongo_srv_check.isEnabled() and not dialog.mongo_srv_check.isChecked()
    assert dialog.mongo_auth_source_edit.text() == ""
    assert not dialog.user_edit.isVisible() and not dialog.password_edit.isVisible()
    assert not dialog.remote_database_edit.isVisible() and not dialog.database_row.isVisible()
    # Hidden stale password widgets cannot inject credentials into None auth.
    dialog.user_edit.setText("discarded-user")
    dialog.password_edit.setText("discarded-secret")
    dialog.remember_check.setChecked(True)
    config = dialog._collect_config()
    assert config.driver == "mongodb" and config.database == "" and config.ssl_mode == "disable"
    assert config.mongo_auth_mechanism == "NONE" and config.mongo_auth_source == "admin"
    assert config.username == config.password == "" and not dialog.remember_password
    choose(dialog.mongo_auth_combo, "PASSWORD")
    assert dialog.user_edit.isVisible() and dialog.password_edit.isVisible()
    assert dialog.mongo_auth_source_edit.isVisible()
    assert dialog.password_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog._collect_config().password == "discarded-secret"
    choose(dialog.mongo_auth_combo, "NONE")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.get_config().password == ""
    assert not calls and not dialog._test_pool.activeThreadCount()


def test_password_scram_empty_password_auth_source_and_retry_flags_roundtrip(qtbot):
    profile = ConnectionConfig("mongo-password", "Synthetic", driver="mongodb", database="default_db",
        host="example.invalid", port=27027, username="fixture-user", password="",
        ssl_mode="disable", mongo_auth_mechanism="SCRAM-SHA-256", mongo_auth_source="authentication_db",
        mongo_retry_reads=False, mongo_retry_writes=False, read_only=True)
    dialog = settings(qtbot, profile)
    assert dialog.mongo_auth_combo.currentData() == "PASSWORD"
    assert dialog.mongo_mechanism_combo.currentData() == "SCRAM-SHA-256"
    assert dialog.port_spin.value() == 27027
    assert dialog.mongo_auth_source_edit.text() == "authentication_db"
    config = dialog._collect_config()
    assert config.username == "fixture-user" and config.password == ""
    assert config.database == "default_db" and config.mongo_auth_source == "authentication_db"
    assert not config.mongo_retry_reads and not config.mongo_retry_writes and config.read_only
    dialog.mongo_auth_source_edit.clear()
    choose(dialog.mongo_mechanism_combo, "DEFAULT")
    assert dialog._collect_config().mongo_auth_source == "admin"
    assert dialog._collect_config().mongo_auth_mechanism == "DEFAULT"
    dialog.user_edit.clear()
    with pytest.raises(ValueError, match="用户名"):
        dialog._collect_config()


@pytest.mark.parametrize("field,value,control,message", [
    ("mongo_auth_mechanism", "GSSAPI", "mongo_auth_combo", "验证方式"),
    ("mongo_auth_mechanism", "CUSTOM-AUTH", "mongo_auth_combo", "验证方式"),
    ("mongo_connection_type", "replica_set", "mongo_topology_combo", "Standalone"),
    ("mongo_connection_type", "unknown-topology", "mongo_topology_combo", "Standalone"),
    ("mongo_read_preference", "secondary", "mongo_read_preference_combo", "primary"),
])
def test_unknown_saved_mongo_modes_stay_visible_and_fail_closed(qtbot, field, value, control, message):
    profile = ConnectionConfig("mongo-unknown", "Synthetic", driver="mongodb", ssl_mode="disable", **{field:value})
    dialog = settings(qtbot, profile)
    combo = getattr(dialog, control)
    assert combo.currentData() == value and not combo.model().item(combo.currentIndex()).isEnabled()
    with pytest.raises(ValueError, match=message):
        dialog._collect_config()
    choose(combo, "NONE" if field == "mongo_auth_mechanism" else "standalone" if field == "mongo_connection_type" else "primary")
    assert dialog._collect_config().driver == "mongodb"


def test_saved_srv_and_member_settings_require_explicit_removal(qtbot):
    profile = ConnectionConfig("mongo-topology", "Synthetic", driver="mongodb", ssl_mode="disable",
        mongo_srv=True, mongo_replica_set="rs0", mongo_members="host1:27017,host2:27017")
    dialog = settings(qtbot, profile)
    assert dialog.mongo_srv_check.isEnabled() and dialog.mongo_srv_check.isChecked()
    with pytest.raises(ValueError, match="SRV"):
        dialog._collect_config()
    dialog.mongo_srv_check.setChecked(False)
    assert not dialog.mongo_srv_check.isEnabled()
    with pytest.raises(ValueError, match="成员"):
        dialog._collect_config()
    dialog.mongo_replica_set_edit.clear()
    dialog.mongo_members_edit.clear()
    assert not dialog._collect_config().mongo_srv
    assert not dialog._collect_config().mongo_members


def test_mongo_tls_and_ssh_preserve_four_independent_secret_flags(qtbot, tmp_path):
    paths = {}
    for name in ("ca", "cert", "key", "crl", "ssh"):
        path = tmp_path / (name + ".pem")
        path.write_text("Readable fixture; cryptographic parsing belongs to worker connection.")
        paths[name] = str(path)
    profile = ConnectionConfig("mongo-secure", "Synthetic", driver="mongodb", username="user", password="db-secret",
        ssl_mode="verify-full", ssl_ca=paths["ca"], ssl_cert=paths["cert"], ssl_key=paths["key"], ssl_crl=paths["crl"],
        ssl_key_password="tls-secret", ssh_enabled=True, ssh_host="ssh.example.invalid", ssh_username="ssh-user",
        ssh_auth_method="password+key", ssh_password="ssh-secret", ssh_key_file=paths["ssh"],
        ssh_key_passphrase="key-secret", ssh_host_key="ssh-ed25519 synthetic-public-pin")
    dialog = settings(qtbot, profile)
    assert dialog.remember_password and dialog.remember_ssl_password
    assert dialog.remember_ssh_password and dialog.remember_ssh_key_passphrase
    config = dialog._collect_config()
    assert config.ssl_ca == paths["ca"] and config.ssl_crl == paths["crl"]
    assert config.ssl_key_password == "tls-secret" and config.ssh_key_passphrase == "key-secret"
    assert config.ssh_host_key == profile.ssh_host_key
    choose(dialog.ssl_combo, "require")
    with pytest.raises(ValueError, match="不能被静默忽略"):
        dialog._collect_config()
    choose(dialog.ssl_combo, "verify-ca")
    dialog.ssl_key_edit.setText(str(tmp_path / "missing.pem"))
    with pytest.raises(ValueError, match="无法读取"):
        dialog._collect_config()
    dialog.ssl_key_edit.setText(paths["key"])
    dialog.ssh_host_edit.setText("other.example.invalid")
    assert dialog._collect_config().ssh_host_key == ""


def test_mongo_native_test_connects_and_closes_in_worker_without_sql(qtbot, monkeypatch):
    gui_thread = threading.get_ident()
    calls = []
    class NativeSession:
        def __init__(self, config):
            assert config.driver == "mongodb"
            assert threading.get_ident() != gui_thread
        def connect(self):
            calls.append(("authenticated-ping", threading.get_ident()))
        def close(self):
            calls.append(("close", threading.get_ident()))
        def execute(self, *args, **kwargs):
            pytest.fail("Mongo connection probe must not send SQL or invented shell PING")
    monkeypatch.setattr(database, "DatabaseSession", NativeSession)
    dialog = settings(qtbot)
    spy = QSignalSpy(dialog.test_finished)
    qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: spy.count() == 1 and not dialog._test_pool.activeThreadCount())
    assert spy.at(0)[0] is True
    assert [name for name, _ in calls] == ["authenticated-ping", "close"]
    assert all(thread != gui_thread for _, thread in calls)
    assert dialog.get_config() is None


def test_mongo_uri_handoff_is_memory_only_and_native_auth_defaults_reach_settings(qtbot, monkeypatch):
    monkeypatch.setattr(database.DatabaseSession, "connect", lambda *_: pytest.fail("URI parsing attempted I/O"))
    dialog = ConnectionManagerDialog(supported_kinds={"mongodb"})
    qtbot.addWidget(dialog)
    dialog.show()
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
    uri = dialog.uri_dialog
    assert uri.uri_edit.echoMode() == QLineEdit.EchoMode.Password
    uri.uri_edit.setText("mongodb://demo%40user:synthetic%3Asecret@[::1]:27018/auth_db?authMechanism=SCRAM-SHA-256&tls=true&retryWrites=false")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1 and uri.uri_edit.text() == ""
    kind, preset = spy.at(0)
    assert kind == "mongodb" and set(preset) <= {field.name for field in fields(ConnectionConfig)}
    profile = ConnectionConfig(id="uri-mongo", **preset)
    target = settings(qtbot, profile)
    assert target.mongo_auth_source_edit.text() == "auth_db"
    config = target._collect_config()
    assert config.username == "demo@user" and config.password == "synthetic:secret"
    assert config.port == 27018 and config.host == "::1" and config.ssl_mode == "verify-full"
    assert not config.mongo_retry_writes and config.mongo_retry_reads


@pytest.mark.parametrize("suffix,expected", [
    ("", {"mongo_auth_source":"admin", "mongo_auth_mechanism":"DEFAULT", "ssl_mode":"disable"}),
    ("/app", {"mongo_auth_source":"app", "database":"app"}),
    ("/app?authSource=admin", {"mongo_auth_source":"admin", "database":"app"}),
    ("/?authSource=users&authMechanism=SCRAM-SHA-1", {"mongo_auth_source":"users", "mongo_auth_mechanism":"SCRAM-SHA-1"}),
    ("/?tls=true", {"ssl_mode":"verify-full"}),
    ("/?ssl=true&tlsAllowInvalidHostnames=true", {"ssl_mode":"verify-ca"}),
    ("/?tls=true&tlsAllowInvalidCertificates=true", {"ssl_mode":"require"}),
    ("/?tls=true&tlsInsecure=true", {"ssl_mode":"require"}),
    ("/?tlsCAFile=%2Fsynthetic%2Fca.pem", {"ssl_mode":"verify-full", "ssl_ca":"/synthetic/ca.pem"}),
    ("/?tls=true&tlsCRLFile=%2Fsynthetic%2Frevoke.pem&tlsAllowInvalidHostnames=true", {"ssl_mode":"verify-ca", "ssl_crl":"/synthetic/revoke.pem"}),
    ("/?directConnection=true&readPreference=primary&retryReads=false&retryWrites=false", {"mongo_retry_reads":False, "mongo_retry_writes":False}),
    ("/?connectTimeoutMS=7000&serverSelectionTimeoutMS=7000", {"connect_timeout":7}),
])
def test_mongo_native_uri_semantics(suffix, expected):
    result = parse_connection_uri("mongodb://user:synthetic@localhost" + suffix)
    assert result["driver"] == "mongodb" and result["port"] == 27017
    assert result["username"] == "user" and result["password"] == "synthetic"
    for key, value in expected.items():
        assert result[key] == value


def test_mongo_unauthenticated_uri_does_not_invent_credentials():
    config = parse_connection_uri("mongodb://localhost/optional_db")
    assert config["username"] == config["password"] == ""
    assert config["mongo_auth_mechanism"] == "NONE"


@pytest.mark.parametrize("uri", [
    "mongodb+srv://user:synthetic-private@server.invalid/app",
    "mongodb://user:synthetic-private@host1,host2/app",
    "mongodb://user:synthetic-private@localhost/?replicaSet=rs0",
    "mongodb://user:synthetic-private@localhost/?directConnection=false",
    "mongodb://user:synthetic-private@localhost/?authMechanism=GSSAPI",
    "mongodb://user:synthetic-private@localhost/?authMechanism=NONE",
    "mongodb://user:synthetic-private@localhost/?authMechanism=DEFAULT",
    "mongodb://user:synthetic-private@localhost/?authSource=",
    "mongodb://:synthetic-private@localhost/",
    "mongodb://user:synthetic-private:unencoded@localhost/",
    "mongodb://user:synthetic-private@localhost/?readPreference=secondary",
    "mongodb://user:synthetic-private@localhost/?retryWrites=1",
    "mongodb://user:synthetic-private@localhost/?TLS=true&tls=false",
    "mongodb://user:synthetic-private@localhost/?tls=true&ssl=true",
    "mongodb://user:synthetic-private@localhost/?tlsInsecure=true",
    "mongodb://user:synthetic-private@localhost/?tls=false&tlsCAFile=ca.pem",
    "mongodb://user:synthetic-private@localhost/?tls=true&tlsCAFile=ca.pem&tlsInsecure=true",
    "mongodb://user:synthetic-private@localhost/?tls=true&tlsInsecure=false&tlsAllowInvalidHostnames=true",
    "mongodb://user:synthetic-private@localhost/?tls=true&tlsCertificateKeyFile=client.pem",
    "mongodb://user:synthetic-private@localhost/?tlsCertificateKeyFilePassword=synthetic-private",
    "mongodb://user:synthetic-private@localhost/?tls=true&tlsCRLFile=",
    "mongodb://user:synthetic-private@localhost/?connectTimeoutMS=1500",
    "mongodb://user:synthetic-private@localhost/?connectTimeoutMS=3000",
    "mongodb://user:synthetic-private@localhost/?connectTimeoutMS=3000&serverSelectionTimeoutMS=4000",
    "mongodb://user:synthetic-private@localhost/?serverSelectionTimeoutMS=3000",
    "mongodb://user:synthetic-private@localhost/?socketTimeoutMS=9000",
    "mongodb://user:synthetic-private@localhost/a%2Fb",
    "mongodb://localhost/?authMechanism=SCRAM-SHA-256",
])
def test_unrepresentable_mongo_uri_is_rejected_without_secret_in_error(uri):
    with pytest.raises(ConnectionURIError) as error:
        parse_connection_uri(uri)
    assert "synthetic-private" not in str(error.value)
    assert uri not in str(error.value)
