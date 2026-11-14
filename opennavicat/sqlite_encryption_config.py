"""SQLite encryption metadata and explicit process-only password provenance.

These helpers do not load an encryption engine, inspect a file, or access a
keyring. Display labels remain verbatim metadata; provider settings are resolved
only for an explicit connection attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import ConnectionConfig


SQLITE_CIPHER_NAMES = (
    "aes128cbc", "aes256cbc", "chacha20", "sqlcipher", "rc4", "ascon128",
    "aes128cbc legacy", "aes256cbc legacy", "chacha20 legacy",
    "sqlcipher legacy v1", "sqlcipher legacy v2", "sqlcipher legacy v3", "sqlcipher legacy v4",
    "rc4 legacy", "ascon128 legacy",
)
SQLITE_CIPHER_PAGE_SIZES = (0, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
SQLiteEncryptionPasswordBinding = tuple[str, str, bool, str, int]


@dataclass(frozen=True)
class SQLiteCipherSettings:
    cipher: str
    legacy: int | None
    page_size: int
    inferred_default: bool = False


def decode_sqlite_encryption_fields(enabled, cipher_name, page_size) -> tuple[bool, str, int]:
    """Validate metadata without coercing or silently discarding a setting.

    The page-size bounds are SQLite/provider structural bounds, not evidence
    that the reference accepts every nonzero value. Only zero was observed.
    """
    if type(enabled) is not bool or type(cipher_name) is not str or cipher_name not in ("", *SQLITE_CIPHER_NAMES):
        raise ValueError("Invalid SQLite encryption metadata")
    if (type(page_size) is not int or page_size not in SQLITE_CIPHER_PAGE_SIZES
            or page_size and " legacy" not in cipher_name):
        raise ValueError("Invalid SQLite encryption page size")
    return enabled, cipher_name, page_size


def resolve_sqlite_cipher(cipher_name: str, page_size: int = 0) -> SQLiteCipherSettings:
    """Resolve a retained display label for the independently tested provider.

    Blank stays blank in metadata. Selecting AES128 at execution is a bounded
    inference from the reference blank-label/AES128 successful read and negative
    AES256/ChaCha20 controls, not a claim about its internal implementation.
    """
    decode_sqlite_encryption_fields(True, cipher_name, page_size)
    if cipher_name == "ascon128 legacy":
        raise ValueError("The selected SQLite cipher mode is not supported by the tested provider")
    if not cipher_name:
        return SQLiteCipherSettings("aes128cbc", None, page_size, inferred_default=True)
    if " legacy v" in cipher_name:
        return SQLiteCipherSettings("sqlcipher", int(cipher_name[-1]), page_size)
    if cipher_name.endswith(" legacy"):
        return SQLiteCipherSettings(cipher_name.removesuffix(" legacy"), 1, page_size)
    return SQLiteCipherSettings(cipher_name, None, page_size)


def _text(value, maximum):
    if type(value) is not str or len(value) > maximum:
        raise ValueError("Invalid SQLite encryption password binding")
    return value


def sqlite_encryption_password_binding(config: ConnectionConfig) -> SQLiteEncryptionPasswordBinding:
    """Exact owner/path/flag/label/page binding, without filesystem resolution."""
    enabled, label, page_size = decode_sqlite_encryption_fields(
        config.sqlite_encryption_enabled, config.sqlite_cipher_name, config.sqlite_cipher_page_size)
    return (_text(config.id, 4096), _text(config.database, 32768), enabled, label, page_size)


def _binding(value) -> SQLiteEncryptionPasswordBinding | None:
    if value is None:
        return None
    if type(value) not in (list, tuple) or len(value) != 5:
        raise ValueError("Invalid SQLite encryption password binding")
    enabled, label, page_size = decode_sqlite_encryption_fields(value[2], value[3], value[4])
    return (_text(value[0], 4096), _text(value[1], 32768), enabled, label, page_size)


def normalize_sqlite_encryption_password(config: ConnectionConfig, *, initial: bool = False) -> tuple[str, SQLiteEncryptionPasswordBinding | None]:
    """Keep matching provenance; only constructor input may initially bind.

    An explicit empty input can retain a matching binding as a clear marker.
    A missing password and None binding instead mean no new input was supplied.
    """
    password = config.sqlite_encryption_password
    if type(password) is not str or len(password) > 65536:
        raise ValueError("Invalid SQLite encryption password")
    expected = sqlite_encryption_password_binding(config)
    previous = _binding(config.sqlite_encryption_password_binding)
    if config.driver != "sqlite" or not config.sqlite_encryption_enabled:
        return "", None
    if previous is None:
        return (password, expected) if initial and password else ("", None)
    return (password, previous) if previous == expected else ("", None)


def bound_sqlite_encryption_password(config: ConnectionConfig) -> str:
    """Return only an already-bound password; copying never supplies new input."""
    return normalize_sqlite_encryption_password(config)[0]


def with_sqlite_encryption_password(config: ConnectionConfig, password: str) -> ConnectionConfig:
    """Return an independent draft with explicitly new (possibly empty) input."""
    from .sqlite_database_config import copy_connection_config

    if type(password) is not str or len(password) > 65536:
        raise ValueError("Invalid SQLite encryption password")
    snapshot = copy_connection_config(config)
    if snapshot.driver == "sqlite" and snapshot.sqlite_encryption_enabled:
        snapshot.sqlite_encryption_password = password
        snapshot.sqlite_encryption_password_binding = sqlite_encryption_password_binding(snapshot)
    return snapshot
