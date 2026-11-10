"""Reviewed, atomic SQLite global index and trigger editing.

Snapshots retain the complete selected schema catalog and connection binding.
Only structured definitions produce executable plans; raw declarations are
read-only provenance, never a user-supplied DDL execution escape hatch.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field

from .database import DatabaseSession
from .schema import (
    IndexDefinition, IndexTerm, SchemaConflictError, SchemaError, TriggerDefinition,
    _binding, _catalog, _expression, _fingerprint, _identifier, _matching,
    _parse_index, _parse_trigger, _q, _qualified, _render_index, _terminate_sql, _tokens,
)


@dataclass
class IndexObjectDefinition(IndexDefinition):
    table: str = ""


@dataclass
class TriggerObjectDefinition(TriggerDefinition):
    table: str = ""
    owner_type: str = "table"


@dataclass
class ObjectSnapshot:
    schema: str
    name: str
    kind: str
    definition: IndexObjectDefinition | TriggerObjectDefinition
    ddl: str
    fingerprint: str
    exists: bool = True
    issues: list[str] = field(default_factory=list)
    read_only: bool = False
    catalog: list[tuple] = field(default_factory=list, repr=False)
    binding: str = field(default="", repr=False)


@dataclass
class ObjectPlan:
    snapshot: ObjectSnapshot
    definition: IndexObjectDefinition | TriggerObjectDefinition
    statements: list[str]
    ddl: str
    changed: bool
    warnings: list[str] = field(default_factory=list)
    steps: list[tuple[str, tuple]] = field(default_factory=list, repr=False)
    fingerprint: str = field(default="", repr=False)

    @property
    def sql(self) -> str:
        return "\n\n".join(_terminate_sql(statement) for statement in self.statements)


_PLAN_KEY = secrets.token_bytes(32)


def new_index(schema: str = "main", table: str = "") -> ObjectSnapshot:
    return ObjectSnapshot(schema, "", "index", IndexObjectDefinition("", table=table), "", "", exists=False)


def new_trigger(schema: str = "main", table: str = "", owner_type: str = "table") -> ObjectSnapshot:
    timing = "INSTEAD OF" if owner_type == "view" else "BEFORE"
    return ObjectSnapshot(schema, "", "trigger", TriggerObjectDefinition("", timing=timing, table=table, owner_type=owner_type),
                          "", "", exists=False)


def load_index(session: DatabaseSession, schema: str, name: str) -> ObjectSnapshot:
    snapshot, entry = _load_base(session, schema, name, "index")
    _, name, table, sql = entry
    try:
        if not sql:
            info = session._query(f"PRAGMA {_q(schema)}.index_list({_q(table)})")
            details = next(row for row in info if _fold(row[1]) == _fold(name))
            terms = [IndexTerm(row[2] or "", row[4] or "", bool(row[3]),
                               "<unknown expression>" if row[2] is None else "")
                     for row in session._query(f"PRAGMA {_q(schema)}.index_xinfo({_q(name)})") if row[5]]
            snapshot.definition = IndexObjectDefinition(name, [term.column or term.expression for term in terms],
                bool(details[2]), source_name=name, terms=terms, table=table)
            snapshot.read_only = True
            snapshot.issues.append("Automatic primary/unique constraint indexes are read-only; edit their table constraint instead")
        else:
            after, _ = _header(sql, "INDEX")
            tokens = _tokens(sql)
            if tokens[after].upper != "ON":
                raise SchemaError("Unrecognized index header")
            owner, _, cursor = _qualified_identifier(tokens, after + 1)
            if _fold(owner) != _fold(table) or tokens[cursor].text != "(":
                raise SchemaError("The index's declared table differs from its catalog owner")
            closing = _matching(tokens, cursor)
            suffix = tokens[closing + 1:]
            if suffix and suffix[0].text != ";" and suffix[0].upper != "WHERE":
                raise SchemaError("Unrecognized index suffix")
            parsed = _parse_index(sql, name)
            snapshot.definition = IndexObjectDefinition(**asdict(parsed), table=table)
            # asdict recursively expands IndexTerm; preserve the public typed API.
            snapshot.definition.terms = copy.deepcopy(parsed.terms)
            if parsed.where or any(term.expression for term in parsed.terms):
                snapshot.read_only = True
                snapshot.issues.append("Expression and partial indexes retain their exact original SQL and are read-only in this designer")
    except (SchemaError, IndexError, StopIteration, sqlite3.Error) as exc:
        snapshot.issues.append(str(exc))
        snapshot.read_only = True
    return snapshot


def load_trigger(session: DatabaseSession, schema: str, name: str) -> ObjectSnapshot:
    snapshot, entry = _load_base(session, schema, name, "trigger")
    _, name, table, sql = entry
    if schema == "temp":
        snapshot.read_only = True
        snapshot.issues.append("Temporary triggers may bind cross-schema owners and are protected in this designer")
    try:
        owner = _owner_entry(snapshot.catalog, table)
        if not sql or owner is None:
            raise SchemaError("The trigger declaration or its owner cannot be resolved in the selected schema")
        parsed, owner_schema = _read_trigger(sql, name, table)
        if owner_schema and _fold(owner_schema) != _fold(schema):
            raise SchemaError("Cross-schema temporary triggers are protected; their exact original SQL remains available")
        snapshot.definition = TriggerObjectDefinition(**asdict(parsed), table=table, owner_type=owner[0])
        _validate_trigger(session, schema, snapshot.definition, snapshot.catalog)
    except (SchemaError, IndexError, StopIteration, sqlite3.Error) as exc:
        snapshot.issues.append(str(exc))
        snapshot.read_only = True
    return snapshot


def plan_index(session: DatabaseSession, snapshot: ObjectSnapshot, definition: IndexObjectDefinition) -> ObjectPlan:
    return _plan(session, snapshot, definition, "index")


def plan_trigger(session: DatabaseSession, snapshot: ObjectSnapshot, definition: TriggerObjectDefinition) -> ObjectPlan:
    return _plan(session, snapshot, definition, "trigger")


def apply_plan(session: DatabaseSession, plan: ObjectPlan) -> ObjectSnapshot:
    if not isinstance(plan, ObjectPlan) or not hmac.compare_digest(plan.fingerprint, _seal(plan)):
        raise SchemaConflictError("The reviewed object plan was modified or was not produced by this planner")
    snapshot = plan.snapshot
    if plan.changed:
        session._assert_write()
    _verify_snapshot(session, snapshot)
    if not plan.changed:
        return _loader(snapshot.kind)(session, snapshot.schema, snapshot.name)
    if session.in_transaction:
        raise SchemaError("Finish the current transaction before saving an index or trigger")
    session._cancel_requested.clear()
    session._active = True
    write_started = False
    try:
        session._query("BEGIN IMMEDIATE")
        _verify_snapshot(session, snapshot)
        for sql, parameters in plan.steps:
            if session._cancel_requested.is_set():
                raise SchemaError("Object save cancelled")
            write_started = True
            session._query(sql, parameters)
        _compile_dependencies(session, snapshot.schema)
        if session._cancel_requested.is_set():
            raise SchemaError("Object save cancelled")
        result = _loader(snapshot.kind)(session, snapshot.schema, plan.definition.name)
        if result.read_only and not snapshot.read_only:
            raise SchemaError("The resulting object could not be loaded without loss; the original object was retained")
        session._query("COMMIT")
        return result
    except BaseException as exc:
        session._cancel_requested.clear()
        try:
            if session.in_transaction:
                session._query("ROLLBACK")
            if write_started:
                _verify_snapshot(session, snapshot)
        except Exception as rollback_error:
            raise SchemaError("Object save failed and rollback could not be confirmed; close and reopen the connection") from rollback_error
        if isinstance(exc, SchemaError):
            raise
        if isinstance(exc, Exception):
            raise SchemaError("Object was not saved; all changes were rolled back: " + str(exc)) from exc
        raise
    finally:
        session._cancel_requested.clear()
        session._active = False


def _fold(name):
    """SQLite identifier comparison folds ASCII only, not Unicode casefold."""
    return name.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _loader(kind):
    if kind == "index":
        return load_index
    if kind == "trigger":
        return load_trigger
    raise SchemaError("This planner supports SQLite indexes and triggers only")


def _owner_entry(catalog, name):
    return next((row for row in catalog if row[0] in {"table", "view"} and _fold(row[1]) == _fold(name)), None)


def _load_base(session, schema, name, kind):
    binding = _binding(session, schema)
    catalog = _catalog(session, schema)
    entry = next((row for row in catalog if row[0] == kind and _fold(row[1]) == _fold(name)), None)
    if entry is None:
        raise SchemaError(f"The selected {kind} does not exist")
    definition = (IndexObjectDefinition(entry[1], table=entry[2], raw_sql=entry[3] or "", source_name=entry[1]) if kind == "index"
                  else TriggerObjectDefinition(entry[1], table=entry[2], raw_sql=entry[3] or "", source_name=entry[1]))
    snapshot = ObjectSnapshot(schema, entry[1], kind, definition, _terminate_sql(entry[3]) if entry[3] else "",
                              _fingerprint(binding, catalog), catalog=catalog, binding=binding)
    if session.config.read_only:
        snapshot.read_only = True
        snapshot.issues.append("This connection is read-only")
    return snapshot, entry


def _header(sql, kind):
    tokens = _tokens(sql)
    if not tokens or tokens[0].upper != "CREATE":
        raise SchemaError("Expected a CREATE object declaration")
    cursor = 1
    temporary = False
    if cursor < len(tokens) and tokens[cursor].upper in {"TEMP", "TEMPORARY"}:
        temporary = True
        cursor += 1
    if kind == "INDEX" and cursor < len(tokens) and tokens[cursor].upper == "UNIQUE":
        cursor += 1
    if cursor >= len(tokens) or tokens[cursor].upper != kind:
        raise SchemaError("Unrecognized CREATE object declaration")
    cursor += 1
    if cursor < len(tokens) and tokens[cursor].upper == "IF":
        if [token.upper for token in tokens[cursor:cursor + 3]] != ["IF", "NOT", "EXISTS"]:
            raise SchemaError("Unrecognized IF NOT EXISTS clause")
        cursor += 3
    _, _, cursor = _qualified_identifier(tokens, cursor)
    return cursor, temporary


def _qualified_identifier(tokens, cursor):
    name = _identifier(tokens[cursor])
    schema = ""
    cursor += 1
    if cursor < len(tokens) and tokens[cursor].text == ".":
        schema, name = name, _identifier(tokens[cursor + 1])
        cursor += 2
    return name, schema, cursor


def _read_trigger(sql, name, table):
    tokens = _tokens(sql)
    cursor, _ = _header(sql, "TRIGGER")
    if tokens[cursor].upper in {"BEFORE", "AFTER"}:
        cursor += 1
    elif tokens[cursor].upper == "INSTEAD":
        if tokens[cursor + 1].upper != "OF":
            raise SchemaError("Unrecognized INSTEAD OF timing")
        cursor += 2
    event = tokens[cursor].upper
    if event not in {"INSERT", "UPDATE", "DELETE"}:
        raise SchemaError("Unrecognized trigger event")
    cursor += 1
    if cursor < len(tokens) and tokens[cursor].upper == "OF":
        if event != "UPDATE":
            raise SchemaError("UPDATE OF requires an UPDATE trigger")
        cursor += 1
        while cursor < len(tokens) and tokens[cursor].upper != "ON":
            _identifier(tokens[cursor])
            cursor += 1
            if tokens[cursor].text == ",":
                cursor += 1
            elif tokens[cursor].upper != "ON":
                raise SchemaError("Unrecognized UPDATE OF column list")
    if tokens[cursor].upper != "ON":
        raise SchemaError("Missing trigger owner")
    owner, owner_schema, cursor = _qualified_identifier(tokens, cursor + 1)
    if _fold(owner) != _fold(table):
        raise SchemaError("The trigger declaration differs from its catalog owner")
    if tokens[cursor].upper == "FOR":
        if [token.upper for token in tokens[cursor:cursor + 3]] != ["FOR", "EACH", "ROW"]:
            raise SchemaError("Unrecognized trigger FOR EACH ROW clause")
        cursor += 3
    if tokens[cursor].upper == "WHEN":
        cursor += 1
        while cursor < len(tokens) and tokens[cursor].upper != "BEGIN":
            cursor += 1
    if cursor >= len(tokens) or tokens[cursor].upper != "BEGIN":
        raise SchemaError("Unrecognized trigger header option")
    parsed = _parse_trigger(sql, name)
    _single_trigger_body(parsed.body)
    return parsed, owner_schema


def _single_trigger_body(body):
    tokens = _tokens(body)
    if not tokens or tokens[0].upper != "BEGIN" or tokens[-1].upper != "END":
        raise SchemaError("Trigger definition must be one BEGIN ... END body")
    candidate = "CREATE TRIGGER __opennavicat_probe AFTER INSERT ON __opennavicat_probe " + body
    complete = _terminate_sql(candidate)
    for token in _tokens(complete):
        if token.text == ";" and sqlite3.complete_statement(complete[:token.end]):
            if _tokens(complete[token.end:]):
                raise SchemaError("A trigger body must not contain an additional top-level SQL statement")
            return
    raise SchemaError("Incomplete trigger body")


def _verify_snapshot(session, snapshot):
    _loader(snapshot.kind)
    binding = _binding(session, snapshot.schema)
    catalog = _catalog(session, snapshot.schema)
    if snapshot.binding != binding or snapshot.catalog != catalog or snapshot.fingerprint != _fingerprint(binding, catalog):
        raise SchemaConflictError("The object schema or connection changed; reload before saving")
    if snapshot.exists:
        authoritative = _loader(snapshot.kind)(session, snapshot.schema, snapshot.name)
        if snapshot != authoritative:
            raise SchemaConflictError("The original object snapshot was modified; reload before saving")


def _normalise_index(definition, original):
    from_terms = [term.column or term.expression for term in definition.terms]
    if definition.terms and definition.columns != from_terms:
        if definition.terms == original.terms and definition.columns != original.columns:
            definition.terms = [IndexTerm(column) for column in definition.columns]
        elif definition.columns == original.columns and definition.terms != original.terms:
            definition.columns = from_terms
        else:
            raise SchemaError("Index terms and column names disagree")
    elif not definition.terms:
        definition.terms = [IndexTerm(column) for column in definition.columns]


def _owner_columns(session, schema, name, owner_type, catalog):
    _q(name)
    owner = _owner_entry(catalog, name)
    if owner is None or owner[0] != owner_type:
        raise SchemaError("The selected table/view does not exist or its type changed")
    if owner_type == "table" and owner[3] and any(t.upper == "VIRTUAL" for t in _tokens(owner[3])[:4]):
        raise SchemaError("SQLite virtual tables require module-specific index/trigger tools")
    try:
        columns = session._query(f"PRAGMA {_q(schema)}.table_xinfo({_q(owner[1])})")
    except sqlite3.Error as exc:
        raise SchemaError("The selected owner's columns cannot be resolved: " + str(exc)) from exc
    if not columns:
        raise SchemaError("The selected table/view has no resolvable columns")
    return columns


def _validate_index(session, schema, definition, catalog):
    columns = _owner_columns(session, schema, definition.table, "table", catalog)
    names = {_fold(row[1]) for row in columns if row[6] != 1}
    if definition.where or any(term.expression for term in definition.terms):
        raise SchemaError("New or changed expression/partial indexes are not supported by this structured designer")
    if not definition.terms:
        raise SchemaError("Choose at least one index column")
    for term in definition.terms:
        if not term.column or _fold(term.column) not in names:
            raise SchemaError(f"The index column does not exist: {term.column}")
        _q(term.column)
        if term.collation:
            _q(term.collation)
        if not isinstance(term.descending, bool):
            raise SchemaError("Index sort order must be ASC or DESC")


def _validate_trigger(session, schema, definition, catalog):
    if schema == "temp":
        raise SchemaError("Temporary triggers require a separate cross-schema owner designer")
    if definition.owner_type not in {"table", "view"}:
        raise SchemaError("Trigger owner type must be table or view")
    columns = _owner_columns(session, schema, definition.table, definition.owner_type, catalog)
    timing = definition.timing.upper()
    if (definition.owner_type == "view" and timing != "INSTEAD OF") or (definition.owner_type == "table" and timing not in {"BEFORE", "AFTER"}):
        raise SchemaError("Views require INSTEAD OF triggers; tables require BEFORE or AFTER")
    if definition.event.upper() not in {"INSERT", "UPDATE", "DELETE"}:
        raise SchemaError("Choose one INSERT, UPDATE or DELETE event")
    if definition.update_columns and definition.event.upper() != "UPDATE":
        raise SchemaError("UPDATE OF columns apply only to an UPDATE trigger")
    writable = {_fold(row[1]) for row in columns if not row[6]}
    if any(_fold(column) not in writable for column in definition.update_columns):
        raise SchemaError("UPDATE OF columns must be existing writable columns of the selected table/view")
    if len({_fold(column) for column in definition.update_columns}) != len(definition.update_columns):
        raise SchemaError("UPDATE OF columns must not be repeated")
    for column in definition.update_columns:
        _q(column)
    if definition.when:
        _expression(definition.when, "Trigger WHEN condition")
    _single_trigger_body(definition.body)


def _render_trigger_object(schema, definition):
    event = definition.event.upper()
    if definition.update_columns:
        event += " OF " + ", ".join(map(_q, definition.update_columns))
    sql = "CREATE TRIGGER " + _qualified(schema, definition.name) + " " + definition.timing.upper() + " " + event
    sql += " ON " + _q(definition.table) + " FOR EACH ROW"
    if definition.when:
        sql += " WHEN (" + definition.when + "\n)"
    return sql + "\n" + definition.body


def _seal(plan):
    values = [asdict(plan.snapshot), asdict(plan.definition), plan.statements,
              plan.ddl, plan.changed, plan.warnings, plan.steps]
    return hmac.new(_PLAN_KEY, json.dumps(values, ensure_ascii=False, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def _plan(session, snapshot, definition, kind):
    expected = IndexObjectDefinition if kind == "index" else TriggerObjectDefinition
    if snapshot.kind != kind or type(definition) is not expected or type(snapshot.definition) is not expected:
        raise SchemaError("The definition does not match this index/trigger snapshot")
    binding = _binding(session, snapshot.schema)
    catalog = _catalog(session, snapshot.schema)
    if snapshot.exists or snapshot.binding:
        _verify_snapshot(session, snapshot)
    snapshot, definition = copy.deepcopy(snapshot), copy.deepcopy(definition)
    if not snapshot.exists:
        snapshot.binding, snapshot.catalog, snapshot.fingerprint = binding, catalog, _fingerprint(binding, catalog)
    original = snapshot.definition
    if definition.raw_sql != original.raw_sql or definition.source_name != original.source_name:
        raise SchemaError("The original object identity or SQL declaration was modified; reload the object")
    if not snapshot.exists and (definition.raw_sql or definition.source_name):
        raise SchemaError("New objects must use structured properties rather than a raw SQL declaration")
    if kind == "index":
        _normalise_index(definition, original)
    changed = not snapshot.exists or definition != original
    if changed and (snapshot.read_only or snapshot.issues):
        raise SchemaError("This object is protected against editing: " + "; ".join(snapshot.issues))
    steps, warnings, ddl = [], [], snapshot.ddl
    if changed:
        session._assert_write()
        if session.in_transaction:
            raise SchemaError("Finish the current transaction before saving an index or trigger")
        if session._query("SELECT name FROM temp.sqlite_schema WHERE type='trigger'"):
            raise SchemaError("Temporary triggers exist on this connection; close them before saving to protect their cross-schema dependencies")
        _q(definition.name)
        if definition.name.lower().startswith("sqlite_"):
            raise SchemaError("SQLite reserves object names beginning with sqlite_")
        namespace = {"index", "table", "view"} if kind == "index" else {"trigger"}
        if any(row[0] in namespace and _fold(row[1]) == _fold(definition.name) and
               not (snapshot.exists and row[0] == kind and row[1] == snapshot.name) for row in catalog):
            raise SchemaError("Another object already uses this name")
        if kind == "index":
            _validate_index(session, snapshot.schema, definition, catalog)
            sql = _render_index(snapshot.schema, definition.table, definition)
        else:
            _validate_trigger(session, snapshot.schema, definition, catalog)
            sql = _render_trigger_object(snapshot.schema, definition)
        if snapshot.exists:
            steps.append(("DROP " + kind.upper() + " " + _qualified(snapshot.schema, snapshot.name), ()))
            warnings.append("The existing object will be replaced within one transaction; validation failure retains its original definition")
        steps.append((sql, ()))
        ddl = _terminate_sql(sql)
    statements = ["BEGIN IMMEDIATE"] + [sql for sql, _ in steps] + ["COMMIT"] if changed else []
    plan = ObjectPlan(snapshot, definition, statements, ddl, changed, warnings, steps)
    plan.fingerprint = _seal(plan)
    return plan


def _first_row(session, sql):
    with session._cursor() as cursor:
        session._run(cursor, sql)
        return cursor.fetchone() if cursor.description else None


def _compile_dependencies(session, schema):
    catalog = _catalog(session, schema)
    owners = {}
    for kind, name, table, sql in catalog:
        if session._cancel_requested.is_set():
            raise SchemaError("Object save cancelled")
        if kind == "view":
            _first_row(session, "SELECT * FROM " + _qualified(schema, name) + " LIMIT 0")
        elif kind == "trigger":
            parsed, owner_schema = _read_trigger(sql, name, table)
            if owner_schema and _fold(owner_schema) != _fold(schema):
                raise SchemaError("A temporary cross-schema trigger cannot be validated by this designer")
            owners.setdefault(table, []).append(parsed)
        elif kind == "table" and not name.lower().startswith("sqlite_"):
            # foreign_key_check does not report a missing parent for empty child
            # tables, so validate the declared target as well as existing rows.
            for fk in session._query(f"PRAGMA {_q(schema)}.foreign_key_list({_q(name)})"):
                target = _owner_entry(catalog, fk[2])
                if target is None or target[0] != "table":
                    raise SchemaError("A foreign key refers to a missing parent table")
    if _first_row(session, f"PRAGMA {_q(schema)}.foreign_key_check") is not None:
        raise SchemaError("Foreign-key validation failed; the original object was retained")
    for owner, triggers in owners.items():
        owner_entry = _owner_entry(catalog, owner)
        if owner_entry is None:
            raise SchemaError("A trigger owner no longer exists")
        columns = _owner_columns(session, schema, owner, owner_entry[0], catalog)
        writable = [row[1] for row in columns if not row[6]]
        names = {_fold(column) for column in writable}
        if any(_fold(column) not in names for trigger in triggers for column in trigger.update_columns):
            raise SchemaError("A trigger's UPDATE OF column does not exist or is not writable")
        target = _qualified(schema, owner)
        events = {trigger.event for trigger in triggers}
        statements = []
        if "INSERT" in events:
            statements.append(f"EXPLAIN INSERT INTO {target} DEFAULT VALUES")
        if "DELETE" in events:
            statements.append(f"EXPLAIN DELETE FROM {target}")
        if "UPDATE" in events and writable:
            statements.append("EXPLAIN UPDATE " + target + " SET " + ", ".join(_q(column) + "=" + _q(column) for column in writable))
        for sql in statements:
            _first_row(session, sql)
    if schema != "temp":
        # TEMP views may explicitly reference this database and INDEXED BY the
        # replaced index; they are not present in the selected persistent catalog.
        for kind, name, _, _ in _catalog(session, "temp"):
            if kind == "view":
                _first_row(session, "SELECT * FROM " + _qualified("temp", name) + " LIMIT 0")
