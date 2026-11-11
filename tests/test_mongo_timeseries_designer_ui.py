"""Detached time-series designer tests without database I/O."""
from __future__ import annotations

from copy import deepcopy

import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_timeseries import TimeSeriesDefinition, TimeSeriesPlan, TimeSeriesSnapshot
from opennavicat.ui.mongo_timeseries_designer import MongoTimeSeriesDesignerPage, MongoTimeSeriesSaveDialog


@pytest.fixture
def page(qtbot):
    widget = MongoTimeSeriesDesignerPage('generated', 'demo_catalog')
    qtbot.addWidget(widget)
    widget.load_snapshot(TimeSeriesSnapshot('demo_catalog', '', TimeSeriesDefinition(), exists=False))
    return widget


def test_timeseries_page_requires_time_field_and_builds_granularity_draft(qtbot, page):
    assert not page.save_action.isEnabled() and 'time_field' in page._invalid
    page.name_edit.setText('measurements')
    page.time_field_edit.setText('time')
    page.meta_field_edit.setText('sensor')
    page.granularity_combo.setCurrentIndex(page.granularity_combo.findData('minutes'))
    page.expire_edit.setText('3600')
    draft = page.draft()
    assert draft == TimeSeriesDefinition('measurements', 'time', 'sensor', 'minutes', None, None, 3600)
    assert page.save_action.isEnabled() and not page._invalid
    assert not page.rounding_edit.isEnabled() and not page.max_span_edit.isEnabled()


def test_custom_bucketing_requires_equal_positive_values(qtbot, page):
    page.name_edit.setText('measurements')
    page.time_field_edit.setText('time')
    page.granularity_combo.setCurrentIndex(page.granularity_combo.findData(''))
    page.rounding_edit.setText('120')
    assert 'bucket' in page._invalid and not page.save_action.isEnabled()
    page.max_span_edit.setText('120')
    assert not page._invalid and page.save_action.isEnabled()
    assert page.draft().granularity == '' and page.draft().bucket_rounding_seconds == 120
    page.max_span_edit.setText('240')
    assert 'bucket' in page._invalid
    with pytest.raises(DatabaseError, match='bucket'):
        page.draft()


def test_timeseries_save_sheet_requires_exact_preview(qtbot, page):
    previews, saves = [], []
    page.previewRequested.connect(previews.append)
    page.saveRequested.connect(saves.append)
    page.name_edit.setText('measurements')
    page.time_field_edit.setText('time')
    page.granularity_combo.setCurrentIndex(page.granularity_combo.findData('hours'))
    page.request_save()
    dialog = page._save_dialog
    assert isinstance(dialog, MongoTimeSeriesSaveDialog)
    qtbot.waitUntil(lambda: bool(previews))
    assert previews[-1].name == 'measurements' and previews[-1].granularity == 'hours'
    assert not dialog.save_button.isEnabled()
    plan = TimeSeriesPlan(
        page.snapshot, deepcopy(page.draft()),
        [{'create': 'measurements', 'timeseries': {'timeField': 'time', 'granularity': 'hours'}}],
        True,
    )
    page.show_plan(plan)
    assert dialog.save_button.isEnabled()
    assert 'db.runCommand' in dialog.preview.toPlainText()
    dialog.preview_mode.setCurrentIndex(1)
    assert '"timeseries"' in dialog.preview.toPlainText()
    dialog.save_button.click()
    assert saves == [page.draft()]
    dialog.reject()
    qtbot.waitUntil(lambda: page._save_dialog is None)


def test_existing_timeseries_keeps_identity_fields_readonly(qtbot):
    definition = TimeSeriesDefinition('measurements', 'time', 'sensor', 'minutes', None, None, 3600)
    snapshot = TimeSeriesSnapshot('demo_catalog', 'measurements', definition, exists=True)
    page = MongoTimeSeriesDesignerPage('generated', 'demo_catalog', 'measurements')
    qtbot.addWidget(page)
    page.load_snapshot(snapshot)
    assert page.name_edit.isReadOnly() and page.time_field_edit.isReadOnly() and page.meta_field_edit.isReadOnly()
    assert page.granularity_combo.isEnabled() and page.expire_edit.isEnabled()
    page.expire_edit.setText('7200')
    assert page.draft().expire_after_seconds == 7200 and page.dirty
