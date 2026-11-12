"""Accepted connection settings survive asynchronous document saves and close."""
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace
import uuid

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_session import MongoSession
from opennavicat.storage import Store
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.window import MainWindow
import opennavicat.ui.window as window_module


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=10000)


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    vault = ModuleType('keyring')
    def no_vault(*args, **kwargs):
        raise RuntimeError('This investigation forbids all actual keychain access')
    vault.get_password = vault.set_password = vault.delete_password = no_vault
    monkeypatch.setitem(sys.modules, 'keyring', vault)
    monkeypatch.setattr(window_module, 'discover_navicat_profiles', lambda: pytest.fail('No discovery allowed'))
    errors = []
    monkeypatch.setattr(MainWindow, 'error', lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, 'information', lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_a, **_k: pytest.fail('Unexpected settings warning'))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *_a, **_k: pytest.fail('Unexpected settings critical'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    files = []
    for label in ('first', 'second'):
        path = tmp_path / (label + '.sqlite')
        with closing(sqlite3.connect(path)) as database, database:
            database.execute('CREATE TABLE owned(id INTEGER PRIMARY KEY, label TEXT)')
            database.execute('INSERT INTO owned VALUES (1, ?)', (label,))
        files.append(path)
    store = Store(tmp_path / 'workspace')
    config = ConnectionConfig('owned-' + uuid.uuid4().hex, 'Generated first', database=str(files[0]))
    store.save_connection(config)
    window = MainWindow(store)
    closed = False
    def finish(_widget):
        nonlocal closed
        if closed:return
        idle(qtbot, window)
        for index in range(window.tabs.count()):
            document = window.tabs.widget(index)
            preview = getattr(document, '_save_dialog', None)
            if preview is not None:preview.reject()
            if getattr(document, 'dirty', False) and getattr(document, 'snapshot', None) is not None:
                document.load_snapshot(document.snapshot)
            model = getattr(document, 'model', None)
            if model is not None and hasattr(model, 'discard'):model.discard()
        window.close()
        qtbot.waitUntil(lambda: window._closed, timeout=10000)
        idle(qtbot, window)
        closed = True
    qtbot.addWidget(window, before_close_func=finish)
    window.show()
    try:
        yield SimpleNamespace(window=window, store=store, config=config, files=files, errors=errors)
    finally:
        finish(window)


def edit_using_actual_settings(qtbot, window, current, update):
    observations, failures = [], []
    def accept():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ConnectionDialog)
        observations.append(dialog)
        try:
            update(dialog)
            qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
        except BaseException as error:
            failures.append(error); dialog.reject()
    QTimer.singleShot(0, accept)
    window.new_connection(config=current)
    if failures:raise failures[0]
    assert observations and observations[0].get_config() is not None
    assert observations[0].result() == QDialog.DialogCode.Accepted
    assert not observations[0].isVisible()
    return observations[0]


def test_actual_sqlite_settings_save_switches_file_and_enforces_readonly(qtbot, host):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    old = w.sessions[identifier]
    before = []
    w.submit(lambda: old.execute('SELECT label FROM owned'), before.extend); idle(qtbot, w)
    assert before[0].rows == [['first']]
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1]))
        dialog.name_edit.setText('Generated second readonly')
        dialog.read_only_check.setChecked(True)
    edit_using_actual_settings(qtbot, w, w.configs[identifier], update)
    idle(qtbot, w)
    current = w.sessions[identifier]
    assert current is not old and old.connection is None
    assert current.config is w.configs[identifier] and current.config.read_only
    assert current.config.database == str(host.files[1])
    assert host.store.connections()[0].read_only and not w.table_action.isEnabled()
    page = w.new_query(connection_id=identifier, sql='SELECT label FROM owned; PRAGMA query_only;')
    w.run_query(page); idle(qtbot, w)
    assert [result.rows for result in page._last_results] == [[['second']], [[1]]]
    page.editor.setPlainText("INSERT INTO owned(label) VALUES ('must-not-write')")
    w.run_query(page); idle(qtbot, w)
    assert host.store.history()[0]['status'] == 'error'
    assert 'readonly' in page.info.toPlainText().lower() or 'read-only' in page.info.toPlainText().lower()
    rows = []
    def independent():
        for path in host.files:
            with closing(sqlite3.connect(path)) as database:
                rows.append(database.execute('SELECT label FROM owned').fetchall())
    w.submit(independent); idle(qtbot, w)
    assert rows == [[('first',)], [('second',)]] and not host.errors
    print(json.dumps({'case':'sqlite-edit-same-id', 'new_session':True, 'ui_session_config_same_object':True,
        'actual_file':'second', 'query_only':1, 'write_rejected':True, 'original_files_intact':True}))


