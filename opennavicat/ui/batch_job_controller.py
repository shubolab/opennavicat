"""GUI-thread Batch Job routing; remote work stays on the window's serial queue."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
import threading
import time
import sqlite3

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QMessageBox, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from opennavicat.batch_jobs import (
    from_nbatpremium, new_batch_job, require_executable,
    resolve_pipeline_reference, to_nbatpremium,
)
from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import WRITE_STAGES, from_nagg

from .batch_jobs import BatchJobPage
from .icons import tool_icon


class BatchJobListPage(QWidget):
    newRequested = Signal()
    openRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.toolbar = QToolBar(); box.addWidget(self.toolbar)
        self.design_action = self.toolbar.addAction(tool_icon('edit'), '设计批处理作业', self.open_selected)
        self.toolbar.addAction(tool_icon('new'), '新建批处理作业', self.newRequested.emit)
        self.delete_action = self.toolbar.addAction(tool_icon('delete'), '删除批处理作业', self.delete_selected)
        self.schedule_actions = []
        for name in ('设置计划', '删除计划'):
            action = self.toolbar.addAction(name); action.setEnabled(False)
            action.setToolTip('系统计划仍在完整对齐范围内，尚未接通。')
            self.schedule_actions.append(action)
        self.refresh_action = self.toolbar.addAction(tool_icon('refresh'), '刷新')
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索')
        self.search.setClearButtonEnabled(True); self.toolbar.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['名称', '文件大小', '创建日期', '修改日期', '访问时间'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide(); self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True); box.addWidget(self.table, 1)
        self.count_label = QLabel(); box.addWidget(self.count_label)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.search.textChanged.connect(self._filter)
        self._selection_changed()

    def set_jobs(self, entries):
        self.table.setSortingEnabled(False); self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            def date(key):
                try:
                    return datetime.fromisoformat(entry.get(key, '')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return ''
            values = [entry['name'], f"{max(1, (entry.get('size', 0) + 1023) // 1024)} KB",
                      date('created'), date('modified'), date('accessed')]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, entry['id']); self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True); self._filter(self.search.text()); self._selection_changed()

    def selected_id(self):
        if self.table.currentRow() < 0 or self.table.isRowHidden(self.table.currentRow()):
            return None
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _selection_changed(self):
        selected = self.selected_id() is not None
        self.design_action.setEnabled(selected); self.delete_action.setEnabled(selected)
        self.design_action.setVisible(selected); self.delete_action.setVisible(selected)
        for action in self.schedule_actions:
            action.setVisible(selected)

    def _filter(self, text):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, text.casefold() not in self.table.item(row, 0).text().casefold())
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        self.count_label.setText(f'找到{visible}个项目' if text else f'{visible}个批处理作业')
        self._selection_changed()

    def open_selected(self):
        if self.selected_id() is not None:
            self.openRequested.emit(self.selected_id())

    def delete_selected(self):
        if self.selected_id() is not None:
            self.deleteRequested.emit(self.selected_id())


class BatchJobController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.list_page = None
        self.runs = {}
        self.unsaved_history = {}

    def show_list(self):
        window = self.window
        if self.list_page is None or not isValid(self.list_page):
            page = self.list_page = BatchJobListPage(window)
            page.newRequested.connect(self.new_job)
            page.openRequested.connect(self.open_job)
            page.deleteRequested.connect(self.delete_job)
            page.refresh_action.triggered.connect(self.refresh_list)
            window.tabs.addTab(page, tool_icon('automation'), '自动运行')
        self.refresh_list()
        window.tabs.setCurrentWidget(self.list_page)
        return self.list_page

    def refresh_list(self):
        if self.list_page is not None and isValid(self.list_page):
            self.list_page.set_jobs(self.window.store.batch_jobs())

    def new_job(self):
        return self.open_job()

    def open_job(self, job_id=None):
        window = self.window
        if job_id is not None:
            for i in range(window.tabs.count()):
                page = window.tabs.widget(i)
                if isinstance(page, BatchJobPage) and page.job_id == job_id:
                    window.tabs.setCurrentWidget(page); return page
        entry = window.store.batch_job(job_id) if job_id is not None else None
        if job_id is not None and entry is None:
            window.error(DatabaseError('The saved Batch Job no longer exists')); return None
        try:
            definition = from_nbatpremium(entry['nbatpremium']) if entry else new_batch_job()
        except DatabaseError as error:
            window.error(error); return None
        page = BatchJobPage(parent=window)
        page.load_definition(definition, job_id=job_id, name=entry['name'] if entry else None)
        page.startRequested.connect(lambda draft: self.start(page, draft))
        page.saveRequested.connect(lambda draft: self.save(page, draft))
        page.cancelRequested.connect(lambda: self.cancel(page))
        page.focusRequested.connect(lambda enabled: window.focus_workspace(page, enabled))
        page.titleChanged.connect(lambda *_: self.update_title(page))
        window.tabs.addTab(page, tool_icon('automation'), page.title)
        self.set_available(page)
        if entry:
            window.store.touch_batch_job(job_id)
            page.load_history(window.store.batch_job_history(job_id))
        window.tabs.setCurrentWidget(page); self.update_title(page)
        return page

    def set_available(self, page):
        entries = []
        for entry in self.window.store.mongo_pipelines():
            config = self.window.configs.get(entry.get('connection_id'))
            if config is not None and config.driver == 'mongodb':
                entries.append(dict(entry, connection_name=config.name, database=entry['schema']))
        page.set_available_pipelines(entries)

    def update_title(self, page):
        if self.alive(page):
            self.window.tabs.setTabText(self.window.tabs.indexOf(page), page.title)
            if self.window.tabs.currentWidget() is page:
                self.window.setWindowTitle(page.title)

    def alive(self, page):
        return isValid(page) and not self.window._closed and self.window.tabs.indexOf(page) >= 0

    def save(self, page, definition):
        try:
            entry = self.window.store.save_batch_job(page.name, to_nbatpremium(definition), job_id=page.job_id)
            page.mark_saved(definition, job_id=entry['id'], name=entry['name'])
            pending = self.unsaved_history.get(id(page), [])
            while pending:
                result = pending[0]
                self.window.store.record_batch_job_run(entry['id'], result)
                pending.pop(0)
            self.unsaved_history.pop(id(page), None)
            page.set_history_pending(False)
            page.load_history(self.window.store.batch_job_history(entry['id']))
            self.refresh_list(); self.update_title(page)
            return entry
        except (DatabaseError, ValueError, OSError, sqlite3.Error) as error:
            page.set_history_pending(bool(self.unsaved_history.get(id(page))))
            page.show_error(error); return None

    def delete_job(self, job_id):
        entry = self.window.store.batch_job(job_id)
        if entry is None:
            return
        if any(isinstance(self.window.tabs.widget(i), BatchJobPage)
               and self.window.tabs.widget(i).job_id == job_id for i in range(self.window.tabs.count())):
            self.window.error(DatabaseError('Close this Batch Job before deleting its local definition')); return
        dialog = QDialog(self.window); dialog.setWindowTitle('删除批处理作业')
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f'你确定要删除 "{entry["name"]}" 吗？'))
        permanent = QCheckBox('我了解此操作是永久性的且无法撤消'); layout.addWidget(permanent)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        delete = buttons.button(QDialogButtonBox.StandardButton.Ok); delete.setText('删除'); delete.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        permanent.toggled.connect(delete.setEnabled)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not permanent.isChecked():
            return
        self.window.store.delete_batch_job(job_id); self.refresh_list()

    def start(self, page, definition):
        from opennavicat.batch_job_runtime import BatchRunControl, PreparedPipelineJob, run_batch_job

        if not self.alive(page) or page.running or self.window._closing_mongo:
            return
        if len(self.unsaved_history.get(id(page), [])) >= 200:
            page.show_error(DatabaseError('Save the pending execution history before running again'))
            return
        try:
            require_executable(definition)
        except DatabaseError as error:
            page.show_error(error); return
        window = self.window
        profiles, pipelines = list(window.configs.values()), window.store.mongo_pipelines()
        prepared, writes = [], False
        for reference in definition.jobs:
            try:
                resolved = resolve_pipeline_reference(reference, profiles, pipelines)
                if window.store.state.get('navicat_imports', {}).get(resolved.connection_id, {}).get('blocked'):
                    raise DatabaseError('This database connection requires settings review')
                config = window.store.hydrate(window.configs[resolved.connection_id])
                saved = from_nagg(resolved.nagg)
                writes = writes or any(stage.enabled and stage.operator in WRITE_STAGES for stage in saved.stages)
                prepared.append(PreparedPipelineJob(deepcopy(reference), resolved, deepcopy(config)))
            except (DatabaseError, ValueError, KeyError):
                prepared.append(PreparedPipelineJob(deepcopy(reference), error='The saved pipeline reference is missing, ambiguous or unavailable'))
        if writes and QMessageBox.question(window, '运行批处理作业',
                '已保存的聚合管道包含 $out 或 $merge，将修改目标数据库。是否运行？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        definition = deepcopy(definition)
        control = BatchRunControl()
        pending, lock = deque(), threading.Lock()
        state = dict(page=page, control=control, pending=pending, lock=lock,
                     cancel_inflight=False, last_cancel=0.0)
        timer = state['timer'] = QTimer(self); timer.setInterval(100)
        self.runs[id(page)] = state
        def emit(event):
            with lock:
                pending.append(event)
        def poll():
            self._poll(state)
        timer.timeout.connect(poll); timer.start(); page.set_running(True)
        def done(result):
            poll()
            if not self.alive(page):
                return
            try:
                if definition.general.get('EnableLogging'):
                    if page.job_id is not None:
                        result = window.store.record_batch_job_run(page.job_id, result)
                        page.load_history(window.store.batch_job_history(page.job_id))
                    else:
                        history = self.unsaved_history.setdefault(id(page), [])
                        history.append(deepcopy(result))
                        page.set_history_pending(True)
                        page.load_history(history)
            except (OSError, ValueError, sqlite3.Error):
                if 'execution_id' not in result:
                    pending_history = self.unsaved_history.setdefault(id(page), [])
                    pending_history.append(deepcopy(result))
                    page.set_history_pending(True)
                page.update_result(result)
                page.show_error(DatabaseError('Batch execution finished, but local history could not be saved; do not rerun completed work automatically'))
                return
            page.update_result(result)
        def failed(_error):
            poll()
            if self.alive(page):
                page.show_error(DatabaseError('Batch Job execution failed; the saved definition is unchanged'))
        def finish():
            timer.stop(); poll(); timer.deleteLater(); self.runs.pop(id(page), None)
            if self.alive(page):
                page.set_running(False)
            window.refresh_actions()
        window.submit(lambda: run_batch_job(definition, prepared, control=control, emit=emit),
                      done, failed, label='运行批处理作业', finished=finish)

    def _poll(self, state):
        page = state['page']
        with state['lock']:
            events = list(state['pending']); state['pending'].clear()
        if self.alive(page):
            for event in events:
                page.append_log(event)
        if state['control'].cancelled and not state['cancel_inflight'] and time.monotonic() - state['last_cancel'] >= .3:
            target = state['control'].cancellation_target()
            if target is not None:
                session, marker = target
                state['cancel_inflight'] = True; state['last_cancel'] = time.monotonic()
                def finish_cancel():
                    state['cancel_inflight'] = False
                self.window.submit(lambda: session.cancel(expected_comment=marker),
                                   failure=lambda _error: None, label='正在取消批处理工作',
                                   finished=finish_cancel, pool=self.window.cancel_pool)

    def cancel(self, page=None):
        for key, state in tuple(self.runs.items()):
            if page is None or key == id(page):
                state['control'].request_cancel(); self._poll(state)

    def can_discard(self, page):
        if page.running:
            return False
        if not page.dirty and not self.unsaved_history.get(id(page)):
            return True
        answer = QMessageBox.question(self.window, '批处理作业未保存', '保存批处理作业的更改？',
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Discard:
            return True
        if answer == QMessageBox.StandardButton.Save:
            page.request_save()
            return not page.dirty and not self.unsaved_history.get(id(page))
        return False
