"""SQLite-only control rendering and retained file-picker dispatch."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QFileDialog, QPushButton, QStyle, QStyleOptionButton
import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.ui.dialogs import ConnectionDialog, _ConnectionDiagram
from opennavicat.ui.sqlite_connection_databases import SQLiteDatabaseDialog
from opennavicat.ui.theme import apply_theme


@pytest.fixture(autouse=True)
def isolated_appearance(qapp):
    style, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme = qapp.property("workbenchTheme")
    qapp.setStyleSheet("")
    yield
    qapp.setPalette(palette)
    qapp.setFont(font)
    qapp.setStyleSheet(style)
    qapp.setProperty("workbenchTheme", theme)


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def controls(qtbot, qapp):
    config = ConnectionConfig("generated-visual", "Generated SQLite controls", database="generated-owned.sqlite")
    dialog = ConnectionDialog(config=config)
    qtbot.addWidget(dialog)
    font = QFont(dialog.font()); font.setPixelSize(12); dialog.setFont(font)
    dialog.show()
    sheet = SQLiteDatabaseDialog(config, parent=dialog)
    qtbot.addWidget(sheet); sheet.show(); settle(qapp)
    return dialog, sheet


def assert_browse_contents(dialog, sheet):
    buttons = (dialog.browse_button, sheet.browse_button)
    assert [button.text() for button in buttons] == ["...", "..."]
    assert all(button.styleSheet() == "" for button in buttons)
    assert buttons[0].width() == buttons[1].width()
    # The sheet's content-aware line edit can request a taller row. Check
    # both real content rectangles instead of forcing that row back down.
    for edit, button in ((dialog.database_edit, buttons[0]), (sheet.file_edit, buttons[1])):
        assert button.x() == edit.geometry().right() + 1
    for button in buttons:
        option = QStyleOptionButton(); button.initStyleOption(option)
        content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert content.width() >= button.fontMetrics().horizontalAdvance(button.text())
        assert content.height() >= button.fontMetrics().height()
        assert button.parentWidget().rect().contains(button.geometry())
        assert not button.autoDefault() and not button.isDefault()
        assert button.width() < 80  # Avoid native standard-button minimum width.


@pytest.mark.parametrize("theme", [None, "dark", "light"], ids=["bare", "dark", "light"])
def test_matching_three_dot_browse_controls_fit_actual_style_content(qtbot, qapp, theme):
    if theme:
        apply_theme(qapp, theme)
    dialog, sheet = controls(qtbot, qapp)
    assert_browse_contents(dialog, sheet)


def test_browse_controls_follow_live_parent_font_and_application_theme(qtbot, qapp):
    dialog, sheet = controls(qtbot, qapp)
    sizes = {}
    for pixels in (12, 36, 20, 12):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        assert dialog.browse_button.font().pixelSize() == sheet.browse_button.font().pixelSize() == pixels
        assert_browse_contents(dialog, sheet)
        sizes.setdefault(pixels, dialog.browse_button.size())
        assert dialog.browse_button.size() == sizes[pixels]
        # Width follows real glyph demand and native chrome, with a 20px
        # minimum. A larger font may grow only the height while still fitting
        # three dots in that width (observed on the Windows native QPA).
        assert dialog.browse_button.width() >= max(20, dialog.browse_button.fontMetrics().horizontalAdvance("...") + 8)
    assert sizes[36].height() > sizes[20].height() > sizes[12].height()
    assert sizes[36].width() >= sizes[20].width() >= sizes[12].width()
    font = QFont(dialog.font()); font.setPixelSize(36); dialog.setFont(font)
    settle(qapp)
    for theme in ("dark", "light"):
        apply_theme(qapp, theme); settle(qapp)
        assert dialog.browse_button.font().pixelSize() == sheet.browse_button.font().pixelSize() == 12
        assert_browse_contents(dialog, sheet)
    qapp.setStyleSheet(""); settle(qapp)
    # Qt can restore a cached application font on the whole general file row
    # when all application QSS is removed. Compare real sibling/fresh controls;
    # a compact button must not introduce a separate font owner there.
    for edit, button in ((dialog.database_edit, dialog.browse_button), (sheet.file_edit, sheet.browse_button)):
        fresh = QPushButton("...", button.parentWidget())
        fresh.ensurePolished()
        assert button.font() == fresh.font() == edit.font()
        fresh.deleteLater()
    for pixels in (20, 36):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        assert dialog.browse_button.font().pixelSize() == sheet.browse_button.font().pixelSize() == pixels
        assert_browse_contents(dialog, sheet)
    assert_browse_contents(dialog, sheet)


def test_browse_cancel_and_create_dispatch_preserve_drafts(qtbot, qapp, monkeypatch, tmp_path):
    dialog, sheet = controls(qtbot, qapp)
    calls = []
    def picker(kind):
        def invoke(parent, title, filename, filters):
            calls.append((kind, parent, filename))
            return "", ""
        return invoke
    monkeypatch.setattr(QFileDialog, "getOpenFileName", picker("open"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", picker("save"))
    general_before = (dialog.name_edit.text(), dialog.database_edit.text())
    sheet.name_edit.setText("generated_aux"); sheet.file_edit.setText(str(tmp_path / "generated-aux.sqlite"))
    sheet_before = (sheet.name_edit.text(), sheet.file_edit.text())
    qtbot.mouseClick(dialog.browse_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(sheet.browse_button, Qt.MouseButton.LeftButton)
    dialog.sqlite_mode_combo.setCurrentIndex(dialog.sqlite_mode_combo.findData("create"))
    qtbot.mouseClick(dialog.browse_button, Qt.MouseButton.LeftButton)
    assert [(kind, parent) for kind, parent, _ in calls] == [("open", dialog), ("open", sheet), ("save", dialog)]
    assert (dialog.name_edit.text(), dialog.database_edit.text()) == general_before
    assert (sheet.name_edit.text(), sheet.file_edit.text()) == sheet_before
    assert list(tmp_path.iterdir()) == []
    assert dialog.get_config() is None and sheet.get_config() is None


def test_sqlite_cylinder_is_a_narrow_unfilled_layered_outline(qtbot, qapp):
    diagram = _ConnectionDiagram()
    qtbot.addWidget(diagram)
    diagram.resize(400, 140)
    palette = QPalette(diagram.palette())
    background, ink = QColor("#172534"), QColor("#a4b2c1")
    palette.setColor(QPalette.ColorRole.Window, background)
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Mid, ink)
    palette.setColor(QPalette.ColorRole.PlaceholderText, ink)
    diagram.setPalette(palette); diagram.setAutoFillBackground(True)
    diagram.show(); settle(qapp)
    image = diagram.grab().toImage()
    scale = image.devicePixelRatio()
    def pixel(x, y):
        return image.pixelColor(round(x*scale), round(y*scale))
    center = round(diagram.width() * .76)
    points = [(x,y) for x in range(center-35, center+36) for y in range(30,95)
              if sum(abs(a-b) for a,b in zip(pixel(x,y).getRgb()[:3], background.getRgb()[:3])) > 80]
    assert points
    width = max(x for x,y in points)-min(x for x,y in points)+1
    height = max(y for x,y in points)-min(y for x,y in points)+1
    assert 27 <= width <= 32 and 36 <= height <= 42
    assert pixel(center, 52) == background  # Interior stays transparent, including dark palettes.
    bands, previous = 0, False
    for y in range(30,95):
        present = sum(abs(a-b) for a,b in zip(pixel(center,y).getRgb()[:3], background.getRgb()[:3])) > 80
        bands += present and not previous
        previous = present
    assert bands == 5  # Top ellipse (two crossings), two internal arcs and bottom arc.
    # The link stops before the outline; success changes only its foreground.
    assert pixel(center-25, 63) == background
    diagram.sqlite_connection_succeeded = True; diagram.update(); settle(qapp)
    green = diagram.grab().toImage().pixelColor(round(200*scale), round(63*scale))
    assert green.green() > green.red()+40 and green.green() > green.blue()+40
