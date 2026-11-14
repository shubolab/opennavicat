"""Oracle control integration; independent Qt artwork is tested separately.

The actual reference establishes the disabled Database-tab glyphs and SQLite's
successful result strip. These tests preserve this app's Oracle behavior; they
do not establish unobserved Oracle clipboard or failure-reference parity.
"""
from dataclasses import asdict, replace

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QInputDialog, QStyle, QStyleOptionButton

from opennavicat.database import ConnectionConfig
from opennavicat.ui.oracle_connection import OracleConnectionDialog
from opennavicat.ui.theme import LIGHT_STYLESHEET, STYLESHEET


def _profile(**changes):
    return replace(ConnectionConfig(
        'generated-oracle-controls', 'Generated Oracle controls', driver='oracle',
        host='database.invalid', port=1521, database='', username='GENERATED_OWNER',
        password='', ssl_mode='disable', oracle_service_identifier='GENERATED_SERVICE',
    ), **changes)


def _dialog(qtbot, config=None):
    dialog = OracleConnectionDialog(config=config or _profile())
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _assert_visible_through_ancestors(widget, dialog):
    assert widget.isVisible()
    rect = QRect(QPoint(), widget.size())
    parent = widget.parentWidget()
    while parent is not None:
        rect.translate(widget.mapTo(parent, QPoint()))
        assert parent.rect().contains(rect), (widget.objectName(), parent.objectName(), rect, parent.rect())
        if parent is dialog:
            break
        widget, parent = parent, parent.parentWidget()
    else:
        pytest.fail('Control has no dialog ancestor')


@pytest.mark.parametrize('enabled', [False, True])
def test_database_icon_buttons_keep_custom_flag_callbacks_and_original_draft(qtbot, monkeypatch, enabled):
    source = _profile(oracle_custom_database_list_enabled=enabled,
        oracle_custom_database_list=[dict(active=False, name='GENERATED_A', auto_open=True)])
    before = asdict(source)
    dialog = _dialog(qtbot, source)
    dialog.tabs.setCurrentWidget(dialog.databases_tab)
    prompts = []

    def prompt(*_args):
        prompts.append(True)
        return 'GENERATED_B', True

    monkeypatch.setattr(QInputDialog, 'getText', prompt)
    add, remove, refresh = (dialog.oracle_database_add, dialog.oracle_database_remove,
                            dialog.oracle_database_refresh)
    assert add.isEnabled() is enabled and remove.isEnabled() is enabled
    assert not refresh.isEnabled() and '尚未实现' in refresh.toolTip()
    refresh_clicked = QSignalSpy(refresh.clicked)
    qtbot.mouseClick(refresh, Qt.MouseButton.LeftButton)
    assert refresh_clicked.count() == 0
    if not enabled:
        qtbot.mouseClick(add, Qt.MouseButton.LeftButton)
        assert not prompts and dialog.oracle_database_table.rowCount() == 1
        dialog.oracle_custom_database_check.setChecked(True)
    qtbot.mouseClick(add, Qt.MouseButton.LeftButton)
    assert prompts == [True]
    assert [entry['name'] for entry in dialog._database_rows()] == ['GENERATED_A', 'GENERATED_B']
    dialog.oracle_database_table.setCurrentCell(0, 1)
    qtbot.mouseClick(remove, Qt.MouseButton.LeftButton)
    expected = [dict(active=True, name='GENERATED_B', auto_open=False)]
    assert dialog._database_rows() == expected
    dialog.oracle_custom_database_check.setChecked(False)
    assert not add.isEnabled() and not remove.isEnabled() and not refresh.isEnabled()
    assert dialog._collect_config().oracle_custom_database_list == expected
    assert not dialog._test_pool.activeThreadCount()
    assert asdict(source) == before


