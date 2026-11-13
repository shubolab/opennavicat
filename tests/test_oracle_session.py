"""Pure contracts for the dedicated Oracle adapter; no server or Qt needed."""
from dataclasses import asdict, replace
from decimal import Decimal
from types import SimpleNamespace
import threading
import time

import oracledb
import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat.oracle_session import OracleSession, OracleError, validate_oracle_execution


def profile(**changes):
    return replace(ConnectionConfig("owned", "Owned Oracle", driver="oracle", database="",
        host="127.0.0.1", port=1521, username="OWNED", password="fixture-only",
        oracle_service_identifier="FREEPDB1", ssl_mode="disable", connect_timeout=1), **changes)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = []
        self.arraysize = 100
        self.prefetchrows = 2
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        self.close()
    def close(self):
        self.connection.closed_cursors += 1
    def execute(self, sql, params=None, *, fetch_decimals=False):
        self.connection.sql.append((sql, params))
        self.connection.fetch_options.append(fetch_decimals)
        if "CURRENT_SCHEMA" in sql:
            self.rows = [("OWNED",)]
            self.description = [SimpleNamespace(name="OWNER", type_code=oracledb.DB_TYPE_VARCHAR)]
        else:
            self.connection.dispatch.set()
            if self.connection.before_dispatch is not None:
                self.connection.before_dispatch()
            if self.connection.wait_for_cancel:
                assert self.connection.broken.wait(2)
                raise Exception(SimpleNamespace(code=1013, full_code="ORA-01013"))
            self.rows = list(self.connection.rows)
            self.description = [SimpleNamespace(name="VALUE", type_code=self.connection.column_type)]
    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    version = "23.26.3.0.0"
    transaction_in_progress = False
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [(Decimal(1),)]
        self.column_type = oracledb.DB_TYPE_NUMBER
        self.sql = []
        self.fetch_options = []
        self.closed = False
        self.closed_cursors = 0
        self.dispatch = threading.Event()
        self.broken = threading.Event()
        self.wait_for_cancel = False
        self.before_dispatch = None
        self.cancel_count = 0
    def cursor(self):
        return Cursor(self)
    def close(self):
        self.closed = True
    def cancel(self):
        self.cancel_count += 1
        self.broken.set()
    def is_healthy(self):
        return not self.closed


def connected(monkeypatch, rows=None):
    connection = Connection(rows)
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: connection)
    session = DatabaseSession(profile())
    session.connect()
    return session, connection


def test_factory_and_flat_config_roundtrip_preserve_inactive_options():
    config = profile(oracle_tns_alias="UNUSED", oracle_keepalive_seconds=240,
        oracle_custom_database_list=[{"active": True, "name": "OWNED", "auto_open": False}],
        oracle_notes="verbatim\nnotes")
    restored = ConnectionConfig(**asdict(config))
    assert restored == config and restored.database == ""
    assert isinstance(DatabaseSession(restored), OracleSession)
    validate_oracle_execution(restored)


@pytest.mark.parametrize("changes", [
    {"oracle_connection_type": "tns"}, {"oracle_connection_type": "unknown"},
    {"oracle_service_type": "sid"}, {"oracle_role": "sysdba"}, {"oracle_role": "sysoper"},
    {"oracle_os_auth": True}, {"ssl_mode": "prefer"}, {"ssl_mode": "require"},
    {"ssl_ca": "/not/read"}, {"ssl_cert": "/not/read"}, {"ssl_key": "/not/read"},
    {"ssl_crl": "/not/read"}, {"ssl_key_password": "secret"}, {"ssh_enabled": True},
    {"read_only": True}, {"database": "NOT_A_SERVICE"}, {"oracle_settings_directory": "/not/read"},
    {"oracle_auto_connect": True}, {"oracle_session_limit_enabled": True},
    {"oracle_keepalive_enabled": True}, {"oracle_client_charset": "AL32UTF8"},
    {"oracle_initial_query": "SELECT 1 FROM DUAL"}, {"oracle_custom_database_list_enabled": True},
    {"username": "proxy[user]"}, {"username": ""}, {"host": ""}, {"port": 0},
    {"connect_timeout": 0}, {"connect_timeout": 61}, {"port": True},
    {"oracle_os_auth": 0}, {"oracle_session_limit": -1},
    {"oracle_custom_database_list": [{"name": "x"}]}, {"oracle_notes": None},
    {"host": "(DESCRIPTION=(ADDRESS=unowned))"}, {"host": "tcp://host/service"},
    {"oracle_service_identifier": "FREEPDB1)(SECURITY=(SSL_SERVER_DN_MATCH=OFF))"},
    {"oracle_service_identifier": "host/service"},
])
def test_unsupported_or_malformed_config_fails_before_network(monkeypatch, changes):
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("must reject before networking"))
    with pytest.raises(ValueError):
        DatabaseSession(profile(**changes)).connect()


