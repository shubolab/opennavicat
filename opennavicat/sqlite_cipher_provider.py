"""Optional, separately tested SQLite3MC adapter; not the standard backend.

Only the exact tested provider is accepted. No stdlib SQLite connection is
created here, including for backups. Ordinary unencrypted SQLite continues to
use the existing backend until a later explicit integration.
"""
from __future__ import annotations

from collections.abc import Mapping
import importlib
from importlib import metadata
import os
import sqlite3
import threading
from urllib.parse import parse_qsl, urlsplit
import weakref

PROVIDER_VERSION = "3.54.0.0"
MC_VERSION = "SQLite3 Multiple Ciphers 2.6.1"
_LEGACY = {"aes128cbc": {0, 1}, "aes256cbc": {0, 1}, "chacha20": {0, 1},
           "sqlcipher": {0, 1, 2, 3, 4}, "rc4": {1}, "ascon128": set()}
_MANAGED_CIPHER_PRAGMAS = frozenset((
    "key", "hexkey", "rekey", "hexrekey", "cipher", "legacy", "legacy_page_size",
    "kdf_iter", "fast_kdf_iter", "hmac_use", "hmac_pgno", "hmac_salt_mask",
    "kdf_algorithm", "hmac_algorithm", "hmac_algorithm_compat", "plaintext_header_size",
    "cipher_salt", "hmac_check", "encrypt_temp_db", "mc_legacy_wal", "mc_chacha20_hwaccel",
    "tcost", "mcost", "pcost", "algorithm",
))


class ProviderUnavailable(sqlite3.NotSupportedError):
    pass


class SQLPreflightLimit(sqlite3.DataError):
    pass


def _provider():
    unavailable = False
    try:
        version = metadata.version("apsw-sqlite3mc")
        module = importlib.import_module("apsw")
        importlib.import_module("apsw.ext")
    except (ImportError, OSError, metadata.PackageNotFoundError):
        unavailable = True
    if unavailable:
        raise ProviderUnavailable("The optional tested encrypted SQLite provider is unavailable") from None
    ordinary_present = True
    try:
        metadata.version("apsw")
    except metadata.PackageNotFoundError:
        ordinary_present = False
    if (ordinary_present or version != PROVIDER_VERSION or module.apswversion() != PROVIDER_VERSION
            or getattr(module, "mc_version", None) != MC_VERSION):
        raise ProviderUnavailable("Encrypted SQLite requires the tested provider without an overlapping APSW distribution")
    return module


