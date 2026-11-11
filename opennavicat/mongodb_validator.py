"""Detached, atomic BSON edits for a MongoDB validator draft.

This is an editing model, not a server-schema validator or a mode converter.
Only an exact single ``$jsonSchema`` document has a visual projection. Unknown
rules remain in the complete BSON document; unsafe property rewrites stop.
Raw BSON that cannot be represented exactly is retained as a read-only draft.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bson import BSON, Code, DBRef

from .database import DatabaseError
from .mongodb_session import BSON_CODEC


Path = tuple[str | int, ...]
MAX_ERROR_LABEL_CHARS = 512
MAX_DEPENDENCY_NAMES = 6
MAX_DEPENDENCY_NAME_CHARS = 40


def _bounded_label(value, limit=MAX_ERROR_LABEL_CHARS):
    return value if len(value) <= limit else value[:limit - 1] + '…'


@dataclass(frozen=True)
class ValidatorLimits:
    max_bytes: int = 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 100000
    max_patches: int = 1024

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise DatabaseError('Validator editing limits must be positive integers')


@dataclass(frozen=True)
class SetKeyword:
    path: Path
    value: Any


@dataclass(frozen=True)
class RemoveKeyword:
    path: Path


@dataclass(frozen=True)
class AddProperty:
    schema_path: Path
    name: str
    schema: dict
    before: str | None = None
    required: bool | None = None


@dataclass(frozen=True)
class RenameProperty:
    schema_path: Path
    name: str
    new_name: str


@dataclass(frozen=True)
class RemoveProperty:
    schema_path: Path
    name: str


class UnresolvedSchemaDependencyError(DatabaseError):
    """A name rewrite would require understanding retained schema rules."""


# Everything else, including future extensions, is an unresolved dependency for
# rename/drop. This is a conservative editing boundary, not an unsupported/N/A
# classification for the complete database tool.
_NAME_INDEPENDENT = frozenset({
    'bsonType', 'type', 'title', 'description', '$comment', 'properties', 'required',
    'additionalProperties', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
    'multipleOf', 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems',
    'uniqueItems', 'items', 'additionalItems', 'minProperties', 'maxProperties', 'enum',
})
_SCHEMA_MAPS = frozenset({'properties', 'patternProperties', 'dependencies',
                          'dependentSchemas', 'definitions', '$defs'})
_SCHEMA_ARRAYS = frozenset({'allOf', 'anyOf', 'oneOf'})
_SCHEMA_CHILDREN = frozenset({'items', 'additionalItems', 'additionalProperties',
                              'not', 'if', 'then', 'else', 'contains'})


def _check_tree(value, limits):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise DatabaseError('The validator exceeds its node or depth bound')
        if isinstance(item, dict):
            if nodes + len(stack) + len(item) > limits.max_nodes:
                raise DatabaseError('The validator exceeds its node or depth bound')
            for key, child in item.items():
                if type(key) is not str or '\0' in key:
                    raise DatabaseError('BSON document keys must be strings without NUL')
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if nodes + len(stack) + len(item) > limits.max_nodes:
                raise DatabaseError('The validator exceeds its node or depth bound')
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, tuple):
            raise DatabaseError('Use explicit BSON arrays rather than implicitly converted tuples')
        elif isinstance(item, Code) and item.scope is not None:
            stack.append((item.scope, depth + 1))
        elif isinstance(item, DBRef):
            stack.append((dict(item.as_doc()), depth + 1))
        elif isinstance(item, datetime):
            observed = item if item.tzinfo is None else item.astimezone(timezone.utc)
            if observed.microsecond % 1000:
                raise DatabaseError('BSON dates require explicit millisecond precision')


def _encode(document, limits):
    if not isinstance(document, dict):
        raise DatabaseError('A complete validator must be a BSON document')
    try:
        _check_tree(document, limits)
        raw = BSON.encode(document, codec_options=BSON_CODEC)
        if len(raw) > limits.max_bytes:
            raise DatabaseError('The validator exceeds its BSON byte bound')
        decoded = BSON(raw).decode(codec_options=BSON_CODEC)
        if BSON.encode(decoded, codec_options=BSON_CODEC) != raw:
            raise DatabaseError('The validator cannot be edited as exact BSON')
        return raw
    except DatabaseError:
        raise
    except Exception as error:
        raise DatabaseError('The validator cannot be encoded as exact bounded BSON') from error


def _path(path):
    if (not isinstance(path, tuple) or not path or
            any(not (type(part) is str and '\0' not in part or type(part) is int and part >= 0)
                for part in path)):
        raise DatabaseError('Use a nonempty tuple path of BSON keys or array indexes')
    return path


def _at(document, path):
    current = document
    for part in path:
        if isinstance(current, dict) and type(part) is str and part in current:
            current = current[part]
        elif isinstance(current, list) and type(part) is int and part < len(current):
            current = current[part]
        else:
            raise DatabaseError('The validator patch path does not identify an existing value')
    return current


def _schema_ancestors(document, path):
    _path(path)
    if list(document) != ['$jsonSchema'] or not isinstance(document['$jsonSchema'], dict):
        raise DatabaseError('Property editing requires an exact single $jsonSchema object')
    if path[0] != '$jsonSchema':
        raise DatabaseError('The property owner must be inside the projected $jsonSchema')
    current, role = document['$jsonSchema'], 'schema'
    ancestors = [current]
    for part in path[1:]:
        child = _at(current, (part,))
        if role == 'schema' and type(part) is str:
            if part in _SCHEMA_MAPS:
                role = 'map'
            elif part in _SCHEMA_ARRAYS:
                role = 'array'
            elif part in _SCHEMA_CHILDREN:
                role = 'array' if isinstance(child, list) else 'schema'
            else:
                raise DatabaseError('The property path does not identify a schema document')
        elif role == 'map' and type(part) is str or role == 'array' and type(part) is int:
            role = 'schema'
        else:
            raise DatabaseError('The property path does not identify a schema document')
        if role in {'schema', 'map'} and not isinstance(child, dict) or role == 'array' and not isinstance(child, list):
            raise DatabaseError('The property path does not identify a schema document')
        current = child
        if role == 'schema':
            ancestors.append(current)
    if role != 'schema':
        raise DatabaseError('The property owner must be a schema document, not a schema map')
    return ancestors


def _schema(document, path):
    return _schema_ancestors(document, path)[-1]


def _check_keyword_structure(document, path):
    if list(document) != ['$jsonSchema'] or not isinstance(document['$jsonSchema'], dict):
        return
    if len(path) == 1 and path[0] == '$jsonSchema':
        raise DatabaseError('Use a new complete query draft to replace the projected schema')
    for offset, part in enumerate(path):
        if part != 'properties' or len(path) > offset + 2:
            continue
        try:
            _schema(document, path[:offset])
        except DatabaseError:
            continue
        raise DatabaseError('Use ordered property patches for property maps or their members')


def _name(name):
    if type(name) is not str or '\0' in name:
        raise DatabaseError('A property name must be an explicit BSON key without NUL')


def _required(schema):
    values = schema.get('required', [])
    if not isinstance(values, list) or any(type(name) is not str for name in values):
        raise DatabaseError('The existing required rule is not a string array; it was retained unchanged')
    return values


def _contains_document(value):
    if isinstance(value, dict):
        return True
    return isinstance(value, list) and any(_contains_document(child) for child in value)


def _check_name_dependencies(document, path):
    # An ancestor's retained combinator/$ref can constrain the nested property.
    for owner in _schema_ancestors(document, path):
        unresolved = set(owner) - _NAME_INDEPENDENT
        if 'enum' in owner and _contains_document(owner['enum']):
            unresolved.add('enum (object-dependent values)')
        if unresolved:
            names = sorted(unresolved)
            shown = names[:MAX_DEPENDENCY_NAMES]
            detail = ', '.join(_bounded_label(repr(name), MAX_DEPENDENCY_NAME_CHARS) for name in shown)
            if len(names) > len(shown):
                detail += f'; … {len(names) - len(shown)} more'
            detail += f' ({len(names)} total)'
            raise UnresolvedSchemaDependencyError(
                _bounded_label('Property rename/removal requires unresolved schema rules: ' + detail))


class ValidatorDraft:
    def __init__(self, original: dict | bytes, *, limits: ValidatorLimits | None = None):
        self.limits = ValidatorLimits() if limits is None else limits
        if not isinstance(self.limits, ValidatorLimits):
            raise DatabaseError('Explicit ValidatorLimits are required')
        self._issues = ()
        if isinstance(original, bytes):
            if len(original) > self.limits.max_bytes:
                raise DatabaseError('The validator exceeds its BSON byte bound')
            self._raw = self._original_raw = bytes(original)
            try:
                document = BSON(original).decode(codec_options=BSON_CODEC)
                if _encode(document, self.limits) != original:
                    raise DatabaseError('The original validator cannot be decoded without changing its BSON')
            except Exception as error:
                self._document = None
                self._issues = (_bounded_label(str(error)),)
                return
        else:
            document = original
            self._raw = self._original_raw = _encode(document, self.limits)
        self._document = deepcopy(document)

    @property
    def original_raw(self):
        return self._original_raw

    @property
    def raw_bson(self):
        return self._raw

    @property
    def read_only(self):
        return self._document is None

    @property
    def issues(self):
        return self._issues

    @property
    def document(self):
        if self.read_only:
            raise DatabaseError(_bounded_label('The original validator BSON is retained read-only: ' + '; '.join(self.issues)))
        return deepcopy(self._document)

    @property
    def changed(self):
        return self._raw != self._original_raw

    @property
    def visual_schema(self):
        if (not self.read_only and list(self._document) == ['$jsonSchema']
                and isinstance(self._document['$jsonSchema'], dict)):
            return deepcopy(self._document['$jsonSchema'])
        return None

    def apply(self, patches):
        """Apply a bounded batch all-or-nothing; return a detached final document."""
        candidate = self.document
        count = 0
        for patch in patches:
            count += 1
            if count > self.limits.max_patches:
                raise DatabaseError('The validator patch batch exceeds its bound')
            self._apply_one(candidate, patch)
            _encode(candidate, self.limits)
        raw = _encode(candidate, self.limits)
        self._document, self._raw = candidate, raw
        return self.document

    def _apply_one(self, document, patch):
        if isinstance(patch, (SetKeyword, RemoveKeyword)):
            path = _path(patch.path)
            _check_keyword_structure(document, path)
            owner = _at(document, path[:-1])
            if not isinstance(owner, dict) or type(path[-1]) is not str:
                raise DatabaseError('A keyword patch must target a BSON document key')
            if isinstance(patch, SetKeyword):
                _encode({'value': patch.value}, self.limits)
                owner[path[-1]] = deepcopy(patch.value)
            elif path[-1] in owner:
                del owner[path[-1]]
            else:
                raise DatabaseError('The keyword to remove does not exist')
            return
        if not isinstance(patch, (AddProperty, RenameProperty, RemoveProperty)):
            raise DatabaseError('Unknown validator patch type')
        schema = _schema(document, patch.schema_path)
        _name(patch.name)
        properties = schema.get('properties', {})
        if not isinstance(properties, dict):
            raise DatabaseError('The existing properties rule is not a document; it was retained unchanged')
        if isinstance(patch, AddProperty):
            if patch.name in properties:
                raise DatabaseError('A property with this name already exists')
            if patch.before is not None:
                _name(patch.before)
                if patch.before not in properties:
                    raise DatabaseError('The requested property insertion position does not exist')
            if not isinstance(patch.schema, dict) or patch.required is not None and type(patch.required) is not bool:
                raise DatabaseError('Provide an explicit property schema and optional boolean required edit')
            _encode(patch.schema, self.limits)
            inserted = {}
            for name, definition in properties.items():
                if name == patch.before:
                    inserted[patch.name] = deepcopy(patch.schema)
                inserted[name] = definition
            if patch.before is None:
                inserted[patch.name] = deepcopy(patch.schema)
            schema['properties'] = inserted
            if patch.required is not None:
                required = _required(schema)
                if patch.required:
                    if patch.name not in required:
                        schema['required'] = [*required, patch.name]
                elif 'required' in schema:
                    schema['required'] = [name for name in required if name != patch.name]
            return
        if patch.name not in properties:
            raise DatabaseError('The property to rename/remove no longer exists')
        if isinstance(patch, RenameProperty):
            _name(patch.new_name)
            if patch.new_name == patch.name:
                return
            if patch.new_name in properties:
                raise DatabaseError('A property with the replacement name already exists')
        _check_name_dependencies(document, patch.schema_path)
        required = _required(schema)
        if isinstance(patch, RenameProperty):
            schema['properties'] = {patch.new_name if name == patch.name else name: value
                                    for name, value in properties.items()}
            if 'required' in schema:
                schema['required'] = ([name for name in required if name != patch.name]
                    if patch.new_name in required else
                    [patch.new_name if name == patch.name else name for name in required])
        else:
            del properties[patch.name]
            if 'required' in schema:
                schema['required'] = [name for name in required if name != patch.name]
