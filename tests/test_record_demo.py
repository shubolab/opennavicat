"""Actual Qt privacy surfaces and recorder-owned timeout/error contracts."""
from __future__ import annotations

import importlib.util
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import threading
import time
import traceback
import uuid
from copy import deepcopy
from contextlib import closing

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


def test_sqlite_test_opt_in_adds_one_phase_before_forms_without_replacing_old_35(recorder):
    previous = recorder.required_demo_phases(mongodb=True, stream=True,
        oracle_form=True, connection_chooser=True)
    extended = recorder.required_demo_phases(mongodb=True, stream=True,
        oracle_form=True, connection_chooser=True, sqlite_test=True)
    assert len(previous) == 35 and len(extended) == len(set(extended)) == 36
    assert extended[:31] == previous[:31]
    assert extended[31] == 'sqlite_connection_test' and extended[32:] == previous[31:]
    assert recorder.required_demo_phases(sqlite_test=True) == recorder.SQLITE_PHASES + recorder.SQLITE_TEST_PHASES


def test_sqlite_test_capture_requires_explicit_output(recorder, monkeypatch):
    monkeypatch.setattr(recorder.sys, 'argv', ['record-demo.py', '--include-sqlite-test'])
    monkeypatch.setattr(recorder, 'dependencies', lambda *_: ('unused-encoder', 'unused-version'))
    monkeypatch.setattr(recorder, 'record', lambda *_: pytest.fail('No default README overwrite for extended capture'))
    with pytest.raises(SystemExit) as caught:
        recorder.main()
    assert caught.value.code == 2


@pytest.mark.parametrize('failure', ['', 'capture', 'encode'])
def test_sqlite_owned_cwd_main_restores_after_draining_jobs_and_keeps_output_absolute(recorder, failure):
    # Exercise main's real directory/executor lifetime in a separate process.
    # The capture and encoder are deliberate stubs: this tests neither a GIF
    # nor a simulated connection-success screen.
    source = '''
import importlib.util,json,os,shutil,sys,tempfile,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
path=Path(sys.argv[1]);failure=sys.argv[2]
spec=importlib.util.spec_from_file_location('record_demo_cwd_child',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
seen={}
class Pool(ThreadPoolExecutor):
 def submit(self,*args,**kwargs):
  if 'first_submit_owned' not in seen:
   seen['first_submit_owned']=Path.cwd()==seen['temporary']
  return super().submit(*args,**kwargs)
def fake_record(args,temporary,pool,*unused):
 seen['temporary']=temporary
 seen['absolute_output']=args.output.is_absolute() and args.output==expected
 seen['relative_path_tool_preserved']=Path(shutil.which(tool_name))==tool
 def pending():
  time.sleep(.04)
  seen['pending_owned']=Path.cwd()==temporary and temporary.is_dir()
  Path('owned-pending.txt').write_text('generated pending job')
 pool.submit(pending)
 if failure=='capture':raise RuntimeError('controlled capture failure')
 return temporary,{'frames':2,'sqlite_connection_test':{}}
def fake_encode(_encoder,_frames,_fps,destination):
 if failure=='encode':raise RuntimeError('controlled encode failure')
 destination.write_bytes(b'GIF-test')
 return {'fixture':'not an actual GIF qualification'}
module.ThreadPoolExecutor=Pool
module.dependencies=lambda *_:('generated-encoder','generated-version')
module.record=fake_record;module.encode=fake_encode;module.verify_gif=lambda *_:None
with tempfile.TemporaryDirectory(prefix='opennavicat-main-lifecycle-test-') as directory:
 original=Path.cwd();owned=Path(directory).resolve()
 try:
  os.chdir(owned);(owned/'keep.txt').write_text('unchanged')
  tools=owned/'owned-tools';tools.mkdir();tool_name='generated-tool.exe' if os.name=='nt' else 'generated-tool';tool=tools/tool_name
  tool.write_text('generated lookup-only fixture');tool.chmod(0o755)
  prior_path=os.environ.get('PATH','');os.environ['PATH']='owned-tools'+os.pathsep+prior_path
  before_path=os.environ['PATH']
  expected=owned/'output'/'generated.gif'
  sys.argv=['record-demo.py','--include-sqlite-test','--output','output/generated.gif','--platform','offscreen']
  try:module.main()
  except RuntimeError as error:
   assert failure and str(error)=='controlled '+failure+' failure',str(error)
  else:assert not failure
  assert Path.cwd()==owned and seen['first_submit_owned'] and seen['absolute_output']
  assert seen['relative_path_tool_preserved'] and os.environ['PATH']==before_path
  assert seen['pending_owned'] and not seen['temporary'].exists()
  assert not (owned/'owned-pending.txt').exists() and (owned/'keep.txt').read_text()=='unchanged'
  if not failure:
   assert expected.read_bytes()==b'GIF-test'
   receipt=json.loads(expected.with_suffix('.json').read_text())
   assert receipt['sqlite_connection_test']['working_directory_restored']
   assert receipt['sqlite_connection_test']['temporary_fixture_removed']
  else:assert not expected.exists()
 finally:os.chdir(original)
print(json.dumps({'passed':True,'failure':failure,'parent_cwd_unchanged':True}))
'''
    env = os.environ.copy(); env['QT_QPA_PLATFORM'] = 'offscreen'
    original = Path.cwd()
    result = subprocess.run([recorder.sys.executable, '-c', source, recorder.__file__, failure],
        env=env, capture_output=True, text=True, timeout=20)
    assert Path.cwd() == original
    assert result.returncode == 0, (result.stdout + result.stderr)[-12000:]
    assert json.loads(result.stdout.splitlines()[-1])['passed']