def test_cancel_existing_dirty_data_close_preserves_original_binding(qtbot, host):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    old = w.sessions[identifier]
    page = w.open_table({'id':identifier,'schema':'main','table':'owned','name':'owned','kind':'table'})
    idle(qtbot, w)
    qtbot.waitUntil(lambda: page.model is not None, timeout=10000)
    idle(qtbot, w)
    assert page.model.setData(page.model.index(0,1), 'unsaved synthetic', Qt.ItemDataRole.EditRole)
    assert page.model.pending
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1]))
        dialog.read_only_check.setChecked(True)
    edit_using_actual_settings(qtbot, w, w.configs[identifier], update)
    idle(qtbot, w)
    assert w.sessions[identifier] is old and w.configs[identifier] is old.config
    assert not old.config.read_only and old.config.database == str(host.files[0])
    assert not host.store.connections()[0].read_only and page.model.pending
    print(json.dumps({'case':'dirty-data-close-cancel','old_binding_preserved':True,'settings_not_saved':True}))


def test_mongo_async_close_blocks_native_js_then_reconnect_uses_new_config(qtbot, host, monkeypatch):
    w = host.window
    identifier = 'controlled-mongo-' + uuid.uuid4().hex
    config = ConnectionConfig(identifier, 'Controlled Mongo old', driver='mongodb', host='old.invalid',
                              database='generated_old', mongo_auth_mechanism='none', ssl_mode='disable')
    host.store.save_connection(config); w.configs[identifier] = config; w.populate_connections()
    session = MongoSession(config)
    entered, release = threading.Event(), threading.Event()
    class ControlledClient:
        def close(self):
            entered.set()
            assert release.wait(10), 'Controlled close must be released during this test'
    session.connection = ControlledClient()
    monkeypatch.setattr(session, 'connect', lambda: pytest.fail('No endpoint may be opened'))
    w.sessions[identifier] = session
    page = w.new_query(connection_id=identifier, sql='db.owned.insertOne({generated:true});')
    page._engine_factory = lambda: pytest.fail('Closing session must not execute JavaScript')
    def update(dialog):
        dialog.host_edit.setText('new.invalid')
        dialog.read_only_check.setChecked(True)
    constructed = []
    def factory(value):
        fresh = MongoSession(value)
        fresh.connect = lambda: setattr(fresh, 'connection', SimpleNamespace(close=lambda:None))
        fresh.schemas = lambda:['generated_new']
        fresh.objects = lambda schema:[]
        fresh.indexes = lambda schema:[]
        constructed.append(fresh)
        return fresh
    monkeypatch.setattr(window_module, 'DatabaseSession', factory)
    try:
        edit_using_actual_settings(qtbot, w, config, update)
        qtbot.waitUntil(entered.is_set)
        assert identifier in w._closing_connections and w.sessions[identifier] is session
        assert w.configs[identifier] is session.config and not session.config.read_only
        assert page.run_native() is False and page._job is None and not page.running
    finally:
        release.set()
    qtbot.waitUntil(lambda: len(constructed) == 1 and identifier in w.sessions, timeout=10000)
    idle(qtbot, w)
    assert identifier not in w._closing_connections
    assert len(constructed) == 1 and w.sessions[identifier] is constructed[0]
    assert constructed[0].config.read_only and constructed[0].config.host == 'new.invalid'
    assert constructed[0].config is w.configs[identifier] and not host.errors
    print(json.dumps({'case':'controlled-mongo-async-edit','old_writable_session_temporarily_retained':True,
        'native_js_blocked_during_close':True,'next_session_readonly':True,'actual_network_opened':False}))


