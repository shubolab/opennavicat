"""Independently drawn 18px action artwork for the retained database tools.

Only vector primitives are used. The caller owns the paint device, logical-size
transform, display density, and QIcon mode/state registration. No reference
artwork, image resources, fonts, widgets, or files are loaded here.
"""
from __future__ import annotations

from collections.abc import Mapping
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


_DEFAULT_COLORS = {
    "blue": "#3098ee", "gray": "#96999d", "light": "#b9bdc1",
    "green": "#6eb950", "yellow": "#d5ab40", "red": "#d4635d",
    "white": "#ffffff",
}


def _pen(p, color, width=1.05):
    p.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _line(p, a, b, color, width=1.05):
    _pen(p, color, width)
    p.drawLine(QPointF(*a), QPointF(*b))


def _path(p, points, color, width=1.05, *, closed=False):
    shape = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        shape.lineTo(QPointF(*point))
    if closed:
        shape.closeSubpath()
    _pen(p, color, width)
    p.drawPath(shape)


def _box(p, x, y, w, h, color, width=1.05, radius=0):
    _pen(p, color, width)
    bounds = QRectF(x, y, w, h)
    if radius:
        p.drawRoundedRect(bounds, radius, radius)
    else:
        p.drawRect(bounds)


def _ellipse(p, x, y, w, h, color, width=1.05):
    _pen(p, color, width)
    p.drawEllipse(QRectF(x, y, w, h))


def _curve(p, start, curves, color, width=1.05):
    shape = QPainterPath(QPointF(*start))
    for controls in curves:
        shape.cubicTo(*controls)
    _pen(p, color, width)
    p.drawPath(shape)


def _solid(p, points, color):
    shape = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        shape.lineTo(QPointF(*point))
    shape.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(shape)

def _check(p, color, x=8.5, y=10.5, scale=1):
    _path(p, [(x, y + 2.4*scale), (x + 2.3*scale, y + 4.6*scale),
              (x + 6.1*scale, y)], color, 1.15)


def _paper(p, color, x=3, y=1.8, w=11.8, h=14.3):
    _path(p, [(x, y), (x + w - 3.5, y), (x + w, y + 3.5),
              (x + w, y + h), (x, y + h)], color, closed=True)
    _path(p, [(x + w - 3.5, y), (x + w - 3.5, y + 3.5),
              (x + w, y + 3.5)], color)


def _cylinder(p, color, *, x=2.2, y=2.4, w=10.4, h=12.7):
    _ellipse(p, x, y, w, 3.3, color)
    _line(p, (x, y + 1.65), (x, y + h - 1.7), color)
    _line(p, (x + w, y + 1.65), (x + w, y + h - 1.7), color)
    _pen(p, color)
    for yy in (y + 4.4, y + 8.5, y + h - 3.3):
        p.drawArc(QRectF(x, yy, w, 3.3), 180*16, 180*16)


def _open(p, c):
    _path(p, [(2, 6.3), (2, 3.8), (6.8, 3.8), (8.3, 5.4),
              (15.3, 5.4), (15.3, 7.2)], c['blue'], 1.1)
    _path(p, [(2, 7.3), (16.1, 7.3), (14.3, 14.8), (2, 14.8)],
          c['blue'], 1.1, closed=True)


def _design(p, c):
    _path(p, [(2.8, 15.3), (4.2, 10.9), (12.7, 2.4), (15.5, 5.2),
              (7, 13.7)], c['blue'], 1.15, closed=True)
    _line(p, (4.2, 10.9), (7, 13.7), c['blue'], 1.05)
    _line(p, (11.2, 3.9), (14, 6.7), c['blue'], 1.05)
    _path(p, [(2.8, 15.3), (4.9, 14.7), (3.4, 13.2)], c['gray'], .85)


def _add(p, c):
    _ellipse(p, 2.5, 2.5, 13, 13, c['green'], 1.1)
    _line(p, (5.5, 9), (12.5, 9), c['green'], 1.1)
    _line(p, (9, 5.5), (9, 12.5), c['green'], 1.1)


