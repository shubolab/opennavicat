"""Local Batch Job persistence, independent run numbering and failed writes."""
from copy import deepcopy
import json

import pytest

from opennavicat.batch_jobs import new_batch_job, to_nbatpremium
from opennavicat.database import DatabaseError
from opennavicat.storage import Store


def test_saved_batch_job_and_history_survive_reload_with_independent_run_numbers(tmp_path):
    store = Store(tmp_path)
    text = to_nbatpremium(new_batch_job())
    first = store.save_batch_job('generated first', text)
    second = store.save_batch_job('generated second', text)
    outcome = dict(status='success', total=0, success=0, failed=0, skipped=0, cancelled=0, logs=[])
    assert store.record_batch_job_run(first['id'], outcome)['execution_id'] == 1
    assert store.record_batch_job_run(second['id'], outcome)['execution_id'] == 1
    assert store.record_batch_job_run(first['id'], outcome)['execution_id'] == 2
    assert 'execution_id' not in outcome
    restored = Store(tmp_path)
    assert restored.batch_job(first['id']) == first
    assert [r['execution_id'] for r in restored.batch_job_history(first['id'])] == [2, 1]
    assert [r['execution_id'] for r in restored.batch_job_history(second['id'])] == [1]
    detached = restored.batch_jobs()
    detached[0]['name'] = 'changed outside store'
    assert restored.batch_job(first['id'])['name'] == 'generated first'


def test_batch_job_failed_flush_keeps_both_memory_and_saved_file(tmp_path, monkeypatch):
    store = Store(tmp_path)
    text = to_nbatpremium(new_batch_job())
    existing = store.save_batch_job('generated', text)
    before, raw = deepcopy(store.state), store.path.read_bytes()
    def failed_write():
        raise OSError('generated write failure')
    monkeypatch.setattr(store, 'flush', failed_write)
    with pytest.raises(OSError):
        store.save_batch_job('renamed', text, job_id=existing['id'])
    assert store.state == before and store.path.read_bytes() == raw
    with pytest.raises(OSError):
        store.touch_batch_job(existing['id'])
    assert store.state == before and store.path.read_bytes() == raw


def test_batch_job_rejects_plaintext_mail_secret_before_workspace_write(tmp_path):
    store = Store(tmp_path)
    entry = store.save_batch_job('generated', to_nbatpremium(new_batch_job()))
    before = store.path.read_bytes()
    document = json.loads(entry['nbatpremium'])
    document['General']['EmailServerPassword'] = 'generated-private-test-value'
    with pytest.raises(DatabaseError) as caught:
        store.save_batch_job('bad', json.dumps(document))
    assert 'generated-private-test-value' not in str(caught.value)
    assert store.path.read_bytes() == before
    assert len(store.batch_jobs()) == 1


def test_batch_history_bounds_reject_before_database_mutation(tmp_path):
    store = Store(tmp_path)
    entry = store.save_batch_job('generated', to_nbatpremium(new_batch_job()))
    with pytest.raises(ValueError):
        store.record_batch_job_run(entry['id'], {'logs': ['generated'] * 5001})
    with pytest.raises(ValueError):
        store.record_batch_job_run(entry['id'], {'logs': [], 'generated': 'x' * (2 * 1024 * 1024)})
    assert store.batch_job_history(entry['id']) == []