@pytest.mark.parametrize("imported", [False, True], ids=["manual", "imported"])
def test_async_designer_save_continues_same_accepted_settings(qtbot, host, monkeypatch, imported):
    w, identifier = host.window, host.config.id
    if imported:
        host.store.state['navicat_imports'][identifier] = {'source_id':'synthetic:owned',
            'source_format':'synthetic', 'group_paths':[], 'blocked':False}
        host.store.flush(); w.populate_connections()
    w.connect_id(identifier); idle(qtbot, w)
    original_session = w.sessions[identifier]
    page = w.open_designer(identifier, 'main', 'owned')
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=10000)
    idle(qtbot, w)
    assert page.field_model.setData(page.field_model.index(1, 0), 'renamed_label')
    assert page.dirty
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Save)
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1]))
        dialog.read_only_check.setChecked(True)
    settings = edit_using_actual_settings(qtbot, w, w.configs[identifier], update)
    assert settings.get_config().database == str(host.files[1]) and settings.get_config().read_only
    preview = page._save_dialog
    assert preview is not None
    qtbot.waitUntil(preview.save_button.isEnabled, timeout=15000)
    qtbot.mouseClick(preview.save_button, Qt.MouseButton.LeftButton)
    assert settings.result() == QDialog.DialogCode.Accepted and not settings.isVisible()
    qtbot.waitUntil(lambda: identifier not in w._connection_edit_tokens, timeout=10000)
    if imported:
        qtbot.waitUntil(lambda: identifier not in w.sessions, timeout=10000)
    else:
        qtbot.waitUntil(lambda: identifier in w.sessions and w.sessions[identifier] is not original_session, timeout=10000)
    idle(qtbot, w)
    assert w.configs[identifier].database == str(host.files[1])
    assert w.configs[identifier].read_only
    if imported:
        assert identifier not in w.sessions  # Reviewed imports remain manual-open.
        assert host.store.state['navicat_imports'][identifier]['locally_edited']
    else:
        assert w.sessions[identifier].config is w.configs[identifier]
    saved = next(c for c in host.store.connections() if c.id == identifier)
    assert saved.database == str(host.files[1]) and saved.read_only
    observed = []
    def independent():
        for path in host.files:
            with closing(sqlite3.connect(path)) as db:
                observed.append([row[1] for row in db.execute('PRAGMA table_info(owned)')])
    w.submit(independent); idle(qtbot, w)
    assert observed == [['id', 'renamed_label'], ['id', 'label']] and not host.errors
    print(json.dumps({'case':'dirty-designer-save-while-saving-profile',
        'settings_dialog_save_accepted':True,'settings_dialog_closed_before_and_after_ddl':True,
        'accepted_payload_endpoint':'second','accepted_payload_readonly':True,'existing_ddl_save_completed':True,
        'connection_disconnected':True,'accepted_settings_applied_after_close':True,
        'persisted_endpoint':'second','persisted_readonly':True}))


def dirty_designer(qtbot, host):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    page = w.open_designer(identifier, 'main', 'owned')
    qtbot.waitUntil(lambda: page.snapshot is not None and not page.loading, timeout=10000)
    idle(qtbot, w)
    assert page.field_model.setData(page.field_model.index(1, 0), 'renamed_label')
    return page


def request_readonly_second(qtbot, host):
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1]))
        dialog.read_only_check.setChecked(True)
    return edit_using_actual_settings(qtbot, host.window, host.window.configs[host.config.id], update)


def confirm_ddl(qtbot, page):
    preview = page._save_dialog
    assert preview is not None
    qtbot.waitUntil(preview.save_button.isEnabled, timeout=15000)
    qtbot.mouseClick(preview.save_button, Qt.MouseButton.LeftButton)


