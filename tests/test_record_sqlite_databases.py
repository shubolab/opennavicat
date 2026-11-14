"""Actual owned SQLite database page/sheet recording, without native GUI lease."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from test_record_demo import recorder


@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('include_test', [False, True])
def test_database_opt_in_preserves_old_six_seven_and_35_36_in_order(recorder, full, include_test):
    flags = dict(mongodb=full, stream=full, oracle_form=full, connection_chooser=full,
                 sqlite_test=include_test)
    previous = recorder.required_demo_phases(**flags)
    extended = recorder.required_demo_phases(**flags, sqlite_databases=True)
    assert len(previous) == (35 if full else 6) + int(include_test)
    assert len(extended) == len(set(extended)) == len(previous) + 2
    assert tuple(name for name in extended if name not in recorder.SQLITE_DATABASES_PHASES) == previous
    start = extended.index('sqlite_connection_databases_empty')
    assert extended[start:start+2] == ('sqlite_connection_databases_empty', 'sqlite_database_add_sheet_empty')
    assert extended[start-1] == ('sqlite_connection_test' if include_test else
                                 'mongodb_finite_stream_output' if full else 'designer_sql_preview')


@pytest.mark.parametrize('include_test', [False, True])
def test_database_opt_in_requires_explicit_output(recorder, monkeypatch, include_test):
    argv = ['record-demo.py', '--include-sqlite-databases']
    if include_test:
        argv.append('--include-sqlite-test')
    monkeypatch.setattr(recorder.sys, 'argv', argv)
    monkeypatch.setattr(recorder, 'dependencies', lambda *_: ('unused-encoder', 'unused-version'))
    monkeypatch.setattr(recorder, 'record', lambda *_: pytest.fail('Extended capture overwrote default output'))
    with pytest.raises(SystemExit) as error:
        recorder.main()
    assert error.value.code == 2


def test_real_empty_add_sheet_cancel_has_no_store_keyring_or_draft_file_changes(qtbot, recorder, tmp_path, monkeypatch):
    import keyring
    from opennavicat.storage import Store

    def forbidden(*_args, **_kwargs):
        pytest.fail('Empty SQLite database demo accessed Store or credential vault')
    monkeypatch.setattr(Store, '__init__', forbidden)
    for name in ('get_password', 'set_password', 'delete_password'):
        monkeypatch.setattr(keyring, name, forbidden)
    errors = []
    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        with recorder.sqlite_connection_test_demo(fixture) as dialog:
            dialog.show(); qtbot.waitExposed(dialog)
            ledger = recorder.begin_sqlite_databases_demo(dialog)
            recorder.verify_sqlite_databases_demo(dialog)
            recorder.assert_visible_privacy(dialog, tmp_path)
            before = dialog.sqlite_databases_page.snapshot()
            def cancel():
                sheet = QApplication.activeModalWidget()
                try:
                    recorder.observe_sqlite_database_sheet(dialog, sheet)
                    recorder.assert_visible_privacy(sheet, tmp_path)
                    assert not sheet.grab().isNull()
                    sheet.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
                except BaseException as error:
                    errors.append(error)
                    sheet.reject()
            QTimer.singleShot(0, cancel)
            dialog.sqlite_databases_page.add_button.click()
            if errors:
                raise errors[0]
            facts = recorder.finish_sqlite_databases_demo(dialog)
            assert facts['sheet_rejected'] and facts['draft_unchanged']
            assert ledger['sheet_results'] == [0] and ledger['changed'] == 0
            assert dialog.sqlite_databases_page.snapshot() == before
            assert dialog.get_config() is None and dialog._demo_sqlite_test_ledger['results'] == []
        assert pool.submit(recorder.verify_sqlite_test_fixture, fixture).result(timeout=5)['fixture_unchanged']


@pytest.mark.parametrize('tamper', ['file', 'password', 'encryption'])
def test_sheet_verifier_rejects_changed_actual_fields_before_capture(qtbot, recorder, tmp_path, tamper):
    errors = []
    with recorder.owned_recording_cwd(tmp_path, enabled=True), ThreadPoolExecutor(max_workers=1) as pool:
        fixture = pool.submit(recorder.prepare_sqlite_test_fixture, tmp_path).result(timeout=5)
        with recorder.sqlite_connection_test_demo(fixture) as dialog:
            dialog.show(); qtbot.waitExposed(dialog)
            recorder.begin_sqlite_databases_demo(dialog)
            def changed():
                sheet = QApplication.activeModalWidget()
                try:
                    recorder.observe_sqlite_database_sheet(dialog, sheet)
                    if tamper == 'file':
                        sheet.file_edit.setText(str(fixture['path']))
                    elif tamper == 'password':
                        sheet.password_edit.setText('generated-private-password')
                    else:
                        sheet.encrypted_check.setChecked(True)
                    with pytest.raises(RuntimeError, match='actual empty fields'):
                        recorder.verify_sqlite_databases_demo(dialog, sheet)
                    if tamper == 'file':
                        with pytest.raises(RuntimeError, match='filesystem path'):
                            recorder.assert_visible_privacy(sheet, tmp_path)
                except BaseException as error:
                    errors.append(error)
                finally:
                    sheet.reject()
            QTimer.singleShot(0, changed)
            dialog.sqlite_databases_page.add_button.click()
            if errors:
                raise errors[0]
            assert dialog.sqlite_databases_page.snapshot().sqlite_databases == ()


@pytest.mark.parametrize('include_test,fail_sheet', [(False, False), (True, False), (False, True), (True, True),
                                                   (False, 'none-modal')],
                         ids=['database-eight', 'test-database-nine', 'real-sheet-capture-error',
                              'test-sheet-capture-error', 'owned-modal-observer-missing'])
def test_real_record_database_opt_in_and_error_cleanup(recorder, include_test, fail_sheet):
    # Real UI callbacks/modal loops, actual frames/privacy checks and owned
    # SQLite I/O. Only presentation holds are capped; no encoder/container.
    source = r'''
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib,importlib.util,json,os,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication,QEvent,QTimer
from PySide6.QtWidgets import QApplication
path=Path(sys.argv[1]);include=sys.argv[2]=='1';failure=sys.argv[3]!='0';missing=sys.argv[3]=='none-modal'
sys.path.insert(0,str(path.parents[1]))
import opennavicat.ui.dialogs as actual_dialogs
from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog
assert Path(actual_dialogs.__file__).resolve().is_relative_to(path.parents[1])
spec=importlib.util.spec_from_file_location('actual_record_sqlite_databases_child',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def forbidden(*args,**kwargs):raise AssertionError('No encoder or Docker in actual record regression')
module.dependencies=forbidden;module.encode=forbidden;module.verify_gif=forbidden;module.OwnedMongoDemo=forbidden
original_hold=QTimer.singleShot
def bounded_hold(*args):
 if args and type(args[0]) is int:args=(min(args[0],250),*args[1:])
 return original_hold(*args)
QTimer.singleShot=staticmethod(bounded_hold)
dialog_ledgers=[];fixtures=[];sheets=[]
real_context=module.sqlite_connection_test_demo
@contextmanager
def observed_context(fixture,parent=None):
 fixtures.append(fixture)
 with real_context(fixture,parent) as dialog:
  dialog_ledgers.append(dialog._demo_sqlite_test_ledger)
  yield dialog
module.sqlite_connection_test_demo=observed_context
real_observe=module.observe_sqlite_database_sheet
def observed_sheet(dialog,sheet):
 facts=real_observe(dialog,sheet)
 state={'finished':[],'destroyed':False};sheets.append(state)
 sheet.finished.connect(lambda code:state['finished'].append(int(code)))
 sheet.destroyed.connect(lambda *_:state.update(destroyed=True))
 return facts
module.observe_sqlite_database_sheet=observed_sheet
# Lose only the Python observer's modal lookup while the genuine owned sheet
# is inside exec(). A separate bounded rescue prevents the old implementation
# hanging the child; a passing implementation must never need that rescue.
modal_fault={'observed':False,'rescued':False,'timer':None,'finished':[]}
real_active_modal=QApplication.activeModalWidget
if missing:
 def missing_owned_modal():
  actual=real_active_modal()
  if type(actual) is SQLiteDatabaseDialog:
   if not modal_fault['observed']:
    modal_fault['observed']=True
    actual.finished.connect(lambda code:modal_fault['finished'].append(int(code)))
    timer=QTimer(QApplication.instance());timer.setSingleShot(True)
    def bounded_rescue():
     if real_active_modal() is actual:
      modal_fault['rescued']=True
      actual.reject()
    timer.timeout.connect(bounded_rescue);modal_fault['timer']=timer;timer.start(1000)
   return None
  return actual
 QApplication.activeModalWidget=staticmethod(missing_owned_modal)
real_privacy=module.assert_visible_privacy
def guarded_privacy(widget,*args):
 real_privacy(widget,*args)
 if failure and not missing and type(widget) is SQLiteDatabaseDialog:raise RuntimeError('controlled actual sheet capture failure')
module.assert_visible_privacy=guarded_privacy
args=SimpleNamespace(width=950,height=620,fps=10,theme='dark',include_stream=False,
 include_oracle_form=False,include_connection_chooser=False,include_sqlite_test=include,include_sqlite_databases=True)
original=Path.cwd()
with tempfile.TemporaryDirectory(prefix='opennavicat-real-databases-record-') as directory:
 temporary=Path(directory).resolve()
 with ThreadPoolExecutor(max_workers=1,thread_name_prefix='actual-record-databases-io') as pool:
  with module.owned_recording_cwd(temporary,enabled=True) as cwd:
   try:
    frames,receipt=module.record(args,temporary,pool)
   except RuntimeError as error:
    expected_error=('The generated database add sheet was not opened by its actual component.'
                    if missing else 'controlled actual sheet capture failure')
    assert failure and str(error)==expected_error,type(error).__name__
   else:
    assert not failure
    expected=module.required_demo_phases(sqlite_test=include,sqlite_databases=True)
    assert tuple(p['name'] for p in receipt['phases'])==expected and len(expected)==(9 if include else 8)
    files=sorted(frames.glob('frame-*.png'))
    assert len(files)==receipt['frames'] and all(p.stat().st_size for p in files)
    assert len({hashlib.sha256(files[p['first_frame']].read_bytes()).hexdigest() for p in receipt['phases']})==len(expected)
    assert all(p['frames']>0 and p['privacy_checked_frames']==p['frames'] for p in receipt['phases'])
    phases={p['name']:p for p in receipt['phases']}
    assert phases['sqlite_connection_databases_empty']['surface']=='sqlite_connection_databases_dialog'
    assert phases['sqlite_database_add_sheet_empty']['surface']=='sqlite_database_add_sheet'
    facts=receipt['sqlite_connection_databases']
    for name in ('actual_add_button_clicked','actual_empty_sheet_opened','actual_cancel_button_clicked','sheet_rejected',
      'empty_database_page_verified','empty_sheet_fields_verified','draft_unchanged','credentials_empty','pool_idle_before_capture',
      'fixture_unchanged','form_closed_after_worker_idle','main_window_pool_idle_before_capture'):
     assert facts[name] is True,name
    assert facts['database_rows']==facts['draft_changed_signals']==0 and facts['rows']==2
    assert facts['profile_saved'] is facts['row_added'] is facts['secret_added'] is False
    assert receipt['query_result']=={'rows':6,'columns':4}
    assert receipt['qt_platform']=='offscreen' and receipt['sqlite_closed_in_worker']
    assert receipt['native_window_closed_before_quit'] and receipt['background_io_completed_before_quit']
    assert (receipt['sqlite_connection_test'] is not None)==include
    if include:assert receipt['sqlite_connection_test']['actual_probe_completed']
    text=json.dumps(receipt,ensure_ascii=False)
    assert not any(value in text for value in (str(module.ROOT),str(temporary),str(Path.home())))
   finally:pool.submit(lambda:None).result(timeout=10)
   if modal_fault['timer'] is not None:modal_fault['timer'].stop()
   assert len(fixtures)==len(dialog_ledgers)==1
   assert len(sheets)==(0 if missing else 1)
   assert pool.submit(module.verify_sqlite_test_fixture,fixtures[0]).result(timeout=5)['fixture_unchanged']
   assert dialog_ledgers[0]['closed'] and dialog_ledgers[0]['accepted']==0
   assert len(dialog_ledgers[0]['probe_threads'])==int(include)
   assert dialog_ledgers[0]['close_threads']==dialog_ledgers[0]['probe_threads']
   QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
   if not missing:assert sheets[0]['finished']==[0] and sheets[0]['destroyed']
   else:assert modal_fault['observed'] and modal_fault['finished']==[0]
 assert cwd['restored'] and Path.cwd()==original
assert not temporary.exists()
if missing:
 print(json.dumps({'lost_real_owned_modal':True,'bounded_rescue_needed':modal_fault['rescued']}))
 assert not modal_fault['rescued'],'The real owned sheet exec needed the test rescue to unwind'
print(json.dumps({'passed':True,'include_test':include,'capture_failure':failure,'actual_qpa':'offscreen'}))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('OPENNAVICAT_TEST_')}
    env['QT_QPA_PLATFORM'] = 'offscreen'
    original = Path.cwd()
    result = subprocess.run([recorder.sys.executable, '-W', 'error', '-c', source, recorder.__file__,
                             str(int(include_test)), fail_sheet if isinstance(fail_sheet,str) else str(int(fail_sheet))],
                            env=env, capture_output=True, text=True, timeout=40)
    assert Path.cwd() == original
    assert result.returncode == 0, (result.stdout + result.stderr)[-16000:]
    assert json.loads(result.stdout.splitlines()[-1])['passed']
