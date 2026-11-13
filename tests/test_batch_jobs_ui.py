"""Detached batch-job ordering, draft preservation and execution-state UI."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from PySide6.QtCore import QItemSelectionModel, QTimer
from PySide6.QtWidgets import QDialog

from opennavicat.batch_jobs import new_batch_job, new_pipeline_reference, to_nbatpremium
from opennavicat.database import DatabaseError
from opennavicat.ui.batch_jobs import BatchJobPage, BatchJobSaveDialog


@pytest.fixture
def page(qtbot):
    page = BatchJobPage()
    qtbot.addWidget(page)
    return page


def entries():
    return [
        {'id': 'pipeline-a', 'name': 'generated_a', 'connection_id': 'profile-a',
         'connection_name': 'Generated Mongo A', 'database': 'generated_db'},
        {'id': 'pipeline-b', 'name': 'generated_b', 'connection_id': 'profile-a',
         'connection_name': 'Generated Mongo A', 'database': 'generated_db'},
        {'id': 'pipeline-c', 'name': 'generated_c', 'connection_id': 'profile-a',
         'connection_name': 'Generated Mongo A', 'database': 'other_db'},
        {'id': 'pipeline-d', 'name': 'generated_d', 'connection_id': 'profile-b',
         'connection_name': 'Generated Mongo B', 'database': 'generated_db'},
    ]


def available(page):
    page.set_available_pipelines(entries())
    page.select_job_type('aggregationpipeline')


def selected_rows(table, rows):
    table.clearSelection()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows:
        table.selectionModel().select(table.model().index(row, 0), flags)


def names(page):
    return [job.reference_name for job in page.draft().jobs]


def saved_definition(*names):
    definition = new_batch_job()
    definition.jobs = [new_pipeline_reference('Generated Mongo A', 'generated_db', name) for name in names]
    return definition


def test_observed_page_toolbar_types_and_columns_are_complete(page):
    assert [page.tabs.tabText(index) for index in range(page.tabs.count())] == [
        '常规', '高级', '消息日志', '历史']
    assert [action.text() for action in page.toolbar.actions() if action.text()] == [
        '开始', '保存', '设置任务计划', '删除任务计划', '添加工作', '添加所有工作',
        '移除工作', '移除所有工作', '专注模式']
    assert [page.type_tree.topLevelItem(index).text(0) for index in range(16)] == [
        '数据传输', '数据生成', '数据同步', '数据字典', '数据脱敏', '备份', '导出',
        '导入', '查询', '聚合管道', 'MongoDump', 'MongoExport', 'MongoImport',
        'MapReduce', '模型', 'BI']
    assert [page.jobs_table.horizontalHeaderItem(index).text() for index in range(10)] == [
        '已选择的工作', '类型', '附加到电子邮件', '连接类型', '连接', '云名称',
        '项目名称', '项目所有者用户 ID', '数据库', '模式']
    assert [page.history_table.horizontalHeaderItem(index).text() for index in range(6)] == [
        '执行编号', '开始时间', '结束时间', '期间', '执行', '结果']


def test_empty_defaults_keep_unimplemented_surfaces_explicit(page):
    assert page.continue_on_error_check.isChecked()
    assert page.logging_check.isChecked()
    assert not page.send_email_check.isChecked()
    assert not page.email_group.isEnabled()
    assert not page.test_email_button.isEnabled()
    assert not page.schedule_action.isEnabled()
    assert not page.remove_schedule_action.isEnabled()
    assert '尚未接通' in page.schedule_action.toolTip()
    assert '尚未接通' in page.availability_notice.text()
    assert not page.start_action.isEnabled()
    assert not page.remove_action.isEnabled()
    assert not page.remove_all_action.isEnabled()
    assert page.save_action.isEnabled()
    assert page.draft().jobs == []


def test_saved_objects_filter_by_category_connection_and_database(page):
    page.set_available_pipelines(entries())
    assert page.available_table.rowCount() == 0
    page.select_job_type('aggregationpipeline')
    assert page.available_table.rowCount() == 3
    connection = page.connection_tree.topLevelItem(0)
    page.connection_tree.setCurrentItem(connection.child(0))
    assert [page.available_table.item(row, 0).text() for row in range(2)] == ['generated_a', 'generated_b']
    page.connection_tree.setCurrentItem(connection.child(1))
    assert page.available_table.rowCount() == 1
    assert page.available_table.item(0, 0).text() == 'generated_c'
    page.connection_tree.setCurrentItem(page.connection_tree.topLevelItem(1))
    assert page.available_table.item(0, 0).text() == 'generated_d'
    page.select_job_type('backup')
    assert page.available_table.rowCount() == 0
    assert not page.add_all_action.isEnabled()
    assert '尚未接通' in page.availability_notice.text()


def test_repeated_pipeline_adds_remain_separate_ordered_references(page):
    available(page)
    selected_rows(page.available_table, [1])
    assert page.add_action.isEnabled()
    page.add_action.trigger()
    page.add_action.trigger()
    assert names(page) == ['generated_b', 'generated_b']
    assert page.definition.jobs[0] is not page.definition.jobs[1]
    assert page.jobs_table.item(0, 3).text() == 'MONGODB'
    assert page.jobs_table.item(0, 4).text() == 'Generated Mongo A'
    assert page.jobs_table.item(0, 8).text() == ''
    assert page.jobs_table.item(0, 9).text() == 'generated_db'
    assert page.dirty and page.start_action.isEnabled()


def test_add_all_uses_current_scope_in_order_and_allows_second_copy(page):
    available(page)
    page.connection_tree.setCurrentItem(page.connection_tree.topLevelItem(0).child(0))
    page.add_all_action.trigger()
    page.add_all_action.trigger()
    assert names(page) == ['generated_a', 'generated_b', 'generated_a', 'generated_b']


def test_multi_remove_targets_positions_without_deduplicating_names(page):
    page.load_definition(saved_definition('generated_a', 'generated_b', 'generated_a', 'generated_c'))
    selected_rows(page.jobs_table, [1, 2])
    page.remove_action.trigger()
    assert names(page) == ['generated_a', 'generated_c']
    assert page.dirty
    page.remove_all_action.trigger()
    assert not names(page)
    assert not page.start_action.isEnabled()
    assert not page.remove_all_action.isEnabled()


def test_advanced_controls_preserve_unknown_fields_and_detach_drafts(page):
    definition = saved_definition('generated_a')
    definition.general['FutureOption'] = {'mode': ['retained', 4]}
    definition.jobs[0].reference_extra['FutureTaskField'] = {'retained': True}
    definition.reference_extra['FutureRoot'] = [1, 2]
    original = to_nbatpremium(definition)
    page.load_definition(definition, job_id='generated-job', name='Generated batch')
    assert not page.dirty and not page.save_action.isEnabled()
    assert to_nbatpremium(page.draft()) == original
    page.logging_check.setChecked(False)
    page.continue_on_error_check.setChecked(False)
    draft = page.draft()
    assert draft.general['EnableLogging'] is False
    assert draft.general['ContinueOnError'] is False
    assert draft.general['FutureOption'] == {'mode': ['retained', 4]}
    assert draft.jobs[0].reference_extra == definition.jobs[0].reference_extra
    assert draft.reference_extra == definition.reference_extra
    assert to_nbatpremium(definition) == original
    draft.jobs.clear()
    assert names(page) == ['generated_a']
    assert page.dirty and page.save_action.isEnabled()
    assert page.title == 'Generated batch — 已编辑'


def test_invalid_load_is_atomic_and_preserves_current_input(page):
    good = saved_definition('generated_a')
    page.load_definition(good, job_id='existing', name='Generated batch')
    before = to_nbatpremium(page.draft())
    invalid = deepcopy(good)
    invalid.general['ContinueOnError'] = 'wrong type'
    with pytest.raises(DatabaseError):
        page.load_definition(invalid, job_id='replacement', name='Should not replace')
    assert to_nbatpremium(page.draft()) == before
    assert page.name == 'Generated batch'
    assert page.job_id == 'existing'


def test_new_save_sheet_cancel_preserves_order_and_unsaved_state(page):
    page.load_definition(saved_definition('generated_a', 'generated_a'))
    page.logging_check.setChecked(False)
    before = to_nbatpremium(page.draft())
    emitted = []
    page.saveRequested.connect(emitted.append)
    def cancel():
        page.save_dialog.name_edit.setText('Generated cancelled batch')
        page.save_dialog.reject()
    QTimer.singleShot(0, cancel)
    page.request_save()
    assert not emitted
    assert page.name == '' and page.job_id is None and page.dirty
    assert to_nbatpremium(page.draft()) == before


def test_new_name_sheet_then_existing_save_uses_detached_definition(page):
    page.load_definition(saved_definition('generated_a'))
    emitted = []
    page.saveRequested.connect(emitted.append)
    def accept():
        dialog = page.save_dialog
        assert isinstance(dialog, BatchJobSaveDialog)
        assert not dialog.save_button.isEnabled()
        dialog.name_edit.setText('  Generated batch  ')
        assert dialog.save_button.isEnabled()
        dialog.accept()
    QTimer.singleShot(0, accept)
    page.request_save()
    assert page.name == 'Generated batch' and len(emitted) == 1
    page.mark_saved(emitted[0], job_id='saved-id', name=page.name)
    assert not page.dirty and not page.save_action.isEnabled()
    emitted[0].jobs.clear()
    assert names(page) == ['generated_a']
    previous_dialog = page.save_dialog
    page.logging_check.setChecked(False)
    page.request_save()
    assert page.save_dialog is previous_dialog
    assert len(emitted) == 2 and not emitted[1].general['EnableLogging']


def test_save_name_requires_nonblank_and_has_no_script_preview(qtbot):
    dialog = BatchJobSaveDialog()
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('   ')
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not hasattr(dialog, 'preview')


def test_start_snapshot_and_running_guards_preserve_job_order(page):
    available(page)
    page.add_all_jobs()
    started = []
    cancelled = []
    page.startRequested.connect(started.append)
    page.cancelRequested.connect(lambda: cancelled.append(True))
    page.request_cancel()
    assert not cancelled
    page.request_start()
    assert len(started) == 1
    started[0].jobs.clear()
    before = to_nbatpremium(page.draft())
    page.set_running(True)
    assert page.tabs.currentWidget() is page.messages
    for action in (page.start_action, page.save_action, page.add_action, page.add_all_action,
                   page.remove_action, page.remove_all_action):
        assert not action.isEnabled()
    assert not page.logging_check.isEnabled()
    page.remove_all_jobs(); page.add_all_jobs(); page.request_start(); page.request_save()
    page.request_cancel()
    assert cancelled == [True] and len(started) == 1
    assert to_nbatpremium(page.draft()) == before
    page.set_running(False)
    assert page.start_action.isEnabled() and page.logging_check.isEnabled()


def test_messages_show_each_job_outcome_and_final_counts(page):
    page.set_running(True)
    for index, status in enumerate(('running', 'success', 'failed', 'skipped', 'cancelled')):
        page.append_log({'timestamp': '2026-11-13T00:00:00Z', 'status': status,
                         'job_index': index, 'name': f'generated_{index}', 'message': 'generated event'})
    result = {'execution_id': 1, 'status': 'cancelled', 'total': 5, 'success': 1,
              'failed': 1, 'skipped': 1, 'cancelled': 2}
    page.update_result(result)
    text = page.messages.toPlainText()
    for status in ('running', 'success', 'failed', 'skipped', 'cancelled'):
        assert f'[{status}]' in text
    assert '#1 generated_0' in text and '#5 generated_4' in text
    assert 'Total: 5 / Success: 1 / Fail: 1 / Skip: 1 / Cancelled: 2' in text
    assert not page.running
    result['status'] = 'changed outside'
    assert page.last_result['status'] == 'cancelled'


def test_history_selection_restores_matching_log_without_replacing_live_messages(page):
    records = [
        {'execution_id': 2, 'start_time': '2026-11-13T00:00:01Z', 'end_time': '2026-11-13T00:00:02Z',
         'duration_ms': 1000.25, 'execute': 'Manual', 'status': 'failed',
         'logs': [{'status': 'failed', 'name': 'generated_a', 'message': 'second run'}]},
        {'execution_id': 1, 'execute': 'Manual', 'status': 'success',
         'logs': [{'status': 'success', 'name': 'generated_a', 'message': 'first run'}]},
    ]
    page.append_log('current live log')
    page.load_history(records)
    records[0]['logs'][0]['message'] = 'changed outside'
    page.history_table.selectRow(0)
    assert 'second run' in page.history_log.toPlainText()
    assert 'first run' not in page.history_log.toPlainText()
    assert page.history_table.item(0, 3).text() == '1000.250 ms'
    assert page.history_table.item(0, 4).text() == 'Manual'
    page.history_table.selectRow(1)
    assert 'first run' in page.history_log.toPlainText()
    assert page.messages.toPlainText() == 'current live log'
    assert not page.dirty


def test_long_display_labels_do_not_truncate_saved_reference(page):
    long_name = 'generated_' + 'x' * 1500
    definition = saved_definition(long_name)
    page.load_definition(definition)
    assert len(page.jobs_table.item(0, 0).text()) == 512
    assert page.jobs_table.item(0, 0).text().endswith('…')
    assert page.draft().jobs[0].reference_name == long_name


def test_bounded_live_log_and_history_show_truncation_notice(page, monkeypatch):
    import opennavicat.ui.batch_jobs as module
    monkeypatch.setattr(module, 'MAX_LOG_CHARS', 140)
    monkeypatch.setattr(module, 'MAX_HISTORY_LOG_CHARS', 200)
    for index in range(20):
        page.append_log(f'generated event {index:02}: ' + 'x' * 15)
    assert '超出显示上限' in page.messages.toPlainText()
    assert 'generated event 19' in page.messages.toPlainText()
    assert 'generated event 00' not in page.messages.toPlainText()
    page.load_history([{'execution_id': index, 'logs': ['x' * 80, f'generated final {index}']} for index in range(5)])
    page.history_table.selectRow(4)
    assert '超出显示上限' in page.history_log.toPlainText()
    assert sum(len(record['display_log']) for record in page._history) < 400


def test_widget_entry_points_reject_worker_thread_access(page):
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match='GUI thread'):
            pool.submit(page.append_log, 'worker must not touch Qt').result()
        with pytest.raises(RuntimeError, match='GUI thread'):
            pool.submit(page.draft).result()
    assert page.messages.toPlainText() == ''


def test_catalog_refresh_is_atomic_and_does_not_dirty_job(page):
    available(page)
    page.add_all_jobs()
    page.mark_saved(page.draft(), job_id='existing', name='Generated batch')
    original = to_nbatpremium(page.draft())
    with pytest.raises(DatabaseError):
        page.set_available_pipelines([entries()[0], {'name': 'missing binding'}])
    assert page.available_table.rowCount() == 3
    assert to_nbatpremium(page.draft()) == original
    assert not page.dirty
