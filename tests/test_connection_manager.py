"""Real selector interactions and URI handoff; not a claim of UI parity."""
from dataclasses import fields
from pathlib import Path

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialog, QLineEdit, QListView, QPushButton

from opennavicat import database
from opennavicat.database import ConnectionConfig
from opennavicat.ui.connection_manager import (
    ConnectionManagerDialog, ConnectionURIError, parse_connection_uri,
)
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.theme import apply_theme


SUPPORTED = {"sqlite", "postgresql", "mysql", "mariadb"}


def show_manager(qtbot, **kwargs):
    dialog = ConnectionManagerDialog(**kwargs)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    return dialog


def labels(dialog):
    return [dialog.connection_list.item(i).text() for i in range(dialog.connection_list.count())]


def select_card(qtbot, dialog, name):
    rows = dialog.connection_list.findItems(name, Qt.MatchFlag.MatchExactly)
    assert len(rows) == 1
    item = rows[0]
    dialog.connection_list.scrollToItem(item)
    qtbot.mouseClick(dialog.connection_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=dialog.connection_list.visualItemRect(item).center())
    assert dialog.connection_list.currentItem() is item
    return item


def select_filter(qtbot, dialog, item):
    dialog.filter_tree.scrollToItem(item)
    qtbot.mouseClick(dialog.filter_tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=dialog.filter_tree.visualItemRect(item).center())
    assert dialog.filter_tree.currentItem() is item


def test_driver_choices_and_direct_aliases_remain_visible_with_actual_settings_handoff(qtbot, monkeypatch):
    monkeypatch.setattr(database, "SUPPORTED_DRIVERS", SUPPORTED, raising=False)
    dialog = show_manager(qtbot)
    assert labels(dialog) == ["MySQL", "PostgreSQL", "SQL Server", "Oracle", "SQLite",
                              "MariaDB", "MongoDB", "Redis", "Snowflake",
                              "MongoDB Atlas", "Redis Enterprise Cloud"]
    assert not dialog.next_button.isEnabled()
    spy = QSignalSpy(dialog.selectionRequested)
    select_card(qtbot, dialog, "Oracle")
    assert not dialog.next_button.isEnabled()
    assert "尚未实现" in dialog.detail_label.text()
    qtbot.mouseDClick(dialog.connection_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=dialog.connection_list.visualItemRect(dialog.connection_list.currentItem()).center())
    assert spy.count() == 0 and dialog.isVisible()
    select_card(qtbot, dialog, "PostgreSQL")
    assert dialog.next_button.isEnabled()
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert spy.count() == 1
    kind, preset = spy.at(0)
    assert kind == preset["driver"] == "postgresql"
    assert preset["port"] == 5432
    # Feed the real existing settings dialog, without a session or Store.
    settings = ConnectionDialog(config=ConnectionConfig(id="selector-test", **preset))
    qtbot.addWidget(settings)
    assert settings.driver_combo.currentData() == "postgresql"
    assert settings.host_edit.text() == "localhost"
    assert settings.port_spin.value() == 5432
    settings.reject()


def test_registry_and_dynamic_adapter_availability(qtbot, monkeypatch):
    monkeypatch.setattr(database, "SUPPORTED_DRIVERS", SUPPORTED | {"sqlserver", "redis"}, raising=False)
    dialog = show_manager(qtbot)
    select_card(qtbot, dialog, "SQL Server")
    assert dialog.next_button.isEnabled()
    dialog.set_supported_kinds(SUPPORTED)
    assert dialog.connection_list.currentItem().text() == "SQL Server"
    assert not dialog.next_button.isEnabled()
    dialog.set_supported_kinds(SUPPORTED | {"redis"})
    select_card(qtbot, dialog, "Redis")
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    kind, preset = spy.at(0)
    assert kind == "redis" and preset["database"] == "0"
    assert preset["port"] == 6379 and preset["ssl_mode"] == "disable"


