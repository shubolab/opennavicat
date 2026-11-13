"""Fresh publisher-pinned native ARM64 Oracle fixture, no existing host data.

The runner is the only caller. Passwords stay in process memory and one new
container tmpfs, never argv, Docker Config.Env, receipts or endpoint files.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid

import oracledb

IMAGE = "docker.io/gvenzl/oracle-free@sha256:b0c7703767ca9403b50c8408d4c6aa824ee6bd93fbed8dc765a9073acf087740"
IMAGE_CONFIG = "sha256:ce865b4992bd4ba70f0042cd01403068111965583f0c4ed9bf0627022da89849"
SECRET_DIR = "/run/opennavicat-owned-oracle"
SECRET_FILE = SECRET_DIR + "/password"


class OwnedOracle:
    def __init__(self, out, *, docker_host=None):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=False)
        self.run_id = uuid.uuid4().hex
        self.name = "opennavicat-oracle-test-" + self.run_id[:12]
        self.labels = {"owner": "opennavicat-codex", "task": "oracle-basic-backend-test", "run": self.run_id}
        self.schema = "ONV_" + self.run_id[:16].upper()
        self.denied_schema = "ONVD_" + self.run_id[:16].upper()
        self.admin_password = "Aa1_" + secrets.token_hex(12)
        self.app_password = "Bb2_" + secrets.token_hex(12)
        self.denied_password = "Cc3_" + secrets.token_hex(12)
        self.connections = []
        self.cid = self.port = None
        self.docker_host = docker_host
        self.anonymous = self.out / "docker-anonymous"
        self.anonymous.mkdir()
        (self.anonymous / "config.json").write_text("{}\n")
        self.receipt = {"run_id": self.run_id, "owner_labels": self.labels, "image": IMAGE,
            "image_config": IMAGE_CONFIG, "publisher": "gvenzl (not Oracle official)",
            "tag_observed": "23.26.3-slim-faststart", "platform": "linux/arm64",
            "data_volumes": [], "credentials_persisted": False, "reference_ui_acceptance": False}
        self.save()

    def docker(self, *args, input=None, timeout=60, check=True):
        cmd = ["docker"] + (["--host", self.docker_host] if self.docker_host else [])
        result = subprocess.run(cmd + ["--config", str(self.anonymous), *args], input=input,
                                text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError("Owned Oracle Docker operation failed: " + args[0])
        return result

    def save(self):
        (self.out / "fixture.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def inspect(self):
        data = json.loads(self.docker("inspect", self.cid).stdout)[0]
        assert data["Id"] == self.cid and data["Image"] == IMAGE_CONFIG
        assert all(data["Config"]["Labels"].get(k) == v for k, v in self.labels.items())
        assert not data["HostConfig"].get("Binds") and not data["HostConfig"].get("VolumesFrom")
        assert all(m["Type"] == "tmpfs" and m["Destination"] == SECRET_DIR for m in data.get("Mounts", []))
        assert set(data["HostConfig"].get("Tmpfs", {})) == {SECRET_DIR}
        for secret in (self.admin_password, self.app_password, self.denied_password):
            assert secret not in str(data["Config"])
        return data

    def connect(self, username, password, *, mode=oracledb.AUTH_MODE_DEFAULT):
        connection = oracledb.connect(user=username, password=password, host="127.0.0.1",
            port=self.port, service_name="FREEPDB1", protocol="tcp", retry_count=0, tcp_connect_timeout=3, mode=mode)
        connection.call_timeout = 10000
        self.connections.append(connection)
        assert connection.thin
        return connection

    def start(self):
        engine = json.loads(self.docker("info", "--format", "{{json .}}", timeout=15).stdout)
        if engine["Architecture"] not in {"aarch64", "arm64"} or engine["OSType"] != "linux":
            raise RuntimeError("This pinned fixture requires a native Linux ARM64 Docker engine; emulation is not accepted")
        local = json.loads(self.docker("image", "inspect", IMAGE).stdout)[0]
        assert local["Id"] == IMAGE_CONFIG and local["Architecture"] == "arm64"
        gate = ("i=0; while [ ! -s " + SECRET_FILE + " ]; do i=$((i+1)); "
                "[ \"$i\" -lt 120 ] || exit 67; sleep 1; done; exec container-entrypoint.sh")
        args = ["create", "--name", self.name, "--platform", "linux/arm64", "--memory", "4g",
            "--cpus", "2", "--shm-size", "1g", "--publish", "127.0.0.1::1521", "--stop-timeout", "30",
            "--tmpfs", SECRET_DIR + ":rw,noexec,nosuid,size=65536,mode=1777",
            "--env", "ORACLE_PASSWORD_FILE=" + SECRET_FILE]
        for key, value in self.labels.items():
            args += ["--label", key + "=" + value]
        self.cid = self.docker(*args, "--entrypoint", "/bin/sh", IMAGE, "-c", gate).stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{64}", self.cid)
        self.receipt["container_id"] = self.cid
        self.save()
        data = self.inspect()
        caps = data["HostConfig"]
        assert caps["Memory"] == 4 * 1024 ** 3 and caps["NanoCpus"] == 2_000_000_000
        assert caps["ShmSize"] == 1024 ** 3
        self.receipt["limits"] = {k: caps[k] for k in ("Memory", "NanoCpus", "ShmSize")}
        self.docker("start", self.cid)
        self.docker("exec", "-i", self.cid, "/bin/sh", "-c", "umask 077; cat > " + SECRET_FILE,
                    input=self.admin_password)
        data = self.inspect()
        ports = data["NetworkSettings"]["Ports"]["1521/tcp"]
        assert len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1"
        self.port = int(ports[0]["HostPort"])
        self.receipt["loopback_endpoint"] = {"host": "127.0.0.1", "port": self.port}
        self.save()
        deadline = time.monotonic() + 360
        while True:
            try:
                self.admin = self.connect("system", self.admin_password)
                break
            except oracledb.Error:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Owned Oracle readiness deadline") from None
                time.sleep(1)
        self.docker("exec", self.cid, "rm", "--", SECRET_FILE)
        assert self.docker("exec", self.cid, "uname", "-m").stdout.strip() == "aarch64"
        self.receipt.update(actual_architecture="aarch64", server_version=self.admin.version,
                            tmpfs_password_file_removed=True)
        self._seed()
        self.save()

    def _seed(self):
        self.receipt["seed_stage"] = "create_users"
        self.save()
        with self.admin.cursor() as cursor:
            for name, password in ((self.schema, self.app_password), (self.denied_schema, self.denied_password)):
                assert re.fullmatch(r"ONV[D]?_[A-F0-9]{16}", name)
                assert re.fullmatch(r"[A-Za-z0-9_]{28}", password)
                cursor.execute('CREATE USER "' + name + '" IDENTIFIED BY "' + password + '" DEFAULT TABLESPACE USERS QUOTA 8M ON USERS')
                cursor.execute('GRANT CREATE SESSION TO "' + name + '"')
            cursor.execute('GRANT CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE MATERIALIZED VIEW TO "' + self.schema + '"')
        # SYS alone grants this SYS-owned observation view. This connection is
        # fixture provisioning, never the normal-user application session.
        sys = self.connect("sys", self.admin_password, mode=oracledb.AUTH_MODE_SYSDBA)
        with sys.cursor() as cursor:
            cursor.execute('GRANT SELECT ON SYS.V_$SESSION TO "' + self.schema + '"')
            cursor.execute('GRANT SELECT ON SYS.V_$SQL TO "' + self.schema + '"')
        self.app = self.connect(self.schema, self.app_password)
        with self.app.cursor() as cursor:
            self.receipt["seed_stage"] = "create_facts"
            self.save()
            cursor.execute("CREATE TABLE owned_facts (id NUMBER(38,0) PRIMARY KEY, label NVARCHAR2(80), amount NUMBER, negative_scale NUMBER(10,-2), created DATE DEFAULT DATE '2026-01-02')")
            data = [(9007199254740993, "generated Oracle 样例", Decimal("0.12345678901234567890123456789012345678"), 12300),
                    (99999999999999999999999999999999999999, "second synthetic", Decimal("123456789012345678.1234567890123456789"), -1200),
                    (3, "filter ' OR 1=1 -- % _", None, 0)]
            cursor.executemany("INSERT INTO owned_facts(id,label,amount,negative_scale) VALUES (:1,:2,:3,:4)", data)
            self.receipt["seed_stage"] = "create_view"
            self.save()
            cursor.execute("CREATE VIEW owned_facts_view AS SELECT id,label,amount FROM owned_facts")
            self.receipt["seed_stage"] = "create_materialized_view"
            self.save()
            cursor.execute("CREATE MATERIALIZED VIEW owned_facts_mv BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND AS SELECT id FROM owned_facts")
            self.receipt["seed_stage"] = "create_lob"
            self.save()
            cursor.execute("CREATE TABLE owned_lobs (id NUMBER PRIMARY KEY, payload CLOB)")
            cursor.execute("INSERT INTO owned_lobs VALUES (1,TO_CLOB('synthetic private LOB'))")
            self.receipt["seed_stage"] = "create_sleep_function"
            self.save()
            cursor.execute("CREATE FUNCTION owned_sleep(seconds NUMBER) RETURN NUMBER AS BEGIN DBMS_SESSION.SLEEP(seconds); RETURN 1; END;")
            self.receipt["seed_stage"] = "create_marker"
            self.save()
            cursor.execute("CREATE TABLE owned_marker (run_id VARCHAR2(32))")
            cursor.execute("INSERT INTO owned_marker VALUES (:1)", [self.run_id])
        self.app.commit()
        self.receipt.update(owned_schema=self.schema, denied_schema=self.denied_schema,
                            test_only_observation_grants=["SELECT ON SYS.V_$SESSION", "SELECT ON SYS.V_$SQL"],
                            owned_objects=["OWNED_FACTS", "OWNED_FACTS_VIEW", "OWNED_FACTS_MV", "OWNED_LOBS", "OWNED_SLEEP", "OWNED_MARKER"])

    def environment(self):
        # Passed only through the child process environment, never a host file.
        return {"OPENNAVICAT_TEST_ORACLE_PORT": str(self.port), "OPENNAVICAT_TEST_ORACLE_USER": self.schema,
            "OPENNAVICAT_TEST_ORACLE_PASSWORD": self.app_password,
            "OPENNAVICAT_TEST_ORACLE_DENIED_USER": self.denied_schema,
            "OPENNAVICAT_TEST_ORACLE_DENIED_PASSWORD": self.denied_password,
            "OPENNAVICAT_TEST_ORACLE_RUN_ID": self.run_id}

    def close(self):
        session_check_failed = False
        if hasattr(self, "admin"):
            try:
                with self.admin.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM V$SESSION WHERE MODULE='OpenNavicat Oracle Basic'")
                    remaining = int(cursor.fetchone()[0])
                    self.receipt["application_sessions_after_test_process_exit"] = remaining
                    session_check_failed = remaining != 0
            except Exception:
                session_check_failed = True
                self.receipt["application_session_check_failed"] = True
        for connection in reversed(self.connections):
            with contextlib.suppress(Exception):
                connection.close()
        self.connections.clear()
        if self.cid:
            self.inspect()
            try:
                self.docker("stop", "--time", "30", self.cid, timeout=45, check=False)
            except Exception as error:
                # Force-remove the same label-verified newly created container
                # even if a graceful stop times out. No volume/global pruning.
                self.receipt["graceful_stop_error_class"] = type(error).__name__
            self.docker("rm", "--force", self.cid)
            result = self.docker("inspect", self.cid, check=False)
            assert result.returncode == 1 and "no such" in result.stderr.lower()
            self.receipt.update(removed=True, exact_id_absent=True, tmpfs_removed=True)
        self.save()
        if session_check_failed:
            raise RuntimeError("Application sessions were not verified closed; owned container was still removed")

    def scan_secrets(self):
        for path in self.out.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                for secret in (self.admin_password, self.app_password, self.denied_password):
                    if secret.encode() in data:
                        raise RuntimeError("A credential was found in owned Oracle evidence")
        self.receipt["secret_scan_passed"] = True
        self.save()