@pytest.mark.parametrize('action', ['close_tab', 'disconnect'])
@pytest.mark.parametrize('kind', ['collection', 'timeseries', 'view'])
def test_invalid_mongo_designer_save_cannot_leave_a_stale_close_retry(qtbot, host, monkeypatch, kind, action):
    from opennavicat.mongodb_collections import CollectionDefinition, CollectionSnapshot
    from opennavicat.mongodb_timeseries import TimeSeriesDefinition, TimeSeriesSnapshot
    from opennavicat.mongodb_views import ViewDefinition, ViewSnapshot
    from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage
    from opennavicat.ui.mongo_timeseries_designer import MongoTimeSeriesDesignerPage
    from opennavicat.ui.mongo_view_designer import MongoViewDesignerPage

    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    session = w.sessions[identifier]
    # Detached MongoDB drafts exercise the real window's close/disconnect
    # boundary without opening any MongoDB client or reading a saved profile.
    if kind == 'collection':
        page = MongoCollectionDesignerPage(identifier, 'generated')
        snapshot = CollectionSnapshot('generated', 'generated_object', CollectionDefinition('generated_object'))
        invalid, corrected = page.validator_editor, '{"score": {"$gte": 0}}'
    elif kind == 'timeseries':
        page = MongoTimeSeriesDesignerPage(identifier, 'generated')
        snapshot = TimeSeriesSnapshot('generated', 'generated_object', TimeSeriesDefinition('generated_object', 'timestamp'))
        invalid, corrected = page.expire_edit, '3600'
    else:
        page = MongoViewDesignerPage(identifier, 'generated')
        snapshot = ViewSnapshot('generated', 'generated_object', ViewDefinition('generated_object', 'source'))
        invalid, corrected = page.pipeline_editor, '[{"$match": {"generated": true}}]'
    page.load_snapshot(snapshot)
    w.tabs.addTab(page, 'Generated draft')
    page.name_edit.setText('generated_object')
    if kind == 'collection':
        page.validator_widget.mode_combo.setCurrentIndex(1)
    invalid.setText('invalid') if kind == 'timeseries' else invalid.setPlainText('{broken')
    assert page.dirty and page._invalid
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Save)

    if action == 'close_tab':
        w.close_tab(w.tabs.indexOf(page))
    else:
        assert w.disconnect(identifier) is False
    assert w.tabs.indexOf(page) >= 0 and w.sessions[identifier] is session
    assert page._save_dialog is None and page.dirty
    assert getattr(page, '_after_save', None) is None
    assert page.message.text() and not host.errors

    invalid.setText(corrected) if kind == 'timeseries' else invalid.setPlainText(corrected)
    assert not page._invalid
    # A later manual Save must start a fresh sheet, with no old close or
    # disconnect action waiting to execute after its eventual successful save.
    page.request_save()
    assert page._save_dialog is not None
    assert getattr(page, '_after_save', None) is None
    page._save_dialog.reject(); idle(qtbot, w)
    page.load_snapshot(snapshot)


def test_cancel_ddl_sheet_cancels_settings_continuation_and_later_manual_save_cannot_revive_it(qtbot, host, monkeypatch):
    w, identifier = host.window, host.config.id
    page = dirty_designer(qtbot, host)
    old = w.sessions[identifier]
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Save)
    settings = request_readonly_second(qtbot, host)
    assert settings.result() == QDialog.DialogCode.Accepted and not settings.isVisible()
    assert identifier in w._connection_edit_tokens
    page._save_dialog.reject(); idle(qtbot, w)
    assert identifier not in w._connection_edit_tokens and page._after_save is None
    assert w.sessions[identifier] is old and w.configs[identifier].database == str(host.files[0])
    assert not host.store.connections()[0].read_only and page.dirty
    page.request_save(); confirm_ddl(qtbot, page)
    qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000); idle(qtbot, w)
    assert w.sessions[identifier] is old and not old.config.read_only
    assert host.store.connections()[0].database == str(host.files[0]) and not host.errors


def test_actual_ddl_conflict_aborts_settings_without_disconnect_or_later_retry(qtbot, host, monkeypatch):
    w, identifier = host.window, host.config.id
    page = dirty_designer(qtbot, host)
    old = w.sessions[identifier]
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Save)
    request_readonly_second(qtbot, host)
    qtbot.waitUntil(page._save_dialog.save_button.isEnabled, timeout=15000)
    def drift():
        with closing(sqlite3.connect(host.files[0])) as database, database:
            database.execute('CREATE INDEX generated_conflict ON owned(label)')
    w.submit(drift); idle(qtbot, w)
    confirm_ddl(qtbot, page)
    qtbot.waitUntil(lambda: not page.loading and bool(page.message.text()), timeout=10000); idle(qtbot, w)
    assert page.dirty and page._after_save is None
    assert identifier not in w._connection_edit_tokens
    assert w.sessions[identifier] is old and w.configs[identifier] is old.config
    assert not old.config.read_only and host.store.connections()[0].database == str(host.files[0])
    assert not host.errors