def _configuration(cipher, legacy, page_size=0):
    if not isinstance(cipher, str) or cipher not in _LEGACY:
        raise sqlite3.NotSupportedError("This SQLite cipher is not supported by the tested provider")
    if legacy is not None and (type(legacy) is not int or legacy not in _LEGACY[cipher]):
        raise sqlite3.NotSupportedError("This SQLite cipher legacy configuration is not supported")
    if (type(page_size) is not int or page_size not in (0, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
            or page_size and legacy not in (1, 2, 3, 4)
            or cipher == "sqlcipher" and legacy in (2, 3, 4) and page_size == 512):
        raise sqlite3.NotSupportedError("This SQLite cipher page size is not supported")


def _bindings(values):
    def valid(value):
        return value is None or isinstance(value, (int, float, str, bytes, bytearray, memoryview))
    if isinstance(values, Mapping):
        if any(not isinstance(key, str) or not valid(value) for key, value in values.items()):
            raise sqlite3.ProgrammingError("Unsupported SQLite binding type")
    elif isinstance(values, (tuple, list)):
        if any(not valid(value) for value in values):
            raise sqlite3.ProgrammingError("Unsupported SQLite binding type")
    else:
        raise sqlite3.ProgrammingError("SQLite bindings require a sequence or mapping")
    return values


def _translated(module, error):
    if isinstance(error, SQLPreflightLimit):
        return SQLPreflightLimit("SQLite statement preflight exceeds the engine SQL scan budget")
    if isinstance(error, sqlite3.NotSupportedError):
        kind = sqlite3.NotSupportedError
    elif isinstance(error, (module.ConstraintError, sqlite3.IntegrityError)):
        kind = sqlite3.IntegrityError
    elif isinstance(error, (module.BindingsError, module.MisuseError, module.ConnectionClosedError,
                            module.CursorClosedError, sqlite3.ProgrammingError, TypeError, ValueError)):
        kind = sqlite3.ProgrammingError
    elif isinstance(error, (module.TooBigError, OverflowError, sqlite3.DataError)):
        kind = sqlite3.DataError
    elif isinstance(error, (module.NotADBError, module.CorruptError)):
        kind = sqlite3.DatabaseError
    else:
        kind = sqlite3.OperationalError
    result = kind("Encrypted SQLite operation failed")
    code = getattr(error, "extendedresult", None)
    if type(code) is int:
        name = module.mapping_extended_result_codes.get(code) or module.mapping_result_codes.get(code & 255)
        if isinstance(name, str) and name.startswith("SQLITE_") and all(c.isupper() or c.isdigit() or c == "_" for c in name):
            result.sqlite_errorcode, result.sqlite_errorname = code, name
    return result


def _call(module, function):
    # Raise after leaving the except block: neither a raw APSW error context
    # nor its SQL/value annotations should become a UI/log exception chain.
    try:
        return function()
    except Exception as error:
        translated = _translated(module, error)
    raise translated from None


def _trivia(module, tail):
    """Let SQLite parse trailing comments/no-ops, outside the real database.

    An authorizer denies every real action before a PRAGMA or other statement
    could affect this scratch connection. Never prepare a second statement on
    the actual database, and never split SQL on guessed semicolon positions.
    """
    parser = module.Connection(":memory:")
    actions = []
    def deny(*args):
        actions.append(args[0])
        return module.SQLITE_DENY
    parser.set_authorizer(deny)
    try:
        info = module.ext.query_info(parser, tail)
        return not actions and not info.has_vdbe and info.query_remaining is None
    except module.Error:
        return False
    finally:
        parser.close()


def _semicolon_candidates(sql, checkpoint):
    """Skip quoted/comment text in one pass; SQLite still decides boundaries.

    This is only a candidate filter, not a SQL parser. SQLite's complete()
    validates each remaining candidate, including CREATE TRIGGER bodies.
    Invalid/unclosed tokens are left to SQLite rather than repaired here.
    """
    index, length, state, next_checkpoint, has_content = 0, len(sql), None, 0, False
    while index < length:
        if index >= next_checkpoint:
            checkpoint()
            next_checkpoint = index + 1024
        character = sql[index]
        following = sql[index + 1] if index + 1 < length else ""
        if state == "--":
            if character == "\n":
                state = None
        elif state == "/*":
            if character == "*" and following == "/":
                state = None
                index += 1
        elif state is not None:
            if character == state:
                if state != "]" and following == state:
                    index += 1
                else:
                    state = None
        elif character in "'\"`[":
            has_content = True
            state = "]" if character == "[" else character
        elif character == "-" and following == "-":
            state = "--"
            index += 1
        elif character == "/" and following == "*":
            state = "/*"
            index += 1
        elif character == ";":
            yield index, has_content
        elif character not in " \t\n\r\f\v":
            has_content = True
        index += 1
    checkpoint()


def _single_statement(module, sql, checkpoint, work_limit):
    """Reject a real tail before SQLite prepares the requested statement.

    Some PRAGMAs act while preparing rather than stepping. SQLite's own
    complete-statement tokenizer decides which candidate ends a statement;
    the scratch parser then rejects any executable tail. Repeated incomplete
    trigger prefixes have a cumulative scan budget equal to the connection's
    actual SQLITE_LIMIT_SQL_LENGTH. Exhaustion rejects the complete input,
    never a truncated prefix. The trace repeats the check after preparation.
    """
    start, work = 0, 0
    for index, has_content in _semicolon_candidates(sql, checkpoint):
        checkpoint()
        if not has_content:
            start = index + 1
            continue
        candidate = sql[start:index + 1]
        work += len(candidate.encode("utf-8"))
        if work > work_limit:
            raise SQLPreflightLimit("SQLite statement preflight exceeds the engine SQL scan budget")
        if module.complete(candidate):
            if _trivia(module, candidate):
                start = index + 1
                continue
            if not _trivia(module, sql[index + 1:]):
                raise sqlite3.ProgrammingError("Only one SQLite statement is allowed")
            return


class CipherCursor:
    def __init__(self, connection):
        self.connection = connection
        self._cursor = connection._raw.cursor()
        self._closed = False
        self._description = None
        self.rowcount, self.arraysize = -1, 1
        self._dml, self._exhausted = False, True

    @property
    def description(self):
        return self._description

    def _check(self):
        self.connection._check()
        if self._closed:
            raise sqlite3.ProgrammingError("The SQLite cursor is closed")

    def execute(self, sql, parameters=()):
        return self._execute(sql, parameters, require_dml=False)

    def _execute(self, sql, parameters, *, require_dml):
        self._check()
        if not isinstance(sql, str) or "\0" in sql:
            raise sqlite3.ProgrammingError("SQLite SQL requires a NUL-free string")
        _bindings(parameters)
        module, raw = self.connection._module, self.connection._raw
        self._description, self.rowcount, self._dml, self._exhausted = None, -1, False, False
        actions, first = [], [True]
        previous = raw.authorizer
        def authorizer(*args):
            actions.append(args)
            # The explicit factories own connection key state. Allowing a
            # public rekey to remove encryption would invalidate the keyed
            # backup-target contract. SQLite calls this before the PRAGMA can
            # act, including its prepare-time effects.
            if ((args[0] == module.SQLITE_PRAGMA and args[1].lower() in _MANAGED_CIPHER_PRAGMAS)
                    or (args[0] == module.SQLITE_FUNCTION and isinstance(args[2], str)
                        and args[2].lower().startswith("sqlite3mc_config"))):
                raise sqlite3.NotSupportedError("Connection key changes require a dedicated provider operation")
            return previous(*args) if previous is not None else module.SQLITE_OK
        def trace(cursor, statement, bindings):
            if not first[0]:
                if cursor.has_vdbe:
                    raise sqlite3.ProgrammingError("Only one SQLite statement is allowed")
                return True
            first[0] = False
            if not sql.startswith(statement) or (sql[len(statement):] and not _trivia(module, sql[len(statement):])):
                raise sqlite3.ProgrammingError("Only one SQLite statement is allowed")
            columns = cursor.get_description()
            self._description = tuple((column[0], None, None, None, None, None, None) for column in columns) or None
            self._dml = any(action in (module.SQLITE_INSERT, module.SQLITE_UPDATE, module.SQLITE_DELETE)
                and table not in ("sqlite_master", "sqlite_schema") and source is None
                for action, table, _column, _schema, source in actions)
            if require_dml and not self._dml:
                raise sqlite3.ProgrammingError("SQLite executemany requires a data mutation")
            if require_dml and self._description:
                raise sqlite3.NotSupportedError("SQLite executemany with RETURNING is not supported")
            if self._dml:
                self.rowcount = 0
            return True
        def run():
            self.connection._preflight_cancel.clear()
            _single_statement(module, sql, self.connection._preflight_checkpoint,
                              raw.limit(module.SQLITE_LIMIT_SQL_LENGTH))
            raw.set_authorizer(authorizer)
            self._cursor.set_exec_trace(trace)
            try:
                self._cursor.execute(sql, parameters, can_cache=False)
            finally:
                raw.set_authorizer(previous)
            if not self._description:
                self._exhausted = True
                if self._dml:
                    self.rowcount = raw.changes()
            return self
        return _call(module, run)

    def executemany(self, sql, parameters):
        self._check()
        count = 0
        for row in parameters:
            self._execute(sql, row, require_dml=True)
            count += self.rowcount
        self.rowcount = count
        return self

    def fetchone(self):
        self._check()
        if self._exhausted:
            return None
        row = _call(self.connection._module, lambda: next(self._cursor, None))
        if row is None:
            self._exhausted = True
            if self._dml:
                self.rowcount = self.connection._raw.changes()
        return row

    def fetchmany(self, size=None):
        self._check()
        size = self.arraysize if size is None else size
        if type(size) is not int or size < 0:
            raise sqlite3.ProgrammingError("SQLite fetch size must be a nonnegative integer")
        rows = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self):
        self._check()
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                return rows
            rows.append(row)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def close(self):
        if not self._closed:
            _call(self.connection._module, lambda: self._cursor.close())
            self._closed = True