def test_validation_before_prompt_is_pure_and_actual_connect_needs_password(monkeypatch):
    config = profile(password="")
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("no connect"))
    validate_oracle_execution(config)
    with pytest.raises(ValueError, match="password"):
        validate_oracle_execution(config, require_password=True)


def test_components_never_parse_alias_or_change_global_thin_mode(monkeypatch):
    calls = []
    connection = Connection()
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: calls.append(kwargs) or connection)
    monkeypatch.setattr(oracledb, "enable_thin_mode", lambda: pytest.fail("no global mode change"))
    monkeypatch.setenv("TNS_ADMIN", "/existing-tns-must-not-read")
    session = DatabaseSession(profile())
    assert session.test_connection() == "Oracle 23.26.3.0.0"
    assert calls == [dict(user="OWNED", password="fixture-only", host="127.0.0.1", port=1521,
        service_name="FREEPDB1", protocol="tcp", externalauth=False, mode=0,
        retry_count=0, tcp_connect_timeout=1, disable_oob=True)]
    assert session._selected_schema == "OWNED" and session.config.database == ""
    assert connection.call_timeout == 1000 and connection.autocommit is False
    session.close()
    assert connection.closed


def test_thick_mode_fails_before_network_without_reinitializing_process(monkeypatch):
    monkeypatch.setattr(oracledb, "is_thin_mode", lambda: False)
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("no connect"))
    with pytest.raises(ValueError, match="Thick"):
        DatabaseSession(profile()).connect()


def test_number_fetch_uses_public_statement_decimal_option_without_global_mutation(monkeypatch):
    original = oracledb.defaults.fetch_decimals
    session, connection = connected(monkeypatch)
    session.execute("SELECT 1 FROM DUAL")
    assert connection.fetch_options == [False, True]
    assert oracledb.defaults.fetch_decimals is original


def test_safe_driver_error_keeps_only_code_and_auth_flag(monkeypatch):
    def reject(**kwargs):
        raise Exception(SimpleNamespace(full_code="ORA-01017", code=1017, message="password=fixture-only business secret"))
    monkeypatch.setattr(oracledb, "connect", reject)
    with pytest.raises(OracleError) as caught:
        DatabaseSession(profile()).connect()
    assert caught.value.code == "ORA-01017" and caught.value.authentication_required
    assert str(caught.value) == "Oracle operation failed (ORA-01017)"
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("type_code", [oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_BLOB,
    oracledb.DB_TYPE_NCLOB, oracledb.DB_TYPE_LONG, oracledb.DB_TYPE_OBJECT, oracledb.DB_TYPE_JSON])
def test_no_lob_or_other_live_driver_value_escapes(monkeypatch, type_code):
    session, connection = connected(monkeypatch, [(object(),)])
    connection.column_type = type_code
    with pytest.raises(DatabaseError, match="not supported"):
        session.execute("SELECT unsafe_value FROM owned")
    assert session.current_operation_marker is None and connection.closed_cursors == 2


def test_bounded_rows_truncation_and_verbatim_sql(monkeypatch):
    session, _ = connected(monkeypatch, [(Decimal(i),) for i in range(5)])
    sql = "  SELECT id FROM owned;  "
    result = session.execute(sql, limit=2)[0]
    assert result.rows == [[Decimal(0)], [Decimal(1)]] and result.truncated
    assert result.sql == sql


