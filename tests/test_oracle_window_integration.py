"""Actual Qt→Oracle acceptance, selected only by test-oracle-window.py.

Default tests exercise the runner's safe contracts and skip the actual class.
No existing profile, endpoint, TNS file, wallet or system keyring is accessed.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[1]
ACTUAL = os.environ.get('OPENNAVICAT_TEST_ORACLE_WINDOW') == '1' and bool(os.environ.get('OPENNAVICAT_TEST_ORACLE_RUN_ID'))


def runner():
    spec = importlib.util.spec_from_file_location('oracle_window_runner', ROOT / 'scripts/test-oracle-window.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate', 'skipped', 'failure', 'error', 'classname'])
def test_runner_rejects_nonexact_or_unpassed_junit(change):
    script = runner()
    node = script.TARGET + '::test_generated'
    case = '<testcase classname="tests.test_oracle_window_integration.TestOwnedOracleWindow" name="test_generated"{}>{}</testcase>'
    attributes, child = '', ''
    if change in {'skipped', 'failure', 'error'}: child = '<' + change + '/>'
    xml = '<testsuites><testsuite>' + case.format(attributes, child) + '</testsuite></testsuites>'
    nodes = [node]
    if change == 'missing': nodes.append(script.TARGET + '::test_missing')
    if change == 'extra': nodes = []
    if change == 'duplicate': xml = xml.replace('</testsuite>', case.format('', '') + '</testsuite>')
    if change == 'classname': xml = xml.replace('.TestOwnedOracleWindow', '.OtherClass')
    with pytest.raises(ValueError): script.strict_junit(xml, nodes)


def test_runner_preserves_backend_scope_and_redacts_before_persistence():
    script = runner()
    assert len(script.backend_source_paths()) >= 9
    assert 'tests/oracle_owned_fixture.py' in script.backend_source_paths()
    assert 'tests/oracle_images.json' in script.backend_source_paths()
    with pytest.raises(ValueError): script.backend_source_paths("TARGET='x.py'\nSOURCE_PATHS=('../outside',)\n")
    with pytest.raises(ValueError): script.backend_source_paths("TARGET='x.py'\nSOURCE_PATHS=('x.py','x.py')\n")
    node = script.TARGET + '::test_generated'
    xml = '<testsuites><testsuite><testcase classname="tests.test_oracle_window_integration.TestOwnedOracleWindow" name="test_generated"/></testsuite></testsuites>'
    assert script.strict_junit(xml, [node])['passed'] == 1
    assert script.redact_output('prefix generated-secret suffix', ['generated-secret']) == 'prefix [REDACTED] suffix'


def test_runner_ambient_pytest_filter_cannot_narrow_the_actual_collection(tmp_path, monkeypatch):
    script = runner()
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k test_dialog')
    clean = script.child_environment('offscreen')
    dirty = dict(clean, PYTEST_ADDOPTS='-k test_dialog')
    counts = []
    for name, env in (('filtered', dirty), ('actual', clean)):
        manifest = tmp_path / (name + '.json')
        result = subprocess.run([sys.executable, '-c', script.COLLECT, str(manifest), script.TARGET],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        counts.append(len(script.read_required(manifest)))
    assert counts[1] > counts[0] > 0


@pytest.mark.skipif(os.name != 'posix', reason='Current actual-fixture CLI is a macOS/Linux contract')
def test_runner_cleanup_before_allocation_needs_no_docker_or_database(tmp_path):
    env = os.environ.copy(); env['PATH'] = ''
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/test-oracle-window.py'),
        '--cleanup-interrupted', '--output', str(tmp_path / 'never-allocated')],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0
    receipt = json.loads(result.stdout)
    assert receipt['passed'] and receipt['no_start'] and receipt['no_database_connection'] and receipt['no_owned_ledger']


@pytest.mark.skipif(os.name != 'posix', reason='Current actual-fixture CLI is a macOS/Linux contract')
def test_runner_refuses_cocoa_on_linux_before_allocating(tmp_path, monkeypatch):
    script = runner()
    monkeypatch.setattr(script.platform, 'system', lambda: 'Linux')
    output = tmp_path / 'must-not-create'
    monkeypatch.setattr(sys, 'argv', ['test-oracle-window.py', '--qt-platform', 'cocoa', '--output', str(output)])
    with pytest.raises(SystemExit) as caught: script.main()
    assert caught.value.code == 2 and not output.exists()


@pytest.mark.skipif(os.name != 'posix', reason='Current macOS/Linux fixture runner uses an inherited JUnit pipe')
def test_runner_timeout_allows_real_child_finalizer_reaps_and_keeps_raw_junit_in_memory(tmp_path):
    script = runner()
    ready, finished = tmp_path / 'ready', tmp_path / 'finalized'
    code = """import os,pathlib,sys,time
