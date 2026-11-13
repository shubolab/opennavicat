"""Independently drawn primary, object and connection pictograms.

The painter uses a 32 by 32 logical canvas.  Symbols use hand-authored Qt
geometry; the literal Chinese provider name uses the system font.  No image or
other application's resources are loaded.  Scaling, QIcon modes and device-
pixel ratios belong to the caller.
"""
from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen


_DRIVER_COLORS = {
    "sqlite": ("#5fd5be", "#43bea5"),
    "mysql": ("#79c730", "#65b326"),
    "postgresql": ("#3674df", "#255dcc"),
    "sqlserver": ("#ef9c3f", "#da7d27"),
    "oracle": ("#f3544b", "#e73b36"),
    "mariadb": ("#b38b63", "#96734f"),
    "mongodb": ("#b87b32", "#a15f24"),
    "redis": ("#f0a1ad", "#e68a9b"),
    "snowflake": ("#46bcd7", "#28a1c1"),
}

_PROVIDER_NAMES = frozenset({
    "provider_alibaba", "provider_huawei", "provider_tencent", "provider_kingbase",
    "provider_pingcap", "provider_highgo", "provider_oceanbase", "provider_aws",
    "provider_fujitsu", "provider_google", "provider_microsoft",
    "provider_oracle_cloud", "provider_mongodb_atlas",
})

PRIMARY_NAMES = frozenset({
    "connection", "new_query", "table", "view", "function", "other", "user",
    "query", "backup", "automation", "model", "chart", "bi", "index",
    "trigger", "ai", "info", "folder", "database", "redis_data",
    *_DRIVER_COLORS, *_PROVIDER_NAMES,
})


def _stroke(p: QPainter, color, width: float = 1.0) -> None:
    brush = QBrush(QColor(color) if isinstance(color, str) else color)
    p.setPen(QPen(brush, width, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _gradient(top: str, bottom: str, y: float = 2, height: float = 28):
    gradient = QLinearGradient(0, y, 0, y + height)
    gradient.setColorAt(0, QColor(top))
    gradient.setColorAt(1, QColor(bottom))
    return gradient


def _fill(p: QPainter, path: QPainterPath, color) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color) if isinstance(color, str) else color)
    p.drawPath(path)


def _round(p: QPainter, x: float, y: float, w: float, h: float, color,
           radius: float = 2) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), radius, radius)
    _fill(p, path, color)


def _ellipse(p: QPainter, x: float, y: float, w: float, h: float, color) -> None:
    path = QPainterPath()
    path.addEllipse(QRectF(x, y, w, h))
    _fill(p, path, color)


def _line(p: QPainter, points, color: str, width: float = 1.0) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    _stroke(p, color, width)
    p.drawPath(path)


def _polygon(p: QPainter, points, color) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    _fill(p, path, color)


def _tile(p: QPainter, x=1.0, y=1.0, w=30.0, h=30.0,
          *, orange=False, view=False, query=False) -> None:
    """Rounded panel: a blue header above adjoining solid mosaic tiles."""
    if orange:
        colors = ("#f69d7e", "#ee8763", "#ffd6c9", "#fdc5b3", "#ffae94", "#fbe9e7", "#ffccbc")
    elif query:
        colors = ("#1689db", "#087dd3", "#90caf9", "#bbdefb", "#64b5f6", "#e3f2fd", "#a9d8fb")
    else:
        colors = ("#0983d8", "#057ad2", "#b8dfff" if view else "#abd9ff",
                  "#cde8ff", "#96d0ff", "#e3f2fd", "#7fc6ff")
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(x, y, w, h), 2.8, 2.8)
    p.save()
    try:
        p.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        _round(p, x, y, w, h, _gradient(colors[0], colors[1], y, h), 2.8)
        header = h * .29
        cell_w = w / 3
        cell_h = (h - header) / 3
        for row in range(3):
            for col in range(3):
                # Adjacent cells overlap slightly under antialiasing: there is
                # no stroke or transparent/white gutter masquerading as a grid.
                p.setPen(Qt.PenStyle.NoPen)
                cell = ((0, 1, 2), (1, 3, 4), (2, 0, 1))[row][col]
                p.setBrush(QColor(colors[2 + cell]))
                p.drawRect(QRectF(x + col * cell_w, y + header + row * cell_h,
                                 cell_w + .1, cell_h + .1))
        _line(p, [(x + 2, y + 1.1), (x + w - 2, y + 1.1)],
              "#55ffffff", .65)
    finally:
        p.restore()


