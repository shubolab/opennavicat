"""Detached native batch-job editor; the window owns all I/O and execution."""
from __future__ import annotations

from collections import deque
from copy import deepcopy

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from opennavicat.batch_jobs import (
    new_batch_job, new_pipeline_reference, validate_definition,
)
from opennavicat.database import DatabaseError

from .icons import tool_icon, toggle_icon


JOB_TYPES = (
    ('数据传输', 'datatransfer'), ('数据生成', 'datageneration'),
    ('数据同步', 'datasynchronization'), ('数据字典', 'datadictionary'),
    ('数据脱敏', 'datamasking'), ('备份', 'backup'), ('导出', 'export'),
    ('导入', 'import'), ('查询', 'query'), ('聚合管道', 'aggregationpipeline'),
    ('MongoDump', 'mongodump'), ('MongoExport', 'mongoexport'),
    ('MongoImport', 'mongoimport'), ('MapReduce', 'mapreduce'),
    ('模型', 'model'), ('BI', 'bi'),
)
TYPE_LABELS = dict((value, label) for label, value in JOB_TYPES)
SELECTED_COLUMNS = (
    '已选择的工作', '类型', '附加到电子邮件', '连接类型', '连接',
    '云名称', '项目名称', '项目所有者用户 ID', '数据库', '模式',
)
HISTORY_COLUMNS = ('执行编号', '开始时间', '结束时间', '期间', '执行', '结果')
MAX_AVAILABLE_PIPELINES = 2000
MAX_HISTORY_ROWS = 1000
MAX_LOG_CHARS = 1024 * 1024
MAX_LOG_LINE_CHARS = 16384
MAX_HISTORY_LOG_CHARS = 4 * MAX_LOG_CHARS


def _text(value, limit=512):
    value = '' if value is None else str(value)
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _item(value):
    item = QTableWidgetItem(_text(value))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _read_only_log():
    editor = QPlainTextEdit()
    editor.setReadOnly(True)
    editor.setMaximumBlockCount(5000)
    return editor


def _log_line(event):
    if not isinstance(event, dict):
        return _text(event, MAX_LOG_LINE_CHARS)
    stamp = _text(event.get('timestamp'), 80)
    status = _text(event.get('status', event.get('kind', '')), 80)
    index = event.get('job_index')
    prefix = f'[{stamp}] ' if stamp else ''
    if status:
        prefix += f'[{status}] '
    if type(index) is int:
        prefix += f'#{index + 1} '
    name = _text(event.get('name'), 512)
    if name:
        prefix += name + ': '
    return _text(prefix + _text(event.get('message'), MAX_LOG_LINE_CHARS), MAX_LOG_LINE_CHARS)


def _history_text(events, budget):
    if budget <= 0:
        return '历史日志超出显示上限。'
    if not isinstance(events, list):
        return _text(events, min(budget, MAX_LOG_CHARS))
    lines = deque()
    size = 0
    clipped = len(events) > 5000
    for event in events[-5000:]:
        line = _log_line(event)
        lines.append(line)
        size += len(line) + 1
        while size > min(budget, MAX_LOG_CHARS) and lines:
            size -= len(lines.popleft()) + 1
            clipped = True
    return ('较早日志已超出显示上限。\n' if clipped else '') + '\n'.join(lines)


