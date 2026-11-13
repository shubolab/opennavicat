"""GridFS worker boundaries, transfer lifecycle and owned native-service flows."""
from copy import deepcopy
from datetime import datetime, timezone
import gc
from pathlib import Path
import json
import hashlib
import os
import re
import subprocess
import threading
from types import SimpleNamespace
import uuid
import weakref

from bson import BSON, Binary, ObjectId
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget
import pytest
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_session import MongoDocument, MongoPage, MongoSession
from opennavicat.tasks import Task
from opennavicat.ui.mongo_gridfs import MongoGridFSPage, _safe_filename


def file_document(name, data, identity=None, **metadata):
    value = {"_id": identity or ObjectId(), "filename": name, "length": len(data), "chunkSize": 255*1024,
             "uploadDate": datetime(2026, 1, 1, tzinfo=timezone.utc), **metadata}
    raw = BSON.encode(value)
    return MongoDocument(value, raw, raw, False, "Use the dedicated GridFS lifecycle")


class ControlledGridFS:
    def __init__(self):
        self.config = SimpleNamespace(read_only=False)
        self.files = [file_document("original.bin", b"\x00\xff\r\n", metadata={"binary":Binary(b"\xff",128)}),
                      file_document("a[1].txt", b"owned")]
        self.data = {self.files[0].document["_id"]:b"\x00\xff\r\n", self.files[1].document["_id"]:b"owned"}
        self.calls = []
        self.threads = []
        self.entered = self.release = None
        self.cancelled = threading.Event()
        self.cancel_called = threading.Event()
        self.progress_size = None

    def io(self, operation, *args):
        self.calls.append((operation, args))
        self.threads.append(threading.get_ident())

    def transfer(self, operation, data, progress, cancelled=None):
        self.io(operation)
        total = self.progress_size or len(data)
        progress(total//2, total)
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(8), "Controlled transfer was not released"
        if self.cancelled.is_set() or (cancelled and cancelled()):
            raise DatabaseError("The transfer was cancelled")
        progress(total, total)

    def list_files(self, database, bucket, filter_doc=None, offset=0, limit=100):
        self.io("list", database, bucket, deepcopy(filter_doc), offset, limit)
        records = self.files
        conditions = filter_doc.get("$and", [filter_doc]) if filter_doc else []
        for condition in conditions:
            expression = condition.get("filename",{}).get("$regex", "")
            records = [item for item in records if re.search(expression,item.document["filename"])]
        return MongoPage(records[offset:offset+limit], offset, limit, offset+limit<len(records), len(records), next_offset=offset+min(limit,len(records)))

    def upload_file(self, database, bucket, source_path, filename=None, metadata=None, progress=None, cancelled=None):
        data = Path(source_path).read_bytes()
        self.transfer("upload", data, progress, cancelled)
        result = file_document(filename or Path(source_path).name, data)
        self.files.append(result)
        self.data[result.document["_id"]] = data
        return result

    def download_file(self, database, bucket, file_id, target_path, overwrite=False, expected=None, progress=None, cancelled=None):
        self.io("download-target", str(target_path), overwrite)
        current = next(item for item in self.files if item.document["_id"] == file_id)
        if expected is not None and current.raw_bson != expected.raw_bson:
            raise DatabaseError("The GridFS metadata changed")
        data = self.data[file_id]
        self.transfer("download", data, progress, cancelled)
        with Path(target_path).open("wb" if overwrite else "xb") as destination:
            destination.write(data)
        return len(data)

    def delete_file(self, database, bucket, expected):
        self.io("delete", database, bucket, expected)
        current = next(item for item in self.files if item.document["_id"] == expected.document["_id"])
        if current.raw_bson != expected.raw_bson:
            raise DatabaseError("The GridFS metadata changed")
        self.files.remove(current)
        self.data.pop(current.document["_id"])

    def cancel(self):
        self.io("cancel")
        self.cancelled.set()
        self.cancel_called.set()


class SerialHost(QWidget):
    def __init__(self, session):
        super().__init__()
        self.sessions = {"owned":session}
        self.tasks = set()
        self.errors = []
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.cancel_pool = QThreadPool(self)
        self.cancel_pool.setMaxThreadCount(1)
        self.pages = []

    def submit(self, work, success=None, failure=None, label="", pool=None, finished=None):
        task = Task(work)
        task.signals.setParent(self)
        self.tasks.add(task)
        if success:
            task.signals.succeeded.connect(success)
        task.signals.failed.connect(failure or self.errors.append)
        def done():
            self.tasks.discard(task)
            if finished:
                finished()
        task.signals.finished.connect(done)
        task.signals.finished.connect(task.signals.deleteLater)
        (pool or self.pool).start(task)

    def when_idle(self, callback):
        if self.tasks:
            QTimer.singleShot(5, self, lambda:self.when_idle(callback))
        else:
            callback()


def ready(qtbot, host, page):
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount() and not page.loading
                   and (page.page is not None or bool(page.message.text())), timeout=15000)
    assert page.page is not None, page.message.text()


