"""Detached finite-stream presentation/signals; no database or native GUI I/O."""
from dataclasses import replace
import threading

from bson import BSON, Binary, Decimal128, Int64, Timestamp
from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QPlainTextEdit
from pymongo.errors import OperationFailure
import pytest

from opennavicat.database import DatabaseError
from opennavicat.mongodb_pipeline import (
    PipelineDefinition, PipelinePreview, PipelineStage, parse_explain_reply,
)
from opennavicat.mongodb_pipeline_stream import FiniteStreamResult, StreamEvent, StreamExecutionError
from opennavicat.mongodb_session import BSON_CODEC, canonical_dumps
from opennavicat.ui.mongo_pipeline import MongoPipelinePage


SCRIPT = 'db.getCollection("generated_sales").aggregate([\n  {$changeStream: {}}]);\n'
PRIVATE_EVENT = "GENERATED_EVENT_PAYLOAD_MUST_STAY_PRIVATE"
PRIVATE_CAUSE = "GENERATED_PASSWORD_AND_ENDPOINT_MUST_STAY_PRIVATE"
SERVER_MESSAGE = "$limit is not permitted in a $changeStream pipeline"
ERROR_SCRIPT = 'db.getCollection("sales").aggregate([{$changeStream: {}}, {$limit: 1}]);\n'


def stream_result(completion="empty_batch", *, elapsed=1.069):
    event = {"_id": {"_data": PRIVATE_EVENT}, "operationType": "insert",
             "fullDocument": {"payload": PRIVATE_EVENT, "n": Int64(9007199254740993),
                              "d": Decimal128("1.250"), "b": Binary(b"\x00\xff", 128),
                              "at": Timestamp(9, 7)}}
    return FiniteStreamResult((StreamEvent(BSON.encode(event, codec_options=BSON_CODEC),
                                          canonical_dumps(event)),), completion, elapsed)


@pytest.fixture
def page(qtbot):
    value = MongoPipelinePage("generated_connection", "generated_database")
    qtbot.addWidget(value)
    value.load_definition(PipelineDefinition(source="generated_sales", stages=[PipelineStage("$changeStream", {})]))
    value.resize(1150, 850)
    value.show()
    return value


def visible_tabs(page):
    return [page.tabs.tabText(index) for index in range(page.tabs.count()) if page.tabs.isTabVisible(index)]


def assert_no_event_presentation(page):
    assert page.last_rows == [] and page.results.rowCount() == 0
    assert page.input_panel.table.rowCount() == page.output_panel.table.rowCount() == 0
    assert not page.export_action.isEnabled()
    assert PRIVATE_EVENT not in "\n".join(widget.toPlainText() for widget in page.findChildren(QPlainTextEdit))


def test_local_stop_is_after_explain_and_emits_only_while_running_on_gui_thread(page, qtbot):
    actions = page.toolbar.actions()
    assert actions.index(page.stop_action) == actions.index(page.explain_action) + 1
    assert not page.stop_action.isEnabled()
    emissions = []
    page.stopRequested.connect(lambda: emissions.append(QThread.currentThread()))
    page.stop_action.trigger()
    page.request_stop()
    assert not emissions

    page.begin_stream_execution(SCRIPT)
    assert not page.stop_action.isEnabled()
    assert visible_tabs(page) == ["管道", "脚本预览"]
    page.set_running(True)
    assert page.stop_action.isEnabled() and not page.operator_combo.isEnabled()
    assert not page.run_action.isEnabled() and not page.preview_all_action.isEnabled()
    spinner = page._busy_spinner
    assert spinner is not None and spinner.timer.isActive()
    qtbot.waitUntil(spinner.isVisible)
    angle = spinner.angle
    qtbot.waitUntil(lambda: spinner.angle != angle)
    with qtbot.waitSignal(page.stopRequested):
        qtbot.mouseClick(page.toolbar.widgetForAction(page.stop_action), Qt.MouseButton.LeftButton)
    assert emissions == [page.thread()]
    page.set_running(False)
    assert not page.stop_action.isEnabled() and page.operator_combo.isEnabled()
    assert page._busy_spinner is spinner and not spinner.timer.isActive() and not spinner.isVisible()
    page.stop_action.trigger()
    assert emissions == [page.thread()]