def _delete(p, c):
    _ellipse(p, 2.5, 2.5, 13, 13, c['red'], 1.1)
    _line(p, (5.5, 9), (12.5, 9), c['red'], 1.15)


def _open_paper(p, color):
    shape = QPainterPath(QPointF(11.6, 6))
    shape.lineTo(11.6, 3.8)
    shape.quadTo(11.6, 2.2, 10, 2.2)
    shape.lineTo(3.8, 2.2)
    shape.quadTo(2.2, 2.2, 2.2, 3.8)
    shape.lineTo(2.2, 12.4)
    shape.quadTo(2.2, 14, 3.8, 14)
    shape.lineTo(6.2, 14)
    _pen(p, color, 1.05)
    p.drawPath(shape)


def _import(p, c):
    _open_paper(p, c['gray'])
    _curve(p, (15.6, 14.6), [(15.6, 9.3, 12.2, 8.5, 7.7, 8.5)], c['green'], 1.15)
    _path(p, [(10.5, 5.7), (7.7, 8.5), (10.5, 11.3)], c['green'], 1.15)


def _export(p, c):
    _open_paper(p, c['gray'])
    _curve(p, (7.7, 14.6), [(7.7, 9.3, 11.1, 8.5, 15.6, 8.5)], c['yellow'], 1.15)
    _path(p, [(12.8, 5.7), (15.6, 8.5), (12.8, 11.3)], c['yellow'], 1.15)


def _refresh(p, c):
    _curve(p, (14.8, 6.6), [(12.4, 1.1, 5.1, 1.3, 2.8, 6.2),
                            (.3, 11.7, 5.1, 16.8, 10.6, 15.6),
                            (13, 15.1, 14.6, 13.4, 15.1, 11.2)], c['gray'], 1.1)
    _path(p, [(11.2, 6.6), (14.8, 6.6), (14.8, 3)], c['gray'], 1.1)


def _save(p, c):
    _path(p, [(2.6, 2.2), (12.6, 2.2), (15.4, 5), (15.4, 15.7),
              (2.6, 15.7)], c['gray'], 1.1, closed=True)
    _path(p, [(5.2, 2.3), (5.2, 7.3), (12, 7.3), (12, 2.3)], c['blue'])
    _line(p, (10, 3.8), (10, 5.7), c['blue'])
    _path(p, [(5.1, 15.5), (5.1, 10.3), (12.9, 10.3), (12.9, 15.5)], c['blue'])


def _test_success(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c['green'])
    p.drawEllipse(QRectF(2.5, 2.5, 13, 13))
    _path(p, [(5.4, 8.7), (7.7, 10.9), (11.9, 6.2)], c['white'], 1.3)


def _copy(p, c):
    # Only the exposed rear edges are visible behind the front paper.
    _path(p, [(4.5, 12.8), (2.8, 12.8), (2.8, 2.3),
              (11.1, 2.3), (11.1, 3.7)], c['blue'], 1)
    _box(p, 5.9, 5, 8.7, 10.7, c['blue'], 1)


def _format(p, c):
    _path(p, [(3, 14.9), (11.2, 6.7), (13, 8.5), (4.8, 16.7)],
          c['gray'], 1.05, closed=True)
    _line(p, (9.8, 8.1), (11.6, 9.9), c['gray'], .95)
    for a, b in (((12.1, 1.5), (12.1, 3.5)), ((15.6, 2.8), (14.2, 4.2)),
                 ((16.5, 6), (14.6, 6)), ((8.4, 2.6), (9.7, 3.9))):
        _line(p, a, b, c['blue'], 1.05)


def _code_braces(p, c):
    for x, side in ((6, -1), (12, 1)):
        _curve(p, (x, 2.3), [(x + 3*side, 2.3, x + side, 5.4, x + side, 7),
                            (x + side, 8.3, x + 2.8*side, 8.7, x + 2.8*side, 9),
                            (x + 2.8*side, 9.3, x + side, 9.7, x + side, 11),
                            (x + side, 12.6, x + 3*side, 15.7, x, 15.7)], c['blue'], 1.05)
    for y in (6, 9, 12):
        _line(p, (8.2, y), (9.8, y), c['gray'], .9)


