"""Actual adapter/Oracle contracts, only through the fresh-owned-fixture runner.

No default connection. ``scripts/test-oracle.py`` creates a new pinned container
and supplies ephemeral credentials in child memory; no user profiles are read.
"""
from dataclasses import replace
from decimal import Decimal
import os
import re
import threading
import time

import oracledb
import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat.oracle_session import OracleError, OracleSession, TABLE_FIELDS, VIEW_FIELDS


pytestmark = pytest.mark.skipif(not os.environ.get("OPENNAVICAT_TEST_ORACLE_RUN_ID"),
    reason="Requires scripts/test-oracle.py fresh owned native fixture")


@pytest.fixture
def config():
    run_id = os.environ["OPENNAVICAT_TEST_ORACLE_RUN_ID"]
    assert re.fullmatch(r"[a-f0-9]{32}", run_id)
    username = os.environ["OPENNAVICAT_TEST_ORACLE_USER"]
    assert username == "ONV_" + run_id[:16].upper()
    config = ConnectionConfig("oracle-owned-" + run_id, "Owned Oracle adapter", driver="oracle",
        host="127.0.0.1", port=int(os.environ["OPENNAVICAT_TEST_ORACLE_PORT"]),
        database="", oracle_service_identifier="FREEPDB1", ssl_mode="disable",
        username=username, password=os.environ["OPENNAVICAT_TEST_ORACLE_PASSWORD"], connect_timeout=5)
    # Prove that this endpoint/schema belongs to this runner before each case.
    with oracledb.connect(user=config.username, password=config.password, host=config.host,
            port=config.port, service_name=config.oracle_service_identifier,
            retry_count=0, tcp_connect_timeout=3) as connection:
        connection.call_timeout = 5000
        with connection.cursor() as cursor:
            cursor.execute('SELECT RUN_ID FROM "' + username + '".OWNED_MARKER')
            assert cursor.fetchall() == [(run_id,)]
    return config


@pytest.fixture
def session(config):
    session = DatabaseSession(config)
    assert isinstance(session, OracleSession)
    try:
        yield session
    finally:
        session.close()


def test_actual_thin_service_test_schema_and_reference_object_metadata(session, config):
    assert session.test_connection().startswith("Oracle 23.26.3.")
    assert session.connection.thin and config.database == "" and session._selected_schema == config.username
    assert config.username in session.schemas()
    objects = session.objects(config.username)
    by_name = {row["name"]: row for row in objects}
    assert by_name["OWNED_FACTS"]["kind"] == "table"
    assert by_name["OWNED_FACTS_VIEW"]["kind"] == "view"
    assert by_name["OWNED_FACTS_MV"]["kind"] == "materialized_view"
    assert by_name["OWNED_SLEEP"]["kind"] == "function"
    assert sum(row["name"] == "OWNED_FACTS_MV" for row in objects) == 1
    assert set(TABLE_FIELDS).union({"name", "status", "comment"}) <= by_name["OWNED_FACTS"].keys()
    assert set(VIEW_FIELDS).union({"name", "status", "comment"}) <= by_name["OWNED_FACTS_VIEW"].keys()
    assert all(row["schema"] == config.username for row in objects)
    assert "table_type" in by_name["OWNED_FACTS"]["unavailable_fields"]
    assert by_name["OWNED_FACTS"]["table_type"] is None


def test_actual_columns_pk_defaults_and_bounded_table_view_read(session, config):
    columns = session.columns(config.username, "OWNED_FACTS")
    assert [col["name"] for col in columns] == ["ID", "LABEL", "AMOUNT", "NEGATIVE_SCALE", "CREATED"]
    assert columns[0]["pk"] and columns[0]["pk_order"] == 1 and columns[0]["precision"] == Decimal(38)
    assert columns[4]["default"] is not None or "default" in columns[4]["unavailable_fields"]
    result = session.fetch_table(config.username, "OWNED_FACTS", limit=1)
    assert result.truncated and len(result.rows) == 1 and result.rows[0][0] == Decimal(3)
    assert ":onv_offset" in result.sql and "LIMIT" not in result.sql
    next_page = session.fetch_table(config.username, "OWNED_FACTS", offset=1, limit=1)
    assert next_page.rows[0][0] == Decimal(9007199254740993)
    assert next_page.rows[0][1] == "generated Oracle 样例"
    view = session.fetch_table(config.username, "OWNED_FACTS_VIEW", limit=5, order_by="ID", descending=True)
    assert len(view.rows) == 3 and not view.truncated
    assert session.count_rows(config.username, "OWNED_FACTS") == 3


