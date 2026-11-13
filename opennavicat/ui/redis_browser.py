"""Native Redis key browser and exact byte editors.

Widget state is read/captured on the GUI thread. Every session operation is
submitted to the owning window's serial worker; callbacks reject replaced
sessions and stale requests. One collection edit is staged at a time so several
independent Redis commands are never presented as an atomic batch save.
"""
from __future__ import annotations

from copy import deepcopy
from html import escape

from PySide6.QtCore import QSize, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QAction, QColor, QFontDatabase, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QToolBar,
    QVBoxLayout, QWidget, QSplitter,
)

from ..redis_session import RedisValuePage, display_redis_bytes, quote_redis_bytes
from .icons import tool_icon, toggle_icon


TYPES = ("string", "hash", "list", "set", "zset", "stream")


class RedisBytesEditor(QWidget):
    """Strict UTF-8 or HEX; changing presentation never changes the value."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["UTF-8", "HEX"])
        self.mode.setToolTip("UTF-8 严格编码；HEX 直接编辑原始字节。切换格式不会丢弃或替换无法解码的字节。")
        layout.addWidget(self.mode)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.editor.setMinimumHeight(80)
        layout.addWidget(self.editor, 1)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self._mode = "UTF-8"
        self.editor.textChanged.connect(self.changed)
        self.mode.currentTextChanged.connect(self._switch)

    @staticmethod
    def _text(value):
        text = value.decode("utf-8", "strict")
        document = QTextDocument()
        document.setPlainText(text)
        if document.toPlainText().encode("utf-8", "strict") != value:
            raise ValueError("文本控件会规范化此换行或分隔符；请使用 HEX 保持原始字节。")
        return text

    def set_bytes(self, value, *, force_hex=False):
        value = bytes(value)
        mode = "HEX"
        if not force_hex:
            try:
                text = self._text(value)
                if all(char.isprintable() or char in "\n\t" for char in text):
                    mode = "UTF-8"
            except (UnicodeError, ValueError):
                pass
        with QSignalBlocker(self.mode), QSignalBlocker(self.editor):
            self._mode = mode
            self.mode.setCurrentText(mode)
            self.editor.setPlainText(value.hex(" ") if mode == "HEX" else self._text(value))
        self.error.hide()

    def bytes_value(self):
        text = self.editor.toPlainText()
        try:
            return bytes.fromhex(text) if self._mode == "HEX" else text.encode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("HEX 必须是完整的十六进制字节；UTF-8 文本必须可以严格编码。") from exc

    def _switch(self, mode):
        if mode == self._mode:
            return
        try:
            value = self.bytes_value()
            representation = value.hex(" ") if mode == "HEX" else self._text(value)
        except (ValueError, UnicodeError) as exc:
            with QSignalBlocker(self.mode):
                self.mode.setCurrentText(self._mode)
            self.error.setText(str(exc)); self.error.show()
            return
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(representation)
        self._mode = mode
        self.error.hide()
        self.changed.emit()

    def set_read_only(self, value):
        self.editor.setReadOnly(value)


class _PairsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["字段（允许重复）", "值"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add, remove = QPushButton("添加字段"), QPushButton("删除字段")
        add.clicked.connect(lambda: self.add_pair())
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        row.addWidget(add); row.addWidget(remove); row.addStretch()
        layout.addLayout(row)
        self.add_pair()

    def add_pair(self, field=b"", value=b""):
        row = self.table.rowCount()
        self.table.insertRow(row); self.table.setRowHeight(row, 140)
        for column, raw in enumerate((field, value)):
            editor = RedisBytesEditor()
            editor.set_bytes(raw)
            self.table.setCellWidget(row, column, editor)

    def pairs(self):
        if not self.table.rowCount():
            raise ValueError("Stream 条目至少需要一个字段和值。")
        return [(self.table.cellWidget(row, 0).bytes_value(), self.table.cellWidget(row, 1).bytes_value())
                for row in range(self.table.rowCount())]


class _ValueFields(QWidget):
    def __init__(self, kind, row=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        if kind == "stream":
            self.entry_id = QLineEdit("*")
            form.addRow("条目 ID", self.entry_id)
            self.pairs = _PairsWidget()
            form.addRow(self.pairs)
            return
        if kind == "hash":
            self.field = RedisBytesEditor()
            self.field.set_bytes(row[0] if row else b"")
            self.field.set_read_only(row is not None)
            form.addRow("字段", self.field)
        self.value = RedisBytesEditor()
        initial = (row[1] if kind in {"hash", "list"} else row[0]) if row else b""
        self.value.set_bytes(initial)
        form.addRow("成员" if kind in {"set", "zset"} else "值", self.value)
        if kind == "zset":
            self.score = QLineEdit(row[1].decode("ascii", "strict") if row else "0")
            form.addRow("分数", self.score)

    def payload(self):
        if self.kind == "stream":
            return self.pairs.pairs()
        value = self.value.bytes_value()
        if self.kind == "string":
            return value
        if self.kind == "hash":
            return [(self.field.bytes_value(), value)]
        if self.kind == "zset":
            return [(value, self.score.text().strip().encode("ascii", "strict"))]
        return [value]


class RedisNewKeyDialog(QDialog):
    createRequested = Signal(object)

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind, self.busy = kind, False
        self.setWindowTitle("新建 " + kind + " 键")
        self.resize(650, 620 if kind in {"hash", "stream"} else 450)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.key_editor = RedisBytesEditor()
        self.key_editor.set_bytes(b"")
        form.addRow("键", self.key_editor)
        layout.addLayout(form)
        self.fields = _ValueFields(kind)
        if kind == "stream":
            self.fields.entry_id.setReadOnly(True)
            self.fields.entry_id.setToolTip("新键的首个条目 ID 由 Redis 自动生成。")
        layout.addWidget(self.fields, 1)
        self.error = QLabel(); self.error.setTextFormat(Qt.TextFormat.PlainText); self.error.setWordWrap(True); layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def accept(self):
        if self.busy:
            return
        try:
            values = {"key": self.key_editor.bytes_value(), "kind": self.kind, "initial": self.fields.payload()}
        except (ValueError, UnicodeError) as exc:
            self.error.setText(str(exc)); return
        self.createRequested.emit(values)

    def set_busy(self, value):
        self.busy = value
        self.buttons.setEnabled(not value); self.key_editor.setEnabled(not value); self.fields.setEnabled(not value)

    def show_error(self, error):
        self.set_busy(False); self.error.setText(str(error))

    def reject(self):
        if not self.busy:
            super().reject()


class RedisElementDialog(QDialog):
    def __init__(self, kind, row=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑元素" if row is not None else "添加元素")
        self.resize(650, 550 if kind in {"hash", "stream"} else 380)
        layout = QVBoxLayout(self)
        self.fields = _ValueFields(kind, row)
        layout.addWidget(self.fields, 1)
        self.error = QLabel(); self.error.setTextFormat(Qt.TextFormat.PlainText); self.error.setWordWrap(True); layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.payload = None
        self.entry_id = b"*"

    def accept(self):
        try:
            self.payload = self.fields.payload()
            if self.fields.kind == "stream":
                self.entry_id = self.fields.entry_id.text().encode("ascii", "strict")
        except (ValueError, UnicodeError) as exc:
            self.error.setText(str(exc)); return
        super().accept()


class _RedisPage(QWidget):
    dirtyChanged = Signal(bool)
    titleChanged = Signal(str)
    keyOpened = Signal(bytes)

    def __init__(self, window, connection_id, schema):
        super().__init__()
        self.window, self.connection_id, self.schema = window, connection_id, str(schema)
        self.loading = self.running = self._closed = False
        self._request = 0
        self._after_save = None
        self._last_dirty = False
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0); self.root_layout.setSpacing(0)
        self.toolbar = QToolBar()
        self.toolbar.setObjectName("objectToolbar"); self.toolbar.setIconSize(QSize(18, 18))
        self.root_layout.addWidget(self.toolbar)
        self.message = QLabel(); self.message.setTextFormat(Qt.TextFormat.PlainText); self.message.setWordWrap(True); self.message.hide()
        self.root_layout.addWidget(self.message)
        self.destroyed.connect(lambda: setattr(self, "_closed", True))

    @property
    def read_only(self):
        session = self.window.sessions.get(self.connection_id)
        return session is None or bool(session.config.read_only)

    def _action(self, text, icon, function):
        action = QAction(tool_icon(icon), text, self)
        action.triggered.connect(function); self.toolbar.addAction(action)
        return action

    def show_error(self, error):
        self.message.setText(str(error)); self.message.show()

    def _submit(self, function, done, label, *, failed=None):
        if self.loading or self._closed:
            return
        session = self.window.sessions.get(self.connection_id)
        if session is None:
            self.show_error("连接已断开。")
            if failed:failed("连接已断开。")
            return
        self._request += 1
        request = self._request
        binding = (self.schema, getattr(self, "key", None))
        self.loading = True; self._update_controls()
        def current():
            return (not self._closed and request == self._request and self.window.sessions.get(self.connection_id) is session
                    and binding == (self.schema, getattr(self, "key", None)))
        def success(value):
            if current():
                self.loading = False; done(value); self._update_controls()
        def failure(error):
            if current():
                self.loading = False; self._after_save = None; self.show_error(error)
                if failed:failed(error)
                self._update_controls()
        self.window.submit(lambda: function(session), success, failure, label=label)

    def _idle(self, callback):
        self.window.when_idle(lambda: callback() if not self._closed else None)

    def current_result(self):
        return None

    def closeEvent(self, event):
        if self.loading or (hasattr(self, "can_discard") and not self.can_discard(retry=self.close)):
            event.ignore(); return
        self._closed = True
        super().closeEvent(event)


def _table(columns):
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setDefaultSectionSize(22)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.setAlternatingRowColors(True)
    return table


def _display(value):
    if isinstance(value, bytes):
        return display_redis_bytes(value[:160]) + (f" … ({len(value)} 字节)" if len(value) > 160 else "")
    if isinstance(value, list):
        return "[" + ", ".join(_display(part) for part in value) + "]"
    return str(value)


class RedisBrowserPage(_RedisPage):
    """A SCAN key list above the selected key's embedded editor."""
    focusRequested = Signal(bool)

    def __init__(self, window, connection_id, schema):
        super().__init__(window, connection_id, schema)
        self.kind, self.table = "redis_database", ""
        self.entries, self.scan_cursor, self.next_cursor, self.history = [], 0, 0, []
        self.key_page = self._new_dialog = None
        self.open_action = self._action("打开", "open", self.open_selected)
        self.new_action = self._action("新建键", "add", lambda: self.new_key("string"))
        menu = QMenu(self)
        for kind in TYPES:
            menu.addAction(kind, lambda checked=False, selected=kind: self.new_key(selected))
        self.new_action.setMenu(menu)
        self.refresh_action = self._action("刷新", "refresh", self.refresh)
        self.editor_action = self._action("编辑器", "design", lambda: None)
        self.editor_action.setCheckable(True); self.editor_action.setChecked(True)
        self.focus_action = self._action("专注模式", "focus", lambda: None)
        self.focus_action.setIcon(toggle_icon("focus", 18))
        self.focus_action.setCheckable(True); self.focus_action.toggled.connect(self.focusRequested)
        controls = QHBoxLayout(); controls.setContentsMargins(8, 4, 8, 4)
        self.type_filter = QComboBox(); self.type_filter.addItems(["所有类型", *TYPES])
        self.type_filter.setToolTip("筛选当前已加载批次；可继续扫描加载其它键。")
        self.type_filter.currentIndexChanged.connect(self._render)
        controls.addWidget(self.type_filter)
        controls.addWidget(QLabel("键包含"))
        self.search = QLineEdit(); self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.refresh); controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("扫描数量"))
        self.page_size = QSpinBox(); self.page_size.setRange(1, 10000); self.page_size.setValue(200)
        self.page_size.setToolTip("Redis SCAN COUNT 是数量提示；服务端实际返回的批次可能更多或更少。")
        controls.addWidget(self.page_size)
        self.root_layout.addLayout(controls)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.keys = _table(["键", "类型", "值摘要", "大小", "TTL"])
        self.keys.setColumnWidth(0, 190); self.keys.setColumnWidth(1, 80); self.keys.setColumnWidth(2, 200)
        self.keys.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.keys.itemSelectionChanged.connect(self._update_controls)
        self.splitter.addWidget(self.keys)
        self.editor_holder = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_holder); self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_editor = QLabel("选择一个键以查看和编辑其值。")
        self.empty_editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editor_layout.addWidget(self.empty_editor)
        self.splitter.addWidget(self.editor_holder); self.splitter.setSizes([600, 400])
        self.editor_action.toggled.connect(self.editor_holder.setVisible)
        self.root_layout.addWidget(self.splitter, 1)
        footer = QHBoxLayout(); footer.setContentsMargins(8, 4, 8, 4)
        self.status = QLabel("尚未扫描")
        self.previous_button, self.next_button = QPushButton("上一批"), QPushButton("加载下一批")
        self.previous_button.clicked.connect(self.previous); self.next_button.clicked.connect(self.next)
        footer.addWidget(self.status, 1); footer.addWidget(self.previous_button); footer.addWidget(self.next_button)
        self.root_layout.addLayout(footer)
        self._update_controls()
        QTimer.singleShot(0, self, lambda: self.refresh(check=False))

    @property
    def title(self):
        return f"(所有数据) @ {self.schema}" + (" — 已编辑" if self.dirty else "")

    @property
    def dirty(self):
        return bool(self.key_page and self.key_page.dirty)

    def can_discard(self, retry=None):
        return self.key_page.can_discard(retry) if self.key_page else True

    def request_save(self):
        if self.key_page:
            self.key_page._after_save = self._after_save; self._after_save = None
            self.key_page.request_save()

    def discard_changes(self):
        if self.key_page:self.key_page.discard_changes()

    def _scan(self, cursor, history):
        if self.loading:
            return
        raw = self.search.text().encode("utf-8", "strict")
        pattern = b"*" + b"".join(b"\\" + bytes([byte]) if byte in b"*?[]\\" else bytes([byte]) for byte in raw) + b"*"
        count, schema = self.page_size.value(), self.schema
        def work(session):
            page = session.scan_keys(schema, cursor=cursor, count=count, match=pattern)
            visible = []
            for entry in page.entries:
                try:
                    value = session.read_key(schema, entry["key"], limit=1, max_bytes=80)
                    if value.type == "none":continue
                    entry["summary"] = _display(value.value if value.type == "string" else value.rows[0] if value.rows else "")
                    if value.truncated:entry["summary"] += " …"
                    entry["size"] = f"{value.total} B" if value.type == "string" else f"{value.total} 项"
                except Exception as exc:
                    if getattr(exc, "authentication_required", False):raise
                    entry["summary"], entry["size"] = "读取失败：" + str(exc), "未知"
                visible.append(entry)
            page.entries = visible
            return page
        def done(page):
            self.entries, self.scan_cursor, self.next_cursor, self.history = page.entries, cursor, page.cursor, history
            self.message.hide(); self._render()
        self._submit(work, done, "扫描 Redis 键")

    def refresh(self, checked=False, *, check=True):
        # Updating the key list does not reload or discard the embedded draft.
        self._scan(0, [])

    def next(self):
        if self.next_cursor:
            self._scan(self.next_cursor, [*self.history, self.scan_cursor])

    def previous(self):
        if self.history:
            self._scan(self.history[-1], self.history[:-1])

    def _render(self):
        selected_type = self.type_filter.currentText()
        visible = [entry for entry in self.entries if selected_type == "所有类型" or entry["type"] == selected_type]
        self.keys.setRowCount(len(visible))
        for row, entry in enumerate(visible):
            for column, value in enumerate((entry["name"], entry["type"], entry.get("summary", ""), entry.get("size", "未知"),
                                            "无 TTL" if entry["pttl"] == -1 else f"{entry['pttl']} ms")):
                item = QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole, entry["key"])
                item.setToolTip("<pre>" + escape(quote_redis_bytes(entry["key"]) if column == 0 else str(value)) + "</pre>")
                self.keys.setItem(row, column, item)
        self.status.setText(f"本批已加载 {len(self.entries)} 个键，显示 {len(visible)} 个 · " + ("扫描完成" if self.next_cursor == 0 else "扫描尚未完成"))
        self._update_controls()

    def open_selected(self):
        row = self.keys.currentRow()
        if row >= 0:
            self.show_key(self.keys.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def show_key(self, key):
        key = bytes(key)
        if self.key_page and self.key_page.key == key:
            self.editor_action.setChecked(True); return self.key_page
        if not self.can_discard(lambda: self.show_key(key)):
            return None
        if self.key_page:
            self.editor_layout.removeWidget(self.key_page); self.key_page._closed = True; self.key_page.deleteLater()
        self.empty_editor.hide()
        page = self.key_page = RedisKeyPage(self.window, self.connection_id, self.schema, key)
        page.dirtyChanged.connect(lambda dirty: (self.dirtyChanged.emit(dirty), self.titleChanged.emit(self.title)))
        page.renamed.connect(lambda renamed: self._renamed(renamed))
        page.deleted.connect(lambda _: self._deleted())
        self.editor_layout.addWidget(page); self.editor_action.setChecked(True)
        self.keyOpened.emit(key)
        return page

    def _renamed(self, key):
        self.keyOpened.emit(key); self._idle(lambda: self.refresh(check=False))

    def _deleted(self):
        if self.key_page:
            self.editor_layout.removeWidget(self.key_page); self.key_page._closed = True; self.key_page.deleteLater(); self.key_page = None
        self.empty_editor.show(); self._idle(lambda: self.refresh(check=False))

    def new_key(self, kind="string"):
        if self.read_only or self.loading or not self.can_discard(lambda: self.new_key(kind)):
            return
        dialog = self._new_dialog = RedisNewKeyDialog(kind, self)
        def create(values):
            captured, schema = deepcopy(values), self.schema
            dialog.set_busy(True)
            def done(_):
                QDialog.accept(dialog); self._new_dialog = None
                self._idle(lambda: (self.refresh(check=False), self.show_key(captured["key"])))
            self._submit(lambda session: session.create_key(schema, captured["key"], captured["kind"], captured["initial"]), done,
                         "创建 Redis 键", failed=dialog.show_error)
        dialog.createRequested.connect(create)
        dialog.rejected.connect(lambda: setattr(self, "_new_dialog", None))
        dialog.open()
        return dialog

    def _update_controls(self):
        if not hasattr(self, "keys"):
            return
        self.open_action.setEnabled(not self.loading and self.keys.currentRow() >= 0)
        self.new_action.setEnabled(not self.loading and not self.read_only)
        self.refresh_action.setEnabled(not self.loading)
        self.previous_button.setEnabled(not self.loading and bool(self.history))
        self.next_button.setEnabled(not self.loading and bool(self.next_cursor))


class RedisKeyPage(_RedisPage):
    renamed = Signal(bytes)
    deleted = Signal(bytes)

    def __init__(self, window, connection_id, schema, key):
        super().__init__(window, connection_id, schema)
        self.kind, self.key, self.table = "redis_key", bytes(key), display_redis_bytes(bytes(key))
        self.value_page = None
        self.offset, self.scan_cursor, self.history = 0, 0, []
        self._pending = self._dialog = None
        self._populating = False
        self.refresh_action = self._action("刷新", "refresh", self.refresh)
        self.add_action = self._action("添加元素", "add", self.add_element)
        self.edit_action = self._action("编辑元素", "design", self.edit_element)
        self.delete_element_action = self._action("删除元素", "delete", self.delete_element)
        self.toolbar.addSeparator()
        self.ttl_action = self._action("TTL / 持久化", "info", self.set_ttl)
        self.rename_action = self._action("重命名键", "design", self.rename_key)
        self.delete_action = self._action("删除键", "delete", self.delete_key)
        form = QFormLayout(); form.setContentsMargins(8, 4, 8, 4)
        self.key_label, self.type_label, self.ttl_label = QLabel(), QLabel(), QLabel()
        for label in (self.key_label, self.type_label, self.ttl_label):label.setTextFormat(Qt.TextFormat.PlainText)
        self.key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("键", self.key_label); form.addRow("类型", self.type_label); form.addRow("TTL", self.ttl_label)
        self.root_layout.addLayout(form)
        self.stack = QStackedWidget()
        self.value_editor = RedisBytesEditor(); self.value_editor.changed.connect(self._dirty_changed)
        self.elements = _table([]); self.elements.itemSelectionChanged.connect(self._update_controls)
        self.elements.itemDoubleClicked.connect(lambda *_: self.edit_element())
        self.stack.addWidget(self.value_editor); self.stack.addWidget(self.elements)
        self.root_layout.addWidget(self.stack, 1)
        footer = QHBoxLayout(); footer.setContentsMargins(8, 4, 8, 4)
        self.status = QLabel("正在读取…"); footer.addWidget(self.status, 1)
        self.page_size = QSpinBox(); self.page_size.setRange(1, 10000); self.page_size.setValue(200)
        self.previous_button, self.next_button = QPushButton("上一页"), QPushButton("下一页")
        self.previous_button.clicked.connect(self.previous); self.next_button.clicked.connect(self.next)
        footer.addWidget(self.page_size); footer.addWidget(self.previous_button); footer.addWidget(self.next_button)
        self.root_layout.addLayout(footer)
        actions = QHBoxLayout(); actions.setContentsMargins(8, 4, 8, 6); actions.addStretch()
        self.save_action = QAction("应用", self); self.save_action.triggered.connect(self.request_save)
        self.discard_action = QAction("放弃", self); self.discard_action.triggered.connect(self.discard_changes)
        self.save_button, self.discard_button = QPushButton("应用"), QPushButton("放弃")
        self.save_button.clicked.connect(self.request_save); self.discard_button.clicked.connect(self.discard_changes)
        actions.addWidget(self.discard_button); actions.addWidget(self.save_button); self.root_layout.addLayout(actions)
        self._update_controls()
        QTimer.singleShot(0, self, lambda: self.refresh(check=False))

    @property
    def title(self):
        return display_redis_bytes(self.key) + f" @ {self.schema}" + (" — 已编辑" if self.dirty else "")

    @property
    def dirty(self):
        if self._pending is not None:
            return True
        if self.value_page and self.value_page.type == "string" and not self.value_page.truncated:
            try:return self.value_editor.bytes_value() != self.value_page.value
            except (ValueError, UnicodeError):return True
        return False

    def _dirty_changed(self):
        if self._populating:
            return
        dirty = self.dirty
        if dirty != self._last_dirty:
            self._last_dirty = dirty; self.dirtyChanged.emit(dirty); self.titleChanged.emit(self.title)
        self._update_controls()

    def can_discard(self, retry=None):
        if self.loading:
            self.show_error("正在处理 Redis 操作，请等待完成。"); return False
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "是否应用更改？", "当前键有尚未应用的更改。",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Discard:
            self.discard_changes(); return True
        if answer == QMessageBox.StandardButton.Save:
            self._after_save = retry; self.request_save()
        return False

    def refresh(self, checked=False, *, check=True):
        if self.loading or (check and not self.can_discard(lambda: self.refresh(check=False))):
            return
        schema, key, offset, cursor, limit = self.schema, self.key, self.offset, self.scan_cursor, self.page_size.value()
        self._submit(lambda session: session.read_key(schema, key, offset=offset, cursor=cursor, limit=limit), self.load_page, "读取 Redis 值")

    def load_page(self, page):
        self.value_page = deepcopy(page); self._pending = None
        self._populating = True
        try:
            self.key_label.setText(display_redis_bytes(self.key)); self.key_label.setToolTip("<pre>" + escape(quote_redis_bytes(self.key)) + "</pre>")
            self.type_label.setText(page.type)
            self.ttl_label.setText("无 TTL" if page.pttl == -1 else "键已不存在" if page.pttl == -2 else f"{page.pttl} ms")
            if page.type == "string":
                self.stack.setCurrentWidget(self.value_editor); self.value_editor.set_bytes(page.value or b"")
            else:
                self.stack.setCurrentWidget(self.elements); self._render_rows()
            self.message.hide()
            self.status.setText(f"共 {page.total} " + ("字节" if page.type == "string" else "项") +
                (" · 已加载部分内容" if page.truncated else "") + (" · 大值分段只读，禁止整体覆盖" if page.type == "string" and page.truncated else ""))
        finally:self._populating = False
        self._dirty_changed(); self._update_controls()

    def _render_rows(self):
        page = self.value_page
        if page is None:return
        rows = deepcopy(page.rows)
        if self._pending:
            action, original, payload = self._pending
            if action == "add":
                rows.append(payload[0] if page.type in {"hash", "zset"} else ["待新增", payload] if page.type == "stream" else ["待新增", payload[0]] if page.type == "list" else [payload[0]])
            elif action == "edit" and original in rows:
                index = rows.index(original)
                rows[index] = payload[0] if page.type in {"hash", "zset"} else [original[0], payload[0]] if page.type == "list" else [payload[0]]
        self.elements.setColumnCount(len(page.columns)); self.elements.setHorizontalHeaderLabels(page.columns)
        self.elements.setRowCount(len(rows))
        for row, record in enumerate(rows):
            for column, raw in enumerate(record):
                item = QTableWidgetItem(_display(raw)); item.setData(Qt.ItemDataRole.UserRole, deepcopy(record))
                item.setToolTip("<pre>" + escape(_display(raw)) + "</pre>")
                if self._pending:item.setBackground(QColor("#69572d") if self.palette().base().color().lightness()<128 else QColor("#fff2c2"))
                self.elements.setItem(row, column, item)
        if self._pending and self._pending[0] == "delete":
            for row, record in enumerate(rows):
                if record == self._pending[1]:
                    for column in range(self.elements.columnCount()):
                        item = self.elements.item(row, column); font = item.font(); font.setStrikeOut(True); item.setFont(font)

    def _selected(self):
        row = self.elements.currentRow()
        return self.elements.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and self.elements.item(row, 0) else None

    def _element_dialog(self, action, original=None):
        if self.read_only or self.loading or self.dirty or not self.value_page:
            return
        kind = self.value_page.type
        dialog = self._dialog = RedisElementDialog(kind, original, self)
        def accepted():
            if self._closed:return
            payload = deepcopy(dialog.payload)
            if kind == "stream":payload = {"fields": payload, "entry_id": dialog.entry_id}
            self._pending = (action, deepcopy(original), payload); self._dialog = None
            self._render_rows(); self._dirty_changed()
        dialog.accepted.connect(accepted)
        dialog.rejected.connect(lambda: setattr(self, "_dialog", None))
        dialog.open(); return dialog

    def add_element(self):
        if self.value_page and self.value_page.type in TYPES[1:]:return self._element_dialog("add")

    def edit_element(self):
        original = self._selected()
        if original is not None and self.value_page.type in {"hash", "list", "set", "zset"}:
            return self._element_dialog("edit", original)

    def delete_element(self):
        original = self._selected()
        if original is not None and not self.read_only and not self.loading and not self.dirty:
            self._pending = ("delete", deepcopy(original), None); self._render_rows(); self._dirty_changed()

    def request_save(self):
        if self.loading or self.read_only or not self.dirty or not self.value_page:
            return
        schema, key, page, pending = self.schema, self.key, deepcopy(self.value_page), deepcopy(self._pending)
        try:value = self.value_editor.bytes_value() if page.type == "string" else None
        except (ValueError, UnicodeError) as exc:self.show_error(exc); self._after_save = None; return
        def work(session):
            if page.type == "string":return session.set_string(schema, key, value, expected=page.value)
            action, old, payload = pending
            if page.type == "hash":
                if action == "delete":return session.delete_hash_field(schema, key, old[0], expected=old[1])
                field, field_value = payload[0]
                return session.set_hash_field(schema, key, field, field_value, expected=None if action == "add" else old[1])
            if page.type == "list":
                if action == "add":return session.append_list(schema, key, payload)
                if action == "delete":return session.delete_list_item(schema, key, old[0], expected=old[1])
                return session.set_list_item(schema, key, old[0], payload[0], expected=old[1])
            if page.type == "set":
                if action == "add":return session.add_set_member(schema, key, payload[0])
                if action == "delete":return session.remove_set_member(schema, key, old[0])
                return session.replace_set_member(schema, key, old[0], payload[0])
            if page.type == "zset":
                if action == "delete":return session.delete_zset_member(schema, key, old[0], expected=old[1])
                member, score = payload[0]
                return (session.set_zset_member(schema, key, member, score, expected=None) if action == "add"
                        else session.replace_zset_member(schema, key, old[0], member, score, expected=old[1]))
            if page.type == "stream":
                return session.stream_delete(schema, key, [old[0]]) if action == "delete" else session.stream_add(schema, key, payload["fields"], entry_id=payload["entry_id"])
        def done(_):
            self._pending = None
            if page.type == "string":self.value_page.value = value
            self._dirty_changed()
            retry, self._after_save = self._after_save, None
            if retry:self._idle(retry)
            else:self.refresh(check=False)
        self._submit(work, done, "应用 Redis 更改")

    save_changes = request_save

    def discard_changes(self):
        if self.loading:return
        self._pending = None
        if self.value_page:self.load_page(self.value_page)

    def next(self):
        if not self.value_page or not self.can_discard(self.next):return
        page = self.value_page
        self.history.append((self.offset, self.scan_cursor))
        if page.type in {"hash", "set", "stream"}:self.scan_cursor = page.cursor
        else:self.offset = page.next_offset
        self.refresh(check=False)

    def previous(self):
        if self.history and self.can_discard(self.previous):
            self.offset, self.scan_cursor = self.history.pop(); self.refresh(check=False)

    def set_ttl(self):
        if self.read_only or self.loading or self.dirty:return
        value, accepted = QInputDialog.getText(self, "TTL / 持久化", "输入毫秒数；空白表示移除 TTL；0 表示立即过期。",
            text=str(self.value_page.pttl) if self.value_page and self.value_page.pttl >= 0 else "")
        if not accepted:return
        try:
            ttl = None if not value.strip() else int(value.strip())
            if ttl is not None and not 0 <= ttl <= 9223372036854775807:raise ValueError()
        except ValueError:self.show_error("TTL 必须是非负毫秒整数。"); return
        if QMessageBox.question(self, "确认 TTL", "应用此 TTL 更改？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:return
        schema, key = self.schema, self.key
        def done(result):
            if ttl == 0 and result:self.deleted.emit(key)
            else:self.refresh(check=False)
        self._submit(lambda session: session.expire_key(schema, key, ttl), done, "设置 Redis TTL")

    def rename_key(self):
        if self.read_only or self.loading or self.dirty:return
        dialog = QDialog(self); dialog.setWindowTitle("重命名键")
        layout = QVBoxLayout(dialog); editor = RedisBytesEditor(); editor.set_bytes(self.key); layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:return
        try:replacement = editor.bytes_value()
        except ValueError as exc:self.show_error(exc);return
        if replacement == self.key:return
        old, schema = self.key, self.schema
        def done(_):
            self.key, self.table = replacement, display_redis_bytes(replacement)
            self.titleChanged.emit(self.title); self.renamed.emit(replacement); self.refresh(check=False)
        self._submit(lambda session: session.rename_key(schema, old, replacement), done, "重命名 Redis 键")

    def delete_key(self):
        if self.read_only or self.loading or self.dirty:return
        if QMessageBox.question(self, "删除键", "确认永久删除键 " + quote_redis_bytes(self.key) + "？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:return
        schema, key = self.schema, self.key
        self._submit(lambda session: session.delete_key(schema, key), lambda _: self.deleted.emit(key), "删除 Redis 键")

    def _update_controls(self):
        if not hasattr(self, "save_button"):return
        page = self.value_page
        writable = bool(page and page.type != "none" and not self.read_only and not self.loading)
        collection = bool(page and page.type in TYPES[1:])
        self.value_editor.setEnabled(not self.loading)
        self.value_editor.set_read_only(not writable or bool(page and page.truncated))
        self.elements.setEnabled(not self.loading)
        self.save_action.setEnabled(writable and self.dirty)
        self.save_button.setEnabled(self.save_action.isEnabled())
        self.discard_action.setEnabled(not self.loading and self.dirty); self.discard_button.setEnabled(self.discard_action.isEnabled())
        self.add_action.setEnabled(writable and collection and not self.dirty)
        self.edit_action.setEnabled(writable and collection and page.type != "stream" and not self.dirty and self._selected() is not None)
        self.delete_element_action.setEnabled(writable and collection and not self.dirty and self._selected() is not None)
        for action in (self.ttl_action, self.rename_action, self.delete_action):action.setEnabled(writable and not self.dirty)
        self.refresh_action.setEnabled(not self.loading)
        more = bool(page and (page.cursor if page.type in {"hash", "set", "stream"} else page.next_offset < page.total))
        self.next_button.setEnabled(not self.loading and more); self.previous_button.setEnabled(not self.loading and bool(self.history))