def register_host(qtbot,host,session,*,close_session=False):
    """All transfer/control workers finish before Qt may delete their pools."""
    state={'done':False}
    def shutdown(widget):
        if state['done']:return
        assert widget is host and isValid(host) and isValid(host.pool) and isValid(host.cancel_pool), 'Host was deleted before transfer I/O was verified complete'
        release=getattr(session,'release',None)
        if release is not None:release.set()
        def idle():
            return not host.tasks and not host.pool.activeThreadCount() and not host.cancel_pool.activeThreadCount()
        qtbot.waitUntil(idle,timeout=20000)
        for page in host.pages:
            if isValid(page):page._closed=True;page.close();page.deleteLater()
        if close_session:
            host.submit(session.close)
            qtbot.waitUntil(idle,timeout=15000)
        assert not host.errors
        host.close()
        state['done']=True
    qtbot.addWidget(host,before_close_func=shutdown)
    return lambda:shutdown(host)


@pytest.fixture
def ui(qtbot, monkeypatch):
    session = ControlledGridFS()
    host = SerialHost(session)
    shutdown=register_host(qtbot,host,session)
    host.show()
    monkeypatch.setattr(QMessageBox, "question", lambda *_a,**_k:QMessageBox.StandardButton.Cancel)
    try:yield host,session
    finally:shutdown()


def page(qtbot, ui):
    host, _ = ui
    widget = MongoGridFSPage(host, "owned", "owned_db", "owned_bucket")
    host.pages.append(widget)
    widget.resize(1200, 740)
    widget.show()
    ready(qtbot, host, widget)
    return widget


def test_gridfs_metadata_default_grid_fixed_columns_and_pagination(qtbot, ui):
    host, session = ui
    widget = page(qtbot, ui)
    assert widget.title == "owned_bucket @ owned_db - GridFS"
    assert widget.model.columns == ["filename", "_id", "length", "chunkSize", "uploadDate", "md5", "contentType", "aliases", "metadata"]
    assert widget.selected_file().document["filename"] == "original.bin"
    assert widget.model.data(widget.model.index(0, 2)) == "4"
    assert not widget.model.flags(widget.model.index(0,0)) & Qt.ItemFlag.ItemIsEditable
    assert not widget.dirty and widget.current_result() is None
    assert widget.download_action.isEnabled() and widget.delete_action.isEnabled()
    assert not widget.open_action.isEnabled() and "尚未实现" in widget.open_action.text()
    widget.page_size.setValue(1)
    widget.go_page(0)
    ready(qtbot, host, widget)
    assert widget.next_button.isEnabled() and not widget.previous_button.isEnabled()
    qtbot.mouseClick(widget.next_button, Qt.MouseButton.LeftButton)
    ready(qtbot, host, widget)
    assert widget.offset == 1 and widget.selected_file().document["filename"] == "a[1].txt"
    assert not widget.next_button.isEnabled() and widget.previous_button.isEnabled()
    assert session.threads and all(thread != threading.get_ident() for thread in session.threads)