def _plus(p: QPainter, x=25, y=25) -> None:
    _ellipse(p, x - 6, y - 6, 12, 12, "#45c300")
    _line(p, [(x - 3, y), (x + 3, y)], "#ffffff", 1.8)
    _line(p, [(x, y - 3), (x, y + 3)], "#ffffff", 1.8)


def _query(p: QPainter, *, new=False) -> None:
    _tile(p, 11, 1, 20, 21, orange=True)
    _tile(p, 1, 10.5, 22, 20.5, query=not new)
    if new:
        _plus(p, 25, 25)


def _view(p: QPainter) -> None:
    _tile(p, 1, 1, 30, 26.5, view=True)
    _stroke(p, "#0375cf", 2)
    p.drawEllipse(QRectF(2.8, 23.5, 10.3, 6.7))
    p.drawEllipse(QRectF(18.9, 23.5, 10.3, 6.7))
    _line(p, [(13.1, 26.2), (15.1, 25), (16.9, 25), (18.9, 26.2)], "#0375cf", 1.8)
    _line(p, [(3.1, 25.7), (1.5, 23.1)], "#0375cf", 1.7)
    _line(p, [(28.9, 25.7), (30.5, 23.1)], "#0375cf", 1.7)


def _function(p: QPainter) -> None:
    # A hand-lettered italic f(x), rather than a platform-dependent font.
    f = QPainterPath(QPointF(21.2, 5.1))
    f.cubicTo(16.6, 2.7, 13.5, 5.7, 12.6, 10)
    f.lineTo(9.7, 23.8)
    f.cubicTo(8.8, 28.2, 6.3, 29.6, 2.6, 27.7)
    blue = _gradient("#25a3fa", "#0e8ceb", 3, 26)
    _stroke(p, blue, 2.3)
    p.drawPath(f)
    _line(p, [(6.5, 12.1), (18.3, 12.1)], blue, 2.3)
    left = QPainterPath(QPointF(20, 16))
    left.cubicTo(16.4, 18.1, 15.6, 24.3, 18.1, 27.9)
    _stroke(p, blue, 1.5)
    p.drawPath(left)
    right = QPainterPath(QPointF(29.4, 16))
    right.cubicTo(31.3, 19.6, 30, 25.6, 26.8, 28)
    p.drawPath(right)
    _line(p, [(21, 19), (25.7, 25)], blue, 1.65)
    _line(p, [(26.5, 19), (20.1, 25)], blue, 1.65)


def _user(p: QPainter) -> None:
    body = QPainterPath(QPointF(3.5, 29))
    body.lineTo(4.3, 24.6)
    body.cubicTo(5.2, 20.5, 10.8, 19.9, 13.2, 19.3)
    body.lineTo(18.8, 19.3)
    body.cubicTo(21.2, 19.9, 26.8, 20.5, 27.7, 24.6)
    body.lineTo(28.5, 29)
    body.closeSubpath()
    _fill(p, body, _gradient("#f9b800", "#f5ab00", 20, 9))
    _round(p, 13, 16.3, 6, 6.5, "#ffe3cc", 1)
    _ellipse(p, 9.7, 4.5, 12.6, 15.5, "#ffe2ca")
    hair = QPainterPath(QPointF(9.4, 14))
    hair.cubicTo(6.8, 8.3, 9.2, 2.5, 15.4, 2.4)
    hair.cubicTo(21.6, 1.5, 24.2, 6.9, 22, 14.8)
    hair.lineTo(20.4, 11.7)
    hair.lineTo(19.9, 8.9)
    hair.cubicTo(16.8, 10.1, 13.4, 7.7, 12, 6.3)
    hair.lineTo(10.3, 10.2)
    hair.closeSubpath()
    _fill(p, hair, "#9f571d")
    _polygon(p, [(10.9, 20.1), (15.9, 24), (13, 20.5)], "#fff7df")
    _polygon(p, [(21.1, 20.1), (16.1, 24), (19, 20.5)], "#fff7df")


