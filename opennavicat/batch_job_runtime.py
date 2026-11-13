"""Serial one-run Batch Job execution, with a separate cancellation authority.

The caller prepares detached local references/configurations on the GUI thread.
This module reads no Store or widgets and creates no schedules or email actions.
Only the external cancellation worker may call a session's cancel method.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import threading
import time

from .batch_jobs import (
    MAX_JOBS, BatchJobDefinition, BatchJobReference, ResolvedPipelineReference,
    require_executable,
)
from .database import ConnectionConfig, DatabaseError, DatabaseSession
from .js_engine.server_errors import server_failure
from .mongodb_pipeline import execute_pipeline, from_nagg


MAX_LABEL_LENGTH = 160
MAX_LOG_EVENTS = 2 * MAX_JOBS + 1
_PRIVATE_CONFIG_FIELDS = (
    'password', 'ssh_password', 'ssh_key_passphrase', 'ssl_key_password',
    'host', 'ssh_host', 'ssl_ca', 'ssl_cert', 'ssl_key', 'ssl_crl', 'ssh_key_file',
)
_ENDPOINT_OR_PATH = re.compile(
    r'(?:[\\/]|\w+://|\b(?:\d{1,3}\.){3}\d{1,3}\b|'
    r'\b[a-z0-9](?:[a-z0-9-]*\.)+[a-z]{2,}\b|\[[0-9a-f:]+\]|[a-z0-9-]+:\d+)', re.I)


@dataclass(frozen=True)
class PreparedPipelineJob:
    reference: BatchJobReference = field(repr=False)
    resolved: ResolvedPipelineReference | None = field(default=None, repr=False)
    config: ConnectionConfig | None = field(default=None, repr=False)
    error: str = field(default='', repr=False)


class BatchRunControl:
    """A cancellation request and this run's current owner-session identity."""

    def __init__(self, cancel_event=None):
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._lock = threading.Lock()
        self._current_session = None

    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def request_cancel(self):
        self.cancel_event.set()

    def cancellation_target(self):
        """Return a detached current marker for the external cancel worker.

        A missing operation marker cannot cancel a later operation. The session
        must enforce expected_comment when the separate worker eventually runs.
        This function performs no network I/O or cancellation itself.
        """
        if not self.cancelled:
            return None
        with self._lock:
            session = self._current_session
            if session is None:
                return None
            marker = session.cancellation_marker()
            return (session, deepcopy(marker)) if marker is not None else None

    def _bind(self, session):
        with self._lock:
            self._current_session = session

    def _unbind(self, session):
        with self._lock:
            if self._current_session is session:
                self._current_session = None


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _private_values(prepared_jobs):
    return tuple(value for item in prepared_jobs if item.config is not None
                 for name in _PRIVATE_CONFIG_FIELDS
                 if isinstance(value := getattr(item.config, name, None), str) and value)


def _safe_name(value, index, private_values):
    if (not isinstance(value, str) or _ENDPOINT_OR_PATH.search(value)
            or any(secret in value for secret in private_values)):
        return f'Task {index + 1}'
    value = ''.join(character if ord(character) >= 32 and ord(character) != 127 else ' ' for character in value)
    return value[:MAX_LABEL_LENGTH] if value else f'Task {index + 1}'


def _failure_message(error, phase, private_values):
    failure = server_failure(error)
    if failure is not None:
        name = failure.code_name
        # codeName is server-provided text, even though its shape is bounded.
        # Never let it smuggle a known credential or private host into history.
        label = f' ({name})' if name and not any(value in name for value in private_values) else ''
        return f'MongoDB server rejected the task: code {failure.code}{label}'
    if isinstance(error, DatabaseError) and error.authentication_required:
        return 'Database authentication was rejected'
    return {
        'prepare': 'LocateObject: the saved pipeline reference could not be resolved',
        'create': 'The task session could not be initialized',
        'connect': 'The task database connection failed',
        'execute': 'The pipeline execution failed; acknowledged changes are not automatically undone',
    }.get(phase, 'The task failed')


def _prepared_definition(reference, prepared):
    if prepared.error or prepared.resolved is None or prepared.config is None:
        raise DatabaseError('Unresolved saved pipeline reference')
    resolved, config = prepared.resolved, prepared.config
    if (not isinstance(resolved, ResolvedPipelineReference) or not isinstance(config, ConnectionConfig)
            or prepared.reference != reference or config.driver != 'mongodb'
            or config.id != resolved.connection_id or config.name != resolved.connection_name
            or reference.server != resolved.connection_name or reference.schema != resolved.schema
            or reference.reference_name != resolved.pipeline_name):
        raise DatabaseError('The prepared saved pipeline binding is stale or mismatched')
    return from_nagg(resolved.nagg)