def test_observed_vendor_filters_search_modes_and_generic_alias_handoff(qtbot):
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    assert list(dialog.vendor_items) == [
        "Alibaba Cloud", "Huawei", "Tencent Cloud", "Kingbase", "PingCAP", "HighGo",
        "OceanBase", "AWS", "Fujitsu", "Google Cloud", "Microsoft", "Oracle",
    ]
    select_filter(qtbot, dialog, dialog.vendor_items["AWS"])
    assert set(labels(dialog)) == {
        "Amazon RDS for MySQL", "Amazon RDS for PostgreSQL", "Amazon RDS for SQL Server",
        "Amazon RDS for Oracle", "Amazon RDS for MariaDB", "Amazon Aurora for MySQL",
        "Amazon Aurora for PostgreSQL", "Amazon Redshift", "Amazon DocumentDB",
        "Amazon ElastiCache for Redis",
    }
    qtbot.keyClicks(dialog.search_edit, "aUrOrA")
    assert set(labels(dialog)) == {"Amazon Aurora for MySQL", "Amazon Aurora for PostgreSQL"}
    select_card(qtbot, dialog, "Amazon Aurora for PostgreSQL")
    assert "专属认证" in dialog.detail_label.text()
    assert "尚未验证" in dialog.detail_label.text()
    qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
    assert dialog.connection_list.viewMode() == QListView.ViewMode.ListMode
    assert dialog.connection_list.currentItem().text() == "Amazon Aurora for PostgreSQL"
    qtbot.mouseClick(dialog.grid_button, Qt.MouseButton.LeftButton)
    assert dialog.connection_list.viewMode() == QListView.ViewMode.IconMode
    assert dialog.connection_list.currentItem().text() == "Amazon Aurora for PostgreSQL"
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    kind, preset = spy.at(0)
    assert kind == "postgresql"
    assert preset["name"] == "Amazon Aurora for PostgreSQL"
    assert set(preset) <= {field.name for field in fields(ConnectionConfig)}
    assert not any(key.startswith("ssh") for key in preset)


def test_search_discovery_no_results_clears_selection_and_unsupported_alias(qtbot):
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    select_card(qtbot, dialog, "MySQL")
    assert dialog.next_button.isEnabled()
    qtbot.keyClicks(dialog.search_edit, "not-a-connection-kind")
    assert labels(dialog) == [] and dialog.empty_label.isVisible()
    assert not dialog.next_button.isEnabled()
    dialog.search_edit.setText("redis enterprise")
    assert labels(dialog) == ["Redis Enterprise Cloud"]
    select_card(qtbot, dialog, "Redis Enterprise Cloud")
    assert not dialog.next_button.isEnabled()
    dialog.search_edit.clear()
    select_filter(qtbot, dialog, dialog.vendor_items["Alibaba Cloud"])
    select_card(qtbot, dialog, "PolarDB for Xscale")
    assert not dialog.next_button.isEnabled()
    assert "尚未确认" in dialog.detail_label.text()
    select_filter(qtbot, dialog, dialog.driver_items["sqlite"])
    assert labels(dialog) == ["SQLite"]


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_native_rendering_and_no_excluded_product_account_entries(qtbot, qapp, mode):
    previous_theme = qapp.property("workbenchTheme") or "dark"
    try:
        apply_theme(qapp, mode)
        dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
        select_card(qtbot, dialog, "SQLite")
        qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        image = dialog.grab().toImage()
        assert not image.isNull() and image.width() >= 760
        text = " ".join(widget.text() for widget in dialog.findChildren(QPushButton))
        assert not any(word in text for word in ("登录", "订阅", "激活", "注册"))
    finally:
        apply_theme(qapp, previous_theme)


