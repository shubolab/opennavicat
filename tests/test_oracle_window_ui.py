"""Owned fake-driver Oracle reads through the actual GUI/serial/cancel queues."""
from dataclasses import replace
from decimal import Decimal
from importlib.machinery import ModuleSpec
import json
import sys
import threading
from types import ModuleType, SimpleNamespace
import uuid

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox, QMenu

from opennavicat.database import ConnectionConfig, DatabaseError, QueryResult
from opennavicat.oracle_session import validate_oracle_execution as real_oracle_support_validation
from opennavicat.storage import Store
from opennavicat.ui.grid import DataModel
from opennavicat.ui.window import MainWindow, ORACLE_OTHER_TOOLS, TablePage
import opennavicat.ui.window as window_module


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
        and not window.cancel_pool.activeThreadCount(), timeout=10000)


class OracleReadFixture:
    def __init__(self, config):
        self.config = config
        self.owner = None
        self.calls = []
        self.close_count = 0
        self.in_transaction = False
        self._selected_schema = 'GENERATED_OWNER'
        self._marker = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_columns = False
        self.more = False
        self.cancels = []
        self.fetch_args = []

    def call(self, name, event=None):
        identity = threading.get_ident()
        assert identity != threading.main_thread().ident
        self.owner = self.owner or identity
        assert self.owner == identity
        if event is not None and event.is_set():
            raise DatabaseError('Generated Oracle read cancelled')
        self.calls.append((name, identity, event))

    @property
    def current_operation_marker(self):
        return self._marker

    def connect(self, *, cancel_event=None):
        self.call('connect', cancel_event)

    def schemas(self, *, cancel_event=None):
        self.call('schemas', cancel_event)
        return ['ANOTHER_OWNER', 'GENERATED_OWNER']

    def objects(self, schema, *, cancel_event=None):
        self.call('objects', cancel_event)
        return [dict(name='GENERATED_TABLE', schema=schema, kind='table', status='VALID',
                     num_rows=None, unavailable_fields=['num_rows', 'logging']),
                dict(name='GENERATED_VIEW', schema=schema, kind='view', status='VALID',
                     text_length=None, unavailable_fields=['text_length'])]

    def columns(self, schema, table, *, cancel_event=None):
        self.call('columns', cancel_event)
        if self.block_columns:
            self._marker = object()
            self.started.set()
            try:
                assert self.release.wait(5)
                if cancel_event.is_set():
                    raise DatabaseError('Generated Oracle read cancelled')
            finally:
                self._marker = None
        return [dict(name='ID', type='NUMBER', nullable=False, default=None, pk=True),
                dict(name='LABEL', type='VARCHAR2', nullable=True, default=None, pk=False)]

    def fetch_table(self, schema, table, offset=0, limit=200, order_by=None, descending=False,
                    filter_text='', *, cancel_event=None):
        self.call('fetch_table', cancel_event)
        self.fetch_args.append((schema, table, offset, limit, order_by))
        assert schema == 'GENERATED_OWNER' and not filter_text
        return QueryResult(['ID', 'LABEL'], [[Decimal('9007199254740993'), '生成数据']],
            truncated=self.more, sql='SELECT * FROM "GENERATED_OWNER"."' + table + '"')

    def cancel_current(self, marker):
        identity = threading.get_ident()
        assert identity not in {threading.main_thread().ident, self.owner}
        self.cancels.append((marker, identity))
        if self._marker is marker:
            self.release.set()
            return True
        return False

    def close(self):
        self.call('close')
        self.close_count += 1

    def __getattr__(self, name):
        if name in {'table_ddl', 'indexes', 'foreign_keys', 'count_rows', 'insert_row', 'delete_row',
                    'update_row', 'import_rows', 'execute', 'use_schema'}:
            raise AssertionError('Unsupported Oracle API was reached: ' + name)
        raise AttributeError(name)