def _snippets(p, c):
    _curve(p, (6.2, 2.2), [(2.1, 4.2, 2.1, 13.8, 6.2, 15.8)], c['blue'], 1.15)
    _curve(p, (11.8, 2.2), [(15.9, 4.2, 15.9, 13.8, 11.8, 15.8)], c['blue'], 1.15)


def _filter(p, c):
    _path(p, [(2.3, 3), (15.7, 3), (10.2, 9), (10.2, 15.3),
              (7.5, 13.7), (7.5, 9)], c['gray'], 1.05, closed=True)


def _sort(p, c):
    for y, right in ((4, 15.5), (8, 13.1), (12, 10.8)):
        _line(p, (8.7, y), (right, y), c['gray'], 1)
    _line(p, (4.3, 2.5), (4.3, 15.1), c['blue'], 1.1)
    _path(p, [(1.8, 12.6), (4.3, 15.1), (6.8, 12.6)], c['blue'], 1.1)


def _filter_sort(p, c):
    _path(p, [(1.5, 3.3), (9.7, 3.3), (6.2, 7.6), (6.2, 14.8),
              (4.2, 13.5), (4.2, 7.6)], c['gray'], .95, closed=True)
    for y, end in ((4.2, 16), (7.1, 14.8), (10, 13.7)):
        _line(p, (11.2, y), (end, y), c['gray'], .95)
    _line(p, (12.5, 11.8), (12.5, 16), c['blue'])
    _path(p, [(10.7, 14.3), (12.5, 16), (14.3, 14.3)], c['blue'])


def _grid_lines(p, color, x, y, w, h, rows=3, columns=3, width=1):
    _box(p, x, y, w, h, color, width)
    for index in range(1, columns):
        xx = x + w*index/columns
        _line(p, (xx, y), (xx, y + h), color, width)
    for index in range(1, rows):
        yy = y + h*index/rows
        _line(p, (x, yy), (x + w, yy), color, width)


def _columns(p, c):
    _grid_lines(p, c['gray'], 2.1, 2.7, 11.9, 12.3, rows=1, columns=3)
    _line(p, (2.1, 6), (14, 6), c['gray'])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c['green'])
    p.drawEllipse(QRectF(10, 10, 6.5, 6.5))
    _path(p, [(11.3, 13.2), (12.5, 14.4), (15, 11.9)], c['white'], 1.05)


def _chart(p, c):
    for x, height in ((1.6, 5), (4.8, 10), (8, 15), (11.2, 10), (14.4, 5)):
        _box(p, x, 9 - height/2, 1.9, height, c['blue'], 1, .6)


def _builder(p, c):
    _path(p, [(2.2, 7), (13.2, 3), (16, 8.2), (5, 12.2)],
          c['blue'], 1.1, closed=True)
    _line(p, (9.1, 10.9), (9.1, 16), c['gray'], 1.15)
    _line(p, (6.1, 16), (12.1, 16), c['gray'], 1.15)


def _explain(p, c):
    for x, y in ((1.6, 2.5), (11.1, 2.5), (6.3, 11.2)):
        _box(p, x, y, 5.3, 4.3, c['gray'], .95)
    _path(p, [(4.3, 6.9), (4.3, 9.1), (13.7, 9.1), (13.7, 6.9)], c['blue'], .95)
    _line(p, (9, 9.1), (9, 11.1), c['blue'], .95)


def _result_below(p, c):
    _box(p, 2.5, 2.5, 13, 13, c['blue'], 1.1)
    _line(p, (2.5, 9), (15.5, 9), c['blue'], 1.1)


def _result_beside(p, c):
    _box(p, 2.5, 2.5, 13, 13, c['blue'], 1.1)
    _line(p, (9, 2.5), (9, 15.5), c['blue'], 1.1)


