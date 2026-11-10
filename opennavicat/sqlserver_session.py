"""Pure Python TDS SQL Server session; all I/O belongs to its serial owner.

T-SQL batches are never split at semicolons. Browser/record operations bind to
the configured database even when an arbitrary SQL batch changes USE context.
The pinned python-tds transport is also used for send-only ATTENTION cancellation
so the query owner remains the sole reader of its connection.
"""
from __future__ import annotations

import contextlib
import ipaddress
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .database import ConnectionConfig, DatabaseError, DatabaseSession, QueryResult


class _LockedTlsSocket:
    """Serialize TLS BIO operations, never hold its lock across a socket read.

    The pytds TLS transport's recv and sendall both drain its outgoing BIO.
    An ATTENTION sender must not race that drain or share an SSL operation with
    the sole query reader. This connection-local wrapper preserves full duplex.
    """
    def __init__(self, encrypted):
        self._transport = encrypted._transport
        self._tls_conn = encrypted._tls_conn
        self._bio_lock = threading.RLock()

    def gettimeout(self):
        return self._transport.gettimeout()

    def settimeout(self, value):
        return self._transport.settimeout(value)

    def _flush(self):
        from OpenSSL import SSL
        while True:
            try:
                packet = self._tls_conn.bio_read(16384)
            except SSL.WantReadError:
                return
            if not packet:
                return
            self._transport.sendall(packet)

    def sendall(self, data, flags=0):
        with self._bio_lock:
            self._tls_conn.sendall(bytes(data))
            self._flush()

    def recv(self, size, flags=0):
        from OpenSSL import SSL
        while True:
            with self._bio_lock:
                self._flush()
                try:
                    return self._tls_conn.recv(size)
                except SSL.ZeroReturnError:
                    return b""
                except SSL.WantReadError:
                    pass
            # Only the owner reads the wire, while the cancellation sender may
            # acquire the BIO lock and send an ATTENTION during this wait.
            packet = self._transport.recv(16384)
            if not packet:
                return b""
            with self._bio_lock:
                self._tls_conn.bio_write(packet)

    def recv_into(self, buffer, size=0, flags=0):
        data = self.recv(size or len(buffer), flags)
        buffer[:len(data)] = data
        return len(data)

    def close(self):
        from OpenSSL import SSL
        try:
            with self._bio_lock:
                with contextlib.suppress(SSL.Error):
                    self._tls_conn.shutdown()
                    self._flush()
        finally:
            self._transport.close()