@pytest.fixture
def oracle_host(qtbot, tmp_path, monkeypatch):
    import keyring
    vault = {}
    monkeypatch.setattr(keyring, 'get_password', lambda service, key: vault.get((service, key)))
    monkeypatch.setattr(keyring, 'set_password', lambda service, key, value: vault.__setitem__((service, key), value))
    monkeypatch.setattr(keyring, 'delete_password', lambda service, key: vault.pop((service, key), None))
    monkeypatch.setattr(window_module, 'discover_navicat_profiles', lambda: pytest.fail('Never discover existing profiles'))
    monkeypatch.setattr(window_module, 'SUPPORTED_DRIVERS', window_module.SUPPORTED_DRIVERS | {'oracle'})
    # This suite qualifies the UI/controller contract, never a fake network success.
    backend = ModuleType('opennavicat.oracle_session')
    backend.__spec__ = ModuleSpec(backend.__name__,loader=None)
    backend.validate_oracle_execution = lambda config, require_password=False: None
    monkeypatch.setitem(sys.modules, backend.__name__, backend)
    sessions, errors, prompts = [], [], []
    def factory(config):
        session = OracleReadFixture(config)
        sessions.append(session)
        return session
    monkeypatch.setattr(window_module, 'DatabaseSession', factory)
    monkeypatch.setattr(MainWindow, 'error', lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_a, **_k: QMessageBox.StandardButton.Ok)
    secret = 'generated-temporary-oracle-secret'
    def password(*args, **kwargs):
        prompts.append(threading.get_ident())
        return secret, True
    monkeypatch.setattr(QInputDialog, 'getText', password)
    config = ConnectionConfig('generated-oracle-' + uuid.uuid4().hex, 'Generated Oracle', driver='oracle',
        host='owned.invalid', port=1521, username='GENERATED_OWNER', database='', ssl_mode='disable',
        oracle_service_identifier='FREEPDB1')
    store = Store(tmp_path / 'owned-oracle-workspace')
    store.state['navicat_auto_discover'] = False
    store.save_connection(config)
    window = MainWindow(store)
    closed = False
    def finish(_widget=None):
        nonlocal closed
        if closed:return
        for session in sessions:session.release.set()
        idle(qtbot, window)
        window.close()
        qtbot.waitUntil(lambda: window._closed, timeout=10000)
        idle(qtbot, window)
        closed = True
    qtbot.addWidget(window, before_close_func=finish)
    window.show()
    host = SimpleNamespace(window=window, store=store, config=config, sessions=sessions,
                           errors=errors, prompts=prompts, secret=secret, close=finish, vault=vault)
    yield host
    finish()
    assert secret not in store.path.read_text()


def connect(qtbot, host):
    host.window.connect_id(host.config.id)
    idle(qtbot, host.window)
    assert host.config.id in host.window.sessions, host.errors
    return host.window.sessions[host.config.id]


def open_owned(qtbot, host, kind='table'):
    page = host.window.open_table({'id':host.config.id, 'schema':'GENERATED_OWNER',
        'table':'GENERATED_TABLE' if kind=='table' else 'GENERATED_VIEW', 'kind':kind})
    qtbot.waitUntil(lambda: page.model is not None or bool(host.errors), timeout=5000)
    idle(qtbot, host.window)
    assert isinstance(page, TablePage) and page.model is not None, host.errors
    return page


def test_password_prompt_is_gui_only_and_not_reusable_profile_cache(qtbot, oracle_host):
    h = oracle_host
    session = connect(qtbot, h)
    assert h.prompts == [threading.main_thread().ident]
    assert session.config.password == h.secret
    assert h.window.configs[h.config.id].password == ''
    assert not h.store.passwords.get(h.config.id)
    assert session.calls[0][0] == 'connect' and session.calls[0][2] is session.calls[1][2]
    assert h.window.active_schema == 'GENERATED_OWNER'
    h.window.disconnect(h.config.id)
    idle(qtbot, h.window)
    assert session.close_count == 1
    second = connect(qtbot, h)
    assert second is not session and len(h.prompts) == 2 and not h.errors


@pytest.mark.parametrize('kind', ['table', 'view'])
def test_table_and_view_read_without_unknown_metadata_calls_or_mutations(qtbot, oracle_host, kind):
    h = oracle_host
    session = connect(qtbot, h)
    page = open_owned(qtbot, h, kind)
    assert page.model.data(page.model.index(0, 0)) == '9007199254740993'
    assert page.model.data(page.model.index(0, 1)) == '生成数据'
    assert page.total is None and not page.total_known and '总数未知' in h.window.status.text()
    assert not page.last.isEnabled() and not page.next.isEnabled()
    assert not page.model.editable and not page.model.setData(page.model.index(0, 0), '2')
    assert not page.insert_button.isEnabled() and not page.delete_button.isEnabled()
    assert not page.save_button.isEnabled() and not page.record_editor_action.isEnabled()
    assert not page.filter_action.isEnabled() and all(not a.isEnabled() for a in page.transaction_actions)
    assert not h.window.import_action.isEnabled() and not h.window.diagram_action.isEnabled()
    assert all(not page.subtabs.isTabEnabled(page.subtabs.indexOf(widget)) for widget in (page.ddl, page.indexes, page.foreign_keys))
    assert [c[0] for c in session.calls[-2:]] == ['columns','fetch_table']
    assert session.calls[-2][2] is session.calls[-1][2]
    for callback in (page.save_changes, page.insert_row, page.delete_row, h.window.import_rows,
                     h.window.new_table, h.window.open_diagram, h.window.compare_data,
                     lambda:h.window.transaction('BEGIN'), lambda:h.window.drop_object(
                         {'id':h.config.id,'schema':'GENERATED_OWNER','name':'GENERATED_TABLE','kind':'table'})):
        callback()
    assert not h.errors and not h.window._oracle_jobs


