"""OR18-01 form/state and worker contracts; no database or native CUA access."""
from dataclasses import asdict, replace
import gc
import threading
import weakref

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QInputDialog, QLineEdit, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store
from opennavicat.ui.connection_manager import ConnectionManagerDialog, supported_driver_kinds
from opennavicat.ui.oracle_connection import ORACLE_CHARACTER_SETS, OracleConnectionDialog


def profile(**changes):
    return replace(ConnectionConfig('generated-oracle', 'Generated Oracle', driver='oracle',
        host='database.invalid', port=1521, username='GENERATED_OWNER', password='',
        database='', ssl_mode='disable', oracle_service_identifier='GENERATED_SERVICE'), **changes)


def editor(qtbot, config=None):
    dialog = OracleConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_or18_five_pages_observed_defaults_and_profile_toggle(qtbot):
    dialog = editor(qtbot)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ['常规', '高级', '数据库', 'SSH', '备注']
    assert dialog.profile_pane.isVisible() and dialog.profile_pane.width() == 200
    assert dialog.profiles.item(0).text() == '主要配置文件（有效）'
    assert dialog.profiles.currentRow() == 0
    assert dialog.back_button.isVisible()
    assert dialog.host_edit.text() == '' and dialog.port_spin.value() == 1521
    assert dialog.oracle_service_edit.text() == 'ORCL'
    assert dialog.oracle_service_radio.isChecked()
    assert [dialog.oracle_type_combo.itemText(i) for i in range(dialog.oracle_type_combo.count())] == ['基本', 'TNS']
    assert [dialog.oracle_role_combo.itemText(i) for i in range(dialog.oracle_role_combo.count())] == ['默认', 'SYSDBA', 'SYSOPER']
    assert not dialog.oracle_os_auth_check.isChecked() and dialog.remember_check.isChecked()
    assert dialog.support_label.isHidden()
    assert not dialog.oracle_session_limit_spin.isEnabled() and dialog.oracle_session_limit_spin.value() == 0
    assert dialog.oracle_session_limit_spin.lineEdit().placeholderText() == '10'
    assert not dialog.oracle_keepalive_spin.isEnabled() and dialog.oracle_keepalive_spin.value() == 240
    assert dialog.oracle_charset_combo.currentText() == '自动'
    assert len(ORACLE_CHARACTER_SETS) == len(set(ORACLE_CHARACTER_SETS)) == 247
    assert dialog.oracle_charset_combo.count() == 248
    assert dialog.oracle_database_table.columnCount() == 3
    assert not dialog.oracle_database_table.isEnabled()
    assert not dialog.oracle_database_refresh.isEnabled()
    assert not dialog.ssh_enabled_check.isChecked() and dialog.ssh_port_spin.value() == 22
    assert [dialog.ssh_auth_combo.itemText(i) for i in range(dialog.ssh_auth_combo.count())] == ['密码', '公钥', '密码和公钥']
    qtbot.mouseClick(dialog.profile_toggle, Qt.MouseButton.LeftButton)
    assert not dialog.profile_pane.isVisible()
    assert dialog.tabs.count() == 5


def test_each_page_keeps_its_controls_above_the_shared_footer(qtbot):
    dialog = editor(qtbot, profile())
    for page, control in ((dialog.general_tab, dialog.remember_check),
            (dialog.advanced_tab, dialog.oracle_initial_query_edit),
            (dialog.databases_tab, dialog.oracle_database_refresh),
            (dialog.ssh_tab, dialog.ssh_key_field), (dialog.notes_tab, dialog.oracle_notes_edit)):
        dialog.tabs.setCurrentWidget(page)
        if page is dialog.ssh_tab:
            dialog.ssh_enabled_check.setChecked(True)
            dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData('password+key'))
        qtbot.waitUntil(lambda: dialog.height() > 0)
        from PySide6.QtCore import QPoint
        assert control.mapTo(dialog, QPoint(0, control.height())).y() < dialog.footer.y()
    assert not dialog.uri_button.isEnabled() and not dialog.new_profile_button.isEnabled()


