"""Actual style content rectangles must fit the inherited editor/button font."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QStyle, QStyleOptionButton, QStyleOptionFrame

from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog
from test_sqlite_connection_databases_font_ui import (
    config, native_font_without_global_stylesheet, parent_dialog, settle,
)


@pytest.fixture(autouse=True)
def verify_native_cocoa_style(qapp, native_font_without_global_stylesheet):
    if qapp.platformName() == "cocoa":
        assert qapp.style().metaObject().className() == "QMacStyle"
        assert qapp.style().objectName() == "macos"


def assert_real_contents_fit(sheet):
    for edit in (sheet.name_edit, sheet.file_edit, sheet.password_edit):
        option = QStyleOptionFrame(); edit.initStyleOption(option)
        content = edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, edit)
        margins = edit.textMargins()
        assert content.height() - margins.top() - margins.bottom() >= edit.fontMetrics().height()
        assert edit.contentsRect().height() - margins.top() - margins.bottom() >= edit.fontMetrics().height()
        assert edit.parentWidget().rect().contains(edit.geometry())
    ok = sheet.buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel = sheet.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    for button in (ok, cancel, sheet.browse_button):
        option = QStyleOptionButton(); button.initStyleOption(option)
        content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert content.height() >= button.fontMetrics().height()
        assert content.width() >= button.fontMetrics().horizontalAdvance(button.text())
        assert button.parentWidget().rect().contains(button.geometry())
    assert ok.mapTo(sheet, QPoint()).x() > cancel.mapTo(sheet, QPoint(cancel.width(), 0)).x()
    assert ok.mapTo(sheet, QPoint(ok.width(), 0)).x() == sheet.width() - 20
    return ok, cancel


@pytest.mark.parametrize("pixels", [12, 20, 36])
def test_native_sheet_editor_and_button_contents_fit_actual_font(qtbot, qapp, config, pixels):
    dialog = parent_dialog(qtbot, qapp, config, pixels)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show(); settle(qapp)
    assert sheet.password_edit.font().pixelSize() == pixels
    ok, cancel = assert_real_contents_fit(sheet)
    # The native form must grow its retained fields, not leave a short editor
    # at the style's width hint while the spanning password row alone grows.
    assert sheet.name_edit.mapTo(sheet, QPoint(sheet.name_edit.width(), 0)).x() == sheet.width() - 20
    assert sheet.browse_button.mapTo(sheet, QPoint(sheet.browse_button.width(), 0)).x() == sheet.width() - 20
    if pixels == 12:
        assert sheet.size().toTuple() == (600, 215)
        assert sheet.password_edit.mapTo(sheet, QPoint()).x() == 179
        assert sheet.password_edit.mapTo(sheet, QPoint(sheet.password_edit.width(), 0)).x() == 580
    if pixels in (12, 20):
        assert ok.width() == cancel.width() == 80
    else:
        assert cancel.width() > 80
    sheet.reject()


def test_native_content_hints_and_actual_heights_restore_after_live_font_changes(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 12)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show(); settle(qapp)
    edits = (sheet.name_edit, sheet.file_edit, sheet.password_edit)
    baseline = [(edit.sizeHint().height(), edit.minimumSizeHint().height(), edit.height()) for edit in edits]
    for pixels in (36, 20, 12):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        assert sheet.password_edit.font().pixelSize() == pixels
        assert_real_contents_fit(sheet)
        assert not sheet.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    assert [(edit.sizeHint().height(), edit.minimumSizeHint().height(), edit.height()) for edit in edits] == baseline
    sheet.reject()


@pytest.mark.parametrize("pixels", [12, 36])
def test_native_line_edit_text_margins_leave_real_space_for_glyphs(qtbot, qapp, config, pixels):
    dialog = parent_dialog(qtbot, qapp, config, pixels)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet)
    for edit in (sheet.name_edit, sheet.file_edit, sheet.password_edit):
        edit.setTextMargins(0, 6, 0, 7)
    sheet.show(); settle(qapp)
    assert_real_contents_fit(sheet)
    sheet.reject()


def test_native_contents_follow_global_qss_then_return_to_parent_font(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 36)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show()
    for pixels in (18, 22):
        qapp.setStyleSheet("QWidget { font-size: " + str(pixels) + "px; }")
        settle(qapp)
        assert sheet.password_edit.font().pixelSize() == pixels
        assert_real_contents_fit(sheet)
    qapp.setStyleSheet(""); settle(qapp)
    assert sheet.password_edit.font().pixelSize() == 36
    assert not sheet.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    assert_real_contents_fit(sheet)
    sheet.reject()
