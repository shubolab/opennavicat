"""Real Qt icon rendering contracts; these do not establish reference parity."""
from __future__ import annotations

import gc
import os
import subprocess
import sys
import weakref
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QToolButton

from opennavicat.ui import icons


def _image(icon, size=28, dpr=2, mode=QIcon.Mode.Normal, state=QIcon.State.Off):
    return icon.pixmap(QSize(size, size), dpr, mode, state).toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _digest(image):
    return sha256(bytes(image.constBits())).hexdigest()


def _colors(image):
    return [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 200]


@pytest.fixture(autouse=True)
def _reset_palette(qapp):
    original = qapp.palette()
    icons._RASTERS.clear()
    yield
    qapp.setPalette(original)
    icons._RASTERS.clear()


@pytest.mark.parametrize("name", icons.NAMES)
def test_original_names_have_painted_pixels(qapp, name):
    result = icons.icon(name)
    assert not result.isNull()
    assert any(c.alpha() for c in _colors(_image(result)))


@pytest.mark.parametrize("factory,name", [(icons.icon, "table"), (icons.tool_icon, "save"),
                                          (icons.toggle_icon, "info")])
@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_requested_context_is_vector_rendered_at_real_device_size(qapp, factory, name, dpr):
    # Deliberately request 28 from an 18px nominal context: no undersized image
    # may be relabeled with a fractional DPR to pretend it has 28px resolution.
    result = factory(name, 18)
    pm = result.pixmap(QSize(28, 28), dpr)
    assert pm.size() == QSize(28 * dpr, 28 * dpr)
    assert pm.devicePixelRatioF() == dpr
    assert pm.deviceIndependentSize().width() == 28
    direct = factory(name, 28).pixmap(QSize(28, 28), dpr)
    assert _digest(pm.toImage()) == _digest(direct.toImage())


@pytest.mark.parametrize("factory,name", [(icons.icon, "table"), (icons.tool_icon, "save"),
                                          (icons.connection_icon, "oracle")])
def test_modes_and_ordinary_check_state_cannot_receive_style_tint(qapp, factory, name):
    result = factory(name)
    normal = _digest(_image(result))
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
        for state in (QIcon.State.Off, QIcon.State.On):
            assert _digest(_image(result, mode=mode, state=state)) == normal
    disabled = _image(result, mode=QIcon.Mode.Disabled)
    assert _digest(disabled) != normal
    if any(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 40
           for c in _colors(_image(result))):
        assert any(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 20
                   for c in _colors(disabled))  # explicit dim, not Qt grayscale
    assert _digest(disabled) == _digest(_image(result, mode=QIcon.Mode.Disabled, state=QIcon.State.On))


@pytest.mark.parametrize("name", ["info", "ai", "result_below", "result_beside", "grid", "list", "focus"])
def test_toggle_color_is_separate_from_mode_and_connection_state(qapp, name):
    result = icons.toggle_icon(name)
    off, on = _image(result), _image(result, state=QIcon.State.On)
    assert _digest(off) != _digest(on)
    assert all(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 15
               for c in _colors(off))
    assert any(c.blue() - c.red() > 100 for c in _colors(on))
    assert _digest(on) == _digest(_image(result, mode=QIcon.Mode.Selected, state=QIcon.State.On))
    assert _digest(off) != _digest(_image(result, mode=QIcon.Mode.Disabled))


def test_connection_off_badge_does_not_disable_action(qapp):
    action = QAction()
    action.setIcon(icons.connection_icon("mysql", connected=False))
    closed = _image(action.icon())
    assert action.isEnabled()
    assert all(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 15
               for c in _colors(closed))
    action.setIcon(icons.connection_icon("mysql", connected=True))
    opened = _image(action.icon())
    assert action.isEnabled()
    assert _digest(opened) != _digest(closed)
    assert any(c.green() - c.blue() > 50 for c in _colors(opened))
    assert _digest(_image(action.icon(), state=QIcon.State.On)) == _digest(opened)
    action.setEnabled(False)
    assert _digest(_image(action.icon(), mode=QIcon.Mode.Disabled)) != _digest(closed)


def test_same_action_retains_scalable_engine_and_updates_after_palette_change(qapp):
    action = QAction()
    action.setIcon(icons.tool_icon("save"))
    qapp.setPalette(QPalette(QColor("#ffffff")))
    light = _image(action.icon(), mode=QIcon.Mode.Disabled)
    normal_light = _image(action.icon())
    qapp.setPalette(QPalette(QColor("#222222")))
    qapp.processEvents()
    dark = _image(action.icon(), mode=QIcon.Mode.Disabled)
    assert _digest(dark) != _digest(light)
    assert _digest(_image(action.icon())) == _digest(normal_light)
    # No replacing action.icon(): existing implicitly copied engine reads theme.
    qapp.setPalette(QPalette(QColor("#ffffff")))
    assert _digest(_image(action.icon(), mode=QIcon.Mode.Disabled)) == _digest(light)