def test_uri_dialog_real_handoff_is_masked_and_never_creates_sqlite_file(qtbot, tmp_path):
    target = tmp_path / "example database.sqlite3"
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
    uri = dialog.uri_dialog
    qtbot.waitUntil(uri.isVisible)
    assert uri.uri_edit.echoMode() == QLineEdit.EchoMode.Password
    uri.uri_edit.setText("sqlite:///" + target.as_posix().replace(" ", "%20") + "?mode=ro")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1
    kind, preset = spy.at(0)
    assert kind == "sqlite"
    assert Path(preset["database"]) == target
    assert preset["read_only"] is True
    assert not target.exists()
    assert uri.uri_edit.text() == ""
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_uri_password_and_tls_reach_real_settings_only_in_memory(qtbot):
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
    uri = dialog.uri_dialog
    uri.uri_edit.setText("postgresql://demo%20user:synth%40tic%3A%E5%AF%86%E7%A0%81@[::1]:5544/example%2Fdb?sslmode=verify-full&connect_timeout=7")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    kind, preset = spy.at(0)
    assert kind == "postgresql"
    assert preset["host"] == "::1" and preset["database"] == "example/db"
    assert preset["username"] == "demo user" and preset["password"] == "synth@tic:密码"
    assert preset["ssl_mode"] == "verify-full" and preset["connect_timeout"] == 7
    assert uri.uri_edit.text() == ""
    settings = ConnectionDialog(config=ConnectionConfig(id="uri-memory-test", **preset))
    qtbot.addWidget(settings)
    assert settings.password_edit.text() == "synth@tic:密码"
    assert settings.password_edit.echoMode() == QLineEdit.EchoMode.Password
    assert settings.ssl_combo.currentData() == "verify-full"
    assert settings.timeout_spin.value() == 7
    settings.reject()


def test_uri_errors_do_not_echo_credentials_or_emit_and_cancel_clears(qtbot, caplog):
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
    uri = dialog.uri_dialog
    uri.uri_edit.setText("mysql://demo:synthetic-private-secret@localhost/example?unsupported=secret")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    assert uri.error_label.text() and "synthetic-private-secret" not in uri.error_label.text()
    assert "secret" not in uri.error_label.text()
    assert spy.count() == 0 and uri.isVisible() and dialog.isVisible()
    uri.uri_edit.setText("redis://demo:synthetic-private-secret@localhost/0")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    assert "尚未实现" in uri.error_label.text() and spy.count() == 0
    qtbot.mouseClick(uri.cancel_button, Qt.MouseButton.LeftButton)
    assert uri.uri_edit.text() == "" and dialog.isVisible()
    assert "synthetic-private-secret" not in caplog.text


def test_overlong_uri_cannot_silently_lose_tls_suffix_on_paste(qtbot):
    dialog = show_manager(qtbot, supported_kinds=SUPPORTED)
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
    uri = dialog.uri_dialog
    prefix = "mysql://demo:synthetic-long-secret@localhost/"
    uri.uri_edit.setText(prefix + "a" * (32768 - len(prefix)) + "?sslmode=verify-full")
    qtbot.mouseClick(uri.next_button, Qt.MouseButton.LeftButton)
    assert uri.isVisible() and spy.count() == 0
    assert "32768" in uri.error_label.text()
    assert "synthetic-long-secret" not in uri.error_label.text()


