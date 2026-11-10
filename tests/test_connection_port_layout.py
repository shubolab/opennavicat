"""Real Qt edit-field geometry; this does not claim Windows desktop acceptance."""
from dataclasses import asdict
import json
import math

import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QLineEdit, QStyle, QStyleFactory, QStyleOptionFrame, QStyleOptionSpinBox

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.theme import LIGHT_STYLESHEET, STYLESHEET


NETWORK_DEFAULTS = {
    "postgresql": 5432, "mysql": 3306, "mariadb": 3306,
    "sqlserver": 1433, "redis": 6379, "mongodb": 27017,
}


@pytest.fixture(autouse=True)
def isolate_application_stylesheet(qapp):
    # Other workbench tests apply a global 12px stylesheet. Native-style
    # geometry must exercise the font chosen here, then restore that state.
    previous = qapp.styleSheet()
    qapp.setStyleSheet("")
    try:
        yield
    finally:
        qapp.setStyleSheet(previous)


def apply_style(dialog, name):
    style = QStyleFactory.create(name)
    assert style is not None
    # Widgets do not own an explicitly selected style. Keep it alive until the
    # dialog is destroyed, without changing the application's global style.
    style.setParent(dialog)
    dialog.setStyle(style)
    for spin in (dialog.port_spin, dialog.ssh_port_spin):
        spin.setStyle(style)


def available_port_width(spin):
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    edit = spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox, option,
        QStyle.SubControl.SC_SpinBoxEditField, spin,
    )
    line = spin.findChild(QLineEdit)
    frame = QStyleOptionFrame()
    line.initStyleOption(frame)
    text_area = line.style().subElementRect(QStyle.SubElement.SE_LineEditContents, frame, line)
    margins = line.textMargins()
    return min(edit.width(), text_area.width() - margins.left() - margins.right())


def fits(spin):
    line = spin.findChild(QLineEdit)
    required = math.ceil(QFontMetricsF(line.font()).horizontalAdvance(spin.text()))
    return spin.isVisible() and available_port_width(spin) >= required


def geometry(spin):
    line = spin.findChild(QLineEdit)
    frame = QStyleOptionFrame()
    line.initStyleOption(frame)
    content = line.style().subElementRect(QStyle.SubElement.SE_LineEditContents, frame, line)
    return {
        "style": spin.style().metaObject().className(), "dpr": spin.devicePixelRatioF(),
        "font": spin.font().toString(), "editor_font": line.font().toString(),
        "size": spin.size().toTuple(), "hint": spin.sizeHint().toTuple(),
        "editor": line.geometry().getRect(), "editor_hint": line.minimumSizeHint().toTuple(),
        "editor_content": content.getRect(), "cursor": line.cursorRect().getRect(),
        "text_margins": [line.textMargins().left(), line.textMargins().right()],
        "available": available_port_width(spin),
        "required": QFontMetricsF(line.font()).horizontalAdvance(spin.text()), "text": spin.text(),
    }


def assert_fits(qtbot, spin):
    # The useful contract is complete visible text, not exact equality with a
    # size hint that native layout/DPI rounding can change by a few pixels.
    try:
        qtbot.waitUntil(lambda: fits(spin) and spin.width() <= (
            spin.sizeHint().width() + spin.findChild(QLineEdit).fontMetrics().maxWidth()
        ), timeout=2000)
    except Exception:
        pytest.fail(json.dumps(geometry(spin), ensure_ascii=True))
    line = spin.findChild(QLineEdit)
    # Ask the actual QLineEdit renderer where both ends land. If it scrolls
    # horizontally to reveal a clipped leading digit, their separation shrinks.
    old_cursor = line.cursorPosition()
    line.setCursorPosition(0)
    start = line.cursorRect().left()
    line.setCursorPosition(len(line.text()))
    end = line.cursorRect().left()
    line.setCursorPosition(old_cursor)
    width = QFontMetricsF(line.font()).horizontalAdvance(line.text())
    assert end - start >= math.floor(width), json.dumps(geometry(spin), ensure_ascii=True)
    # Fixed policy must still keep the port separate from full-width host
    # inputs. Allow a character of native/DPI rounding instead of exact pixels.
    assert spin.width() <= spin.sizeHint().width() + line.fontMetrics().maxWidth(), geometry(spin)
    print("PORT_GEOMETRY " + json.dumps(geometry(spin), ensure_ascii=True), flush=True)


