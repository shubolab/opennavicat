"""MainWindow cancellation routing with real markers and no server endpoint."""
from types import SimpleNamespace

import pytest

from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_session import MongoSession
from opennavicat.storage import Store
from opennavicat.ui.query import QueryPage
from opennavicat.ui.window import MainWindow


class Control:
    def __init__(self, session):
        self.admin = self
        self.session = session
        self.operations = {}
        self.queries = []
        self.killed = []
        self.closed_cursors = 0

    def aggregate(self, pipeline, **kwargs):
        self.queries.append(pipeline)
        comment = pipeline[1]["$match"]["command.comment"]
        rows = [{"opid": name} for name, marker in self.operations.items()
                if marker == comment and marker == self.session.cancellation_marker()]
        owner = self
        class Cursor(list):
            def close(self):
                owner.closed_cursors += 1
        return Cursor(rows)

    def command(self, command, **kwargs):
        assert command.get("killOp") == 1
        self.killed.append(command["op"])
        return {"ok": 1}


@pytest.fixture
def controlled(qtbot, tmp_path, monkeypatch):
    config = ConnectionConfig("owned", "Generated", driver="mongodb", database="generated", ssl_mode="disable")
    store = Store(tmp_path / "generated-workspace")
    store.save_connection(config)
    window = MainWindow(store)
    session = MongoSession(config)
    monkeypatch.setattr(session, "connect", lambda: pytest.fail("This test must not open any endpoint"))
    session.connection = object()  # _operation only requires an existing owner.
    control = Control(session)
    session._control = control
    window.sessions[config.id] = session
    page = QueryPage(window, config.id)
    window.tabs.addTab(page, "Generated")
    page.running = True
    pending = []
    def submit(function, *args, **kwargs):
        pending.append(SimpleNamespace(run=function, finish=kwargs.get("finished"), pool=kwargs.get("pool")))
    monkeypatch.setattr(window, "submit", submit)
    done = False
    def cleanup(_widget):
        nonlocal done
        if done:
            return
        for item in pending:
            if item.finish:
                item.finish()
        page.running = False
        window.sessions.clear()
        session.connection = session._control = None
        window.close()
        done = True
    qtbot.addWidget(window, before_close_func=cleanup)
    try:
        yield window, page, session, control, pending
    finally:
        cleanup(window)


def test_delayed_query_stop_does_not_cancel_a_later_operation_on_same_mongo_session(controlled):
    window, _page, session, control, pending = controlled
    with session._operation() as first:
        control.operations["A"] = first
        window.stop()
        assert len(pending) == 1 and window._cancelling
        assert pending[0].pool is window.cancel_pool
    with session._operation() as second:
        control.operations["B"] = second
        assert first != second
        pending[0].run()
        assert not session._cancel_requested.is_set()
        assert not control.queries and not control.killed
    pending[0].finish()
    assert not window._cancelling


def test_query_stop_still_cancels_the_exact_current_owned_operation(controlled):
    window, _page, session, control, pending = controlled
    with session._operation() as marker:
        control.operations["A"] = marker
        window.stop()
        assert len(pending) == 1
        pending[0].run()
        assert session._cancel_requested.is_set()
        assert control.killed == ["A"] and control.closed_cursors == 1
        assert control.queries[0][1]["$match"]["command.comment"] == marker
    pending[0].finish()
    assert not window._cancelling


def test_stale_running_gui_flag_with_no_active_marker_cannot_target_future_work(controlled):
    window, _page, session, control, pending = controlled
    assert session.cancellation_marker() is None
    window.stop()
    assert not pending and not window._cancelling
    with session._operation() as marker:
        control.operations["later"] = marker
        assert not session._cancel_requested.is_set()
    assert not control.queries and not control.killed


def test_two_running_query_documents_share_one_marker_bound_control_request(controlled):
    window, _page, session, control, pending = controlled
    other = QueryPage(window, session.config.id)
    other.running = True
    window.tabs.addTab(other, "Generated second")
    try:
        with session._operation() as marker:
            control.operations["A"] = marker
            window.stop()
            assert len(pending) == 1
            pending[0].run()
            assert control.killed == ["A"]
        pending[0].finish()
    finally:
        other.running = False