def test_stream_success_retains_exact_typed_events_and_four_text_tabs_without_export(page):
    original_dirty, original_title = page.dirty, page.title
    result = stream_result()
    page.begin_stream_execution(SCRIPT)
    assert page.tabs.currentWidget() is page.pipeline_tab
    page.show_stream_execution(result, SCRIPT)
    assert page._last_stream_result is result
    assert page._last_stream_result.events[0].raw_bson == result.events[0].raw_bson
    assert page.messages.toPlainText() == SCRIPT + "> OK\n> 查询时间: 1.069s\n"
    assert page.stream_output.toPlainText() == page.messages.toPlainText()
    assert page.tabs.currentWidget() is page.messages
    assert visible_tabs(page) == ["管道", "脚本预览", "消息", "输出"]
    assert page.tabs.count() == 5  # Existing Explain slot stays hidden; no extra result page.
    assert page.tabs.indexOf(page.results) == -1
    assert page.dirty is original_dirty and page.title == original_title
    assert_no_event_presentation(page)


def test_empty_event_then_empty_success_switches_output_visibility_without_sticky_result(page):
    empty = replace(stream_result(), events=())
    page.show_stream_execution(empty, SCRIPT)
    assert visible_tabs(page) == ["管道", "脚本预览", "消息"]
    assert "> OK" in page.messages.toPlainText()
    page.show_stream_execution(stream_result(), SCRIPT)
    assert visible_tabs(page) == ["管道", "脚本预览", "消息", "输出"]
    assert page.stream_output.toPlainText() == page.messages.toPlainText()
    page.tabs.setCurrentWidget(page.stream_output)
    page.show_stream_execution(empty, SCRIPT)
    assert visible_tabs(page) == ["管道", "脚本预览", "消息"]
    assert page.tabs.currentWidget() is page.messages
    assert page._last_stream_result is empty
    assert_no_event_presentation(page)


def test_idle_stream_text_toolbar_keeps_save_focus_and_disabled_run_then_recovers_pipeline_actions(page):
    page.show_stream_execution(stream_result(), SCRIPT)
    for view in (page.messages, page.stream_output):
        page.tabs.setCurrentWidget(view)
        assert page.save_action.isVisible() and page.focus_action.isVisible()
        assert page.run_action.isVisible() and not page.run_action.isEnabled()
        assert all(not action.isVisible() for action in (
            page.import_action, page.preview_all_action, page.explain_action,
            page.stop_action, page.export_action))
    page.tabs.setCurrentWidget(page.pipeline_tab)
    assert page.import_action.isVisible() and page.preview_all_action.isVisible()
    assert page.run_action.isEnabled() and page.explain_action.isEnabled()
    assert page.stop_action.isVisible() and not page.stop_action.isEnabled()
    assert not page.export_action.isEnabled()
    page.show_error(DatabaseError("generated ordinary error"))
    assert not page._stream_mode and page.run_action.isEnabled()
    assert page.import_action.isVisible() and page.preview_all_action.isVisible()


@pytest.mark.parametrize("completion,expected", [
    ("row_limit", "[Warning] Row limit reached"),
    ("deadline", "[Error] Finite stream time limit reached"),
    ("byte_limit", "[Error] Byte limit reached"),
    ("cancelled", "[Error] Stopped by user"),
    ("cleanup_failed", "[Error] Stream cursor cleanup could not be confirmed"),
    ("error", "[Error] Finite stream execution failed"),
])
def test_incomplete_or_failed_stream_never_presents_unconditional_ok(page, completion, expected):
    result = stream_result(completion, elapsed=0.257)
    page.show_stream_execution(result, SCRIPT)
    assert expected in page.messages.toPlainText()
    assert "> OK" not in page.messages.toPlainText()
    assert "> 查询时间: 0.257s" in page.messages.toPlainText()
    assert page._last_stream_result is result
    assert_no_event_presentation(page)


