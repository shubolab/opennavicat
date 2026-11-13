"""Chooser artwork routing and real delegate geometry, without DB/network I/O."""
import threading

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics, QIcon
from PySide6.QtTest import QSignalSpy
import pytest

from opennavicat.ui import connection_manager as manager
from test_connection_manager import labels, select_card, select_filter, show_manager


@pytest.fixture
def artwork_calls(monkeypatch):
    requested, paints, identities = [], [], {}
    real_icon, real_provider = manager.icon, manager.provider_icon
    real_paint = manager._ConnectionDelegate._paint_icon

    def driver(name, size=32):
        requested.append(('driver', name, size, None))
        result = real_icon(name, size)
        identities[result.cacheKey()] = ('driver', name)
        return result

    def provider(name, size=16, *, driver=None):
        requested.append(('provider', name, size, driver))
        result = real_provider(name, size, driver=driver)
        identities[result.cacheKey()] = ('provider', name)
        return result

    def observe_paint(painter, artwork, rect, mode):
        assert threading.current_thread() is threading.main_thread()
        paints.append((identities[artwork.cacheKey()], QRect(rect), mode, QIcon.State.Off))
        real_paint(painter, artwork, rect, mode)

    monkeypatch.setattr(manager, 'icon', driver)
    monkeypatch.setattr(manager, 'provider_icon', provider)
    monkeypatch.setattr(manager._ConnectionDelegate, '_paint_icon', staticmethod(observe_paint))
    return requested, paints


def test_driver_grid_and_compact_list_use_real_50_and_16_pixel_delegate_targets(qtbot, artwork_calls):
    requested, paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'mysql'})
    assert dialog.size() == QSize(900, 700)
    assert dialog.connection_list.gridSize() == QSize(126, 96)
    assert dialog.connection_list.iconSize() == QSize(50, 50)
    assert dialog.filter_tree.iconSize() == QSize(16, 16)
    dialog.grab()
    driver_paints = [rect for (family, _name), rect, _mode, _state in paints if family == 'driver']
    assert driver_paints and any(rect.size() == QSize(50, 50) for rect in driver_paints)
    mysql = dialog.connection_list.visualItemRect(dialog.connection_list.item(0))
    mysql_paint = next(rect for identity, rect, _mode, _state in paints if identity == ('driver', 'mysql'))
    assert mysql_paint.x() == mysql.x() + (mysql.width() - 50)//2
    assert mysql_paint.y() == mysql.y() + 4
    assert all(('driver', kind, 16, None) in requested for kind in manager.DRIVER_LABELS)
    assert all(('driver', kind, 50, None) in requested for kind in manager.DRIVER_LABELS)

    select_card(qtbot, dialog, 'MySQL')
    paints.clear()
    qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
    dialog.grab()
    assert dialog.connection_list.iconSize() == QSize(16, 16)
    assert dialog.connection_list.currentItem().text() == 'MySQL' and dialog.next_button.isEnabled()
    for index in range(dialog.connection_list.count()):
        row = dialog.connection_list.item(index)
        assert row.sizeHint().height() == 26
        assert dialog.connection_list.visualItemRect(row).height() == 26
    list_paints = [rect for (family, _name), rect, _mode, _state in paints if family == 'driver']
    assert list_paints and all(rect.size() == QSize(16, 16) for rect in list_paints)
    mysql = dialog.connection_list.visualItemRect(dialog.connection_list.item(0))
    mysql_paint = next(rect for identity, rect, _mode, _state in paints if identity == ('driver', 'mysql'))
    assert mysql_paint.y() == mysql.y() + (mysql.height() - 16)//2
    assert len(labels(dialog)) == 9


