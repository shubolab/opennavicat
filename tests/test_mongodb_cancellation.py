"""Cancellation identities survive queued callbacks and operation handoffs."""
from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

from bson import BSON, Binary, Int64

from opennavicat.database import ConnectionConfig
from opennavicat.mongodb_session import BSON_CODEC, MongoSession


def session_with_control(admin):
    session = MongoSession(ConnectionConfig('own', 'Synthetic', driver='mongodb'))
    session.connection = object()
    session._control = SimpleNamespace(admin=admin)
    return session


class Control:
    def __init__(self, started=None, release=None):
        self.calls = []
        self.started, self.release = started, release
        self.cursor_closed = False

    def aggregate(self, pipeline, **kwargs):
        self.calls.append(('aggregate', copy.deepcopy(pipeline)))
        if self.started is not None:
            self.started.set()
            assert self.release.wait(3), 'The test must release its own control request'
        owner = self

        class Rows:
            def __iter__(self):
                return iter([{'opid': 17}])

            def close(self):
                owner.cursor_closed = True

        return Rows()

    def command(self, command, **kwargs):
        self.calls.append(('command', command))


def test_delayed_old_marker_and_explicit_none_cannot_cancel_the_next_operation():
    control = Control()
    session = session_with_control(control)
    assert session.cancellation_marker() is None
    with session._operation():
        old = session.cancellation_marker()
    deferred_cancel = lambda: session.cancel(expected_comment=old)
    with session._operation():
        current = session.cancellation_marker()
        assert current != old
        deferred_cancel()
        session.cancel(expected_comment=None)
        session.cancel(expected_comment={'not': 'a captured BSON comment'})
        assert session.cancellation_marker() == current
        assert not session._cancel_requested.is_set()
        assert control.calls == []
    session.cancel(expected_comment=old)
    assert not session._cancel_requested.is_set()


def test_marker_is_detached_typed_bson_and_matching_marker_cancels_only_its_nonce():
    control = Control()
    session = session_with_control(control)
    with session._operation(user_comment={'n': Int64(1), 'b': Binary(b'\0\xff', 128), 'nan': float('nan')}):
        marker = session.cancellation_marker()
        assert type(marker['userComment']['n']) is Int64
        mutated = session.cancellation_marker()
        mutated['userComment']['n'] = 1
        # Int32 and Int64 compare equal in Python, but are different comments.
        session.cancel(expected_comment=mutated)
        assert not session._cancel_requested.is_set() and not control.calls
        session.cancel(expected_comment=marker)
        assert session._cancel_requested.is_set()
        actual = control.calls[0][1][1]['$match']['command.comment']
        assert BSON.encode(actual, codec_options=BSON_CODEC) == BSON.encode(marker, codec_options=BSON_CODEC)
        assert control.calls[1] == ('command', {'killOp': 1, 'op': 17})
        assert control.cursor_closed


def test_inflight_old_control_request_cannot_set_new_operation_flag_or_target_it():
    started, release = threading.Event(), threading.Event()
    control = Control(started, release)
    session = session_with_control(control)
    errors = []

    def cancel(marker):
        try:
            session.cancel(expected_comment=marker)
        except BaseException as exc:
            errors.append(exc)

    thread = None
    try:
        with session._operation():
            old = session.cancellation_marker()
            thread = threading.Thread(target=cancel, args=(old,))
            thread.start()
            assert started.wait(3)
            assert session._cancel_requested.is_set()
        with session._operation():
            current = session.cancellation_marker()
            assert current != old and not session._cancel_requested.is_set()
            release.set()
            thread.join(3)
            assert not thread.is_alive() and not errors
            assert not session._cancel_requested.is_set()
            assert session.cancellation_marker() == current
            assert control.calls[0][1][1]['$match']['command.comment'] == old
            assert control.calls[1] == ('command', {'killOp': 1, 'op': 17})
            session.cancel(expected_comment=old)
            assert len(control.calls) == 2 and not session._cancel_requested.is_set()
    finally:
        release.set()
        if thread is not None:
            thread.join(3)
            assert not thread.is_alive()


def test_no_argument_retains_immediate_cancellation_and_nested_marker_scope():
    control = Control()
    session = session_with_control(control)
    with session._operation():
        outer = session.cancellation_marker()
        with session._operation():
            inner = session.cancellation_marker()
            session.cancel(expected_comment=outer)
            assert not session._cancel_requested.is_set() and not control.calls
            session.cancel()
            assert session._cancel_requested.is_set()
            assert control.calls[0][1][1]['$match']['command.comment'] == inner
        assert session.cancellation_marker() == outer
    assert session.cancellation_marker() is None
    session.cancel()
    assert session._cancel_requested.is_set()  # Existing no-argument contract.