class CipherConnection:
    def __init__(self, module, raw, *, main_keyed, codec):
        self._module, self._raw = module, raw
        self._main_keyed = main_keyed
        self._codec = codec
        self._closed = False
        self._cursors = weakref.WeakSet()
        self._preflight_cancel = threading.Event()
        self._progress_handler = None
        self._progress_n = 1000
        self._raw.set_progress_handler(self._native_progress, 1000)

    def _check(self):
        if self._closed:
            raise sqlite3.ProgrammingError("The SQLite connection is closed")

    @property
    def in_transaction(self):
        self._check()
        return self._raw.in_transaction

    def cursor(self):
        self._check()
        cursor = CipherCursor(self)
        self._cursors.add(cursor)
        return cursor

    def execute(self, sql, parameters=()):
        cursor = self.cursor()
        try:
            return cursor.execute(sql, parameters)
        except BaseException:
            cursor.close()
            raise

    def executemany(self, sql, parameters):
        cursor = self.cursor()
        try:
            return cursor.executemany(sql, parameters)
        except BaseException:
            cursor.close()
            raise

    def getlimit(self, category):
        self._check()
        return _call(self._module, lambda: self._raw.limit(category))

    def set_progress_handler(self, handler, n=1000):
        self._check()
        if handler is not None and not callable(handler):
            raise sqlite3.ProgrammingError("Invalid encrypted SQLite progress handler")
        result = _call(self._module, lambda: self._raw.set_progress_handler(self._native_progress, n if handler is not None else 1000))
        self._progress_handler = handler
        self._progress_n = n if handler is not None else 1000
        return result

    def _native_progress(self):
        return bool(self._preflight_cancel.is_set() or self._progress_handler is not None and self._progress_handler())

    def _preflight_checkpoint(self):
        if self._preflight_cancel.is_set() or self._progress_handler is not None and self._progress_handler():
            error = self._module.InterruptError("Encrypted SQLite operation interrupted")
            error.extendedresult = self._module.SQLITE_INTERRUPT
            raise error

    def interrupt(self):
        self._check()
        self._preflight_cancel.set()
        return _call(self._module, self._raw.interrupt)

    def configure_attachment(self, *, cipher, legacy=None, page_size=0):
        """Select transient cipher options for the next bound ATTACH KEY.

        Filename and key remain execute() bindings, never URI/SQL fragments.
        This configuration does not itself open, encrypt or validate a file.
        """
        self._check()
        _configuration(cipher, legacy, page_size)
        def configure():
            if self._raw.pragma("cipher", cipher, schema="temp") != cipher:
                raise sqlite3.NotSupportedError("The provider rejected the attachment cipher")
            if legacy is not None and self._raw.pragma("legacy", legacy, schema="temp") != str(legacy):
                raise sqlite3.NotSupportedError("The provider rejected the attachment cipher configuration")
            if page_size and self._raw.pragma("legacy_page_size", page_size, schema="temp") != str(page_size):
                raise sqlite3.NotSupportedError("The provider rejected the attachment page size")
        return _call(self._module, configure)

    def commit(self):
        if self.in_transaction:
            self.execute("COMMIT").close()

    def rollback(self, *, savepoint=None):
        """Run fixed cleanup controls even after a cancellation was requested.

        No user SQL or key is accepted. A still-existing outer transaction can
        be retained using its generated savepoint; an engine-rolled-back outer
        transaction is never recreated or replayed.
        """
        self._check()
        if savepoint is not None and (type(savepoint) is not str or not savepoint or "\0" in savepoint or len(savepoint) > 128):
            raise sqlite3.ProgrammingError("Invalid SQLite cleanup savepoint")
        if not self.in_transaction:
            return
        quoted = '"' + savepoint.replace('"', '""') + '"' if savepoint is not None else None
        statements = (f"ROLLBACK TO SAVEPOINT {quoted}", f"RELEASE SAVEPOINT {quoted}") if quoted else ("ROLLBACK",)
        def cleanup():
            self._raw.set_progress_handler(None)
            try:
                for statement in statements:
                    self._raw.execute(statement, can_cache=False).close()
            finally:
                self._raw.set_progress_handler(self._native_progress, self._progress_n)
        return _call(self._module, cleanup)

    def _backup_configuration(self):
        """Return only certified keyed-main format metadata, without a key."""
        self._check()
        if self._main_keyed is not True or type(self._codec) is not tuple or len(self._codec) != 2:
            raise sqlite3.NotSupportedError("Encrypted SQLite backup requires known keyed main cipher settings")
        cipher, legacy = self._codec
        _configuration(cipher, legacy)
        page_size = _call(self._module, lambda: self._raw.pragma("page_size", schema="main"))
        if type(page_size) is not int or page_size not in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536):
            raise sqlite3.NotSupportedError("Encrypted SQLite backup requires a known valid page size")
        _configuration(cipher, legacy, page_size if legacy in (1, 2, 3, 4) else 0)
        return cipher, legacy, page_size

    def open_backup_target(self, database, *, key):
        """Open an exclusively reserved empty target with this main's format.

        The caller must reserve its new file first. Existing-only open and an
        empty catalog check precede key configuration. No header is materialized
        here, allowing the caller to recheck file ownership before backup writes.
        The original or a deliberately supplied target key stays in memory only.
        """
        cipher, legacy, page_size = self._backup_configuration()
        if not isinstance(key, str) or not key or "\0" in key:
            raise sqlite3.ProgrammingError("Encrypted SQLite requires a nonempty NUL-free key")
        return _open_connection(database, key=key, cipher=cipher, legacy=legacy,
            page_size=page_size if legacy in (1, 2, 3, 4) else 0, database_page_size=page_size,
            read_only=False, create=False, timeout=5.0, uri=False)

    def backup(self, target, *, pages=-1, progress=None, name="main", sleep=.250):
        self._check()
        if (not isinstance(target, CipherConnection) or target is self or target._module is not self._module
                or target._main_keyed is not True):
            raise sqlite3.NotSupportedError("Encrypted SQLite backup requires a separate keyed provider target")
        target._check()
        # SQLite's page backup is not a cipher-conversion operation. Different
        # reserve/page configurations can report a completed copy that cannot
        # be reopened. This leaf accepts only known identical main codecs;
        # attached-schema metadata is not yet tracked by a dedicated API.
        if name != "main":
            raise sqlite3.NotSupportedError("Encrypted SQLite backup requires matching known main cipher settings")
        if self._backup_configuration() != target._backup_configuration():
            raise sqlite3.NotSupportedError("Encrypted SQLite backup requires matching main cipher and page sizes")
        if type(pages) is not int or pages == 0 or sleep < 0:
            raise sqlite3.ProgrammingError("Invalid encrypted SQLite backup bounds")
        if self.in_transaction or target.in_transaction:
            raise sqlite3.OperationalError("Encrypted SQLite backup requires no open transaction")
        def copy():
            with target._raw.backup("main", self._raw, name) as backup:
                while not backup.done:
                    done = backup.step(pages if pages > 0 else 128)
                    if progress is not None:
                        progress(self._module.SQLITE_DONE if done else self._module.SQLITE_OK,
                                 backup.remaining, backup.page_count)
        # Busy/locked errors fail; no indefinite retry and no plain fallback.
        return _call(self._module, copy)

    def close(self):
        if not self._closed:
            for cursor in tuple(self._cursors):
                cursor.close()
            _call(self._module, self._raw.close)
            self._closed = True