def test_actual_number_precision_preserves_38_digits_fraction_and_negative_scale(session, config):
    result = session.fetch_table(config.username, "OWNED_FACTS", limit=10, order_by="ID")
    by_id = {row[0]: row for row in result.rows}
    assert type(next(iter(by_id))) is Decimal
    assert by_id[Decimal(9007199254740993)][2] == Decimal("0.12345678901234567890123456789012345678")
    assert by_id[Decimal(99999999999999999999999999999999999999)][2] == Decimal("123456789012345678.1234567890123456789")
    assert by_id[Decimal(9007199254740993)][3] == Decimal(12300)
    assert all(type(row[2]) is Decimal or row[2] is None for row in result.rows)


def test_actual_lob_is_rejected_without_locator_or_secret_payload_escape(session, config):
    with pytest.raises(DatabaseError, match="LOB") as caught:
        session.fetch_table(config.username, "OWNED_LOBS")
    assert "synthetic private LOB" not in str(caught.value)
    assert caught.value.partial_results == []
    assert session.execute("SELECT 7 FROM DUAL")[0].rows == [[Decimal(7)]]


def test_actual_denied_user_has_no_other_schema_objects_and_cannot_read(config):
    denied = DatabaseSession(replace(config,
        username=os.environ["OPENNAVICAT_TEST_ORACLE_DENIED_USER"],
        password=os.environ["OPENNAVICAT_TEST_ORACLE_DENIED_PASSWORD"]))
    try:
        assert denied.test_connection().startswith("Oracle ")
        assert denied.objects(config.username) == []
        with pytest.raises(DatabaseError):
            denied.fetch_table(config.username, "OWNED_FACTS")
        with pytest.raises(OracleError) as caught:
            denied.execute('SELECT ID FROM "' + config.username + '".OWNED_FACTS')
        assert caught.value.code == "ORA-00942" and not caught.value.authentication_required
        assert "OWNED_FACTS" not in str(caught.value)
    finally:
        denied.close()


def test_actual_password_rejection_safe_code_and_no_credentials(config):
    denied = DatabaseSession(replace(config, password="synthetic-wrong-password"))
    with pytest.raises(OracleError) as caught:
        denied.test_connection()
    assert caught.value.code == "ORA-01017" and caught.value.authentication_required
    assert config.password not in str(caught.value) and "synthetic-wrong-password" not in str(caught.value)
    assert denied.connection is None


def test_actual_schema_binding_does_not_change_service_or_generate_ddl(session, config):
    session.use_schema(config.username)
    assert session._selected_schema == config.username
    assert session.config.oracle_service_identifier == "FREEPDB1" and session.config.database == ""
    session.use_schema("SYS")  # Browser state only; generated SQL is always qualified.
    assert session._selected_schema == "SYS"
    assert session.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM DUAL")[0].rows == [[config.username]]
    with pytest.raises(DatabaseError, match="not accessible"):
        session.use_schema("ONV_SCHEMA_THAT_DOES_NOT_EXIST")


def test_actual_filter_and_all_mutations_remain_closed_and_rows_unchanged(session, config):
    with pytest.raises(DatabaseError, match="filters"):
        session.fetch_table(config.username, "OWNED_FACTS", filter_text="' OR 1=1 --")
    with pytest.raises(DatabaseError, match="not implemented"):
        session.update_row(config.username, "OWNED_FACTS", {"ID": 3}, {"LABEL": "must not write"})
    with pytest.raises(DatabaseError, match="not implemented"):
        session.execute('DELETE FROM "' + config.username + '".OWNED_FACTS')
    assert session.count_rows(config.username, "OWNED_FACTS") == 3


def test_actual_sql_error_is_bounded_safe_and_next_query_reuses_connection(session):
    session.connect()
    connection = session.connection
    with pytest.raises(OracleError) as caught:
        session.execute("SELECT SYNTHETIC_SECRET_COLUMN FROM DUAL")
    assert caught.value.code == "ORA-00904" and "SYNTHETIC_SECRET_COLUMN" not in str(caught.value)
    assert session.execute("SELECT 123 FROM DUAL")[0].rows == [[Decimal(123)]]
    assert session.connection is connection


