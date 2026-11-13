"""Small Oracle controls: input safety and real Qt icon/state behavior, no DB."""
from dataclasses import asdict, replace
import json

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QIcon, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDialogButtonBox, QInputDialog, QLineEdit, QMessageBox,
    QStyleFactory, QWidget,
)

from opennavicat.database import ConnectionConfig
from opennavicat.ui.oracle_connection import OracleConnectionDialog
from opennavicat.ui.theme import LIGHT_STYLESHEET, STYLESHEET


def _profile(**changes):
    return replace(ConnectionConfig(
        "generated-small-controls", "Generated Oracle", driver="oracle",
        host="database.invalid", port=1521, username="GENERATED_OWNER",
        password="", database="", ssl_mode="disable",
        oracle_service_identifier="GENERATED_SERVICE",
    ), **changes)


def _dialog(qtbot, config=None):
    dialog = OracleConnectionDialog(config=config or _profile())
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _port_editor(dialog):
    """Reach the old native editor too, so red tests exercise its fallback."""
    field = dialog.port_spin
    return field if isinstance(field, QLineEdit) else field.lineEdit()


def _block_external_work(monkeypatch, dialog):
    prompts, dispatches, warnings = [], [], []

    def prompt(*args, **kwargs):
        prompts.append((args, kwargs))
        return "generated-password-must-not-be-requested", True

    monkeypatch.setattr(QInputDialog, "getText", prompt)
    monkeypatch.setattr(dialog, "_start_connection_test", dispatches.append)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text, *args:
                        warnings.append(text) or QMessageBox.StandardButton.Ok)
    return prompts, dispatches, warnings


def test_general_port_is_plain_edit_without_native_spin_chrome(qtbot):
    dialog = _dialog(qtbot)
    field = dialog.port_spin
    assert isinstance(field, QLineEdit)
    assert not isinstance(field, QAbstractSpinBox)
    assert field.text() == "1521" and field.value() == 1521


def test_general_port_arrow_keys_do_not_step_the_port(qtbot):
    dialog = _dialog(qtbot)
    field = dialog.port_spin
    field.setFocus()
    QTest.keyClick(field, Qt.Key.Key_Up)
    assert field.text() == "1521" and field.value() == 1521
    QTest.keyClick(field, Qt.Key.Key_Down)
    assert field.text() == "1521" and field.value() == 1521
    assert dialog._collect_config().port == 1521


@pytest.mark.parametrize("port", [1, 1521, 65535])
def test_general_port_compatibility_set_value_preserves_valid_boundaries(qtbot, port):
    dialog = _dialog(qtbot)
    dialog.port_spin.setValue(port)
    assert dialog.port_spin.value() == port
    assert _port_editor(dialog).text() == str(port)
    assert dialog._collect_config().port == port
    assert dialog._collect_config(testing=True).port == port


@pytest.mark.parametrize("text", [
    "", "0", "65536", "1000000", "1" * 128, "-1", "１２３", "١٥٢١", " 1521", "1521 ",
    "\t1521", "abc", "generated-sensitive-port-marker",
], ids=["empty", "zero", "above-range", "million", "long-digits", "negative", "fullwidth-digits",
        "arabic-digits", "leading-space", "trailing-space", "tab",
        "letters", "sensitive-invalid-input"])
def test_programmatic_invalid_port_cannot_save_prompt_or_dispatch(qtbot, monkeypatch, text):
    dialog = _dialog(qtbot)
    prompts, dispatches, warnings = _block_external_work(monkeypatch, dialog)
    edit = _port_editor(dialog)
    edit.setText(text)  # Programmatic setText bypasses interactive validators.
    assert edit.text() == text
    for testing in (False, True):
        with pytest.raises(ValueError) as caught:
            dialog._collect_config(testing=testing)
        message = str(caught.value)
        assert 0 < len(message) <= 160
        assert "generated-sensitive-port-marker" not in message
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    qtbot.waitUntil(lambda: not save.isEnabled() and not dialog.test_button.isEnabled())
    # A disabled button is insufficient: callers can invoke these methods.
    dialog.accept()
    dialog.test_connection()
    assert dialog.get_config() is None and dialog.isVisible()
    assert not dialog._testing and dialog._test_pool.activeThreadCount() == 0
    assert not prompts and not dispatches
    assert warnings and all("generated-sensitive-port-marker" not in item for item in warnings)
    assert edit.text() == text  # No coercion to the original/default port.
    edit.setText("1521")
    qtbot.waitUntil(lambda: save.isEnabled() and dialog.test_button.isEnabled())
    assert dialog._collect_config().port == 1521


