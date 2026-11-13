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
    def execute(self, sql, params=None):
        self.connection.sql.append((sql, params))
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
        retry_count=0, tcp_connect_timeout=1)]
    assert session._selected_schema == "OWNED" and session.config.database == ""
    assert connection.call_timeout == 1000 and connection.autocommit is False
    session.close()
    assert connection.closed


def test_thick_mode_fails_before_network_without_reinitializing_process(monkeypatch):
    monkeypatch.setattr(oracledb, "is_thin_mode", lambda: False)
    monkeypatch.setattr(oracledb, "connect", lambda **kwargs: pytest.fail("no connect"))
    with pytest.raises(ValueError, match="Thick"):
        DatabaseSession(profile()).connect()


def test_number_output_handler_uses_decimal_without_binary_float():
    calls = []
    cursor = SimpleNamespace(arraysize=7, var=lambda *args, **kwargs: calls.append((args, kwargs)) or "var")
    assert OracleSession._output_type_handler(cursor, SimpleNamespace(type_code=oracledb.DB_TYPE_NUMBER)) == "var"
    assert calls[0][1]["outconverter"] is Decimal and calls[0][1]["arraysize"] == 7
    assert calls[0][1]["outconverter"]("9007199254740993.123456789012345678901") == Decimal("9007199254740993.123456789012345678901")


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
