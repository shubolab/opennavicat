"""Qt BSON presentation/lifecycle contracts with real serialization helpers.

ControlledSession supplies owned, exact BSON replies to exercise widget/worker
boundaries. Live server cases are added separately against the adapter fixture.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import threading
from types import SimpleNamespace

import pytest
from bson import BSON, Binary, Decimal128, Int64, ObjectId
from bson.datetime_ms import DatetimeMS
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget
from shiboken6 import isValid

from opennavicat.database import DatabaseError
from opennavicat.mongodb_session import MongoDocument, MongoPage, canonical_dumps, canonical_loads
from opennavicat.tasks import Task
from opennavicat.ui.mongo_browser import MongoCollectionPage, MongoDocumentDialog


def document(value, *, editable=True, reason=""):
    raw=BSON.encode(value)
    return MongoDocument(deepcopy(value),raw,raw,editable,reason)


def fixtures():
    return [document({"_id":ObjectId("000000000000000000000001"),"long":Int64(9007199254740993),
        "decimal":Decimal128("123456789012345678901234567890.125"),"binary":Binary(b"\x00\xff",128),
        "nested":{"date":datetime(2026,1,1,tzinfo=timezone.utc),"text":"a\u2028b\u2029中文"},
        "array":[Int64(1),{"value":None}],"null":None}),
        document({"_id":ObjectId("000000000000000000000002"),"long":Int64(1),"only_second":True})]


class ControlledSession:
    def __init__(self, records=None):
        self.config=SimpleNamespace(read_only=False)
        self.records=fixtures() if records is None else records
        self.calls=[];self.threads=[];self.gui=threading.get_ident()
        self.fail_next=None
        self.entered=self.release=None

    def io(self,operation,*arguments):
        self.threads.append(threading.get_ident())
        assert threading.get_ident()!=self.gui
        self.calls.append((operation,arguments))
        if self.entered is not None:
            self.entered.set();self.release.wait(5)
        if self.fail_next is not None:
            failure,self.fail_next=self.fail_next,None
            raise failure

    def fetch_documents(self,database,collection,filter_doc=None,sort=None,projection=None,offset=0,limit=1000):
        self.io("fetch",database,collection,deepcopy(filter_doc),deepcopy(sort),deepcopy(projection),offset,limit)
        records=self.records[offset:offset+limit]
        if projection:records=[replace(record,editable=False,edit_reason="Projection results are read-only") for record in records]
        return MongoPage(records,offset,limit,offset+len(records)<len(self.records),len(self.records),
            'db.getCollection("owned").find().limit(%s).skip(%s)'%(limit,offset),offset+len(records))

    def replace_document(self,database,collection,value,expected):
        self.io("replace",database,collection,deepcopy(value),expected)
        saved=document(value)
        self.records=[saved if record.cas_token==expected.cas_token else record for record in self.records]
        return saved

    def insert_document(self,database,collection,value):
        self.io("insert",database,collection,deepcopy(value))
        value=deepcopy(value);value.setdefault("_id",ObjectId())
        saved=document(value);self.records.append(saved);return saved

    def delete_document(self,database,collection,expected):
        self.io("delete",database,collection,expected)
        self.records=[record for record in self.records if record.cas_token!=expected.cas_token]


class SerialHost(QWidget):
    def __init__(self,session):
        super().__init__();self.sessions={"owned":session};self.tasks=set()
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(1)
        self.pages=[];self.errors=[];self.worker_threads=[]

    def submit(self,function,success=None,failure=None,label="",**kwargs):
        def work():
            self.worker_threads.append(threading.get_ident());return function()
        task=Task(work);self.tasks.add(task)
        if success:task.signals.succeeded.connect(success)
        task.signals.failed.connect(failure or self.errors.append)
        task.signals.finished.connect(lambda:self.tasks.discard(task))
        self.pool.start(task)

    def when_idle(self,callback):
        if self.tasks:QTimer.singleShot(5,self,lambda:self.when_idle(callback))
        else:callback()


def ready(qtbot,host,page):
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount() and not page.loading and (page.page is not None or bool(page.message.text())),timeout=10000)
    assert page.page is not None, page.message.text()


def register_host(qtbot,host,session,*,close_session=False):
    """Drain while Qt objects exist, before pytest-qt closes/deletes widgets."""
    state={'done':False}
    def shutdown(widget):
        if state['done']:return
        assert widget is host and isValid(host) and isValid(host.pool), 'Host was deleted before its I/O was verified complete'
        release=getattr(session,'release',None)
        if release is not None:release.set()
        qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount(),timeout=15000)
        for page in host.pages:
            if isValid(page):
                page.discard_changes();page._closed=True;page.close();page.deleteLater()
        if close_session:
            host.submit(session.close)
            qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount(),timeout=15000)
        assert not host.errors
        host.close()
        state['done']=True
    qtbot.addWidget(host,before_close_func=shutdown)
    return lambda:shutdown(host)


@pytest.fixture
def ui(qtbot,monkeypatch):
    session=ControlledSession();host=SerialHost(session)
    shutdown=register_host(qtbot,host,session)
    host.show()
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Cancel)
    try:yield host,session
    finally:shutdown()


def page(qtbot,ui,**options):
    host,_=ui
    widget=MongoCollectionPage(host,"owned","owned_db","owned_collection",**options)
    host.pages.append(widget);widget.resize(1100,740);widget.show()
    ready(qtbot,host,widget)
    return widget


def test_grid_default_bson_summaries_missing_null_and_exact_native_document_sources(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    assert widget.views.currentWidget() is widget.grid
    columns=widget.model.columns
    def cell(row,name):return widget.model.data(widget.model.index(row,columns.index(name)))
    assert cell(0,"long")=="9007199254740993"
    assert cell(0,"decimal")=="123456789012345678901234567890.125"
    assert cell(0,"binary")=="BLOB 2 bytes"
    assert cell(0,"nested")=="(Document) 2 Fields"
    assert cell(0,"array")=="(Array) 2 Items"
    assert cell(0,"null")=="null" and cell(1,"null")=="（缺失）"
    assert not widget.model.flags(widget.model.index(0,0))&Qt.ItemFlag.ItemIsEditable
    assert isinstance(widget.model.documents[0].document["long"],Int64)
    assert widget.model.documents[0].raw_bson==session.records[0].raw_bson
    assert widget.query_label.text().startswith('db.getCollection("owned")')
    assert all(thread!=threading.get_ident() for thread in host.worker_threads)


def test_tree_lazy_navigation_and_json_page_preserve_bson_and_unicode(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    qtbot.mouseClick(widget.view_buttons.button(1),Qt.MouseButton.LeftButton)
    assert widget.tree.isHeaderHidden()
    assert widget.tree.topLevelItemCount()==len(session.records[0].document)
    nested=next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount()) if widget.tree.topLevelItem(i).text(0)=="nested")
    assert nested.childCount()==0
    nested.setExpanded(True)
    text=next(nested.child(i) for i in range(nested.childCount()) if nested.child(i).text(0)=="text")
    assert text.text(1)=="a\u2028b\u2029中文"
    count=len(session.calls)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    assert widget.selected_document() is session.records[1] and len(session.calls)==count
    assert [widget.tree.topLevelItem(i).text(0) for i in range(widget.tree.topLevelItemCount())]==list(session.records[1].document)
    assert widget.record_position.text()=="第 2 条记录"
    qtbot.mouseClick(widget.view_buttons.button(2),Qt.MouseButton.LeftButton)
    assert widget.json_view.isReadOnly() and widget.json_view.language=="text"
    assert widget.json_view.textCursor().position()==widget._json_offsets[1]
    assert not widget.edit_action.isVisible() and not widget.collapse_action.isVisible()
    view=widget.json_view.toPlainText()
    assert view.startswith("// 1\n") and "// 2\n" in view and '"$numberLong"' in view and '"$numberDecimal"' in view
    assert "\\u2028" in view and "\\u2029" in view
    dialog=widget.edit_document()
    assert isinstance(dialog,MongoDocumentDialog) and "Extended JSON" in dialog.windowTitle()
    assert isinstance(canonical_loads(dialog.editor.toPlainText())["long"],Int64)
    dialog.reject()


def test_tree_records_cross_pages_and_unknown_total_never_claims_last(qtbot,ui):
    host,session=ui
    session.records.extend(document({"_id":value,"label":str(value)}) for value in range(3,6))
    widget=page(qtbot,ui);widget.page_size.setValue(2)
    widget.filter_edit.setText('{"long":{"$gte":{"$numberLong":"1"}}}')
    widget.sort_edit.setText('{"_id":1}')
    widget.apply_filter();ready(qtbot,host,widget)
    widget.set_view(1)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    assert widget.offset==0 and widget._selected_row==1
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==2 and widget._selected_row==0
    arguments=session.calls[-1][1]
    assert isinstance(arguments[2]["long"]["$gte"],Int64) and arguments[3]==[("_id",1)]
    assert arguments[5:]==(2,2)
    qtbot.mouseClick(widget.previous_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==0 and widget._selected_row==1
    qtbot.mouseClick(widget.last_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==4 and widget.selected_document() is session.records[4]
    qtbot.mouseClick(widget.first_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==0 and widget._selected_row==0
    widget.load_page(replace(widget.page,total=None))
    assert widget.next_button.isEnabled() and not widget.last_button.isEnabled()
    assert "总数未知" in widget.last_button.toolTip()


def test_cancel_discard_and_save_navigation_keep_original_bson_preimage(qtbot,ui,monkeypatch):
    host,session=ui;widget=page(qtbot,ui)
    original=widget.selected_document()
    dialog=widget.edit_document();changed=deepcopy(original.document);changed["long"]=Int64(999)
    dialog.editor.setPlainText(canonical_dumps(changed))
    widget.grid.setCurrentIndex(widget.model.index(1,0))
    assert widget.selected_document() is original and widget.grid.currentIndex().row()==0
    widget.set_view(2)
    cursor=widget.json_view.textCursor();cursor.setPosition(widget._json_offsets[1]);widget.json_view.setTextCursor(cursor)
    assert widget.selected_document() is original and widget.json_view.textCursor().position()==widget._json_offsets[0]
    widget.set_view(1)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    assert widget.selected_document() is original and widget.dirty
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Discard)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    assert widget.selected_document() is session.records[1] and not widget.dirty
    assert original.raw_bson==session.records[0].raw_bson
    widget.select_record(0)
    dialog=widget.edit_document();dialog.editor.setPlainText(canonical_dumps(changed))
    session.entered,session.release=threading.Event(),threading.Event()
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(session.entered.is_set)
    assert widget.loading and widget.selected_document() is original and not widget.next_button.isEnabled()
    session.release.set()
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading and widget._selected_row==1)
    expected=next(arguments[3] for operation,arguments in session.calls if operation=="replace")
    assert expected is original and expected.raw_bson==original.raw_bson
    assert session.records[0].document["long"]==Int64(999) and not widget.dirty


def test_refresh_preserves_typed_identity_and_failed_page_keeps_previous_record(qtbot,ui):
    host,session=ui
    session.records=[document({"_id":1,"value":"int"}),document({"_id":Int64(1),"value":"long"}),document({"_id":True,"value":"bool"})]
    widget=page(qtbot,ui);widget.select_record(1);widget.set_view(1)
    original=widget.selected_document()
    session.records=[session.records[2],session.records[0],session.records[1]]
    widget.refresh();ready(qtbot,host,widget)
    assert widget.selected_document() is original and widget._selected_row==2
    assert isinstance(widget.selected_document().document["_id"],Int64)
    widget.page_size.setValue(1);widget.go_page(0);ready(qtbot,host,widget)
    original=widget.selected_document()
    session.fail_next=DatabaseError("Owned page read failed")
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading)
    assert widget.offset==widget.page.offset==0 and widget.selected_document() is original
    assert "read failed" in widget.message.text() and widget._pending_row is None


def test_filter_sort_projection_inputs_capture_typed_values_and_invalid_json_never_runs(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    count=len(session.calls)
    widget.filter_edit.setText('{"long":{"$gte":{"$numberLong":"1"}}}')
    widget.sort_edit.setText('{"long":-1}')
    widget.projection_edit.setText('{"long":1}')
    widget.apply_filter();ready(qtbot,host,widget)
    operation,arguments=session.calls[-1]
    assert operation=="fetch" and isinstance(arguments[2]["long"]["$gte"],Int64)
    assert arguments[3]==[("long",-1)] and arguments[4]=={"long":1}
    assert not widget.edit_action.isEnabled() and not widget.delete_button.isEnabled()
    count=len(session.calls)
    widget.filter_edit.setText('{"same":1,"same":2}')
    widget.apply_filter()
    assert len(session.calls)==count and "Duplicate" in widget.message.text()
    widget.filter_edit.setText('{}');widget.sort_edit.setText('{"long":true}')
    widget.apply_filter();assert len(session.calls)==count


def test_paging_uses_backend_offsets_and_server_sort_inputs(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    widget.page_size.setValue(1);widget.go_page(0);ready(qtbot,host,widget)
    assert widget.next_button.isEnabled() and not widget.previous_button.isEnabled()
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==1 and widget.previous_button.isEnabled() and not widget.next_button.isEnabled()
    assert widget.selected_document().document["_id"]==session.records[1].document["_id"]
    widget.sort_column(widget.model.columns.index("long"));ready(qtbot,host,widget)
    assert session.calls[-1][1][3]==[("long",1)] and widget.offset==0


def test_insert_validation_has_no_io_and_add_writes_exact_bson_in_worker(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    dialog=widget.add_document();qtbot.waitUntil(dialog.isVisible)
    value={"long":Int64(9007199254740995),"bin":Binary(b"\x00\xff",128),"date":DatetimeMS(-(2**62))}
    dialog.editor.setPlainText(canonical_dumps(value));assert widget.dirty
    count=len(session.calls)
    qtbot.mouseClick(dialog.validate_button,Qt.MouseButton.LeftButton)
    assert len(session.calls)==count and "有效" in dialog.error.text()
    qtbot.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading and widget._dialog is None)
    ready(qtbot,host,widget)
    inserted=next(arguments[2] for operation,arguments in session.calls if operation=="insert")
    assert isinstance(inserted["long"],Int64) and isinstance(inserted["date"],DatetimeMS)
    assert inserted["bin"]==Binary(b"\x00\xff",128) and not widget.dirty


def test_edit_conflict_retains_text_and_sealed_preimage_and_clears_stale_leave_retry(qtbot,ui,monkeypatch):
    host,session=ui;widget=page(qtbot,ui)
    original=widget.selected_document()
    dialog=widget.edit_document()
    value=deepcopy(original.document);value["long"]=Int64(9007199254740997)
    dialog.editor.setPlainText(canonical_dumps(value))
    text=dialog.editor.toPlainText();retried=[]
    session.fail_next=DatabaseError("Concurrent BSON conflict")
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    assert widget.can_discard(lambda:retried.append(True)) is False
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading)
    assert widget._dialog is dialog and dialog.editor.toPlainText()==text and widget.dirty
    assert "conflict" in dialog.error.text() and widget._after_save is None and not retried
    assert session.records[0].raw_bson==original.raw_bson
    expected=next(arguments[3] for operation,arguments in session.calls if operation=="replace")
    assert expected is original and expected.cas_token==original.cas_token
    qtbot.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and widget._dialog is None)
    ready(qtbot,host,widget)
    assert not retried and session.records[0].document["long"]==Int64(9007199254740997)


def test_invalid_editor_save_never_keeps_old_close_retry(qtbot,ui,monkeypatch):
    host,session=ui;widget=page(qtbot,ui)
    dialog=widget.add_document();dialog.editor.setPlainText('{"bad":')
    retried=[]
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    widget.can_discard(lambda:retried.append(True))
    assert widget._after_save is None and widget.dirty and not widget.loading
    assert not any(call[0]=="insert" for call in session.calls)
    dialog.editor.setPlainText('{"good":{"$numberLong":"1"}}')
    qtbot.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:widget._dialog is None and not host.tasks)
    ready(qtbot,host,widget)
    assert not retried


def test_cancel_then_save_close_persists_before_destroy_and_no_deleted_widget_callback(qtbot,ui,monkeypatch):
    host,session=ui;widget=page(qtbot,ui)
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
    dialog=widget.edit_document();value=deepcopy(widget.selected_document().document);value["long"]=Int64(99)
    dialog.editor.setPlainText(canonical_dumps(value))
    assert widget.close() is False and isValid(widget) and widget.dirty
    assert session.records[0].document["long"]!=99
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    widget.close()
    qtbot.waitUntil(lambda:not isValid(widget),timeout=10000)
    assert session.records[0].document["long"]==Int64(99)
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount())
    delivered=[];QTimer.singleShot(0,lambda:delivered.append(True));qtbot.waitUntil(lambda:bool(delivered))


def test_readonly_and_unsafe_documents_cannot_mutate_but_exact_raw_export_works(qtbot,ui,tmp_path,monkeypatch):
    host,session=ui
    session.records=[document({"_id":1,"literal":{"$date":"2026-01-01T00:00:00Z"}},editable=False,reason="BSON wrapper cannot round-trip")]
    widget=page(qtbot,ui)
    assert not widget.edit_action.isEnabled() and widget.edit_document() is None
    assert "cannot round-trip" in widget.message.text()
    target=tmp_path/"exact.bson"
    monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *_a,**_k:(str(target),"BSON 原始文档 (*.bson)"))
    widget.export_document();ready(qtbot,host,widget)
    assert target.read_bytes()==session.records[0].raw_bson
    json_target=tmp_path/"must-not-write.json"
    monkeypatch.setattr(QFileDialog,"getSaveFileName",lambda *_a,**_k:(str(json_target),"Canonical Extended JSON (*.json)"))
    widget.export_document();assert not json_target.exists()
    session.config.read_only=True;widget._update_controls()
    assert not widget.add_button.isEnabled() and widget.add_document() is None


def test_readonly_view_gates_inserts_independently_of_session_permissions(qtbot,ui):
    widget=page(qtbot,ui,kind="view")
    assert widget.read_only and not widget.add_button.isEnabled() and widget.add_document() is None


def test_old_session_reply_is_ignored_and_refresh_can_bind_the_replacement(qtbot,ui):
    host,session=ui;widget=page(qtbot,ui)
    old=widget.page.documents[0].document["_id"]
    session.entered,session.release=threading.Event(),threading.Event()
    widget.refresh();qtbot.waitUntil(session.entered.is_set)
    replacement=ControlledSession([document({"_id":ObjectId("000000000000000000000099"),"fresh":True})])
    host.sessions["owned"]=replacement;session.release.set()
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading)
    assert widget.page.documents[0].document["_id"]==old and "连接已改变" in widget.message.text()
    widget.refresh();ready(qtbot,host,widget)
    assert widget.page.documents[0].document["_id"]==replacement.records[0].document["_id"]


@pytest.fixture
def live_ui(qtbot,tmp_path,monkeypatch):
    """Opt-in explicitly owned service, unique collection, no user metadata."""
    import json,os,subprocess
    from pathlib import Path
    import uuid
    from opennavicat.database import ConnectionConfig
    from opennavicat.mongodb_session import MongoSession
    endpoint_file=os.environ.get("OPENNAVICAT_TEST_MONGODB_ENDPOINT")
    if not endpoint_file:pytest.skip("Set an explicitly owned MongoDB endpoint receipt for real Qt integration")
    endpoint=json.loads(Path(endpoint_file).read_text())
    if endpoint.get("owned") is not True:pytest.fail("The MongoDB UI fixture only accepts an explicitly owned test service")
    container=endpoint.get("container_id", "")
    if not container or endpoint["host"] not in {"localhost","127.0.0.1"}:
        pytest.fail("The MongoDB UI fixture has no owned loopback container identity")
    inspected=subprocess.run(["docker","inspect","--format", "{{.Id}}\n{{json .Config.Labels}}\n{{json .NetworkSettings.Ports}}",container],
        check=True,capture_output=True,text=True,timeout=10).stdout.splitlines()
    if len(inspected)!=3 or inspected[0]!=container:
        pytest.fail("The MongoDB fixture container identity cannot be verified")
    labels,ports=json.loads(inspected[1]),json.loads(inspected[2])
    if labels.get("opennavicat.test")!="true" or labels.get("opennavicat.task")!="mongodb-reference":
        pytest.fail("The MongoDB service does not carry the exact owned reference fixture labels")
    mappings=ports.get("27017/tcp",[])
    if not mappings or any(mapping.get("HostIp")!="127.0.0.1" or int(mapping.get("HostPort",0))!=int(endpoint["port"]) for mapping in mappings):
        pytest.fail("The MongoDB fixture is not the declared loopback-only endpoint")
    config=ConnectionConfig("owned-mongo-ui","Owned MongoDB UI",driver="mongodb",database=endpoint["database"],
        host=endpoint["host"],port=int(endpoint["port"]),username=endpoint.get("username",""),
        password=endpoint.get("password",""),ssl_mode=endpoint.get("ssl_mode","disable"),ssl_ca=endpoint.get("ssl_ca",""))
    for key in ("mongo_auth_source","mongo_auth_mechanism"):
        if key in endpoint:setattr(config,key,endpoint[key])
    database=config.database;collection="onv_ui_"+uuid.uuid4().hex
    admin=MongoSession(deepcopy(config))
    session=host=widget=None
    shutdown=None
    created=False
    try:
        admin.connect()
        admin.connection[database].create_collection(collection);created=True
        for record in fixtures():admin.insert_document(database,collection,record.document)
        session=MongoSession(deepcopy(config))
        # Connection/session work remains inside the page owner's serial worker.
        io_threads=[];main_thread=threading.get_ident()
        for name in ("fetch_documents","insert_document","replace_document","delete_document"):
            method=getattr(session,name)
            def traced(*args,_method=method,**kwargs):
                io_threads.append(threading.get_ident())
                assert threading.get_ident()!=main_thread
                return _method(*args,**kwargs)
            monkeypatch.setattr(session,name,traced)
        host=SerialHost(session)
        shutdown=register_host(qtbot,host,session,close_session=True)
        host.show()
        host.submit(session.connect)
        qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount(),timeout=10000)
        assert not host.errors
        widget=MongoCollectionPage(host,"owned",database,collection)
        host.pages.append(widget);widget.resize(1100,740);widget.show()
        monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Cancel)
        ready(qtbot,host,widget)
        yield host,widget,admin,database,collection,io_threads
    finally:
        try:
            if shutdown is not None:shutdown()
        finally:
            if widget is not None and isValid(widget):widget.discard_changes();widget._closed=True;widget.deleteLater()
            try:
                if session is not None and shutdown is None:session.close()
            finally:
                try:
                    if created:admin.connection[database].drop_collection(collection)
                finally:admin.close()
        if host is not None:assert not host.errors


def test_live_mongo_grid_paging_and_explicit_add_preserve_bson_types(qtbot,live_ui):
    host,widget,admin,database,collection,threads=live_ui
    assert widget.page.documents and widget.page.documents[0].editable
    assert isinstance(widget.page.documents[0].document["long"],Int64)
    assert widget.page.documents[0].document["binary"]==Binary(b"\x00\xff",128)
    widget.page_size.setValue(1);widget.sort_edit.setText('{"_id":1}');widget.apply_filter();ready(qtbot,host,widget)
    assert widget.page.has_more and widget.next_button.isEnabled()
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton);ready(qtbot,host,widget)
    assert widget.offset==1 and widget.page.documents[0].document["_id"]==ObjectId("000000000000000000000002")
    dialog=widget.add_document()
    value={"name":"written-via-real-Qt","long":Int64(9007199254740995),"decimal":Decimal128("1.00"),
           "binary":Binary(b"\x00\xff\r\n",128),"text":"a\u2028b\u2029中文"}
    dialog.editor.setPlainText(canonical_dumps(value))
    qtbot.mouseClick(dialog.validate_button,Qt.MouseButton.LeftButton)
    assert admin.fetch_documents(database,collection,{"name":"written-via-real-Qt"}).documents==[]
    qtbot.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and widget._dialog is None and not widget.loading,timeout=10000)
    ready(qtbot,host,widget)
    saved=admin.fetch_documents(database,collection,{"name":"written-via-real-Qt"}).documents[0].document
    assert isinstance(saved["long"],Int64) and saved["long"]==9007199254740995
    assert str(saved["decimal"])=="1.00" and saved["binary"]==value["binary"] and saved["text"]==value["text"]
    assert threads and all(thread!=threading.get_ident() for thread in threads)


def test_live_mongo_conflict_retains_draft_then_save_close_waits_for_real_cas(qtbot,live_ui,monkeypatch):
    host,widget,admin,database,collection,threads=live_ui
    widget.sort_edit.setText('{"_id":1}');widget.apply_filter();ready(qtbot,host,widget)
    original=widget.selected_document();identity=original.document["_id"]
    dialog=widget.edit_document();draft=deepcopy(original.document);draft["long"]=Int64(77)
    dialog.editor.setPlainText(canonical_dumps(draft));typed_text=dialog.editor.toPlainText()
    peer=admin.fetch_documents(database,collection,{"_id":identity}).documents[0]
    peer_value=deepcopy(peer.document);peer_value["peer_update"]=Int64(8)
    admin.replace_document(database,collection,peer_value,peer)
    qtbot.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading,timeout=10000)
    assert widget._dialog is dialog and widget.dirty and dialog.editor.toPlainText()==typed_text
    assert "changed" in dialog.error.text().lower()
    actual=admin.fetch_documents(database,collection,{"_id":identity}).documents[0].document
    assert actual["peer_update"]==Int64(8) and actual["long"]==original.document["long"]
    dialog.reject();widget.refresh();ready(qtbot,host,widget)
    dialog=widget.edit_document();draft=deepcopy(widget.selected_document().document);draft["long"]=Int64(79)
    dialog.editor.setPlainText(canonical_dumps(draft))
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose,True)
    assert widget.close() is False and widget.dirty
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    widget.close();qtbot.waitUntil(lambda:not isValid(widget),timeout=15000)
    qtbot.waitUntil(lambda:not host.tasks and not host.pool.activeThreadCount())
    actual=admin.fetch_documents(database,collection,{"_id":identity}).documents[0].document
    assert isinstance(actual["long"],Int64) and actual["long"]==79 and actual["peer_update"]==Int64(8)


def test_live_mongo_delete_conflict_cannot_delete_a_concurrently_changed_document(qtbot,live_ui,monkeypatch):
    host,widget,admin,database,collection,threads=live_ui
    widget.sort_edit.setText('{"_id":1}');widget.apply_filter();ready(qtbot,host,widget)
    original=widget.selected_document();identity=original.document["_id"]
    peer=admin.fetch_documents(database,collection,{"_id":identity}).documents[0]
    changed=deepcopy(peer.document);changed["peer_update"]=True
    admin.replace_document(database,collection,changed,peer)
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(widget.delete_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading,timeout=10000)
    assert "changed" in widget.message.text().lower()
    assert admin.fetch_documents(database,collection,{"_id":identity}).documents[0].document["peer_update"] is True
    widget.refresh();ready(qtbot,host,widget)
    qtbot.mouseClick(widget.delete_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading,timeout=10000)
    ready(qtbot,host,widget)
    assert admin.fetch_documents(database,collection,{"_id":identity}).documents==[]


def test_live_tree_cross_page_save_navigates_after_exact_bson_write(qtbot,live_ui,monkeypatch):
    host,widget,admin,database,collection,threads=live_ui
    widget.page_size.setValue(1);widget.sort_edit.setText('{"_id":1}')
    widget.apply_filter();ready(qtbot,host,widget);widget.set_view(1)
    original=widget.selected_document();identity=original.document["_id"]
    assert widget.tree.topLevelItemCount()==len(original.document)
    assert not widget.last_button.isEnabled() and widget.page.total is None
    dialog=widget.edit_document();changed=deepcopy(original.document);changed["long"]=Int64(9007199254740999)
    dialog.editor.setPlainText(canonical_dumps(changed))
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    assert widget.offset==0 and widget.selected_document() is original and widget.dirty
    monkeypatch.setattr(QMessageBox,"question",lambda *_a,**_k:QMessageBox.StandardButton.Save)
    qtbot.mouseClick(widget.next_button,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not host.tasks and not widget.loading and widget.offset==1,timeout=15000)
    assert widget.selected_document().document["_id"]!=identity
    assert [widget.tree.topLevelItem(i).text(0) for i in range(widget.tree.topLevelItemCount())]==["_id","long","only_second"]
    saved=admin.fetch_documents(database,collection,{"_id":identity}).documents[0]
    assert saved.document["long"]==Int64(9007199254740999) and isinstance(saved.document["long"],Int64)
    assert saved.document["binary"]==Binary(b"\x00\xff",128)
    widget.set_view(2);assert widget._selected_row==0
    widget.set_view(1);qtbot.mouseClick(widget.previous_button,Qt.MouseButton.LeftButton)
    ready(qtbot,host,widget)
    assert widget.offset==0 and widget.selected_document().document["_id"]==identity
    assert threads and all(thread!=threading.get_ident() for thread in threads)


def test_view_switching_keeps_the_visibly_selected_document_bound_to_mutation(qtbot,ui):
    widget=page(qtbot,ui)
    widget.grid.setCurrentIndex(widget.model.index(1,0))
    widget.set_view(1)
    assert widget.tree.currentItem() is widget.tree.topLevelItem(0)
    assert [widget.tree.topLevelItem(i).text(0) for i in range(widget.tree.topLevelItemCount())]==["_id","long","only_second"]
    widget.tree.setCurrentItem(widget.tree.topLevelItem(1))
    widget.set_view(0)
    assert widget.grid.currentIndex().row()==1 and widget._selected_row==1
    widget.set_view(1)
    qtbot.mouseClick(widget.first_button,Qt.MouseButton.LeftButton)
    assert widget.grid.currentIndex().row()==0 and widget._selected_row==0
    widget.set_view(2)
    cursor=widget.json_view.textCursor();cursor.setPosition(widget._json_offsets[1]);widget.json_view.setTextCursor(cursor)
    assert widget._selected_row==1 and widget.selected_document().document["_id"]==ObjectId("000000000000000000000002")
    widget.set_view(0)
    assert widget.grid.currentIndex().row()==1


def test_pytestqt_cleanup_drains_actual_pending_io_and_owner_close_before_qt_deletes_pool(qtbot,request):
    from pytestqt.qtbot import _close_widgets
    from PySide6.QtCore import QCoreApplication,QEvent
    session=ControlledSession();host=SerialHost(session);pool=host.pool
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
    host.submit(work,lambda value:calls.append(('reply',value)))
    qtbot.waitUntil(entered.is_set,timeout=2000)
    observations=[]
    def allow_completion():
        observations.append((isValid(host),isValid(pool),not completed.is_set()))
        release.set()
    QTimer.singleShot(30,allow_completion)
    try:
        # Exercise pytest-qt's real close/delete order in the test body, before
        # the fixture finalizer would have run in the original CI failure.
        _close_widgets(request.node)
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert observations==[(True,True,True)]
        assert completed.is_set() and closed.is_set()
        assert calls==['io-complete',('reply','owned result'),'owner-close']
        assert not isValid(host) and not isValid(pool)
        shutdown()  # A later fixture finalizer uses verified completion only.
    finally:release.set()