def _transaction_begin(p, c):
    _cylinder(p, c['gray'], x=1.6, y=2.1, w=10.8, h=13.4)
    _path(p, [(11.1, 9.5), (16.5, 12.6), (11.1, 15.7)],
          c['blue'], 1.1, closed=True)


def _transaction_commit(p, c):
    _cylinder(p, c['gray'], x=1.6, y=2.1, w=10.8, h=13.4)
    _check(p, c['blue'], 9.5, 10.4, 1)


def _transaction_rollback(p, c):
    _cylinder(p, c['gray'], x=1.6, y=2.1, w=10.8, h=13.4)
    _curve(p, (15.9, 15), [(18, 10, 11.2, 7.9, 9.6, 11.8)], c['blue'], 1.1)
    _path(p, [(9.6, 8.5), (9.6, 11.8), (12.9, 11.8)], c['blue'], 1.1)


def _record_add(p, c):
    _line(p, (4, 9), (14, 9), c['gray'], 1.1)
    _line(p, (9, 4), (9, 14), c['gray'], 1.1)


def _record_delete(p, c):
    _line(p, (4, 9), (14, 9), c['gray'], 1.1)


def _record_apply(p, c):
    _path(p, [(3.4, 9), (7.1, 12.7), (14.7, 5.1)], c['gray'], 1.1)


def _record_cancel(p, c):
    _line(p, (4.8, 4.8), (13.2, 13.2), c['gray'], 1.1)
    _line(p, (4.8, 13.2), (13.2, 4.8), c['gray'], 1.1)


def _record_stop(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c['gray'])
    p.drawRect(QRectF(5.5, 5.5, 7, 7))


def _left_arrow(p, color, *, terminal=False):
    tip, base, stem = (5.8, 10.7, 16) if terminal else (2.5, 8.7, 15.1)
    _solid(p, [(tip, 9), (base, 4.2), (base, 7.6), (stem, 7.6),
               (stem, 10.4), (base, 10.4), (base, 13.8)], color)
    if terminal:
        _solid(p, [(2.7, 4.2), (4, 4.2), (4, 13.8), (2.7, 13.8)], color)


def _previous(p, c):
    _left_arrow(p, c['gray'])


def _next(p, c):
    p.translate(18, 0)
    p.scale(-1, 1)
    _left_arrow(p, c['gray'])


def _first(p, c):
    _left_arrow(p, c['gray'], terminal=True)


def _last(p, c):
    p.translate(18, 0)
    p.scale(-1, 1)
    _left_arrow(p, c['gray'], terminal=True)


def _chevron_left(p, c):
    _path(p, [(11.5, 4.5), (6.5, 9), (11.5, 13.5)], c['gray'], 1.1)


def _chevron_right(p, c):
    _path(p, [(6.5, 4.5), (11.5, 9), (6.5, 13.5)], c['gray'], 1.1)


def _chevron_up(p, c):
    _path(p, [(4.5, 11.5), (9, 6.5), (13.5, 11.5)], c['gray'], 1.1)


def _chevron_down(p, c):
    _path(p, [(4.5, 6.5), (9, 11.5), (13.5, 6.5)], c['gray'], 1.1)


def _navigation(p, c):
    _box(p, 2.2, 3, 13.6, 12, c['gray'])
    _line(p, (6.2, 3), (6.2, 15), c['blue'])
    for y in (6, 9, 12):
        _line(p, (3.6, y), (4.8, y), c['blue'], .9)
    _path(p, [(10.2, 6.4), (12.8, 9), (10.2, 11.6)], c['blue'])


def _info(p, c):
    _ellipse(p, 2.5, 2.5, 13, 13, c['blue'], 1.1)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c['blue'])
    p.drawEllipse(QRectF(8.35, 5.1, 1.3, 1.3))
    _path(p, [(7.8, 8), (9, 8), (9, 12.8)], c['blue'], 1.1)
    _line(p, (7.7, 12.8), (10.3, 12.8), c['blue'], 1.1)


