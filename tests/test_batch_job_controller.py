"""Local controller failures retain execution facts without rerunning work."""
from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QMainWindow,
    QMessageBox, QTabWidget,
)

from opennavicat.batch_jobs import new_batch_job, new_pipeline_reference, to_nbatpremium
from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, to_nagg
from opennavicat.storage import Store
from opennavicat.ui.batch_job_controller import BatchJobController, BatchJobListPage


class DetachedWindow(QMainWindow):
    """Capture callbacks without opening databases or running queued work."""
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._closed = self._closing_mongo = False
        self.configs = {}
        self.calls = []
        self.errors = []

    def submit(self, function, success=None, failure=None, label='', finished=None, pool=None):
        self.calls.append(dict(function=function, success=success, failure=failure, finished=finished))

    def refresh_actions(self):
        pass

    def error(self, error):
        self.errors.append(str(error))

    def focus_workspace(self, *args):
        pass


@pytest.fixture
def controller(qtbot, tmp_path):
    window = DetachedWindow(Store(tmp_path))
    qtbot.addWidget(window)
    controller = BatchJobController(window)
    yield controller
    for state in tuple(controller.runs.values()):
        state['timer'].stop()
        state['timer'].deleteLater()
        state['page'].set_running(False)
    controller.runs.clear()


def definition():
    value = new_batch_job()
    value.jobs = [new_pipeline_reference('Generated Mongo', 'generated_db', 'generated_pipeline')]
    return value


def result(label='generated completed run'):
    return dict(status='success', total=1, success=1, failed=0, skipped=0, cancelled=0,
                logs=[dict(status='success', name='generated_pipeline', message=label)])


def saved_page(controller):
    entry = controller.window.store.save_batch_job('Generated batch', to_nbatpremium(definition()))
    return controller.open_job(entry['id'])


def deliver_result(controller, page, outcome):
    controller.start(page, page.draft())
    call = controller.window.calls[-1]
    try:
        call['success'](outcome)
    finally:
        call['finished']()


def test_history_write_failure_keeps_completed_result_and_requires_explicit_close_choice(controller, monkeypatch):
    page = saved_page(controller)
    store = controller.window.store
    before = store.batch_job(page.job_id)['nbatpremium']
    def failed_history(*args):
        raise sqlite3.OperationalError('generated local history failure')
    monkeypatch.setattr(store, 'record_batch_job_run', failed_history)
    outcome = result()
    deliver_result(controller, page, outcome)
    assert page.last_result == outcome
    assert not page.running
    assert controller.unsaved_history[id(page)] == [outcome]
    assert page.history_pending and page.save_action.isEnabled()
    assert store.batch_job_history(page.job_id) == []
    assert store.batch_job(page.job_id)['nbatpremium'] == before
    assert 'do not rerun' in page.messages.toPlainText()
    prompts = []
    def cancel_close(*args):
        prompts.append(True)
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, 'question', cancel_close)
    assert not controller.can_discard(page)
    assert prompts == [True]
    assert controller.unsaved_history[id(page)] == [outcome]
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Discard)
    assert controller.can_discard(page)


def test_pending_history_retry_only_replays_uncommitted_history_and_never_execution(controller, monkeypatch):
    page = controller.new_job()
    page.load_definition(definition())
    page.name = 'Generated unsaved batch'
    first, second = result('generated first run'), result('generated second run')
    controller.unsaved_history[id(page)] = [deepcopy(first), deepcopy(second)]
    store = controller.window.store
    original = store.record_batch_job_run
    writes = []
    def fail_second(job_id, outcome):
        writes.append(outcome['logs'][0]['message'])
        if len(writes) == 2:
            raise sqlite3.OperationalError('generated second history failure')
        return original(job_id, outcome)
    monkeypatch.setattr(store, 'record_batch_job_run', fail_second)
    assert controller.save(page, page.draft()) is None
    assert controller.unsaved_history[id(page)] == [second]
    assert page.history_pending and page.save_action.isEnabled()
    assert [row['logs'][0]['message'] for row in store.batch_job_history(page.job_id)] == ['generated first run']
    monkeypatch.setattr(store, 'record_batch_job_run', original)
    page.logging_check.setChecked(False)
    assert controller.save(page, page.draft()) is not None
    assert not controller.unsaved_history.get(id(page))
    assert not page.history_pending and not page.save_action.isEnabled()
    history = store.batch_job_history(page.job_id)
    assert [row['execution_id'] for row in history] == [2, 1]
    assert [row['logs'][0]['message'] for row in history] == ['generated second run', 'generated first run']
    assert controller.window.calls == []


