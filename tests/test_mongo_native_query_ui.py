"""Native-JS page owner-thread, presentation and cancellation contracts.

The controlled engine proves UI/worker handoff; native-helper cases execute the
real official engine, and one fresh-owned Mongo case exercises actual writes.
"""
from contextlib import contextmanager
from pathlib import Path
from dataclasses import replace
import os
import threading
import uuid

import pytest
from bson import Int64, json_util
from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import QTabWidget, QWidget, QVBoxLayout
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig
from opennavicat.js_engine.codec import encode
from opennavicat.js_engine.presentation import JSON_OPTIONS
from opennavicat.js_engine.records import CommandReceipt
from opennavicat.js_engine.runtime import JavaScriptError, JavaScriptResult, NativeJavaScript
from opennavicat.tasks import Task
from opennavicat.ui.mongo_native_query import NativeMongoQueryPage


class Session:
    def __init__(self):
        self.config = ConnectionConfig('mongo', 'Synthetic Mongo', driver='mongodb', database='owned')
        self._selected_schema = 'owned'
        self.gui = threading.get_ident()
        self.calls = []

    def use_schema(self, database):
        assert threading.get_ident() != self.gui
        self.calls.append(database)
        self._selected_schema = database


class Host(QWidget):
    def __init__(self, session):
        super().__init__()
        self.sessions = {'mongo': session}
        self.configs = {'mongo': session.config, 'sqlite': ConnectionConfig('sqlite', 'Synthetic SQLite')}
        self.tabs = QTabWidget()
        QVBoxLayout(self).addWidget(self.tabs)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.tasks = set()
        self.failures = []
        self.generic_runs = self.generic_stops = 0

    def submit(self, function, success=None, failure=None, label='', finished=None):
        task = Task(function)
        self.tasks.add(task)
        if success: task.signals.succeeded.connect(success)
        task.signals.failed.connect(failure or self.failures.append)
        def finish():
            self.tasks.discard(task)
            if finished: finished()
        task.signals.finished.connect(finish)
        self.pool.start(task)

    def connection_item(self, *_): return None
    def save_sql(self, **_): return True
    def export_rows(self): pass
    def focus_workspace(self, *_): pass
    def set_active(self, *_): pass
    def refresh_actions(self): pass
    def run_query(self, *_): self.generic_runs += 1
    def stop(self): self.generic_stops += 1


def register(qtbot, host, release=None, cleanup=None):
    def close(widget):
        assert widget is host and isValid(host.pool)
        if release is not None: release.set()
        for i in range(host.tabs.count()):
            page = host.tabs.widget(i)
            if isinstance(page, NativeMongoQueryPage): page.request_cancel()
        qtbot.waitUntil(lambda:not host.tasks and host.pool.activeThreadCount() == 0, timeout=10000)
        if cleanup is not None:
            host.submit(cleanup)
            qtbot.waitUntil(lambda:not host.tasks and host.pool.activeThreadCount() == 0, timeout=10000)
            assert not host.failures
        assert host.pool.waitForDone(1000)
    qtbot.addWidget(host, before_close_func=close)


def page_for(qtbot, engine, session=None, release=None):
    host = Host(session or Session())
    register(qtbot, host, release)
    factory_threads = []
    def factory():
        factory_threads.append(threading.get_ident())
        return engine
    page = NativeMongoQueryPage(host, 'mongo', engine_factory=factory)
    host.tabs.addTab(page, 'Synthetic JS')
    return host, page, factory_threads


def ready(qtbot, host, page):
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount() and not page.running, timeout=10000)
    assert not host.failures


def test_run_button_and_ctrl_enter_capture_gui_state_and_exact_bson_on_serial_worker(qtbot):
    gui = threading.get_ident()
    calls = []
    class Engine:
        def execute(self, session, source, **options):
            assert threading.get_ident() != gui
            calls.append((source, options))
            return JavaScriptResult(value_tree=encode([{'long': Int64(9007199254740995), 'text': '<b>literal</b>'}]),
                commands=(CommandReceipt(1, 'find', False, 'acknowledged'),), child_reaped=True)
    host, page, factories = page_for(qtbot, Engine())
    assert page.connection_picker.count() == 1 and not page.format_button.isEnabled()
    page.editor.setPlainText('const x=1; db.docs.find({x});')
    qtbot.mouseClick(page.run_button, Qt.MouseButton.LeftButton)
    ready(qtbot, host, page)
    assert calls[0][0] == 'const x=1; db.docs.find({x});'
    assert factories[0] != gui and calls[0][1]['database'] == 'owned'
    result = page.current_result()
    assert result.rows[0][0] == 'Canonical Extended JSON'
    assert json_util.loads(result.rows[0][1], json_options=JSON_OPTIONS)['long'] == Int64(9007199254740995)
    assert page.summary.item(0, 4).text() == 'unknown'
    qtbot.keyClick(page.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    ready(qtbot, host, page)
    assert len(calls) == 2 and host.generic_runs == host.generic_stops == 0
    assert calls[0][1]['cancel_event'] is not calls[1][1]['cancel_event']


def test_stop_sets_only_own_token_until_terminal_and_late_cancel_cannot_affect_new_run(qtbot):
    entered, release = threading.Event(), threading.Event()
    events = []
    class Engine:
        def execute(self, session, source, *, cancel_event, **_):
            events.append(cancel_event)
            if len(events) == 1:
                entered.set()
                assert cancel_event.wait(3)
                assert release.wait(3)
                result = JavaScriptResult(cancelled=True, child_reaped=True, hard_killed=True,
                    commands=(CommandReceipt(1, 'insert', True, 'acknowledged', 1),), error='Cancelled after acknowledged write')
                raise JavaScriptError(result.error, result)
            return JavaScriptResult(value_tree=['double', '42'], child_reaped=True)
    host, page, _ = page_for(qtbot, Engine(), release=release)
    page.editor.setPlainText('synthetic source')
    assert page.run_native()
    qtbot.waitUntil(entered.is_set)
    qtbot.mouseClick(page.stop_button, Qt.MouseButton.LeftButton)
    assert page.running and not page.run_button.isEnabled() and events[0].is_set()
    release.set()
    ready(qtbot, host, page)
    assert 'remain committed' in page.info.toPlainText()
    assert page.summary.item(0, 3).text() == 'acknowledged'
    assert not page.request_cancel()
    assert page.run_native()
    ready(qtbot, host, page)
    assert not events[1].is_set() and events[0] is not events[1]
    assert host.generic_stops == 0


def test_destroyed_page_cancels_owned_task_and_drains_before_host_deletion(qtbot):
    entered = threading.Event()
    class Engine:
        def execute(self, session, source, *, cancel_event, **_):
            entered.set()
            assert cancel_event.wait(3)
            result = JavaScriptResult(error='Cancelled', cancelled=True, child_reaped=True)
            raise JavaScriptError(result.error, result)
    host, page, _ = page_for(qtbot, Engine())
    page.editor.setPlainText('while(true){}')
    page.run_native()
    qtbot.waitUntil(entered.is_set)
    page.deleteLater()
    qtbot.waitUntil(lambda:not isValid(page))
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount())
    assert not host.failures