def test_partial_byte_error_keeps_metadata_private_and_never_renders_error_or_cause_text(page):
    partial = replace(stream_result("byte_limit"), undelivered_event_count=1,
                      undelivered_raw_bytes=101, undelivered_rendered_bytes=211)
    error = StreamExecutionError(PRIVATE_EVENT, partial)
    error.__cause__ = RuntimeError(PRIVATE_CAUSE)
    page.show_stream_error(error, SCRIPT)
    assert page._last_stream_result is partial
    assert page._last_stream_result.undelivered_event_count == 1
    assert page._last_stream_result.undelivered_raw_bytes == 101
    assert "an obtained event was not delivered" in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert page.stream_output.toPlainText() == page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_cleanup_failure_cannot_be_hidden_by_cancel_wording_or_authentication_flag(page):
    partial = stream_result("cleanup_failed", elapsed=3.2)
    error = StreamExecutionError("Stopped by user " + PRIVATE_CAUSE, partial,
                                 authentication_required=True)
    page.show_stream_error(error, SCRIPT)
    assert "cleanup could not be confirmed" in page.messages.toPlainText()
    assert "Stopped by user" not in page.messages.toPlainText()
    assert "authentication" not in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.stream_output.toPlainText()
    assert page._last_stream_result is partial


def test_typed_server_failure_displays_only_validated_numeric_code_not_reply_or_cause(page):
    partial = stream_result("error")
    error = StreamExecutionError(PRIVATE_CAUSE, partial)
    error.__cause__ = OperationFailure(PRIVATE_EVENT, code=40600, details={
        "ok": 0, "code": 40600, "codeName": PRIVATE_CAUSE, "errmsg": PRIVATE_EVENT,
        "fullDocument": {"private": PRIVATE_EVENT}})
    page.show_stream_error(error, SCRIPT)
    assert "(server code 40600)" in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def structured_error(completion="error", *, authentication_required=False):
    error = StreamExecutionError(PRIVATE_CAUSE, stream_result(completion, elapsed=0.002),
        authentication_required=authentication_required, server_message=SERVER_MESSAGE,
        error_phase="aggregate", script_location=(1, 27))
    error.__cause__ = OperationFailure(PRIVATE_CAUSE, code=40600, details={
        "ok": 0, "code": 40600, "codeName": PRIVATE_CAUSE, "errmsg": PRIVATE_EVENT,
        "fullDocument": {"private": PRIVATE_EVENT}})
    return error


def test_owner_structured_aggregate_error_displays_bounded_message_location_and_actual_elapsed(page):
    error = structured_error()
    page.show_stream_error(error, ERROR_SCRIPT)
    assert page.messages.toPlainText() == (
        ERROR_SCRIPT + "> [Error] " + SERVER_MESSAGE + "\nat line 1, column 27\n> 查询时间: 0.002s\n")
    assert page.stream_output.toPlainText() == page.messages.toPlainText()
    assert visible_tabs(page) == ["管道", "脚本预览", "消息"]
    assert page._last_stream_result is error.partial_result
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_owner_getmore_message_without_location_is_preserved_without_fabricated_position(page):
    error = structured_error()
    error.error_phase, error.script_location = "get_more", None
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "> [Error] " + SERVER_MESSAGE in page.messages.toPlainText()
    assert "at line" not in page.messages.toPlainText()
    assert "server code" not in page.messages.toPlainText()
    assert "> 查询时间: 0.002s" in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_owner_aggregate_message_is_preserved_when_frozen_script_has_no_location(page):
    error = structured_error()
    error.script_location = None
    page.show_stream_error(error, "")
    assert page.messages.toPlainText() == "> [Error] " + SERVER_MESSAGE + "\n> 查询时间: 0.002s\n"
    assert page._last_stream_result is error.partial_result
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_plain_database_error_cannot_supply_structured_server_text_or_location(page):
    trusted = structured_error()
    error = DatabaseError(PRIVATE_CAUSE)
    error.partial_result = trusted.partial_result
    error.server_message, error.error_phase, error.script_location = SERVER_MESSAGE, "aggregate", (1, 27)
    error.__cause__ = trusted.__cause__
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "server code 40600" in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


@pytest.mark.parametrize("message", ["x" * 2049, "first\nsecond", "bad\x00value", "bidi\u202evalue", "bad\ud800value", 17])
def test_malformed_structured_message_falls_back_without_coercion_or_location(page, message):
    error = structured_error()
    # Defend the UI boundary as well as the constructor's normalisation.
    error.server_message = message
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "server code 40600" in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


@pytest.mark.parametrize("location", [[1, 27], (True, 27), (0, 27), (1, 0),
                                     (1, 2**31), (99, 1), (1, 9999), (1, "27"), (1, 27, 3)])
