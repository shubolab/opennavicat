"""Lossless SQLite table design, reviewed plans, and transactional application.

This module deliberately does not reconstruct an existing table from the lossy
browser metadata.  Original SQL clauses remain authoritative for properties the
designer has not changed.  Unsupported declarations fail closed on mutation.
The other adapters need their own planners (MySQL DDL is not transactional).

The macOS Navicat 18.0.5 reference was observed retaining an old backup table
for one simple rename. This implementation instead uses native dependency-aware
renames or a rollback-capable rebuild. Backup-table retention is an explicitly
open reference-parity difference, not evidence of equivalent save behavior.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .database import ConnectionConfig, DatabaseError, DatabaseSession


class SchemaError(DatabaseError):
    """A schema change was rejected or rolled back; no partial apply is allowed."""


class SchemaConflictError(SchemaError):
    """The database schema or connection changed after the preview was made."""


@dataclass
class IndexTerm:
    column: str = ""
    collation: str = ""
    descending: bool = False
    expression: str = ""


@dataclass
class ColumnDefinition:
    name: str
    type: str = "TEXT"
    nullable: bool = True
    default: str | None = None
    pk_order: int = 0
    collation: str = ""
    notnull_conflict: str = ""
    generated_type: str = ""
    generated_expression: str = ""
    autoincrement: bool = False
    source_name: str = ""
    raw_sql: str = ""


@dataclass
class IndexDefinition:
    name: str
    columns: list[str] = field(default_factory=list)
    unique: bool = False
    where: str = ""
    raw_sql: str = ""
    source_name: str = ""
    terms: list[IndexTerm] = field(default_factory=list)


@dataclass
class ForeignKeyDefinition:
    name: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str] = field(default_factory=list)
    on_delete: str = "NO ACTION"
    on_update: str = "NO ACTION"
    deferrable: bool = False
    initially_deferred: bool = False
    raw_sql: str = ""
    source_name: str = ""
    match: str = ""


@dataclass
class UniqueKeyDefinition:
    name: str
    columns: list[str] = field(default_factory=list)
    on_conflict: str = ""
    raw_sql: str = ""
    source_name: str = ""
    terms: list[IndexTerm] = field(default_factory=list)


@dataclass
class TriggerDefinition:
    name: str
    timing: str = "BEFORE"
    event: str = "INSERT"
    body: str = "BEGIN\nEND"
    when: str = ""
    update_columns: list[str] = field(default_factory=list)
    raw_sql: str = ""
    source_name: str = ""


@dataclass
class TableOptions:
    without_rowid: bool = False
    strict: bool = False
    pk_conflict: str = ""
    autoincrement_seed: int | None = None


@dataclass
class TableDefinition:
    name: str
    columns: list[ColumnDefinition] = field(default_factory=list)
    indexes: list[IndexDefinition] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDefinition] = field(default_factory=list)
    unique_keys: list[UniqueKeyDefinition] = field(default_factory=list)
    triggers: list[TriggerDefinition] = field(default_factory=list)
    options: TableOptions = field(default_factory=TableOptions)
    # CHECK clauses and declarations without editable controls are never erased.
    opaque_constraints: list[str] = field(default_factory=list, repr=False)
    primary_key_sql: str = field(default="", repr=False)
    primary_key_name: str = field(default="", repr=False)
    constraint_order: list[tuple[str, str]] = field(default_factory=list, repr=False)


@dataclass
class TableSnapshot:
    schema: str
    table: str
    definition: TableDefinition
    ddl: str
    fingerprint: str
    exists: bool = True
    issues: list[str] = field(default_factory=list)
    catalog: list[tuple] = field(default_factory=list, repr=False)
    binding: str = field(default="", repr=False)


@dataclass
class SchemaPlan:
    snapshot: TableSnapshot
    definition: TableDefinition
    statements: list[str]
    ddl: str
    changed: bool
    warnings: list[str] = field(default_factory=list)
    mode: str = "none"
    # The preview is rendered from these exact statements. Values remain bound.
    steps: list[tuple[str, tuple]] = field(default_factory=list, repr=False)
    fingerprint: str = field(default="", repr=False)
    foreign_keys_enabled: bool = True
    assertions: dict[int, str] = field(default_factory=dict, repr=False)

    @property
    def sql(self) -> str:
        return "\n\n".join(_terminate_sql(statement) for statement in self.statements)


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int
    kind: str = "word"

    @property
    def upper(self):
        return self.text.upper() if self.kind == "word" else ""


def _tokens(sql: str) -> list[_Token]:
    """SQLite lexer retaining source offsets; comments cannot become syntax."""
    result = []
    i = 0
    while i < len(sql):
        start = i
        c = sql[i]
        if c.isspace():
            i += 1
            continue
        if sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise SchemaError("Unterminated SQL comment")
            i = end + 2
            continue
        if c in "'\"`[":
            end_quote = "]" if c == "[" else c
            i += 1
            while i < len(sql):
                if sql[i] == end_quote:
                    i += 1
                    if end_quote != "]" and i < len(sql) and sql[i] == end_quote:
                        i += 1
                        continue
                    break
                i += 1
            else:
                raise SchemaError("Unterminated quoted SQL token")
            result.append(_Token(sql[start:i], start, i, "string" if c == "'" else "identifier"))
            continue
        if c.isalpha() or c == "_" or ord(c) >= 128:
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in "_$" or ord(sql[i]) >= 128):
                i += 1
            result.append(_Token(sql[start:i], start, i))
            continue
        if c.isdigit():
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in ".xX"):
                i += 1
            result.append(_Token(sql[start:i], start, i, "number"))
            continue
        i += 1
        result.append(_Token(c, start, i, "symbol"))
    return result


def _identifier(token: _Token) -> str:
    text = token.text
    if token.kind in {"identifier", "string"}:
        return text[1:-1].replace(text[0] * 2, text[0]) if text[0] != "[" else text[1:-1]
    if token.kind != "word":
        raise SchemaError("Expected an SQL identifier")
    return text


def _q(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise SchemaError("An identifier must be nonempty and contain no NUL bytes")
    return '"' + name.replace('"', '""') + '"'


def _qualified(schema: str, name: str) -> str:
    return _q(schema) + "." + _q(name)


def _matching(tokens: list[_Token], start: int) -> int:
    depth = 0
    for i in range(start, len(tokens)):
        if tokens[i].text == "(":
            depth += 1
        elif tokens[i].text == ")":
            depth -= 1
            if depth == 0:
                return i
    raise SchemaError("Unbalanced SQL parentheses")


def _split_sql(sql: str) -> list[str]:
    tokens = _tokens(sql)
    depth, start, result = 0, 0, []
    for token in tokens:
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        elif token.text == "," and depth == 0:
            result.append(sql[start:token.start].strip())
            start = token.end
        if depth < 0:
            raise SchemaError("Unbalanced SQL parentheses")
    if depth:
        raise SchemaError("Unbalanced SQL parentheses")
    result.append(sql[start:].strip())
    return result


def _terminate_sql(sql: str) -> str:
    tokens = _tokens(sql)
    if not tokens:
        return sql
    if tokens[-1].text == ";":
        return sql.rstrip() + ("\n" if "--" in sql[tokens[-1].end:] else "")
    suffix = sql[tokens[-1].end:]
    return sql.rstrip() + ("\n;" if "--" in suffix else ";")


def _expression(sql: str, label: str) -> str:
    if not sql.strip():
        raise SchemaError(f"{label} cannot be empty")
    tokens = _tokens(sql)
    if any(t.text == ";" for t in tokens):
        raise SchemaError(f"{label} must contain one SQL expression")
    # A line comment at the end would swallow appended grammar. Wrapping callers
    # place their closing delimiter on a separate line.
    _split_sql("(" + sql + "\n)")
    return sql.strip()


def _constraint_head(tokens: list[_Token], i: int) -> tuple[str, int, str]:
    name = ""
    head = i
    if tokens[i].upper == "CONSTRAINT":
        if i + 2 >= len(tokens):
            raise SchemaError("Incomplete named constraint")
        name, i = _identifier(tokens[i + 1]), i + 2
    word = tokens[i].upper
    next_word = tokens[i + 1].upper if i + 1 < len(tokens) else ""
    if word == "PRIMARY" and next_word == "KEY":
        return "primary", i, name
    if word == "NOT" and next_word == "NULL":
        return "notnull", i, name
    if word in {"UNIQUE", "CHECK", "DEFAULT", "COLLATE", "REFERENCES", "GENERATED"}:
        return word.lower(), i, name
    if word == "AS" and i + 1 < len(tokens) and tokens[i + 1].text == "(":
        return "generated", i, name
    if word == "NULL":
        return "null", i, name
    if head != i:
        return "unknown", i, name
    return "", i, name


def _column_parts(sql: str) -> tuple[str, str, list[tuple[str, str]]]:
    tokens = _tokens(sql)
    if not tokens:
        raise SchemaError("Empty column declaration")
    name = _identifier(tokens[0])
    starts = []
    depth = 0
    current_kind = ""
    previous_start = -1
    for i in range(1, len(tokens)):
        token = tokens[i]
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        if depth:
            continue
        kind, actual, _ = _constraint_head(tokens, i)
        if not kind:
            continue
        # CONSTRAINT name <head>, GENERATED ALWAYS AS, and DEFAULT NULL each
        # form a single clause rather than several apparent keyword clauses.
        if previous_start >= 0:
            _, prior_actual, _ = _constraint_head(tokens, previous_start)
            if i <= prior_actual:
                continue
            if current_kind == "generated" and kind == "generated":
                continue
            if current_kind == "default" and kind == "null" and i == prior_actual + 1:
                continue
            if current_kind == "notnull" and kind == "null" and i == prior_actual + 1:
                continue
            if current_kind == "references" and kind in {"null", "default"} and tokens[i - 1].upper == "SET":
                continue
        starts.append((i, kind))
        current_kind, previous_start = kind, i
    end_type = tokens[starts[0][0]].start if starts else len(sql)
    type_name = sql[tokens[0].end:end_type].strip()
    clauses = [(kind, sql[tokens[i].start:tokens[starts[n + 1][0]].start if n + 1 < len(starts) else len(sql)].strip())
               for n, (i, kind) in enumerate(starts)]
    return name, type_name, clauses


def _conflict(sql: str) -> str:
    tokens = _tokens(sql)
    for i, token in enumerate(tokens[:-2]):
        if token.upper == "ON" and tokens[i + 1].upper == "CONFLICT":
            return tokens[i + 2].upper
    return ""


def _term(sql: str) -> IndexTerm:
    tokens = _tokens(sql)
    if not tokens:
        raise SchemaError("Empty index term")
    end = len(tokens)
    descending = False
    if tokens[end - 1].upper in {"ASC", "DESC"}:
        descending = tokens[end - 1].upper == "DESC"
        end -= 1
    collation = ""
    if end >= 2 and tokens[end - 2].upper == "COLLATE":
        collation = _identifier(tokens[end - 1])
        end -= 2
    core = sql[tokens[0].start:tokens[end - 1].end]
    if end == 1 and tokens[0].kind in {"word", "identifier", "string"}:
        return IndexTerm(_identifier(tokens[0]), collation, descending)
    return IndexTerm(collation=collation, descending=descending, expression=core)


def _terms(sql: str) -> list[IndexTerm]:
    return [_term(part) for part in _split_sql(sql)]


def _paren_contents(sql: str, tokens: list[_Token], start: int = 0) -> tuple[str, int, int]:
    opening = next((i for i in range(start, len(tokens)) if tokens[i].text == "("), None)
    if opening is None:
        raise SchemaError("Expected a parenthesized column list")
    closing = _matching(tokens, opening)
    return sql[tokens[opening].end:tokens[closing].start], opening, closing


def _parse_fk(sql: str, source: str, column: str = "") -> ForeignKeyDefinition:
    tokens = _tokens(sql)
    _, head, name = _constraint_head(tokens, 0)
    if column:
        local = [column]
        references = head
    else:
        contents, _, close = _paren_contents(sql, tokens, head)
        local = [_identifier(_tokens(part)[0]) for part in _split_sql(contents)]
        references = close + 1
    if references >= len(tokens) or tokens[references].upper != "REFERENCES":
        raise SchemaError("Unsupported foreign-key declaration")
    target = _identifier(tokens[references + 1])
    cursor = references + 2
    target_columns = []
    if cursor < len(tokens) and tokens[cursor].text == "(":
        contents, _, close = _paren_contents(sql, tokens, cursor)
        target_columns = [_identifier(_tokens(part)[0]) for part in _split_sql(contents)]
        cursor = close + 1
    fk = ForeignKeyDefinition(name, local, target, target_columns, raw_sql=sql, source_name=source)
    for i in range(cursor, len(tokens)):
        word = tokens[i].upper
        if word == "ON" and i + 2 < len(tokens):
            event = tokens[i + 1].upper
            action = tokens[i + 2].upper
            if action in {"SET", "NO"} and i + 3 < len(tokens):
                action += " " + tokens[i + 3].upper
            if event == "DELETE":
                fk.on_delete = action
            elif event == "UPDATE":
                fk.on_update = action
        elif word == "MATCH" and i + 1 < len(tokens):
            fk.match = _identifier(tokens[i + 1])
        elif word == "DEFERRABLE":
            fk.deferrable = i == 0 or tokens[i - 1].upper != "NOT"
        elif word == "INITIALLY" and i + 1 < len(tokens):
            fk.initially_deferred = tokens[i + 1].upper == "DEFERRED"
    if column:
        prefix = f"CONSTRAINT {_q(name)} " if name else ""
        fk.raw_sql = prefix + "FOREIGN KEY (" + _q(column) + ") " + sql[tokens[head].start:]
    return fk


def _parse_trigger(sql: str, name: str) -> TriggerDefinition:
    tokens = _tokens(sql)
    begin = next((i for i, t in enumerate(tokens) if t.upper == "BEGIN"), None)
    if begin is None:
        raise SchemaError("Unsupported trigger body")
    on = next((i for i, t in enumerate(tokens[:begin]) if t.upper == "ON"), None)
    trigger = next((i for i, t in enumerate(tokens[:begin]) if t.upper == "TRIGGER"), None)
    if on is None or trigger is None:
        raise SchemaError("Unsupported trigger declaration")
    event_index = next((i for i in range(trigger + 2, on) if tokens[i].upper in {"INSERT", "UPDATE", "DELETE"}), None)
    if event_index is None:
        raise SchemaError("Unsupported trigger event")
    timing = "BEFORE"
    for token in tokens[trigger + 2:event_index]:
        if token.upper in {"BEFORE", "AFTER"}:
            timing = token.upper
        elif token.upper == "INSTEAD":
            timing = "INSTEAD OF"
    update_columns = []
    if event_index + 1 < on and tokens[event_index + 1].upper == "OF":
        update_columns = [_identifier(_tokens(part)[0]) for part in _split_sql(sql[tokens[event_index + 1].end:tokens[on].start])]
    when_index = next((i for i in range(on + 2, begin) if tokens[i].upper == "WHEN"), None)
    when = sql[tokens[when_index].end:tokens[begin].start].strip() if when_index is not None else ""
    body = sql[tokens[begin].start:].strip().rstrip(";").rstrip()
    return TriggerDefinition(name, timing, tokens[event_index].upper, body, when, update_columns, sql, name)


def _parse_index(sql: str, name: str) -> IndexDefinition:
    tokens = _tokens(sql)
    on = next((i for i, t in enumerate(tokens) if t.upper == "ON"), None)
    if on is None:
        raise SchemaError("Unsupported index declaration")
    contents, _, close = _paren_contents(sql, tokens, on + 2)
    terms = _terms(contents)
    where = ""
    if close + 1 < len(tokens) and tokens[close + 1].upper == "WHERE":
        where = sql[tokens[close + 1].end:].strip().rstrip(";").rstrip()
    return IndexDefinition(name, [t.column or t.expression for t in terms],
                           any(t.upper == "UNIQUE" for t in tokens[:on]), where, sql, name, terms)


def _binding(session: DatabaseSession, schema: str) -> str:
    if session.config.driver != "sqlite":
        raise SchemaError("This table-design planner currently supports SQLite only")
    databases = session._query("PRAGMA database_list")
    database = next((row[2] for row in databases if row[1] == schema), None)
    if database is None:
        raise SchemaError("The selected attached database no longer exists")
    # Keep the prior (possibly closed) object until the next binding read, so
    # Python allocator id reuse cannot make a reconnect appear identical. These
    # attributes belong to the session; there is no global connection registry.
    if getattr(session, "_schema_identity_connection", None) is not session.connection:
        session._schema_identity_connection = session.connection
        session._schema_identity_token = uuid.uuid4().hex
    return json.dumps([session.config.id, session.config.driver, schema, database,
                       session.config.database, session._schema_identity_token,
                       session._query(f"PRAGMA {_q(schema)}.schema_version")[0][0],
                       session._query("PRAGMA legacy_alter_table")[0][0],
                       session._query("SELECT type,name,tbl_name,sql FROM temp.sqlite_schema ORDER BY type,name")], ensure_ascii=False)


def _catalog(session: DatabaseSession, schema: str) -> list[tuple]:
    return session._query(f"SELECT type,name,tbl_name,sql FROM {_q(schema)}.sqlite_schema ORDER BY type,name")


def _fingerprint(binding: str, catalog: list[tuple]) -> str:
    return hashlib.sha256(json.dumps([binding, catalog], ensure_ascii=False, default=str).encode()).hexdigest()


def new_table(schema: str = "main", name: str = "") -> TableSnapshot:
    return TableSnapshot(schema, name, TableDefinition(name), "", "", exists=False)


def load_table(session: DatabaseSession, schema: str, table: str) -> TableSnapshot:
    binding = _binding(session, schema)
    catalog = _catalog(session, schema)
    entry = next((row for row in catalog if row[0] == "table" and row[1] == table), None)
    if not entry or not entry[3]:
        raise SchemaError("The selected table does not exist or has no editable declaration")
    sql = entry[3]
    definition = TableDefinition(table)
    snapshot = TableSnapshot(schema, table, definition, _terminate_sql(sql),
                             _fingerprint(binding, catalog), catalog=catalog, binding=binding)
    tokens = _tokens(sql)
    if any(t.upper == "VIRTUAL" for t in tokens[:4]):
        snapshot.issues.append("Virtual tables require their module-specific designer; ordinary table rebuild is disabled")
        definition.columns = [ColumnDefinition(c["name"], c["type"], c["nullable"], c["default"],
                              source_name=c["name"]) for c in session.columns(schema, table)]
        return snapshot
    try:
        body, _, close = _paren_contents(sql, tokens)
        tail = [t.upper for t in tokens[close + 1:] if t.text != ";" and t.text != ","]
        if any(word not in {"WITHOUT", "ROWID", "STRICT"} for word in tail):
            raise SchemaError("Unrecognized CREATE TABLE option; its original SQL is protected")
        definition.options.without_rowid = "WITHOUT" in tail
        definition.options.strict = "STRICT" in tail
        inline_pk = []
        for ordinal, part in enumerate(_split_sql(body)):
            part_tokens = _tokens(part)
            kind, head, constraint_name = _constraint_head(part_tokens, 0)
            # Column names which happen to be keywords must be quoted by SQLite
            # to avoid this same grammar ambiguity in CREATE TABLE.
            if kind in {"primary", "unique", "check"} or part_tokens[head].upper == "FOREIGN":
                source = f"@constraint:{ordinal}"
                if kind == "primary":
                    definition.primary_key_sql = part
                    definition.primary_key_name = constraint_name
                    definition.options.pk_conflict = _conflict(part)
                    contents, _, _ = _paren_contents(part, part_tokens, head)
                    inline_pk = [term.column for term in _terms(contents)]
                    definition.constraint_order.append(("primary", source))
                elif kind == "unique":
                    contents, _, _ = _paren_contents(part, part_tokens, head)
                    terms = _terms(contents)
                    definition.unique_keys.append(UniqueKeyDefinition(constraint_name,
                        [t.column or t.expression for t in terms], _conflict(part), part, source, terms))
                    definition.constraint_order.append(("unique", source))
                elif part_tokens[head].upper == "FOREIGN":
                    definition.foreign_keys.append(_parse_fk(part, source))
                    definition.constraint_order.append(("foreign", source))
                else:
                    definition.opaque_constraints.append(part)
                    definition.constraint_order.append(("opaque", str(len(definition.opaque_constraints) - 1)))
                continue
            name, type_name, clauses = _column_parts(part)
            column = ColumnDefinition(name, type_name, source_name=name, raw_sql=part)
            seen = set()
            for clause_kind, clause in clauses:
                if clause_kind in seen and clause_kind not in {"check", "references", "unique"}:
                    snapshot.issues.append(f"Column {name} has repeated {clause_kind} clauses; protected against editing")
                seen.add(clause_kind)
                clause_tokens = _tokens(clause)
                _, ch, cn = _constraint_head(clause_tokens, 0)
                if clause_kind == "notnull":
                    column.nullable = False
                    column.notnull_conflict = _conflict(clause)
                elif clause_kind == "default":
                    column.default = clause[clause_tokens[ch].end:].strip()
                elif clause_kind == "collate":
                    column.collation = _identifier(clause_tokens[ch + 1])
                elif clause_kind == "primary":
                    column.pk_order = 1
                    definition.primary_key_name = cn
                    definition.options.pk_conflict = _conflict(clause)
                    column.autoincrement = any(t.upper == "AUTOINCREMENT" for t in clause_tokens)
                elif clause_kind == "generated":
                    expression, _, _ = _paren_contents(clause, clause_tokens, ch)
                    column.generated_expression = expression
                    column.generated_type = "STORED" if any(t.upper == "STORED" for t in clause_tokens) else "VIRTUAL"
                elif clause_kind == "unique":
                    source = f"@inline-unique:{name}:{len(definition.unique_keys)}"
                    prefix = f"CONSTRAINT {_q(cn)} " if cn else ""
                    raw = prefix + "UNIQUE (" + _q(name) + ")"
                    if _conflict(clause):
                        raw += " ON CONFLICT " + _conflict(clause)
                    definition.unique_keys.append(UniqueKeyDefinition(cn, [name], _conflict(clause), raw, source, [IndexTerm(name)]))
                elif clause_kind == "references":
                    definition.foreign_keys.append(_parse_fk(clause, f"@inline-fk:{name}", name))
                elif clause_kind == "unknown":
                    snapshot.issues.append(f"Column {name} contains an unrecognized constraint; its original SQL is protected")
            definition.columns.append(column)
        if inline_pk:
            by_name = {c.name.casefold(): c for c in definition.columns}
            for i, name in enumerate(inline_pk, 1):
                if name.casefold() not in by_name:
                    raise SchemaError("Unrecognized primary-key expression")
                by_name[name.casefold()].pk_order = i
        for kind, name, owner, object_sql in catalog:
            if owner != table or not object_sql:
                continue
            if kind == "index":
                definition.indexes.append(_parse_index(object_sql, name))
            elif kind == "trigger":
                definition.triggers.append(_parse_trigger(object_sql, name))
        if any(c.autoincrement for c in definition.columns):
            if any(row[1] == "sqlite_sequence" for row in catalog):
                sequence = session._query(f"SELECT seq FROM {_q(schema)}.sqlite_sequence WHERE name=?", (table,))
                if sequence:
                    definition.options.autoincrement_seed = sequence[0][0]
    except (SchemaError, IndexError, StopIteration) as exc:
        snapshot.issues.append(str(exc))
    return snapshot


_CONFLICTS = {"", "ROLLBACK", "ABORT", "FAIL", "IGNORE", "REPLACE"}
_ACTIONS = {"NO ACTION", "RESTRICT", "SET NULL", "SET DEFAULT", "CASCADE"}


def _same_object(left, right) -> bool:
    """UI-visible semantics, independently of original declaration formatting."""
    if type(left) is not type(right):
        return False
    a, b = asdict(left), asdict(right)
    for values in (a, b):
        values.pop("raw_sql", None)
        values.pop("source_name", None)
    return a == b


def _normalise_terms(definition: TableDefinition, original: TableDefinition):
    for kind in ("indexes", "unique_keys"):
        before = {item.source_name: item for item in getattr(original, kind) if item.source_name}
        for item in getattr(definition, kind):
            old = before.get(item.source_name)
            from_terms = [term.column or term.expression for term in item.terms]
            if item.terms and item.columns != from_terms:
                if old and item.terms == old.terms and item.columns != old.columns:
                    item.terms = [IndexTerm(column) for column in item.columns]
                elif old and item.columns == old.columns and item.terms != old.terms:
                    item.columns = from_terms
                else:
                    raise SchemaError("Index terms and column names disagree")
            elif not item.terms:
                item.terms = [IndexTerm(column) for column in item.columns]


def _validate_sources(definition: TableDefinition, original: TableDefinition):
    for kind in ("columns", "indexes", "foreign_keys", "unique_keys", "triggers"):
        before = {item.source_name: item for item in getattr(original, kind) if item.source_name}
        seen = set()
        for item in getattr(definition, kind):
            if not item.source_name:
                if item.raw_sql:
                    raise SchemaError("New objects must use structured properties, not an untrusted original SQL declaration")
                continue
            old = before.get(item.source_name)
            if old is None or item.source_name in seen or item.raw_sql != old.raw_sql:
                raise SchemaError("The original schema identity or SQL was altered; reload the table")
            seen.add(item.source_name)
    if (definition.opaque_constraints != original.opaque_constraints or
            definition.primary_key_sql != original.primary_key_sql or
            definition.primary_key_name != original.primary_key_name or
            definition.constraint_order != original.constraint_order):
        raise SchemaError("Protected table declarations cannot be changed by this designer")


def _unique_names(items, label, *, allow_empty=False):
    seen = set()
    for item in items:
        name = item.name
        if allow_empty and not name:
            continue
        _q(name)
        if name.casefold() in seen:
            raise SchemaError(f"Duplicate {label} name: {name}")
        seen.add(name.casefold())


def _validate_definition(definition: TableDefinition, aliases=()):
    _q(definition.name)
    if definition.name.lower().startswith("sqlite_"):
        raise SchemaError("SQLite reserves object names beginning with sqlite_")
    if not definition.columns:
        raise SchemaError("A table must contain at least one column")
    for kind, label in (("columns", "column"), ("indexes", "index"), ("triggers", "trigger")):
        _unique_names(getattr(definition, kind), label)
    _unique_names(definition.foreign_keys + definition.unique_keys, "constraint", allow_empty=True)
    names = {c.name.casefold() for c in definition.columns} | {name.casefold() for name in aliases}
    primary = sorted([c for c in definition.columns if c.pk_order], key=lambda c: c.pk_order)
    if [c.pk_order for c in primary] != list(range(1, len(primary) + 1)):
        raise SchemaError("Primary-key order must contain consecutive values starting at 1")
    if definition.options.without_rowid and not primary:
        raise SchemaError("WITHOUT ROWID requires a primary key")
    if definition.options.pk_conflict.upper() not in _CONFLICTS:
        raise SchemaError("Unsupported primary-key conflict action")
    seed = definition.options.autoincrement_seed
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 9223372036854775807):
        raise SchemaError("The AUTOINCREMENT sequence must be an integer from 0 to 9223372036854775807")
    automatic = [c for c in definition.columns if c.autoincrement]
    if automatic and (len(automatic) != 1 or len(primary) != 1 or automatic[0] != primary[0] or
                      automatic[0].type.strip().upper() != "INTEGER" or definition.options.without_rowid):
        raise SchemaError("AUTOINCREMENT requires a single INTEGER PRIMARY KEY on a rowid table")
    if seed is not None and not automatic:
        raise SchemaError("The AUTOINCREMENT sequence requires an AUTOINCREMENT column")
    for column in definition.columns:
        type_tokens = _tokens(column.type)
        if any(t.text not in {"(", ")", ",", "+", "-"} and t.kind not in {"word", "identifier", "number"} for t in type_tokens):
            raise SchemaError(f"Unsupported column type declaration for {column.name}")
        if any(_constraint_head(type_tokens, i)[0] for i in range(len(type_tokens))):
            raise SchemaError("Constraints belong in their own properties, not the column type")
        _split_sql("(" + column.type + ")")
        if column.notnull_conflict.upper() not in _CONFLICTS:
            raise SchemaError("Unsupported NOT NULL conflict action")
        if column.default is not None:
            _expression(column.default, "Default value")
        if column.collation:
            _q(column.collation)
        if column.generated_type.upper() not in {"", "VIRTUAL", "STORED"}:
            raise SchemaError("Generated column type must be VIRTUAL or STORED")
        if column.generated_type:
            _expression(column.generated_expression, "Generated value")
            if column.default is not None or column.pk_order or column.autoincrement:
                raise SchemaError("A generated column cannot have a default, primary key or AUTOINCREMENT")
        elif column.generated_expression:
            raise SchemaError("Choose a generated-column type before entering its expression")
    if not any(not c.generated_type for c in definition.columns):
        raise SchemaError("A table needs at least one writable, non-generated column")
    for item in definition.indexes + definition.unique_keys:
        if not item.terms:
            raise SchemaError("An index or unique key needs at least one column")
        for term in item.terms:
            if bool(term.column) == bool(term.expression):
                raise SchemaError("An index term must specify a column or an expression")
            if term.column and term.column.casefold() not in names:
                raise SchemaError(f"Unknown index column: {term.column}")
            if term.expression:
                _expression(term.expression, "Index expression")
            if term.collation:
                _q(term.collation)
        if isinstance(item, UniqueKeyDefinition):
            if item.on_conflict.upper() not in _CONFLICTS:
                raise SchemaError("Unsupported unique-key conflict action")
            if any(term.expression for term in item.terms):
                raise SchemaError("SQLite table UNIQUE constraints cannot contain expressions; use a unique index")
        elif item.where:
            _expression(item.where, "Partial-index condition")
    for fk in definition.foreign_keys:
        if not fk.columns or any(c.casefold() not in names for c in fk.columns):
            raise SchemaError("Foreign-key source columns must exist")
        if len({c.casefold() for c in fk.columns}) != len(fk.columns):
            raise SchemaError("Foreign-key source columns must not be repeated")
        _q(fk.referenced_table)
        for col in fk.referenced_columns:
            _q(col)
        if fk.referenced_columns and len(fk.referenced_columns) != len(fk.columns):
            raise SchemaError("Foreign-key source and target column counts must match")
        if fk.on_delete.upper() not in _ACTIONS or fk.on_update.upper() not in _ACTIONS:
            raise SchemaError("Unsupported foreign-key action")
        if fk.match:
            _q(fk.match)
    for trigger in definition.triggers:
        if trigger.timing.upper() not in {"BEFORE", "AFTER"}:
            raise SchemaError("Ordinary table triggers must use BEFORE or AFTER")
        if trigger.event.upper() not in {"INSERT", "UPDATE", "DELETE"}:
            raise SchemaError("Unsupported trigger event")
        if trigger.update_columns and trigger.event.upper() != "UPDATE":
            raise SchemaError("UPDATE OF columns apply only to UPDATE triggers")
        if any(c.casefold() not in names for c in trigger.update_columns):
            raise SchemaError("Unknown UPDATE OF trigger column")
        if trigger.when:
            _expression(trigger.when, "Trigger WHEN condition")
        body_tokens = _tokens(trigger.body)
        if not body_tokens or body_tokens[0].upper != "BEGIN" or body_tokens[-1].upper != "END":
            raise SchemaError("Trigger definition must be a BEGIN ... END body")
        # sqlite3.execute enforces the single-statement boundary on apply, even
        # for trigger bodies containing several statements and quoted semicolons.
        if not sqlite3.complete_statement("CREATE TRIGGER probe AFTER INSERT ON probe " + trigger.body + ";"):
            raise SchemaError("Incomplete trigger body")


def _render_terms(terms: list[IndexTerm]) -> str:
    result = []
    for term in terms:
        sql = _q(term.column) if term.column else "(" + term.expression + "\n)"
        if term.collation:
            sql += " COLLATE " + _q(term.collation)
        if term.descending:
            sql += " DESC"
        result.append(sql)
    return ", ".join(result)


def _prefix_constraint(name: str) -> str:
    return "CONSTRAINT " + _q(name) + " " if name else ""


def _render_fk(fk: ForeignKeyDefinition) -> str:
    sql = _prefix_constraint(fk.name) + "FOREIGN KEY (" + ", ".join(map(_q, fk.columns)) + ") REFERENCES " + _q(fk.referenced_table)
    if fk.referenced_columns:
        sql += " (" + ", ".join(map(_q, fk.referenced_columns)) + ")"
    if fk.match:
        sql += " MATCH " + _q(fk.match)
    sql += " ON DELETE " + fk.on_delete.upper() + " ON UPDATE " + fk.on_update.upper()
    sql += " DEFERRABLE" if fk.deferrable else " NOT DEFERRABLE"
    sql += " INITIALLY DEFERRED" if fk.initially_deferred else " INITIALLY IMMEDIATE"
    return sql


def _render_unique(key: UniqueKeyDefinition) -> str:
    sql = _prefix_constraint(key.name) + "UNIQUE (" + _render_terms(key.terms) + ")"
    if key.on_conflict:
        sql += " ON CONFLICT " + key.on_conflict.upper()
    return sql


def _pk_signature(definition):
    return [(c.name, c.pk_order, c.autoincrement)
            for c in sorted(definition.columns, key=lambda col: col.pk_order) if c.pk_order]


def _render_column(column: ColumnDefinition, old: ColumnDefinition | None, *, preserve_pk: bool,
                   inline_primary: bool, pk_conflict: str, inline_constraints=None) -> str:
    clauses = _column_parts(old.raw_sql)[2] if old and old.raw_sql else []
    source = {kind: raw for kind, raw in clauses}
    replacements = {}
    if old and (column.nullable, column.notnull_conflict) == (old.nullable, old.notnull_conflict):
        replacements["notnull"] = source.get("notnull", "")
    elif not column.nullable:
        clause = "NOT NULL"
        if column.notnull_conflict:
            clause += " ON CONFLICT " + column.notnull_conflict.upper()
        if "notnull" in source:
            tokens = _tokens(source["notnull"])
            _, head, name = _constraint_head(tokens, 0)
            clause = _prefix_constraint(name) + clause
        replacements["notnull"] = clause
    else:
        replacements["notnull"] = ""
    if old and column.default == old.default:
        replacements["default"] = source.get("default", "")
    else:
        replacements["default"] = "DEFAULT (" + column.default + "\n)" if column.default is not None else ""
    if old and column.collation == old.collation:
        replacements["collate"] = source.get("collate", "")
    else:
        replacements["collate"] = "COLLATE " + _q(column.collation) if column.collation else ""
    if old and (column.generated_type, column.generated_expression) == (old.generated_type, old.generated_expression):
        replacements["generated"] = source.get("generated", "")
    else:
        replacements["generated"] = ("GENERATED ALWAYS AS (" + column.generated_expression + "\n) " + column.generated_type.upper()
                                      if column.generated_type else "")
    if preserve_pk:
        replacements["primary"] = source.get("primary", "")
    elif inline_primary:
        replacements["primary"] = "PRIMARY KEY"
        if "primary" in source:
            direction = next((token.upper for token in _tokens(source["primary"]) if token.upper in {"ASC", "DESC"}), "")
            if direction:
                replacements["primary"] += " " + direction
        if pk_conflict:
            replacements["primary"] += " ON CONFLICT " + pk_conflict.upper()
        if column.autoincrement:
            replacements["primary"] += " AUTOINCREMENT"
    else:
        replacements["primary"] = ""
    for kind, value in replacements.items():
        if value and kind in source:
            _, _, name = _constraint_head(_tokens(source[kind]), 0)
            if name and not value.startswith("CONSTRAINT "):
                replacements[kind] = _prefix_constraint(name) + value
    result = _q(column.name) + (" " + column.type if column.type else "")
    emitted = set()
    inline_count = {"references": 0, "unique": 0}
    for kind, raw in clauses:
        # These inline constraints are represented separately so deletions and
        # edits cannot accidentally leave the original constraint in force.
        if kind in {"references", "unique"}:
            replacement = (inline_constraints or {}).get((kind, inline_count[kind]), "")
            inline_count[kind] += 1
            if replacement:
                result += " " + replacement
            continue
        if kind in replacements:
            if kind not in emitted and replacements[kind]:
                result += " " + replacements[kind]
            emitted.add(kind)
        else:
            result += " " + raw
    for kind in ("primary", "notnull", "default", "collate", "generated"):
        if kind not in emitted and replacements.get(kind):
            result += " " + replacements[kind]
    return result


def _render_create(schema: str, name: str, definition: TableDefinition, original: TableDefinition) -> str:
    old_columns = {c.source_name: c for c in original.columns if c.source_name}
    preserve_pk = (_pk_signature(definition) == _pk_signature(original) and
                   definition.options.pk_conflict == original.options.pk_conflict)
    primary = sorted([c for c in definition.columns if c.pk_order], key=lambda c: c.pk_order)
    # An INTEGER PRIMARY KEY must stay inline when AUTOINCREMENT is requested;
    # preserving an existing inline PK also retains the SQLite DESC exception.
    inline_primary = len(primary) == 1 and (primary[0].autoincrement or
        (not definition.options.without_rowid and not original.primary_key_sql))
    inline_by_column = {}
    handled = set()
    for column in definition.columns:
        old = old_columns.get(column.source_name)
        if old is None:
            continue
        replacements = {}
        for kind, before_items, after_items in (
                ("unique", [x for x in original.unique_keys if x.source_name.startswith(f"@inline-unique:{old.name}:")], definition.unique_keys),
                ("references", [x for x in original.foreign_keys if x.source_name == f"@inline-fk:{old.name}"], definition.foreign_keys)):
            raw_clauses = [raw for clause_kind, raw in _column_parts(old.raw_sql)[2] if clause_kind == kind]
            current = {x.source_name: x for x in after_items if x.source_name}
            for i, previous in enumerate(before_items):
                edited = current.get(previous.source_name)
                if edited is None:
                    continue
                value = ""
                if _same_object(edited, previous):
                    value = raw_clauses[i]
                elif kind == "unique" and len(edited.terms) == 1 and edited.terms[0] == IndexTerm(column.name):
                    value = _prefix_constraint(edited.name) + "UNIQUE"
                    if edited.on_conflict:
                        value += " ON CONFLICT " + edited.on_conflict.upper()
                elif kind == "references" and edited.columns == [column.name]:
                    rendered = _render_fk(edited)
                    tokens = _tokens(rendered)
                    position = next(i for i, t in enumerate(tokens) if t.upper == "REFERENCES")
                    value = _prefix_constraint(edited.name) + rendered[tokens[position].start:]
                if value:
                    replacements[(kind, i)] = value
                    handled.add(("unique" if kind == "unique" else "foreign", edited.source_name))
        inline_by_column[column.source_name] = replacements
    pieces = [_render_column(c, old_columns.get(c.source_name), preserve_pk=preserve_pk,
                             inline_primary=bool(inline_primary and c.pk_order), pk_conflict=definition.options.pk_conflict,
                             inline_constraints=inline_by_column.get(c.source_name)) for c in definition.columns]
    remaining = {}
    for kind, items, render in (("foreign", definition.foreign_keys, _render_fk),
                                ("unique", definition.unique_keys, _render_unique)):
        old = {x.source_name: x for x in getattr(original, "foreign_keys" if kind == "foreign" else "unique_keys")}
        for i, item in enumerate(items):
            key = item.source_name or f"@new:{i}"
            if (kind, key) in handled:
                continue
            remaining[(kind, key)] = item.raw_sql if item.source_name in old and _same_object(item, old[item.source_name]) else render(item)
    pk_sql = ""
    if preserve_pk:
        pk_sql = original.primary_key_sql
    elif primary and not inline_primary:
        original_terms = {}
        if original.primary_key_sql:
            contents, _, _ = _paren_contents(original.primary_key_sql, _tokens(original.primary_key_sql))
            original_terms = {term.column: term for term in _terms(contents)}
        terms = [replace(original_terms[c.name], column=c.name) if c.name in original_terms else IndexTerm(c.name) for c in primary]
        pk_sql = _prefix_constraint(original.primary_key_name) + "PRIMARY KEY (" + _render_terms(terms) + ")"
        if definition.options.pk_conflict:
            pk_sql += " ON CONFLICT " + definition.options.pk_conflict.upper()
    opaque_done = set()
    for kind, source in original.constraint_order:
        if kind == "primary":
            if pk_sql:
                pieces.append(pk_sql)
                pk_sql = ""
        elif kind == "opaque":
            pieces.append(definition.opaque_constraints[int(source)])
            opaque_done.add(int(source))
        else:
            value = remaining.pop((kind, source), "")
            if value:
                pieces.append(value)
    if pk_sql:
        pieces.append(pk_sql)
    pieces.extend(remaining.values())
    pieces.extend(value for i, value in enumerate(definition.opaque_constraints) if i not in opaque_done)
    # Newlines terminate any original -- comment before the following comma.
    sql = "CREATE TABLE " + _qualified(schema, name) + " (\n  " + ",\n  ".join(piece + "\n" for piece in pieces) + ")"
    options = []
    if definition.options.without_rowid:
        options.append("WITHOUT ROWID")
    if definition.options.strict:
        options.append("STRICT")
    return sql + (" " + ", ".join(options) if options else "")


def _qualified_object_sql(sql: str, kind: str, schema: str, name: str) -> str:
    tokens = _tokens(sql)
    position = next((i for i, t in enumerate(tokens) if t.upper == kind), None)
    if position is None:
        raise SchemaError("Missing CREATE object header")
    position += 1
    if tokens[position].upper == "IF":
        position += 3
    end = position
    if position + 1 < len(tokens) and tokens[position + 1].text == ".":
        end += 2
    return sql[:tokens[position].start] + _qualified(schema, name) + sql[tokens[end].end:]


def _render_index(schema, table, item, old=None):
    if old and _same_object(item, old):
        return _qualified_object_sql(old.raw_sql, "INDEX", schema, item.name)
    sql = "CREATE " + ("UNIQUE " if item.unique else "") + "INDEX " + _qualified(schema, item.name)
    sql += " ON " + _q(table) + " (" + _render_terms(item.terms) + ")"
    if item.where:
        sql += " WHERE (" + item.where + "\n)"
    return sql


def _render_trigger(schema, table, item, old=None):
    if old and _same_object(item, old):
        return _qualified_object_sql(old.raw_sql, "TRIGGER", schema, item.name)
    event = item.event.upper()
    if event == "UPDATE" and item.update_columns:
        event += " OF " + ", ".join(map(_q, item.update_columns))
    sql = "CREATE TRIGGER " + _qualified(schema, item.name) + " " + item.timing.upper() + " " + event + " ON " + _q(table)
    sql += " FOR EACH ROW"
    if item.when:
        sql += " WHEN (" + item.when + "\n)"
    return sql + "\n" + item.body


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _preview_step(sql, parameters):
    # Bound values only occur in internally-generated sqlite_sequence statements.
    for value in parameters:
        sql = sql.replace("?", _literal(value), 1)
    return sql


def _seal(plan):
    values = [asdict(plan.snapshot), asdict(plan.definition), plan.statements,
              plan.ddl, plan.changed, plan.mode, plan.steps, plan.foreign_keys_enabled, plan.assertions]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, default=str).encode()).hexdigest()


def _rename_only(before, after):
    if len(before.columns) != len(after.columns):
        return False
    candidate = copy.deepcopy(before)
    candidate.name = after.name
    for original, edited in zip(candidate.columns, after.columns):
        if original.source_name != edited.source_name:
            return False
        original.name = edited.name
    return candidate == after


def _table_properties(definition):
    result = copy.deepcopy(definition)
    result.indexes = []
    result.triggers = []
    result.options.autoincrement_seed = None
    return result


def _sequence_steps(schema, table, sequence):
    target = _q(schema) + ".sqlite_sequence"
    return [(f"UPDATE {target} SET seq=? WHERE name=?", (sequence, table)),
            (f"INSERT INTO {target}(name,seq) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM {target} WHERE name=?)",
             (table, sequence, table))]


def _rowid_alias(definition):
    if definition.options.without_rowid:
        return ""
    primary = [c for c in definition.columns if c.pk_order]
    if len(primary) != 1 or primary[0].type.strip().upper() != "INTEGER":
        return ""
    column = primary[0]
    if column.raw_sql:
        for kind, raw in _column_parts(column.raw_sql)[2]:
            if kind == "primary" and any(t.upper == "DESC" for t in _tokens(raw)):
                return ""
    return column.name


def _copy_step(schema, old_name, new_name, before, after):
    before_columns = {c.name: c for c in before.columns}
    targets, sources = [], []
    for col in after.columns:
        if col.generated_type or not col.source_name:
            continue
        if col.source_name not in before_columns:
            raise SchemaError("A mapped source column no longer exists")
        targets.append(col.name)
        sources.append(col.source_name)
    if not before.options.without_rowid and not after.options.without_rowid:
        old_alias, new_alias = _rowid_alias(before), _rowid_alias(after)
        if not new_alias or new_alias not in targets:
            source_names = {c.name.casefold() for c in before.columns}
            target_names = {c.name.casefold() for c in after.columns}
            source_rowid = old_alias or next((x for x in ("rowid", "_rowid_", "oid") if x not in source_names), "")
            target_rowid = new_alias or next((x for x in ("rowid", "_rowid_", "oid") if x not in target_names), "")
            if not source_rowid or not target_rowid:
                raise SchemaError("All hidden rowid aliases are shadowed; this rebuild cannot guarantee rowid preservation")
            targets.insert(0, target_rowid)
            sources.insert(0, source_rowid)
    if not targets:
        raise SchemaError("No existing writable columns can be mapped; copy the data explicitly before this change")
    return ("INSERT INTO " + _qualified(schema, new_name) + " (" + ", ".join(map(_q, targets)) + ")\nSELECT " +
            ", ".join(map(_q, sources)) + " FROM " + _qualified(schema, old_name), ())


def _foreign_targets(session, snapshot, definition):
    for fk in definition.foreign_keys:
        if fk.referenced_table.casefold() == definition.name.casefold():
            columns = {c.name.casefold() for c in definition.columns}
            primary = [c for c in definition.columns if c.pk_order]
        else:
            target = session.columns(snapshot.schema, fk.referenced_table)
            if not target:
                raise SchemaError(f"Referenced table does not exist: {fk.referenced_table}")
            columns = {c["name"].casefold() for c in target}
            primary = [c for c in target if c["pk"]]
        if any(c.casefold() not in columns for c in fk.referenced_columns):
            raise SchemaError("A referenced foreign-key column does not exist")
        if not fk.referenced_columns and len(primary) != len(fk.columns):
            raise SchemaError("Implicit foreign-key columns must match the referenced primary key")


def _rename_ddl_preview(snapshot, definition, steps):
    """Let SQLite rewrite SQL in a schema-only, owned in-memory sandbox.

    No data or source connection state is copied. If a module/function/collation
    unavailable in the sandbox prevents preparation, the changes preview remains
    exact and the full-DDL view states that limitation instead of guessing SQL.
    """
    sandbox = DatabaseSession(ConnectionConfig("schema-preview", "Schema preview", database=":memory:"))
    try:
        sandbox.connect()
        schema = snapshot.schema
        if schema != "main":
            sandbox._query(f"ATTACH DATABASE ':memory:' AS {_q(schema)}")
        for kind in ("table", "index", "view", "trigger"):
            for entry_kind, name, _, sql in snapshot.catalog:
                if entry_kind == kind and sql and not name.lower().startswith("sqlite_"):
                    sandbox._query(_qualified_object_sql(sql, kind.upper(), schema, name))
        for sql, parameters in steps:
            sandbox._query(sql, parameters)
        declarations = [row[3] for row in _catalog(sandbox, schema)
                        if row[2] == definition.name and row[0] in {"table", "index", "trigger"} and row[3]]
        declarations.sort(key=lambda sql: 0 if _tokens(sql)[1].upper == "TABLE" else 1)
        return "\n\n".join(_terminate_sql(sql) for sql in declarations)
    except Exception:
        return ("-- Complete post-rename DDL cannot be prepared in the schema-only sandbox.\n"
                "-- The Changes tab shows the exact native ALTER statements; reload after saving to inspect the resulting DDL.\n"
                + snapshot.ddl)
    finally:
        sandbox.close()


def plan_table(session: DatabaseSession, snapshot: TableSnapshot, definition: TableDefinition) -> SchemaPlan:
    """Freeze a preview; this function reads metadata and never writes the DB."""
    binding = _binding(session, snapshot.schema)
    catalog = _catalog(session, snapshot.schema)
    current = _fingerprint(binding, catalog)
    if snapshot.exists and (snapshot.binding != binding or snapshot.fingerprint != current):
        raise SchemaConflictError("The table schema or connection changed; reload before designing it")
    snapshot = copy.deepcopy(snapshot)
    definition = copy.deepcopy(definition)
    if not snapshot.exists:
        snapshot.binding, snapshot.catalog, snapshot.fingerprint = binding, catalog, current
    _normalise_terms(definition, snapshot.definition)
    _validate_sources(definition, snapshot.definition)
    changed = not snapshot.exists or definition != snapshot.definition
    foreign_keys = bool(session._query("PRAGMA foreign_keys")[0][0])
    if not changed:
        plan = SchemaPlan(snapshot, definition, [], snapshot.ddl, False, foreign_keys_enabled=foreign_keys)
        plan.fingerprint = _seal(plan)
        return plan
    if snapshot.issues:
        raise SchemaError("This declaration cannot yet be edited without loss: " + "; ".join(snapshot.issues))
    native_rename = snapshot.exists and _rename_only(snapshot.definition, definition)
    _validate_definition(definition, [c.name for c in snapshot.definition.columns] if native_rename else ())
    if not native_rename:
        _foreign_targets(session, snapshot, definition)
    if session.config.read_only:
        raise SchemaError("This connection is read-only")
    if session.in_transaction:
        raise SchemaError("Finish the current transaction before saving table structure")
    schema, table, before = snapshot.schema, snapshot.table, snapshot.definition
    occupied = {row[1].casefold() for row in catalog}
    if (not snapshot.exists or table.casefold() != definition.name.casefold()) and definition.name.casefold() in occupied:
        raise SchemaError("An object with the requested table name already exists")
    for kind, objects in (("index", definition.indexes), ("trigger", definition.triggers)):
        old_names = {obj.source_name.casefold() for obj in getattr(before, "indexes" if kind == "index" else "triggers")}
        object_names = {row[1].casefold() for row in catalog if row[0] in ({"table", "index", "view"} if kind == "index" else {"trigger"})}
        for obj in objects:
            if obj.name.casefold() in object_names - old_names:
                raise SchemaError(f"An object with this {kind} name already exists: {obj.name}")
    steps = []
    assertions = {}
    warnings = []
    mode = "create" if not snapshot.exists else "auxiliary"
    if snapshot.exists and _rename_only(before, definition):
        mode = "rename"
        if session._query("PRAGMA legacy_alter_table")[0][0]:
            raise SchemaError("Disable legacy_alter_table before renaming so SQLite can preserve dependent foreign keys, views and triggers")
        active = table
        # SQLite rewrites dependent views, triggers, indexes and inbound FKs.
        if table != definition.name:
            steps.append((f"ALTER TABLE {_qualified(schema, active)} RENAME TO {_q(definition.name)}", ()))
            active = definition.name
        current_names = {c.name.casefold() for c in before.columns}
        for old, edited in zip(before.columns, definition.columns):
            if old.name != edited.name:
                if edited.name.casefold() in current_names - {old.name.casefold()}:
                    raise SchemaError("Column rename order would collide; save intermediate unique names first")
                steps.append((f"ALTER TABLE {_qualified(schema, active)} RENAME COLUMN {_q(old.name)} TO {_q(edited.name)}", ()))
                current_names.discard(old.name.casefold())
                current_names.add(edited.name.casefold())
        ddl = _rename_ddl_preview(snapshot, definition, steps)
    else:
        renamed_columns = [c for c in definition.columns if c.source_name and c.name != c.source_name]
        if snapshot.exists and (table != definition.name or renamed_columns):
            raise SchemaError("Save table/column renames separately before other structure edits so SQLite can safely rewrite dependencies")
        rebuild = snapshot.exists and _table_properties(before) != _table_properties(definition)
        mode = "rebuild" if rebuild else mode
        if rebuild and session._query("PRAGMA legacy_alter_table")[0][0]:
            raise SchemaError("Disable legacy_alter_table before rebuilding so SQLite can preserve dependent declarations")
        if rebuild and session._query("SELECT name FROM temp.sqlite_schema WHERE type IN ('trigger','view')"):
            raise SchemaError("Temporary views or triggers exist on this connection; close them before rebuilding a table to protect their dependencies")
        old_indexes = {x.source_name: x for x in before.indexes}
        old_triggers = {x.source_name: x for x in before.triggers}
        if not snapshot.exists or rebuild:
            digest = hashlib.sha256(json.dumps([current, asdict(definition)], ensure_ascii=False).encode()).hexdigest()[:16]
            temporary = "__opennavicat_" + digest if rebuild else definition.name
            if rebuild and temporary.casefold() in occupied:
                raise SchemaConflictError("The temporary rebuild name is occupied; reload before retrying")
            preserve_sequence = (rebuild and any(c.autoincrement for c in before.columns) and
                                 any(c.autoincrement for c in definition.columns) and
                                 definition.options.autoincrement_seed == before.options.autoincrement_seed)
            sequence_name = temporary + "_seq"
            if preserve_sequence:
                steps.extend([(f"CREATE TEMP TABLE {_q(sequence_name)} (seq INTEGER)", ()),
                    (f"INSERT INTO temp.{_q(sequence_name)} SELECT seq FROM {_q(schema)}.sqlite_sequence WHERE name=?", (table,))])
            steps.append((_render_create(schema, temporary, definition, before), ()))
            views = [row for row in catalog if row[0] == "view"] if rebuild else []
            view_names = {row[1] for row in views}
            view_triggers = [row for row in catalog if row[0] == "trigger" and row[2] in view_names and row[3]]
            if rebuild:
                removed = {c.name.casefold() for c in before.columns if not any(x.source_name == c.name for x in definition.columns)}
                if removed:
                    warnings.append("Columns will be removed: " + ", ".join(c.name for c in before.columns if c.name.casefold() in removed))
                    for kind, name, owner, sql in catalog:
                        if kind == "trigger" and owner != table and sql:
                            identifiers = {_identifier(t).casefold() for t in _tokens(sql) if t.kind in {"word", "identifier"}}
                            if table.casefold() in identifiers and identifiers & removed:
                                raise SchemaError(f"External trigger {name} may reference a removed column; edit that dependency first")
                steps.append(_copy_step(schema, table, temporary, before, definition))
                assertions[len(steps)] = "Table copy discarded rows under a constraint conflict policy; the original data was retained"
                steps.append((f"SELECT (SELECT COUNT(*) FROM {_qualified(schema, temporary)}) = (SELECT COUNT(*) FROM {_qualified(schema, table)})", ()))
                # Removing/recreating views avoids the transient missing-table
                # error in SQLite's ALTER schema validation. Their exact SQL and
                # INSTEAD OF triggers are restored inside this same transaction.
                steps.extend((f"DROP VIEW {_qualified(schema, row[1])}", ()) for row in views)
                steps.append((f"DROP TABLE {_qualified(schema, table)}", ()))
                steps.append((f"ALTER TABLE {_qualified(schema, temporary)} RENAME TO {_q(definition.name)}", ()))
            for item in definition.indexes:
                steps.append((_render_index(schema, definition.name, item, old_indexes.get(item.source_name)), ()))
            for item in definition.triggers:
                steps.append((_render_trigger(schema, definition.name, item, old_triggers.get(item.source_name)), ()))
            steps.extend((_qualified_object_sql(row[3], "VIEW", schema, row[1]), ()) for row in views)
            steps.extend((_qualified_object_sql(row[3], "TRIGGER", schema, row[1]), ()) for row in view_triggers)
            if preserve_sequence:
                sequence = f"{_q(schema)}.sqlite_sequence"
                saved = f"SELECT COALESCE(MAX(seq),0) FROM temp.{_q(sequence_name)}"
                steps.extend([(f"UPDATE {sequence} SET seq=MAX(seq,({saved})) WHERE name=?", (definition.name,)),
                    (f"INSERT INTO {sequence}(name,seq) SELECT ?,({saved}) WHERE NOT EXISTS (SELECT 1 FROM {sequence} WHERE name=?)", (definition.name, definition.name)),
                    (f"DROP TABLE temp.{_q(sequence_name)}", ())])
            elif definition.options.autoincrement_seed is not None:
                steps.extend(_sequence_steps(schema, definition.name, definition.options.autoincrement_seed))
            if rebuild:
                warnings.append("SQLite will rebuild the table under a write transaction; row data and dependent declarations are checked before commit")
        else:
            for kind, old_items, new_items, renderer in (("INDEX", old_indexes, definition.indexes, _render_index),
                                                         ("TRIGGER", old_triggers, definition.triggers, _render_trigger)):
                current_items = {x.source_name: x for x in new_items if x.source_name}
                for source, old in old_items.items():
                    edited = current_items.get(source)
                    if edited is None or not _same_object(old, edited):
                        steps.append((f"DROP {kind} {_qualified(schema, old.name)}", ()))
                for item in new_items:
                    old = old_items.get(item.source_name)
                    if old is None or not _same_object(old, item):
                        steps.append((renderer(schema, definition.name, item, old), ()))
            if definition.options.autoincrement_seed != before.options.autoincrement_seed:
                steps.extend(_sequence_steps(schema, definition.name, definition.options.autoincrement_seed))
        full_ddl = [_render_create(schema, definition.name, definition, before)]
        full_ddl.extend(_render_index(schema, definition.name, x, old_indexes.get(x.source_name)) for x in definition.indexes)
        full_ddl.extend(_render_trigger(schema, definition.name, x, old_triggers.get(x.source_name)) for x in definition.triggers)
        ddl = "\n\n".join(_terminate_sql(s) for s in full_ddl)
    work_statements = [_preview_step(sql, parameters) for sql, parameters in steps]
    statements = ["BEGIN IMMEDIATE"] + work_statements + [f"PRAGMA {_q(schema)}.foreign_key_check", "COMMIT"]
    if mode == "rebuild":
        statements = ["PRAGMA foreign_keys=OFF"] + statements + ["PRAGMA foreign_keys=" + ("ON" if foreign_keys else "OFF")]
    plan = SchemaPlan(snapshot, definition, statements, ddl, True, warnings, mode, steps,
                      foreign_keys_enabled=foreign_keys, assertions=assertions)
    plan.fingerprint = _seal(plan)
    return plan


def apply_plan(session: DatabaseSession, plan: SchemaPlan) -> TableSnapshot:
    """Apply the reviewed plan, rolling back all schema and data on any error.

    There must be no outer transaction because PRAGMA foreign_keys can only be
    changed outside one.  The GUI uses the session's existing serial worker.
    """
    if plan.fingerprint != _seal(plan):
        raise SchemaConflictError("The reviewed plan was modified; generate a fresh SQL preview")
    if session.config.driver != "sqlite":
        raise SchemaError("This table-design planner currently supports SQLite only")
    session._assert_write()
    session.connect()
    if session.in_transaction:
        raise SchemaError("Finish the current transaction before saving table structure")
    snapshot = plan.snapshot
    binding = _binding(session, snapshot.schema)
    if binding != snapshot.binding or _fingerprint(binding, _catalog(session, snapshot.schema)) != snapshot.fingerprint:
        raise SchemaConflictError("The database schema or connection changed after SQL preview; reload the table")
    if not plan.changed:
        return load_table(session, snapshot.schema, snapshot.table)
    initial_foreign_keys = bool(session._query("PRAGMA foreign_keys")[0][0])
    if initial_foreign_keys != plan.foreign_keys_enabled:
        raise SchemaConflictError("Foreign-key enforcement changed after SQL preview; generate a new preview")
    session._cancel_requested.clear()
    session._active = True
    transaction = False
    try:
        if plan.mode == "rebuild":
            session._query("PRAGMA foreign_keys=OFF")
            if session._query("PRAGMA foreign_keys")[0][0]:
                raise SchemaError("Could not suspend foreign-key enforcement for transactional table rebuilding")
        session._query("BEGIN IMMEDIATE")
        transaction = True
        if _fingerprint(_binding(session, snapshot.schema), _catalog(session, snapshot.schema)) != snapshot.fingerprint:
            raise SchemaConflictError("The schema changed before the write lock was acquired; reload the table")
        for index, (sql, parameters) in enumerate(plan.steps):
            if session._cancel_requested.is_set():
                raise SchemaError("Table structure save cancelled")
            rows = session._query(sql, parameters)
            if index in plan.assertions and rows != [(1,)]:
                raise SchemaError(plan.assertions[index])
        violations = session._query(f"PRAGMA {_q(snapshot.schema)}.foreign_key_check")
        if violations:
            raise SchemaError("Foreign-key validation failed; all table changes were rolled back")
        # Preparing every view detects missing/deleted columns and preserves a
        # failure as a rollback rather than leaving a broken view in the catalog.
        for kind, name, _, _ in _catalog(session, snapshot.schema):
            if kind == "view":
                session._query(f"SELECT * FROM {_qualified(snapshot.schema, name)} LIMIT 0")
        # CREATE TRIGGER accepts unresolved columns in its body and silently
        # accepts unknown UPDATE OF names. Compile the actual event programs
        # without executing any writes, including triggers on dependent views.
        owners = {}
        for kind, name, owner, sql in _catalog(session, snapshot.schema):
            if kind == "trigger":
                owners.setdefault(owner, []).append(_parse_trigger(sql, name))
        for owner in sorted(owners):
            columns = session._query(f"PRAGMA {_q(snapshot.schema)}.table_xinfo({_q(owner)})")
            writable = [row[1] for row in columns if not row[6]]
            target = _qualified(snapshot.schema, owner)
            events = {trigger.event for trigger in owners[owner]}
            writable_names = {col.casefold() for col in writable}
            if any(col.casefold() not in writable_names for trigger in owners[owner] for col in trigger.update_columns):
                raise SchemaError("A trigger's UPDATE OF column no longer exists; all changes were rolled back")
            statements = []
            if "INSERT" in events:
                statements.append(f"EXPLAIN INSERT INTO {target} DEFAULT VALUES")
            if "DELETE" in events:
                statements.append(f"EXPLAIN DELETE FROM {target}")
            if writable and "UPDATE" in events:
                statements.append("EXPLAIN UPDATE " + target + " SET " + ", ".join(_q(col) + "=" + _q(col) for col in writable))
            for statement in statements:
                session._query(statement)
        if session._cancel_requested.is_set():
            raise SchemaError("Table structure save cancelled")
        # Read the result while rollback is still possible. COMMIT is the final
        # fallible action before returning this snapshot.
        result = load_table(session, snapshot.schema, plan.definition.name)
        session._query("COMMIT")
        transaction = False
        return result
    except BaseException as exc:
        session._cancel_requested.clear()
        if transaction or session.in_transaction:
            try:
                session._query("ROLLBACK")
            except Exception as rollback_error:
                raise SchemaError("Table save failed and rollback could not be confirmed; close and reopen the connection") from rollback_error
        if isinstance(exc, SchemaError):
            raise
        if isinstance(exc, Exception):
            raise SchemaError("Table structure was not saved; all changes were rolled back: " + str(exc)) from exc
        raise
    finally:
        session._cancel_requested.clear()
        session._active = False
        if plan.mode == "rebuild":
            session._query("PRAGMA foreign_keys=" + ("ON" if initial_foreign_keys else "OFF"))
