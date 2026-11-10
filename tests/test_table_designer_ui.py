"""Real Qt draft/preview/save workflows; schema execution belongs to the owner."""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import sqlite3

import pytest
from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QToolButton

from opennavicat.database import ConnectionConfig, DatabaseSession
from opennavicat.schema import (
    ColumnDefinition, IndexDefinition, IndexTerm, SchemaPlan, TableDefinition,
    TableOptions, TableSnapshot, apply_plan, load_table, new_table, plan_table,
)
from opennavicat.tasks import Task
from opennavicat.ui.table_designer import (
    ForeignKeyFieldsDialog, IndexFieldsDialog, TableDesignerPage, TableSaveDialog,
)


@pytest.fixture
def page(qtbot):
    result = TableDesignerPage("fixture", "main", "items")
    result.resize(1080, 720)
    qtbot.addWidget(result)
    result.show()
    yield result
    if result._save_dialog:
        result._save_dialog.reject()
    result.close()


def snapshot():
    definition = TableDefinition("items", [
        ColumnDefinition("id", "INTEGER", pk_order=1, source_name="id", raw_sql='"id" INTEGER PRIMARY KEY'),
        ColumnDefinition("name", "VARCHAR(80)", nullable=False, default="'unknown'", collation="NOCASE", source_name="name"),
        ColumnDefinition("total", "REAL", generated_type="STORED", generated_expression="length(name)", source_name="total"),
    ], indexes=[IndexDefinition("ix_items_name", ["name"], terms=[IndexTerm("name", "NOCASE", True)])],
       options=TableOptions(strict=False), opaque_constraints=['CHECK(length("name") > 0)'],
       primary_key_name="pk_items", constraint_order=[("opaque", "0")])
    return TableSnapshot("main", "items", definition, 'CREATE TABLE "items" ("id" INTEGER PRIMARY KEY, "name" TEXT);', "source-fingerprint")


def plan(source, definition, sql="ALTER TABLE items ADD COLUMN note TEXT"):
    return SchemaPlan(deepcopy(source), deepcopy(definition), [sql], "CREATE TABLE revised (...);", True)