def test_history_read_failure_after_commit_does_not_queue_or_write_duplicate_run(controller, monkeypatch):
    page = saved_page(controller)
    store = controller.window.store
    original_read = store.batch_job_history
    def failed_read(*args):
        raise sqlite3.OperationalError('generated local history read failure')
    monkeypatch.setattr(store, 'batch_job_history', failed_read)
    deliver_result(controller, page, result())
    assert page.last_result['execution_id'] == 1
    assert not controller.unsaved_history.get(id(page))
    monkeypatch.setattr(store, 'batch_job_history', original_read)
    assert controller.save(page, page.draft()) is not None
    assert [row['execution_id'] for row in original_read(page.job_id)] == [1]
    assert len(controller.window.calls) == 1


def test_hidden_filtered_selection_cannot_be_opened_or_deleted(qtbot):
    page = BatchJobListPage()
    qtbot.addWidget(page)
    page.set_jobs([{'id': 'generated-a', 'name': 'Generated Alpha'},
                   {'id': 'generated-b', 'name': 'Generated Beta'}])
    row = next(row for row in range(2) if page.table.item(row, 0).text() == 'Generated Alpha')
    page.table.selectRow(row)
    assert page.selected_id() == 'generated-a'
    opened, deleted = [], []
    page.openRequested.connect(opened.append)
    page.deleteRequested.connect(deleted.append)
    page.search.setText('Beta')
    assert page.selected_id() is None
    assert not page.design_action.isEnabled() and not page.delete_action.isEnabled()
    page.open_selected(); page.delete_selected()
    assert not opened and not deleted
    assert '1' in page.count_label.text()


@pytest.mark.parametrize('close_mode', ['cancel', 'accept_without_permanent_check'])
def test_delete_cancel_or_missing_permanent_check_keeps_definition_and_profiles(controller, close_mode):
    store = controller.window.store
    store.state['connections'] = [{'id': 'generated-profile', 'name': 'Generated profile', 'driver': 'mongodb'}]
    entry = store.save_batch_job('Generated batch', to_nbatpremium(new_batch_job()))
    before = store.path.read_bytes()
    profiles = deepcopy(store.state['connections'])
    def close_sheet():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        try:
            checkbox = dialog.findChild(QCheckBox)
            buttons = dialog.findChild(QDialogButtonBox)
            confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
            assert not confirm.isEnabled()
            if close_mode == 'accept_without_permanent_check':
                dialog.accept()
            else:
                checkbox.setChecked(True)
                assert confirm.isEnabled()
                buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        finally:
            if dialog.isVisible():
                dialog.reject()
    QTimer.singleShot(0, close_sheet)
    controller.delete_job(entry['id'])
    assert store.batch_job(entry['id']) == entry
    assert store.path.read_bytes() == before
    assert store.state['connections'] == profiles


def test_unreviewed_profile_is_not_hydrated_or_prepared_for_remote_execution(controller, monkeypatch):
    import opennavicat.batch_job_runtime as runtime
    store, window = controller.window.store, controller.window
    config = ConnectionConfig('generated-profile', 'Generated Mongo', driver='mongodb')
    window.configs[config.id] = config
    store.save_mongo_pipeline(config.id, 'generated_db', 'generated_pipeline',
                              to_nagg(PipelineDefinition(source='generated_documents',
                                  stages=[PipelineStage('$match', {})])))
    store.state['navicat_imports'][config.id] = {'blocked': True}
    def forbidden_hydrate(*args):
        pytest.fail('Unreviewed profiles must not load any credentials')
    monkeypatch.setattr(store, 'hydrate', forbidden_hydrate)
    prepared = []
    def detached_run(_definition, jobs, **kwargs):
        prepared.extend(jobs)
        return result()
    monkeypatch.setattr(runtime, 'run_batch_job', detached_run)
    page = controller.new_job()
    page.load_definition(definition())
    controller.start(page, page.draft())
    call = window.calls[-1]
    try:
        call['function']()
        assert len(prepared) == 1
        assert prepared[0].config is None and prepared[0].resolved is None
        assert prepared[0].error
    finally:
        call['finished']()


def test_full_pending_history_prevents_another_run_without_dropping_receipts(controller):
    page = saved_page(controller)
    pending = [result(f'generated unsaved run {index}') for index in range(200)]
    controller.unsaved_history[id(page)] = deepcopy(pending)
    controller.start(page, page.draft())
    assert controller.window.calls == []
    assert controller.unsaved_history[id(page)] == pending
    assert not page.running
    assert page.messages.toPlainText()
