"""A small native SQL editor with highlighting, completion and a line gutter."""

from __future__ import annotations

import re

import sqlparse
from PySide6.QtCore import QEvent, Qt, QRect, QSize, QStringListModel, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QKeySequence, QPainter, QPalette, QTextCharFormat,
    QTextCursor, QTextFormat, QSyntaxHighlighter,
)
from PySide6.QtWidgets import (
    QApplication, QCompleter, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)


SQL_WORDS = """
SELECT FROM WHERE JOIN INNER LEFT RIGHT FULL CROSS OUTER ON USING AS AND OR NOT
NULL IS IN BETWEEN LIKE ILIKE EXISTS DISTINCT ALL UNION INTERSECT EXCEPT WITH
RECURSIVE INSERT INTO VALUES UPDATE SET DELETE CREATE ALTER DROP TABLE VIEW
INDEX UNIQUE PRIMARY KEY FOREIGN REFERENCES CONSTRAINT DEFAULT CHECK DATABASE
SCHEMA TRIGGER FUNCTION PROCEDURE IF BEGIN COMMIT ROLLBACK SAVEPOINT RELEASE
TRANSACTION EXPLAIN ANALYZE ORDER BY ASC DESC GROUP HAVING LIMIT OFFSET RETURNING
CASE WHEN THEN ELSE END OVER PARTITION ROWS RANGE CURRENT ROW PRECEDING FOLLOWING
COUNT SUM MIN MAX AVG COALESCE CAST ROUND CONCAT NOW CURRENT_TIMESTAMP TRUE FALSE
INTEGER INT BIGINT SMALLINT REAL FLOAT DOUBLE NUMERIC DECIMAL TEXT VARCHAR CHAR
BOOLEAN BOOL DATE TIME TIMESTAMP JSON JSONB BLOB BYTEA AUTOINCREMENT SERIAL
""".split()


def pretty_sql(sql: str) -> str:
    """Format SQL without changing string literals or quoted identifiers."""
    return sqlparse.format(sql, reindent=True, keyword_case="upper").strip()


