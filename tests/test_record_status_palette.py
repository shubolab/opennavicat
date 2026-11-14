"""Palette-only preservation of independently drawn connection status artwork."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from opennavicat.ui import action_artwork, icons


@pytest.fixture
def recorder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "record-demo.py"
    spec = importlib.util.spec_from_file_location("record_status_palette_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def palette_without_status(recorder, path, *, padded=False):
    """A bounded palette with all nine drivers, but no exact success green."""
    palette = QImage(16, 16, QImage.Format.Format_RGBA8888)
    for index in range(256):
        color = QColor("black") if padded else QColor(
            220 + index % 36, index * 17 % 256, index * 29 % 256)
        palette.setPixelColor(index % 16, index // 16, color)
    for index, color in enumerate(("#000000", "#ffffff", "#435241", "#7cd95f")):
        palette.setPixelColor(index % 16, index // 16, QColor(color))
    for index, color in enumerate(recorder.DEMO_DRIVER_COLORS.values(), 10):
        palette.setPixelColor(index % 16, index // 16, QColor(color))
    palette.setPixelColor(15, 15, QColor(0, 255, 0, 0))
    assert palette.save(str(path), "PNG")
    return [palette.pixelColor(index % 16, index // 16) for index in range(256)]


def read_palette(path):
    palette = QImage(str(path))
    assert not palette.isNull() and palette.size() == QSize(16, 16)
    return [palette.pixelColor(index % 16, index // 16) for index in range(256)]


def test_status_reservation_matches_owned_artwork_without_reclassifying_drivers(recorder):
    assert len(recorder.DEMO_DRIVER_COLORS) == 9
    assert recorder.DEMO_STATUS_COLORS == {
        "test_success": action_artwork._DEFAULT_COLORS["success_green"],
    } == {"test_success": "#70d35d"}
    assert not set(recorder.DEMO_STATUS_COLORS) & set(recorder.DEMO_DRIVER_COLORS)


@pytest.mark.parametrize("padded", [False, True], ids=["full-palette", "padded-palette"])
def test_worker_reserves_ten_colors_without_changing_palette_bounds_or_protected_slots(
        qapp, recorder, tmp_path, padded):
    path = tmp_path / "palette.png"
    original = palette_without_status(recorder, path, padded=padded)
    status = QColor("#70d35d")
    assert status not in original
    caller = threading.get_ident()

    def reserve():
        assert threading.get_ident() != caller
        return recorder.reserve_demo_driver_colors(path)

    with ThreadPoolExecutor(max_workers=1) as worker:
        facts = worker.submit(reserve).result(timeout=10)
    after = read_palette(path)
    assert len(after) == 256
    assert [index for index, color in enumerate(after) if color.alpha() == 0] == [255]
    assert all(color.alpha() == 255 for color in after[:255])
    assert after[255] == original[255]
    assert after[:2] == original[:2]  # Keep black and white.
    assert after[10:19] == original[10:19]  # All existing exact drivers.
    assert all(QColor(color) in after for color in recorder.DEMO_DRIVER_COLORS.values())
    assert status in after
    changed = [index for index in range(256) if after[index] != original[index]]
    assert len(changed) == 1 and after[changed[0]] == status
    if padded:
        assert after[3] == original[3]  # Reuse padding before a distinct color.
    else:
        assert changed == [3]  # The closest unprotected distinct entry.
    assert facts["reserved_driver_colors"] == recorder.DEMO_DRIVER_COLORS
    assert len(facts["reserved_driver_colors"]) == 9
    assert facts["reserved_status_colors"] == recorder.DEMO_STATUS_COLORS
    assert len(facts["reserved_status_colors"]) == 1
    assert facts["reserved_color_count"] == 10
    assert facts["transparency_slot_preserved"] is True
    # A second reservation must leave every saved entry unchanged.
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(reserve).result(timeout=10) == facts
    assert read_palette(path) == after


def marker_frame(size, dpr):
    """Paint the actual icon factory result, at its real logical control size."""
    frame = QImage(64 * dpr, 64 * dpr, QImage.Format.Format_RGBA8888)
    frame.setDevicePixelRatio(dpr)
    frame.fill(QColor("#435241"))
    pixmap = icons.tool_icon("test_success", size).pixmap(QSize(size, size), dpr)
    assert pixmap.size() == QSize(size * dpr, size * dpr)
    assert pixmap.devicePixelRatioF() == dpr
    painter = QPainter(frame)
    try:
        painter.drawPixmap(QPoint((64 - size) // 2, (64 - size) // 2), pixmap)
    finally:
        painter.end()
    return frame


def decode_palette_mapping(ffmpeg, frame, palette, output):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(frame), "-i", str(palette), "-lavfi",
        "paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
        "-frames:v", "1", "-gifflags", "+offsetting+transdiff", str(output)],
        capture_output=True, check=True, timeout=30)
    reader = QImageReader(str(output))
    result = reader.read()
    assert not result.isNull(), reader.errorString()
    assert not reader.canRead()  # One real decoded frame.
    return result


@pytest.mark.parametrize("size", [16, 28])
@pytest.mark.parametrize("dpr", [1, 2, 3])
def test_actual_marker_core_survives_palette_mapping_and_gif_decode(
        qapp, recorder, tmp_path, monkeypatch, size, dpr):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for actual palette mapping and GIF decoding")
    source = marker_frame(size, dpr)
    frame = tmp_path / "actual-marker.png"
    assert source.save(str(frame), "PNG")
    frame_bytes = frame.read_bytes()
    expected = QColor("#70d35d")
    core = [(x, y) for y in range(source.height()) for x in range(source.width())
            if source.pixelColor(x, y) == expected]
    white = [(x, y) for y in range(source.height()) for x in range(source.width())
             if source.pixelColor(x, y) == QColor("white")]
    assert len(core) >= 30 * dpr * dpr and len(white) >= dpr * dpr
    old_palette = tmp_path / "nine-driver-only.png"
    original = palette_without_status(recorder, old_palette)
    # With all nine drivers already present, the previous nine-only reservation
    # leaves this saturated palette unchanged and cannot represent the marker.
    with monkeypatch.context() as legacy:
        legacy.setattr(recorder, "DEMO_STATUS_COLORS", {}, raising=False)
        recorder.reserve_demo_driver_colors(old_palette)
    assert read_palette(old_palette) == original and expected not in original
    palette = tmp_path / "with-status.png"
    palette_without_status(recorder, palette)
    recorder.reserve_demo_driver_colors(palette)
    old = decode_palette_mapping(ffmpeg, frame, old_palette, tmp_path / "nine-only.gif")
    new = decode_palette_mapping(ffmpeg, frame, palette, tmp_path / "with-status.gif")
    assert old.size() == new.size() == source.size()
    assert all(old.pixelColor(x, y) != expected for x, y in core)
    assert all(new.pixelColor(x, y) == expected for x, y in core)
    assert all(new.pixelColor(x, y) == QColor("white") for x, y in white)
    assert frame.read_bytes() == frame_bytes  # Only the palette was adjusted.
