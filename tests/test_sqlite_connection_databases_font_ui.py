"""Real Qt font/row/layout ownership, independent of stylesheet-only tests."""
from contextlib import closing
import sqlite3

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QStyle, QStyleOptionButton, QStyleOptionViewItem

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog


@pytest.fixture(autouse=True)
def native_font_without_global_stylesheet(qapp, monkeypatch):
    previous = qapp.styleSheet()
    qapp.setStyleSheet("")
    import keyring
    def forbidden(*_args, **_kwargs):
        pytest.fail("Font-only SQLite UI accessed the keyring")
    for method in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, method, forbidden)
    yield
    qapp.setStyleSheet(previous)


@pytest.fixture
def config(tmp_path):
    aux = tmp_path
    for index in range(5):
        aux /= "generated-owned-segment-" + str(index)
    aux.mkdir(parents=True)
    main, aux = tmp_path / "main.sqlite", aux / "generated-long-path-aux.sqlite"
    before = {}
    for path in (main, aux):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE owned(id INTEGER PRIMARY KEY)")
            connection.executemany("INSERT INTO owned VALUES(?)", [(1,), (2,)])
        before[path] = path.read_bytes()
    yield ConnectionConfig("owned-font-layout", "Owned font layout", database=str(main), sqlite_open_mode="existing",
        sqlite_databases=(SQLiteDatabaseEntry("owned-row", "owned_aux", str(aux)),))
    assert all(path.read_bytes() == data for path, data in before.items())


def parent_dialog(qtbot, qapp, config, pixels):
    assert qapp.styleSheet() == ""
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
    dialog.show(); dialog.tabs.setCurrentWidget(dialog.databases_tab)
    qapp.processEvents()
    assert dialog.styleSheet() == ""
    assert dialog.sqlite_databases_page.table.styleSheet() == ""
    assert dialog.sqlite_databases_page.table.font().pixelSize() == pixels
    return dialog


def settle(qapp):
    for _ in range(3):
        qapp.processEvents()


def assert_editor_and_buttons_fit(sheet):
    for edit in (sheet.name_edit, sheet.file_edit, sheet.password_edit):
        assert edit.height() >= edit.fontMetrics().height()
        assert edit.parentWidget().rect().contains(edit.geometry())
    ok = sheet.buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel = sheet.buttons.button(QDialogButtonBox.StandardButton.Cancel)
    assert ok.mapTo(sheet, QPoint()).x() > cancel.mapTo(sheet, QPoint(cancel.width(), 0)).x()
    assert ok.mapTo(sheet, QPoint(ok.width(), 0)).x() == sheet.width() - 20
    for button in (ok, cancel):
        assert sheet.rect().contains(button.mapTo(sheet, QPoint()))
        assert sheet.rect().contains(button.mapTo(sheet, QPoint(button.width()-1, button.height()-1)))
        option = QStyleOptionButton(); button.initStyleOption(option)
        available = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert available.width() >= button.fontMetrics().horizontalAdvance(button.text())
        assert available.height() >= button.fontMetrics().height()
    return ok, cancel


def test_long_filename_uses_a_real_single_line_row_and_elision(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 12)
    table = dialog.sqlite_databases_page.table
    item = table.item(0, 1)
    assert item.text() == config.sqlite_databases[0].database
    assert table.fontMetrics().horizontalAdvance(item.text()) > table.columnWidth(1)
    assert not table.wordWrap()
    option = QStyleOptionViewItem(); table.initViewItemOption(option)
    assert not option.features & QStyleOptionViewItem.ViewItemFeature.WrapText
    rectangle = table.visualItemRect(item)
    assert table.fontMetrics().height() <= rectangle.height() < table.fontMetrics().height() * 2
    assert table.textElideMode() == Qt.TextElideMode.ElideRight


