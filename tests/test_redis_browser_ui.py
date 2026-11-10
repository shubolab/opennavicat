"""Qt byte fidelity and serial-worker UI against owned disposable Redis."""
from __future__ import annotations

from dataclasses import replace
import threading

import pytest
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from opennavicat.redis_session import RedisSession
from opennavicat.tasks import Task
from opennavicat.ui.redis_browser import RedisBrowserPage, RedisBytesEditor, RedisKeyPage
from test_redis_session import redis_service


@pytest.mark.parametrize("value", [b"", b"plain", b"line\nnext", b"cr\r\nlf", b"\x00\xff", bytes(range(256)), "a\u2028b".encode()])
def test_byte_editor_round_trips_original_and_never_normalizes_newlines(qtbot, value):
    editor = RedisBytesEditor(); qtbot.addWidget(editor); editor.show(); editor.set_bytes(value)
    assert editor.bytes_value() == value
    editor.mode.setCurrentText("HEX")
    assert editor.bytes_value() == value
    editor.mode.setCurrentText("UTF-8")
    assert editor.bytes_value() == value
    if b"\r" in value or b"\xff" in value or b"\xe2\x80\xa8" in value:
        assert editor.mode.currentText() == "HEX"


def test_invalid_hex_is_preserved_as_a_draft(qtbot):
    editor = RedisBytesEditor(); qtbot.addWidget(editor); editor.set_bytes(b"\xff")
    editor.editor.setPlainText("f")
    with pytest.raises(ValueError):editor.bytes_value()
    editor.mode.setCurrentText("UTF-8")
    assert editor.mode.currentText() == "HEX" and editor.editor.toPlainText() == "f"


class SerialWindow(QWidget):
    def __init__(self, config):
        super().__init__()
        self.sessions = {config.id: RedisSession(replace(config))}
        self.configs = {config.id: config}
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(1)
        self.tasks, self.io_threads, self.pages = set(), [], []

    def submit(self, function, success=None, failure=None, label="", **kwargs):
        def work():
            self.io_threads.append(threading.get_ident())
            return function()
        task = Task(work); self.tasks.add(task)
        if success:task.signals.succeeded.connect(success)
        if failure:task.signals.failed.connect(failure)
        task.signals.finished.connect(lambda: self.tasks.discard(task))
        self.pool.start(task)

    def when_idle(self, callback):
        if self.tasks:QTimer.singleShot(5, self, lambda:self.when_idle(callback))
        else:callback()


@pytest.fixture
def live(qtbot, redis_service):
    admin = RedisSession(replace(redis_service)); admin.connect(); admin._raw("FLUSHALL")
    window = SerialWindow(redis_service); qtbot.addWidget(window); window.show()
    yield window, admin, redis_service.id
    qtbot.waitUntil(lambda:not window.tasks, timeout=10000)
    for page in window.pages:
        if isinstance(page, RedisBrowserPage) and page.key_page:
            page.key_page._pending = None; page.key_page.value_page = None
        if isinstance(page, RedisKeyPage):page._pending = None; page.value_page = None
        page._closed = True; page.deleteLater()
    window.pool.waitForDone(3000)
    for session in window.sessions.values():session.close()
    admin.close()


def ready(qtbot, window, page):
    qtbot.waitUntil(lambda:not page.loading and not window.tasks and
                   (page.value_page is not None if isinstance(page, RedisKeyPage) else page.status.text() != "尚未扫描"), timeout=10000)


def key_page(qtbot, live, key):
    window, _, connection_id = live
    page = RedisKeyPage(window, connection_id, "0", key)
    window.pages.append(page); page.resize(750, 650); page.show(); ready(qtbot, window, page)
    return page


def test_real_string_hex_save_cancel_refresh_and_failure_preserve_draft(qtbot, live, monkeypatch):
    window, admin, _ = live
    admin.set_string("0", b"key\xff", b"line\r\n\x00\xff")
    page = key_page(qtbot, live, b"key\xff")
    assert page.value_editor.mode.currentText() == "HEX" and not page.dirty
    page.value_editor.editor.setPlainText("6e 65 77 00 ff")
    assert page.dirty and page.save_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Cancel)
    page.refresh()
    assert page.dirty and page.value_editor.bytes_value() == b"new\x00\xff"
    assert admin.read_key("0", b"key\xff").value == b"line\r\n\x00\xff"
    page.request_save(); ready(qtbot, window, page)
    qtbot.waitUntil(lambda:not page.dirty and page.value_page.value == b"new\x00\xff")
    assert admin.read_key("0", b"key\xff").value == b"new\x00\xff"
    page.value_editor.editor.setPlainText("61 66 74 65 72")
    admin.set_string("0", b"key\xff", b"concurrent")
    page.request_save(); qtbot.waitUntil(lambda:not page.loading and not window.tasks)
    assert page.dirty and page.value_editor.bytes_value() == b"after"
    assert "changed" in page.message.text() and admin.read_key("0", b"key\xff").value == b"concurrent"
    assert window.io_threads and all(thread != threading.get_ident() for thread in window.io_threads)


