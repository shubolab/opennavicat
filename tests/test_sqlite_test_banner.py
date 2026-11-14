"""Owned SQLite async test-result integration, separate from reference parity."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

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
