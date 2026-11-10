from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

from PySide6.QtCore import QAbstractTableModel, QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QStyle, QStyleOptionButton, QTableView


def display_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


class DataModel(QAbstractTableModel):
    dirty_changed = Signal()

    def __init__(self, result, editable=False, parent=None, metadata=None):
        super().__init__(parent)
        self.result = result
        self.editable = editable
        self.pending = {}
        self.metadata = {c["name"]: c for c in metadata or []}

    def column_editable(self, column):
        meta = self.metadata.get(self.result.columns[column], {})
        return self.editable and not meta.get("generated") and meta.get("identity_generation") != "ALWAYS"

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.result.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.result.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        key = (index.row(), index.column())
        value = self.pending.get(key, self.result.rows[index.row()][index.column()])
        if role == Qt.ItemDataRole.DisplayRole:
            return display_value(value)
        if role == Qt.ItemDataRole.EditRole:
            return "" if value is None else display_value(value)
        if role == Qt.ItemDataRole.ForegroundRole:
            return QApplication.palette().color(QPalette.ColorRole.PlaceholderText) if value is None else None
        if role == Qt.ItemDataRole.BackgroundRole and key in self.pending:
            dark = QApplication.palette().color(QPalette.ColorRole.Base).lightness() < 128
            return QColor("#5a4520" if dark else "#fff0c2")
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, (int, float, Decimal)):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return display_value(value)[:8000]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.result.columns[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self.column_editable(index.column()) and not isinstance(self.result.rows[index.row()][index.column()], (bytes, memoryview, bytearray)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not self.column_editable(index.column()):
            return False
        original = self.result.rows[index.row()][index.column()]
        try:
            meta = self.metadata.get(self.result.columns[index.column()], {})
            if value is not None and (isinstance(original, (dict, list)) or meta.get("type", "").lower() in ("json", "jsonb")):
                value = json.loads(value) if isinstance(value, str) else value
            elif value is not None and isinstance(original, bool):
                if str(value).lower() not in ("true", "false", "1", "0"):
                    return False
                value = str(value).lower() in ("true", "1")
            elif value is not None and isinstance(original, (int, float, Decimal)):
                value = type(original)(value)
        except (ValueError, TypeError):
            return False
        key = (index.row(), index.column())
        if value == original:
            self.pending.pop(key, None)
        else:
            self.pending[key] = value
        self.dataChanged.emit(index, index)
        self.dirty_changed.emit()
        return True

    def discard(self):
        self.beginResetModel()
        self.pending.clear()
        self.endResetModel()
        self.dirty_changed.emit()

    def changes(self):
        grouped = {}
        for (row, column), value in self.pending.items():
            grouped.setdefault(row, {})[self.result.columns[column]] = value
        return [(dict(zip(self.result.columns, self.result.rows[row])), changes) for row, changes in grouped.items()]


class RowSelectorHeader(QHeaderView):
    """Native checkbox indicators for real selected rows, outside the data model."""

    def __init__(self, table):
        super().__init__(Qt.Orientation.Vertical, table)
        self.table = table
        self._selection_model = None
        self._anchor_row = None
        self.setObjectName("rowSelectorHeader")
        self.setFixedWidth(20)
        self.setMinimumSectionSize(20)
        self.setDefaultSectionSize(20)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setToolTip("选择或取消选择整行")
        self.setAccessibleName("行选择")

    def sizeHint(self):
        return QSize(20, super().sizeHint().height())

    def bind_selection_model(self):
        model = self.table.selectionModel()
        if model is self._selection_model:
            return
        if self._selection_model is not None:
            try:
                self._selection_model.selectionChanged.disconnect(self._selection_changed)
            except (RuntimeError, TypeError):
                pass
        self._selection_model = model
        self._anchor_row = None
        if model is not None:
            model.selectionChanged.connect(self._selection_changed)
        self.viewport().update()

    def _selection_changed(self, *_):
        self.viewport().update()

    def row_checked(self, row):
        selection = self.table.selectionModel()
        return bool(selection and selection.isRowSelected(row, QModelIndex()))

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        palette = self.table.palette()
        dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        base = palette.color(QPalette.ColorRole.Base)
        alternate = getattr(self.table, "_alternate_grid_color", QColor("#252525" if dark else "#f7f7f7"))
        painter.fillRect(rect, alternate if logical_index % 2 else base)
        painter.setPen(getattr(self.table, "_vertical_grid_color", palette.color(QPalette.ColorRole.Mid)))
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        option = QStyleOptionButton()
        option.initFrom(self)
        option.palette = palette
        checked = self.row_checked(logical_index)
        option.state &= ~(QStyle.StateFlag.State_On | QStyle.StateFlag.State_Off | QStyle.StateFlag.State_NoChange)
        option.state |= QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off
        width = min(16, self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, option, self))
        height = min(16, self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, option, self))
        option.rect = QRect(rect.x() + (rect.width() - width) // 2,
                            rect.y() + (rect.height() - height) // 2, width, height)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter, self)
        if dark and not checked:
            # Keep the native indicator visible when a platform style supplies
            # a light-appearance shadow against our dark neutral palette.
            painter.setPen(QColor("#686868"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(0, 0, -1, -1), 2, 2)
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        self.table._paint_unused_stripes(painter, self.viewport().rect(), header=True)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        row = self.logicalIndexAt(int(event.position().y()))
        model, selection = self.table.model(), self.table.selectionModel()
        if row < 0 or model is None or selection is None or not model.columnCount():
            event.accept()
            return
        flags = QItemSelectionModel.SelectionFlag
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self._anchor_row is not None:
            start, end = sorted((self._anchor_row, row))
            selected = QItemSelection(model.index(start, 0), model.index(end, model.columnCount() - 1))
            selection.select(selected, flags.Select | flags.Rows)
        else:
            # Checkboxes toggle independently; Ctrl-click also preserves the rest
            # of the existing cell/row selection instead of clearing it.
            selection.select(model.index(row, 0),
                             (flags.Deselect if self.row_checked(row) else flags.Select) | flags.Rows)
            self._anchor_row = row
        selection.setCurrentIndex(model.index(row, 0), flags.NoUpdate)
        self.table.setFocus(Qt.FocusReason.MouseFocusReason)
        self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class DataGrid(QTableView):
    def __init__(self, parent=None, row_selectors=True):
        super().__init__(parent)
        self.setObjectName("dataGrid")
        self._palette_signature = None
        font = self.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        self.setFont(font)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(False)
        self._row_selectors_enabled = bool(row_selectors)
        if row_selectors:
            self.setVerticalHeader(RowSelectorHeader(self))
        self.verticalHeader().setMinimumSectionSize(20)
        self.verticalHeader().setDefaultSectionSize(20)
        self.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setMinimumSectionSize(32)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.horizontalHeader().setResizeContentsPrecision(200)
        self.horizontalHeader().setDefaultSectionSize(100)
        self.horizontalHeader().setMinimumHeight(26)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.paints_unused_grid = True
        self._apply_palette()

    def set_row_selectors_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self._row_selectors_enabled:
            return
        self._row_selectors_enabled = enabled
        header = RowSelectorHeader(self) if enabled else QHeaderView(Qt.Orientation.Vertical, self)
        self.setVerticalHeader(header)
        header.setMinimumSectionSize(20)
        header.setDefaultSectionSize(20)
        if enabled:
            header.bind_selection_model()
        self.updateGeometries()

    def setModel(self, model):
        super().setModel(model)
        if isinstance(self.verticalHeader(), RowSelectorHeader):
            self.verticalHeader().bind_selection_model()
        if model is not None and model.columnCount() > 0:
            self.resizeColumnsToContents()
            for column in range(model.columnCount()):
                self.setColumnWidth(column, max(74, min(320, self.columnWidth(column))))
            name = str(model.headerData(0, Qt.Orientation.Horizontal) or "").casefold()
            if name == "id":
                self.setColumnWidth(0, 74)

    def _apply_palette(self):
        palette = QApplication.palette()
        base = palette.color(QPalette.ColorRole.Base)
        dark = base.lightness() < 128
        text = palette.color(QPalette.ColorRole.Text)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        signature = (dark, base.name(), text.name(), highlight.name())
        if getattr(self, "_palette_signature", None) == signature:
            return
        self._palette_signature = signature
        alternate = "#252525" if dark else "#f7f7f7"
        header = "#272727" if dark else "#eeeeee"
        border = "#454545" if dark else "#c7c7c7"
        self._vertical_grid_color = QColor(border)
        self._alternate_grid_color = QColor(alternate)
        self.setStyleSheet(f"""
            QTableView#dataGrid {{ background: {base.name()}; color: {text.name()};
                alternate-background-color: {alternate}; border: 0;
                selection-background-color: {highlight.name()}; selection-color: #ffffff; }}
            QTableView#dataGrid QHeaderView::section {{ background: {header}; color: {text.name()};
                font-size: 12px; font-weight: normal; min-height: 26px; padding: 0 4px;
                border: 0; border-right: 1px solid {border}; border-bottom: 1px solid {border}; }}
            QTableView#dataGrid QTableCornerButton::section {{ background: {header};
                border: 0; border-right: 1px solid {border}; border-bottom: 1px solid {border}; }}
        """)
        self.viewport().update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._apply_palette()

    def _paint_unused_stripes(self, painter, rect, header=False):
        model = self.model()
        if model is None:
            return
        count = model.rowCount()
        top = self.rowViewportPosition(count - 1) + self.rowHeight(count - 1) if count else 0
        if top >= rect.height():
            return
        # These are painted background bands, never indexes, records or cells.
        row, y = count, max(0, top)
        height = self.verticalHeader().defaultSectionSize()
        base = self.palette().color(QPalette.ColorRole.Base)
        alternate = getattr(self, "_alternate_grid_color", base)
        while y < rect.height():
            painter.fillRect(QRect(0, y, rect.width(), min(height, rect.height() - y)),
                             alternate if row % 2 else base)
            y += height
            row += 1
        if header:
            painter.setPen(getattr(self, "_vertical_grid_color", self.palette().color(QPalette.ColorRole.Mid)))
            painter.drawLine(rect.right(), max(0, top), rect.right(), rect.bottom())

    def paintEvent(self, event):
        super().paintEvent(event)
        model = self.model()
        if model is None:
            return
        painter = QPainter(self.viewport())
        header = self.horizontalHeader()
        occupied = max((header.sectionViewportPosition(c) + header.sectionSize(c)
                        for c in range(header.count()) if not header.isSectionHidden(c)), default=0)
        if 0 <= occupied < self.viewport().width() and model.rowCount():
            row = max(0, self.rowAt(0))
            while row < model.rowCount():
                top, height = self.rowViewportPosition(row), self.rowHeight(row)
                if top >= self.viewport().height():
                    break
                if height:
                    color = self._alternate_grid_color if row % 2 else self.palette().color(QPalette.ColorRole.Base)
                    painter.fillRect(QRect(occupied, top, self.viewport().width() - occupied, height), color)
                row += 1
        self._paint_unused_stripes(painter, self.viewport().rect())
        painter.setPen(QPen(getattr(self, "_vertical_grid_color", QColor("#454545")), 1))
        header = self.horizontalHeader()
        for visual in range(header.count()):
            column = header.logicalIndex(visual)
            if header.isSectionHidden(column):
                continue
            x = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
            if 0 <= x < self.viewport().width():
                painter.drawLine(x, 0, x, self.viewport().height())
        painter.end()

    def keyPressEvent(self, event):
        from PySide6.QtGui import QKeySequence
        if event.matches(QKeySequence.StandardKey.Copy):
            selected = self.selectedIndexes()
            if selected:
                rows = sorted({i.row() for i in selected})
                cols = sorted({i.column() for i in selected})
                keys = {(i.row(), i.column()) for i in selected}
                output = io.StringIO()
                writer = csv.writer(output, delimiter="\t", lineterminator="\n")
                for row in rows:
                    writer.writerow([self.model().data(self.model().index(row, col)) if (row, col) in keys else "" for col in cols])
                QApplication.clipboard().setText(output.getvalue())
            return
        super().keyPressEvent(event)
