"""Real Qt edits and serial-worker MongoDB saves using synthetic owned fixtures."""
from copy import deepcopy
from dataclasses import replace
import threading
import uuid

from bson import Binary, Int64
from pymongo import MongoClient
import pytest
from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QStyle, QStyleOptionButton, QWidget

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_session import MongoSession
from opennavicat import mongodb_indexes as tools
from opennavicat.tasks import Task
from opennavicat.ui.mongo_index_designer import MongoIndexDeleteDialog, MongoIndexDesignerPage, index_error_text
from test_mongodb_session import mongodb_service


@pytest.fixture
def offline(qtbot, monkeypatch):
    session = MongoSession(ConnectionConfig("owned", "Generated", driver="mongodb", ssl_mode="disable"))
    monkeypatch.setattr(session, "connect", lambda: pytest.fail("Offline designer must not connect"))
    snapshot = tools.new_index(session, "generated")
    page = MongoIndexDesignerPage("owned", "generated")
    qtbot.addWidget(page)
    page.resize(1080, 740); page.show()
    page.set_collections(["docs", "other"])
    page.load_snapshot(snapshot)
    return page, session


def edit_field(qtbot, page, row, value, order=1):
    page.fields.setCurrentCell(row, 0)
    page.fields.editItem(page.fields.item(row, 0))
    editor = QApplication.focusWidget()
    qtbot.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClicks(editor, value)
    qtbot.keyClick(editor, Qt.Key.Key_Tab)
    page.fields.cellWidget(row, 1).setCurrentIndex(page.fields.cellWidget(row, 1).findData(order))


def add_keys(qtbot, page, fields):
    for row, (name, order) in enumerate(fields):
        page.add_action.trigger()
        edit_field(qtbot, page, row, name, order)


def test_six_real_pages_ordered_keys_advanced_options_and_invalid_json_gate(qtbot, offline):
    page, _ = offline
    assert [page.tabs.tabText(i) for i in range(6)] == ["常规", "存储引擎", "排序规则", "文本", "地理空间", "脚本预览"]
    page.collection_combo.setCurrentText("docs")
    add_keys(qtbot, page, [("title", "text"), ("body", "text")])
    page.tabs.setCurrentIndex(3)
    page.add_weight_button.click()
    page.weights.item(0, 0).setText("title")
    page.weights.item(0, 1).setText("5")
    page._widgets["default_language"].setText("english")
    page.tabs.setCurrentIndex(2)
    assert not page._widgets["collation.strength"].isEnabled()
    page.locale_edit.setText("en")
    assert page._widgets["collation.strength"].isEnabled()
    page._widgets["collation.strength"].setCurrentText("2")
    page.tabs.setCurrentIndex(0)
    page.partial_editor.setPlainText('{"active":{"$eq":true}}')
    definition = page.draft()
    assert [(key.field, key.order) for key in definition.keys] == [("title", "text"), ("body", "text")]
    assert definition.options["weights"] == {"title": 5}
    assert definition.options["collation"] == {"locale": "en", "strength": 2}
    assert definition.options["partialFilterExpression"] == {"active": {"$eq": True}}
    spy = QSignalSpy(page.saveRequested)
    page.partial_editor.setPlainText('{"active":')
    assert page.dirty and not page.save_action.isEnabled()
    page.request_save()
    assert page._save_dialog is None and spy.count() == 0
    with pytest.raises(DatabaseError, match="Extended JSON"):
        page.draft()
    page.partial_editor.setPlainText("")
    assert page.save_action.isEnabled() and not page._invalid
    assert not page.bucket_size.isEnabled() and "移除" in page.bucket_size.toolTip()


def test_unknown_raw_options_remain_exact_and_readonly(qtbot, offline):
    page, session = offline
    spec = {"name": "future", "key": {"a": 1}, "futureOption": {"x": Int64(1), "b": Binary(b'\0\xff', 128)}}
    snapshot = tools._snapshot(session, "generated", "docs", "future", spec, Binary(b'x' * 16, 4))
    original = snapshot.raw_spec
    page.load_snapshot(snapshot)
    assert page.snapshot.raw_spec == original and page._read_only and not page.dirty
    assert page.definition.options == snapshot.definition.options
    assert not page.save_action.isEnabled() and not page.add_action.isEnabled()
    page.add_field(); page.add_weight()
    assert page.snapshot.raw_spec == original and page.definition == snapshot.definition
    page.tabs.setCurrentIndex(5); page.preview_mode.setCurrentText("DDL")
    assert "futureOption" in page.sql_preview.toPlainText()


