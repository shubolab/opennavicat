"""Dedicated Oracle Thin Basic/service browser; no implicit client configuration.

Only this session's serial owner performs database reads. ``cancel_current`` is
the sole concurrent I/O entry and is scoped to an exact operation token. Result
limits bound delivered rows/bytes, not Oracle execution work or total RSS.
"""
from __future__ import annotations

import contextlib
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import ipaddress
import sys
import threading
import time
from typing import Any

import sqlparse

from .database import ConnectionConfig, DatabaseError, DatabaseSession, QueryResult

MAX_ROWS = 10_000
MAX_BYTES = 8 * 1024 * 1024
MAX_CELL_BYTES = 1024 * 1024

TABLE_FIELDS = {
    "table_type": None, "tablespace_name": "TABLESPACE_NAME", "cluster_name": "CLUSTER_NAME",
    "cluster_owner": "CLUSTER_OWNER", "iot_name": "IOT_NAME", "iot_type": "IOT_TYPE",
    "logging": "LOGGING", "backed_up": "BACKED_UP", "num_rows": "NUM_ROWS",
    "degree": "DEGREE", "instances": "INSTANCES", "cache": "CACHE", "table_lock": "TABLE_LOCK",
    "sample_size": "SAMPLE_SIZE", "last_analyzed": "LAST_ANALYZED", "partitioned": "PARTITIONED",
    "temporary": "TEMPORARY", "secondary": "SECONDARY", "nested": "NESTED", "monitoring": "MONITORING",
    "dependencies": "DEPENDENCIES", "row_movement": "ROW_MOVEMENT", "compression": "COMPRESSION",
    "generated": "GENERATED", "buffer_pool": "BUFFER_POOL",
}
VIEW_FIELDS = {"text_length": "TEXT_LENGTH", "type_text": "TYPE_TEXT", "type_text_length": "TYPE_TEXT_LENGTH",
    "oid_text": "OID_TEXT", "oid_text_length": "OID_TEXT_LENGTH", "view_type": "VIEW_TYPE",
    "view_type_owner": "VIEW_TYPE_OWNER", "superview_name": "SUPERVIEW_NAME",
    "editioning_view": "EDITIONING_VIEW", "read_only": "READ_ONLY"}


class OracleError(DatabaseError):
    def __init__(self, message, *, code=None, cancelled=False, authentication_required=False):
        super().__init__(message, authentication_required=authentication_required)
        self.code = code
        self.cancelled = cancelled


@dataclass(eq=False)
class _Operation:
    external_cancel: threading.Event | None
    timeout_seconds: int = 10
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    connection: Any = None
    retiring: bool = False
    cancel_inflight: bool = False
    cancel_done: threading.Event = field(default_factory=threading.Event)
    discard_connection: Any = None

    def __post_init__(self):
        self.cancel_done.set()


def _safe_error(error):
    """Driver text may contain SQL, credentials or business values. Keep code only."""
    detail = error.args[0] if getattr(error, "args", ()) else None
    full = getattr(detail, "full_code", None)
    number = getattr(detail, "code", None)
    code = full if type(full) is str and re.fullmatch(r"(?:ORA|DPY|DPI)-[0-9]{4,5}", full) else None
    if code is None and type(number) is int and 0 < number < 100000:
        code = f"ORA-{number:05d}"
    auth = number in {1017, 28000, 28001, 28009}
    return OracleError("Oracle operation failed" + (f" ({code})" if code else ""),
                       code=code, authentication_required=auth)


def validate_oracle_execution(config, *, require_password=False):
    """Pure support validation usable before a GUI's temporary password prompt."""
    if not require_password and config.password == "":
        config = replace(config, password="validation-only-placeholder")
    OracleSession(config)._connection_options()


