"""Qt object documents and their reviewed, serial SQLite application boundary."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import sqlite3
import threading

import pytest
from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import QDialog, QFormLayout

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.schema import IndexTerm
from opennavicat import schema_objects as objects
from opennavicat.tasks import Task
from opennavicat.ui.schema_object_designer import IndexDesignerPage, TriggerDesignerPage


@pytest.fixture
def index_page(qtbot):
    page = IndexDesignerPage("fixture")
    qtbot.addWidget(page)
    page.resize(1050, 700)
    page.show()
    yield page
    if page._save_dialog:
        page._save_dialog.reject()


@pytest.fixture
def trigger_page(qtbot):
    page = TriggerDesignerPage("fixture")
    qtbot.addWidget(page)
    page.resize(1050, 700)
    page.show()
    yield page
    if page._save_dialog:
        page._save_dialog.reject()


def index_snapshot():
    definition = objects.IndexObjectDefinition("ix_items_label", ["label"], table="items",
        source_name="ix_items_label", raw_sql="CREATE INDEX ix_items_label ON items(label COLLATE NOCASE DESC)",
        terms=[IndexTerm("label", "NOCASE", True)])
    return objects.ObjectSnapshot("main", definition.name, "index", definition,
        definition.raw_sql + ";", "original-fingerprint")


def make_plan(source, definition, sql="CREATE INDEX planned ON items(label)"):
    return objects.ObjectPlan(deepcopy(source), deepcopy(definition), [sql], sql + ";", True)


def test_index_inline_terms_are_ordered_structured_and_metadata_is_not_an_edit(qtbot, index_page):
    page = index_page
    source = index_snapshot()
    owners = []
    page.ownerChanged.connect(lambda kind, owner: owners.append((kind, owner)))
    page.load_snapshot(source)
    page.set_owners(["items", "other"])
    assert page.set_columns("items", [{"name": "id"}, {"name": "label"}])
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == ["常规", "SQL 预览"]
    assert not page.dirty and page.definition == source.definition
    assert owners[-1] == ("table", "items")
    assert page.fields.cellWidget(0, 1).currentText() == "NOCASE"
    assert page.fields.cellWidget(0, 2).currentText() == "DESC"
    qtbot.mouseClick(page.toolbar.widgetForAction(page.add_action), Qt.MouseButton.LeftButton)
    page.fields.cellWidget(1, 0).setCurrentText("id")
    page.fields.cellWidget(1, 1).setCurrentText("BINARY")
    page.fields.cellWidget(1, 2).setCurrentText("ASC")
    assert [(term.column, term.collation, term.descending) for term in page.definition.terms] == [("label", "NOCASE", True), ("id", "BINARY", False)]
    assert page.definition.columns == ["label", "id"]
    assert source.definition.columns == ["label"]
    page.table_combo.setCurrentText("other")
    assert owners[-1] == ("table", "other")
    assert not page.set_columns("items", ["old response"])
    assert page.set_columns("other", ["id", "label"])
    assert page.definition.source_name == source.definition.source_name
    assert page.definition.raw_sql == source.definition.raw_sql


def test_read_only_auto_and_expression_indexes_keep_original_definitions(qtbot, index_page):
    for explanation in ("SQLite 自动索引由约束维护", "表达式或部分索引只读保留"):
        source = index_snapshot()
        source.read_only = True
        source.issues = [explanation]
        if "表达式" in explanation:
            source.definition.terms = [IndexTerm(expression="lower(label)")]
            source.definition.where = "id > 0"
        index_page.load_snapshot(source)
        before = index_page.draft()
        index_page.add_field()
        index_page.delete_field()
        index_page.request_save()
        assert index_page.definition == before and index_page._save_dialog is None
        assert not index_page.general.isEnabled() and not index_page.save_action.isEnabled()
        assert explanation in index_page.message.text()
        index_page.tabs.setCurrentIndex(1)
        index_page.preview_mode.setCurrentText("DDL")
        assert index_page.sql_preview.toPlainText() == source.ddl


def test_index_save_sheet_rejects_old_plan_and_emits_a_detached_confirmed_request(qtbot, index_page):
    page = index_page
    page.load_snapshot(objects.new_index(table="items"))
    page.set_columns("items", ["label"])
    page.add_field()
    page.fields.cellWidget(0, 0).setCurrentText("label")
    requests, saves = [], []
    page.previewRequested.connect(requests.append)
    page.saveRequested.connect(saves.append)
    page.request_save()
    dialog = page._save_dialog
    form = next(form for form in dialog.findChildren(QFormLayout) if form.labelForField(dialog.name_edit))
    assert form.labelForField(dialog.name_edit).text() == "索引名称"
    dialog.name_edit.setText("old_name")
    previous = requests[-1]
    dialog.name_edit.setText("new_name")
    current = requests[-1]
    page.show_plan(make_plan(page.snapshot, previous))
    assert not dialog.save_button.isEnabled()
    reviewed = make_plan(page.snapshot, current)
    page.show_plan(reviewed)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert len(saves) == 1 and saves[0].name == "new_name"
    assert page.confirmed_plan is reviewed
    saves[0].terms[0].column = "mutated_outside"
    assert page.definition.terms[0].column == "label"
    page.show_error(ValueError("apply failed"))
    page.fields.cellWidget(0, 2).setCurrentText("DESC")
    assert page.definition.terms[0].descending and page.dirty


def test_trigger_single_event_update_of_controls_and_view_timing(qtbot, trigger_page):
    page = trigger_page
    source = objects.new_trigger(table="items")
    page.load_snapshot(source)
    page.set_owners(["items"], ["v_items"])
    page.set_columns("items", ["id", "label"])
    assert [page.tabs.tabText(i) for i in range(3)] == ["常规", "定义", "SQL 预览"]
    assert page.event_checks["INSERT"].isChecked()
    assert not page.update_columns.isEnabled()
    qtbot.mouseClick(page.event_checks["UPDATE"], Qt.MouseButton.LeftButton)
    assert page.definition.event == "UPDATE"
    assert sum(check.isChecked() for check in page.event_checks.values()) == 1
    assert page.update_columns.isEnabled()
    qtbot.mouseClick(page.select_all_button, Qt.MouseButton.LeftButton)
    assert page.definition.update_columns == ["id", "label"]
    qtbot.mouseClick(page.deselect_all_button, Qt.MouseButton.LeftButton)
    assert page.definition.update_columns == []
    page.update_columns.item(1).setCheckState(Qt.CheckState.Checked)
    page.when_edit.setText("NEW.label <> OLD.label")
    page.body_editor.setPlainText("BEGIN SELECT 1; END")
    assert page.definition.when == "NEW.label <> OLD.label"
    assert page.definition.body == "BEGIN SELECT 1; END"
    qtbot.mouseClick(page.event_checks["INSERT"], Qt.MouseButton.LeftButton)
    assert page.definition.update_columns == [] and not page.update_columns.isEnabled()
    page.owner_type_combo.setCurrentText("View")
    page.table_combo.setCurrentText("v_items")
    assert page.owner_type == "view" and page.definition.timing == "INSTEAD OF"
    assert page.timing_combo.count() == 1
    assert not page.set_columns("items", ["old"], "table")
    assert page.set_columns("v_items", ["id", "label"], "view")
    assert source.definition.owner_type == "table" and source.definition.body != page.definition.body


def test_existing_trigger_load_select_and_busy_do_not_rewrite_hidden_properties(qtbot, trigger_page):
    definition = objects.TriggerObjectDefinition("items_changed", timing="AFTER", event="UPDATE",
        table="items", update_columns=[" label "], when="NEW.id > 0", body="BEGIN SELECT 1; END",
        source_name="items_changed", raw_sql='CREATE TRIGGER "items_changed" AFTER UPDATE OF " label " ON items BEGIN SELECT 1; END')
    source = objects.ObjectSnapshot("main", definition.name, "trigger", definition, definition.raw_sql, "original")
    page = trigger_page
    page.load_snapshot(source)
    page.set_owners(["items"])
    page.set_columns("items", ["id", " label "])
    assert not page.dirty and page.definition == source.definition
    assert page.update_columns.item(1).text() == " label "
    assert page.update_columns.item(1).checkState() == Qt.CheckState.Checked
    page.set_busy(True)
    assert page.body_editor.isReadOnly() and not page.general.isEnabled()
    page.set_busy(False)
    assert not page.body_editor.isReadOnly() and not page.dirty
    page.set_read_only(True)
    assert page.body_editor.isReadOnly() and not page.save_action.isEnabled()


def test_cancel_sheet_keeps_index_draft_without_requesting_application(qtbot, index_page):
    index_page.load_snapshot(index_snapshot())
    index_page.unique_check.setChecked(True)
    requests = []
    index_page.saveRequested.connect(requests.append)
    index_page.request_save()
    dialog = index_page._save_dialog
    index_page.show_error(ValueError("planner refuses unsafe declaration"))
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.reject()
    assert index_page.dirty and index_page.definition.unique and not requests


def test_save_preview_waits_for_worker_without_blocking_gui(qtbot, index_page):
    page = index_page
    page.load_snapshot(index_snapshot())
    page.unique_check.setChecked(True)
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    tasks = []
    entered, release = threading.Event(), threading.Event()
    def preview(definition):
        source = deepcopy(page.snapshot)  # capture widgets/data on the GUI thread.
        def work():
            entered.set()
            release.wait(5)
            return make_plan(source, definition)
        task = Task(work)
        tasks.append(task)
        task.signals.succeeded.connect(page.show_plan)
        pool.start(task)
    page.previewRequested.connect(preview)
    page.request_save()
    try:
        qtbot.waitUntil(entered.is_set)
        processed = []
        QTimer.singleShot(0, lambda: processed.append(True))
        qtbot.waitUntil(lambda: bool(processed))
        assert page._save_dialog.isVisible() and not page._save_dialog.save_button.isEnabled()
    finally:
        release.set()
    qtbot.waitUntil(lambda: page._save_dialog.save_button.isEnabled())
    pool.waitForDone(5000)
    page._save_dialog.reject()


class _SerialObjects:
    def __init__(self, page, path):
        self.page = page
        self.session = DatabaseSession(ConnectionConfig("fixture", "Owned fixture", database=str(path)))
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.tasks, self.errors = set(), []
        page.previewRequested.connect(self.preview)
        page.saveRequested.connect(self.save)

    def submit(self, work, success):
        task = Task(work)
        self.tasks.add(task)
        task.signals.succeeded.connect(success)
        task.signals.failed.connect(self.failed)
        task.signals.finished.connect(lambda: self.tasks.discard(task))
        self.pool.start(task)

    def failed(self, error):
        self.errors.append(error)
        self.page.show_error(error)

    def preview(self, definition):
        source = deepcopy(self.page.snapshot)
        planner = objects.plan_index if self.page.kind == "index" else objects.plan_trigger
        self.submit(lambda: planner(self.session, source, definition), self.page.show_plan)

    def save(self, definition):
        reviewed = deepcopy(self.page.confirmed_plan)
        self.page.set_busy(True)
        self.submit(lambda: objects.apply_plan(self.session, reviewed), self.page.save_succeeded)

    def close(self, qtbot):
        qtbot.waitUntil(lambda: not self.tasks and not self.pool.activeThreadCount(), timeout=10000)
        self.session.close()


def fixture_database(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE items(id INTEGER PRIMARY KEY,label TEXT);
            INSERT INTO items VALUES(1,'same'),(2,'same');
            CREATE TABLE audit(item_id INTEGER,label TEXT);
            CREATE VIEW v_items AS SELECT id,label FROM items;
        """)


