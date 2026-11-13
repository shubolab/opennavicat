"""Native control palettes and restrained desktop database-workbench styling.

Geometry belongs to the window/widget implementation. These themes keep dense
native controls, neutral surfaces, thin dividers and the system selection model.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

DARK_COLORS = {
    "window": "#303030", "toolbar": "#303030", "object_toolbar": "#1e1e1e",
    "sidebar": "#454545", "sidebar_inactive": "#282828", "base": "#1e1e1e",
    "alternate": "#232323", "header": "#272727", "border": "#454545",
    "text": "#dddddd", "muted": "#999999", "disabled": "#737373",
    "button": "#383838", "hover": "#4c4c4c", "selection": "#0b5ac6",
    "selection_text": "#ffffff", "edit": "#252525", "tab": "#333333",
    "selected_tab": "#454545", "link": "#73aaf2", "tooltip": "#424242",
}
LIGHT_COLORS = {
    "window": "#ededed", "toolbar": "#eeeeee", "object_toolbar": "#f4f4f4",
    "sidebar": "#e5e5e5", "sidebar_inactive": "#eeeeee", "base": "#ffffff",
    "alternate": "#f7f7f7", "header": "#eeeeee", "border": "#c7c7c7",
    "text": "#252525", "muted": "#737373", "disabled": "#a0a0a0",
    "button": "#eeeeee", "hover": "#dcdcdc", "selection": "#0b5ac6",
    "selection_text": "#ffffff", "edit": "#ffffff", "tab": "#e5e5e5",
    "selected_tab": "#ffffff", "link": "#155ca9", "tooltip": "#ffffdc",
}


def _stylesheet(c):
    return """
QWidget { color: %(text)s; font-size: 12px; }
QMainWindow, QDialog { background: %(window)s; }
QToolBar { background: %(toolbar)s; border: 0; border-bottom: 1px solid %(border)s; spacing: 0; padding: 3px 4px; }
QToolBar#objectToolbar, QToolBar#secondaryToolbar { background: %(object_toolbar)s; }
QToolBar QToolButton { font-size: 11px; color: %(text)s; border: 1px solid transparent; padding: 2px 3px; min-width: 46px; }
QToolBar QToolButton:hover { background: %(hover)s; border-color: %(border)s; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: %(selected_tab)s; border-color: %(border)s; }
QToolBar QToolButton:disabled { color: %(disabled)s; }
QToolBar::separator { background: %(border)s; width: 1px; margin: 5px 4px; }
QToolButton#workspaceTabClose { min-width: 0; padding: 0; border: 0; background: transparent; }
QToolButton#workspaceTabClose:hover { background: %(hover)s; border-radius: 3px; }
QWidget#viewButtonGroup { background: %(button)s; border: 1px solid %(border)s; border-radius: 17px; }
QWidget#viewButtonGroup QToolButton { min-width: 0; border: 0; padding: 0; background: transparent; }
QWidget#viewButtonGroup QToolButton:hover { background: %(hover)s; border-radius: 12px; }
QFrame#viewButtonDivider { background: %(muted)s; border: 0; }

