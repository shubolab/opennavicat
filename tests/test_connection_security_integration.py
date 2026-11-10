"""Opt-in security tests against owned Docker DBs and a real SSH forwarding peer.

Run OPENNAVICAT_SECURITY_TESTS=1 python -m pytest -q this_file.
No existing connection profiles, known_hosts files, keys, agents or databases
are used. Servers bind host loopback and are removed by fixture finalizers.
"""
from __future__ import annotations

import base64
import contextlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid

import pytest

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession

pytestmark = pytest.mark.skipif(os.environ.get("OPENNAVICAT_SECURITY_TESTS") != "1",
                                reason="Opt in to owned Docker DB/TLS/SSH integration with OPENNAVICAT_SECURITY_TESTS=1")

DB_PASSWORD = "opennavicat-security-test-only"
SSH_PASSWORD = "ssh-password-test-only"
KEY_PASSWORD = "private-key-test-only"
REPORT = Path(__file__).resolve().parents[1] / "artifacts" / "security-integration"


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for the owned security fixture")


def command(args, **kwargs):
    try:
        return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=kwargs.pop("timeout", 30), **kwargs).stdout.strip()
    except subprocess.CalledProcessError as error:
        error.add_note("Owned fixture command stderr: " + error.stderr)
        raise


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory = tmp_path_factory.mktemp("owned-security-certificates")
    now = datetime.now(timezone.utc)
    def key():
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    def subject(name):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    def issue(name, private, issuer=None, issuer_key=None, *, ca=False, server=False):
        builder = (x509.CertificateBuilder().subject_name(subject(name))
                   .issuer_name(issuer.subject if issuer else subject(name))
                   .public_key(private.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(private.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or private).public_key()), critical=False)
                   .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                       key_encipherment=not ca, data_encipherment=False, key_agreement=False,
                       key_cert_sign=ca, crl_sign=ca, encipher_only=None, decipher_only=None), critical=True))
        if not ca:
            builder = builder.add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        if server:
            builder = builder.add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.DNSName("db.test")]), critical=False)
        return builder.sign(issuer_key or private, hashes.SHA256())
    def save(name, data, private=False):
        path = directory / name
        path.write_bytes(data)
        path.chmod(0o600 if private else 0o644)
        return path
    ca_key = key(); ca = issue("OpenNavicat isolated test CA", ca_key, ca=True)
    wrong_key = key(); wrong_ca = issue("Unrelated isolated test CA", wrong_key, ca=True)
    server_key = key(); server = issue("db.test", server_key, ca, ca_key, server=True)
    client_key = key(); client = issue("security_client", client_key, ca, ca_key)
    revoked_key = key(); revoked = issue("security_client", revoked_key, ca, ca_key)
    values = {"directory": directory}
    for name, cert in [("ca", ca), ("wrong-ca", wrong_ca), ("server", server),
                       ("client", client), ("revoked-client", revoked)]:
        values[name] = save(name + ".pem", cert.public_bytes(serialization.Encoding.PEM))
    for name, private in [("server-key", server_key), ("client-key", client_key), ("revoked-client-key", revoked_key)]:
        values[name] = save(name + ".pem", private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), True)
    values["encrypted-client-key"] = save("encrypted-client-key.pem", client_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(KEY_PASSWORD.encode())), True)
    for name, revoked_serials in [("empty-crl", []), ("server-crl", [server.serial_number]),
                                   ("client-crl", [revoked.serial_number])]:
        builder = (x509.CertificateRevocationListBuilder().issuer_name(ca.subject)
                   .last_update(now - timedelta(minutes=1)).next_update(now + timedelta(days=2))
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False))
        for serial in revoked_serials:
            builder = builder.add_revoked_certificate(x509.RevokedCertificateBuilder()
                .serial_number(serial).revocation_date(now - timedelta(minutes=1)).build())
        values[name] = save(name + ".pem", builder.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    save("pg_hba.conf", b"local all all trust\nhostssl all security_client all cert\nhostssl all security_password all scram-sha-256\nhost all all all reject\n")
    yield values
    shutil.rmtree(directory)


class OwnedDatabase:
    def __init__(self, driver, certificates):
        self.driver, self.certificates = driver, certificates
        self.name = "opennavicat-security-" + driver + "-" + uuid.uuid4().hex[:10]
        image = {"postgresql": "postgres:15-alpine", "mysql": "mysql:8.0.36", "mariadb": "mariadb:11.4"}[driver]
        port = 5432 if driver == "postgresql" else 3306
        user = "postgres" if driver == "postgresql" else "mysql"
        args = ["docker", "run", "--detach", "--name", self.name, "--label", "opennavicat.test=true",
                "--publish", f"127.0.0.1::{port}", "--mount",
                f"type=bind,source={certificates['directory']},target=/security-src,readonly", "--entrypoint", "sh"]
        if driver == "postgresql":
            args += ["--env", "POSTGRES_DB=security_fixture", "--env", "POSTGRES_PASSWORD=" + DB_PASSWORD]
            daemon = ("postgres -c ssl=on -c ssl_cert_file=/security/server.pem "
                      "-c ssl_key_file=/security/server-key.pem -c ssl_ca_file=/security/ca.pem "
                      "-c ssl_crl_file=/security/client-crl.pem -c hba_file=/security/pg_hba.conf")
        else:
            prefix = "MYSQL" if driver == "mysql" else "MARIADB"
            args += ["--env", prefix + "_DATABASE=security_fixture", "--env", prefix + "_ROOT_PASSWORD=" + DB_PASSWORD,
                     "--env", prefix + "_ROOT_HOST=%"]
            daemon = ("mysqld" if driver == "mysql" else "mariadbd") + (
                " --ssl-ca=/security/ca.pem --ssl-cert=/security/server.pem --ssl-key=/security/server-key.pem"
                " --ssl-crl=/security/client-crl.pem --require-secure-transport=ON")
        script = f"cp -R /security-src /security; chown -R {user}:{user} /security; chmod 600 /security/*key.pem; exec docker-entrypoint.sh {daemon}"
        self.ident = command(args + [image, "-ceu", script])
        try:
            bound = command(["docker", "inspect", "--format", f'{{{{(index (index .NetworkSettings.Ports "{port}/tcp") 0).HostPort}}}}', self.name])
            self.port = int(bound)
            wait_for(self.ready, timeout=100)
            if driver == "postgresql":
                self.admin_sql(f"CREATE ROLE security_password LOGIN PASSWORD '{DB_PASSWORD}'; CREATE ROLE security_client LOGIN;")
            else:
                self.admin_sql(f"CREATE USER 'security_password'@'%' IDENTIFIED BY '{DB_PASSWORD}' REQUIRE SSL; "
                               f"CREATE USER 'security_client'@'%' IDENTIFIED BY '{DB_PASSWORD}' REQUIRE X509; "
                               "GRANT ALL ON security_fixture.* TO 'security_password'@'%'; "
                               "GRANT ALL ON security_fixture.* TO 'security_client'@'%';")
            self.version = self.admin_sql("SELECT version()")
        except BaseException:
            REPORT.mkdir(parents=True, exist_ok=True)
            logs = subprocess.run(["docker", "logs", self.name], text=True, capture_output=True)
            (REPORT / (driver + "-setup.log")).write_text(logs.stdout + logs.stderr, "utf-8")
            self.close()
            raise
    def admin_sql(self, sql):
        if self.driver == "postgresql":
            return command(["docker", "exec", "--user", "postgres", self.name,
                            "psql", "-U", "postgres", "-d", "security_fixture", "-tAc", sql], timeout=10)
        program = "mysql" if self.driver == "mysql" else "mariadb"
        return command(["docker", "exec", "--env", "MYSQL_PWD=" + DB_PASSWORD, self.name,
                        program, "--protocol=socket", "--default-character-set=utf8mb4", "-uroot", "--batch", "--skip-column-names", "-e", sql], timeout=10)
    def ready(self):
        import pymysql
        try:
            self.admin_sql("SELECT 1")
            if self.driver == "postgresql":
                with socket.create_connection(("127.0.0.1", self.port), 0.2):
                    return True
            # Docker's published socket may accept before a temporary init
            # server finishes. Require a real TLS root login, not just TCP.
            import ssl
            context = ssl.create_default_context(cafile=str(self.certificates["ca"]))
            with pymysql.connect(host="localhost", port=self.port, database="security_fixture", user="root",
                                 password=DB_PASSWORD, ssl=context, connect_timeout=2,
                                 read_timeout=2, write_timeout=2) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() == (1,)
        except (subprocess.SubprocessError, OSError, pymysql.MySQLError):
            return False
    def config(self, **overrides):
        values = dict(id=uuid.uuid4().hex, name="Isolated security " + self.driver,
                      driver=self.driver, database="security_fixture", host="localhost", port=self.port,
                      username="security_password", password=DB_PASSWORD, ssl_mode="verify-full",
                      ssl_ca=str(self.certificates["ca"]), connect_timeout=3)
        values.update(overrides)
        return ConnectionConfig(**values)
    def close(self):
        if not getattr(self, "ident", None):
            return
        label = command(["docker", "inspect", "--format", '{{index .Config.Labels "opennavicat.test"}}', self.ident])
        assert label == "true"
        command(["docker", "rm", "--force", self.ident])
        self.ident = None


@pytest.fixture(scope="module", params=["postgresql", "mysql", "mariadb"])
def security_db(request, certificates):
    if not shutil.which("docker"):
        pytest.fail("Opted-in integration requires Docker")
    db = OwnedDatabase(request.param, certificates)
    REPORT.mkdir(parents=True, exist_ok=True)
    report = REPORT / "servers.json"
    versions = json.loads(report.read_text()) if report.exists() else {}
    versions[db.driver] = {"version": db.version, "loopback_port": db.port, "container": db.name}
    report.write_text(json.dumps(versions, indent=2), "utf-8")
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def isolate_libpq_client_defaults(monkeypatch, certificates):
    """Do not consult the user's libpq service/password/client-key defaults."""
    for name in list(os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name, raising=False)
    directory = certificates["directory"]
    for name, filename in [("PGPASSFILE", "empty-pgpass"), ("PGSERVICEFILE", "empty-pgservice")]:
        path = directory / filename
        path.touch(mode=0o600, exist_ok=True)
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("PGSYSCONFDIR", str(directory))
    monkeypatch.setenv("PGSSLCERT", str(directory / "absent-default-client.pem"))
    monkeypatch.setenv("PGSSLKEY", str(directory / "absent-default-client-key.pem"))
    monkeypatch.setenv("PGSSLCRL", str(certificates["empty-crl"]))


def assert_tls(session):
    assert session.execute("SELECT 42")[0].rows == [[42]]
    if session.config.driver == "postgresql":
        assert session.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")[0].rows == [[True]]
    else:
        assert session.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")[0].rows[0][1]


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_valid_ca_and_real_tls(security_db, mode):
    session = DatabaseSession(security_db.config(ssl_mode=mode))
    try:
        session.connect(); assert_tls(session)
    finally:
        session.close()


@pytest.mark.parametrize("wrong", ["ca", "hostname"])
def test_wrong_ca_and_hostname_are_rejected(security_db, certificates, wrong):
    config = security_db.config(**({"ssl_ca": str(certificates["wrong-ca"])} if wrong == "ca" else {"host": "127.0.0.1"}))
    session = DatabaseSession(config)
    try:
        with pytest.raises(DatabaseError) as error:
            session.connect()
        assert any(word in str(error.value).lower() for word in ("certificate", "ssl", "tls", "hostname"))
        assert not error.value.authentication_required
        assert session.connection is None
    finally:
        session.close()


def test_verify_ca_does_not_claim_hostname_verification(security_db):
    session = DatabaseSession(security_db.config(host="127.0.0.1", ssl_mode="verify-ca"))
    try:
        session.connect(); assert_tls(session)
    finally:
        session.close()


def test_database_password_unicode_is_transmitted_as_utf8(security_db):
    password = "数据库-测试-✓"
    def change(value):
        sql = (f"ALTER ROLE security_password PASSWORD '{value}'" if security_db.driver == "postgresql"
               else f"ALTER USER 'security_password'@'%' IDENTIFIED BY '{value}'")
        security_db.admin_sql(sql)
    change(password)
    session = DatabaseSession(security_db.config(password=password))
    try:
        session.connect(); assert_tls(session)
    finally:
        session.close()
        change(DB_PASSWORD)


def client_config(db, certificates, **overrides):
    values = dict(username="security_client", ssl_cert=str(certificates["client"]),
                  ssl_key=str(certificates["encrypted-client-key"]), ssl_key_password=KEY_PASSWORD)
    values.update(overrides)
    return db.config(**values)


def test_client_certificate_auth_with_encrypted_private_key(security_db, certificates):
    session = DatabaseSession(client_config(security_db, certificates))
    try:
        session.connect(); assert_tls(session)
        if security_db.driver == "postgresql":
            assert "CN=security_client" in session.execute("SELECT client_dn FROM pg_stat_ssl WHERE pid=pg_backend_pid()")[0].rows[0][0]
    finally:
        session.close()


@pytest.mark.parametrize("wrong", ["missing", "revoked", "key-password"])
def test_required_client_certificate_and_revocation_are_enforced(security_db, certificates, wrong):
    from opennavicat.transport import TLSKeyPasswordError
    config = client_config(security_db, certificates)
    if wrong == "missing":
        config = replace(config, ssl_cert="", ssl_key="", ssl_key_password="")
    elif wrong == "revoked":
        config = replace(config, ssl_cert=str(certificates["revoked-client"]),
                         ssl_key=str(certificates["revoked-client-key"]), ssl_key_password="")
    else:
        config = replace(config, ssl_key_password="incorrect-test-passphrase")
    session = DatabaseSession(config)
    try:
        with pytest.raises(TLSKeyPasswordError if wrong == "key-password" else DatabaseError):
            session.connect()
        assert session.connection is None
    finally:
        session.close()


def test_client_crl_checks_the_actual_server_certificate(security_db, certificates):
    session = DatabaseSession(security_db.config(ssl_crl=str(certificates["empty-crl"])))
    try:
        session.connect(); assert_tls(session)
    finally:
        session.close()
    session = DatabaseSession(security_db.config(ssl_crl=str(certificates["server-crl"])))
    try:
        with pytest.raises(DatabaseError) as error:
            session.connect()
        # libpq may expose only OpenSSL's generic verify error. The identical
        # connection succeeded with an empty same-issuer CRL immediately above.
        assert any(word in str(error.value).lower() for word in ("revok", "certificate verify failed"))
        assert not error.value.authentication_required
        assert session.connection is None
    finally:
        session.close()


class OwnedSSHServer:
    """A real SSH peer: authentication and direct-tcpip only to the owned DB."""
    def __init__(self, db, directory, *, combined=False):
        import paramiko
        self.paramiko = paramiko; self.db = db; self.combined = combined
        self.host_key = paramiko.RSAKey.generate(2048)
        self.client_key = paramiko.RSAKey.generate(2048)
        self.private_key = directory / "encrypted-ssh-key.pem"
        self.client_key.write_private_key_file(str(self.private_key), password=KEY_PASSWORD)
        self.private_key.chmod(0o600)
        self.agent_key = directory / "agent-only-ssh-key.pem"
        self.client_key.write_private_key_file(str(self.agent_key)); self.agent_key.chmod(0o600)
        self.pin = self.host_key.get_name() + " " + self.host_key.get_base64()
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(self.host_key.asbytes()).digest()).decode().rstrip("=")
        self.stop = threading.Event(); self.transports = []; self.destinations = []
        self.authentication = []; self.auth_attempts = 0; self.threads = []
        self.listener = socket.socket(); self.listener.bind(("127.0.0.1", 0)); self.listener.listen()
        self.listener.settimeout(0.2); self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True); self.thread.start()
    def accept(self):
        while not self.stop.is_set():
            try:
                client, _ = self.listener.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            thread = threading.Thread(target=self.serve, args=(client,), daemon=True)
            self.threads.append(thread); thread.start()
    def serve(self, client):
        owner, paramiko = self, self.paramiko
        destinations = {}
        class Server(paramiko.ServerInterface):
            key_seen = False
            def get_allowed_auths(self, username):
                return "password" if owner.combined and self.key_seen else "publickey,password"
            def check_auth_password(self, username, password):
                owner.auth_attempts += 1
                if username == "security_ssh" and password == SSH_PASSWORD and (not owner.combined or self.key_seen):
                    owner.authentication.append("password")
                    return paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED
            def check_auth_publickey(self, username, key):
                owner.auth_attempts += 1
                if username == "security_ssh" and key.asbytes() == owner.client_key.asbytes():
                    self.key_seen = True; owner.authentication.append("publickey")
                    return paramiko.AUTH_PARTIALLY_SUCCESSFUL if owner.combined else paramiko.AUTH_SUCCESSFUL
                return paramiko.AUTH_FAILED
            def check_channel_direct_tcpip_request(self, chanid, origin, destination):
                owner.destinations.append(destination)
                if destination[1] != owner.db.port or destination[0] not in {"localhost", "db.test", "wrong.test", "127.0.0.1"}:
                    return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
                destinations[chanid] = destination
                return paramiko.OPEN_SUCCEEDED
        transport = paramiko.Transport(client); self.transports.append(transport)
        transport.add_server_key(self.host_key); transport.use_compression(True)
        try:
            transport.start_server(server=Server())
            while transport.is_active() and not self.stop.is_set():
                channel = transport.accept(0.2)
                if channel is not None:
                    thread = threading.Thread(target=self.forward, args=(channel,), daemon=True)
                    self.threads.append(thread); thread.start()
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            transport.close(); client.close()
    def forward(self, channel):
        try:
            with socket.create_connection(("127.0.0.1", self.db.port), 3) as remote:
                while not self.stop.is_set():
                    readable, _, _ = select.select([channel, remote], [], [], 0.2)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (remote if source is channel else channel).sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            with contextlib.suppress(OSError, EOFError, self.paramiko.SSHException):
                channel.close()
    def close(self):
        self.stop.set(); self.listener.close()
        for transport in self.transports:
            transport.close()
        self.thread.join(2)
        for thread in self.threads:
            thread.join(2)
        self.private_key.unlink(missing_ok=True)
        self.agent_key.unlink(missing_ok=True)


