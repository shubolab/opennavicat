"""Synthetic Navicat metadata through the real serial Qt MainWindow route."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from opennavicat.database import ConnectionConfig
from opennavicat.navicat_profiles import ImportedProfile, ImportIssue, NavicatImportReport
from opennavicat.storage import Store
from opennavicat.ui.dialogs import ConnectionDialog
from opennavicat.ui.window import MainWindow, TablePage
import opennavicat.ui.window as window_module


def source(source_id="synthetic:one", *, driver="postgresql", blocked=False, paths=(("Work", "Development"), ("Secondary",)), **config):
    profile = ConnectionConfig("ignored-source-id", "Synthetic imported", driver=driver,
        host="synthetic.invalid", username="test-user", database="owned", ssl_mode="disable")
    profile = replace(profile, **config)
    return ImportedProfile(source_id, profile, source_path="/synthetic/conn.plist", source_format="macos-plist",
        blocked=blocked, group_paths=paths, unsupported_fields=("unknownjsonproperties",) if blocked else (),
        requires_secrets=("password",))


def report(*profiles, **kw):
    return NavicatImportReport(tuple(profiles), platform="darwin", sources_found=1, **kw)


def idle(qtbot, window):
    qtbot.waitUntil(lambda: not window.tasks and not window.pool.activeThreadCount() and not window._navicat_discovering, timeout=10000)


@pytest.fixture
def setup(qtbot, tmp_path, monkeypatch):
    messages, errors, discoveries = [], [], []
    selected = [report()]
    gui = threading.get_ident()
    def discover():
        discoveries.append(threading.get_ident())
        selected_report = selected[0]
        if isinstance(selected_report, Exception):
            raise selected_report
        return selected_report
    monkeypatch.setattr(window_module, "discover_navicat_profiles", discover)
    monkeypatch.setattr(MainWindow, "error", lambda _self, error: errors.append(str(error)))
    monkeypatch.setattr(QMessageBox, "information", lambda _p, title, message, *_a, **_k: messages.append((title, message)) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    store = Store(tmp_path/"workspace")
    window = MainWindow(store)
    qtbot.addWidget(window); window.show()
    yield SimpleNamespace(window=window, store=store, selected=selected, messages=messages,
                          errors=errors, discoveries=discoveries, gui=gui)
    idle(qtbot, window)
    for index in range(window.tabs.count()):
        page = window.tabs.widget(index)
        if isinstance(page, TablePage) and page.model:page.model.discard()
    window.close()
    assert errors == []


def discover(qtbot, setup, *profiles):
    setup.selected[0] = report(*profiles)
    assert setup.window.start_navicat_discovery()
    idle(qtbot, setup.window)
    return setup.window.last_navicat_counts


def imported_id(setup, source_id="synthetic:one"):
    return next(identifier for identifier, metadata in setup.store.state["navicat_imports"].items() if metadata["source_id"] == source_id)


def click(qtbot, window, item, *, double=False):
    window.tree.scrollToItem(item)
    point = window.tree.visualItemRect(item).center()
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    if double:qtbot.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)


def test_injected_store_never_discovers_until_explicit_call_and_auto_option_persists(qtbot, setup):
    window = setup.window
    assert setup.discoveries == [] and not window.tasks and not window.sessions
    assert window.navicat_auto_action.isChecked()
    window.navicat_auto_action.setChecked(False)
    assert setup.discoveries == []
    assert json.loads(setup.store.path.read_text())["navicat_auto_discover"] is False
    window.navicat_reload_action.trigger()
    idle(qtbot, window)
    assert len(setup.discoveries) == 1
    assert setup.discoveries[0] != setup.gui
    assert not window.sessions


def test_metadata_discovery_worker_gui_sync_deduplicates_memberships_and_never_connects(qtbot, setup, monkeypatch):
    window, store = setup.window, setup.store
    manual = ConnectionConfig("manual-owned", "Synthetic imported", database=":memory:")
    store.save_connection(manual); window.configs[manual.id] = manual; window.populate_connections()
    def forbidden(*_a, **_k):pytest.fail("Discovery must not connect or hydrate")
    monkeypatch.setattr(store, "hydrate", forbidden)
    monkeypatch.setattr(window_module, "DatabaseSession", forbidden)
    real_sync = store.sync_navicat_profiles
    def gui_sync(result):
        assert threading.get_ident() == setup.gui
        return real_sync(result)
    monkeypatch.setattr(store, "sync_navicat_profiles", gui_sync)
    counts = discover(qtbot, setup, source(), source("synthetic:two", driver="redis", name="Redis", database="0", paths=()))
    assert counts["added"] == 2 and counts["total"] == 2 and not window.sessions
    assert all(thread != setup.gui for thread in setup.discoveries)
    assert window.tree.topLevelItem(0).text(0) == manual.name
    assert window.tree.topLevelItem(1).text(0) == "Navicat 连接"
    identifier = imported_id(setup)
    assert len(window.connection_items(identifier)) == 2
    assert window.connection_picker.count() == 4  # placeholder + unique profiles
    assert "需补充凭据" in window.connection_items(identifier)[0].toolTip(0)
    counts = discover(qtbot, setup, source(), source("synthetic:two", driver="redis", name="Redis", database="0", paths=()))
    assert counts["unchanged"] == 2 and len(window.configs) == 3
    assert len(window.connection_items(identifier)) == 2


def test_group_selection_filter_and_active_only_have_no_connection_side_effects(qtbot, setup):
    discover(qtbot, setup, source())
    window = setup.window
    root = window.tree.topLevelItem(0)
    group = root.child(0)
    click(qtbot, window, root)
    click(qtbot, window, group, double=True)
    assert window.active_id is None and window.selected_info() is None and not window.sessions
    window.tree_filter.setText("Secondary")
    assert not root.isHidden()
    identifier = imported_id(setup)
    assert any(not item.isHidden() for item in window.connection_items(identifier))
    window.tree_filter.clear()
    window.active_only.setChecked(True)
    assert root.isHidden()
    window.active_only.setChecked(False)
    assert not root.isHidden()


def test_unknown_fields_and_unimplemented_driver_are_guarded_before_any_io_or_dialog(qtbot, setup, monkeypatch):
    pg = source(blocked=True)
    oracle = source("synthetic:oracle", driver="oracle", blocked=True, name="Oracle metadata")
    discover(qtbot, setup, pg, oracle)
    window = setup.window
    def forbidden(*_a, **_k):pytest.fail("Blocked profiles must not hydrate/connect/open another driver's settings")
    monkeypatch.setattr(setup.store, "hydrate", forbidden)
    monkeypatch.setattr(window_module, "DatabaseSession", forbidden)
    monkeypatch.setattr(window_module, "ConnectionDialog", forbidden)
    pg_id, oracle_id = imported_id(setup), imported_id(setup, "synthetic:oracle")
    window.connect_id(pg_id); window.connect_id(oracle_id)
    assert [message[0] for message in setup.messages] == ["待复核连接", "未实现驱动"]
    window.set_active(oracle_id); window.edit_connection()
    window.new_connection(config=window.configs[oracle_id])
    assert len(setup.messages) == 4 and not window.sessions and not window.tasks
    assert "未实现驱动" in window.connection_item(oracle_id).toolTip(0)
    assert "待复核" in window.connection_item(pg_id).toolTip(0)


def test_cancel_real_settings_keeps_import_review_guard_and_profile_untouched(qtbot, setup, monkeypatch):
    discover(qtbot, setup, source(blocked=True))
    identifier = imported_id(setup)
    window = setup.window; window.set_active(identifier)
    before = deepcopy(setup.store.state)
    def cancel():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ConnectionDialog)
        assert dialog.driver_combo.currentData() == "postgresql"
        dialog.reject()
    QTimer.singleShot(0, cancel)
    window.edit_connection()
    assert setup.store.state == before and not window.sessions
    assert setup.store.state["navicat_imports"][identifier]["blocked"]


def test_reviewed_import_save_has_no_implicit_connection_and_later_refresh_preserves_local_edit(qtbot, setup, monkeypatch):
    discover(qtbot, setup, source(blocked=True))
    identifier = imported_id(setup)
    window = setup.window; window.set_active(identifier)
    def forbidden(*_a, **_k):pytest.fail("Saving imported settings must not connect automatically")
    monkeypatch.setattr(window, "connect_id", forbidden)
    def edit_and_save():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ConnectionDialog)
        dialog.name_edit.setText("Reviewed local name")
        dialog.host_edit.setText("approved.synthetic.invalid")
        dialog.accept()
    QTimer.singleShot(0, edit_and_save)
    window.edit_connection()
    assert window.configs[identifier].name == "Reviewed local name"
    assert not setup.store.state["navicat_imports"][identifier]["blocked"]
    assert setup.store.state["navicat_imports"][identifier]["locally_edited"]
    counts = discover(qtbot, setup, source(blocked=True, host="source-changed.invalid"))
    assert counts["preserved"] == 1
    assert window.configs[identifier].host == "approved.synthetic.invalid" and not window.sessions


def sqlite_source(tmp_path, label, *, name="Synthetic SQLite"):
    path = tmp_path/(label+".sqlite")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE owned_records(id INTEGER PRIMARY KEY,label TEXT)")
        db.execute("INSERT INTO owned_records(label) VALUES(?)", [label])
    return source(driver="sqlite", name=name, database=str(path), username="", host="")


def connect(qtbot, setup, identifier):
    setup.window.connect_id(identifier)
    qtbot.waitUntil(lambda: identifier in setup.window.sessions, timeout=10000)
    idle(qtbot, setup.window)
    return setup.window.sessions[identifier]


def test_same_profile_memberships_open_one_session_share_objects_and_all_disconnect(qtbot, setup, tmp_path):
    profile = sqlite_source(tmp_path, "owned")
    discover(qtbot, setup, profile)
    window = setup.window; identifier = imported_id(setup)
    aliases = window.connection_items(identifier)
    click(qtbot, window, aliases[1], double=True)
    qtbot.waitUntil(lambda: identifier in window.sessions, timeout=10000); idle(qtbot, window)
    session = window.sessions[identifier]
    assert len(window.sessions) == 1
    for alias in aliases:
        assert alias.childCount() == 1 and alias.child(0).text(0) == "main"
        schema = alias.child(0)
        tables = next(schema.child(i) for i in range(schema.childCount()) if schema.child(i).text(0) == "表")
        assert [tables.child(i).text(0) for i in range(tables.childCount())] == ["owned_records"]
    click(qtbot, window, aliases[0], double=True); idle(qtbot, window)
    assert window.sessions[identifier] is session
    schema = aliases[1].child(0)
    tables = schema.child(0); tables.setExpanded(True)
    page_count = window.tabs.count()
    click(qtbot, window, tables.child(0), double=True)
    qtbot.waitUntil(lambda: isinstance(window.tabs.currentWidget(), TablePage) and window.tabs.currentWidget().model is not None)
    idle(qtbot, window)
    assert window.tabs.count() == page_count+1
    assert window.disconnect(identifier)
    for alias in window.connection_items(identifier):
        assert alias.childCount() == 1 and alias.child(0).text(0) == "双击以打开连接"
    assert not window.sessions


def test_active_source_update_preserves_session_config_until_explicit_disconnect(qtbot, setup, tmp_path):
    first = sqlite_source(tmp_path, "first")
    second = sqlite_source(tmp_path, "second", name="New source name")
    discover(qtbot, setup, first)
    window = setup.window; identifier = imported_id(setup)
    session = connect(qtbot, setup, identifier)
    active_config = session.config
    query = window.new_query(connection_id=identifier)
    counts = discover(qtbot, setup, second)
    assert counts["pending"] == 1
    assert window.sessions[identifier] is session and window.configs[identifier] is active_config
    assert active_config.database == first.config.database
    assert setup.store.connections()[0].database == second.config.database
    assert "关闭当前连接后生效" in window.connection_item(identifier).toolTip(0)
    results = []
    window.submit(lambda: session.execute("SELECT label FROM owned_records"), results.extend)
    idle(qtbot, window)
    assert results[0].rows == [["first"]]
    assert window.disconnect(identifier)
    assert window.configs[identifier].database == second.config.database and not window.sessions
    assert identifier not in window._navicat_pending_configs
    assert query.connection_picker.currentText() == "New source name"
    session = connect(qtbot, setup, identifier)
    results.clear(); window.submit(lambda: session.execute("SELECT label FROM owned_records"), results.extend)
    idle(qtbot, window)
    assert results[0].rows == [["second"]]


def test_failed_discovery_keeps_existing_profiles_groups_and_does_not_expose_raw_error(qtbot, setup):
    discover(qtbot, setup, source())
    window = setup.window; identifier = imported_id(setup)
    before = deepcopy(setup.store.state)
    aliases = window.connection_items(identifier)
    setup.selected[0] = report(issues=(ImportIssue("source_read_failed", "Synthetic safe message"),))
    window.start_navicat_discovery(); idle(qtbot, window)
    assert window.last_navicat_counts == {"failed": 1}
    assert setup.store.state == before and window.connection_items(identifier) == aliases
    setup.selected[0] = ValueError("SYNTHETIC-PRIVATE-SOURCE-CONTENT")
    window.start_navicat_discovery(); idle(qtbot, window)
    assert setup.store.state == before and window.connection_items(identifier) == aliases
    assert "SYNTHETIC-PRIVATE" not in window.statusBar().currentMessage() and not setup.messages


def test_late_old_catalog_callback_cannot_touch_replaced_qtree_items(qtbot, setup, tmp_path, monkeypatch):
    profile = sqlite_source(tmp_path, "late")
    discover(qtbot, setup, profile)
    window = setup.window; identifier = imported_id(setup)
    connect(qtbot, setup, identifier)
    callbacks=[]
    real_submit = window.submit
    def capture(function, success=None, failure=None, label="", **kwargs):
        if label == "读取对象":callbacks.append((success, failure))
        else:return real_submit(function, success, failure, label, **kwargs)
    monkeypatch.setattr(window,"submit",capture)
    old = window.connection_item(identifier).child(0)
    info = old.data(0, Qt.ItemDataRole.UserRole); info["loaded"]=False
    window.load_objects(old,info)
    assert len(callbacks) == 1
    window.populate_connections()
    assert len(callbacks) == 2  # Replacement expanded schema requests a fresh catalog.
    assert not isValid(old)
    callbacks[0][0]([{"name":"stale-should-not-appear","kind":"table"}])
    callbacks[0][1](ValueError("stale-should-not-show"))
    assert not setup.errors
    for alias in window.connection_items(identifier):
        assert alias.child(0).child(0).child(0).text(0)=="owned_records"
    callbacks[1][0]([{"name":"fresh-owned-catalog","kind":"table"}])
    for alias in window.connection_items(identifier):
        assert alias.child(0).child(0).child(0).text(0)=="fresh-owned-catalog"