def confirm(qtbot, page, name=None):
    page.request_save()
    dialog = page._save_dialog
    assert dialog is not None
    if name:
        dialog.name_edit.setText(name)
    qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)


def test_real_serial_index_create_and_unique_failure_preserve_old_index(qtbot, index_page, tmp_path):
    path = tmp_path / "index.sqlite3"
    fixture_database(path)
    page = index_page
    page.load_snapshot(objects.new_index(table="items"))
    page.set_owners(["items"])
    page.set_columns("items", ["id", "label"])
    page.add_field()
    page.fields.cellWidget(0, 0).setCurrentText("label")
    page.fields.cellWidget(0, 1).setCurrentText("NOCASE")
    page.fields.cellWidget(0, 2).setCurrentText("DESC")
    queue = _SerialObjects(page, path)
    try:
        page.request_save()
        dialog = page._save_dialog
        dialog.name_edit.setText("ix_ui_label")
        qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
        with closing(sqlite3.connect(path)) as db:
            assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='ix_ui_label'").fetchall()
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.object_name == "ix_ui_label" and not page.dirty)
        with closing(sqlite3.connect(path)) as db:
            terms = db.execute('PRAGMA index_xinfo("ix_ui_label")').fetchall()
            assert terms[0][2:5] == ("label", 1, "NOCASE")
            original = db.execute("SELECT sql FROM sqlite_schema WHERE name='ix_ui_label'").fetchone()[0]
        page.unique_check.setChecked(True)
        confirm(qtbot, page)
        qtbot.waitUntil(lambda: bool(queue.errors))
        assert page.dirty and not page.loading
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT sql FROM sqlite_schema WHERE name='ix_ui_label'").fetchone()[0] == original
            assert db.execute("SELECT count(*) FROM items").fetchone() == (2,)
    finally:
        queue.close(qtbot)


