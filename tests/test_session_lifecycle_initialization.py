"""Actual adapter construction/idle lifecycle without any endpoint or GUI."""
from contextlib import closing
import socket
import sqlite3

import pytest

from opennavicat.database import ConnectionConfig, DatabaseSession, SUPPORTED_DRIVERS
from opennavicat.mongodb_session import MongoSession
from opennavicat.oracle_session import OracleSession
from opennavicat.redis_session import RedisSession
from opennavicat.sqlserver_session import SqlServerSession


ADAPTERS = {
    "sqlite": DatabaseSession, "postgresql": DatabaseSession,
    "mysql": DatabaseSession, "mariadb": DatabaseSession,
    "sqlserver": SqlServerSession, "redis": RedisSession,
    "mongodb": MongoSession, "oracle": OracleSession,
}
SPECIAL = {driver: adapter for driver, adapter in ADAPTERS.items() if adapter is not DatabaseSession}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Constructor/idle lifecycle attempted an endpoint connection")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def config(driver):
    database = {"sqlite": ":memory:", "sqlserver": "master", "redis": "0", "mongodb": "admin"}.get(driver, "generated")
    return ConnectionConfig("owned-lifecycle-" + driver, "Owned adapter lifecycle", driver=driver,
                            database=database, host="owned-endpoint.invalid", ssl_mode="disable")


def check_idle_lifecycle(session, driver):
    # The first close is the exact disconnected SQL Server path used by
    # packaged smoke. No fake connection or active-command flag is installed.
    session.close()
    assert session.connection is None and session._tunnel is None
    expected_schema = {"redis": "0", "mongodb": "admin"}.get(driver)
    assert session._selected_schema == expected_schema
    session.cancel()
    expected_signal = driver not in {"oracle", "redis"}
    assert session._cancel_requested.is_set() is expected_signal
    session.close()
    session.close()
    assert session.connection is None and session._selected_schema == expected_schema
    assert session._cancel_requested.is_set() is expected_signal
    assert session._active is False
    # Inherited lifecycle paths require this state; derived cancellation
    # implementations retain their original signal/nonce semantics.
    with session._cancel_lock:
        assert session._cancel_generation == (1 if ADAPTERS[driver] is DatabaseSession else 0)


@pytest.mark.parametrize("driver", tuple(ADAPTERS))
def test_actual_factory_constructs_and_repeatedly_closes_each_supported_adapter(driver):
    value = config(driver)
    session = DatabaseSession(value)
    assert type(session) is ADAPTERS[driver] and session.config is value
    check_idle_lifecycle(session, driver)


@pytest.mark.parametrize("driver", tuple(SPECIAL))
def test_direct_custom_adapter_initialization_satisfies_inherited_lifecycle(driver):
    check_idle_lifecycle(SPECIAL[driver](config(driver)), driver)


@pytest.mark.parametrize("driver", tuple(SPECIAL))
def test_custom_adapter_common_locks_and_events_are_private_per_instance(driver):
    first, second = SPECIAL[driver](config(driver)), SPECIAL[driver](config(driver))
    assert first._cancel_requested is not second._cancel_requested
    assert first._cancel_lock is not second._cancel_lock
    with first._cancel_lock:
        assert second._cancel_lock.acquire(blocking=False)
        second._cancel_lock.release()
    assert first._cancel_generation == second._cancel_generation == 0
    first.close(); second.close()


@pytest.mark.parametrize("adapter", [SqlServerSession, RedisSession, OracleSession])
def test_existing_driver_specific_constructor_rejection_remains_before_lifecycle(adapter):
    with pytest.raises(ValueError, match="requires driver"):
        adapter(config("sqlite"))


def test_all_application_custom_initializers_are_covered_and_snowflake_stays_unsupported():
    assert set(ADAPTERS) == set(SUPPORTED_DRIVERS)
    assert {cls for cls in DatabaseSession.__subclasses__() if cls.__module__.startswith("opennavicat.")} == set(SPECIAL.values())
    with pytest.raises(ValueError, match="Unsupported database driver: snowflake"):
        DatabaseSession(config("snowflake"))


def test_actual_sqlite_cancel_close_reconnect_keeps_its_generation_and_owned_file(tmp_path):
    path = tmp_path / "owned.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE owned(id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO owned VALUES(1)")
    before = path.read_bytes()
    session = DatabaseSession(ConnectionConfig("owned-sqlite", "Owned SQLite", database=str(path), sqlite_open_mode="existing"))
    try:
        session.connect()
        old = session.connection
        session.cancel()
        assert session._cancel_generation == 1 and session._cancel_requested.is_set()
        session.close()
        assert not session._cancel_requested.is_set() and session.connection is None
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            old.execute("SELECT 1")
        session.connect()
        assert session.connection is not old and session.execute("SELECT id FROM owned")[0].rows == [[1]]
        assert session._cancel_generation == 1
    finally:
        session.close()
    assert path.read_bytes() == before