QPushButton { padding: 2px 8px; min-height: 20px; }
QPushButton:disabled, QLabel:disabled, QToolButton:disabled { color: %(disabled)s; }
QLineEdit, QSpinBox, QDoubleSpinBox { background: %(edit)s; color: %(text)s; border: 1px solid %(border)s; padding: 2px 4px; min-height: 18px; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: %(selection)s; }
QComboBox { min-height: 20px; padding: 1px 4px; }
QComboBox QAbstractItemView { background: %(base)s; color: %(text)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
QSplitter::handle { background: %(border)s; width: 1px; height: 1px; }
QWidget#sidebar { background: %(sidebar)s; border-right: 1px solid %(border)s; }
QWidget#sidebar:!active { background: %(sidebar_inactive)s; }
QWidget#sidebar QTreeView, QWidget#sidebar QTreeWidget { background: transparent; border: 0; }
QTreeView, QTreeWidget { background: %(sidebar)s; color: %(text)s; border: 0; outline: 0; alternate-background-color: %(sidebar)s; }
QTreeView:!active, QTreeWidget:!active { background: %(sidebar_inactive)s; }
QTreeView::item, QTreeWidget::item { height: 22px; padding: 0; border: 0; }
QTreeView::item:selected, QTreeWidget::item:selected { background: %(selection)s; color: %(selection_text)s; }
QTreeView::item:hover:!selected, QTreeWidget::item:hover:!selected { background: %(hover)s; }
QWidget#sidebar QLineEdit { background: %(edit)s; font-size: 11px; min-height: 17px; padding: 1px 4px; }
QWidget#workspace, QWidget#welcome { background: %(base)s; }
QTabWidget::pane { background: %(base)s; border: 0; border-top: 1px solid %(border)s; }
QTabBar { background: %(tab)s; }
QTabBar::tab { background: %(tab)s; color: %(text)s; border: 0; border-right: 1px solid %(border)s; padding: 4px 14px; min-height: 18px; }
QTabBar::tab:selected { background: %(selected_tab)s; }
QTabBar::tab:hover:!selected { background: %(hover)s; }
QTabWidget#workspaceTabs > QTabBar::tab { min-height: 22px; padding: 3px 16px; }
QTabWidget#queryResults::tab-bar, QTabWidget#structureTabs::tab-bar { alignment: center; }
QTabWidget#queryResults > QTabBar::tab, QTabWidget#structureTabs > QTabBar::tab { background: transparent; border: 0; min-height: 20px; padding: 2px 8px; margin: 4px 1px; }
QTabWidget#queryResults > QTabBar::tab:selected, QTabWidget#structureTabs > QTabBar::tab:selected { background: %(selection)s; color: white; border-radius: 3px; }
QWidget#queryToolbar, QWidget#queryExecutionBar, QWidget#queryFooter, QWidget#tableFooter { background: %(base)s; border-bottom: 1px solid %(border)s; }
QWidget#queryFooter, QWidget#tableFooter { border-top: 1px solid %(border)s; border-bottom: 0; }
QWidget#queryToolbar QToolButton, QWidget#queryExecutionBar QToolButton, QWidget#tableFooter QToolButton { border: 0; background: transparent; padding: 2px; }
QWidget#queryToolbar QToolButton:hover, QWidget#queryExecutionBar QToolButton:hover, QWidget#tableFooter QToolButton:hover { background: %(hover)s; }
QWidget#connectionSettingsPane { background: %(base)s; }
QWidget#inspector { background: %(sidebar_inactive)s; border-left: 1px solid %(border)s; }
QTabWidget#connectionTabs::tab-bar { alignment: center; }
QTabWidget#connectionTabs > QTabBar::tab { border: 0; margin: 5px 1px; padding: 3px 7px; background: transparent; }
QTabWidget#connectionTabs > QTabBar::tab:selected { background: %(selection)s; color: white; border-radius: 3px; }
QPlainTextEdit, QTextEdit { background: %(base)s; color: %(text)s; border: 0; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
QTableView, QTableWidget { background: %(base)s; color: %(text)s; alternate-background-color: %(alternate)s; border: 0; gridline-color: %(border)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
QHeaderView::section { background: %(header)s; color: %(text)s; border: 0; border-right: 1px solid %(border)s; border-bottom: 1px solid %(border)s; padding: 3px 6px; min-height: 18px; font-size: 11px; }
QTableCornerButton::section { background: %(header)s; border: 0; border-right: 1px solid %(border)s; border-bottom: 1px solid %(border)s; }
QLabel#pageStatus { background: %(window)s; color: %(muted)s; font-size: 11px; padding: 3px 6px; border-top: 1px solid %(border)s; }
QLabel#tableTitle { color: %(text)s; }
QStatusBar { background: %(toolbar)s; border-top: 1px solid %(border)s; padding: 0 4px; }
QStatusBar QLabel { color: %(muted)s; font-size: 11px; }
QStatusBar::item { border: 0; }
QProgressBar { background: %(base)s; border: 1px solid %(border)s; min-height: 5px; max-height: 8px; }
QProgressBar::chunk { background: %(selection)s; }
QMenu { background: %(window)s; color: %(text)s; border: 1px solid %(border)s; padding: 2px; }
QMenu::item { padding: 3px 24px 3px 18px; }
QMenu::item:selected { background: %(selection)s; color: %(selection_text)s; }
QMenu::item:disabled { color: %(disabled)s; }
QMenu::separator { background: %(border)s; height: 1px; margin: 3px 5px; }
QListWidget { background: %(base)s; color: %(text)s; border: 1px solid %(border)s; }
QListWidget::item { min-height: 22px; padding: 2px 4px; }
QListWidget::item:selected { background: %(selection)s; color: %(selection_text)s; }
QToolTip { background: %(tooltip)s; color: %(text)s; border: 1px solid %(border)s; padding: 3px 5px; }
""" % c


STYLESHEET = _stylesheet(DARK_COLORS)
LIGHT_STYLESHEET = _stylesheet(LIGHT_COLORS)


def native_palette(dark: bool = True) -> QPalette:
    c = DARK_COLORS if dark else LIGHT_COLORS
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: c["window"], QPalette.ColorRole.WindowText: c["text"],
        QPalette.ColorRole.Base: c["base"], QPalette.ColorRole.AlternateBase: c["alternate"],
        QPalette.ColorRole.Text: c["text"], QPalette.ColorRole.Button: c["button"],
        QPalette.ColorRole.ButtonText: c["text"], QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: c["selection"], QPalette.ColorRole.HighlightedText: c["selection_text"],
        QPalette.ColorRole.Link: c["link"], QPalette.ColorRole.LinkVisited: "#aa8cc8" if dark else "#7648a2",
        QPalette.ColorRole.ToolTipBase: c["tooltip"], QPalette.ColorRole.ToolTipText: c["text"],
        QPalette.ColorRole.PlaceholderText: c["muted"], QPalette.ColorRole.Mid: c["border"],
        QPalette.ColorRole.Dark: "#181818" if dark else "#b0b0b0",
        QPalette.ColorRole.Light: "#555555" if dark else "#ffffff",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor(c["selection"]))
    return palette


def apply_theme(app, mode: str = "system") -> str:
    """Apply dark/light palette without replacing the platform's native style.

    System mode follows QStyleHints.colorScheme and updates when it changes.
    Returns the effective theme name for editors that have semantic colors.
    """
    if mode not in {"system", "dark", "light"}:
        raise ValueError("Theme must be system, dark or light")
    hints = app.styleHints()
    previous = getattr(app, "_opennavicat_theme_listener", None)
    if previous is not None:
        try:
            hints.colorSchemeChanged.disconnect(previous)
        except (RuntimeError, TypeError):
            pass
        app._opennavicat_theme_listener = None

    def update(*_):
        if mode == "system":
            scheme = hints.colorScheme()
            dark = scheme == Qt.ColorScheme.Dark
            if scheme == Qt.ColorScheme.Unknown:
                dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
        else:
            dark = mode == "dark"
        app.setPalette(native_palette(dark))
        app.setStyleSheet(STYLESHEET if dark else LIGHT_STYLESHEET)
        app.setProperty("workbenchTheme", "dark" if dark else "light")
    update()
    if mode == "system":
        hints.colorSchemeChanged.connect(update)
        app._opennavicat_theme_listener = update
    return app.property("workbenchTheme")
