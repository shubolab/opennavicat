"""Standalone Redis sessions with native, binary-safe RESP2 commands.

This adapter deliberately has no SQL table/transaction facade. Collection
browser requests use SCAN/ranges; SCAN COUNT is a server hint, not a hard bound
on server work or redis-py's wire-response memory. Native command result rows
are capped after Redis has returned its response.
"""
from __future__ import annotations

import contextlib
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from redis.connection import Connection
from redis.exceptions import AuthenticationError, RedisError, ResponseError

from .database import ConnectionConfig, DatabaseError, DatabaseSession, QueryResult, _tls_context


class RedisConflictError(DatabaseError):
    """An optimistic key/element edit no longer matches the displayed value."""


@dataclass
class RedisScanPage:
    entries: list[dict] = field(default_factory=list)
    cursor: int = 0
    complete: bool = True


@dataclass
class RedisValuePage:
    schema: str
    key: bytes
    type: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total: int = 0
    ttl: int = -2
    pttl: int = -2
    truncated: bool = False
    cursor: int | bytes = 0
    next_offset: int = 0
    value: bytes | None = None


_UNSET = object()
_STATEFUL = {b"MULTI", b"EXEC", b"DISCARD", b"WATCH", b"UNWATCH", b"SUBSCRIBE", b"PSUBSCRIBE", b"SSUBSCRIBE",
             b"UNSUBSCRIBE", b"PUNSUBSCRIBE", b"SUNSUBSCRIBE", b"MONITOR", b"AUTH", b"HELLO", b"RESET", b"QUIT", b"SYNC", b"PSYNC", b"REPLCONF"}


def _bytes(value: bytes | str | int | float) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", "strict")
    if isinstance(value, bool):
        raise ValueError("Redis arguments must be bytes, text or explicit numbers")
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float) and math.isfinite(value):
        return repr(value).encode("ascii")
    raise ValueError("Redis arguments must be bytes, text or finite numbers")


def quote_redis_bytes(value: bytes | str) -> str:
    """A redis-cli double-quoted argument that round-trips every byte."""
    value = _bytes(value)
    escaped = []
    substitutions = {34: '\\"', 92: "\\\\", 10: "\\n", 13: "\\r", 9: "\\t", 8: "\\b", 7: "\\a"}
    for byte in value:
        escaped.append(substitutions.get(byte, chr(byte) if 32 <= byte < 127 else f"\\x{byte:02x}"))
    return '"' + "".join(escaped) + '"'


def display_redis_bytes(value: bytes | str) -> str:
    raw = _bytes(value)
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return quote_redis_bytes(raw)
    return text if text and text.isprintable() and "\\" not in text and '"' not in text else quote_redis_bytes(raw)


def parse_redis_commands(text: str) -> list[list[bytes]]:
    """Parse editor lines with redis-cli quotes and byte escapes, not SQL.

    Semicolons are ordinary argument bytes. Newlines outside quotes separate
    commands. A quoted argument must end before whitespace or end of input.
    """
    if not isinstance(text, str):
        raise ValueError("Redis command text must be a string")
    commands, arguments, token = [], [], bytearray()
    active, quote, i = False, "", 0
    whitespace = " \t\r\n\v\f"
    escapes = {"n": b"\n", "r": b"\r", "t": b"\t", "b": b"\b", "a": b"\a"}
    while i < len(text):
        char = text[i]
        if quote:
            if quote == '"' and char == "\\":
                if i + 1 >= len(text):
                    raise ValueError("Unterminated Redis quoted argument")
                following = text[i + 1]
                if following == "x" and i + 3 < len(text) and all(c in "0123456789abcdefABCDEF" for c in text[i + 2:i + 4]):
                    token.append(int(text[i + 2:i + 4], 16)); i += 4; continue
                token.extend(escapes.get(following, following.encode("utf-8", "strict")))
                i += 2; continue
            if quote == "'" and char == "\\" and i + 1 < len(text) and text[i + 1] == "'":
                token.extend(b"'"); i += 2; continue
            if char == quote:
                if i + 1 < len(text) and text[i + 1] not in whitespace:
                    raise ValueError("A closing Redis quote must be followed by whitespace")
                quote = ""
            else:
                token.extend(char.encode("utf-8", "strict"))
            i += 1; continue
        if char in {'"', "'"}:
            quote, active = char, True
        elif char in whitespace:
            if active:
                arguments.append(bytes(token)); token.clear(); active = False
            if char in "\r\n" and arguments:
                commands.append(arguments); arguments = []
        else:
            token.extend(char.encode("utf-8", "strict")); active = True
        i += 1
    if quote:
        raise ValueError("Unterminated Redis quoted argument")
    if active:
        arguments.append(bytes(token))
    if arguments:
        commands.append(arguments)
    return commands


