"""Content growth must preserve the window size selected by its user."""
from PySide6.QtCore import QSize
from PySide6.QtGui import QFont

from opennavicat.ui.theme import apply_theme
from test_sqlite_small_visuals import (
    assert_browse_contents, controls, isolated_appearance, settle,
)


def test_browse_font_growth_keeps_an_explicitly_enlarged_window(qtbot, qapp):
    dialog, sheet = controls(qtbot, qapp)
    dialog.resize(dialog.width() + 250, dialog.height() + 300)
    settle(qapp)
    enlarged = QSize(dialog.size())
    for pixels in (36, 20, 12):
        font = QFont(dialog.font()); font.setPixelSize(pixels); dialog.setFont(font)
        settle(qapp)
        assert dialog.width() >= enlarged.width() and dialog.height() >= enlarged.height()
        assert_browse_contents(dialog, sheet)
    for theme in ("dark", "light"):
        apply_theme(qapp, theme); settle(qapp)
        assert dialog.width() >= enlarged.width() and dialog.height() >= enlarged.height()
        assert_browse_contents(dialog, sheet)