def test_new_name_sheet_is_confirmed_and_cancel_has_no_save_signal(qtbot, offline):
    page, _ = offline
    page.collection_combo.setCurrentText("docs")
    add_keys(qtbot, page, [("a", 1)])
    saved = QSignalSpy(page.saveRequested)
    preference = QSignalSpy(page.savePreviewPreferenceChanged)
    def preview(definition):
        plan = tools.IndexPlan(deepcopy(page.snapshot), deepcopy(definition), [], "exact reviewed command", "DDL", True)
        page.show_plan(plan)
    page.previewRequested.connect(preview)
    page.request_save()
    sheet = page._save_dialog
    assert sheet.plan is None
    assert sheet.name_edit.isVisible() and not sheet.save_button.isEnabled()
    assert not sheet.suppress_preview_check.isChecked() and sheet.suppress_preview_check.isEnabled()
    qtbot.keyClicks(sheet.name_edit, "owned_index")
    qtbot.waitUntil(lambda: sheet.save_button.isEnabled())
    assert sheet.preview.toPlainText() == "exact reviewed command"
    sheet.suppress_preview_check.setChecked(True)
    qtbot.mouseClick(sheet.buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page._save_dialog is None)
    assert saved.count() == 0 and page.dirty and page.definition.name == ""
    assert preference.count() == 0 and not page.suppress_save_preview
    page.request_save(); sheet = page._save_dialog
    qtbot.keyClicks(sheet.name_edit, "confirmed_index")
    qtbot.waitUntil(lambda: sheet.save_button.isEnabled())
    sheet.suppress_preview_check.setChecked(True)
    qtbot.mouseClick(sheet.save_button, Qt.MouseButton.LeftButton)
    assert saved.count() == 1 and page.definition.name == "confirmed_index"
    assert page.confirmed_plan.definition.name == "confirmed_index"
    assert preference.count() == 1 and page.suppress_save_preview


def test_stale_preview_cannot_replace_new_draft_or_invalid_input(qtbot, offline):
    page, _ = offline
    page.collection_combo.setCurrentText("docs")
    add_keys(qtbot, page, [("a", 1)])
    before = page.draft()
    stale = tools.IndexPlan(deepcopy(page.snapshot), before, [], "old command", "old DDL", True)
    page.fields.item(0, 0).setText("b")
    page.show_plan(stale)
    assert page._plan is None
    page.partial_editor.setPlainText("{")
    invalid = tools.IndexPlan(deepcopy(page.snapshot), deepcopy(page.definition), [], "still old", "old", True)
    page.show_plan(invalid)
    assert page._plan is None and page._invalid


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(1)
        self.tasks = set(); self.errors = []; self.threads = []

    def submit(self, function, success=None, failure=None):
        def work():
            assert threading.get_ident() != threading.main_thread().ident
            self.threads.append(threading.get_ident())
            return function()
        task = Task(work); self.tasks.add(task)
        if success:
            task.signals.succeeded.connect(success)
        task.signals.failed.connect(failure or self.errors.append)
        task.signals.finished.connect(lambda: self.tasks.discard(task))
        self.pool.start(task)


def idle(qtbot, host):
    qtbot.waitUntil(lambda: not host.tasks and not host.pool.activeThreadCount(), timeout=20000)


@pytest.fixture
def live(qtbot, mongodb_service):
    config = replace(mongodb_service, database="onv_idx_ui_" + uuid.uuid4().hex)
    session = MongoSession(config)
    host = Host(); host.show()
    page = MongoIndexDesignerPage("owned", config.database, "docs", parent=host)
    def close(widget):
        idle(qtbot, host)
        if not getattr(host, "closed", False):
            def cleanup():
                session.connection.drop_database(config.database)
                session.close()
            host.submit(cleanup)
            idle(qtbot, host)
            host.closed = True
    qtbot.addWidget(host, before_close_func=close)
    def prepare():
        session.connect(); session.connection[config.database].create_collection("docs")
        return tools.new_index(session, config.database, "docs")
    page.set_collections(["docs"])
    host.submit(prepare, page.load_snapshot); idle(qtbot, host)
    assert not host.errors
    def preview(definition):
        snapshot, draft = deepcopy(page.snapshot), deepcopy(definition)
        host.submit(lambda: tools.plan_index(session, snapshot, draft), page.show_plan, page.show_error)
    def save(definition):
        plan = deepcopy(page.confirmed_plan)
        page.set_busy(True)
        host.submit(lambda: tools.apply_plan(session, plan), page.save_succeeded, page.show_error)
    page.previewRequested.connect(preview); page.saveRequested.connect(save)
    page.resize(1080, 740); page.show()
    try:
        yield host, page, session
    finally:
        close(host)
    assert not host.errors


def independent_indexes(session):
    c = session.config
    with MongoClient(host=c.host, port=c.port, username=c.username, password=c.password,
                     authSource=c.mongo_auth_source, directConnection=True) as client:
        return list(client[c.database].docs.list_indexes())


def save_sheet(qtbot, host, page, name=None):
    page.request_save(); sheet = page._save_dialog
    if name:
        qtbot.keyClicks(sheet.name_edit, name)
    qtbot.waitUntil(lambda: sheet.save_button.isEnabled(), timeout=15000)
    return sheet