class _TLSConnection(Connection):
    def __init__(self, *, tls_context, server_hostname, **kwargs):
        self._tls_context = tls_context
        self._tls_hostname = server_hostname
        super().__init__(**kwargs)

    def _connect(self):
        sock = super()._connect()
        try:
            return self._tls_context.wrap_socket(sock, server_hostname=self._tls_hostname)
        except BaseException:
            sock.close()
            raise


class RedisSession(DatabaseSession):
    def __init__(self, config: ConnectionConfig):
        if config.driver != "redis":
            raise ValueError("RedisSession requires driver='redis'")
        self.config = config
        self.connection: Connection | None = None
        self._tunnel = None
        self._tls = None
        self._cancel_requested = threading.Event()
        self._active = False
        self._selected_schema = self._database(config.database or "0")
        self._command_cache: dict[bytes, list | None] = {}
        self.server_info: dict[str, str] = {}
        self.database_count: int | None = None
        self.schema_list_complete = False
        self.last_scan_cursor = 0

    @staticmethod
    def _database(value):
        text = str(value)
        if not text.isascii() or not text.isdecimal() or len(text) > 10:
            raise ValueError("Redis database must be a nonnegative numeric index")
        result = int(text)
        if result > 2147483647:
            raise ValueError("Redis database index is out of range")
        return str(result)

    @property
    def in_transaction(self):
        return False

    def _new_connection(self):
        config = self.config
        host, port = (self._tunnel.local_host, self._tunnel.local_port) if self._tunnel else (config.host, config.port or 6379)
        kwargs = dict(host=host, port=port, db=int(self._selected_schema),
                      username=config.username or None, password=config.password or None,
                      socket_timeout=config.connect_timeout, socket_connect_timeout=config.connect_timeout,
                      protocol=2, decode_responses=False, encoding="utf-8", encoding_errors="strict",
                      health_check_interval=0, driver_info=None)
        return (_TLSConnection(tls_context=self._tls, server_hostname=config.host, **kwargs)
                if self._tls else Connection(**kwargs))

    def connect(self):
        if self.connection is not None:
            return
        from .transport import SSHTunnel, SSHAuthenticationError, SSHHostKeyError, TLSKeyPasswordError
        config = self.config
        if isinstance(config.connect_timeout, bool) or not isinstance(config.connect_timeout, int) or config.connect_timeout < 1:
            raise ValueError("Connection timeout must be a positive number of seconds")
        if config.ssl_mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise DatabaseError("Redis supports TLS disable, require, verify-ca or verify-full; opportunistic allow/prefer modes are unavailable")
        try:
            self._tls = _tls_context(config)
            if config.ssh_enabled:
                self._tunnel = SSHTunnel(config, config.host, config.port or 6379)
                self._tunnel.start()
            self.connection = self._new_connection()
            self.connection.connect()
            self._wire(b"PING")
            self.server_info = self._info(self._wire(b"INFO", b"server"))
            mode = self.server_info.get("redis_mode", "")
            if mode != "standalone":
                raise DatabaseError("This Redis adapter supports standalone nodes only; cluster and sentinel remain unsupported")
            if config.username and int(self.server_info.get("redis_version", "0").split(".")[0]) < 6:
                raise DatabaseError("Redis ACL usernames require Redis 6 or newer; the username was not accepted as a default-user fallback")
        except BaseException as exc:
            self.close()
            if isinstance(exc, (SSHAuthenticationError, SSHHostKeyError, TLSKeyPasswordError, DatabaseError)):
                raise
            if isinstance(exc, Exception):
                raise DatabaseError("Redis connection failed: " + str(exc), authentication_required=isinstance(exc, AuthenticationError)) from exc
            raise

    def close(self):
        if self.connection is not None:
            with contextlib.suppress(Exception):
                self.connection.disconnect()
            self.connection = None
        if self._tunnel is not None:
            self._tunnel.close()
            self._tunnel = None
        self._command_cache.clear()

    def cancel(self):
        if self._active and self.connection is not None:
            self._cancel_requested.set()
            # Closing this dedicated socket interrupts a blocking RESP read and
            # leaves no unread response on a pooled/shared connection. The same
            # connection object reconnects/authenticates/selects DB on next use.
            self.connection.disconnect()

    def _wire(self, *arguments):
        connection = self.connection
        if connection is None:
            raise DatabaseError("Redis is disconnected")
        connection.send_command(*arguments)
        return connection.read_response()

    def _raw(self, *arguments):
        self.connect()
        try:
            return self._wire(*[_bytes(value) for value in arguments])
        except RedisError as exc:
            if self._cancel_requested.is_set():
                raise DatabaseError("Redis command cancelled") from exc
            raise DatabaseError(str(exc), authentication_required=isinstance(exc, AuthenticationError)) from exc

    @staticmethod
    def _info(response):
        return {key.decode("ascii"): value.decode("utf-8", "backslashreplace")
                for line in response.splitlines() if line and not line.startswith(b"#") and b":" in line
                for key, value in [line.split(b":", 1)]}

    def schemas(self):
        self.connect()
        try:
            configuration = self._raw(b"CONFIG", b"GET", b"databases")
            if len(configuration) == 2 and configuration[0] == b"databases":
                total = int(configuration[1])
                if 0 < total <= 65536:
                    self.database_count, self.schema_list_complete = total, True
                    return [str(index) for index in range(total)]
        except DatabaseError as exc:
            if exc.authentication_required:
                raise
        active = self._info(self._raw(b"INFO", b"keyspace"))
        known = {int(self._selected_schema), 0}
        known.update(int(key[2:]) for key in active if key.startswith("db") and key[2:].isdigit())
        self.database_count, self.schema_list_complete = None, False
        return [str(index) for index in sorted(known)]

    def use_schema(self, schema):
        database = self._database(schema)
        self.connect()
        if self._selected_schema == database:
            return
        response = self._raw(b"SELECT", database)
        if response != b"OK":
            raise DatabaseError("Redis did not select the requested database")
        self._selected_schema = database
        self.connection.db = int(database)

    def _command_info(self, arguments):
        name = arguments[0].lower()
        top = self._raw(b"COMMAND", b"INFO", name)
        if not top or top[0] is None:
            return None
        metadata = top[0]
        if len(arguments) > 1 and len(metadata) > 9 and metadata[9]:
            sub_name = name + b"|" + arguments[1].lower()
            sub = next((entry for entry in metadata[9] if entry[0].lower() == sub_name), None)
            if sub is not None:
                return sub
        return metadata

    def _check_command(self, arguments):
        command = arguments[0].upper()
        if command in _STATEFUL or (command == b"CLIENT" and len(arguments) > 1 and arguments[1].upper() in {b"REPLY", b"TRACKING", b"CACHING", b"UNBLOCK", b"PAUSE"}):
            raise DatabaseError("This connection-state, transaction or streaming command is not supported by the command editor")
        if self.config.read_only:
            try:
                info = self._command_info(arguments)
            except DatabaseError as exc:
                raise DatabaseError("Read-only execution requires Redis COMMAND INFO permission; the command was not executed") from exc
            flags = {flag.lower() for flag in info[2]} if info and len(info) > 2 else set()
            # PING/ECHO are protocol-level, non-mutating controls; they do not
            # carry Redis's data-set readonly flag. Everything else needs that
            # authoritative flag, including module and *_RO commands.
            safe_control = bool(info and command in {b"PING", b"ECHO", b"SELECT"} and b"write" not in flags)
            if b"readonly" not in flags and not safe_control:
                raise DatabaseError("This command is not marked readonly by Redis COMMAND metadata")

    def execute(self, sql: str, limit: int = 1000) -> list[QueryResult]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ValueError("Result limit must be between 1 and 100000")
        try:
            commands = parse_redis_commands(sql)
        except (ValueError, UnicodeError) as exc:
            raise DatabaseError(str(exc)) from exc
        self.connect()
        self._cancel_requested.clear()
        self._active = True
        results = []
        try:
            for arguments in commands:
                if self._cancel_requested.is_set():
                    raise DatabaseError("Redis command cancelled")
                started = time.perf_counter()
                self._check_command(arguments)
                if arguments[0].upper() == b"SELECT":
                    if len(arguments) != 2:
                        raise DatabaseError("SELECT requires one database index")
                    self.use_schema(arguments[1].decode("ascii", "strict"))
                    response = b"OK"
                else:
                    response = self._raw(*arguments)
                text = " ".join(quote_redis_bytes(argument) for argument in arguments)
                if isinstance(response, list):
                    result = QueryResult(["Index", "Value"], [[i + 1, item] for i, item in enumerate(response[:limit])],
                        elapsed_ms=(time.perf_counter() - started) * 1000, truncated=len(response) > limit, sql=text)
                else:
                    result = QueryResult(["Result"], [[response]], affected=max(response, 0) if isinstance(response, int) else 0,
                        elapsed_ms=(time.perf_counter() - started) * 1000, sql=text)
                results.append(result)
            return results
        except Exception as exc:
            if self._cancel_requested.is_set():
                raise DatabaseError("Redis command cancelled", results) from exc
            if isinstance(exc, DatabaseError):
                exc.partial_results = results
                raise
            raise DatabaseError(str(exc), results) from exc
        finally:
            self._active = False
            self._cancel_requested.clear()

    def objects(self, schema):
        page = self.scan_keys(schema)
        self.last_scan_cursor = page.cursor
        return page.entries

    def scan_keys(self, schema, cursor=0, count=200, match=b"*"):
        self.use_schema(schema)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("Redis SCAN cursor must be a nonnegative integer")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 10000:
            raise ValueError("Redis SCAN count must be between 1 and 10000")
        next_cursor, keys = self._raw(b"SCAN", cursor, b"MATCH", _bytes(match), b"COUNT", count)
        entries = []
        for key in dict.fromkeys(keys):
            kind = self._raw(b"TYPE", key).decode("ascii", "strict")
            if kind == "none":
                continue
            ttl, pttl = self._raw(b"TTL", key), self._raw(b"PTTL", key)
            entries.append({"name": display_redis_bytes(key), "key": key, "kind": "redis_key", "schema": self._selected_schema,
                            "type": kind, "ttl": ttl, "pttl": pttl})
        return RedisScanPage(entries, int(next_cursor), int(next_cursor) == 0)

    def read_key(self, schema, key, offset=0, limit=200, cursor=0, max_bytes=65536):
        self.use_schema(schema)
        key = _bytes(key)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Redis offset must be a nonnegative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 10000:
            raise ValueError("Redis page size must be between 1 and 10000")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise ValueError("Redis string view must be between 1 byte and 16 MiB")
        kind = self._raw(b"TYPE", key).decode("ascii", "strict")
        page = RedisValuePage(self._selected_schema, key, kind, ttl=self._raw(b"TTL", key), pttl=self._raw(b"PTTL", key))
        if kind == "none" or page.pttl == -2:
            page.type = "none"
            return page
        if kind == "string":
            page.total = self._raw(b"STRLEN", key)
            page.value = self._raw(b"GETRANGE", key, offset, offset + max_bytes - 1)
            page.columns, page.rows = ["Value"], [[page.value]]
            page.next_offset = offset + len(page.value)
            page.truncated = offset > 0 or page.next_offset < page.total
        elif kind in {"hash", "set"}:
            if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
                raise ValueError("Redis collection cursor must be a nonnegative integer")
            response = self._raw(b"HSCAN" if kind == "hash" else b"SSCAN", key, cursor, b"COUNT", limit)
            page.cursor = int(response[0])
            page.total = self._raw(b"HLEN" if kind == "hash" else b"SCARD", key)
            page.columns = ["Field", "Value"] if kind == "hash" else ["Member"]
            page.rows = [response[1][i:i + 2] for i in range(0, len(response[1]), 2)] if kind == "hash" else [[member] for member in response[1]]
            page.truncated = cursor != 0 or page.cursor != 0
        elif kind in {"list", "zset"}:
            page.total = self._raw(b"LLEN" if kind == "list" else b"ZCARD", key)
            response = self._raw(b"LRANGE", key, offset, offset + limit - 1) if kind == "list" else self._raw(b"ZRANGE", key, offset, offset + limit - 1, b"WITHSCORES")
            page.columns = ["Index", "Value"] if kind == "list" else ["Member", "Score"]
            page.rows = [[offset + index, value] for index, value in enumerate(response)] if kind == "list" else [response[i:i + 2] for i in range(0, len(response), 2)]
            page.next_offset = offset + len(page.rows)
            page.truncated = offset > 0 or page.next_offset < page.total
        elif kind == "stream":
            start = b"-" if cursor in (0, b"0", "0", b"-", "-") else b"(" + _bytes(cursor)
            response = self._raw(b"XRANGE", key, start, b"+", b"COUNT", limit + 1)
            page.total, page.columns = self._raw(b"XLEN", key), ["ID", "Fields"]
            page.rows = response[:limit]
            page.cursor = page.rows[-1][0] if len(response) > limit and page.rows else 0
            page.truncated = start != b"-" or len(response) > limit
        else:
            raise DatabaseError("This Redis key type is not supported by the current value browser: " + kind)
        final_type = self._raw(b"TYPE", key).decode("ascii", "strict")
        if final_type == "none":
            return RedisValuePage(self._selected_schema, key, "none")
        if final_type != kind:
            raise RedisConflictError("The key type changed while reading; refresh the value")
        return page

    def _edit(self, schema, key, kind, read_expression, mutation, arguments, *, expected=_UNSET):
        """One server-side atomic compare/edit; scripts are application constants."""
        self._assert_write()
        self.use_schema(schema)
        flag = b"0" if expected is _UNSET else b"1" if expected is None else b"2"
        original = b"" if expected is _UNSET or expected is None else _bytes(expected)
        script = """local kind=redis.call('TYPE',KEYS[1]).ok
if kind~='none' and kind~=ARGV[3] then return redis.error_reply('WRONGTYPE key type changed') end
local current=""" + read_expression + """
if ARGV[1]=='1' and current~=false then return 0 end
if ARGV[1]=='2' and current~=ARGV[2] then return 0 end
""" + mutation + "\nreturn 1"
        response = self._raw(b"EVAL", script, 1, _bytes(key), flag, original, kind, *arguments)
        if response != 1:
            raise RedisConflictError("The key/element changed or the destination already exists; refresh before editing")
        return 1

    def create_key(self, schema, key, kind, initial):
        """Atomically create one new typed key; an existing key is never edited.

        Hash/stream values are ordered (field,value) pairs; zsets are ordered
        (member,score) pairs. Other collections take a list of binary members.
        There is one Redis write command after the NX check, not a script that
        deletes/rebuilds an existing object on an intermediate failure.
        """
        self._assert_write()
        self.use_schema(schema)
        if kind == "string":
            arguments = [b"SET", _bytes(initial)]
        elif kind in {"list", "set"}:
            if not initial:
                raise ValueError("A Redis collection needs an initial member")
            arguments = [b"RPUSH" if kind == "list" else b"SADD", *[_bytes(value) for value in initial]]
        elif kind in {"hash", "zset", "stream"}:
            if not initial or any(len(pair) != 2 for pair in initial):
                raise ValueError("Hash, sorted-set and stream initial values require ordered pairs")
            arguments = [b"HSET" if kind == "hash" else b"ZADD" if kind == "zset" else b"XADD"]
            if kind == "stream":
                arguments.append(b"*")
            for first, second in initial:
                arguments.extend([_bytes(second), _bytes(first)] if kind == "zset" else [_bytes(first), _bytes(second)])
        else:
            raise ValueError("Choose a string, hash, list, set, zset or stream key")
        if len(arguments) > 4096:
            raise ValueError("The initial key contains too many elements; create it with fewer elements and append later")
        script = """if redis.call('EXISTS',KEYS[1])~=0 then return 0 end
redis.call(ARGV[1],KEYS[1],unpack(ARGV,2))
return 1"""
        if self._raw(b"EVAL", script, 1, _bytes(key), *arguments) != 1:
            raise RedisConflictError("The key already exists; no existing value was changed")
        return True

    def set_string(self, schema, key, value, *, expected=_UNSET, keep_ttl=True):
        """Replace a full string. A truncated browser page is not a full value."""
        # SET KEEPTTL is one native write. A SET followed by PEXPIRE could leave
        # a changed value with no TTL if the second command is ACL-denied: Lua
        # isolation does not provide rollback on runtime errors.
        mutation = "redis.call('SET',KEYS[1],ARGV[4],'KEEPTTL')" if keep_ttl else "redis.call('SET',KEYS[1],ARGV[4])"
        return self._edit(schema, key, b"string", "redis.call('GET',KEYS[1])",
                          mutation, [_bytes(value)], expected=expected)

    def set_hash_field(self, schema, key, field, value, *, expected=_UNSET):
        return self._edit(schema, key, b"hash", "redis.call('HGET',KEYS[1],ARGV[4])",
                          "redis.call('HSET',KEYS[1],ARGV[4],ARGV[5])", [_bytes(field), _bytes(value)], expected=expected)

    def delete_hash_field(self, schema, key, field, *, expected=_UNSET):
        return self._edit(schema, key, b"hash", "redis.call('HGET',KEYS[1],ARGV[4])",
                          "if current==false then return 0 end\nredis.call('HDEL',KEYS[1],ARGV[4])", [_bytes(field)], expected=expected)

    def set_list_item(self, schema, key, index, value, *, expected=_UNSET):
        self._list_index(index)
        return self._edit(schema, key, b"list", "redis.call('LINDEX',KEYS[1],ARGV[4])",
                          "if current==false then return 0 end\nredis.call('LSET',KEYS[1],ARGV[4],ARGV[5])", [index, _bytes(value)], expected=expected)

    @staticmethod
    def _list_index(index):
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("List index must be an integer")

    def append_list(self, schema, key, values, *, left=False):
        self._assert_write()
        self.use_schema(schema)
        if not values:
            raise ValueError("Choose at least one list value")
        return self._raw(b"LPUSH" if left else b"RPUSH", _bytes(key), *[_bytes(value) for value in values])

    def delete_list_item(self, schema, key, index, *, expected=_UNSET):
        self._list_index(index)
        marker = b"\x00OpenNavicat:" + secrets.token_bytes(32)
        # Check the marker inside the same script: no legitimate equal value can
        # be accidentally removed by LREM, even with duplicate list elements.
        return self._edit(schema, key, b"list", "redis.call('LINDEX',KEYS[1],ARGV[4])",
            "if current==false or redis.call('LPOS',KEYS[1],ARGV[5])~=false then return 0 end\n"
            "if not redis.acl_check_cmd then return redis.error_reply('Safe compound edits require Redis 7 or newer') end\n"
            "if not redis.acl_check_cmd('LSET',KEYS[1],ARGV[4],ARGV[5]) or not redis.acl_check_cmd('LREM',KEYS[1],1,ARGV[5]) then return redis.error_reply('NOPERM compound list edit requires LSET and LREM; nothing was changed') end\n"
            "redis.call('LSET',KEYS[1],ARGV[4],ARGV[5])\nredis.call('LREM',KEYS[1],1,ARGV[5])", [index, marker], expected=expected)

    def add_set_member(self, schema, key, member):
        self._assert_write()
        self.use_schema(schema)
        return self._raw(b"SADD", _bytes(key), _bytes(member))

    def replace_set_member(self, schema, key, member, replacement):
        member, replacement = _bytes(member), _bytes(replacement)
        return self._edit(schema, key, b"set", "redis.call('SISMEMBER',KEYS[1],ARGV[4])==1 and ARGV[4] or false",
            "if ARGV[4]~=ARGV[5] and redis.call('SISMEMBER',KEYS[1],ARGV[5])==1 then return 0 end\n"
            "if not redis.acl_check_cmd then return redis.error_reply('Safe compound edits require Redis 7 or newer') end\n"
            "if not redis.acl_check_cmd('SADD',KEYS[1],ARGV[5]) or (ARGV[4]~=ARGV[5] and not redis.acl_check_cmd('SREM',KEYS[1],ARGV[4])) then return redis.error_reply('NOPERM compound set edit requires SADD and SREM; nothing was changed') end\n"
            "redis.call('SADD',KEYS[1],ARGV[5])\nif ARGV[4]~=ARGV[5] then redis.call('SREM',KEYS[1],ARGV[4]) end", [member, replacement], expected=member)

    def remove_set_member(self, schema, key, member):
        member = _bytes(member)
        return self._edit(schema, key, b"set", "redis.call('SISMEMBER',KEYS[1],ARGV[4])==1 and ARGV[4] or false",
                          "redis.call('SREM',KEYS[1],ARGV[4])", [member], expected=member)

    def set_zset_member(self, schema, key, member, score, *, expected=_UNSET):
        return self._edit(schema, key, b"zset", "redis.call('ZSCORE',KEYS[1],ARGV[4])",
                          "redis.call('ZADD',KEYS[1],ARGV[5],ARGV[4])", [_bytes(member), _bytes(score)], expected=expected)

    def replace_zset_member(self, schema, key, member, replacement, score, *, expected=_UNSET):
        return self._edit(schema, key, b"zset", "redis.call('ZSCORE',KEYS[1],ARGV[4])",
            "if current==false then return 0 end\n"
            "if ARGV[4]~=ARGV[5] and redis.call('ZSCORE',KEYS[1],ARGV[5])~=false then return 0 end\n"
            "if not redis.acl_check_cmd then return redis.error_reply('Safe compound edits require Redis 7 or newer') end\n"
            "if not redis.acl_check_cmd('ZADD',KEYS[1],ARGV[6],ARGV[5]) or (ARGV[4]~=ARGV[5] and not redis.acl_check_cmd('ZREM',KEYS[1],ARGV[4])) then return redis.error_reply('NOPERM compound sorted-set edit requires ZADD and ZREM; nothing was changed') end\n"
            # Lua scripts are isolated but do not roll back earlier writes after
            # a runtime error. Validate/add the destination before removing the
            # source, so an invalid score never deletes the original member.
            "redis.call('ZADD',KEYS[1],ARGV[6],ARGV[5])\nif ARGV[4]~=ARGV[5] then redis.call('ZREM',KEYS[1],ARGV[4]) end",
            [_bytes(member), _bytes(replacement), _bytes(score)], expected=expected)

    def delete_zset_member(self, schema, key, member, *, expected=_UNSET):
        return self._edit(schema, key, b"zset", "redis.call('ZSCORE',KEYS[1],ARGV[4])",
                          "if current==false then return 0 end\nredis.call('ZREM',KEYS[1],ARGV[4])", [_bytes(member)], expected=expected)

    def stream_add(self, schema, key, fields, *, entry_id=b"*"):
        self._assert_write()
        self.use_schema(schema)
        if not fields:
            raise ValueError("A stream entry requires at least one field/value pair")
        arguments = [_bytes(part) for pair in fields for part in pair]
        if any(len(pair) != 2 for pair in fields):
            raise ValueError("Stream fields must be ordered field/value pairs")
        return self._raw(b"XADD", _bytes(key), _bytes(entry_id), *arguments)

    def stream_delete(self, schema, key, entry_ids):
        self._assert_write()
        self.use_schema(schema)
        if not entry_ids:
            raise ValueError("Choose at least one stream entry ID")
        return self._raw(b"XDEL", _bytes(key), *[_bytes(entry_id) for entry_id in entry_ids])

    def expire_key(self, schema, key, ttl_ms=None):
        self._assert_write()
        self.use_schema(schema)
        if ttl_ms is None:
            return self._raw(b"PERSIST", _bytes(key))
        if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms < 0:
            raise ValueError("Expiration must be a nonnegative millisecond integer, or None to persist")
        return self._raw(b"PEXPIRE", _bytes(key), ttl_ms)

    def rename_key(self, schema, key, replacement, *, replace=False):
        self._assert_write()
        self.use_schema(schema)
        response = self._raw(b"RENAME" if replace else b"RENAMENX", _bytes(key), _bytes(replacement))
        if not replace and response != 1:
            raise RedisConflictError("The destination key already exists; nothing was overwritten")
        return True

    def delete_key(self, schema, key):
        self._assert_write()
        self.use_schema(schema)
        response = self._raw(b"DEL", _bytes(key))
        if response != 1:
            raise RedisConflictError("The key no longer exists; refresh the key list")
        return True

    def _unsupported_sql(self, *args, **kwargs):
        raise DatabaseError("Redis uses native commands and key types; this SQL table operation is unavailable")

    columns = indexes = foreign_keys = table_ddl = fetch_table = count_rows = _unsupported_sql
    quote_identifier = qualified_name = _unsupported_sql
    insert_row = update_row = delete_row = import_rows = backup_to = _unsupported_sql