def validate_tds_hostname(cert, name: bytes) -> bool:
    """SAN-first hostname verification without removed pyOpenSSL X509 APIs.

    python-tds 1.17.1's verifier calls X509.get_extension, removed by the pinned
    pyOpenSSL 26.4. This stateless replacement keeps the original TDS hostname,
    uses cryptography's parsed SAN values and never changes chain verification.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    try:
        host = name.decode("ascii") if isinstance(name, bytes) else str(name)
        host = host.rstrip(".")
        cert = cert.to_cryptography() if hasattr(cert, "to_cryptography") else cert
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        try:
            alternative = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            alternative = None
        if address is not None:
            return alternative is not None and address in alternative.get_values_for_type(x509.IPAddress)
        if alternative is not None:
            names = alternative.get_values_for_type(x509.DNSName)
        else:
            names = [attribute.value for attribute in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
        host = host.encode("idna").decode("ascii").lower()
        for name in names:
            if "\x00" in name:
                continue
            name = name.rstrip(".").encode("idna").decode("ascii").lower()
            if "*" not in name and name == host:
                return True
            if name.startswith("*.") and name.count("*") == 1:
                target, pattern = host.split("."), name.split(".")
                if len(target) == len(pattern) and target[0] and target[1:] == pattern[1:]:
                    return True
        return False
    except (ValueError, UnicodeError):
        return False


def split_batches(sql: str) -> list[str]:
    """Recognize SQLCMD GO lines outside strings, identifiers and comments."""
    if not isinstance(sql, str):
        raise TypeError("SQL must be text")
    batches, lines = [], []
    state, depth = "normal", 0
    for line in sql.splitlines(keepends=True):
        visible, positions, index = [], [], 0
        while index < len(line):
            char = line[index]
            next_char = line[index + 1:index + 2]
            if state == "comment":
                if char == "/" and next_char == "*":
                    depth += 1
                    index += 2
                    continue
                if char == "*" and next_char == "/":
                    depth -= 1
                    index += 2
                    if not depth:
                        state = "normal"
                    continue
            elif state in {"'", '"', "]"}:
                visible.append("X")
                positions.append(index)
                if char == state:
                    if next_char == state:
                        index += 2
                        continue
                    state = "normal"
            elif char == "-" and next_char == "-":
                break
            elif char == "/" and next_char == "*":
                state, depth = "comment", 1
                index += 2
                continue
            elif char in {"'", '"', "["}:
                state = "]" if char == "[" else char
                visible.append(char)
                positions.append(index)
            else:
                visible.append(char)
                positions.append(index)
            index += 1
        match = re.fullmatch(r"\s*GO(?:\s+(\d+))?\s*", "".join(visible), re.I) if state == "normal" else None
        if match:
            count = int(match[1] or 1)
            if not 1 <= count <= 1000:
                raise ValueError("GO repetition must be between 1 and 1000")
            first = next(i for i, char in enumerate(visible) if not char.isspace())
            prefix = line[:positions[first]]
            if prefix.strip():
                # Retain a closing block comment that began in this batch.
                # Dropping the complete delimiter line would leave it unclosed.
                lines.append(prefix + "\n")
            batch = "".join(lines).strip()
            if batch:
                batches.extend([batch] * count)
            lines = []
        else:
            lines.append(line)
    if "".join(lines).strip():
        batches.append("".join(lines).strip())
    return batches


class SqlServerSession(DatabaseSession):
    def __init__(self, config: ConnectionConfig):
        if config.driver != "sqlserver":
            raise ValueError("SqlServerSession requires driver='sqlserver'")
        # Independent initialization also permits adapter tests before the root
        # factory/driver registry has been integrated into DatabaseSession.
        self.config = config
        self.connection = None
        self._cancel_requested = threading.Event()
        self._active = False
        self._selected_schema = None
        self._default_schema = None
        self._transaction_count = 0
        self._tunnel = None
        self._active_cursor = None
        self._writer_lock = threading.RLock()

    @property
    def placeholder(self):
        return "%s"

    @staticmethod
    def _escape_quoted_percent(sql):
        # python-tds formats parameter *names* with Python's percent operator
        # before binding their values. Bracket identifiers and T-SQL modulo
        # operators therefore need escaping too, not just string literals.
        output, state, depth, index = [], "normal", 0, 0
        while index < len(sql):
            char, following = sql[index], sql[index + 1:index + 2]
            if char == "%":
                if state == "normal" and following == "s":
                    output.append("%s")
                    index += 2
                    continue
                output.append("%%")
            else:
                output.append(char)
            if state == "line":
                if char == "\n":
                    state = "normal"
            elif state == "block":
                if char == "/" and following == "*":
                    depth += 1
                    output.append(following)
                    index += 1
                elif char == "*" and following == "/":
                    depth -= 1
                    output.append(following)
                    index += 1
                    if not depth:
                        state = "normal"
            elif state in {"'", '"', "]"}:
                if char == state:
                    if following == state:
                        output.append(following)
                        index += 1
                    else:
                        state = "normal"
            elif char in {"'", '"', "["}:
                state = "]" if char == "[" else char
            elif char == "-" and following == "-":
                output.append(following)
                index += 1
                state = "line"
            elif char == "/" and following == "*":
                output.append(following)
                index += 1
                state, depth = "block", 1
            index += 1
        return "".join(output)

    def quote_identifier(self, name):
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("SQL Server identifiers must be nonempty text without NUL")
        return "[" + name.replace("]", "]]") + "]"

    @property
    def _sys(self):
        return self.quote_identifier(self.config.database or "master") + ".sys"

    def qualified_name(self, schema, table):
        return ".".join(map(self.quote_identifier, (self.config.database or "master", schema, table)))

    def _connection_options(self):
        import certifi
        c = self.config
        if not isinstance(c.connect_timeout, int) or not 1 <= c.connect_timeout <= 31536000:
            raise ValueError("Connection timeout must be a positive number of seconds")
        if c.read_only:
            raise ValueError("SQL Server read-only connections cannot yet be enforced by this adapter")
        if not c.username.strip():
            raise ValueError("SQL Server SQL authentication requires a username")
        if not c.host.strip() or "\\" in c.host:
            raise ValueError("Use a SQL Server TCP hostname and explicit port, not a named instance")
        if c.ssl_mode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("SQL Server TLS mode must be disable, require, verify-ca or verify-full")
        if any((c.ssl_cert, c.ssl_key, c.ssl_crl, c.ssl_key_password)):
            raise ValueError("SQL Server TLS client certificates, private keys and CRLs are not supported")
        ca = None
        if c.ssl_mode != "disable":
            ca = str(Path(c.ssl_ca).expanduser().resolve()) if c.ssl_ca else certifi.where()
            # Validate trust material before opening a tunnel or a database socket.
            import pytds.tls
            pytds.tls.create_context(ca)
        return dict(dsn=c.host.encode("idna").decode("ascii"), port=c.port or 1433, database=c.database or "master", user=c.username,
            password=c.password, login_timeout=c.connect_timeout, timeout=None, autocommit=True,
            use_mars=False, pooling=False, use_sso=False, cafile=ca,
            validate_host=c.ssl_mode == "verify-full", enc_login_only=False,
            disable_connect_retry=True, appname="OpenNavicat")

    def connect(self):
        if self.connection is not None:
            return
        from .transport import SSHTunnel, SSHHostKeyError, SSHAuthenticationError
        import pytds
        import pytds.tls
        # A single pure verifier is safe across concurrent sessions; unlike a
        # per-connection monkeypatch it contains no mutable hostname/context.
        pytds.tls.validate_host = validate_tds_hostname
        options = self._connection_options()
        raw_socket = connection = None
        try:
            if self.config.ssh_enabled:
                self._tunnel = SSHTunnel(self.config, self.config.host, self.config.port or 1433)
                self._tunnel.start()
                raw_socket = socket.create_connection(self._tunnel.endpoint, self.config.connect_timeout)
                options["sock"] = raw_socket
            connection = pytds.connect(**options)
            self.connection = connection
            self._install_writer_guard(connection)
            self._default_schema = self._query("SELECT SCHEMA_NAME()")[0][0] or "dbo"
            self._selected_schema = self._default_schema
            self._sync_transaction_count()
        except Exception as error:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()
            elif raw_socket is not None:
                raw_socket.close()
            self.connection = None
            self._selected_schema = self._default_schema = None
            if self._tunnel is not None:
                self._tunnel.close()
                self._tunnel = None
            if isinstance(error, (SSHHostKeyError, SSHAuthenticationError)):
                raise
            number = getattr(error, "number", getattr(error, "msg_no", None))
            auth = number in {18456, 18486, 18487, 18488, 18452} or "login failed for user" in str(error).lower()
            message = str(error) or ("SQL Server connection timed out or was refused" if isinstance(error, TimeoutError) else type(error).__name__)
            raise DatabaseError(message, authentication_required=auth) from error

    def _install_writer_guard(self, connection):
        tds_socket = getattr(connection, "_tds_socket", None)
        session = getattr(tds_socket, "main_session", None)
        if session is None:
            return
        from pytds.tls import EncryptedSocket
        encrypted = getattr(tds_socket, "sock", None)
        if isinstance(encrypted, EncryptedSocket):
            locked = _LockedTlsSocket(encrypted)
            tds_socket.sock = locked
            session._reader._transport = locked
            session._writer._transport = locked
        original = session.querying_context
        @contextlib.contextmanager
        def guarded(packet_type):
            with self._writer_lock:
                if self._active and self._cancel_requested.is_set():
                    raise DatabaseError("Query cancelled before sending its next request")
                with original(packet_type):
                    yield
        session.querying_context = guarded

    def close(self):
        try:
            super().close()
        finally:
            self._default_schema = None
            self._transaction_count = 0
            self._active_cursor = None

    @property
    def in_transaction(self):
        if self.connection is None:
            return False
        # Read by the GUI: this property must perform absolutely no network I/O.
        tds_socket = getattr(self.connection, "_tds_socket", None)
        return bool(getattr(tds_socket, "tds72_transaction", self._transaction_count))

    def _sync_transaction_count(self):
        if self.connection is not None:
            self._transaction_count = int(self._query("SELECT @@TRANCOUNT")[0][0])

    def cancel(self):
        self._cancel_requested.set()
        if self.connection is None or not self._active:
            return
        from pytds import tds_base
        with self._writer_lock:
            cursor = self._active_cursor
            session = getattr(cursor, "_session", None) if cursor is not None else None
            if session is not None and session.state in {tds_base.TDS_PENDING, tds_base.TDS_READING} and not session.in_cancel:
                # Cursor.cancel also reads the response. Only the owner may do
                # that; this concurrent path sends the ATTENTION packet alone.
                session.put_cancel()

    def _cancel_owned(self, cursor):
        # Serialize any owner-side ATTENTION write with the concurrent sender.
        # Reading its acknowledgement here is safe: this is the query owner.
        with self._writer_lock:
            cursor.cancel()

    def execute(self, sql, limit=1000):
        if not isinstance(limit, int) or not 1 <= limit <= 100000:
            raise ValueError("Result limit must be between 1 and 100000")
        batches = split_batches(sql)
        self.connect()
        self._cancel_requested.clear()
        self._active = True
        results = []
        try:
            for batch in batches:
                if self._cancel_requested.is_set():
                    raise DatabaseError("Query cancelled", results)
                started = time.perf_counter()
                with self._cursor() as cursor:
                    self._active_cursor = cursor
                    try:
                        cursor.execute(batch)
                        while True:
                            if self._cancel_requested.is_set():
                                self._cancel_owned(cursor)
                                raise DatabaseError("Query cancelled", results)
                            columns = [column[0] for column in cursor.description] if cursor.description else []
                            rows = cursor.fetchmany(limit + 1) if columns else []
                            if self._cancel_requested.is_set():
                                self._cancel_owned(cursor)
                                raise DatabaseError("Query cancelled", results)
                            results.append(QueryResult(columns, [list(row) for row in rows[:limit]],
                                len(rows[:limit]) if columns else max(cursor.rowcount, 0),
                                (time.perf_counter() - started) * 1000, len(rows) > limit, batch))
                            if not cursor.nextset():
                                break
                    except Exception:
                        if self._cancel_requested.is_set():
                            with contextlib.suppress(Exception):
                                self._cancel_owned(cursor)
                        raise
                    finally:
                        self._active_cursor = None
            return results
        except Exception as error:
            if self._cancel_requested.is_set():
                raise DatabaseError("Query cancelled", results) from error
            if isinstance(error, DatabaseError):
                error.partial_results = error.partial_results or results
                raise
            raise DatabaseError(str(error), results) from error
        finally:
            self._active = False
            self._cancel_requested.clear()
            with contextlib.suppress(Exception):
                self._sync_transaction_count()

    def use_schema(self, schema):
        self.connect()
        if schema == self._default_schema:
            self._selected_schema = schema
            return
        if schema not in self.schemas():
            raise ValueError("SQL Server schema does not exist")
        raise DatabaseError("SQL Server cannot change a login's default schema for one session; use qualified [schema].[object] names")

    def schemas(self):
        return [row[0] for row in self._query(f"SELECT name FROM {self._sys}.schemas WHERE name NOT IN ('sys','INFORMATION_SCHEMA') ORDER BY name")]

    def objects(self, schema):
        rows = self._query(f"""SELECT o.name,o.type FROM {self._sys}.objects o
            JOIN {self._sys}.schemas s ON s.schema_id=o.schema_id
            WHERE s.name=%s AND o.type IN ('U','V') AND o.is_ms_shipped=0 ORDER BY o.name""", [schema])
        return [{"name": row[0], "kind": "view" if row[1].strip() == "V" else "table", "schema": schema} for row in rows]

    def columns(self, schema, table):
        rows = self._query(f"""SELECT c.name,t.name,c.max_length,c.precision,c.scale,c.is_nullable,
                dc.definition,c.is_computed,cc.definition,c.is_identity,c.generated_always_type,
                COALESCE(pk.key_ordinal,0),CONVERT(nvarchar(100),ic.seed_value),CONVERT(nvarchar(100),ic.increment_value),
                t.is_user_defined,ts.name
            FROM {self._sys}.columns c JOIN {self._sys}.objects o ON o.object_id=c.object_id
            JOIN {self._sys}.schemas s ON s.schema_id=o.schema_id
            JOIN {self._sys}.types t ON t.user_type_id=c.user_type_id
            JOIN {self._sys}.schemas ts ON ts.schema_id=t.schema_id
            LEFT JOIN {self._sys}.default_constraints dc ON dc.object_id=c.default_object_id
            LEFT JOIN {self._sys}.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
            LEFT JOIN {self._sys}.identity_columns ic ON ic.object_id=c.object_id AND ic.column_id=c.column_id
            LEFT JOIN (SELECT i.object_id,ix.column_id,ix.key_ordinal FROM {self._sys}.indexes i
                JOIN {self._sys}.index_columns ix ON ix.object_id=i.object_id AND ix.index_id=i.index_id
                WHERE i.is_primary_key=1) pk ON pk.object_id=c.object_id AND pk.column_id=c.column_id
            WHERE s.name=%s AND o.name=%s AND o.type IN ('U','V') AND c.is_hidden=0 ORDER BY c.column_id""", [schema, table])
        result = []
        for row in rows:
            name, typ, length, precision, scale = row[:5]
            if row[14]:
                rendered = self.quote_identifier(row[15]) + "." + self.quote_identifier(typ)
            elif typ in {"varchar", "char", "varbinary", "binary", "nvarchar", "nchar"}:
                size = "max" if length == -1 else str(length // 2 if typ in {"nvarchar", "nchar"} else length)
                rendered = f"{typ}({size})"
            elif typ in {"decimal", "numeric"}:
                rendered = f"{typ}({precision},{scale})"
            elif typ in {"datetime2", "datetimeoffset", "time"}:
                rendered = f"{typ}({scale})"
            else:
                rendered = typ
            result.append({"name": name, "type": rendered, "nullable": bool(row[5]), "default": row[6],
                "generated": bool(row[7] or row[10] or typ in {"timestamp", "rowversion"}),
                "computed": bool(row[7]), "computed_expression": row[8], "identity": bool(row[9]),
                "identity_generation": "ALWAYS" if row[9] else "", "generated_always_type": row[10],
                "pk": bool(row[11]), "pk_order": row[11], "identity_seed": row[12], "identity_increment": row[13]})
        return result

    def indexes(self, schema, table):
        rows = self._query(f"""SELECT i.name,i.is_unique,i.is_primary_key,i.is_unique_constraint,i.type_desc,
                i.filter_definition,c.name,ix.key_ordinal,ix.is_descending_key,ix.is_included_column
            FROM {self._sys}.indexes i JOIN {self._sys}.objects o ON o.object_id=i.object_id
            JOIN {self._sys}.schemas s ON s.schema_id=o.schema_id
            LEFT JOIN {self._sys}.index_columns ix ON ix.object_id=i.object_id AND ix.index_id=i.index_id
            LEFT JOIN {self._sys}.columns c ON c.object_id=ix.object_id AND c.column_id=ix.column_id
            WHERE s.name=%s AND o.name=%s AND i.index_id>0 AND i.name IS NOT NULL AND i.is_hypothetical=0
            ORDER BY i.is_primary_key DESC,i.name,ix.is_included_column,ix.key_ordinal,ix.index_column_id""", [schema, table])
        grouped = {}
        for row in rows:
            entry = grouped.setdefault(row[0], {"name": row[0], "unique": bool(row[1]), "primary": bool(row[2]),
                "constraint": bool(row[3]), "type": row[4], "where": row[5] or "", "columns": [], "included": [], "keys": []})
            if row[6] is not None:
                if row[9]:
                    entry["included"].append(row[6])
                elif row[7]:
                    entry["columns"].append(row[6])
                    entry["keys"].append(self.quote_identifier(row[6]) + (" DESC" if row[8] else " ASC"))
        for entry in grouped.values():
            keys = ", ".join(entry["keys"])
            if entry["type"] not in {"CLUSTERED", "NONCLUSTERED"}:
                entry["definition"] = "-- SQL Server " + entry["type"] + " index metadata; full DDL is not reconstructed."
            elif entry["primary"] or entry["constraint"]:
                entry["definition"] = "CONSTRAINT " + self.quote_identifier(entry["name"]) + (" PRIMARY KEY " if entry["primary"] else " UNIQUE ") + entry["type"] + " (" + keys + ")"
            else:
                entry["definition"] = "CREATE " + ("UNIQUE " if entry["unique"] else "") + entry["type"] + " INDEX " + self.quote_identifier(entry["name"]) + " ON " + self.qualified_name(schema, table) + " (" + keys + ")"
                if entry["included"]:
                    entry["definition"] += " INCLUDE (" + ", ".join(map(self.quote_identifier, entry["included"])) + ")"
                if entry["where"]:
                    entry["definition"] += " WHERE " + entry["where"]
        return list(grouped.values())

    def foreign_keys(self, schema, table):
        rows = self._query(f"""SELECT fk.name,pc.name,rs.name,rt.name,rc.name,
                fk.delete_referential_action_desc,fk.update_referential_action_desc,fk.is_disabled,fk.is_not_trusted
            FROM {self._sys}.foreign_keys fk JOIN {self._sys}.foreign_key_columns fc ON fc.constraint_object_id=fk.object_id
            JOIN {self._sys}.objects pt ON pt.object_id=fk.parent_object_id JOIN {self._sys}.schemas ps ON ps.schema_id=pt.schema_id
            JOIN {self._sys}.objects rt ON rt.object_id=fk.referenced_object_id JOIN {self._sys}.schemas rs ON rs.schema_id=rt.schema_id
            JOIN {self._sys}.columns pc ON pc.object_id=pt.object_id AND pc.column_id=fc.parent_column_id
            JOIN {self._sys}.columns rc ON rc.object_id=rt.object_id AND rc.column_id=fc.referenced_column_id
            WHERE ps.name=%s AND pt.name=%s ORDER BY fk.name,fc.constraint_column_id""", [schema, table])
        grouped = {}
        for row in rows:
            entry = grouped.setdefault(row[0], {"name": row[0], "columns": [], "referenced_schema": row[2],
                "referenced_table": row[3], "referenced_columns": [], "on_delete": row[5].replace("_", " "),
                "on_update": row[6].replace("_", " "), "disabled": bool(row[7]), "not_trusted": bool(row[8])})
            entry["columns"].append(row[1])
            entry["referenced_columns"].append(row[4])
        return list(grouped.values())

    def table_ddl(self, schema, table):
        rows = self._query(f"""SELECT o.type,m.definition FROM {self._sys}.objects o
            JOIN {self._sys}.schemas s ON s.schema_id=o.schema_id
            LEFT JOIN {self._sys}.sql_modules m ON m.object_id=o.object_id
            WHERE s.name=%s AND o.name=%s AND o.type IN ('U','V')""", [schema, table])
        if not rows:
            return ""
        if rows[0][0].strip() == "V":
            return rows[0][1] or "-- SQL Server view definition is encrypted or not visible to this login."
        definitions = []
        for column in self.columns(schema, table):
            item = self.quote_identifier(column["name"])
            if column["computed"]:
                item += " AS " + column["computed_expression"]
            else:
                item += " " + column["type"]
                if column["identity"]:
                    item += f" IDENTITY({column['identity_seed']},{column['identity_increment']})"
                if column["default"] is not None:
                    item += " DEFAULT " + column["default"]
                item += " NULL" if column["nullable"] else " NOT NULL"
            definitions.append("  " + item)
        definitions.extend("  " + entry["definition"] for entry in self.indexes(schema, table) if entry["primary"] or entry["constraint"])
        checks = self._query(f"""SELECT ck.name,ck.definition FROM {self._sys}.check_constraints ck
            JOIN {self._sys}.objects o ON o.object_id=ck.parent_object_id JOIN {self._sys}.schemas s ON s.schema_id=o.schema_id
            WHERE s.name=%s AND o.name=%s ORDER BY ck.name""", [schema, table])
        definitions.extend("  CONSTRAINT " + self.quote_identifier(name) + " CHECK " + expression for name, expression in checks)
        for fk in self.foreign_keys(schema, table):
            definitions.append("  CONSTRAINT " + self.quote_identifier(fk["name"]) + " FOREIGN KEY (" + ", ".join(map(self.quote_identifier, fk["columns"])) + ") REFERENCES " +
                self.qualified_name(fk["referenced_schema"], fk["referenced_table"]) + " (" + ", ".join(map(self.quote_identifier, fk["referenced_columns"])) + ") ON DELETE " + fk["on_delete"] + " ON UPDATE " + fk["on_update"])
        return ("-- Reconstructed SQL Server table metadata, not a lossless backup. Standalone indexes, triggers,\n"
            "-- constraint trust/disabled state, computed persistence, temporal/storage/security properties are not included.\n" +
            "CREATE TABLE " + self.qualified_name(schema, table) + " (\n" + ",\n".join(definitions) + "\n);")

    def _filter(self, schema, table, text):
        if not text:
            return "", []
        columns = self.columns(schema, table)
        if not columns:
            raise DatabaseError("Table or view has no visible columns")
        escaped = text.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")
        terms = []
        for column in columns:
            name = self.quote_identifier(column["name"])
            binary = column["type"].split("(", 1)[0].lower() in {"binary", "varbinary", "image", "timestamp", "rowversion"}
            rendered = "CONVERT(varchar(max),CONVERT(varbinary(max)," + name + "),2)" if binary else "TRY_CONVERT(nvarchar(max)," + name + ")"
            terms.append(rendered + " LIKE %s ESCAPE '!'")
        return " WHERE (" + " OR ".join(terms) + ")", ["%" + escaped + "%"] * len(terms)

    def fetch_table(self, schema, table, offset=0, limit=200, order_by=None, descending=False, filter_text=""):
        if offset < 0 or not 1 <= limit <= 100000:
            raise ValueError("Invalid page size or offset")
        columns = self.columns(schema, table)
        names = [column["name"] for column in columns]
        if not names or (order_by and order_by not in names):
            raise ValueError("Table has no visible columns or the sort column does not exist")
        where, params = self._filter(schema, table, filter_text)
        primary = [column["name"] for column in sorted(columns, key=lambda item: item["pk_order"]) if column["pk"]]
        order = ([order_by] if order_by else []) + [name for name in primary if name != order_by]
        ordering = ", ".join(self.quote_identifier(name) + (" DESC" if name == order_by and descending else " ASC") for name in order) or "(SELECT NULL)"
        sql = "SELECT " + ", ".join(map(self.quote_identifier, names)) + " FROM " + self.qualified_name(schema, table) + where + " ORDER BY " + ordering + " OFFSET %s ROWS FETCH NEXT %s ROWS ONLY"
        started = time.perf_counter()
        with self._cursor() as cursor:
            self._run(cursor, sql, params + [offset, limit + 1])
            rows = cursor.fetchmany(limit + 1)
            return QueryResult(names, [list(row) for row in rows[:limit]], len(rows[:limit]),
                (time.perf_counter() - started) * 1000, len(rows) > limit, sql)

    def count_rows(self, schema, table, filter_text=""):
        where, params = self._filter(schema, table, filter_text)
        return int(self._query("SELECT COUNT_BIG(*) FROM " + self.qualified_name(schema, table) + where, params)[0][0])

    @contextlib.contextmanager
    def _atomic(self):
        self._assert_write()
        self.connect()
        existing = self.in_transaction
        savepoint = "onv_" + uuid.uuid4().hex[:24]
        if existing:
            if self._query("SELECT XACT_STATE()")[0][0] != 1:
                raise DatabaseError("The current SQL Server transaction cannot commit; ROLLBACK before editing records")
            self._query("SAVE TRANSACTION " + self.quote_identifier(savepoint))
        else:
            self._query("BEGIN TRANSACTION")
        try:
            yield
            if not existing:
                self._query("COMMIT TRANSACTION")
        except BaseException as original:
            try:
                state = int(self._query("SELECT XACT_STATE()")[0][0])
                if existing:
                    if state == 1:
                        self._query("ROLLBACK TRANSACTION " + self.quote_identifier(savepoint))
                    elif state == -1:
                        raise DatabaseError("SQL Server left the outer transaction uncommittable; explicitly ROLLBACK before continuing") from original
                elif state:
                    self._query("ROLLBACK TRANSACTION")
            except DatabaseError:
                raise
            except Exception as rollback_error:
                raise DatabaseError("SQL Server rollback could not be confirmed; close and reconnect before editing") from rollback_error
            raise
        finally:
            with contextlib.suppress(Exception):
                self._sync_transaction_count()

    def _validate_names(self, schema, table, values):
        columns = super()._validate_names(schema, table, values)
        if any(column.get("identity") and column["name"] in values for column in columns):
            raise ValueError("SQL Server identity columns cannot be written by the record editor")
        return columns

    def _adapt_values(self, columns, values):
        import pytds
        return [pytds.Binary(bytes(value)) if isinstance(value, (bytes, bytearray, memoryview)) else value for value in values.values()]

    def _locked_original(self, schema, table, original):
        columns = self.columns(schema, table)
        primary = [column["name"] for column in columns if column["pk"]]
        names = [column["name"] for column in columns]
        if not primary or any(name not in original or original[name] is None for name in primary):
            raise DatabaseError("Editing requires every non-null primary-key value")
        if any(name not in original for name in names):
            raise DatabaseError("The original row must include every visible column for conflict checking")
        where = " AND ".join(self.quote_identifier(name) + " = %s" for name in primary)
        params = self._adapt_values(columns, {name: original[name] for name in primary})
        sql = "SELECT " + ", ".join(map(self.quote_identifier, names)) + " FROM " + self.qualified_name(schema, table) + " WITH (UPDLOCK,HOLDLOCK) WHERE " + where
        rows = self._query(sql, params)
        if len(rows) != 1 or any(not self._same_value(value, original[name]) for name, value in zip(names, rows[0])):
            raise DatabaseError("The row changed or was removed; refresh before editing")
        return where, params

    def _affected_dml(self, sql, params):
        marker = "__onv_affected_" + uuid.uuid4().hex
        count = None
        with self._cursor() as cursor:
            self._run(cursor, sql + "; SELECT @@ROWCOUNT AS " + self.quote_identifier(marker), params)
            while True:
                if cursor.description and [column[0] for column in cursor.description] == [marker]:
                    count = int(cursor.fetchone()[0])
                if not cursor.nextset():
                    break
        if count != 1:
            raise DatabaseError("Record modification did not affect exactly one row")
        return count

    def update_row(self, schema, table, original, changes):
        if not changes:
            return 0
        columns = self._validate_names(schema, table, changes)
        try:
            with self._atomic():
                where, params = self._locked_original(schema, table, original)
                if all(self._same_value(original.get(name), value) for name, value in changes.items()):
                    return 1
                sql = "UPDATE " + self.qualified_name(schema, table) + " SET " + ", ".join(self.quote_identifier(name) + " = %s" for name in changes) + " WHERE " + where
                return self._affected_dml(sql, self._adapt_values(columns, changes) + params)
        except Exception as error:
            if isinstance(error, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(error)) from error

    def delete_row(self, schema, table, original):
        try:
            with self._atomic():
                where, params = self._locked_original(schema, table, original)
                return self._affected_dml("DELETE FROM " + self.qualified_name(schema, table) + " WHERE " + where, params)
        except Exception as error:
            if isinstance(error, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(error)) from error

    def insert_row(self, schema, table, values):
        columns = self._validate_names(schema, table, values)
        sql = "INSERT INTO " + self.qualified_name(schema, table)
        sql += " (" + ", ".join(map(self.quote_identifier, values)) + ") VALUES (" + ", ".join(["%s"] * len(values)) + ")" if values else " DEFAULT VALUES"
        try:
            with self._atomic():
                return self._affected_dml(sql, self._adapt_values(columns, values))
        except Exception as error:
            if isinstance(error, (DatabaseError, ValueError)):
                raise
            raise DatabaseError(str(error)) from error


SQLServerSession = SqlServerSession
