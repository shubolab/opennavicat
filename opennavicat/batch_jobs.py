"""Bounded local Batch Job documents and exact saved-pipeline references.

This module has no database, network, scheduling or email execution capability.
The reference's UUID/Hash are retained as metadata, not authenticated. New local
identities match the observed text shapes without claiming its Hash algorithm.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import uuid

from .database import DatabaseError


MAX_FILE_BYTES = 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 100000
MAX_JOBS = 1000
REFERENCE_VERSION = 1.4

_GENERAL_BOOLS = frozenset({
    'EnableLogging', 'SendEmail', 'SendEmailWhenSuccess', 'SendEmailWhenFail',
    'UseAuthentication', 'ContinueOnError',
})
_GENERAL_STRINGS = frozenset({
    'UUID', 'Hash', 'EmailFrom', 'EmailTo', 'EmailCC', 'EmailSubject', 'EmailBody',
    'EmailServerHost', 'EmailServerPort', 'EmailServerUserName',
    'EmailServerPassword', 'SecureMethod',
})
_SECRET_NAMES = frozenset({
    'password', 'passwd', 'pwd', 'secret', 'token', 'apikey', 'credential',
    'credentials', 'authorization', 'privatekey', 'accesskey',
})
_REFERENCE_FIELDS = {
    'TypeName': 'type_name', 'CloudInstanceID': 'cloud_instance_id',
    'ProjectOwnerNavicatID': 'project_owner_navicat_id', 'Project': 'project',
    'ServerType': 'server_type', 'Server': 'server', 'Catalog': 'catalog',
    'Schema': 'schema', 'DatabaseBrandName': 'database_brand_name',
    'ReferenceName': 'reference_name', 'Param1': 'param1', 'Param2': 'param2',
    'ExtraParams': 'extra_params', 'UseAttachment': 'use_attachment',
    'CommandSwitchOptions': 'command_switch_options',
}


def _error(message):
    # Error messages describe the contract, never imported keys or values.
    return DatabaseError(message)


def _new_general():
    return {
        'UUID': str(uuid.uuid4()).upper(), 'Hash': uuid.uuid4().hex.upper(),
        'EnableLogging': True, 'SendEmail': False, 'SendEmailWhenSuccess': True,
        'SendEmailWhenFail': True, 'EmailFrom': '', 'EmailTo': '', 'EmailCC': '',
        'EmailSubject': '[%F] Batch Job %J Finished %R', 'EmailBody': '%L',
        'EmailServerHost': '', 'EmailServerPort': '', 'EmailServerUserName': '',
        'EmailServerPassword': '', 'UseAuthentication': False, 'SecureMethod': '',
        'ContinueOnError': True,
    }


@dataclass
class BatchJobReference:
    type_name: str = 'aggregationpipeline'
    server_type: str = 'MONGODB'
    server: str = ''
    schema: str = ''
    reference_name: str = ''
    cloud_instance_id: str = ''
    project_owner_navicat_id: str = ''
    project: str = ''
    catalog: str = ''
    database_brand_name: str = 'MongoDB'
    param1: str = ''
    param2: str = ''
    extra_params: dict = field(default_factory=dict)
    use_attachment: bool = False
    command_switch_options: list = field(default_factory=list)
    reference_extra: dict = field(default_factory=dict)
    # Missing imported fields remain missing on export. New references emit the
    # complete observed shape. This local bookkeeping is never serialized.
    present_fields: frozenset[str] | None = field(default=None, repr=False, compare=False)


@dataclass
class BatchJobDefinition:
    version: float = REFERENCE_VERSION
    general: dict = field(default_factory=_new_general)
    jobs: list[BatchJobReference] = field(default_factory=list)
    reference_extra: dict = field(default_factory=dict)
    empty_jobs_as_object: bool = field(default=True, repr=False, compare=False)

    @property
    def enable_logging(self):
        return self.general.get('EnableLogging')

    @property
    def continue_on_error(self):
        return self.general.get('ContinueOnError')

    @property
    def send_email(self):
        return self.general.get('SendEmail')


@dataclass(frozen=True)
class ResolvedPipelineReference:
    connection_id: str
    connection_name: str
    schema: str
    pipeline_id: str
    pipeline_name: str
    nagg: str = field(repr=False)


def _empty_secret(value):
    return value is None or type(value) is str and value == '' or type(value) in (dict, list) and not value


def _secret_field(key):
    normalized = ''.join(character.lower() for character in key if character.isalnum())
    return normalized in _SECRET_NAMES or normalized.endswith(
        ('password', 'passphrase', 'secret', 'token', 'apikey', 'privatekey'))


def _check_json(value):
    pending, count = [(value, 0)], 0
    while pending:
        current, depth = pending.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise _error('The Batch Job exceeds its depth or node bound')
        kind = type(current)
        if kind is dict:
            for key, child in current.items():
                if type(key) is not str:
                    raise _error('Batch Job document keys must be text')
                try:
                    key.encode('utf-8')
                except UnicodeError:
                    raise _error('The Batch Job contains invalid UTF-8 text') from None
                if _secret_field(key) and not _empty_secret(child):
                    raise _error('Batch Job documents cannot contain stored credentials')
                pending.append((child, depth + 1))
            count += len(current)
        elif kind is list:
            pending.extend((child, depth + 1) for child in current)
        elif kind is str:
            try:
                current.encode('utf-8')
            except UnicodeError:
                raise _error('The Batch Job contains invalid UTF-8 text') from None
        elif kind is float:
            if not math.isfinite(current):
                raise _error('Batch Job JSON numbers must be finite')
        elif current is not None and kind not in (int, bool):
            raise _error('Batch Job documents require plain JSON data')


def _reference_document(reference):
    if not isinstance(reference, BatchJobReference):
        raise _error('A Batch Job reference is required')
    if type(reference.reference_extra) is not dict or set(reference.reference_extra) & set(_REFERENCE_FIELDS):
        raise _error('Invalid Batch Job reference extensions')
    _check_json(reference.reference_extra)
    present = reference.present_fields
    if present is not None and (type(present) is not frozenset or not present <= _REFERENCE_FIELDS.keys()):
        raise _error('Invalid imported Batch Job field set')
    result = {}
    for name, attribute in _REFERENCE_FIELDS.items():
        value = getattr(reference, attribute)
        expected = dict if name == 'ExtraParams' else list if name == 'CommandSwitchOptions' else bool if name == 'UseAttachment' else str
        if type(value) is not expected:
            raise _error('A known Batch Job reference field has the wrong type')
        if expected in (dict, list):
            _check_json(value)
        if present is None or name in present:
            result[name] = deepcopy(value)
    result.update(deepcopy(reference.reference_extra))
    return result


def _document(definition):
    if not isinstance(definition, BatchJobDefinition):
        raise _error('A Batch Job definition is required')
    if type(definition.version) not in (float, int) or type(definition.version) is float and not math.isfinite(definition.version):
        raise _error('The Batch Job Version must be a finite number')
    if type(definition.general) is not dict:
        raise _error('The Batch Job General section must be a document')
    _check_json(definition.general)
    for name, value in definition.general.items():
        if name in _GENERAL_BOOLS and type(value) is not bool or name in _GENERAL_STRINGS and type(value) is not str:
            raise _error('A known Batch Job General field has the wrong type')
    if type(definition.jobs) is not list or len(definition.jobs) > MAX_JOBS:
        raise _error('The Batch Job exceeds its ordered job bound')
    if type(definition.empty_jobs_as_object) is not bool:
        raise _error('Invalid empty Batch Job serialization state')
    if type(definition.reference_extra) is not dict or set(definition.reference_extra) & {'Version', 'General', 'Jobs'}:
        raise _error('Invalid Batch Job document extensions')
    _check_json(definition.reference_extra)
    jobs = [_reference_document(reference) for reference in definition.jobs]
    result = {'Version': definition.version, 'General': deepcopy(definition.general),
              'Jobs': {} if not jobs and definition.empty_jobs_as_object else jobs}
    result.update(deepcopy(definition.reference_extra))
    _check_json(result)
    return result


def _dump(document):
    try:
        text = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise _error('Batch Job files are limited to 1 MiB')
        return text
    except DatabaseError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise _error('The Batch Job cannot be encoded as bounded plain JSON') from None


def validate_definition(definition):
    _dump(_document(definition))
    return definition


def new_batch_job():
    return BatchJobDefinition()


def new_pipeline_reference(connection_name, schema, pipeline_name):
    if any(type(value) is not str or not value for value in (connection_name, schema, pipeline_name)):
        raise _error('A saved pipeline reference requires exact nonempty names')
    value = BatchJobReference(server=connection_name, schema=schema, reference_name=pipeline_name)
    _dump(_reference_document(value))
    return value


def to_nbatpremium(definition):
    return _dump(_document(definition))


def from_nbatpremium(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise _error('Duplicate Batch Job JSON fields are not accepted')
            result[key] = value
        return result

    try:
        if type(text) is bytes:
            if len(text) > MAX_FILE_BYTES:
                raise _error('Batch Job files are limited to 1 MiB')
            text = text.decode('utf-8')
        if type(text) is not str:
            raise _error('A Batch Job file must be UTF-8 JSON text')
        if len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise _error('Batch Job files are limited to 1 MiB')
        document = json.loads(text, object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(_error('Batch Job JSON numbers must be finite')))
        _check_json(document)
        if type(document) is not dict or not {'Version', 'General', 'Jobs'} <= document.keys():
            raise _error('The Batch Job requires Version, General and Jobs sections')
        jobs = document['Jobs']
        if type(jobs) is dict and not jobs:
            jobs = []
        elif type(jobs) is not list:
            raise _error('Batch Job Jobs must be an ordered list or an empty document')
        if any(type(value) is not dict for value in jobs):
            raise _error('Batch Job Jobs must be an ordered list of documents')
        references = []
        for value in jobs:
            attributes = {attribute: deepcopy(value[name]) for name, attribute in _REFERENCE_FIELDS.items() if name in value}
            attributes['reference_extra'] = {key: deepcopy(child) for key, child in value.items() if key not in _REFERENCE_FIELDS}
            attributes['present_fields'] = frozenset(value.keys() & _REFERENCE_FIELDS.keys())
            references.append(BatchJobReference(**attributes))
        definition = BatchJobDefinition(document['Version'], deepcopy(document['General']), references,
            {key: deepcopy(value) for key, value in document.items() if key not in {'Version', 'General', 'Jobs'}},
            empty_jobs_as_object=type(document['Jobs']) is dict or bool(jobs))
        return validate_definition(definition)
    except DatabaseError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise _error('Invalid bounded UTF-8 Batch Job JSON') from None


def _position(value, length, *, insertion=False):
    if type(value) is not int or not 0 <= value < length + int(insertion):
        raise _error('The Batch Job position is outside its ordered list')


def add_job(definition, reference, index=None):
    validate_definition(definition)
    candidate = deepcopy(definition)
    index = len(candidate.jobs) if index is None else index
    _position(index, len(candidate.jobs), insertion=True)
    candidate.jobs.insert(index, deepcopy(reference))
    return validate_definition(candidate)


def remove_job(definition, index):
    validate_definition(definition)
    _position(index, len(definition.jobs))
    candidate = deepcopy(definition)
    candidate.jobs.pop(index)
    if not candidate.jobs:
        candidate.empty_jobs_as_object = True
    return validate_definition(candidate)


def move_job(definition, source, target):
    validate_definition(definition)
    _position(source, len(definition.jobs)); _position(target, len(definition.jobs))
    candidate = deepcopy(definition)
    candidate.jobs.insert(target, candidate.jobs.pop(source))
    return validate_definition(candidate)


def _require_reference(reference):
    _dump(_reference_document(reference))
    required = {'TypeName', 'ServerType', 'Server', 'Schema', 'ReferenceName'}
    if reference.present_fields is not None and not required <= reference.present_fields:
        raise _error('The saved pipeline reference is incomplete')
    if reference.type_name != 'aggregationpipeline' or reference.server_type != 'MONGODB':
        raise _error('This Batch Job task type or database driver is not executable')
    if any((reference.cloud_instance_id, reference.project_owner_navicat_id, reference.project)):
        raise _error('Cloud-account Batch Job references are not local database references')
    if any((reference.catalog, reference.param1, reference.param2, reference.extra_params,
            reference.use_attachment, reference.command_switch_options, reference.reference_extra)):
        raise _error('This Batch Job reference contains unqualified execution options')
    if any(not value for value in (reference.server, reference.schema, reference.reference_name)):
        raise _error('The saved pipeline reference is incomplete')


def require_executable(definition):
    validate_definition(definition)
    if definition.version != REFERENCE_VERSION:
        raise _error('This Batch Job Version has not been qualified for execution')
    if definition.general.get('SendEmail') is not False:
        raise _error('Batch Job email execution is not supported')
    if any(type(definition.general.get(name)) is not bool for name in ('EnableLogging', 'ContinueOnError')):
        raise _error('Batch Job execution requires explicit logging and error policies')
    if not definition.jobs:
        raise _error('A Batch Job requires at least one executable task')
    for reference in definition.jobs:
        _require_reference(reference)
    if definition.reference_extra or set(definition.general) - (_GENERAL_BOOLS | _GENERAL_STRINGS):
        raise _error('This Batch Job contains unqualified execution metadata')
    return definition


def resolve_pipeline_reference(reference, profiles, pipelines):
    """Freeze the latest exact-name local saved pipeline for this one run.

    Only id/name/driver are read from supplied profiles. A rename deliberately
    breaks an old name reference; stable IDs never silently follow it.
    """
    from .mongodb_pipeline import from_nagg

    _require_reference(reference)
    if any(type(values) not in (list, tuple) or len(values) > MAX_NODES for values in (profiles, pipelines)):
        raise _error('Batch Job reference resolution requires bounded supplied collections')
    def attribute(value, name):
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)

    matches = [profile for profile in profiles if attribute(profile, 'name') == reference.server]
    if len(matches) != 1:
        raise _error('The Batch Job connection reference is missing or ambiguous')
    profile = matches[0]
    identity = attribute(profile, 'id')
    if attribute(profile, 'driver') != 'mongodb':
        raise _error('The Batch Job connection reference has a different database driver')
    if type(identity) is not str or not identity or sum(attribute(value, 'id') == identity for value in profiles) != 1:
        raise _error('The Batch Job connection identity is missing or ambiguous')
    matches = [pipeline for pipeline in pipelines if isinstance(pipeline, Mapping)
               and pipeline.get('connection_id') == identity and pipeline.get('schema') == reference.schema
               and pipeline.get('name') == reference.reference_name]
    if len(matches) != 1:
        raise _error('The Batch Job saved pipeline reference is missing or ambiguous')
    pipeline = matches[0]
    if type(pipeline.get('id')) is not str or not pipeline['id'] or type(pipeline.get('nagg')) is not str:
        raise _error('The Batch Job saved pipeline identity or document is invalid')
    from_nagg(pipeline['nagg'])
    return ResolvedPipelineReference(identity, reference.server, reference.schema,
        pipeline['id'], reference.reference_name, pipeline['nagg'])
