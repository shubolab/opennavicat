"""Owned SQLite async test-result integration, separate from reference parity."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSize, QThread, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QPushButton, QStyle, QStyleOptionButton

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.ui.dialogs import ConnectionDialog, _ConnectionProbeResult
from opennavicat.ui.theme import LIGHT_STYLESHEET, STYLESHEET


def editor(qtbot, path):
    dialog = ConnectionDialog(config=ConnectionConfig('generated-sqlite-banner', 'Generated result strip', database=str(path)))
    qtbot.addWidget(dialog); dialog.show()
    return dialog


def create_database(path):
    with closing(sqlite3.connect(path)) as database, database:
        database.execute('CREATE TABLE generated(id INTEGER PRIMARY KEY, label TEXT)')
        database.execute('INSERT INTO generated VALUES (?, ?)', (1, 'Generated owned row'))
        return database.execute('SELECT sqlite_version()').fetchone()[0]


def connection_line_is_green(dialog):
    image = dialog.diagram.grab().toImage()
    dpr = image.devicePixelRatio()
    center = dialog.diagram.width()//2
    colors = [image.pixelColor(round(x*dpr), round(y*dpr))
              for x in range(center-3, center+4) for y in range(62, 65)]
    return any(c.green() > c.red()+40 and c.green() > c.blue()+40 for c in colors)


@pytest.mark.parametrize('stylesheet', ['', STYLESHEET, LIGHT_STYLESHEET], ids=['native', 'dark', 'light'])
def test_real_async_sqlite_version_strip_close_and_dismiss_retest_preserve_database(qtbot, tmp_path, monkeypatch, stylesheet):
    path = tmp_path/'generated-banner.sqlite'
    expected_version = create_database(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    calls, completed = [], []
    original_close, original_execute = DatabaseSession.close, DatabaseSession.execute
    gui = threading.get_ident()

    def close(session):
        original_close(session)
        calls.append(('close', threading.get_ident(), session.connection is None))

    def execute(session, sql, limit=1000):
        calls.append(('execute', threading.get_ident(), sql))
        return original_execute(session, sql, limit=limit)

    monkeypatch.setattr(DatabaseSession, 'close', close)
    monkeypatch.setattr(DatabaseSession, 'execute', execute)
    dialog = editor(qtbot, path)
    dialog.setStyleSheet(stylesheet)
    initial = asdict(dialog._collect_config())
    dialog.test_finished.connect(lambda ok, message: completed.append((ok, message, threading.get_ident())))
    assert dialog.sqlite_test_banner.isHidden() and calls == []
    assert not connection_line_is_green(dialog)
    for attempt in range(2):
        with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
            qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
        strip = dialog.sqlite_test_banner
        assert result.args == [True, '连接成功']  # Retained public signal contract.
        assert strip.isVisible() and strip.status_label.text() == '连接成功'
        assert strip.detail_label.text() == '服务器版本: '+expected_version
        assert strip.status_label.toolTip() == expected_version
        assert strip.copy_text == '连接成功  服务器版本: '+expected_version
        assert strip.status_icon.isVisible() and dialog.test_status.isHidden()
        assert connection_line_is_green(dialog)
        assert dialog.test_status.text() == '连接成功'
        assert strip.parentWidget() is dialog.settings_pane
        assert strip.geometry().bottom() < dialog.footer.y()
        assert dialog.buttons.x() > dialog.test_button.geometry().right()+50
        assert dialog.buttons.geometry().right() >= dialog.footer.width()-21
        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_right = save.mapTo(dialog.footer, QPoint(save.width(), 0)).x()
        assert save_right >= dialog.footer.width()-32  # Check the actual button, not its larger box.
        rail_bottom = dialog.profile_pane.mapTo(dialog, QPoint(0, dialog.profile_pane.height())).y()
        footer_bottom = dialog.footer.mapTo(dialog, QPoint(0, dialog.footer.height())).y()
        assert rail_bottom == footer_bottom  # The rail continues beside the footer.
        qtbot.mouseClick(strip.dismiss_button, Qt.MouseButton.LeftButton)
        assert strip.isHidden() and dialog.isVisible()
        assert dialog.get_config() is None and asdict(dialog._collect_config()) == initial
        assert len([c for c in calls if c[0] == 'close']) == attempt+1
    assert completed == [(True, '连接成功', gui)]*2
    assert all(call[1] != gui for call in calls)
    assert all(call[2] is True for call in calls if call[0] == 'close')
    assert [call[2] for call in calls if call[0] == 'execute'] == ['SELECT 1', 'SELECT sqlite_version()']*2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(path)) as database:
        assert database.execute('SELECT * FROM generated').fetchall() == [(1, 'Generated owned row')]


@pytest.mark.parametrize('empty_error', [False, True])
def test_failure_retains_footer_result_and_retry_never_reuses_old_success(qtbot, tmp_path, monkeypatch, empty_error):
    path = tmp_path/'generated-retry.sqlite'; path.write_text('Generated invalid SQLite')
    dialog = editor(qtbot, path)
    dialog._tested_driver = 'sqlite'; dialog._test_server_version = 'Generated previous version'
    dialog._display_test_result(True, '连接成功')
    assert dialog.sqlite_test_banner.isVisible()
    assert connection_line_is_green(dialog)
    if empty_error:
        original_probe = dialog._probe_session
        monkeypatch.setattr(dialog, '_probe_session', lambda *_args: (_ for _ in ()).throw(RuntimeError('')))
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
    assert result.args[0] is False
    assert dialog.sqlite_test_banner.isHidden() and dialog.sqlite_test_banner.copy_text == ''
    assert not connection_line_is_green(dialog)
    assert dialog.test_status.isVisible() and dialog.test_status.text() == '连接失败'
    assert dialog.test_status.toolTip() == result.args[1]
    if empty_error:
        assert result.args[1] == ''
        monkeypatch.setattr(dialog, '_probe_session', original_probe)
    path.unlink(); expected = create_database(path)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000) as result:
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
    assert result.args == [True, '连接成功']
    assert dialog.sqlite_test_banner.isVisible()
    assert dialog.sqlite_test_banner.detail_label.text() == '服务器版本: '+expected
    assert connection_line_is_green(dialog)


def test_busy_retest_clears_old_copy_blocks_close_and_delivers_on_gui(qtbot, tmp_path, monkeypatch):
    path = tmp_path/'generated-busy.sqlite'; create_database(path)
    started, release = threading.Event(), threading.Event()
    closed, probes, copied = [], [], []
    gui = threading.get_ident()

    class Session:
        def __init__(self, config):
            probes.append(('create', threading.get_ident(), config.database))

        def connect(self):
            started.set(); assert release.wait(5)

        def execute(self, sql, limit):
            probes.append((sql, threading.get_ident(), limit))
            return [SimpleNamespace(rows=[['Generated busy version']])]

        def objects(self, schema):
            probes.append(('objects', threading.get_ident(), schema))

        def close(self):
            closed.append(threading.get_ident())

    monkeypatch.setattr('opennavicat.database.DatabaseSession', Session)
    monkeypatch.setattr(QApplication, 'clipboard', lambda: SimpleNamespace(setText=copied.append))
    dialog = editor(qtbot, path)
    dialog._tested_driver = 'sqlite'; dialog._test_server_version = 'Old generated version'
    dialog._display_test_result(True, '连接成功')
    assert connection_line_is_green(dialog)
    callbacks = []
    dialog.test_finished.connect(lambda *_args: callbacks.append(QThread.currentThread() == dialog.thread()))
    try:
        with qtbot.waitSignal(dialog.test_finished, timeout=5000):
            qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
            qtbot.waitUntil(started.is_set)
            assert dialog._testing and dialog.sqlite_test_banner.isHidden()
            assert not connection_line_is_green(dialog)
            assert dialog.sqlite_test_banner.copy_text == ''
            dialog.sqlite_test_banner.copy_result(); assert copied == []
            assert not dialog.content.isEnabled() and not dialog.test_button.isEnabled()
            assert not dialog.buttons.isEnabled()
            dialog.accept(); dialog.reject(); dialog.close()
            assert dialog.isVisible() and dialog.get_config() is None
            release.set()
        qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
        assert closed and all(t != gui for t in closed)
        assert all(p[1] != gui for p in probes)
        assert callbacks == [True] and dialog.sqlite_test_banner.isVisible()
        assert 'Generated busy version' in dialog.sqlite_test_banner.detail_label.text()
        assert connection_line_is_green(dialog)
    finally:
        release.set()
        qtbot.waitUntil(lambda: not dialog._test_pool.activeThreadCount(), timeout=5000)


def test_sqlite_banner_is_plain_bounded_and_copy_action_uses_generated_result(qtbot, tmp_path, monkeypatch):
    path = tmp_path/'generated-long.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    copied = []
    monkeypatch.setattr(QApplication, 'clipboard', lambda: SimpleNamespace(setText=copied.append))
    dialog._tested_driver = 'sqlite'
    dialog._test_succeeded(_ConnectionProbeResult('连接成功', '<b>Generated</b>'*200))
    dialog._display_test_result(True, '连接成功')
    strip = dialog.sqlite_test_banner
    qtbot.waitUntil(lambda: strip.width() == dialog.settings_pane.width())
    assert strip.status_label.textFormat() == strip.detail_label.textFormat() == Qt.TextFormat.PlainText
    assert strip.detail_label.text().startswith('服务器版本: <b>Generated</b>')
    assert len(strip.detail_label.text()) <= 512 and len(strip.status_label.toolTip()) == 512
    qtbot.mouseClick(strip.copy_button, Qt.MouseButton.LeftButton)
    assert copied == ['连接成功  服务器版本: '+('<b>Generated</b>'*200)[:512]]
    assert strip.width() == dialog.settings_pane.width()
    assert strip.contentsRect().contains(strip.copy_button.geometry())
    assert strip.contentsRect().contains(strip.dismiss_button.geometry())


def test_driver_switch_hides_sqlite_result_and_preserves_other_footer_hierarchy(qtbot, tmp_path):
    path = tmp_path/'generated-switch.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    dialog._tested_driver = 'sqlite'; dialog._test_server_version = 'Generated version'
    dialog._display_test_result(True, '连接成功')
    assert dialog.sqlite_test_banner.isVisible() and dialog.footer.parentWidget() is dialog.settings_pane
    assert connection_line_is_green(dialog)
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('postgresql'))
    assert dialog.sqlite_test_banner.isHidden() and dialog.sqlite_test_banner.copy_text == ''
    assert dialog.test_status.text() == '' and dialog.test_status.isVisible()
    assert dialog.footer.parentWidget() is dialog and dialog.footer.height() == 60
    assert not connection_line_is_green(dialog)
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('sqlite'))
    assert dialog.sqlite_test_banner.isHidden()
    assert dialog.footer.parentWidget() is dialog.settings_pane and dialog.footer.height() == 44
    assert not connection_line_is_green(dialog)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()


@pytest.mark.parametrize('change', ['mode', 'database', 'read_only', 'timeout'])
def test_sqlite_binding_changes_clear_success_line_without_implicit_test(qtbot, tmp_path, change):
    path = tmp_path/'generated-binding.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000):
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
    assert connection_line_is_green(dialog)
    if change == 'mode':
        dialog.sqlite_mode_combo.setCurrentIndex(dialog.sqlite_mode_combo.findData('create'))
    elif change == 'database':
        dialog.database_edit.setText(str(tmp_path/'generated-other.sqlite'))
    elif change == 'read_only':
        dialog.read_only_check.setChecked(True)
    else:
        dialog.timeout_spin.setValue(dialog.timeout_spin.value()+1)
    assert not connection_line_is_green(dialog)
    assert dialog.sqlite_test_banner.isHidden() and dialog.sqlite_test_banner.copy_text == ''
    assert not dialog._testing and not dialog._test_pool.activeThreadCount() and dialog._test_task is None


def test_sqlite_validation_failure_keeps_line_neutral_and_does_not_launch_worker(qtbot, tmp_path, monkeypatch):
    path = tmp_path/'generated-validation.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    with qtbot.waitSignal(dialog.test_finished, timeout=5000):
        qtbot.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not dialog._testing and not dialog._test_pool.activeThreadCount())
    assert connection_line_is_green(dialog)
    path.unlink()  # Owned fixture only; the next Test fails GUI-side validation.
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda _parent, _title, text: warnings.append(text))
    dialog.test_connection()
    assert warnings and not connection_line_is_green(dialog)
    assert dialog.sqlite_test_banner.isHidden() and dialog.sqlite_test_banner.copy_text == ''
    assert not dialog._testing and not dialog._test_pool.activeThreadCount() and dialog._test_task is None


def test_sqlite_profile_add_is_icon_only_in_header_and_stays_unavailable(qtbot, tmp_path):
    path = tmp_path/'generated-header.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    button = dialog.new_profile_button
    assert button.text() == ''
    assert button.parentWidget() is dialog.profile_header
    assert dialog.profile_header.isVisible() and dialog.profile_header_title.text() == '连接配置文件'
    assert dialog.profile_pane.layout().indexOf(dialog.profile_header) == 0
    assert dialog.profile_pane.layout().indexOf(dialog.profiles) == 1
    assert dialog.profile_header.layout().indexOf(button) == 1
    assert dialog.profiles.item(0).text() == '主要配置文件（有效）'
    assert dialog.profiles.currentRow() == 0
    assert button.iconSize() == QSize(16, 16) and not button.icon().isNull()
    assert button.accessibleName() == '新建连接配置文件' and '尚未实现' in button.toolTip()
    assert not button.isEnabled() and button.isFlat() and not button.autoDefault()
    qtbot.waitUntil(lambda: button.height() == 26 and button.width() == 26)
    assert dialog.profile_header.rect().contains(button.geometry())
    assert button.y() < dialog.profiles.y()
    clicked = QSignalSpy(button.clicked)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicked.count() == 0 and dialog.profiles.count() == 1
    assert dialog.get_config() is None and dialog.isVisible() and not dialog._test_pool.activeThreadCount()


def profile_button_state(button):
    policy = button.sizePolicy()
    font = button.font()
    return dict(text=button.text(), tooltip=button.toolTip(), accessible=button.accessibleName(),
        minimum=button.minimumSize().toTuple(), maximum=button.maximumSize().toTuple(),
        policy=(policy.horizontalPolicy(), policy.verticalPolicy(), policy.horizontalStretch(),
                policy.verticalStretch(), policy.controlType(), policy.retainSizeWhenHidden()),
        stylesheet=button.styleSheet(), flat=button.isFlat(), auto_default=button.autoDefault(),
        font=(font.family(), font.pixelSize(), font.pointSizeF(), font.weight(), font.resolveMask()),
        explicit_font=button.testAttribute(Qt.WidgetAttribute.WA_SetFont),
        icon_size=button.iconSize().toTuple(), enabled=button.isEnabled())


@pytest.mark.parametrize('driver', ['postgresql', 'mysql', 'mariadb', 'sqlserver', 'redis', 'mongodb'])
def test_sqlite_profile_header_roundtrips_reuse_button_and_restore_native_driver_state(qtbot, tmp_path, driver):
    path = tmp_path/('generated-'+driver+'.sqlite'); create_database(path)
    dialog = editor(qtbot, path)
    button, model, first_item = dialog.new_profile_button, dialog.profiles.model(), dialog.profiles.item(0)
    dialog.profiles.addItem('Generated retained profile row')
    second_item = dialog.profiles.item(1)
    second_item.setData(Qt.ItemDataRole.UserRole, {'generated': 'retained'})
    dialog.profiles.setCurrentRow(1)
    dialog.name_edit.setText('Generated immutable name')
    dialog.user_edit.setText('Generated database user')
    dialog.password_edit.setText('generated-process-memory-only')
    dialog.remember_check.setChecked(True)
    dialog.read_only_check.setChecked(True)
    dialog.timeout_spin.setValue(17)
    retained = (dialog.name_edit.text(), dialog.database_edit.text(), dialog.user_edit.text(),
                dialog.password_edit.text(), dialog.remember_check.isChecked(),
                dialog.read_only_check.isChecked(), dialog.timeout_spin.value())
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
    native = profile_button_state(button)
    native_control = ConnectionDialog(config=ConnectionConfig('generated-native', 'Generated native driver',
        driver=driver, database='generated_database', ssl_mode='disable'))
    qtbot.addWidget(native_control); native_control.show()
    assert native == profile_button_state(native_control.new_profile_button)
    assert native['text'] == '新建连接配置文件'
    assert native['stylesheet'] == '' and not native['flat']
    for _ in range(20):
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('sqlite'))
        assert dialog.new_profile_button is button and button.parentWidget() is dialog.profile_header
        assert button.text() == '' and not button.isEnabled()
        assert dialog.profile_header.isVisible() and dialog.profile_pane.width() == 200
        assert first_item.text() == '主要配置文件（有效）'
        assert dialog.profiles.model() is model and dialog.profiles.item(0) is first_item
        assert dialog.profiles.item(1) is second_item and dialog.profiles.currentItem() is second_item
        assert second_item.data(Qt.ItemDataRole.UserRole) == {'generated': 'retained'}
        dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData(driver))
        assert dialog.new_profile_button is button and button.parentWidget() is dialog.profile_pane
        assert dialog.profile_header.isHidden()
        assert dialog.profile_pane.layout().indexOf(dialog.profile_header) == -1
        assert dialog.profile_pane.layout().indexOf(dialog.profiles) == 0
        assert dialog.profile_pane.layout().indexOf(button) == 1
        assert profile_button_state(button) == native
        assert first_item.text() == 'Main Profile（有效）'
        assert dialog.profiles.model() is model and dialog.profiles.currentItem() is second_item
        assert dialog.profile_pane.findChildren(QPushButton, 'newConnectionProfile') == [button]
        assert len(dialog.profile_pane.findChildren(type(dialog.profile_header), 'sqliteConnectionProfileHeader')) == 1
    assert (dialog.name_edit.text(), dialog.database_edit.text(), dialog.user_edit.text(),
            dialog.password_edit.text(), dialog.remember_check.isChecked(),
            dialog.read_only_check.isChecked(), dialog.timeout_spin.value()) == retained
    assert not dialog._testing and not dialog._test_pool.activeThreadCount() and dialog.get_config() is None


def test_external_profile_model_text_selection_and_user_data_are_not_replaced(qtbot, tmp_path):
    path = tmp_path/'generated-custom-profile.sqlite'; create_database(path)
    dialog = editor(qtbot, path)
    item = dialog.profiles.item(0)
    item.setText('Generated externally retained primary')
    item.setData(Qt.ItemDataRole.UserRole, ('generated', 7))
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('postgresql'))
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('sqlite'))
    assert dialog.profiles.item(0) is item and dialog.profiles.currentItem() is item
    assert item.text() == 'Generated externally retained primary'
    assert item.data(Qt.ItemDataRole.UserRole) == ('generated', 7)


@pytest.mark.parametrize('dpr', [1, 2, 3])
@pytest.mark.parametrize('stylesheet', ['', STYLESHEET, LIGHT_STYLESHEET], ids=['native', 'dark', 'light'])
def test_sqlite_header_circle_plus_fits_real_contents_at_display_density(qtbot, tmp_path, dpr, stylesheet):
    path = tmp_path/'generated-header-density.sqlite'; create_database(path)
    dialog = editor(qtbot, path); dialog.setStyleSheet(stylesheet)
    button = dialog.new_profile_button
    qtbot.waitUntil(lambda: button.width() == 26 and dialog.profile_header.rect().contains(button.geometry()))
    option = QStyleOptionButton(); option.initFrom(button); option.rect = button.rect()
    contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
    assert contents.width() >= 16 and contents.height() >= 16
    title = dialog.profile_header_title
    assert title.textFormat() == Qt.TextFormat.PlainText
    assert title.fontMetrics().horizontalAdvance(title.text()) <= title.contentsRect().width()
    pixmap = button.icon().pixmap(QSize(16, 16), dpr, QIcon.Mode.Disabled)
    assert pixmap.width() == pixmap.height() == 16*dpr and pixmap.devicePixelRatio() == dpr
    image = pixmap.toImage()
    ink = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
           if image.pixelColor(x, y).alpha() > 96]
    assert ink and all(max(c.red(), c.green(), c.blue())-min(c.red(), c.green(), c.blue()) < 20 for c in ink)
    assert all(image.pixelColor(x, 0).alpha() == image.pixelColor(x, image.height()-1).alpha() == 0 for x in range(image.width()))
    qtbot.mouseClick(dialog.profile_toggle, Qt.MouseButton.LeftButton)
    assert not dialog.profile_pane.isVisible() and not dialog.profile_header.isVisible()
    qtbot.mouseClick(dialog.profile_toggle, Qt.MouseButton.LeftButton)
    assert dialog.profile_header.isVisible() and dialog.new_profile_button is button


@pytest.mark.parametrize('explicit', [False, True], ids=['inherited', 'explicit'])
def test_profile_button_restores_current_parent_font_ownership(explicit):
    # A fresh offscreen app prevents earlier tests' global font stylesheets
    # from masking inheritance. No desktop/application setting is changed.
    source = '''
import json, sys
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton
from opennavicat.database import ConnectionConfig
import opennavicat.ui.dialogs as module
app = QApplication([])
assert app.styleSheet() == '' and app.platformName() == 'offscreen'
explicit = sys.argv[1] == '1'
if explicit:
    def button_factory(*args, **kwargs):
        button = QPushButton(*args, **kwargs)
        if button.text() == '新建连接配置文件':
            font = QFont(button.font()); font.setPixelSize(19); button.setFont(font)
        return button
    module.QPushButton = button_factory
dialog = module.ConnectionDialog(config=ConnectionConfig('generated-font', 'Generated font', database=':memory:'))
dialog.show()
button = dialog.new_profile_button
observed = []
for pixels in (36, 12, 24):
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('sqlite'))
    font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
    app.processEvents()
    dialog.driver_combo.setCurrentIndex(dialog.driver_combo.findData('postgresql'))
    app.processEvents()
    native = module.ConnectionDialog(config=ConnectionConfig('generated-native-font', 'Generated native font', driver='postgresql', database='generated'))
    native.setFont(font); native.show(); app.processEvents()
    expected = native.new_profile_button
    assert button.font().pixelSize() == expected.font().pixelSize() == (19 if explicit else pixels), (pixels, button.font().pixelSize(), expected.font().pixelSize())
    assert button.testAttribute(Qt.WidgetAttribute.WA_SetFont) is explicit
    assert button.fontMetrics().horizontalAdvance(button.text()) == expected.fontMetrics().horizontalAdvance(expected.text())
    assert button.fontMetrics().height() == expected.fontMetrics().height()
    assert button.minimumSize() == expected.minimumSize() and button.maximumSize() == expected.maximumSize()
    assert dialog.new_profile_button is button and not button.isEnabled()
    assert not dialog._test_pool.activeThreadCount() and not native._test_pool.activeThreadCount()
    observed.append({'parent_pixels': pixels, 'button_pixels': button.font().pixelSize(), 'text_advance': button.fontMetrics().horizontalAdvance(button.text())})
    native.close(); native.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
dialog.close(); dialog.deleteLater(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
print(json.dumps({'passed': True, 'explicit': explicit, 'observed': observed, 'plugin': app.platformName()}))
'''
    result = subprocess.run([sys.executable, '-c', source, '1' if explicit else '0'],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
            'PYTHONWARNINGS': 'error'}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr
    report = json.loads(result.stdout)
    assert report['passed'] and report['explicit'] is explicit and report['plugin'] == 'offscreen'
    assert [r['parent_pixels'] for r in report['observed']] == [36, 12, 24]