def _other(p: QPainter) -> None:
    # A toolbox base, with two separate crossed steel tools.
    _round(p, 5, 20, 23, 9.5, _gradient("#0985d9", "#0579d1", 20, 10), 2)
    _round(p, 9, 17.9, 15, 5, "#0961a4", 1.5)
    driver = QPainterPath(QPointF(8.3, 3))
    driver.cubicTo(7.3, 2, 5.2, 3.2, 5.8, 4.6)
    driver.lineTo(10.8, 13.5)
    driver.lineTo(13.1, 13)
    driver.lineTo(19.1, 24)
    driver.lineTo(20.8, 23.1)
    driver.lineTo(14.7, 12)
    driver.lineTo(15.3, 10.2)
    driver.closeSubpath()
    _fill(p, driver, _gradient("#7a8690", "#c1c8cc", 3, 22))
    wrench = QPainterPath(QPointF(10.3, 24.6))
    wrench.lineTo(18.1, 13.1)
    wrench.cubicTo(14.5, 8.5, 18.5, 2.4, 22.2, 3.2)
    wrench.lineTo(20.4, 7.5)
    wrench.lineTo(23.4, 9.4)
    wrench.lineTo(27, 6.6)
    wrench.cubicTo(28.1, 11.2, 25.3, 15, 21.5, 14.9)
    wrench.lineTo(13.1, 26.2)
    wrench.closeSubpath()
    _fill(p, wrench, _gradient("#ccd3d7", "#7a8690", 4, 22))
    _line(p, [(11.8, 24.4), (20, 13)], "#e5eaec", .7)
    _line(p, [(6.2, 27.9), (26.8, 27.9)], "#88c8f8", .7)


def _backup(p: QPainter) -> None:
    _round(p, 1.5, 1.5, 29, 29, _gradient("#838c93", "#535a60", 1.5, 29), 3.6)
    arc = QPainterPath(QPointF(10.7, 24.3))
    arc.cubicTo(23.8, 29.2, 29, 16.7, 22.1, 10.1)
    arc.cubicTo(17.8, 5.9, 11.2, 7.1, 8.8, 10.9)
    _stroke(p, "#ffffff", 2.6)
    p.drawPath(arc)
    _polygon(p, [(5.7, 6), (15.3, 7.3), (7.1, 15.1)], "#ffffff")


def _automation(p: QPainter) -> None:
    _line(p, [(16, 3.1), (16, 6.2)], "#4b9ea6", 1.8)
    _ellipse(p, 13.8, .8, 4.4, 4.4, "#ed7779")
    _round(p, 3.5, 6, 25, 19, _gradient("#31c28e", "#028387", 6, 19), 6)
    _round(p, 6.8, 9.1, 18.5, 11.9, "#ffffff", 5)
    _ellipse(p, 10.2, 12.8, 4, 4.7, "#224880")
    _ellipse(p, 18, 12.8, 4, 4.7, "#224880")
    _round(p, 8, 25, 6, 4.3, "#028387", 1.2)
    _round(p, 19, 25, 6, 4.3, "#028387", 1.2)
    _round(p, .7, 12.3, 2.5, 7.1, "#31c28e", 1.2)
    _round(p, 28.8, 12.3, 2.5, 7.1, "#31c28e", 1.2)
    _ellipse(p, 19, 19.2, 12.5, 12.5, "#869bba")
    _ellipse(p, 20.5, 20.7, 9.5, 9.5, "#fcfdfd")
    _line(p, [(25.2, 22), (25.2, 25.4), (27.3, 26.4)], "#224880", 1.2)


def _model(p: QPainter) -> None:
    _line(p, [(9, 7), (25, 7)], "#c8681f", 2.1)
    _line(p, [(7, 10), (7, 25)], "#c8681f", 2.1)
    for x, y in ((1, 1), (19, 1), (1, 19)):
        _round(p, x, y, 12, 12, _gradient("#ffb12d", "#ffaa35", y, 12), 2)
        _stroke(p, "#c8681f", 1)
        p.drawRoundedRect(QRectF(x, y, 12, 12), 2, 2)
        _round(p, x + 1.7, y + 1.5, 8.6, 3, "#ffd078", .55)


