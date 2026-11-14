"""The compact, native three-dot file button shared by SQLite forms."""
from PySide6.QtCore import QEvent, QRect
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton


class SQLiteBrowseButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__("...", parent)
        self.setObjectName("sqliteFileBrowseButton")
        self.setAutoDefault(False)
        self.setAccessibleName("选择数据库文件")
        self.setToolTip("选择数据库文件")

    def sizeHint(self):
        hint = super().sizeHint()
        # Discard the native standard-button width, but retain real native
        # chrome. Application QSS can consume more than this compact inset.
        width = self.fontMetrics().horizontalAdvance(self.text())
        height = self.fontMetrics().height()
        hint.setWidth(max(20, width + 8))
        option = QStyleOptionButton()
        self.initStyleOption(option)
        for _ in range(8):
            option.rect = QRect(0, 0, hint.width(), hint.height())
            content = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
            extra_width = max(0, width - content.width())
            extra_height = max(0, height - content.height())
            if not extra_width and not extra_height:
                break
            hint.setWidth(hint.width() + extra_width)
            hint.setHeight(hint.height() + extra_height)
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.ApplicationFontChange, QEvent.Type.ContentsRectChange,
                            QEvent.Type.DevicePixelRatioChange):
            self.updateGeometry()
        return handled