def test_existing_action_toolbutton_repaints_theme_without_replacing_icon(qapp, qtbot):
    action = QAction()
    action.setIcon(icons.tool_icon("save"))
    action.setEnabled(False)
    button = QToolButton()
    qtbot.addWidget(button)
    button.setDefaultAction(action)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIconSize(QSize(28, 28))
    button.setFixedSize(40, 40)
    button.setStyleSheet("QToolButton { background: #ffffff; border: 0; padding: 6px; }")
    button.ensurePolished()
    key = action.icon().cacheKey()
    renders = []
    for background in ("#ffffff", "#222222"):
        palette = qapp.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(background))
        qapp.setPalette(palette)
        qapp.processEvents()
        surface = QPixmap(40, 40)
        surface.fill(Qt.GlobalColor.transparent)
        button.render(surface)
        renders.append(surface.toImage())
    assert action.icon().cacheKey() == key  # no replacing the QAction's QIcon
    assert _digest(renders[0]) != _digest(renders[1])
    assert renders[0].pixelColor(0, 0) == renders[1].pixelColor(0, 0)


@pytest.mark.parametrize("destroy_original_first", [False, True])
def test_icon_copy_detach_clone_and_gc_keep_engine_owned(qapp, qtbot, destroy_original_first):
    original = icons.icon("table")
    copied = QIcon(original)
    expected = _digest(_image(original))
    original.addPixmap(QPixmap(9, 9))  # Qt detaches and calls the Python clone.
    refs = [weakref.ref(engine) for engine in icons._LIVE_CLONES.values()]
    if destroy_original_first:
        del original
        gc.collect()
        assert _digest(_image(copied)) == expected
    else:
        del copied
        gc.collect()
        assert _digest(_image(original)) == expected
        copied = QIcon(original)
        del original
    action = QAction()
    action.setIcon(copied)
    del copied
    gc.collect()
    assert _digest(_image(action.icon())) == expected
    # Retaining a QAction is independent of raster eviction.
    icons._RASTERS.clear()
    assert _digest(_image(action.icon())) == expected
    del action
    gc.collect()
    qtbot.waitUntil(lambda: (gc.collect(), all(ref() is None for ref in refs))[1], timeout=2000)
    assert not icons._LIVE_CLONES


@pytest.mark.parametrize("destroy_before_exit", [False, True])
def test_clone_process_teardown_exits_without_dangling_native_engine(qapp, destroy_before_exit):
    source = '''
import gc
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction,QIcon,QPixmap
from PySide6.QtCore import QSize
from opennavicat.ui.icons import icon
app=QApplication([])
original=icon('table');copy=QIcon(original)
original.addPixmap(QPixmap(9,9))
action=QAction();action.setIcon(original)
assert not action.icon().pixmap(QSize(28,28),2).isNull()
del original,copy;gc.collect()
if DESTROY:
 del action;gc.collect();app.processEvents()
'''.replace("DESTROY", repr(destroy_before_exit))
    child = subprocess.run([sys.executable, "-c", source], env=os.environ.copy(),
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr


def test_icon_paint_uses_requested_device_density_and_restores_painter(qapp):
    result = icons.tool_icon("save", 18)
    target = QPixmap(112, 112)
    target.setDevicePixelRatio(2)
    target.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target)
    painter.translate(2, 3)
    before = painter.transform()
    result.paint(painter, QRect(0, 0, 28, 28), Qt.AlignmentFlag.AlignCenter)
    assert painter.transform() == before
    painter.end()
    assert any(c.alpha() for c in _colors(target.toImage()))


@pytest.mark.parametrize("size", [16, 28, 256])
@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_scaled_raster_retains_interior_coverage_without_opaque_edge_clip(qapp, size, dpr):
    for result in (icons.icon("table", size), icons.tool_icon("save", size)):
        image = _image(result, size=size, dpr=dpr)
        ink = [(x, y) for x in range(image.width()) for y in range(image.height())
               if image.pixelColor(x, y).alpha()]
        assert ink
        # A 1-unit vector inset can legitimately antialias into the edge pixel
        # at DPR1. It must not crop an opaque body at the image boundary.
        edge = ([image.pixelColor(x, y).alpha() for x in (0, image.width() - 1)
                 for y in range(image.height())]
                + [image.pixelColor(x, y).alpha() for y in (0, image.height() - 1)
                   for x in range(image.width())])
        assert max(edge) < 128
        assert any(image.pixelColor(x, y).alpha() > 200 for x, y in ink)


def test_known_semantics_never_fall_back_to_other(qapp):
    images = [_digest(_image(icons.tool_icon(name))) for name in ("edit", "history", "key", "other")]
    assert len(set(images)) == 4
    assert _digest(_image(icons.tool_icon("schema"))) == _digest(_image(icons.tool_icon("database")))
    with pytest.raises(ValueError, match="Unknown"):
        icons.tool_icon("invented_unknown")
    with pytest.raises(ValueError, match="Unknown"):
        icons.icon("invented_unknown")