def _bi(p: QPainter) -> None:
    for x, y, h, top, bottom in (
        (1, 11.5, 19, "#cf6ddc", "#b64ac8"),
        (7.1, 1.5, 29, "#b96bd5", "#a446c3"),
        (13.2, 8.5, 22, "#d66cdd", "#bd4acb"),
        (19.3, 4.5, 26, "#c66bd7", "#ad45c3"),
        (25.4, 15.5, 15, "#da74df", "#bc4dcb"),
    ):
        _round(p, x, y, 5.6, h, _gradient(top, bottom, y, h), 2.8)


def _index(p: QPainter) -> None:
    color = "#169fdd"
    _line(p, [(2.5, 25), (7, 7.2), (11.5, 25)], color, 2.2)
    _line(p, [(4.2, 19.2), (9.7, 19.2)], color, 1.8)
    _line(p, [(13.4, 16.7), (17, 16.7)], color, 1.6)
    _line(p, [(20, 7.7), (29.3, 7.7), (20, 25), (29.3, 25)], color, 2.2)


def _trigger(p: QPainter) -> None:
    _polygon(p, [(14.1, 2), (27.1, 2), (20.4, 12), (26, 12),
                 (7.1, 30), (12.4, 18.1), (6, 18.1)],
             _gradient("#ffdc56", "#efbb2e", 2, 28))


def _info(p: QPainter) -> None:
    blue = "#269fdb"
    _stroke(p, blue, 1.9)
    p.drawEllipse(QRectF(2, 2, 28, 28))
    _ellipse(p, 14.4, 8, 3.2, 3.2, blue)
    _round(p, 14.7, 13.6, 2.6, 10.6, blue, .5)


def _ai(p: QPainter) -> None:
    path = QPainterPath(QPointF(16, 1.8))
    path.cubicTo(17.8, 11, 20.8, 14, 30.2, 16)
    path.cubicTo(20.8, 17.9, 17.8, 21, 16, 30.2)
    path.cubicTo(14.1, 21, 11, 17.9, 1.8, 16)
    path.cubicTo(11, 14, 14.1, 11, 16, 1.8)
    _stroke(p, "#72a5e5", 1.7)
    p.drawPath(path)


def _folder(p: QPainter) -> None:
    back = QPainterPath(QPointF(2.5, 9))
    back.quadTo(2.5, 6, 5.5, 6)
    back.lineTo(12, 6)
    back.quadTo(14, 6, 15.5, 9)
    back.lineTo(26.8, 9)
    back.quadTo(29.5, 9, 29.5, 12)
    back.lineTo(29.5, 25.7)
    back.quadTo(29.5, 28, 27, 28)
    back.lineTo(5, 28)
    back.quadTo(2.5, 28, 2.5, 25.7)
    back.closeSubpath()
    _fill(p, back, "#e7ac3b")
    _round(p, 2.5, 12, 27, 16, _gradient("#ffda6b", "#f0be4b", 12, 16), 2.5)
    _line(p, [(5, 13.1), (27, 13.1)], "#ffe69c", .8)


def _database(p: QPainter) -> None:
    # Three green shelves and their visible elliptical shoulders.
    for y in (14.5, 8.3, 2.1):
        _round(p, 2, y + 4, 28, 8, _gradient("#6bc38b", "#45a86a", y+4, 8), 1.4)
        _ellipse(p, 2, y + 7.8, 28, 7, "#4daa6d")
        _ellipse(p, 2, y, 28, 8, _gradient("#94d9aa", "#65bc81", y, 8))
    _ellipse(p, 4, 3.6, 24, 4.7, "#b5e4c0")


def _connection(p: QPainter) -> None:
    p.save()
    try:
        p.translate(15.2, 15.4)
        p.rotate(42)
        _line(p, [(0, 7), (0, 17)], "#6591b3", 3.5)
        _round(p, -5.6, -9.5, 11.2, 11, _gradient("#9cabb6", "#717d86", -9.5, 11), 2.5)
        _round(p, -5.6, 1.6, 11.2, 10, _gradient("#8e9aa5", "#6591b3", 1.6, 10), 2.4)
        _line(p, [(-3, -12.5), (-3, -9)], "#8d99a4", 2)
        _line(p, [(3, -12.5), (3, -9)], "#8d99a4", 2)
        _line(p, [(-3.6, -6.6), (3.6, -6.6)], "#edf1f3", .9)
    finally:
        p.restore()
    _plus(p, 25, 25)


