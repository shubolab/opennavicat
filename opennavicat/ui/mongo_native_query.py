"""Independent native-JS query component; main-window routing is not enabled yet.

The existing QueryPage continues to serve Canonical EJSON/legacy command files.
An integrating window explicitly chooses this component for JavaScript scripts.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem
from shiboken6 import isValid

from ..database import DatabaseError
from ..js_engine.payload import load_engine
from ..js_engine.presentation import present_execution
from ..js_engine.runtime import ExecutionLimits, JavaScriptError
from .query import QueryPage


@dataclass(frozen=True)
class _Run:
    connection_id: str
    database: str
    source: str
    limits: ExecutionLimits
    cancelled: threading.Event


class NativeMongoQueryPage(QueryPage):
    """Capture GUI state once; every engine/file/DB action runs on owner.submit."""

    query_language = 'mongodb-javascript'

    def __init__(self, window, connection_id, sql='', *, engine_factory=load_engine):
        if window.configs[connection_id].driver != 'mongodb':
            raise ValueError('A native MongoDB JavaScript page requires a MongoDB connection')
        self._job = None
        self._engine_factory = engine_factory
        self.last_execution = None
        super().__init__(window, connection_id, sql)
        self.run_button.clicked.disconnect()
        self.stop_button.clicked.disconnect()
        self.editor.execute_requested.disconnect()
        self.run_button.clicked.connect(self.run_native)
        self.stop_button.clicked.connect(self.request_cancel)
        self.editor.execute_requested.connect(self.run_native)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.connection_label.setTextFormat(Qt.TextFormat.PlainText)
        self.destroyed.connect(lambda: self._cancel_destroyed())

    def _cancel_destroyed(self):
        # Python-only state: even forced Qt deletion must not leave a live
        # child running. The serial task retains cleanup ownership to terminal.
        if self._job is not None:
            self._job.cancelled.set()

    def _apply_protocol_mode(self):
        super()._apply_protocol_mode()
        self.editor.set_language('text')
        self.editor.setPlaceholderText('MongoDB JavaScript：变量、函数、循环与 db.getCollection(...).find(...)；原生 helper 开发资格，完整 Navicat 全局/API 对齐仍在进行。')
        self.run_button.setToolTip('运行 MongoDB JavaScript（⌘ / Ctrl + Enter）')

    def refresh_label(self):
        super().refresh_label()
        # A JS document cannot silently switch its execution language to SQL.
        self.connection_picker.blockSignals(True)
        for index in range(self.connection_picker.count()-1, -1, -1):
            config = self.window.configs.get(self.connection_picker.itemData(index))
            if config is None or config.driver != 'mongodb':
                self.connection_picker.removeItem(index)
        self.connection_picker.setCurrentIndex(self.connection_picker.findData(self.connection_id))
        self.connection_picker.blockSignals(False)

    def run_native(self):
        if self.running:
            return False
        if self.window.tasks:
            self.message.setText('请等待当前操作完成。')
            return False
        if self.connection_id not in self.window.sessions:
            connect = getattr(self.window, 'connect_id', None)
            if callable(connect):
                connect(self.connection_id, self.run_native)
            else:
                self.show_error(DatabaseError('Connect the MongoDB profile before running JavaScript'))
            return False
        source = self.editor.selected_sql()
        if not source.strip():
            return False
        session = self.window.sessions[self.connection_id]
        if session.config.driver != 'mongodb':
            self.show_error(DatabaseError('The selected session is not MongoDB'))
            return False
        job = _Run(self.connection_id, self.schema, source,
            ExecutionLimits(timeout_ms=120000, max_rows=self.limit.value()), threading.Event())
        factory = self._engine_factory
        self._job = job
        self.last_execution = None
        self.running = True
        self.message.setText('正在运行 MongoDB JavaScript…')

        def work():
            # No widget/window reads inside the serial worker. Source, limits,
            # database and the exact connected session were captured above.
            try:
                session.use_schema(job.database)
                execution = factory().execute(session, job.source, database=job.database,
                    limits=job.limits, cancel_event=job.cancelled)
            except JavaScriptError as error:
                execution = error.result
            return execution, present_execution(execution, job.source)

        def done(reply):
            if not isValid(self) or self._job is not job:
                return
            execution, presentation = reply
            self.last_execution = execution
            self.show_results([presentation.result])
            self.info.setPlainText(presentation.messages)
            self.summary.clearContents()
            self.summary.setColumnCount(6)
            self.summary.setHorizontalHeaderLabels(['命令', '操作', '读/写', '确认状态', '受影响数量', '错误'])
            self.summary.setRowCount(len(presentation.summary))
            for row, values in enumerate(presentation.summary):
                for column, value in enumerate(values):
                    self.summary.setItem(row, column, QTableWidgetItem(value))
            self.message.setText(execution.error or presentation.completion)
            self.message.setToolTip(execution.error or presentation.completion)
            if execution.error:
                self.results.setCurrentWidget(self.info)
            record = getattr(getattr(self.window, 'store', None), 'record', None)
            if callable(record):
                record(session.config, job.source, execution.elapsed_ms, 'error' if execution.error else 'success', query_language=self.query_language)

        def failed(error):
            if isValid(self) and self._job is job:
                self.show_error(error, sql=job.source)

        def finish():
            if isValid(self) and self._job is job:
                self._job = None
                self.running = False
                refresh = getattr(self.window, 'refresh_actions', None)
                if callable(refresh):
                    refresh()

        self.window.submit(work, done, failed, '执行 MongoDB JavaScript', finish)
        return True

    def request_cancel(self):
        if self._job is None:
            return False
        # No queued session.cancel call: this token belongs to this exact run.
        # The watchdog captures the active Mongo nonce before its control work.
        self._job.cancelled.set()
        self.message.setText('正在停止；已确认的写入会保留…')
        return True
