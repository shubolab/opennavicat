"""Native aggregation-pipeline workspace tests without database I/O."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from opennavicat.mongodb_pipeline import PipelineDefinition, PipelinePreview, PipelineStage
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
