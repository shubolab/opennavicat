import pytest

from opennavicat.compare import apply_plan, compare_tables
from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession


@pytest.fixture
def pair(tmp_path):
    sessions = [DatabaseSession(ConnectionConfig(str(i), name, database=str(tmp_path / f'{i}.sqlite'))) for i, name in enumerate(['Source', 'Target'])]
    for session in sessions:
        session.execute('CREATE TABLE things (id INTEGER PRIMARY KEY, value TEXT, amount REAL)')
    sessions[0].import_rows('main', 'things', ['id', 'value', 'amount'], [[1, 'new', 2.5], [2, 'same', None], [3, 'insert', 1]])
    sessions[1].import_rows('main', 'things', ['id', 'value', 'amount'], [[1, 'old', 2.5], [2, 'same', None], [4, 'extra', 9]])
    yield sessions
    for session in sessions:
        session.close()


def preview(pair):
    return compare_tables(*pair, 'main', 'things', 'main', 'things')


def test_compare_is_read_only_and_apply_keeps_extras_by_default(pair):
    source, target = pair
    before = target.fetch_table('main', 'things').rows
    plan = preview(pair)
    assert (len(plan.inserts), len(plan.updates), len(plan.deletes), plan.unchanged) == (1, 1, 1, 1)
    assert target.fetch_table('main', 'things').rows == before
    assert apply_plan(target, plan) == {'inserted': 1, 'updated': 1, 'deleted': 0}
    assert target.fetch_table('main', 'things').rows == [[1, 'new', 2.5], [2, 'same', None], [3, 'insert', 1], [4, 'extra', 9]]
    with pytest.raises(ValueError, match='already'):
        apply_plan(target, plan)


def test_opt_in_delete_makes_target_match_source(pair):
    source, target = pair
    apply_plan(target, preview(pair), include_deletes=True)
    assert target.fetch_table('main', 'things').rows == source.fetch_table('main', 'things').rows


def test_stale_preview_rolls_back_entire_apply(pair):
    source, target = pair
    plan = preview(pair)
    target.execute("UPDATE things SET value='someone else' WHERE id=1")
    with pytest.raises(DatabaseError, match='changed'):
        apply_plan(target, plan, include_deletes=True)
    assert target.fetch_table('main', 'things').rows == [[1, 'someone else', 2.5], [2, 'same', None], [4, 'extra', 9]]
    assert not target.in_transaction
    assert not plan.applied


def test_insert_conflict_rolls_back_prior_updates_and_deletes(pair):
    _, target = pair
    plan = preview(pair)
    target.insert_row('main', 'things', {'id': 3, 'value': 'concurrent insert'})
    before = target.fetch_table('main', 'things').rows
    with pytest.raises(DatabaseError):
        apply_plan(target, plan, include_deletes=True)
    assert target.fetch_table('main', 'things').rows == before


def test_preview_refuses_partial_or_schema_mismatch(pair):
    with pytest.raises(ValueError, match='No partial'):
        compare_tables(*pair, 'main', 'things', 'main', 'things', max_rows=2)
    pair[1].execute('ALTER TABLE things ADD COLUMN extra TEXT')
    with pytest.raises(ValueError, match='same column'):
        preview(pair)


def test_apply_rejects_reconfigured_profile_and_schema_drift(pair):
    _, target = pair
    plan = preview(pair)
    target.config.host = "different-target"
    with pytest.raises(ValueError, match='settings changed'):
        apply_plan(target, plan)
    target.config.host = "localhost"
    target.execute('ALTER TABLE things ADD COLUMN surprise TEXT')
    with pytest.raises(ValueError, match='structure changed'):
        apply_plan(target, plan)


def test_preview_is_bound_to_jump_host_and_approved_identity(pair):
    _, target = pair
    target.config.ssh_enabled = True
    target.config.ssh_host = "first.invalid"
    target.config.ssh_username = "test"
    target.config.ssh_host_key = "fixture-public-pin"
    plan = preview(pair)  # existing local session isolates the profile-binding check
    target.config.ssh_host = "second.invalid"
    with pytest.raises(ValueError, match="settings changed"):
        apply_plan(target, plan)
    target.config.ssh_host = "first.invalid"
    target.config.ssh_host_key = "changed-public-pin"
    with pytest.raises(ValueError, match="settings changed"):
        apply_plan(target, plan)
    target.config.ssh_enabled = False
    with pytest.raises(ValueError, match="settings changed"):
        apply_plan(target, plan)


def test_logical_comparison_keeps_driver_types_idempotent_and_precision():
    from datetime import date, datetime
    from decimal import Decimal
    from opennavicat.compare import logical_value
    assert logical_value('2026-11-10', {'type': 'DATE'}) == logical_value(date(2026, 11, 10), {'type': 'date'})
    assert logical_value('2026-11-10 12:30:00', {'type': 'TIMESTAMP'}) == logical_value(datetime(2026, 11, 10, 12, 30), {'type': 'timestamp without time zone'})
    assert logical_value(0.1, {'type': 'NUMERIC'}) == logical_value(Decimal('0.1'), {'type': 'numeric(12,2)'})
    assert logical_value(0.1, {'type': 'NUMERIC'}) != logical_value(Decimal('0.100000000000000001'), {'type': 'numeric'})
    assert logical_value('01', {'type': 'TEXT'}) != logical_value(1, {'type': 'INTEGER'})