def test_malformed_or_out_of_script_location_discards_the_whole_structured_diagnostic(page, location):
    error = structured_error()
    error.script_location = location
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "server code 40600" in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert_no_event_presentation(page)


@pytest.mark.parametrize("phase", [None, "unknown", 17])
def test_unknown_phase_cannot_display_the_structured_diagnostic(page, phase):
    error = structured_error()
    error.error_phase = phase
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "server code 40600" in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_getmore_cannot_fabricate_aggregate_location(page):
    error = structured_error()
    error.error_phase = "get_more"
    page.show_stream_error(error, ERROR_SCRIPT)
    assert "server code 40600" in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert_no_event_presentation(page)


@pytest.mark.parametrize("completion,authentication,expected", [
    ("cancelled", False, "Stopped by user"),
    ("cleanup_failed", False, "cleanup could not be confirmed"),
    ("byte_limit", False, "Byte limit reached"),
    ("row_limit", False, "Row limit reached"),
    ("deadline", False, "time limit reached"),
    ("error", True, "authentication was rejected"),
])
def test_structured_fields_never_override_cancellation_cleanup_bounds_or_authentication(
        page, completion, authentication, expected):
    error = structured_error(completion, authentication_required=authentication)
    page.show_stream_error(error, ERROR_SCRIPT)
    assert expected in page.messages.toPlainText()
    assert SERVER_MESSAGE not in page.messages.toPlainText() and "at line" not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_structured_location_uses_utf16_columns_of_the_frozen_script(page):
    error = structured_error()
    script = "😀\n" + ERROR_SCRIPT
    error.script_location = (1, 3)  # One supplementary character occupies two UTF-16 units.
    page.show_stream_error(error, script)
    assert "at line 1, column 3" in page.messages.toPlainText()
    error.script_location = (1, 4)
    page.show_stream_error(error, script)
    assert "server code 40600" in page.messages.toPlainText()
    assert "at line" not in page.messages.toPlainText()


@pytest.mark.parametrize("code", [True, -1, "40600" + PRIVATE_CAUSE])
def test_malformed_server_code_is_not_coerced_into_ui_text(page, code):
    error = StreamExecutionError(PRIVATE_CAUSE, stream_result("error"))
    error.__cause__ = OperationFailure(PRIVATE_EVENT, code=code, details={"ok": 0, "code": code})
    page.show_stream_error(error, SCRIPT)
    assert "server code" not in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.stream_output.toPlainText()
    assert_no_event_presentation(page)


def test_pre_dispatch_error_has_no_fabricated_elapsed_or_payload_and_replaces_old_state(page):
    page.show_stream_execution(stream_result(), SCRIPT)
    page.show_stream_error(DatabaseError(PRIVATE_CAUSE, authentication_required=True), SCRIPT)
    assert page._last_stream_result is None
    assert "Database authentication was rejected" in page.messages.toPlainText()
    assert "查询时间" not in page.messages.toPlainText()
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


def test_begin_new_stream_clears_partial_state_old_grids_and_stale_completion(page):
    ordinary = canonical_dumps({"ordinary_generated_field": "old ordinary result"})
    page.show_execution([ordinary])
    assert page.last_rows and page.export_action.isEnabled()
    page.show_stream_error(StreamExecutionError("ignored", stream_result("cancelled")), SCRIPT)
    assert "Stopped by user" in page.messages.toPlainText()
    next_script = SCRIPT + "\n// generated next frozen script\n\n"
    page.begin_stream_execution(next_script)
    assert page._last_stream_result is None
    assert page.messages.toPlainText() == next_script
    assert page.stream_output.toPlainText() == next_script
    assert page.tabs.currentWidget() is page.pipeline_tab
    assert "Stopped by user" not in page.messages.toPlainText()
    assert visible_tabs(page) == ["管道", "脚本预览"]
    assert_no_event_presentation(page)