@pytest.fixture
def ssh_server(security_db, tmp_path):
    server = OwnedSSHServer(security_db, tmp_path)
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def isolated_tunnels(monkeypatch):
    # The backend exposes known_hosts_paths=[] specifically for isolated peers.
    # No test may inspect or trust the user's real system/user known_hosts.
    from opennavicat.transport import SSHTunnel
    original_init, original_start = SSHTunnel.__init__, SSHTunnel.start
    endpoints = []
    def init(self, config, remote_host, remote_port, known_hosts_paths=None):
        return original_init(self, config, remote_host, remote_port, known_hosts_paths=[])
    def start(self):
        result = original_start(self)
        endpoints.append(self.endpoint)
        return result
    monkeypatch.setattr(SSHTunnel, "__init__", init)
    monkeypatch.setattr(SSHTunnel, "start", start)
    return endpoints


def ssh_config(db, server, **overrides):
    values = dict(host="db.test", ssh_enabled=True, ssh_host="127.0.0.1", ssh_port=server.port,
                  ssh_username="security_ssh", ssh_password=SSH_PASSWORD,
                  ssh_auth_method="password", ssh_host_key=server.pin)
    values.update(overrides)
    return db.config(**values)


def endpoint_closed(endpoint):
    try:
        with socket.create_connection(endpoint, 0.1):
            return False
    except OSError:
        return True


