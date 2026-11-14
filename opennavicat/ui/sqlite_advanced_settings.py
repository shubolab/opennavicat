"""SQLite's reference-shaped advanced page and its private password draft.

Collecting this page never opens a database or accesses the system keyring.
The encryption helpers own validation and exact credential binding.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QSizePolicy, QStyle, QStyleFactory, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import copy_connection_config
from opennavicat.sqlite_encryption_config import (
    SQLITE_CIPHER_NAMES, bound_sqlite_encryption_password, with_sqlite_encryption_password,
)
from .sqlite_browse_button import SQLiteBrowseButton
from .sqlite_connection_databases import _FontLineEdit


class _CipherCombo(QComboBox):
    """Keep editable values intact and reserve the native editor's height."""

    def __init__(self, parent=None):
        self._changing_style = True
        self._font_style = None
        super().__init__(parent)
        self.setEditable(True)
        self.setLineEdit(_FontLineEdit())
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._changing_style = False
        self._fit_native_font()

    def _fit_native_font(self):
        if getattr(self, "_changing_style", True):
            return
        application = QApplication.instance()
        if application.platformName() != "cocoa":
            return
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.rect = QRect(0, 0, 300, max(32, self.fontMetrics().height() + 12))
        # QMacStyle locks editable combos to the native control's small text
        # field. Query the application style without this widget's local style.
        native = application.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, None)
        clipped = native.height() < self.fontMetrics().height()
        self._changing_style = True
        try:
            if clipped:
                if self._font_style is None:
                    self._font_style = QStyleFactory.create("Fusion")
                    self._font_style.setParent(self)
                if self.style() is not self._font_style:
                    self.setStyle(self._font_style)
            elif self._font_style is not None:
                self.setStyle(None)
        finally:
            self._changing_style = False

    def _content_hint(self, hint):
        hint = QSize(hint)
        edit = self.lineEdit()
        if edit is None:
            return hint
        margins = edit.textMargins()
        required = edit.fontMetrics().height() + margins.top() + margins.bottom()
        # QComboBox's AdjustToContentsOnFirstShow can cache the small-font
        # width. The finite reference labels must still fit after live font
        # changes; arbitrary editable text instead keeps normal scrolling.
        text_width = max(self.fontMetrics().horizontalAdvance(name) for name in SQLITE_CIPHER_NAMES)
        text_width += margins.left() + margins.right() + self.fontMetrics().horizontalAdvance(" ")
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        for _ in range(8):
            option.rect = QRect(0, 0, hint.width(), hint.height())
            content = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                QStyle.SubControl.SC_ComboBoxEditField, self)
            height_deficit = required - content.height()
            width_deficit = text_width - content.width()
            if height_deficit <= 0 and width_deficit <= 0:
                break
            hint.setHeight(hint.height() + max(0, height_deficit))
            hint.setWidth(hint.width() + max(0, width_deficit))
        return hint

    def sizeHint(self):
        return self._content_hint(super().sizeHint())

    def minimumSizeHint(self):
        return self._content_hint(super().minimumSizeHint())

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.ApplicationFontChange, QEvent.Type.DevicePixelRatioChange):
            self._fit_native_font()
            self.updateGeometry()
        return handled