def run_batch_job(definition, prepared_jobs, *, control, emit, session_factory=DatabaseSession):
    """Run one detached, ordered plan on the calling database-owner worker.

    Duplicates are independent attempts. Completed execution remains success
    when cancellation arrives afterward; subsequent tasks are skipped. No task
    is retried, and no successful/uncertain database effect is rolled back.
    """
    if not isinstance(control, BatchRunControl) or not callable(emit):
        raise DatabaseError('A Batch Job requires its own run control and event sink')
    require_executable(definition)
    if (type(prepared_jobs) not in (list, tuple) or len(prepared_jobs) != len(definition.jobs)
            or any(not isinstance(value, PreparedPipelineJob) for value in prepared_jobs)):
        raise DatabaseError('The prepared Batch Job does not match its ordered references')
    # Freeze again at the worker boundary; session-side changes cannot alter
    # the GUI's prepared references, configurations or latest saved documents.
    definition, prepared_jobs = deepcopy(definition), deepcopy(prepared_jobs)
    private_values = _private_values(prepared_jobs)
    started, start_time = time.monotonic(), _timestamp()
    result = dict(start_time=start_time, end_time='', duration_ms=0.0, execute='Manual',
                  status='success', total=len(definition.jobs), success=0, failed=0,
                  skipped=0, cancelled=0, logs=[], event_delivery_failures=0)

    def event(status, index, message, **details):
        value = dict(timestamp=_timestamp(), status=status, job_index=index,
                     name=_safe_name(definition.jobs[index].reference_name, index, private_values),
                     message=message, **details)
        if len(result['logs']) < MAX_LOG_EVENTS:
            result['logs'].append(value)
        try:
            emit(deepcopy(value))
        except Exception:
            # The final returned result still carries the bounded complete log.
            # Never let a presentation callback bypass session cleanup or echo
            # an exception's argv, endpoint, secrets, result rows or SQL text.
            result['event_delivery_failures'] += 1

    stopped_on_error = False
    for index, reference in enumerate(definition.jobs):
        if control.cancelled or stopped_on_error:
            result['skipped'] += 1
            event('skipped', index, 'Skipped after cancellation' if control.cancelled else 'Skipped after an earlier task failed')
            continue
        session, phase, completed = None, 'prepare', False
        status, message, cleanup_failed = 'failed', '', False
        event('running', index, 'Starting the saved pipeline task')
        try:
            item = prepared_jobs[index]
            pipeline = _prepared_definition(reference, item)
            if control.cancelled:
                status, message = 'cancelled', 'Cancelled before connecting; no task execution was dispatched'
            else:
                phase = 'create'
                session = session_factory(deepcopy(item.config))
                control._bind(session)
                if control.cancelled:
                    status, message = 'cancelled', 'Cancelled before connecting; no task execution was dispatched'
                else:
                    phase = 'connect'
                    session.connect()
                    if control.cancelled:
                        status, message = 'cancelled', 'Cancelled before execution; no pipeline execution was dispatched'
                    else:
                        phase = 'execute'
                        execute_pipeline(session, item.resolved.schema, pipeline)
                        completed = True
                        status, message = 'success', 'Pipeline execution completed'
        except Exception as error:
            if control.cancelled:
                status, message = 'cancelled', 'Cancellation requested; acknowledgement may be unknown, and changes are not automatically undone or retried'
            else:
                status, message = 'failed', _failure_message(error, phase, private_values)
        finally:
            if session is not None:
                control._unbind(session)
                try:
                    session.close()
                except Exception:
                    cleanup_failed = True
        if cleanup_failed:
            status = 'failed'
            message = ('Pipeline execution completed, but session cleanup failed; do not automatically retry'
                       if completed else 'Task session cleanup failed; do not automatically retry')
        if completed and control.cancelled and not cleanup_failed:
            message = 'Pipeline execution completed before cancellation; completed database effects are not undone'
        result[status] += 1
        event(status, index, message, execution_completed=completed, cancel_requested=control.cancelled)
        if status == 'failed' and not definition.continue_on_error:
            stopped_on_error = True
    result['status'] = 'cancelled' if control.cancelled else 'failed' if result['failed'] else 'success'
    result['end_time'] = _timestamp()
    result['duration_ms'] = round((time.monotonic() - started) * 1000, 3)
    return result