def test_byte_limit_fails_without_partial_driver_objects(monkeypatch):
    session, connection = connected(monkeypatch, [("x" * (1024 * 1024 + 1),)])
    connection.column_type = oracledb.DB_TYPE_VARCHAR
    with pytest.raises(DatabaseError, match="byte limit") as caught:
        session.execute("SELECT value FROM owned")
    assert caught.value.partial_results == [] and session.current_operation_marker is None


@pytest.mark.parametrize("sql", ["DELETE FROM t", "CREATE TABLE t(i int)", "BEGIN NULL; END;", "SELECT 1 FROM DUAL; SELECT 2 FROM DUAL"])
def test_write_or_multi_statement_is_explicitly_unsupported_before_connect(monkeypatch, sql):
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("not supported"))
    with pytest.raises(DatabaseError, match="not implemented"):
        DatabaseSession(profile()).execute(sql)


@pytest.mark.parametrize("method,args", [("insert_row", ("OWNED", "T", {"ID": 1})),
    ("update_row", ("OWNED", "T", {"ID": 1}, {"ID": 2})),
    ("delete_row", ("OWNED", "T", {"ID": 1})),
    ("import_rows", ("OWNED", "T", ["ID"], [[1]])), ("backup_to", ("/not/write",)),
    ("table_ddl", ("OWNED", "T")), ("indexes", ("OWNED", "T")), ("foreign_keys", ("OWNED", "T"))])
def test_inherited_mutation_and_mysql_tools_cannot_leak_through(monkeypatch, method, args):
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("no inherited DB path"))
    with pytest.raises(DatabaseError, match="not implemented"):
        getattr(DatabaseSession(profile()), method)(*args)


def test_already_cancelled_queued_job_does_not_connect(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("cancelled queued job"))
    session = DatabaseSession(profile())
    with pytest.raises(OracleError, match="Stopped"):
        session.execute("SELECT 1 FROM DUAL", cancel_event=stop)
    assert session.current_operation_marker is None


def test_cancel_during_cold_connect_prevents_bootstrap_and_query_dispatch(monkeypatch):
    connection = Connection()
    stop = threading.Event()
    def connect(**kwargs):
        stop.set()
        return connection
    monkeypatch.setattr(oracledb, "connect", connect)
    session = DatabaseSession(profile())
    with pytest.raises(OracleError, match="Stopped"):
        session.execute("SELECT 1 FROM DUAL", cancel_event=stop)
    assert connection.closed and connection.sql == [] and session.connection is None


def test_cancel_retries_exact_token_across_pre_dispatch_gap_and_next_query_reuses(monkeypatch):
    session, connection = connected(monkeypatch)
    gate, release = threading.Event(), threading.Event()
    def before_dispatch():
        gate.set()
        assert release.wait(2)
        connection.broken.clear()  # A single pre-dispatch break would be lost.
    connection.before_dispatch = before_dispatch
    connection.wait_for_cancel = True
    errors = []
    def run():
        try:
            session.execute("SELECT slow FROM owned")
        except BaseException as error:
            errors.append(error)
    owner = threading.Thread(target=run)
    owner.start()
    assert gate.wait(2)
    marker = session.current_operation_marker
    cancel = threading.Thread(target=lambda: session.cancel_current(marker))
    cancel.start()
    deadline = time.monotonic() + 2
    while connection.cancel_count == 0 and time.monotonic() < deadline:
        time.sleep(0.001)
    assert connection.cancel_count >= 1
    release.set()
    owner.join(3)
    cancel.join(3)
    assert not owner.is_alive() and not cancel.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], OracleError) and errors[0].cancelled
    assert connection.cancel_count >= 2 and session.current_operation_marker is None
    connection.before_dispatch = None
    connection.wait_for_cancel = False
    count = connection.cancel_count
    assert session.cancel_current(marker) is False
    assert session.execute("SELECT 1 FROM DUAL")[0].rows == [[Decimal(1)]]
    assert connection.cancel_count == count