def test_preview_and_progress_are_below_table_and_pagination_with_reference_toolbar_order(qtbot,ui):
    host,session=ui
    widget=page(qtbot,ui)
    ordered=[action for action in widget.toolbar.actions() if action in {
        widget.open_action,widget.delete_action,widget.upload_action,widget.download_action,
        widget.filter_action,widget.preview_action,widget.progress_action,widget.focus_action}]
    assert ordered==[widget.open_action,widget.delete_action,widget.upload_action,widget.download_action,
        widget.filter_action,widget.preview_action,widget.progress_action,widget.focus_action]
    assert widget.splitter.orientation()==Qt.Orientation.Vertical
    assert widget.splitter.widget(0) is widget.file_panel
    assert widget.grid.parentWidget() is widget.file_panel and widget.footer.parentWidget() is widget.file_panel
    assert widget.delete_button is widget.toolbar.widgetForAction(widget.delete_action)
    assert not widget.footer.isAncestorOf(widget.delete_button)
    widget.preview_action.setChecked(True);ready(qtbot,host,widget)
    qtbot.waitUntil(lambda:widget.preview_panel.isVisible())
    assert widget.preview_panel.geometry().top()>widget.file_panel.geometry().bottom()
    assert widget.footer.geometry().top()>widget.grid.geometry().bottom()
    widget.progress_action.setChecked(True)
    qtbot.waitUntil(lambda:widget.progress_panel.isVisible())
    assert widget.splitter.widget(1) is widget.preview_panel and widget.splitter.widget(2) is widget.progress_panel
    assert widget.progress_panel.geometry().top()>widget.preview_panel.geometry().bottom()
    widget.preview_action.setChecked(False)
    assert not widget.preview_panel.isVisible() and widget.progress_panel.isVisible()
    assert widget.progress_action.isChecked() and widget.selected_file() is session.files[0]


def test_filters_escape_literal_name_and_clear_recovers_rows(qtbot, ui):
    host, _ = ui
    widget = page(qtbot, ui)
    widget.filter_action.setChecked(True)
    widget._filter_rows[0][3].setText("a[1]")
    qtbot.mouseClick(widget.filter_apply_button, Qt.MouseButton.LeftButton)
    ready(qtbot, host, widget)
    assert [item.document["filename"] for item in widget.page.documents] == ["a[1].txt"]
    widget.add_filter()
    widget._filter_rows[1][3].setText("nonexistent")
    widget.apply_filter()
    ready(qtbot, host, widget)
    assert widget.model.rowCount() == 0 and not widget.download_action.isEnabled()
    widget.clear_filter()
    ready(qtbot, host, widget)
    assert widget.model.rowCount() == 2


