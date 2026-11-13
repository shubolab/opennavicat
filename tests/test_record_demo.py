"""Actual Qt privacy surfaces and recorder-owned timeout/error contracts."""
from __future__ import annotations

import importlib.util
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import traceback
import uuid
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QPushButton, QToolButton, QVBoxLayout, QWidget

from test_mongodb_session import mongodb_service, session


@pytest.fixture
def recorder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "record-demo.py"
    spec = importlib.util.spec_from_file_location("record_demo_tests",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_form_records_actual_three_pages_with_no_endpoint_or_success(qtbot, recorder, tmp_path, monkeypatch):
    import oracledb
    from PySide6.QtGui import QImage
    from opennavicat.oracle_session import OracleSession
    original = OracleSession.connect
    monkeypatch.setattr(oracledb, 'connect', lambda **kwargs: pytest.fail('Form capture must not connect'))
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitExposed(dialog)
        for index, title in enumerate(['常规', '高级', '数据库']):
            dialog.tabs.setCurrentIndex(index)
            qtbot.wait(10)
            facts = recorder.verify_oracle_form_demo(dialog)
            assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == title
            assert facts['pages'] == ['常规', '高级', '数据库', 'SSH', '备注']
            assert facts['service_identifier'] == 'ORCL'
            assert facts['host_empty'] and facts['username_empty'] and facts['credentials_empty']
            assert not facts['profile_saved'] and not facts['database_connection_attempted']
            assert not facts['database_success_claimed']
            recorder.assert_visible_privacy(dialog, tmp_path)
            source = dialog.grab().toImage()
            fitted = recorder.fit_capture_image(source, 1440, 900, save_sheet=True)
            assert not source.isNull() and fitted.format() == QImage.Format.Format_RGB32
            assert (fitted.width(), fitted.height()) == (1440, 900)
    assert not dialog.isVisible() and OracleSession.connect is original


@pytest.mark.parametrize('field,value', [
    ('host_edit', '127.0.0.1'), ('user_edit', 'private-user'), ('password_edit', 'private-password'),
    ('oracle_settings_edit', '/private/owned-profile-path'), ('oracle_service_edit', 'PRIVATE_SERVICE'),
    ('ssh_password_edit', 'private-password'), ('ssh_key_passphrase_edit', 'private-key-passphrase'),
])
def test_oracle_form_rejects_hidden_or_visible_private_values_before_capture(qtbot, recorder, field, value):
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        dialog.show()
        # Check every form field even when another page is selected; a masked
        # password must remain forbidden when remember-password is unchecked.
        dialog.remember_check.setChecked(False)
        getattr(dialog, field).setText(value)
        dialog.tabs.setCurrentIndex(2)
        with pytest.raises(RuntimeError, match='nondefault data'):
            recorder.verify_oracle_form_demo(dialog)


def test_oracle_form_guard_blocks_real_backend_connect_and_restores_on_error(qtbot, recorder, monkeypatch):
    import oracledb
    from opennavicat.database import ConnectionConfig, DatabaseSession
    from opennavicat.oracle_session import OracleSession
    original = OracleSession.connect
    monkeypatch.setattr(oracledb, 'connect', lambda **kwargs: pytest.fail('No native Oracle I/O'))
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        session = DatabaseSession(ConnectionConfig('generated', 'Generated', driver='oracle'))
        with pytest.raises(RuntimeError, match='forbids database connections'):
            session.connect()
        with pytest.raises(RuntimeError, match='connection/save attempt'):
            recorder.verify_oracle_form_demo(dialog)
    assert OracleSession.connect is original
    # A job created during the form cannot gain network access by starting late.
    with pytest.raises(RuntimeError, match='forbids database connections'):
        session.connect()


def test_oracle_form_acceptance_cannot_be_reported_as_an_unsaved_demo(qtbot, recorder):
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        dialog.accept()
        assert dialog._config is not None
        with pytest.raises(RuntimeError, match='connection/save attempt'):
            recorder.verify_oracle_form_demo(dialog)


def test_oracle_form_native_guard_restores_even_when_reject_fails(qtbot, recorder, monkeypatch):
    from opennavicat.oracle_session import OracleSession
    original = OracleSession.connect
    guard = recorder.oracle_form_demo()
    dialog = guard.__enter__()
    qtbot.addWidget(dialog)
    original_reject = dialog.reject
    def failed_reject():
        raise RuntimeError('controlled reject failure')
    monkeypatch.setattr(dialog, 'reject', failed_reject)
    try:
        with pytest.raises(RuntimeError, match='controlled reject failure'):
            guard.__exit__(None, None, None)
        assert OracleSession.connect is original
    finally:
        monkeypatch.setattr(dialog, 'reject', original_reject)
        dialog.reject()


def test_oracle_demo_widget_verification_rejects_worker_thread(qtbot, recorder):
    from concurrent.futures import ThreadPoolExecutor
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(RuntimeError, match='GUI owner'):
                pool.submit(recorder.verify_oracle_form_demo, dialog).result(timeout=3)


def test_oracle_ui_extension_retains_all_prior_workflows_in_order(recorder):
    previous = recorder.required_demo_phases(mongodb=True, stream=True)
    extended = recorder.required_demo_phases(mongodb=True, stream=True, oracle_form=True)
    assert len(previous) == 31 and extended[:31] == previous
    assert extended[31:] == ('oracle_connection_general', 'oracle_connection_advanced', 'oracle_connection_databases')
    assert len(extended) == 34 and len(set(extended)) == 34
    assert recorder.required_demo_phases(oracle_form=True) == recorder.SQLITE_PHASES + recorder.ORACLE_FORM_PHASES
    with pytest.raises(ValueError, match='requires'):
        recorder.required_demo_phases(stream=True)


def test_oracle_extended_capture_requires_explicit_output_before_any_recording(recorder, monkeypatch):
    monkeypatch.setattr(recorder.sys, 'argv', ['record-demo.py', '--include-oracle-form'])
    monkeypatch.setattr(recorder, 'dependencies', lambda *_args: ('unused-encoder', 'unused-version'))
    monkeypatch.setattr(recorder, 'record', lambda *_args: pytest.fail('No capture without explicit output'))
    with pytest.raises(SystemExit) as caught:
        recorder.main()
    assert caught.value.code == 2


@pytest.mark.parametrize('surface', ['profile_list', 'host_placeholder', 'notes_placeholder'])
def test_oracle_form_privacy_reads_actual_list_and_empty_editor_placeholder_surfaces(qtbot, recorder, tmp_path, surface):
    with recorder.oracle_form_demo() as dialog:
        qtbot.addWidget(dialog)
        dialog.show()
        if surface == 'profile_list':
            dialog.profiles.item(0).setText('private-host 127.0.0.1')
        elif surface == 'host_placeholder':
            dialog.host_edit.setPlaceholderText('127.0.0.1')
        else:
            dialog.tabs.setCurrentIndex(4)
            dialog.oracle_notes_edit.setPlaceholderText(str(tmp_path / 'private-note'))
        with pytest.raises(RuntimeError, match='endpoint|filesystem path'):
            recorder.assert_visible_privacy(dialog, tmp_path)


@pytest.mark.parametrize("corruption", [None, "rounded_stock", "wrong_price", "wrong_sku", "wrong_display"])
def test_generated_javascript_demo_requires_exact_bson_and_actual_display(recorder, corruption):
    from bson import Decimal128, Int64
    from opennavicat.js_engine.codec import encode
    from opennavicat.js_engine.presentation import display_value

    values = [{"sku": "DEMO-001", "stock": Int64(9007199254740994), "price": Decimal128("1299.95")},
              {"sku": "DEMO-002", "stock": Int64(9007199254740995), "price": Decimal128("349.50")},
              {"sku": "DEMO-003", "stock": Int64(9007199254740996), "price": Decimal128("79.00")}]
    if corruption == "rounded_stock":
        values[1]["stock"] = float(values[1]["stock"])
    elif corruption == "wrong_price":
        values[0]["price"] = "1299.95"
    elif corruption == "wrong_sku":
        values[2]["sku"] = "WRONG-SAMPLE"
    tree = encode(values)
    rows = [list(display_value(child)) for child in tree[1]]
    if corruption == "wrong_display":
        altered = deepcopy(values[0]); altered["stock"] = 0
        rows[0] = list(display_value(encode(altered)))
    if corruption is None:
        recorder.verify_mongo_query_values(tree, rows)
    else:
        with pytest.raises(RuntimeError, match="exact generated BSON"):
            recorder.verify_mongo_query_values(tree, rows)


@pytest.mark.parametrize('corruption', [None, 'name', 'options', 'option_type', 'non_bson_value', 'not_empty', 'engine', 'index',
    'cluster_missing', 'cluster_name', 'cluster_unique', 'expire', 'change_images',
    'collation_missing', 'collation_value', 'collation_version'])
def test_demo_storage_requires_exact_independent_catalog_and_actual_engine_settings(recorder, corruption):
    from bson import Int64
    selected_options = recorder.mongo_collection_options()
    cluster = {**selected_options['clusteredIndex'], 'v': 2}
    catalog = {'name': recorder.MONGO_STORAGE_COLLECTION, 'type': 'collection',
               'options': {**recorder.mongo_storage_options(),
                   'clusteredIndex': cluster,
                   'expireAfterSeconds': selected_options['expireAfterSeconds'],
                   'changeStreamPreAndPostImages': deepcopy(selected_options['changeStreamPreAndPostImages']),
                   'collation': {**recorder.mongo_demo_collation(), 'version': '57.1'}}}
    stats = {'wiredTiger': {'creationString': 'allocation_size=4KB,block_compressor=zlib'},
             'indexDetails': {'generated_cluster': {
                 'name': 'generated_cluster', 'clustered': True}}}
    count = 0
    if corruption == 'name': catalog['name'] = 'not_the_generated_collection'
    elif corruption == 'options': catalog['options'].pop('indexOptionDefaults')
    elif corruption == 'option_type': catalog['options']['storageEngine']['wiredTiger']['configString'] = Int64(1)
    elif corruption == 'non_bson_value': catalog['options']['storageEngine']['wiredTiger']['configString'] = object()
    elif corruption == 'not_empty': count = 1
    elif corruption == 'engine': stats['wiredTiger']['creationString'] = 'block_compressor=snappy'
    elif corruption == 'index': stats['indexDetails']['generated_cluster']['clustered'] = False
    elif corruption == 'cluster_missing': catalog['options'].pop('clusteredIndex')
    elif corruption == 'cluster_name': catalog['options']['clusteredIndex']['name'] = 'wrong_cluster'
    elif corruption == 'cluster_unique': catalog['options']['clusteredIndex']['unique'] = False
    elif corruption == 'expire': catalog['options']['expireAfterSeconds'] = 1
    elif corruption == 'change_images': catalog['options']['changeStreamPreAndPostImages']['enabled'] = False
    elif corruption == 'collation_missing': catalog['options'].pop('collation')
    elif corruption == 'collation_value': catalog['options']['collation']['strength'] = 3
    elif corruption == 'collation_version': catalog['options']['collation']['version'] = ''
    if corruption is None:
        receipt = recorder.verify_mongo_storage_values(catalog, stats, count)
        assert receipt['options_verified'] and receipt['collection_storage_verified']
        assert receipt['index_option_defaults_verified'] and receipt['clustered_index_metadata_verified']
        assert not receipt['default_index_storage_verified']
        assert receipt['default_index_storage_observation'] == 'clustered_index_exposes_no_creation_string'
        assert receipt['collection_options_verified'] and receipt['collection_options_selected'] == selected_options
        assert receipt['collation_verified'] and receipt['collation_selected'] == recorder.mongo_demo_collation()
        assert receipt['collection'] == recorder.MONGO_STORAGE_COLLECTION
    else:
        with pytest.raises(RuntimeError, match='independently verified'):
            recorder.verify_mongo_storage_values(catalog, stats, count)


def test_real_demo_storage_verifier_reads_owned_collection_and_default_id_index(recorder, session):
    database = session.connection[session.config.database]
    database.create_collection(recorder.MONGO_STORAGE_COLLECTION,
        collation=recorder.mongo_demo_collation(), **recorder.mongo_storage_options(),
        **recorder.mongo_collection_options())
    with database.list_collections(filter={'name': recorder.MONGO_STORAGE_COLLECTION}) as cursor:
        catalog = list(cursor)
    assert len(catalog) == 1
    stats = database.command({'collStats': recorder.MONGO_STORAGE_COLLECTION})
    receipt = recorder.verify_mongo_storage_values(catalog[0], stats,
        database[recorder.MONGO_STORAGE_COLLECTION].count_documents({}, limit=1))
    assert (receipt['empty_collection_verified'] and receipt['index_option_defaults_verified']
            and receipt['clustered_index_metadata_verified']
            and receipt['collection_options_verified'] and receipt['collation_verified'])


@pytest.mark.parametrize('method', ['verify_storage_collection', 'batch_source_snapshot'])
def test_demo_storage_independent_client_never_runs_on_gui_thread(recorder, monkeypatch, method):
    service = recorder.OwnedMongoDemo()
    monkeypatch.setattr(service, 'inspect', lambda: pytest.fail('No Docker access belongs on the GUI thread'))
    with pytest.raises(RuntimeError, match='background owner'):
        getattr(service, method)()


@pytest.mark.parametrize('method', ['verify_storage_collection', 'batch_source_snapshot'])
def test_demo_storage_native_client_failure_does_not_expose_endpoint_or_password(recorder, monkeypatch, method):
    from concurrent.futures import ThreadPoolExecutor
    import pymongo
    service = recorder.OwnedMongoDemo()
    service.port = 54321
    monkeypatch.setattr(service, 'inspect', lambda: None)
    def unavailable(**kwargs):
        raise OSError(service.password + ' ' + service.host + ':' + str(service.port))
    monkeypatch.setattr(pymongo, 'MongoClient', unavailable)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError) as captured:
            pool.submit(getattr(service, method)).result()
    rendered = ''.join(traceback.format_exception(captured.value))
    assert service.password not in rendered and service.host not in rendered and str(service.port) not in rendered
    assert captured.value.__suppress_context__ is True