def test_unknown_ssh_key_is_not_authenticated_then_explicit_pin_works(security_db, ssh_server, isolated_tunnels):
    from opennavicat.transport import SSHHostKeyError
    config = ssh_config(security_db, ssh_server, ssh_host_key="")
    session = DatabaseSession(config)
    try:
        with pytest.raises(SSHHostKeyError) as error:
            session.connect()
        assert error.value.expected is None
        assert error.value.pin == ssh_server.pin and error.value.fingerprint == ssh_server.fingerprint
        assert ssh_server.auth_attempts == 0
        session.config = replace(config, ssh_host_key=error.value.pin)
        session.connect(); assert_tls(session)
        assert ssh_server.destinations and all(host == "db.test" for host, _ in ssh_server.destinations)
    finally:
        session.close()
    assert isolated_tunnels and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_mismatched_ssh_pin_fails_before_authentication(security_db, ssh_server, isolated_tunnels):
    from opennavicat.transport import SSHHostKeyError
    import paramiko
    wrong = paramiko.RSAKey.generate(2048)
    session = DatabaseSession(ssh_config(security_db, ssh_server,
        ssh_host_key=wrong.get_name() + " " + wrong.get_base64()))
    try:
        with pytest.raises(SSHHostKeyError) as error:
            session.connect()
        assert error.value.expected
        assert ssh_server.auth_attempts == 0
        assert not isolated_tunnels
    finally:
        session.close()


