"""Owned offscreen SQLite database-page workflows; no native reference access."""
from contextlib import closing
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sqlite3
import threading

import pytest
from PySide6.QtCore import QPoint, QTimer, Qt, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QMessageBox

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.sqlite_database_config import SQLiteDatabaseEntry, copy_connection_config
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog
from opennavicat.ui.theme import LIGHT_STYLESHEET, STYLESHEET


@pytest.fixture(autouse=True)
def forbid_keyring(monkeypatch):
    import keyring
    def forbidden(*_args, **_kwargs):
        pytest.fail("SQLite draft accessed the OS keyring")
    for method in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, method, forbidden)


@pytest.fixture
def files(tmp_path):
    paths = []
    for name in ("main", "aux", "alternate"):
        path = tmp_path / ("generated-" + name + ".sqlite")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, label TEXT)")
            connection.executemany("INSERT INTO items VALUES(?,?)", [(1, name + "-1"), (2, name + "-2")])
        paths.append(path)
    return tuple(paths)


def profile(main, *rows, passwords=None):
    return ConnectionConfig("owned-ui", "Owned SQLite draft", database=str(main), sqlite_open_mode="existing",
        sqlite_databases=tuple(rows), sqlite_database_passwords=passwords or {})


def row(path, *, identifier="aux-row", name="owned_aux", encrypted=False):
    return SQLiteDatabaseEntry(identifier, name, str(path), encrypted)


def editor(qtbot, config=None):
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.tabs.setCurrentWidget(dialog.databases_tab)
    return dialog


def drive_sheet(trigger, interact):
    """Operate the actual nested Qt modal event loop and always release it."""
    errors, seen = [], []
    def act():
        sheet = QApplication.activeModalWidget()
        try:
            assert isinstance(sheet, SQLiteDatabaseDialog)
            seen.append(True)
            interact(sheet)
        except BaseException as error:
            errors.append(error)
        finally:
            if isinstance(sheet, SQLiteDatabaseDialog) and sheet.isVisible():
                sheet.reject()
    QTimer.singleShot(0, act)
    trigger()
    if errors:
        raise errors[0]
    assert seen


def add_row(qtbot, page, path, name="owned_aux", *, encrypted=False, password=None):
    def fill(sheet):
        sheet.name_edit.setText(name)
        sheet.file_edit.setText(str(path))
        sheet.encrypted_check.setChecked(encrypted)
        if password is not None:
            qtbot.keyClicks(sheet.password_edit, password)
        qtbot.mouseClick(sheet.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    drive_sheet(lambda: qtbot.mouseClick(page.add_button, Qt.MouseButton.LeftButton), fill)


def enter_password(qtbot, sheet, value):
    sheet.password_edit.setFocus()
    sheet.password_edit.selectAll()
    if value:
        qtbot.keyClicks(sheet.password_edit, value)
    else:
        qtbot.keyClick(sheet.password_edit, Qt.Key.Key_Backspace)


def test_bare_buttons_table_selection_browse_and_sheet_cancel_do_not_save(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    original = profile(main)
    before = {path: path.read_bytes() for path in files}
    dialog = editor(qtbot, original)
    page = dialog.sqlite_databases_page
    assert [page.table.horizontalHeaderItem(i).text() for i in range(3)] == ["数据库", "文件", "已加密"]
    for button in (page.add_button, page.remove_button):
        assert button.text() == "" and button.isFlat() and button.isEnabled()
        assert button.iconSize() == QSize(16, 16) and not button.icon().isNull()
        assert not button.autoDefault() and not button.isDefault()
    qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    assert page.table.rowCount() == 0
    def cancel(sheet):
        assert sheet.windowModality() == Qt.WindowModality.WindowModal
        assert sheet.size().width() == 600 and sheet.size().height() == 215
        assert sheet.name_edit.text() == sheet.file_edit.text() == ""
        assert not sheet.encrypted_check.isChecked() and not sheet.password_edit.isEnabled()
        sheet.name_edit.setText("retained_picker_name")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
        qtbot.mouseClick(sheet.browse_button, Qt.MouseButton.LeftButton)
        assert sheet.name_edit.text() == "retained_picker_name" and sheet.file_edit.text() == ""
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(aux), ""))
        qtbot.mouseClick(sheet.browse_button, Qt.MouseButton.LeftButton)
        assert sheet.file_edit.text() == str(aux)
        qtbot.mouseClick(sheet.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    drive_sheet(lambda: qtbot.mouseClick(page.add_button, Qt.MouseButton.LeftButton), cancel)
    assert page.table.rowCount() == 0 and dialog.get_config() is None
    assert original.sqlite_databases == () and original.sqlite_database_passwords == {}
    assert all(path.read_bytes() == value for path, value in before.items())


def test_new_row_is_unselected_and_minus_deletes_only_selected_draft_without_file_delete(qtbot, files):
    main, aux, _alternate = files
    dialog = editor(qtbot, profile(main))
    page = dialog.sqlite_databases_page
    before = aux.read_bytes()
    add_row(qtbot, page, aux)
    assert page.table.rowCount() == 1 and not page.table.selectedItems()
    assert page.table.item(0, 2).text() == "否"
    assert not page.table.item(0, 2).flags() & Qt.ItemFlag.ItemIsUserCheckable
    qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    assert page.table.rowCount() == 1
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=page.table.visualItemRect(page.table.item(0, 2)).center())
    assert len(page.table.selectedItems()) == 3
    qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    assert page.table.rowCount() == 0 and page.snapshot().sqlite_databases == ()
    assert aux.read_bytes() == before and dialog.get_config() is None