def test_multiline_initial_query_editor_preserves_verbatim_text(qtbot, monkeypatch):
    from PySide6.QtWidgets import QPlainTextEdit
    expected = "SELECT 'generated' FROM DUAL;\n-- generated comment\nSELECT 2 FROM DUAL;\n"
    dialog = editor(qtbot, profile())
    def edit(child):
        child.findChild(QPlainTextEdit).setPlainText(expected)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', edit)
    dialog._edit_initial_query()
    assert dialog._collect_config().oracle_initial_query == expected
    assert dialog.oracle_initial_query_edit.text() == expected.replace('\n', ' ')


def test_tns_sid_os_auth_and_password_state_do_not_downgrade_draft(qtbot):
    dialog = editor(qtbot, profile(password='generated-only'))
    assert not dialog.back_button.isVisible()
    dialog.oracle_sid_radio.click()
    assert dialog.oracle_service_label.text() == 'SID:'
    assert dialog._collect_config().oracle_service_type == 'sid'
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData('tns'))
    assert dialog.host_edit.isHidden() and dialog.oracle_service_edit.isHidden()
    assert not dialog.oracle_tns_combo.isHidden()
    dialog.oracle_tns_combo.setEditText('GENERATED_ALIAS')
    dialog.oracle_os_auth_check.setChecked(True)
    assert dialog.user_edit.isHidden() and dialog.password_edit.isHidden()
    assert not dialog.remember_check.isEnabled()
    config = dialog._collect_config()
    assert (config.oracle_connection_type, config.oracle_service_type, config.oracle_tns_alias) == ('tns', 'sid', 'GENERATED_ALIAS')
    assert config.oracle_os_auth and config.password == ''
    assert dialog.support_label.text()
    dialog.oracle_os_auth_check.setChecked(False)
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData('basic'))
    dialog.oracle_service_radio.click()
    assert not dialog.user_edit.isHidden()
    dialog.remember_check.setChecked(False)
    assert dialog.password_edit.isHidden() and dialog.password_edit.text() == ''
    dialog.remember_check.setChecked(True)
    assert dialog.password_edit.text() == ''  # Unchecking actually discarded it.


def test_new_selector_preset_retains_fields_and_actual_new_form_state(qtbot):
    preset = profile(password='')
    dialog = OracleConnectionDialog(config=preset, is_new=True)
    qtbot.addWidget(dialog); dialog.show()
    assert dialog.back_button.isVisible() and dialog.remember_password
    assert dialog.windowTitle() == '新建连接 (Oracle)'
    assert dialog.oracle_service_edit.text() == preset.oracle_service_identifier
    assert dialog.host_edit.text() == preset.host
    assert dialog._collect_config().id == preset.id
    dialog.back_button.click()
    assert dialog.back_requested and dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize('method,password,key', [('password', True, False), ('key', False, True), ('password+key', True, True)])
def test_ssh_forms_keep_observed_method_rows_and_refuse_execution(qtbot, method, password, key):
    dialog = editor(qtbot, profile())
    dialog.tabs.setCurrentWidget(dialog.ssh_tab)
    dialog.ssh_enabled_check.setChecked(True)
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData(method))
    assert (not dialog.ssh_password_edit.isHidden()) is password
    assert (not dialog.ssh_key_field.isHidden()) is key
    if password: assert dialog.ssh_password_edit.isEnabled()
    if key: assert dialog.ssh_key_field.isEnabled()
    assert dialog._collect_config().ssh_auth_method == method
    assert 'SSH' in dialog.support_label.text()


