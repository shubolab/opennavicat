"""SQLite database-page metadata and process-only credential boundaries.

These are structural bounds, not SQLite execution limits or reference input
validation. Aliases and filenames are preserved verbatim for a later worker.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import ConnectionConfig


MAX_SQLITE_DATABASES = 1024
CONNECTION_SECRET_FIELDS = ("password", "ssh_password", "ssh_key_passphrase", "ssl_key_password")
_ENTRY_FIELDS = frozenset({"id", "name", "database", "encrypted"})


def _text(value, maximum, *, identifier=False):
    if (type(value) is not str or len(value) > maximum
            or identifier and (not value or "\0" in value)):
        # Never interpolate malformed values or unexpected keys into errors.
        raise ValueError("Invalid SQLite database metadata")
    return value


@dataclass(frozen=True)
class SQLiteDatabaseEntry:
    id: str
    name: str
    database: str
    encrypted: bool = False

    def __post_init__(self):
        _text(self.id, 128, identifier=True)
        _text(self.name, 4096)
        _text(self.database, 32768)
        if type(self.encrypted) is not bool:
            raise ValueError("Invalid SQLite database encryption metadata")


def decode_sqlite_databases(value) -> tuple[SQLiteDatabaseEntry, ...]:
    """Decode only known metadata, retaining order and independent entries."""
    if type(value) not in (list, tuple) or len(value) > MAX_SQLITE_DATABASES:
        raise ValueError("Invalid SQLite database list")
    entries, identifiers = [], set()
    for row in value:
        if isinstance(row, SQLiteDatabaseEntry):
            row = {"id": row.id, "name": row.name, "database": row.database, "encrypted": row.encrypted}
        if (type(row) is not dict or set(row) - _ENTRY_FIELDS
                or not {"id", "name", "database"}.issubset(row)):
            raise ValueError("Invalid SQLite database metadata fields")
        entry = SQLiteDatabaseEntry(row["id"], row["name"], row["database"], row.get("encrypted", False))
        if entry.id in identifiers:
            raise ValueError("Repeated SQLite database identity")
        identifiers.add(entry.id)
        entries.append(entry)
    return tuple(entries)


def encode_sqlite_databases(value) -> list[dict]:
    """Never serialize arbitrary nested fields, including injected passwords."""
    return [{"id": row.id, "name": row.name, "database": row.database, "encrypted": row.encrypted}
            for row in decode_sqlite_databases(value)]


def copy_sqlite_database_passwords(value, entries) -> dict[str, str]:
    """Copy supplied memory-only secrets; removed row IDs are not retained."""
    if type(value) is not dict or len(value) > MAX_SQLITE_DATABASES:
        raise ValueError("Invalid SQLite database passwords")
    identifiers = {row.id for row in decode_sqlite_databases(entries)}
    result = {}
    for key, secret in value.items():
        _text(key, 128, identifier=True)
        if type(secret) is not str or len(secret) > 65536:
            raise ValueError("Invalid SQLite database password")
        if key in identifiers:
            result[key] = secret
    return result


def sqlite_database_binding(entry: SQLiteDatabaseEntry) -> tuple[str, bool]:
    """Exact supplied file/flag binding; no filesystem or alias interpretation."""
    row, = decode_sqlite_databases((entry,))
    return row.database, row.encrypted


def connection_metadata(config: ConnectionConfig) -> dict:
    """Encode a profile with all runtime credential carriers explicitly empty."""
    from .database import ConnectionConfig

    if not isinstance(config, ConnectionConfig):
        raise ValueError("Invalid connection metadata")
    value = {}
    for item in fields(ConnectionConfig):
        if item.name in CONNECTION_SECRET_FIELDS:
            value[item.name] = ""
        elif item.name == "sqlite_database_passwords":
            value[item.name] = {}
        elif item.name == "sqlite_databases":
            value[item.name] = encode_sqlite_databases(config.sqlite_databases)
        else:
            value[item.name] = deepcopy(getattr(config, item.name))
    return value


def connection_from_metadata(value) -> ConnectionConfig:
    """Persistent input never hydrates either scalar or attached credentials."""
    from .database import ConnectionConfig

    known = {item.name for item in fields(ConnectionConfig)}
    if type(value) is not dict or set(value) - known:
        raise ValueError("Invalid connection metadata fields")
    metadata = {key: deepcopy(item) for key, item in value.items()
                if key not in CONNECTION_SECRET_FIELDS and key != "sqlite_database_passwords"}
    for key in CONNECTION_SECRET_FIELDS:
        metadata[key] = ""
    try:
        return ConnectionConfig(**metadata)
    except TypeError:
        raise ValueError("Invalid connection metadata fields") from None


def copy_connection_config(config: ConnectionConfig) -> ConnectionConfig:
    """An independent runtime snapshot, including memory-only credentials."""
    from .database import ConnectionConfig

    return ConnectionConfig(**{item.name: deepcopy(getattr(config, item.name))
                               for item in fields(ConnectionConfig)})