@pytest.mark.parametrize("column", [0, 2])
def test_observed_double_click_cells_open_prefilled_sheet_and_cancel_retains_selection(qtbot, files, column):
    main, aux, _alternate = files
    dialog = editor(qtbot, profile(main, row(aux)))
    page = dialog.sqlite_databases_page
    rectangle = page.table.visualItemRect(page.table.item(0, column))
    qtbot.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center())
    def cancel(sheet):
        assert sheet.name_edit.text() == "owned_aux" and sheet.file_edit.text() == str(aux)
        sheet.name_edit.setText("cancelled_name")
        sheet.encrypted_check.setChecked(True)
        assert sheet.password_edit.isEnabled()
        sheet.reject()
    drive_sheet(lambda: qtbot.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center()), cancel)
    assert page.table.item(0, 0).text() == "owned_aux" and page.table.item(0, 2).text() == "否"
    assert len(page.table.selectedItems()) == 3 and dialog.get_config() is None


def test_empty_encrypted_password_is_accepted_as_draft_but_real_test_rejects_capability(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    dialog = editor(qtbot, profile(main))
    add_row(qtbot, dialog.sqlite_databases_page, aux, encrypted=True)
    captured = dialog._collect_config()
    assert captured.sqlite_databases[0].encrypted and captured.sqlite_database_passwords == {}
    assert dialog.sqlite_databases_page.table.item(0, 2).text() == "是"
    def forbidden(*_args, **_kwargs):
        pytest.fail("Unsupported encrypted draft opened a database")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as completed:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())
    assert completed.args[0] is False and "encryption provider" in completed.args[1]
    assert not dialog.diagram.sqlite_connection_succeeded and not dialog.sqlite_database_diagram.sqlite_connection_succeeded
    assert dialog.sqlite_test_banner.isHidden()


