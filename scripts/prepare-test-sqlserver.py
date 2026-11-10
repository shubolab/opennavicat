#!/usr/bin/env python3
"""Run tests against one disposable native SQL Server 2022 container.

Only fixture credentials/CA files in a private temporary directory are exposed
to the child. Evidence is redacted. An explicit existing endpoint environment
variable remains supported and its external resources are never removed.

Microsoft's supported platform/configuration references:
https://learn.microsoft.com/sql/linux/install-upgrade/quickstart-install-docker
https://learn.microsoft.com/sql/linux/sql-server-linux-configure-mssql-conf
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET


IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
LABEL = "opennavicat.sqlserver-fixture"


def _private(path, contents):
    path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
    path.chmod(0o600)
    return path


def make_certificates(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    directory.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    def authority(common):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
        return key, cert
    key, ca = authority("OpenNavicat disposable SQL Server CA")
    _, wrong = authority("OpenNavicat unrelated SQL Server CA")
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256()))
    for name, certificate in (("ca.pem", ca), ("wrong-ca.pem", wrong), ("server.pem", server)):
        _private(directory/name, certificate.public_bytes(serialization.Encoding.PEM))
    _private(directory/"server.key", server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    _private(directory/"mssql.conf", "[network]\ntlscert = /owned-certs/server.pem\ntlskey = /owned-certs/server.key\ntlsprotocols = 1.2\nforceencryption = 0\n")
    return {"ca": str(directory/"ca.pem"), "wrong_ca": str(directory/"wrong-ca.pem")}


def _docker(*arguments, check=True, timeout=180):
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Never echo docker argv/env or backend error text with fixture secrets.
        raise RuntimeError("Owned SQL Server Docker operation failed")
    return result


def require_native_docker():
    info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
    if info.get("OSType") != "linux" or info.get("Architecture", "").lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("Owned SQL Server requires a native Linux amd64 Docker daemon; emulation is not used")


def probe_tls(endpoint, *, create_database=False):
    """External TDS socket, CA + SAN verification, actual query and encryption."""
    import pytds
    import pytds.tls
    from opennavicat.sqlserver_session import validate_tds_hostname
    pytds.tls.validate_host = validate_tds_hostname  # Pinned driver's public compatibility hook.
    material = endpoint.get("tls", {})
    ca = endpoint.get("ssl_ca") or material.get("ca")
    if not ca:
        raise RuntimeError("The owned SQL Server endpoint has no test CA")
    connection = pytds.connect(dsn=endpoint["host"], port=endpoint["port"],
        user=endpoint["username"], password=endpoint["password"], database="master",
        cafile=ca, validate_host=True, enc_login_only=False, autocommit=True,
        login_timeout=3, timeout=5, disable_connect_retry=True)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(80)),encrypt_option FROM sys.dm_exec_connections WHERE session_id=@@SPID")
            version, encrypted = cursor.fetchone()
            if encrypted != "TRUE":
                raise RuntimeError("The external SQL Server readiness query was not encrypted")
            if create_database:
                cursor.execute("IF DB_ID(N'opennavicat_test') IS NULL CREATE DATABASE [opennavicat_test]")
            return {"external_tls_select": True, "encrypted": True, "server_version": version}
        finally:
            cursor.close()
    finally:
        connection.close()


@contextmanager
def owned_sqlserver(evidence, *, startup_timeout=180):
    require_native_docker()
    token = uuid.uuid4().hex
    name = "opennavicat-sqlserver-owned-" + token[:16]
    evidence.update(mode="owned-docker", container=name, image=IMAGE, cleanup_removed=False)
    with tempfile.TemporaryDirectory(prefix="opennavicat-sqlserver-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        material = make_certificates(directory/"certs")
        password = "ONv!" + secrets.token_urlsafe(30) + "9aA"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::add-mask::" + password, flush=True)
        environment = _private(directory/"container.env", "ACCEPT_EULA=Y\nMSSQL_PID=Developer\nMSSQL_MEMORY_LIMIT_MB=2048\nMSSQL_SA_PASSWORD=" + password + "\n")
        try:
            evidence["phase"] = "docker-start"
            # Root is restricted to this disposable fixture container: generated
            # key material stays mode0600, with no host UID/permission changes.
            _docker("run", "--detach", "--rm", "--name", name,
                "--label", "opennavicat.test=true", "--label", LABEL + "=" + token,
                "--user", "0:0", "--memory", "3g", "--env-file", str(environment),
                "--publish", "127.0.0.1::1433", "--mount", f"type=bind,source={directory/'certs'},target=/owned-certs,readonly",
                "--mount", f"type=bind,source={directory/'certs/mssql.conf'},target=/var/opt/mssql/mssql.conf,readonly", IMAGE)
            mapping = _docker("port", name, "1433/tcp").stdout.strip()
            if not mapping.startswith("127.0.0.1:") or "\n" in mapping:
                raise RuntimeError("The owned SQL Server port is not loopback-only")
            port = int(mapping.rsplit(":", 1)[1])
            endpoint = dict(host="localhost", port=port, username="sa", password=password,
                database="opennavicat_test", ssl_mode="disable", tls=material)
            evidence["phase"] = "external-tls-readiness"
            deadline = time.monotonic() + startup_timeout
            while True:
                try:
                    evidence.update(probe_tls(endpoint, create_database=True))
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Owned SQL Server failed external verified TLS readiness") from None
                    state = _docker("inspect", "--format", "{{.State.Running}}", name, check=False)
                    if state.returncode or state.stdout.strip() != "true":
                        raise RuntimeError("Owned SQL Server stopped before external verified TLS readiness") from None
                    time.sleep(0.5)
            filename = _private(directory/"endpoint.json", json.dumps(endpoint))
            evidence["port_loopback_only"] = True
            evidence["image_id"] = _docker("inspect", "--format", "{{.Image}}", name).stdout.strip()
            evidence["phase"] = "running-tests"
            yield filename
        except BaseException:
            logs = _docker("logs", "--tail", "50", name, check=False, timeout=5)
            evidence["server_log_tail"] = (logs.stdout + getattr(logs, "stderr", ""))[-8000:].replace(password, "<redacted>")
            raise
        finally:
            owner = _docker("inspect", "--format", '{{index .Config.Labels "' + LABEL + '"}}', name, check=False, timeout=20)
            if owner.returncode == 0:
                if owner.stdout.strip() != token:
                    raise RuntimeError("Refusing to clean a SQL Server container without this fixture's exact owner token")
                _docker("rm", "--force", "--volumes", name, timeout=30)
                if _docker("inspect", name, check=False, timeout=20).returncode == 0:
                    raise RuntimeError("Owned SQL Server container cleanup did not complete")
            else:
                remaining = _docker("container", "ls", "--all", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}", check=False, timeout=5)
                if remaining.returncode or name in remaining.stdout.splitlines():
                    raise RuntimeError("Could not verify absence of the owned SQL Server container")
            evidence["cleanup_removed"] = True


def check_junit(path):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases or any(case.find("skipped") is not None for case in cases):
        raise RuntimeError("Protocol integration requires executed cases with zero skips")
    sql = [case for case in cases if case.get("classname", "").endswith("test_sqlserver_session") and case.get("name", "").startswith("test_real_")]
    if len(sql) < 17 or any(case.find("failure") is not None or case.find("error") is not None for case in sql):
        raise RuntimeError("Protocol integration did not pass all 17 real SQL Server cases")
    return {"tests": len(cases), "sqlserver_real_tests": len(sql), "skipped": 0}


def run_tests(command, endpoint, timeout):
    environment = dict(os.environ, OPENNAVICAT_TEST_SQLSERVER="1", OPENNAVICAT_TEST_SQLSERVER_ENDPOINT=str(endpoint))
    process = subprocess.Popen(command, env=environment, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            if os.name == "posix": os.killpg(process.pid, signal.SIGTERM)
            else: process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix": os.killpg(process.pid, signal.SIGKILL)
                else: process.kill()
                process.wait(timeout=10)


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/security-integration/sqlserver-fixture.json"))
    parser.add_argument("--junit-report", type=Path, required=True)
    parser.add_argument("--test-timeout", type=int, default=900)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("A test command after -- is required")
    evidence = {"passed": False}
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    for number in (signal.SIGINT, signal.SIGTERM): signal.signal(number, interrupted)
    result = 1
    try:
        arguments.junit_report.unlink(missing_ok=True)
        existing = os.environ.get("OPENNAVICAT_TEST_SQLSERVER_ENDPOINT")
        if existing:
            endpoint = Path(existing)
            evidence.update(mode="existing-owned-endpoint", external_resources_untouched=True)
            evidence["phase"] = "external-tls-readiness"
            evidence.update(probe_tls(json.loads(endpoint.read_text())))
            evidence["phase"] = "running-tests"
            result = run_tests(command, endpoint, arguments.test_timeout)
        else:
            with owned_sqlserver(evidence) as endpoint:
                result = run_tests(command, endpoint, arguments.test_timeout)
        if result == 0:
            evidence["phase"] = "junit-validation"
            evidence.update(check_junit(arguments.junit_report))
            evidence["passed"] = True
            evidence["phase"] = "complete"
    except (Exception, KeyboardInterrupt) as error:
        evidence["error_type"] = type(error).__name__
        if isinstance(error, ModuleNotFoundError):
            evidence["missing_module"] = error.name
        print("Owned SQL Server integration failed; see redacted fixture evidence.", file=sys.stderr)
        result = 1
    finally:
        arguments.evidence.parent.mkdir(parents=True, exist_ok=True)
        arguments.evidence.write_text(json.dumps(evidence, indent=2) + "\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
