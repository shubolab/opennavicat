"""SQLite advanced drafts: real controls, exact bindings and no implicit I/O."""
import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QStyle, QStyleOptionComboBox, QStyleOptionFrame

from opennavicat.database import ConnectionConfig
from opennavicat.sqlite_encryption_config import (
    SQLITE_CIPHER_NAMES, bound_sqlite_encryption_password, with_sqlite_encryption_password,
)
from opennavicat.ui.sqlite_advanced_settings import SQLiteAdvancedSettings


def page(qtbot, config=None):
    config = config or ConnectionConfig("generated-advanced", "Generated advanced", database="generated-owned.sqlite")
    widget = SQLiteAdvancedSettings(QWidget(), config)
    qtbot.addWidget(widget)
    widget.resize(600, 720)
    widget.show()
    return widget, config


def test_fresh_controls_keep_empty_cipher_and_separate_unavailable_features(qtbot):
    widget, config = page(qtbot)
    assert not widget.encrypted_check.isChecked()
    assert widget.remember_check.isChecked() and not widget.remember_check.isEnabled()
    assert not widget.password_edit.isHidden() and not widget.password_edit.isEnabled()
    assert widget.password_edit.text() == ""
    assert widget.cipher_combo.isEditable()
    assert widget.cipher_combo.currentText() == "" and not widget.cipher_combo.isEnabled()
    assert tuple(widget.cipher_combo.itemText(i) for i in range(widget.cipher_combo.count())) == SQLITE_CIPHER_NAMES
    assert len(SQLITE_CIPHER_NAMES) == 15
    assert widget.page_size_edit.isHidden()
    for field in (widget.settings_edit, widget.settings_browse, widget.initial_query_edit,
                  widget.initial_query_browse, widget.auto_connect_check):
        assert not field.isEnabled() and "尚未完成" in field.toolTip()
    assert widget.settings_browse.text() == widget.initial_query_browse.text() == "..."
    assert widget.settings_browse.styleSheet() == widget.initial_query_browse.styleSheet() == ""
    draft = widget.apply_to(config)
    assert not draft.sqlite_encryption_enabled and draft.sqlite_cipher_name == ""
    widget.encrypted_check.setChecked(True)
    assert widget.password_edit.isEnabled() and widget.remember_check.isEnabled()
    assert widget.cipher_combo.isEnabled() and widget.cipher_combo.currentText() == ""
    widget.remember_check.setChecked(False)
    assert not widget.password_edit.isHidden() and not widget.password_edit.isEnabled()
    assert widget.password_edit.text() == ""


@pytest.mark.parametrize("change", ["path", "owner", "enabled", "cipher", "page", "remember"])
def test_loaded_password_cannot_reappear_after_binding_changes_and_revert(qtbot, change):
    config = ConnectionConfig("generated-owner", "Generated secret", database="generated-a.sqlite",
        sqlite_encryption_enabled=True, sqlite_cipher_name="aes128cbc legacy")
    config = with_sqlite_encryption_password(config, "generated memory secret")
    widget, _ = page(qtbot, config)
    assert widget.password_edit.text() == "generated memory secret"
    if change in {"path", "owner"}:
        endpoint = (config.id, "generated-b.sqlite") if change == "path" else ("generated-other", config.database)
        widget.set_endpoint(*endpoint)
        widget.set_endpoint(config.id, config.database)
    elif change == "enabled":
        widget.encrypted_check.setChecked(False); widget.encrypted_check.setChecked(True)
    elif change == "cipher":
        widget.cipher_combo.setEditText("aes256cbc legacy")
        widget.cipher_combo.setEditText(config.sqlite_cipher_name)
    elif change == "page":
        widget.page_size_edit.setText("4096"); widget.page_size_edit.setText("0")
    else:
        widget.remember_check.setChecked(False); widget.remember_check.setChecked(True)
    assert widget.password_edit.text() == ""
    assert bound_sqlite_encryption_password(widget.apply_to(config)) == ""
    widget.set_password("generated explicit replacement")
    assert bound_sqlite_encryption_password(widget.apply_to(config)) == "generated explicit replacement"
    assert bound_sqlite_encryption_password(config) == "generated memory secret"


def test_editable_cipher_preserves_values_and_only_legacy_shows_page_size(qtbot):
    widget, config = page(qtbot)
    widget.encrypted_check.setChecked(True)
    for name in SQLITE_CIPHER_NAMES:
        widget.cipher_combo.setEditText(name)
        assert widget.cipher_combo.currentText() == name
        assert widget.cipher_combo.toolTip() == name
        assert widget.page_size_edit.isVisible() == ("legacy" in name)
        assert widget.apply_to(config).sqlite_cipher_name == name
    widget.cipher_combo.setEditText("generated unsupported value")
    assert widget.cipher_combo.currentText() == "generated unsupported value"
    with pytest.raises(ValueError):
        widget.apply_to(config)


@pytest.mark.parametrize("pixels", [12, 20, 36])
def test_native_editor_contents_fit_live_fonts_without_fixed_geometry(qtbot, qapp, pixels):
    old = qapp.styleSheet()
    qapp.setStyleSheet("")
    try:
        widget, config = page(qtbot)
        font = QFont(widget.font()); font.setPixelSize(pixels); widget.setFont(font)
        widget.encrypted_check.setChecked(True)
        widget.cipher_combo.setEditText("sqlcipher legacy v4")
        widget.resize(widget.size().expandedTo(widget.minimumSizeHint()))
        for _ in range(3): qapp.processEvents()
        for edit in (widget.settings_edit, widget.initial_query_edit, widget.password_edit,
                     widget.page_size_edit, widget.cipher_combo.lineEdit()):
            option = QStyleOptionFrame(); edit.initStyleOption(option)
            content = edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, option, edit)
            assert content.height() >= edit.fontMetrics().height()
            assert edit.parentWidget().rect().contains(edit.geometry())
        combo = widget.cipher_combo
        option = QStyleOptionComboBox(); combo.initStyleOption(option)
        content = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, combo)
        assert content.height() >= combo.fontMetrics().height()
        assert combo.currentText() == "sqlcipher legacy v4"
    finally:
        qapp.setStyleSheet(old)