def test_transfer_progress_keeps_large_byte_counts_and_cannot_close_before_worker_end(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    source = tmp_path / "owned.dat"
    source.write_bytes(b"\x00\xff\r\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a,**_k:(str(source), ""))
    renders = []
    original_render = widget._render_transfer
    def render(row):
        renders.append(threading.get_ident())
        original_render(row)
    monkeypatch.setattr(widget, "_render_transfer", render)
    session.progress_size = 2**40+2
    session.entered, session.release = threading.Event(), threading.Event()
    widget.upload_file()
    qtbot.waitUntil(session.entered.is_set)
    qtbot.waitUntil(lambda:widget._transfers[0]["done"] == session.progress_size//2)
    assert widget.loading and widget.running and not widget.can_discard()
    assert not widget.upload_action.isEnabled() and widget.cancel_button.isEnabled()
    assert widget.close() is False and isValid(widget)
    assert widget.progress_table.cellWidget(0,2).format() == f"{session.progress_size//2:,} / {session.progress_size:,} bytes"
    session.release.set()
    ready(qtbot, host, widget)
    assert widget._transfers[0]["status"] == "完成" and widget._transfers[0]["done"] == session.progress_size
    assert widget.can_discard() and "完成 1" in widget.progress_summary.text()
    assert session.data[widget.page.documents[-1].document["_id"]] == b"\x00\xff\r\n"
    assert renders and all(thread == threading.get_ident() for thread in renders)


def test_cancel_uses_per_transfer_token_and_waits_for_actual_worker_failure(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    target = tmp_path / "download"
    target.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a,**_k:str(target))
    session.entered, session.release = threading.Event(), threading.Event()
    widget.download_file()
    qtbot.waitUntil(session.entered.is_set)
    qtbot.mouseClick(widget.cancel_button, Qt.MouseButton.LeftButton)
    assert widget._operation_cancelled.is_set() and not session.cancel_called.is_set()
    assert widget.loading and not widget.can_discard()
    assert widget._transfers[0]["status"] == "正在取消…"
    assert not widget.cancel_button.isEnabled() and not (target / "original.bin").exists()
    session.release.set()
    ready(qtbot, host, widget)
    assert "cancelled" in widget.message.text()
    assert widget._transfers[0]["status"] == "失败" and not (target / "original.bin").exists()


def test_cancelling_queued_transfer_never_cancels_the_current_other_query(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    source = tmp_path / "queued.bin"
    source.write_bytes(b"queued-only")
    monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *_a,**_k:(str(source),""))
    entered, release = threading.Event(), threading.Event()
    def other_query():
        entered.set()
        assert release.wait(5)
    host.submit(other_query)
    qtbot.waitUntil(entered.is_set)
    try:
        widget.upload_file()
        widget.cancel_transfer()
        assert widget.loading and not session.cancel_called.is_set()
    finally:
        release.set()
    ready(qtbot,host,widget)
    assert "cancelled before starting" in widget.message.text()
    assert not any(name in {"cancel","upload"} for name,_ in session.calls)
    assert len(session.files) == 2


def test_remote_filename_cannot_escape_download_directory_and_existing_file_is_preserved(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    selected = widget.selected_file()
    session.files[0] = file_document("../../outside.bin", b"\x00\xff\r\n", selected.document["_id"])
    widget.refresh()
    ready(qtbot, host, widget)
    folder = tmp_path / "target"
    folder.mkdir()
    protected = tmp_path / "outside.bin"
    protected.write_bytes(b"protected-outside")
    existing = folder / "outside.bin"
    existing.write_bytes(b"protected-inside")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a,**_k:str(folder))
    widget.download_file()
    ready(qtbot, host, widget)
    assert "exist" in widget.message.text().lower()
    assert existing.read_bytes() == b"protected-inside" and protected.read_bytes() == b"protected-outside"
    existing.unlink()
    widget.download_file()
    ready(qtbot, host, widget)
    assert existing.read_bytes() == b"\x00\xff\r\n" and protected.read_bytes() == b"protected-outside"


def test_image_preview_is_bounded_read_only_and_text_offers_download(qtbot, ui):
    host, session = ui
    widget = page(qtbot, ui)
    widget.preview_action.setChecked(True)
    ready(qtbot, host, widget)
    assert "小于 1 MB 的图像" in widget.preview_label.text()
    assert widget.preview_download_button.isEnabled() and widget._preview_image is None
    before = len(session.calls)
    selected = widget.selected_file()
    session.files[0] = file_document("oversize.png", b"", selected.document["_id"])
    session.files[0].document["length"] = 1_000_000
    widget.refresh()
    ready(qtbot, host, widget)
    qtbot.waitUntil(lambda:not host.tasks)
    assert widget._preview_image is None
    assert not any(call[0] == "download" for call in session.calls[before:])


def test_readonly_gates_upload_delete_but_preserves_download(qtbot, ui):
    host, session = ui
    session.config.read_only = True
    widget = page(qtbot, ui)
    assert widget.read_only and not widget.upload_action.isEnabled()
    assert not widget.delete_action.isEnabled() and widget.download_action.isEnabled()
    widget.upload_file()
    widget.delete_file()
    assert [name for name,_ in session.calls] == ["list"]


def test_real_image_decoder_receives_worker_download_and_releases_owned_preview_temp(qtbot, ui, tmp_path):
    host, session = ui
    source = tmp_path / "owned.png"
    image = QImage(32,24,QImage.Format.Format_RGB32)
    image.fill(QColor("#2864a0"))
    assert image.save(str(source),"PNG")
    payload = source.read_bytes()
    original = session.files[0]
    identity = original.document["_id"]
    session.files[0] = file_document("owned.png",payload,identity)
    session.data[identity] = payload
    widget = page(qtbot,ui)
    widget.preview_action.setChecked(True)
    ready(qtbot,host,widget)
    assert widget._preview_image.size().width() == 32 and widget._preview_image.size().height() == 24
    destinations = [args[0] for name,args in session.calls if name == "download-target"]
    assert destinations and all(not Path(path).exists() and not Path(path).parent.exists() for path in destinations)
    assert session.files[0].raw_bson == BSON.encode(session.files[0].document)


def test_old_session_transfer_reply_does_not_replace_new_binding(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    original_page = widget.page
    source = tmp_path / "owned.bin"
    source.write_bytes(b"owned")
    monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *_a,**_k:(str(source),""))
    session.entered, session.release = threading.Event(), threading.Event()
    widget.upload_file()
    qtbot.waitUntil(session.entered.is_set)
    replacement = ControlledGridFS()
    host.sessions["owned"] = replacement
    widget.cancel_transfer()
    assert widget._operation_cancelled.is_set() and not session.cancel_called.is_set()
    assert not replacement.cancel_called.is_set()
    session.release.set()
    ready(qtbot,host,widget)
    assert widget.page is original_page and "连接已改变" in widget.message.text()
    widget.refresh()
    ready(qtbot,host,widget)
    assert len(widget.page.documents) == 2


def test_delete_requires_confirmation_and_uses_metadata_preimage(qtbot, ui, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    original = widget.selected_file()
    widget.delete_file()
    assert len(session.files) == 2
    session.files[0] = file_document("peer-changed.bin", b"\x00\xff\r\n", original.document["_id"])
    monkeypatch.setattr(QMessageBox, "question", lambda *_a,**_k:QMessageBox.StandardButton.Yes)
    widget.delete_file()
    ready(qtbot, host, widget)
    assert "metadata changed" in widget.message.text() and len(session.files) == 2
    widget.refresh()
    ready(qtbot, host, widget)
    widget.delete_file()
    ready(qtbot, host, widget)
    assert len(session.files) == 1 and original.document["_id"] not in session.data


def test_deleted_page_ignores_inflight_worker_reply(qtbot, ui, tmp_path, monkeypatch):
    host, session = ui
    widget = page(qtbot, ui)
    source = tmp_path / "owned.bin"
    source.write_bytes(b"owned")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a,**_k:(str(source),""))
    session.entered, session.release = threading.Event(), threading.Event()
    widget.upload_file()
    qtbot.waitUntil(session.entered.is_set)
    widget.deleteLater()
    qtbot.waitUntil(lambda:not isValid(widget))
    session.release.set()
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount())
    assert len(session.files) == 3 and not host.errors


@pytest.mark.parametrize("name", ["../outside", "..\\outside", "C:\\path\\outside", "CON", "..", "", "a\x00b"])
def test_download_basename_is_one_portable_component(name):
    safe = _safe_filename(name)
    assert safe not in {"", ".", "..", "CON"}
    assert not any(char in safe for char in "/\\:\x00")


@pytest.fixture
def live_ui(qtbot, monkeypatch):
    endpoint_file = os.environ.get("OPENNAVICAT_TEST_MONGODB_ENDPOINT")
    if not endpoint_file:
        pytest.skip("Set an explicitly owned MongoDB endpoint receipt for real GridFS UI integration")
    endpoint = json.loads(Path(endpoint_file).read_text())
    identity = endpoint.get("container_id", "")
    if endpoint.get("owned") is not True or not identity or endpoint.get("host") not in {"localhost", "127.0.0.1"}:
        pytest.fail("The GridFS UI fixture requires an owned loopback container identity")
    lines = subprocess.run(["docker","inspect","--format","{{.Id}}\n{{json .Config.Labels}}\n{{json .NetworkSettings.Ports}}",identity],
                           check=True,capture_output=True,text=True,timeout=10).stdout.splitlines()
    if len(lines) != 3 or lines[0] != identity:
        pytest.fail("The GridFS fixture identity cannot be verified")
    labels, ports = json.loads(lines[1]), json.loads(lines[2])
    if labels.get("opennavicat.test") != "true" or labels.get("opennavicat.task") != "mongodb-reference":
        pytest.fail("The GridFS fixture lacks its exact ownership labels")
    if not ports.get("27017/tcp") or any(item.get("HostIp") != "127.0.0.1" or int(item.get("HostPort",0)) != int(endpoint["port"]) for item in ports["27017/tcp"]):
        pytest.fail("The GridFS fixture endpoint is not the declared loopback port")
    config = ConnectionConfig("owned", "Synthetic GridFS", driver="mongodb", database=endpoint["database"],
        host=endpoint["host"], port=int(endpoint["port"]), username=endpoint.get("username",""),
        password=endpoint.get("password",""), ssl_mode=endpoint.get("ssl_mode","disable"),
        mongo_auth_source=endpoint.get("mongo_auth_source","admin"), mongo_auth_mechanism=endpoint.get("mongo_auth_mechanism","DEFAULT"))
    admin, session = MongoSession(deepcopy(config)), MongoSession(deepcopy(config))
    bucket = "onv_gridfs_" + uuid.uuid4().hex
    host = widget = None
    shutdown=None
    try:
        admin.connect()
        import gridfs
        storage = gridfs.GridFSBucket(admin.connection[config.database], bucket_name=bucket)
        storage.upload_from_stream("seed.bin",b"\x00\xff\r\n",metadata={"owned":True})
        storage.upload_from_stream("a[1].txt",b"synthetic")
        host = SerialHost(session)
        shutdown=register_host(qtbot,host,session,close_session=True)
        host.show()
        host.submit(session.connect)
        qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount(),timeout=15000)
        assert not host.errors
        threads = []
        for name in ("list_files", "upload_file", "download_file", "delete_file"):
            method = getattr(session, name)
            def traced(*args,_method=method,**kwargs):
                threads.append(threading.get_ident())
                assert threading.get_ident() != threading.main_thread().ident
                return _method(*args,**kwargs)
            monkeypatch.setattr(session,name,traced)
        widget = MongoGridFSPage(host,"owned",config.database,bucket)
        host.pages.append(widget)
        widget.resize(1200,740)
        widget.show()
        monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Cancel)
        ready(qtbot,host,widget)
        yield host,widget,admin,storage,threads
    finally:
        try:
            if shutdown is not None:shutdown()
        finally:
            if widget is not None and isValid(widget):
                widget._closed = True
                widget.deleteLater()
            try:
                if shutdown is None:session.close()
            finally:
                try:
                    if admin.connection is not None:
                        for suffix in (".files", ".chunks"):
                            admin.connection[config.database].drop_collection(bucket+suffix)
                finally:
                    admin.close()
        if host is not None:
            assert not host.errors


