"""Driver error routing must never ask for a DB password on an SSH/TLS failure."""
from types import SimpleNamespace
import pytest
from opennavicat.database import is_database_auth_error, DatabaseError, ConnectionConfig, DatabaseSession
from opennavicat.transport import SSHAuthenticationError, TLSKeyPasswordError


@pytest.mark.parametrize("error", [
    Exception(1045, "Access denied"), Exception(1698, "Access denied"),
    Exception("fe_sendauth: no password supplied"),
    Exception('password authentication failed for user "fixture"'),
])
def test_database_password_required_driver_errors(error):
    assert is_database_auth_error(error)


@pytest.mark.parametrize("error", [
    Exception("certificate verify failed"), Exception("connection refused"),
    SSHAuthenticationError("password"), TLSKeyPasswordError(),
    Exception("SSH authentication failed"), Exception(2003, "Cannot connect"),
])
def test_transport_errors_never_request_db_password(error):
    assert not is_database_auth_error(error)


def test_connection_rethrows_typed_ssh_auth_after_cleanup(monkeypatch):
    import opennavicat.transport as transport
    instances = []
    failure = SSHAuthenticationError("password")
    class Tunnel:
        def __init__(self,*args):self.closed=False;instances.append(self)
        def start(self):raise failure
        def close(self):self.closed=True
    monkeypatch.setattr(transport,"SSHTunnel",Tunnel)
    session=DatabaseSession(ConnectionConfig("test","SSH","postgresql",ssh_enabled=True))
    with pytest.raises(SSHAuthenticationError) as error:session.connect()
    assert error.value is failure and instances[0].closed and session._tunnel is None


def test_mysql_connect_timeout_also_bounds_server_greeting():
    import socket
    import threading
    import time
    listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen();listener.settimeout(3)
    stopped=threading.Event()
    def stall():
        try:
            client,_=listener.accept()
            with client:stopped.wait(3)
        except OSError:pass
    thread=threading.Thread(target=stall,daemon=True);thread.start()
    session=DatabaseSession(ConnectionConfig("timeout","Timeout","mysql",host="127.0.0.1",port=listener.getsockname()[1],connect_timeout=1,ssl_mode="disable"))
    started=time.monotonic()
    try:
        with pytest.raises(DatabaseError) as error:session.connect()
        assert time.monotonic()-started<2.5
        assert not error.value.authentication_required and session.connection is None
    finally:
        stopped.set();listener.close();thread.join(2);session.close()