class SQLiteAdvancedSettings(QWidget):
    """One isolated SQLite draft; displayed masks are never password values."""

    changed = Signal()

    def __init__(self, diagram: QWidget, config: ConnectionConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("sqliteAdvancedSettings")
        self._base = copy_connection_config(config)
        self._endpoint = (config.id, config.database)
        self._password_provided = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 12)
        layout.setSpacing(12)
        layout.addWidget(diagram)

        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(13)
        unavailable = "设置位置、自动连接和初始查询尚未完成支持；此入口保留供后续参考验收。"
        self.settings_edit, self.settings_browse, settings_row = self._browse_row("设置位置", unavailable)
        self.settings_edit.setObjectName("sqliteSettingsDirectory")
        self.form.addRow("设置位置:", settings_row)
        self.auto_connect_check = QCheckBox("自动连接")
        self.auto_connect_check.setEnabled(False)
        self.auto_connect_check.setToolTip(unavailable)
        self.form.addRow("", self.auto_connect_check)
        self.initial_query_edit, self.initial_query_browse, initial_row = self._browse_row("初始查询", unavailable)
        self.initial_query_edit.setObjectName("sqliteInitialQuery")
        self.form.addRow("初始查询:", initial_row)
        self.form.addRow(self._separator())

        self.encrypted_check = QCheckBox("加密")
        self.encrypted_check.setObjectName("sqliteEncryptionEnabled")
        self.encrypted_check.setChecked(config.sqlite_encryption_enabled)
        self.form.addRow("", self.encrypted_check)
        self.password_edit = _FontLineEdit()
        self.password_edit.setObjectName("sqliteEncryptionPassword")
        self.password_edit.setMaxLength(65536)
        self.password_edit.setEchoMode(_FontLineEdit.EchoMode.Password)
        self.password_edit.setAccessibleName("数据库加密密码")
        secret = bound_sqlite_encryption_password(config)
        self.password_edit.setText(secret)
        self.form.addRow("密码:", self.password_edit)
        self.remember_check = QCheckBox("保存密码")
        self.remember_check.setObjectName("sqliteRememberEncryptionPassword")
        # The reference fresh draft has this checked even with encryption off.
        # Store restoration can independently set it from remembered metadata.
        self.remember_check.setChecked(True)
        self.remember_check.setToolTip("勾选后可将数据库加密密码保存到本机系统钥匙串。")
        self.form.addRow("", self.remember_check)
        self.form.addRow(self._separator())

        self.cipher_combo = _CipherCombo()
        self.cipher_combo.setObjectName("sqliteCipherName")
        self.cipher_combo.setAccessibleName("密码名称")
        self.cipher_combo.addItems(SQLITE_CIPHER_NAMES)
        self.cipher_combo.setCurrentIndex(self.cipher_combo.findText(config.sqlite_cipher_name))
        self.cipher_combo.setEditText(config.sqlite_cipher_name)
        self.form.addRow("密码名称:", self.cipher_combo)
        self.page_size_edit = _FontLineEdit(str(config.sqlite_cipher_page_size))
        self.page_size_edit.setObjectName("sqliteCipherLegacyPageSize")
        self.page_size_edit.setAccessibleName("密码旧版页面大小")
        self.page_size_edit.setValidator(QIntValidator(0, 2147483647, self.page_size_edit))
        self.page_size_edit.setToolTip("0 保留驱动默认值；其它值由加密驱动校验。")
        self.form.addRow("密码旧版页面大小:", self.page_size_edit)
        layout.addLayout(self.form)
        layout.addStretch(1)

        self.encrypted_check.toggled.connect(self._binding_changed)
        self.remember_check.toggled.connect(self._remember_changed)
        self.cipher_combo.currentTextChanged.connect(self._cipher_changed)
        self.page_size_edit.textChanged.connect(self._binding_changed)
        self.password_edit.textEdited.connect(self._password_edited)
        self._update_state()

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    @staticmethod
    def _browse_row(label, tooltip):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        edit, button = _FontLineEdit(), SQLiteBrowseButton()
        edit.setAccessibleName(label)
        button.setAccessibleName("选择" + label)
        for widget in (edit, button):
            widget.setEnabled(False)
            widget.setToolTip(tooltip)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return edit, button, row

    @property
    def remember_password(self):
        return self.encrypted_check.isChecked() and self.remember_check.isChecked()

    def set_endpoint(self, connection_id, database):
        endpoint = (connection_id, database)
        if endpoint != self._endpoint:
            self._endpoint = endpoint
            self._binding_changed()

    def set_password(self, password: str):
        """Record explicit new input; callers must never supply mask text."""
        if self.remember_password:
            self.password_edit.setText(password)
            self._password_provided = True
            self.changed.emit()

    def _forget_password(self):
        self.password_edit.clear()
        self.password_edit.setModified(False)
        self._password_provided = False
        self._base.sqlite_encryption_password = ""
        self._base.sqlite_encryption_password_binding = None

    def _binding_changed(self, *_):
        self._forget_password()
        self._update_state()
        self.changed.emit()

    def _remember_changed(self, checked):
        if not checked:
            self._forget_password()
        self._update_state()
        self.changed.emit()

    def _cipher_changed(self, text):
        self.cipher_combo.setToolTip(text)
        self._binding_changed()

    def _password_edited(self, *_):
        self._password_provided = True
        self.changed.emit()

    def _update_state(self):
        enabled = self.encrypted_check.isChecked()
        self.remember_check.setEnabled(enabled)
        self.password_edit.setEnabled(enabled and self.remember_check.isChecked())
        self.cipher_combo.setEnabled(enabled)
        legacy = "legacy" in self.cipher_combo.currentText()
        self.form.setRowVisible(self.page_size_edit, legacy)
        self.page_size_edit.setEnabled(enabled)
        self.cipher_combo.setToolTip(self.cipher_combo.currentText())

    def apply_to(self, config: ConnectionConfig) -> ConnectionConfig:
        """Collect metadata and an exactly bound process secret, without I/O."""
        draft = copy_connection_config(config)
        draft.sqlite_encryption_enabled = self.encrypted_check.isChecked()
        draft.sqlite_cipher_name = self.cipher_combo.currentText()
        text = self.page_size_edit.text()
        if not text or not self.page_size_edit.hasAcceptableInput():
            raise ValueError("请输入非负整数的密码旧版页面大小。")
        draft.sqlite_cipher_page_size = int(text) if "legacy" in draft.sqlite_cipher_name else 0
        draft.sqlite_encryption_password = self._base.sqlite_encryption_password
        draft.sqlite_encryption_password_binding = self._base.sqlite_encryption_password_binding
        draft = copy_connection_config(draft)
        if self._password_provided and self.remember_password:
            draft = with_sqlite_encryption_password(draft, self.password_edit.text())
        return draft
