"""Remaining glyph replacements and actual detail-popup ownership; no DB I/O."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt
from PySide6.QtWidgets import QPushButton, QGraphicsProxyWidget
from shiboken6 import isValid

from opennavicat.mongodb_pipeline import parse_explain_reply
from opennavicat.ui.mongo_pipeline import MongoExplainCanvas
from test_mongo_gridfs_ui import ui, page, ready


@pytest.fixture
def canvas(qtbot):
    view = MongoExplainCanvas()
    qtbot.addWidget(view)
    view.resize(1100, 650)
    view.set_explain(parse_explain_reply({"executionStats": {"executionTimeMillis": 9,
        "executionStages": {"stage": "GROUP", "nReturned": 1,
            "inputStage": {"stage": "COLLSCAN", "nReturned": 3,
                           "generatedExtra": "preserved"}}}, "ok": 1}))
    view.show()
    return view


def flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_explain_close_clears_owner_and_releases_real_proxy_and_widgets(qtbot, canvas):
    for index in range(8):
        canvas.select_index(index % 2)
        proxy = canvas._detail_proxy
        panel = proxy.widget()
        close = panel.findChild(QPushButton)
        detail = canvas.detail_view
        selected = canvas._selected
        assert detail.toPlainText() and proxy.scene() is canvas.scene
        qtbot.mouseClick(close, Qt.MouseButton.LeftButton)
        assert canvas._detail_proxy is None and canvas.detail_view is None
        assert canvas._selected is selected and selected.selected
        assert not any(isinstance(item, QGraphicsProxyWidget) for item in canvas.scene.items())
        flush_deletes()
        assert all(not isValid(widget) for widget in (proxy, panel, detail, close))


def test_explain_replacing_open_details_releases_previous_proxy(qtbot, canvas):
    canvas.select_index(0)
    old = canvas._detail_proxy
    old_detail = canvas.detail_view
    old_close = old.widget().findChild(QPushButton)
    canvas.select_index(1)
    assert canvas._detail_proxy is not old
    current = canvas._detail_proxy
    old_close.click()
    assert canvas._detail_proxy is current
    assert "generatedExtra" in canvas.detail_view.toPlainText()
    assert len([item for item in canvas.scene.items() if isinstance(item, QGraphicsProxyWidget)]) == 1
    flush_deletes()
    assert not isValid(old) and not isValid(old_detail)


def test_explain_dismiss_uses_drawn_icon_with_accessible_meaning(canvas):
    canvas.select_index(1)
    close = canvas._detail_proxy.widget().findChild(QPushButton)
    assert close.text() == "" and not close.icon().isNull()
    assert close.iconSize() == QSize(16, 16)
    assert close.accessibleName() == "关闭执行计划节点详情"
    assert close.toolTip() == close.accessibleName()
    assert close.contentsRect().width() >= 16 and close.contentsRect().height() >= 16


def test_gridfs_filter_add_remove_keep_rows_and_do_not_dispatch_io(qtbot, ui):
    host, session = ui
    widget = page(qtbot, ui)
    widget.filter_action.setChecked(True)
    button = widget.filter_add_button
    assert button.text() == "" and not button.icon().isNull()
    assert button.accessibleName() == "添加名称条件"
    assert button.iconSize() == QSize(16, 16)
    original = widget._filter_rows[0]
    original[3].setText("generated[filter]")
    calls = list(session.calls)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(widget._filter_rows) == 2 and widget._filter_rows[0] is original
    assert original[3].text() == "generated[filter]"
    added = widget._filter_rows[1]
    remove = added[-1]
    assert remove.text() == "" and not remove.icon().isNull()
    assert remove.accessibleName() == "删除名称条件"
    qtbot.mouseClick(remove, Qt.MouseButton.LeftButton)
    assert widget._filter_rows == [original] and session.calls == calls
    flush_deletes()
    assert all(not isValid(field) for field in added)