def test_provider_known_unobserved_is_null_and_never_database(qapp):
    assert len(icons.PROVIDER_NAMES) == 14
    for name in icons.PROVIDER_NAMES:
        result = icons.provider_icon(name)
        assert result.isNull() == (name in icons.UNOBSERVED_PROVIDER_NAMES)
    assert icons.provider_icon("Redis Enterprise Cloud").isNull()
    assert not icons.provider_icon("Oracle").isNull()
    with pytest.raises(ValueError, match="Unknown"):
        icons.provider_icon("invented_provider")


def test_schema_open_state_is_independent_of_action_mode(qapp):
    closed = icons.database_icon(opened=False)
    opened = icons.database_icon(opened=True)
    assert _digest(_image(closed)) != _digest(_image(opened))
    assert all(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 32
               for c in _colors(_image(closed)))
    assert any(c.green() - c.red() > 30 for c in _colors(_image(opened)))
    assert _digest(_image(opened)) == _digest(_image(opened, state=QIcon.State.On))
    assert _digest(_image(closed)) != _digest(_image(opened, mode=QIcon.Mode.Disabled))
    with pytest.raises(ValueError, match="state"):
        icons.database_icon(opened=1)


@pytest.mark.parametrize("provider", ["alibaba", "aws", "mongodb_atlas", "oracle_cloud"])
def test_provider_filter_badge_and_driver_tile_are_separate_cached_artwork(qapp, provider):
    badge = icons.provider_icon(provider, 50)
    mysql = icons.provider_icon(provider, 50, driver="mysql")
    oracle = icons.provider_icon(provider, 50, driver="oracle")
    pixels = [_digest(_image(i, size=50)) for i in (badge, mysql, oracle)]
    assert len(set(pixels)) == 3
    assert any(c.green() - c.blue() > 40 for c in _colors(_image(mysql, size=50)))
    assert any(c.red() - c.blue() > 70 for c in _colors(_image(oracle, size=50)))
    assert _digest(_image(mysql, size=50)) == pixels[1]  # cached variant not poisoned
    with pytest.raises(ValueError, match="driver"):
        icons.provider_icon(provider, driver="future_driver")


@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_busy_frames_have_real_density_and_periodic_phase(qapp, dpr):
    pm = icons.busy_icon(0).pixmap(QSize(16, 16), dpr)
    assert pm.size() == QSize(16 * dpr, 16 * dpr)
    assert pm.devicePixelRatioF() == dpr
    assert _digest(_image(icons.busy_icon(0))) == _digest(_image(icons.busy_icon(360)))
    assert _digest(_image(icons.busy_icon(0))) != _digest(_image(icons.busy_icon(90)))


def test_cache_caps_memory_and_entries_and_returns_unpoisoned_copies(qapp):
    for phase in range(300):
        icons.busy_icon(phase).pixmap(QSize(16, 16), 3)
    assert len(icons._RASTERS.items) <= icons._RASTERS.max_entries
    assert icons._RASTERS.bytes <= icons._RASTERS.max_bytes
    for phase in range(25):
        pm = icons.busy_icon(phase, 256).pixmap(QSize(256, 256), 3)
        assert pm.size() == QSize(768, 768)
    assert icons._RASTERS.bytes <= icons._RASTERS.max_bytes
    result = icons.tool_icon("save")
    pm = result.pixmap(QSize(28, 28), 2)
    expected = _digest(pm.toImage())
    pm.fill(Qt.GlobalColor.red)
    assert _digest(result.pixmap(QSize(28, 28), 2).toImage()) == expected


def test_render_and_factory_reject_worker_even_with_cached_pixels(qapp):
    spec = icons._Spec("action", "save")
    icons._render(spec, QSize(18, 18), 1, QIcon.Mode.Normal, QIcon.State.Off)
    with ThreadPoolExecutor(max_workers=1) as worker:
        with pytest.raises(RuntimeError, match="GUI thread"):
            worker.submit(icons.tool_icon, "save").result()
        with pytest.raises(RuntimeError, match="GUI thread"):
            worker.submit(icons._render, spec, QSize(18, 18), 1,
                          QIcon.Mode.Normal, QIcon.State.Off).result()


@pytest.mark.parametrize("size", [True, False, 7, 257, 18.0, "18"])
def test_public_size_validation(qapp, size):
    with pytest.raises(ValueError, match="size"):
        icons.icon("table", size)


@pytest.mark.parametrize("phase", [True, None, float("inf"), float("nan")])
def test_busy_phase_validation(qapp, phase):
    with pytest.raises(ValueError, match="phase"):
        icons.busy_icon(phase)
