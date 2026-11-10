"""Local database adapters. No GUI code, and no implicit remote connections.

Sessions have one owner thread; only ``cancel`` may be called concurrently.
SQL runs in autocommit unless the user explicitly begins a transaction.
"""
from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import sqlparse


@dataclass
class ConnectionConfig:
    id: str
    name: str
    driver: str = "sqlite"
    database: str = ""
    host: str = "localhost"
    port: int = 0
    username: str = ""
    password: str = field(default="", repr=False)
    ssl_mode: str = "prefer"
    read_only: bool = False
    connect_timeout: int = 10
    ssl_ca: str = ""
    ssl_cert: str = ""
    ssl_key: str = ""
    ssl_crl: str = ""
    ssl_key_password: str = field(default="", repr=False)
    ssh_enabled: bool = False
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_auth_method: str = "password"
    ssh_password: str = field(default="", repr=False)
    ssh_key_file: str = ""
    ssh_key_passphrase: str = field(default="", repr=False)
    ssh_compression: bool = False
    ssh_host_key: str = ""


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    affected: int = 0
    elapsed_ms: float = 0.0
    truncated: bool = False
    sql: str = ""


class DatabaseError(RuntimeError):
    """A real driver error; earlier autocommitted statements may have succeeded."""

    def __init__(self, message: str, partial_results: list[QueryResult] | None = None, *, authentication_required: bool = False):
        super().__init__(message)
        self.partial_results = partial_results or []
        self.authentication_required = authentication_required


def is_database_auth_error(error) -> bool:
    """Classify driver authentication rejection, not transport/TLS failures."""
    from .transport import SSHAuthenticationError, SSHHostKeyError
    if isinstance(error, (SSHAuthenticationError, SSHHostKeyError)):
        return False
    message = str(error).lower()
    if any(word in message for word in ("certificate", "ssl error", "tls error")):
        return False
    state = getattr(error, "sqlstate", None)
    if state in {"28P01", "28000"}:
        return True
    if getattr(error, "args", ()) and error.args[0] in (1045, 1698):
        return True
    return any(text in message for text in (
        "no password supplied", "password authentication failed for user",
        "peer authentication failed for user", "authentication failed for user",
    ))


def _tls_context(config):
    mode = config.ssl_mode or "prefer"
    if mode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise ValueError("Unsupported TLS mode")
    if mode == "disable":
        return None
    if bool(config.ssl_cert) != bool(config.ssl_key):
        raise ValueError("Both TLS client certificate and private-key files are required")
    verify = mode in {"verify-ca", "verify-full"}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = mode == "verify-full"
    context.verify_mode = ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
    if verify:
        if config.ssl_ca:
            context.load_verify_locations(cafile=str(Path(config.ssl_ca).expanduser()))
        else:
            context.load_default_certs()
        if config.ssl_crl:
            context.load_verify_locations(cafile=str(Path(config.ssl_crl).expanduser()))
            context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    if config.ssl_cert:
        # Passing an explicit empty password avoids OpenSSL's terminal prompt.
        try:
            context.load_cert_chain(str(Path(config.ssl_cert).expanduser()),
                                    str(Path(config.ssl_key).expanduser()), password=config.ssl_key_password)
        except ssl.SSLError as exc:
            with Path(config.ssl_key).expanduser().open("rb") as key_file:
                encrypted = b"ENCRYPTED" in key_file.read(8192)
            if encrypted:
                from .transport import TLSKeyPasswordError
                raise TLSKeyPasswordError() from exc
            raise
    return context