try:
    pathlib.Path(sys.argv[2]).write_text('ready')
    print('generated-memory-secret',flush=True)
    os.write(int(sys.argv[1]),b'<testsuites>generated-memory-secret</testsuites>')
    while True: time.sleep(.05)
finally:
    pathlib.Path(sys.argv[3]).write_text('finalized')
"""
    result = script.run_bounded_child(lambda fd: [sys.executable, '-c', code, str(fd), str(ready), str(finished)],
        os.environ.copy(), timeout=3, grace=3)
    assert ready.read_text() == 'ready' and finished.read_text() == 'finalized'
    assert result['timed_out'] and result['reaped'] and result['returncode'] != 0
    assert not result['output_limited']
    assert result['readers_closed'] and result['pipe_cleanup_complete'] and result['reader_threads'] == 0
    assert 'generated-memory-secret' in result['junit']
    assert script.redact_output(result['junit'], ['generated-memory-secret']) == '<testsuites>[REDACTED]</testsuites>'
    with pytest.raises(ChildProcessError): os.waitpid(result['pid'], os.WNOHANG)
    assert not (tmp_path / 'results.xml').exists()


@pytest.mark.skipif(os.name != 'posix', reason='Owned fork/process-group regression for the POSIX fixture runner')
@pytest.mark.parametrize('pipe_mode', ['held', 'quiet'])
def test_runner_reaps_exit_zero_parent_and_cleans_held_or_quiet_grandchild(tmp_path, pipe_mode):
    # Run the harness in another owned process so a regression cannot hang the
    # source suite itself. PID files are written before the held-pipe stage.
    child = r'''import os,pathlib,sys,time
directory=pathlib.Path(sys.argv[2]);(directory/'leader.pid').write_text(str(os.getpid()))
pid=os.fork()
if pid==0:
 try:
  (directory/'grandchild.pid').write_text(str(os.getpid()))
  if sys.argv[3]=='quiet':
   for fd in {1,2,int(sys.argv[1])}: os.close(fd)
  else: os.write(int(sys.argv[1]),b'<testsuites/>')
  while True: time.sleep(.05)
 except KeyboardInterrupt: pass
 finally: (directory/'grandchild.finalized').write_text('yes')
 os._exit(0)
os._exit(0)
'''
    outer_code = r'''import importlib.util,json,os,pathlib,sys
spec=importlib.util.spec_from_file_location('owned_runner',sys.argv[1]);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
result=module.run_bounded_child(lambda fd:[sys.executable,'-c',sys.argv[3],str(fd),sys.argv[2],sys.argv[4]],os.environ.copy(),timeout=5,grace=1)
pathlib.Path(sys.argv[2],'result.json').write_text(json.dumps(result))
'''
    outer = subprocess.Popen([sys.executable, '-c', outer_code, str(ROOT / 'scripts/test-oracle-window.py'),
        str(tmp_path), child, pipe_mode], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    started = time.monotonic()
    watchdog = False
    try:
        try: output = outer.communicate(timeout=8)[0]
        except subprocess.TimeoutExpired:
            watchdog = True
            output = b''
        assert not watchdog, 'Held-pipe cleanup exceeded its independent watchdog'
        assert outer.returncode == 0, output.decode(errors='replace')
        result = json.loads((tmp_path / 'result.json').read_text())
        assert time.monotonic() - started < 8
        assert result['returncode'] == 0 and result['reaped']
        if pipe_mode == 'held':
            assert result['timed_out'] and result['inherited_pipe_timeout'] and result['group_cleanup_requested']
            assert (tmp_path / 'grandchild.finalized').read_text() == 'yes'
        else:
            assert not result['timed_out'] and not result['group_cleanup_requested']
        assert result['readers_closed'] and result['pipe_cleanup_complete'] and result['reader_threads'] == 0
        assert result['final_group_cleanup'] and result['process_group_absent']
        leader = int((tmp_path / 'leader.pid').read_text())
        grandchild = int((tmp_path / 'grandchild.pid').read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try: os.kill(grandchild, 0)
            except ProcessLookupError: break
            time.sleep(.01)
        else: pytest.fail('Owned grandchild PID was not absent after group cleanup')
        with pytest.raises(ProcessLookupError): os.kill(leader, 0)
        with pytest.raises(ProcessLookupError): os.killpg(leader, 0)
        with pytest.raises(ChildProcessError): os.waitpid(outer.pid, os.WNOHANG)
    finally:
        leader_file, grandchild_file = tmp_path / 'leader.pid', tmp_path / 'grandchild.pid'
        if leader_file.exists() and grandchild_file.exists():
            leader, grandchild = int(leader_file.read_text()), int(grandchild_file.read_text())
            try:
                if os.getpgid(grandchild) == leader: os.killpg(leader, signal.SIGKILL)
            except ProcessLookupError: pass
        if outer.poll() is None:
            os.killpg(outer.pid, signal.SIGKILL); outer.communicate(timeout=3)


def canonical_rows(rows):
    def cell(value):
        if isinstance(value, Decimal): return {'type': 'Decimal', 'value': str(value)}
        if isinstance(value, (date, datetime)): return {'type': type(value).__name__, 'value': value.isoformat()}
        if value is None or type(value) in {str, int, float, bool}: return {'type': type(value).__name__, 'value': value}
        raise AssertionError('Unexpected fixture value type')
    return [[cell(value) for value in row] for row in rows]


class OracleWindowHarness:
    def __init__(self, qtbot, tmp_path, monkeypatch, nodeid):
        import keyring
        import oracledb
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from opennavicat.database import ConnectionConfig
        from opennavicat.oracle_session import OracleSession
        from opennavicat.storage import Store
        from opennavicat.ui.window import MainWindow

        self.qtbot, self.gui = qtbot, threading.get_ident()
        self.run_id = os.environ['OPENNAVICAT_TEST_ORACLE_RUN_ID']
        self.username = os.environ['OPENNAVICAT_TEST_ORACLE_USER']
        if (not re.fullmatch(r'[a-f0-9]{32}', self.run_id)
                or self.username != 'ONV_' + self.run_id[:16].upper()):
            raise AssertionError('Actual window scope requires its owned schema identity')
        self.password = os.environ['OPENNAVICAT_TEST_ORACLE_PASSWORD']
        self.slow_view = os.environ['OPENNAVICAT_TEST_ORACLE_WINDOW_COMPUTE_VIEW']
        if self.slow_view != 'ONVW_COMPUTE_' + self.run_id[:12].upper():
            raise AssertionError('Compute view does not belong to this owned run')
        self.config = ConnectionConfig('oracle-window-' + hashlib.sha256(nodeid.encode()).hexdigest()[:16],
            'Oracle isolated example ' + self.run_id[:8], driver='oracle', host='127.0.0.1',
            port=int(os.environ['OPENNAVICAT_TEST_ORACLE_PORT']), username=self.username,
            database='', ssl_mode='disable', oracle_service_identifier='FREEPDB1', connect_timeout=5)
        self.report_dir = Path(os.environ['OPENNAVICAT_TEST_ORACLE_WINDOW_REPORT_DIR'])
        self.case_id = hashlib.sha256(nodeid.encode()).hexdigest()[:16]
        self.path = self.report_dir / (self.case_id + '.json')
        self.ledger = {'nodeid': nodeid, 'run_id': self.run_id, 'passed': False, 'phases': [], 'source_calls': []}
        actual_qt_platform = QApplication.platformName()
        if actual_qt_platform != os.environ['OPENNAVICAT_TEST_ORACLE_WINDOW_QT_PLATFORM']:
            raise AssertionError('The actual QApplication platform differs from the requested scope')
        self.ledger['qt_platform'] = actual_qt_platform
        self.calls, self.failures, self.messages, self.prompts = [], [], [], []
        self.dialogs, self.test_results = [], []
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='owned-oracle-observer')
        self.lock = threading.Lock()
        self.session_serials, self.next_session_serial = weakref.WeakKeyDictionary(), 0
        self.passed, self.finished = False, False
        original_connect = oracledb.connect
        def connect(*args, **kwargs):
            if threading.get_ident() == self.gui:
                raise AssertionError('Oracle driver I/O reached the GUI thread')
            return original_connect(*args, **kwargs)
        monkeypatch.setattr(oracledb, 'connect', connect)
        for name in ('connect', 'test_connection', 'schemas', 'objects', 'columns', 'fetch_table',
                     'close', '_read', '_metadata', 'cancel_current'):
            original = getattr(OracleSession, name)
            def wrap(method, operation):
                @functools.wraps(method)
                def call(session, *args, **kwargs):
                    thread = threading.get_ident()
                    if thread == self.gui:
                        raise AssertionError('An Oracle session I/O method reached the GUI thread')
                    record = {'method': operation, 'thread': thread, 'returned': False}
                    if operation == 'cancel_current': record['marker'] = id(args[0])
                    with self.lock:
                        if session not in self.session_serials:
                            self.next_session_serial += 1
                            self.session_serials[session] = self.next_session_serial
                        record['session'] = self.session_serials[session]
                        self.calls.append(record)
                    try:
                        result = method(session, *args, **kwargs)
                    except BaseException as error:
                        record['error_class'] = type(error).__name__
                        record['cancelled'] = getattr(error, 'cancelled', False) is True
                        raise
                    record['returned'] = True
                    return result
                return call
            monkeypatch.setattr(OracleSession, name, wrap(original, name))
        def forbidden(*_args, **_kwargs): raise AssertionError('Actual window scope must not access a system keyring')
        monkeypatch.setattr(keyring, 'get_password', forbidden)
        monkeypatch.setattr(keyring, 'set_password', forbidden)
        monkeypatch.setattr(keyring, 'delete_password', lambda *_args: None)
        self.store = Store(tmp_path / 'isolated-workspace')
        self.store.state['navicat_auto_discover'] = False
        self.window = MainWindow(self.store)
        qtbot.addWidget(self.window, before_close_func=lambda _widget: self.finish())
        self.window.show()
        self.modal_guard = QTimer(self.window); self.modal_guard.setInterval(10)
        def dismiss():
            from PySide6.QtWidgets import QApplication
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                self.messages.append({'title': dialog.windowTitle(), 'text': dialog.text()[:512]})
                self._capture('message-' + str(len(self.messages)), dialog)
                button = dialog.button(QMessageBox.StandardButton.Ok)
                if button is not None: button.click()
                else: dialog.accept()
        self.modal_guard.timeout.connect(dismiss); self.modal_guard.start()
        self.db(lambda connection: self._prove_owner(connection))
        self.baseline = self.read_facts()

    def _connection(self):
        import oracledb
        connection = oracledb.connect(user=self.username, password=self.password, host=self.config.host,
            port=self.config.port, service_name='FREEPDB1', retry_count=0, tcp_connect_timeout=3)
        connection.call_timeout = 5000
        return connection

    def _prove_owner(self, connection):
        with connection.cursor() as cursor:
            cursor.execute('SELECT RUN_ID FROM "' + self.username + '".OWNED_MARKER')
            if cursor.fetchall() != [(self.run_id,)]:
                raise AssertionError('Endpoint/schema did not prove the current owned run')

    def db(self, function, timeout=10000):
        def execute():
            with self._connection() as connection: return function(connection)
        future = self.pool.submit(execute)
        self.qtbot.waitUntil(future.done, timeout=timeout)
        return future.result()

    def read_facts(self):
        def read(connection):
            with connection.cursor() as cursor:
                cursor.execute('SELECT ID,LABEL,AMOUNT,NEGATIVE_SCALE,CREATED FROM "' + self.username
                    + '".OWNED_FACTS ORDER BY ID', fetch_decimals=True)
                return [list(row) for row in cursor.fetchall()]
        return self.db(read)

    def application_sessions(self):
        def read(connection):
            with connection.cursor() as cursor:
                cursor.execute("SELECT SID,SERIAL# FROM V$SESSION WHERE USERNAME=:owner AND MODULE=:module",
                    {'owner': self.username, 'module': 'OpenNavicat Oracle Basic'})
                return [(int(sid), int(serial)) for sid, serial in cursor.fetchall()]
        return self.db(read)

    def idle(self, timeout=10000):
        self.qtbot.waitUntil(lambda: not self.window.tasks and not self.window.pool.activeThreadCount()
            and not self.window.cancel_pool.activeThreadCount(), timeout=timeout)

    def _capture(self, phase, widget=None):
        self.ledger['phases'].append(phase)
        image = (widget or self.window).grab()
        if image.isNull() or not image.save(str(self.report_dir / (self.case_id + '-' + phase + '.png'))):
            raise AssertionError('Actual widget screenshot could not be retained')

    def _defer_modal(self, kind, function, timeout=4):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        deadline = time.monotonic() + timeout
        def poll():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, kind):
                try: function(dialog)
                except BaseException as error:
                    self.failures.append(error); dialog.reject()
            elif time.monotonic() < deadline:
                QTimer.singleShot(10, self.window, poll)
            else:
                self.failures.append(AssertionError('Expected actual Qt modal dialog did not open'))
        QTimer.singleShot(0, self.window, poll)

    def password_prompt(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialogButtonBox, QInputDialog, QLineEdit
        def fill(dialog):
            assert dialog.textEchoMode() == QLineEdit.EchoMode.Password
            self.prompts.append({'thread': threading.get_ident(), 'actual_class': type(dialog).__name__})
            dialog.setTextValue(self.password)
            self.qtbot.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
                Qt.MouseButton.LeftButton)
        self._defer_modal(QInputDialog, fill)

    def raise_failures(self):
        if self.failures: raise self.failures[0]

    def no_saved_password(self):
        if self.password.encode() in self.store.path.read_bytes():
            raise AssertionError('A temporary Oracle credential reached workspace JSON')
        if any(config.password for config in self.window.configs.values()):
            raise AssertionError('A temporary Oracle credential reached reusable window profiles')
        if any(value == self.password for value in self.store.passwords.values()):
            raise AssertionError('A temporary Oracle credential reached Store password cache')
        assert not self.store.state['remembered_passwords']

    def new_test_save(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialogButtonBox
        from opennavicat.ui.oracle_connection import OracleConnectionDialog
        def configure(dialog):
            self.dialogs.append(dialog)
            assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ['常规', '高级', '数据库', 'SSH', '备注']
            assert dialog.back_button.isVisible() and dialog.remember_check.isChecked()
            dialog.remember_check.setChecked(False)
            def tested(success, message):
                try:
                    self.test_results.append((success, message))
                    assert success and message.startswith('Oracle ')
                    assert dialog.test_banner.isVisible() and '连接成功' in dialog.test_status.text()
                    assert dialog.diagram.connection_succeeded
                    assert dialog.password_edit.text() == '' and not dialog.remember_password
                    self._capture('test-success', dialog)
                    self.qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Save), Qt.MouseButton.LeftButton)
                except BaseException as error:
                    self.failures.append(error); dialog.reject()
            dialog.test_finished.connect(tested)
            self.password_prompt()
            self.qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
        self._defer_modal(OracleConnectionDialog, configure)
        self.window.new_connection(config=self.config)
        self.raise_failures()
        self.qtbot.waitUntil(lambda: not self.dialogs[0]._test_pool.activeThreadCount(), timeout=5000)
        self.idle()
        assert len(self.test_results) == 1 and len(self.prompts) == 1
        assert self.config.id in self.window.configs and self.config.id not in self.window.sessions
        item = self.window.connection_item(self.config.id)
        assert item.childCount() == 1 and item.child(0).text(0) == '双击以打开连接'
        assert self.application_sessions() == []
        self.no_saved_password()
        self._capture('saved-disconnected')

    def edit_reopen(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialogButtonBox
        from opennavicat.ui.oracle_connection import OracleConnectionDialog
        def check(dialog):
            assert not dialog.back_button.isVisible() and not dialog.remember_check.isChecked()
            assert dialog.host_edit.text() == self.config.host and dialog.port_spin.value() == self.config.port
            assert dialog.user_edit.text() == self.username and dialog.oracle_service_edit.text() == 'FREEPDB1'
            assert dialog.oracle_role_combo.currentData() == 'default' and not dialog.oracle_os_auth_check.isChecked()
            assert dialog.password_edit.text() == ''
            self._capture('edit-reopened', dialog)
            self.qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
        self._defer_modal(OracleConnectionDialog, check)
        self.window.edit_connection()
        self.raise_failures()
        assert len(self.prompts) == 1 and not self.window.sessions

    def action(self, menu, label):
        top = next(action.menu() for action in self.window.menuBar().actions() if action.text() == menu)
        return next(action for action in top.actions() if action.text() == label)

    def open(self):
        self.password_prompt()
        self.action('连接', '打开连接').trigger()
        self.qtbot.waitUntil(lambda: self.config.id in self.window.sessions or bool(self.messages), timeout=15000)
        self.raise_failures(); self.idle()
        assert not self.messages
        self.session = self.window.sessions[self.config.id]
        from opennavicat.oracle_session import OracleSession
        assert type(self.session) is OracleSession
        self.connection = self.session.connection
        if self.session.config.password != self.password:
            raise AssertionError('The owned session did not receive its temporary credential')
        assert len(self.prompts) == 2 and all(prompt['thread'] == self.gui for prompt in self.prompts)
        self.no_saved_password()
        picker = self.window.schema_picker
        assert self.username in [picker.itemText(i) for i in range(picker.count())]
        assert self.window.active_schema == self.username
        item = next(item for item in self.window._schema_items(self.config.id, self.username))
        self.window.tree.expandItem(item); self.idle()
        assert [item.child(index).text(0) for index in range(item.childCount())] == ['表', '视图', '实体化视图', '函数', '查询']
        self.identity, = self.application_sessions()
        assert self.identity[0] > 0 and self.identity[1] > 0
        self._capture('connected-schemas')

    def catalog(self, kind):
        from opennavicat.ui.workspace import ORACLE_TABLE_COLUMNS, ORACLE_VIEW_COLUMNS
        self.window.category_actions[kind].trigger(); self.idle()
        browser = self.window.object_page
        expected = ORACLE_TABLE_COLUMNS if kind == 'table' else ORACLE_VIEW_COLUMNS
        assert browser.kind == kind and browser.table.columnCount() == len(expected)
        assert [browser.table.horizontalHeaderItem(i).text() for i in range(len(expected))] == [item[0] for item in expected]
        assert len(expected) == (28 if kind == 'table' else 13)
        assert not browser.design_action.isEnabled() and not browser.new_action.isEnabled()
        self._capture('catalog-' + kind)
        return browser

    def grid(self, kind, name, *, wait=True):
        from PySide6.QtCore import Qt
        from opennavicat.ui.window import TablePage
        browser = self.catalog(kind)
        row = next(index for index in range(browser.table.rowCount()) if browser.table.item(index, 0).text() == name)
        browser.table.setCurrentCell(row, 0)
        browser.table.scrollToItem(browser.table.item(row, 0))
        self.qtbot.mouseClick(browser.table.viewport(), Qt.MouseButton.LeftButton,
            pos=browser.table.visualItemRect(browser.table.item(row, 0)).center())
        assert browser.selected()['name'] == name and browser.open_action.isEnabled()
        self.qtbot.mouseDClick(browser.table.viewport(), Qt.MouseButton.LeftButton,
            pos=browser.table.visualItemRect(browser.table.item(row, 0)).center())
        self.qtbot.waitUntil(lambda: isinstance(self.window.tabs.currentWidget(), TablePage), timeout=5000)
        page = self.window.tabs.currentWidget()
        if wait:
            self.qtbot.waitUntil(lambda: page.model is not None or bool(self.messages), timeout=10000)
            self.idle(); assert not self.messages
            assert page.model is not None and not page.model.editable and page.total is None
            self._capture('grid-' + kind)
        return page

    def verify_grid(self, page, view=False):
        rows = page.current_result().rows
        expected = [row[:3] for row in self.baseline] if view else self.baseline
        assert canonical_rows(sorted(rows, key=lambda row: row[0]) if view else rows) == canonical_rows(expected)
        by_id = {row[0]: row for row in rows}
        assert type(rows[0][0]) is Decimal
        assert by_id[Decimal(9007199254740993)][1] == 'generated Oracle 样例'
        assert by_id[Decimal(9007199254740993)][2] == Decimal('0.12345678901234567890123456789012345678')
        assert by_id[Decimal(99999999999999999999999999999999999999)][2] == Decimal('123456789012345678.1234567890123456789')
        for index, row in enumerate(rows):
            assert page.model.data(page.model.index(index, 0)) == str(row[0])
            assert page.model.data(page.model.index(index, 1)) == row[1]
            assert page.model.data(page.model.index(index, 2)) == ('NULL' if row[2] is None else str(row[2]))
        assert not page.insert_button.isEnabled() and not page.delete_button.isEnabled()
        assert not page.save_button.isEnabled() and not page.record_editor_action.isEnabled()
        self.ledger['typed_' + ('view' if view else 'table')] = canonical_rows(rows)

    def observe_compute(self):
        sid, serial = self.identity
        def observe(connection):
            with connection.cursor() as cursor:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    cursor.execute('''SELECT S.STATUS,S.USERNAME,S.SID,S.SERIAL#,S.SQL_ID,S.SQL_CHILD_NUMBER,Q.SQL_TEXT
                        FROM V$SESSION S JOIN V$SQL Q ON Q.SQL_ID=S.SQL_ID AND Q.CHILD_NUMBER=S.SQL_CHILD_NUMBER
                        WHERE S.SID=:sid AND S.SERIAL#=:serial AND S.USERNAME=:owner''',
                        {'sid': sid, 'serial': serial, 'owner': self.username})
                    state = cursor.fetchone()
                    if state and state[0] == 'ACTIVE' and self.slow_view in state[6]:
                        return {'status': state[0], 'username': state[1], 'sid': int(state[2]), 'serial': int(state[3]),
                            'sql_id': state[4], 'child': int(state[5]), 'view': self.slow_view,
                            'sql_sha256': hashlib.sha256(state[6].encode()).hexdigest()}
                    time.sleep(.02)
            raise AssertionError('The exact owned session compute-view fetch was not observed')
        return self.db(observe)

    def cancel_compute(self):
        from PySide6.QtCore import QTimer
        page = self.grid('view', self.slow_view, wait=False)
        beats = []
        timer = QTimer(self.window); timer.setInterval(10); timer.timeout.connect(lambda: beats.append(time.monotonic())); timer.start()
        try:
            observed = self.observe_compute()
            self.qtbot.waitUntil(lambda: len(beats) >= 3, timeout=1000)
            assert observed['username'] == self.username and (observed['sid'], observed['serial']) == self.identity
            assert re.fullmatch(r'[a-z0-9]{13}', observed['sql_id']) and observed['child'] >= 0
            marker = self.session.current_operation_marker
            assert marker is not None and self.window.stop_action.isEnabled()
            self._capture('compute-running')
            self.window.stop_action.trigger()
            self.idle()
            assert not page.loading and self.messages and 'Stopped by user' in self.messages[-1]['text']
            assert self.session.current_operation_marker is None
            assert self.window.sessions[self.config.id] is self.session and self.session.connection is self.connection
            calls = [call for call in self.calls if call['session'] == self.session_serials[self.session]]
            owners = {call['thread'] for call in calls if call['method'] != 'cancel_current'}
            controls = [call for call in calls if call['method'] == 'cancel_current']
            assert len(owners) == 1 and controls and all(call['thread'] not in owners and call['thread'] != self.gui for call in controls)
            assert any(call['marker'] == id(marker) and call['returned'] for call in controls)
            assert any(call['method'] == 'fetch_table' and call.get('error_class') == 'OracleError'
                       and call.get('cancelled') for call in calls)
            self.ledger.update(compute_observed=observed, gui_heartbeat_count=len(beats), actual_stop_message=self.messages[-1])
            self.messages.clear()
            self.verify_grid(self.grid('table', 'OWNED_FACTS'))
            assert self.window.sessions[self.config.id] is self.session and self.session.connection is self.connection
            self._capture('same-session-reused')
        finally: timer.stop()

    def disconnect(self):
        self.action('连接', '关闭连接').trigger(); self.idle()
        assert self.config.id not in self.window.sessions and self.application_sessions() == []
        assert self.window.connection_item(self.config.id).child(0).text(0) == '双击以打开连接'
        self._capture('disconnected')

    def finish(self):
        if self.finished: return
        self.finished = True
        try:
            if self.window.tasks:
                self.window.stop_action.trigger(); self.idle(timeout=15000)
            self.window.close()
            self.qtbot.waitUntil(lambda: self.window._closed, timeout=15000)
            self.idle()
            assert self.application_sessions() == []
            assert canonical_rows(self.read_facts()) == canonical_rows(self.baseline)
            self.no_saved_password()
            with self.lock: calls = [dict(call) for call in self.calls]
            for session in {call['session'] for call in calls}:
                operations = [call for call in calls if call['session'] == session and call['method'] != 'cancel_current']
                assert len({call['thread'] for call in operations}) == 1
                assert any(call['method'] == 'close' and call['returned'] for call in operations)
            self.ledger.update(passed=self.passed, source_calls=calls, password_prompt_count=len(self.prompts),
                actual_password_dialogs=self.prompts, application_sessions_after_close=0, source_rows_unchanged=True,
                reusable_profiles_have_password=False, window_closed=self.window._closed)
        except BaseException as error:
            self.ledger.update(passed=False, cleanup_error_class=type(error).__name__)
            raise
        finally:
            self.modal_guard.stop()
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.path.write_text(json.dumps(self.ledger, indent=2, ensure_ascii=False) + '\n')


@pytest.fixture
def actual_window(qtbot, tmp_path, monkeypatch, request):
    harness = OracleWindowHarness(qtbot, tmp_path, monkeypatch, request.node.nodeid)
    try: yield harness
    finally: harness.finish()


@pytest.mark.skipif(not ACTUAL, reason='Requires scripts/test-oracle-window.py fresh owned native fixture')
class TestOwnedOracleWindow:
    def test_dialog_test_save_reopen_open_real_catalog_and_typed_grids(self, actual_window):
        h = actual_window
        h.new_test_save(); h.edit_reopen(); h.open()
        h.verify_grid(h.grid('table', 'OWNED_FACTS'))
        h.verify_grid(h.grid('view', 'OWNED_FACTS_VIEW'), view=True)
        h.disconnect()
        assert not h.messages
        h.passed = True

    @pytest.mark.parametrize('close_mode', ['disconnect', 'shutdown'])
    def test_real_compute_view_stop_independent_cancel_owner_reuse_and_close(self, actual_window, close_mode):
        h = actual_window
        h.new_test_save(); h.open()
        h.cancel_compute()
        if close_mode == 'disconnect': h.disconnect()
        else:
            h.window.close(); h.qtbot.waitUntil(lambda: h.window._closed, timeout=10000); h.idle()
            assert h.application_sessions() == []
        assert not h.messages
        h.ledger['close_mode'] = close_mode
        h.passed = True
