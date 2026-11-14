"""SQLite database-page drafts and their independent add/edit sheet."""
from __future__ import annotations

from pathlib import Path
import uuid

from PySide6.QtCore import QEvent, Qt, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QStyle, QStyleOptionFrame, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import (
    SQLiteDatabaseEntry, copy_connection_config, with_sqlite_database_password,
)
from .icons import tool_icon
from .sqlite_browse_button import SQLiteBrowseButton


def _absolute_file(value: str) -> str:
    # Capture CWD-dependent text on the GUI thread, without opening the file or
    # resolving symlinks. Empty draft fields remain empty.
    return str(Path(value).expanduser().absolute()) if value else ""


def _normalized_draft(config: ConnectionConfig) -> ConnectionConfig:
    draft = copy_connection_config(config)
    draft.sqlite_databases = tuple(SQLiteDatabaseEntry(row.id, row.name, _absolute_file(row.database), row.encrypted)
                                   for row in draft.sqlite_databases)
    # Existing carriers keep their original literal binding. Normalizing an
    # old relative filename is not evidence of newly entered credentials.
    return copy_connection_config(draft)


class _FontLineEdit(QLineEdit):
    """Reserve actual native content space instead of a fixed style height."""

    def _content_hint(self, hint):
        hint = QSize(hint)
        margins = self.textMargins()
        frame_margins = self.contentsMargins()
        padding = max(1, self.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameVMargin, None, self))
        required = self.fontMetrics().height() + margins.top() + margins.bottom() + 2 * padding
        option = QStyleOptionFrame()
        self.initStyleOption(option)
        for _ in range(8):
            option.rect = QRect(0, 0, hint.width(), hint.height())
            content = self.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, self)
            available = min(content.height(), hint.height() - frame_margins.top() - frame_margins.bottom())
            deficit = required - available
            if deficit <= 0:
                break
            hint.setHeight(hint.height() + deficit)
        return hint

    def sizeHint(self):
        return self._content_hint(super().sizeHint())

    def minimumSizeHint(self):
        return self._content_hint(super().minimumSizeHint())

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.ApplicationFontChange,
                            QEvent.Type.Polish, QEvent.Type.ContentsRectChange, QEvent.Type.DevicePixelRatioChange):
            self.updateGeometry()
        return handled