def test_all_five_page_drafts_roundtrip_through_store_without_secret_fallback(qtbot, tmp_path, monkeypatch):
    import keyring
    writes = []
    monkeypatch.setattr(keyring, 'set_password', lambda *args: writes.append(args))
    monkeypatch.setattr(keyring, 'delete_password', lambda *_: None)
    source = profile(oracle_connection_type='tns', oracle_service_type='sid', oracle_tns_alias='GENERATED_ALIAS',
        oracle_role='sysoper', oracle_os_auth=True, oracle_settings_directory='generated-settings',
        oracle_auto_connect=True, oracle_session_limit_enabled=True, oracle_session_limit=2147483647,
        oracle_keepalive_enabled=False, oracle_keepalive_seconds=0, oracle_client_charset='AL32UTF8',
        oracle_initial_query='SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;',
        oracle_custom_database_list_enabled=True,
        oracle_custom_database_list=[dict(active=True, name='OWNER_B', auto_open=False), dict(active=False, name='OWNER_A', auto_open=True)],
        oracle_notes='Generated note\n第二行', ssh_enabled=True, ssh_host='jump.invalid', ssh_port=22022,
        ssh_username='generated', ssh_auth_method='password+key', ssh_password='generated-ssh-secret',
        ssh_key_file='generated-key', ssh_key_passphrase='generated-key-phrase', ssh_compression=True,
        read_only=True, ssl_mode='verify-full', ssl_ca='generated-ca', ssl_key_password='generated-tls-phrase')
    dialog = editor(qtbot, source)
    dialog.accept()
    saved = dialog.get_config()
    assert saved == source
    assert saved.oracle_custom_database_list is not source.oracle_custom_database_list
    store = Store(tmp_path)
    store.save_connection(saved)
    assert not writes
    raw = store.path.read_text()
    assert all(secret not in raw for secret in ('generated-ssh-secret', 'generated-key-phrase', 'generated-tls-phrase'))
    reopened = editor(qtbot, Store(tmp_path).connections()[0])
    reopened.accept()
    redacted = asdict(source)
    for key in Store.SECRET_FIELDS: redacted[key] = ''
    assert asdict(reopened.get_config()) == redacted
    assert reopened.oracle_initial_query_edit.text() == 'SELECT 1 FROM DUAL; SELECT 2 FROM DUAL;'


def test_remembered_oracle_password_uses_only_keyring_and_reopens(qtbot, tmp_path, monkeypatch):
    import keyring
    secrets = {}
    monkeypatch.setattr(keyring, 'get_password', lambda service, key: secrets.get((service, key)))
    monkeypatch.setattr(keyring, 'set_password', lambda service, key, value: secrets.__setitem__((service, key), value))
    monkeypatch.setattr(keyring, 'delete_password', lambda service, key: secrets.pop((service, key), None))
    dialog = editor(qtbot, profile())
    dialog.remember_check.setChecked(True)
    dialog.password_edit.setText('generated-remembered-password')
    dialog.accept()
    store = Store(tmp_path)
    store.save_connection(dialog.get_config(), remember=dialog.remember_password)
    assert secrets == {('OpenNavicat', 'generated-oracle'): 'generated-remembered-password'}
    assert 'generated-remembered-password' not in store.path.read_text()
    fresh_store = Store(tmp_path)
    reopened = editor(qtbot, fresh_store.hydrate(fresh_store.connections()[0]))
    assert reopened.remember_password and reopened.password_edit.text() == 'generated-remembered-password'
    reopened.remember_check.setChecked(False)
    reopened.accept()
    fresh_store.save_connection(reopened.get_config(), remember=reopened.remember_password)
    assert not secrets and fresh_store.connections()[0].password == ''
    assert Store(tmp_path).hydrate(Store(tmp_path).connections()[0]).password == ''


def test_custom_database_rows_local_order_changes_and_source_immutability(qtbot, monkeypatch):
    source = profile(oracle_custom_database_list=[dict(active=False, name='OWNER_A', auto_open=True)])
    dialog = editor(qtbot, source)
    dialog.oracle_custom_database_check.setChecked(True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: ('OWNER_B', True))
    dialog._add_database()
    assert [row['name'] for row in dialog._collect_config().oracle_custom_database_list] == ['OWNER_A', 'OWNER_B']
    dialog.oracle_database_table.setCurrentCell(0, 1)
    dialog._remove_database()
    assert dialog._collect_config().oracle_custom_database_list == [dict(active=True, name='OWNER_B', auto_open=False)]
    assert source.oracle_custom_database_list == [dict(active=False, name='OWNER_A', auto_open=True)]


