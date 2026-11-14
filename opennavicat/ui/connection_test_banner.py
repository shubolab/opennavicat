"""Bounded connection-test result strip using original scalable artwork.

The successful strip is observed on Navicat 18's SQLite form. Clipboard payload
and failure presentation remain independent product behavior, not accepted
reference behavior. Callers supply both visible text and the copy payload.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QStyle, QStyleOption, QWidget,
)

from .icons import tool_icon


class _ElidingPlainText(QLabel):
    """Keep bounded source text accessible without enlarging the form for it."""

    def __init__(self, preferred_width, parent=None):
        super().__init__(parent)
        self._preferred_width = preferred_width
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(min(size.width(), self._preferred_width), size.height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group, QPalette.ColorRole.WindowText))
        painter.setFont(self.font())
        rect = self.contentsRect().adjusted(self.margin(), self.margin(), -self.margin(), -self.margin())
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, rect.width()))
        painter.drawText(rect, self.alignment(), text)


class _SuccessIcon(QWidget):
    """Let QIcon paint at the current screen density and palette each time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setAccessibleName('连接成功')
        self._icon = tool_icon('test_success', 16)

    def paintEvent(self, event):
        painter = QPainter(self)
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        self._icon.paint(painter, self.contentsRect(), Qt.AlignmentFlag.AlignCenter, mode)


class ConnectionTestBanner(QFrame):
    """Success status, version detail, adjacent copy action and trailing close.

    The display/tooltip limit is 512 characters; copy text is separately bounded
    at 1024 to retain existing callers' status prefixes and bounded versions.
    Long details elide inside their allocated area rather than moving the close
    button outside the dialog. No database or background work occurs here.
    """

    DISPLAY_LIMIT = 512
    COPY_LIMIT = 1024

    def __init__(self, parent=None):
        self._ready = False
        self._success = False
        self._copy_text = ''
        super().__init__(parent)
        self.setObjectName('connectionTestBanner')
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 12, 5)
        row.setSpacing(8)
        self.status_icon = _SuccessIcon(self)
        self.status_icon.setObjectName('connectionTestSuccessIcon')
        self.status_label = _ElidingPlainText(320, self)
        self.status_label.setObjectName('connectionTestStatus')
        self.detail_label = _ElidingPlainText(512, self)
        self.detail_label.setObjectName('connectionTestDetail')
        self.copy_button = self._button('copy', '复制连接测试结果')
        self.dismiss_button = self._button('dismiss', '关闭连接测试结果')
        row.addWidget(self.status_icon)
        row.addWidget(self.status_label)
        row.addWidget(self.detail_label)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        row.addWidget(self.dismiss_button)
        self.copy_button.clicked.connect(self.copy_result)
        self.dismiss_button.clicked.connect(self.hide)
        self._ready = True
        self.clear_result()

    def _button(self, role, name):
        button = QPushButton(self)
        button.setIcon(tool_icon(role, 16))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(22, 24)
        button.setAutoDefault(False)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setStyleSheet('QPushButton { padding: 0; min-width: 0; min-height: 0; border: 0; background: transparent; }')
        return button

    @property
    def copy_text(self):
        return self._copy_text

    def set_result(self, success, status_text, *, detail_text='', copy_text=None, tooltip=''):
        self._success = bool(success)
        self.status_label.setText(status_text[:self.DISPLAY_LIMIT])
        self.detail_label.setText(detail_text[:self.DISPLAY_LIMIT])
        self.detail_label.setVisible(bool(detail_text))
        self.status_icon.setVisible(self._success)
        self._copy_text = (status_text if copy_text is None else copy_text)[:self.COPY_LIMIT]
        self.copy_button.setEnabled(bool(self._copy_text))
        self.status_label.setToolTip(tooltip[:self.DISPLAY_LIMIT])
        self.detail_label.setToolTip(tooltip[:self.DISPLAY_LIMIT])
        self._update_status_color()
        self.show()
        self.updateGeometry()
        self.update()

    def clear_result(self):
        self._success = False
        self._copy_text = ''
        self.status_label.clear()
        self.detail_label.clear()
        self.status_label.setToolTip('')
        self.detail_label.setToolTip('')
        self.status_icon.hide()
        self.copy_button.setEnabled(False)
        self.hide()

    def copy_result(self):
        if self._copy_text:
            QApplication.clipboard().setText(self._copy_text)

    def _update_status_color(self):
        # The dark success band is directly observed. Light-mode tint and its
        # contrast are a local rendering choice pending a light reference run.
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        color = '#68bc58' if dark else '#287724'
        self.status_label.setStyleSheet('color: ' + color + '; background: transparent;' if self._success else '')
        metrics = self.status_label.fontMetrics()
        self.status_label.setMinimumWidth(min(metrics.horizontalAdvance(self.status_label.text()),
                                             metrics.horizontalAdvance('连接成功')))

    def changeEvent(self, event):
        super().changeEvent(event)
        if self._ready and event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange,
                            QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._update_status_color()
            self.updateGeometry()
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._success:
            dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#435241' if dark else '#e3efdf'))