def test_actual_save_sheet_privacy_rejects_modal_title_and_name_before_capture(qtbot, recorder, tmp_path):
    from opennavicat.mongodb_collections import CollectionDefinition, CollectionPlan, CollectionSnapshot
    from opennavicat.ui.mongo_collection_designer import MongoCollectionSaveDialog
    service = recorder.OwnedMongoDemo()
    service.port = 54321
    definition = CollectionDefinition(recorder.MONGO_STORAGE_COLLECTION, recorder.mongo_storage_options())
    snapshot = CollectionSnapshot(recorder.MONGO_DATABASE, '', CollectionDefinition(), exists=False)
    dialog = MongoCollectionSaveDialog(definition, True)
    qtbot.addWidget(dialog)
    dialog.show_plan(CollectionPlan(snapshot, definition,
        [{'create': recorder.MONGO_STORAGE_COLLECTION, **recorder.mongo_storage_options()}], True))
    dialog.show()
    qtbot.waitExposed(dialog)
    recorder.assert_visible_privacy(dialog, tmp_path, service)
    dialog.setWindowTitle(service.password)
    with pytest.raises(RuntimeError, match='endpoint or credential'):
        recorder.assert_visible_privacy(dialog, tmp_path, service)
    dialog.setWindowTitle('保存集合')
    dialog.name_edit.setText(str(tmp_path / 'private-generated-path'))
    with pytest.raises(RuntimeError, match='filesystem path'):
        recorder.assert_visible_privacy(dialog, tmp_path, service)