def test_wrong_ssh_password_fails_and_transport_is_cleaned(security_db, ssh_server, isolated_tunnels):
    import paramiko
    session = DatabaseSession(ssh_config(security_db, ssh_server, ssh_password="wrong-test-password"))
    try:
        with pytest.raises((DatabaseError, paramiko.AuthenticationException)):
            session.connect()
        assert session.connection is None
        wait_for(lambda: all(not transport.is_active() for transport in ssh_server.transports))
    finally:
        session.close()


def test_refused_ssh_socket_does_not_open_a_database_forward(security_db, ssh_server, isolated_tunnels):
    config = ssh_config(security_db, ssh_server, connect_timeout=1)
    ssh_server.close()
    session = DatabaseSession(config)
    try:
        with pytest.raises(DatabaseError) as error:
            session.connect()
        assert not error.value.authentication_required
        assert session.connection is None and not isolated_tunnels
    finally:
        session.close()


def test_refused_ssh_forward_channel_closes_local_listener(security_db, ssh_server, isolated_tunnels):
    session = DatabaseSession(ssh_config(security_db, ssh_server, host="blocked.test"))
    try:
        with pytest.raises(DatabaseError):
            session.connect()
        assert session.connection is None
        assert ("blocked.test", security_db.port) in ssh_server.destinations
    finally:
        session.close()
    assert isolated_tunnels and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_encrypted_ssh_private_key_and_compression(security_db, ssh_server, isolated_tunnels):
    session = DatabaseSession(ssh_config(security_db, ssh_server, ssh_auth_method="key", ssh_password="",
        ssh_key_file=str(ssh_server.private_key), ssh_key_passphrase=KEY_PASSWORD, ssh_compression=True))
    try:
        session.connect(); assert_tls(session)
        assert "publickey" in ssh_server.authentication
        assert any("zlib" in transport.remote_compression for transport in ssh_server.transports if transport.is_authenticated())
    finally:
        session.close()
    assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