def _driver_glyph(p: QPainter, name: str, cutout: str) -> None:
    white = "#ffffff"
    if name == "oracle":
        _stroke(p, white, 2.5)
        p.drawRoundedRect(QRectF(5.8, 10.8, 20.4, 10.4), 5.2, 5.2)
    elif name == "sqlite":
        path = QPainterPath(QPointF(26.9, 5.2))
        path.cubicTo(17, 5.2, 10.6, 10.3, 9.1, 20.3)
        path.lineTo(9.3, 24.2)
        path.cubicTo(15.2, 22.9, 23.1, 13.8, 26.9, 5.2)
        _fill(p, path, white)
        _line(p, [(7.1, 27), (12.7, 18), (22.2, 8.7)], white, 1.5)
        _line(p, [(12.5, 18.8), (20.1, 13.4)], cutout, .85)
        _line(p, [(11.3, 21), (17.2, 17.8)], cutout, .8)
    elif name == "mysql":
        path = QPainterPath(QPointF(4.5, 21.5))
        path.cubicTo(7.8, 17.4, 12.6, 12.7, 18.5, 12.2)
        path.lineTo(17.5, 7)
        path.cubicTo(20.6, 8.2, 22.6, 10.4, 23.2, 12.2)
        path.cubicTo(26, 12.5, 27.6, 14.5, 28.5, 17.2)
        path.cubicTo(25.3, 15.9, 22.4, 16.2, 19.9, 17.5)
        path.cubicTo(15.4, 20, 13.7, 23.8, 10.6, 25.5)
        path.lineTo(10.9, 21.8)
        path.lineTo(5.2, 25)
        path.lineTo(7.2, 21.2)
        path.closeSubpath()
        _fill(p, path, white)
        _ellipse(p, 24, 13.5, 1.2, 1.2, cutout)
    elif name == "postgresql":
        path = QPainterPath(QPointF(16, 7.2))
        path.cubicTo(11.1, 3, 5.1, 6.2, 5.5, 12.8)
        path.cubicTo(5.6, 17.1, 7.4, 21.5, 11.2, 21.3)
        path.lineTo(14.2, 18.4)
        path.lineTo(15.1, 24.2)
        path.cubicTo(16.2, 29.9, 23.4, 29.1, 24.8, 24.9)
        path.lineTo(22.6, 22.9)
        path.cubicTo(21.2, 26.9, 18.8, 26.4, 18.9, 23.4)
        path.lineTo(19.6, 17.7)
        path.cubicTo(24.2, 22.4, 27, 17.5, 26.5, 11)
        path.cubicTo(26.2, 5.8, 20.5, 3.7, 16, 7.2)
        _fill(p, path, white)
        _line(p, [(12.2, 9.1), (11.8, 15.3), (14.3, 17.5)], cutout, .8)
        _line(p, [(21, 8.7), (22.1, 14.8)], cutout, .8)
        _ellipse(p, 14.2, 11.3, 1.2, 1.2, cutout)
        _ellipse(p, 19.5, 11.3, 1.2, 1.2, cutout)
    elif name == "sqlserver":
        # A woven sail: curved boundary plus crossing ribs.
        path = QPainterPath(QPointF(8.3, 6.7))
        path.cubicTo(13.1, 8.7, 20.1, 6.5, 25.2, 4.7)
        path.cubicTo(18.9, 11.9, 16.5, 17.4, 26, 25.6)
        path.cubicTo(18.7, 24.6, 11.8, 23, 5.7, 25.2)
        path.cubicTo(12.2, 18.3, 12.6, 13, 8.3, 6.7)
        _stroke(p, white, 1.8)
        p.drawPath(path)
        for points in (
            [(9.6, 9), (22.1, 8)], [(10.6, 12.1), (20.1, 11.4)],
            [(11.3, 15.2), (18.8, 15.1)], [(10.8, 18.4), (21.1, 19.1)],
            [(8.6, 22), (23.8, 23.4)], [(12, 7.4), (15.9, 24.3)],
            [(17, 7), (17, 23.7)], [(22, 5.9), (12.1, 24.2)],
        ):
            _line(p, points, white, .8)
    elif name == "mariadb":
        path = QPainterPath(QPointF(4.1, 24.3))
        path.lineTo(8.3, 17.7)
        path.cubicTo(10.8, 14.5, 16.2, 16.9, 20.1, 12.9)
        path.cubicTo(23.1, 9.9, 26.8, 9.5, 28, 9.7)
        path.lineTo(25.2, 12.4)
        path.cubicTo(24.3, 15.7, 23.7, 19.3, 19.2, 21.2)
        path.cubicTo(14, 23.2, 11.4, 22.5, 9.8, 22)
        path.lineTo(10.7, 25.3)
        path.lineTo(7.2, 23.6)
        path.closeSubpath()
        _fill(p, path, white)
        _ellipse(p, 24.6, 11.6, 1.2, 1.2, cutout)
        _line(p, [(13.4, 19.9), (17.7, 19.9)], cutout, .8)
    elif name == "mongodb":
        path = QPainterPath(QPointF(16.3, 4.1))
        path.cubicTo(12.4, 8.5, 8.7, 11.4, 10, 17.1)
        path.cubicTo(10.6, 21, 14, 24, 16.2, 25.8)
        path.cubicTo(19.6, 23.9, 23.1, 19.5, 22.6, 15)
        path.cubicTo(22.1, 10.6, 18.6, 7.9, 16.3, 4.1)
        _fill(p, path, white)
        _line(p, [(16.2, 25), (15.7, 28)], white, 1.4)
        _line(p, [(16.1, 9), (16.3, 23)], cutout, .7)
    elif name == "redis":
        swirl = QPainterPath(QPointF(8.1, 24.9))
        swirl.cubicTo(12.6, 24.8, 12.7, 15.9, 16, 9.3)
        swirl.cubicTo(18.9, 3.4, 26.9, 6.9, 24, 11.5)
        swirl.cubicTo(21.9, 14.4, 15.2, 14.4, 11.2, 15.3)
        swirl.cubicTo(7.2, 16.3, 5.5, 20, 8.1, 21.3)
        swirl.cubicTo(11.9, 23, 16.1, 18.8, 17.3, 16.1)
        _stroke(p, white, 2)
        p.drawPath(swirl)
        _line(p, [(17.1, 14), (22.5, 25)], white, 2)
        _line(p, [(20.8, 25), (25.1, 25)], white, 1.7)
    elif name == "snowflake":
        for k in range(6):
            a = k * pi / 3 - pi / 2
            dx, dy = cos(a), sin(a)
            _line(p, [(16, 16), (16 + dx * 10.5, 16 + dy * 10.5)], white, 1.65)
            for direction in (-1, 1):
                b = a + direction * pi / 3
                x, y = 16 + dx * 6.7, 16 + dy * 6.7
                _line(p, [(x + cos(b) * 3.2, y + sin(b) * 3.2), (x, y)], white, 1.35)