def test_modal_capture_preserves_actual_aspect_ratio_on_plain_neutral_canvas(recorder):
    from PySide6.QtGui import QColor, QImage
    source = QImage(40, 20, QImage.Format.Format_RGB32)
    source.fill(QColor('#ffffff'))
    source.setDevicePixelRatio(2)
    fitted = recorder.fit_capture_image(source, 160, 100, save_sheet=True)
    assert fitted.width() == 160 and fitted.height() == 100 and fitted.devicePixelRatio() == 1
    assert fitted.pixelColor(0, 0) == QColor('#202124')
    assert fitted.pixelColor(80, 9) == QColor('#202124')
    assert fitted.pixelColor(0, 10) == QColor('#ffffff')
    assert fitted.pixelColor(159, 89) == QColor('#ffffff')
    assert fitted.pixelColor(80, 90) == QColor('#202124')
    main = recorder.fit_capture_image(source, 160, 100)
    assert main.pixelColor(0, 0) == QColor('#ffffff') and main.pixelColor(159, 99) == QColor('#ffffff')


def test_null_modal_capture_is_rejected_instead_of_encoding_blank_success(recorder):
    from PySide6.QtGui import QImage
    with pytest.raises(RuntimeError, match='did not produce a capture'):
        recorder.fit_capture_image(QImage(), 160, 100, save_sheet=True)


def test_actual_main_and_modal_frames_share_one_encodable_pixel_format(recorder, tmp_path):
    import shutil
    from PySide6.QtGui import QColor, QImage

    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        pytest.skip('Actual GIF encoding requires ffmpeg and ffprobe')
    frames = tmp_path / 'frames'; frames.mkdir()
    for number in range(6):
        modal = number >= 3
        source = QImage(40, 20, QImage.Format.Format_ARGB32_Premultiplied
                        if modal else QImage.Format.Format_RGB32)
        source.fill(QColor('#7bb75e' if number % 2 else '#ffffff'))
        fitted = recorder.fit_capture_image(source, 160, 100, save_sheet=modal)
        assert fitted.format() == QImage.Format.Format_RGB32
        assert fitted.save(str(frames / f'frame-{number:05d}.png'))
    output = tmp_path / 'actual-surfaces.gif'
    recorder.encode(shutil.which('ffmpeg'), frames, 6, output)
    receipt = dict(frames=6, fps=6, size=[160, 100], duration_seconds=1)
    recorder.verify_gif(output, receipt)
    assert receipt['gif_probe']['frames'] == 6


