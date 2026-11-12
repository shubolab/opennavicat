"""Native aggregation-pipeline workspace tests without database I/O."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox

from opennavicat.mongodb_pipeline import (
    PipelineDefinition, PipelinePreview, PipelineStage, parse_explain_reply,
)
from opennavicat.ui.mongo_pipeline import MongoPipelinePage, MongoPipelineSaveDialog


def test_pipeline_page_edits_source_stages_order_enable_and_script(qtbot):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    page.set_sources(["docs", "other"])
    page.source_combo.setCurrentText("docs")
    page.stage_editor.setPlainText('{"_id": {"$gte": 4}}')
    page.operator_combo.setCurrentText("$match")
    page.add_stage()
    page.stage_editor.setPlainText('{"_id": null, "count": {"$sum": 1}}')
    page.operator_combo.setCurrentText("$group")
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

    page.operator_combo.setCurrentText("$group")
    assert page.stage_mode.currentData() == "create"
    page.group_tool.id_edit.setText("$group")
    page.group_tool.add_row("total", "$sum", "$amount")
    assert page.draft().stages[0].body == {
        "_id": "$group", "total": {"$sum": "$amount"}}

    page.operator_combo.setCurrentText("$sort")
    page.sort_tool.add_row("amount", -1)
    assert page.draft().stages[0].body == {"amount": -1}

    page.operator_combo.setCurrentText("$limit")
    page.limit_tool.number.setValue(25)
    assert page.draft().stages[0].body == 25
    page.operator_combo.setCurrentText("$unwind")
    page.unwind_tool.path.setText("$tags")
    page.unwind_tool.preserve.setChecked(True)
    assert page.draft().stages[0].body == {
        "path": "$tags", "preserveNullAndEmptyArrays": True}


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
    assert page.explain_panel.visual_tree.topLevelItemCount() == 1
    root = page.explain_panel.visual_tree.topLevelItem(0)
    assert root.text(0) == "GROUP" and root.child(0).text(0) == "COLLSCAN"
    assert root.child(0).text(3) == "4"
    assert page.explain_panel.stats_table.rowCount() == 2
    assert "unknownMetric" in page.explain_panel.json_view.toPlainText()
    page.explain_panel.search_edit.setText("COLLSCAN")
    assert not root.isHidden() and not root.child(0).isHidden()
    page.explain_panel.search_edit.setText("no-such-node")
    assert root.isHidden()


def test_pipeline_explain_unexpected_shape_and_png_export(qtbot, monkeypatch, tmp_path):
    page = MongoPipelinePage("generated", "demo_catalog")
    qtbot.addWidget(page)
    explain = parse_explain_reply({"ok": 1, "unexpectedPlan": {"futureShape": True}})
    page.show_explain(explain)
    assert page.explain_panel.visual_tree.topLevelItemCount() == 0
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
