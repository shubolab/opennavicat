from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from opennavicat.database import QueryResult
from opennavicat.ui.grid import DataGrid,DataModel,RowSelectorHeader
from opennavicat.ui.theme import apply_theme


def make_grid(qtbot):
    apply_theme(QApplication.instance(),'dark')
    grid=DataGrid()
    grid.setModel(DataModel(QueryResult(['id','name'],[[1,'first'],[2,'other'],[3,'third']])))
    qtbot.addWidget(grid);grid.resize(500,250);grid.show();qtbot.wait(10)
    return grid


def test_real_header_checkbox_toggles_rows_and_ctrl_keeps_multiple(qtbot):
    grid=make_grid(qtbot);header=grid.verticalHeader()
    assert isinstance(header,RowSelectorHeader) and header.width()==20
    qtbot.mouseClick(header.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(10,10))
    assert header.row_checked(0) and len(grid.selectedIndexes())==2
    qtbot.mouseClick(header.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,pos=QPoint(10,50))
    assert header.row_checked(0) and header.row_checked(2) and len(grid.selectedIndexes())==4
    qtbot.mouseClick(header.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(10,10))
    assert not header.row_checked(0) and header.row_checked(2)
    qtbot.keyClick(grid,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text()=='3\tthird\n'
    assert grid.model().rowCount()==3 and grid.model().columnCount()==2


def test_ordinary_cell_selection_and_copy_do_not_include_row_selector(qtbot):
    grid=make_grid(qtbot)
    index=grid.model().index(1,1)
    qtbot.mouseClick(grid.viewport(),Qt.MouseButton.LeftButton,pos=grid.visualRect(index).center())
    qtbot.keyClick(grid,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text()=='other\n'
    assert not grid.verticalHeader().row_checked(1)
    grid.set_row_selectors_enabled(False)
    assert not isinstance(grid.verticalHeader(),RowSelectorHeader)
    grid.set_row_selectors_enabled(True)
    assert isinstance(grid.verticalHeader(),RowSelectorHeader)
    assert grid.model().rowCount()==3 and grid.model().columnCount()==2


def test_blank_zebra_has_no_indexes_or_fake_values(qtbot):
    grid=make_grid(qtbot)
    assert not grid.indexAt(QPoint(5,180)).isValid()
    assert grid.model().rowCount()==3
    image=grid.viewport().grab().toImage()
    # Below real rows: alternating painted backgrounds, not records.
    assert image.pixelColor(250,70).name()=='#252525'
    assert image.pixelColor(250,90).name()=='#1e1e1e'


def test_default_columns_fit_email_dates_and_keep_long_payload_bounded(qtbot):
    grid = DataGrid()
    grid.setModel(DataModel(QueryResult(['id', 'email', 'joined_at', 'payload'],
                                      [[1, 'olivia@example.com', '2026-11-10 00:59:41', 'x' * 2000]])))
    qtbot.addWidget(grid)
    grid.show()
    assert grid.columnWidth(0) == 74
    assert grid.columnWidth(1) >= grid.fontMetrics().horizontalAdvance('olivia@example.com') + 6
    assert grid.columnWidth(2) >= grid.fontMetrics().horizontalAdvance('2026-11-10 00:59:41') + 6
    assert grid.columnWidth(3) == 320
    assert grid.horizontalHeader().defaultAlignment() & Qt.AlignmentFlag.AlignLeft