@pytest.mark.parametrize("style_name", QStyleFactory.keys())
@pytest.mark.parametrize("font_pixels", [12, 24, 36])
@pytest.mark.parametrize("stylesheet", [None, STYLESHEET, LIGHT_STYLESHEET], ids=["native", "dark", "light"])
def test_all_network_and_ssh_ports_fit_native_edit_area_and_cancel_does_not_save(
    qtbot, monkeypatch, style_name, font_pixels, stylesheet,
):
    monkeypatch.setattr(DatabaseSession, "connect", lambda *_: pytest.fail("Layout must not connect"))
    dialog = ConnectionDialog()
    qtbot.addWidget(dialog)
    apply_style(dialog, style_name)
    font = dialog.font()
    font.setPixelSize(font_pixels)
    dialog.setFont(font)
    if stylesheet is not None:
        dialog.setStyleSheet(stylesheet + f"\nQWidget {{ font-size: {font_pixels}px; }}")
    accepted, rejected = QSignalSpy(dialog.accepted), QSignalSpy(dialog.rejected)
    dialog.show()
    # SQLite has no database port and does not present an SSH connection page.
    assert not dialog.port_spin.isVisible() and dialog.tabs.indexOf(dialog.ssh_tab) == -1
    for driver, default in NETWORK_DEFAULTS.items():
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
        assert dialog.port_spin.value() == default
        dialog.tabs.setCurrentWidget(dialog.general_tab)
        assert_fits(qtbot, dialog.port_spin)
        assert dialog.port_spin.text() == str(default)
        dialog.port_spin.setValue(65535)
        assert_fits(qtbot, dialog.port_spin)
        assert dialog.port_spin.font().pixelSize() == font_pixels
        assert dialog.port_spin.text() == "65535" and dialog.port_spin.value() == 65535
        dialog.ssh_enabled_check.setChecked(True)
        dialog.ssh_port_spin.setValue(65535)
        dialog.tabs.setCurrentWidget(dialog.ssh_tab)
        assert_fits(qtbot, dialog.ssh_port_spin)
        assert dialog.ssh_port_spin.text() == "65535" and dialog.ssh_port_spin.value() == 65535
    dialog.reject()
    assert rejected.count() == 1 and accepted.count() == 0
    assert dialog.get_config() is None and not dialog._test_pool.activeThreadCount()


def test_live_font_and_style_changes_relayout_without_changing_port_values(qtbot):
    config = ConnectionConfig(
        "generated-port-layout", "Generated MongoDB", driver="mongodb", port=27017,
        ssl_mode="disable", ssh_enabled=True, ssh_host="jump.invalid",
        ssh_port=65535, ssh_username="fixture",
    )
    original = asdict(config)
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    database_changes = QSignalSpy(dialog.port_spin.valueChanged)
    ssh_changes = QSignalSpy(dialog.ssh_port_spin.valueChanged)
    dialog.show()
    widths = {}
    for style_name in QStyleFactory.keys():
        apply_style(dialog, style_name)
        for pixels in (12, 36, 18, 12):
            font = dialog.font()
            font.setPixelSize(pixels)
            dialog.setFont(font)
            for name, spin, tab, expected in (
                ("database", dialog.port_spin, dialog.general_tab, 27017),
                ("ssh", dialog.ssh_port_spin, dialog.ssh_tab, 65535),
            ):
                dialog.tabs.setCurrentWidget(tab)
                assert_fits(qtbot, spin)
                assert spin.value() == expected and spin.text() == str(expected)
                widths[style_name, pixels, name] = spin.width()
        for name in ("database", "ssh"):
            assert widths[style_name, 36, name] > widths[style_name, 12, name]
    assert database_changes.count() == ssh_changes.count() == 0
    dialog.reject()
    assert dialog.get_config() is None and asdict(config) == original


def test_actual_editor_font_can_grow_independently_from_spin_font(qtbot):
    dialog = ConnectionDialog(config=ConnectionConfig(
        "generated-editor-font", "Generated", driver="mongodb", port=65535,
        ssh_enabled=True, ssh_host="jump.invalid", ssh_username="fixture", ssh_port=65535,
    ))
    qtbot.addWidget(dialog)
    dialog.show()
    for spin, tab in ((dialog.port_spin, dialog.general_tab), (dialog.ssh_port_spin, dialog.ssh_tab)):
        dialog.tabs.setCurrentWidget(tab)
        line = spin.findChild(QLineEdit)
        initial = spin.width()
        font = line.font()
        font.setPixelSize(42)
        line.setFont(font)
        assert_fits(qtbot, spin)
        assert line.font().pixelSize() == 42 and spin.font().pixelSize() == 12
        assert spin.width() > initial and spin.value() == 65535
        expanded = spin.width()
        font.setPixelSize(12)
        line.setFont(font)
        assert_fits(qtbot, spin)
        assert spin.width() < expanded
    dialog.reject()
    assert dialog.get_config() is None