def _driver(p: QPainter, name: str, *, disconnected: bool) -> None:
    top, bottom = ("#b3b9bc", "#949da2") if disconnected else _DRIVER_COLORS[name]
    _round(p, 1, 1, 30, 30, _gradient(top, bottom), 4.7)
    _driver_glyph(p, name, bottom)


def _provider_text(p: QPainter, text: str, color: str, *, pixels: int,
                   bold=False, rect=QRectF(3, 3, 26, 26)) -> None:
    # Literal provider names are text, not imported logo images or font outlines.
    font = QFont()
    font.setPixelSize(pixels)
    font.setBold(bold)
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _provider(p: QPainter, name: str) -> None:
    _round(p, 1.8, 1.8, 28.4, 28.4, "#ffffff", 4.7)
    if name == "provider_alibaba":
        orange = "#ee8526"
        _line(p, [(10.5, 7.5), (6, 7.5), (4.6, 9), (4.6, 23),
                  (6, 24.5), (10.5, 24.5)], orange, 2.2)
        _line(p, [(21.5, 7.5), (26, 7.5), (27.4, 9), (27.4, 23),
                  (26, 24.5), (21.5, 24.5)], orange, 2.2)
        _round(p, 12, 14.8, 8, 2.5, orange, .5)
    elif name == "provider_huawei":
        # Eight separated petals, opening upwards from a common lower centre.
        for k in range(8):
            angle = (-165 + 150 * k / 7) * pi / 180
            dx, dy = cos(angle), sin(angle)
            sx, sy = -dy, dx
            tip = (16 + dx * 14.5, 23 + dy * 18)
            path = QPainterPath(QPointF(16 + dx * 2, 23 + dy * 2))
            path.cubicTo(16 + dx * 10 + sx * 2.5, 23 + dy * 12 + sy * 2.5,
                         tip[0] + sx * 1.3, tip[1] + sy * 1.3, *tip)
            path.cubicTo(tip[0] - sx * 1.3, tip[1] - sy * 1.3,
                         16 + dx * 10 - sx * 2.5, 23 + dy * 12 - sy * 2.5,
                         16 + dx * 2, 23 + dy * 2)
            _fill(p, path, "#d72d39")
    elif name == "provider_tencent":
        blue = QPainterPath(QPointF(17.1, 11.5))
        blue.cubicTo(11.7, 6.5, 5.2, 9, 5.2, 15.5)
        blue.cubicTo(5.2, 21.4, 12.1, 24.6, 17.3, 20.4)
        _stroke(p, "#1389d6", 3.6)
        p.drawPath(blue)
        cyan = QPainterPath(QPointF(14.6, 11.4))
        cyan.cubicTo(18.9, 5.2, 27, 7.9, 27, 14.7)
        cyan.cubicTo(27, 20.6, 21.3, 23.5, 16.2, 20.1)
        _stroke(p, "#25bbd4", 3.6)
        p.drawPath(cyan)
    elif name == "provider_kingbase":
        red = "#c92d39"
        _round(p, 6.2, 5.5, 4.4, 21, red, .5)
        _polygon(p, [(10, 14.5), (21.7, 5.5), (27.1, 5.5),
                     (15, 15.7), (27.5, 26.5), (21.5, 26.5), (10, 16.7)], red)
    elif name == "provider_pingcap":
        _round(p, 5.5, 5, 6.3, 22, "#4b57a6", .5)
        _round(p, 11.8, 5, 13.9, 5.3, "#7880c5", .4)
        _round(p, 20.2, 10.3, 5.5, 8.8, "#9b68b8", .4)
        _round(p, 11.8, 14.1, 8.4, 5, "#5c62ad", .4)
        _round(p, 11.8, 10.3, 5.3, 3.8, "#c4b4d5", .25)
    elif name == "provider_highgo":
        _provider_text(p, "瀚高", "#242529", pixels=11, bold=True)
    elif name == "provider_oceanbase":
        blue = "#2288c7"
        _stroke(p, blue, 2.2)
        p.drawEllipse(QRectF(4.3, 9, 10.7, 14))
        b = QPainterPath(QPointF(18.4, 23))
        b.lineTo(18.4, 9)
        b.lineTo(22.1, 9)
        b.cubicTo(28.3, 9, 28.6, 15.1, 23.7, 15.5)
        b.cubicTo(29.3, 16.2, 28.1, 23, 22.6, 23)
        b.closeSubpath()
        p.drawPath(b)
        _line(p, [(18.6, 15.5), (23.4, 15.5)], blue, 1.6)
    elif name == "provider_aws":
        ink = "#333a45"
        _stroke(p, ink, 1.8)
        p.drawEllipse(QRectF(5, 11.1, 5.8, 8.3))
        _line(p, [(10.8, 11.1), (10.8, 19.8)], ink, 1.8)
        _line(p, [(13.3, 11.2), (15.1, 19.8), (18.1, 13.5),
                  (20.7, 19.8), (22.8, 11.2)], ink, 1.8)
        s = QPainterPath(QPointF(28.1, 11.6))
        s.cubicTo(23.4, 8.4, 22.4, 14.4, 26.1, 15.1)
        s.cubicTo(30.6, 16.2, 28, 21.5, 24.3, 19)
        _stroke(p, ink, 1.65)
        p.drawPath(s)
        smile = QPainterPath(QPointF(6, 23))
        smile.quadTo(16, 29, 26, 22.8)
        _stroke(p, "#ef9d24", 1.8)
        p.drawPath(smile)
        _line(p, [(22.8, 22.1), (26.5, 22.3), (25.3, 25.5)], "#ef9d24", 1.1)
    elif name == "provider_fujitsu":
        infinity = QPainterPath(QPointF(15.9, 15.9))
        infinity.cubicTo(10.9, 7.5, 4.2, 9.2, 4.2, 16.1)
        infinity.cubicTo(4.2, 23.3, 11.1, 24.2, 16, 16)
        infinity.cubicTo(21, 7.7, 27.8, 9.1, 27.8, 16.1)
        infinity.cubicTo(27.8, 23.3, 21.1, 24.1, 15.9, 15.9)
        _stroke(p, "#db3045", 2)
        p.drawPath(infinity)
    elif name == "provider_google":
        # Independent four-colour cloud outline, with joins hidden by round caps.
        blue = QPainterPath(QPointF(11, 24))
        blue.lineTo(9, 24)
        blue.cubicTo(1.7, 24, 1.7, 15.3, 8.4, 14.5)
        _stroke(p, "#4285e8", 4)
        p.drawPath(blue)
        red = QPainterPath(QPointF(8.4, 14.5))
        red.cubicTo(8.2, 6.9, 18.7, 4.8, 22.4, 11.6)
        _stroke(p, "#e64e43", 4)
        p.drawPath(red)
        yellow = QPainterPath(QPointF(22.4, 11.6))
        yellow.cubicTo(28.7, 11, 31.1, 19.3, 26.4, 22.4)
        _stroke(p, "#f4bc30", 4)
        p.drawPath(yellow)
        green = QPainterPath(QPointF(26.4, 22.4))
        green.quadTo(24.7, 24, 22, 24)
        green.lineTo(11, 24)
        _stroke(p, "#43a85b", 4)
        p.drawPath(green)
    elif name == "provider_microsoft":
        for x, y, color in ((5.2, 5.2, "#ee5a32"), (16.6, 5.2, "#80b843"),
                            (5.2, 16.6, "#32a9d8"), (16.6, 16.6, "#f1bc33")):
            _round(p, x, y, 10.2, 10.2, color, .1)
    elif name == "provider_oracle_cloud":
        _stroke(p, "#dc4a49", 2.6)
        p.drawRoundedRect(QRectF(5.3, 10.5, 21.4, 11), 5.5, 5.5)
    elif name == "provider_mongodb_atlas":
        leaf = QPainterPath(QPointF(16.2, 4.2))
        leaf.cubicTo(9.9, 10.6, 6.3, 17.4, 16, 26.1)
        leaf.cubicTo(25.6, 20.1, 23.6, 12.7, 16.2, 4.2)
        _fill(p, leaf, "#4b9f58")
        _line(p, [(16, 24.9), (15.8, 28.2)], "#477f4b", 1.4)
        _line(p, [(16.2, 9), (16, 23.9)], "#e6f1e4", .8)