class _DatabaseTable(QTableWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.viewport().width()
        self.setColumnWidth(0, max(70, round(width * .235)))
        self.setColumnWidth(2, max(self.fontMetrics().horizontalAdvance("已加密") + 20, round(width * .23)))


class SQLiteDatabaseDialog(QDialog):
    """A private sheet; accepting it changes only its returned process draft."""

    def __init__(self, config: ConnectionConfig, row_id: str | None = None, parent=None):
        super().__init__(parent)
        # Top-level dialogs normally use the application font. Propagate the
        # parent's live font without pinning a copied QFont over later changes.
        self.setAttribute(Qt.WidgetAttribute.WA_WindowPropagation, True)
        self._font_refresh = QTimer(self)
        self._font_refresh.setSingleShot(True)
        self._font_refresh.timeout.connect(self._resolve_parent_font)
        self.setObjectName("sqliteDatabaseSheet")
        self.setWindowTitle("数据库")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(600, 215)
        self._base = _normalized_draft(config)
        self._result = None
        self._row_id = row_id or uuid.uuid4().hex
        self._editing = row_id is not None
        entry = next((row for row in self._base.sqlite_databases if row.id == row_id), None)
        if self._editing and entry is None:
            raise ValueError("Unknown SQLite database draft identity")
        self._password_provided = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(7)
        self.name_edit = _FontLineEdit(entry.name if entry else "")
        self.name_edit.setObjectName("sqliteDatabaseName")
        form.addRow("数据库名称:", self.name_edit)
        file_field = QWidget()
        file_layout = QHBoxLayout(file_field)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(0)
        self.file_edit = _FontLineEdit(entry.database if entry else "")
        self.file_edit.setObjectName("sqliteDatabaseFile")
        file_layout.addWidget(self.file_edit, 1)
        self.browse_button = SQLiteBrowseButton()
        self.browse_button.clicked.connect(self._browse)
        file_layout.addWidget(self.browse_button)
        form.addRow("数据库文件:", file_field)
        self.encrypted_check = QCheckBox("加密")
        self.encrypted_check.setChecked(bool(entry and entry.encrypted))
        form.addRow(self.encrypted_check)
        self.password_edit = _FontLineEdit(self._base.sqlite_database_passwords.get(self._row_id, "")
                                       if entry and entry.encrypted else "")
        self.password_edit.setObjectName("sqliteDatabasePassword")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setEnabled(self.encrypted_check.isChecked())
        password_row = QWidget()
        password_layout = QHBoxLayout(password_row)
        # The retained password row has its own inset, independent of the
        # name/file form column. Its field shares the same right margin.
        password_layout.setContentsMargins(123, 0, 0, 0)
        password_layout.setSpacing(8)
        password_layout.addWidget(QLabel("密码:"))
        password_layout.addWidget(self.password_edit, 1)
        form.addRow(password_row)
        self.file_edit.textChanged.connect(self._binding_changed)
        self.encrypted_check.toggled.connect(self._binding_changed)
        self.password_edit.textEdited.connect(self._password_edited)
        layout.addLayout(form)
        layout.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setAutoDefault(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("好")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        for button in self.buttons.buttons():
            button.setMinimumWidth(80)
        mac_buttons = self.buttons.style().styleHint(QStyle.StyleHint.SH_DialogButtonLayout) == QDialogButtonBox.ButtonLayout.MacLayout.value
        self.buttons.setLayoutDirection(Qt.LayoutDirection.LeftToRight if mac_buttons else Qt.LayoutDirection.RightToLeft)
        self.buttons.layout().setSpacing(12)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
        self.name_edit.setFocus()

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.ApplicationFontChange, QEvent.Type.Show):
            timer = getattr(self, "_font_refresh", None)
            if timer is not None:
                timer.start(0)
        return handled

    def _resolve_parent_font(self):
        if self.parentWidget() is not None and not self.testAttribute(Qt.WidgetAttribute.WA_SetFont):
            # Qt's stylesheet unpolish can restore an application-font cache
            # on a window. An empty resolve mask restores live inheritance;
            # it neither pins parent properties nor overrides active QSS.
            self.setFont(QFont())

    def _browse(self):
        filename, _filter = QFileDialog.getOpenFileName(self, "选择数据库文件", self.file_edit.text(),
            "SQLite 数据库 (*.sqlite *.sqlite3 *.db *.db3);;所有文件 (*)")
        if filename:
            self.file_edit.setText(filename)

    def _binding_changed(self, *_):
        self.password_edit.clear()
        self.password_edit.setModified(False)
        self._password_provided = False
        # A change followed by reverting the visible file/flag is still not
        # permission to resurrect a credential already cleared in this sheet.
        self._base.sqlite_database_passwords.pop(self._row_id, None)
        self._base.sqlite_database_password_bindings.pop(self._row_id, None)
        self.password_edit.setEnabled(self.encrypted_check.isChecked())

    def _password_edited(self, *_):
        self._password_provided = True

    def accept(self):
        self._result = None
        try:
            self._result = self._collect_config()
        except (ValueError, OSError, RuntimeError):
            QMessageBox.warning(self, "数据库", "数据库设置无法保存，请检查名称及文件路径。")
            return
        super().accept()

    def _collect_config(self):
        draft = copy_connection_config(self._base)
        entry = SQLiteDatabaseEntry(self._row_id, self.name_edit.text(), _absolute_file(self.file_edit.text()),
                                    self.encrypted_check.isChecked())
        if self._editing:
            draft.sqlite_databases = tuple(entry if row.id == self._row_id else row for row in draft.sqlite_databases)
        else:
            draft.sqlite_databases = (*draft.sqlite_databases, entry)
        # Changing a filename/flag drops an old carrier before explicit new
        # input can bind to the already normalized destination.
        draft = copy_connection_config(draft)
        if self._password_provided and entry.encrypted:
            draft = with_sqlite_database_password(draft, entry.id, self.password_edit.text())
        return draft

    def get_config(self) -> ConnectionConfig | None:
        return copy_connection_config(self._result) if self.result() == QDialog.DialogCode.Accepted and self._result is not None else None


class SQLiteConnectionDatabases(QWidget):
    """Metadata-only table with stable row IDs and a private secret carrier."""

    changed = Signal()

    def __init__(self, diagram: QWidget, config: ConnectionConfig | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("sqliteConnectionDatabases")
        if config is None or config.driver != "sqlite":
            config = ConnectionConfig(config.id if config else uuid.uuid4().hex, "", driver="sqlite")
        self._draft = _normalized_draft(config)
        self._revision = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 12)
        layout.setSpacing(10)
        layout.addWidget(diagram)
        self.table = _DatabaseTable(0, 3)
        self.table.setObjectName("sqliteDatabaseTable")
        self.table.setHorizontalHeaderLabels(["数据库", "文件", "已加密"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.cellDoubleClicked.connect(self._edit_cell)
        layout.addWidget(self.table, 1)
        tools = QHBoxLayout()
        tools.setSpacing(0)
        self.add_button = self._small_button("record_add", "添加数据库")
        self.remove_button = self._small_button("record_delete", "删除数据库")
        self.add_button.clicked.connect(self.add_database)
        self.remove_button.clicked.connect(self.remove_selected)
        tools.addWidget(self.add_button)
        tools.addWidget(self.remove_button)
        tools.addStretch(1)
        layout.addLayout(tools)
        self._render()

    @staticmethod
    def _small_button(icon, label):
        button = QPushButton()
        button.setIcon(tool_icon(icon, 16))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(24, 26)
        button.setFlat(True)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.setStyleSheet("QPushButton { padding: 0px; }")
        button.setAccessibleName(label)
        button.setToolTip(label)
        return button

    @property
    def connection_id(self) -> str:
        return self._draft.id

    def snapshot(self) -> ConnectionConfig:
        return copy_connection_config(self._draft)

    def apply_to(self, config: ConnectionConfig) -> ConnectionConfig:
        snapshot = self.snapshot()
        if config.id != snapshot.id:
            raise ValueError("SQLite database draft owner changed")
        result = copy_connection_config(config)
        result.sqlite_databases = snapshot.sqlite_databases
        result.sqlite_database_passwords = snapshot.sqlite_database_passwords
        result.sqlite_database_password_bindings = snapshot.sqlite_database_password_bindings
        return copy_connection_config(result)

    def _selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _render(self, selected_id=None):
        self.table.setRowCount(len(self._draft.sqlite_databases))
        for index, row in enumerate(self._draft.sqlite_databases):
            for column, text in enumerate((row.name, row.database, "是" if row.encrypted else "否")):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.id)
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        if selected_id is not None:
            for index, row in enumerate(self._draft.sqlite_databases):
                if row.id == selected_id:
                    self.table.selectRow(index)
                    break

    def _replace_draft(self, config, selected_id=None):
        if config.id != self.connection_id:
            raise ValueError("SQLite database draft owner changed")
        self._draft = _normalized_draft(config)
        self._revision += 1
        self._render(selected_id)
        self.changed.emit()

    def add_database(self):
        self._open_editor()

    def _edit_cell(self, row, column):
        if column in (0, 2) and 0 <= row < len(self._draft.sqlite_databases):
            self._open_editor(self._draft.sqlite_databases[row].id)

    def _open_editor(self, row_id=None):
        revision = self._revision
        sheet = SQLiteDatabaseDialog(self.snapshot(), row_id, self.window())
        try:
            if sheet.exec() != QDialog.DialogCode.Accepted:
                return
            if revision != self._revision:
                QMessageBox.warning(self, "数据库", "数据库列表已更改，请重新打开编辑窗口。")
                return
            result = sheet.get_config()
            if result is not None:
                self._replace_draft(result, row_id)
        finally:
            sheet.deleteLater()

    def remove_selected(self):
        row_id = self._selected_id()
        if row_id is None:
            return
        draft = self.snapshot()
        draft.sqlite_databases = tuple(row for row in draft.sqlite_databases if row.id != row_id)
        self._replace_draft(draft)
