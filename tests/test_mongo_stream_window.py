"""Real owned replica-set stream execution through the MainWindow queues."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading
import time
import uuid

from bson import BSON, Binary, Decimal128, Int64, Timestamp
from pymongo import MongoClient
from pymongo.errors import InvalidOperation
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid
import pytest

from opennavicat.database import DatabaseError
from opennavicat.batch_jobs import new_batch_job, new_pipeline_reference, to_nbatpremium
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, pipeline_script, to_nagg
from opennavicat.mongodb_pipeline_stream import FiniteStreamResult, StreamEvent, StreamExecutionError, execute_finite_stream
from opennavicat.mongodb_session import BSON_CODEC, MongoSession, canonical_dumps, canonical_loads
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow
from test_mongodb_pipeline_stream import replica_service, Wire  # noqa: F401


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount()
                   and not window.cancel_pool.activeThreadCount(), timeout=20000)


def owned_read(qtbot, executor, function):
    future = executor.submit(function)
    qtbot.waitUntil(future.done, timeout=10000)
    return future.result()


@pytest.fixture
def stream_window(qtbot, tmp_path, monkeypatch, replica_service):
    import keyring
    import opennavicat.ui.window as window_module

    memory_keys = {}
    monkeypatch.setattr(keyring, 'get_password', lambda service, key: memory_keys.get((service, key)))
    monkeypatch.setattr(keyring, 'set_password', lambda service, key, value: memory_keys.__setitem__((service, key), value))
    monkeypatch.setattr(keyring, 'delete_password', lambda service, key: memory_keys.pop((service, key), None))
    monkeypatch.setattr(window_module, 'discover_navicat_profiles',
        lambda: pytest.fail('Owned stream windows must never discover user profiles'))
    config = replace(replica_service, id='stream-window-' + uuid.uuid4().hex,
        name='Generated Stream Window', database='stream_window_' + uuid.uuid4().hex, read_only=True)
    executor = ThreadPoolExecutor(max_workers=2)

    def native_setup():
        client = MongoClient(host=config.host, port=config.port, username=config.username,
            password=config.password, authSource='admin', directConnection=True,
            connectTimeoutMS=3000, serverSelectionTimeoutMS=3000, socketTimeoutMS=3000)
        client[config.database].sales.insert_one({'_id': 0, 'generated': True})
        return client

    native = executor.submit(native_setup).result(5)
    wire, errors, owner_threads, cancel_threads = Wire(config.database), [], {}, []
    options = MongoSession._connection_options

    def configured(session):
        value = options(session)
        return {**value, 'event_listeners': [wire]} if session.config.id == config.id else value

    monkeypatch.setattr(MongoSession, '_connection_options', configured)
    for method in ('connect', 'schemas', 'objects', '_new_bounded_pipeline_client', 'close'):
        original = getattr(MongoSession, method)

        def owner_call(session, *args, _original=original, _method=method, **kwargs):
            identity = threading.get_ident()
            assert identity != threading.main_thread().ident
            owner = owner_threads.setdefault(id(session), identity)
            assert owner == identity
            return _original(session, *args, **kwargs)

        monkeypatch.setattr(MongoSession, method, owner_call)
    original_cancel = MongoSession.cancel

    def control_call(session, *args, **kwargs):
        identity = threading.get_ident()
        assert identity != threading.main_thread().ident
        assert identity != owner_threads[id(session)]
        cancel_threads.append(identity)
        return original_cancel(session, *args, **kwargs)

    monkeypatch.setattr(MongoSession, 'cancel', control_call)
    monkeypatch.setattr(MainWindow, 'error', lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok)
    store = Store(tmp_path / 'generated-stream-workspace')
    store.state['navicat_auto_discover'] = False
    store.save_connection(config)
    window = MainWindow(store)

    def before_close(widget):
        if isValid(widget) and not widget._closed:
            idle(qtbot, widget)
            widget.close()
            qtbot.waitUntil(lambda: widget._closed, timeout=15000)
            idle(qtbot, widget)

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()
    try:
        window.connect_id(config.id)
        qtbot.waitUntil(lambda: config.id in window.sessions or bool(errors), timeout=15000)
        idle(qtbot, window)
        assert not errors
        yield window, config, native, executor, wire, errors, owner_threads, cancel_threads
    finally:
        before_close(window)
        executor.submit(native.drop_database, config.database).result(5)
        executor.submit(native.close).result(5)
        executor.shutdown(wait=True, cancel_futures=True)
    assert not errors
    assert config.password not in store.path.read_text()


def page_for(qtbot, fixture, body=None):
    window, config, *_rest = fixture
    page = window.open_mongo_pipeline(config.id, config.database)
    idle(qtbot, window)
    page.load_definition(PipelineDefinition(source='sales',
        stages=[PipelineStage('$changeStream', body or {})]))
    return page


def finish(qtbot, window, page):
    qtbot.waitUntil(lambda: not page.running, timeout=15000)
    idle(qtbot, window)
    assert not window._mongo_pipeline_jobs and not window._mongo_pipeline_cancel_states


def assert_no_cursors(qtbot, fixture):
    _window, config, native, executor, *_rest = fixture

    def check():
        with native.admin.aggregate([{'$currentOp': {'allUsers': False, 'idleCursors': True}},
            {'$match': {'$or': [{'ns': config.database + '.sales'}, {'command.$db': config.database}]}},
            {'$limit': 16}]) as cursor:
            assert not tuple(cursor)

    owned_read(qtbot, executor, check)


def test_real_empty_stream_run_is_text_only_finite_and_late_insert_does_not_poll(qtbot, stream_window):
    window, config, native, executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    script = pipeline_script(page.draft())
    original_icon = window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey()
    run_button = page.toolbar.widgetForAction(page.run_action)
    assert run_button is not None and run_button.isVisible() and run_button.isEnabled()
    with qtbot.waitSignal(page.runRequested, timeout=1000) as request:
        qtbot.mouseClick(run_button, Qt.MouseButton.LeftButton)
    assert request.args[0] == page.draft()
    assert page.running and not page.run_action.isEnabled() and page.stop_action.isEnabled()
    assert not page.operator_combo.isEnabled() and page.tabs.currentWidget() is page.pipeline_tab
    assert [page.tabs.tabText(index) for index in range(page.tabs.count())
            if page.tabs.isTabVisible(index)] == ['管道', '脚本预览']
    assert window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey() != original_icon
    finish(qtbot, window, page)
    assert page._last_stream_result.completion == 'empty_batch' and not page._last_stream_result.events
    assert page.tabs.currentWidget() is page.messages and '> OK' in page.messages.toPlainText()
    assert script in page.messages.toPlainText() and '> 查询时间:' in page.messages.toPlainText()
    assert page.tabs.indexOf(page.results) == -1 and page.results.rowCount() == 0
    before = len([command for command in wire.commands if 'getMore' in command])
    assert before == 1
    owned_read(qtbot, executor, lambda: native[config.database].sales.insert_one({'_id': 1, 'late': True}))
    qtbot.wait(150)
    assert len([command for command in wire.commands if 'getMore' in command]) == before
    assert not page.run_action.isEnabled() and not page.stop_action.isEnabled()
    assert window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey() == original_icon
    page.tabs.setCurrentWidget(page.pipeline_tab)
    assert page.run_action.isEnabled()
    assert_no_cursors(qtbot, stream_window)


def test_real_two_events_keep_exact_bson_private_and_messages_output_match(qtbot, stream_window):
    window, config, native, executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    records = [{'_id': 1, 'private': 'generated-private-event-value', 'n': Int64(9007199254740993),
        'd': Decimal128('1.25'), 'b': Binary(b'\x00\xff', 128), 't': Timestamp(9, 2)}, {'_id': 2, 'owned': True}]

    def produce():
        assert wire.getmore.wait(5)
        native[config.database].sales.insert_many(records)

    producer = executor.submit(produce)
    run_button = page.toolbar.widgetForAction(page.run_action)
    assert run_button is not None and run_button.isVisible() and run_button.isEnabled()
    with qtbot.waitSignal(page.runRequested, timeout=1000) as request:
        qtbot.mouseClick(run_button, Qt.MouseButton.LeftButton)
    assert request.args[0] == page.draft()
    finish(qtbot, window, page)
    producer.result(2)
    result = page._last_stream_result
    assert [canonical_loads(event.canonical_json)['fullDocument'] for event in result.events] == records
    assert all(BSON.encode(canonical_loads(event.canonical_json), codec_options=BSON_CODEC) == event.raw_bson
               for event in result.events)
    assert page.stream_output.toPlainText() == page.messages.toPlainText()
    assert 'generated-private-event-value' not in page.messages.toPlainText()
    assert 'fullDocument' not in page.messages.toPlainText() and page.last_rows == []
    assert page.tabs.indexOf(page.results) == -1 and not page.export_action.isEnabled()
    page.tabs.setCurrentWidget(page.stream_output)
    assert '> OK' in page.stream_output.toPlainText()
    assert_no_cursors(qtbot, stream_window)


def test_real_local_stop_uses_independent_control_worker_and_retains_draft(qtbot, stream_window):
    window, _config, _native, _executor, wire, _errors, _owners, cancels = stream_window
    page = page_for(qtbot, stream_window)
    before = to_nagg(page.draft())
    assert window.run_mongo_pipeline(page, page.draft())
    qtbot.waitUntil(wire.getmore.is_set, timeout=5000)
    page.stop_action.trigger()
    finish(qtbot, window, page)
    assert cancels and window.cancel_pool is not window.pool
    assert page._last_stream_result.completion == 'cancelled'
    assert 'Stopped by user' in page.messages.toPlainText() and '> OK' not in page.messages.toPlainText()
    assert to_nagg(page.draft()) == before
    assert_no_cursors(qtbot, stream_window)


def test_real_queued_local_stop_before_execute_is_typed_cancel_and_no_aggregate(qtbot, stream_window):
    window, _config, _native, _executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    entered, release = threading.Event(), threading.Event()

    def preceding():
        entered.set()
        assert release.wait(5)

    window.submit(preceding, label='Owned queue gate')
    qtbot.waitUntil(entered.is_set, timeout=3000)
    try:
        assert window.run_mongo_pipeline(page, page.draft())
        job = next(iter(window._mongo_pipeline_jobs.values()))
        assert not job[2].is_set()
        page.stop_action.trigger()
        assert job[1].is_set() and not job[2].is_set()
    finally:
        release.set()
    finish(qtbot, window, page)
    assert not [command for command in wire.commands if command.get('aggregate') == 'sales']
    assert page._last_stream_result.completion == 'cancelled'
    assert 'Stopped by user' in page.messages.toPlainText()
    assert_no_cursors(qtbot, stream_window)


def test_real_local_stop_only_targets_its_page_and_does_not_stop_queued_sibling(qtbot, stream_window):
    window, _config, _native, _executor, wire, *_rest = stream_window
    first = page_for(qtbot, stream_window)
    second = page_for(qtbot, stream_window)
    assert first is not second
    assert window.run_mongo_pipeline(first, first.draft())
    assert window.run_mongo_pipeline(second, second.draft())
    qtbot.waitUntil(wire.getmore.is_set, timeout=5000)
    first.stop_action.trigger()
    qtbot.waitUntil(lambda: not first.running and not second.running, timeout=15000)
    idle(qtbot, window)
    assert first._last_stream_result.completion == 'cancelled'
    assert second._last_stream_result.completion == 'empty_batch'
    assert '> OK' in second.messages.toPlainText()
    assert_no_cursors(qtbot, stream_window)


def test_real_fresh_run_switching_tabs_and_saved_draft_do_not_share_result_or_token(qtbot, stream_window):
    window, config, native, executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    draft = page.draft(); draft.name = 'generated_stream_saved'
    saved = window.save_mongo_pipeline(page, draft)
    assert saved is not None
    before = window.store.mongo_pipeline(saved['id'])['nagg']

    def produce():
        assert wire.getmore.wait(5)
        native[config.database].sales.insert_one({'_id': 1, 'generated': True})

    producer = executor.submit(produce)
    assert window.run_mongo_pipeline(page, page.draft())
    window.tabs.setCurrentWidget(window.object_page)
    finish(qtbot, window, page)
    producer.result(2)
    assert window.tabs.currentWidget() is window.object_page
    assert len(page._last_stream_result.events) == 1 and not page.dirty
    assert window.run_mongo_pipeline(page, page.draft())
    finish(qtbot, window, page)
    assert not page._last_stream_result.events
    assert window.store.mongo_pipeline(saved['id'])['nagg'] == before
    assert 'resumeAfter' not in page.draft().stages[0].body
    assert_no_cursors(qtbot, stream_window)


@pytest.mark.parametrize('completion', ['byte_limit', 'cleanup_failed'])
def test_real_window_stream_partial_errors_are_private_not_ok(qtbot, stream_window, monkeypatch, completion):
    import opennavicat.ui.window as window_module

    window, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    document = {'private': 'generated-private-partial-value'}
    partial = FiniteStreamResult((StreamEvent(BSON.encode(document), json.dumps(document)),),
        completion, .02, undelivered_event_count=1, undelivered_raw_bytes=128)

    def failed(_session, _schema, _definition, *, cancel_event):
        assert threading.get_ident() != threading.main_thread().ident
        if completion == 'cleanup_failed':
            cancel_event.set()
        raise StreamExecutionError('never render this raw private exception', partial)

    monkeypatch.setattr(window_module.mongo_pipeline_tools, 'execute_pipeline', failed)
    assert window.run_mongo_pipeline(page, page.draft())
    finish(qtbot, window, page)
    assert page._last_stream_result is partial and '> OK' not in page.messages.toPlainText()
    assert 'generated-private-partial-value' not in page.messages.toPlainText()
    assert 'raw private exception' not in page.messages.toPlainText()
    assert page.last_rows == [] and page.tabs.indexOf(page.results) == -1
    if completion == 'cleanup_failed':
        assert 'cleanup' in page.messages.toPlainText() and 'Stopped by user' not in page.messages.toPlainText()


def test_real_window_ordinary_result_tuple_still_uses_existing_grid(qtbot, stream_window):
    window, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    assert window.run_mongo_pipeline(page, page.draft())
    finish(qtbot, window, page)
    page.load_definition(PipelineDefinition(source='sales', stages=[PipelineStage('$match', {})]))
    assert window.run_mongo_pipeline(page, page.draft())
    finish(qtbot, window, page)
    assert page._last_stream_result is None and page.last_rows
    assert page.tabs.currentWidget() is page.results and page.results.rowCount() == 1
    assert page.tabs.tabText(page.tabs.indexOf(page.results)) == '结果'


def test_real_cold_transport_local_stop_prevents_aggregate_after_warmup(qtbot, stream_window, monkeypatch):
    window, _config, _native, _executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    entered, release = threading.Event(), threading.Event()
    original = MongoSession._new_bounded_pipeline_client

    def warm_then_gate(session, *args, **kwargs):
        client = original(session, *args, **kwargs)
        entered.set()
        assert release.wait(5)
        return client

    monkeypatch.setattr(MongoSession, '_new_bounded_pipeline_client', warm_then_gate)
    assert window.run_mongo_pipeline(page, page.draft())
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert window.stop_mongo_pipeline(page)
    finally:
        release.set()
    finish(qtbot, window, page)
    assert page._last_stream_result.completion == 'cancelled'
    assert 'Stopped by user' in page.messages.toPlainText()
    assert not [command for command in wire.commands if command.get('aggregate') == 'sales']
    assert_no_cursors(qtbot, stream_window)


def test_real_owner_failure_awaiting_gui_delivery_is_not_masked_by_late_stop(qtbot, stream_window, monkeypatch):
    import opennavicat.ui.window as window_module

    window, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    partial = FiniteStreamResult((), 'error', .01)

    def failed(_session, _schema, _definition, *, cancel_event):
        assert threading.get_ident() != threading.main_thread().ident
        assert not cancel_event.is_set()
        raise StreamExecutionError('private cause must not become a cancellation', partial)

    monkeypatch.setattr(window_module.mongo_pipeline_tools, 'execute_pipeline', failed)
    assert window.run_mongo_pipeline(page, page.draft())
    state = next(iter(window._mongo_pipeline_cancel_states.values()))
    # Hold GUI delivery while the real serial worker completes. This wait
    # reads only a thread Event; no database call or Qt callback runs here.
    assert state['owner_finished'].wait(3)
    assert page.running
    assert not window.stop_mongo_pipeline(page)
    window.stop()
    assert next(iter(window._mongo_pipeline_jobs.values()))[1].is_set()
    finish(qtbot, window, page)
    assert page._last_stream_result is partial
    assert 'Stopped by user' not in page.messages.toPlainText()
    assert '> OK' not in page.messages.toPlainText()
    assert 'private cause' not in page.messages.toPlainText()
    assert_no_cursors(qtbot, stream_window)


def test_real_byte_overflow_from_backend_is_partial_failure_without_business_text(qtbot, stream_window, monkeypatch):
    import opennavicat.ui.window as window_module

    window, config, native, executor, _wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)

    def prepare():
        collection = native[config.database].sales
        with collection.watch(max_await_time_ms=50) as probe:
            collection.insert_one({'_id': 1, 'anchor': True})
            token = probe.try_next()['_id']
            collection.insert_many([{'_id': number, 'private': 'generated-private-byte-' + 'x' * 180}
                                    for number in (2, 3)])
            events = (probe.try_next(), probe.try_next())
        assert all(event is not None for event in events)
        budget = max(len(canonical_dumps(event).encode()) for event in events) + 10
        return token, budget

    token, budget = owned_read(qtbot, executor, prepare)
    draft = page.draft()
    draft.stages[0].body = {'resumeAfter': token}
    draft.stages[0].reference_mql = None
    page.load_definition(draft)

    def bounded(session, database, definition, *, cancel_event):
        assert threading.get_ident() != threading.main_thread().ident
        return execute_finite_stream(session, database, definition, max_bytes=budget,
                                     await_ms=50, cancel_event=cancel_event)

    # Change only the delivery bound at the real backend entry. Cursor I/O,
    # BSON conversion, overflow detection, and owner cleanup all remain real.
    monkeypatch.setattr(window_module.mongo_pipeline_tools, 'execute_pipeline', bounded)
    assert window.run_mongo_pipeline(page, page.draft())
    finish(qtbot, window, page)
    result = page._last_stream_result
    assert result.completion == 'byte_limit' and len(result.events) == 1
    assert result.undelivered_event_count == 1 and result.undelivered_raw_bytes > 0
    assert 'generated-private-byte-' in result.events[0].canonical_json
    assert 'generated-private-byte-' not in page.messages.toPlainText()
    assert 'generated-private-byte-' not in page.stream_output.toPlainText()
    assert '> OK' not in page.messages.toPlainText() and page.last_rows == []
    assert_no_cursors(qtbot, stream_window)


def test_real_queued_run_captures_session_definition_and_script_before_caller_mutates(qtbot, stream_window, monkeypatch):
    import opennavicat.ui.window as window_module

    window, config, _native, _executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    requested = page.draft()
    script = pipeline_script(requested)
    session = window.sessions[config.id]
    entered, release = threading.Event(), threading.Event()
    captured = []
    original = window_module.mongo_pipeline_tools.execute_pipeline

    def execute(owner, database, definition, *, cancel_event):
        assert threading.get_ident() != threading.main_thread().ident
        captured.append((owner, database, to_nagg(definition)))
        return original(owner, database, definition, cancel_event=cancel_event)

    def preceding():
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(window_module.mongo_pipeline_tools, 'execute_pipeline', execute)
    window.submit(preceding, label='Owned snapshot gate')
    qtbot.waitUntil(entered.is_set, timeout=3000)
    try:
        assert window.run_mongo_pipeline(page, requested)
        requested.source = 'not_the_captured_collection'
        requested.stages[0].body['fullDocument'] = 'updateLookup'
    finally:
        release.set()
    finish(qtbot, window, page)
    assert captured == [(session, config.database, to_nagg(page.draft()))]
    aggregate = next(command for command in wire.commands if command.get('aggregate') == 'sales')
    assert aggregate['pipeline'] == [{'$changeStream': {}}]
    assert script in page.messages.toPlainText()
    assert 'not_the_captured_collection' not in page.messages.toPlainText()
    assert page._last_stream_result.completion == 'empty_batch'
    assert_no_cursors(qtbot, stream_window)


def saved_stream_batch(window, config, *, continue_on_error=True):
    pipeline = window.store.save_mongo_pipeline(config.id, config.database, 'generated_batch_stream',
        to_nagg(PipelineDefinition(source='sales', stages=[PipelineStage('$changeStream', {})])))
    definition = new_batch_job()
    definition.general['ContinueOnError'] = continue_on_error
    definition.general['EnableLogging'] = True
    definition.jobs = [new_pipeline_reference(config.name, config.database, pipeline['name']) for _ in range(2)]
    entry = window.store.save_batch_job('generated_stream_batch', to_nbatpremium(definition))
    return window.batch_job_controller.open_job(entry['id'])


def capture_owned_batch_sessions(monkeypatch, config):
    connected, closed = [], []
    original_connect, original_close = MongoSession.connect, MongoSession.close

    def connect(session):
        assert threading.get_ident() != threading.main_thread().ident
        assert (session.config.id, session.config.host, session.config.port, session.config.database) == (
            config.id, config.host, config.port, config.database)
        assert session.config.read_only
        connected.append(session)
        return original_connect(session)

    def close(session):
        assert any(session is owner for owner in connected)
        clients = (session.connection, session._control, session._finite_stream_client)
        original_close(session)
        closed.append(session)
        assert session.connection is None
        for client in clients:
            if client is not None:
                # The owner has closed every transport. A public command on a
                # closed client rejects locally; it cannot dispatch new I/O.
                with pytest.raises(InvalidOperation):
                    client.admin.command('ping')

    monkeypatch.setattr(MongoSession, 'connect', connect)
    monkeypatch.setattr(MongoSession, 'close', close)
    return connected, closed


def start_batch_from_toolbar(qtbot, window, page):
    button = page.toolbar.widgetForAction(page.start_action)
    assert button is not None and button.isVisible() and button.isEnabled()
    with qtbot.waitSignal(page.startRequested, timeout=1000) as request:
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert request.args[0] == page.draft()
    qtbot.waitUntil(lambda: not page.running and not window.batch_job_controller.runs, timeout=15000)
    idle(qtbot, window)
    assert page.last_result is not None
    return page.last_result


def test_real_batch_empty_stream_duplicates_start_off_with_independent_sessions_and_persist_history(
        qtbot, stream_window, monkeypatch):
    import opennavicat.batch_job_runtime as runtime

    window, config, native, executor, wire, *_rest = stream_window
    main_session = window.sessions[config.id]
    assert window.disconnect(config.id)
    idle(qtbot, window)
    assert config.id not in window.sessions
    root = window.connection_item(config.id)
    assert root.childCount() == 1 and root.child(0).text(0) == '双击以打开连接'
    connected, closed = capture_owned_batch_sessions(monkeypatch, config)
    outcomes = []
    original = runtime.execute_pipeline

    def execute(session, database, definition, *, cancel_event):
        result = original(session, database, definition, cancel_event=cancel_event)
        outcomes.append(result)
        return result

    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    page = saved_stream_batch(window, config)
    job_id, directory = page.job_id, window.store.directory
    result = start_batch_from_toolbar(qtbot, window, page)
    assert result['status'] == 'success' and result['total'] == result['success'] == 2
    assert result['failed'] == result['cancelled'] == result['skipped'] == 0
    assert len(outcomes) == 2 and all(isinstance(value, FiniteStreamResult)
        and value.completion == 'empty_batch' and not value.events for value in outcomes)
    assert len(connected) == len(closed) == 2 and connected[0] is not connected[1]
    assert all(owner is not main_session and owner is done and owner.connection is None
               for owner, done in zip(connected, closed))
    assert len([command for command in wire.commands if command.get('aggregate') == 'sales']) == 2
    assert len([command for command in wire.commands if 'getMore' in command]) == 2
    assert config.id not in window.sessions
    assert root.childCount() == 1 and root.child(0).text(0) == '双击以打开连接'
    assert owned_read(qtbot, executor, lambda: native[config.database].sales.count_documents({})) == 1
    assert_no_cursors(qtbot, stream_window)
    persisted = owned_read(qtbot, executor, lambda: Store(directory).batch_job_history(job_id))
    assert persisted == [result] and result['execution_id'] == 1
    assert page.history_table.rowCount() == 1
    window.close_tab(window.tabs.indexOf(page))
    reopened = window.batch_job_controller.open_job(job_id)
    assert reopened is not page and reopened.history_table.rowCount() == 1
    assert reopened._history[0]['execution_id'] == 1 and reopened._history[0]['status'] == 'success'
    assert config.id not in window.sessions


def test_real_batch_stream_row_limit_fails_skips_next_and_keeps_partial_events_out_of_history(
        qtbot, stream_window, monkeypatch):
    import opennavicat.batch_job_runtime as runtime

    window, config, native, executor, wire, *_rest = stream_window
    main_session = window.sessions[config.id]
    assert window.disconnect(config.id)
    idle(qtbot, window)
    connected, closed = capture_owned_batch_sessions(monkeypatch, config)
    original, outcomes = runtime.execute_pipeline, []

    def bounded(session, database, definition, *, cancel_event):
        # Exercise production routing and a real returned row-limit result;
        # only the supported public limit argument differs from the default.
        result = original(session, database, definition, limit=1, cancel_event=cancel_event)
        outcomes.append(result)
        return result

    monkeypatch.setattr(runtime, 'execute_pipeline', bounded)
    page = saved_stream_batch(window, config, continue_on_error=False)
    job_id, directory = page.job_id, window.store.directory
    records = [{'_id': value, 'private': 'generated-private-batch-row-limit-value',
                'n': Int64(9007199254740993)} for value in (1, 2)]

    def produce():
        assert wire.getmore.wait(5)
        native[config.database].sales.insert_many(records)

    producer = executor.submit(produce)
    result = start_batch_from_toolbar(qtbot, window, page)
    producer.result(2)
    assert result['status'] == 'failed' and result['failed'] == result['skipped'] == 1
    assert result['success'] == result['cancelled'] == 0 and result['total'] == 2
    assert len(outcomes) == 1 and isinstance(outcomes[0], FiniteStreamResult)
    partial = outcomes[0]
    assert partial.completion == 'row_limit' and len(partial.events) == 1
    assert canonical_loads(partial.events[0].canonical_json)['fullDocument'] == records[0]
    assert partial.truncated and partial.undelivered_event_count == 0
    assert len(connected) == len(closed) == 1 and connected[0] is closed[0]
    assert connected[0] is not main_session and connected[0].connection is None
    assert len([command for command in wire.commands if command.get('aggregate') == 'sales']) == 1
    assert config.id not in window.sessions
    assert owned_read(qtbot, executor, lambda: native[config.database].sales.count_documents({})) == 3
    assert_no_cursors(qtbot, stream_window)
    persisted = owned_read(qtbot, executor, lambda: Store(directory).batch_job_history(job_id))
    assert persisted == [result] and result['execution_id'] == 1
    assert [log['status'] for log in persisted[0]['logs']] == ['running', 'failed', 'skipped']
    assert 'row limit' in persisted[0]['logs'][1]['message']
    assert not persisted[0]['logs'][1]['execution_completed']
    page.history_table.selectRow(0)
    text = json.dumps(persisted) + page.messages.toPlainText() + page.history_log.toPlainText()
    assert 'generated-private-batch-row-limit-value' not in text
    assert 'fullDocument' not in text and config.password not in text
    assert 'partial_result' not in text and 'raw_bson' not in text


def test_real_bad_limit_toolbar_run_preserves_bounded_server_error_and_aggregate_location(
        qtbot, stream_window, monkeypatch):
    window, _config, _native, _executor, wire, *_rest = stream_window
    page = page_for(qtbot, stream_window)
    draft = page.draft()
    draft.stages.append(PipelineStage('$limit', 1))
    page.load_definition(draft)
    errors, present = [], page.show_stream_error

    def show(error, script):
        assert threading.get_ident() == threading.main_thread().ident
        errors.append(error)
        return present(error, script)

    monkeypatch.setattr(page, 'show_stream_error', show)
    button = page.toolbar.widgetForAction(page.run_action)
    assert button is not None and button.isVisible() and button.isEnabled()
    with qtbot.waitSignal(page.runRequested, timeout=1000):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    finish(qtbot, window, page)
    assert len(errors) == 1 and isinstance(errors[0], StreamExecutionError)
    failure = errors[0]
    expected = '$limit is not permitted in a $changeStream pipeline'
    assert failure.server_message == expected and failure.error_phase == 'aggregate'
    assert failure.script_location == (1, 27)
    assert failure.partial_result.completion == 'error' and not failure.partial_result.events
    text = page.messages.toPlainText()
    assert '> [Error] ' + expected in text and '\nat line 1, column 27\n' in text
    assert '> 查询时间:' in text and '> OK' not in text
    assert page.tabs.currentWidget() is page.messages
    assert not page.tabs.isTabVisible(page.tabs.indexOf(page.stream_output))
    assert page.tabs.indexOf(page.results) == -1 and page.last_rows == []
    aggregate = [command for command in wire.commands if command.get('aggregate') == 'sales']
    assert len(aggregate) == 1 and aggregate[0]['pipeline'] == [{'$changeStream': {}}, {'$limit': 1}]
    assert not [command for command in wire.commands if 'getMore' in command]
    assert_no_cursors(qtbot, stream_window)
