"""Badge and layout state through actual generated SQLite window workflows.

Pixel differences here protect state propagation, not reference visual parity.
"""
from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt
from PySide6.QtWidgets import QAbstractButton
from PySide6.QtGui import QIcon

from test_ui import idle, open_sample, run_query, window


def pixels(icon, size=16):
    image = icon.pixmap(QSize(size, size), 2.0, QIcon.Mode.Normal,
                        QIcon.State.Off).toImage()
    return image.size(), bytes(image.constBits())


def test_alias_and_query_badges_follow_real_connect_disconnect_reconnect(qtbot, window):
    page = open_sample(qtbot, window)
    identifier = page.connection_id
    window.store.state["navicat_imports"][identifier] = {
        "group_paths": [["Generated group A"], ["Generated group B"]]}
    window.populate_connections()
    idle(qtbot, window)
    window.refresh_actions()
    aliases = window.connection_items(identifier)
    assert len(aliases) == 2
    connected = pixels(aliases[0].icon(0))
    assert all(pixels(item.icon(0)) == connected for item in aliases)
    picker_row = page.connection_picker.findData(identifier)
    picker_connected = pixels(page.connection_picker.itemIcon(picker_row), 14)
    schema_connected = pixels(page.schema_picker.itemIcon(page.schema_picker.currentIndex()), 14)
    sql = page.editor.toPlainText()
    context = (page.connection_picker.currentData(), page.schema_picker.currentText())

    assert window.disconnect(identifier)
    assert identifier not in window.sessions
    assert all(pixels(item.icon(0)) != connected for item in aliases)
    assert pixels(page.connection_picker.itemIcon(picker_row), 14) != picker_connected
    assert pixels(page.schema_picker.itemIcon(page.schema_picker.currentIndex()), 14) != schema_connected
    assert (page.connection_picker.currentData(), page.schema_picker.currentText()) == context
    assert page.editor.toPlainText() == sql

    window.connect_id(identifier)
    idle(qtbot, window)
    window.refresh_actions()
    assert identifier in window.sessions
    assert all(pixels(item.icon(0)) == connected for item in aliases)
    assert pixels(page.connection_picker.itemIcon(picker_row), 14) == picker_connected
    assert page.editor.toPlainText() == sql


def test_layout_icons_follow_clicks_without_losing_sql_or_results(qtbot, window):
    page = open_sample(qtbot, window)
    page.editor.setPlainText("SELECT 731 AS generated_value;")
    result = run_query(qtbot, window, page)
    assert result.rows == [[731]]
    grid = page.results.currentWidget()
    assert page.results_below_button.isChecked()
    assert not page.results_beside_button.isChecked()
    below_on = pixels(page.results_below_button.icon())
    for _ in range(2):
        qtbot.mouseClick(page.results_beside_button, Qt.MouseButton.LeftButton)
        assert page.split.orientation() == Qt.Orientation.Horizontal
        assert page.results_beside_button.isChecked()
        assert not page.results_below_button.isChecked()
        qtbot.mouseClick(page.results_below_button, Qt.MouseButton.LeftButton)
        assert page.split.orientation() == Qt.Orientation.Vertical
        assert page.results_below_button.isChecked()
        assert not page.results_beside_button.isChecked()
    assert pixels(page.results_below_button.icon()) == below_on
    assert page.results.currentWidget() is grid
    assert page.current_result() is result
    assert page.editor.toPlainText() == "SELECT 731 AS generated_value;"


def test_unknown_imported_family_remains_visible_without_a_fake_driver_badge(qtbot, window):
    from opennavicat.database import ConnectionConfig

    config = ConnectionConfig(id="generated-future-family", name="Generated future family",
                              driver="unknown", database="generated")
    window.configs[config.id] = config
    window.store.state["navicat_imports"][config.id] = {"blocked": True}
    window.populate_connections()
    window.refresh_actions()
    item = window.connection_item(config.id)
    assert item is not None and item.text(0) == config.name
    assert item.icon(0).isNull()
    assert "未实现驱动" in item.toolTip(0)
    assert config.id not in window.sessions


def test_close_glyph_tracks_its_page_when_tab_indices_shift(qtbot, window):
    page = open_sample(qtbot, window)
    second = window.new_query(connection_id=page.connection_id, sql="SELECT 732;")
    third = window.new_query(connection_id=page.connection_id, sql="SELECT 733;")
    bar = window.tabs.tabBar()
    def close_button(target):
        index = window.tabs.indexOf(target)
        return next(bar.tabButton(index, side) for side in
                    (bar.ButtonPosition.LeftSide, bar.ButtonPosition.RightSide)
                    if bar.tabButton(index, side) is not None)
    second_close, third_close = close_button(second), close_button(third)
    assert not second_close.icon().isNull()
    qtbot.mouseClick(second_close, Qt.MouseButton.LeftButton)
    assert window.tabs.indexOf(second) == -1
    assert window.tabs.indexOf(third) >= 0
    qtbot.mouseClick(third_close, Qt.MouseButton.LeftButton)
    assert window.tabs.indexOf(third) == -1
    assert window.tabs.indexOf(page) == 1
    assert window.tabs.widget(0) is window.object_page


def test_replacing_native_close_glyph_releases_hidden_buttons(qtbot, window):
    page = open_sample(qtbot, window)
    bar = window.tabs.tabBar()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    baseline = len(bar.findChildren(QAbstractButton))
    for number in range(30):
        generated = window.new_query(connection_id=page.connection_id,
                                     sql=f"SELECT {number};")
        window.close_tab(window.tabs.indexOf(generated))
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(bar.findChildren(QAbstractButton)) == baseline
    assert window.tabs.count() == 2