def test_busy_after_settings_acceptance_uses_one_immutable_payload_and_no_deleted_qt_reads(qtbot, host, monkeypatch):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    entered, release = threading.Event(), threading.Event()
    calls, dialogs = [], []
    real_save = host.store.save_connection
    def saved(config, remember=False, **flags):
        assert threading.current_thread() is threading.main_thread()
        calls.append((replace(config), remember, dict(flags)))
        return real_save(config, remember, **flags)
    monkeypatch.setattr(host.store, 'save_connection', saved)
    real_dialog = window_module.ConnectionDialog
    def create(*args, **kwargs):
        dialog = real_dialog(*args, **kwargs); dialogs.append(dialog); return dialog
    monkeypatch.setattr(window_module, 'ConnectionDialog', create)
    def busy():
        entered.set(); assert release.wait(10)
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1])); dialog.read_only_check.setChecked(True)
        for box in (dialog.remember_check, dialog.ssh_remember_check, dialog.ssh_key_remember_check, dialog.ssl_remember_check):
            box.setChecked(True)
        w.submit(busy)
    try:
        settings = edit_using_actual_settings(qtbot, w, w.configs[identifier], update)
        qtbot.waitUntil(entered.is_set)
        assert not calls and identifier in w._connection_edit_tokens
        settings.get_config().database = str(host.files[0])
        settings.get_config().read_only = False
        for box in (settings.remember_check, settings.ssh_remember_check, settings.ssh_key_remember_check, settings.ssl_remember_check):
            box.setChecked(False)
        settings.deleteLater()
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(calls) and identifier in w.sessions and w.configs[identifier].read_only, timeout=10000)
    idle(qtbot, w)
    assert len(dialogs) == len(calls) == 1
    assert calls[0][0].database == str(host.files[1]) and calls[0][0].read_only
    # SQLite deliberately has no applicable password-persistence switches.
    assert calls[0][1] is False and calls[0][2] == {'remember_ssh':False,'remember_ssh_key':False,'remember_ssl':False}
    assert identifier not in w._connection_edit_tokens and not host.errors


def test_late_busy_continuation_after_real_window_close_cannot_save_or_reconnect(qtbot, host, monkeypatch):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    entered, release = threading.Event(), threading.Event()
    pending = []
    monkeypatch.setattr(w, 'when_idle', pending.append)
    def busy():
        entered.set(); assert release.wait(10)
    def update(dialog):
        dialog.database_edit.setText(str(host.files[1])); dialog.read_only_check.setChecked(True)
        w.submit(busy)
    try:
        edit_using_actual_settings(qtbot, w, w.configs[identifier], update)
        qtbot.waitUntil(entered.is_set)
        assert pending
    finally:
        release.set()
    idle(qtbot, w)
    w.close(); assert w._closed
    for callback in pending:callback()
    assert all(session.connection is None for session in w.sessions.values())
    assert not w._connection_edit_tokens
    saved = host.store.connections()[0]
    assert saved.database == str(host.files[0]) and not saved.read_only and not host.errors


def test_cancel_actual_settings_never_disconnects_or_stores_payload(qtbot, host):
    w, identifier = host.window, host.config.id
    w.connect_id(identifier); idle(qtbot, w)
    session = w.sessions[identifier]
    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ConnectionDialog)
        dialog.database_edit.setText(str(host.files[1])); dialog.read_only_check.setChecked(True)
        qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, cancel)
    w.new_connection(config=w.configs[identifier]); idle(qtbot, w)
    assert w.sessions[identifier] is session and w.configs[identifier] is session.config
    assert not w._connection_edit_tokens and not host.store.connections()[0].read_only


def test_failed_mongo_close_cancels_payload_and_does_not_reconnect(qtbot, host, monkeypatch):
    w = host.window
    config = ConnectionConfig('controlled-' + uuid.uuid4().hex, 'Generated Mongo', driver='mongodb',
        host='old.invalid', database='generated', ssl_mode='disable', mongo_auth_mechanism='none')
    host.store.save_connection(config); w.configs[config.id] = config; w.populate_connections()
    session = MongoSession(config); session.connection = SimpleNamespace(close=lambda:None)
    w.sessions[config.id] = session
    original_close = session.close
    def failed_close():
        raise RuntimeError('Generated close failure')
    monkeypatch.setattr(session, 'close', failed_close)
    monkeypatch.setattr(window_module, 'DatabaseSession', lambda *_: pytest.fail('A failed close must not reconnect'))
    def update(dialog):
        dialog.host_edit.setText('new.invalid'); dialog.read_only_check.setChecked(True)
    try:
        edit_using_actual_settings(qtbot, w, config, update); idle(qtbot, w)
        assert host.errors == ['Generated close failure']
        assert not w._connection_edit_tokens and config.id not in w._closing_connections
        assert w.sessions[config.id] is session and w.configs[config.id] is config
        assert next(c for c in host.store.connections() if c.id == config.id).host == 'old.invalid'
    finally:
        monkeypatch.setattr(session, 'close', original_close)
        host.errors.clear()