def test_large_string_is_partial_readonly_and_native_offset_paging(qtbot, live):
    window, admin, _ = live
    data = bytes(range(256)) * 400
    admin.set_string("0", "large", data)
    page = key_page(qtbot, live, b"large")
    assert page.value_page.total == len(data) and page.value_page.truncated
    assert page.value_editor.bytes_value() == data[:65536]
    assert page.value_editor.editor.isReadOnly() and not page.save_button.isEnabled()
    page.next(); ready(qtbot, window, page)
    qtbot.waitUntil(lambda:page.offset == 65536 and page.value_page.next_offset == len(data))
    assert page.value_editor.bytes_value() == data[65536:]
    assert page.value_editor.editor.isReadOnly() and not page.next_button.isEnabled()
    assert admin.read_key("0", "large", max_bytes=len(data)).value == data


@pytest.mark.parametrize("kind,initial,new_value", [("hash",[(b"field",b"before\xff")],b"after\x00"),
    ("list",[b"before\xff"],b"after\x00"),("set",[b"before\xff"],b"after\x00"),
    ("zset",[(b"before\xff",b"1.5")],b"after\x00")])
def test_real_collection_staged_edit_save_then_delete(qtbot,live,kind,initial,new_value):
    window,admin,_=live
    admin.create_key("0",kind,kind,initial)
    page=key_page(qtbot,live,kind.encode())
    page.elements.setCurrentCell(0,0)
    dialog=page.edit_element(); assert dialog is not None
    dialog.fields.value.set_bytes(new_value)
    if kind=="zset":dialog.fields.score.setText("2.25")
    dialog.accept()
    assert page.dirty and page._pending is not None
    assert admin.read_key("0",kind).rows==page.value_page.rows
    page.request_save();qtbot.waitUntil(lambda:not page.loading and not window.tasks and not page.dirty)
    # A queued refresh may start on the next GUI turn.
    qtbot.waitUntil(lambda:page.value_page.rows != ([initial[0]] if kind in {"hash","zset"} else [[0,initial[0]]] if kind=="list" else [[initial[0]]]))
    loaded=admin.read_key("0",kind)
    if kind=="hash":assert loaded.rows==[[b"field",new_value]]
    elif kind=="list":assert loaded.rows==[[0,new_value]]
    elif kind=="set":assert loaded.rows==[[new_value]]
    else:assert loaded.rows==[[new_value,b"2.25"]]
    page.elements.setCurrentCell(0,0);page.delete_element();assert page.dirty
    page.discard_changes();assert not page.dirty and admin.read_key("0",kind).type==kind


def test_real_browser_scan_bytes_embedded_editor_and_new_key_cancel_collision(qtbot,live):
    window,admin,connection_id=live
    admin.set_string("0",b"key\xff",b"value\x00")
    browser=RedisBrowserPage(window,connection_id,"0");window.pages.append(browser);browser.resize(1100,700);browser.show();ready(qtbot,window,browser)
    assert browser.keys.rowCount()==1
    assert browser.splitter.orientation()==Qt.Orientation.Vertical
    assert browser.keys.item(0,0).data(Qt.ItemDataRole.UserRole)==b"key\xff"
    browser.keys.setCurrentCell(0,0);browser.open_selected()
    qtbot.waitUntil(lambda:browser.key_page is not None and browser.key_page.value_page is not None)
    assert browser.key_page.key==b"key\xff" and browser.key_page.parent() is browser.editor_holder
    dialog=browser.new_key("string");dialog.key_editor.set_bytes(b"cancelled");dialog.fields.value.set_bytes(b"never written");dialog.reject()
    assert admin.read_key("0",b"cancelled").type=="none"
    dialog=browser.new_key("string");dialog.key_editor.set_bytes(b"key\xff");dialog.fields.value.set_bytes(b"wrong")
    dialog.accept();qtbot.waitUntil(lambda:not dialog.busy and not window.tasks)
    assert dialog.isVisible() and dialog.fields.value.bytes_value()==b"wrong"
    assert admin.read_key("0",b"key\xff").value==b"value\x00"
    dialog.key_editor.set_bytes(b"created\x00");dialog.accept()
    qtbot.waitUntil(lambda:browser._new_dialog is None and browser.key_page.key==b"created\x00" and not window.tasks)
    assert admin.read_key("0",b"created\x00").value==b"wrong"