def _run(p, c):
    _path(p, [(5.1, 2.8), (15.1, 9), (5.1, 15.2)], c['blue'], 1.1, closed=True)


def _stop(p, c):
    _box(p, 4.2, 4.2, 9.6, 9.6, c['red'], 1.15)


def _profile(p, c):
    _paper(p, c['gray'], x=3.2, y=1.5, w=10.9, h=14.9)
    _grid_lines(p, c['blue'], 6.6, 8.4, 9.2, 6.7, rows=2, columns=3, width=.95)


def _profile_pane(p, c):
    _box(p, 1.1, 2, 15.8, 14, c['blue'], 1.05)
    _line(p, (5.3, 2), (5.3, 16), c['blue'], 1.05)


def _profile_add(p, c):
    _ellipse(p, 2.1, 2.1, 13.8, 13.8, c['gray'], 1.05)
    _line(p, (5.5, 9), (12.5, 9), c['gray'], 1.05)
    _line(p, (9, 5.5), (9, 12.5), c['gray'], 1.05)


def _cell_editor(p, c):
    _box(p, 2, 5.3, 10.4, 10.4, c['gray'], 1)
    _path(p, [(6, 12), (7.2, 8.7), (13.5, 2.4), (15.8, 4.7),
              (9.5, 11)], c['blue'], 1.1, closed=True)
    _line(p, (12, 3.9), (14.3, 6.2), c['blue'])


def _history(p, c):
    _curve(p, (4, 5), [(7, .4, 15.6, 3.1, 15.5, 9),
                       (15.5, 14.1, 9.5, 17.5, 5.2, 13.9),
                       (3.6, 12.6, 2.9, 10.8, 3, 9)], c['blue'], 1.1)
    _path(p, [(4, 1.8), (4, 5), (7.2, 5)], c['blue'], 1.1)
    _line(p, (9.2, 5.4), (9.2, 9.3), c['blue'], 1.05)
    _line(p, (9.2, 9.3), (12, 10.6), c['blue'], 1.05)


def _ai(p, c):
    _path(p, [(9, 1.7), (11, 6.8), (16.2, 9), (11, 11),
              (9, 16.2), (6.9, 11), (1.7, 9), (6.9, 6.8)],
          c['blue'], 1.1, closed=True)


def _analysis(p, c):
    for x, y, h in ((2.1, 9, 6.4), (5.9, 4.4, 11), (9.7, 6.5, 8.9)):
        _box(p, x, y, 2, h, c['blue'], 1)
    _ellipse(p, 9.3, 2, 5.9, 5.9, c['gray'], 1.05)
    _line(p, (14.3, 7.1), (16.6, 9.4), c['gray'], 1.15)


def _micro_character(p, character, x, y, color):
    if character == '1':
        _path(p, [(x, y + .6), (x + .7, y), (x + .7, y + 2.3)], color, .55)
    elif character == '0':
        _box(p, x, y, 1.5, 2.3, color, .55, .5)
    elif character == 'A':
        _path(p, [(x, y + 2.3), (x + .75, y), (x + 1.5, y + 2.3)], color, .55)
        _line(p, (x + .3, y + 1.4), (x + 1.2, y + 1.4), color, .55)
    elif character == 'B':
        _path(p, [(x, y + 2.3), (x, y), (x + 1.1, y), (x + 1.5, y + .5),
                  (x + 1, y + 1.1), (x, y + 1.1), (x + 1.1, y + 1.1),
                  (x + 1.5, y + 1.7), (x + 1.1, y + 2.3), (x, y + 2.3)], color, .55)
    else:
        _path(p, [(x + 1.5, y), (x + .3, y), (x, y + .5), (x, y + 1.8),
                  (x + .3, y + 2.3), (x + 1.5, y + 2.3)], color, .55)


def _generate(p, c):
    _cylinder(p, c['gray'], x=1.5, y=2.1, w=9.6, h=13.5)
    _box(p, 7.9, 7.1, 8.6, 8.4, c['blue'], .9, .5)
    for y, text in ((8.2, '101'), (11.7, 'ABC')):
        for index, character in enumerate(text):
            _micro_character(p, character, 8.9 + index*2.2, y, c['blue'])


