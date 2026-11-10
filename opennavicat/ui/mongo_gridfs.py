"""GridFS workspace. All service and file I/O belongs to the owner's workers.

An upload creates a new GridFS version. Downloads use the adapter's exclusive
atomic destination commit. Preview is intentionally restricted to small images,
matching the observed reference workflow; it never opens remote files in an app.
"""
from __future__ import annotations

from copy import deepcopy
from html import escape
from pathlib import Path
import re
import tempfile
import threading
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from ..database import DatabaseError
from .grid import DataGrid
from .icons import tool_icon
from .mongo_browser import MongoDocumentModel


_FILE_COLUMNS = ("filename", "_id", "length", "chunkSize", "uploadDate", "md5", "contentType", "aliases", "metadata")
_FILE_LABELS = ("名称", "ID", "长度", "数据块大小", "上传日期", "MD5", "内容类型", "别名", "元数据")
_PREVIEW_MAX_BYTES = 1_000_000


def _safe_filename(value):
    """Remote metadata must never choose a destination outside the picked folder."""
    name = re.split(r"[/\\]", str(value or ""))[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', "_", name).rstrip(" .")
    if not name or name in {".", ".."}:
        return "gridfs.bin"
    if name.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        name = "_" + name
    return name


class GridFSFileModel(MongoDocumentModel):
    def set_documents(self, documents):
        self.beginResetModel()
        self.documents = list(documents)
        self.columns = list(_FILE_COLUMNS)
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _FILE_LABELS[section]
        return super().headerData(section, orientation, role)


class _TransferSignals(QObject):
    # Python integers retain exact lengths beyond Qt's 32-bit Signal(int).
    progressed = Signal(object, object, object)


class MongoGridFSPage(QWidget):
    dirtyChanged = Signal(bool)
    titleChanged = Signal(str)
    focusRequested = Signal(bool)

    def __init__(self, window, connection_id, database, bucket):
        super().__init__()
        self.window, self.connection_id = window, connection_id
        self.database = self.schema = database
        self.bucket = self.table = bucket
        self.kind = "mongo_gridfs"
        self.loading = self.running = self._closed = False
        self._request = 0
        self._active_transfer = None
        self._operation_cancelled = None
        self._cancelling = False
        self.offset = 0
        self.page = None
        self._filter = {}
        self._filter_rows = []
        self._transfers = []
        self._preview_image = None
        self._setting_page = False
        self.destroyed.connect(lambda: setattr(self, "_closed", True))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar()
        self.toolbar.setObjectName("objectToolbar")
        self.toolbar.setFixedHeight(36)
        self.toolbar.setIconSize(QSize(18, 18))
        layout.addWidget(self.toolbar)
        self.upload_action = self._action("上传文件", "import", self.upload_file)
        self.open_action = self._action("打开文件（外部应用打开尚未实现）", "open")
        self.open_action.setEnabled(False)
        self.delete_action = self._action("删除文件", "delete", self.delete_file)
        self.download_action = self._action("下载文件", "export", self.download_file)
        self.toolbar.addSeparator()
        self.filter_action = self._action("筛选", "filter")
        self.filter_action.setCheckable(True)
        self.preview_action = self._action("预览", "view")
        self.preview_action.setCheckable(True)
        self.progress_action = self._action("进度", "history")
        self.progress_action.setCheckable(True)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(stretch)
        self.focus_action = self._action("专注模式", "design")
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)

        self.filter_bar = QWidget()
        filters = QVBoxLayout(self.filter_bar)
        filters.setContentsMargins(8, 5, 8, 5)
        self.filter_fields = QGridLayout()
        filters.addLayout(self.filter_fields)
        controls = QHBoxLayout()
        self.filter_add_button = QPushButton("＋")
        self.filter_add_button.setFixedWidth(28)
        self.filter_add_button.setToolTip("添加名称条件；所有启用条件须同时满足。")
        self.filter_add_button.clicked.connect(self.add_filter)
        controls.addWidget(self.filter_add_button)
        controls.addStretch()
        self.filter_apply_button = QPushButton("应用")
        self.filter_apply_button.clicked.connect(self.apply_filter)
        self.filter_clear_button = QPushButton("清除")
        self.filter_clear_button.clicked.connect(self.clear_filter)
        controls.addWidget(self.filter_apply_button)
        controls.addWidget(self.filter_clear_button)
        filters.addLayout(controls)
        self.add_filter()
        self.filter_bar.hide()
        self.filter_action.toggled.connect(self.filter_bar.setVisible)
        layout.addWidget(self.filter_bar)

        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.grid = DataGrid(row_selectors=False)
        self.model = GridFSFileModel(self.grid)
        self.model.set_documents([])
        self.grid.setModel(self.model)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.verticalHeader().hide()
        self.grid.horizontalHeader().setDefaultSectionSize(110)
        self.grid.setColumnWidth(0, 200)
        self.grid.setColumnWidth(1, 190)
        self.grid.setColumnWidth(4, 195)
        self.grid.selectionModel().currentRowChanged.connect(self._selection_changed)
        self.splitter.addWidget(self.grid)
        self.preview_panel = QWidget()
        preview = QVBoxLayout(self.preview_panel)
        self.preview_label = QLabel("选择文件以预览。")
        self.preview_label.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setMinimumSize(240, 180)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        preview.addWidget(self.preview_label, 1)
        self.preview_download_button = QPushButton("保存到磁盘")
        self.preview_download_button.clicked.connect(self.download_file)
        preview.addWidget(self.preview_download_button)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 1)
        self.preview_panel.hide()
        self.preview_action.toggled.connect(self._toggle_preview)
        layout.addWidget(self.splitter, 1)

        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(5, 3, 5, 3)
        self.progress_table = QTableWidget(0, 4)
        self.progress_table.setHorizontalHeaderLabels(["名称", "操作", "进度", "状态"])
        self.progress_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.progress_table.verticalHeader().hide()
        self.progress_table.horizontalHeader().setStretchLastSection(True)
        self.progress_table.setColumnWidth(0, 230)
        self.progress_table.setColumnWidth(2, 210)
        self.progress_table.setMaximumHeight(165)
        progress_layout.addWidget(self.progress_table)
        progress_controls = QHBoxLayout()
        self.progress_summary = QLabel("总计 0 · 完成 0 · 失败 0")
        progress_controls.addWidget(self.progress_summary, 1)
        self.cancel_button = QPushButton("取消传输")
        self.cancel_button.clicked.connect(self.cancel_transfer)
        progress_controls.addWidget(self.cancel_button)
        progress_layout.addLayout(progress_controls)
        self.progress_panel.hide()
        self.progress_action.toggled.connect(self.progress_panel.setVisible)
        layout.addWidget(self.progress_panel)

        self.footer = QWidget()
        self.footer.setObjectName("tableFooter")
        self.footer.setFixedHeight(28)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(6, 2, 6, 2)
        footer.setSpacing(3)
        self.delete_button = self._button(footer, "删除文件", "record_delete", self.delete_file)
        self.refresh_button = self._button(footer, "刷新", "refresh", self.refresh)
        self.status = QLabel("正在读取…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        footer.addWidget(self.status, 1)
        self.first_button = self._button(footer, "第一页", "first", lambda: self.go_page(0))
        self.previous_button = self._button(footer, "上一页", "previous", lambda: self.go_page(max(0, self.offset-self.page_size.value())))
        self.page_number = QSpinBox()
        self.page_number.setRange(1, 1)
        self.page_number.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_number.setFixedWidth(42)
        self.page_number.editingFinished.connect(lambda: self.go_page((self.page_number.value()-1)*self.page_size.value()))
        footer.addWidget(self.page_number)
        self.next_button = self._button(footer, "下一页", "next", lambda: self.go_page(self.page.next_offset))
        self.last_button = self._button(footer, "最后一页", "last", self.last_page)
        self.page_size = QSpinBox(self)
        self.page_size.setRange(1, 1000)
        self.page_size.setValue(100)
        self.page_size.setToolTip("每页文件数")
        self.page_size.editingFinished.connect(lambda: self.go_page(0))
        self.page_size.hide()
        self.settings_button = self._button(footer, "设置", "other", self.page_settings)
        layout.addWidget(self.footer)
        self._update_controls()
        QTimer.singleShot(0, self, self.refresh)

    @property
    def title(self):
        return f"{self.bucket} @ {self.database} - GridFS"

    @property
    def dirty(self):
        return False

    @property
    def read_only(self):
        session = self.window.sessions.get(self.connection_id)
        return session is None or bool(session.config.read_only)

    def current_result(self):
        return None

    def selected_file(self):
        index = self.grid.currentIndex()
        if self.page and index.isValid() and 0 <= index.row() < len(self.page.documents):
            return self.page.documents[index.row()]
        return None

    def _action(self, title, symbol, slot=None):
        action = QAction(tool_icon(symbol, 18), title, self)
        if slot:
            action.triggered.connect(slot)
        self.toolbar.addAction(action)
        return action

    @staticmethod
    def _button(layout, title, symbol, slot):
        button = QToolButton()
        button.setIcon(tool_icon(symbol, 16))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(title)
        button.setAutoRaise(True)
        button.setFixedSize(24, 24)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    def add_filter(self):
        if self.loading:
            return
        enabled, field, operation, text, remove = QCheckBox(), QComboBox(), QComboBox(), QLineEdit(), QPushButton("−")
        enabled.setChecked(True)
        field.addItem("名称", "filename")
        operation.addItem("包含", "contains")
        text.returnPressed.connect(self.apply_filter)
        remove.setFixedWidth(28)
        row = (enabled, field, operation, text, remove)
        remove.clicked.connect(lambda: self.remove_filter(row))
        self._filter_rows.append(row)
        self._layout_filters()

    def remove_filter(self, row):
        if self.loading or row not in self._filter_rows:
            return
        self._filter_rows.remove(row)
        for widget in row:
            self.filter_fields.removeWidget(widget)
            widget.deleteLater()
        if not self._filter_rows:
            self.add_filter()
        self._layout_filters()

    def _layout_filters(self):
        for index, row in enumerate(self._filter_rows):
            for column, widget in enumerate(row):
                self.filter_fields.addWidget(widget, index, column)
        self.filter_fields.setColumnStretch(3, 1)

    def apply_filter(self):
        if self.loading:
            return
        conditions = [{"filename": {"$regex": re.escape(text.text())}} for enabled, _, _, text, _ in self._filter_rows
                      if enabled.isChecked() and text.text()]
        self._filter = conditions[0] if len(conditions) == 1 else {"$and": conditions} if conditions else {}
        self.offset = 0
        self.refresh()

    def clear_filter(self):
        if self.loading:
            return
        for enabled, _, _, text, _ in self._filter_rows:
            enabled.setChecked(True)
            text.clear()
        self._filter = {}
        self.offset = 0
        self.refresh()

    def show_error(self, error):
        self.message.setText(str(error))
        self.message.show()

    def _submit(self, work, done, label, *, transfer=None, failed=None):
        if self.loading or self._closed or not isValid(self):
            return False
        session = self.window.sessions.get(self.connection_id)
        if session is None:
            self.show_error("连接已断开。")
            return False
        self._request += 1
        request = self._request
        binding = (self.database, self.bucket)
        self.loading = self.running = True
        cancelled = threading.Event()
        self._operation_cancelled = cancelled
        self._cancelling = False
        self._active_transfer = transfer
        self.message.clear()
        self.message.hide()
        signals = _TransferSignals()
        signals.progressed.connect(self._on_progress)
        self._progress_signals = signals
        last_emit = [0.0]

        def progress(transferred, total):
            now = time.monotonic()
            if transferred == total or now-last_emit[0] >= .04:
                last_emit[0] = now
                signals.progressed.emit(request, transferred, total)

        def alive():
            return isValid(self) and not self._closed and request == self._request

        def complete(value=None, error=None):
            if not alive():
                return
            self.loading = self.running = self._cancelling = False
            self._operation_cancelled = None
            self._active_transfer = None
            same = self.window.sessions.get(self.connection_id) is session and binding == (self.database, self.bucket)
            if transfer is not None:
                record = self._transfers[transfer]
                record["status"] = "失败" if error is not None else "完成"
                record["error"] = str(error) if error is not None else ""
                self._render_transfer(transfer)
            self._update_controls()
            if not same:
                self.show_error("连接已改变，操作已结束；请刷新后继续。")
                return
            if error is not None:
                self.show_error(error)
                if failed:
                    failed(error)
            else:
                done(value)

        def execute():
            # A queued transfer owns no server operation yet. Cancelling it
            # must not kill another query currently using the same session.
            if cancelled.is_set():
                raise DatabaseError("GridFS transfer cancelled before starting")
            return work(session, progress, cancelled.is_set)

        self.window.submit(execute, lambda value: complete(value),
                           lambda error: complete(error=error), label=label)
        self._update_controls()
        return True

    def _idle(self, callback):
        # Keep the document busy across the completed-write -> queued-refresh
        # handoff. A brief false idle state would expose stale file metadata.
        self.loading = self.running = True
        self._update_controls()
        def resume():
            if isValid(self) and not self._closed:
                self.loading = self.running = False
                callback()
                self._update_controls()
        self.window.when_idle(resume)

    def refresh(self, _checked=False):
        database, bucket, offset, limit, query = self.database, self.bucket, self.offset, self.page_size.value(), deepcopy(self._filter)
        self._submit(lambda session, _progress, _cancelled: session.list_files(database, bucket, filter_doc=query, offset=offset, limit=limit),
                     self.load_page, "读取 GridFS 文件")

    def load_page(self, page):
        selected = self.selected_file()
        selected_id = selected.document.get("_id") if selected else None
        self.page, self.offset = page, page.offset
        self._setting_page = True
        self.model.set_documents(page.documents)
        row = next((i for i, value in enumerate(page.documents) if value.document.get("_id") == selected_id), 0)
        if page.documents:
            self.grid.setCurrentIndex(self.model.index(row, 0))
        self._setting_page = False
        total = f"共 {page.total:,} 个文件" if page.total is not None else "总数未知"
        self.status.setText(f"本页 {len(page.documents):,} 个 · {total}" + (" · 只读" if self.read_only else ""))
        maximum = max(1, (page.total+self.page_size.value()-1)//self.page_size.value()) if page.total is not None else max(1, self.offset//self.page_size.value()+1+int(page.has_more))
        self.page_number.setMaximum(maximum)
        self.page_number.setValue(self.offset//self.page_size.value()+1)
        self._clear_preview()
        self._update_controls()
        if self.preview_action.isChecked():
            self._idle(self.preview_file)

    def go_page(self, offset):
        if not self.loading:
            self.offset = max(0, int(offset))
            self.refresh()

    def last_page(self):
        if self.page and self.page.total is not None:
            self.go_page(max(0, (self.page.total-1)//self.page_size.value()*self.page_size.value()))

    def page_settings(self):
        if self.loading:
            return
        value, accepted = QInputDialog.getInt(self, "记录设置", "每页文件数", self.page_size.value(), 1, 1000)
        if accepted:
            self.page_size.setValue(value)
            self.go_page(0)

    def _new_transfer(self, name, operation):
        row = len(self._transfers)
        self._transfers.append({"name": str(name), "operation": operation, "done": 0, "total": 0, "status": "正在传输", "error": ""})
        self.progress_table.insertRow(row)
        for column in (0, 1, 3):
            self.progress_table.setItem(row, column, QTableWidgetItem())
        bar = QProgressBar()
        bar.setRange(0, 0)
        self.progress_table.setCellWidget(row, 2, bar)
        self.progress_action.setChecked(True)
        self._render_transfer(row)
        return row

    def _render_transfer(self, row):
        record = self._transfers[row]
        for column, key in ((0, "name"), (1, "operation"), (3, "status")):
            self.progress_table.item(row, column).setText(record[key])
            self.progress_table.item(row, column).setToolTip(record["error"] or record[key])
        bar = self.progress_table.cellWidget(row, 2)
        total, transferred = record["total"], record["done"]
        if total > 0:
            bar.setRange(0, 1000)
            bar.setValue(min(1000, max(0, transferred*1000//total)))
        elif record["status"] != "正在传输":
            bar.setRange(0, 1)
            bar.setValue(int(record["status"] == "完成"))
        bar.setFormat(f"{transferred:,} / {total:,} bytes" if total else f"{transferred:,} bytes")
        done = sum(item["status"] == "完成" for item in self._transfers)
        failed = sum(item["status"] == "失败" for item in self._transfers)
        self.progress_summary.setText(f"总计 {len(self._transfers)} · 完成 {done} · 失败 {failed}")

    def _on_progress(self, request, transferred, total):
        if self._closed or request != self._request or not self.loading or self._active_transfer is None:
            return
        record = self._transfers[self._active_transfer]
        record["done"], record["total"] = max(0, int(transferred)), max(0, int(total))
        self._render_transfer(self._active_transfer)

    def upload_file(self):
        if self.loading or self.read_only:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "上传文件", "", "所有文件 (*)")
        if not filename:
            return
        database, bucket = self.database, self.bucket
        transfer = self._new_transfer(Path(filename).name, "上传")
        def done(_document):
            self._idle(self.refresh)
        self._submit(lambda session, progress, cancelled: session.upload_file(database, bucket, filename, progress=progress, cancelled=cancelled),
                     done, "上传 GridFS 文件", transfer=transfer)

    def download_file(self):
        selected = self.selected_file()
        if self.loading or selected is None:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择下载文件夹")
        if not directory:
            return
        name = _safe_filename(selected.document.get("filename"))
        destination = str(Path(directory) / name)
        database, bucket, file_id = self.database, self.bucket, selected.document["_id"]
        transfer = self._new_transfer(name, "下载")
        self._submit(lambda session, progress, cancelled: session.download_file(database, bucket, file_id, destination,
                         overwrite=False, expected=selected, progress=progress, cancelled=cancelled),
                     lambda count: self.status.setText(f"已保存 {count:,} bytes · {name}"), "下载 GridFS 文件", transfer=transfer)

    def delete_file(self):
        selected = self.selected_file()
        if self.loading or self.read_only or selected is None:
            return
        name = str(selected.document.get("filename", ""))
        # The filename is remote metadata, not QMessageBox rich-text markup.
        text = (f"<p>确认永久删除文件“{escape(name)}”？</p>"
                f"<p>ID：{escape(str(selected.document['_id']))}</p>"
                "<p>GridFS 独立服务器中的文件和数据块删除不能保证多文档原子性。</p>")
        answer = QMessageBox.question(self, "删除文件", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        database, bucket = self.database, self.bucket
        self._submit(lambda session, _progress, _cancelled: session.delete_file(database, bucket, expected=selected),
                     lambda _result: self._idle(self.refresh), "删除 GridFS 文件")

    def _clear_preview(self):
        self._preview_image = None
        self.preview_label.clear()
        self.preview_label.setText("选择文件以预览。")

    def _selection_changed(self, *_):
        self._clear_preview()
        self._update_controls()
        if self.preview_action.isChecked() and not self.loading and not self._setting_page:
            self.preview_file()

    def _toggle_preview(self, enabled):
        self.preview_panel.setVisible(enabled)
        if enabled:
            self.preview_file()

    def preview_file(self):
        selected = self.selected_file()
        if self.loading or not self.preview_action.isChecked() or selected is None:
            return
        length = selected.document.get("length")
        if isinstance(length, bool) or not isinstance(length, int) or not 0 < length < _PREVIEW_MAX_BYTES:
            self.preview_label.setText("“预览”仅支持小于 1 MB 的图像。请保存到磁盘。")
            return
        database, bucket, file_id = self.database, self.bucket, selected.document["_id"]
        def work(session, progress, cancelled):
            with tempfile.TemporaryDirectory(prefix="opennavicat-gridfs-preview-") as temporary:
                target = str(Path(temporary) / "preview")
                session.download_file(database, bucket, file_id, target, overwrite=False, expected=selected, progress=progress, cancelled=cancelled)
                reader = QImageReader(target)
                try:
                    size = reader.size()
                    if not reader.canRead() or not size.isValid() or size.width()*size.height() > 64_000_000:
                        raise ValueError("“预览”仅支持小于 1 MB 的图像。请保存到磁盘。")
                    if size.width() > 1600 or size.height() > 1200:
                        reader.setScaledSize(size.scaled(QSize(1600, 1200), Qt.AspectRatioMode.KeepAspectRatio))
                    image = reader.read()
                finally:
                    # Release QFile before TemporaryDirectory removes its own
                    # file, including on Windows where open files cannot unlink.
                    del reader
                if image.isNull():
                    raise ValueError("无法解码此图像；请保存到磁盘。")
                return image
        def done(image):
            current = self.selected_file()
            if current is not None and current.cas_token == selected.cas_token:
                self._preview_image = image
                self._render_preview()
        self._submit(work, done, "预览 GridFS 图像", failed=lambda error: self.preview_label.setText(str(error)))

    def _render_preview(self):
        if self._preview_image is not None:
            image = self._preview_image.scaled(self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
            self.preview_label.setPixmap(QPixmap.fromImage(image))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_preview()

    def cancel_transfer(self):
        if not self.loading or self._active_transfer is None or self._cancelling:
            return
        if self._operation_cancelled is None:
            return
        self._cancelling = True
        self._operation_cancelled.set()
        self.cancel_button.setEnabled(False)
        record = self._transfers[self._active_transfer]
        record["status"] = "正在取消…"
        record["error"] = "正在等待当前网络 I/O 返回，随后停止传输并清理临时内容。"
        self._render_transfer(self._active_transfer)
        # Per-transfer cancellation belongs to this job, even when queued. The
        # adapter checks it between chunks and before publication/target commit.
        # A connection-wide killOp could cancel an unrelated next operation.

    def _update_controls(self):
        if not hasattr(self, "refresh_button"):
            return
        selected = self.selected_file()
        idle, writable = not self.loading, not self.loading and not self.read_only
        self.upload_action.setEnabled(writable)
        self.delete_action.setEnabled(writable and selected is not None)
        self.delete_button.setEnabled(writable and selected is not None)
        self.download_action.setEnabled(idle and selected is not None)
        self.preview_download_button.setEnabled(idle and selected is not None)
        self.refresh_button.setEnabled(idle)
        self.filter_bar.setEnabled(idle)
        self.grid.setEnabled(idle)
        self.first_button.setEnabled(idle and self.offset > 0)
        self.previous_button.setEnabled(idle and self.offset > 0)
        self.next_button.setEnabled(idle and bool(self.page and self.page.has_more))
        self.last_button.setEnabled(idle and bool(self.page and self.page.has_more and self.page.total is not None))
        self.page_size.setEnabled(idle)
        self.settings_button.setEnabled(idle)
        self.page_number.setEnabled(idle)
        self.cancel_button.setEnabled(self.loading and self._active_transfer is not None and not self._cancelling)

    def can_discard(self, retry=None):
        return not self.loading

    def discard_changes(self):
        pass

    def closeEvent(self, event):
        if self.loading:
            event.ignore()
            return
        self._closed = True
        super().closeEvent(event)