def test_malformed_or_unknown_draft_is_not_silently_replaced(qtbot, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[2]))
    malformed = editor(qtbot, profile(oracle_custom_database_list=[{'name': 'OWNER_A'}]))
    malformed.accept()
    assert malformed.get_config() is None and errors
    unknown = editor(qtbot, profile(oracle_connection_type='future-mode', oracle_service_type='future-selector',
        oracle_role='future-role', oracle_client_charset='FUTURE_CHARSET'))
    unknown.accept()
    assert unknown.get_config().oracle_connection_type == 'future-mode'
    assert unknown.get_config().oracle_service_type == 'future-selector'
    assert unknown.get_config().oracle_role == 'future-role'
    assert unknown.get_config().oracle_client_charset == 'FUTURE_CHARSET'
    long_value = 'FUTURE_' * 1000
    bounded = editor(qtbot, profile(oracle_role=long_value))
    assert len(bounded.oracle_role_combo.currentText()) < 140
    assert bounded._collect_config().oracle_role == long_value


@pytest.mark.parametrize('options', [dict(oracle_connection_type='tns'), dict(oracle_service_type='sid'),
    dict(oracle_role='sysdba'), dict(oracle_os_auth=True), dict(ssl_mode='verify-full'),
    dict(ssh_enabled=True), dict(read_only=True), dict(oracle_auto_connect=True),
    dict(oracle_custom_database_list_enabled=True), dict(oracle_initial_query='SELECT 1 FROM DUAL')])
def test_unsupported_test_fails_before_password_prompt_or_session(qtbot, monkeypatch, options):
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[2]))
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: pytest.fail('Unsupported modes must not prompt for a password'))
    monkeypatch.setattr('opennavicat.database.DatabaseSession', lambda *_: pytest.fail('Unsupported modes must not create a session'))
    dialog = editor(qtbot, profile(**options))
    dialog.test_connection()
    assert errors and not dialog._testing and dialog._test_task is None
    dialog.accept()  # Unsupported settings remain saveable as a truthful draft.
    assert dialog.get_config() is not None


def test_password_prompt_cancel_does_not_create_session_or_cache(qtbot, monkeypatch):
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: ('ignored-generated-secret', False))
    monkeypatch.setattr('opennavicat.database.DatabaseSession', lambda *_: pytest.fail('Cancelled prompt must not create a session'))
    dialog = editor(qtbot, profile())
    dialog.test_connection()
    assert not dialog._testing and dialog.password_edit.text() == ''
    assert dialog._collect_config().password == ''