@pytest.mark.parametrize("change", ["file", "flag", "file-revert", "flag-revert"])
def test_binding_edits_clear_prefilled_ui_and_carrier_even_if_reverted(qtbot, files, change):
    main, aux, alternate = files
    secret = "generated-process-only-secret"
    source = profile(main, row(aux, encrypted=True), passwords={"aux-row": secret})
    sheet = SQLiteDatabaseDialog(source, "aux-row")
    qtbot.addWidget(sheet); sheet.show()
    assert sheet.password_edit.text() == secret and sheet.password_edit.echoMode() == sheet.password_edit.EchoMode.Password
    if change.startswith("file"):
        sheet.file_edit.setText(str(alternate))
        if change.endswith("revert"):
            sheet.file_edit.setText(str(aux))
    else:
        sheet.encrypted_check.setChecked(False)
        if change.endswith("revert"):
            sheet.encrypted_check.setChecked(True)
    assert sheet.password_edit.text() == ""
    sheet.accept()
    result = sheet.get_config()
    assert result.sqlite_database_passwords == result.sqlite_database_password_bindings == {}
    assert source.sqlite_database_passwords == {"aux-row": secret}


@pytest.mark.parametrize("fresh", [False, True, "empty"])
def test_true_password_input_binds_after_absolute_path_capture_and_plain_prefill_is_not_new(qtbot, files, monkeypatch, fresh):
    main, aux, alternate = files
    monkeypatch.chdir(main.parent)
    secret = "generated-same-value"
    source = profile(main, row(aux, encrypted=True), passwords={"aux-row": secret})
    sheet = SQLiteDatabaseDialog(source, "aux-row")
    qtbot.addWidget(sheet); sheet.show()
    sheet.file_edit.setText(alternate.name)
    if fresh is False:
        sheet.password_edit.setText(secret)  # Programmatic prefill is not user input.
    else:
        enter_password(qtbot, sheet, secret)
        if fresh == "empty":
            enter_password(qtbot, sheet, "")
    sheet.accept()
    result = sheet.get_config()
    assert result.sqlite_databases[0].database == str(alternate)
    if fresh is False:
        assert result.sqlite_database_passwords == {}
    else:
        assert result.sqlite_database_passwords == {"aux-row": "" if fresh == "empty" else secret}
        assert result.sqlite_database_password_bindings == {"aux-row": (source.id, str(alternate), True)}
    assert source.sqlite_databases[0].database == str(aux) and source.sqlite_database_passwords["aux-row"] == secret