def test_live_gridfs_file_list_literal_filter_and_paging(qtbot,live_ui):
    host,widget,_admin,_storage,threads = live_ui
    assert widget.model.rowCount() == 2
    widget._filter_rows[0][3].setText("a[1]")
    widget.apply_filter()
    ready(qtbot,host,widget)
    assert [item.document["filename"] for item in widget.page.documents] == ["a[1].txt"]
    widget.clear_filter()
    ready(qtbot,host,widget)
    widget.page_size.setValue(1)
    widget.go_page(0)
    ready(qtbot,host,widget)
    assert widget.next_button.isEnabled()
    widget.next_button.click()
    ready(qtbot,host,widget)
    assert widget.offset == 1 and not widget.next_button.isEnabled()
    assert threads and all(thread != threading.get_ident() for thread in threads)


def test_live_gridfs_upload_image_preview_download_exact_bytes_and_delete(qtbot,live_ui,tmp_path,monkeypatch):
    host,widget,admin,storage,threads = live_ui
    source = tmp_path / "synthetic.png"
    image = QImage(32,24,QImage.Format.Format_RGB32)
    image.fill(QColor("#2864a0"))
    assert image.save(str(source),"PNG")
    original = source.read_bytes()
    monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *_a,**_k:(str(source),""))
    widget.upload_file()
    ready(qtbot,host,widget)
    record = next(value for value in widget.page.documents if value.document["filename"] == source.name)
    assert record.document["contentType"] == "image/png"
    assert record.document["md5"] == hashlib.md5(original,usedforsecurity=False).hexdigest()
    index = widget.page.documents.index(record)
    widget.grid.setCurrentIndex(widget.model.index(index,0))
    widget.preview_action.setChecked(True)
    ready(qtbot,host,widget)
    assert widget._preview_image is not None and widget._preview_image.size().width() > 0
    assert not widget.preview_label.pixmap().isNull()
    widget.preview_action.setChecked(False)
    target = tmp_path / "download"
    target.mkdir()
    monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *_a,**_k:str(target))
    widget.download_file()
    ready(qtbot,host,widget)
    assert (target/source.name).read_bytes() == original
    assert storage.open_download_stream(record.document["_id"]).read() == original
    assert widget._transfers[0]["status"] == widget._transfers[1]["status"] == "完成"
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Yes)
    widget.delete_file()
    ready(qtbot,host,widget)
    namespace = admin.connection[widget.database]
    assert namespace[widget.bucket+".files"].find_one({"_id":record.document["_id"]}) is None
    assert namespace[widget.bucket+".chunks"].count_documents({"files_id":record.document["_id"]}) == 0
    assert namespace[widget.bucket+".files"].count_documents({}) == 2
    assert threads and all(thread != threading.get_ident() for thread in threads)


