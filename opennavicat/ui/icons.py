"""Original, Qt-painted workbench icons.

The pictograms share common database-tool metaphors but are drawn here from
simple geometry. They do not contain artwork or resources from another app.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

NAMES = (
    "connection", "new_query", "table", "view", "function", "other", "user",
    "query", "backup", "automation", "model", "chart", "bi", "index", "trigger", "ai", "folder", "database",
    "sqlite", "postgresql", "mysql", "mariadb", "sqlserver", "redis", "oracle", "mongodb", "snowflake", "redis_data", "open", "design", "add",
    "delete", "import", "export", "refresh", "run", "stop", "save", "format",
    "filter", "list", "er", "info", "navigation", "commit", "rollback",
)

BLUE = "#459bd9"
BLUE_DARK = "#2472b2"
BLUE_LIGHT = "#97d4fa"
ORANGE = "#e5a04c"
ORANGE_DARK = "#ac6d30"
GREEN = "#7bb75e"
GREEN_DARK = "#49813c"
PURPLE = "#a78bce"
GRAY = "#b9b9b9"
GRAY_DARK = "#777b80"
INK = "#4a5159"
WHITE = "#f0f3f5"


def _pen(p: QPainter, color: str, width: float = 1.0):
    p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))


def _rect(p, x, y, w, h, fill, stroke=None, radius=0):
    p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
    if stroke:
        _pen(p, stroke)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    bounds = QRectF(x, y, w, h)
    p.drawRoundedRect(bounds, radius, radius) if radius else p.drawRect(bounds)


def _line(p, x1, y1, x2, y2, color=INK, width=1.2):
    _pen(p, color, width)
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(p, points, fill, stroke=None, width=1):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
    if stroke:
        _pen(p, stroke, width)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(path)


def _ellipse(p, x, y, w, h, fill, stroke=None, width=1):
    p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
    if stroke:
        _pen(p, stroke, width)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(x, y, w, h))


def _table(p, x=5, y=6, w=22, h=21, color=BLUE):
    _rect(p, x, y, w, h, "#e1edf5", BLUE_DARK, 1)
    _rect(p, x + .6, y + .6, w - 1.2, 5.2, color)
    for k in range(1, 3):
        _line(p, x + w * k / 3, y + 6, x + w * k / 3, y + h, "#80a6c1", .8)
    for k in range(1, 4):
        yy = y + 6 + (h - 6) * k / 4
        _line(p, x, yy, x + w, yy, "#80a6c1", .8)


def _database(p, x=6, y=4, w=20, h=24, color=BLUE):
    _rect(p, x, y + 4, w, h - 8, color, BLUE_DARK)
    _ellipse(p, x, y + h - 8, w, 8, color, BLUE_DARK)
    _rect(p, x + .6, y + 5, w - 1.2, h - 10, color)
    for yy in (y + 8, y + 14):
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, "#b0dcf4", 1)
        p.drawArc(QRectF(x + .7, yy, w - 1.4, 7), 180 * 16, 180 * 16)
    _ellipse(p, x, y, w, 8, BLUE_LIGHT if color == BLUE else color, BLUE_DARK)


def _plus(p, x=24, y=24, color=GREEN, backing=True):
    if backing:
        _ellipse(p, x - 6, y - 6, 12, 12, "#f1f3ec", GREEN_DARK, .7)
    _line(p, x - 3.5, y, x + 3.5, y, color, 2.8)
    _line(p, x, y - 3.5, x, y + 3.5, color, 2.8)


def _arrow(p, x, y, direction="right", color=BLUE, width=3, length=12):
    dx, dy = {"right": (1, 0), "left": (-1, 0), "down": (0, 1), "up": (0, -1)}[direction]
    ex, ey = x + dx * length, y + dy * length
    _line(p, x, y, ex, ey, color, width)
    head = min(5.5, length * .45)
    _line(p, ex - dx * head + dy * head, ey - dy * head - dx * head, ex, ey, color, width)
    _line(p, ex - dx * head - dy * head, ey - dy * head + dx * head, ex, ey, color, width)


def _document(p, x=7, y=3, w=18, h=25):
    _poly(p, [(x, y), (x + w - 6, y), (x + w, y + 6), (x + w, y + h), (x, y + h)], WHITE, GRAY_DARK)
    _poly(p, [(x + w - 6, y), (x + w - 6, y + 6), (x + w, y + 6)], "#cbd9e2", GRAY_DARK)


def _draw(p: QPainter, name: str):
    if name == "connection":
        _line(p, 5, 26, 11, 20, BLUE_DARK, 3)
        _poly(p, [(8, 15), (16, 7), (24, 15), (16, 23)], BLUE, BLUE_DARK)
        _line(p, 17, 6, 21, 2, BLUE_LIGHT, 2.8)
        _line(p, 23, 12, 27, 8, BLUE_LIGHT, 2.8)
        _plus(p, 24, 25)
    elif name in {"table", "redis_data"}:
        _table(p)
    elif name == "index":
        font = p.font()
        font.setPixelSize(17)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(BLUE))
        p.drawText(1, 23, "A-Z")
    elif name == "trigger":
        _poly(p, [(15, 2), (28, 2), (20, 13), (27, 13), (8, 31), (13, 18), (5, 18)], "#f3c444")
    elif name == "bi":
        for x, y, height, color in [(4, 9, 16, "#bb48ca"), (12, 3, 26, "#a74bcb"), (21, 7, 19, "#d05ddb")]:
            _rect(p, x, y, 7, height, color, radius=2)
    elif name == "ai":
        _poly(p, [(16, 2), (20, 12), (30, 16), (20, 20), (16, 30), (12, 20), (2, 16), (12, 12)], "#b6c8f5", BLUE)
    elif name == "view":
        _table(p, y=4, h=20)
        # Two lenses and a bridge make this visibly distinct from a table.
        _ellipse(p, 5, 20, 10, 8, "#cfecfa", INK, 1.8)
        _ellipse(p, 18, 20, 10, 8, "#cfecfa", INK, 1.8)
        _line(p, 15, 23, 18, 23, INK, 1.7)
        _line(p, 4, 22, 2, 18, INK, 1.5)
        _line(p, 29, 22, 30, 18, INK, 1.5)
    elif name == "function":
        _ellipse(p, 5, 4, 22, 24, "#e2a54f", ORANGE_DARK)
        _pen(p, "#fff8e8", 2)
        path = QPainterPath(QPointF(20, 8))
        path.cubicTo(12, 5, 17, 27, 10, 25)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        _line(p, 11, 14, 22, 14, "#fff8e8", 1.8)
    elif name == "other":
        for x, y, c in [(5, 5, BLUE), (18, 5, GREEN), (5, 18, ORANGE), (18, 18, PURPLE)]:
            _rect(p, x, y, 9, 9, c, INK, 1)
    elif name == "user":
        _ellipse(p, 10, 3, 12, 12, "#e3b581", "#a37e58")
        path = QPainterPath(QPointF(5, 28))
        path.cubicTo(5, 12, 27, 12, 27, 28)
        path.closeSubpath()
        p.setBrush(QColor(BLUE))
        _pen(p, BLUE_DARK)
        p.drawPath(path)
        _poly(p, [(13, 17), (16, 23), (19, 17)], "#ecf0ed")
    elif name in {"query", "new_query"}:
        _table(p, 9, 3, 20, 19, ORANGE)
        _table(p, 3, 10, 20, 19, BLUE)
        if name == "new_query":
            _plus(p, 25, 25)
    elif name == "backup":
        _rect(p, 4, 5, 24, 23, "#aaaeb2", "#656a70", 1)
        _rect(p, 6, 7, 20, 6, "#d1d4d7")
        _rect(p, 8, 17, 16, 8, "#727a80", "#5f656b")
        _arrow(p, 16, 13, "down", "#ecf2f5", 2.7, 10)
    elif name == "automation":
        _line(p, 16, 3, 16, 7, GRAY, 1.5)
        _ellipse(p, 14, 1, 4, 4, ORANGE)
        _rect(p, 6, 7, 20, 17, "#b7c1c8", "#6a7781", 3)
        _rect(p, 9, 10, 14, 7, "#586776", None, 1)
        _ellipse(p, 11, 12, 3, 3, BLUE_LIGHT)
        _ellipse(p, 18, 12, 3, 3, BLUE_LIGHT)
        for xx in (11, 15, 19):
            _line(p, xx, 21, xx + 1, 21, INK, 1.3)
        _line(p, 3, 12, 3, 21, GRAY, 3)
        _line(p, 29, 12, 29, 21, GRAY, 3)
        _line(p, 11, 25, 11, 29, GRAY, 3)
        _line(p, 21, 25, 21, 29, GRAY, 3)
    elif name in {"model", "er"}:
        _line(p, 11, 11, 22, 11, GRAY, 1.6)
        _line(p, 16, 11, 16, 24, GRAY, 1.6)
        _line(p, 9, 24, 24, 24, GRAY, 1.6)
        for x, y in [(2, 3), (20, 3), (10, 20)]:
            _rect(p, x, y, 10, 9, "#eeb86c", ORANGE_DARK)
            _rect(p, x + 1, y + 1, 8, 2.5, ORANGE)
            _line(p, x + 2, y + 6, x + 7, y + 6, ORANGE_DARK, .8)
        if name == "er":
            _ellipse(p, 2, 24, 5, 5, BLUE, BLUE_DARK)
    elif name == "chart":
        _line(p, 4, 4, 4, 28, GRAY_DARK, 1)
        _line(p, 4, 28, 29, 28, GRAY_DARK, 1)
        for x, y, h, c in [(7, 17, 10, "#a18ac6"), (15, 8, 19, "#b59bd8"), (23, 12, 15, "#8b72b4")]:
            _rect(p, x, y, 5, h, c, "#715b98")
    elif name in {"folder", "open"}:
        _poly(p, [(3, 7), (13, 7), (16, 10), (29, 10), (29, 27), (3, 27)], "#dfaf59", "#a37b34")
        _poly(p, [(3, 13), (30, 13), (26, 27), (3, 27)], "#ecc773", "#a37b34")
        if name == "open":
            _arrow(p, 11, 17, "right", "#fff3c9", 2, 12)
    elif name in {"database", "sqlite", "postgresql", "mysql", "mariadb", "sqlserver", "redis", "oracle", "mongodb", "snowflake"}:
        _database(p, color={"sqlserver": ORANGE, "redis": "#d4515a", "oracle": "#d4515a", "mongodb": GREEN, "snowflake": BLUE_LIGHT}.get(name, BLUE))
        if name == "sqlite":
            _poly(p, [(20, 15), (30, 9), (25, 25), (17, 29)], "#e5eef4", "#637c8d")
            _line(p, 18, 29, 27, 15, "#637c8d", 1)
        elif name == "postgresql":
            _ellipse(p, 18, 17, 11, 10, "#7097bb", "#325b83")
            _ellipse(p, 15, 16, 7, 8, "#91b2ce", "#325b83")
            _line(p, 26, 23, 26, 29, "#325b83", 3)
            _line(p, 26, 29, 23, 29, "#325b83", 2)
            _ellipse(p, 24, 19, 1.5, 1.5, "#203e58")
        elif name == "mysql":
            path = QPainterPath(QPointF(16, 26))
            path.cubicTo(22, 15, 30, 18, 29, 23)
            path.cubicTo(26, 20, 24, 24, 20, 25)
            path.lineTo(16, 29)
            path.closeSubpath()
            p.setBrush(QColor(ORANGE))
            _pen(p, ORANGE_DARK)
            p.drawPath(path)
        elif name == "mariadb":
            _ellipse(p, 17, 20, 13, 7, "#c2c8ca", "#6d797e")
            _poly(p, [(17, 22), (13, 18), (14, 26), (18, 25)], "#a1abad", "#6d797e")
            _ellipse(p, 26, 20, 2, 2, INK)
    elif name == "design":
        _table(p, 3, 5, 20, 21)
        _poly(p, [(16, 25), (26, 10), (30, 13), (20, 28)], "#edc36b", ORANGE_DARK)
        _poly(p, [(16, 25), (15, 30), (20, 28)], "#ece4ca", ORANGE_DARK)
    elif name == "add":
        _plus(p, 16, 16, GREEN, False)
    elif name == "delete":
        _line(p, 7, 7, 25, 25, "#d56c64", 4)
        _line(p, 7, 25, 25, 7, "#d56c64", 4)
    elif name in {"import", "export"}:
        _table(p, 6, 5, 20, 22)
        if name == "import":
            _arrow(p, 2, 18, "right", GREEN, 3.5, 16)
        else:
            _arrow(p, 15, 18, "right", ORANGE, 3.5, 15)
    elif name == "refresh":
        path = QPainterPath(QPointF(25, 11))
        path.cubicTo(18, -1, 3, 5, 5, 18)
        path.cubicTo(6, 30, 24, 31, 27, 20)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, BLUE, 3.2)
        p.drawPath(path)
        _poly(p, [(19, 12), (28, 13), (27, 4)], BLUE)
    elif name == "run":
        _poly(p, [(9, 4), (27, 16), (9, 28)], GREEN, GREEN_DARK)
    elif name == "stop":
        _rect(p, 7, 7, 18, 18, "#cf7770", "#954b47", 1)
    elif name == "save":
        _rect(p, 5, 3, 22, 26, BLUE, BLUE_DARK, 1)
        _rect(p, 9, 4, 14, 9, "#d9e8f1", BLUE_DARK)
        _rect(p, 19, 5, 3, 6, "#6d7a85")
        _rect(p, 9, 18, 14, 10, "#ecf0f3", BLUE_DARK)
        for y in (21, 24):
            _line(p, 11, y, 20, y, "#9faeb8", .8)
    elif name in {"format", "list"}:
        for index, yy in enumerate((7, 13, 19, 25)):
            start = 10 if name == "list" else 5 + (index % 2) * 5
            _line(p, start, yy, 27, yy, BLUE if index % 2 == 0 else GRAY, 2)
            if name == "list":
                _rect(p, 4, yy - 1.5, 3, 3, ORANGE)
    elif name == "filter":
        _poly(p, [(3, 5), (29, 5), (19, 17), (19, 27), (13, 24), (13, 17)], "#c8cfd4", "#727c84")
        _line(p, 6, 7, 25, 7, "#eef2f4", 1)
    elif name == "info":
        _ellipse(p, 4, 4, 24, 24, BLUE, BLUE_DARK)
        _ellipse(p, 14.4, 9, 3.2, 3.2, WHITE)
        _line(p, 16, 15, 16, 24, WHITE, 2.8)
    elif name == "navigation":
        _rect(p, 3, 4, 26, 24, "#d8e1e7", GRAY_DARK)
        _rect(p, 4, 5, 8, 22, "#91a9bb")
        for y in (9, 14, 19, 24):
            _line(p, 6, y, 10, y, "#e9f0f3", 1)
        _arrow(p, 15, 16, "right", BLUE, 2, 10)
    elif name == "commit":
        _ellipse(p, 3, 3, 26, 26, "#eef4e9", GREEN_DARK, 1)
        _line(p, 9, 16, 14, 22, GREEN_DARK, 3)
        _line(p, 14, 22, 24, 10, GREEN_DARK, 3)
    elif name == "rollback":
        path = QPainterPath(QPointF(7, 12))
        path.cubicTo(25, 3, 32, 24, 19, 27)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, ORANGE, 3)
        p.drawPath(path)
        _poly(p, [(4, 5), (4, 17), (15, 14)], ORANGE)
    else:
        raise ValueError(f"Unknown workbench icon: {name}")


@lru_cache(maxsize=256)
def icon(name: str, size: int = 32) -> QIcon:
    """Return a high-DPI-capable semantic icon at a logical pixel size.

    Call after creating QApplication. Multiple raster resolutions are registered
    with QIcon so native controls select a crisp image at their device ratio.
    """
    if not isinstance(size, int) or not 8 <= size <= 256:
        raise ValueError("Icon size must be an integer between 8 and 256")
    if name not in NAMES:
        raise ValueError(f"Unknown workbench icon: {name}")
    result = QIcon()
    for factor in (1, 2, 3):
        pixmap = QPixmap(size * factor, size * factor)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.scale(size * factor / 32, size * factor / 32)
            _draw(painter, name)
        finally:
            painter.end()
        pixmap.setDevicePixelRatio(factor)
        result.addPixmap(pixmap)
    return result


# Small action bars use thin, unfilled pictograms instead of the larger colored
# object illustrations above. Coordinates are independently drawn at 18px.
TOOL_NAMES = (
    "open", "design", "add", "delete", "import", "export", "refresh", "save",
    "format", "snippets", "filter", "sort", "columns", "chart", "builder",
    "transaction_begin", "transaction_commit", "transaction_rollback",
    "record_add", "record_delete", "record_apply", "record_cancel",
    "first", "previous", "next", "last", "navigation", "info", "run", "stop",
)
_TOOL_BLUE = "#679fc4"
_TOOL_GREEN = "#81a76b"
_TOOL_RED = "#ba7770"
_TOOL_ORANGE = "#c99b63"
_TOOL_GRAY = "#9a9a9a"
_TOOL_LIGHT = "#b4b4b4"
_TOOL_ALIASES = {
    "model": "builder", "query_builder": "builder", "code": "snippets",
    "new_query": "snippets", "query": "snippets", "function": "snippets",
    "table": "columns", "view": "columns", "column": "columns",
    "new": "add", "remove": "delete", "prev": "previous",
    "begin": "transaction_begin", "transaction": "transaction_begin",
    "commit": "transaction_commit", "rollback": "transaction_rollback",
    "record_insert": "record_add", "record_remove": "record_delete",
    "record_save": "record_apply", "record_revert": "record_cancel",
}


def _tool_path(p, points, color=_TOOL_BLUE, width=1.05, closed=False, fill=None):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    if closed:
        path.closeSubpath()
    _pen(p, color, width)
    p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _tool_cylinder(p):
    p.setBrush(Qt.BrushStyle.NoBrush)
    _pen(p, _TOOL_GRAY, 1)
    p.drawEllipse(QRectF(2, 2, 10.5, 3.8))
    p.drawLine(QPointF(2, 3.9), QPointF(2, 12.8))
    p.drawLine(QPointF(12.5, 3.9), QPointF(12.5, 9))
    p.drawArc(QRectF(2, 10.8, 10.5, 3.8), 180 * 16, 180 * 16)
    p.drawArc(QRectF(2, 6.4, 10.5, 3.8), 180 * 16, 180 * 16)


def _draw_tool(p, name):
    blue, gray = _TOOL_BLUE, _TOOL_GRAY
    if name == "open":
        _tool_path(p, [(2, 6), (2, 3.5), (6.5, 3.5), (8, 5.3), (15, 5.3), (15, 7)], blue)
        _tool_path(p, [(2, 7), (16.3, 7), (14.6, 14.8), (2, 14.8)], blue, closed=True)
    elif name == "design":
        _tool_path(p, [(2.5, 15.5), (3.7, 11.5), (12.6, 2.6), (15.4, 5.4), (6.5, 14.3)], blue, closed=True)
        _line(p, 11, 4.2, 13.8, 7, blue, 1)
        _line(p, 3.7, 11.5, 6.5, 14.3, blue, 1)
    elif name in {"add", "delete"}:
        color = _TOOL_GREEN if name == "add" else _TOOL_RED
        _ellipse(p, 2.4, 2.4, 13.2, 13.2, None, color, 1.1)
        _line(p, 5.7, 9, 12.3, 9, color, 1.1)
        if name == "add":
            _line(p, 9, 5.7, 9, 12.3, color, 1.1)
    elif name in {"import", "export"}:
        color = _TOOL_GREEN if name == "import" else _TOOL_ORANGE
        if name == "import":
            _tool_path(p, [(8.5, 4), (15.5, 4), (15.5, 15.5), (4.5, 15.5), (4.5, 12)], gray)
            path = QPainterPath(QPointF(2, 3))
            path.cubicTo(1.5, 9.5, 5.5, 10.5, 11.5, 10.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            _pen(p, color, 1.2)
            p.drawPath(path)
            _tool_path(p, [(8.5, 7.5), (11.5, 10.5), (8.5, 13.5)], color, 1.2)
        else:
            _tool_path(p, [(10, 9), (10, 15.5), (2.5, 15.5), (2.5, 5), (6.5, 5)], gray)
            path = QPainterPath(QPointF(6, 12))
            path.cubicTo(6, 5, 9, 3.5, 16, 3.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            _pen(p, color, 1.2)
            p.drawPath(path)
            _tool_path(p, [(13, .8), (16, 3.5), (13, 6.3)], color, 1.2)
    elif name == "refresh":
        path = QPainterPath(QPointF(14.8, 6.8))
        path.cubicTo(12.5, .5, 4, 1.5, 2.5, 7.8)
        path.cubicTo(.6, 15.2, 11.5, 18, 15.2, 11.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, gray, 1.2)
        p.drawPath(path)
        _tool_path(p, [(11, 6.8), (14.8, 6.8), (14.8, 3)], gray, 1.2)
    elif name == "save":
        _tool_path(p, [(2.5, 2), (13, 2), (15.5, 4.5), (15.5, 16), (2.5, 16)], blue, closed=True)
        _tool_path(p, [(5.5, 2), (5.5, 7), (12, 7), (12, 2)], blue)
        _tool_path(p, [(5.5, 15.8), (5.5, 10), (12.5, 10), (12.5, 15.8)], blue)
        _line(p, 10, 3.5, 10, 5.5, blue, 1)
    elif name == "format":
        _tool_path(p, [(3, 14.3), (11.8, 5.5), (14, 7.7), (5.2, 16.5)], blue, closed=True)
        _line(p, 10, 7.3, 12.2, 9.5, blue, 1)
        for start, end in [((11.5, .8), (11.5, 3.3)), ((15, 2.5), (13.5, 4.1)),
                           ((16.8, 6), (15, 6)), ((7.3, 3), (9, 4.3))]:
            _line(p, *start, *end, blue, 1)
    elif name == "snippets":
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, blue, 1.15)
        for x, control in [(5, 1.8), (13, 16.2)]:
            path = QPainterPath(QPointF(x, 2))
            path.cubicTo(control, 5, control, 13, x, 16)
            p.drawPath(path)
        for y in (6, 9, 12):
            _line(p, 7.5, y, 10.5, y, blue, .9)
    elif name == "filter":
        _tool_path(p, [(2, 3), (16, 3), (10.5, 9.5), (10.5, 15.5), (7.5, 14), (7.5, 9.5)], gray, closed=True)
    elif name == "sort":
        _line(p, 4, 2, 4, 15, blue, 1.15)
        _tool_path(p, [(1.5, 12.5), (4, 15), (6.5, 12.5)], blue, 1.15)
        for y, end in [(4, 16), (8, 14), (12, 12)]:
            _line(p, 9, y, end, y, gray, 1)
    elif name == "columns":
        _rect(p, 2, 3.5, 14, 12, None, blue)
        _line(p, 2, 7.5, 16, 7.5, blue, 1)
        _line(p, 6.5, 3.5, 6.5, 15.5, blue, 1)
        _line(p, 11.2, 3.5, 11.2, 15.5, blue, 1)
    elif name == "chart":
        _tool_path(p, [(2, 2), (2, 16), (16, 16)], blue)
        for x, y, h in [(5, 10, 5.5), (9, 4.5, 11), (13, 7.5, 8)]:
            _rect(p, x, y, 2, h, None, blue)
    elif name == "builder":
        for x, y in [(1.5, 2), (11.5, 2), (6.5, 12)]:
            _rect(p, x, y, 5, 4, None, blue)
        _tool_path(p, [(4, 6), (4, 9), (14, 9), (14, 6)], gray, .9)
        _line(p, 9, 9, 9, 12, gray, .9)
    elif name.startswith("transaction_"):
        _tool_cylinder(p)
        if name == "transaction_begin":
            _tool_path(p, [(11, 9), (17, 12.2), (11, 15.5)], blue, closed=True)
        elif name == "transaction_commit":
            _tool_path(p, [(10, 12.5), (12.5, 15), (17, 9)], blue, 1.4)
        else:
            path = QPainterPath(QPointF(16.5, 14.5))
            path.cubicTo(18, 8, 11, 7.5, 9.5, 11)
            p.setBrush(Qt.BrushStyle.NoBrush)
            _pen(p, blue, 1.2)
            p.drawPath(path)
            _tool_path(p, [(9.5, 7.8), (9.5, 11), (12.7, 11)], blue, 1.2)
    elif name.startswith("record_"):
        color = _TOOL_LIGHT
        if name in {"record_add", "record_delete"}:
            _line(p, 4, 9, 14, 9, color, 1.1)
            if name == "record_add":
                _line(p, 9, 4, 9, 14, color, 1.1)
        elif name == "record_apply":
            _tool_path(p, [(3.2, 9), (7.1, 12.9), (14.8, 4.2)], color, 1.2)
        else:
            _line(p, 4.5, 4.5, 13.5, 13.5, color, 1.1)
            _line(p, 4.5, 13.5, 13.5, 4.5, color, 1.1)
    elif name in {"first", "previous", "next", "last"}:
        left = name in {"first", "previous"}
        if left:
            points = [(11.5, 4), (6, 9), (11.5, 14)]
        else:
            points = [(6.5, 4), (12, 9), (6.5, 14)]
        _tool_path(p, points, gray, 1.1, closed=True)
        if name in {"first", "last"}:
            x = 3.5 if left else 14.5
            _line(p, x, 4, x, 14, gray, 1.1)
    elif name in {"navigation", "info"}:
        _rect(p, 2, 3, 14, 12, None, blue)
        _line(p, 6.2, 3, 6.2, 15, blue, 1)
        for y in (6.5, 9, 11.5):
            _line(p, 3.5, y, 4.8, y, gray, .8)
        if name == "navigation":
            _tool_path(p, [(10.5, 6), (13.5, 9), (10.5, 12)], blue)
        else:
            _line(p, 11, 8, 11, 12.5, blue, 1.1)
            _ellipse(p, 10.5, 5.5, 1, 1, blue)
    elif name == "run":
        _tool_path(p, [(5, 2.8), (15, 9), (5, 15.2)], blue, 1.05, closed=True)
    elif name == "stop":
        _rect(p, 4, 4, 10, 10, None, gray)


@lru_cache(maxsize=256)
def tool_icon(name: str, size: int = 18) -> QIcon:
    """Return a high-DPI thin-outline icon for secondary/action toolbars.

    Object-toolbar ``icon`` remains independent. Less common names fall back to
    the existing original illustrations rather than loading external artwork.
    """
    if not isinstance(size, int) or not 8 <= size <= 256:
        raise ValueError("Icon size must be an integer between 8 and 256")
    canonical = _TOOL_ALIASES.get(name, name)
    if canonical not in TOOL_NAMES:
        return icon(name if name in NAMES else "other", size)
    result = QIcon()
    for factor in (1, 2, 3):
        pixmap = QPixmap(size * factor, size * factor)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(size * factor / 18, size * factor / 18)
            _draw_tool(painter, canonical)
        finally:
            painter.end()
        pixmap.setDevicePixelRatio(factor)
        result.addPixmap(pixmap)
    return result