def test_catalog_columns_unknown_values_and_inspector_never_request_ddl(qtbot, oracle_host):
    h=oracle_host
    connect(qtbot,h)
    h.window.inspector.show()
    for kind, count, field, label in [('table',28,10,'行'),('view',13,2,'文本长度')]:
        h.window.show_objects(kind)
        idle(qtbot,h.window)
        browser=h.window.object_page
        assert browser.table.columnCount()==count and browser.table.rowCount()==1
        assert browser.table.horizontalHeaderItem(field).text()==label
        assert browser.table.item(0,field).text()=='—' and '不可用' in browser.table.item(0,field).toolTip()
        browser.table.setCurrentCell(0,0)
        idle(qtbot,h.window)
        assert not h.window.inspector.ddl_button.isEnabled()
        assert all(not a.isEnabled() for a in (browser.design_action,browser.new_action,browser.delete_action,
                                                browser.import_action,browser.er_action))
        assert browser.open_action.isEnabled()
    assert not h.errors


def test_navigation_other_tools_and_query_drafts_remain_explicitly_unavailable(qtbot,oracle_host,monkeypatch):
    h=oracle_host
    connect(qtbot,h)
    root=h.window.connection_item(h.config.id)
    schema=next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0)=='GENERATED_OWNER')
    h.window.load_objects(schema,schema.data(0,Qt.ItemDataRole.UserRole))
    idle(qtbot,h.window)
    assert [schema.child(i).text(0) for i in range(schema.childCount())]==['表','视图','实体化视图','函数','查询']
    assert all(schema.child(i).isDisabled() for i in (2,3,4))
    assert h.window.category_actions['materialized_view'].isVisible()
    assert h.window.category_actions['data_pump'].isVisible()
    assert not h.window.query_action.isEnabled()
    assert not h.window.category_actions['data_pump'].isEnabled()
    menus=[]
    class CapturedMenu(QMenu):
        def exec(self,*_args):menus.append(self)
    monkeypatch.setattr(window_module,'QMenu',CapturedMenu)
    h.window.show_other_tools()
    assert [a.text() for a in menus[-1].actions()]==list(ORACLE_OTHER_TOOLS)
    assert len(menus[-1].actions())==15 and all(not a.isEnabled() for a in menus[-1].actions())
    draft={'connection_id':h.config.id,'sql':'SELECT preserved_verbatim FROM generated_table;','schema':'GENERATED_OWNER'}
    h.store.state['drafts']=[draft]
    h.window.restore_drafts()
    assert h.window._unrestored_query_drafts==[draft] and h.window.new_query(connection_id=h.config.id) is None


def test_active_read_cancellation_uses_independent_worker_and_exact_session(qtbot,oracle_host):
    h=oracle_host
    session=connect(qtbot,h)
    session.block_columns=True
    page=h.window.open_table({'id':h.config.id,'schema':'GENERATED_OWNER','table':'GENERATED_TABLE','kind':'table'})
    qtbot.waitUntil(session.started.is_set,timeout=5000)
    marker=session.current_operation_marker
    assert h.window.stop_action.isEnabled()
    h.window.stop()
    idle(qtbot,h.window)
    assert session.cancels and session.cancels[0][0] is marker
    assert session.cancels[0][1] not in {session.owner,threading.main_thread().ident}
    assert not h.window._oracle_jobs and not page.loading
    assert not [call for call in session.calls if call[0]=='fetch_table']
    assert len(h.errors)==1 and 'cancelled' in h.errors[0]


def test_queued_stop_never_dispatches_and_late_stop_cannot_cancel_successor(qtbot,oracle_host):
    h=oracle_host
    session=connect(qtbot,h)
    gate,entered=threading.Event(),threading.Event()
    def block():entered.set();assert gate.wait(5)
    h.window.submit(block)
    qtbot.waitUntil(entered.is_set,timeout=5000)
    seen=[]
    token=h.window._submit_oracle_read(session,lambda event:seen.append('dispatched'),failure=lambda _e:None)
    h.window.stop()
    gate.set()
    idle(qtbot,h.window)
    assert seen==[] and session.cancels==[]
    h.window._cancel_oracle_job(token)
    done=[]
    h.window._submit_oracle_read(session,lambda event:session.schemas(cancel_event=event),done.append)
    idle(qtbot,h.window)
    assert done==[['ANOTHER_OWNER','GENERATED_OWNER']] and session.cancels==[]