class BatchJobSaveDialog(QDialog):
    def __init__(self, name='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('保存')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(440, 130)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.name_edit.setObjectName('batchJobSaveName')
        self.name_edit.setMaxLength(1024)
        form.addRow('批处理作业名称:', self.name_edit)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('保存')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.save_button.setEnabled(bool(name.strip()))
        self.name_edit.textChanged.connect(lambda value: self.save_button.setEnabled(bool(value.strip())))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @property
    def name(self):
        return self.name_edit.text().strip()

    def accept(self):
        if self.name:
            super().accept()


class BatchJobPage(QWidget):
    titleChanged = Signal(str)
    dirtyChanged = Signal(bool)
    changed = Signal()
    startRequested = Signal(object)
    saveRequested = Signal(object)
    cancelRequested = Signal()
    focusRequested = Signal(bool)

    def __init__(self, name='', parent=None):
        super().__init__(parent)
        self.setObjectName('batchJobPage')
        self.kind = 'batch_job'
        self.name = name
        self.job_id = None
        self.definition = new_batch_job()
        self.dirty = False
        self.running = False
        self.loading = False
        self.history_pending = False
        self._available = []
        self._visible_available = []
        self._history = []
        self._log_lines = deque()
        self._log_chars = 0
        self._log_truncated = False
        self.last_result = None
        self.save_dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName('batchJobToolbar')
        layout.addWidget(self.toolbar)
        self.start_action = self._action('开始', 'run', self.request_start)
        self.save_action = self._action('保存', 'save', self.request_save)
        self.schedule_action = self._action('设置任务计划', 'automation', None)
        self.remove_schedule_action = self._action('删除任务计划', 'delete', None)
        for action in (self.schedule_action, self.remove_schedule_action):
            action.setEnabled(False)
            action.setToolTip('操作系统任务计划尚未接通')
        self.add_action = self._action('添加工作', 'add', self.add_selected_jobs)
        self.add_all_action = self._action('添加所有工作', 'add', self.add_all_jobs)
        self.remove_action = self._action('移除工作', 'delete', self.remove_selected_jobs)
        self.remove_all_action = self._action('移除所有工作', 'delete', self.remove_all_jobs)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.focus_action = self._action('专注模式', 'focus', None)
        self.focus_action.setIcon(toggle_icon('focus', 18))
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.general_page = QWidget()
        self._build_general()
        self.tabs.addTab(self.general_page, '常规')
        self.advanced_page = QWidget()
        self._build_advanced()
        self.tabs.addTab(self.advanced_page, '高级')
        self.messages = _read_only_log()
        self.messages.setObjectName('batchJobMessages')
        self.tabs.addTab(self.messages, '消息日志')
        self.history_page = QWidget()
        self._build_history()
        self.tabs.addTab(self.history_page, '历史')
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        self.load_definition(self.definition)
        self._render_available()

    @property
    def title(self):
        return _text(self.name or '无标题批处理作业', 128) + (' — 已编辑' if self.dirty else '')

    def _assert_gui_thread(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError('Batch-job widgets must be accessed on their GUI thread')

    def _action(self, text, icon, callback):
        action = QAction(tool_icon(icon), text, self)
        if callback is not None:
            action.triggered.connect(callback)
        self.toolbar.addAction(action)
        return action

    def _build_general(self):
        layout = QVBoxLayout(self.general_page)
        split = QSplitter(Qt.Orientation.Vertical)
        self.jobs_table = QTableWidget(0, len(SELECTED_COLUMNS))
        self.jobs_table.setObjectName('batchJobSelectedJobs')
        self.jobs_table.setHorizontalHeaderLabels(SELECTED_COLUMNS)
        self.jobs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.jobs_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.jobs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.jobs_table.verticalHeader().hide()
        self.jobs_table.setColumnWidth(0, 240)
        for column in range(1, len(SELECTED_COLUMNS)):
            self.jobs_table.setColumnWidth(column, 120)
        self.jobs_table.itemSelectionChanged.connect(self._update_controls)
        split.addWidget(self.jobs_table)
        self.available_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.type_tree = QTreeWidget()
        self.type_tree.setObjectName('batchJobTypes')
        self.type_tree.setHeaderLabel('类型')
        self.type_tree.setRootIsDecorated(False)
        for label, kind in JOB_TYPES:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, kind)
            self.type_tree.addTopLevelItem(item)
        self.type_tree.setCurrentItem(self.type_tree.topLevelItem(0))
        self.type_tree.itemSelectionChanged.connect(self._render_available)
        self.connection_tree = QTreeWidget()
        self.connection_tree.setObjectName('batchJobConnections')
        self.connection_tree.setHeaderLabel('连接')
        self.connection_tree.itemSelectionChanged.connect(self._render_available)
        self.available_table = QTableWidget(0, 2)
        self.available_table.setObjectName('batchJobAvailableObjects')
        self.available_table.setHorizontalHeaderLabels(['名称', '类型'])
        self.available_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.available_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.available_table.verticalHeader().hide()
        self.available_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.available_table.itemSelectionChanged.connect(self._update_controls)
        self.available_table.itemDoubleClicked.connect(lambda *_: self.add_selected_jobs())
        for widget in (self.type_tree, self.connection_tree, self.available_table):
            self.available_splitter.addWidget(widget)
        self.available_splitter.setStretchFactor(2, 2)
        split.addWidget(self.available_splitter)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        self.availability_notice = QLabel()
        self.availability_notice.setWordWrap(True)
        self.availability_notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.availability_notice)

    def _build_advanced(self):
        outer = QVBoxLayout(self.advanced_page)
        self.advanced_scroll = QScrollArea()
        self.advanced_scroll.setWidgetResizable(True)
        content = QWidget()
        self.advanced_scroll.setWidget(content)
        outer.addWidget(self.advanced_scroll)
        layout = QVBoxLayout(content)
        common = QGroupBox('常规')
        common_layout = QVBoxLayout(common)
        self.continue_on_error_check = QCheckBox('遇到错误时继续')
        self.logging_check = QCheckBox('启用批处理作业历史记录')
        common_layout.addWidget(self.continue_on_error_check)
        common_layout.addWidget(self.logging_check)
        self.continue_on_error_check.toggled.connect(self._advanced_changed)
        self.logging_check.toggled.connect(self._advanced_changed)
        layout.addWidget(common)
        self.email_group = QGroupBox('电子邮箱')
        email_layout = QVBoxLayout(self.email_group)
        self.send_email_check = QCheckBox('发送邮件')
        email_layout.addWidget(self.send_email_check)
        when = QHBoxLayout()
        when.addWidget(QLabel('当:'))
        self.email_success_check = QCheckBox('成功')
        self.email_fail_check = QCheckBox('失败')
        when.addWidget(self.email_success_check)
        when.addWidget(self.email_fail_check)
        when.addStretch()
        email_layout.addLayout(when)
        form = QFormLayout()
        self.email_fields = {}
        fields = (
            ('EmailFrom', '发件人:'), ('EmailTo', '收件人:'), ('EmailCC', '副本:'),
            ('EmailSubject', '主旨:'), ('EmailBody', '主体:'),
        )
        for key, label in fields:
            edit = QPlainTextEdit() if key == 'EmailBody' else QLineEdit()
            if key == 'EmailBody':
                edit.setMaximumHeight(70)
            self.email_fields[key] = edit
            form.addRow(label, edit)
        email_layout.addLayout(form)
        legend = QLabel('%J  %F  %R  %L  %FirstFailedJobName  %SuccessfulJobCount  %FailedJobCount  %ProcessedJobCount')
        legend.setWordWrap(True)
        email_layout.addWidget(legend)
        server_form = QFormLayout()
        for key, label in (('EmailServerHost', '主机（SMTP 服务器）:'), ('EmailServerPort', '端口:')):
            edit = QLineEdit()
            self.email_fields[key] = edit
            server_form.addRow(label, edit)
        self.secure_combo = QComboBox()
        self.secure_combo.addItems(['永不', 'TLS', 'SSL'])
        server_form.addRow('安全连接:', self.secure_combo)
        self.authentication_check = QCheckBox('使用验证')
        server_form.addRow(self.authentication_check)
        self.email_user_edit = QLineEdit()
        self.email_password_edit = QLineEdit()
        self.email_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        server_form.addRow('用户名:', self.email_user_edit)
        server_form.addRow('密码:', self.email_password_edit)
        self.test_email_button = QPushButton('发送测试邮件')
        server_form.addRow(self.test_email_button)
        email_layout.addLayout(server_form)
        self.email_group.setEnabled(False)
        self.email_group.setToolTip('邮件发送尚未接通；保留已有设置，不保存或发送密码')
        layout.addWidget(self.email_group)
        notice = QLabel('邮件发送尚未接通；邮件设置仅显示，执行不会发送邮件。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        layout.addStretch()

    def _build_history(self):
        layout = QVBoxLayout(self.history_page)
        split = QSplitter(Qt.Orientation.Vertical)
        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setObjectName('batchJobHistory')
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.itemSelectionChanged.connect(self._show_history_log)
        split.addWidget(self.history_table)
        self.history_log = _read_only_log()
        self.history_log.setObjectName('batchJobHistoryLog')
        split.addWidget(self.history_log)
        layout.addWidget(split)
        self.history_notice = QLabel()
        self.history_notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.history_notice)

    def select_job_type(self, kind):
        self._assert_gui_thread()
        for index in range(self.type_tree.topLevelItemCount()):
            item = self.type_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == kind:
                self.type_tree.setCurrentItem(item)
                return

    def set_available_pipelines(self, entries):
        self._assert_gui_thread()
        entries = list(entries)
        normalized = []
        for entry in entries[:MAX_AVAILABLE_PIPELINES]:
            entry = deepcopy(entry)
            name = entry.get('name', entry.get('pipeline_name', entry.get('reference_name', '')))
            server = entry.get('connection_name', entry.get('server', ''))
            schema = entry.get('database', entry.get('schema', ''))
            if not all(isinstance(value, str) and value for value in (name, server, schema)):
                raise DatabaseError('Saved pipelines need a name, connection name and database')
            entry.update(name=name, connection_name=server, schema=schema)
            normalized.append(entry)
        self._available = normalized
        self._catalog_truncated = len(entries) > MAX_AVAILABLE_PIPELINES
        self.connection_tree.blockSignals(True)
        try:
            self.connection_tree.clear()
            connections = {}
            schemas = set()
            for entry in self._available:
                identity = entry.get('connection_id', entry['connection_name'])
                if identity not in connections:
                    connection = QTreeWidgetItem([_text(entry['connection_name'])])
                    connection.setData(0, Qt.ItemDataRole.UserRole, {'connection': identity})
                    self.connection_tree.addTopLevelItem(connection)
                    connections[identity] = connection
                key = (identity, entry['schema'])
                if key not in schemas:
                    database = QTreeWidgetItem([_text(entry['schema'])])
                    database.setData(0, Qt.ItemDataRole.UserRole, {'connection': identity, 'schema': entry['schema']})
                    connections[identity].addChild(database)
                    schemas.add(key)
            self.connection_tree.expandAll()
            if self.connection_tree.topLevelItemCount():
                self.connection_tree.setCurrentItem(self.connection_tree.topLevelItem(0))
        finally:
            self.connection_tree.blockSignals(False)
        self._render_available()

    def _render_available(self):
        if not hasattr(self, 'available_table'):
            return
        current = self.type_tree.currentItem()
        kind = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        connection = self.connection_tree.currentItem()
        binding = connection.data(0, Qt.ItemDataRole.UserRole) if connection is not None else {}
        binding = binding or {}
        self._visible_available = []
        if kind == 'aggregationpipeline':
            for entry in self._available:
                identity = entry.get('connection_id', entry['connection_name'])
                if ('connection' in binding and identity != binding['connection']
                        or 'schema' in binding and entry['schema'] != binding['schema']):
                    continue
                self._visible_available.append(entry)
        self.available_table.setRowCount(len(self._visible_available))
        for row, entry in enumerate(self._visible_available):
            self.available_table.setItem(row, 0, _item(entry['name']))
            self.available_table.setItem(row, 1, _item('聚合管道'))
        notice = '' if kind == 'aggregationpipeline' else '该工作类型尚未接通；本轮仅支持已保存的聚合管道。'
        if kind == 'aggregationpipeline' and not self._visible_available:
            notice = '当前选择没有已保存的聚合管道。'
        if getattr(self, '_catalog_truncated', False):
            notice += f' 仅显示前 {MAX_AVAILABLE_PIPELINES} 个对象。'
        self.availability_notice.setText(notice)
        self._update_controls()

    def _render_jobs(self):
        self.jobs_table.setRowCount(len(self.definition.jobs))
        for row, job in enumerate(self.definition.jobs):
            values = (
                job.reference_name, TYPE_LABELS.get(job.type_name, job.type_name), '',
                job.server_type, job.server, job.cloud_instance_id, job.project,
                job.project_owner_navicat_id, job.catalog, job.schema,
            )
            for column, value in enumerate(values):
                self.jobs_table.setItem(row, column, _item(value))
            attachment = self.jobs_table.item(row, 2)
            attachment.setCheckState(Qt.CheckState.Checked if job.use_attachment else Qt.CheckState.Unchecked)

    def _advanced_changed(self, *_):
        if self.loading or self.running:
            return
        self.definition.general['ContinueOnError'] = self.continue_on_error_check.isChecked()
        self.definition.general['EnableLogging'] = self.logging_check.isChecked()
        self._set_dirty(True)

    def _set_dirty(self, value):
        changed = self.dirty != bool(value)
        self.dirty = bool(value)
        if changed:
            self.dirtyChanged.emit(self.dirty)
        if value:
            self.changed.emit()
        self._update_controls()

    def load_definition(self, definition, job_id=None, name=None):
        self._assert_gui_thread()
        candidate = deepcopy(definition)
        validate_definition(candidate)
        self.loading = True
        try:
            self.definition = candidate
            self.job_id = job_id
            if name is not None:
                self.name = name
            self._render_jobs()
            general = candidate.general
            self.continue_on_error_check.setChecked(general.get('ContinueOnError', True))
            self.logging_check.setChecked(general.get('EnableLogging', True))
            self.send_email_check.setChecked(general.get('SendEmail', False))
            self.email_success_check.setChecked(general.get('SendEmailWhenSuccess', True))
            self.email_fail_check.setChecked(general.get('SendEmailWhenFail', True))
            for key, edit in self.email_fields.items():
                value = _text(general.get(key, ''), 16384)
                edit.setPlainText(value) if isinstance(edit, QPlainTextEdit) else edit.setText(value)
            self.email_user_edit.setText(_text(general.get('EmailServerUserName', '')))
            self.email_password_edit.clear()
            self.authentication_check.setChecked(bool(general.get('UseAuthentication', False)))
            index = self.secure_combo.findText(str(general.get('SecureMethod', '')))
            self.secure_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.loading = False
        self._set_dirty(False)

    def draft(self):
        self._assert_gui_thread()
        candidate = deepcopy(self.definition)
        validate_definition(candidate)
        return candidate

    def mark_saved(self, definition, job_id=None, name=None):
        self.load_definition(definition, job_id=job_id, name=name)

    def _append_entries(self, entries):
        if self.running:
            return
        jobs = [new_pipeline_reference(entry['connection_name'], entry['schema'], entry['name']) for entry in entries]
        if not jobs:
            return
        candidate = self.draft()
        candidate.jobs.extend(jobs)
        validate_definition(candidate)
        self.definition = candidate
        self._render_jobs()
        self._set_dirty(True)

    def add_selected_jobs(self):
        self._assert_gui_thread()
        rows = sorted({index.row() for index in self.available_table.selectionModel().selectedRows()})
        try:
            self._append_entries([self._visible_available[row] for row in rows])
        except DatabaseError as error:
            self.show_error(error)

    def add_all_jobs(self):
        self._assert_gui_thread()
        try:
            self._append_entries(self._visible_available)
        except DatabaseError as error:
            self.show_error(error)

    def remove_selected_jobs(self):
        self._assert_gui_thread()
        if self.running:
            return
        rows = {index.row() for index in self.jobs_table.selectionModel().selectedRows()}
        if rows:
            self.definition.jobs = [job for index, job in enumerate(self.definition.jobs) if index not in rows]
            self._render_jobs()
            self._set_dirty(True)

    def remove_all_jobs(self):
        self._assert_gui_thread()
        if not self.running and self.definition.jobs:
            self.definition.jobs.clear()
            self._render_jobs()
            self._set_dirty(True)

    def request_start(self):
        self._assert_gui_thread()
        if self.running or not self.definition.jobs:
            return
        try:
            self.startRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_cancel(self):
        self._assert_gui_thread()
        if self.running:
            self.cancelRequested.emit()

    def request_save(self):
        self._assert_gui_thread()
        if self.running:
            return
        try:
            definition = self.draft()
        except DatabaseError as error:
            self.show_error(error)
            return
        if self.job_id is None:
            dialog = BatchJobSaveDialog(self.name, self)
            self.save_dialog = dialog
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.name = dialog.name
            self._update_controls()
        self.saveRequested.emit(definition)

    def show_error(self, error):
        self._assert_gui_thread()
        self.append_log({'status': 'failed', 'message': str(error)})
        self.tabs.setCurrentWidget(self.messages)

    def set_running(self, value):
        self._assert_gui_thread()
        if value and not self.running:
            self._log_lines.clear()
            self._log_chars = 0
            self._log_truncated = False
            self.messages.clear()
            self.status_label.clear()
            self.last_result = None
            self.tabs.setCurrentWidget(self.messages)
        self.running = bool(value)
        self._update_controls()

    def set_history_pending(self, value):
        self._assert_gui_thread()
        self.history_pending = bool(value)
        self.save_action.setToolTip('保存批处理作业和待保存运行记录' if self.history_pending else '保存批处理作业')
        self._update_controls()

    def append_log(self, event):
        self._assert_gui_thread()
        line = _log_line(event)
        self._log_lines.append(line)
        self._log_chars += len(line) + 1
        trimmed = False
        while self._log_chars > MAX_LOG_CHARS and self._log_lines:
            self._log_chars -= len(self._log_lines.popleft()) + 1
            self._log_truncated = True
            trimmed = True
        if trimmed:
            self.messages.setPlainText('较早日志已超出显示上限。\n' + '\n'.join(self._log_lines))
        else:
            self.messages.appendPlainText(line)
        self.messages.verticalScrollBar().setValue(self.messages.verticalScrollBar().maximum())

    def update_result(self, result):
        self._assert_gui_thread()
        self.last_result = deepcopy(result)
        counts = ' / '.join(f'{label}: {_text(result.get(key, 0), 30)}' for label, key in (
            ('Total', 'total'), ('Success', 'success'), ('Fail', 'failed'), ('Skip', 'skipped'), ('Cancelled', 'cancelled')))
        self.append_log({'status': result.get('status', ''), 'message': counts})
        self.status_label.setText(counts)
        self.set_running(False)

    def load_history(self, records):
        self._assert_gui_thread()
        records = list(records)
        self._history = []
        budget = MAX_HISTORY_LOG_CHARS
        for record in records[:MAX_HISTORY_ROWS]:
            item = {key: deepcopy(record.get(key)) for key in
                    ('execution_id', 'start_time', 'end_time', 'duration_ms', 'execute', 'status')}
            item['display_log'] = _history_text(record.get('logs', []), budget)
            budget -= len(item['display_log'])
            self._history.append(item)
        self.history_table.setRowCount(len(self._history))
        for row, record in enumerate(self._history):
            duration = record.get('duration_ms')
            duration = f'{duration:.3f} ms' if isinstance(duration, (int, float)) else duration
            values = (record.get('execution_id', ''), record.get('start_time', ''), record.get('end_time', ''),
                      duration, record.get('execute', 'Manual'), record.get('status', ''))
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, _item(value))
        self.history_log.clear()
        self.history_notice.setText(f'仅显示前 {MAX_HISTORY_ROWS} 条历史。' if len(records) > MAX_HISTORY_ROWS else '')

    def _show_history_log(self):
        row = self.history_table.currentRow()
        if not 0 <= row < len(self._history):
            self.history_log.clear()
            return
        self.history_log.setPlainText(self._history[row]['display_log'])

    def _update_controls(self):
        if not hasattr(self, 'status_label'):
            return
        editable = not self.running and not self.loading
        self.start_action.setEnabled(editable and bool(self.definition.jobs))
        self.save_action.setEnabled(editable and (self.dirty or self.job_id is None or self.history_pending))
        self.add_action.setEnabled(editable and bool(self.available_table.selectionModel().selectedRows()))
        self.add_all_action.setEnabled(editable and bool(self._visible_available))
        self.remove_action.setEnabled(editable and bool(self.jobs_table.selectionModel().selectedRows()))
        self.remove_all_action.setEnabled(editable and bool(self.definition.jobs))
        self.jobs_table.setEnabled(editable)
        self.available_splitter.setEnabled(editable)
        self.continue_on_error_check.setEnabled(editable)
        self.logging_check.setEnabled(editable)
        self.titleChanged.emit(self.title)