def test_populated_table_rows_follow_parent_qfont_changes_and_new_rows(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 12)
    page, previous = dialog.sqlite_databases_page, {}
    for pixels in (12, 36, 20, 12):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        table = page.table
        rectangle = table.visualItemRect(table.item(0, 1))
        assert table.font().pixelSize() == pixels
        assert rectangle.height() >= table.fontMetrics().height() + 2
        previous[pixels] = rectangle.height()
    assert previous[36] > previous[20] > previous[12]
    font = QFont(dialog.font()); font.setPixelSize(36); dialog.setFont(font)
    draft = page.snapshot()
    draft.sqlite_databases = (*draft.sqlite_databases, SQLiteDatabaseEntry("new-row", "new_aux", config.sqlite_databases[0].database))
    page._replace_draft(draft)
    settle(qapp)
    assert page.table.visualItemRect(page.table.item(1, 1)).height() >= page.table.fontMetrics().height() + 2


@pytest.mark.parametrize("pixels", [12, 20, 36])
def test_sheet_inherits_pure_parent_qfont_at_creation_and_keeps_real_controls(qtbot, qapp, config, pixels):
    dialog = parent_dialog(qtbot, qapp, config, pixels)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show(); settle(qapp)
    assert sheet.password_edit.font().pixelSize() == pixels
    assert sheet.name_edit.font().pixelSize() == pixels
    assert sheet.fontMetrics().height() == dialog.fontMetrics().height()
    assert not sheet.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    assert sheet.width() >= 600 and sheet.height() >= 215
    ok, cancel = assert_editor_and_buttons_fit(sheet)
    if pixels in (12, 20):
        assert ok.width() == cancel.width() == 80
    else:
        assert ok.width() >= 80 and cancel.width() >= 80
        if qapp.platformName() == "cocoa":
            # Observed QMacStyle/fallback metrics require growth at 36px.
            # Other native styles may fit the same glyphs in the base width;
            # assert_editor_and_buttons_fit checks their actual contents.
            assert cancel.width() > 80
    if pixels == 12:
        assert sheet.size().toTuple() == (600, 215)
    sheet.reject()


def test_open_sheet_tracks_live_parent_qfont_changes_without_pinning(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 12)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show()
    for pixels in (12, 36, 20, 12):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        assert sheet.password_edit.font().pixelSize() == pixels
        assert sheet.fontMetrics().height() == dialog.fontMetrics().height()
        assert not sheet.testAttribute(Qt.WidgetAttribute.WA_SetFont)
        assert_editor_and_buttons_fit(sheet)
    sheet.reject()


def test_global_qss_keeps_precedence_and_removing_it_restores_live_parent_font(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 36)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show()
    for pixels in (18, 22):
        qapp.setStyleSheet("QWidget { font-size: " + str(pixels) + "px; }")
        settle(qapp)
        assert sheet.password_edit.font().pixelSize() == pixels
        assert sheet.password_edit.font().pixelSize() == dialog.sqlite_databases_page.table.font().pixelSize()
    qapp.setStyleSheet("")
    settle(qapp)
    assert dialog.font().pixelSize() == sheet.password_edit.font().pixelSize() == 36
    assert not sheet.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    assert_editor_and_buttons_fit(sheet)
    sheet.reject()


def test_default_password_row_has_its_own_reference_inset_and_preserves_right_edge(qtbot, qapp, config):
    dialog = parent_dialog(qtbot, qapp, config, 12)
    sheet = SQLiteDatabaseDialog(dialog.sqlite_databases_page.snapshot(), "owned-row", dialog)
    qtbot.addWidget(sheet); sheet.show(); settle(qapp)
    password_left = sheet.password_edit.mapTo(sheet, QPoint()).x()
    name_left = sheet.name_edit.mapTo(sheet, QPoint()).x()
    assert password_left > name_left
    if qapp.platformName() == "cocoa":
        # This absolute geometry is observed only in the macOS reference.
        assert 177 <= password_left <= 181
        assert 85 <= password_left - name_left <= 89
    assert sheet.password_edit.mapTo(sheet, QPoint(sheet.password_edit.width(), 0)).x() == 580
    assert_editor_and_buttons_fit(sheet)
    sheet.reject()