@pytest.mark.parametrize('shutdown',[False,True])
def test_close_always_runs_on_the_original_serial_owner(qtbot,oracle_host,shutdown):
    h=oracle_host
    session=connect(qtbot,h)
    owner=session.owner
    assert h.window.pool.expiryTimeout()==-1
    assert h.window.cancel_pool.expiryTimeout()!=-1
    if shutdown:
        h.close()
    else:
        h.window.disconnect(h.config.id)
        idle(qtbot,h.window)
    assert session.close_count==1 and session.calls[-1][:2]==('close',owner)
    assert owner != threading.main_thread().ident and not h.errors


def test_unknown_total_uses_1000_default_and_only_bounded_next_page(qtbot,oracle_host):
    h=oracle_host
    session=connect(qtbot,h)
    session.more=True
    page=open_owned(qtbot,h)
    assert session.fetch_args[-1][2:4]==(0,1000)
    assert page.next.isEnabled() and not page.last.isEnabled() and page.page_number.isReadOnly()
    session.more=False
    qtbot.mouseClick(page.next,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(session.fetch_args)==2,timeout=5000)
    idle(qtbot,h.window)
    assert session.fetch_args[-1][2:4]==(1000,1000)
    assert page.offset==1000 and not page.next.isEnabled() and page.total is None
    before=len(session.calls)
    page.go_page(99)
    page.change_page(1)
    assert len(session.calls)==before and '总数未知' in h.window.status.text()


def test_late_stop_after_owner_failure_preserves_error_and_fresh_job(qtbot,oracle_host):
    h=oracle_host
    session=connect(qtbot,h)
    failures=[]
    def fail(_event):raise DatabaseError('Generated original failure')
    token=h.window._submit_oracle_read(session,fail,failure=failures.append)
    state=h.window._oracle_jobs[token]
    # Deliberately hold GUI delivery until the actual serial owner has ended.
    assert state['finished'].wait(2)
    h.window.stop()
    assert not state['cancelled'].is_set()
    returned=[]
    h.window._submit_oracle_read(session,lambda event:session.schemas(cancel_event=event),returned.append)
    idle(qtbot,h.window)
    assert len(failures)==1 and str(failures[0])=='Generated original failure'
    assert returned==[['ANOTHER_OWNER','GENERATED_OWNER']] and not session.cancels


def test_cancelled_password_prompt_never_constructs_session(qtbot,oracle_host,monkeypatch):
    h=oracle_host
    monkeypatch.setattr(QInputDialog,'getText',lambda *_a,**_k:('',False))
    h.window.connect_id(h.config.id)
    idle(qtbot,h.window)
    assert not h.sessions and not h.window._connecting and not h.window._oracle_jobs
    assert not h.store.passwords.get(h.config.id)


def test_unsupported_draft_is_rejected_before_password_or_session(qtbot,oracle_host,monkeypatch):
    h=oracle_host
    def reject(*_a,**_k):raise ValueError('Generated unsupported Oracle option')
    monkeypatch.setattr(sys.modules['opennavicat.oracle_session'],'validate_oracle_execution',reject)
    h.window.connect_id(h.config.id)
    idle(qtbot,h.window)
    assert not h.prompts and not h.sessions and not h.window._connecting
    assert h.errors==['Generated unsupported Oracle option']


def test_only_oracle_uses_dedicated_connection_dialog(qtbot,oracle_host,monkeypatch):
    from PySide6.QtWidgets import QComboBox,QDialog
    h=oracle_host
    calls=[]
    class RejectedDialog:
        def __init__(self,parent,config=None,**_kwargs):
            self.driver_combo=QComboBox(parent)
            self.driver_combo.addItem('Oracle','oracle')
            self.driver_combo.addItem('SQLite','sqlite')
        def exec(self):return QDialog.DialogCode.Rejected
    class OracleDialog(RejectedDialog):
        def __init__(self,*a,**k):calls.append('oracle');super().__init__(*a,**k)
    class GenericDialog(RejectedDialog):
        def __init__(self,*a,**k):calls.append('generic');super().__init__(*a,**k)
    module=ModuleType('opennavicat.ui.oracle_connection')
    module.OracleConnectionDialog=OracleDialog
    monkeypatch.setitem(sys.modules,module.__name__,module)
    monkeypatch.setattr(window_module,'ConnectionDialog',GenericDialog)
    h.window.new_connection(driver='oracle')
    h.window.new_connection(driver='sqlite')
    assert calls==['oracle','generic'] and not h.sessions