@pytest.mark.parametrize('include_sqlite_test', [False, True], ids=['default-six', 'sqlite-test-seven'])
def test_real_no_mongo_record_returns_truthful_receipt_after_all_actual_phases(recorder, include_sqlite_test):
    # This calls record(), its native Qt event loop, async SQL, real Test and
    # frame privacy guards. Only long presentation holds are capped; no probe,
    # result signal, widget, store, phase or captured frame is fabricated.
    source = '''
import hashlib,importlib.util,json,os,sys,tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QTimer
path=Path(sys.argv[1]);include=sys.argv[2]=='1'
sys.path.insert(0,str(path.parents[1]))
import opennavicat.ui.dialogs as actual_dialogs
assert Path(actual_dialogs.__file__).resolve().is_relative_to(path.parents[1])
spec=importlib.util.spec_from_file_location('actual_record_no_mongo_child',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def forbidden(*unused,**kwargs):raise AssertionError('No FFmpeg or Docker in actual record() regression')
module.dependencies=forbidden;module.encode=forbidden;module.verify_gif=forbidden;module.OwnedMongoDemo=forbidden
original_single_shot=QTimer.singleShot
def bounded_presentation_hold(*args):
 if args and type(args[0]) is int:args=(min(args[0],250),*args[1:])
 return original_single_shot(*args)
QTimer.singleShot=staticmethod(bounded_presentation_hold)
args=SimpleNamespace(width=950,height=620,fps=10,theme='dark',include_stream=False,
 include_oracle_form=False,include_connection_chooser=False,include_sqlite_test=include)
original=Path.cwd()
with tempfile.TemporaryDirectory(prefix='opennavicat-real-record-unit-') as directory:
 temporary=Path(directory).resolve()
 with ThreadPoolExecutor(max_workers=1,thread_name_prefix='actual-record-unit-io') as pool:
  with module.owned_recording_cwd(temporary,enabled=include):
   try:frames,receipt=module.record(args,temporary,pool)
   finally:pool.submit(lambda:None).result(timeout=10)
   expected=module.required_demo_phases(sqlite_test=include)
   assert tuple(row['name'] for row in receipt['phases'])==expected
   assert len(expected)==(7 if include else 6)
   assert all(row['frames']>0 and row['privacy_checked_frames']==row['frames'] for row in receipt['phases'])
   files=sorted(frames.glob('frame-*.png'))
   assert len(files)==receipt['frames'] and all(p.stat().st_size>0 for p in files)
   phase_hashes={hashlib.sha256(files[row['first_frame']].read_bytes()).hexdigest() for row in receipt['phases']}
   assert len(phase_hashes)==len(expected)
   for name in ('mongodb_included','mongodb_gridfs_image_previewed','mongodb_javascript_read_completed',
    'mongodb_inline_key_editor_opened','mongodb_collection_collation_save','mongodb_collection_storage_reopened_readonly',
    'mongodb_collection_options_reopened_readonly','mongodb_collection_options_verified','mongodb_aggregation_pipeline_stage_template'):
    assert receipt[name] is False,(name,receipt[name])
   for name in ('mongodb_collection_storage_save','mongodb_aggregation_pipeline_options','mongodb_aggregation_pipeline_preview',
    'mongodb_aggregation_pipeline_explain','mongodb_aggregation_pipeline_results','mongodb_batch_job','mongodb_finite_stream'):
    assert receipt[name] is None,(name,receipt[name])
   assert receipt['mongodb_products']==0 and receipt['mongodb_bson_types_checked']==[]
   assert receipt['query_result']=={'rows':6,'columns':4}
   assert receipt['sqlite_closed_in_worker'] and receipt['native_window_closed_before_quit']
   assert receipt['background_io_completed_before_quit'] and receipt['qt_platform']=='offscreen'
   if include:
    result=receipt['sqlite_connection_test']
    assert result['actual_probe_completed'] and result['test_session_closed_in_worker'] and result['fixture_unchanged']
    assert result['success_diagram_line'] and result['form_closed_after_worker_idle']
    assert result['relative_file_used_by_real_test'] and not result['profile_saved']
   else:assert receipt['sqlite_connection_test'] is None
   text=json.dumps(receipt,ensure_ascii=False)
   assert not any(value in text for value in (str(module.ROOT),str(temporary),str(Path.home())))
assert Path.cwd()==original and not temporary.exists()
print(json.dumps({'passed':True,'phases':len(expected),'frames':receipt['frames'],'actual_qpa':receipt['qt_platform']}))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('OPENNAVICAT_TEST_')}
    env['QT_QPA_PLATFORM'] = 'offscreen'
    original = Path.cwd()
    result = subprocess.run([recorder.sys.executable, '-W', 'error', '-c', source, recorder.__file__, '1' if include_sqlite_test else '0'],
        env=env, capture_output=True, text=True, timeout=40)
    assert Path.cwd() == original
    assert result.returncode == 0, (result.stdout + result.stderr)[-16000:]
    facts = json.loads(result.stdout.splitlines()[-1])
    assert facts['passed'] and facts['phases'] == (7 if include_sqlite_test else 6)


def _complete_generated_sqlite_test(qtbot, recorder, dialog, fixture, pool):
    from PySide6.QtCore import Qt
    dialog.show()
    qtbot.waitExposed(dialog)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as completed:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: recorder.sqlite_test_idle(dialog))
    assert completed.args == [True, '连接成功']
    dialog._demo_sqlite_test_ledger['fixture_verified'] = pool.submit(recorder.verify_sqlite_test_fixture, fixture).result(timeout=5)
    return recorder.verify_sqlite_connection_test_demo(dialog, fixture)


def test_sqlite_demo_uses_real_owned_file_worker_version_close_and_no_profile_or_keyring(qtbot, recorder, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import ModuleType
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    from opennavicat.database import DatabaseSession
    from opennavicat.storage import Store

    def forbidden(*_args, **_kwargs):
        pytest.fail('The generated connection Test cannot use a workspace or credential vault')

    monkeypatch.setattr(Store, '__init__', forbidden)
    denied = ModuleType('keyring')
    for name in ('get_password', 'set_password', 'delete_password', 'get_keyring'):
        setattr(denied, name, forbidden)
    monkeypatch.setitem(recorder.sys.modules, 'keyring', denied)
    closed = []
    original_close = DatabaseSession.close

    def close(session):
        original_close(session)
        closed.append((threading.get_ident(), session.connection is None))

    monkeypatch.setattr(DatabaseSession, 'close', close)
    before = Path.cwd()
    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        with recorder.sqlite_connection_test_demo(fixture) as dialog:
            facts = _complete_generated_sqlite_test(qtbot, recorder, dialog, fixture, pool)
            recorder.assert_visible_privacy(dialog, tmp_path)
            assert dialog.database_edit.text() == recorder.SQLITE_TEST_FILENAME
            assert facts['server_version'] == sqlite3.sqlite_version
            assert facts['fixture_unchanged'] and facts['rows'] == 2
            assert not facts['profile_saved'] and facts['new_unsaved_profile']
            assert facts['actual_probe_completed'] and facts['test_session_closed_in_worker']
            assert closed == [(dialog._demo_sqlite_test_ledger['probe_threads'][0], True)]
            assert closed[0][0] != threading.get_ident()
            assert pool.submit(lambda: hashlib.sha256(fixture['path'].read_bytes()).hexdigest()).result(timeout=5) == fixture['sha256']
            assert not dialog.grab().isNull()
        ledger = dialog._demo_sqlite_test_ledger
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dialog) and ledger['closed']
    assert Path.cwd() == before and not (before / recorder.SQLITE_TEST_FILENAME).exists()


@pytest.mark.parametrize('tamper', ['path', 'password', 'hidden_tls_password', 'status', 'profile_save'])
def test_sqlite_demo_rejects_changed_real_privacy_or_result_state(qtbot, recorder, tmp_path, tamper):
    from concurrent.futures import ThreadPoolExecutor

    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        with recorder.sqlite_connection_test_demo(fixture) as dialog:
            _complete_generated_sqlite_test(qtbot, recorder, dialog, fixture, pool)
            if tamper == 'path': dialog.database_edit.setText(str(fixture['path']))
            elif tamper == 'password': dialog.password_edit.setText('generated-private-password')
            elif tamper == 'hidden_tls_password': dialog.ssl_key_password_edit.setText('generated-private-key')
            elif tamper == 'status': dialog.sqlite_test_banner.detail_label.setText('not the actual tested server')
            else: dialog.accept()
            with pytest.raises(RuntimeError, match='actual completed, unsaved fixture state'):
                recorder.verify_sqlite_connection_test_demo(dialog, fixture)
            if tamper == 'path':
                with pytest.raises(RuntimeError, match='filesystem path'):
                    recorder.assert_visible_privacy(dialog, tmp_path)


def test_sqlite_demo_error_waits_real_active_test_before_deleting_its_signals(qtbot, recorder, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from shiboken6 import isValid
    from opennavicat.database import DatabaseSession

    entered, release = threading.Event(), threading.Event()
    original_execute = DatabaseSession.execute

    def gated_execute(session, sql, *args, **kwargs):
        if sql == 'SELECT 1':
            entered.set()
            assert release.wait(5)
        return original_execute(session, sql, *args, **kwargs)

    monkeypatch.setattr(DatabaseSession, 'execute', gated_execute)
    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        try:
            with pytest.raises(RuntimeError, match='controlled capture failure'):
                with recorder.sqlite_connection_test_demo(fixture) as dialog:
                    dialog.show(); qtbot.waitExposed(dialog)
                    dialog.test_button.click()
                    qtbot.waitUntil(entered.is_set)
                    signal_object = dialog._test_task.signals
                    assert dialog._testing and not recorder.sqlite_test_idle(dialog)
                    QTimer.singleShot(25, release.set)
                    raise RuntimeError('controlled capture failure')
        finally:
            release.set()
        ledger = dialog._demo_sqlite_test_ledger
        assert ledger['results'] == [(True, '连接成功')] and ledger['close_threads'] == ledger['probe_threads']
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dialog) and not isValid(signal_object) and ledger['closed']


def test_sqlite_fixture_and_widget_verifiers_reject_wrong_owners(qtbot, recorder, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with pytest.raises(RuntimeError, match='I/O owner'):
        recorder.prepare_sqlite_test_fixture(tmp_path)
    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        with recorder.sqlite_connection_test_demo(fixture) as dialog:
            with pytest.raises(RuntimeError, match='GUI owner'):
                pool.submit(recorder.verify_sqlite_connection_test_demo, dialog, fixture).result(timeout=5)
        def mutate_owned_fixture():
            with closing(sqlite3.connect(fixture['path'])) as database, database:
                database.execute("UPDATE generated_connection SET label=? WHERE id=?", ('generated changed row', 1))
        pool.submit(mutate_owned_fixture).result(timeout=5)
        with pytest.raises(RuntimeError, match='changed its owned fixture'):
            pool.submit(recorder.verify_sqlite_test_fixture, fixture).result(timeout=5)


def test_connection_chooser_uses_actual_unbound_grid_and_deletes_after_capture(qtbot, recorder, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent, QSize
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QListView
    from shiboken6 import isValid
    from types import ModuleType
    from opennavicat import database
    from opennavicat.storage import Store
    from opennavicat.ui.connection_manager import ConnectionManagerDialog, DRIVER_LABELS

    def forbidden(*_args, **_kwargs):
        pytest.fail("The isolated chooser cannot read a workspace, credentials or create a session")
    monkeypatch.setattr(Store, '__init__', forbidden)
    monkeypatch.setattr(Store, 'secret', forbidden)
    monkeypatch.setattr(database, 'DatabaseSession', forbidden)
    monkeypatch.setattr(recorder.sqlite3, 'connect', forbidden)
    denied_keyring = ModuleType('keyring')
    for name in ('get_password', 'set_password', 'delete_password', 'get_keyring'):
        setattr(denied_keyring, name, forbidden)
    monkeypatch.setitem(recorder.sys.modules, 'keyring', denied_keyring)
    with recorder.connection_chooser_demo() as dialog:
        assert type(dialog) is ConnectionManagerDialog
        emitted = QSignalSpy(dialog.selectionRequested)
        dialog.show()
        qtbot.waitExposed(dialog)
        facts = recorder.verify_connection_chooser_demo(dialog)
        assert set(DRIVER_LABELS.values()) <= set(facts['labels'])
        assert facts['direct_aliases'] == [label for label in facts['labels']
                                          if label in {'MongoDB Atlas', 'Redis Enterprise Cloud'}]
        assert dialog.connection_list.viewMode() == QListView.ViewMode.IconMode
        assert dialog.connection_list.iconSize() == QSize(50, 50)
        assert facts['search_empty'] and facts['selection_empty'] and facts['artwork_present']
        assert not facts['next_enabled'] and not facts['settings_handoff_attempted']
        assert not facts['profile_saved'] and not facts['database_connection_attempted']
        assert emitted.count() == 0
        recorder.assert_visible_privacy(dialog, tmp_path)
        source = dialog.grab().toImage()
        fitted = recorder.fit_capture_image(source, 1440, 900, save_sheet=True)
        assert not source.isNull() and fitted.format() == QImage.Format.Format_RGB32
        assert (fitted.width(), fitted.height()) == (1440, 900)
    assert not dialog.isVisible() and emitted.count() == 0
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(dialog)


@pytest.mark.parametrize('change', ['search', 'selection', 'list', 'missing_artwork', 'vendor', 'uri'])
def test_connection_chooser_refuses_changed_real_widget_state(qtbot, recorder, change):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon

    with recorder.connection_chooser_demo() as dialog:
        dialog.show()
        qtbot.waitExposed(dialog)
        if change == 'search': dialog.search_edit.setText('SQLite')
        elif change == 'selection': dialog.connection_list.setCurrentRow(0)
        elif change == 'list': qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
        elif change == 'missing_artwork': dialog.connection_list.item(0).setIcon(QIcon())
        elif change == 'vendor': dialog.filter_tree.setCurrentItem(dialog.vendor_items['AWS'])
        else: qtbot.mouseClick(dialog.uri_button, Qt.MouseButton.LeftButton)
        with pytest.raises(RuntimeError, match='catalog, artwork, empty selection or no-handoff'):
            recorder.verify_connection_chooser_demo(dialog)


def test_connection_chooser_detects_actual_handoff_even_after_selection_is_cleared(qtbot, recorder):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy

    with recorder.connection_chooser_demo() as dialog:
        dialog.show()
        qtbot.waitExposed(dialog)
        emitted = QSignalSpy(dialog.selectionRequested)
        dialog.connection_list.setCurrentRow(0)
        assert dialog.next_button.isEnabled()
        qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
        assert emitted.count() == 1
        dialog.connection_list.setCurrentRow(-1)
        with pytest.raises(RuntimeError, match='no-handoff'):
            recorder.verify_connection_chooser_demo(dialog)


@pytest.mark.parametrize('surface', ['title', 'card', 'sidebar'])
def test_connection_chooser_actual_visible_text_is_privacy_checked(qtbot, recorder, tmp_path, surface):
    with recorder.connection_chooser_demo() as dialog:
        dialog.show()
        qtbot.waitExposed(dialog)
        if surface == 'title': dialog.setWindowTitle('private endpoint 198.51.100.41')
        elif surface == 'card': dialog.connection_list.item(0).setText('198.51.100.41')
        else: dialog.driver_items['mysql'].setText(0, str(tmp_path / 'private-path'))
        with pytest.raises(RuntimeError, match='endpoint|filesystem path'):
            recorder.assert_visible_privacy(dialog, tmp_path)


def test_connection_chooser_is_deleted_when_capture_body_fails(qtbot, recorder):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    with pytest.raises(RuntimeError, match='controlled capture failure'):
        with recorder.connection_chooser_demo() as dialog:
            dialog.show()
            qtbot.waitExposed(dialog)
            raise RuntimeError('controlled capture failure')
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(dialog)


def test_connection_chooser_verification_refuses_non_gui_owner(qtbot, recorder):
    from concurrent.futures import ThreadPoolExecutor

    with recorder.connection_chooser_demo() as dialog, ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match='GUI owner'):
            pool.submit(recorder.verify_connection_chooser_demo, dialog).result(timeout=3)


def test_chooser_opt_in_adds_one_phase_and_preserves_all_previous_modes(recorder):
    assert len(recorder.required_demo_phases()) == 6
    assert len(recorder.required_demo_phases(mongodb=True, stream=True)) == 31
    previous = recorder.required_demo_phases(mongodb=True, stream=True, oracle_form=True)
    assert len(previous) == 34
    assert recorder.required_demo_phases(mongodb=True, stream=True, oracle_form=True,
        connection_chooser=True) == previous + ('connection_chooser',)
    assert recorder.required_demo_phases(connection_chooser=True) == recorder.SQLITE_PHASES + ('connection_chooser',)


def test_chooser_extended_capture_requires_explicit_output_before_recording(recorder, monkeypatch):
    monkeypatch.setattr(recorder.sys, 'argv', ['record-demo.py', '--include-connection-chooser'])
    monkeypatch.setattr(recorder, 'dependencies', lambda *_args: ('unused-encoder', 'unused-version'))
    monkeypatch.setattr(recorder, 'record', lambda *_args: pytest.fail('No capture without explicit output'))
    with pytest.raises(SystemExit) as caught:
        recorder.main()
    assert caught.value.code == 2


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


@pytest.mark.parametrize('few_colors', [False, True], ids=['full-palette', 'padded-palette'])
def test_palette_reserves_distinct_driver_colors_and_keeps_transparency_in_worker(qapp, recorder, tmp_path, few_colors):
    from concurrent.futures import ThreadPoolExecutor
    from PySide6.QtGui import QColor, QImage

    path = tmp_path / 'palette.png'
    palette = QImage(16, 16, QImage.Format.Format_RGBA8888)
    for index in range(256):
        palette.setPixelColor(index % 16, index // 16, QColor('black') if few_colors else QColor(index, index, index))
    palette.setPixelColor(1, 0, QColor('white'))
    palette.setPixelColor(2, 0, QColor('#7bb75e'))
    for index, kind in ((20, 'postgresql'), (21, 'oracle'), (22, 'redis')):
        palette.setPixelColor(index % 16, index // 16, QColor(recorder.DEMO_DRIVER_COLORS[kind]))
    palette.setPixelColor(15, 15, QColor(0, 255, 0, 0))
    assert palette.save(str(path))
    before = [palette.pixelColor(index % 16, index // 16) for index in range(256)]
    gui_thread = threading.get_ident()
    def reserve_in_worker():
        assert threading.get_ident() != gui_thread
        return recorder.reserve_demo_driver_colors(path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        facts = pool.submit(reserve_in_worker).result(timeout=5)
    saved = QImage(str(path))
    after = [saved.pixelColor(index % 16, index // 16) for index in range(256)]
    assert saved.size() == palette.size()
    assert after[255] == before[255] and after[255].alpha() == 0
    assert sum(color.alpha() == 0 for color in after) == 1
    assert after[0] == before[0] and after[1] == before[1]
    if few_colors:
        assert after[2] == before[2]  # The sole source green is not sacrificed for a reserved brand.
    assert [after[index] for index in (20, 21, 22)] == [before[index] for index in (20, 21, 22)]
    targets = [QColor(value) for value in recorder.DEMO_DRIVER_COLORS.values()]
    assert len(set(color.rgba() for color in targets)) == 9
    assert all(color in after for color in targets)
    changed = [index for index in range(256) if after[index] != before[index]]
    assert len(changed) == 6 and all(after[index] in targets for index in changed)
    assert facts['reserved_color_count'] == 9 and facts['transparency_slot_preserved']


@pytest.mark.parametrize('invalid', ['unreadable', 'geometry', 'opaque', 'two-transparent', 'partial-alpha'])
def test_invalid_palette_is_rejected_before_its_file_is_changed(recorder, tmp_path, invalid):
    from PySide6.QtGui import QColor, QImage

    path = tmp_path / 'invalid-palette.png'
    if invalid == 'unreadable':
        path.write_bytes(b'not a PNG')
    else:
        palette = QImage(15 if invalid == 'geometry' else 16, 16, QImage.Format.Format_RGBA8888)
        palette.fill(QColor('#808080'))
        if invalid != 'opaque':
            palette.setPixelColor(0, 0, QColor(0, 255, 0, 0))
        if invalid == 'two-transparent':
            palette.setPixelColor(1, 0, QColor(0, 255, 0, 0))
        if invalid == 'partial-alpha':
            palette.setPixelColor(1, 0, QColor(20, 30, 40, 128))
        assert palette.save(str(path))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='palette'):
        recorder.reserve_demo_driver_colors(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('changing_panel', [False, True], ids=['late-static-icons', 'unchanged-icons'])
def test_late_static_driver_icons_keep_color_text_and_frame_order_after_encoding(qapp, recorder, tmp_path, changing_panel):
    import shutil
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter
    from opennavicat.ui.icons import icon

    if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
        pytest.skip('Actual GIF color verification requires ffmpeg and ffprobe')
    width, height = 480, 320
    busy = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(width):
            busy.setPixelColor(x, y, QColor(x * 255 // (width - 1),
                y * 255 // (height - 1), (x * 13 + y * 7) % 256))
    static = QImage(width, height, QImage.Format.Format_RGB32)
    static.fill(QColor('white'))
    targets = {
        'mysql': '41d549', 'postgresql': '4083ff', 'sqlserver': 'ffaa01',
        'oracle': 'ff0033', 'sqlite': '61dbd2', 'mariadb': 'c3986d',
        'mongodb': 'ba5101', 'redis': 'fe7d7b', 'snowflake': '29b5e8',
    }
    painter = QPainter(static)
    font = QFont(qapp.font()); font.setPixelSize(14)
    painter.setFont(font)
    painter.setPen(QColor('black'))
    for number, kind in enumerate(targets):
        x, y = 24 + (number % 3) * 156, 30 + (number // 3) * 96
        icon(kind, 16).paint(painter, QRect(x, y, 16, 16),
            Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal, QIcon.State.Off)
        painter.drawText(x, y + 36, kind)
    painter.end()
    rgb = static.convertToFormat(QImage.Format.Format_RGB888)
    row_bytes = rgb.bytesPerLine()
    original = bytes(rgb.constBits())
    expected = {kind: bytes.fromhex(value) for kind, value in targets.items()}
    color_kinds = {color: kind for kind, color in expected.items()}
    masks = {kind: [] for kind in targets}
    text_pixels = []
    for y in range(height):
        for x in range(width):
            at = y * row_bytes + x * 3
            value = original[at:at + 3]
            kind = color_kinds.get(value)
            if kind is not None:
                masks[kind].append((y * width + x) * 3)
            if max(value) < 40:
                text_pixels.append((y * width + x) * 3)
    assert all(len(indices) > 80 for indices in masks.values())
    assert len(text_pixels) > 100
    frames = tmp_path / 'frames'; frames.mkdir()
    # A varied earlier scene must not consume the colors of small icons held
    # later. Returning to each scene also catches a palette/frame mismatch.
    panel_colors = ['#891122', '#1111ff', '#8a22cd', '#44bbcc', '#1111ff', '#ffffff']
    if changing_panel:
        sequence = []
        for color in panel_colors:
            frame = static.copy()
            painter = QPainter(frame)
            painter.fillRect(QRect(80, 80, 90, 30), QColor(color))
            painter.end()
            sequence.append(frame)
    else:
        sequence = [busy, *([static] * 18), busy, static]
    for number, frame in enumerate(sequence):
        assert frame.save(str(frames / f'frame-{number:05d}.png'))
    output = tmp_path / 'late-icons.gif'
    recorder.encode(shutil.which('ffmpeg'), frames, 6, output)
    receipt = dict(frames=len(sequence), fps=6, size=[width, height],
                   duration_seconds=len(sequence) / 6)
    recorder.verify_gif(output, receipt)
    decoded = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-threads', '1',
        '-ignore_loop', '1', '-i', str(output), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30).stdout
    frame_bytes = width * height * 3
    assert len(decoded) == len(sequence) * frame_bytes
    for number in range(len(sequence)):
        pixels = decoded[number * frame_bytes:(number + 1) * frame_bytes]
        if changing_panel:
            at = (90 * width + 90) * 3
            color = bytes.fromhex(panel_colors[number][1:])
            assert all(abs(pixels[at + channel] - color[channel]) <= 8 for channel in range(3))
        elif number in (0, 19):
            assert max(pixels[:3]) < 40
        else:
            assert min(pixels[:3]) > 240
    for number in range(len(sequence)) if changing_panel else (1, 9, 18, 20):
        pixels = decoded[number * frame_bytes:(number + 1) * frame_bytes]
        for kind, indices in masks.items():
            color = expected[kind]
            errors = [sum(abs(pixels[at + channel] - color[channel]) for at in indices)
                      / len(indices) for channel in range(3)]
            within = sum(all(abs(pixels[at + channel] - color[channel]) <= 16
                             for channel in range(3)) for at in indices)
            assert max(errors) <= 8, (number, kind, errors)
            assert within / len(indices) > .95, (number, kind, within, len(indices))
        assert sum(max(pixels[at:at + 3]) < 80 for at in text_pixels) / len(text_pixels) > .98


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
