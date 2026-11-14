"""Connection-result pictograms at real control sizes; no reference assets or DB."""
from hashlib import sha256

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette

from opennavicat.ui import icons
from opennavicat.ui.action_artwork import draw_action


NAMES = ("test_success", "copy", "dismiss")


@pytest.fixture(autouse=True)
def restore_palette(qapp):
    original = qapp.palette()
    yield
    qapp.setPalette(original)
    icons._RASTERS.clear()


def image(name, dpr, *, mode=QIcon.Mode.Normal, state=QIcon.State.Off):
    pm = icons.tool_icon(name, 16).pixmap(QSize(16, 16), dpr, mode, state)
    assert pm.size() == QSize(16 * dpr, 16 * dpr)
    assert pm.devicePixelRatioF() == dpr
    return pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def digest(result):
    return sha256(bytes(result.constBits())).hexdigest()


def colors(result):
    return [result.pixelColor(x, y)
            for y in range(result.height()) for x in range(result.width())]


def region(result, left, top, right, bottom):
    """Select a fraction of the requested 16px icon, independent of density."""
    return [result.pixelColor(x, y)
            for y in range(round(result.height() * top), round(result.height() * bottom))
            for x in range(round(result.width() * left), round(result.width() * right))]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_result_roles_are_painted_without_boundary_clipping(qapp, name, dpr):
    result = image(name, dpr)
    assert name in icons.TOOL_NAMES
    # A subpixel antialiased diagonal need not contain a fully opaque pixel.
    assert any(c.alpha() > 150 for c in colors(result))
    edges = ([result.pixelColor(x, y).alpha() for x in (0, result.width() - 1)
              for y in range(result.height())]
             + [result.pixelColor(x, y).alpha() for y in (0, result.height() - 1)
                for x in range(result.width())])
    assert max(edges) == 0
    # Hover/selection/check state must not style-tint semantic status colors.
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
        for state in (QIcon.State.Off, QIcon.State.On):
            assert digest(image(name, dpr, mode=mode, state=state)) == digest(result)


@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_success_is_a_filled_green_disc_with_a_white_check(qapp, dpr):
    result = image("test_success", dpr)
    opaque = [c for c in colors(result) if c.alpha() > 200]
    green = [c for c in opaque if c.green() - c.red() > 50 and c.green() - c.blue() > 50]
    white = [c for c in opaque if min(c.red(), c.green(), c.blue()) > 230]
    assert len(green) > 45 * dpr * dpr  # Filled body, not a thin circle or bare tick.
    assert len(white) >= 2 * dpr * dpr
    assert any(c.alpha() == 255 and c.name() == '#70d35d' for c in green)
    points = [(x, y) for y in range(result.height()) for x in range(result.width())
              if result.pixelColor(x, y).alpha() > 200]
    assert max(x for x, _ in points) - min(x for x, _ in points) + 1 >= 12 * dpr
    assert max(y for _, y in points) - min(y for _, y in points) + 1 >= 12 * dpr
    assert all(c.alpha() > 200 for c in region(result, .40, .24, .60, .38))
    assert all(c.alpha() == 0 for c in region(result, 0, 0, .15, .15))
    # Both strokes must remain visible at the actual 16px control size.
    assert any(min(c.red(), c.green(), c.blue()) > 230
               for c in region(result, .28, .47, .46, .64))
    assert any(min(c.red(), c.green(), c.blue()) > 230
               for c in region(result, .50, .32, .75, .58))
    assert digest(result) != digest(image("record_apply", dpr))


@pytest.mark.parametrize('override', ['#245acf', QColor('#245acf')], ids=['string', 'QColor'])
def test_success_palette_is_separate_from_existing_green_action_artwork(qapp, override):
    # Connection status has its own observed color; ordinary green add/check
    # actions must retain their existing palette and may still be overridden.
    assert any(c.alpha() == 255 and c.name() == '#6eb950' for c in colors(image('add', 3)))
    result = QImage(54, 54, QImage.Format.Format_RGBA8888)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    try:
        painter.scale(3, 3)
        assert draw_action(painter, 'test_success', palette={
            'green': '#ff00ff', 'success_green': override,
        })
    finally:
        painter.end()
    assert any(c.alpha() == 255 and c.name() == '#245acf' for c in colors(result))
    assert not any(c.alpha() == 255 and c.name() == '#ff00ff' for c in colors(result))


@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_copy_has_two_blue_paper_outlines_and_transparent_interiors(qapp, dpr):
    result = image("copy", dpr)
    opaque = [c for c in colors(result) if c.alpha() > 150]
    assert opaque and all(c.blue() - c.red() > 100 for c in opaque)
    assert any(c.alpha() > 150 for c in region(result, .12, .10, .68, .22))  # Rear top.
    assert any(c.alpha() > 150 for c in region(result, .74, .30, .88, .90))  # Front right.
    assert any(c.alpha() > 150 for c in region(result, .32, .80, .88, .93))  # Front bottom.
    assert all(c.alpha() == 0 for c in region(result, .46, .42, .70, .70))
    assert all(c.alpha() == 0 for c in region(result, .22, .20, .28, .24))
    assert digest(result) != digest(image("save", dpr))


@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_dismiss_is_a_thin_gray_cross_without_a_circle_or_box(qapp, dpr):
    result = image("dismiss", dpr)
    ink = [c for c in colors(result) if c.alpha() > 100]
    assert ink and all(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 15
                       for c in ink)
    assert len(ink) < 48 * dpr * dpr  # Not a solid X/block.
    for left, top in ((.23, .23), (.60, .23), (.23, .60), (.60, .60)):
        assert any(c.alpha() > 100 for c in region(result, left, top, left + .17, top + .17))
    for left, top, right, bottom in ((.43, .10, .57, .24), (.10, .43, .24, .57),
                                     (.76, .43, .90, .57), (.43, .76, .57, .90)):
        assert all(c.alpha() == 0 for c in region(result, left, top, right, bottom))
    assert digest(result) != digest(image("delete", dpr))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("background", ["#ffffff", "#222222"])
def test_disabled_modes_dim_for_each_theme_without_changing_alpha_or_identity(qapp, name, background):
    qapp.setPalette(QPalette(QColor(background)))
    normal = image(name, 2)
    disabled = image(name, 2, mode=QIcon.Mode.Disabled)
    assert digest(disabled) != digest(normal)
    assert [c.alpha() for c in colors(disabled)] == [c.alpha() for c in colors(normal)]
    assert digest(disabled) == digest(image(name, 2, mode=QIcon.Mode.Disabled, state=QIcon.State.On))
    # A pale enabled icon or a recycled circle-plus must not replace this role.
    assert all(digest(normal) != digest(image(other, 2))
               for other in ("add", "delete", "profile_add", "record_add", "record_delete", "refresh"))
