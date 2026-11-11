"""Detached MongoDB view designer tests without database I/O."""
from __future__ import annotations

from copy import deepcopy

import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_views import ViewDefinition, ViewPlan, ViewSnapshot
from opennavicat.ui.mongo_view_designer import MongoViewDesignerPage, MongoViewDropDialog, MongoViewSaveDialog


@pytest.fixture
def page(qtbot):
    widget = MongoViewDesignerPage('generated', 'demo_catalog')
    qtbot.addWidget(widget)
    widget.load_snapshot(ViewSnapshot('demo_catalog', '', ViewDefinition(), exists=False))
    widget.set_sources(['docs', 'measurements'])
    return widget


def test_new_view_requires_name_source_and_document_pipeline(qtbot, page):
    assert not page.save_action.isEnabled()
    page.name_edit.setText('active_docs')
    page.source_combo.setCurrentText('docs')
    page.pipeline_editor.setPlainText('{bad')
    assert 'pipeline' in page._invalid and not page.save_action.isEnabled()
    with pytest.raises(DatabaseError, match='Pipeline'):
        page.draft()
    page.pipeline_editor.setPlainText('[{"$match": {"active": true}}]')
    assert not page._invalid and page.save_action.isEnabled()
    assert page.draft() == ViewDefinition('active_docs', 'docs', [{'$match': {'active': True}}])


def test_view_save_sheet_requires_exact_plan(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append); page.saveRequested.connect(saves.append)
    page.name_edit.setText('active_docs'); page.source_combo.setCurrentText('docs')
    page.pipeline_editor.setPlainText('[{"$match": {"active": true}}]')
    page.request_save(); dialog = page._save_dialog
    assert isinstance(dialog, MongoViewSaveDialog)
    qtbot.waitUntil(lambda: bool(previews))
    assert previews[-1].name == 'active_docs' and previews[-1].view_on == 'docs'
    plan = ViewPlan(page.snapshot, deepcopy(page.draft()),
        [{'create': 'active_docs', 'viewOn': 'docs', 'pipeline': [{'$match': {'active': True}}]}], True)
    page.show_plan(plan)
    assert dialog.save_button.isEnabled() and 'db.runCommand' in dialog.preview.toPlainText()
    dialog.preview_mode.setCurrentIndex(1)
    assert '"viewOn"' in dialog.preview.toPlainText()
    dialog.save_button.click()
    assert saves == [page.draft()]
    dialog.reject(); qtbot.waitUntil(lambda: page._save_dialog is None)


def test_existing_view_name_is_readonly_but_pipeline_is_editable(qtbot):
    definition = ViewDefinition('owned_view', 'docs', [{'$match': {'_id': 0}}])
    snapshot = ViewSnapshot('demo_catalog', 'owned_view', definition, exists=True)
    page = MongoViewDesignerPage('generated', 'demo_catalog', 'owned_view')
    qtbot.addWidget(page); page.set_sources(['docs']); page.load_snapshot(snapshot)
    assert page.name_edit.isReadOnly()
    assert page.source_combo.isEnabled() and not page.pipeline_editor.isReadOnly()
    page.pipeline_editor.setPlainText('[]')
    assert page.dirty and page.draft().pipeline == []


def test_view_drop_dialog_requires_permanent_acknowledgement(qtbot, page):
    plan = ViewPlan(page.snapshot, ViewDefinition('active_docs', 'docs', []), [{'drop': 'active_docs'}], True,
                    ['Drop is permanent.', 'Ordinary views have no UUID.'])
    dialog = MongoViewDropDialog(plan)
    qtbot.addWidget(dialog)
    assert not dialog.confirm_button.isEnabled()
    assert '"drop"' in dialog.preview.toPlainText()
    assert 'UUID' in dialog.permanent_check.text() and 'no UUID' in dialog.warning.text()
    dialog.permanent_check.setChecked(True)
    assert dialog.confirm_button.isEnabled()
