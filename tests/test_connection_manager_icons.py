"""Chooser artwork routing and real delegate geometry, without DB/network I/O."""
import threading

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFontDatabase, QFontMetrics, QIcon, QImage
from PySide6.QtTest import QSignalSpy
import pytest

from opennavicat.ui import connection_manager as manager
from opennavicat.ui import icons
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


@pytest.mark.parametrize('label_font', ['default', 'fixed'])
def test_driver_grid_and_compact_list_use_real_50_and_16_pixel_delegate_targets(qtbot, artwork_calls, label_font):
    requested, paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={'mysql'})
    if label_font == 'fixed':
        dialog.connection_list.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(dialog.grid_button, Qt.MouseButton.LeftButton)
    assert dialog.size() == QSize(900, 700)
    grid_size = dialog.connection_list.gridSize()
    assert grid_size.width() == 126 and grid_size.height() >= 96
    # The two direct aliases also use this grid. Their complete names can wrap
    # onto another line with the platform font; retain the 50px icon and fit
    # the actual label instead of forcing every platform to the minimum row.
    font = dialog.connection_list.itemDelegate().label_font(dialog.connection_list.font())
    assert font.pixelSize() == 14
    metrics = QFontMetrics(font)
    for row in range(dialog.connection_list.count()):
        item = dialog.connection_list.item(row)
        bounds = dialog.connection_list.visualItemRect(item)
        assert bounds.size() == grid_size
        label_area = bounds.adjusted(6, 4 + 50 + 5, -6, -3)
        natural = metrics.boundingRect(QRect(0, 0, label_area.width(), 4096),
                                       int(Qt.TextFlag.TextWordWrap), item.text())
        assert natural.height() <= label_area.height(), item.text()
    assert dialog.connection_list.iconSize() == QSize(50, 50)
    assert dialog.filter_tree.iconSize() == QSize(16, 16)
    paints.clear()
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
    assert len(labels(dialog)) == 11


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


def _render_bytes(artwork, size):
    assert not artwork.isNull() and artwork.cacheKey()
    image = artwork.pixmap(QSize(size, size), 2.0, QIcon.Mode.Normal,
                           QIcon.State.Off).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.size() == QSize(size * 2, size * 2)
    return bytes(image.constBits())


@pytest.mark.parametrize('label, kind', [
    ('MongoDB Atlas', 'mongodb'), ('Redis Enterprise Cloud', 'redis'),
])
def test_direct_alias_uses_database_brand_in_grid_and_list_and_preserves_availability(qtbot, artwork_calls, label, kind):
    requested, _paints = artwork_calls
    dialog = show_manager(qtbot, supported_kinds={kind})
    assert label in labels(dialog) and label not in dialog.vendor_items
    row = select_card(qtbot, dialog, label)
    assert row.icon().cacheKey() == row.data(Qt.ItemDataRole.DecorationRole).cacheKey()
    assert _render_bytes(row.icon(), 50) == _render_bytes(icons.icon(kind, 50), 50)
    assert ('driver', kind, 50, None) in requested
    assert not row.data(Qt.ItemDataRole.UserRole + 2)
    assert '图标待对齐' not in row.toolTip()
    assert '图标待对齐' not in row.data(Qt.ItemDataRole.AccessibleTextRole)
    assert not any(family == 'provider' and name in {'provider_mongodb_atlas', 'redis_enterprise'}
                   for family, name, _size, _driver in requested)
    qtbot.mouseClick(dialog.list_button, Qt.MouseButton.LeftButton)
    row = dialog.connection_list.currentItem()
    assert row.text() == label and row.icon().cacheKey()
    assert _render_bytes(row.icon(), 16) == _render_bytes(icons.icon(kind, 16), 16)
    assert ('driver', kind, 16, None) in requested
    dialog.set_supported_kinds(set())
    assert dialog.connection_list.currentItem().text() == label
    assert not dialog.next_button.isEnabled()
    dialog.set_supported_kinds({kind})
    assert dialog.next_button.isEnabled()
    spy = QSignalSpy(dialog.selectionRequested)
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    assert spy.count() == 1 and spy.at(0)[0] == kind
    assert spy.at(0)[1]['driver'] == kind and spy.at(0)[1]['name'] == label


@pytest.mark.parametrize('label, kind', [
    ('MongoDB Atlas', 'mongodb'), ('Redis Enterprise Cloud', 'redis'),
])
def test_direct_alias_remains_discoverable_in_driver_filter_and_search_has_no_provider_entry(qtbot, label, kind):
    dialog = show_manager(qtbot, supported_kinds=set())
    assert label in labels(dialog)  # Availability never hides the entry.
    select_filter(qtbot, dialog, dialog.driver_items[kind])
    assert labels(dialog) == [manager.DRIVER_LABELS[kind], label]
    select_filter(qtbot, dialog, dialog.all_item)
    dialog.search_edit.setText(label)
    assert labels(dialog) == [label]
    assert label not in dialog.vendor_items and len(dialog.vendor_items) == 12
    row = select_card(qtbot, dialog, label)
    assert not row.icon().isNull() and not dialog.next_button.isEnabled()
    dialog.search_edit.clear()
    assert label in labels(dialog)


def test_catalog_retains_fourteen_alias_groups_but_only_twelve_provider_filters(qtbot):
    dialog = show_manager(qtbot)
    assert len(manager.VENDOR_ALIASES) == 14
    assert manager.DIRECT_DRIVER_ALIASES == {'MongoDB Atlas': 'mongodb', 'Redis Enterprise Cloud': 'redis'}
    assert len(dialog.vendor_items) == 12
    assert set(dialog.vendor_items) == set(manager.VENDOR_ALIASES) - set(manager.DIRECT_DRIVER_ALIASES)
    assert labels(dialog) == [*manager.DRIVER_LABELS.values(), 'MongoDB Atlas', 'Redis Enterprise Cloud']
    actual = {(p.label, p.kind, p.vendor) for p in dialog._catalog}
    assert actual == ({(label, kind, '') for kind, label in manager.DRIVER_LABELS.items()}
                      | {(label, kind, vendor) for vendor, rows in manager.VENDOR_ALIASES.items()
                         for label, kind in rows})


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