@pytest.mark.parametrize('new_profile',[True,False])
def test_actual_oracle_settings_save_stays_disconnected_and_preserves_new_edit_state(
        qtbot,oracle_host,new_profile):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QDialogButtonBox
    from opennavicat.ui.oracle_connection import OracleConnectionDialog
    h=oracle_host
    config=replace(h.config,id='generated-preset-'+uuid.uuid4().hex,name='Generated new preset') if new_profile else h.config
    observations,failures=[],[]
    def save():
        dialog=QApplication.activeModalWidget()
        try:
            assert isinstance(dialog,OracleConnectionDialog)
            assert dialog.back_button.isVisible() is new_profile
            assert dialog.remember_check.isChecked() is new_profile
            assert dialog.host_edit.text()==config.host and dialog.oracle_service_edit.text()=='FREEPDB1'
            dialog.name_edit.setText('Generated saved profile')
            dialog.remember_check.setChecked(False)
            dialog.password_edit.setText('generated-secret-must-not-be-saved')
            observations.append(dialog)
            qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save),Qt.MouseButton.LeftButton)
        except BaseException as error:
            failures.append(error)
            if dialog:dialog.reject()
    QTimer.singleShot(0,save)
    h.window.new_connection(config=config)
    if failures:raise failures[0]
    idle(qtbot,h.window)
    assert observations and config.id in h.window.configs
    assert h.window.configs[config.id].name=='Generated saved profile'
    assert h.window.configs[config.id].password=='' and not h.store.passwords.get(config.id)
    assert not h.sessions and not h.prompts and not h.window.sessions and not h.errors
    assert 'generated-secret-must-not-be-saved' not in h.store.path.read_text()


def test_actual_new_oracle_back_returns_to_chooser_without_save_or_connection(qtbot,oracle_host):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from opennavicat.ui.oracle_connection import OracleConnectionDialog
    h=oracle_host
    failures=[]
    def back():
        dialog=QApplication.activeModalWidget()
        try:
            assert isinstance(dialog,OracleConnectionDialog)
            qtbot.mouseClick(dialog.back_button,Qt.MouseButton.LeftButton)
        except BaseException as error:
            failures.append(error)
            if dialog:dialog.reject()
    QTimer.singleShot(0,back)
    h.window.new_connection(driver='oracle')
    if failures:raise failures[0]
    assert h.window._connection_manager.isVisible()
    assert not h.sessions and not h.prompts and len(h.store.connections())==1
    h.window._connection_manager.reject()


def test_unsupported_tls_draft_edit_preserves_existing_keyring_choice_and_still_refuses_connect(
        qtbot,oracle_host,monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication,QDialogButtonBox
    from opennavicat.ui.oracle_connection import OracleConnectionDialog
    h=oracle_host
    phrase='generated-retained-tls-key-phrase'
    config=replace(h.config,ssl_mode='verify-full',ssl_key='generated-key.pem',ssl_key_password=phrase)
    h.store.save_connection(config,remember_ssl=True)
    h.window.configs[config.id]=config
    failures=[]
    def save():
        dialog=QApplication.activeModalWidget()
        try:
            assert isinstance(dialog,OracleConnectionDialog) and dialog.ssl_remember_check.isChecked()
            dialog.oracle_notes_edit.setPlainText('Generated revised note')
            qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save),Qt.MouseButton.LeftButton)
        except BaseException as error:
            failures.append(error)
            if dialog:dialog.reject()
    QTimer.singleShot(0,save)
    h.window.new_connection(config=config)
    if failures:raise failures[0]
    assert h.store.state['remembered_secrets'][config.id]==['ssl_key_password']
    assert h.vault[('OpenNavicat',config.id+':ssl_key_password')]==phrase
    assert h.store.hydrate(h.window.configs[config.id]).ssl_key_password==phrase
    assert phrase not in h.store.path.read_text() and not h.sessions and not h.prompts
    monkeypatch.setattr(sys.modules['opennavicat.oracle_session'],'validate_oracle_execution',real_oracle_support_validation)
    h.window.connect_id(config.id)
    assert len(h.errors)==1 and 'TLS' in h.errors[0] and not h.prompts and not h.sessions