def test_cached_status_and_marker_do_not_perform_io_and_active_close_refuses(monkeypatch):
    session, _ = connected(monkeypatch)
    with session._operation() as marker:
        assert session.current_operation_marker is marker and session.in_transaction is False
        with pytest.raises(DatabaseError, match="before closing"):
            session.close()
        with pytest.raises(DatabaseError, match="already"):
            session.execute("SELECT 1 FROM DUAL")
    assert session.current_operation_marker is None


def test_slow_cancel_send_never_blocks_cached_marker_and_owner_waits_before_retirement(monkeypatch):
    session, connection = connected(monkeypatch)
    send_started, release_send, finish_owner = threading.Event(), threading.Event(), threading.Event()
    owner_ready, owner_done = threading.Event(), threading.Event()
    errors = []
    def send():
        send_started.set()
        assert release_send.wait(2)
    connection.cancel = send
    def own():
        try:
            with session._operation():
                owner_ready.set()
                assert finish_owner.wait(2)
        except OracleError as error:
            errors.append(error)
        finally:
            owner_done.set()
    owner = threading.Thread(target=own)
    owner.start()
    assert owner_ready.wait(2)
    marker = session.current_operation_marker
    canceller = threading.Thread(target=lambda: session.cancel_current(marker))
    canceller.start()
    assert send_started.wait(2)
    read_done = threading.Event()
    read = threading.Thread(target=lambda: (session.current_operation_marker, read_done.set()))
    read.start()
    try:
        assert read_done.wait(0.2), "cached GUI marker must not wait for a network send"
        finish_owner.set()
        assert not owner_done.wait(0.1), "owner cannot retire the token with a break still in flight"
    finally:
        release_send.set()
        finish_owner.set()
        owner.join(2)
        canceller.join(2)
        read.join(2)
    assert owner_done.is_set() and len(errors) == 1 and errors[0].cancelled
    assert session.current_operation_marker is None
    assert session.cancel_current(marker) is False


def test_unhealthy_connection_is_retired_before_next_operation(monkeypatch):
    session, connection = connected(monkeypatch)
    connection.is_healthy = lambda: False
    assert session.execute("SELECT 1 FROM DUAL")[0].rows == [[Decimal(1)]]
    assert session.connection is None and connection.closed and session._selected_schema is None
    replacement = Connection()
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: replacement)
    assert session.execute("SELECT 1 FROM DUAL")[0].rows == [[Decimal(1)]]
    assert session.connection is replacement


@pytest.mark.parametrize("limit", [0, -1, 10001, True, 1.5])
def test_invalid_delivery_limit_rejects_before_connect(monkeypatch, limit):
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("invalid limit must not connect"))
    with pytest.raises(ValueError, match="limit"):
        DatabaseSession(profile()).execute("SELECT 1 FROM DUAL", limit=limit)


def test_generic_adapter_cursor_and_commands_are_explicitly_disabled():
    session = DatabaseSession(profile())
    with pytest.raises(DatabaseError, match="Generic SQL-adapter"):
        session._cursor()
    with pytest.raises(DatabaseError, match="Generic SQL-adapter"):
        session._run(object(), "DELETE FROM owned")


@pytest.mark.parametrize("original_error", [False, True])
def test_health_probe_failure_always_retires_token_and_never_exposes_native_text(monkeypatch, original_error):
    session, connection = connected(monkeypatch)
    def broken_probe():
        raise RuntimeError("business-password-must-not-escape")
    connection.is_healthy = broken_probe
    with pytest.raises(OracleError) as caught:
        with session._operation():
            if original_error:
                raise OracleError("Stopped by user", cancelled=True)
    assert "business-password" not in str(caught.value)
    assert str(caught.value) == ("Stopped by user" if original_error else "Oracle connection health could not be verified; connection discarded")
    assert session.current_operation_marker is None and session.connection is None and connection.closed


def test_changed_live_profile_cannot_rebind_an_existing_connection_silently(monkeypatch):
    session, connection = connected(monkeypatch)
    before = list(connection.sql)
    session.config.oracle_service_identifier = "ANOTHER_SERVICE"
    with pytest.raises(DatabaseError, match="profile changed"):
        session.execute("SELECT 1 FROM DUAL")
    assert connection.sql == before
    session.close()