@pytest.mark.parametrize("value, expected", [
    ("sqlite:///:memory:", {"driver": "sqlite", "database": ":memory:"}),
    ("sqlite:///relative%20file.db?mode=rwc", {"database": "relative file.db", "read_only": False}),
    ("sqlite:////tmp/example%23one.sqlite3?mode=ro", {"database": "/tmp/example#one.sqlite3", "read_only": True}),
    ("sqlite:///C:/Examples/database.sqlite3", {"database": "C:/Examples/database.sqlite3"}),
    ("postgres://u:p@db.example/demo", {"driver": "postgresql", "port": 5432, "password": "p"}),
    ("mysql://u:p@db.example/demo?sslmode=require", {"driver": "mysql", "port": 3306, "ssl_mode": "require"}),
    ("mariadb://u:p@db.example:3307/demo", {"driver": "mariadb", "port": 3307}),
    ("sqlserver://u:p@db.example/demo?sslmode=verify-full", {"driver": "sqlserver", "port": 1433, "ssl_mode": "verify-full"}),
    ("sqlserver://u:p@db.example/demo", {"ssl_mode": "verify-full"}),
    ("sqlserver://u:p@db.example/demo?sslmode=require&sslrootcert=%2Ftmp%2Fca.pem", {"ssl_mode": "require", "ssl_ca": "/tmp/ca.pem"}),
    ("redis://:p@localhost/0002", {"driver": "redis", "database": "2", "ssl_mode": "disable"}),
    ("rediss://acl:p@cache.example/0", {"driver": "redis", "username": "acl", "ssl_mode": "verify-full"}),
    ("redis://localhost", {"database": "0", "port": 6379}),
    ("postgresql://localhost/db?sslmode=verify-ca&sslrootcert=%2Ftmp%2Fca.pem&sslcert=%2Ftmp%2Fclient.crt&sslkey=%2Ftmp%2Fclient.key&sslpassword=memory-only", {"ssl_ca": "/tmp/ca.pem", "ssl_cert": "/tmp/client.crt", "ssl_key": "/tmp/client.key", "ssl_key_password": "memory-only"}),
])
def test_uri_paths_credentials_and_explicit_tls_semantics(value, expected):
    actual = parse_connection_uri(value)
    assert {key: actual.get(key) for key in expected} == expected
    assert set(actual) <= {field.name for field in fields(ConnectionConfig)}


@pytest.mark.parametrize("value", [
    "", "mysql:localhost/demo", "oracle://user:do-not-echo@localhost/db",
    "mysql://u:do-not-echo@host:0/db", "mysql://u:do-not-echo@host:65536/db",
    "mysql://u:do-not-echo@host:nope/db", "mysql://u:do-not-echo@host:/db",
    "mysql://u:do-not-echo@[broken/db", "mysql://u:do-not-echo@/db",
    "postgresql://u:do-not-echo@[::1]ignored:5432/db",
    "mysql://u:do-not-echo@host1,host2/db", "mysql://u:do-not-echo@host/db/extra",
    "mysql://u:do-not-echo@host/db#fragment", "mysql://u:do-not-echo@host/db?foo=1",
    "mysql://u:do-not-echo@host/db?sslmode=require&SSLmode=disable",
    "mysql://u:do-not-echo@host/db?sslmode=allow", "mysql://u:do-not-echo@host/db?sslmode=verify-full&sslcert=client.crt",
    "mysql://u:do-not-echo@host/db?sslrootcert=ca.pem",
    "postgresql://u:do-not-echo@host/db?sslmode=disable&sslrootcert=ca.pem",
    "postgresql://u:do-not-echo@host/db?connect_timeout=-1",
    "postgresql://u:do-not-echo@host/db?connect_timeout=31536001",
    "postgresql://u:do-not-echo@host/db?sslmode", "postgresql://u:do-not-echo@host/%FF",
    "postgresql://u:do-not-echo@host/%00", "mysql://u:do-not-echo@host/%zz",
    "mysql://u:do-not-echo@host/a b", "mysql://u:do-not-echo@host/db?sslmode=require%00",
    "sqlite://remotehost/example.db", "sqlite://", "sqlite:///file:example.db",
    "sqlite:///demo.db?mode=rw", "sqlite:///demo.db?sslmode=disable",
    "sqlserver://u:do-not-echo@host/db?sslmode=prefer", "redis://host/-1",
    "sqlserver://u:do-not-echo@host/db?sslmode=verify-full&sslcrl=list.pem",
    "sqlserver://u:do-not-echo@host/db?sslmode=verify-full&sslcert=client.pem&sslkey=key.pem",
    "redis://host/2147483648", "redis://host/" + "9" * 5000,
    "postgresql://u:do-not-echo@host/db?connect_timeout=" + "9" * 5000,
    "redis://host/example", "redis://host/0?sslmode=prefer", "rediss://host/0?sslmode=disable",
])
def test_invalid_or_unrepresentable_uri_fails_closed_without_secret(value):
    with pytest.raises(ConnectionURIError) as captured:
        parse_connection_uri(value)
    assert "do-not-echo" not in str(captured.value)