@pytest.fixture(scope='module')
def native():
    name = 'opennavicat-js.exe' if os.name == 'nt' else 'opennavicat-js'
    helper = Path(__file__).resolve().parents[1]/'artifacts/js-engine'/name
    if not helper.exists(): pytest.skip('Build the pinned helper for actual native Qt execution')
    return NativeJavaScript(helper)


def test_actual_native_qt_variables_bigint_and_cpu_loop_stop(qtbot, native):
    # The actual helper runs without a DB capability. This platform-native VM
    # test does not pull a transport/cryptography SDK into the six Qt lanes.
    class VMOnly:
        def execute(self, session, source, *, database, limits, cancel_event):
            return native.evaluate(source, limits=limits, cancel_event=cancel_event)
    host, page, _ = page_for(qtbot, VMOnly())
    page.editor.setPlainText('function twice(x){return x*2n;} twice(9007199254740993n)')
    page.run_native()
    ready(qtbot, host, page)
    assert '18014398509481986' in page.current_result().rows[0][1]
    assert page.last_execution.child_reaped
    page.editor.setPlainText('while(true){}')
    page.run_native()
    qtbot.waitUntil(lambda:page.running)
    qtbot.wait(40)
    page.request_cancel()
    ready(qtbot, host, page)
    assert page.last_execution.cancelled and page.last_execution.child_reaped
    assert host.generic_stops == 0


# Reuse only the fresh, random, exact-label-owned container fixture. This file
# never accepts the retained manual reference endpoint or Dell GUI fixture.
@pytest.fixture(scope='module')
def mongodb_service(request):
    from test_mongodb_session import mongodb_service as owned_fixture
    yield from owned_fixture.__wrapped__(request)


def test_fresh_mongo_native_query_ui_real_bson_writes_partial_failure_and_readonly(qtbot, native, mongodb_service, monkeypatch):
    from opennavicat.mongodb_session import MongoSession
    gui = threading.get_ident()
    threads = []
    check = MongoSession._check
    def traced(session):
        threads.append(threading.get_ident())
        assert threading.get_ident() != gui
        return check(session)
    monkeypatch.setattr(MongoSession, '_check', traced)
    config = replace(mongodb_service, id='mongo', database='onv_native_ui_'+uuid.uuid4().hex)
    session = MongoSession(config)
    host = Host(session)
    def cleanup():
        try:
            if session.connection is not None: session.connection.drop_database(config.database)
        finally: session.close()
    register(qtbot, host, cleanup=cleanup)
    host.submit(session.connect)
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount(), timeout=10000)
    assert not host.failures
    page = NativeMongoQueryPage(host, 'mongo', engine_factory=lambda:native)
    host.tabs.addTab(page, 'Synthetic live JS')
    page.editor.setPlainText("for(let i=0;i<3;i++)db.docs.insertOne({_id:'row'+i,long:Long('9007199254740995'),literal:i});db.docs.find().sort({_id:1})")
    page.run_native()
    ready(qtbot, host, page)
    assert not page.last_execution.error and page.last_execution.confirmed_write_count == 3
    assert len(page.current_result().rows) == 3
    for row in page.current_result().rows:
        doc = json_util.loads(row[1], json_options=JSON_OPTIONS)
        assert type(doc['long']) is Int64 and doc['long'] == 9007199254740995
        assert type(doc['literal']) is float
    page.editor.setPlainText("db.docs.insertOne({_id:'first-write'});db.docs.insertOne({_id:'row0'});db.docs.insertOne({_id:'must-not-run'})")
    page.run_native()
    ready(qtbot, host, page)
    assert page.last_execution.error and page.last_execution.confirmed_write_count == 1
    assert 'remain committed' in page.info.toPlainText()
    config.read_only = True
    page.editor.setPlainText('db.docs.deleteMany({})')
    page.run_native()
    ready(qtbot, host, page)
    assert page.last_execution.error and not page.last_execution.has_confirmed_writes
    verified = []
    host.submit(lambda: verified.extend(session.connection[config.database].docs.distinct('_id')))
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount())
    assert set(verified) == {'row0', 'row1', 'row2', 'first-write'}
    assert threads and all(thread != gui for thread in threads)
