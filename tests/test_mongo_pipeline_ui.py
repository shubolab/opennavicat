"""Native aggregation-pipeline workspace tests without database I/O."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from opennavicat.mongodb_pipeline import (
    PipelineDefinition, PipelinePreview, PipelineStage, parse_explain_reply,
)
from opennavicat.mongodb_stage_templates import CREATE_TOOL_STAGES, STAGE_EDITOR_TEMPLATES
from opennavicat.ui.mongo_pipeline import MongoPipelinePage, MongoPipelineSaveDialog


def test_pipeline_page_edits_source_stages_order_enable_and_script(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs", "other"])
    page.source_combo.setCurrentText("docs")
    page.stage_editor.setPlainText('{"_id": {"$gte": 4}}')
    page.operator_combo.setCurrentText("$match")
    page.add_stage()
    page.operator_combo.setCurrentText("$group")
    page.stage_editor.setPlainText('{"_id": null, "count": {"$sum": 1}}')
    definition = page.draft()
    assert definition.source == "docs"
    assert [stage.operator for stage in definition.stages] == ["$match", "$group"]
    page.move_stage(-1)
    assert [stage.operator for stage in page.draft().stages] == ["$group", "$match"]
    assert page.definition.selected_stage == 0
    page.enabled_check.setChecked(False)
    assert page.draft().stages[0].enabled is False
    assert '$group' not in page.script_editor.toPlainText()
    assert '$match' in page.script_editor.toPlainText()


def test_match_create_tool_builds_structured_conditions(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    assert page.stage_mode.currentData() == "create"
    page.match_tool.add_row("_id", "$gte", "4")
    page.match_tool.add_row("active", "$eq", "true")
    assert page.draft().stages[0].body == {
        "_id": {"$gte": 4},
        "active": True,
    }
    page.match_tool.combinator.setCurrentIndex(1)
    assert page.draft().stages[0].body == {"$or": [
        {"_id": {"$gte": 4}}, {"active": True}]}
    page.stage_editor.setPlainText('{"created_at":{"$date":"2026-01-01T00:00:00Z"}}')
    page.stage_mode.setCurrentIndex(page.stage_mode.findData("editor"))
    assert page.draft().stages[0].body["created_at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_pipeline_editor_preserves_and_replaces_reference_mql_comments(qtbot):
    original = '''{
/* Perform a left outer join. */
"from":"customers","localField":"customer","foreignField":"customer","as":"customer_docs"
}'''
    definition = PipelineDefinition(
        source="sales", stages=[PipelineStage(
            "$lookup", {"from": "customers", "localField": "customer",
                        "foreignField": "customer", "as": "customer_docs"},
            reference_mql=original)])
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.load_definition(definition)
    assert page.stage_editor.toPlainText() == original
    assert page.draft().stages[0].reference_mql == original

    edited = '''{
/* Generated edited join. */
"from":"customers","localField":"id","foreignField":"customer","as":"matched"
}'''
    page.stage_editor.setPlainText(edited)
    drafted = page.draft()
    assert drafted.stages[0].body == {
        "from": "customers", "localField": "id", "foreignField": "customer", "as": "matched"}
    assert drafted.stages[0].reference_mql == edited


def test_match_create_tool_preserves_untouched_reference_mql(qtbot):
    original = '''{
/* Filter out generated documents. */
}'''
    definition = PipelineDefinition(
        source="sales", stages=[PipelineStage("$match", {}, reference_mql=original)])
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.load_definition(definition)
    assert page.stage_stack.currentWidget() is page.match_tool
    assert page.draft().stages[0].reference_mql == original
    page.match_tool.add_row("active", "$eq", "true")
    changed = page.draft()
    assert changed.stages[0].body == {"active": True}
    assert changed.stages[0].reference_mql is None


def test_reference_preview_sample_controls_include_all_first_random(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    assert [page.sample_mode.itemText(i) for i in range(page.sample_mode.count())] == [
        "全部", "最初", "随机"]
    assert page.sample_mode.currentData() == "random"
    page.sample_mode.setCurrentIndex(page.sample_mode.findData("all"))
    assert page.draft().sample_mode == "all"
    assert not page.sample_size.isEnabled()
    page.sample_mode.setCurrentIndex(page.sample_mode.findData("first"))
    assert page.draft().sample_mode == "first"
    assert page.sample_size.isEnabled()


def test_pipeline_options_page_preserves_reference_ids_and_unknown_fields(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    editor = page.options_editor
    page.definition.reference_options = {"FutureOption": {"preserved": True}}
    page._render_options()
    page.options_button.setChecked(True)
    assert page.detail_stack.currentWidget() is editor
    editor.allow_disk_combo.setCurrentIndex(editor.allow_disk_combo.findData(-1))
    editor.batch_size_edit.setText("25")
    editor.max_time_edit.setText("1200")
    editor.bypass_validation_combo.setCurrentIndex(editor.bypass_validation_combo.findData(1))
    editor.read_concern_combo.setCurrentIndex(editor.read_concern_combo.findData(3))
    editor.locale_combo.setCurrentText("zh")
    editor.strength_combo.setCurrentIndex(editor.strength_combo.findData(3))
    editor.case_level_combo.setCurrentIndex(editor.case_level_combo.findData(1))
    editor.case_first_combo.setCurrentIndex(editor.case_first_combo.findData(2))
    editor.numeric_ordering_combo.setCurrentIndex(editor.numeric_ordering_combo.findData(1))
    editor.alternate_combo.setCurrentIndex(editor.alternate_combo.findData(2))
    editor.max_variable_combo.setCurrentIndex(editor.max_variable_combo.findData(2))
    editor.backwards_combo.setCurrentIndex(editor.backwards_combo.findData(1))
    editor.normalization_combo.setCurrentIndex(editor.normalization_combo.findData(1))
    editor.hint_editor.setPlainText("generated_index")
    editor.comment_edit.setText("generated_options")
    editor.write_concern_check.setChecked(True)
    editor.w_edit.setText("1")
    editor.journal_combo.setCurrentIndex(editor.journal_combo.findData(1))
    editor.wtimeout_edit.setText("500")
    editor.let_editor.setPlainText('{"tenant":"generated"}')
    definition = page.draft()
    assert definition.reference_options == {
        "FutureOption": {"preserved": True},
        "allowDiskUse": -1,
        "cursorBatchSize": 25,
        "maxTimeMS": 1200,
        "bypassDocumentValidation": 1,
        "readConcernLevel": 3,
        "locale": "zh",
        "caseLevel": 1,
        "caseFirst": 2,
        "strength": 3,
        "numericOrdering": 1,
        "alternate": 2,
        "maxVariable": 2,
        "backwards": 1,
        "normalization": 1,
        "hint": "generated_index",
        "comment": "generated_options",
        "enableWriteConcern": True,
        "w": "1",
        "j": 1,
        "wtimeout": 500,
        "let": '{"tenant":"generated"}',
    }
    assert '"hint": "generated_index"' in page.script_editor.toPlainText()
    assert '"wtimeout": 500' in page.script_editor.toPlainText()


def test_common_stage_create_tools_generate_exact_documents(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    page.operator_combo.setCurrentText("$project")
    assert page.stage_mode.currentData() == "create"
    page.project_tool.add_row("name", "include", "")
    page.project_tool.add_row("computed", "expression", '{"$literal": 5}')
    assert page.draft().stages[0].body == {"name": 1, "computed": {"$literal": 5}}

    page.operator_combo.setCurrentText("$sort")
    page.sort_tool.add_row("amount", -1)
    assert page.draft().stages[0].body == {"amount": -1}

    page.operator_combo.setCurrentText("$limit")
    page.limit_tool.number.setValue(25)
    assert page.draft().stages[0].body == 25


def test_reference_stage_create_tools_and_editor_templates(qtbot):
    assert len(STAGE_EDITOR_TEMPLATES) == 42
    assert CREATE_TOOL_STAGES == {"$match", "$project", "$sort", "$limit", "$skip"}
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    for operator in ("$group", "$addFields", "$set", "$unset", "$unwind", "$count", "$sample"):
        page.operator_combo.setCurrentText(operator)
        assert page.stage_mode.isHidden()
        assert page.stage_stack.currentWidget() is page.stage_editor
        assert page.stage_editor.toPlainText() == STAGE_EDITOR_TEMPLATES[operator]
    page.operator_combo.setCurrentText("$group")
    page.stage_editor.setPlainText('{"_id":"$group","total":{"$sum":"$amount"}}')
    assert page.draft().stages[0].body == {
        "_id": "$group", "total": {"$sum": "$amount"}}


def test_stage_drag_target_moves_stage_in_definition(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    page.add_stage()
    page.operator_combo.setCurrentText("$group")
    page.stage_editor.setPlainText('{"_id":"$group"}')
    page.move_stage_to(1, 0)
    assert [stage.operator for stage in page.draft().stages] == ["$group", "$match"]
    assert page.definition.selected_stage == 0


def test_pipeline_page_delete_requires_confirmation(qtbot, monkeypatch):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.add_stage()
    assert len(page.definition.stages) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel)
    page.delete_stage()
    assert len(page.definition.stages) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Yes)
    page.delete_stage()
    assert len(page.definition.stages) == 1


def test_pipeline_preview_and_execution_tables_preserve_ejson(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs"])
    page.source_combo.setCurrentText("docs")
    row = '{"_id":{"$numberLong":"5"},"amount":10}'
    grouped = '{"_id":null,"total":{"$numberLong":"10"}}'
    page.show_preview(PipelinePreview(
        [row], [{"index": 0, "operator": "$group", "rows": [grouped], "truncated": False}],
        [grouped]))
    assert page.output_panel.table.rowCount() == 1
    assert page.results.rowCount() == 1
    page.show_execution([grouped])
    assert page.last_rows == [grouped]
    assert page.export_action.isEnabled()


def test_pipeline_visual_explain_plan_views_and_search(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    explain = parse_explain_reply({
        "executionStats": {
            "executionTimeMillis": 17,
            "executionStages": {
                "stage": "GROUP",
                "nReturned": 1,
                "executionTimeMillisEstimate": 12,
                "inputStage": {
                    "stage": "COLLSCAN",
                    "nReturned": 3,
                    "docsExamined": 4,
                    "executionTimeMillisEstimate": 10,
                },
            },
        },
        "unknownMetric": {"preserved": True},
        "ok": 1,
    })
    page.show_explain(explain)
    assert page.tabs.currentWidget() is page.explain_panel
    assert page.tabs.tabText(page.explain_tab_index) == "解释 1"
    canvas = page.explain_panel.explain_canvas
    assert [item.node.stage for item in canvas._items] == ["GROUP", "COLLSCAN"]
    assert canvas._items[0].pos().x() < canvas._items[1].pos().x()
    assert canvas._items[1].node.docs_examined == 4
    canvas.select_index(1)
    assert canvas._selected.node.stage == "COLLSCAN"
    assert "docsExamined" in canvas.detail_view.toPlainText()
    assert page.explain_panel.stats_table.rowCount() == 2
    assert "unknownMetric" in page.explain_panel.json_view.toPlainText()
    page.explain_panel.search_edit.setText("COLLSCAN")
    assert canvas._items[0].opacity() < 1 and canvas._items[1].opacity() == 1
    page.explain_panel.search_edit.setText("no-such-node")
    assert all(item.opacity() < 1 for item in canvas._items)


def test_pipeline_explain_unexpected_shape_and_png_export(qtbot, monkeypatch, tmp_path):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    explain = parse_explain_reply({"ok": 1, "unexpectedPlan": {"futureShape": True}})
    page.show_explain(explain)
    assert page.explain_panel.explain_canvas._items == []
    assert "unexpectedPlan" in page.explain_panel.json_view.toPlainText()
    target = tmp_path / "plan"
    monkeypatch.setattr(
        "opennavicat.ui.mongo_pipeline.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "PNG image (*.png)"))
    page.explain_panel.export_image_button.click()
    image = QImage(str(target) + ".png")
    assert not image.isNull()


def test_pipeline_save_dialog_requires_name_and_keeps_definition():
    definition = PipelineDefinition(
        name="", source="docs", stages=[PipelineStage("$match", {"active": True})])
    dialog = MongoPipelineSaveDialog(definition)
    try:
        assert not dialog.save_button.isEnabled()
        assert "db.getCollection" in dialog.preview.toPlainText()
        dialog.name_edit.setText("generated_pipeline")
        saved = dialog.definition()
        assert saved.name == "generated_pipeline"
        assert saved.source == "docs"
        assert saved.stages == definition.stages
    finally:
        dialog.close()