def test_stream_duplicates_are_preserved_and_readonly_never_enables_writes(qtbot,live):
    window,admin,connection_id=live
    admin.create_key("0","stream","stream",[(b"duplicate",b"one"),(b"duplicate",b"two\xff")])
    page=key_page(qtbot,live,b"stream")
    fields=page.elements.item(0,0).data(Qt.ItemDataRole.UserRole)[1]
    assert fields==[b"duplicate",b"one",b"duplicate",b"two\xff"]
    assert not page.edit_action.isEnabled()
    window.sessions[connection_id].config.read_only=True
    page._update_controls()
    assert not page.add_action.isEnabled() and not page.delete_action.isEnabled() and not page.ttl_action.isEnabled()
    assert page.add_element() is None and not page.dirty


def test_delete_and_ttl_cancellation_never_submit_writes(qtbot,live,monkeypatch):
    window,admin,_=live
    admin.set_string("0","retained",b"value")
    page=key_page(qtbot,live,b"retained")
    before=len(window.io_threads)
    monkeypatch.setattr(QMessageBox,"question",lambda *args,**kwargs:QMessageBox.StandardButton.Cancel)
    page.delete_key()
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog,"getText",lambda *args,**kwargs:("1000",False))
    page.set_ttl()
    assert len(window.io_threads)==before and admin.read_key("0","retained").value==b"value"
    assert admin.read_key("0","retained").pttl==-1


@pytest.mark.parametrize("kind", ["string","hash","list","set","zset","stream"])
def test_real_new_key_dialog_all_types_and_stream_repeated_fields(qtbot,live,kind):
    window,admin,connection_id=live
    browser=RedisBrowserPage(window,connection_id,"0");window.pages.append(browser);browser.show();ready(qtbot,window,browser)
    dialog=browser.new_key(kind);dialog.key_editor.set_bytes(kind.encode()+b"\xff")
    if kind=="stream":
        pairs=dialog.fields.pairs
        pairs.table.cellWidget(0,0).set_bytes(b"same");pairs.table.cellWidget(0,1).set_bytes(b"first\x00")
        pairs.add_pair(b"same",b"second\xff")
        assert dialog.fields.entry_id.isReadOnly()
    else:
        dialog.fields.value.set_bytes(b"value\x00\xff")
        if kind=="hash":dialog.fields.field.set_bytes(b"field\xff")
        if kind=="zset":dialog.fields.score.setText("1.25")
    dialog.accept()
    qtbot.waitUntil(lambda:browser._new_dialog is None and browser.key_page is not None and browser.key_page.value_page is not None and not window.tasks,timeout=10000)
    value=admin.read_key("0",kind.encode()+b"\xff")
    assert value.type==kind
    if kind=="stream":assert value.rows[0][1]==[b"same",b"first\x00",b"same",b"second\xff"]
    assert all(thread!=threading.get_ident() for thread in window.io_threads)


def test_close_save_retries_only_after_success_and_browser_refresh_preserves_editor_draft(qtbot,live,monkeypatch):
    window,admin,connection_id=live
    admin.set_string("0","saved",b"original")
    page=key_page(qtbot,live,b"saved");page.value_editor.editor.setPlainText("applied before close")
    monkeypatch.setattr(QMessageBox,"question",lambda *args,**kwargs:QMessageBox.StandardButton.Save)
    page.close()
    qtbot.waitUntil(lambda:page._closed and not window.tasks,timeout=10000)
    assert admin.read_key("0","saved").value==b"applied before close"
    browser=RedisBrowserPage(window,connection_id,"0");window.pages.append(browser);browser.show();ready(qtbot,window,browser)
    embedded=browser.show_key(b"saved");qtbot.waitUntil(lambda:embedded.value_page is not None and not window.tasks)
    embedded.value_editor.editor.setPlainText("preserved draft")
    monkeypatch.setattr(QMessageBox,"question",lambda *args,**kwargs:pytest.fail("Refreshing the key list must not prompt or discard the embedded draft"))
    browser.refresh();ready(qtbot,window,browser)
    assert embedded.dirty and embedded.value_editor.bytes_value()==b"preserved draft"
    assert admin.read_key("0","saved").value==b"applied before close"