def test_live_gridfs_stale_metadata_delete_and_existing_download_are_fail_closed(qtbot,live_ui,tmp_path,monkeypatch):
    host,widget,admin,storage,_threads = live_ui
    selected = widget.selected_file()
    identity = selected.document["_id"]
    files = admin.connection[widget.database][widget.bucket+".files"]
    files.update_one({"_id":identity},{"$set":{"filename":"peer-changed.bin"}})
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Yes)
    widget.delete_file()
    ready(qtbot,host,widget)
    assert widget.message.text() and files.find_one({"_id":identity})["filename"] == "peer-changed.bin"
    assert storage.open_download_stream(identity).read() in {b"\x00\xff\r\n",b"synthetic"}
    widget.refresh()
    ready(qtbot,host,widget)
    row = next(i for i,value in enumerate(widget.page.documents) if value.document["_id"] == identity)
    widget.grid.setCurrentIndex(widget.model.index(row,0))
    target = tmp_path / "peer-changed.bin"
    target.write_bytes(b"do-not-overwrite")
    monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *_a,**_k:str(tmp_path))
    widget.download_file()
    ready(qtbot,host,widget)
    assert widget._transfers[-1]["status"] == "失败" and target.read_bytes() == b"do-not-overwrite"


def test_live_gridfs_cancel_partial_native_upload_removes_only_its_chunks(qtbot,live_ui,tmp_path,monkeypatch):
    host,widget,admin,_storage,_threads = live_ui
    source = tmp_path / "cancelled-native.bin"
    source.write_bytes(b"\x00\xff\r\n"*300_000)
    monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *_a,**_k:(str(source),""))
    session = host.sessions["owned"]
    upload = session.upload_file
    entered, release = threading.Event(), threading.Event()
    def gated(*args,progress=None,**kwargs):
        def report(transferred,total):
            progress(transferred,total)
            if 0 < transferred < total and not entered.is_set():
                entered.set()
                assert release.wait(8)
        return upload(*args,progress=report,**kwargs)
    monkeypatch.setattr(session,"upload_file",gated)
    namespace = admin.connection[widget.database]
    before = namespace[widget.bucket+".chunks"].count_documents({})
    try:
        widget.upload_file()
        qtbot.waitUntil(entered.is_set,timeout=10000)
        assert widget.loading and not widget.can_discard()
        widget.cancel_transfer()
        assert widget._operation_cancelled.is_set() and widget.loading
    finally:
        release.set()
    ready(qtbot,host,widget)
    assert widget._transfers[-1]["status"] == "失败"
    assert "cancel" in widget.message.text().lower()
    assert namespace[widget.bucket+".files"].count_documents({}) == 2
    assert namespace[widget.bucket+".files"].find_one({"filename":source.name}) is None
    assert namespace[widget.bucket+".chunks"].count_documents({}) == before