def test_relative_old_metadata_normalizes_without_rebinding_old_secret_and_copies_are_independent(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    monkeypatch.chdir(main.parent)
    source = profile(main, row(aux.name, encrypted=True), passwords={"aux-row": "generated-old-relative"})
    dialog = editor(qtbot, source)
    page = dialog.sqlite_databases_page
    first, second = page.snapshot(), page.snapshot()
    assert first.sqlite_databases[0].database == str(aux)
    assert first.sqlite_database_passwords == first.sqlite_database_password_bindings == {}
    first.sqlite_database_passwords["aux-row"] = "generated-isolated-copy"
    assert second.sqlite_database_passwords == page.snapshot().sqlite_database_passwords == {}
    assert source.sqlite_databases[0].database == aux.name and source.sqlite_database_passwords["aux-row"] == "generated-old-relative"
    with pytest.raises(FrozenInstanceError):
        second.sqlite_databases[0].name = "cannot-mutate-shared-entry"


def test_rename_reorder_and_outer_cancel_preserve_owner_identity_without_secret_cross_talk(qtbot, files):
    main, aux, alternate = files
    source = profile(main, row(aux, encrypted=True), row(alternate, identifier="second-row", name="second", encrypted=True),
                     passwords={"aux-row": "generated-first", "second-row": "generated-second"})
    original = copy_connection_config(source)
    dialog = editor(qtbot, source)
    page = dialog.sqlite_databases_page
    def rename(sheet):
        sheet.name_edit.setText("renamed")
        sheet.accept()
    drive_sheet(lambda: page._edit_cell(0, 0), rename)
    draft = page.snapshot()
    draft.sqlite_databases = tuple(reversed(draft.sqlite_databases))
    page._replace_draft(draft)
    first, second = dialog._collect_config(), dialog._collect_config()
    assert first.id == second.id == source.id == page.connection_id
    assert [entry.id for entry in first.sqlite_databases] == ["second-row", "aux-row"]
    assert first.sqlite_databases[1].name == "renamed"
    assert first.sqlite_database_passwords == original.sqlite_database_passwords
    first.sqlite_database_passwords.clear()
    assert second.sqlite_database_passwords == page.snapshot().sqlite_database_passwords == original.sqlite_database_passwords
    for index in range(page.table.rowCount()):
        for column in range(3):
            item = page.table.item(index, column)
            for secret in original.sqlite_database_passwords.values():
                assert secret not in item.text() + item.toolTip() + item.statusTip() + item.whatsThis()
                assert secret not in repr(second)
    dialog.reject()
    assert dialog.get_config() is None and source == original


def test_stale_sheet_cannot_restore_a_deleted_row_or_its_new_secret(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    source = profile(main, row(aux, encrypted=True), passwords={"aux-row": "generated-original"})
    dialog = editor(qtbot, source)
    page = dialog.sqlite_databases_page
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    def stale(sheet):
        page.table.selectRow(0)
        page.remove_selected()
        sheet.name_edit.setText("stale-name")
        enter_password(qtbot, sheet, "generated-new-stale-secret")
        sheet.accept()
    drive_sheet(lambda: page._edit_cell(0, 0), stale)
    assert warnings and page.table.rowCount() == 0
    assert page.snapshot().sqlite_database_passwords == page.snapshot().sqlite_database_password_bindings == {}
    assert source.sqlite_database_passwords == {"aux-row": "generated-original"}


def test_real_async_all_attachment_test_closes_worker_and_edits_reset_both_diagrams(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    before = {path: path.read_bytes() for path in files}
    dialog = editor(qtbot)
    dialog.database_edit.setText(str(main))
    dialog.name_edit.setText("Owned two-file test")
    page = dialog.sqlite_databases_page
    add_row(qtbot, page, aux)
    gui = threading.get_ident()
    observed, closed = [], []
    original_connect, original_close = DatabaseSession.connect, DatabaseSession.close
    def connect(session):
        original_connect(session)
        with closing(session.connection.execute("SELECT id,label FROM owned_aux.items ORDER BY id")) as cursor:
            observed.append((session.config.id, threading.get_ident(), cursor.fetchall()))
    def close(session):
        original_close(session)
        closed.append((threading.get_ident(), session.connection is None))
    monkeypatch.setattr(DatabaseSession, "connect", connect)
    monkeypatch.setattr(DatabaseSession, "close", close)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as completed:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())
    assert completed.args == [True, "连接成功"]
    assert observed and all(item == (page.connection_id, item[1], [(1, "aux-1"), (2, "aux-2")]) and item[1] != gui for item in observed)
    assert closed and all(thread != gui and empty for thread, empty in closed)
    assert dialog.diagram.sqlite_connection_succeeded and dialog.sqlite_database_diagram.sqlite_connection_succeeded
    assert dialog.sqlite_test_banner.isVisible()
    def rename(sheet):
        sheet.name_edit.setText("renamed_after_test")
        sheet.accept()
    drive_sheet(lambda: page._edit_cell(0, 0), rename)
    assert dialog.sqlite_test_banner.isHidden()
    assert not dialog.diagram.sqlite_connection_succeeded and not dialog.sqlite_database_diagram.sqlite_connection_succeeded
    dialog.accept()
    result = dialog.get_config()
    assert result.id == page.connection_id and result.sqlite_open_mode == "existing"
    assert result.sqlite_databases[0].name == "renamed_after_test" and result.sqlite_databases[0].database == str(aux)
    assert all(path.read_bytes() == value for path, value in before.items())


def test_real_corrupt_second_attachment_fails_test_and_closes_native_candidate(qtbot, files, monkeypatch):
    main, aux, second = files
    second.write_bytes(b"owned corrupt additional SQLite file")
    before = {path: path.read_bytes() for path in files}
    dialog = editor(qtbot, profile(main))
    page = dialog.sqlite_databases_page
    add_row(qtbot, page, aux)
    add_row(qtbot, page, second, "second")
    original_connect, candidates = sqlite3.connect, []
    class Candidate(sqlite3.Connection):
        closed = False
        def close(self):
            super().close()
            self.closed = True
    def connect(*args, **kwargs):
        candidate = original_connect(*args, **kwargs, factory=Candidate)
        candidates.append(candidate)
        return candidate
    monkeypatch.setattr(sqlite3, "connect", connect)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as completed:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount())
    assert completed.args[0] is False and candidates and all(candidate.closed for candidate in candidates)
    assert dialog.sqlite_test_banner.isHidden()
    assert not dialog.diagram.sqlite_connection_succeeded and not dialog.sqlite_database_diagram.sqlite_connection_succeeded
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("driver", ["postgresql", "mysql", "mariadb", "sqlserver", "redis", "mongodb"])
def test_driver_roundtrip_retains_private_sqlite_draft_and_other_driver_note(qtbot, files, driver):
    main, aux, _alternate = files
    dialog = editor(qtbot, profile(main, row(aux)))
    page, initial = dialog.sqlite_databases_page, dialog.sqlite_databases_page.snapshot()
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
    assert page.isHidden() and not dialog.network_database_note.isHidden()
    assert dialog.databases_tab.layout().contentsMargins().left() == 20
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData("sqlite"))
    assert not page.isHidden() and dialog.network_database_note.isHidden()
    assert page is dialog.sqlite_databases_page and page.snapshot().sqlite_databases == initial.sqlite_databases
    assert dialog.databases_tab.layout().contentsMargins().left() == 0