def _mask(p, c):
    _path(p, [(2, 3.5), (6.9, 1.8), (11.8, 3.5), (11.8, 8.3),
              (10.5, 11.5), (6.9, 14.7), (3.3, 11.5), (2, 8.3)], c['blue'], closed=True)
    _path(p, [(6.4, 5), (11.3, 3.3), (16.2, 5), (16.2, 9.8),
              (14.9, 13), (11.3, 16.2), (7.7, 13), (6.4, 9.8)], c['blue'], closed=True)


def _focus(p, c):
    for points in ([(6.8, 3), (3, 3), (3, 6.8)], [(11.2, 3), (15, 3), (15, 6.8)],
                   [(3, 11.2), (3, 15), (6.8, 15)], [(11.2, 15), (15, 15), (15, 11.2)]):
        _path(p, points, c['blue'], 1.15)


def _settings(p, c):
    points = []
    for index in range(32):
        angle = index*math.pi/16 - math.pi/2
        radius = 7.1 if index % 4 in (1, 2) else 5.7
        points.append((9 + math.cos(angle)*radius, 9 + math.sin(angle)*radius))
    _path(p, points, c['gray'], 1, closed=True)
    _ellipse(p, 6.3, 6.3, 5.4, 5.4, c['gray'], 1)


def _zoom(p, c):
    _ellipse(p, 2.3, 2.3, 9.5, 9.5, c['gray'], 1.1)
    _line(p, (10.6, 10.6), (15.8, 15.8), c['gray'], 1.3)
    _line(p, (4.7, 7), (9.3, 7), c['gray'], 1)
    _line(p, (7, 4.7), (7, 9.3), c['gray'], 1)


def _zoom_out(p, c):
    _ellipse(p, 2.3, 2.3, 9.5, 9.5, c['gray'], 1.1)
    _line(p, (10.6, 10.6), (15.8, 15.8), c['gray'], 1.3)
    _line(p, (4.7, 7), (9.3, 7), c['gray'], 1)


def _grid(p, c):
    _grid_lines(p, c['blue'], 2.3, 2.3, 13.4, 13.4, width=1.05)


def _form(p, c):
    _box(p, 2.4, 2.5, 13.2, 13, c['gray'])
    for y in (4.8, 8.4, 12):
        _line(p, (4.2, y + .9), (5.7, y + .9), c['gray'], .9)
        _box(p, 7.3, y, 6.2, 1.9, c['gray'], .8)


def _key(p, c):
    _ellipse(p, 2.3, 2.3, 6.3, 6.3, c['yellow'], 1.1)
    _path(p, [(7.6, 7.6), (15.4, 15.4), (16, 13), (13.9, 13.2),
              (14.1, 11.2), (12.1, 11.3)], c['yellow'], 1.15)


def _list(p, c):
    for y in (4, 8.5, 13):
        _box(p, 2.1, y, 1.5, 1.5, c['blue'], .9)
        _line(p, (6, y + .75), (15.9, y + .75), c['blue'], 1.1)


def _er(p, c):
    _grid_lines(p, c['blue'], 1.6, 2.2, 5.3, 5, rows=2, columns=1, width=.9)
    _grid_lines(p, c['blue'], 11, 2.2, 5.3, 5, rows=2, columns=1, width=.9)
    _grid_lines(p, c['blue'], 6.3, 11.2, 5.3, 5, rows=2, columns=1, width=.9)
    _path(p, [(4.3, 7.4), (4.3, 9.1), (13.6, 9.1), (13.6, 7.4)], c['gray'], .9)
    _line(p, (9, 9.1), (9, 11), c['gray'], .9)


def _automation(p, c):
    _paper(p, c['gray'], x=3, y=1.8, w=9.4, h=13.9)
    _ellipse(p, 7.4, 7.4, 8.8, 8.8, c['blue'], 1)
    _path(p, [(11.8, 9.2), (11.8, 11.8), (14, 12.9)], c['blue'], 1)


