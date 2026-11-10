"""Data-only query presentation; neither Qt objects nor a database capability.

All grid cells are text in an explicitly named encoding. BSON and JS-only
values must never fall through a generic json.dumps(default=str) renderer.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import timezone

from bson import BSON, json_util
from bson.errors import BSONError
from bson.binary import UuidRepresentation
from bson.codec_options import DatetimeConversion

from ..database import QueryResult
from .codec import BSON_CODEC, decode
from .runtime import JavaScriptResult


JSON_OPTIONS = json_util.CANONICAL_JSON_OPTIONS.with_options(
    uuid_representation=UuidRepresentation.UNSPECIFIED, tz_aware=True,
    tzinfo=timezone.utc, datetime_conversion=DatetimeConversion.DATETIME_AUTO,
)


@dataclass(frozen=True)
class QueryPresentation:
    result: QueryResult
    messages: str
    summary: tuple[tuple[str, ...], ...]
    completion: str


def _js_only(tree):
    if tree[0] in {'js-bigint', 'js-undefined'}:
        return True
    children = [pair[1] for pair in tree[1]] if tree[0] == 'document' else tree[1] if tree[0] == 'array' else []
    return any(_js_only(child) for child in children)


def display_value(tree):
    """Return (encoding, exact text), proving EJSON reversibility before use."""
    value = decode(tree, allow_big=True)
    if _js_only(tree):
        # BigInt has no unbounded BSON counterpart. This is our tagged IPC
        # format, explicitly not Canonical EJSON or a MongoDB import document.
        return 'JavaScript tagged value v1', json.dumps(
            {'format': 'opennavicat-js-value-v1', 'tree': tree},
            ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    raw = BSON.encode({'value': value}, codec_options=BSON_CODEC)
    encoded = json_util.dumps({'value': value}, json_options=JSON_OPTIONS,
                              ensure_ascii=True, separators=(',', ':'))
    try:
        restored = json_util.loads(encoded, json_options=JSON_OPTIONS)
        exact = BSON.encode(restored, codec_options=BSON_CODEC) == raw
    except (BSONError, ValueError, TypeError, KeyError, OverflowError):
        exact = False
    if exact:
        # Serialize the value itself only after proving it in a BSON wrapper;
        # scalar Int32/Int64/Double and out-of-range dates stay distinguishable.
        return 'Canonical Extended JSON', json_util.dumps(value, json_options=JSON_OPTIONS,
            ensure_ascii=True, separators=(',', ':'))
    # Literal $date/$oid fields may collide with EJSON type wrappers. Preserve
    # the exact BSON wrapper instead of silently exporting a changed document.
    return 'BSON wrapper bytes (base64)', json.dumps(
        {'format': 'opennavicat-bson-value-v1', 'field': 'value',
         'bsonBase64': base64.b64encode(raw).decode('ascii')}, separators=(',', ':'))


def present_execution(execution: JavaScriptResult, source: str) -> QueryPresentation:
    rows = []
    tree = execution.value_tree
    completion = 'No successful completion value'
    if not execution.error and tree is not None:
        decode(tree, allow_big=True)  # Validate the complete tagged tree first.
        if tree[0] == 'js-undefined':
            completion = 'JavaScript undefined (no result row)'
        elif tree[0] == 'array' and all(child[0] == 'document' for child in tree[1]):
            rows = [list(display_value(child)) for child in tree[1]]
            completion = f'Array: {len(rows)} documents'
        else:
            rows = [list(display_value(tree))]
            completion = 'JavaScript BigInt' if tree[0] == 'js-bigint' else 'Completion value'
    summary = tuple((str(row.sequence), row.verb, 'write' if row.writes else 'read',
        row.outcome, str(row.affected) if row.affected is not None else 'unknown', row.error)
        for row in execution.commands)
    messages = [completion, f'Elapsed: {execution.elapsed_ms:.3f} ms',
        f'Child reaped: {execution.child_reaped}',
        f'Open cursors: {execution.open_cursors}; open sessions: {execution.open_sessions}']
    for row in execution.commands:
        messages.append(f'[{row.sequence}] {row.verb}: {row.outcome}; affected: '
                        + (str(row.affected) if row.affected is not None else 'unknown'))
        if row.error:
            messages.append(row.error)
    if execution.has_confirmed_writes:
        messages.append('Acknowledged writes remain committed, including when a later statement fails.')
    if execution.uncertain_writes:
        messages.append('Write acknowledgement is uncertain. Inspect the server before retrying this write.')
    if execution.error:
        messages.append('[Error] ' + execution.error)
    result = QueryResult(columns=['Encoding', 'Value'] if rows else [], rows=rows,
        affected=execution.confirmed_write_count, elapsed_ms=execution.elapsed_ms,
        sql=source, partial=bool(execution.error and execution.commands))
    return QueryPresentation(result, '\n'.join(messages), summary, completion)
