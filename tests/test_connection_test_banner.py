"""Actual Qt result-strip behavior; reference clipboard/failure parity is open."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from opennavicat.ui.connection_test_banner import ConnectionTestBanner


def _application_style_state(app):
    return {
        'stylesheet': app.styleSheet(),
        'font': QFont(app.font()),
        'palette': QPalette(app.palette()),
        'theme': (b'workbenchTheme' in [bytes(name) for name in app.dynamicPropertyNames()],
                  app.property('workbenchTheme')),
    }


def _assert_application_style_state(app, expected):
    actual = _application_style_state(app)
    assert actual['stylesheet'] == expected['stylesheet']
    assert actual['theme'] == expected['theme']
    assert actual['font'] == expected['font']
    assert actual['font'].resolveMask() == expected['font'].resolveMask()
    assert actual['palette'] == expected['palette']
    assert actual['palette'].resolveMask() == expected['palette'].resolveMask()
    assert actual['palette'].currentColorGroup() == expected['palette'].currentColorGroup()


@contextmanager
def _bare_application_style(app):
    """Isolate intrinsic font/palette tests from earlier global theme rules."""
    original = _application_style_state(app)
    try:
        # An application-wide QWidget font/color rule intentionally overrides
        # host-only setFont/setPalette. These component tests need the bare
        # style contract; themed application behavior is exercised separately.
        app.setStyleSheet('')
        assert app.styleSheet() == ''
        yield
    finally:
        app.setStyleSheet(original['stylesheet'])
        font = app.font()
        if font != original['font'] or font.resolveMask() != original['font'].resolveMask():
            app.setFont(original['font'])
        palette = app.palette()
        if (palette != original['palette']
                or palette.resolveMask() != original['palette'].resolveMask()
                or palette.currentColorGroup() != original['palette'].currentColorGroup()):
            app.setPalette(original['palette'])
        present, value = original['theme']
        app.setProperty('workbenchTheme', value if present else None)
        # Only the session QApplication is read here. qtbot may already have
        # closed/deleted tested widgets before this fixture's finalizer runs.
        _assert_application_style_state(app, original)


@pytest.fixture(autouse=True)
def _intrinsic_application_style(qapp):
    with _bare_application_style(qapp):
        yield


def banner(qtbot, width=700, pixels=12, dark=True):
    host = QDialog()
    palette = host.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#303030' if dark else '#ededed'))
    palette.setColor(QPalette.ColorRole.WindowText, QColor('#dddddd' if dark else '#252525'))
    host.setPalette(palette)
    font = QFont(host.font()); font.setPixelSize(pixels); host.setFont(font)
    layout = QVBoxLayout(host); layout.setContentsMargins(0, 0, 0, 0)
    result = ConnectionTestBanner(host); layout.addWidget(result)
    host.resize(width, 100)
    qtbot.addWidget(host); host.show()
    return host, result


def render(widget, dpr):
    image = QImage(widget.width()*dpr, widget.height()*dpr, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr); image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        widget.render(painter, QPoint())
    finally:
        painter.end()
    return image


def test_success_strip_places_copy_after_version_and_dismiss_at_far_edge(qtbot):
    host, result = banner(qtbot)
    assert result.isHidden() and result.copy_text == ''
    result.set_result(True, '连接成功', detail_text='服务器版本: 3.45.0',
                      copy_text='Generated local copy result', tooltip='3.45.0')
    qtbot.waitUntil(lambda: result.isVisible() and result.height() >= result.minimumHeight())
    assert result.status_label.text() == '连接成功'
    assert result.detail_label.text() == '服务器版本: 3.45.0'
    assert result.status_label.textFormat() == result.detail_label.textFormat() == Qt.TextFormat.PlainText
    assert result.status_icon.isVisible()
    assert result.copy_button.text() == result.dismiss_button.text() == ''
    assert result.copy_button.accessibleName() == '复制连接测试结果'
    assert result.dismiss_button.accessibleName() == '关闭连接测试结果'
    assert result.status_label.geometry().right() < result.detail_label.x()
    assert 0 <= result.copy_button.x() - result.detail_label.geometry().right() <= 9
    assert result.dismiss_button.x() > result.copy_button.geometry().right() + 100
    assert result.height() == 34
    assert result.status_label.fontMetrics().horizontalAdvance(result.status_label.text()) <= result.status_label.width()
    qtbot.mouseClick(result.dismiss_button, Qt.MouseButton.LeftButton)
    assert result.isHidden() and host.isVisible()
    result.set_result(True, '连接成功', detail_text='服务器版本: 3.46.0')
    assert result.isVisible() and result.detail_label.text().endswith('3.46.0')


def test_copy_retains_separate_bounded_plain_payload_without_touching_user_clipboard(qtbot, monkeypatch):
    _, result = banner(qtbot)
    payload = '✓  连接成功  服务器版本: ' + '<b>generated</b>'*100
    result.set_result(True, '<script>generated</script>'*100,
                      detail_text='<b>owned detail</b>'*100, copy_text=payload, tooltip='t'*2000)
    assert len(result.status_label.text()) == len(result.detail_label.text()) == 512
    assert len(result.status_label.toolTip()) == len(result.detail_label.toolTip()) == 512
    assert result.copy_text == payload[:1024]
    assert result.status_label.textFormat() == result.detail_label.textFormat() == Qt.TextFormat.PlainText
    copied = []
    class OwnedClipboard:
        def setText(self, text):
            copied.append(text)
    original_accessor = QApplication.clipboard
    with monkeypatch.context() as patch:
        patch.setattr(QApplication, 'clipboard', lambda: OwnedClipboard())
        qtbot.mouseClick(result.copy_button, Qt.MouseButton.LeftButton)
        assert copied == [payload[:1024]]
        result.clear_result()
        assert result.copy_text == '' and not result.copy_button.isEnabled() and result.isHidden()
        result.copy_result()
        assert copied == [payload[:1024]]
    assert QApplication.clipboard == original_accessor


def test_copy_button_reaches_actual_isolated_offscreen_virtual_clipboard():
    # No Cocoa or desktop clipboard is read or modified. The offscreen plugin
    # provides a new process-local clipboard, cleared before this child exits.
    source = '''
import json
from pathlib import Path
from PySide6.QtWidgets import QApplication
import opennavicat.ui.connection_test_banner as module
assert Path(module.__file__).resolve().is_relative_to(Path.cwd())
app = QApplication([])
banner = module.ConnectionTestBanner()
banner.set_result(True, '连接成功', copy_text='Generated virtual clipboard')
banner.copy_button.click()
assert app.clipboard().text() == 'Generated virtual clipboard'
banner.clear_result()
banner.copy_result()
assert app.clipboard().text() == 'Generated virtual clipboard'
app.clipboard().clear()
banner.close()
banner.deleteLater()
app.processEvents()
print(json.dumps({'passed': True, 'plugin': app.platformName()}))
'''
    result = subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'passed': True, 'plugin': 'offscreen'}


def test_failure_has_no_invented_success_icon_or_red_background_and_can_retry(qtbot):
    _, result = banner(qtbot)
    result.set_result(True, '连接成功', detail_text='服务器版本: generated')
    result.set_result(False, '连接失败', copy_text='连接失败', tooltip='Generated failure')
    assert result.isVisible() and result.status_icon.isHidden()
    assert result.detail_label.isHidden() and result.detail_label.text() == ''
    assert result.status_label.text() == '连接失败' and result.status_label.styleSheet() == ''
    assert result.status_label.toolTip() == 'Generated failure'
    result.clear_result()
    assert result.status_label.text() == '' and result.detail_label.text() == ''
    result.set_result(True, '连接成功', detail_text='服务器版本: retry')
    assert result.status_icon.isVisible() and result.isVisible()


@pytest.mark.parametrize('pixels', [12, 24, 36])
@pytest.mark.parametrize('width', [400, 700])
@pytest.mark.parametrize('dark', [False, True])
def test_long_result_stays_inside_current_form_with_large_fonts(qtbot, pixels, width, dark):
    host, result = banner(qtbot, width, pixels, dark)
    result.set_result(True, '连接成功', detail_text='服务器版本: '+'宽'*512,
                      copy_text='Generated copy', tooltip='Generated detail')
    qtbot.waitUntil(lambda: result.width() == host.width())
    assert host.width() == width
    assert result.status_label.font().pixelSize() == pixels
    assert result.detail_label.font().pixelSize() == pixels
    assert result.status_label.fontMetrics().horizontalAdvance('连接成功') <= result.status_label.width()
    for widget in (result.status_icon, result.status_label, result.detail_label,
                   result.copy_button, result.dismiss_button):
        rect = widget.geometry()
        assert result.contentsRect().contains(rect)
        assert widget.width() > 0 and widget.height() > 0
    assert result.copy_button.geometry().right() < result.dismiss_button.x()
    assert result.copy_text == 'Generated copy'


def test_active_result_keeps_status_and_detail_font_inheritance_after_changes(qtbot):
    host, result = banner(qtbot)
    result.set_result(True, '连接成功', detail_text='服务器版本: '+'Generated '*100)
    for pixels in (36, 18, 24):
        font = QFont(host.font()); font.setPixelSize(pixels); host.setFont(font)
        qtbot.waitUntil(lambda: result.status_label.font().pixelSize() == pixels
            and result.detail_label.font().pixelSize() == pixels)
        qtbot.waitUntil(lambda: result.status_label.width() >= result.status_label.fontMetrics().horizontalAdvance('连接成功'))
        assert result.contentsRect().contains(result.status_label.geometry())
        assert result.contentsRect().contains(result.copy_button.geometry())
        assert result.contentsRect().contains(result.dismiss_button.geometry())
    palette = host.palette(); palette.setColor(QPalette.ColorRole.Window, QColor('#ededed')); host.setPalette(palette)
    assert result.status_label.font().pixelSize() == result.detail_label.font().pixelSize() == 24


def test_plain_result_uses_inactive_and_disabled_text_palette_roles(qtbot):
    host, result = banner(qtbot, pixels=24)
    palette = host.palette()
    colors = {QPalette.ColorGroup.Active: '#1010e0',
              QPalette.ColorGroup.Inactive: '#c010c0', QPalette.ColorGroup.Disabled: '#107010'}
    for group, color in colors.items():
        palette.setColor(group, QPalette.ColorRole.WindowText, QColor(color))
    host.setPalette(palette)
    result.set_result(False, 'MMMM', detail_text='MMMM')
    other = QDialog(); qtbot.addWidget(other); other.show()
    other.activateWindow()
    qtbot.waitUntil(lambda: not host.isActiveWindow())

    def includes_color(label, color):
        image = render(label, 1)
        return any(image.pixelColor(x, y).name() == color
                   for x in range(image.width()) for y in range(image.height()))

    assert includes_color(result.status_label, colors[QPalette.ColorGroup.Inactive])
    assert includes_color(result.detail_label, colors[QPalette.ColorGroup.Inactive])
    result.set_result(True, 'MMMM', detail_text='MMMM')
    result.setEnabled(False)
    assert includes_color(result.status_label, colors[QPalette.ColorGroup.Disabled])
    assert includes_color(result.detail_label, colors[QPalette.ColorGroup.Disabled])


@pytest.mark.parametrize('dpr', [1, 2, 3])
def test_actual_strip_pixels_repaint_after_palette_change_at_display_density(qtbot, dpr):
    _, result = banner(qtbot)
    result.set_result(True, '连接成功', detail_text='服务器版本: 3.45.0')
    dark = render(result, dpr)
    assert dark.devicePixelRatio() == dpr
    assert dark.pixelColor(2*dpr, 2*dpr).name() == '#435241'
    icon_pixels = [dark.pixelColor((result.status_icon.x()+x)*dpr, (result.status_icon.y()+y)*dpr)
                   for x in range(16) for y in range(16)]
    assert any(c.green() > c.red()+20 and c.green() > c.blue()+20 for c in icon_pixels)
    assert any(min(c.red(), c.green(), c.blue()) > 230 for c in icon_pixels)
    palette = result.palette(); palette.setColor(QPalette.ColorRole.Window, QColor('#ededed')); result.setPalette(palette)
    light = render(result, dpr)
    assert light.pixelColor(2*dpr, 2*dpr).name() == '#e3efdf'
    assert light != dark


@pytest.mark.parametrize('theme_present', [False, True], ids=['absent-theme', 'present-theme'])
@pytest.mark.parametrize('raise_inside', [False, True], ids=['normal', 'exception'])
def test_bare_style_context_restores_exact_global_state(qapp, theme_present, raise_inside):
    font = QFont(qapp.font()); font.setPixelSize(17); qapp.setFont(font)
    palette = QPalette(qapp.palette())
    palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Window, QColor('#234541'))
    palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.WindowText, QColor('#875432'))
    qapp.setPalette(palette)
    qapp.setProperty('workbenchTheme', 'generated-original-theme' if theme_present else None)
    qapp.setStyleSheet('QWidget { font-size: 12px; color: #112233; }')
    expected = _application_style_state(qapp)
    try:
        with _bare_application_style(qapp):
            assert qapp.styleSheet() == ''
            assert _application_style_state(qapp)['theme'] == expected['theme']
            changed_font = QFont(qapp.font()); changed_font.setPixelSize(29); qapp.setFont(changed_font)
            changed_palette = QPalette(qapp.palette())
            changed_palette.setColor(QPalette.ColorRole.Window, QColor('#654321'))
            qapp.setPalette(changed_palette)
            qapp.setProperty('workbenchTheme', 'generated-inner-theme')
            qapp.setStyleSheet('QWidget { font-size: 31px; }')
            if raise_inside:
                raise RuntimeError('generated context failure')
    except RuntimeError as error:
        assert raise_inside and str(error) == 'generated context failure'
    else:
        assert not raise_inside
    _assert_application_style_state(qapp, expected)