@pytest.mark.parametrize("next_view", ["execution", "preview", "explain", "error", "definition"])
def test_ordinary_operations_restore_result_slot_and_clear_private_stream_state(page, next_view):
    page.show_stream_execution(stream_result(), SCRIPT)
    ordinary = canonical_dumps({"generated": 17})
    if next_view == "execution":
        page.show_execution([ordinary])
        assert page.tabs.currentWidget() is page.results and page.export_action.isEnabled()
        assert page.last_rows == [ordinary] and page.results.rowCount() == 1
    elif next_view == "preview":
        page.operator_combo.setCurrentText("$match")
        page.show_preview(PipelinePreview([ordinary], [{"index": 0, "operator": "$match",
            "rows": [ordinary], "truncated": False}], [ordinary]))
        assert page.results.rowCount() == 1 and "源预览" in page.messages.toPlainText()
        assert page.tabs.currentWidget() is page.pipeline_tab
    elif next_view == "explain":
        page.show_explain(parse_explain_reply({"ok": 1, "queryPlanner": {
            "winningPlan": {"stage": "COLLSCAN"}}}))
        assert page.tabs.currentWidget() is page.explain_panel
    elif next_view == "error":
        page.show_error(DatabaseError("generated ordinary failure"))
        assert page.messages.toPlainText() == "generated ordinary failure"
    else:
        page.load_definition(PipelineDefinition(source="generated_new", stages=[PipelineStage("$match", {})]))
        assert page.definition.source == "generated_new" and not page.dirty
        assert page.tabs.currentWidget() is page.pipeline_tab
    assert not page._stream_mode and page._last_stream_result is None
    assert page.tabs.indexOf(page.results) == 3 and page.tabs.indexOf(page.stream_output) == -1
    assert page.stream_output.toPlainText() == "" and page.stream_output.isHidden()
    assert "输出" not in visible_tabs(page)


def test_prior_explain_is_hidden_during_stream_and_normal_explain_family_recovers(page):
    plan = parse_explain_reply({"ok": 1, "queryPlanner": {"winningPlan": {"stage": "COLLSCAN"}}})
    page.show_explain(plan)
    assert page.tabs.isTabVisible(page.explain_tab_index)
    page.show_stream_execution(stream_result(), SCRIPT)
    assert visible_tabs(page) == ["管道", "脚本预览", "消息", "输出"]
    page.show_explain(plan)
    assert page.tabs.currentWidget() is page.explain_panel
    assert visible_tabs(page) == ["管道", "脚本预览", "消息", "结果", "解释 2"]


def test_busy_overlay_covers_lower_workspace_and_source_sampling_restore_their_modes(page, qtbot):
    page.sample_mode.setCurrentIndex(page.sample_mode.findData("all"))
    page.begin_stream_execution(SCRIPT)
    page.set_running(True)
    qtbot.waitUntil(page._busy_overlay.isVisible)
    assert not page.source_combo.isEnabled() and not page.sample_mode.isEnabled()
    assert not page.sample_size.isEnabled() and not page.operator_combo.isEnabled()
    assert page.import_action.isVisible() and page.import_action.isEnabled()
    assert page._busy_spinner.parentWidget() is page._busy_overlay
    assert not page.graph_widget.findChildren(type(page._busy_spinner))
    page.resize(1300, 950)
    qtbot.waitUntil(lambda: page._busy_overlay.size() == page.detail_stack.size())
    assert abs(page._busy_spinner.geometry().center().x() - page.detail_stack.rect().center().x()) <= 1
    assert abs(page._busy_spinner.geometry().center().y() - page.detail_stack.rect().center().y()) <= 1
    page.show_stream_execution(replace(stream_result(), events=()), SCRIPT)
    page.set_running(False)
    assert page.source_combo.isEnabled() and page.sample_mode.isEnabled()
    assert not page.sample_size.isEnabled() and page.operator_combo.isEnabled()
    page.tabs.setCurrentWidget(page.pipeline_tab)
    page.sample_mode.setCurrentIndex(page.sample_mode.findData("random"))
    assert page.sample_size.isEnabled() and not page._busy_overlay.isVisible()


def test_repeated_busy_empty_event_error_and_ordinary_explain_keep_stable_tab_slots(page):
    plan = parse_explain_reply({"ok": 1, "queryPlanner": {"winningPlan": {"stage": "COLLSCAN"}}})
    for count in range(1, 4):
        page.begin_stream_execution(SCRIPT)
        page.set_running(True)
        assert visible_tabs(page) == ["管道", "脚本预览"]
        page.show_stream_execution(stream_result(), SCRIPT)
        page.set_running(False)
        assert visible_tabs(page) == ["管道", "脚本预览", "消息", "输出"]
        page.begin_stream_execution(SCRIPT)
        page.show_stream_error(StreamExecutionError("ignored", stream_result("cancelled")), SCRIPT)
        assert visible_tabs(page) == ["管道", "脚本预览", "消息"]
        page.show_explain(plan)
        assert page.explain_tab_index == 4 and page.tabs.count() == 5
        assert page.tabs.tabText(4) == f"解释 {count}"
        assert page.tabs.currentWidget() is page.explain_panel
        assert page._last_stream_result is None and page.tabs.indexOf(page.results) == 3