def test_real_serial_qt_save_sheet_ordered_keys_and_independent_wire(qtbot, live):
    host, page, session = live
    add_keys(qtbot, page, [("quantity", -1), ("name", 1)])
    sheet = save_sheet(qtbot, host, page, "owned_index")
    assert "createIndex(" in sheet.preview.toPlainText() and "quantity" in sheet.preview.toPlainText()
    sheet.preview_mode.setCurrentIndex(1)
    assert "db.runCommand(" in sheet.preview.toPlainText() and "collectionUUID" in sheet.preview.toPlainText()
    qtbot.mouseClick(sheet.save_button, Qt.MouseButton.LeftButton)
    idle(qtbot, host)
    assert not page.dirty and page.snapshot.exists and page.title == "owned_index"
    wire = []
    host.submit(lambda: independent_indexes(session), wire.extend); idle(qtbot, host)
    row = next(row for row in wire if row["name"] == "owned_index")
    assert list(row["key"].items()) == [("quantity", -1), ("name", 1)]
    assert len(set(host.threads)) == 1


def test_real_rebuild_failure_keeps_dirty_draft_and_shows_actual_completed_steps(qtbot, live):
    host, page, session = live
    host.submit(lambda: session.connection[session.config.database].docs.insert_many([{"a": 1}, {"a": 1}]))
    idle(qtbot, host)
    add_keys(qtbot, page, [("a", 1)])
    sheet = save_sheet(qtbot, host, page, "owned_index")
    qtbot.mouseClick(sheet.save_button, Qt.MouseButton.LeftButton); idle(qtbot, host)
    page.unique_check.click()
    sheet = save_sheet(qtbot, host, page)
    assert sheet.name_edit.isHidden() and "不是事务" in sheet.warning.text()
    assert "dropIndex(" in sheet.preview.toPlainText()
    qtbot.mouseClick(sheet.save_button, Qt.MouseButton.LeftButton); idle(qtbot, host)
    assert page.dirty and not page.loading and page.definition.options["unique"]
    assert "服务器已确认" in page.message.text() and "服务器报错" in page.message.text()
    assert "无目标索引" in page.message.text()
    wire = []; host.submit(lambda: independent_indexes(session), wire.extend); idle(qtbot, host)
    assert [row["name"] for row in wire] == ["_id_"]


def test_existing_suppression_emits_draft_for_fresh_worker_plan_without_reusing_confirmation(qtbot, offline):
    page, session = offline
    snapshot = tools._snapshot(session, "generated", "docs", "existing",
        {"name": "existing", "key": {"a": 1}, "v": 2}, Binary(b'x' * 16, 4))
    page.load_snapshot(snapshot)
    page.suppress_save_preview = True
    page.unique_check.click()
    saved = QSignalSpy(page.saveRequested)
    page.request_save()
    assert saved.count() == 1 and page._save_dialog is None and page.confirmed_plan is None
    assert saved.at(0)[0].options["unique"] and page.dirty


def test_shared_error_formatter_retains_typed_observation_and_acknowledgement_distinctions():
    outcome = tools.IndexOutcome([tools.IndexStep("drop", "docs", "idx", "acknowledged"),
        tools.IndexStep("create", "docs", "idx", "unknown")],
        {"docs": {"available": True, "namespace_uuid": Binary(b'x' * 16, 4),
                  "indexes": [{"name": "idx", "key": {"a": Int64(1)}}]}})
    text = index_error_text(tools.IndexApplyError("synthetic", outcome))
    assert "服务器已确认" in text and "确认未知" in text and "$numberLong" in text
    assert '"subType": "04"' in text


def test_real_delete_requires_permanent_checkbox_before_any_worker_write(qtbot, live):
    host, page, session = live
    add_keys(qtbot, page, [("a", 1)])
    sheet = save_sheet(qtbot, host, page, "owned_index")
    qtbot.mouseClick(sheet.save_button, Qt.MouseButton.LeftButton); idle(qtbot, host)
    plans = []
    snapshot = deepcopy(page.snapshot)
    host.submit(lambda: tools.plan_drop(session, snapshot), plans.append); idle(qtbot, host)
    dialog = MongoIndexDeleteDialog(plans[0], host)
    dialog.open(); dialog.accept()
    assert dialog.isVisible() and not dialog.delete_button.isEnabled()
    accepted = QSignalSpy(dialog.accepted)
    def apply_delete():
        plan = deepcopy(dialog.plan)
        host.submit(lambda: tools.apply_plan(session, plan))
    dialog.accepted.connect(apply_delete)
    option = QStyleOptionButton(); option.initFrom(dialog.permanent_check)
    indicator = dialog.permanent_check.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, dialog.permanent_check)
    qtbot.mouseClick(dialog.permanent_check, Qt.MouseButton.LeftButton, pos=indicator.center())
    assert dialog.delete_button.isEnabled()
    qtbot.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton); idle(qtbot, host)
    assert accepted.count() == 1
    wire = []; host.submit(lambda: independent_indexes(session), wire.extend); idle(qtbot, host)
    assert [row["name"] for row in wire] == ["_id_"]