def test_actual_queued_cancel_and_live_server_cancel_then_reuse(session, config):
    stop = threading.Event()
    stop.set()
    with pytest.raises(OracleError, match="Stopped"):
        session.execute("SELECT 1 FROM DUAL", cancel_event=stop)
    assert session.connection is None
    sid = int(session.execute("SELECT SYS_CONTEXT('USERENV','SID') FROM DUAL")[0].rows[0][0])
    connection = session.connection
    errors = []
    started = time.monotonic()
    sql_marker = "ONV_CANCEL_" + os.environ["OPENNAVICAT_TEST_ORACLE_RUN_ID"]
    sql = ("SELECT /* " + sql_marker + " */ SUM(SQRT(A.N*B.N)) FROM "
           "(SELECT LEVEL N FROM DUAL CONNECT BY LEVEL<=100000) A CROSS JOIN "
           "(SELECT LEVEL N FROM DUAL CONNECT BY LEVEL<=100000) B")
    def run():
        try:
            session.execute(sql)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    with oracledb.connect(user=config.username, password=config.password, host=config.host, port=config.port,
            service_name=config.oracle_service_identifier, retry_count=0, tcp_connect_timeout=3) as observer:
        observer.call_timeout = 3000
        with observer.cursor() as cursor:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                cursor.execute("""SELECT S.STATUS,Q.SQL_TEXT FROM V$SESSION S JOIN V$SQL Q
                    ON Q.SQL_ID=S.SQL_ID AND Q.CHILD_NUMBER=S.SQL_CHILD_NUMBER WHERE S.SID=:sid""", {"sid": sid})
                state = cursor.fetchone()
                if state and state[0] == "ACTIVE" and sql_marker in state[1]:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("Owned Oracle marked compute query was not observed running on the server")
    marker = session.current_operation_marker
    cancel = threading.Thread(target=lambda: session.cancel_current(marker))
    cancel.start()
    worker.join(4)
    cancel.join(4)
    assert not worker.is_alive() and not cancel.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], OracleError) and errors[0].cancelled
    assert time.monotonic() - started < 4
    assert session.cancel_current(marker) is False
    assert session.connection is connection and session.current_operation_marker is None
    assert session.execute("SELECT 42 FROM DUAL")[0].rows == [[Decimal(42)]]


def test_actual_row_delivery_truncation_and_result_byte_rejection(session):
    result = session.execute("SELECT LEVEL AS ID FROM DUAL CONNECT BY LEVEL <= 1000", limit=3)[0]
    assert result.rows == [[Decimal(1)], [Decimal(2)], [Decimal(3)]] and result.truncated
    # Ordinary VARCHAR rows, not LOBs. This verifies delivery-byte enforcement;
    # it does not claim to bound Oracle's server work or all driver buffers.
    with pytest.raises(DatabaseError, match="byte limit"):
        session.execute("SELECT RPAD('x',4000,'x') AS V FROM DUAL CONNECT BY LEVEL <= 3000", limit=3000)


def test_actual_cold_connect_start_gap_event_closes_connection_without_dispatch_then_reopens(session, monkeypatch):
    connected, release = threading.Event(), threading.Event()
    stop = threading.Event()
    original = oracledb.connect
    native_connections = []
    def gated_connect(**kwargs):
        connection = original(**kwargs)
        native_connections.append(connection)
        connected.set()
        assert release.wait(3)
        return connection
    monkeypatch.setattr(oracledb, "connect", gated_connect)
    errors = []
    def run():
        try:
            session.execute("SELECT 987654321 FROM DUAL", cancel_event=stop)
        except BaseException as error:
            errors.append(error)
    owner = threading.Thread(target=run)
    owner.start()
    assert connected.wait(3)
    marker = session.current_operation_marker
    stop.set()
    canceller = threading.Thread(target=lambda: session.cancel_current(marker))
    canceller.start()
    release.set()
    owner.join(4)
    canceller.join(4)
    assert not owner.is_alive() and not canceller.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], OracleError) and errors[0].cancelled
    assert len(native_connections) == 1 and not native_connections[0].is_healthy()
    assert session.connection is None and session.current_operation_marker is None
    monkeypatch.setattr(oracledb, "connect", original)
    assert session.execute("SELECT 9 FROM DUAL")[0].rows == [[Decimal(9)]]
    assert session.cancel_current(marker) is False


def test_actual_old_marker_cannot_cancel_next_active_operation(session, config):
    session.connect()
    with session._operation() as old_marker:
        pass
    ready, release = threading.Event(), threading.Event()
    errors = []
    def run():
        try:
            with session._operation():
                ready.set()
                assert release.wait(3)
                assert session._read("SELECT 88 FROM DUAL", limit=1).rows == [[Decimal(88)]]
        except BaseException as error:
            errors.append(error)
    owner = threading.Thread(target=run)
    owner.start()
    assert ready.wait(3)
    assert session.current_operation_marker is not old_marker
    assert session.cancel_current(old_marker) is False
    release.set()
    owner.join(4)
    assert not owner.is_alive() and errors == []
    assert session.execute("SELECT 99 FROM DUAL")[0].rows == [[Decimal(99)]]


def test_actual_number_conversion_is_independent_of_session_decimal_separator(session):
    session.connect()
    # Fixture-only session setting, not a claim that the app's advanced initial
    # query/charset settings are implemented. The server demonstrably uses comma
    # decimal text while NUMBER must still arrive as exact Decimal values.
    with session.connection.cursor() as cursor:
        cursor.execute("ALTER SESSION SET NLS_NUMERIC_CHARACTERS=',.'")
        cursor.execute("SELECT TO_CHAR(1234.5) FROM DUAL")
        assert cursor.fetchone() == ("1234,5",)
    result = session.execute("SELECT 1234.5, 0.12345678901234567890123456789012345678 FROM DUAL")[0]
    assert result.rows == [[Decimal("1234.5"), Decimal("0.12345678901234567890123456789012345678")]]
