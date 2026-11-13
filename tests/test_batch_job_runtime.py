"""One-worker batch execution and real independent cancellation regressions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
import os
import subprocess
import sys
import threading

from bson import Int64
from pymongo.errors import OperationFailure
import pytest

import opennavicat.batch_job_runtime as runtime
from opennavicat.batch_job_runtime import BatchRunControl, PreparedPipelineJob, run_batch_job
from opennavicat.batch_jobs import ResolvedPipelineReference, add_job, new_batch_job, new_pipeline_reference
from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, to_nagg


def plan(*names, continue_on_error=True):
    definition, prepared = new_batch_job(), []
    definition.general['ContinueOnError'] = continue_on_error
    for name in names:
        reference = new_pipeline_reference('Generated Mongo', 'generated_catalog', name)
        definition = add_job(definition, reference)
        resolved = ResolvedPipelineReference('connection-a', 'Generated Mongo', 'generated_catalog',
            'pipeline-' + name, name, to_nagg(PipelineDefinition(source='generated_documents',
                stages=[PipelineStage('$match', {'revision': Int64(2)})])))
        config = ConnectionConfig('connection-a', 'Generated Mongo', driver='mongodb',
            host='generated-host.invalid', password='generated-database-secret',
            ssh_password='generated-ssh-secret', ssh_key_passphrase='generated-key-secret',
            ssl_key_password='generated-tls-secret', ssl_ca='/private/generated/ca.pem')
        prepared.append(PreparedPipelineJob(reference, resolved, config))
    return definition, prepared


class FakeSession:
    def __init__(self, config):
        self.config = config
        self.owner = threading.get_ident()
        self.calls = []
        self.closed = False
        self.marker = None

    def connect(self):
        assert threading.get_ident() == self.owner
        self.calls.append('connect')

    def close(self):
        assert threading.get_ident() == self.owner
        self.calls.append('close')
        self.closed = True

    def cancellation_marker(self):
        return deepcopy(self.marker)


def test_duplicates_execute_in_order_with_independent_owned_sessions_and_frozen_latest_nagg(monkeypatch):
    definition, prepared = plan('repeated', 'repeated')
    original_definition, original_prepared = deepcopy(definition), deepcopy(prepared)
    instances, observed, events, event_threads = [], [], [], []

    def factory(config):
        value = FakeSession(config); instances.append(value); return value

    def execute(session, schema, pipeline):
        assert session.calls == ['connect'] and not session.closed
        observed.append((schema, pipeline.stages[0].body['revision']))
        assert type(pipeline.stages[0].body['revision']) is Int64
        session.config.password = 'mutated-owner-copy'
        pipeline.stages[0].body['revision'] = 99
        # Runtime must not put returned business rows in events or history.
        return ['business-row-must-not-enter-log'], False

    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    def emit(event):
        events.append(event); event_threads.append(threading.get_ident()); event['message'] = 'mutated-sink-copy'

    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(run_batch_job, definition, prepared,
            control=BatchRunControl(), emit=emit, session_factory=factory).result(3)
    assert observed == [('generated_catalog', Int64(2)), ('generated_catalog', Int64(2))]
    assert len(instances) == 2 and instances[0] is not instances[1]
    assert all(value.closed and value.calls == ['connect', 'close'] for value in instances)
    assert len(set(event_threads)) == 1 and event_threads[0] == instances[0].owner != threading.get_ident()
    assert result['status'] == 'success' and result['success'] == result['total'] == 2
    assert result['failed'] == result['cancelled'] == result['skipped'] == 0
    assert result['execute'] == 'Manual' and result['duration_ms'] >= 0
    assert datetime.fromisoformat(result['start_time']).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(result['end_time']).utcoffset().total_seconds() == 0
    assert [event['job_index'] for event in result['logs']] == [0, 0, 1, 1]
    assert all('mutated-sink-copy' != value['message'] for value in result['logs'])
    assert 'business-row-must-not-enter-log' not in json.dumps(result)
    assert definition == original_definition and prepared == original_prepared


@pytest.mark.parametrize('continue_on_error', [False, True])
def test_missing_reference_failure_obeys_continue_policy_without_creating_a_session(monkeypatch, continue_on_error):
    definition, prepared = plan('missing', 'valid', continue_on_error=continue_on_error)
    prepared[0] = PreparedPipelineJob(prepared[0].reference, error='private-reference-error-must-not-be-echoed')
    instances = []
    def factory(config):
        value = FakeSession(config); instances.append(value); return value
    monkeypatch.setattr(runtime, 'execute_pipeline', lambda *_args: ([], False))
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=factory)
    assert result['failed'] == 1 and result['status'] == 'failed'
    assert result['success'] == int(continue_on_error) and result['skipped'] == int(not continue_on_error)
    assert len(instances) == int(continue_on_error) and all(value.closed for value in instances)
    assert 'LocateObject' in result['logs'][1]['message']
    assert 'private-reference-error' not in json.dumps(result)


@pytest.mark.parametrize('phase', ['create', 'connect', 'execute', 'close'])
def test_every_session_failure_is_safe_and_cleanup_runs_on_the_owner(monkeypatch, phase):
    definition, prepared = plan('failing', 'following', continue_on_error=False)
    instances = []
    secret = 'generated-database-secret generated-ssh-secret generated-key-secret generated-tls-secret '
    unsafe = secret + 'generated-host.invalid:27017 /private/generated/ca.pem argv --password hidden'

    class FailingSession(FakeSession):
        def connect(self):
            super().connect()
            if phase == 'connect': raise RuntimeError(unsafe)

        def close(self):
            super().close()
            if phase == 'close': raise RuntimeError(unsafe)

    def factory(config):
        if phase == 'create': raise RuntimeError(unsafe)
        value = FailingSession(config); instances.append(value); return value

    def execute(*_args):
        if phase == 'execute': raise DatabaseError(unsafe)
        return [], False
    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=factory)
    assert result['status'] == 'failed' and result['failed'] == 1 and result['skipped'] == 1
    assert all(value.closed and value.calls.count('close') == 1 for value in instances)
    assert len(instances) == (0 if phase == 'create' else 1)
    for value in ('generated-database-secret', 'generated-ssh-secret', 'generated-key-secret',
                  'generated-tls-secret', 'generated-host.invalid', '/private/generated/ca.pem', 'argv', 'hidden'):
        assert value not in json.dumps(result)
    if phase == 'close':
        assert result['logs'][1]['execution_completed'] is True
        assert 'completed' in result['logs'][1]['message']


def test_safe_server_code_survives_without_server_error_body(monkeypatch):
    definition, prepared = plan('server_error')
    def execute(*_args):
        try:
            raise OperationFailure('private response body', code=121, details={
                'ok': 0, 'code': 121, 'codeName': 'DocumentValidationFailure',
                'errmsg': 'generated-database-secret', 'business': 'must-never-leak'})
        except OperationFailure as error:
            raise DatabaseError('private endpoint generated-host.invalid') from error
    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=FakeSession)
    value = result['logs'][-1]['message']
    assert '121' in value and 'DocumentValidationFailure' in value
    assert not any(private in json.dumps(result) for private in ('generated-database-secret', 'generated-host.invalid', 'must-never-leak', 'private response'))


def test_server_code_name_cannot_smuggle_a_known_credential_into_history(monkeypatch):
    definition, prepared = plan('server_error')
    private = 'PRIVATEBSONTOKEN'
    prepared[0].config.password = private
    def execute(*_args):
        raise OperationFailure('private response body', code=121, details={
            'ok': 0, 'code': 121, 'codeName': private})
    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=FakeSession)
    assert '121' in result['logs'][-1]['message'] and private not in json.dumps(result)


def test_secret_endpoints_paths_and_oversized_job_names_are_not_logged(monkeypatch):
    names = ('generated-database-secret', 'generated-ssh-secret', 'generated-key-secret',
             'generated-tls-secret', '/private/personal/script.nagg', 'example.internal:27017', 'x' * 1000)
    definition, prepared = plan(*names)
    monkeypatch.setattr(runtime, 'execute_pipeline', lambda *_args: ([], False))
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=FakeSession)
    assert result['success'] == len(names)
    assert all(len(event['name']) <= runtime.MAX_LABEL_LENGTH for event in result['logs'])
    for name in names[:-1]:
        assert name not in json.dumps(result)
    assert all(secret not in repr(prepared) for secret in names[:4])


def test_cancel_while_queued_prevents_factory_connection_and_execution(monkeypatch):
    definition, prepared = plan('one', 'two')
    control = BatchRunControl()
    blocked, release = threading.Event(), threading.Event()
    def occupy():
        blocked.set(); assert release.wait(3)
    def forbidden(*_args):
        pytest.fail('Queued cancellation must not create or execute a session')
    monkeypatch.setattr(runtime, 'execute_pipeline', forbidden)
    with ThreadPoolExecutor(max_workers=1) as worker:
        earlier = worker.submit(occupy)
        assert blocked.wait(2)
        future = worker.submit(run_batch_job, definition, prepared,
            control=control, emit=lambda _event: None, session_factory=forbidden)
        control.request_cancel()
        assert control.cancellation_target() is None
        release.set(); earlier.result(3)
        result = future.result(3)
    assert result['status'] == 'cancelled' and result['skipped'] == 2
    assert result['success'] == result['failed'] == result['cancelled'] == 0


@pytest.mark.parametrize('when', ['before_connect', 'after_connect'])
def test_cancel_boundary_before_execution_closes_any_created_session(monkeypatch, when):
    definition, prepared = plan('one', 'two')
    control = BatchRunControl(); instances = []
    class CancellingSession(FakeSession):
        def connect(self):
            super().connect(); control.request_cancel()
    def factory(config):
        value = CancellingSession(config); instances.append(value); return value
    def emit(event):
        if when == 'before_connect' and event['status'] == 'running': control.request_cancel()
    monkeypatch.setattr(runtime, 'execute_pipeline', lambda *_args: pytest.fail('Cancellation before execution must prevent pipeline dispatch'))
    result = run_batch_job(definition, prepared, control=control, emit=emit, session_factory=factory)
    assert result['status'] == 'cancelled' and result['cancelled'] == 1 and result['skipped'] == 1
    assert len(instances) == int(when == 'after_connect')
    assert all(value.closed for value in instances)


def test_cancellation_after_acknowledged_execution_preserves_success_and_skips_following(monkeypatch):
    definition, prepared = plan('acknowledged', 'following')
    control, instances = BatchRunControl(), []
    def factory(config):
        value = FakeSession(config); instances.append(value); return value
    def execute(*_args):
        control.request_cancel()
        return [], False
    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    result = run_batch_job(definition, prepared, control=control, emit=lambda _event: None, session_factory=factory)
    assert result['status'] == 'cancelled' and result['success'] == 1 and result['skipped'] == 1
    assert result['cancelled'] == result['failed'] == 0
    completed = result['logs'][1]
    assert completed['status'] == 'success' and completed['cancel_requested'] and completed['execution_completed']
    assert 'not undone' in completed['message'] and instances[0].closed


def test_real_slow_child_requires_independent_cancel_worker_and_is_reaped(monkeypatch):
    definition, prepared = plan('slow', 'following')
    control, started, instances = BatchRunControl(), threading.Event(), []

    class SlowSession(FakeSession):
        def __init__(self, config):
            super().__init__(config); self.child = None; self.cancel_thread = None

        def cancel(self, expected_comment):
            self.cancel_thread = threading.get_ident()
            assert self.cancel_thread != self.owner
            assert expected_comment == self.marker
            self.child.terminate()

        def close(self):
            try:
                if self.child is not None and self.child.poll() is None:
                    self.child.kill(); self.child.wait(timeout=3)
            finally:
                super().close()

    def factory(config):
        session = SlowSession(config); instances.append(session); return session

    def execute(session, *_args):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        session.child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        session.marker = {'OpenNavicat': 'this-one-owned-slow-operation'}
        started.set()
        session.child.wait(timeout=8)
        raise DatabaseError('private child argv must not be logged')

    monkeypatch.setattr(runtime, 'execute_pipeline', execute)
    with ThreadPoolExecutor(max_workers=1) as owner, ThreadPoolExecutor(max_workers=1) as cancellation:
        future = owner.submit(run_batch_job, definition, prepared,
            control=control, emit=lambda _event: None, session_factory=factory)
        assert started.wait(3)
        assert control.cancellation_target() is None
        control.request_cancel()
        session, marker = control.cancellation_target()
        marker['mutated-copy'] = True
        assert 'mutated-copy' not in session.marker
        session, marker = control.cancellation_target()
        cancellation.submit(session.cancel, expected_comment=marker).result(3)
        result = future.result(3)
    assert result['status'] == 'cancelled' and result['cancelled'] == 1 and result['skipped'] == 1
    assert len(instances) == 1 and session.closed and session.child.poll() is not None
    assert session.cancel_thread != session.owner and control.cancellation_target() is None
    assert 'argv' not in json.dumps(result)


def test_prepared_binding_changes_are_refused_before_any_session(monkeypatch):
    definition, prepared = plan('one')
    prepared[0].config.name = 'different connection identity'
    monkeypatch.setattr(runtime, 'execute_pipeline', lambda *_args: pytest.fail('Invalid bindings cannot execute'))
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None,
        session_factory=lambda *_args: pytest.fail('Invalid bindings cannot connect'))
    assert result['failed'] == 1 and result['status'] == 'failed'
    assert 'LocateObject' in result['logs'][-1]['message']


def test_stream_uses_existing_pipeline_rejection_and_still_closes_the_session():
    definition, prepared = plan('stream')
    original = prepared[0]
    resolved = ResolvedPipelineReference(original.resolved.connection_id,
        original.resolved.connection_name, original.resolved.schema, original.resolved.pipeline_id,
        original.resolved.pipeline_name, to_nagg(PipelineDefinition(source='generated_documents',
            stages=[PipelineStage('$changeStream', {})])))
    prepared[0] = PreparedPipelineJob(original.reference, resolved, original.config)
    instances = []
    def factory(config):
        value = FakeSession(config); instances.append(value); return value
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=lambda _event: None, session_factory=factory)
    assert result['status'] == 'failed' and result['failed'] == 1
    assert instances[0].closed and instances[0].calls == ['connect', 'close']


def test_event_sink_failure_cannot_skip_cleanup_or_disclose_its_exception(monkeypatch):
    definition, prepared = plan('one')
    instances = []
    def factory(config):
        value = FakeSession(config); instances.append(value); return value
    def emit(_event): raise RuntimeError('private sink exception argv generated-database-secret')
    monkeypatch.setattr(runtime, 'execute_pipeline', lambda *_args: ([], False))
    result = run_batch_job(definition, prepared, control=BatchRunControl(), emit=emit, session_factory=factory)
    assert result['status'] == 'success' and instances[0].closed
    assert result['event_delivery_failures'] == 2 and len(result['logs']) == 2
    assert 'private sink' not in json.dumps(result)
