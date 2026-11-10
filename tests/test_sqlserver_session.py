"""Pinned TDS contracts and opt-in owned SQL Server integration.

Set OPENNAVICAT_TEST_SQLSERVER_ENDPOINT to the ignored fixture JSON path. Tests
create a unique schema, use no existing tables, and drop only that schema.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace
from decimal import Decimal
import json
import ipaddress
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.sqlserver_session import SqlServerSession, split_batches, validate_tds_hostname, _LockedTlsSocket


def profile(**changes):
    return replace(ConnectionConfig("fixture", "Owned SQL Server", driver="sqlserver", database="owned",
        host="database.example.invalid", port=1433, username="fixture_login", password="fixture-only", ssl_mode="disable"), **changes)


@pytest.mark.parametrize("sql,expected", [
    ("BEGIN SELECT 1; SELECT 2; END", ["BEGIN SELECT 1; SELECT 2; END"]),
    ("SELECT 1;\nGO\nSELECT 2", ["SELECT 1;", "SELECT 2"]),
    ("SELECT 'GO'\nGO -- end\nSELECT 2", ["SELECT 'GO'", "SELECT 2"]),
    ("SELECT 'a\nGO\nb';\nGO\nSELECT 2", ["SELECT 'a\nGO\nb';", "SELECT 2"]),
    ("SELECT 'a\n'GO\nSELECT 2", ["SELECT 'a\n'GO\nSELECT 2"]),
    ("SELECT [a\n]GO\nSELECT 2", ["SELECT [a\n]GO\nSELECT 2"]),
    ("/* outer\n/* nested */\nGO\n*/ SELECT 1\nGO\nSELECT 2", ["/* outer\n/* nested */\nGO\n*/ SELECT 1", "SELECT 2"]),
    ("SELECT 1\ngo 2\nSELECT 3", ["SELECT 1", "SELECT 1", "SELECT 3"]),
    ("SELECT 1 /* note\n*/ GO\nSELECT 2", ["SELECT 1 /* note\n*/", "SELECT 2"]),
])
def test_go_lexer_preserves_tsql_blocks_quotes_and_nested_comments(sql, expected):
    assert split_batches(sql) == expected


@pytest.mark.parametrize("config", [
    profile(ssl_mode="prefer"), profile(ssl_mode="allow"), profile(read_only=True),
    profile(username=""), profile(ssl_cert="client-cert"), profile(ssl_key="client-key"),
    profile(ssl_crl="crl"), profile(ssl_key_password="key-secret"), profile(host="server\\instance"),
])
def test_unsupported_security_options_fail_before_connecting(monkeypatch, config):
    calls = []
    monkeypatch.setattr("pytds.connect", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError):
        SqlServerSession(config).connect()
    assert calls == []


@pytest.mark.parametrize("mode,hostname", [("require", False), ("verify-ca", False), ("verify-full", True)])
def test_tls_options_always_verify_chain_and_only_full_verifies_original_host(mode, hostname):
    import certifi
    options = SqlServerSession(profile(ssl_mode=mode))._connection_options()
    assert options["cafile"] == certifi.where()
    assert options["validate_host"] is hostname
    assert options["enc_login_only"] is False
    assert options["autocommit"] is True and options["use_sso"] is False
    assert options["dsn"] == "database.example.invalid"


class _MetadataCursor:
    description = [("value",)]
    def execute(self, sql, params=None):
        self.rows = [("dbo",)] if "SCHEMA_NAME()" in sql else [(0,)]
    def fetchall(self):
        return self.rows
    def close(self):
        pass


class _Connection:
    def __init__(self):
        @contextlib.contextmanager
        def querying_context(packet_type):
            yield
        self._tds_socket = SimpleNamespace(tds72_transaction=0, main_session=SimpleNamespace(querying_context=querying_context))
        self.closed = False
    def cursor(self):
        return _MetadataCursor()
    def close(self):
        self.closed = True


def test_ssh_supplied_socket_retains_original_hostname_and_closes_tunnel(monkeypatch):
    options, calls = [], []
    connection = _Connection()
    monkeypatch.setattr("pytds.connect", lambda **kwargs: options.append(kwargs) or connection)
    raw_socket = SimpleNamespace(close=lambda: calls.append("socket close"))
    monkeypatch.setattr("opennavicat.sqlserver_session.socket.create_connection", lambda endpoint, timeout: calls.append(endpoint) or raw_socket)
    class Tunnel:
        def __init__(self, config, host, port):
            assert host == "database.example.invalid" and port == 1433
        def start(self):
            calls.append("tunnel start")
        @property
        def endpoint(self):
            return ("127.0.0.1", 43210)
        def close(self):
            calls.append("tunnel close")
    monkeypatch.setattr("opennavicat.transport.SSHTunnel", Tunnel)
    session = SqlServerSession(profile(ssh_enabled=True, ssl_mode="verify-full"))
    session.connect()
    assert options[0]["sock"] is raw_socket and options[0]["dsn"] == "database.example.invalid"
    assert options[0]["validate_host"] is True
    assert session._selected_schema == "dbo"
    monkeypatch.setattr(session, "_query", lambda *_args: pytest.fail("GUI transaction property must not perform I/O"))
    connection._tds_socket.tds72_transaction = 42
    assert session.in_transaction
    session.close()
    assert connection.closed and calls[-1] == "tunnel close" and not session.in_transaction


def test_concurrent_cancel_is_send_only_and_owner_remains_the_reader():
    from pytds import tds_base
    calls = []
    protocol = SimpleNamespace(state=tds_base.TDS_PENDING, in_cancel=False, put_cancel=lambda: calls.append("attention"))
    session = SqlServerSession(profile())
    session.connection = object()
    session._active = True
    session._active_cursor = SimpleNamespace(_session=protocol, cancel=lambda: pytest.fail("concurrent cancel must not read"))
    session.cancel()
    assert calls == ["attention"] and session._cancel_requested.is_set()


def test_cancel_before_the_first_packet_is_not_lost_while_session_is_idle():
    session = SqlServerSession(profile())
    connection = _Connection()
    session._install_writer_guard(connection)
    session.connection = connection
    session._active = True
    session._cancel_requested.set()
    with pytest.raises(DatabaseError, match="cancelled before sending"):
        with connection._tds_socket.main_session.querying_context(1):
            pytest.fail("cancelled request must not start writing a TDS batch")


def test_authentication_error_uses_tds_code_instead_of_english_message(monkeypatch):
    class LocalizedError(Exception):
        msg_no = 18456
    def reject(**kwargs):
        raise LocalizedError("测试登录被拒绝")
    monkeypatch.setattr("pytds.connect", reject)
    with pytest.raises(DatabaseError) as caught:
        SqlServerSession(profile()).connect()
    assert caught.value.authentication_required


def test_tds_percent_substitution_preserves_brackets_literals_comments_and_modulo():
    sql = "SELECT [a%s]]x],N'100%', 7 % 2, %s -- %s is a comment\n/* 10% */"
    escaped = SqlServerSession._escape_quoted_percent(sql)
    assert escaped % ("@P1",) == "SELECT [a%s]]x],N'100%', 7 % 2, @P1 -- %s is a comment\n/* 10% */"


def test_hostname_verifier_prioritizes_san_and_distinguishes_ip_dns_and_wildcards():
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    def certificate(cn, alternatives=None):
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1)))
        if alternatives is not None:
            builder = builder.add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        return builder.sign(key, hashes.SHA256())
    assert not validate_tds_hostname(certificate("good.example", [x509.DNSName("other.example")]), b"good.example")
    wildcard = certificate("unused", [x509.DNSName("*.example.test")])
    assert validate_tds_hostname(wildcard, b"one.example.test")
    assert not validate_tds_hostname(wildcard, b"two.one.example.test")
    ip = certificate("unused", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
    assert validate_tds_hostname(ip, b"127.0.0.1")
    assert not validate_tds_hostname(certificate("127.0.0.1"), b"127.0.0.1")
    assert validate_tds_hostname(certificate("Legacy.Example"), b"legacy.example")


def test_tls_sender_and_receiver_cannot_steal_each_others_outgoing_bio_records():
    from OpenSSL import SSL
    ready = threading.Event()
    class TLS:
        outgoing = b""
        def sendall(self, data):
            self.outgoing += data
            ready.set()
            time.sleep(0.04)  # place the reader between SSL_write and BIO drain.
        def bio_read(self, size):
            if not self.outgoing:
                raise SSL.WantReadError()
            packet, self.outgoing = self.outgoing[:size], self.outgoing[size:]
            return packet
        def recv(self, size):
            raise SSL.WantReadError()
    sent, errors, received = [], [], []
    transport = SimpleNamespace(sendall=sent.append, recv=lambda _size: b"")
    secured = _LockedTlsSocket(SimpleNamespace(_transport=transport, _tls_conn=TLS()))
    payload = b"owned attention/data" * 4000
    def send():
        try:
            secured.sendall(payload)
        except Exception as error:
            errors.append(error)
    sender = threading.Thread(target=send)
    sender.start()
    assert ready.wait(2)
    receiver = threading.Thread(target=lambda: received.append(secured.recv(1)))
    receiver.start()
    sender.join(2)
    receiver.join(2)
    assert not errors and received == [b""]
    assert b"".join(sent) == payload


@pytest.fixture
def sqlserver():
    filename = os.environ.get("OPENNAVICAT_TEST_SQLSERVER_ENDPOINT")
    if not filename:
        if os.environ.get("OPENNAVICAT_TEST_SQLSERVER") == "1":
            pytest.fail("SQL Server integration was required but its owned endpoint was not prepared")
        pytest.skip("Set an owned SQL Server endpoint JSON path for real TDS integration")
    endpoint = json.loads(Path(filename).read_text())
    config = profile(host=endpoint["host"], port=int(endpoint["port"]), database=endpoint["database"],
        username=endpoint["username"], password=endpoint["password"], ssl_mode=endpoint.get("ssl_mode", "disable"),
        ssl_ca=endpoint.get("ssl_ca", ""))
    session = SqlServerSession(config)
    session.connect()
    schema = "onv_sql_" + uuid.uuid4().hex[:12]
    session.execute("CREATE SCHEMA " + session.quote_identifier(schema))
    try:
        yield session, schema
    finally:
        if session.in_transaction:
            session.execute("ROLLBACK TRANSACTION")
        session.execute("USE " + session.quote_identifier(config.database))
        # Every object below belongs to the unique fixture schema only.
        for name, owner in session._query(f"""SELECT fk.name,o.name FROM {session._sys}.foreign_keys fk
                JOIN {session._sys}.objects o ON o.object_id=fk.parent_object_id
                JOIN {session._sys}.schemas s ON s.schema_id=o.schema_id WHERE s.name=%s""", [schema]):
            session.execute("ALTER TABLE " + session.qualified_name(schema, owner) + " DROP CONSTRAINT " + session.quote_identifier(name))
        for kind in ("V", "P", "U"):
            entries = session._query(f"SELECT o.name FROM {session._sys}.objects o JOIN {session._sys}.schemas s ON s.schema_id=o.schema_id WHERE s.name=%s AND o.type=%s", [schema, kind])
            for (name,) in entries:
                target = session.qualified_name(schema, name) if kind == "U" else session.quote_identifier(schema) + "." + session.quote_identifier(name)
                session.execute("DROP " + {"V": "VIEW", "P": "PROCEDURE", "U": "TABLE"}[kind] + " " + target)
        session.execute("DROP SCHEMA " + session.quote_identifier(schema))
        session.close()


def test_real_tsql_batch_local_variables_procedure_go_and_multiple_results(sqlserver):
    session, schema = sqlserver
    sql = "DECLARE @value int=7; BEGIN SELECT @value AS first_value; SELECT @value+1 AS second_value; END;"
    results = [result for result in session.execute(sql) if result.columns]
    assert [result.rows for result in results] == [[[7]], [[8]]]
    procedure = session.quote_identifier(schema) + "." + session.quote_identifier("batch_probe")
    results = session.execute(f"CREATE PROCEDURE {procedure} AS BEGIN SELECT 21 AS a; SELECT 22 AS b; END\nGO\nEXEC {procedure}")
    assert [result.rows for result in results if result.columns] == [[[21]], [[22]]]
    result = next(result for result in session.execute("SELECT TOP(10) object_id FROM sys.objects", limit=2) if result.columns)
    assert len(result.rows) == 2 and result.truncated


def create_records(session, schema):
    table = session.qualified_name(schema, "records")
    session.execute(f"CREATE TABLE {table}(id int IDENTITY(5,2) PRIMARY KEY,label nvarchar(80) NOT NULL UNIQUE,amount decimal(12,2) DEFAULT 1.25,payload varbinary(max),label_size AS LEN(label),revision rowversion)")
    return table


def test_real_metadata_identity_generated_types_indexes_fk_and_partial_ddl(sqlserver):
    session, schema = sqlserver
    table = create_records(session, schema)
    child = session.qualified_name(schema, "children")
    session.execute(f"CREATE TABLE {child}(id int PRIMARY KEY, parent_id int,CONSTRAINT fk_parent FOREIGN KEY(parent_id) REFERENCES {table}(id) ON DELETE CASCADE)")
    session.execute(f"CREATE INDEX ix_amount ON {table}(amount DESC) INCLUDE(label) WHERE amount>0")
    view = session.quote_identifier(schema) + "." + session.quote_identifier("records_view")
    session.execute(f"CREATE VIEW {view} AS SELECT id,label FROM {table}")
    assert schema in session.schemas()
    assert {item["kind"] for item in session.objects(schema)} == {"table", "view"}
    columns = {column["name"]: column for column in session.columns(schema, "records")}
    assert columns["id"]["identity"] and columns["id"]["identity_generation"] == "ALWAYS"
    assert columns["id"]["pk"] and columns["id"]["identity_seed"] == "5"
    assert columns["label"]["type"] == "nvarchar(80)"
    assert columns["amount"]["type"] == "decimal(12,2)"
    assert columns["label_size"]["generated"] and columns["revision"]["generated"]
    indexes = session.indexes(schema, "records")
    amount = next(index for index in indexes if index["name"] == "ix_amount")
    assert amount["columns"] == ["amount"] and amount["included"] == ["label"]
    assert "DESC" in amount["definition"] and "WHERE" in amount["definition"]
    assert session.foreign_keys(schema, "children")[0]["on_delete"] == "CASCADE"
    ddl = session.table_ddl(schema, "records")
    assert "not a lossless backup" in ddl and "IDENTITY(5,2)" in ddl and "PRIMARY KEY" in ddl
    assert "CREATE VIEW" in session.table_ddl(schema, "records_view")
    with pytest.raises(DatabaseError, match="default schema"):
        session.use_schema(schema)


def test_real_crud_binary_decimal_literal_filter_paging_and_concurrent_conflict(sqlserver):
    session, schema = sqlserver
    create_records(session, schema)
    assert session.insert_row(schema, "records", {"label": "100%_[literal]", "amount": Decimal("3.14"), "payload": b"\x00\xff"}) == 1
    assert session.insert_row(schema, "records", {"label": "other", "amount": Decimal("2.00")}) == 1
    result = session.fetch_table(schema, "records", limit=1)
    assert result.truncated and len(result.rows) == 1
    original = dict(zip(result.columns, result.rows[0]))
    assert original["id"] == 5 and bytes(original["payload"]) == b"\x00\xff"
    assert session.count_rows(schema, "records", "%_[literal]") == 1
    assert session.fetch_table(schema, "records", offset=1, limit=1).rows[0][0] == 7
    assert session.update_row(schema, "records", original, {"amount": Decimal("9.99")}) == 1
    with pytest.raises(DatabaseError, match="changed"):
        session.delete_row(schema, "records", original)
    with pytest.raises(ValueError, match="identity"):
        session.insert_row(schema, "records", {"id": 42, "label": "forbidden"})
    current = session.fetch_table(schema, "records", limit=10)
    fresh = dict(zip(current.columns, current.rows[0]))
    assert session.delete_row(schema, "records", fresh) == 1
    assert session.count_rows(schema, "records") == 1


def test_real_save_transaction_failure_preserves_outer_work_and_import_is_atomic(sqlserver):
    session, schema = sqlserver
    create_records(session, schema)
    session.execute("BEGIN TRANSACTION")
    assert session.in_transaction
    session.insert_row(schema, "records", {"label": "outer"})
    with pytest.raises(DatabaseError):
        session.import_rows(schema, "records", ["label"], [["partial"], ["outer"]])
    assert session.in_transaction and session.count_rows(schema, "records") == 1
    session.execute("ROLLBACK TRANSACTION")
    assert not session.in_transaction and session.count_rows(schema, "records") == 0


@pytest.mark.parametrize("mode", ["disable", "verify-full"])
def test_real_attention_cancels_waitfor_and_keeps_connection_usable(sqlserver, request, mode):
    setup, schema = sqlserver
    session = setup
    if mode == "verify-full":
        session = SqlServerSession(replace(setup.config, ssl_mode=mode, ssl_ca=tls_material()["ca"]))
        session.connect()
        request.addfinalizer(session.close)
    create_records(session, schema)
    session.execute("BEGIN TRANSACTION")
    session.insert_row(schema, "records", {"label": "keep outer work until explicit rollback"})
    completed, errors = threading.Event(), []
    def query():
        try:
            session.execute("WAITFOR DELAY '00:00:20'; SELECT 99 AS should_not_return;")
        except DatabaseError as error:
            errors.append(error)
        finally:
            completed.set()
    worker = threading.Thread(target=query)
    worker.start()
    deadline = time.monotonic() + 5
    while not session._active and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    session.cancel()
    assert completed.wait(5), "ATTENTION did not cancel the owned WAITFOR query"
    worker.join(1)
    assert errors and "cancelled" in str(errors[0]).lower()
    assert session.in_transaction and session.count_rows(schema, "records") == 1
    assert next(result for result in session.execute("SELECT 17") if result.columns).rows == [[17]]
    session.execute("ROLLBACK TRANSACTION")
    assert session.count_rows(schema, "records") == 0


def test_real_browser_and_crud_remain_bound_after_user_use_changes_database(sqlserver):
    session, schema = sqlserver
    create_records(session, schema)
    session.execute("USE tempdb")
    assert session.insert_row(schema, "records", {"label": "fixed configured database"}) == 1
    assert session.count_rows(schema, "records") == 1
    assert session.fetch_table(schema, "records").rows[0][1] == "fixed configured database"
    assert next(result for result in session.execute("SELECT DB_NAME()") if result.columns).rows == [["tempdb"]]


def test_real_dml_rowcount_ignores_trigger_result_sets_and_partial_batch_errors(sqlserver):
    session, schema = sqlserver
    table = create_records(session, schema)
    session.insert_row(schema, "records", {"label": "before"})
    trigger = session.quote_identifier(schema) + "." + session.quote_identifier("update_probe")
    session.execute(f"CREATE TRIGGER {trigger} ON {table} AFTER UPDATE AS BEGIN SELECT 999 AS trigger_result; END")
    result = session.fetch_table(schema, "records")
    original = dict(zip(result.columns, result.rows[0]))
    assert session.update_row(schema, "records", original, {"label": "after"}) == 1
    assert session.fetch_table(schema, "records").rows[0][1] == "after"
    with pytest.raises(DatabaseError) as caught:
        session.execute("SELECT 42 AS completed\nGO\nTHROW 51000, 'owned expected error', 1;")
    assert [result.rows for result in caught.value.partial_results if result.columns] == [[[42]]]


def test_real_authentication_rejection_is_classified_without_secret_echo(sqlserver):
    session, schema = sqlserver
    wrong = SqlServerSession(replace(session.config, password="owned-invalid-" + uuid.uuid4().hex))
    with pytest.raises(DatabaseError) as caught:
        wrong.connect()
    assert caught.value.authentication_required
    assert wrong.connection is None
    assert wrong.config.password not in str(caught.value)


def test_real_percent_and_bracket_identifiers_work_with_bound_values(sqlserver):
    session, schema = sqlserver
    name, column = "percent%table]", "label%]"
    target = session.qualified_name(schema, name)
    session.execute("CREATE TABLE " + target + " (id int PRIMARY KEY," + session.quote_identifier(column) + " nvarchar(80))")
    session.insert_row(schema, name, {"id": 1, column: "100%_[literal]"})
    session.import_rows(schema, name, ["id", column], [[2, "other"]])
    assert session.count_rows(schema, name, "%_[literal]") == 1
    result = session.fetch_table(schema, name, limit=10)
    original = dict(zip(result.columns, result.rows[0]))
    assert session.update_row(schema, name, original, {column: "bound after"}) == 1
    assert session.fetch_table(schema, name).rows[0][1] == "bound after"


def tls_material():
    endpoint = json.loads(Path(os.environ["OPENNAVICAT_TEST_SQLSERVER_ENDPOINT"]).read_text())
    material = endpoint.get("tls", {})
    if not material.get("ca"):
        if os.environ.get("OPENNAVICAT_TEST_SQLSERVER") == "1":
            pytest.fail("Required SQL Server integration endpoint has no TLS CA fixture")
        pytest.skip("Owned SQL Server endpoint has no TLS CA fixture")
    return material


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_real_tls_chain_and_san_hostname_modes(sqlserver, mode):
    setup, schema = sqlserver
    material = tls_material()
    secured = SqlServerSession(replace(setup.config, ssl_mode=mode, ssl_ca=material["ca"]))
    try:
        secured.connect()
        assert next(result for result in secured.execute("SELECT 73") if result.columns).rows == [[73]]
        encrypted = secured._query("SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id=@@SPID")[0][0]
        assert encrypted == "TRUE"
        if mode == "verify-full":
            large = "隔离" * 20000
            assert secured._query("SELECT DATALENGTH(%s)", [large]) == [(len(large) * 2,)]
    finally:
        secured.close()


@pytest.mark.parametrize("use_default_ca", [False, True])
def test_real_require_rejects_untrusted_chain_without_becoming_an_auth_prompt(sqlserver, use_default_ca):
    setup, schema = sqlserver
    material = tls_material()
    config = replace(setup.config, ssl_mode="require", ssl_ca="" if use_default_ca else material["wrong_ca"])
    secured = SqlServerSession(config)
    try:
        with pytest.raises(DatabaseError) as caught:
            secured.connect()
        assert not caught.value.authentication_required and secured.connection is None
    finally:
        secured.close()


def test_real_verify_full_uses_original_hostname_while_verify_ca_only_checks_chain(sqlserver, monkeypatch):
    setup, schema = sqlserver
    material = tls_material()
    import socket
    original = socket.create_connection
    wrong_name = "wrong-owned-sqlserver.example.invalid"
    def route_owned(address, *args, **kwargs):
        if address[0] == wrong_name:
            address = (setup.config.host, setup.config.port)
        return original(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", route_owned)
    full = SqlServerSession(replace(setup.config, host=wrong_name, ssl_mode="verify-full", ssl_ca=material["ca"]))
    chain = SqlServerSession(replace(setup.config, host=wrong_name, ssl_mode="verify-ca", ssl_ca=material["ca"]))
    try:
        with pytest.raises(DatabaseError, match="does not match host") as caught:
            full.connect()
        assert not caught.value.authentication_required
        chain.connect()
        assert next(result for result in chain.execute("SELECT 1") if result.columns).rows == [[1]]
    finally:
        full.close()
        chain.close()


def test_real_tls_over_owned_ssh_preserves_database_hostname_and_closes_listeners(sqlserver, tmp_path, monkeypatch):
    from test_connection_security_integration import OwnedSSHServer, SSH_PASSWORD, endpoint_closed
    from opennavicat.transport import SSHTunnel
    setup, schema = sqlserver
    material = tls_material()
    peer = OwnedSSHServer(SimpleNamespace(port=setup.config.port), tmp_path)
    original_init = SSHTunnel.__init__
    def isolated_init(self, config, host, port, known_hosts_paths=None):
        return original_init(self, config, host, port, known_hosts_paths=[])
    monkeypatch.setattr(SSHTunnel, "__init__", isolated_init)
    common = replace(setup.config, host="localhost", ssl_mode="verify-full", ssl_ca=material["ca"],
        ssh_enabled=True, ssh_host="127.0.0.1", ssh_port=peer.port, ssh_username="security_ssh",
        ssh_password=SSH_PASSWORD, ssh_host_key=peer.pin)
    secure = SqlServerSession(common)
    wrong = SqlServerSession(replace(common, host="wrong.test"))
    try:
        secure.connect()
        endpoint = secure._tunnel.endpoint
        assert next(result for result in secure.execute("SELECT 61") if result.columns).rows == [[61]]
        assert ("localhost", setup.config.port) in peer.destinations
        secure.close()
        assert endpoint_closed(endpoint)
        with pytest.raises(DatabaseError, match="does not match host"):
            wrong.connect()
        assert wrong.connection is None and wrong._tunnel is None
        assert ("wrong.test", setup.config.port) in peer.destinations
    finally:
        secure.close()
        wrong.close()
        peer.close()
