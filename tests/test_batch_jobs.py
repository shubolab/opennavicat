"""Synthetic Batch Job format and local-reference contracts, without I/O."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import re
import uuid

from bson import Int64
import pytest

from opennavicat.database import DatabaseError
from opennavicat.batch_jobs import (
    MAX_FILE_BYTES, MAX_JOBS, BatchJobDefinition, BatchJobReference, add_job,
    from_nbatpremium, move_job, new_batch_job, new_pipeline_reference,
    remove_job, require_executable, resolve_pipeline_reference,
    to_nbatpremium, validate_definition,
)
from opennavicat.mongodb_pipeline import PipelineDefinition, PipelineStage, from_nagg, to_nagg


def document():
    """Hand-authored records; no raw reference file is copied into the package."""
    return {
        'Version': 1.4,
        'General': {
            'UUID': 'synthetic-local-identity', 'Hash': 'opaque-unverified-reference-hash',
            'EnableLogging': True, 'ContinueOnError': True, 'SendEmail': False,
            'EmailServerPassword': '', 'FutureGeneral': {'enabled': False, 'order': [2, 1]},
        },
        'Jobs': [{
            'TypeName': 'aggregationpipeline', 'ServerType': 'MONGODB',
            'Server': 'Generated Mongo', 'Schema': 'generated_catalog',
            'ReferenceName': 'generated_pipeline', 'FutureJob': {'label': '保留'},
        }],
        'FutureTop': {'empty': None, 'number': 1.25},
    }


def text(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def executable():
    return add_job(new_batch_job(), new_pipeline_reference(
        'Generated Mongo', 'generated_catalog', 'generated_pipeline'))


def supplied():
    profile = {'id': 'connection-a', 'name': 'Generated Mongo', 'driver': 'mongodb',
               'password': 'must-never-enter-the-snapshot'}
    pipeline = {'id': 'pipeline-a', 'connection_id': 'connection-a',
                'schema': 'generated_catalog', 'name': 'generated_pipeline',
                'nagg': to_nagg(PipelineDefinition(source='generated_documents',
                    stages=[PipelineStage('$match', {'revision': Int64(1)})]))}
    return profile, pipeline


def test_new_empty_draft_has_observed_defaults_and_local_unverified_id_shapes():
    first, second = new_batch_job(), new_batch_job()
    assert validate_definition(first) is first and first.jobs == []
    assert first.enable_logging is True and first.continue_on_error is True and first.send_email is False
    assert str(uuid.UUID(first.general['UUID'])).upper() == first.general['UUID']
    assert re.fullmatch('[0-9A-F]{32}', first.general['Hash'])
    assert first.general['UUID'] != second.general['UUID'] and first.general['Hash'] != second.general['Hash']
    assert first.general['EmailServerPassword'] == ''
    assert json.loads(to_nbatpremium(first))['Jobs'] == {}
    with pytest.raises(DatabaseError, match='at least one'):
        require_executable(first)


def test_roundtrip_preserves_unknown_metadata_missing_fields_order_and_opaque_hash():
    source = document()
    source['Jobs'].append(deepcopy(source['Jobs'][0]))
    definition = from_nbatpremium(text(source).encode('utf-8'))
    assert len(definition.jobs) == 2 and definition.jobs[0] == definition.jobs[1]
    assert json.loads(to_nbatpremium(definition)) == source
    assert list(json.loads(to_nbatpremium(definition))) == list(source)
    assert definition.general['Hash'] == 'opaque-unverified-reference-hash'
    assert 'Param1' not in json.loads(to_nbatpremium(definition))['Jobs'][0]
    definition.jobs[0].reference_extra['FutureJob']['label'] = 'edited local copy'
    assert definition.jobs[1].reference_extra['FutureJob']['label'] == '保留'


def test_unknown_type_and_future_version_are_retained_but_not_executable():
    source = document(); source['Version'] = 2
    source['Jobs'][0] = {'TypeName': 'future_profile_kind', 'UnknownTypeFields': [1, 2]}
    definition = from_nbatpremium(text(source))
    assert json.loads(to_nbatpremium(definition)) == source
    with pytest.raises(DatabaseError, match='Version'):
        require_executable(definition)
    definition.version = 1.4
    with pytest.raises(DatabaseError, match='incomplete|not executable'):
        require_executable(definition)


@pytest.mark.parametrize('payload', [
    '{"Version":1.4,"Version":1.4,"General":{},"Jobs":[]}',
    '{"Version":1.4,"General":{"Nested":{"same":1,"same":2}},"Jobs":[]}',
    '{"Version":1.4,"General":{},"Jobs":[{"TypeName":"a","TypeName":"b"}]}',
])
def test_duplicate_fields_are_rejected_at_every_document_level(payload):
    with pytest.raises(DatabaseError, match='Duplicate'):
        from_nbatpremium(payload)


@pytest.mark.parametrize('payload', [b'\xff', 'not-json', '[]', '{}',
    '{"Version":NaN,"General":{},"Jobs":[]}',
    '{"Version":1.4,"General":{"unknown":Infinity},"Jobs":[]}',
    '{"Version":1.4,"General":{"unknown":"\\ud800"},"Jobs":[]}',
    '{"Version":1.4,"General":{},"Jobs":[]} trailing', None])
def test_invalid_nonfinite_or_non_utf8_input_is_refused(payload):
    with pytest.raises(DatabaseError):
        from_nbatpremium(payload)


@pytest.mark.parametrize('section,key,value', [
    ('top', 'Version', True), ('top', 'Version', '1.4'),
    ('top', 'General', []), ('top', 'Jobs', {'nonempty': True}),
    ('general', 'EnableLogging', 1), ('general', 'ContinueOnError', 'true'),
    ('general', 'SendEmail', None), ('general', 'EmailServerPort', 25),
    ('general', 'Hash', []), ('general', 'EmailServerPassword', None),
    ('job', 'TypeName', 1), ('job', 'ServerType', None), ('job', 'Server', False),
    ('job', 'ExtraParams', []), ('job', 'UseAttachment', 0),
    ('job', 'CommandSwitchOptions', {}),
])
def test_known_fields_have_strict_types(section, key, value):
    source = document()
    target = source if section == 'top' else source['General'] if section == 'general' else source['Jobs'][0]
    target[key] = value
    with pytest.raises(DatabaseError):
        from_nbatpremium(text(source))


@pytest.mark.parametrize('section,key', [
    ('general', 'EmailServerPassword'), ('general', 'FutureAccessToken'),
    ('top', 'api_key'), ('job', 'Password'),
])
def test_nonempty_credentials_are_refused_without_echo_on_import_and_export(section, key):
    secret = 'synthetic-secret-that-must-not-be-echoed'
    source = document()
    target = source if section == 'top' else source['General'] if section == 'general' else source['Jobs'][0]
    target[key] = secret
    with pytest.raises(DatabaseError) as failure:
        from_nbatpremium(text(source))
    assert secret not in str(failure.value) and key not in str(failure.value)
    model = new_batch_job(); model.general[key] = secret
    with pytest.raises(DatabaseError) as failure:
        to_nbatpremium(model)
    assert secret not in str(failure.value) and key not in str(failure.value)


def test_send_email_metadata_roundtrips_without_secret_but_cannot_execute():
    value = executable(); value.general['SendEmail'] = True
    imported = from_nbatpremium(to_nbatpremium(value))
    assert imported.send_email is True
    with pytest.raises(DatabaseError, match='email execution'):
        require_executable(imported)


def test_json_byte_depth_node_and_ordered_job_bounds_are_enforced():
    source = {'Version': 1.4, 'General': {}, 'Jobs': [], 'Padding': ''}
    source['Padding'] = 'x' * (MAX_FILE_BYTES - len(text(source).encode('utf-8')))
    exact = text(source)
    assert len(exact.encode('utf-8')) == MAX_FILE_BYTES
    assert to_nbatpremium(from_nbatpremium(exact)) == exact
    source['Padding'] += 'x'
    with pytest.raises(DatabaseError, match='1 MiB'):
        from_nbatpremium(text(source))
    with pytest.raises(DatabaseError, match='1 MiB'):
        from_nbatpremium(text({'Version': 1.4, 'General': {}, 'Jobs': [], 'Padding': '中' * MAX_FILE_BYTES}))
    nested = None
    for _ in range(66):
        nested = [nested]
    with pytest.raises(DatabaseError, match='depth or node'):
        from_nbatpremium(text({'Version': 1.4, 'General': {}, 'Jobs': [], 'nested': nested}))
    with pytest.raises(DatabaseError, match='depth or node'):
        from_nbatpremium(text({'Version': 1.4, 'General': {}, 'Jobs': [], 'nodes': [0] * 100001}))
    with pytest.raises(DatabaseError, match='ordered job bound'):
        validate_definition(BatchJobDefinition(jobs=[BatchJobReference()] * (MAX_JOBS + 1)))


@pytest.mark.parametrize('location', ['general', 'top', 'reference', 'extra_params'])
def test_direct_deep_or_cyclic_models_are_bounded_before_recursive_copy(location):
    nested = []
    nested.append(nested)
    value = executable()
    if location == 'general': value.general['Nested'] = nested
    elif location == 'top': value.reference_extra['Nested'] = nested
    elif location == 'reference': value.jobs[0].reference_extra['Nested'] = nested
    else: value.jobs[0].extra_params['Nested'] = nested
    with pytest.raises(DatabaseError, match='depth or node'):
        validate_definition(value)


@pytest.mark.parametrize('value', [object(), b'bytes', (1, 2), float('nan')])
def test_direct_models_refuse_non_plain_json(value):
    model = new_batch_job(); model.reference_extra['Unknown'] = value
    with pytest.raises(DatabaseError):
        to_nbatpremium(model)


def test_ordered_operations_allow_duplicates_and_leave_originals_detached():
    base = new_batch_job(); reference = new_pipeline_reference('Generated Mongo', 'db', 'one')
    first = add_job(base, reference)
    second = add_job(first, reference)
    third = add_job(second, new_pipeline_reference('Generated Mongo', 'db', 'two'), 1)
    assert [value.reference_name for value in third.jobs] == ['one', 'two', 'one']
    moved = move_job(third, 1, 0)
    assert [value.reference_name for value in moved.jobs] == ['two', 'one', 'one']
    reduced = remove_job(moved, 0)
    assert [value.reference_name for value in reduced.jobs] == ['one', 'one']
    reduced.jobs[0].server = 'changed'
    assert reduced.jobs[1].server == first.jobs[0].server == reference.server == 'Generated Mongo'
    assert base.jobs == [] and len(second.jobs) == 2


@pytest.mark.parametrize('shape', [{}, []])
def test_imported_empty_job_shapes_roundtrip_without_changing_them(shape):
    source = {'Version': 1.4, 'General': {}, 'Jobs': shape}
    assert json.loads(to_nbatpremium(from_nbatpremium(text(source)))) == source


def test_removing_the_last_reference_uses_observed_empty_object_shape():
    source = json.loads(to_nbatpremium(executable()))
    imported = from_nbatpremium(text(source))
    removed = remove_job(imported, 0)
    assert removed.jobs == [] and json.loads(to_nbatpremium(removed))['Jobs'] == {}
    assert len(imported.jobs) == 1
    legacy_empty = from_nbatpremium(text({'Version': 1.4, 'General': {}, 'Jobs': []}))
    edited = add_job(legacy_empty, new_pipeline_reference('Generated Mongo', 'db', 'one'))
    assert json.loads(to_nbatpremium(remove_job(edited, 0)))['Jobs'] == {}


@pytest.mark.parametrize('operation,args', [
    (add_job, (BatchJobReference(), -1)), (add_job, (BatchJobReference(), 2)),
    (remove_job, (-1,)), (remove_job, (True,)), (remove_job, (1,)),
    (move_job, (0, 1)), (move_job, (False, 0)),
])
def test_invalid_edits_fail_atomically(operation, args):
    value = executable(); before = to_nbatpremium(value)
    with pytest.raises(DatabaseError):
        operation(value, *args)
    assert to_nbatpremium(value) == before


def test_resolver_reads_latest_profile_then_freezes_only_nonsecret_identity_and_nagg():
    profile, pipeline = supplied(); reference = executable().jobs[0]
    first = resolve_pipeline_reference(reference, [profile], [pipeline])
    pipeline['nagg'] = to_nagg(PipelineDefinition(source='generated_documents',
        stages=[PipelineStage('$match', {'revision': Int64(2)})]))
    second = resolve_pipeline_reference(reference, [profile], [pipeline])
    assert first.connection_id == second.connection_id == 'connection-a'
    assert first.nagg != second.nagg
    assert from_nagg(first.nagg).stages[0].body == {'revision': Int64(1)}
    assert from_nagg(second.nagg).stages[0].body == {'revision': Int64(2)}
    assert 'password' not in vars(second) and profile['password'] not in repr(second)
    with pytest.raises(FrozenInstanceError):
        second.nagg = 'changed'


def test_resolver_never_reads_secret_attributes():
    _profile, pipeline = supplied()
    class Profile:
        id, name, driver = 'connection-a', 'Generated Mongo', 'mongodb'

        @property
        def password(self):
            pytest.fail('Reference resolution must never read a password')
    result = resolve_pipeline_reference(executable().jobs[0], [Profile()], [pipeline])
    assert result.connection_id == 'connection-a'


@pytest.mark.parametrize('problem', [
    'missing_connection', 'ambiguous_connection', 'wrong_driver', 'duplicate_connection_id',
    'missing_pipeline', 'ambiguous_pipeline', 'wrong_schema', 'wrong_connection',
    'connection_rename', 'pipeline_rename', 'invalid_nagg',
])
def test_resolver_refuses_missing_ambiguous_stale_and_mismatched_references(problem):
    profile, pipeline = supplied(); profiles, pipelines = [profile], [pipeline]
    if problem == 'missing_connection': profiles = []
    elif problem == 'ambiguous_connection': profiles.append(dict(profile, id='connection-b'))
    elif problem == 'wrong_driver': profile['driver'] = 'mysql'
    elif problem == 'duplicate_connection_id': profiles.append(dict(profile, name='Other Mongo'))
    elif problem == 'missing_pipeline': pipelines = []
    elif problem == 'ambiguous_pipeline': pipelines.append(dict(pipeline, id='pipeline-b'))
    elif problem == 'wrong_schema': pipeline['schema'] = 'other_catalog'
    elif problem == 'wrong_connection': pipeline['connection_id'] = 'connection-b'
    elif problem == 'connection_rename': profile['name'] = 'Renamed Mongo'
    elif problem == 'pipeline_rename': pipeline['name'] = 'renamed_pipeline'
    elif problem == 'invalid_nagg': pipeline['nagg'] = '{}'
    with pytest.raises(DatabaseError):
        resolve_pipeline_reference(executable().jobs[0], profiles, pipelines)


@pytest.mark.parametrize('attribute,value', [
    ('type_name', 'query'), ('server_type', 'MYSQL'), ('cloud_instance_id', 'cloud-metadata'),
    ('project', 'account-project'), ('project_owner_navicat_id', 'account-id'),
    ('param1', 'unknown-option'), ('extra_params', {'future': True}),
    ('reference_extra', {'future': True}), ('use_attachment', True), ('catalog', 'other'),
    ('server', ''), ('schema', ''), ('reference_name', ''),
])
def test_unqualified_or_product_account_references_are_not_database_connections(attribute, value):
    model = executable(); setattr(model.jobs[0], attribute, value)
    assert validate_definition(model) is model  # Retained metadata/draft, not execution acceptance.
    with pytest.raises(DatabaseError):
        require_executable(model)
    profile, pipeline = supplied()
    with pytest.raises(DatabaseError):
        resolve_pipeline_reference(model.jobs[0], [profile], [pipeline])


def test_missing_imported_type_is_not_silently_defaulted_for_execution():
    value = document(); del value['Jobs'][0]['TypeName']; del value['Jobs'][0]['FutureJob']
    model = from_nbatpremium(text(value))
    assert 'TypeName' not in json.loads(to_nbatpremium(model))['Jobs'][0]
    with pytest.raises(DatabaseError, match='incomplete'):
        require_executable(model)


@pytest.mark.parametrize('location', ['top', 'general'])
def test_unknown_batch_settings_are_retained_without_executing_ignored_semantics(location):
    model = new_batch_job()
    model.jobs.append(new_pipeline_reference('Generated', 'generated_db', 'generated_pipeline'))
    if location == 'top':
        model.reference_extra['FutureSetting'] = {'enabled': True}
    else:
        model.general['FutureSetting'] = {'enabled': True}
    saved = to_nbatpremium(model)
    imported = from_nbatpremium(saved)
    with pytest.raises(DatabaseError, match='metadata'):
        require_executable(imported)
    assert to_nbatpremium(imported) == saved