def test_existing_sqlite_snapshot_is_detached_and_preserves_unexposed_constraints(qtbot, page, tmp_path):
    path = tmp_path / "designer.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT COLLATE NOCASE NOT NULL ON CONFLICT FAIL DEFAULT 'draft',
                parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED,
                label TEXT GENERATED ALWAYS AS (upper(name)) STORED,
                CONSTRAINT uq_name UNIQUE(name COLLATE NOCASE DESC) ON CONFLICT IGNORE,
                CHECK(length(name) > 1)
            );
            CREATE INDEX ix_name ON items(name COLLATE NOCASE DESC);
            CREATE INDEX ix_expression ON items(lower(name)) WHERE parent_id IS NOT NULL;
            CREATE TRIGGER item_update AFTER UPDATE OF name ON items
              WHEN NEW.name <> OLD.name BEGIN SELECT 1; END;
            INSERT INTO items(name) VALUES('initial');
        ''')
    session = DatabaseSession(ConnectionConfig("fixture", "Fixture", database=str(path)))
    try:
        session.connect()
        source = load_table(session, "main", "items")
    finally:
        session.close()
    assert not source.issues
    original = deepcopy(source)
    page.load_snapshot(source)
    assert page.tabs.count() == 7
    assert [page.tabs.tabText(index) for index in range(7)] == ["字段", "索引", "外键", "唯一键", "触发器", "选项", "SQL 预览"]
    assert not page.dirty
    assert page.definition == source.definition
    page.fields.setCurrentIndex(page.field_model.index(1, 0))
    assert page.default_edit.currentText() == "'draft'"
    assert page.collation_combo.currentText() == "NOCASE"
    assert page.notnull_conflict_combo.currentText() == "FAIL"
    page.tabs.setCurrentIndex(4)
    page.triggers.setCurrentCell(0, 0)
    assert "SELECT 1" in page.trigger_body.toPlainText()
    assert page.trigger_update_columns.text() == "name"
    assert not page.dirty  # selection and rendering must not rewrite a declaration.
    page.tabs.setCurrentIndex(0)
    page.fields.setCurrentIndex(page.field_model.index(1, 0))
    page.default_edit.setCurrentText("'changed'")
    assert page.dirty
    assert source == original
    assert page.draft().opaque_constraints == original.definition.opaque_constraints
    assert page.draft().constraint_order == original.definition.constraint_order
    assert page.draft().foreign_keys[0].initially_deferred
    complex_index = next(i for i, record in enumerate(page.definition.indexes) if record.where)
    holder = page.indexes.cellWidget(complex_index, 1)
    assert not holder.findChild(QToolButton).isEnabled()
    assert "lower(name)" in page.definition.indexes[complex_index].raw_sql


def test_field_edits_native_type_delegate_properties_pk_and_busy_guard(qtbot, page):
    page.load_snapshot(snapshot())
    page.fields.setCurrentIndex(page.field_model.index(1, 1))
    page.fields.edit(page.field_model.index(1, 1))
    editor = page.fields.viewport().findChild(QComboBox)
    assert editor is not None and editor.isEditable()
    editor.setCurrentText("DECIMAL")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: page.definition.columns[1].type == "DECIMAL(80)")
    assert page.field_model.setData(page.field_model.index(1, 2), "12")
    assert page.field_model.setData(page.field_model.index(1, 3), "2")
    assert page.definition.columns[1].type == "DECIMAL(12,2)"
    assert not page.field_model.setData(page.field_model.index(1, 2), "12); DROP TABLE items")
    page.fields.setCurrentIndex(page.field_model.index(1, 0))
    page.generated_type_combo.setCurrentText("VIRTUAL")
    page.generated_expression_edit.setText("length(name)")
    assert page.definition.columns[1].generated_expression == "length(name)"
    page.toggle_primary_key()
    assert [column.pk_order for column in page.definition.columns] == [1, 2, 0]
    page.fields.setCurrentIndex(page.field_model.index(0, 0))
    page.toggle_primary_key()
    assert [column.pk_order for column in page.definition.columns] == [0, 1, 0]
    assert page.title.endswith("— 已编辑")
    before = page.draft()
    page.set_busy(True, "正在保存…")
    page.add_record()
    page.delete_record()
    assert page.definition == before
    assert not page.tabs.isEnabled() and not page.save_action.isEnabled()
    page.show_error(ValueError("schema changed"))
    assert not page.loading and page.dirty
    assert page.message.text() == "schema changed"


def test_compound_index_field_dialog_preserves_collation_sort_and_cancel(qtbot, page):
    source = snapshot()
    page.load_snapshot(source)
    page.tabs.setCurrentIndex(1)
    record = page.definition.indexes[0]
    def edit_fields():
        dialog = next(widget for widget in page.findChildren(IndexFieldsDialog) if widget.isVisible())
        assert dialog.table.cellWidget(0, 2).currentText() == "DESC"
        dialog.add_term({"column": "id", "collation": "BINARY", "descending": False})
        qtbot.mouseClick(dialog.buttons.button(dialog.buttons.StandardButton.Ok), Qt.MouseButton.LeftButton)
    QTimer.singleShot(0, edit_fields)
    page.edit_index_fields("indexes", record)
    assert [(term.column, term.collation, term.descending) for term in record.terms] == [("name", "NOCASE", True), ("id", "BINARY", False)]
    assert record.columns == ["name", "id"]
    assert source.definition.indexes[0].columns == ["name"]
    before = deepcopy(record)
    def cancel_fields():
        dialog = next(widget for widget in page.findChildren(IndexFieldsDialog) if widget.isVisible())
        dialog.table.cellWidget(0, 0).setCurrentText("other")
        dialog.reject()
    QTimer.singleShot(0, cancel_fields)
    page.edit_index_fields("indexes", record)
    assert record == before


def test_new_table_starts_blank_and_save_sheet_waits_for_current_named_plan(qtbot, page):
    page.load_snapshot(new_table())
    assert page.fields.currentIndex().row() == 0
    assert page.definition.columns[0].name == ""
    assert page.dirty and page.title.startswith("无标题")
    page.field_model.setData(page.field_model.index(0, 0), "id")
    page.field_model.setData(page.field_model.index(0, 1), "INTEGER")
    requests, saves = [], []
    page.previewRequested.connect(requests.append)
    page.saveRequested.connect(saves.append)
    page.request_save()
    dialog = page._save_dialog
    qtbot.waitUntil(lambda: dialog.isVisible())
    assert not dialog.save_button.isEnabled()
    assert not dialog.suppress_preview_check.isChecked()
    dialog.name_edit.setText("old_name")
    old = requests[-1]
    dialog.name_edit.setText("new_name")
    current = requests[-1]
    page.show_plan(plan(page.snapshot, old, "CREATE TABLE old_name (id INTEGER)"))
    assert not dialog.save_button.isEnabled()
    current_plan = plan(page.snapshot, current, "CREATE TABLE new_name (id INTEGER)")
    page.show_plan(current_plan)
    assert dialog.save_button.isEnabled()
    assert "new_name" in dialog.preview.toPlainText()
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert len(saves) == 1 and saves[0].name == "new_name"
    assert page.confirmed_plan is current_plan
    assert page.definition.name == "new_name"
    # A failed database apply must leave subsequent cell edits bound to this
    # named draft, rather than to a discarded copy from before the save sheet.
    page.show_error(ValueError("temporary failure"))
    page.field_model.setData(page.field_model.index(0, 0), "still_editable")
    assert page.definition.columns[0].name == "still_editable"
    saves[0].columns[0].name = "outside_mutation"
    assert page.definition.columns[0].name == "still_editable"
    assert page.snapshot.definition.name == ""


def test_existing_save_sheet_cancel_and_planner_error_never_request_apply(qtbot, page):
    page.load_snapshot(snapshot())
    page.field_model.setData(page.field_model.index(1, 0), "renamed")
    saves = []
    page.saveRequested.connect(saves.append)
    page.request_save()
    dialog = page._save_dialog
    assert dialog.name_edit.isHidden()
    page.show_error(ValueError("dependent view cannot be safely rewritten"))
    assert "dependent view" in dialog.preview.toPlainText()
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert not saves and dialog.isVisible()
    dialog.reject()
    assert page.dirty and page.definition.columns[1].name == "renamed"
    assert not saves


def test_async_preview_keeps_gui_responsive_and_does_not_access_widgets_in_worker(qtbot, page):
    import threading
    page.load_snapshot(snapshot())
    page.field_model.setData(page.field_model.index(1, 0), "renamed")
    entered, release = threading.Event(), threading.Event()
    pool, tasks = QThreadPool(), []
    pool.setMaxThreadCount(1)
    def preview(definition):
        source = deepcopy(page.snapshot)  # capture on the GUI thread.
        def work():
            entered.set()
            release.wait(5)
            return plan(source, definition)
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
        assert page._save_dialog.isVisible()
        assert not page._save_dialog.save_button.isEnabled()
    finally:
        release.set()
    qtbot.waitUntil(lambda: page._save_dialog.save_button.isEnabled())
    pool.waitForDone(5000)
    page._save_dialog.reject()


def test_unsupported_snapshot_is_visible_read_only_and_never_emits_save(qtbot, page):
    source = snapshot()
    source.issues = ["Virtual table module is not supported by this designer"]
    page.load_snapshot(source)
    saves = []
    page.saveRequested.connect(saves.append)
    assert "只读" in page.message.text()
    assert not page.field_model.flags(page.field_model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    assert not page.field_model.setData(page.field_model.index(0, 0), "bad")
    before = page.draft()
    page.add_record()
    page.delete_record()
    page.request_save()
    assert page.definition == before and not saves


def test_save_commits_active_native_name_editor_before_capturing_draft(qtbot, page):
    page.load_snapshot(snapshot())
    index = page.field_model.index(1, 0)
    page.fields.setCurrentIndex(index)
    page.fields.edit(index)
    from PySide6.QtWidgets import QLineEdit
    editor = page.fields.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("active_edit")
    page.request_save()
    assert page._save_dialog.definition.columns[1].name == "active_edit"
    page._save_dialog.reject()


def test_foreign_key_field_pairs_are_ordered_identifiers(qtbot):
    dialog = ForeignKeyFieldsDialog(["tenant id", "customer id"], ["tenant id"], ["tenant id"])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.add_pair("customer id", "id")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values() == (["tenant id", "customer id"], ["tenant id", "id"])


def test_identifier_whitespace_is_not_rewritten_by_native_editors(qtbot, page):
    source = snapshot()
    source.definition.columns[1].name = " name "
    page.load_snapshot(source)
    page.field_model.setData(page.field_model.index(1, 0), " name ")
    assert not page.dirty
    index = IndexFieldsDialog([" name "], [{"column": " name ", "collation": "", "descending": False}])
    qtbot.addWidget(index)
    assert index.terms()[0]["column"] == " name "
    foreign = ForeignKeyFieldsDialog([" name "], [" name "], [" key "])
    qtbot.addWidget(foreign)
    assert foreign.values() == ([" name "], [" key "])


class _SerialDesigner:
    """The same ownership boundary as MainWindow's serial task queue."""

    def __init__(self, page, path):
        self.page = page
        self.session = DatabaseSession(ConnectionConfig("fixture", "Fixture", database=str(path)))
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.tasks = set()
        self.errors = []
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
        self.submit(lambda: plan_table(self.session, source, definition), self.page.show_plan)

    def save(self, definition):
        reviewed = self.page.confirmed_plan
        self.page.set_busy(True, "正在保存…")
        self.submit(lambda: apply_plan(self.session, reviewed), self.page.save_succeeded)

    def close(self, qtbot):
        qtbot.waitUntil(lambda: not self.tasks and not self.pool.activeThreadCount(), timeout=10000)
        self.session.close()


def test_new_table_real_background_preview_confirm_apply_and_reload(qtbot, page, tmp_path):
    path = tmp_path / "new.sqlite3"
    source = new_table()
    page.load_snapshot(source)
    queue = _SerialDesigner(page, path)
    try:
        page.field_model.setData(page.field_model.index(0, 0), "id")
        page.field_model.setData(page.field_model.index(0, 1), "INTEGER")
        page.toggle_primary_key()
        page.request_save()
        dialog = page._save_dialog
        dialog.name_edit.setText("created_with_ui")
        qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
        assert "CREATE TABLE" in dialog.preview.toPlainText()
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT count(*) FROM sqlite_schema WHERE name='created_with_ui'").fetchone() == (0,)
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: page.table == "created_with_ui" and not page.dirty, timeout=10000)
        with closing(sqlite3.connect(path)) as db:
            info = db.execute('PRAGMA table_info("created_with_ui")').fetchall()
            assert info[0][1:3] == ("id", "INTEGER") and info[0][5] == 1
        assert page.snapshot.exists and not queue.errors
    finally:
        queue.close(qtbot)


def test_existing_background_rebuild_preserves_data_constraints_and_failure_rolls_back(qtbot, page, tmp_path):
    path = tmp_path / "existing.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT UNIQUE CHECK(length(name)>1));
            CREATE INDEX ix_name ON items(name COLLATE NOCASE DESC);
            CREATE TRIGGER items_updated AFTER UPDATE ON items BEGIN SELECT 1; END;
            INSERT INTO items VALUES(1,'first'),(2,'second');
        ''')
    queue = _SerialDesigner(page, path)
    try:
        queue.submit(lambda: load_table(queue.session, "main", "items"), page.load_snapshot)
        qtbot.waitUntil(lambda: page.snapshot is not None)
        page.add_record()
        row = page.field_model.rowCount() - 1
        page.field_model.setData(page.field_model.index(row, 0), "note")
        page.fields.setCurrentIndex(page.field_model.index(row, 0))
        page.default_edit.setCurrentText("'kept'")
        page.request_save()
        dialog = page._save_dialog
        qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
        assert "BEGIN" in dialog.preview.toPlainText() or "CREATE TABLE" in dialog.preview.toPlainText()
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not page.dirty and not page.loading, timeout=10000)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT * FROM items ORDER BY id").fetchall() == [(1, "first", "kept"), (2, "second", "kept")]
            before = db.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall()
            assert any(entry[1] == "ix_name" and "DESC" in entry[2] for entry in before)
            assert any(entry[1] == "items_updated" for entry in before)
            assert "CHECK(length(name)>1)" in next(entry[2] for entry in before if entry[1] == "items")
        page.add_record()
        row = page.field_model.rowCount() - 1
        page.field_model.setData(page.field_model.index(row, 0), "required")
        page.field_model.setData(page.field_model.index(row, 4), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        page.request_save()
        dialog = page._save_dialog
        qtbot.waitUntil(lambda: dialog.save_button.isEnabled(), timeout=10000)
        qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: bool(queue.errors), timeout=10000)
        assert page.dirty and not page.loading
        assert "rolled back" in str(queue.errors[-1])
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall() == before
            assert db.execute("SELECT count(*) FROM items").fetchone() == (2,)
    finally:
        queue.close(qtbot)
