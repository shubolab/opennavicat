"""Read-only schema visualization from caller-supplied database metadata.

Only table positions are movable. This widget owns no database connection and
never changes the schema. Export is native Qt painting, including vector SVG.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QFileDialog, QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsPolygonItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout,
    QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


def _identifier_parts(value: str) -> list[str]:
    """Split qualified SQL names without splitting dots inside quoted names."""
    parts, current = [], []
    quote, index = None, 0
    while index < len(value):
        character = value[index]
        if quote:
            end = "]" if quote == "[" else quote
            if character == end:
                if index + 1 < len(value) and value[index + 1] == end:
                    current.append(end)
                    index += 1
                else:
                    quote = None
            else:
                current.append(character)
        elif character in ('"', '`', '['):
            quote = character
        elif character == ".":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    parts.append("".join(current).strip())
    return parts


class TableNode(QGraphicsObject):
    HEADER = 38.0
    ROW = 24.0

    def __init__(self, name: str, columns: list[dict]):
        super().__init__()
        self.name = name
        self.columns = columns
        self.edges: list[ForeignKeyEdge] = []
        self.body_font = QFont()
        self.body_font.setPointSizeF(10)
        self.title_font = QFont(self.body_font)
        self.title_font.setBold(True)
        metrics = QFontMetricsF(self.body_font)
        widest = max([metrics.horizontalAdvance(name) + 40] + [
            metrics.horizontalAdvance(str(c.get("name", ""))) + metrics.horizontalAdvance(str(c.get("type", ""))) + 85
            for c in columns
        ])
        self.width = max(265.0, min(440.0, widest))
        self.height = self.HEADER + self.ROW * max(1, len(columns)) + 10
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(1)
        details = [name] + [f"{'PK · ' if c.get('pk') else ''}{c.get('name', '')}  {c.get('type', '')}" for c in columns]
        self.setToolTip("\n".join(details))

    def boundingRect(self) -> QRectF:
        return QRectF(-1, -1, self.width + 2, self.height + 2)

    def paint(self, painter: QPainter, option, widget=None):
        body = QRectF(0, 0, self.width, self.height)
        painter.setPen(QPen(QColor("#3675db" if self.isSelected() else "#c7d4e5"), 1.6 if self.isSelected() else 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(body, 7, 7)
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(body, 7, 7)
        painter.setClipPath(clip)
        painter.fillRect(QRectF(0, 0, self.width, self.HEADER), QColor("#e8f0fc"))
        painter.restore()
        painter.setFont(self.title_font)
        painter.setPen(QColor("#254a7b"))
        title = QFontMetricsF(self.title_font).elidedText(self.name, Qt.TextElideMode.ElideRight, self.width - 28)
        painter.drawText(QRectF(14, 0, self.width - 28, self.HEADER), Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(self.body_font)
        metrics = QFontMetricsF(self.body_font)
        if not self.columns:
            painter.setPen(QColor("#8a96a8"))
            painter.drawText(QRectF(14, self.HEADER, self.width - 28, self.ROW), Qt.AlignmentFlag.AlignVCenter, "No columns")
        for index, column in enumerate(self.columns):
            top = self.HEADER + index * self.ROW
            if index % 2:
                painter.fillRect(QRectF(1, top, self.width - 2, self.ROW), QColor("#f7f9fc"))
            painter.setPen(QColor("#3675db"))
            if column.get("pk"):
                painter.drawText(QRectF(10, top, 25, self.ROW), Qt.AlignmentFlag.AlignVCenter, "PK")
            type_width = min(self.width * 0.4, metrics.horizontalAdvance(str(column.get("type", ""))) + 8)
            name_width = self.width - type_width - 56
            name = metrics.elidedText(str(column.get("name", "")), Qt.TextElideMode.ElideRight, name_width)
            painter.setPen(QColor("#2d3c52"))
            painter.drawText(QRectF(39, top, name_width, self.ROW), Qt.AlignmentFlag.AlignVCenter, name)
            value_type = metrics.elidedText(str(column.get("type", "")), Qt.TextElideMode.ElideRight, type_width)
            painter.setPen(QColor("#7c8da5"))
            painter.drawText(QRectF(self.width - type_width - 12, top, type_width, self.ROW),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, value_type)

    def anchor(self, columns: list[str], right: bool) -> QPointF:
        index = next((i for i, c in enumerate(self.columns) if c.get("name") in columns), None)
        y = self.HEADER / 2 if index is None else self.HEADER + (index + 0.5) * self.ROW
        return self.mapToScene(QPointF(self.width if right else 0, y))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update_path()
        return super().itemChange(change, value)


class ForeignKeyEdge(QGraphicsPathItem):
    def __init__(self, source: TableNode, target: TableNode, metadata: dict):
        super().__init__()
        self.source, self.target, self.metadata = source, target, metadata
        self.setPen(QPen(QColor("#7997c0"), 1.7))
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.arrow = QGraphicsPolygonItem(self)
        self.arrow.setPen(QPen(Qt.PenStyle.NoPen))
        self.arrow.setBrush(QColor("#7997c0"))
        self.setZValue(0)
        columns = ", ".join(str(c) for c in metadata.get("columns", []))
        references = ", ".join(str(c) for c in metadata.get("referenced_columns", []))
        self.setToolTip(f"{source.name} ({columns}) → {target.name} ({references})")
        source.edges.append(self)
        if target is not source:
            target.edges.append(self)
        self.update_path()

    def update_path(self):
        rightward = self.target.sceneBoundingRect().center().x() >= self.source.sceneBoundingRect().center().x()
        self.start = self.source.anchor(self.metadata.get("columns", []), rightward)
        self.end = self.target.anchor(self.metadata.get("referenced_columns", []), not rightward)
        path = QPainterPath(self.start)
        direction = 1 if rightward else -1
        bend = max(65.0, abs(self.end.x() - self.start.x()) * 0.45)
        if self.source is self.target:
            # A loop stays outside the node and terminates at its title/column.
            self.start = self.source.anchor(self.metadata.get("columns", []), True)
            self.end = self.target.anchor(self.metadata.get("referenced_columns", []), True)
            direction = -1
            path = QPainterPath(self.start)
            path.cubicTo(self.start + QPointF(95, 0), self.end + QPointF(95, -50), self.end)
        else:
            path.cubicTo(self.start + QPointF(direction * bend, 0), self.end - QPointF(direction * bend, 0), self.end)
        # Separate fill prevents the open Bézier curve itself being filled.
        self.arrow.setPolygon(QPolygonF([self.end, self.end + QPointF(-direction * 9, -4),
                                        self.end + QPointF(-direction * 9, 4)]))
        self.setPath(path)


class DiagramView(QGraphicsView):
    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            scale = self.transform().m11() * factor
            if 0.08 <= scale <= 5:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class SchemaDiagram(QWidget):
    def __init__(self, connection_name: str, schema: str, tables: list[dict], parent=None):
        super().__init__(parent)
        self.connection_name, self.schema = connection_name, schema
        self.nodes: dict[str, TableNode] = {}
        self.edges: list[ForeignKeyEdge] = []
        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(QColor("#f7f9fc"))
        self.view = DiagramView(self.scene)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setMinimumSize(360, 220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(14, 10, 14, 10)
        title = QLabel(f"{connection_name} / {schema}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("tableTitle")
        toolbar.addWidget(title)
        toolbar.addStretch()
        for label, callback in [("−", lambda: self.zoom(1 / 1.2)), ("+", lambda: self.zoom(1.2)),
                                ("100%", self.reset_zoom), ("Fit", self.fit), ("Export…", self.export_dialog)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        layout.addWidget(self.view, 1)
        self.status = QLabel()
        self.status.setObjectName("pageStatus")
        layout.addWidget(self.status)

        columns = max(1, math.ceil(math.sqrt(len(tables))))
        row_y, max_height = 30.0, 0.0
        for metadata in tables:
            name = str(metadata.get("name", ""))
            if not name or name in self.nodes:
                continue
            node = TableNode(name, list(metadata.get("columns", [])))
            number = len(self.nodes)
            if number and number % columns == 0:
                row_y += max_height + 100
                max_height = 0.0
            node.setPos(30 + (number % columns) * 550, row_y)
            max_height = max(max_height, node.height)
            self.scene.addItem(node)
            self.nodes[name] = node

        self.unresolved_relationships = 0
        for metadata in tables:
            source = self.nodes.get(str(metadata.get("name", "")))
            if not source:
                continue
            for foreign_key in metadata.get("foreign_keys", []):
                target = self._target_node(foreign_key)
                if target is None:
                    self.unresolved_relationships += 1
                    continue
                edge = ForeignKeyEdge(source, target, foreign_key)
                self.scene.addItem(edge)
                self.edges.append(edge)
        if not self.nodes:
            message = self.scene.addSimpleText("No tables in this schema")
            message.setBrush(QColor("#7c8da5"))
            message.setPos(30, 30)
        text = f"{len(self.nodes)} tables · {len(self.edges)} relationships · Drag tables to arrange · Ctrl+wheel to zoom"
        if self.unresolved_relationships:
            text += f" · {self.unresolved_relationships} references outside this diagram"
        self.status.setText(text)
        self.view.setToolTip("Read-only schema snapshot. Moving a table changes only this diagram's layout.")
        QTimer.singleShot(0, self.fit)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def _target_node(self, foreign_key: dict) -> TableNode | None:
        target_schema = foreign_key.get("referenced_schema")
        if target_schema is not None and target_schema != self.schema:
            return None
        value = str(foreign_key.get("referenced_table", ""))
        if value in self.nodes:
            return self.nodes[value]
        parts = _identifier_parts(value)
        if len(parts) == 1:
            return self.nodes.get(parts[0])
        if len(parts) == 2 and parts[0] == self.schema:
            return self.nodes.get(parts[1])
        return None

    def zoom(self, factor: float):
        value = self.view.transform().m11() * factor
        if 0.08 <= value <= 5:
            self.view.scale(factor, factor)

    def reset_zoom(self):
        self.view.resetTransform()

    def fit(self):
        bounds = self.scene.itemsBoundingRect().adjusted(-35, -35, 35, 35)
        self.scene.setSceneRect(bounds)
        self.view.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def export_dialog(self):
        filename, selected_filter = QFileDialog.getSaveFileName(self, "Export schema diagram", "schema-diagram.png",
                                                               "PNG image (*.png);;SVG vector image (*.svg)")
        if filename:
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix(".svg" if "SVG" in selected_filter else ".png")
            try:
                self.export_to(path)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "Export diagram", str(exc))

    def export_to(self, path: str | Path):
        """Paint the entire diagram, independent of viewport position or zoom."""
        path = Path(path)
        bounds = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        if path.suffix.lower() == ".svg":
            generator = QSvgGenerator()
            generator.setFileName(str(path))
            size = QSize(max(1, math.ceil(bounds.width())), max(1, math.ceil(bounds.height())))
            generator.setSize(size)
            generator.setViewBox(QRectF(0, 0, size.width(), size.height()))
            generator.setTitle(f"{self.connection_name} / {self.schema}")
            generator.setDescription("Database schema snapshot generated by OpenNavicat; layout only, no schema changes.")
            painter = QPainter(generator)
            if not painter.isActive():
                raise OSError(f"Could not write diagram: {path}")
            try:
                self.scene.render(painter, QRectF(0, 0, size.width(), size.height()), bounds)
            finally:
                painter.end()
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError(f"Could not write diagram: {path}")
        elif path.suffix.lower() == ".png":
            # Bound raster allocation even when the schema has thousands of rows.
            scale = min(1.5, 16000 / max(bounds.width(), bounds.height()),
                        math.sqrt(32_000_000 / max(1, bounds.width() * bounds.height())))
            image = QImage(max(1, math.ceil(bounds.width() * scale)), max(1, math.ceil(bounds.height() * scale)),
                           QImage.Format.Format_ARGB32_Premultiplied)
            if image.isNull():
                raise RuntimeError("Not enough memory to render this diagram; use SVG export.")
            image.fill(QColor("#f7f9fc"))
            painter = QPainter(image)
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            try:
                self.scene.render(painter, QRectF(0, 0, image.width(), image.height()), bounds)
            finally:
                painter.end()
            if not image.save(str(path), "PNG"):
                raise OSError(f"Could not write diagram: {path}")
        else:
            raise ValueError("Choose a .png or .svg file for diagram export.")
