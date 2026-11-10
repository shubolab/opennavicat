"""Binary Redis commands and real, fixture-owned standalone service behavior.

Set OPENNAVICAT_TEST_REDIS=1 to create disposable Docker services. No externally
provided Redis endpoint or existing container is ever used by these tests.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError
from opennavicat.redis_session import (
    RedisConflictError, RedisSession, display_redis_bytes, parse_redis_commands, quote_redis_bytes,
)
from test_transport import ssh_fixture, pin


@pytest.mark.parametrize("value", [b"", b"ascii", b"space value", b"\x00\xff\x80", bytes(range(256)), '中文\\"\n'.encode()])
def test_binary_argument_quote_round_trip(value):
    assert parse_redis_commands("GET " + quote_redis_bytes(value)) == [[b"GET", value]]


def test_native_parser_quotes_multiline_and_semicolon_are_not_sql():
    text = 'SET "binary\\xff" "first\\x00\\nsecond"\r\nGET \'literal\\xff\'\nSET raw\\key "semi;colon"\n'
    assert parse_redis_commands(text) == [[b"SET", b"binary\xff", b"first\x00\nsecond"], [b"GET", b"literal\\xff"], [b"SET", b"raw\\key", b"semi;colon"]]
    assert parse_redis_commands('SET key "actual\nnewline"') == [[b"SET", b"key", b"actual\nnewline"]]
    assert parse_redis_commands("PING;SET key value") == [[b"PING;SET", b"key", b"value"]]
    assert display_redis_bytes(b"\\xff") != display_redis_bytes(b"\xff")
    assert display_redis_bytes(b"") != display_redis_bytes(b'""')


@pytest.mark.parametrize("text", ['GET "unterminated', "GET 'unterminated", 'GET "closed"tail', 'GET "trailing\\'])
def test_invalid_redis_quotes_fail_before_execution(text):
    with pytest.raises(ValueError):
        parse_redis_commands(text)


@pytest.mark.parametrize("database", ["-1", "１", "0;FLUSHALL", "one", "2147483648"])
def test_database_selector_is_numeric(database):
    with pytest.raises(ValueError):
        RedisSession(ConnectionConfig("unit", "Unit", driver="redis", database=database, ssl_mode="disable"))


def _docker(*arguments):
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True, timeout=90).stdout.strip()


@pytest.fixture(scope="module")
def redis_service():
    if os.environ.get("OPENNAVICAT_TEST_REDIS") != "1":
        pytest.skip("Set OPENNAVICAT_TEST_REDIS=1 for owned disposable Redis integration")
    if not shutil.which("docker"):
        pytest.skip("Disposable Redis integration needs Docker")
    identifier = "opennavicat-redis-owned-" + uuid.uuid4().hex[:12]
    container = _docker("run", "--detach", "--rm", "--name", identifier, "--label", "opennavicat.test=true",
        "--label", "opennavicat.task=redis-adapter-tests", "--publish", "127.0.0.1::6379", "redis:7-alpine",
        "redis-server", "--save", "", "--appendonly", "no", "--requirepass", "owned-fixture-password")
    try:
        port = int(_docker("port", container, "6379/tcp").rsplit(":", 1)[1])
        config = ConnectionConfig(identifier, "Owned Redis", driver="redis", host="127.0.0.1", port=port,
            database="0", password="owned-fixture-password", ssl_mode="disable", connect_timeout=3)
        deadline = time.monotonic() + 15
        while True:
            probe = RedisSession(config)
            try:
                probe.connect()
                break
            except DatabaseError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.1)
            finally:
                probe.close()
        yield config
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, timeout=15)


@pytest.fixture
def session(redis_service):
    session = RedisSession(replace(redis_service))
    session.connect()
    # This server was created by the module fixture; never use a user endpoint.
    session._raw("FLUSHALL")
    yield session
    session.close()


def test_native_binary_results_partial_error_and_no_sql_transaction_facade(session):
    results = session.execute('SET "key\\xff" "value\\x00"\nGET "key\\xff"\nPING')
    assert [result.rows for result in results] == [[[b"OK"]], [[b"value\x00"]], [[b"PONG"]]]
    with pytest.raises(DatabaseError) as error:
        session.execute("SET retained first\nNO_SUCH_REDIS_COMMAND\nSET never_written last")
    assert len(error.value.partial_results) == 1
    assert session._raw("GET", "retained") == b"first" and session._raw("EXISTS", "never_written") == 0
    with pytest.raises(DatabaseError, match="transaction"):
        session.execute("MULTI\nSET must_not_run value\nEXEC")
    assert not session.in_transaction and session._raw("EXISTS", "must_not_run") == 0
    with pytest.raises(DatabaseError, match="SQL table"):
        session.fetch_table("0", "key")


def test_native_array_result_limit_and_schema_selection(session):
    session.execute("RPUSH values one two three four")
    result = session.execute("LRANGE values 0 -1", limit=2)[0]
    assert result.rows == [[1, b"one"], [2, b"two"]] and result.truncated
    assert session.schemas() == [str(index) for index in range(16)] and session.schema_list_complete
    session.execute("SELECT 1\nSET in_database_one value")
    assert session.read_key("1", "in_database_one").value == b"value"
    assert session.read_key("0", "in_database_one").type == "none"
    with pytest.raises(DatabaseError):
        session.use_schema("999")
    assert session._selected_schema == "0"


def test_scan_visits_all_binary_keys_without_keys_or_fake_count(session, monkeypatch):
    keys = [f"key:{i}".encode() for i in range(105)] + [b"\x00key\xff", b"\\xff"]
    for key in keys:
        session.set_string("0", key, b"value")
    wire, commands = session._wire, []
    def observe(*arguments):
        commands.append(arguments[0].upper())
        return wire(*arguments)
    monkeypatch.setattr(session, "_wire", observe)
    cursor, observed, pages = 0, set(), 0
    while True:
        page = session.scan_keys("0", cursor=cursor, count=7)
        observed.update(entry["key"] for entry in page.entries)
        assert all(entry["kind"] == "redis_key" for entry in page.entries)
        cursor, pages = page.cursor, pages + 1
        if page.complete:
            break
        assert pages < 1000
    assert observed == set(keys) and pages > 1
    assert b"SCAN" in commands and b"KEYS" not in commands


def test_string_range_cas_ttl_and_rename_preserve_exact_bytes(session):
    value = bytes(range(256)) * 4
    session.set_string("0", b"key\xff", value, expected=None)
    session.expire_key("0", b"key\xff", 10000)
    page = session.read_key("0", b"key\xff", max_bytes=10)
    assert page.value == value[:10] and page.total == len(value) and page.truncated and page.next_offset == 10
    with pytest.raises(RedisConflictError):
        session.set_string("0", b"key\xff", b"bad", expected=page.value)
    session.set_string("0", b"key\xff", b"new\x00", expected=value)
    assert 0 < session.read_key("0", b"key\xff").pttl <= 10000
    session.rename_key("0", b"key\xff", b"renamed\x00")
    assert session.read_key("0", b"renamed\x00").value == b"new\x00"
    assert 0 < session.read_key("0", b"renamed\x00").pttl <= 10000
    session.set_string("0", "occupied", b"retained")
    with pytest.raises(RedisConflictError):
        session.rename_key("0", b"renamed\x00", "occupied")
    assert session.read_key("0", "occupied").value == b"retained"
    session.expire_key("0", b"renamed\x00", None)
    assert session.read_key("0", b"renamed\x00").pttl == -1
    session.delete_key("0", b"renamed\x00")
    assert session.read_key("0", b"renamed\x00").type == "none"


def test_hash_list_set_zset_exact_edits_conflicts_and_ttls(session):
    session.set_hash_field("0", "hash", b"field\xff", b"value\x00", expected=None)
    session.expire_key("0", "hash", 10000)
    assert session.read_key("0", "hash").rows == [[b"field\xff", b"value\x00"]]
    with pytest.raises(RedisConflictError):
        session.set_hash_field("0", "hash", b"field\xff", b"lost", expected=b"stale")
    session.set_hash_field("0", "hash", b"field\xff", b"after", expected=b"value\x00")
    assert 0 < session.read_key("0", "hash").pttl <= 10000
    session.delete_hash_field("0", "hash", b"field\xff", expected=b"after")
    session.append_list("0", "list", [b"duplicate", b"duplicate", b"last\xff"])
    session.expire_key("0", "list", 10000)
    session.delete_list_item("0", "list", 1, expected=b"duplicate")
    session.set_list_item("0", "list", -1, b"updated\x00", expected=b"last\xff")
    assert session.read_key("0", "list").rows == [[0, b"duplicate"], [1, b"updated\x00"]]
    assert 0 < session.read_key("0", "list").pttl <= 10000
    session.add_set_member("0", "set", b"only\xff"); session.expire_key("0", "set", 10000)
    session.replace_set_member("0", "set", b"only\xff", b"new\x00")
    assert session.read_key("0", "set").rows == [[b"new\x00"]] and session.read_key("0", "set").pttl > 0
    session.add_set_member("0", "set", b"occupied")
    with pytest.raises(RedisConflictError):session.replace_set_member("0", "set", b"new\x00", b"occupied")
    session.remove_set_member("0", "set", b"occupied")
    session.set_zset_member("0", "zset", b"member\xff", b"1.25", expected=None); session.expire_key("0", "zset", 10000)
    with pytest.raises(RedisConflictError):session.set_zset_member("0", "zset", b"member\xff", b"2", expected=b"stale")
    session.replace_zset_member("0", "zset", b"member\xff", b"new\x00", b"2.5", expected=b"1.25")
    assert session.read_key("0", "zset").rows == [[b"new\x00", b"2.5"]] and session.read_key("0", "zset").pttl > 0
    with pytest.raises(DatabaseError):session.replace_zset_member("0", "zset", b"new\x00", b"would_lose", b"1e99999", expected=b"2.5")
    assert session.read_key("0", "zset").rows == [[b"new\x00", b"2.5"]]
    session.delete_zset_member("0", "zset", b"new\x00", expected=b"2.5")


def test_stream_duplicate_fields_binary_and_cursor_boundaries(session):
    first = session.stream_add("0", "stream", [(b"duplicate", b"one\xff"), (b"duplicate", b"two\x00")])
    second = session.stream_add("0", "stream", [(b"other", b"last")])
    page = session.read_key("0", "stream", limit=1)
    assert page.rows == [[first, [b"duplicate", b"one\xff", b"duplicate", b"two\x00"]]]
    assert page.cursor == first and page.truncated
    last = session.read_key("0", "stream", limit=1, cursor=page.cursor)
    assert last.rows == [[second, [b"other", b"last"]]] and last.cursor == 0
    session.stream_delete("0", "stream", [first])
    assert session.read_key("0", "stream").rows == [[second, [b"other", b"last"]]]


@pytest.mark.parametrize("kind,initial", [("string",b"value\xff"),("hash",[(b"f",b"v\x00")]),("list",[b"one",b"two"]),
    ("set",[b"one",b"two"]),("zset",[(b"member",b"1.25")]),("stream",[(b"same",b"one"),(b"same",b"two")])])
def test_atomic_new_key_types_and_existing_key_collision(session,kind,initial):
    assert session.create_key("0",b"new\xff",kind,initial)
    assert session.read_key("0",b"new\xff").type==kind
    session.set_string("0","existing",b"retained");session.expire_key("0","existing",10000)
    with pytest.raises(RedisConflictError,match="already exists"):
        session.create_key("0","existing",kind,initial)
    assert session.read_key("0","existing").value==b"retained" and session.read_key("0","existing").pttl>0


def test_atomic_new_key_concurrent_collision_and_invalid_initial_value(session,redis_service):
    barrier=threading.Barrier(2);results=[]
    def create(value):
        candidate=RedisSession(replace(redis_service))
        try:
            candidate.connect();barrier.wait(3)
            try:results.append(candidate.create_key("0","racing","string",value))
            except RedisConflictError:results.append(False)
        finally:candidate.close()
    threads=[threading.Thread(target=create,args=(value,)) for value in (b"first",b"second")]
    for worker in threads:worker.start()
    for worker in threads:worker.join(5);assert not worker.is_alive()
    assert sorted(results)==[False,True] and session.read_key("0","racing").value in {b"first",b"second"}
    with pytest.raises(DatabaseError):session.create_key("0","invalid-score","zset",[(b"member",b"bad-score")])
    assert session.read_key("0","invalid-score").type=="none"


def test_readonly_uses_server_metadata_and_all_mutation_apis_refuse(session, monkeypatch):
    session.set_string("0", "key", b"retained")
    session.config.read_only = True
    assert session.execute("GET key")[0].rows == [[b"retained"]]
    assert session.execute("PING")[0].rows == [[b"PONG"]]
    for command in ["SET key lost", "DEL key", "EVAL 'return redis.call(\"SET\",\"key\",\"lost\")' 0", "UNKNOWN_COMMAND"]:
        with pytest.raises(DatabaseError, match="not marked readonly"):
            session.execute(command)
    with pytest.raises(DatabaseError, match="read-only"):
        session.set_string("0", "key", b"lost")
    original = session._command_info
    monkeypatch.setattr(session, "_command_info", lambda arguments: [b"get", 2, [b"write"]])
    with pytest.raises(DatabaseError, match="not marked readonly"):
        session.execute("GET key")
    monkeypatch.setattr(session, "_command_info", original)
    assert session.read_key("0", "key").value == b"retained"


def test_acl_authentication_readonly_command_permission_and_info_schema_fallback(session, redis_service):
    username = "owned-user-" + uuid.uuid4().hex[:8]
    password = "数据库🔐密码"
    session._raw("ACL", "SETUSER", username, "on", ">" + password, "~*", "+ping", "+info", "+get", "+select")
    user = RedisSession(replace(redis_service, username=username, password=password, read_only=True))
    try:
        user.connect()
        assert not user.schemas() == [] and not user.schema_list_complete
        with pytest.raises(DatabaseError, match="COMMAND INFO permission") as error:
            user.execute("GET missing")
        assert not error.value.authentication_required
    finally:
        user.close(); session._raw("ACL", "DELUSER", username)
    for config in [replace(redis_service, password="wrong"), replace(redis_service, password="")]:
        invalid = RedisSession(config)
        try:
            with pytest.raises(DatabaseError) as error:invalid.connect()
            assert error.value.authentication_required and invalid.connection is None
        finally:invalid.close()


def test_cancel_blocking_read_is_prompt_and_session_reconnects_selected_database(session):
    session.use_schema("1")
    session.config.connect_timeout = 30
    session.connection.socket_timeout = 30
    errors = []
    def blocked():
        try:session.execute("BLPOP definitely_missing 0")
        except DatabaseError as error:errors.append(error)
    worker = threading.Thread(target=blocked)
    worker.start()
    deadline = time.monotonic() + 3
    while not session._active and time.monotonic() < deadline:time.sleep(.01)
    time.sleep(.05)
    started = time.monotonic(); session.cancel(); worker.join(3)
    assert not worker.is_alive() and time.monotonic() - started < 3
    assert len(errors) == 1 and "cancel" in str(errors[0]).lower()
    session.execute("SET after_cancel selected_one")
    assert session.read_key("1", "after_cancel").value == b"selected_one"
    assert session.read_key("0", "after_cancel").type == "none"


def _restricted_user(admin, redis_service, denied):
    username = "owned-restricted-" + uuid.uuid4().hex[:10]
    password = "owned-restricted-password"
    admin._raw("ACL", "SETUSER", username, "on", ">" + password, "~*", "+@all", *["-" + command for command in denied])
    return username, RedisSession(replace(redis_service, username=username, password=password))


def test_acl_denied_pexpire_cannot_strip_ttl_after_single_value_edits(session, redis_service):
    session.set_string("0", "ttl-string", b"before")
    session.set_hash_field("0", "ttl-hash", b"field", b"before")
    session.append_list("0", "ttl-list", [b"before"])
    session.set_zset_member("0", "ttl-zset", b"member", b"1.25")
    for key in ("ttl-string", "ttl-hash", "ttl-list", "ttl-zset"):
        session.expire_key("0", key, 60000)
    username, limited = _restricted_user(session, redis_service, ["pexpire"])
    try:
        limited.set_string("0", "ttl-string", b"after", expected=b"before")
        limited.set_hash_field("0", "ttl-hash", b"field", b"after", expected=b"before")
        limited.set_list_item("0", "ttl-list", 0, b"after", expected=b"before")
        limited.set_zset_member("0", "ttl-zset", b"member", b"2.5", expected=b"1.25")
        assert limited.read_key("0", "ttl-string").value == b"after"
        for key in ("ttl-string", "ttl-hash", "ttl-list", "ttl-zset"):
            assert 0 < session.read_key("0", key).pttl <= 60000
    finally:
        limited.close(); session._raw("ACL", "DELUSER", username)


@pytest.mark.parametrize("denied,kind", [("lrem", "list"), ("lset", "list"),
    ("srem", "set"), ("sadd", "set"), ("zrem", "zset"), ("zadd", "zset")])
def test_compound_edit_checks_all_acl_permissions_before_any_write(session, redis_service, denied, kind):
    initial = [b"A", b"B"] if kind in {"list", "set"} else [(b"A", b"1.25"), (b"B", b"2.5")]
    session.create_key("0", "protected", kind, initial)
    session.expire_key("0", "protected", 60000)
    before = session.read_key("0", "protected")
    username, limited = _restricted_user(session, redis_service, [denied, "pexpire"])
    try:
        with pytest.raises(DatabaseError, match="nothing was changed") as error:
            if kind == "list":limited.delete_list_item("0", "protected", 0, expected=b"A")
            elif kind == "set":limited.replace_set_member("0", "protected", b"A", b"replacement")
            else:limited.replace_zset_member("0", "protected", b"A", b"replacement", b"3.75", expected=b"1.25")
        assert not error.value.authentication_required
        after = session.read_key("0", "protected")
        assert after.type == before.type and after.total == before.total
        assert (sorted(after.rows) if kind == "set" else after.rows) == (sorted(before.rows) if kind == "set" else before.rows)
        assert 0 < after.pttl <= 60000
        if kind == "list":assert session._raw("LRANGE", "protected", 0, -1) == [b"A", b"B"]
    finally:
        limited.close(); session._raw("ACL", "DELUSER", username)


@pytest.fixture(scope="module")
def tls_service(tmp_path_factory):
    if os.environ.get("OPENNAVICAT_TEST_REDIS") != "1":
        pytest.skip("Set OPENNAVICAT_TEST_REDIS=1 for owned Redis TLS integration")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    directory = tmp_path_factory.mktemp("owned-redis-tls")
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenNavicat owned Redis CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(ca_key,hashes.SHA256()))
    (directory/"ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    certificates = {}
    for role in ("server", "client"):
        private = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"db.test" if role=="server" else "owned client")])
        builder = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(private.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2))
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if role=="server" else ExtendedKeyUsageOID.CLIENT_AUTH]),critical=False))
        if role=="server":builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),x509.DNSName("db.test")]),critical=False)
        certificate=builder.sign(ca_key,hashes.SHA256()); certificates[role]=certificate
        (directory/f"{role}.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        encryption=serialization.NoEncryption() if role=="server" else serialization.BestAvailableEncryption(b"owned-client-key-passphrase")
        (directory/f"{role}.key").write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,encryption))
    revoked=x509.RevokedCertificateBuilder().serial_number(certificates["server"].serial_number).revocation_date(now-timedelta(minutes=1)).build()
    crl=(x509.CertificateRevocationListBuilder().issuer_name(ca_name).last_update(now-timedelta(minutes=2)).next_update(now+timedelta(days=1))
         .add_revoked_certificate(revoked).sign(ca_key,hashes.SHA256()))
    (directory/"revoked.crl").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    other_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    other=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(other_key.public_key())
           .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2))
           .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(other_key,hashes.SHA256()))
    (directory/"wrong-ca.pem").write_bytes(other.public_bytes(serialization.Encoding.PEM))
    identifier="opennavicat-redis-tls-"+uuid.uuid4().hex[:12]
    container=_docker("run","--detach","--rm","--name",identifier,"--label","opennavicat.test=true",
        "--label","opennavicat.task=redis-adapter-tests","--user","0:0","--publish","127.0.0.1::6379",
        "--mount",f"type=bind,source={directory},target=/owned-certs,readonly","redis:7-alpine","redis-server",
        "--save","","--appendonly","no","--port","0","--tls-port","6379","--tls-cert-file","/owned-certs/server.pem",
        "--tls-key-file","/owned-certs/server.key","--tls-ca-cert-file","/owned-certs/ca.pem","--tls-auth-clients","yes","--requirepass","owned-tls-password")
    try:
        port=int(_docker("port",container,"6379/tcp").rsplit(":",1)[1])
        config=ConnectionConfig(identifier,"Owned Redis TLS",driver="redis",host="localhost",port=port,database="0",password="owned-tls-password",
            ssl_mode="verify-full",ssl_ca=str(directory/"ca.pem"),ssl_cert=str(directory/"client.pem"),ssl_key=str(directory/"client.key"),
            ssl_key_password="owned-client-key-passphrase",connect_timeout=3)
        deadline=time.monotonic()+15
        while True:
            probe=RedisSession(config)
            try:probe.connect();break
            except DatabaseError:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
            finally:probe.close()
        yield config,directory
    finally:
        subprocess.run(["docker","rm","--force",container],capture_output=True,timeout=15)


@pytest.mark.parametrize("mode", ["require","verify-ca","verify-full"])
def test_real_tls_modes_client_certificate_and_encrypted_key(tls_service, mode):
    config,_=tls_service
    session=RedisSession(replace(config,ssl_mode=mode))
    try:
        assert session.execute("PING")[0].rows==[[b"PONG"]]
        session.set_string("0",b"binary\xff",b"value\x00")
        assert session.read_key("0",b"binary\xff").value==b"value\x00"
    finally:session.close()


@pytest.mark.parametrize("failure", ["wrong-ca","hostname","missing-client","revoked"])
def test_real_tls_failures_are_not_database_password_requests(tls_service, failure):
    config,directory=tls_service
    config=replace(config)
    if failure=="wrong-ca":config.ssl_ca=str(directory/"wrong-ca.pem")
    elif failure=="hostname":config.host="127.0.0.1"
    elif failure=="missing-client":config.ssl_cert=config.ssl_key=config.ssl_key_password=""
    else:config.ssl_crl=str(directory/"revoked.crl")
    session=RedisSession(config)
    try:
        with pytest.raises(DatabaseError) as error:session.connect()
        assert not error.value.authentication_required
        assert session.connection is None and session._tunnel is None
    finally:session.close()


@pytest.mark.parametrize("password", ["", "wrong"])
def test_encrypted_tls_key_password_failure_is_typed_without_terminal_prompt(tls_service,password):
    from opennavicat.transport import TLSKeyPasswordError
    config,_=tls_service
    session=RedisSession(replace(config,ssl_key_password=password))
    started=time.monotonic()
    try:
        with pytest.raises(TLSKeyPasswordError):session.connect()
        assert time.monotonic()-started<2 and session.connection is None
    finally:session.close()


def test_real_ssh_tls_preserves_original_hostname_pin_and_listener_cleanup(tls_service,ssh_fixture):
    from opennavicat.transport import SSHHostKeyError
    server,ssh_config,_=ssh_fixture
    config,_=tls_service
    bridge=server.bridge
    destinations=[]
    def resolve_owned_host(channel,destination):
        destinations.append(destination)
        bridge(channel,("127.0.0.1",destination[1]))
    server.bridge=resolve_owned_host
    config=replace(config,host="db.test",ssh_enabled=True,ssh_host="127.0.0.1",ssh_port=server.port,
        ssh_username=ssh_config.ssh_username,ssh_password=ssh_config.ssh_password)
    session=RedisSession(config)
    try:
        with pytest.raises(SSHHostKeyError) as error:session.connect()
        assert not server.auth_calls and session._tunnel is None
        approved=error.value.pin
        config.ssh_host_key=pin(server.user_key)
        with pytest.raises(SSHHostKeyError) as mismatch:session.connect()
        assert mismatch.value.expected and not server.auth_calls and session._tunnel is None
        config.ssh_host_key=approved
        assert session.execute("PING")[0].rows==[[b"PONG"]]
        local=session._tunnel.endpoint
        assert destinations and destinations[-1]==("db.test",config.port)
        session.close()
        with socket.socket() as probe:assert probe.connect_ex(local)!=0
    finally:session.close()


def test_real_ssh_tls_hostname_failure_drains_tunnel(tls_service,ssh_fixture,monkeypatch):
    from opennavicat.transport import SSHTunnel
    server,ssh_config,_=ssh_fixture
    config,_=tls_service
    bridge=server.bridge
    server.bridge=lambda channel,destination:bridge(channel,("127.0.0.1",destination[1]))
    started=[]; start=SSHTunnel.start
    def observe(tunnel):
        result=start(tunnel);started.append(tunnel.endpoint);return result
    monkeypatch.setattr(SSHTunnel,"start",observe)
    config=replace(config,host="wrong.test",ssh_enabled=True,ssh_host="127.0.0.1",ssh_port=server.port,
        ssh_username=ssh_config.ssh_username,ssh_password=ssh_config.ssh_password,ssh_host_key=pin(server.host_key))
    session=RedisSession(config)
    try:
        with pytest.raises(DatabaseError) as error:session.connect()
        assert not error.value.authentication_required and session._tunnel is None and session.connection is None
        assert started
        with socket.socket() as probe:assert probe.connect_ex(started[-1])!=0
    finally:session.close()
