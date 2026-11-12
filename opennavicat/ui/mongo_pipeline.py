"""Native Qt workspace for MongoDB aggregation pipelines."""
from __future__ import annotations

from copy import deepcopy
import json

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDrag, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QGraphicsObject, QGraphicsPathItem, QGraphicsProxyWidget, QGraphicsScene,
    QGraphicsView, QSpinBox, QSplitter, QStackedWidget, QTabWidget, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from bson import BSON

from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import (
    REFERENCE_OPTION_DEFAULTS, STAGE_NAMES, PipelineDefinition, PipelineExplain,
    PipelinePreview, PipelineStage,
    explain_pipeline, from_nagg, parse_mql, pipeline_script, preview_pipeline,
    to_nagg, validate_pipeline,
)
from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps, canonical_loads
from opennavicat.mongodb_stage_templates import CREATE_TOOL_STAGES, STAGE_EDITOR_TEMPLATES

from .icons import tool_icon
from .mongo_collation import LOCALE_SUGGESTIONS


class _PipelineStageCard(QPushButton):
    dropRequested = Signal(int, int)

    def __init__(self, page, index, text, parent=None):
        super().__init__(text, parent)
        self.page = page
        self.index = index
        self._drag_start = None
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_start is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint() - self._drag_start).manhattanLength()
                < self.page.drag_threshold()):
            return super().mouseMoveEvent(event)
        mime = QMimeData()
        mime.setData("application/x-opennavicat-pipeline-stage", str(self.index).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-opennavicat-pipeline-stage"):
            event.acceptProposedAction()
            self.setProperty("dropTarget", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dropTarget", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        try:
            source = int(bytes(event.mimeData().data(
                "application/x-opennavicat-pipeline-stage")).decode("ascii"))
        except Exception:
            event.ignore()
            return
        self.dropRequested.emit(source, self.index)
        event.acceptProposedAction()


class MongoPipelineMatchTool(QWidget):
    """Structured AND/OR builder for simple $match conditions."""
    changed = Signal()

    OPERATORS = (
        ("等于", "$eq"), ("不等于", "$ne"), ("大于", "$gt"), ("大于等于", "$gte"),
        ("小于", "$lt"), ("小于等于", "$lte"), ("包含于", "$in"),
        ("不包含于", "$nin"), ("正则", "$regex"), ("存在", "$exists"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        combinator_row = QHBoxLayout()
        combinator_row.addWidget(QLabel("组合:"))
        self.combinator = QComboBox()
        self.combinator.addItem("全部条件（AND）", "and")
        self.combinator.addItem("任一条件（OR）", "or")
        combinator_row.addWidget(self.combinator)
        combinator_row.addStretch()
        layout.addLayout(combinator_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["字段", "运算符", "值"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.remove_button = QPushButton("−")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_row)
        self.combinator.currentIndexChanged.connect(self._changed)
        self._complex = None

    def add_row(self, field="", operator="$eq", value=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        operator_combo = QComboBox()
        for label, operator_value in self.OPERATORS:
            operator_combo.addItem(label, operator_value)
        index = operator_combo.findData(operator)
        operator_combo.setCurrentIndex(index if index >= 0 else 0)
        if isinstance(value, str):
            value_text = value
        elif isinstance(value, dict):
            value_text = canonical_dumps(value)
        else:
            value_text = json.dumps(value)
        value_edit = QLineEdit(value_text)
        self.table.setCellWidget(row, 0, field_edit)
        self.table.setCellWidget(row, 1, operator_combo)
        self.table.setCellWidget(row, 2, value_edit)
        field_edit.textChanged.connect(self._changed)
        operator_combo.currentIndexChanged.connect(self._changed)
        value_edit.textChanged.connect(self._changed)

    def remove_row(self):
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            self._complex = None
            if not isinstance(body, dict):
                self._complex = body
                self.setEnabled(False)
                return
            keys = list(body)
            if body and set(keys) == {"$or"}:
                self.combinator.setCurrentIndex(1)
                clauses = body["$or"]
                if not isinstance(clauses, list):
                    self._complex = body
                    self.setEnabled(False)
                    return
                merged = {}
                for clause in clauses:
                    if not isinstance(clause, dict):
                        self._complex = body
                        self.setEnabled(False)
                        return
                    merged.update(clause)
            elif any(key.startswith("$") for key in keys):
                self._complex = body
                self.setEnabled(False)
                return
            else:
                self.combinator.setCurrentIndex(0)
                merged = body
            for field, value in merged.items():
                if isinstance(value, dict) and len(value) == 1 and next(iter(value)).startswith("$"):
                    operator, operand = next(iter(value.items()))
                    if operator in {item[1] for item in self.OPERATORS}:
                        self.add_row(field, operator, operand)
                    else:
                        self._complex = body
                        self.setEnabled(False)
                        return
                else:
                    self.add_row(field, "$eq", value)
            self.setEnabled(True)
        finally:
            self._loading = False
            self.add_button.setEnabled(self._complex is None)
            self.remove_button.setEnabled(self._complex is None)

    def document(self):
        if self._complex is not None:
            return deepcopy(self._complex)
        conditions = []
        for row in range(self.table.rowCount()):
            field = self.table.cellWidget(row, 0).text().strip()
            operator = self.table.cellWidget(row, 1).currentData()
            text = self.table.cellWidget(row, 2).text().strip()
            if not field:
                continue
            value = canonical_loads('{"value":' + (text or "null") + '}')["value"]
            conditions.append({field: value if operator == "$eq" else {operator: value}})
        if self.combinator.currentData() == "or" and len(conditions) > 1:
            return {"$or": conditions}
        result = {}
        for condition in conditions:
            result.update(condition)
        return result


def _parse_stage_value(text):
    text = str(text).strip()
    if not text:
        return ""
    try:
        return canonical_loads('{"value":' + text + '}')["value"]
    except Exception:
        return text


def _stage_value_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return canonical_dumps(value)
    if isinstance(value, list):
        return canonical_dumps({"value": value})
    return json.dumps(value)


class _PipelineRowsTool(QWidget):
    changed = Signal()

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(labels))
        self.table.setHorizontalHeaderLabels(labels)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.remove_button = QPushButton("−")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_row)

    def add_row(self, *values):
        raise NotImplementedError

    def remove_row(self):
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()


class MongoPipelineProjectTool(_PipelineRowsTool):
    MODES = (("包含", "include"), ("排除", "exclude"), ("表达式", "expression"))

    def __init__(self, parent=None):
        super().__init__(["字段", "操作", "表达式"], parent)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def add_row(self, field="", mode="include", expression=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        mode_combo = QComboBox()
        for label, value in self.MODES:
            mode_combo.addItem(label, value)
        mode_combo.setCurrentIndex(max(0, mode_combo.findData(mode)))
        expression_edit = QLineEdit(expression)
        expression_edit.setEnabled(mode == "expression")
        mode_combo.currentIndexChanged.connect(
            lambda _index, editor=expression_edit, combo=mode_combo: editor.setEnabled(combo.currentData() == "expression"))
        self.table.setCellWidget(row, 0, field_edit)
        self.table.setCellWidget(row, 1, mode_combo)
        self.table.setCellWidget(row, 2, expression_edit)
        for widget in (field_edit, expression_edit):
            widget.textChanged.connect(self._changed)
        mode_combo.currentIndexChanged.connect(self._changed)

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            if not isinstance(body, dict) or any(key.startswith("$") for key in body):
                self.setEnabled(False)
                return
            for field, value in body.items():
                if value in (1, True):
                    self.add_row(field, "include", "")
                elif value in (0, False):
                    self.add_row(field, "exclude", "")
                else:
                    self.add_row(field, "expression", _stage_value_text(value))
            self.setEnabled(True)
        finally:
            self._loading = False

    def document(self):
        result = {}
        for row in range(self.table.rowCount()):
            field = self.table.cellWidget(row, 0).text().strip()
            if not field:
                continue
            mode = self.table.cellWidget(row, 1).currentData()
            if mode == "include":
                result[field] = 1
            elif mode == "exclude":
                result[field] = 0
            else:
                result[field] = _parse_stage_value(self.table.cellWidget(row, 2).text())
        return result


class MongoPipelineSortTool(_PipelineRowsTool):
    def __init__(self, parent=None):
        super().__init__(["字段", "方向"], parent)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

    def add_row(self, field="", direction=1):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        direction_combo = QComboBox()
        direction_combo.addItem("ASC", 1)
        direction_combo.addItem("DESC", -1)
        direction_combo.setCurrentIndex(0 if direction in (1, "ASC") else 1)
        self.table.setCellWidget(row, 0, field_edit)
        self.table.setCellWidget(row, 1, direction_combo)
        field_edit.textChanged.connect(self._changed)
        direction_combo.currentIndexChanged.connect(self._changed)

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            if not isinstance(body, dict) or any(value not in (1, -1) for value in body.values()):
                self.setEnabled(False)
                return
            for field, direction in body.items():
                self.add_row(field, direction)
            self.setEnabled(True)
        finally:
            self._loading = False

    def document(self):
        return {self.table.cellWidget(row, 0).text().strip(): self.table.cellWidget(row, 1).currentData()
                for row in range(self.table.rowCount())
                if self.table.cellWidget(row, 0).text().strip()}


class MongoPipelineGroupTool(QWidget):
    changed = Signal()
    ACCUMULATORS = ("$sum", "$avg", "$min", "$max", "$first", "$last", "$push", "$addToSet")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        id_row = QHBoxLayout()
        id_row.addWidget(QLabel("分组 _id:"))
        self.id_edit = QLineEdit("null")
        id_row.addWidget(self.id_edit, 1)
        layout.addLayout(id_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["输出字段", "累加器", "表达式"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.remove_button = QPushButton("−")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.id_edit.textChanged.connect(self._changed)
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_row)

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()

    def add_row(self, field="", operator="$sum", expression="1"):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        operator_combo = QComboBox()
        operator_combo.addItems(self.ACCUMULATORS)
        operator_combo.setCurrentText(operator)
        expression_edit = QLineEdit(expression)
        self.table.setCellWidget(row, 0, field_edit)
        self.table.setCellWidget(row, 1, operator_combo)
        self.table.setCellWidget(row, 2, expression_edit)
        field_edit.textChanged.connect(self._changed)
        operator_combo.currentIndexChanged.connect(self._changed)
        expression_edit.textChanged.connect(self._changed)

    def remove_row(self):
        row = self.table.currentRow()
        if row < 0:
            row = self.table.rowCount() - 1
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            if not isinstance(body, dict) or set(body) - {"_id"} and not any(
                    isinstance(value, dict) and len(value) == 1 and next(iter(value)) in self.ACCUMULATORS
                    for key, value in body.items() if key != "_id"):
                self.setEnabled(False)
                return
            if "_id" not in body:
                self.setEnabled(False)
                return
            self.id_edit.setText(_stage_value_text(body["_id"]))
            for field, value in body.items():
                if field == "_id":
                    continue
                if not isinstance(value, dict) or len(value) != 1:
                    self.setEnabled(False)
                    return
                operator, expression = next(iter(value.items()))
                if operator not in self.ACCUMULATORS:
                    self.setEnabled(False)
                    return
                self.add_row(field, operator, _stage_value_text(expression))
            self.setEnabled(True)
        finally:
            self._loading = False

    def document(self):
        result = {"_id": _parse_stage_value(self.id_edit.text())}
        for row in range(self.table.rowCount()):
            field = self.table.cellWidget(row, 0).text().strip()
            if field:
                result[field] = {self.table.cellWidget(row, 1).currentText():
                                 _parse_stage_value(self.table.cellWidget(row, 2).text())}
        return result


class MongoPipelineScalarTool(QWidget):
    changed = Signal()

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        layout = QHBoxLayout(self)
        self.number = QSpinBox()
        self.number.setRange(1 if kind == "sample" else 0, 100000)
        self.number.setValue(100 if kind in {"limit", "sample"} else 0)
        self.text = QLineEdit("count")
        if kind in {"limit", "skip", "sample"}:
            layout.addWidget(QLabel("数量:"))
            layout.addWidget(self.number)
            self.text.hide()
        else:
            layout.addWidget(QLabel("输出字段:"))
            layout.addWidget(self.text)
            self.number.hide()
        layout.addStretch()
        self.number.valueChanged.connect(lambda _value: self.changed.emit())
        self.text.textChanged.connect(lambda _value: self.changed.emit())

    def load(self, body):
        self.setEnabled(True)
        if self.kind == "count":
            if not isinstance(body, str):
                self.setEnabled(False)
                return
            self.text.setText(body)
        elif isinstance(body, bool) or not isinstance(body, int):
            self.setEnabled(False)
        else:
            self.number.setValue(body)

    def document(self):
        return self.text.text() if self.kind == "count" else self.number.value()


class MongoPipelineFieldsTool(_PipelineRowsTool):
    def __init__(self, parent=None):
        super().__init__(["字段", "表达式"], parent)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def add_row(self, field="", expression=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        expression_edit = QLineEdit(expression)
        self.table.setCellWidget(row, 0, field_edit)
        self.table.setCellWidget(row, 1, expression_edit)
        field_edit.textChanged.connect(self._changed)
        expression_edit.textChanged.connect(self._changed)

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            if not isinstance(body, dict):
                self.setEnabled(False)
                return
            for field, value in body.items():
                self.add_row(field, _stage_value_text(value))
            self.setEnabled(True)
        finally:
            self._loading = False

    def document(self):
        return {self.table.cellWidget(row, 0).text().strip():
                _parse_stage_value(self.table.cellWidget(row, 1).text())
                for row in range(self.table.rowCount())
                if self.table.cellWidget(row, 0).text().strip()}


class MongoPipelineUnsetTool(_PipelineRowsTool):
    def __init__(self, parent=None):
        super().__init__(["字段"], parent)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def add_row(self, field=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        field_edit = QLineEdit(field)
        self.table.setCellWidget(row, 0, field_edit)
        field_edit.textChanged.connect(self._changed)

    def load(self, body):
        self._loading = True
        try:
            self.table.setRowCount(0)
            fields = [body] if isinstance(body, str) else body
            if not isinstance(fields, list) or any(not isinstance(value, str) for value in fields):
                self.setEnabled(False)
                return
            for field in fields:
                self.add_row(field)
            self.setEnabled(True)
        finally:
            self._loading = False

    def document(self):
        fields = [self.table.cellWidget(row, 0).text().strip() for row in range(self.table.rowCount())]
        fields = [field for field in fields if field]
        return fields[0] if len(fields) == 1 else fields


class MongoPipelineUnwindTool(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.path = QLineEdit("$field")
        self.preserve = QCheckBox("保留空值和缺失值")
        self.index = QLineEdit()
        layout.addRow("路径:", self.path)
        layout.addRow("", self.preserve)
        layout.addRow("索引字段:", self.index)
        self.path.textChanged.connect(lambda _value: self.changed.emit())
        self.preserve.toggled.connect(lambda _value: self.changed.emit())
        self.index.textChanged.connect(lambda _value: self.changed.emit())

    def load(self, body):
        self.setEnabled(True)
        if isinstance(body, str):
            self.path.setText(body)
            self.preserve.setChecked(False)
            self.index.clear()
        elif isinstance(body, dict) and set(body) <= {"path", "preserveNullAndEmptyArrays", "includeArrayIndex"}:
            self.path.setText(str(body.get("path", "")))
            self.preserve.setChecked(body.get("preserveNullAndEmptyArrays") is True)
            self.index.setText(str(body.get("includeArrayIndex", "")))
        else:
            self.setEnabled(False)

    def document(self):
        path = self.path.text().strip()
        result = {"path": path}
        if self.preserve.isChecked():
            result["preserveNullAndEmptyArrays"] = True
        if self.index.text().strip():
            result["includeArrayIndex"] = self.index.text().strip()
        return result


class _ExplainNodeItem(QGraphicsObject):
    def __init__(self, node, expensive, callback):
        super().__init__()
        self.node = node
        self.expensive = bool(expensive)
        self.callback = callback
        self.selected = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{node.stage}\nexecutionTimeMillis: {node.total_elapsed_ms or 0:g}\n"
            f"nReturned: {node.returned if node.returned is not None else '---'}")

    def boundingRect(self):
        return QRectF(0, 0, 58, 72)

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon = QRectF(10, 9, 34, 34)
        painter.setBrush(QColor("#f1f4f7"))
        painter.setPen(QPen(QColor("#8db5e8") if self.selected else QColor("#89919c"), 1.5 if self.selected else 0.8))
        painter.drawRoundedRect(icon, 3, 3)
        painter.setPen(QPen(QColor("#54677c"), 1))
        for offset, width in ((14, 20), (19, 16), (24, 22), (29, 12)):
            painter.drawLine(QPointF(17, offset + 8), QPointF(17 + width, offset + 8))
        painter.setPen(QColor("#d9dde3"))
        painter.drawText(QRectF(0, -1, 58, 11), Qt.AlignmentFlag.AlignLeft, f"{self.node.total_elapsed_ms or 0:g}")
        painter.setPen(QColor("#d58a58") if self.expensive else QColor("#d9dde3"))
        painter.drawText(QRectF(0, 49, 58, 18), Qt.AlignmentFlag.AlignHCenter, self.node.stage)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.callback(self)
        super().mousePressEvent(event)


class MongoExplainCanvas(QGraphicsView):
    """Native left-to-right execution-plan canvas matching the reference."""

    NODE_WIDTH = 58
    NODE_HEIGHT = 72
    COLUMN_GAP = 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mongoExplainCanvas")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#202124"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._scale = 1.0
        self._threshold = 50.0
        self._root_elapsed = None
        self._items = []
        self._selected = None
        self._detail_proxy = None
        self.detail_view = None
        self.scene = None
        self.set_explain(PipelineExplain(raw={}, raw_text=""))

    def set_explain(self, explain):
        if self.scene is not None:
            self.scene.deleteLater()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self._items = []
        self._selected = None
        self._detail_proxy = None
        self.detail_view = None
        roots = explain.roots
        self._root_elapsed = roots[0].total_elapsed_ms if roots else None
        positions = {}
        next_row = [90]

        def place(node, depth):
            if node.children:
                child_positions = [place(child, depth + 1) for child in node.children]
                y = sum(item[1] for item in child_positions) / len(child_positions)
            else:
                y = next_row[0]
                next_row[0] += 118
            positions[id(node)] = (70 + depth * self.COLUMN_GAP, y)
            return positions[id(node)]

        for root in roots:
            place(root, 0)
            next_row[0] += 28
        edge_pen = QPen(QColor("#80a9d9"), 2)
        for root in roots:
            self._add_tree(root, positions, edge_pen)
        options = []
        for root in roots:
            options.extend([node for node in self._walk(root)])
        left = min((positions[id(node)][0] for node in options), default=0)
        right = max((positions[id(node)][0] + self.NODE_WIDTH for node in options), default=400)
        top = min((positions[id(node)][1] for node in options), default=0)
        bottom = max((positions[id(node)][1] + self.NODE_HEIGHT for node in options), default=300)
        self.scene.setSceneRect(QRectF(left - 35, top - 65, right - left + 650, bottom - top + 220))
        if options:
            self.centerOn((left + right) / 2, (top + bottom) / 2)
        self.resetTransform()
        self._scale = 1.0

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _add_tree(self, node, positions, edge_pen):
        parent_x, parent_y = positions[id(node)]
        for child in node.children:
            child_x, child_y = positions[id(child)]
            path = QPainterPath(QPointF(parent_x + self.NODE_WIDTH, parent_y + 26))
            midpoint = (parent_x + self.NODE_WIDTH + child_x) / 2
            path.cubicTo(midpoint, parent_y + 26, midpoint, child_y + 26, child_x, child_y + 26)
            edge = QGraphicsPathItem(path)
            edge.setPen(edge_pen)
            self.scene.addItem(edge)
        elapsed = node.stage_elapsed_ms
        percent = (100.0 * elapsed / self._root_elapsed
                   if elapsed is not None and self._root_elapsed else 0.0)
        item = _ExplainNodeItem(node, percent >= self._threshold, self._select_item)
        item.setPos(parent_x, parent_y)
        self.scene.addItem(item)
        self._items.append(item)
        for child in node.children:
            self._add_tree(child, positions, edge_pen)

    def _select_item(self, item):
        if self._selected is not None:
            self._selected.selected = False
            self._selected.update()
        self._selected = item
        item.selected = True
        item.update()
        self._show_detail(item)

    def _show_detail(self, item):
        if self._detail_proxy is not None:
            self.scene.removeItem(self._detail_proxy)
            self._detail_proxy = None
        panel = QFrame()
        panel.setObjectName("mongoExplainDetailPanel")
        panel.setStyleSheet(
            "QFrame#mongoExplainDetailPanel { background: #25282c; border: 1px solid #69717b; }")
        panel.setFixedSize(300, 390)
        layout = QVBoxLayout(panel)
        header = QHBoxLayout()
        title = QLabel(item.node.stage)
        title.setStyleSheet("font-weight: 600; color: #f0f2f5;")
        close_button = QPushButton("×")
        close_button.setFixedSize(24, 24)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_button)
        layout.addLayout(header)
        self.detail_view = QPlainTextEdit()
        self.detail_view.setObjectName("mongoExplainNodeDetail")
        self.detail_view.setReadOnly(True)
        self.detail_view.setPlainText(canonical_dumps(item.node.raw))
        layout.addWidget(self.detail_view, 1)
        proxy = self.scene.addWidget(panel)
        x = item.x() + self.NODE_WIDTH + 28
        y = max(24.0, item.y() - 35)
        proxy.setPos(x, y)
        self._detail_proxy = proxy
        close_button.clicked.connect(lambda: self.scene.removeItem(proxy))

    def select_index(self, index):
        if 0 <= index < len(self._items):
            self._select_item(self._items[index])

    def set_threshold(self, value):
        self._threshold = float(value)
        for item in self._items:
            elapsed = item.node.stage_elapsed_ms
            percent = (100.0 * elapsed / self._root_elapsed
                       if elapsed is not None and self._root_elapsed else 0.0)
            item.expensive = percent >= self._threshold
            item.update()

    def set_search(self, text):
        needle = str(text).strip().lower()
        for item in self._items:
            matches = not needle or needle in item.node.stage.lower()
            item.setOpacity(1.0 if matches else 0.22)

    def zoom(self, delta):
        self._scale = max(0.45, min(2.5, self._scale + delta))
        factor = self._scale / self.transform().m11()
        self.scale(factor, factor)


class MongoPipelineExplainPanel(QWidget):
    """Reference-shaped Explain workspace with a native plan canvas and raw views."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mongoPipelineExplainPanel")
        self._explain = PipelineExplain(raw={}, raw_text="")
        self._connection_name = ""
        self._schema = ""
        self._script = ""
        self._zoom = 1.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.setContentsMargins(8, 5, 8, 5)
        controls.addWidget(QLabel("耗时阈值(%):"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setObjectName("mongoExplainThreshold")
        self.threshold_spin.setRange(0, 1000)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(50)
        self.threshold_spin.setToolTip("当节点成本超过阈值时，节点将被高亮显示")
        controls.addWidget(self.threshold_spin)
        self.zoom_out_button = QPushButton("缩小")
        self.zoom_in_button = QPushButton("放大")
        controls.addWidget(self.zoom_out_button)
        controls.addWidget(self.zoom_in_button)
        controls.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("mongoExplainSearch")
        self.search_edit.setClearButtonEnabled(True)
        controls.addWidget(self.search_edit, 1)
        self.export_image_button = QPushButton("导出图像")
        controls.addWidget(self.export_image_button)
        layout.addLayout(controls)

        self.view_tabs = QTabWidget()
        self.view_tabs.setObjectName("mongoExplainViewTabs")
        layout.addWidget(self.view_tabs, 1)

        self.visual_page = QWidget()
        visual_layout = QVBoxLayout(self.visual_page)
        visual_layout.setContentsMargins(4, 4, 4, 4)
        self.explain_canvas = MongoExplainCanvas()
        visual_layout.addWidget(self.explain_canvas)
        self.view_tabs.addTab(self.visual_page, "可视化")

        self.json_view = QPlainTextEdit()
        self.json_view.setObjectName("mongoExplainJson")
        self.json_view.setReadOnly(True)
        self.view_tabs.addTab(self.json_view, "JSON")

        self.grid_table = QTableWidget(0, 1)
        self.grid_table.setObjectName("mongoExplainGrid")
        self.grid_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view_tabs.addTab(self.grid_table, "网格")

        self.stats_table = QTableWidget(0, 4)
        self.stats_table.setObjectName("mongoExplainStats")
        self.stats_table.setHorizontalHeaderLabels(["节点类型", "计数", "耗时 (ms)", "耗时 (%)"])
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.view_tabs.addTab(self.stats_table, "统计信息")

        self.info_page = QWidget()
        info_layout = QVBoxLayout(self.info_page)
        self.info_summary = QLabel()
        self.info_summary.setObjectName("mongoExplainInfoSummary")
        self.info_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self.info_summary)
        self.info_script = QPlainTextEdit()
        self.info_script.setObjectName("mongoExplainInfoScript")
        self.info_script.setReadOnly(True)
        info_layout.addWidget(self.info_script, 1)
        self.view_tabs.addTab(self.info_page, "信息")

        self.zoom_out_button.clicked.connect(lambda: self._set_zoom(-0.1))
        self.zoom_in_button.clicked.connect(lambda: self._set_zoom(0.1))
        self.search_edit.textChanged.connect(self._filter_plan)
        self.threshold_spin.valueChanged.connect(self._highlight_threshold)
        self.export_image_button.clicked.connect(self._export_image)

    @staticmethod
    def _summary(value):
        if isinstance(value, dict):
            return f"(Document) {len(value)} Fields"
        if isinstance(value, list):
            return f"(Array) {len(value)} Items"
        if isinstance(value, str):
            return value
        return canonical_dumps({"value": value})

    def set_explain(self, explain, *, connection_name="", schema="", script=""):
        if not isinstance(explain, PipelineExplain):
            explain = PipelineExplain(raw={}, raw_text=str(explain))
        self._explain = explain
        self._connection_name = str(connection_name)
        self._schema = str(schema)
        self._script = str(script)
        self.explain_canvas.set_explain(explain)
        self.json_view.setPlainText(explain.raw_text)
        self._populate_grid(explain.raw)
        self._populate_stats(explain.stats_rows())
        elapsed = explain.execution_stats.get("executionTimeMillis")
        self.info_summary.setText(
            f"查询时间: {elapsed if elapsed is not None else '---'} ms；"
            f"连接: {self._connection_name or '---'}；数据库: {self._schema or '---'}")
        self.info_script.setPlainText(self._script)
        self._filter_plan(self.search_edit.text())
        self._highlight_threshold(self.threshold_spin.value())

    def _populate_grid(self, raw):
        self.grid_table.setRowCount(0)
        self.grid_table.setColumnCount(0)
        if not isinstance(raw, dict) or not raw:
            return
        keys = [str(key) for key in raw]
        self.grid_table.setColumnCount(len(keys))
        self.grid_table.setHorizontalHeaderLabels(keys)
        self.grid_table.setRowCount(1)
        for column, key in enumerate(raw):
            self.grid_table.setItem(0, column, QTableWidgetItem(self._summary(raw[key])))
        self.grid_table.resizeColumnsToContents()

    def _populate_stats(self, rows):
        self.stats_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (row["stage"], row["count"], f"{row['elapsed_ms']:.2f}",
                      f"{row['elapsed_percent']:.2f}%")
            for column, value in enumerate(values):
                self.stats_table.setItem(row_index, column, QTableWidgetItem(str(value)))

    def _highlight_threshold(self, value):
        self.explain_canvas.set_threshold(value)

    def _filter_plan(self, text):
        self.explain_canvas.set_search(text)

    def _set_zoom(self, delta):
        self.explain_canvas.zoom(delta)

    def _export_image(self):
        filename, _selected = QFileDialog.getSaveFileName(
            self, "导出 Explain 图像", "mongo-explain.png", "PNG image (*.png)")
        if not filename:
            return False
        if not filename.lower().endswith(".png"):
            filename += ".png"
        return self.visual_page.grab().save(filename, "PNG")


class MongoPipelineOptionsEditor(QWidget):
    """Reference-shaped aggregation-pipeline options with Version 0.0 ids."""

    changed = Signal()
    TRUE_FALSE = (("", 0), ("true", 1), ("false", -1))
    READ_CONCERN = (("", 0), ("local", 1), ("available", 2), ("majority", 3), ("linearizable", 4))
    CASE_FIRST = (("", 0), ("upper", 1), ("lower", 2), ("off", 3))
    STRENGTH = (("", 0), ("Primary Level", 1), ("Secondary Level", 2),
                ("Tertiary Level", 3), ("Quaternary Level", 4), ("Identical Level", 5))
    ALTERNATE = (("", 0), ("non-ignorable", 1), ("shifted", 2))
    MAX_VARIABLE = (("", 0), ("punct", 1), ("space", 2))
    COLLATION_KEYS = (
        "caseLevel", "caseFirst", "strength", "numericOrdering",
        "alternate", "maxVariable", "backwards", "normalization",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mongoPipelineOptionsEditor")
        self._loading = False
        self._unknown = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        self.form = QFormLayout(body)
        layout.addWidget(self.scroll, 1)
        self.scroll.setWidget(body)

        self.allow_disk_combo = self._combo("mongoPipelineOptionsAllowDiskUse", self.TRUE_FALSE)
        self.form.addRow("允许使用磁盘:", self.allow_disk_combo)
        self.batch_size_edit = QLineEdit()
        self.batch_size_edit.setObjectName("mongoPipelineOptionsBatchSize")
        self.batch_size_edit.setPlaceholderText("留空")
        self.form.addRow("光标批处理大小:", self.batch_size_edit)
        self.max_time_edit = QLineEdit()
        self.max_time_edit.setObjectName("mongoPipelineOptionsMaxTimeMS")
        self.max_time_edit.setPlaceholderText("留空")
        self.form.addRow("最大时间（毫秒 ms）:", self.max_time_edit)
        self.bypass_validation_combo = self._combo(
            "mongoPipelineOptionsBypassValidation", self.TRUE_FALSE)
        self.form.addRow("绕过文档验证:", self.bypass_validation_combo)
        self.read_concern_combo = self._combo(
            "mongoPipelineOptionsReadConcern", self.READ_CONCERN)
        self.form.addRow("读关注级别:", self.read_concern_combo)

        self.locale_combo = QComboBox()
        self.locale_combo.setObjectName("mongoPipelineOptionsLocale")
        self.locale_combo.setEditable(True)
        self.locale_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.locale_combo.addItem("", "")
        for value in LOCALE_SUGGESTIONS:
            self.locale_combo.addItem(value, value)
        self.form.addRow("区域设置:", self.locale_combo)
        self.strength_combo = self._combo("mongoPipelineOptionsStrength", self.STRENGTH)
        self.form.addRow("强度:", self.strength_combo)
        self.case_level_combo = self._combo("mongoPipelineOptionsCaseLevel", self.TRUE_FALSE)
        self.form.addRow("大小写级别:", self.case_level_combo)
        self.case_first_combo = self._combo("mongoPipelineOptionsCaseFirst", self.CASE_FIRST)
        self.form.addRow("大小写优先:", self.case_first_combo)
        self.numeric_ordering_combo = self._combo(
            "mongoPipelineOptionsNumericOrdering", self.TRUE_FALSE)
        self.form.addRow("数值排序:", self.numeric_ordering_combo)
        self.alternate_combo = self._combo("mongoPipelineOptionsAlternate", self.ALTERNATE)
        self.form.addRow("替代:", self.alternate_combo)
        self.max_variable_combo = self._combo("mongoPipelineOptionsMaxVariable", self.MAX_VARIABLE)
        self.form.addRow("最大变量:", self.max_variable_combo)
        self.backwards_combo = self._combo("mongoPipelineOptionsBackwards", self.TRUE_FALSE)
        self.form.addRow("逆向:", self.backwards_combo)
        self.normalization_combo = self._combo("mongoPipelineOptionsNormalization", self.TRUE_FALSE)
        self.form.addRow("规范化:", self.normalization_combo)

        self.hint_editor = QPlainTextEdit()
        self.hint_editor.setObjectName("mongoPipelineOptionsHint")
        self.hint_editor.setMaximumHeight(90)
        self.form.addRow("提示:", self.hint_editor)
        self.comment_edit = QLineEdit()
        self.comment_edit.setObjectName("mongoPipelineOptionsComment")
        self.form.addRow("注释:", self.comment_edit)

        self.write_concern_check = QCheckBox("写关注")
        self.write_concern_check.setObjectName("mongoPipelineOptionsWriteConcern")
        self.form.addRow("", self.write_concern_check)
        self.w_edit = QLineEdit()
        self.w_edit.setObjectName("mongoPipelineOptionsW")
        self.form.addRow("w:", self.w_edit)
        self.journal_combo = self._combo("mongoPipelineOptionsJournal", self.TRUE_FALSE)
        self.form.addRow("j:", self.journal_combo)
        self.wtimeout_edit = QLineEdit()
        self.wtimeout_edit.setObjectName("mongoPipelineOptionsWTimeout")
        self.wtimeout_edit.setPlaceholderText("0")
        self.form.addRow("w 超时:", self.wtimeout_edit)
        self.let_editor = QPlainTextEdit()
        self.let_editor.setObjectName("mongoPipelineOptionsLet")
        self.let_editor.setPlaceholderText("Canonical Extended JSON 文档")
        self.let_editor.setMaximumHeight(110)
        self.form.addRow("Let:", self.let_editor)

        for combo in self._combos():
            combo.currentIndexChanged.connect(self._changed)
        self.locale_combo.currentTextChanged.connect(self._locale_changed)
        self.batch_size_edit.textChanged.connect(self._changed)
        self.max_time_edit.textChanged.connect(self._changed)
        self.hint_editor.textChanged.connect(self._changed)
        self.comment_edit.textChanged.connect(self._changed)
        self.write_concern_check.toggled.connect(self._write_concern_changed)
        self.w_edit.textChanged.connect(self._changed)
        self.wtimeout_edit.textChanged.connect(self._changed)
        self.let_editor.textChanged.connect(self._changed)
        self._set_collation_enabled(False)
        self._set_write_concern_enabled(False)

    @staticmethod
    def _combo(object_name, values):
        combo = QComboBox()
        combo.setObjectName(object_name)
        for label, value in values:
            combo.addItem(label, value)
        return combo

    def _combos(self):
        return (
            self.allow_disk_combo, self.bypass_validation_combo, self.read_concern_combo,
            self.case_level_combo, self.case_first_combo, self.strength_combo,
            self.numeric_ordering_combo, self.alternate_combo, self.max_variable_combo,
            self.backwards_combo, self.normalization_combo, self.journal_combo,
        )

    @staticmethod
    def _set_combo(combo, value):
        index = combo.findData(value)
        if index < 0:
            combo.addItem(str(value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _set_collation_enabled(self, enabled):
        for key in self.COLLATION_KEYS:
            getattr(self, {
                "caseLevel": "case_level_combo", "caseFirst": "case_first_combo",
                "strength": "strength_combo", "numericOrdering": "numeric_ordering_combo",
                "alternate": "alternate_combo", "maxVariable": "max_variable_combo",
                "backwards": "backwards_combo", "normalization": "normalization_combo",
            }[key]).setEnabled(bool(enabled))

    def _set_write_concern_enabled(self, enabled):
        for widget in (self.w_edit, self.journal_combo, self.wtimeout_edit):
            widget.setEnabled(bool(enabled))

    def _locale_changed(self, *_):
        self._set_collation_enabled(bool(self.locale_combo.currentText().strip()))
        self._changed()

    def _write_concern_changed(self, checked):
        self._set_write_concern_enabled(checked)
        self._changed()

    def _changed(self, *_):
        if not self._loading:
            self.changed.emit()

    def load_options(self, options):
        self._loading = True
        try:
            value = deepcopy(REFERENCE_OPTION_DEFAULTS)
            value.update(deepcopy(options or {}))
            self._unknown = {key: deepcopy(item) for key, item in value.items()
                             if key not in REFERENCE_OPTION_DEFAULTS}
            self._set_combo(self.allow_disk_combo, value["allowDiskUse"])
            self.batch_size_edit.setText("" if value["cursorBatchSize"] == -1 else str(value["cursorBatchSize"]))
            self.max_time_edit.setText("" if value["maxTimeMS"] == -1 else str(value["maxTimeMS"]))
            self._set_combo(self.bypass_validation_combo, value["bypassDocumentValidation"])
            self._set_combo(self.read_concern_combo, value["readConcernLevel"])
            self.locale_combo.setCurrentText(value["locale"])
            for key, widget in (
                    ("caseLevel", self.case_level_combo), ("caseFirst", self.case_first_combo),
                    ("strength", self.strength_combo), ("numericOrdering", self.numeric_ordering_combo),
                    ("alternate", self.alternate_combo), ("maxVariable", self.max_variable_combo),
                    ("backwards", self.backwards_combo), ("normalization", self.normalization_combo)):
                self._set_combo(widget, value[key])
            self.hint_editor.setPlainText(value["hint"])
            self.comment_edit.setText(value["comment"])
            self.write_concern_check.setChecked(value["enableWriteConcern"])
            self.w_edit.setText(str(value["w"]))
            self._set_combo(self.journal_combo, value["j"])
            self.wtimeout_edit.setText("" if not value["wtimeout"] else str(value["wtimeout"]))
            self.let_editor.setPlainText(value["let"])
            self._set_collation_enabled(bool(value["locale"]))
            self._set_write_concern_enabled(value["enableWriteConcern"])
        finally:
            self._loading = False

    @staticmethod
    def _integer(edit, *, blank, minimum):
        text = edit.text().strip()
        if not text:
            return blank
        try:
            value = int(text)
        except ValueError:
            raise DatabaseError("选项中的批处理大小、最大时间和写关注超时必须为整数") from None
        if value < minimum:
            raise DatabaseError("选项中的整数低于允许的最小值")
        return value

    def options(self):
        let = self.let_editor.toPlainText().strip()
        if let:
            try:
                value = canonical_loads('{"value":' + let + '}')["value"]
            except Exception:
                raise DatabaseError("Let 必须是 Canonical Extended JSON 文档") from None
            if not isinstance(value, dict):
                raise DatabaseError("Let 必须是 Canonical Extended JSON 文档")
        result = deepcopy(REFERENCE_OPTION_DEFAULTS)
        result.update(deepcopy(self._unknown))
        result.update({
            "allowDiskUse": self.allow_disk_combo.currentData(),
            "cursorBatchSize": self._integer(self.batch_size_edit, blank=-1, minimum=0),
            "maxTimeMS": self._integer(self.max_time_edit, blank=-1, minimum=0),
            "bypassDocumentValidation": self.bypass_validation_combo.currentData(),
            "readConcernLevel": self.read_concern_combo.currentData(),
            "locale": self.locale_combo.currentText().strip(),
            "caseLevel": self.case_level_combo.currentData(),
            "caseFirst": self.case_first_combo.currentData(),
            "strength": self.strength_combo.currentData(),
            "numericOrdering": self.numeric_ordering_combo.currentData(),
            "alternate": self.alternate_combo.currentData(),
            "maxVariable": self.max_variable_combo.currentData(),
            "backwards": self.backwards_combo.currentData(),
            "normalization": self.normalization_combo.currentData(),
            "hint": self.hint_editor.toPlainText().strip(),
            "comment": self.comment_edit.text().strip(),
            "enableWriteConcern": self.write_concern_check.isChecked(),
            "w": self.w_edit.text().strip(),
            "j": self.journal_combo.currentData(),
            "wtimeout": self._integer(self.wtimeout_edit, blank=0, minimum=0),
            "let": let,
        })
        return result


class MongoPipelineSaveDialog(QDialog):
    def __init__(self, definition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存聚合管道")
        self.setMinimumSize(720, 520)
        self._definition = deepcopy(definition)
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        form.addWidget(QLabel("名称:"))
        self.name_edit = QLineEdit(definition.name)
        self.name_edit.setObjectName("mongoPipelineName")
        form.addWidget(self.name_edit, 1)
        layout.addLayout(form)
        layout.addWidget(QLabel("脚本预览:"))
        self.preview = QPlainTextEdit(pipeline_script(definition))
        self.preview.setReadOnly(True)
        self.preview.setObjectName("mongoPipelineSavePreview")
        layout.addWidget(self.preview, 1)
        self.suppress_check = QCheckBox("不再显示脚本预览")
        layout.addWidget(self.suppress_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.name_edit.textChanged.connect(self._name_changed)
        layout.addWidget(buttons)
        self._name_changed(self.name_edit.text())

    def _name_changed(self, text):
        self.save_button.setEnabled(bool(text.strip()))

    def definition(self):
        value = deepcopy(self._definition)
        value.name = self.name_edit.text().strip()
        return value


class MongoPipelinePage(QWidget):
    titleChanged = Signal(str)
    dirtyChanged = Signal(bool)
    previewRequested = Signal(object)
    runRequested = Signal(object)
    explainRequested = Signal(object)
    saveRequested = Signal(object)
    importRequested = Signal()
    exportRequested = Signal()
    focusRequested = Signal(bool)

    def __init__(self, connection_id, schema, name="", parent=None):
        super().__init__(parent)
        self.setObjectName("mongoPipelinePage")
        self.kind = "mongo_pipeline"
        self.connection_id = connection_id
        self.connection_name = connection_id
        self.schema = schema
        self.pipeline_id = None
        self.definition = PipelineDefinition(name=name)
        self.dirty = False
        self.loading = False
        self.running = False
        self.suppress_save_preview = False
        self._invalid = None
        self._loading_mode = False
        self._preview = None
        self._explain = None
        self._explain_count = 0
        self._sources = []
        self.last_rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbar = QToolBar(self)
        self.toolbar.setObjectName("mongoPipelineToolbar")
        layout.addWidget(self.toolbar)
        self.save_action = self._action("保存", "save", self.request_save)
        self.import_action = self._action("导入查询", "import", self.importRequested.emit)
        self.preview_all_action = self._action("预览全部", "run", self.request_preview_all)
        self.run_action = self._action("运行", "run", self.request_run)
        self.explain_action = self._action("解释", "query", self.request_explain)
        self.export_action = self._action("导出结果", "export", self.exportRequested.emit)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.focus_action = self._action("进入专注模式", "design", None)
        self.focus_action.setCheckable(True)
        self.focus_action.toggled.connect(self.focusRequested)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        self.pipeline_tab = QWidget()
        pipeline_layout = QVBoxLayout(self.pipeline_tab)
        pipeline_layout.setContentsMargins(8, 8, 8, 8)
        source_controls = QHBoxLayout()
        source_controls.addWidget(QLabel("源集合:"))
        self.source_combo = QComboBox()
        self.source_combo.setObjectName("mongoPipelineSourceCombo")
        self.source_combo.setEditable(True)
        source_controls.addWidget(self.source_combo, 1)
        source_controls.addWidget(QLabel("预览样本:"))
        self.sample_mode = QComboBox()
        self.sample_mode.addItem("全部", "all")
        self.sample_mode.addItem("最初", "first")
        self.sample_mode.addItem("随机", "random")
        self.sample_mode.setCurrentIndex(self.sample_mode.findData("random"))
        source_controls.addWidget(self.sample_mode)
        self.sample_size = QSpinBox()
        self.sample_size.setRange(1, 100000)
        self.sample_size.setValue(1000)
        source_controls.addWidget(self.sample_size)
        source_controls.addWidget(QLabel("文档"))
        pipeline_layout.addLayout(source_controls)
        option_row = QHBoxLayout()
        self.options_button = QPushButton("选项")
        self.options_button.setObjectName("mongoPipelineOptionsButton")
        self.options_button.setCheckable(True)
        self.options_button.setMinimumWidth(110)
        self.options_button.setStyleSheet(
            "QPushButton { border: 2px solid #e6a23c; border-radius: 12px; padding: 5px 18px; }"
            "QPushButton:checked { background: #3b3326; }")
        option_row.addWidget(self.options_button)
        option_row.addStretch()
        pipeline_layout.addLayout(option_row)
        self.graph_scroll = QScrollArea()
        self.graph_scroll.setWidgetResizable(True)
        self.graph_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.graph_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.graph_scroll.setMinimumHeight(230)
        self.graph_scroll.setMaximumHeight(310)
        self.graph_widget = QWidget()
        self.graph_row = QHBoxLayout(self.graph_widget)
        self.graph_row.setContentsMargins(16, 10, 16, 10)
        self.graph_row.setSpacing(12)
        self.graph_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.graph_widget.setStyleSheet("background: #202124; color: #e8eaed;")
        self.graph_scroll.setWidget(self.graph_widget)
        pipeline_layout.addWidget(self.graph_scroll)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.input_panel = self._preview_panel("输入预览")
        self.editor_panel = QWidget()
        editor_layout = QVBoxLayout(self.editor_panel)
        editor_layout.setContentsMargins(6, 4, 6, 4)
        stage_header = QHBoxLayout()
        stage_header.addWidget(QLabel("运算符:"))
        self.operator_combo = QComboBox()
        self.operator_combo.setObjectName("mongoPipelineOperator")
        self.operator_combo.addItems(STAGE_NAMES)
        stage_header.addWidget(self.operator_combo)
        stage_header.addStretch()
        self.stage_mode = QComboBox()
        self.stage_mode.setObjectName("mongoPipelineStageMode")
        self.stage_mode.addItem("创建工具", "create")
        self.stage_mode.addItem("编辑器", "editor")
        self.stage_mode.setMinimumWidth(115)
        stage_header.addWidget(self.stage_mode)
        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        stage_header.addWidget(self.enabled_check)
        editor_layout.addLayout(stage_header)
        self.stage_stack = QStackedWidget()
        self.match_tool = MongoPipelineMatchTool()
        self.project_tool = MongoPipelineProjectTool()
        self.sort_tool = MongoPipelineSortTool()
        self.limit_tool = MongoPipelineScalarTool("limit")
        self.skip_tool = MongoPipelineScalarTool("skip")
        self.stage_tools = {
            "$match": self.match_tool,
            "$project": self.project_tool,
            "$sort": self.sort_tool,
            "$limit": self.limit_tool,
            "$skip": self.skip_tool,
        }
        assert frozenset(self.stage_tools) == CREATE_TOOL_STAGES
        self.stage_editor = QPlainTextEdit()
        self.stage_editor.setObjectName("mongoPipelineStageEditor")
        for tool in dict.fromkeys(self.stage_tools.values()):
            self.stage_stack.addWidget(tool)
        self.stage_stack.addWidget(self.stage_editor)
        editor_layout.addWidget(self.stage_stack, 1)
        stage_actions = QHBoxLayout()
        self.add_stage_button = QPushButton("添加阶段")
        self.stage_preview_button = QPushButton("预览")
        self.delete_stage_button = QPushButton("删除")
        self.up_button = QPushButton("上移")
        self.down_button = QPushButton("下移")
        stage_actions.addWidget(self.add_stage_button)
        stage_actions.addWidget(self.stage_preview_button)
        stage_actions.addWidget(self.delete_stage_button)
        stage_actions.addWidget(self.up_button)
        stage_actions.addWidget(self.down_button)
        stage_actions.addStretch()
        editor_layout.addLayout(stage_actions)
        self.output_panel = self._preview_panel("输出预览")
        self.splitter.addWidget(self.input_panel)
        self.splitter.addWidget(self.editor_panel)
        self.splitter.addWidget(self.output_panel)
        self.splitter.setSizes([320, 480, 320])
        self.options_editor = MongoPipelineOptionsEditor()
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self.splitter)
        self.detail_stack.addWidget(self.options_editor)
        pipeline_layout.addWidget(self.detail_stack, 1)
        self.tabs.addTab(self.pipeline_tab, "管道")

        self.script_tab = QWidget()
        script_layout = QVBoxLayout(self.script_tab)
        self.script_mode = QTabWidget()
        self.script_editor = QPlainTextEdit()
        self.script_editor.setReadOnly(True)
        self.script_editor.setObjectName("mongoPipelineScript")
        self.ddl_editor = QPlainTextEdit()
        self.ddl_editor.setReadOnly(True)
        self.ddl_editor.setObjectName("mongoPipelineDDL")
        self.script_mode.addTab(self.script_editor, "更改")
        self.script_mode.addTab(self.ddl_editor, "DDL")
        script_layout.addWidget(self.script_mode)
        self.tabs.addTab(self.script_tab, "脚本预览")

        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setObjectName("mongoPipelineMessages")
        self.tabs.addTab(self.messages, "消息")
        self.results = QTableWidget(0, 1)
        self.results.setObjectName("mongoPipelineResults")
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setHorizontalHeaderLabels(["Canonical Extended JSON"])
        self.tabs.addTab(self.results, "结果")
        self.explain_panel = MongoPipelineExplainPanel()
        self.explain_tab_index = self.tabs.addTab(self.explain_panel, "解释")
        self.tabs.setTabVisible(self.explain_tab_index, False)

        self.operator_combo.currentTextChanged.connect(self._operator_changed)
        self.stage_editor.textChanged.connect(self._body_changed)
        for tool in dict.fromkeys(self.stage_tools.values()):
            tool.changed.connect(self._create_tool_changed)
        self.stage_mode.currentIndexChanged.connect(self._mode_changed)
        self.enabled_check.toggled.connect(self._enabled_changed)
        self.add_stage_button.clicked.connect(self.add_stage)
        self.stage_preview_button.clicked.connect(self.request_preview_all)
        self.delete_stage_button.clicked.connect(self.delete_stage)
        self.up_button.clicked.connect(lambda: self.move_stage(-1))
        self.down_button.clicked.connect(lambda: self.move_stage(1))
        self.source_combo.currentTextChanged.connect(self._source_changed)
        self.sample_mode.currentIndexChanged.connect(self._sample_changed)
        self.sample_size.valueChanged.connect(self._sample_changed)
        self.options_button.toggled.connect(self._show_options)
        self.options_editor.changed.connect(self._reference_options_changed)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.load_definition(self.definition)

    @property
    def title(self):
        return (self.definition.name or "无标题") + (" — 已编辑" if self.dirty else "")

    def _action(self, text, symbol, callback):
        action = QAction(tool_icon(symbol, 18), text, self)
        if callback is not None:
            action.triggered.connect(callback)
        self.toolbar.addAction(action)
        self.addAction(action)
        return action

    def _preview_panel(self, title):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel(title))
        table = QTableWidget(0, 1)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setHorizontalHeaderLabels(["Canonical Extended JSON"])
        layout.addWidget(table, 1)
        panel.table = table
        return panel

    def set_sources(self, sources):
        current = self.source_combo.currentText()
        self._sources = [str(value) for value in sources]
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItems(self._sources)
        if current:
            self.source_combo.setCurrentText(current)
        elif self.definition.source:
            self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)
        self._source_changed(self.source_combo.currentText())

    def load_definition(self, definition, *, pipeline_id=None):
        self.definition = deepcopy(definition)
        if not self.definition.stages:
            self.definition.stages = [PipelineStage()]
        self.definition.selected_stage = min(
            max(0, self.definition.selected_stage or 0), len(self.definition.stages) - 1)
        self.pipeline_id = pipeline_id
        self._invalid = None
        self.dirty = False
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)
        self.sample_mode.blockSignals(True)
        self.sample_mode.setCurrentIndex(self.sample_mode.findData(self.definition.sample_mode))
        self.sample_mode.blockSignals(False)
        self.sample_size.blockSignals(True)
        self.sample_size.setValue(self.definition.sample_size)
        self.sample_size.blockSignals(False)
        self._render_all()
        self.dirtyChanged.emit(False)
        self.titleChanged.emit(self.title)

    def load_nagg(self, text, *, pipeline_id=None, name=None):
        definition = from_nagg(text)
        if isinstance(name, str) and name:
            definition.name = name
        self.load_definition(definition, pipeline_id=pipeline_id)

    def refresh(self):
        self._render_all()

    def can_discard(self, retry=None):
        if not self.dirty:
            return True
        choice = QMessageBox.question(
            self, "聚合管道未保存",
            "聚合管道有未保存的更改。是否保存？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if choice == QMessageBox.StandardButton.Save:
            self.request_save()
            return False
        return choice == QMessageBox.StandardButton.Discard

    def draft(self):
        self._commit_stage_editor()
        self._commit_options_editor()
        if self._invalid:
            raise DatabaseError(self._invalid)
        validate_pipeline(self.definition)
        return deepcopy(self.definition)

    def _edited(self):
        self.dirty = True
        self.dirtyChanged.emit(True)
        self.titleChanged.emit(self.title)
        self._render_script()
        self._update_controls()

    def _render_all(self):
        self._render_sources()
        self._render_graph()
        self._render_stage_editor()
        self._render_options()
        self._render_script()
        self._update_controls()

    def _render_sources(self):
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentText(self.definition.source)
        self.source_combo.blockSignals(False)
        self.sample_size.setEnabled(self.definition.sample_mode != "all")

    def _render_options(self):
        self.options_editor.load_options(self.definition.reference_options)
        self.options_button.setChecked(False)
        self.detail_stack.setCurrentWidget(self.splitter)

    def _show_options(self, checked):
        if checked:
            self._commit_stage_editor()
            self.detail_stack.setCurrentWidget(self.options_editor)
        else:
            self.detail_stack.setCurrentWidget(self.splitter)

    def _commit_options_editor(self):
        try:
            self.definition.reference_options = self.options_editor.options()
        except DatabaseError as error:
            self._invalid = str(error)

    def _reference_options_changed(self):
        try:
            self.definition.reference_options = self.options_editor.options()
        except DatabaseError:
            return
        self._invalid = None
        self._edited()

    def _render_graph(self):
        while self.graph_row.count():
            item = self.graph_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        source = QLabel("源\n" + (self.definition.source or "选择集合"))
        source.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source.setObjectName("mongoPipelineSourceCard")
        source.setFixedSize(105, 105)
        source.setStyleSheet("QLabel { border: 2px solid #36b879; border-radius: 52px; padding: 12px; }")
        self.graph_row.addWidget(source)
        for index, stage in enumerate(self.definition.stages):
            self.graph_row.addWidget(QLabel("→"))
            card = _PipelineStageCard(self, index, f"阶段 {index + 1}\n{stage.operator}")
            card.setObjectName(f"mongoPipelineStageCard{index}")
            card.setCheckable(True)
            card.setChecked(index == self.definition.selected_stage)
            card.setFixedSize(115, 105)
            card.setStyleSheet(
                "QPushButton[dropTarget='true'] { border: 2px solid #8ab4f8; }")
            card.clicked.connect(lambda checked=False, value=index: self.select_stage(value))
            card.dropRequested.connect(self.move_stage_to)
            self.graph_row.addWidget(card)
            plus = QPushButton("+")
            plus.setObjectName(f"mongoPipelineAddAfter{index}")
            plus.setToolTip("添加下一阶段")
            plus.setFixedSize(28, 28)
            plus.clicked.connect(lambda checked=False, value=index: self.add_stage_after(value))
            self.graph_row.addWidget(plus)
        self.graph_row.addWidget(QLabel("→"))
        output = QLabel("输出")
        output.setAlignment(Qt.AlignmentFlag.AlignCenter)
        output.setObjectName("mongoPipelineOutputCard")
        output.setFixedSize(105, 105)
        output.setStyleSheet("QLabel { border: 2px solid #36b879; border-radius: 52px; padding: 12px; }")
        self.graph_row.addWidget(output)
        self.graph_row.addStretch()

    def _render_stage_editor(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            self.stage_editor.setPlainText("")
            return
        stage = self.definition.stages[index]
        self.operator_combo.blockSignals(True)
        self.operator_combo.setCurrentText(stage.operator)
        self.operator_combo.blockSignals(False)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(stage.enabled)
        self.enabled_check.blockSignals(False)
        self.stage_editor.blockSignals(True)
        self.stage_editor.setPlainText(
            stage.reference_mql if stage.reference_mql is not None else
            STAGE_EDITOR_TEMPLATES.get(stage.operator, _stage_value_text(stage.body)))
        self.stage_editor.blockSignals(False)
        tool = self.stage_tools.get(stage.operator)
        if tool is not None:
            tool.load(stage.body)
        can_create = tool is not None and tool.isEnabled()
        self.stage_mode.setEnabled(can_create)
        self.stage_mode.setVisible(can_create)
        self.stage_mode.blockSignals(True)
        self.stage_mode.setCurrentIndex(self.stage_mode.findData("create" if can_create else "editor"))
        self.stage_mode.blockSignals(False)
        self.stage_stack.setCurrentWidget(tool if can_create else self.stage_editor)

    def _render_script(self):
        try:
            text = pipeline_script(self.definition)
        except DatabaseError as error:
            text = str(error)
        self.script_editor.setPlainText(text)
        self.ddl_editor.setPlainText(text)

    def _tab_changed(self, index):
        if self.tabs.widget(index) is self.script_tab:
            self._render_script()

    def select_stage(self, index):
        self.options_button.setChecked(False)
        self._commit_stage_editor()
        self.definition.selected_stage = index
        self._render_graph()
        self._render_stage_editor()

    def _commit_stage_editor(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        try:
            if self.stage_stack.currentWidget() is not self.stage_editor:
                tool = self.stage_stack.currentWidget()
                body = tool.document()
                if body is None:
                    raise ValueError("Stage create tool did not produce a value")
                BSON.encode({"body": body}, codec_options=BSON_CODEC)
                current = self.definition.stages[index]
                unchanged = (
                    current.reference_mql is not None
                    and BSON.encode({"body": current.body}, codec_options=BSON_CODEC)
                    == BSON.encode({"body": body}, codec_options=BSON_CODEC))
                reference_mql = current.reference_mql if unchanged else None
            else:
                reference_mql = self.stage_editor.toPlainText()
                body = parse_mql(reference_mql)
                canonical_dumps(body)
        except Exception as error:
            self._invalid = "Stage body is not valid Canonical Extended JSON: " + str(error)
            return
        self._invalid = None
        self.definition.stages[index].body = body
        self.definition.stages[index].reference_mql = reference_mql

    def _operator_changed(self, value):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        if self.definition.stages[index].operator == value:
            return
        self.definition.stages[index].operator = value
        self.definition.stages[index].body = self._default_stage_body(value)
        self.definition.stages[index].reference_mql = STAGE_EDITOR_TEMPLATES.get(value)
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def _mode_changed(self, _index):
        if self.stage_mode.currentData() == "create":
            tool = self.stage_tools.get(self.operator_combo.currentText())
            self.stage_stack.setCurrentWidget(tool or self.stage_editor)
        else:
            self.stage_stack.setCurrentWidget(self.stage_editor)

    def _create_tool_changed(self):
        if self._loading_mode:
            return
        self._commit_stage_editor()
        if self._invalid:
            self.messages.setPlainText(self._invalid)
            return
        self._edited()

    @staticmethod
    def _default_stage_body(operator):
        return {
            "$match": {}, "$project": {}, "$sort": {}, "$group": {"_id": None},
            "$addFields": {}, "$set": {}, "$unset": [], "$unwind": {"path": "$field"},
            "$limit": 100, "$skip": 0, "$count": "count", "$sample": {"size": 100},
        }.get(operator, {})

    def _body_changed(self):
        self._commit_stage_editor()
        if self._invalid:
            self.messages.setPlainText(self._invalid)
            return
        self._edited()

    def _enabled_changed(self, checked):
        index = self.definition.selected_stage
        if index is None:
            return
        self.definition.stages[index].enabled = bool(checked)
        self._render_graph()
        self._edited()

    def _source_changed(self, value):
        value = str(value).strip()
        if self.definition.source == value:
            return
        self.definition.source = value
        self._render_graph()
        self._edited()

    def _sample_changed(self, *_):
        mode = self.sample_mode.currentData()
        size = self.sample_size.value()
        if self.definition.sample_mode == mode and self.definition.sample_size == size:
            return
        self.definition.sample_mode, self.definition.sample_size = mode, size
        self.sample_size.setEnabled(mode != "all")
        self._edited()

    def add_stage(self):
        self.add_stage_after(self.definition.selected_stage if self.definition.selected_stage is not None else len(self.definition.stages) - 1)

    def add_stage_after(self, index):
        self._commit_stage_editor()
        insert = min(len(self.definition.stages), max(0, index + 1))
        self.definition.stages.insert(insert, PipelineStage())
        self.definition.selected_stage = insert
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def delete_stage(self):
        index = self.definition.selected_stage
        if index is None or not 0 <= index < len(self.definition.stages):
            return
        if QMessageBox.question(self, "删除阶段", f"确定删除阶段 {index + 1}？") != QMessageBox.StandardButton.Yes:
            return
        self.definition.stages.pop(index)
        if not self.definition.stages:
            self.definition.stages.append(PipelineStage())
        self.definition.selected_stage = min(index, len(self.definition.stages) - 1)
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def move_stage(self, offset):
        index = self.definition.selected_stage
        if index is None:
            return
        target = index + offset
        if not 0 <= target < len(self.definition.stages):
            return
        self._commit_stage_editor()
        self.definition.stages[index], self.definition.stages[target] = (
            self.definition.stages[target], self.definition.stages[index])
        self.definition.selected_stage = target
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def drag_threshold(self):
        return QApplication.startDragDistance()

    def move_stage_to(self, source, target):
        if (not 0 <= source < len(self.definition.stages)
                or not 0 <= target < len(self.definition.stages) or source == target):
            return
        self._commit_stage_editor()
        stage = self.definition.stages.pop(source)
        self.definition.stages.insert(target, stage)
        self.definition.selected_stage = target
        self._render_graph()
        self._render_stage_editor()
        self._edited()

    def show_rows(self, table, rows, title=None):
        table.setRowCount(0)
        parsed = []
        keys = []
        for row in rows:
            try:
                value = canonical_loads(row)
            except Exception:
                value = {"$raw": row}
            if isinstance(value, dict):
                for key in value:
                    if key not in keys:
                        keys.append(str(key))
            parsed.append(value)
        if not keys:
            keys = ["Canonical Extended JSON"]
        table.setColumnCount(len(keys))
        table.setHorizontalHeaderLabels(keys)
        table.setRowCount(len(parsed))
        for row_index, value in enumerate(parsed):
            if isinstance(value, dict) and value and keys != ["Canonical Extended JSON"]:
                for column, key in enumerate(keys):
                    item = value.get(key, "")
                    if isinstance(item, dict):
                        shown = canonical_dumps(item)
                    elif isinstance(item, list):
                        shown = canonical_dumps({"value": item})
                    else:
                        shown = str(item)
                    table.setItem(row_index, column, QTableWidgetItem(shown))
            else:
                table.setItem(row_index, 0, QTableWidgetItem(rows[row_index]))
        if title:
            table.setToolTip(title)

    def show_preview(self, preview):
        self.options_button.setChecked(False)
        self._preview = preview
        index = self.definition.selected_stage
        enabled = [i for i, stage in enumerate(self.definition.stages) if stage.enabled]
        try:
            position = enabled.index(index)
        except ValueError:
            position = len(preview.stages) - 1
        if position < 0:
            self.show_rows(self.input_panel.table, preview.source_rows)
            self.show_rows(self.output_panel.table, preview.source_rows)
        else:
            rows = preview.stages[position]["rows"]
            before = preview.source_rows if position == 0 else preview.stages[position - 1]["rows"]
            self.show_rows(self.input_panel.table, before)
            self.show_rows(self.output_panel.table, rows)
        self.show_rows(self.results, preview.final_rows)
        self.messages.setPlainText(
            f"源预览 {len(preview.source_rows)} 行；阶段预览 {len(preview.stages)} 个；"
            f"最终 {len(preview.final_rows)} 行" + ("；结果已截断" if preview.truncated else ""))

    def show_execution(self, rows, truncated=False):
        self.options_button.setChecked(False)
        self.last_rows = list(rows)
        self.show_rows(self.results, rows)
        self.tabs.setCurrentWidget(self.results)
        self.messages.setPlainText(f"运行完成：{len(rows)} 行" + ("；结果已截断" if truncated else ""))
        self._update_controls()

    def show_explain(self, explain):
        self.options_button.setChecked(False)
        self._explain = explain
        self._explain_count += 1
        script = ""
        try:
            script = pipeline_script(deepcopy(self.definition))
        except DatabaseError:
            pass
        self.explain_panel.set_explain(
            explain, connection_name=self.connection_name, schema=self.schema, script=script)
        self.tabs.setTabText(self.explain_tab_index, f"解释 {self._explain_count}")
        self.tabs.setTabVisible(self.explain_tab_index, True)
        self.tabs.setCurrentWidget(self.explain_panel)

    def request_preview_all(self):
        try:
            self.previewRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_run(self):
        try:
            self.runRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_explain(self):
        try:
            self.explainRequested.emit(self.draft())
        except DatabaseError as error:
            self.show_error(error)

    def request_save(self):
        try:
            definition = self.draft()
        except DatabaseError as error:
            self.show_error(error)
            return
        if self.suppress_save_preview and definition.name:
            self.saveRequested.emit(definition)
            return
        dialog = MongoPipelineSaveDialog(definition, self)
        dialog._definition = definition
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.suppress_save_preview = dialog.suppress_check.isChecked()
            self.saveRequested.emit(dialog.definition())

    def mark_saved(self, definition, pipeline_id):
        self.load_definition(definition, pipeline_id=pipeline_id)

    def show_error(self, error):
        self.options_button.setChecked(False)
        self.messages.setPlainText(str(error))
        self.tabs.setCurrentWidget(self.messages)

    def set_running(self, value):
        self.running = bool(value)
        self._update_controls()

    def _update_controls(self):
        self.run_action.setEnabled(not self.running and not self.loading)
        self.export_action.setEnabled(bool(self.last_rows) and not self.running)
        self.preview_all_action.setEnabled(not self.running and not self.loading)
        self.explain_action.setEnabled(not self.running and not self.loading)
        self.save_action.setEnabled(not self.loading and (self.dirty or not self.pipeline_id))
        self.delete_stage_button.setEnabled(
            not self.running and len(self.definition.stages) > 1)
        self.add_stage_button.setEnabled(not self.running)
        self.stage_preview_button.setEnabled(not self.running and not self.loading)
        self.up_button.setEnabled(not self.running and bool(self.definition.selected_stage))
        self.down_button.setEnabled(
            not self.running and self.definition.selected_stage is not None
            and self.definition.selected_stage + 1 < len(self.definition.stages))
        self.titleChanged.emit(self.title)
