"""Bounded primary-key data comparison and atomic, conflict-checked application."""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .database import DatabaseSession


@dataclass
class DiffPlan:
    source: str
    target: str
    target_id: str
    schema: str
    table: str
    primary_keys: list[str]
    target_fingerprint: str
    target_columns: list[dict]
    inserts: list[dict] = field(default_factory=list)
    updates: list[tuple[dict, dict]] = field(default_factory=list)
    deletes: list[dict] = field(default_factory=list)
    unchanged: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    applied: bool = False


def fingerprint(session):
    config = session.config
    # The same database address can resolve to different servers on different
    # jump hosts. A preview must remain bound to its original SSH endpoint and
    # approved host key, rather than only the address inside that network.
    tunnel = None
    if getattr(config, "ssh_enabled", False):
        tunnel = [config.ssh_host, config.ssh_port, config.ssh_username,
                  config.ssh_host_key]
    return json.dumps([config.driver, config.host, config.port, config.database,
                       config.username, tunnel])


def require_transactional_table(session, schema, table):
    if session.config.driver in {"mysql", "mariadb"}:
        rows = session._query("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (schema, table))
        if not rows or (rows[0][0] or "").lower() != "innodb":
            raise ValueError("Atomic data comparison applies only to InnoDB target tables on MySQL/MariaDB.")


def logical_value(value, column):
    """Compare equivalent driver representations without coercing text columns.

    SQLite yields text dates and binary floats for NUMERIC columns; server drivers
    return date/Decimal objects. Keep high precision distinctions and timezone
    semantics while avoiding an update on every repeated cross-driver preview.
    """
    if value is None:
        return None
    datatype = column["type"].lower().strip()
    try:
        if re.match(r"^(tinyint|smallint|mediumint|int|integer|bigint|decimal|numeric|real|float|double)\b", datatype):
            number = Decimal(str(value))
            return number if number.is_finite() else value
        if datatype == "date":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if datatype.startswith(("timestamp", "datetime")):
            return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, InvalidOperation):
        pass
    return value


def compare_tables(source: DatabaseSession, target: DatabaseSession, source_schema: str,
                   source_table: str, target_schema: str, target_table: str,
                   max_rows: int = 10000) -> DiffPlan:
    if source.in_transaction or target.in_transaction:
        raise ValueError("Commit or roll back open transactions before comparing tables.")
    require_transactional_table(target, target_schema, target_table)
    source_meta = source.columns(source_schema, source_table)
    target_meta = target.columns(target_schema, target_table)
    source_names = {c["name"] for c in source_meta}
    target_names = {c["name"] for c in target_meta}
    if source_names != target_names or not source_names:
        raise ValueError("Data comparison requires tables with the same column names. Create or migrate the target schema first.")
    keys = [c["name"] for c in source_meta if c["pk"]]
    if not keys or set(keys) != {c["name"] for c in target_meta if c["pk"]}:
        raise ValueError("Both tables need the same primary-key columns.")
    if any(c.get("identity_generation") == "ALWAYS" and c["name"] in keys for c in target_meta):
        raise ValueError("Target has an ALWAYS identity primary key. Use explicit SQL for identity-aware transfer.")
    source_data = source.fetch_table(source_schema, source_table, limit=max_rows)
    target_data = target.fetch_table(target_schema, target_table, limit=max_rows)
    if source_data.truncated or target_data.truncated:
        raise ValueError(f"This preview supports up to {max_rows:,} rows per table. No partial comparison is produced.")
    writable = {c["name"] for c in target_meta if not c.get("generated") and c.get("identity_generation") != "ALWAYS"}
    source_types = {c["name"]: c for c in source_meta}
    target_types = {c["name"]: c for c in target_meta}
    def keyed(result, types):
        output = {}
        for row in result.rows:
            record = dict(zip(result.columns, row))
            key = tuple(bytes(record[k]) if isinstance(record[k], memoryview) else logical_value(record[k], types[k]) for k in keys)
            if any(value is None for value in key) or key in output:
                raise ValueError("Comparison requires unique, non-null primary-key values.")
            output[key] = record
        return output
    left, right = keyed(source_data, source_types), keyed(target_data, target_types)
    plan = DiffPlan(f"{source.config.name} / {source_schema}.{source_table}",
                    f"{target.config.name} / {target_schema}.{target_table}",
                    target.config.id, target_schema, target_table, keys, fingerprint(target), target_meta)
    for key, row in left.items():
        if key not in right:
            plan.inserts.append({name: value for name, value in row.items() if name in writable})
        else:
            original = right[key]
            changes = {name: row[name] for name in writable if not source._same_value(
                logical_value(row[name], source_types[name]), logical_value(original[name], target_types[name]))}
            if changes:
                plan.updates.append((original, changes))
            else:
                plan.unchanged += 1
    plan.deletes = [row for key, row in right.items() if key not in left]
    return plan


def apply_plan(target: DatabaseSession, plan: DiffPlan, include_deletes: bool = False) -> dict:
    if target.config.id != plan.target_id:
        raise ValueError("The preview belongs to a different target connection.")
    if fingerprint(target) != plan.target_fingerprint:
        raise ValueError("The target connection settings changed. Compare again before applying.")
    if plan.applied:
        raise ValueError("This preview has already been applied. Compare again to create a fresh preview.")
    if target.config.read_only or target.in_transaction:
        raise ValueError("Apply requires a writable target with no open transaction.")
    if target.columns(plan.schema, plan.table) != plan.target_columns:
        raise ValueError("The target table structure changed. Compare again before applying.")
    require_transactional_table(target, plan.schema, plan.table)
    target.execute("BEGIN")
    try:
        # Existing rows retain every original field for conflict detection.
        for original, changes in plan.updates:
            target.update_row(plan.schema, plan.table, original, changes)
        if include_deletes:
            for original in plan.deletes:
                target.delete_row(plan.schema, plan.table, original)
        for row in plan.inserts:
            target.insert_row(plan.schema, plan.table, row)
        target.execute("COMMIT")
    except Exception:
        with contextlib.suppress(Exception):
            target.execute("ROLLBACK")
        raise
    plan.applied = True
    return {"inserted": len(plan.inserts), "updated": len(plan.updates), "deleted": len(plan.deletes) if include_deletes else 0}