@pytest.mark.parametrize('kind',['transfer','control'])
def test_pytestqt_cleanup_waits_for_both_pools_and_native_owner_close_before_deletion(qtbot,request,kind):
    from pytestqt.qtbot import _close_widgets
    from PySide6.QtCore import QCoreApplication,QEvent
    session=ControlledGridFS();host=SerialHost(session)
    pool=host.pool if kind=='transfer'else host.cancel_pool
    entered,release,completed,closed=[threading.Event() for _ in range(4)]
    calls=[]
    def close_session():
        assert threading.get_ident()!=threading.main_thread().ident
        calls.append('owner-close');closed.set()
    session.close=close_session
    shutdown=register_host(qtbot,host,session,close_session=True)
    host.show()
    def work():
        entered.set();assert release.wait(5)
        calls.append('io-complete');completed.set()
        return 'owned result'
    host.submit(work,lambda value:calls.append(('reply',value)),pool=pool)
    qtbot.waitUntil(entered.is_set,timeout=2000)
    observations=[]
    def allow_completion():
        observations.append((isValid(host),isValid(pool),not completed.is_set()))
        release.set()
    QTimer.singleShot(30,allow_completion)
    try:
        _close_widgets(request.node)
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert observations==[(True,True,True)]
        assert completed.is_set() and closed.is_set()
        assert calls==['io-complete',('reply','owned result'),'owner-close']
        assert not isValid(host) and not isValid(pool)
        shutdown()
    finally:release.set()