class OracleSession(DatabaseSession):
    supports_grid_mutations = False
    supports_arbitrary_ddl = False

    def __init__(self, config: ConnectionConfig):
        if config.driver != "oracle":
            raise ValueError("OracleSession requires driver='oracle'")
        self.config = config
        self.connection = None
        self._selected_schema = None
        self._tunnel = None
        self._active = False
        self._cancel_requested = threading.Event()
        self._operation_lock = threading.RLock()
        self._operation_token: _Operation | None = None
        self._connected_config = None

    @property
    def placeholder(self):
        return ":1"

    @property
    def in_transaction(self):
        # Cached/local driver property; never issue GUI-thread SQL.
        with self._operation_lock:
            connection = self.connection
            try:
                return bool(connection is not None and connection.transaction_in_progress)
            except Exception:
                # A connection concurrently marked unusable is not a reason to
                # propagate a native driver exception through a GUI status read.
                return False

    @property
    def current_operation_marker(self):
        with self._operation_lock:
            token = self._operation_token
            return token if token is not None and not token.retiring else None

    def _connection_options(self, config=None):
        c = config if config is not None else self.config
        strings = (c.oracle_connection_type, c.oracle_service_type, c.oracle_service_identifier,
                   c.oracle_role, c.oracle_tns_alias, c.oracle_settings_directory,
                   c.oracle_client_charset, c.oracle_initial_query, c.oracle_notes)
        if any(type(value) is not str for value in strings):
            raise ValueError("Oracle text options must be strings")
        if c.oracle_connection_type != "basic" or c.oracle_service_type != "service":
            raise ValueError("Oracle TNS and SID connections are not implemented")
        if c.oracle_role != "default" or c.oracle_os_auth is not False:
            raise ValueError("Oracle privileged roles and OS authentication are not implemented")
        if c.database:
            raise ValueError("Oracle service must use its service field, not the selected schema")
        if c.read_only is not False:
            raise ValueError("Oracle read-only session enforcement is not implemented")
        if c.ssl_mode != "disable" or any((c.ssl_ca, c.ssl_cert, c.ssl_key, c.ssl_crl, c.ssl_key_password)):
            raise ValueError("Oracle TLS connections are not implemented; select explicit TCP")
        if c.ssh_enabled is not False:
            raise ValueError("Oracle SSH connections are not implemented")
        if any((c.oracle_settings_directory, c.oracle_auto_connect, c.oracle_session_limit_enabled,
                c.oracle_keepalive_enabled, c.oracle_client_charset, c.oracle_initial_query,
                c.oracle_custom_database_list_enabled)):
            raise ValueError("The selected Oracle advanced options are not implemented")
        for value in (c.host, c.username, c.oracle_service_identifier):
            if type(value) is not str or not value.strip() or any(ord(ch) < 32 for ch in value):
                raise ValueError("Oracle host, username and service are required plain text")
        # Component-based driver options still become an Oracle Net descriptor.
        # Accept a literal IP/DNS host and plain service, never descriptor syntax
        # smuggled through these fields or an implicit Easy Connect alias.
        try:
            ipaddress.ip_address(c.host)
        except ValueError:
            try:
                hostname = c.host.encode("idna").decode("ascii")
            except UnicodeError:
                raise ValueError("Oracle host must be a literal IP address or DNS hostname") from None
            if len(hostname) > 253 or not re.fullmatch(r"[A-Za-z0-9_.-]+", hostname):
                raise ValueError("Oracle host must be a literal IP address or DNS hostname")
        if not re.fullmatch(r"[A-Za-z0-9_.$#-]{1,512}", c.oracle_service_identifier):
            raise ValueError("Oracle Basic currently requires a plain service identifier")
        if any(ch in c.username for ch in "[]/@"):
            raise ValueError("Oracle proxy or external authentication is not implemented")
        if type(c.password) is not str or not c.password:
            raise ValueError("Oracle password authentication requires a password")
        if type(c.port) is not int or not 1 <= c.port <= 65535:
            raise ValueError("Oracle port must be between 1 and 65535")
        if type(c.connect_timeout) is not int or not 1 <= c.connect_timeout <= 60:
            raise ValueError("Oracle connection timeout must be between 1 and 60 seconds")
        if type(c.oracle_os_auth) is not bool or type(c.oracle_auto_connect) is not bool:
            raise ValueError("Oracle switches must be booleans")
        for switch, count in ((c.oracle_session_limit_enabled, c.oracle_session_limit),
                              (c.oracle_keepalive_enabled, c.oracle_keepalive_seconds)):
            if type(switch) is not bool or type(count) is not int or not 0 <= count <= 2147483647:
                raise ValueError("Oracle advanced numeric options are invalid")
        if type(c.oracle_custom_database_list_enabled) is not bool or type(c.oracle_custom_database_list) is not list:
            raise ValueError("Oracle database list is invalid")
        for entry in c.oracle_custom_database_list:
            if (type(entry) is not dict or set(entry) != {"active", "name", "auto_open"}
                    or type(entry["active"]) is not bool or type(entry["auto_open"]) is not bool
                    or type(entry["name"]) is not str):
                raise ValueError("Oracle database list row is invalid")
        return dict(user=c.username, password=c.password, host=c.host, port=c.port,
                    service_name=c.oracle_service_identifier, protocol="tcp", externalauth=False,
                    mode=0, retry_count=0, tcp_connect_timeout=c.connect_timeout,
                    # In-band Oracle breaks survive TCP forwarders which may
                    # drop urgent/OOB data. This is a public driver option, not
                    # a private socket/driver monkeypatch.
                    disable_oob=True)

    def connect(self, *, cancel_event=None):
        self._check_cancel(cancel_event=cancel_event)
        if self.connection is not None:
            if self._connected_config != self.config:
                raise DatabaseError("Oracle profile changed; close and reopen the connection before reading")
            return
        snapshot = deepcopy(self.config)
        options = self._connection_options(snapshot)  # Validate before driver/file/network operations.
        import oracledb
        if not oracledb.is_thin_mode():
            raise ValueError("Oracle Basic currently requires Thin mode; the process already uses Thick mode")
        connection = None
        try:
            # Explicit components, no DSN/alias/wallet/config directory and no
            # global enable_thin_mode()/defaults mutation.
            connection = oracledb.connect(**options)
            connection.call_timeout = snapshot.connect_timeout * 1000
            connection.autocommit = False
            connection.module = "OpenNavicat Oracle Basic"
            self._check_cancel(cancel_event=cancel_event)
            if self.config != snapshot:
                raise DatabaseError("Oracle profile changed while connecting; reopen the connection")
            with self._operation_lock:
                self.connection = connection
                if self._operation_token is not None:
                    self._operation_token.connection = connection
            # The authenticated owner is obtained from the server, not inferred
            # by uppercasing a potentially quoted username.
            with connection.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM DUAL")
                self._selected_schema = cursor.fetchone()[0]
            self._check_cancel(cancel_event=cancel_event)
            if self.config != snapshot:
                raise DatabaseError("Oracle profile changed while connecting; reopen the connection")
            self._connected_config = snapshot
        except BaseException as error:
            close_now = connection
            with self._operation_lock:
                if self._operation_token is not None:
                    self._operation_token.connection = None
                    self._operation_token.discard_connection = connection
                    close_now = None
                self.connection = None
                self._selected_schema = None
                self._connected_config = None
            if close_now is not None:
                with contextlib.suppress(Exception):
                    close_now.close()
            if isinstance(error, (DatabaseError, ValueError, KeyboardInterrupt, SystemExit)):
                raise
            raise _safe_error(error) from None

    def close(self):
        with self._operation_lock:
            if self._operation_token is not None:
                raise DatabaseError("Cancel and finish the Oracle operation before closing its connection")
            connection, self.connection = self.connection, None
            self._selected_schema = None
            self._connected_config = None
        if connection is not None:
            try:
                connection.close()
            except Exception as error:
                raise _safe_error(error) from None

    def _check_cancel(self, token=None, *, cancel_event=None):
        token = token or self._operation_token
        if ((cancel_event is not None and cancel_event.is_set()) or
                (token is not None and (token.cancelled.is_set() or
                 (token.external_cancel is not None and token.external_cancel.is_set())))):
            raise OracleError("Stopped by user", cancelled=True)

    @contextlib.contextmanager
    def _operation(self, cancel_event=None):
        timeout = self.config.connect_timeout
        token = _Operation(cancel_event, timeout_seconds=timeout if type(timeout) is int and 1 <= timeout <= 60 else 1)
        with self._operation_lock:
            if self._operation_token is not None:
                raise DatabaseError("Oracle session already has an active operation")
            self._operation_token = token
            self._active = True
        try:
            self._check_cancel(token)
            self.connect(cancel_event=cancel_event)
            token.connection = self.connection
            self._check_cancel(token)
            yield token
            with self._operation_lock:
                self._check_cancel(token)
                token.retiring = True
        except Exception as error:
            if isinstance(error, (DatabaseError, ValueError)):
                raise
            if token.cancelled.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise OracleError("Stopped by user", cancelled=True) from None
            raise _safe_error(error) from None
        finally:
            pending_error = sys.exc_info()[0] is not None
            health_failed = False
            with self._operation_lock:
                token.retiring = True
            # No network I/O is performed while holding the short marker lock.
            # The serial owner cannot retire this token/start a new operation
            # until every already accepted break for this token has returned.
            token.cancel_done.wait()
            discarded = token.discard_connection
            with self._operation_lock:
                try:
                    healthy = self.connection is None or self.connection.is_healthy()
                except Exception:
                    healthy = False
                    health_failed = True
                if not healthy:
                    discarded, self.connection = self.connection, None
                    self._selected_schema = None
                    self._connected_config = None
                    token.connection = None
                self._operation_token = None
                self._active = False
                token.finished.set()
            if discarded is not None:
                with contextlib.suppress(Exception):
                    discarded.close()
            if health_failed and not pending_error:
                raise OracleError("Oracle connection health could not be verified; connection discarded") from None

    def cancel_current(self, marker):
        """Run on the separate cancellation worker, never the GUI/owner queue.

        Repeat breaks until this token exits: one early break can arrive before
        execute dispatch and otherwise miss a statement. The lock serializes
        accepted break with token retirement, so a late sender cannot hit the
        next operation or a replacement connection. No network send holds the
        marker lock used by cached GUI reads.
        """
        with self._operation_lock:
            if marker is None or marker is not self._operation_token or marker.retiring:
                return False
            marker.cancelled.set()
            if marker.cancel_inflight:
                return True
            marker.cancel_inflight = True
            marker.cancel_done.clear()
        try:
            deadline = time.monotonic() + marker.timeout_seconds + 1
            while True:
                with self._operation_lock:
                    if marker is not self._operation_token or marker.retiring:
                        return True
                    connection = marker.connection
                    if connection is not self.connection:
                        connection = None
                if connection is not None:
                    try:
                        connection.cancel()
                    except Exception as error:
                        raise _safe_error(error) from None
                if marker.finished.wait(0.025) or time.monotonic() >= deadline:
                    return True
        finally:
            with self._operation_lock:
                marker.cancel_inflight = False
                marker.cancel_done.set()

    def cancel(self):
        self.cancel_current(self.current_operation_marker)

    def test_connection(self, *, cancel_event=None):
        with self._operation(cancel_event):
            self._read("SELECT 1 FROM DUAL", limit=1)
            version = self.connection.version
            return "Oracle " + (version if type(version) is str and re.fullmatch(r"[0-9.]{1,64}", version) else "(version unavailable)")

    @staticmethod
    def quote_identifier(name):
        if type(name) is not str or not name or "\x00" in name:
            raise ValueError("Oracle identifiers must be nonempty text without NUL")
        return '"' + name.replace('"', '""') + '"'

    def qualified_name(self, schema, table):
        return self.quote_identifier(schema) + "." + self.quote_identifier(table)

    @staticmethod
    def _validate_query(sql):
        if type(sql) is not str or not sql.strip():
            raise ValueError("Oracle query must be nonempty text")
        statements = [statement for statement in sqlparse.parse(sql) if str(statement).strip()]
        if len(statements) != 1 or statements[0].get_type() != "SELECT":
            raise DatabaseError("This Oracle milestone supports one SELECT/WITH query; DML, DDL and PL/SQL tools are not implemented")
        # This is a feature boundary, not a server-side read-only guarantee.
        return str(statements[0]).strip().removesuffix(";").rstrip()

    @staticmethod
    def _cell(value):
        if value is None or type(value) in {bool, int, float, Decimal, datetime, date, timedelta}:
            return value, len(str(value).encode("utf-8"))
        if type(value) in {str, bytes}:
            size = len(value.encode("utf-8")) if type(value) is str else len(value)
            if size > MAX_CELL_BYTES:
                raise DatabaseError("Oracle cell exceeds the supported delivery byte limit")
            return value, size
        # In particular, never hand a driver LOB, cursor or DbObject to Qt.
        raise DatabaseError("Oracle result contains an unsupported driver value; no driver objects were delivered")

    def _read(self, sql, params=None, *, limit=MAX_ROWS):
        if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
            raise ValueError("Oracle result limit must be between 1 and 10000")
        import oracledb
        started = time.perf_counter()
        with self.connection.cursor() as cursor:
            cursor.arraysize = min(128, limit + 1)
            cursor.prefetchrows = 0
            self._check_cancel()
            # Public per-statement conversion in the pinned driver; no global
            # defaults and no binary float or server VARCHAR intermediate for
            # NUMBER, including unconstrained and negative scales.
            cursor.execute(sql, params or {}, fetch_decimals=True)
            self._check_cancel()
            description = cursor.description or []
            allowed = {getattr(oracledb, name) for name in ("DB_TYPE_NUMBER", "DB_TYPE_VARCHAR", "DB_TYPE_NVARCHAR",
                "DB_TYPE_CHAR", "DB_TYPE_NCHAR", "DB_TYPE_RAW", "DB_TYPE_DATE", "DB_TYPE_TIMESTAMP",
                "DB_TYPE_TIMESTAMP_TZ", "DB_TYPE_TIMESTAMP_LTZ", "DB_TYPE_BINARY_FLOAT", "DB_TYPE_BINARY_DOUBLE",
                "DB_TYPE_INTERVAL_DS", "DB_TYPE_ROWID", "DB_TYPE_UROWID")}
            if any(column.type_code not in allowed for column in description):
                raise DatabaseError("Oracle LOB, LONG, object, JSON, vector and cursor results are not supported by this browser")
            rows, size = [], 0
            while len(rows) <= limit:
                self._check_cancel()
                row = cursor.fetchone()
                self._check_cancel()
                if row is None:
                    break
                values = []
                for value in row:
                    value, count = self._cell(value)
                    size += count
                    values.append(value)
                if size > MAX_BYTES:
                    raise DatabaseError("Oracle result exceeds the supported delivery byte limit")
                rows.append(values)
            return QueryResult([d.name for d in description], rows[:limit], len(rows[:limit]),
                               (time.perf_counter() - started) * 1000, len(rows) > limit, sql)

    def execute(self, sql, limit=1000, *, cancel_event=None):
        if type(limit) is not int or not 1 <= limit <= MAX_ROWS:
            raise ValueError("Oracle result limit must be between 1 and 10000")
        statement = self._validate_query(sql)
        with self._operation(cancel_event):
            result = self._read(statement, limit=limit)
            result.sql = sql  # Preserve the user's draft verbatim.
            return [result]

    def _query(self, sql, params=None):
        self._validate_query(sql)
        with self._operation():
            result = self._read(sql, params)
            if result.truncated:
                raise DatabaseError("Oracle metadata exceeds the supported row limit")
            return [tuple(row) for row in result.rows]

    def _metadata(self, sql, params=None):
        result = self._read(sql, params)
        if result.truncated:
            raise DatabaseError("Oracle metadata exceeds the supported row limit")
        return [dict(zip(result.columns, row)) for row in result.rows]

    def schemas(self, *, cancel_event=None):
        with self._operation(cancel_event):
            rows = self._metadata("SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
            return [row["USERNAME"] for row in rows]

    def use_schema(self, schema, *, cancel_event=None):
        self.quote_identifier(schema)
        with self._operation(cancel_event):
            if schema == self._selected_schema:
                return
            if self.in_transaction:
                raise DatabaseError("Commit or roll back before changing the Oracle schema")
            known = self._metadata("SELECT USERNAME FROM ALL_USERS WHERE USERNAME=:owner", {"owner": schema})
            if not known:
                raise DatabaseError("Oracle schema is not accessible")
            # Binding a schema is local browser state. All generated browser SQL
            # remains owner-qualified; no implicit ALTER SESSION or service edit.
            self._selected_schema = schema

    def _dictionary_fields(self, name):
        rows = self._metadata("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='SYS' AND TABLE_NAME=:name",
                              {"name": name})
        return {row["COLUMN_NAME"] for row in rows}

    def objects(self, schema, *, cancel_event=None):
        self.quote_identifier(schema)
        with self._operation(cancel_event):
            objects = self._metadata("""SELECT O.OBJECT_NAME, O.OBJECT_TYPE, O.STATUS, O.GENERATED,
                C.COMMENTS FROM ALL_OBJECTS O LEFT JOIN ALL_TAB_COMMENTS C
                ON C.OWNER=O.OWNER AND C.TABLE_NAME=O.OBJECT_NAME
                WHERE O.OWNER=:owner AND O.SUBOBJECT_NAME IS NULL
                AND O.OBJECT_TYPE IN ('TABLE','VIEW','MATERIALIZED VIEW','FUNCTION')
                ORDER BY O.OBJECT_TYPE,O.OBJECT_NAME""", {"owner": schema})
            all_tables = self._dictionary_fields("ALL_TABLES")
            all_views = self._dictionary_fields("ALL_VIEWS")
            table_columns = {v for v in TABLE_FIELDS.values() if v and v in all_tables}
            view_columns = {v for v in VIEW_FIELDS.values() if v in all_views}
            tables = self._metadata("SELECT TABLE_NAME" + ("," + ",".join(sorted(table_columns)) if table_columns else "") +
                                    " FROM ALL_TABLES WHERE OWNER=:owner", {"owner": schema})
            views = self._metadata("SELECT VIEW_NAME" + ("," + ",".join(sorted(view_columns)) if view_columns else "") +
                                   " FROM ALL_VIEWS WHERE OWNER=:owner", {"owner": schema})
            table_map = {row["TABLE_NAME"]: row for row in tables}
            view_map = {row["VIEW_NAME"]: row for row in views}
            # An Oracle materialized view has a backing TABLE entry. Present its
            # actual kind once rather than duplicating it as an ordinary table.
            mv_names = {r["OBJECT_NAME"] for r in objects if r["OBJECT_TYPE"] == "MATERIALIZED VIEW"}
            result = []
            for row in objects:
                name, raw_kind = row["OBJECT_NAME"], row["OBJECT_TYPE"]
                if raw_kind == "TABLE" and name in mv_names:
                    continue
                kind = {"TABLE": "table", "VIEW": "view", "MATERIALIZED VIEW": "materialized_view", "FUNCTION": "function"}[raw_kind]
                entry = {"schema": schema, "name": name, "kind": kind, "status": row["STATUS"],
                         "comment": row["COMMENTS"], "unavailable_fields": []}
                mapping, available, details = (TABLE_FIELDS, all_tables, table_map.get(name, {})) if kind == "table" else (VIEW_FIELDS, all_views, view_map.get(name, {})) if kind == "view" else ({}, set(), {})
                for key, source in mapping.items():
                    entry[key] = details.get(source) if source else None
                    if not source or source not in available or not details:
                        entry["unavailable_fields"].append(key)
                if kind == "table":
                    entry["generated"] = row["GENERATED"]
                    if "generated" in entry["unavailable_fields"]:
                        entry["unavailable_fields"].remove("generated")
                result.append(entry)
            return result

    def _columns(self, schema, table):
        # DATA_DEFAULT is LONG. Never fetch an unbounded LONG/LOB just to fill
        # the inspector. Newer servers expose bounded DATA_DEFAULT_VC; otherwise
        # report that field unavailable instead of claiming a NULL default.
        available = self._dictionary_fields("ALL_TAB_COLS")
        default = "C.DATA_DEFAULT_VC" if "DATA_DEFAULT_VC" in available else "CAST(NULL AS VARCHAR2(1))"
        rows = self._metadata("""SELECT C.COLUMN_NAME,C.DATA_TYPE,C.DATA_PRECISION,C.DATA_SCALE,
            C.DATA_LENGTH,C.CHAR_LENGTH,C.NULLABLE,""" + default + """ AS DATA_DEFAULT,C.VIRTUAL_COLUMN,C.IDENTITY_COLUMN,
            P.POSITION FROM ALL_TAB_COLS C LEFT JOIN
            (SELECT CC.OWNER,CC.TABLE_NAME,CC.COLUMN_NAME,CC.POSITION FROM ALL_CONSTRAINTS CO
             JOIN ALL_CONS_COLUMNS CC ON CC.OWNER=CO.OWNER AND CC.CONSTRAINT_NAME=CO.CONSTRAINT_NAME
             WHERE CO.CONSTRAINT_TYPE='P') P
            ON P.OWNER=C.OWNER AND P.TABLE_NAME=C.TABLE_NAME AND P.COLUMN_NAME=C.COLUMN_NAME
            WHERE C.OWNER=:owner AND C.TABLE_NAME=:name AND C.HIDDEN_COLUMN='NO' ORDER BY C.COLUMN_ID""",
            {"owner": schema, "name": table})
        return [{"name": r["COLUMN_NAME"], "type": r["DATA_TYPE"], "precision": r["DATA_PRECISION"],
                 "scale": r["DATA_SCALE"], "length": r["DATA_LENGTH"], "char_length": r["CHAR_LENGTH"],
                 "nullable": r["NULLABLE"] == "Y", "default": r["DATA_DEFAULT"],
                 "unavailable_fields": [] if "DATA_DEFAULT_VC" in available else ["default"],
                 "pk": r["POSITION"] is not None, "pk_order": int(r["POSITION"] or 0),
                 "generated": r["VIRTUAL_COLUMN"] == "YES", "identity": r["IDENTITY_COLUMN"] == "YES"} for r in rows]

    def columns(self, schema, table, *, cancel_event=None):
        self.qualified_name(schema, table)
        with self._operation(cancel_event):
            return self._columns(schema, table)

    def fetch_table(self, schema, table, offset=0, limit=200, order_by=None, descending=False,
                    filter_text="", *, cancel_event=None):
        if type(offset) is not int or not 0 <= offset <= 2147483647 or type(limit) is not int or not 1 <= limit <= MAX_ROWS:
            raise ValueError("Invalid Oracle page size or offset")
        if filter_text:
            raise DatabaseError("Oracle grid filters are not implemented")
        qualified = self.qualified_name(schema, table)
        with self._operation(cancel_event):
            columns = self._columns(schema, table)
            if not columns:
                raise DatabaseError("Oracle table or view has no accessible columns")
            names = [c["name"] for c in columns]
            if order_by is not None and order_by not in names:
                raise ValueError("Oracle sort column does not exist")
            sql = "SELECT " + ",".join(map(self.quote_identifier, names)) + " FROM " + qualified
            order = [order_by] if order_by else [c["name"] for c in sorted(columns, key=lambda c: c["pk_order"]) if c["pk"]]
            if order:
                sql += " ORDER BY " + ",".join(self.quote_identifier(n) + (" DESC" if descending else " ASC") for n in order)
            sql += " OFFSET :onv_offset ROWS FETCH NEXT :onv_count ROWS ONLY"
            return self._read(sql, {"onv_offset": offset, "onv_count": limit + 1}, limit=limit)

    def count_rows(self, schema, table, filter_text="", *, cancel_event=None):
        if filter_text:
            raise DatabaseError("Oracle grid filters are not implemented")
        sql = "SELECT COUNT(*) FROM " + self.qualified_name(schema, table)
        with self._operation(cancel_event):
            return int(self._read(sql, limit=1).rows[0][0])

    def _assert_write(self):
        raise DatabaseError("Oracle grid mutations, imports, comparison application and DDL tools are not implemented")

    def _cursor(self, *, stream=False):
        raise DatabaseError("Generic SQL-adapter cursors are not supported for Oracle; use its dedicated read APIs")

    def _run(self, cursor, sql, params=None):
        raise DatabaseError("Generic SQL-adapter commands are not supported for Oracle")

    def insert_row(self, schema, table, values):
        self._assert_write()

    def update_row(self, schema, table, original, changes):
        self._assert_write()

    def delete_row(self, schema, table, original):
        self._assert_write()

    def import_rows(self, schema, table, columns, rows):
        self._assert_write()

    def table_ddl(self, schema, table):
        raise DatabaseError("Oracle object designer and DDL extraction are not implemented")

    def indexes(self, schema, table):
        raise DatabaseError("Oracle index tools are not implemented")

    def foreign_keys(self, schema, table):
        raise DatabaseError("Oracle foreign-key tools are not implemented")

    def backup_to(self, path):
        raise DatabaseError("Oracle backup and Data Pump are not implemented")