def connect(database, *, key, cipher="chacha20", legacy=None, page_size=0, read_only=False, create=False, timeout=5.0, uri=False):
    """Open only an explicitly keyed candidate; no backend selection occurs.

    Existing-file mode is the default. Newly created files are populated so an
    accepted key is not mistaken for proof that a zero-byte file is encrypted.
    Key application and the first real catalog read precede publication.
    """
    _configuration(cipher, legacy, page_size)
    if not isinstance(key, str) or not key or "\0" in key:
        raise sqlite3.ProgrammingError("Encrypted SQLite requires a nonempty NUL-free key")
    return _open_connection(database, key=key, cipher=cipher, legacy=legacy, page_size=page_size, read_only=read_only,
                            create=create, timeout=timeout, uri=uri)


def connect_plain_main(database, *, read_only=False, create=False, timeout=5.0, uri=False):
    """Explicit MC connection for a plain main plus encrypted attachments.

    Provider absence still fails. This is never an encrypted backup target and
    never a fallback from an invalid or empty encrypted-main key.
    """
    return _open_connection(database, key=None, cipher=None, legacy=None, page_size=0, read_only=read_only,
                            create=create, timeout=timeout, uri=uri)


def _open_connection(database, *, key, cipher, legacy, page_size, read_only, create, timeout, uri, database_page_size=0):
    if (any(type(flag) is not bool for flag in (read_only, create, uri)) or read_only and create
            or type(timeout) not in (int, float) or not 0 <= timeout <= 2147483):
        raise sqlite3.ProgrammingError("Invalid encrypted SQLite open options")
    if (type(database_page_size) is not int or database_page_size not in (0, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)
            or database_page_size and (read_only or create)):
        raise sqlite3.ProgrammingError("Invalid encrypted SQLite empty target page layout")
    try:
        filename = os.fspath(database)
    except (TypeError, ValueError):
        filename = None
    if not isinstance(filename, str) or not filename or "\0" in filename:
        raise sqlite3.ProgrammingError("Encrypted SQLite requires a nonempty NUL-free filename")
    if uri and any(name != "mode" or value not in ("ro", "rw", "rwc")
                   for name, value in parse_qsl(urlsplit(filename).query, keep_blank_values=True)):
        raise sqlite3.ProgrammingError("SQLite cipher options and keys cannot be supplied through a URI")
    module = _provider()
    flags = module.SQLITE_OPEN_READONLY if read_only else module.SQLITE_OPEN_READWRITE
    if create:
        flags |= module.SQLITE_OPEN_CREATE
    if uri:
        flags |= module.SQLITE_OPEN_URI
    raw = None
    def open_candidate():
        nonlocal raw
        raw = module.Connection(filename, flags=flags, statementcachesize=0)
        raw.set_busy_timeout(round(timeout * 1000))
        if database_page_size:
            if raw.pragma("page_count") != 0:
                raise sqlite3.NotSupportedError("Encrypted SQLite backup target must be an empty reserved file")
            raw.pragma("page_size", database_page_size)
        codec = None
        if key is not None:
            if raw.pragma("cipher", cipher) != cipher:
                raise sqlite3.NotSupportedError("The SQLite provider rejected the requested cipher")
            if legacy is not None and raw.pragma("legacy", legacy) != str(legacy):
                raise sqlite3.NotSupportedError("The SQLite provider rejected the requested cipher configuration")
            if page_size and raw.pragma("legacy_page_size", page_size) != str(page_size):
                raise sqlite3.NotSupportedError("The SQLite provider rejected the requested page size")
            effective_legacy = raw.pragma("legacy")
            if effective_legacy is not None:
                effective_legacy = int(effective_legacy)
            codec = (cipher, effective_legacy)
            if raw.pragma("key", key) != "ok":
                raise sqlite3.DatabaseError("The SQLite provider rejected key application")
        if raw.readonly("main") != bool(read_only):
            raise sqlite3.OperationalError("The SQLite provider opened an unexpected access mode")
        raw.execute("SELECT 1 FROM main.sqlite_schema LIMIT 1", can_cache=False).fetchall()
        if page_size and raw.pragma("page_size") != page_size:
            raise sqlite3.NotSupportedError("The SQLite provider did not apply the requested page size")
        if database_page_size and raw.pragma("page_size") != database_page_size:
            raise sqlite3.NotSupportedError("The SQLite provider did not apply the backup page layout")
        if create and raw.pragma("page_count") == 0:
            raw.pragma("user_version", raw.pragma("user_version"))
        return CipherConnection(module, raw, main_keyed=key is not None, codec=codec)
    try:
        return _call(module, open_candidate)
    except BaseException:
        if raw is not None:
            raw.close()
        raise