@pytest.mark.parametrize('fail', [False, True])
def test_oracle_test_uses_serial_dedicated_probe_and_ephemeral_password(qtbot, monkeypatch, fail):
    started, release = threading.Event(), threading.Event()
    calls, destroy_threads = [], []
    gui_thread = threading.get_ident()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *_: ('generated-ephemeral-password', True))

    class Session:
        def __init__(self, config):
            calls.append(('create', threading.get_ident(), config.password))

        def test_connection(self, *, cancel_event):
            assert isinstance(cancel_event, threading.Event)
            calls.append(('probe', threading.get_ident()))
            started.set()
            assert release.wait(5)
            if fail: raise RuntimeError('Generated failure')
            return 'Oracle generated version'

        def connect(self): pytest.fail('The UI must use the dedicated Oracle probe')
        def execute(self, *_args, **_kwargs): pytest.fail('No generic SELECT 1 may be issued')
        def close(self): calls.append(('close', threading.get_ident()))

    monkeypatch.setattr('opennavicat.database.DatabaseSession', Session)
    dialog = editor(qtbot, profile())
    try:
        with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
            qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(started.is_set)
            task_ref = weakref.ref(dialog._test_task)
            signals_ref = weakref.ref(dialog._test_task.signals)
            assert dialog._test_task.signals.parent() is dialog
            assert dialog._test_task.signals.thread() == QThread.currentThread()
            dialog._test_task.signals.destroyed.connect(lambda: destroy_threads.append(threading.get_ident()))
            assert not dialog.content.isEnabled() and not dialog.buttons.isEnabled()
            assert not dialog.back_button.isEnabled()
            dialog._go_back()
            assert not dialog.back_requested
            dialog.accept(); dialog.reject(); dialog.close()
            assert dialog.isVisible() and dialog.get_config() is None
            assert dialog._collect_config().password == '' and dialog.password_edit.text() == ''
            release.set()
        qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())
        qtbot.waitUntil(lambda: bool(destroy_threads))
        gc.collect()
        assert task_ref() is None
        assert signals_ref() is None or not isValid(signals_ref())
        assert destroy_threads == [gui_thread]
        assert result.args == ([False, 'Generated failure'] if fail else [True, 'Oracle generated version'])
        assert dialog.test_banner.isVisible()
        assert dialog.test_banner.y() < dialog.footer.y()
        assert dialog.test_banner.width() > dialog.settings_pane.width()
        assert [diagram.connection_succeeded for diagram in dialog._diagrams] == [not fail] * 5
        assert dialog.test_status.text() == ('连接失败' if fail else '连接成功')
        assert dialog.test_banner.detail_label.text() == ('' if fail else '服务器版本: Oracle generated version')
        assert dialog.test_banner.status_icon.isVisible() is not fail
        assert [item[0] for item in calls] == ['create', 'probe', 'close']
        assert len({item[1] for item in calls}) == 1 and calls[0][1] != gui_thread
        assert calls[0][2] == 'generated-ephemeral-password'
        assert not dialog.remember_password and dialog._collect_config().password == ''
        dialog.accept()
        assert dialog.get_config().password == ''
    finally:
        release.set()
        qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())


def test_success_banner_copy_dismiss_and_new_test_clears_old_state(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    dialog = editor(qtbot, profile())
    assert dialog.test_banner.isHidden()
    copies = []
    class Clipboard:
        def setText(self, text): copies.append(text)
    monkeypatch.setattr(QApplication, 'clipboard', lambda: Clipboard())
    dialog.test_finished.emit(True, 'Oracle generated version')
    assert dialog.test_banner.isVisible()
    dialog.test_copy_button.click()
    assert copies == ['✓  连接成功  服务器版本: Oracle generated version']
    dialog.test_dismiss_button.click()
    assert dialog.test_banner.isHidden()
    started = []
    monkeypatch.setattr('opennavicat.ui.dialogs.ConnectionDialog._start_connection_test', lambda _self, config: started.append(config))
    dialog._start_connection_test(profile())
    assert started and dialog.test_banner.isHidden()
    assert not any(diagram.connection_succeeded for diagram in dialog._diagrams)


def test_oracle_selector_requires_dedicated_adapter_and_passes_service_not_schema(qtbot, monkeypatch):
    from opennavicat import database
    import importlib.util
    monkeypatch.setattr(database, 'SUPPORTED_DRIVERS', {'sqlite', 'oracle'})
    actual_find = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None if name == 'opennavicat.oracle_session' else actual_find(name))
    assert supported_driver_kinds() == frozenset({'sqlite'})
    monkeypatch.setattr(importlib.util, 'find_spec', actual_find)
    manager = ConnectionManagerDialog()
    qtbot.addWidget(manager)
    manager.show()
    rows = manager.connection_list.findItems('Oracle', Qt.MatchFlag.MatchExactly)
    assert len(rows) == 1
    manager.connection_list.setCurrentItem(rows[0])
    assert manager.next_button.isEnabled()
    with qtbot.waitSignal(manager.selectionRequested) as result:
        manager.next_button.click()
    kind, preset = result.args
    assert kind == 'oracle' and preset['database'] == ''
    assert preset['name'] == ''
    assert preset['oracle_service_identifier'] == 'ORCL' and preset['ssl_mode'] == 'disable'
    assert preset['port'] == 1521