@pytest.mark.parametrize("payload_kind", ["image", "unsupported"])
def test_preview_host_retires_native_signals_on_gui_before_worker_collection(
        qtbot, ui, monkeypatch, tmp_path, payload_kind):
    """The fake serial owner must preserve the real window's signal lifetime."""
    host, session = ui
    if payload_kind == "image":
        source = tmp_path / "generated.png"
        image = QImage(8, 6, QImage.Format.Format_RGB32)
        image.fill(QColor("#2864a0"))
        assert image.save(str(source), "PNG")
        payload = source.read_bytes()
        identity = session.files[0].document["_id"]
        session.files[0] = file_document(source.name, payload, identity)
        session.data[identity] = payload
    widget = page(qtbot, ui)
    gui, original_task = threading.get_ident(), Task
    tail_entered, release_tail = threading.Event(), threading.Event()
    references, destroyed, collections = [], [], []
    was_enabled = gc.isenabled()
    gc.disable()

    class TailTask(original_task):
        def run(self):
            super().run()
            tail_entered.set()
            assert release_tail.wait(5)

    def observed_task(function):
        index = len(references)
        task = TailTask(function) if index == 0 else original_task(function)
        task.signals.destroyed.connect(
            lambda *_args, number=index: destroyed.append((number, threading.get_ident())),
            Qt.ConnectionType.DirectConnection)
        references.append((weakref.ref(task), weakref.ref(task.signals)))
        return task

    monkeypatch.setitem(globals(), "Task", observed_task)
    try:
        widget.preview_action.setChecked(True)
        qtbot.waitUntil(lambda: tail_entered.is_set() and not host.tasks and not widget.loading)
        assert host.pool.activeThreadCount() == 1 and not widget.running
        assert widget.page is not None and widget.selected_file() is session.files[0]
        if payload_kind == "image":
            assert widget._preview_image is not None and not widget.message.text()
        else:
            assert widget._preview_image is None and "小于 1 MB 的图像" in widget.message.text()
        # Queue a new preview while the completed native runnable is held at
        # its tail. Retiring the old signals must not delete this new task.
        widget.preview_action.setChecked(False)
        widget.preview_action.setChecked(True)
        assert widget.loading and len(references) == 2
        assert isValid(references[1][1]())
        release_tail.set()
        ready(qtbot, host, widget)
        assert len(references) == 2

        def collect_in_worker():
            collections.append(threading.get_ident())
            return gc.collect()

        host.submit(collect_in_worker)
        qtbot.waitUntil(lambda: not host.tasks and not host.pool.activeThreadCount())
        qtbot.waitUntil(lambda: all(task() is None and signals() is None
                                  for task, signals in references), timeout=2000)
        assert len(references) == 3 and collections and collections[0] != gui
        assert sorted(destroyed) == [(0, gui), (1, gui), (2, gui)]
        assert not widget.loading and not widget.running and not host.errors
        assert [name for name, _args in session.calls].count("download") == 2
    finally:
        release_tail.set()
        qtbot.waitUntil(lambda: not host.tasks and not host.pool.activeThreadCount())
        if was_enabled:
            gc.enable()