def test_all_four_network_secret_preferences_are_captured_before_settings_deletion(qtbot, host, tmp_path, monkeypatch):
    w = host.window
    paths = []
    for name in ('ca.pem', 'cert.pem', 'key.pem', 'ssh-key'):
        path = tmp_path / name; path.write_text('GENERATED SETTINGS FIXTURE ONLY'); paths.append(str(path))
    config = ConnectionConfig('controlled-secrets-' + uuid.uuid4().hex, 'Generated Mongo auth', driver='mongodb',
        database='generated', host='old.invalid', username='generated', password='GENERATED-DB-SECRET',
        mongo_auth_mechanism='SCRAM-SHA-256', ssl_mode='verify-full', ssl_ca=paths[0], ssl_cert=paths[1],
        ssl_key=paths[2], ssl_key_password='GENERATED-TLS-SECRET', ssh_enabled=True, ssh_host='jump.invalid',
        ssh_username='generated', ssh_auth_method='password+key', ssh_password='GENERATED-SSH-SECRET',
        ssh_key_file=paths[3], ssh_key_passphrase='GENERATED-KEY-SECRET')
    host.store.save_connection(config); w.configs[config.id] = config; w.populate_connections()
    session = MongoSession(config); session.connection = SimpleNamespace(close=lambda:None)
    w.sessions[config.id] = session
    memory = {}
    vault = ModuleType('keyring')
    vault.get_password = lambda service, key:memory.get((service, key))
    vault.set_password = lambda service, key, value:memory.__setitem__((service, key), value)
    vault.delete_password = lambda service, key:memory.pop((service, key), None)
    monkeypatch.setitem(sys.modules, 'keyring', vault)
    def factory(value):
        fresh = MongoSession(value)
        fresh.connect = lambda:setattr(fresh, 'connection', SimpleNamespace(close=lambda:None))
        fresh.schemas = lambda:['generated']; fresh.objects = lambda schema:[]; fresh.indexes = lambda schema:[]
        return fresh
    monkeypatch.setattr(window_module, 'DatabaseSession', factory)
    entered, release = threading.Event(), threading.Event()
    def busy():
        entered.set(); assert release.wait(10)
    def update(dialog):
        dialog.host_edit.setText('new.invalid'); dialog.read_only_check.setChecked(True)
        for box in (dialog.remember_check, dialog.ssh_remember_check, dialog.ssh_key_remember_check, dialog.ssl_remember_check):
            box.setChecked(True)
        assert all((dialog.remember_password, dialog.remember_ssh_password,
                    dialog.remember_ssh_key_passphrase, dialog.remember_ssl_password))
        w.submit(busy)
    try:
        settings = edit_using_actual_settings(qtbot, w, config, update)
        qtbot.waitUntil(entered.is_set)
        for box in (settings.remember_check, settings.ssh_remember_check, settings.ssh_key_remember_check, settings.ssl_remember_check):
            box.setChecked(False)
        settings.get_config().host = 'discarded-mutation.invalid'
        settings.deleteLater(); qtbot.waitUntil(lambda:not isValid(settings))
    finally:
        release.set()
    qtbot.waitUntil(lambda:config.id in w.sessions and w.sessions[config.id] is not session, timeout=10000); idle(qtbot, w)
    assert w.configs[config.id].host == 'new.invalid' and w.configs[config.id].read_only
    assert config.id in host.store.state['remembered_passwords']
    assert set(host.store.state['remembered_secrets'][config.id]) == {'ssh_password','ssh_key_passphrase','ssl_key_password'}
    assert len(memory) == 4 and not host.errors and not w._connection_edit_tokens
    text = host.store.path.read_text()
    assert all(secret not in text for secret in ('GENERATED-DB-SECRET','GENERATED-SSH-SECRET','GENERATED-KEY-SECRET','GENERATED-TLS-SECRET'))