def _format(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    result = QTextCharFormat()
    result.setForeground(QColor(color))
    result.setFontWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    result.setFontItalic(italic)
    return result


class SqlHighlighter(QSyntaxHighlighter):
    """Lexical SQL colors, with comment/string state carried between blocks."""

    _token = re.compile(
        r"(--[^\n]*|/\*|\*/|'|\"|`|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z_0-9$]*\b)"
    )

    def __init__(self, document):
        super().__init__(document)
        self.words = set(SQL_WORDS)
        self.set_palette(QApplication.palette())

    def set_palette(self, palette):
        dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        self.keyword = _format("#8bc7eb" if dark else "#205b89")
        self.string = _format("#d39d85" if dark else "#9f4e37")
        self.comment = _format("#6e8c72" if dark else "#608064")
        self.number = _format("#b5cea8" if dark else "#37745f")
        self.identifier = _format(palette.color(QPalette.ColorRole.Text).name())
        self.rehighlight()

    def highlightBlock(self, text: str):
        # state 1: block comment, 2: single-quoted string. Quoted identifiers
        # deliberately stay local to a line; most dialects reject their newline.
        previous = self.previousBlockState()
        state = previous if previous in (1, 2) else 0
        self.setCurrentBlockState(state if not text else 0)
        i = 0
        while i < len(text):
            if state == 1:
                end = text.find("*/", i)
                if end < 0:
                    self.setFormat(i, len(text) - i, self.comment)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(i, end + 2 - i, self.comment)
                i, state = end + 2, 0
                continue
            if state == 2:
                start = i
                while i < len(text):
                    if text[i] == "'":
                        if i + 1 < len(text) and text[i + 1] == "'":
                            i += 2
                            continue
                        i += 1
                        state = 0
                        break
                    i += 1
                self.setFormat(start, i - start, self.string)
                if state == 2:
                    self.setCurrentBlockState(2)
                    return
                continue
            match = self._token.search(text, i)
            if not match:
                return
            token = match.group()
            start, i = match.start(), match.end()
            if token.startswith("--"):
                self.setFormat(start, len(text) - start, self.comment)
                return
            if token == "/*":
                self.setFormat(start, 2, self.comment)
                state = 1
            elif token == "'":
                self.setFormat(start, 1, self.string)
                state = 2
            elif token in ('"', '`'):
                end = i
                while end < len(text):
                    if text[end] == token:
                        if end + 1 < len(text) and text[end + 1] == token:
                            end += 2
                            continue
                        end += 1
                        break
                    end += 1
                self.setFormat(start, end - start, self.identifier)
                i = end
            elif token[0].isdigit():
                self.setFormat(start, len(token), self.number)
            elif token.upper() in self.words:
                self.setFormat(start, len(token), self.keyword)
        if state:
            self.setCurrentBlockState(state)


class _LineArea(QWidget):
    def __init__(self, editor: "SqlEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_area(event)


class SqlEditor(QPlainTextEdit):
    execute_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sqlEditor")
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = SqlHighlighter(self.document())
        self.line_area = _LineArea(self)
        self._base_color = self.palette().color(QPalette.ColorRole.Base)
        self._number_color = self.palette().color(QPalette.ColorRole.Text)
        self._line_color = QColor("#222222")
        self._apply_palette()
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._update_line_area)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_margin()
        self._highlight_line()
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated[str].connect(self._insert_completion)
        self.set_completions([])
        self._find_dialog = None
        self._find_input = None

    def selected_sql(self) -> str:
        cursor = self.textCursor()
        return (cursor.selectedText().replace("\u2029", "\n")
                if cursor.hasSelection() else self.toPlainText())

    def set_completions(self, words: list[str]):
        candidates = sorted(set(SQL_WORDS + list(words)), key=str.casefold)
        self._completion_model = QStringListModel(candidates, self.completer)
        self.completer.setModel(self._completion_model)

    def line_area_width(self):
        digits = len(str(max(1, self.blockCount())))
        return 32 + self.fontMetrics().horizontalAdvance("9") * (digits - 1)

    def _apply_palette(self):
        palette = QApplication.palette()
        self._base_color = palette.color(QPalette.ColorRole.Base)
        self._number_color = palette.color(QPalette.ColorRole.Text)
        self._line_color = QColor("#222222" if self._base_color.lightness() < 128 else "#f6f6f6")
        if hasattr(self, "highlighter"):
            self.highlighter.set_palette(palette)
        if hasattr(self, "line_area"):
            self.line_area.update()
            self._highlight_line()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._apply_palette()

    def _update_margin(self, *_):
        width = self.line_area_width()
        self.setViewportMargins(width, 0, 0, 0)
        rect = self.contentsRect()
        self.line_area.setGeometry(QRect(rect.left(), rect.top(), width, rect.height()))

    def _update_line_area(self, rect: QRect, dy: int):
        if dy:
            self.line_area.scroll(0, dy)
        else:
            self.line_area.update(0, rect.y(), self.line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_area.setGeometry(QRect(rect.left(), rect.top(), self.line_area_width(), rect.height()))

    def paint_line_area(self, event):
        painter = QPainter(self.line_area)
        painter.fillRect(event.rect(), self._base_color)
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(self._number_color)
                painter.drawText(0, top, self.line_area.width() - 10,
                                 self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    def _highlight_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(self._line_color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self.line_area.update()

    def _word(self):
        cursor = self.textCursor()
        position = cursor.position()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfWord,
                            QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText() if cursor.position() != position else ""

    def _insert_completion(self, completion: str):
        cursor = self.textCursor()
        prefix = self.completer.completionPrefix()
        if prefix:
            cursor.movePosition(QTextCursor.MoveOperation.Left,
                                QTextCursor.MoveMode.KeepAnchor, len(prefix))
        cursor.insertText(completion)
        self.setTextCursor(cursor)

    def _complete(self, force=False):
        word = self._word()
        if not force and len(word) < 2:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(word)
        if self.completer.completionCount() == 0:
            self.completer.popup().hide()
            return
        popup = self.completer.popup()
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(220, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width()))
        self.completer.complete(rect)

    def _find(self, backwards=False):
        if not self._find_input or not self._find_input.text():
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag.FindBackward if backwards else QTextDocument.FindFlag(0)
        if not self.find(self._find_input.text(), flags):
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End if backwards else QTextCursor.MoveOperation.Start)
            self.setTextCursor(cursor)
            self.find(self._find_input.text(), flags)

    def show_find(self):
        if self._find_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Find in SQL")
            layout = QVBoxLayout(dialog)
            self._find_input = QLineEdit(dialog)
            self._find_input.setPlaceholderText("Find text")
            layout.addWidget(self._find_input)
            buttons = QHBoxLayout()
            previous, following, close = QPushButton("Previous"), QPushButton("Next"), QPushButton("Close")
            buttons.addWidget(previous)
            buttons.addWidget(following)
            buttons.addWidget(close)
            layout.addLayout(buttons)
            previous.clicked.connect(lambda: self._find(True))
            following.clicked.connect(lambda: self._find(False))
            close.clicked.connect(dialog.hide)
            self._find_input.returnPressed.connect(lambda: self._find(False))
            following.setDefault(True)
            self._find_dialog = dialog
        if self.textCursor().hasSelection():
            self._find_input.setText(self.textCursor().selectedText())
        self._find_dialog.show()
        self._find_dialog.raise_()
        self._find_input.setFocus()
        self._find_input.selectAll()

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        command = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if self.completer.popup().isVisible() and event.key() in (
            Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab
        ) and not command:
            event.ignore()
            return
        if command and event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.completer.popup().hide()
            self.execute_requested.emit()
            return
        if event.matches(QKeySequence.StandardKey.Find):
            self.show_find()
            return
        if command and event.key() == Qt.Key.Key_Space:
            self._complete(True)
            return
        if event.key() == Qt.Key.Key_Tab and not modifiers:
            self.insertPlainText("    ")
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            line = self.textCursor().block().text()
            indentation = re.match(r"[ \t]*", line).group()
            super().keyPressEvent(event)
            self.insertPlainText(indentation)
            return
        super().keyPressEvent(event)
        if not command and event.text() and (event.text().isalnum() or event.text() == "_"):
            self._complete()
        else:
            self.completer.popup().hide()