@pytest.mark.parametrize("passphrase", ["", "incorrect-test-passphrase"])
def test_encrypted_ssh_key_requests_its_own_passphrase(security_db, ssh_server, isolated_tunnels, passphrase):
    from opennavicat.transport import SSHAuthenticationError
    session = DatabaseSession(ssh_config(security_db, ssh_server, ssh_auth_method="key", ssh_password="",
        ssh_key_file=str(ssh_server.private_key), ssh_key_passphrase=passphrase))
    try:
        with pytest.raises(SSHAuthenticationError) as error:
            session.connect()
        assert error.value.needs_passphrase
        assert session.connection is None and not isolated_tunnels
    finally:
        session.close()


def test_combined_ssh_key_and_password_partial_auth(security_db, tmp_path, isolated_tunnels):
    server = OwnedSSHServer(security_db, tmp_path, combined=True)
    session = DatabaseSession(ssh_config(security_db, server, ssh_auth_method="password+key",
        ssh_key_file=str(server.private_key), ssh_key_passphrase=KEY_PASSWORD))
    try:
        session.connect(); assert_tls(session)
        assert "publickey" in server.authentication and "password" in server.authentication
    finally:
        session.close(); server.close()


def test_combined_server_refuses_key_without_second_factor(security_db, tmp_path, isolated_tunnels):
    from opennavicat.transport import SSHAuthenticationError
    server = OwnedSSHServer(security_db, tmp_path, combined=True)
    session = DatabaseSession(ssh_config(security_db, server, ssh_auth_method="key", ssh_password="",
        ssh_key_file=str(server.private_key), ssh_key_passphrase=KEY_PASSWORD))
    try:
        with pytest.raises(SSHAuthenticationError):
            session.connect()
        assert "publickey" in server.authentication and "password" not in server.authentication
        assert not isolated_tunnels
    finally:
        session.close(); server.close()