@pytest.mark.parametrize("owner_type", ["table", "view"])
def test_real_serial_table_and_view_triggers_execute_owned_effects(qtbot, trigger_page, tmp_path, owner_type):
    path = tmp_path / "trigger.sqlite3"
    fixture_database(path)
    page = trigger_page
    page.load_snapshot(objects.new_trigger(table="items" if owner_type == "table" else "v_items", owner_type=owner_type))
    page.set_owners(["items"], ["v_items"])
    page.set_columns(page.table, ["id", "label"], owner_type)
    if owner_type == "table":
        page.timing_combo.setCurrentText("AFTER")
        page.body_editor.setPlainText("BEGIN INSERT INTO audit(item_id,label) VALUES(NEW.id,NEW.label); END")
    else:
        page.body_editor.setPlainText("BEGIN INSERT INTO items(id,label) VALUES(NEW.id,NEW.label); END")
    page.when_edit.setText("NEW.label IS NOT NULL")
    queue = _SerialObjects(page, path)
    try:
        confirm(qtbot, page, "trigger_from_ui")
        qtbot.waitUntil(lambda: page.object_name == "trigger_from_ui" and not page.dirty)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("INSERT INTO " + ("items" if owner_type == "table" else "v_items") + " VALUES(9,'effect')")
            actual = db.execute("SELECT item_id,label FROM audit" if owner_type == "table" else "SELECT id,label FROM items WHERE id=9").fetchall()
            assert actual == [(9, "effect")]
        assert not queue.errors
    finally:
        queue.close(qtbot)