@pytest.mark.parametrize("stylesheet", ["", STYLESHEET, LIGHT_STYLESHEET], ids=["native", "dark", "light"])
@pytest.mark.parametrize("pixels", [12, 20])
def test_database_page_and_sheet_keep_controls_visible_with_font_and_theme(qtbot, files, stylesheet, pixels):
    main, aux, _alternate = files
    dialog = editor(qtbot, profile(main, row(aux)))
    dialog.setStyleSheet(stylesheet + "\nQWidget { font-size: " + str(pixels) + "px; }")
    font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
    page = dialog.sqlite_databases_page
    qtbot.waitUntil(lambda: page.table.height() > 0)
    assert page.rect().contains(page.table.geometry())
    assert page.rect().contains(page.add_button.geometry())
    assert page.table.item(0, 1).text() == str(aux) and page.table.textElideMode() == Qt.TextElideMode.ElideRight
    sheet = SQLiteDatabaseDialog(page.snapshot(), "aux-row", dialog)
    qtbot.addWidget(sheet); sheet.show()
    assert sheet.width() >= 600 and sheet.height() >= 215
    for control in (sheet.name_edit, sheet.file_edit, sheet.encrypted_check, sheet.password_edit, sheet.buttons):
        assert sheet.rect().contains(control.geometry()) if control.parentWidget() is sheet else control.parentWidget().rect().contains(control.geometry())
    assert not sheet.browse_button.autoDefault()
    assert sheet.browse_button.fontMetrics().horizontalAdvance("...") < sheet.browse_button.width()
    assert sheet.password_edit.font().pixelSize() == pixels
    if os.environ.get("QT_SCALE_FACTOR"):
        assert sheet.devicePixelRatioF() == pytest.approx(float(os.environ["QT_SCALE_FACTOR"]))
    ok, cancel = (sheet.buttons.button(kind) for kind in (QDialogButtonBox.StandardButton.Ok, QDialogButtonBox.StandardButton.Cancel))
    assert ok.x() > cancel.x()
    assert ok.mapTo(sheet, QPoint(ok.width(), 0)).x() >= sheet.width() - 21
    assert page.table.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeft
    sheet.reject()