def test_client_certificate_auth_through_ssh_preserves_tls_identity(security_db, certificates, ssh_server, isolated_tunnels):
    config = ssh_config(security_db, ssh_server, username="security_client", ssl_cert=str(certificates["client"]),
        ssl_key=str(certificates["encrypted-client-key"]), ssl_key_password=KEY_PASSWORD)
    session = DatabaseSession(config)
    try:
        session.connect(); assert_tls(session)
        assert ("db.test", security_db.port) in ssh_server.destinations
    finally:
        session.close()
    assert isolated_tunnels and all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)


def test_owned_agent_authentication_never_uses_user_agent(security_db, ssh_server, isolated_tunnels, tmp_path, monkeypatch):
    import paramiko
    if not shutil.which("ssh-agent") or not shutil.which("ssh-add"):
        pytest.skip("Owned SSH agent executables are unavailable on this platform")
    # macOS Unix-domain paths are limited to 104 bytes; pytest's full temp
    # directory can exceed that before an agent filename is appended.
    agent_directory = tempfile.TemporaryDirectory(prefix="onv-agent-", dir="/tmp")
    agent_socket = Path(agent_directory.name) / "agent.sock"
    process = subprocess.Popen(["ssh-agent", "-D", "-a", str(agent_socket)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(agent_socket.exists)
        environment = dict(os.environ, SSH_AUTH_SOCK=str(agent_socket), SSH_ASKPASS_REQUIRE="never")
        command(["ssh-add", str(ssh_server.agent_key)], env=environment, stdin=subprocess.DEVNULL, timeout=5)
        monkeypatch.setenv("SSH_AUTH_SOCK", str(agent_socket))
        agent = paramiko.Agent()
        try:
            assert any(key.asbytes() == ssh_server.client_key.asbytes() for key in agent.get_keys())
        finally:
            agent.close()
        session = DatabaseSession(ssh_config(security_db, ssh_server, ssh_auth_method="agent", ssh_password=""))
        try:
            session.connect(); assert_tls(session)
            assert "publickey" in ssh_server.authentication
        finally:
            session.close()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        agent_directory.cleanup()


def test_verify_full_over_ssh_uses_original_name_and_cleans_failed_tls(security_db, ssh_server, isolated_tunnels):
    good = DatabaseSession(ssh_config(security_db, ssh_server))
    try:
        good.connect(); assert_tls(good)
    finally:
        good.close()
    bad = DatabaseSession(ssh_config(security_db, ssh_server, host="wrong.test"))
    try:
        with pytest.raises(DatabaseError):
            bad.connect()
        assert bad.connection is None
    finally:
        bad.close()
    assert len(isolated_tunnels) == 2
    assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)
    assert ("db.test", security_db.port) in ssh_server.destinations
    assert ("wrong.test", security_db.port) in ssh_server.destinations


def test_ssh_cancel_reconnect_and_listener_cleanup(security_db, ssh_server, isolated_tunnels):
    session = DatabaseSession(ssh_config(security_db, ssh_server))
    failures = []
    session.connect(); assert_tls(session)
    def run_long():
        try:
            session.execute("SELECT pg_sleep(20)" if security_db.driver == "postgresql" else "SELECT SLEEP(20)")
        except Exception as error:
            failures.append(error)
    thread = threading.Thread(target=run_long, daemon=True); thread.start()
    try:
        def active_on_server():
            sql = ("SELECT count(*) FROM pg_stat_activity WHERE usename='security_password' AND state='active' AND query LIKE '%pg_sleep%'"
                   if security_db.driver == "postgresql" else
                   "SELECT count(*) FROM information_schema.PROCESSLIST WHERE USER='security_password' AND INFO LIKE '%SLEEP%'")
            return int(security_db.admin_sql(sql)) > 0
        wait_for(active_on_server, timeout=5)
        session.cancel(); thread.join(5)
        assert not thread.is_alive() and failures
        assert session.execute("SELECT 7")[0].rows == [[7]]
        session.close()
        assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)
        session.connect(); assert_tls(session)
    finally:
        if thread.is_alive():
            session.cancel(); thread.join(5)
        session.close()
    assert all(endpoint_closed(endpoint) for endpoint in isolated_tunnels)