@pytest.mark.parametrize("port", [0, 65536, 1000000])
def test_invalid_compatibility_set_value_does_not_clamp_or_use_default(qtbot, port):
    dialog = _dialog(qtbot)
    dialog.port_spin.setValue(port)
    assert _port_editor(dialog).text() == str(port)
    with pytest.raises(ValueError):
        dialog.port_spin.value()
    with pytest.raises(ValueError):
        dialog._collect_config()


def test_stored_zero_port_is_retained_as_invalid_instead_of_defaulting(qtbot):
    dialog = _dialog(qtbot, _profile(port=0))
    assert _port_editor(dialog).text() == "0"
    with pytest.raises(ValueError):
        dialog._collect_config()
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    assert not dialog.test_button.isEnabled()


def test_ssh_keeps_its_real_spin_control_and_step_behavior(qtbot):
    dialog = _dialog(qtbot)
    dialog.tabs.setCurrentWidget(dialog.ssh_tab)
    dialog.ssh_enabled_check.setChecked(True)
    spin = dialog.ssh_port_spin
    assert isinstance(spin, QAbstractSpinBox)
    spin.setValue(22)
    changes = QSignalSpy(spin.valueChanged)
    spin.stepUp()
    assert spin.value() == 23
    spin.stepDown()
    assert spin.value() == 22 and changes.count() == 2


def test_profile_controls_use_semantic_icons_and_keep_visibility_and_disabled_gates(qtbot):
    dialog = _dialog(qtbot)
    toggle, add = dialog.profile_toggle, dialog.new_profile_button
    assert toggle.text() == add.text() == ""
    assert not toggle.icon().isNull() and not add.icon().isNull()
    assert toggle.iconSize() == add.iconSize() == QSize(16, 16)
    assert toggle.accessibleName().strip() and add.accessibleName().strip()
    assert toggle.toolTip().strip() and add.toolTip().strip()
    assert toggle.isCheckable() and toggle.isChecked() and dialog.profile_pane.isVisible()
    assert not add.isEnabled()
    toggles = QSignalSpy(toggle.toggled)
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert not toggle.isChecked() and not dialog.profile_pane.isVisible()
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert toggle.isChecked() and dialog.profile_pane.isVisible()
    assert toggles.count() == 2 and not add.isEnabled()


def _image(icon, dpr, mode, state):
    pm = icon.pixmap(QSize(16, 16), dpr, mode, state)
    assert not pm.isNull()
    assert pm.size() == QSize(round(16 * dpr), round(16 * dpr))
    assert pm.devicePixelRatioF() == dpr
    return pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _ink(image):
    return [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 96]


@pytest.mark.parametrize("dpr", [1.0, 2.0, 3.0])
def test_profile_pane_icon_checked_blue_unchecked_gray_at_each_dpr(qtbot, dpr):
    dialog = _dialog(qtbot)
    icon = dialog.profile_toggle.icon()
    off = _image(icon, dpr, QIcon.Mode.Normal, QIcon.State.Off)
    on = _image(icon, dpr, QIcon.Mode.Normal, QIcon.State.On)
    off_colors, on_colors = _ink(off), _ink(on)
    assert off_colors and on_colors
    assert bytes(off.constBits()) != bytes(on.constBits())
    assert sum(c.blue() > c.red() + 20 and c.green() > c.red() + 10 for c in on_colors) > len(on_colors) / 2
    assert sum(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 20
               for c in off_colors) > len(off_colors) * .8


@pytest.mark.parametrize("dpr", [1.0, 2.0, 3.0])
def test_disabled_add_profile_icon_has_real_gray_ink_at_each_dpr(qtbot, dpr):
    dialog = _dialog(qtbot)
    button = dialog.new_profile_button
    assert not button.isEnabled()
    image = _image(button.icon(), dpr, QIcon.Mode.Disabled, QIcon.State.Off)
    colors = _ink(image)
    assert colors
    assert sum(max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) < 20
               for c in colors) > len(colors) * .8


@pytest.mark.parametrize("raw_port", [0, 65536, None, "generated-raw-port"],
                         ids=["zero", "above-range", "none", "raw-string"])
def test_tns_hidden_port_draft_retains_original_type_and_cannot_connect(qtbot, monkeypatch, raw_port):
    dialog = _dialog(qtbot, _profile(
        port=raw_port, oracle_connection_type="tns", oracle_tns_alias="GENERATED_ALIAS"))
    prompts, dispatches, warnings = _block_external_work(monkeypatch, dialog)
    assert dialog.port_spin.isHidden()
    config = dialog._collect_config()
    assert config.port == raw_port and type(config.port) is type(raw_port)
    assert json.loads(json.dumps(asdict(config)))["port"] == raw_port
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()
    dialog.test_connection()
    assert not prompts and not dispatches and not dialog._testing
    assert warnings and any("TNS" in warning for warning in warnings)
    assert dialog._test_pool.activeThreadCount() == 0
    dialog.accept()
    saved = dialog.get_config()
    assert saved is not None and saved.oracle_connection_type == "tns"
    assert saved.port == raw_port and type(saved.port) is type(raw_port)