def test_stream_component_entry_points_reject_worker_thread_before_widget_access(page):
    result = stream_result()
    calls = [lambda: page.begin_stream_execution(SCRIPT),
             lambda: page.show_stream_execution(result, SCRIPT),
             lambda: page.show_stream_error(StreamExecutionError("ignored", result), SCRIPT),
             lambda: page.set_running(True), lambda: page.request_stop()]
    errors = []

    def worker():
        for call in calls:
            try:
                call()
            except RuntimeError as error:
                errors.append(str(error))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(3)
    assert not thread.is_alive() and len(errors) == len(calls)
    assert all("GUI thread" in message for message in errors)
    assert not page.running and not page._stream_mode and page._last_stream_result is None


def required_image_error(image='pre'):
    from opennavicat.mongodb_pipeline_stream import StreamReferenceSentinel
    sentinel = (StreamReferenceSentinel.MISSING_REQUIRED_PRE_IMAGE if image == 'pre'
                else StreamReferenceSentinel.MISSING_REQUIRED_POST_IMAGE)
    message = sentinel.server_prefix + ' {operationType: "update"}'
    error = StreamExecutionError('safe code 47 classification', stream_result('error', elapsed=.023),
        server_message=message, error_phase='get_more', reference_sentinel=sentinel)
    error.__cause__ = OperationFailure(PRIVATE_CAUSE, 47, {'ok': 0, 'code': 47, 'errmsg': message})
    return error


@pytest.mark.parametrize('image', ['pre', 'post'])
def test_required_image_getmore_sentinel_is_static_display_without_real_location(page, image):
    error = required_image_error(image)
    page.show_stream_error(error, SCRIPT)
    assert error.script_location is None
    assert page.messages.toPlainText() == (SCRIPT + '> [Error] ' + error.server_message
        + '\nat line 0, column 0\n> 查询时间: 0.023s\n')
    assert visible_tabs(page) == ['管道', '脚本预览', '消息']
    assert PRIVATE_CAUSE not in page.messages.toPlainText()
    assert_no_event_presentation(page)


@pytest.mark.parametrize('mutation', ['string', 'wrong-kind', 'aggregate', 'different-code',
    'unknown-cause', 'zero-coordinates', 'positive-coordinates', 'different-message',
    'cancelled', 'cleanup_failed', 'byte_limit', 'row_limit', 'deadline', 'auth', 'oversized', 'bidi'])
def test_forged_or_lower_priority_image_metadata_cannot_add_zero_position_line(page, mutation):
    from opennavicat.mongodb_pipeline_stream import StreamReferenceSentinel
    error = required_image_error()
    if mutation == 'string': error.reference_sentinel = 'missing_required_pre_image'
    elif mutation == 'wrong-kind': error.reference_sentinel = StreamReferenceSentinel.MISSING_REQUIRED_POST_IMAGE
    elif mutation == 'aggregate': error.error_phase = 'aggregate'
    elif mutation == 'different-code': error.__cause__ = OperationFailure(PRIVATE_CAUSE, 20)
    elif mutation == 'unknown-cause': error.__cause__ = None
    elif mutation == 'zero-coordinates': error.script_location = (0, 0)
    elif mutation == 'positive-coordinates': error.script_location = (1, 1)
    elif mutation == 'different-message': error.server_message = 'Another valid code-47 server rejection'
    elif mutation == 'auth': error.authentication_required = True
    elif mutation == 'oversized': error.server_message = error.server_message + 'x' * 2049
    elif mutation == 'bidi': error.server_message += '\u202e'
    else: error.partial_result = replace(error.partial_result, completion=mutation)
    page.show_stream_error(error, SCRIPT)
    text = page.messages.toPlainText()
    assert 'at line' not in text and PRIVATE_CAUSE not in text
    assert visible_tabs(page) == ['管道', '脚本预览', '消息']
    assert_no_event_presentation(page)