def test_enter_accepts_only_the_sheet_and_new_owner_is_stable_for_test_and_save(qtbot, files):
    main, aux, _alternate = files
    dialog = editor(qtbot)
    dialog.name_edit.setText("Owned stable new owner")
    dialog.database_edit.setText(str(main))
    page = dialog.sqlite_databases_page
    def enter(sheet):
        sheet.name_edit.setText("entered_aux")
        sheet.file_edit.setText(str(aux))
        sheet.name_edit.setFocus()
        qtbot.keyClick(sheet.name_edit, Qt.Key.Key_Return)
    drive_sheet(lambda: qtbot.mouseClick(page.add_button, Qt.MouseButton.LeftButton), enter)
    assert dialog.isVisible() and dialog.get_config() is None and page.table.rowCount() == 1
    first, second = dialog._collect_config(testing=True), dialog._collect_config()
    assert first.id == second.id == page.connection_id
    dialog.sqlite_create_radio.click()
    assert dialog._collect_config().sqlite_open_mode == "create"


@pytest.mark.parametrize("action", ["add", "rename", "delete", "password"])
def test_accepted_database_or_secret_edits_invalidate_presentation_state(qtbot, files, action):
    main, aux, alternate = files
    source = profile(main, row(aux, encrypted=action == "password"),
                     passwords={"aux-row": "generated-before"} if action == "password" else {})
    dialog = editor(qtbot, source)
    page = dialog.sqlite_databases_page
    # Presentation setup only: encrypted execution remains explicitly unsupported.
    dialog._tested_driver = "sqlite"
    dialog._test_server_version = "Generated previous result"
    dialog._display_test_result(True, "连接成功")
    assert dialog.sqlite_test_banner.isVisible() and dialog.sqlite_database_diagram.sqlite_connection_succeeded
    if action == "add":
        add_row(qtbot, page, alternate, "another")
    elif action == "delete":
        page.table.selectRow(0)
        qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
        assert page.snapshot().sqlite_database_passwords == {}
    else:
        def change(sheet):
            if action == "password":
                enter_password(qtbot, sheet, "generated-after")
            else:
                sheet.name_edit.setText("renamed")
            sheet.accept()
        drive_sheet(lambda: page._edit_cell(0, 0), change)
    assert dialog.sqlite_test_banner.isHidden() and dialog.sqlite_test_banner.copy_text == ""
    assert not dialog.diagram.sqlite_connection_succeeded and not dialog.sqlite_database_diagram.sqlite_connection_succeeded
    assert dialog.get_config() is None and dialog._test_task is None
    if action == "password":
        assert source.sqlite_database_passwords == {"aux-row": "generated-before"}
        assert page.snapshot().sqlite_database_passwords == {"aux-row": "generated-after"}


def test_structurally_invalid_sheet_input_leaves_draft_open_with_finite_secret_free_error(qtbot, files, monkeypatch):
    main, aux, _alternate = files
    source = profile(main, row(aux, encrypted=True), passwords={"aux-row": "generated-private-value"})
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    sheet = SQLiteDatabaseDialog(source, "aux-row")
    qtbot.addWidget(sheet); sheet.show()
    sheet.name_edit.setText("x" * 4097)
    sheet.accept()
    assert sheet.isVisible() and sheet.get_config() is None and warnings
    assert all("generated-private-value" not in text and len(text) < 100 for text in warnings)
    assert source.sqlite_databases[0].name == "owned_aux" and source.sqlite_database_passwords["aux-row"] == "generated-private-value"
    sheet.reject()


def test_new_bare_controls_use_existing_record_icon_artwork(qtbot, files, monkeypatch):
    import opennavicat.ui.sqlite_connection_databases as module
    original, requests = module.tool_icon, []
    def icon(name, size):
        requests.append((name, size))
        return original(name, size)
    monkeypatch.setattr(module, "tool_icon", icon)
    main, _aux, _alternate = files
    dialog = editor(qtbot, profile(main))
    assert requests == [("record_add", 16), ("record_delete", 16)]
    assert dialog.sqlite_databases_page.remove_button.isEnabled()