@pytest.mark.parametrize("raw_port", [0, 65536, None, "generated-raw-port"],
                         ids=["zero", "above-range", "none", "raw-string"])
def test_invalid_basic_port_can_be_retained_in_tns_then_blocks_basic_again(qtbot, monkeypatch, raw_port):
    dialog = _dialog(qtbot)
    prompts, dispatches, warnings = _block_external_work(monkeypatch, dialog)
    dialog.port_spin.setValue(raw_port)
    original_text = _port_editor(dialog).text()
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    with pytest.raises(ValueError):
        dialog._collect_config()
    qtbot.waitUntil(lambda: not save.isEnabled() and not dialog.test_button.isEnabled())
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData("tns"))
    dialog.oracle_tns_combo.setEditText("GENERATED_ALIAS")
    assert dialog.port_spin.isHidden() and save.isEnabled()
    retained = dialog._collect_config()
    assert retained.port == raw_port and type(retained.port) is type(raw_port)
    dialog.test_connection()
    assert not prompts and not dispatches
    assert warnings and any("TNS" in warning for warning in warnings)
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData("basic"))
    assert _port_editor(dialog).text() == original_text
    with pytest.raises(ValueError):
        dialog._collect_config()
    qtbot.waitUntil(lambda: not save.isEnabled() and not dialog.test_button.isEnabled())
    assert dialog.get_config() is None and not dialog._testing


def _horizontal_ancestor_visibility(field, dialog):
    """Intersect actual ancestor clips in the child's coordinates, not hints."""
    left, right = 0, field.width()
    ancestor = field.parentWidget()
    clips = []
    while ancestor is not None:
        x = field.mapTo(ancestor, QPoint(0, 0)).x()
        left, right = max(left, -x), min(right, ancestor.width() - x)
        clips.append({"class": ancestor.metaObject().className(),
                      "name": ancestor.objectName(), "field_x": x,
                      "ancestor_width": ancestor.width(),
                      "visible_width": max(0, right - left)})
        if ancestor is dialog:
            break
        ancestor = ancestor.parentWidget()
    return max(0, right - left), clips


def _assert_full_horizontal_visibility(qtbot, field, dialog):
    assert field.isVisible()
    qtbot.waitUntil(lambda: field.width() > 0)
    width, clips = _horizontal_ancestor_visibility(field, dialog)
    assert width == field.width(), json.dumps({
        "field": field.objectName() or type(field).__name__,
        "field_width": field.width(), "visible_width": width, "clips": clips,
    }, ensure_ascii=True)


def _check_oracle_form_widths(qtbot, dialog):
    dialog.tabs.setCurrentWidget(dialog.general_tab)
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData("basic"))
    for field in (dialog.name_edit, dialog.host_edit, dialog.port_spin, dialog.oracle_service_edit):
        _assert_full_horizontal_visibility(qtbot, field, dialog)
    dialog.oracle_type_combo.setCurrentIndex(dialog.oracle_type_combo.findData("tns"))
    _assert_full_horizontal_visibility(qtbot, dialog.oracle_tns_combo, dialog)
    _assert_full_horizontal_visibility(qtbot, dialog.name_edit, dialog)
    dialog.tabs.setCurrentWidget(dialog.ssh_tab)
    dialog.ssh_enabled_check.setChecked(True)
    dialog.ssh_auth_combo.setCurrentIndex(dialog.ssh_auth_combo.findData("password+key"))
    for field in (dialog.ssh_host_edit, dialog.ssh_user_edit, dialog.ssh_port_spin,
                  dialog.ssh_auth_combo, dialog.ssh_password_edit, dialog.ssh_key_field,
                  dialog.ssh_key_passphrase_edit):
        _assert_full_horizontal_visibility(qtbot, field, dialog)


@pytest.mark.parametrize("stylesheet", [LIGHT_STYLESHEET, STYLESHEET], ids=["light", "dark"])
def test_oracle_fields_keep_full_horizontal_width_through_ancestor_clips_and_style_changes(qtbot, stylesheet):
    dialog = _dialog(qtbot)
    dialog.setStyleSheet(stylesheet)
    assert dialog.font().pixelSize() == 12
    _check_oracle_form_widths(qtbot, dialog)
    for name in QStyleFactory.keys():
        style = QStyleFactory.create(name)
        assert style is not None
        style.setParent(dialog)  # QWidget does not own explicitly selected styles.
        for widget in [dialog, *dialog.findChildren(QWidget)]:
            widget.setStyle(style)
        _check_oracle_form_widths(qtbot, dialog)