@pytest.mark.parametrize('stylesheet', [LIGHT_STYLESHEET, STYLESHEET], ids=['light', 'dark'])
@pytest.mark.parametrize('dpr', [1, 2, 3])
def test_database_and_result_controls_have_icon_contents_without_glyph_text_or_clipping(qtbot, stylesheet, dpr):
    dialog = _dialog(qtbot)
    dialog.setStyleSheet(stylesheet)
    dialog.tabs.setCurrentWidget(dialog.databases_tab)
    dialog.test_finished.emit(True, 'Oracle generated version')
    qtbot.waitUntil(lambda: dialog.test_banner.isVisible())
    QApplication.processEvents()
    controls = (
        (dialog.oracle_database_add, '添加数据库'),
        (dialog.oracle_database_remove, '删除数据库'),
        (dialog.oracle_database_refresh, '刷新数据库列表'),
        (dialog.test_copy_button, '复制连接测试结果'),
        (dialog.test_dismiss_button, '关闭连接测试结果'),
    )
    for button, name in controls:
        assert button.text() == '' and not button.icon().isNull()
        assert button.accessibleName() == name and button.toolTip()
        assert button.iconSize() == QSize(16, 16)
        option = QStyleOptionButton(); button.initStyleOption(option)
        content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert content.width() >= 16 and content.height() >= 16
        _assert_visible_through_ancestors(button, dialog)
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled):
            pixmap = button.icon().pixmap(QSize(16, 16), dpr, mode)
            assert pixmap.size() == QSize(16*dpr, 16*dpr)
            assert pixmap.devicePixelRatioF() == dpr
            image = pixmap.toImage()
            assert any(image.pixelColor(x, y).alpha() > 96
                       for x in range(image.width()) for y in range(image.height()))
    assert dialog.test_banner.y() + dialog.test_banner.height() <= dialog.footer.y()


def test_result_strip_keeps_exact_bounded_plaintext_copy_and_clears_after_retest(qtbot, monkeypatch):
    dialog = _dialog(qtbot)
    copied = []

    class Clipboard:
        def setText(self, text): copied.append(text)

    monkeypatch.setattr(QApplication, 'clipboard', lambda: Clipboard())
    version = '<b>Generated version</b> ' + '数据🙂' * 600
    expected = '✓  连接成功  服务器版本: ' + version[:512]
    dialog.test_finished.emit(True, version)
    assert dialog.test_status.text() == '连接成功'
    assert dialog.test_status.textFormat() == Qt.TextFormat.PlainText
    assert dialog.test_banner.detail_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.test_status.toolTip() == version[:512]
    assert dialog.test_banner.status_icon.isVisible()
    dialog.test_copy_button.click()
    dialog._copy_test_result()
    assert copied == [expected, expected]
    dialog.test_dismiss_button.click()
    assert dialog.test_banner.isHidden()
    started = []
    monkeypatch.setattr('opennavicat.ui.dialogs.ConnectionDialog._start_connection_test',
                        lambda _self, config: started.append(config))
    dialog._start_connection_test(_profile())
    assert started and dialog.test_banner.isHidden()
    assert dialog.test_banner.copy_text == ''
    assert not any(diagram.connection_succeeded for diagram in dialog._diagrams)
    dialog.test_finished.emit(False, 'Generated failure <b>not markup</b>')
    assert dialog.test_banner.isVisible() and dialog.test_status.text() == '连接失败'
    assert dialog.test_banner.status_icon.isHidden()
    assert dialog.test_status.toolTip() == 'Generated failure <b>not markup</b>'
    dialog.test_copy_button.click()
    assert copied[-1] == '连接失败'


@pytest.mark.parametrize('stylesheet', [LIGHT_STYLESHEET, STYLESHEET], ids=['light', 'dark'])
def test_long_test_detail_keeps_copy_and_close_inside_reference_width(qtbot, stylesheet):
    dialog = _dialog(qtbot)
    dialog.setStyleSheet(stylesheet)
    qtbot.waitUntil(lambda: dialog.frameGeometry().width() == 900)
    before = dialog.frameGeometry().width()
    dialog.test_finished.emit(True, 'GENERATED_LONG_VERSION_' * 1000)
    qtbot.waitUntil(lambda: dialog.test_banner.isVisible())
    QApplication.processEvents()
    assert dialog.frameGeometry().width() == before
    for widget in (dialog.test_banner.status_icon, dialog.test_status,
                   dialog.test_banner.detail_label, dialog.test_copy_button,
                   dialog.test_dismiss_button):
        _assert_visible_through_ancestors(widget, dialog)
    assert dialog.test_copy_button.x() < dialog.test_dismiss_button.x()