class DatabaseSession:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        if config.driver not in {"sqlite", "postgresql", "mysql", "mariadb"}:
            raise ValueError(f"Unsupported database driver: {config.driver}")
        self.connection: Any = None
        self._cancel_requested = threading.Event()
        self._active = False
        self._selected_schema: str | None = None
        self._tunnel = None

    @property
    def placeholder(self) -> str:
        return "?" if self.config.driver == "sqlite" else "%s"

    def connect(self) -> None:
        if self.connection is not None:
            return
        from .transport import SSHTunnel, SSHHostKeyError, SSHAuthenticationError, TLSKeyPasswordError
        c = self.config
        connection = None
        if not isinstance(c.connect_timeout, int) or not 1 <= c.connect_timeout <= 31536000:
            raise ValueError("Connection timeout must be a positive number of seconds")
        try:
            if c.driver == "sqlite":
                if c.ssh_enabled:
                    raise ValueError("SQLite uses local files and does not support SSH TCP forwarding")
                database = c.database or ":memory:"
                if c.read_only and database != ":memory:":
                    database = Path(database).expanduser().resolve().as_uri() + "?mode=ro"
                    connection = sqlite3.connect(database, uri=True, isolation_level=None,
                                                 check_same_thread=False, timeout=c.connect_timeout)
                else:
                    database = str(Path(database).expanduser()) if database != ":memory:" else database
                    connection = sqlite3.connect(database, isolation_level=None,
                                                 check_same_thread=False, timeout=c.connect_timeout)
                connection.execute("PRAGMA foreign_keys = ON")
                if c.read_only:
                    connection.execute("PRAGMA query_only = ON")
                connection.set_progress_handler(lambda: int(self._cancel_requested.is_set()), 1000)
            else:
                # Validate TLS files/key passwords before opening SSH or DB sockets.
                _tls_context(c)
                if c.driver != "postgresql" and c.ssl_mode == "allow":
                    raise ValueError("TLS allow mode is supported by PostgreSQL only; choose prefer for MySQL/MariaDB")
                if c.ssh_enabled:
                    self._tunnel = SSHTunnel(c, c.host, c.port or (5432 if c.driver == "postgresql" else 3306))
                    self._tunnel.start()
                if c.driver == "postgresql":
                    connection = self._connect_postgresql()
                    if c.read_only:
                        connection.execute("SET default_transaction_read_only = on")
                else:
                    connection = self._connect_mysql()
                    if c.read_only:
                        with connection.cursor() as cur:
                            cur.execute("SET SESSION TRANSACTION READ ONLY")
            self.connection = connection
            if c.driver == "sqlite":
                self._selected_schema = "main"
            elif c.driver in {"mysql", "mariadb"}:
                self._selected_schema = c.database or None
            else:
                with connection.cursor() as cur:
                    cur.execute("SELECT current_schema()")
                    self._selected_schema = cur.fetchone()[0]
        except Exception as exc:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()
            self.connection = None
            self._selected_schema = None
            if self._tunnel is not None:
                self._tunnel.close()
                self._tunnel = None
            if isinstance(exc, (SSHHostKeyError, SSHAuthenticationError, TLSKeyPasswordError)):
                raise
            raise DatabaseError(str(exc), authentication_required=is_database_auth_error(exc)) from exc

    def _connect_postgresql(self):
        import psycopg
        c = self.config
        kwargs = dict(host=c.host, port=c.port or 5432, dbname=c.database or "postgres",
                      user=c.username, password=c.password, sslmode=c.ssl_mode or "prefer",
                      connect_timeout=c.connect_timeout, autocommit=True)
        if self._tunnel is not None:
            kwargs.update(hostaddr=self._tunnel.local_host, port=self._tunnel.local_port)
        for field, parameter in (("ssl_ca", "sslrootcert"), ("ssl_cert", "sslcert"),
                                 ("ssl_key", "sslkey"), ("ssl_crl", "sslcrl")):
            value = getattr(c, field)
            if value and c.ssl_mode != "disable":
                kwargs[parameter] = str(Path(value).expanduser())
        if c.ssl_key and c.ssl_mode != "disable":
            kwargs["sslpassword"] = c.ssl_key_password
        return psycopg.connect(**kwargs)

    def _connect_mysql(self, control=False):
        import pymysql
        from pymysql.constants import FIELD_TYPE
        c = self.config
        conversions = dict(pymysql.converters.conversions)
        conversions[FIELD_TYPE.JSON] = json.loads
        context = _tls_context(c)
        connection = pymysql.Connection(host=c.host, port=c.port or 3306,
            database=None if control else (c.database or None), user=c.username, password=c.password.encode("utf-8"),
            connect_timeout=c.connect_timeout, read_timeout=c.connect_timeout, write_timeout=c.connect_timeout,
            charset="utf8mb4", autocommit=True, conv=conversions, defer_connect=True,
            ssl=context, ssl_disabled=context is None)
        # PyMySQL 1.2.4 makes explicit SSL contexts REQUIRED. Prefer is explicitly
        # opportunistic, including when client key material has been supplied.
        if (c.ssl_mode or "prefer") == "prefer":
            connection._ssl_required = False
        sock = None
        try:
            if self._tunnel is not None:
                import socket
                sock = socket.create_connection(self._tunnel.endpoint, c.connect_timeout)
            # A supplied socket connects to loopback while self.host stays the
            # real DB hostname for SNI and verify-full certificate matching.
            connection.connect(sock=sock)
            if not control:
                # Handshake/TLS/auth obey connect_timeout; query I/O retains the
                # established workbench timeout after the connection succeeds.
                connection._read_timeout = connection._write_timeout = 60
            if c.ssl_mode in {"require", "verify-ca", "verify-full"} and not connection._secure:
                raise DatabaseError("The server did not negotiate the required TLS connection")
            return connection
        except BaseException:
            with contextlib.suppress(Exception):
                connection.close()
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
            raise

    def close(self) -> None:
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            self.connection = None
            self._selected_schema = None
            if self._tunnel is not None:
                self._tunnel.close()
                self._tunnel = None

    @property
    def in_transaction(self) -> bool:
        if self.connection is None:
            return False
        if self.config.driver == "sqlite":
            return self.connection.in_transaction
        if self.config.driver == "postgresql":
            from psycopg.pq import TransactionStatus
            return self.connection.info.transaction_status != TransactionStatus.IDLE
        return bool(self.connection.server_status & 1)

    def cancel(self) -> None:
        """Cancel the current command without dropping an explicit transaction silently."""
        self._cancel_requested.set()
        connection = self.connection
        if connection is None or not self._active:
            return
        if self.config.driver == "sqlite":
            connection.interrupt()
        elif self.config.driver == "postgresql":
            from psycopg import capabilities
            if not capabilities.has_cancel_safe():
                raise DatabaseError("Secure bounded PostgreSQL cancellation requires libpq 17 or newer")
            connection.cancel_safe(timeout=self.config.connect_timeout)
        else:
            # A separate control connection is needed; the busy connection cannot
            # safely carry KILL QUERY. PyMySQL has no thread-safe cancel primitive.
            with self._connect_mysql(control=True) as control:
                with control.cursor() as cur:
                    cur.execute(f"KILL QUERY {int(connection.thread_id())}")

    def quote_identifier(self, name: str) -> str:
        if not isinstance(name, str) or "\x00" in name:
            raise ValueError("Database identifiers must be strings without NUL bytes")
        delimiter = "`" if self.config.driver in {"mysql", "mariadb"} else '"'
        return delimiter + name.replace(delimiter, delimiter * 2) + delimiter

    def qualified_name(self, schema: str, table: str) -> str:
        return f"{self.quote_identifier(schema)}.{self.quote_identifier(table)}"

    @contextlib.contextmanager
    def _cursor(self, *, stream: bool = False) -> Iterator[Any]:
        self.connect()
        if stream and self.config.driver in {"mysql", "mariadb"}:
            import pymysql.cursors
            cur = self.connection.cursor(pymysql.cursors.SSCursor)
        else:
            cur = self.connection.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def _run(self, cur: Any, sql: str, params: Any = None) -> Any:
        if not params:
            return cur.execute(sql)
        if self.config.driver != "sqlite":
            sql = self._escape_quoted_percent(sql)
        return cur.execute(sql, params)

    @staticmethod
    def _escape_quoted_percent(sql: str) -> str:
        # DB-API's percent placeholders are parsed even inside SQL identifiers.
        # Escape percent signs in the identifiers/literals of generated SQL only.
        out: list[str] = []
        delimiter: str | None = None
        i = 0
        while i < len(sql):
            ch = sql[i]
            if delimiter:
                if ch == "%":
                    out.append("%%")
                else:
                    out.append(ch)
                if ch == delimiter:
                    if i + 1 < len(sql) and sql[i + 1] == delimiter:
                        out.append(sql[i + 1])
                        i += 1
                    else:
                        delimiter = None
            else:
                out.append(ch)
                if ch in {"'", '"', "`"}:
                    delimiter = ch
            i += 1
        return "".join(out)

    def _query(self, sql: str, params: Any = None) -> list[tuple]:
        with self._cursor() as cur:
            self._run(cur, sql, params)
            return cur.fetchall() if cur.description else []

    def execute(self, sql: str, limit: int = 1000) -> list[QueryResult]:
        if not 1 <= limit <= 100000:
            raise ValueError("Result limit must be between 1 and 100000")
        self.connect()
        self._cancel_requested.clear()
        self._active = True
        results: list[QueryResult] = []
        try:
            statements = [s for s in sqlparse.split(sql) if sqlparse.format(s, strip_comments=True).strip().rstrip(";")]
            for statement in statements:
                if self._cancel_requested.is_set():
                    raise DatabaseError("Query cancelled", results)
                started = time.perf_counter()
                with self._cursor(stream=True) as cur:
                    cur.execute(statement)
                    if self._cancel_requested.is_set():
                        raise DatabaseError("Query cancelled", results)
                    while True:
                        columns = [col[0] for col in cur.description] if cur.description else []
                        data = cur.fetchmany(limit + 1) if columns else []
                        if self._cancel_requested.is_set():
                            raise DatabaseError("Query cancelled", results)
                        truncated = len(data) > limit
                        affected = len(data[:limit]) if columns else max(cur.rowcount, 0)
                        results.append(QueryResult(columns=columns,
                            rows=[list(row) for row in data[:limit]], affected=affected,
                            elapsed_ms=(time.perf_counter() - started) * 1000,
                            truncated=truncated, sql=statement))
                        # SQLite/psycopg cursors do not need extra result-set traversal.
                        if self.config.driver not in {"mysql", "mariadb"}:
                            break
                        # SSCursor.close()/nextset drains remaining wire results without
                        # storing them, keeping client memory bounded.
                        if not cur.nextset():
                            break
            return results
        except Exception as exc:
            if isinstance(exc, DatabaseError):
                if not exc.partial_results:
                    exc.partial_results = results
                raise
            raise DatabaseError(str(exc), results) from exc
        finally:
            self._active = False
            self._cancel_requested.clear()

    def use_schema(self, schema: str) -> None:
        """Bind unqualified editor queries to an existing namespace.

        Namespace switching is kept outside explicit transactions: PostgreSQL SET
        is transactional, so caching an in-transaction switch would become stale
        after ROLLBACK. A repeated selection does no database work and therefore
        still allows the SQL editor to roll back a failed PostgreSQL transaction.
        SQLite has no session search_path; its main/attached lookup rules apply.
        """
        self.connect()
        if schema == self._selected_schema:
            return
        if self.in_transaction:
            raise DatabaseError("Commit or roll back the open transaction before changing the active schema")
        if schema not in self.schemas():
            raise DatabaseError(f"Schema does not exist or is not accessible: {schema}")
        if self.config.driver == "postgresql":
            self._query(f"SET search_path TO {self.quote_identifier(schema)}")
        elif self.config.driver in {"mysql", "mariadb"}:
            self.connection.select_db(schema)
        self._selected_schema = schema

    def schemas(self) -> list[str]:
        if self.config.driver == "sqlite":
            return [row[1] for row in self._query("PRAGMA database_list")]
        if self.config.driver == "postgresql":
            return [row[0] for row in self._query("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY nspname")]
        return [row[0] for row in self._query("SHOW DATABASES")]

    def objects(self, schema: str) -> list[dict]:
        if self.config.driver == "sqlite":
            rows = self._query(f"SELECT name, type FROM {self.quote_identifier(schema)}.sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type,name")
        elif self.config.driver == "postgresql":
            rows = self._query("SELECT c.relname, CASE WHEN c.relkind IN ('v','m') THEN 'view' ELSE 'table' END FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname", (schema,))
        else:
            rows = self._query("SELECT TABLE_NAME, CASE TABLE_TYPE WHEN 'VIEW' THEN 'view' ELSE 'table' END FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (schema,))
        return [{"name": row[0], "kind": row[1], "schema": schema} for row in rows]

    def columns(self, schema: str, table: str) -> list[dict]:
        if self.config.driver == "sqlite":
            rows = self._query(f"PRAGMA {self.quote_identifier(schema)}.table_xinfo({self.quote_identifier(table)})")
            return [{"name": row[1], "type": row[2], "nullable": not bool(row[3] or row[5]),
                     "default": row[4], "pk": bool(row[5]), "generated": bool(row[6]),
                     "pk_order": row[5]} for row in rows if row[6] != 1]
        if self.config.driver == "postgresql":
            rows = self._query("""SELECT a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),
                NOT a.attnotnull, pg_get_expr(d.adbin,d.adrelid),
                EXISTS(SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid AND i.indisprimary AND a.attnum=ANY(i.indkey)),
                a.attgenerated <> '', a.attidentity
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid
                LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
                WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (schema, table))
            return [{"name": r[0], "type": r[1], "nullable": bool(r[2]), "default": r[3],
                     "pk": bool(r[4]), "generated": bool(r[5]), "identity": bool(r[6]),
                     "identity_generation": "ALWAYS" if r[6] == "a" else "BY DEFAULT"} for r in rows]
        rows = self._query("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,COLUMN_KEY,EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (schema, table))
        metadata = [{"name": r[0], "type": r[1], "nullable": r[2] == "YES", "default": r[3],
                 "pk": r[4] == "PRI", "generated": any(k in (r[5] or "") for k in ("VIRTUAL GENERATED", "STORED GENERATED")),
                 "identity": "auto_increment" in (r[5] or "")} for r in rows]
        if self.config.driver == "mariadb":
            # MariaDB's JSON alias is stored as LONGTEXT plus an exact JSON_VALID
            # constraint. Recognize that declaration without treating arbitrary
            # LONGTEXT as JSON or guessing from the column name.
            checks = self._query("SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=%s AND TABLE_NAME=%s", (schema, table))
            json_names = set()
            for check in checks:
                match = re.fullmatch(r"\s*json_valid\(\s*`((?:``|[^`])+)`\s*\)\s*", check[0], re.IGNORECASE)
                if match:
                    json_names.add(match.group(1).replace("``", "`"))
            for col in metadata:
                if col["name"] in json_names:
                    col["storage_type"] = col["type"]
                    col["type"] = "json"
        return metadata

    def indexes(self, schema: str, table: str) -> list[dict]:
        """Read declared index keys, uniqueness and available static definitions."""
        if self.config.driver == "sqlite":
            rows = self._query(f"PRAGMA {self.quote_identifier(schema)}.index_list({self.quote_identifier(table)})")
            result = []
            for row in rows:
                keys = self._query(f"PRAGMA {self.quote_identifier(schema)}.index_xinfo({self.quote_identifier(row[1])})")
                columns = [key[2] if key[2] is not None else "<expression>" for key in keys if key[5]]
                definition = self._query(f"SELECT sql FROM {self.quote_identifier(schema)}.sqlite_schema WHERE type='index' AND name=?", (row[1],))
                result.append({"name": row[1], "columns": columns, "unique": bool(row[2]),
                    "primary": row[3] == "pk", "definition": definition[0][0] or "Automatic constraint index" if definition else "Automatic constraint index"})
            primary = [col["name"] for col in sorted(self.columns(schema, table), key=lambda c: c.get("pk_order", 0)) if col["pk"]]
            if primary and not any(index["primary"] for index in result):
                result.insert(0, {"name": "PRIMARY (rowid)", "columns": primary, "unique": True,
                    "primary": True, "definition": "INTEGER PRIMARY KEY uses SQLite's rowid B-tree"})
            return result
        if self.config.driver == "postgresql":
            rows = self._query("""SELECT indexrel.relname, i.indisunique, i.indisprimary,
                pg_get_indexdef(i.indexrelid), ARRAY(
                    SELECT COALESCE(a.attname, pg_get_indexdef(i.indexrelid, k.ord::integer, true))
                    FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
                    LEFT JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                    WHERE k.ord <= i.indnkeyatts ORDER BY k.ord)
                FROM pg_index i JOIN pg_class tab ON tab.oid=i.indrelid
                JOIN pg_class indexrel ON indexrel.oid=i.indexrelid
                JOIN pg_namespace n ON n.oid=tab.relnamespace
                WHERE n.nspname=%s AND tab.relname=%s ORDER BY i.indisprimary DESC, indexrel.relname""", (schema, table))
            return [{"name": r[0], "columns": list(r[4]), "unique": bool(r[1]),
                     "primary": bool(r[2]), "definition": r[3]} for r in rows]
        with self._cursor() as cur:
            cur.execute(f"SHOW INDEX FROM {self.qualified_name(schema, table)}")
            fields = [d[0] for d in cur.description]
            rows = [dict(zip(fields, row)) for row in cur.fetchall()]
        grouped = {}
        for row in sorted(rows, key=lambda r: (r["Key_name"], r["Seq_in_index"])):
            name = row["Key_name"]
            item = grouped.setdefault(name, {"name": name, "columns": [], "unique": not bool(row["Non_unique"]),
                "primary": name == "PRIMARY", "definition": "", "_keys": [], "_type": row.get("Index_type", "")})
            column = row.get("Column_name") or row.get("Expression") or "<expression>"
            item["columns"].append(column)
            key = self.quote_identifier(column) if row.get("Column_name") else f"({column})"
            if row.get("Sub_part"):
                key += f"({int(row['Sub_part'])})"
            if row.get("Collation") == "D":
                key += " DESC"
            item["_keys"].append(key)
        for item in grouped.values():
            kind = "PRIMARY KEY" if item["primary"] else ("UNIQUE INDEX " if item["unique"] else "INDEX ") + self.quote_identifier(item["name"])
            item["definition"] = kind + " (" + ", ".join(item.pop("_keys")) + ")"
            index_type = item.pop("_type")
            if index_type:
                item["definition"] += " USING " + index_type
        return sorted(grouped.values(), key=lambda i: (not i["primary"], i["name"]))

    def foreign_keys(self, schema: str, table: str) -> list[dict]:
        """Read ordered source/target columns and referential actions."""
        if self.config.driver == "sqlite":
            rows = self._query(f"PRAGMA {self.quote_identifier(schema)}.foreign_key_list({self.quote_identifier(table)})")
            grouped = {}
            for row in sorted(rows, key=lambda r: (r[0], r[1])):
                item = grouped.setdefault(row[0], {"name": f"fk_{table}_{row[0]}", "columns": [],
                    "referenced_table": row[2], "referenced_columns": [], "on_update": row[5], "on_delete": row[6]})
                item["columns"].append(row[3])
                target = row[4]
                if target is None:
                    pk = sorted((c for c in self.columns(schema, row[2]) if c["pk"]), key=lambda c: c.get("pk_order", 0))
                    target = pk[row[1]]["name"] if row[1] < len(pk) else "<implicit primary key>"
                item["referenced_columns"].append(target)
            return list(grouped.values())
        if self.config.driver == "postgresql":
            rows = self._query("""SELECT con.conname, target_ns.nspname, target.relname,
                ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum,ord)
                      JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum ORDER BY k.ord),
                ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum,ord)
                      JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=k.attnum ORDER BY k.ord),
                con.confupdtype, con.confdeltype
                FROM pg_constraint con JOIN pg_class tab ON tab.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=tab.relnamespace
                JOIN pg_class target ON target.oid=con.confrelid
                JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace
                WHERE con.contype='f' AND n.nspname=%s AND tab.relname=%s ORDER BY con.conname""", (schema, table))
            actions = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
            return [{"name": r[0], "columns": list(r[3]), "referenced_table": r[2],
                "referenced_schema": r[1], "referenced_columns": list(r[4]),
                "on_update": actions.get(r[5], r[5]), "on_delete": actions.get(r[6], r[6])} for r in rows]
        rows = self._query("""SELECT k.CONSTRAINT_NAME,k.COLUMN_NAME,k.REFERENCED_TABLE_SCHEMA,
            k.REFERENCED_TABLE_NAME,k.REFERENCED_COLUMN_NAME,r.UPDATE_RULE,r.DELETE_RULE,k.ORDINAL_POSITION
            FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r
            ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME AND r.TABLE_NAME=k.TABLE_NAME
            WHERE k.TABLE_SCHEMA=%s AND k.TABLE_NAME=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY k.CONSTRAINT_NAME,k.ORDINAL_POSITION""", (schema, table))
        grouped = {}
        for row in rows:
            item = grouped.setdefault(row[0], {"name": row[0], "columns": [], "referenced_schema": row[2],
                "referenced_table": row[3], "referenced_columns": [], "on_update": row[5], "on_delete": row[6]})
            item["columns"].append(row[1])
            item["referenced_columns"].append(row[4])
        return list(grouped.values())

    def table_ddl(self, schema: str, table: str) -> str:
        if self.config.driver == "sqlite":
            rows = self._query(f"SELECT sql FROM {self.quote_identifier(schema)}.sqlite_schema WHERE name=? AND type IN ('table','view')", (table,))
            return (rows[0][0] or "") + ";" if rows else ""
        if self.config.driver in {"mysql", "mariadb"}:
            rows = self._query(f"SHOW CREATE TABLE {self.qualified_name(schema, table)}")
            return rows[0][1] + ";" if rows else ""
        # PostgreSQL exposes view definitions and relation/constraint metadata, but
        # not full table CREATE statements. Generate an explicit reconstruction.
        rows = self._query("SELECT c.relkind, pg_get_viewdef(c.oid,true) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s AND c.relkind IN ('v','m')", (schema, table))
        if rows:
            prefix = "CREATE MATERIALIZED VIEW" if rows[0][0] == "m" else "CREATE VIEW"
            return f"{prefix} {self.qualified_name(schema, table)} AS\n{rows[0][1]}"
        columns = self.columns(schema, table)
        if not columns:
            return ""
        definitions = []
        for col in columns:
            text = f"  {self.quote_identifier(col['name'])} {col['type']}"
            if col.get("generated"):
                text += f" GENERATED ALWAYS AS ({col['default']}) STORED"
            elif col.get("identity"):
                text += f" GENERATED {col.get('identity_generation', 'BY DEFAULT')} AS IDENTITY"
            elif col["default"] is not None:
                text += f" DEFAULT {col['default']}"
            if not col["nullable"]:
                text += " NOT NULL"
            definitions.append(text)
        constraints = self._query("SELECT con.conname,pg_get_constraintdef(con.oid,true) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s ORDER BY con.conname", (schema, table))
        definitions.extend(f"  CONSTRAINT {self.quote_identifier(r[0])} {r[1]}" for r in constraints)
        ddl = "-- Reconstructed PostgreSQL table definition; ownership, grants, storage and standalone indexes are not included.\n"
        return ddl + f"CREATE TABLE {self.qualified_name(schema, table)} (\n" + ",\n".join(definitions) + "\n);"

    def _filter(self, schema: str, table: str, text: str) -> tuple[str, list[Any]]:
        if not text:
            return "", []
        names = [col["name"] for col in self.columns(schema, table)]
        if not names:
            raise DatabaseError("Table or view has no visible columns")
        # Escape LIKE wildcards: user input is a literal substring, not SQL/pattern.
        escaped = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        cast_type = "CHAR" if self.config.driver in {"mysql", "mariadb"} else "TEXT"
        expressions = [f"CAST({self.quote_identifier(n)} AS {cast_type}) LIKE {self.placeholder} ESCAPE '!'" for n in names]
        return " WHERE (" + " OR ".join(expressions) + ")", [f"%{escaped}%"] * len(names)

    def fetch_table(self, schema: str, table: str, offset: int = 0, limit: int = 200,
                    order_by: str | None = None, descending: bool = False,
                    filter_text: str = "") -> QueryResult:
        if offset < 0 or not 1 <= limit <= 100000:
            raise ValueError("Invalid page size or offset")
        columns = self.columns(schema, table)
        names = [c["name"] for c in columns]
        if not names:
            raise DatabaseError("Table or view has no visible columns")
        if order_by and order_by not in names:
            raise ValueError("Sort column does not exist")
        where, params = self._filter(schema, table, filter_text)
        selected = ",".join(self.quote_identifier(n) for n in names)
        sql = f"SELECT {selected} FROM {self.qualified_name(schema, table)}{where}"
        order = [order_by] if order_by else [c["name"] for c in columns if c["pk"]]
        if order:
            sql += " ORDER BY " + ",".join(self.quote_identifier(n) for n in order)
            if descending:
                sql += " DESC"
        sql += f" LIMIT {self.placeholder} OFFSET {self.placeholder}"
        started = time.perf_counter()
        with self._cursor() as cur:
            self._run(cur, sql, params + [limit + 1, offset])
            rows = cur.fetchall()
            if self.config.driver in {"sqlite", "mariadb"}:
                json_columns = [i for i, col in enumerate(columns) if col["type"].lower() in {"json", "jsonb"}]
                decoded = []
                for row in rows:
                    values = list(row)
                    for i in json_columns:
                        if isinstance(values[i], (str, bytes)):
                            with contextlib.suppress(ValueError, TypeError):
                                values[i] = json.loads(values[i])
                    decoded.append(values)
                rows = decoded
            return QueryResult([d[0] for d in cur.description], [list(r) for r in rows[:limit]],
                               len(rows[:limit]), (time.perf_counter()-started)*1000,
                               len(rows)>limit, sql)

    def count_rows(self, schema: str, table: str, filter_text: str = "") -> int:
        where, params = self._filter(schema, table, filter_text)
        return int(self._query(f"SELECT COUNT(*) FROM {self.qualified_name(schema, table)}{where}", params)[0][0])

    def _assert_write(self) -> None:
        if self.config.read_only:
            raise DatabaseError("This connection is read-only")

    @contextlib.contextmanager
    def _atomic(self) -> Iterator[None]:
        self._assert_write()
        self.connect()
        existing = self.in_transaction
        savepoint = "onv_" + uuid.uuid4().hex
        if existing:
            self._query(f"SAVEPOINT {savepoint}")
        else:
            self._query("BEGIN IMMEDIATE" if self.config.driver == "sqlite" else "BEGIN")
        try:
            yield
            self._query(f"RELEASE SAVEPOINT {savepoint}" if existing else "COMMIT")
        except BaseException:
            with contextlib.suppress(Exception):
                self._query(f"ROLLBACK TO SAVEPOINT {savepoint}" if existing else "ROLLBACK")
                if existing:
                    self._query(f"RELEASE SAVEPOINT {savepoint}")
            raise

    def _validate_names(self, schema: str, table: str, values: dict) -> list[dict]:
        columns = self.columns(schema, table)
        known = {c["name"] for c in columns}
        if not known:
            raise DatabaseError("Table does not exist or has no visible columns")
        if not set(values).issubset(known):
            raise ValueError("Unknown table column")
        if any(c.get("generated") and c["name"] in values for c in columns):
            raise ValueError("Generated columns cannot be written")
        return columns

    def _adapt_values(self, columns: list[dict], values: dict) -> list[Any]:
        types = {column["name"]: column["type"].lower() for column in columns}
        adapted = []
        for name, value in values.items():
            if isinstance(value, memoryview):
                value = bytes(value)
            if value is not None and types.get(name) in {"json", "jsonb"}:
                if self.config.driver == "postgresql":
                    from psycopg.types.json import Json, Jsonb
                    value = (Jsonb if types[name] == "jsonb" else Json)(value)
                elif self.config.driver in {"sqlite", "mysql", "mariadb"}:
                    value = json.dumps(value, ensure_ascii=False)
            if self.config.driver == "sqlite":
                if isinstance(value, Decimal):
                    if not value.is_finite():
                        raise ValueError("SQLite transfer does not support non-finite Decimal values")
                    value = str(value)
                elif isinstance(value, (datetime, date, datetime_time)):
                    value = value.isoformat()
            adapted.append(value)
        return adapted

    def _locked_original(self, schema: str, table: str, original: dict) -> tuple[str, list[Any]]:
        columns = self.columns(schema, table)
        pks = [c["name"] for c in columns if c["pk"]]
        if not pks:
            raise DatabaseError("Editing and deleting rows requires a primary key")
        if not all(pk in original and original[pk] is not None for pk in pks):
            raise DatabaseError("The original row must include every non-null primary key value")
        names = [c["name"] for c in columns]
        if not all(name in original for name in names):
            raise DatabaseError("The original row must include every visible column for conflict checking")
        where = " AND ".join(f"{self.quote_identifier(pk)} = {self.placeholder}" for pk in pks)
        params = [original[pk] for pk in pks]
        suffix = "" if self.config.driver == "sqlite" else " FOR UPDATE"
        selected = ",".join(self.quote_identifier(n) for n in names)
        rows = self._query(f"SELECT {selected} FROM {self.qualified_name(schema, table)} WHERE {where}{suffix}", params)
        if len(rows) != 1:
            raise DatabaseError("The row was removed or its primary key changed; refresh before editing")
        current = dict(zip(names, rows[0]))
        if self.config.driver in {"sqlite", "mariadb"}:
            for col in columns:
                value = current[col["name"]]
                if col["type"].lower() in {"json", "jsonb"} and isinstance(value, (str, bytes)):
                    with contextlib.suppress(ValueError, TypeError):
                        current[col["name"]] = json.loads(value)
        if any(not self._same_value(current[n], original[n]) for n in names):
            raise DatabaseError("The row changed in the database; refresh before editing")
        return where, params

    @staticmethod
    def _same_value(left: Any, right: Any) -> bool:
        # memoryview vs bytes is common with PostgreSQL bytea values.
        if isinstance(left, memoryview):
            left = bytes(left)
        if isinstance(right, memoryview):
            right = bytes(right)
        if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
            return True
        return left == right

    def update_row(self, schema: str, table: str, original: dict, changes: dict) -> int:
        if not changes:
            return 0
        columns = self._validate_names(schema, table, changes)
        try:
            with self._atomic():
                where, params = self._locked_original(schema, table, original)
                # A no-op edit is still successful after optimistic conflict checking.
                if all(self._same_value(original.get(n), v) for n, v in changes.items()):
                    return 1
                sql = f"UPDATE {self.qualified_name(schema, table)} SET " + ",".join(
                    f"{self.quote_identifier(n)} = {self.placeholder}" for n in changes) + f" WHERE {where}"
                with self._cursor() as cur:
                    self._run(cur, sql, self._adapt_values(columns, changes) + params)
                    if cur.rowcount != 1:
                        raise DatabaseError("Update did not affect exactly one row")
            return 1
        except Exception as exc:
            if isinstance(exc, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(exc)) from exc

    def delete_row(self, schema: str, table: str, original: dict) -> int:
        try:
            with self._atomic():
                where, params = self._locked_original(schema, table, original)
                with self._cursor() as cur:
                    self._run(cur, f"DELETE FROM {self.qualified_name(schema, table)} WHERE {where}", params)
                    if cur.rowcount != 1:
                        raise DatabaseError("Delete did not affect exactly one row")
            return 1
        except Exception as exc:
            if isinstance(exc, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(exc)) from exc

    def insert_row(self, schema: str, table: str, values: dict) -> int:
        columns = self._validate_names(schema, table, values)
        try:
            with self._atomic():
                if values:
                    sql = f"INSERT INTO {self.qualified_name(schema, table)} (" + ",".join(
                        self.quote_identifier(n) for n in values) + ") VALUES (" + ",".join(
                            [self.placeholder] * len(values)) + ")"
                else:
                    sql = f"INSERT INTO {self.qualified_name(schema, table)} " + (
                        "() VALUES ()" if self.config.driver in {"mysql", "mariadb"} else "DEFAULT VALUES")
                with self._cursor() as cur:
                    self._run(cur, sql, self._adapt_values(columns, values))
                    if cur.rowcount != 1:
                        raise DatabaseError("Insert did not affect exactly one row")
            return 1
        except Exception as exc:
            if isinstance(exc, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(exc)) from exc

    def import_rows(self, schema: str, table: str, columns: list[str], rows: list[list[Any]]) -> int:
        if not columns or len(set(columns)) != len(columns):
            raise ValueError("Import requires unique column names")
        metadata = self._validate_names(schema, table, dict.fromkeys(columns))
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("Every imported row must have the same number of columns")
        if not rows:
            return 0
        sql = f"INSERT INTO {self.qualified_name(schema, table)} (" + ",".join(
            self.quote_identifier(n) for n in columns) + ") VALUES (" + ",".join(
                [self.placeholder] * len(columns)) + ")"
        try:
            with self._atomic():
                with self._cursor() as cur:
                    cur.executemany(self._escape_quoted_percent(sql) if self.config.driver != "sqlite" else sql,
                                    [self._adapt_values(metadata, dict(zip(columns, row))) for row in rows])
            return len(rows)
        except Exception as exc:
            if isinstance(exc, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(exc)) from exc

    def backup_to(self, path: str) -> None:
        """Write a consistent SQLite snapshot through SQLite's online backup API.

        Refuse an overwrite and an open write transaction. Other database engines
        need vendor dump tools rather than a misleading partial SQL export.
        """
        if self.config.driver != "sqlite":
            raise DatabaseError("Physical backup is currently available for SQLite only")
        self.connect()
        if self.in_transaction:
            raise DatabaseError("Commit or roll back the open transaction before backing up")
        destination = Path(path).expanduser().resolve()
        source = self.config.database
        if source and source != ":memory:" and destination == Path(source).expanduser().resolve():
            raise DatabaseError("Backup destination must differ from the source database")
        # Exclusive creation ensures an existing file is never accidentally lost.
        with destination.open("xb"):
            pass
        try:
            target = sqlite3.connect(destination)
            try:
                self.connection.backup(target)
            finally:
                target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