def draw_primary(painter: QPainter, name: str, *, connection_state=None) -> bool:
    """Paint a known name in the caller's 32-unit canvas; otherwise return False.

    Driver ``connection_state`` may be ``None`` (brand-color chooser artwork),
    ``"connected"`` or ``"disconnected"`` (grey badge).  Other states are not
    invented here.  The painter's transform, clipping, pen and brush survive.
    Non-driver names ignore connection_state so a wrapper may pass it uniformly.
    """
    if name not in PRIMARY_NAMES:
        return False
    if name in _DRIVER_COLORS and connection_state not in (None, "connected", "disconnected"):
        raise ValueError("Unsupported connection icon state")
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if name in _PROVIDER_NAMES:
            _provider(painter, name)
        elif name in _DRIVER_COLORS:
            _driver(painter, name, disconnected=connection_state == "disconnected")
        elif name in {"table", "redis_data"}:
            _tile(painter)
        elif name == "view":
            _view(painter)
        elif name == "function":
            _function(painter)
        elif name == "user":
            painter.translate(16, 16)
            painter.scale(1.12, 1.12)
            painter.translate(-16, -16)
            _user(painter)
        elif name == "other":
            painter.translate(16, 16)
            painter.scale(1.2, 1.12)
            painter.translate(-16, -16)
            _other(painter)
        elif name in {"new_query", "query"}:
            _query(painter, new=name == "new_query")
        elif name == "backup":
            _backup(painter)
        elif name == "automation":
            _automation(painter)
        elif name == "model":
            _model(painter)
        elif name in {"bi", "chart"}:
            _bi(painter)
        elif name == "index":
            _index(painter)
        elif name == "trigger":
            _trigger(painter)
        elif name == "info":
            _info(painter)
        elif name == "ai":
            _ai(painter)
        elif name == "folder":
            _folder(painter)
        elif name == "database":
            _database(painter)
        elif name == "connection":
            _connection(painter)
    finally:
        painter.restore()
    return True