@pytest.mark.parametrize("button_class",[QPushButton,QToolButton])
def test_actual_visible_button_endpoint_is_rejected_before_capture(qtbot,recorder,tmp_path,button_class):
    window = QWidget()
    qtbot.addWidget(window)
    window.setWindowTitle("Generated sample")
    layout = QVBoxLayout(window)
    button = button_class()
    button.setText("192.0.2.51:54321")  # Reserved documentation address, no server.
    layout.addWidget(button)
    window.show()
    qtbot.waitExposed(window)
    assert button.isVisible() and button.text() in list(recorder.visible_texts(window))
    with pytest.raises(RuntimeError,match="endpoint or credential"):
        recorder.assert_visible_privacy(window,tmp_path)
    button.setText("生成示例")
    recorder.assert_visible_privacy(window,tmp_path)


def test_visible_button_local_path_is_rejected(qtbot,recorder,tmp_path):
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    button = QPushButton(str(tmp_path / "generated-private-path"))
    layout.addWidget(button)
    window.show()
    qtbot.waitExposed(window)
    with pytest.raises(RuntimeError,match="filesystem path"):
        recorder.assert_visible_privacy(window,tmp_path)


@pytest.mark.parametrize("failure",["timeout","oserror"])
def test_docker_transport_failures_do_not_put_generated_password_in_traceback(recorder,monkeypatch,failure):
    service = recorder.OwnedMongoDemo()
    arguments = ["docker","run","-e","MONGO_INITDB_ROOT_PASSWORD="+service.password]
    def unavailable(*args,**kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments,120)
        raise OSError("Generated launch failure: "+service.password)
    monkeypatch.setattr(recorder.subprocess,"run",unavailable)
    with pytest.raises(RuntimeError) as captured:
        service._docker(*arguments[1:])
    rendered = "".join(traceback.format_exception(captured.value))
    safe = service.password not in rendered and "MONGO_INITDB_ROOT_PASSWORD=" not in rendered
    assert safe, "Docker transport failure exposed its generated credential argv"
    assert captured.value.__suppress_context__ is True


def _fake_owned_start(recorder,monkeypatch,*,seed_delay=0):
    """No Docker process: isolate which native timeout scope each phase sees."""
    from pymongo import _csot
    import opennavicat.mongodb_session as module

    observed, closed = [], []
    class ReadySession:
        def __init__(self,config):
            self.connection = self
        def connect(self):
            observed.append(("connect",_csot.remaining()))
        def server_info(self):
            observed.append(("server_info",_csot.remaining()))
            return {"version":"generated-fixture"}
        def close(self):
            closed.append(True)
    monkeypatch.setattr(module,"MongoSession",ReadySession)
    service = recorder.OwnedMongoDemo()
    def docker(*args,**kwargs):
        if args[0] == "run":
            return "generated-container"
        if args[0] == "image":
            return json.dumps([{"Architecture":"arm64"}])
        if args[0] == "info":
            return "arm64"
        pytest.fail("No other Docker commands belong to the readiness test")
    monkeypatch.setattr(service,"_docker",docker)
    monkeypatch.setattr(service,"inspect",lambda **kwargs:{"Image":"generated-image",
        "NetworkSettings":{"Ports":{"27017/tcp":[{"HostIp":"127.0.0.1","HostPort":"54321"}]}}})
    def seed(session,*,deadline):
        observed.append(("seed",_csot.remaining()))
        assert 0 < recorder.remaining_seconds(deadline) <= .2
        if seed_delay:
            time.sleep(seed_delay)
    monkeypatch.setattr(service,"_seed",seed)
    return service, observed, closed


def test_same_remaining_native_deadline_covers_connect_server_info_and_seed(recorder,monkeypatch):
    service, observed, closed = _fake_owned_start(recorder,monkeypatch)
    service.start(readiness_seconds=.2)
    assert [phase for phase,_ in observed] == ["connect","server_info","seed"]
    assert all(remaining is not None and 0 < remaining <= .2 for _,remaining in observed)
    assert observed[-1][1] <= observed[0][1]
    assert closed == [True]


def test_seed_deadline_expiry_fails_and_closes_probe(recorder,monkeypatch):
    service, observed, closed = _fake_owned_start(recorder,monkeypatch,seed_delay=.08)
    with pytest.raises(RuntimeError,match="readiness deadline expired"):
        service.start(readiness_seconds=.04)
    assert observed[-1][0] == "seed" and closed == [True]


def test_real_pymongo_server_info_wait_is_bounded_by_remaining_budget(recorder):
    """Native TCP accepts MongoDB's hello but never answers; no external DB."""
    from pymongo import MongoClient, timeout
    from pymongo.errors import PyMongoError

    stop = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1",0))
    server.listen()
    server.settimeout(.1)
    sockets = []
    def peer():
        while not stop.is_set():
            try:
                connection,_ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sockets.append(connection)
    thread = threading.Thread(target=peer,daemon=True)
    thread.start()
    client = MongoClient("127.0.0.1",server.getsockname()[1],connect=False,
                         connectTimeoutMS=60000,serverSelectionTimeoutMS=60000,timeoutMS=120000)
    started = time.monotonic()
    try:
        with pytest.raises(PyMongoError):
            with timeout(recorder.remaining_seconds(started+.15)):
                client.server_info()
        assert time.monotonic()-started < 3
        assert sockets  # The actual PyMongo client reached the owned TCP peer.
    finally:
        client.close()
        stop.set()
        server.close()
        thread.join(timeout=2)
        for connection in sockets:
            connection.close()
        assert not thread.is_alive()