def test_provider_filter_badges_and_cloud_tiles_receive_different_brand_context(qtbot, artwork_calls):
    requested, paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'mysql', 'postgresql'})
    assert ('provider', 'provider_aws', 16, None) in requested
    assert not dialog.vendor_items['AWS'].icon(0).isNull()
    select_filter(qtbot, dialog, dialog.vendor_items['AWS'])
    select_card(qtbot, dialog, 'Amazon Aurora for PostgreSQL')
    assert ('provider', 'provider_aws', 50, 'postgresql') in requested
    assert ('provider', 'provider_aws', 50, 'mysql') in requested
    dialog.grab()
    assert any(family == 'provider' and rect.size() == QSize(50, 50)
               for (family, _name), rect, _mode, _state in paints)
    qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
    assert ('provider', 'provider_aws', 16, 'postgresql') in requested
    assert dialog.connection_list.currentItem().text() == 'Amazon Aurora for PostgreSQL'
    assert dialog.next_button.isEnabled()


def test_unobserved_provider_never_substitutes_driver_art_or_changes_backend_availability(qtbot, artwork_calls):
    requested, _paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'redis'})
    assert dialog.vendor_items['Redis Enterprise Cloud'].icon(0).isNull()
    assert '图标待对齐' in dialog.vendor_items['Redis Enterprise Cloud'].toolTip(0)
    requested.clear()
    select_filter(qtbot, dialog, dialog.vendor_items['Redis Enterprise Cloud'])
    row = select_card(qtbot, dialog, 'Redis Enterprise Cloud')
    assert row.icon().isNull() and row.data(Qt.ItemDataRole.UserRole + 2)
    assert '图标待对齐' in row.toolTip()
    assert '图标待对齐' in row.data(Qt.ItemDataRole.AccessibleTextRole)
    assert not requested
    assert dialog.next_button.isEnabled()
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1 and spy.at(0)[0] == 'redis'
    assert spy.at(0)[1]['name'] == 'Redis Enterprise Cloud'


def test_unknown_driver_alias_keeps_provider_identity_without_fabricated_database_brand(qtbot, artwork_calls):
    requested, _paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'mysql'})
    select_filter(qtbot, dialog, dialog.vendor_items['Alibaba Cloud'])
    row = select_card(qtbot, dialog, 'PolarDB for Xscale')
    assert not dialog.next_button.isEnabled()
    assert '尚未确认' in dialog.detail_label.text()
    assert not row.icon().isNull()
    assert ('provider', 'provider_alibaba', 50, None) in requested
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseDClick(dialog.connection_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=dialog.connection_list.visualItemRect(row).center())
    assert spy.count() == 0 and dialog.isVisible()


def test_long_cloud_names_have_real_multiline_height_without_shrinking_font(qtbot):
    dialog = show_manager(qtbot, supported_kinds={'mysql'})
    select_filter(qtbot, dialog, dialog.vendor_items['Alibaba Cloud'])
    row = select_card(qtbot, dialog, 'ApsaraDB for OceanBase (MySQL Mode)')
    delegate = dialog.connection_list.itemDelegate()
    font = delegate.label_font(dialog.connection_list.font())
    assert font.pixelSize() == 14
    natural = QFontMetrics(font).boundingRect(QRect(0, 0, 114, 4096), int(Qt.TextFlag.TextWordWrap), row.text())
    actual = dialog.connection_list.visualItemRect(row)
    assert actual.width() == 126
    assert actual.height() >= 4 + 50 + 5 + natural.height() + 3
    assert row.sizeHint().height() == actual.height()
    assert row.text() in row.toolTip()
    assert not row.data(Qt.ItemDataRole.UserRole + 2)


def test_delegate_forwards_selection_and_disabled_modes_and_keeps_icon_only_toggles_accessible(qtbot, artwork_calls):
    _requested, paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'mysql'})
    assert dialog.grid_button.text() == dialog.list_button.text() == ''
    assert dialog.grid_button.accessibleName() == '图标视图'
    assert dialog.list_button.accessibleName() == '列表视图'
    assert not dialog.grid_button.icon().isNull() and not dialog.list_button.icon().isNull()
    select_card(qtbot, dialog, 'MySQL')
    paints.clear(); dialog.grab()
    assert any(identity == ('driver', 'mysql') and mode == QIcon.Mode.Selected
               for identity, _rect, mode, _state in paints)
    dialog.connection_list.setEnabled(False)
    paints.clear(); dialog.grab()
    assert paints and all(mode == QIcon.Mode.Disabled for _identity, _rect, mode, _state in paints)