def test_profile_edit_during_connect_closes_unpublished_connection(monkeypatch):
    session = DatabaseSession(profile())
    connection = Connection()
    def connect(**kwargs):
        session.config.host = "changed.example.invalid"
        return connection
    monkeypatch.setattr(oracledb, "connect", connect)
    with pytest.raises(DatabaseError, match="profile changed while connecting"):
        session.execute("SELECT 1 FROM DUAL")
    assert connection.closed and connection.sql == []
    assert session.connection is None and session.current_operation_marker is None


@pytest.mark.parametrize("scope_id", [
    pytest.param("lo0)(HOST=unowned.invalid", id="descriptor-host"),
    pytest.param("lo0)(PROTOCOL=tcps", id="descriptor-protocol"),
    pytest.param('lo0"', id="quote"),
    pytest.param("lo0(", id="open-parenthesis"),
    pytest.param("lo0)", id="close-parenthesis"),
    pytest.param("lo0=other", id="assignment"),
    pytest.param("lo 0", id="space"),
    pytest.param("lo0\tother", id="tab"),
    pytest.param("lo0\nother", id="newline"),
    pytest.param("lo0\rother", id="carriage-return"),
    pytest.param("lo0\x00other", id="control-null"),
    pytest.param("lo0\x7fother", id="control-del"),
    pytest.param("lo0\u2028other", id="unicode-line-separator"),
    pytest.param("lo0/other", id="slash"),
    pytest.param("lo0\\other", id="backslash"),
    pytest.param("lo0:other", id="colon"),
    pytest.param("lo0@other", id="at-sign"),
    pytest.param("lo0[other]", id="brackets"),
])
def test_ipv6_zone_rejects_descriptor_whitespace_and_control_before_network(monkeypatch, scope_id):
    config = profile(host="fe80::1%" + scope_id)
    calls = []
    def unexpected_connect(**options):
        calls.append(options["host"])
        pytest.fail("Malformed Oracle host reached the driver connect call")
    monkeypatch.setattr(oracledb, "connect", unexpected_connect)
    for operation in (lambda: validate_oracle_execution(config),
                      lambda: DatabaseSession(config).connect()):
        with pytest.raises(ValueError, match="Oracle host"):
            operation()
    assert calls == []


def test_ipv6_descriptor_zone_is_rejected_by_connect_without_prompt_validation(monkeypatch):
    calls = []
    def unexpected_connect(**options):
        calls.append(options["host"])
        pytest.fail("Malformed Oracle host reached the driver connect call")
    monkeypatch.setattr(oracledb, "connect", unexpected_connect)
    with pytest.raises(ValueError, match="Oracle host"):
        DatabaseSession(profile(host="::1%lo0)(HOST=unowned.invalid")).connect()
    assert calls == []


@pytest.mark.parametrize("host", [
    "127.0.0.1", "192.0.2.42", "generated.example.invalid", "generated_host-1.invalid",
    "::1", "2001:db8::1", "fe80::1%lo0", "fe80::1%en0", "fe80::1%12",
    "fe80::1%eth0.100", "fe80::1%veth_0-test",
])
def test_plain_and_scoped_hosts_preserve_one_host_in_public_driver_descriptor(monkeypatch, host):
    monkeypatch.setattr(oracledb, "connect", lambda **_options: pytest.fail("No network in host validation"))
    config = profile(host=host)
    validate_oracle_execution(config)
    options = OracleSession(config)._connection_options()
    params = oracledb.ConnectParams(**options)
    descriptor = params.get_connect_string()
    restored = oracledb.ConnectParams()
    restored.parse_connect_string(descriptor)
    assert options["host"] == host and restored.host == host
    assert descriptor.count("(HOST=") == 1
    assert restored.port == config.port and restored.service_name == config.oracle_service_identifier
    assert restored.protocol == options["protocol"] == "tcp"


def test_ipv6_zone_character_validation_does_not_invent_an_interface_length_limit():
    # This is character validation only, not proof such an OS interface exists.
    validate_oracle_execution(profile(host="fe80::1%" + "generated_interface_" * 32))