def _batch_facts(recorder):
    from bson import BSON, Binary
    result = dict(status='success', execute='Manual', execution_id=1, total=2,
        success=2, failed=0, skipped=0, cancelled=0, event_delivery_failures=0,
        start_time='2026-01-01T00:00:00+00:00', end_time='2026-01-01T00:00:01+00:00',
        duration_ms=1000.0, logs=[])
    for index in range(2):
        for status in ('running', 'success'):
            event = dict(status=status, job_index=index, name=recorder.MONGO_BATCH_PIPELINE_NAME)
            if status == 'success':
                event.update(execution_completed=True, cancel_requested=False)
            result['logs'].append(event)
    before = dict(documents=6, document_bson_sha256='a' * 64,
                  source_uuid_bson=BSON.encode({'uuid': Binary(b'\x01' * 16, 4)}))
    return result, [deepcopy(result)], before, deepcopy(before)


@pytest.mark.parametrize('corruption', [None, 'failed', 'skip', 'bool_count', 'log_order',
    'incomplete_execution', 'cancelled', 'wrong_name', 'missing_history', 'different_history',
    'different_bson', 'different_uuid', 'wrong_rows', 'missing_time', 'infinite_duration', 'event_delivery'])
def test_batch_demo_requires_real_ordered_success_history_and_exact_source_facts(recorder, corruption):
    result, history, before, after = _batch_facts(recorder)
    if corruption == 'failed': result['status'] = 'failed'
    elif corruption == 'skip': result.update(success=1, skipped=1)
    elif corruption == 'bool_count': result['failed'] = False
    elif corruption == 'log_order': result['logs'][2]['job_index'] = 0
    elif corruption == 'incomplete_execution': result['logs'][3]['execution_completed'] = False
    elif corruption == 'cancelled': result['logs'][3]['cancel_requested'] = True
    elif corruption == 'wrong_name': result['logs'][0]['name'] = 'not_the_generated_pipeline'
    elif corruption == 'missing_history': history.clear()
    elif corruption == 'different_history': history[0]['execution_id'] = 2
    elif corruption == 'different_bson': after['document_bson_sha256'] = 'b' * 64
    elif corruption == 'different_uuid': after['source_uuid_bson'] = b'changed-identity'
    elif corruption == 'wrong_rows': before['documents'] = after['documents'] = 5
    elif corruption == 'missing_time': result['start_time'] = ''
    elif corruption == 'infinite_duration': result['duration_ms'] = float('inf')
    elif corruption == 'event_delivery': result['event_delivery_failures'] = 1
    if corruption is None:
        receipt = recorder.verify_mongo_batch_values(result, history, before, after)
        assert receipt['tasks'] == receipt['success'] == 2
        assert receipt['history_persisted'] and receipt['ordered_duplicate_references_verified']
        assert receipt['source_exact_bson_unchanged'] and receipt['source_collection_identity_unchanged']
        assert 'source_uuid_bson' not in receipt and 'logs' not in receipt
    else:
        with pytest.raises(RuntimeError, match='Batch Job execution, persisted history or exact source BSON'):
            recorder.verify_mongo_batch_values(result, history, before, after)


