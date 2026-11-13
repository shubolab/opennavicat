"""Category refresh must preserve settled native toolbar widgets; no DB I/O."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from opennavicat.database import ConnectionConfig
from opennavicat.storage import Store
from opennavicat.ui.window import MainWindow
import opennavicat.ui.window as window_module


@pytest.fixture
def toolbar_host(qtbot, tmp_path, monkeypatch):
    store = Store(tmp_path / "generated-toolbar-workspace")
    store.state["connections"] = []
    store.state["favorites"] = []
    store.state["navicat_auto_discover"] = False
    monkeypatch.setattr(window_module, "discover_navicat_profiles",
                        lambda: pytest.fail("No existing profiles may be read"))
    window = MainWindow(store)

    def before_close(_widget):
        # pytest-qt may deliver deleteLater before fixture finalizers run.
        # Keep all Qt reads here, while the registered widget is still alive.
        assert not window.tasks and not window.pool.activeThreadCount()
        assert not window.cancel_pool.activeThreadCount()
        window.sessions.clear()  # Fake sessions have no network/close API.

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()

    def context(driver, connected=True):
        config = ConnectionConfig("generated-toolbar", "Generated toolbar", driver=driver,
                                  host="generated.invalid", username="GENERATED_OWNER",
                                  ssl_mode="disable", oracle_service_identifier="GENERATED_SERVICE")
        window.configs = {config.id: config}
        window.active_id = config.id
        window.sessions = ({config.id: SimpleNamespace(config=config, in_transaction=False)}
                           if connected else {})
        window.refresh_actions()
        QApplication.processEvents()  # Settle an intentional driver-context change.
        return window

    yield context
    assert window._closed  # Pure Python state; Qt owns close/deleteLater.


def _assert_order(window, driver):
    actions = window.main_toolbar.actions()
    category = window.category_actions
    if driver == "mongodb":
        assert actions[actions.index(category["index"]) - 1] is category["user"]
        assert actions[actions.index(window.mongo_pipeline_action) - 1] is category["other"]
    else:
        expected = [category[key] for key in (("user", "other") if driver == "sqlite"
                                             else ("other", "user"))]
        query = actions.index(category["query"])
        assert actions[query - 2:query] == expected


def _snapshot(window):
    return {name: (window.main_toolbar.widgetForAction(action),
                   window.main_toolbar.widgetForAction(action).geometry(),
                   window.main_toolbar.widgetForAction(action).isVisible())
            for name, action in window.category_actions.items() if name in {"other", "user"}}


@pytest.mark.parametrize("driver", ["oracle", "mysql", "sqlite", "mongodb", "redis"])
@pytest.mark.parametrize("connected", [True, False], ids=["connected", "disconnected"])
def test_settled_refresh_preserves_button_identity_visibility_and_geometry(toolbar_host, driver, connected):
    window = toolbar_host(driver, connected)
    _assert_order(window, driver)
    before = _snapshot(window)
    for _ in range(3):
        window.refresh_actions()
        _assert_order(window, driver)
        after = _snapshot(window)
        # No processEvents/wait: refresh must not create a blank frame and then
        # depend on a later toolbar layout event to show replacement buttons.
        for name, (button, geometry, visible) in before.items():
            assert after[name][0] is button
            assert after[name][1] == geometry
            assert after[name][2] is visible
        assert window.category_actions["other"].isEnabled() is (connected and driver not in {"sqlite", "redis"})
        assert not window.category_actions["user"].isEnabled()
        assert window.category_actions["other"].isVisible() is (driver not in {"sqlite", "redis"})


@pytest.mark.parametrize("driver", ["oracle", "mysql", "sqlite", "mongodb", "redis"])
def test_wrong_order_is_corrected_and_then_remains_stable(toolbar_host, driver):
    window = toolbar_host(driver)
    # Deliberately move both actions away from their required driver-specific
    # neighbors, preserving the same QActions and their disabled states.
    for name in ("other", "user"):
        window.main_toolbar.insertAction(window.category_actions["table"], window.category_actions[name])
    window.refresh_actions()
    _assert_order(window, driver)
    QApplication.processEvents()
    before = _snapshot(window)
    window.refresh_actions()
    _assert_order(window, driver)
    assert _snapshot(window) == before


def test_switching_driver_context_retains_each_required_order(toolbar_host):
    window = toolbar_host("oracle")
    for driver in ("mongodb", "sqlite", "redis", "mysql", "oracle"):
        config = replace(window.configs[window.active_id], driver=driver)
        window.configs[window.active_id] = config
        window.sessions[window.active_id].config = config
        window.refresh_actions()
        _assert_order(window, driver)
        QApplication.processEvents()
        before = _snapshot(window)
        window.refresh_actions()
        assert _snapshot(window) == before