def _index(p, c):
    _line(p, (4.2, 3.5), (4.2, 14.5), c['gray'])
    for y, end in ((3.5, 14.5), (9, 12.2), (14.5, 15.7)):
        _line(p, (4.2, y), (7.2, y), c['gray'])
        _box(p, 7.2, y - 1.5, end - 7.2, 3, c['blue'], .95)


def _user(p, c):
    _ellipse(p, 6, 2.1, 6, 6, c['blue'], 1.1)
    _curve(p, (2.8, 15.7), [(2.8, 7.1, 15.2, 7.1, 15.2, 15.7)], c['blue'], 1.1)
    _line(p, (2.8, 15.7), (15.2, 15.7), c['blue'], 1.1)


def _database(p, c):
    _cylinder(p, c['gray'], x=3.5, y=1.9, w=11, h=14.1)
    _line(p, (6.2, 8.3), (11.8, 8.3), c['blue'], 1.1)


_DRAWERS = {
    'open': _open, 'design': _design, 'add': _add, 'delete': _delete,
    'import': _import, 'export': _export, 'refresh': _refresh, 'save': _save,
    'test_success': _test_success, 'copy': _copy, 'dismiss': _record_cancel,
    'format': _format, 'snippets': _snippets, 'code_braces': _code_braces,
    'filter': _filter, 'sort': _sort,
    'columns': _columns, 'chart': _chart, 'builder': _builder, 'query_builder': _builder,
    'explain': _explain, 'model': _er,
    'result_below': _result_below, 'result_beside': _result_beside,
    'transaction_begin': _transaction_begin, 'transaction_commit': _transaction_commit,
    'transaction_rollback': _transaction_rollback,
    'record_add': _record_add, 'record_delete': _record_delete,
    'record_apply': _record_apply, 'record_cancel': _record_cancel,
    'record_refresh': _refresh, 'record_stop': _record_stop,
    'first': _first, 'previous': _previous, 'next': _next, 'last': _last,
    'chevron_left': _chevron_left, 'chevron_right': _chevron_right,
    'chevron_up': _chevron_up, 'chevron_down': _chevron_down,
    'navigation': _navigation, 'info': _info, 'run': _run, 'stop': _stop,
    'profile': _profile, 'table_profile': _profile, 'cell_editor': _cell_editor,
    'profile_pane': _profile_pane, 'profile_add': _profile_add,
    'filter_sort': _filter_sort, 'history': _history, 'ai': _ai, 'new_ai': _ai,
    'analysis': _analysis, 'generate': _generate, 'mask': _mask, 'focus': _focus,
    'settings': _settings, 'zoom': _zoom, 'zoom_in': _zoom, 'zoom_out': _zoom_out,
    'grid': _grid, 'form': _form, 'key': _key, 'list': _list, 'er': _er,
    'automation': _automation, 'index': _index, 'user': _user, 'database': _database,
    'edit': _cell_editor, 'record_check': _record_apply, 'record_cross': _record_cancel,
}
ACTION_NAMES = frozenset(_DRAWERS)


def draw_action(painter: QPainter, name: str, *, palette: Mapping | None = None) -> bool:
    """Paint one 18x18 logical pictogram and preserve the caller's paint state.

    ``palette`` may override blue/gray/light/green/yellow/red/white with QColor or Qt
    color strings. The caller chooses the mode/theme colors. An unknown name
    returns False without changing the painter or substituting unrelated art.
    """
    if not isinstance(name, str) or name not in _DRAWERS:
        return False
    colors = {key: QColor(value) for key, value in _DEFAULT_COLORS.items()}
    if palette is not None:
        for key in colors.keys() & palette.keys():
            color = QColor(palette[key])
            if not color.isValid():
                raise ValueError('Action palette contains an invalid color')
            colors[key] = color
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _DRAWERS[name](painter, colors)
    finally:
        painter.restore()
    return True