def test_batch_demo_history_sqlite_requires_io_owner_and_returns_actual_persisted_record(recorder, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from opennavicat.batch_jobs import new_batch_job, to_nbatpremium
    from opennavicat.storage import Store
    store = Store(tmp_path)
    entry = store.save_batch_job('生成批处理作业', to_nbatpremium(new_batch_job()))
    proxy = recorder._batch_history_snapshot(store)
    record = _batch_facts(recorder)[0]
    record.pop('execution_id')
    with pytest.raises(RuntimeError, match='background owner'):
        recorder._batch_history_io(proxy, entry['id'], record)
    observed = []
    original = Store.record_batch_job_run
    def write_on_owner(*args):
        observed.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(Store, 'record_batch_job_run', write_on_owner)
    with ThreadPoolExecutor(max_workers=1) as owner:
        written = owner.submit(recorder._batch_history_io, proxy, entry['id'], record).result()
        history = owner.submit(recorder._batch_history_io, proxy, entry['id']).result()
    assert observed and observed[0] != threading.main_thread().ident
    assert written['execution_id'] == 1 and history == [written]
    assert 'execution_id' not in record


def test_batch_demo_history_failure_suppresses_local_private_exception_text(recorder, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from opennavicat.storage import Store
    store = Store(tmp_path)
    proxy = recorder._batch_history_snapshot(store)
    private = 'generated-sensitive-history-value'
    def unavailable(*args):
        raise OSError(str(tmp_path) + ' ' + private)
    monkeypatch.setattr(Store, 'batch_job_history', unavailable)
    with ThreadPoolExecutor(max_workers=1) as owner:
        with pytest.raises(OSError) as captured:
            owner.submit(recorder._batch_history_io, proxy, 'generated-id').result()
    text = ''.join(traceback.format_exception(captured.value))
    assert private not in text and str(tmp_path) not in text
    assert captured.value.__suppress_context__ is True


@pytest.mark.parametrize('surface', ['references', 'messages', 'history'])
def test_batch_page_privacy_checks_actual_task_and_log_surfaces(qtbot, recorder, tmp_path, surface):
    from opennavicat.batch_jobs import new_batch_job, new_pipeline_reference
    from opennavicat.ui.batch_jobs import BatchJobPage
    service = recorder.OwnedMongoDemo(); service.port = 54321
    page = BatchJobPage()
    qtbot.addWidget(page)
    definition = new_batch_job()
    definition.jobs = [new_pipeline_reference('MongoDB · 生成示例', 'demo_catalog', '生成聚合管道')]
    if surface == 'references':
        definition.jobs[0].server = service.password
    page.load_definition(definition)
    if surface == 'messages':
        page.append_log({'status': 'success', 'message': service.password})
        page.tabs.setCurrentWidget(page.messages)
    elif surface == 'history':
        page.load_history([{'execution_id': 1, 'logs': [{'status': 'success', 'message': service.password}]}])
        page.tabs.setCurrentWidget(page.history_page)
        page.history_table.selectRow(0)
    page.show(); qtbot.waitExposed(page)
    with pytest.raises(RuntimeError, match='endpoint or credential'):
        recorder.assert_visible_privacy(page, tmp_path, service)


def test_real_batch_demo_runs_two_native_sessions_persists_history_and_detects_bson_or_uuid_change(recorder, session, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from bson import Decimal128, Int64
    from opennavicat.batch_jobs import (
        new_batch_job, new_pipeline_reference, resolve_pipeline_reference, to_nbatpremium,
    )
    from opennavicat.batch_job_runtime import BatchRunControl, PreparedPipelineJob, run_batch_job
    from opennavicat.database import DatabaseSession
    from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, to_nagg
    from opennavicat.storage import Store
    monkeypatch.setattr(recorder, 'MONGO_DATABASE', session.config.database)
    config = replace(session.config, id=recorder.MONGO_CONNECTION_ID, name=recorder.MONGO_CONNECTION_NAME)
    store = Store(tmp_path)
    pipeline = PipelineDefinition(source=recorder.MONGO_COLLECTION, stages=[PipelineStage('$match', {})])
    store.save_mongo_pipeline(config.id, config.database, recorder.MONGO_BATCH_PIPELINE_NAME, to_nagg(pipeline))
    definition = new_batch_job()
    definition.jobs = [new_pipeline_reference(config.name, config.database, recorder.MONGO_BATCH_PIPELINE_NAME) for _ in range(2)]
    entry = store.save_batch_job(recorder.MONGO_BATCH_NAME, to_nbatpremium(definition))
    resolved = resolve_pipeline_reference(definition.jobs[0], [config], store.mongo_pipelines())
    prepared = [PreparedPipelineJob(reference, resolved, config) for reference in definition.jobs]
    service = recorder.OwnedMongoDemo()
    service.host, service.port, service.username, service.password = config.host, config.port, config.username, config.password
    # This distinct fixture's module already verifies exact ownership/cleanup;
    # its labels differ from a recorder-created service, so do not claim them.
    monkeypatch.setattr(service, 'inspect', lambda: None)
    documents = [{'_id': number, 'stock': Int64(10 + number), 'price': Decimal128('1.00')}
                 for number in range(6)]
    collection = session.connection[config.database][recorder.MONGO_COLLECTION]
    created, events = [], []
    def actual_session(configuration):
        value = DatabaseSession(configuration)
        created.append(value)
        return value
    with ThreadPoolExecutor(max_workers=1) as owner:
        owner.submit(collection.insert_many, deepcopy(documents)).result()
        before = owner.submit(service.batch_source_snapshot).result()
        outcome = owner.submit(run_batch_job, definition, prepared, control=BatchRunControl(),
                               emit=events.append, session_factory=actual_session).result()
        proxy = recorder._batch_history_snapshot(store)
        written = owner.submit(recorder._batch_history_io, proxy, entry['id'], outcome).result()
        history = owner.submit(recorder._batch_history_io, proxy, entry['id']).result()
        after = owner.submit(service.batch_source_snapshot).result()
        receipt = recorder.verify_mongo_batch_values(written, history, before, after)
        assert receipt['success'] == 2 and len(created) == 2 and created[0] is not created[1]
        assert all(value.connection is None for value in created)
        assert owner.submit(session.fetch_documents, config.database, recorder.MONGO_COLLECTION).result().documents
        owner.submit(collection.replace_one, {'_id': 0}, {**documents[0], 'stock': 10.0}).result()
        changed = owner.submit(service.batch_source_snapshot).result()
        assert before['document_bson_sha256'] != changed['document_bson_sha256']
        with pytest.raises(RuntimeError, match='not verified'):
            recorder.verify_mongo_batch_values(written, history, before, changed)
        owner.submit(collection.drop).result()
        owner.submit(collection.insert_many, deepcopy(documents)).result()
        recreated = owner.submit(service.batch_source_snapshot).result()
        assert recreated['document_bson_sha256'] == before['document_bson_sha256']
        assert recreated['source_uuid_bson'] != before['source_uuid_bson']
        with pytest.raises(RuntimeError, match='not verified'):
            recorder.verify_mongo_batch_values(written, history, before, recreated)


def _stream_facts(recorder):
    from bson import BSON, Binary, Timestamp
    from opennavicat.mongodb_pipeline_stream import FiniteStreamResult, StreamEvent
    from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps
    events = []
    for number, document in enumerate(recorder.mongo_stream_documents(), 1):
        value = {"_id": {"_data": "generated-token-" + str(number)}, "operationType": "insert",
                 "ns": {"db": recorder.MONGO_DATABASE, "coll": recorder.MONGO_STREAM_COLLECTION},
                 "documentKey": {"_id": document["_id"]}, "fullDocument": document,
                 "clusterTime": Timestamp(1700000000 + number, number)}
        events.append(StreamEvent(BSON.encode(value, codec_options=BSON_CODEC), canonical_dumps(value)))
    result = FiniteStreamResult(tuple(events), "empty_batch", .1)
    before = dict(documents=6, document_bson_sha256="a" * 64,
                  source_uuid_bson=BSON.encode({"uuid": Binary(b"\x01" * 16, 4)}),
                  stream_uuid_bson=BSON.encode({"uuid": Binary(b"\x02" * 16, 4)}))
    readback = dict(marker_absent=True, stream_uuid_bson=before["stream_uuid_bson"],
                    documents_raw=[BSON.encode(document, codec_options=BSON_CODEC) for document in recorder.mongo_stream_documents()])
    return result, dict(exact_getmore_observed=True, inserted=2), readback, before, deepcopy(before)


@pytest.mark.parametrize("corruption", [None, "missing_event", "partial", "wrong_long", "wrong_namespace",
    "wrong_canonical", "wrong_readback", "live_marker", "missed_getmore", "source_bson", "source_uuid", "stream_uuid"])
def test_stream_demo_requires_exact_private_bson_independent_readback_and_cleanup(recorder, corruption):
    from dataclasses import replace
    from bson import BSON
    from opennavicat.mongodb_pipeline_stream import StreamEvent
    from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps, canonical_loads
    result, production, readback, before, after = _stream_facts(recorder)
    if corruption == "missing_event": result = replace(result, events=result.events[:1])
    elif corruption == "partial": result = replace(result, completion="row_limit")
    elif corruption in {"wrong_long", "wrong_namespace", "wrong_canonical"}:
        decoded = canonical_loads(result.events[0].canonical_json)
        if corruption == "wrong_long": decoded["fullDocument"]["stock"] = float(decoded["fullDocument"]["stock"])
        elif corruption == "wrong_namespace": decoded["ns"]["coll"] = "other_generated_collection"
        else: decoded["extra"] = "not in raw BSON"
        raw = result.events[0].raw_bson if corruption == "wrong_canonical" else BSON.encode(decoded, codec_options=BSON_CODEC)
        result = replace(result, events=(StreamEvent(raw, canonical_dumps(decoded)), result.events[1]))
    elif corruption == "wrong_readback": readback["documents_raw"][0] = b"changed"
    elif corruption == "live_marker": readback["marker_absent"] = False
    elif corruption == "missed_getmore": production["exact_getmore_observed"] = False
    elif corruption == "source_bson": after["document_bson_sha256"] = "b" * 64
    elif corruption == "source_uuid": after["source_uuid_bson"] = b"changed identity"
    elif corruption == "stream_uuid": readback["stream_uuid_bson"] = b"changed identity"
    if corruption is None:
        verified = recorder.verify_mongo_stream_values(result, production, readback, before, after)
        assert verified["events"] == 2 and verified["raw_bson_and_canonical_verified"]
        assert verified["cursor_cleanup_verified"] and not verified["automatic_resume_used"]
        assert "marker" not in verified and "documents_raw" not in verified
    else:
        with pytest.raises(RuntimeError, match="exact BSON, readback or source identity"):
            recorder.verify_mongo_stream_values(result, production, readback, before, after)


@pytest.mark.parametrize("method", ["prepare_stream", "stream_readback", "produce_stream_events"])
def test_stream_demo_independent_io_refuses_gui_thread_before_docker_or_network(recorder, monkeypatch, method):
    service = recorder.OwnedMongoDemo(replica_set=True)
    monkeypatch.setattr(service, "inspect", lambda: pytest.fail("No Docker inspection belongs on the GUI thread"))
    arguments = () if method == "prepare_stream" else ({},) if method == "stream_readback" else (None, "generated", *[threading.Event() for _ in range(4)])
    with pytest.raises(RuntimeError, match="background owner"):
        getattr(service, method)(*arguments)


def test_stream_cli_requires_explicit_mongodb_before_dependency_or_fixture_access(recorder, monkeypatch):
    monkeypatch.setattr(recorder.sys, "argv", ["record-demo.py", "--include-stream", "--check-dependencies"])
    monkeypatch.setattr(recorder, "dependencies", lambda *_args: pytest.fail("Invalid flags must not access external tools"))
    with pytest.raises(SystemExit) as error:
        recorder.main()
    assert error.value.code == 2


@pytest.mark.parametrize("method", ["prepare_stream", "stream_readback", "produce_stream_events"])
def test_stream_native_failure_hides_generated_credentials_and_endpoint(recorder, monkeypatch, method):
    from concurrent.futures import ThreadPoolExecutor
    import pymongo
    service = recorder.OwnedMongoDemo(replica_set=True); service.port = 54321
    monkeypatch.setattr(service, "inspect", lambda: None)
    def unavailable(**_kwargs):
        raise OSError(" ".join(service.private_values()))
    monkeypatch.setattr(pymongo, "MongoClient", unavailable)
    arguments = () if method == "prepare_stream" else ({},) if method == "stream_readback" else (None, "generated", *[threading.Event() for _ in range(4)])
    with ThreadPoolExecutor(max_workers=1) as owner:
        with pytest.raises(RuntimeError) as error:
            owner.submit(getattr(service, method), *arguments).result()
    text = "".join(traceback.format_exception(error.value))
    assert not any(value in text for value in service.private_values())
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("output", [False, True])
def test_actual_stream_text_pages_are_checked_for_private_visible_values(qtbot, recorder, tmp_path, output):
    from opennavicat.ui.mongo_pipeline import MongoPipelinePage
    from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage
    page = MongoPipelinePage("generated", recorder.MONGO_DATABASE, name="Generated")
    qtbot.addWidget(page)
    page.load_definition(PipelineDefinition(source=recorder.MONGO_STREAM_COLLECTION,
                                           stages=[PipelineStage("$changeStream", {})]))
    result, *_facts = _stream_facts(recorder)
    page.show_stream_execution(result, 'db.getCollection("generated_stream_events").aggregate([{$changeStream:{}}]);')
    if output: page.tabs.setCurrentWidget(page.stream_output)
    page.show(); qtbot.waitExposed(page)
    recorder.verify_mongo_stream_page(page, output=output)
    service = recorder.OwnedMongoDemo(replica_set=True); service.port = 54321
    target = page.stream_output if output else page.messages
    target.appendPlainText(service.replica_key)
    with pytest.raises(RuntimeError, match="endpoint or credential"):
        recorder.assert_visible_privacy(page, tmp_path, service)


@pytest.mark.skipif(os.environ.get("OPENNAVICAT_TEST_MONGODB_RECORDER_STREAM") != "1",
                    reason="Recorder stream qualification creates its own authenticated pinned replica set")
def test_real_recorder_replica_stream_uses_toolbar_run_preserves_typed_events_and_original_session(
        qtbot, recorder, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict
    from pymongo.monitoring import CommandListener
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure
    from PySide6.QtWidgets import QMessageBox
    from opennavicat.mongodb_session import MongoSession
    from opennavicat.storage import Store
    from opennavicat.ui.window import MainWindow
    from shiboken6 import isValid

    service = recorder.OwnedMongoDemo(replica_set=True)
    observed_threads, errors = [], []
    class Wire(CommandListener):
        def started(self, event): observed_threads.append(threading.get_ident())
        def succeeded(self, event): pass
        def failed(self, event): pass
    original_options = MongoSession._connection_options
    monkeypatch.setattr(MongoSession, "_connection_options", lambda self: {**original_options(self), "event_listeners": [Wire()]})
    monkeypatch.setattr(MainWindow, "error", lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(MainWindow, "start_navicat_discovery", lambda *_args: pytest.fail("No existing profiles may be discovered"))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
    class MemoryStore(Store):
        def secret(self, config, field="password"):
            return service.password if field == "password" else getattr(config, field)
    armed, active, release, abort = [threading.Event() for _ in range(4)]
    window = page = None
    producer = None
    auth_rejected = False
    source_before = {name: hashlib.sha256((recorder.ROOT / name).read_bytes()).hexdigest() for name in (
        "scripts/record-demo.py", "tests/test_record_demo.py", "opennavicat/mongodb_pipeline_stream.py",
        "opennavicat/mongodb_session.py", "opennavicat/ui/window.py", "opennavicat/ui/mongo_pipeline.py")}
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="recorder-stream-owned") as owner:
        try:
            config = owner.submit(service.start).result(timeout=90)
            def verify_auth_required():
                with MongoClient(service.host, service.port, directConnection=True,
                                 serverSelectionTimeoutMS=2000, socketTimeoutMS=2000) as anonymous:
                    with pytest.raises(OperationFailure) as rejected:
                        anonymous[config.database][recorder.MONGO_COLLECTION].find_one()
                    assert rejected.value.code == 13
            owner.submit(verify_auth_required).result(timeout=10)
            auth_rejected = True
            before = owner.submit(service.prepare_stream).result(timeout=15)
            store = MemoryStore(tmp_path / "generated-workspace")
            store.state["connections"] = [{**asdict(config), "password": ""}]
            store.state["navicat_auto_discover"] = False
            window = MainWindow(store); window.show()
            window.connect_id(config.id)
            qtbot.waitUntil(lambda: config.id in window.sessions or bool(errors), timeout=15000)
            qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=15000)
            assert not errors
            session = window.sessions[config.id]
            page = window.open_mongo_pipeline(config.id, config.database)
            qtbot.waitUntil(lambda: recorder.MONGO_STREAM_COLLECTION in page._sources and not window.tasks, timeout=15000)
            page.source_combo.setCurrentText(recorder.MONGO_STREAM_COLLECTION)
            page.operator_combo.setCurrentText("$changeStream"); page.stage_editor.setPlainText("{}")
            comment = "generated recorder qualification " + uuid.uuid4().hex
            page.options_editor.comment_edit.setText(comment)
            assert page.draft().stages[0].body == {}  # No automatic resume option.
            original_icon = window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey()
            producer = owner.submit(service.produce_stream_events, session, comment, armed, active, release, abort)
            qtbot.waitUntil(armed.is_set, timeout=10000)
            page.run_action.trigger()
            qtbot.waitUntil(active.is_set, timeout=10000)
            recorder.verify_mongo_stream_page(page, busy=True)
            assert window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey() != original_icon
            recorder.assert_visible_privacy(window, tmp_path, service)
            qtbot.wait(250); release.set()
            qtbot.waitUntil(lambda: not page.running and page._last_stream_result is not None
                           and not window.tasks and producer.done(), timeout=15000)
            production = producer.result()
            readback, after = owner.submit(service.stream_readback, production["marker"]).result(timeout=15)
            verified = recorder.verify_mongo_stream_values(page._last_stream_result, production, readback, before, after)
            recorder.verify_mongo_stream_page(page)
            page.tabs.setCurrentWidget(page.stream_output); recorder.verify_mongo_stream_page(page, output=True)
            recorder.assert_visible_privacy(window, tmp_path, service)
            assert window.sessions[config.id] is session
            ordinary = owner.submit(session.fetch_documents, config.database, recorder.MONGO_COLLECTION, limit=7).result(timeout=10)
            assert len(ordinary.documents) == 6 and not ordinary.has_more and not errors
            assert observed_threads and threading.main_thread().ident not in observed_threads
            assert window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey() == original_icon
            verified["main_window_session_preserved_and_readable"] = True
        finally:
            abort.set(); release.set()
            try:
                if page is not None and isValid(page) and page.running:
                    window.stop_mongo_pipeline(page)
                if producer is not None:
                    qtbot.waitUntil(producer.done, timeout=20000)
                if window is not None and isValid(window):
                    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=15000)
                    if page is not None and isValid(page):
                        index = window.tabs.indexOf(page)
                        if index >= 0: window.tabs.removeTab(index)
                        page.deleteLater()
                    window.close(); qtbot.waitUntil(lambda: window._closed, timeout=15000)
                    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount(), timeout=15000)
                    window.deleteLater()
            finally:
                owner.submit(service.cleanup).result(timeout=30)
                directory = Path(os.environ.get("OPENNAVICAT_MONGODB_RECORDER_STREAM_REPORT_DIR",
                                                recorder.ROOT / "artifacts/recorder-stream-owned"))
                directory.mkdir(parents=True, exist_ok=True)
                after_hashes = {name: hashlib.sha256((recorder.ROOT / name).read_bytes()).hexdigest() for name in source_before}
                (directory / "fixture.json").write_text(json.dumps({**service.receipt(), "container_id": service.container_id,
                    "volumes": service.volumes, "source_sha256": source_before, "source_unchanged": after_hashes == source_before,
                    "anonymous_access_rejected": auth_rejected, "stream": locals().get("verified"),
                    "all_database_io_off_gui_thread": bool(observed_threads)
                        and threading.main_thread().ident not in observed_threads}, indent=2) + "\n")
