"""Fresh publisher-pinned native amd64/arm64 Oracle, no existing host data.

The runner is the only caller. Passwords stay in process memory and one new
container tmpfs, never argv, Docker Config.Env, receipts or endpoint files.
"""
from __future__ import annotations

import contextlib
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import subprocess
import time
import uuid

import oracledb

IMAGE_MANIFEST = json.loads(Path(__file__).with_name("oracle_images.json").read_text())
PINS = IMAGE_MANIFEST["platforms"]
SECRET_DIR = "/run/opennavicat-owned-oracle"
SECRET_FILE = SECRET_DIR + "/password"
READINESS_SECONDS = 360
LOG_BYTES = 65536
LOG_LINES = 200
LOG_SECONDS = 5
LOG_CODES = 32
DOCKER_OPERATIONS = frozenset({"info", "pull", "image", "create", "inspect", "start", "exec", "stop", "rm", "logs"})
STARTUP_PHASES = frozenset({"engine_probe", "image_pull", "image_inspect", "container_create",
    "ownership_check", "container_start", "password_delivery", "endpoint_check", "readiness",
    "password_removal", "architecture_check", "seed", "complete"})


def _driver_code(error):
    detail = error.args[0] if getattr(error, "args", ()) else None
    code = getattr(detail, "full_code", None)
    return code if type(code) is str and re.fullmatch(r"(?:ORA|DPY|DPI)-[0-9]{4,5}", code) else None


def _bounded_command_output(command, *, timeout=LOG_SECONDS, limit=LOG_BYTES):
    """POSIX fixture CLI output stays in bounded memory, never a temporary file."""
    if os.name != "posix" or not all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "WEXITED", "P_PID")):
        raise RuntimeError("Owned Oracle log capture requires POSIX")
    output = bytearray()
    selector = selectors.DefaultSelector()
    process = None
    status = "captured"
    def leader_done():
        # Keep our waitable child's PID reserved until its entire owned group
        # has been signalled. poll()/wait() here could enable PGID reuse.
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline, drain_deadline = time.monotonic() + timeout, None
        while True:
            done = leader_done()
            if done and not selector.get_map():
                break
            now = time.monotonic()
            if done and drain_deadline is None:
                drain_deadline = min(deadline, now + .25)
            remaining = min(deadline, drain_deadline if drain_deadline is not None else deadline) - now
            if remaining <= 0:
                status = "deadline"
                break
            if not selector.get_map():
                time.sleep(min(.01, remaining))
            for key, _ in selector.select(min(.05, remaining)) if selector.get_map() else ():
                chunk = os.read(key.fd, min(4096, limit + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                room = limit - len(output)
                output.extend(chunk[:room])
                if len(chunk) > room:
                    status = "output_limit"
                    break
            if status == "output_limit":
                break
    finally:
        selector.close()
        if process is not None:
            try:
                # Always clean the reserved group, including descendants that
                # close stdout and outlive an already-exited Docker CLI.
                done = leader_done()
                if not done:
                    try:
                        if os.getpgid(process.pid) != process.pid:
                            raise RuntimeError("Owned Oracle log process group identity changed")
                    except ProcessLookupError:
                        if not leader_done():
                            raise
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin can report EPERM for an all-zombie group. This
                    # is pending, not proof of absence; check after reaping.
                    if not done:
                        raise
                process.wait(timeout=1)
                cleanup_deadline = time.monotonic() + 1
                while True:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    except PermissionError:
                        pass  # Pending zombie group; never treat EPERM as absence.
                    if time.monotonic() >= cleanup_deadline:
                        raise RuntimeError("Owned Oracle log process group absence was not verified")
                    time.sleep(.01)
            finally:
                process.stdout.close()
    if status == "captured" and process.returncode:
        status = "command_failed"
    return bytes(output), status


def _safe_log_summary(value):
    if type(value) is not dict:
        return {}
    result = {}
    if type(value.get("status")) is str and value["status"] in {"captured", "deadline", "output_limit", "command_failed", "unavailable"}:
        result["status"] = value["status"]
    if type(value.get("bytes_read")) is int and 0 <= value["bytes_read"] <= LOG_BYTES:
        result["bytes_read"] = value["bytes_read"]
    codes = value.get("codes")
    if type(codes) is list and len(codes) <= LOG_CODES:
        safe = []
        seen = set()
        for entry in codes:
            if (type(entry) is dict and type(entry.get("code")) is str
                    and re.fullmatch(r"ORA-[0-9]{5}|SP2-[0-9]{4}", entry["code"])
                    and entry["code"] not in seen and type(entry.get("count")) is int
                    and 1 <= entry["count"] <= LOG_BYTES):
                safe.append({"code": entry["code"], "count": entry["count"]})
                seen.add(entry["code"])
        result["codes"] = safe
    if value.get("code_limit_reached") is True:
        result["code_limit_reached"] = True
    return result


def startup_diagnostic(receipt):
    """Only finite classifications, bounded numbers and owned-state facts leave startup."""
    value = receipt.get("startup", {})
    if type(value) is not dict: return {}
    result = {}
    enums = {"phase": STARTUP_PHASES, "status": {"running", "failed", "ready"},
        "reason": {"readiness_deadline", "docker_operation", "docker_deadline", "fixture_contract",
                   "driver_error", "interrupted", "startup_failure", "container_exited"},
        "error_class": {"RuntimeError", "AssertionError", "TimeoutExpired", "DatabaseError",
                        "OperationalError", "InterfaceError", "KeyboardInterrupt", "OSError"}}
    for key, choices in enums.items():
        if type(value.get(key)) is str and value[key] in choices: result[key] = value[key]
    for key in ("elapsed_ms", "readiness_attempts"):
        if type(value.get(key)) is int and 0 <= value[key] <= 2**31 - 1: result[key] = value[key]
    if type(value.get("readiness_budget_seconds")) is int and value["readiness_budget_seconds"] == READINESS_SECONDS:
        result["readiness_budget_seconds"] = READINESS_SECONDS
    for key in ("driver_code", "last_driver_code"):
        code = value.get(key)
        if type(code) is str and re.fullmatch(r"(?:ORA|DPY|DPI)-[0-9]{4,5}", code): result[key] = code
    state = value.get("container_state", {})
    safe_state = {}
    if type(state) is dict:
        if type(state.get("status")) is str and state["status"] in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}:
            safe_state["status"] = state["status"]
        for key in ("running", "oom_killed"):
            if type(state.get(key)) is bool: safe_state[key] = state[key]
        if type(state.get("exit_code")) is int and -(2**31) <= state["exit_code"] < 2**31:
            safe_state["exit_code"] = state["exit_code"]
    if safe_state: result["container_state"] = safe_state
    if value.get("container_state_unavailable") is True: result["container_state_unavailable"] = True
    logs = _safe_log_summary(value.get("server_log"))
    if logs: result["server_log"] = logs
    return result


def native_architecture(engine):
    aliases = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}
    arch = aliases.get(engine.get("Architecture"))
    if engine.get("OSType") != "linux" or arch not in PINS:
        raise RuntimeError("Oracle fixture requires a native Linux amd64 or arm64 Docker engine; emulation is not accepted")
    return arch


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
        self.architecture = self.image = self.image_config = None
        self.docker_host = docker_host
        self.anonymous = self.out / "docker-anonymous"
        self.anonymous.mkdir()
        (self.anonymous / "config.json").write_text("{}\n")
        self.receipt = {"run_id": self.run_id, "owner_labels": self.labels, "container_name": self.name,
            "image": None, "image_config": None, "publisher": IMAGE_MANIFEST["publisher"],
            "tag_observed": IMAGE_MANIFEST["version_tag"], "index_digest": IMAGE_MANIFEST["index_digest"], "platform": None,
            "data_volumes": [], "credentials_persisted": False, "reference_ui_acceptance": False}
        self.save()

    def docker(self, *args, input=None, timeout=60, check=True):
        cmd = ["docker"] + (["--host", self.docker_host] if self.docker_host else [])
        operation = args[0] if args and args[0] in DOCKER_OPERATIONS else "other"
        try:
            result = subprocess.run(cmd + ["--config", str(self.anonymous), *args], input=input,
                                    text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(["docker", operation], timeout) from None
        except OSError:
            raise RuntimeError("Owned Oracle Docker operation unavailable: " + operation) from None
        if check and result.returncode:
            self.receipt["docker_failure"] = {"operation": operation, "exit_code": result.returncode}
            self.save()
            raise RuntimeError("Owned Oracle Docker operation failed: " + operation)
        return result

    def _server_log_summary(self):
        # The caller must first validate exact ID, image, owner and mounts.
        # Raw text and raw CLI exceptions never enter a receipt or exception.
        command = ["docker"] + (["--host", self.docker_host] if self.docker_host else [])
        command += ["--config", str(self.anonymous), "logs", "--tail", str(LOG_LINES), self.cid]
        try:
            raw, status = _bounded_command_output(command)
            counts = {}
            limited = False
            for match in re.finditer(rb"(?<![A-Za-z0-9_])(?:ORA-[0-9]{5}|SP2-[0-9]{4})(?![A-Za-z0-9_])", raw):
                code = match.group().decode("ascii")
                if code not in counts and len(counts) == LOG_CODES:
                    limited = True
                    continue
                counts[code] = counts.get(code, 0) + 1
            return _safe_log_summary({"status": status, "bytes_read": len(raw),
                "codes": [{"code": code, "count": count} for code, count in sorted(counts.items())],
                "code_limit_reached": limited})
        except Exception:
            return {"status": "unavailable"}

    def save(self):
        # A signal between Docker create and the next receipt must leave the
        # prior complete name/run/image ledger available for exact cleanup.
        target = self.out / "fixture.json"
        temporary = self.out / "fixture.json.tmp"
        temporary.write_text(json.dumps(self.receipt, indent=2) + "\n")
        temporary.replace(target)

    def inspect(self, *, timeout=60):
        data = json.loads(self.docker("inspect", self.cid, timeout=timeout).stdout)[0]
        assert data["Id"] == self.cid and data["Image"] == self.image_config
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

    def _startup_phase(self, phase):
        if type(phase) is not str or phase not in STARTUP_PHASES:
            raise ValueError("Unknown owned Oracle startup phase")
        self.receipt["startup"].update(phase=phase,
            elapsed_ms=max(0, int((time.monotonic() - self._startup_started) * 1000)))
        self.save()

    def _startup_failure(self, error):
        startup = self.receipt["startup"]
        reason = self._startup_reason or ("interrupted" if isinstance(error, KeyboardInterrupt)
            else "docker_deadline" if isinstance(error, subprocess.TimeoutExpired)
            else "fixture_contract" if isinstance(error, AssertionError)
            else "docker_operation" if self.receipt.get("docker_failure")
            else "driver_error" if isinstance(error, oracledb.Error) else "startup_failure")
        startup.update(status="failed", reason=reason, error_class=type(error).__name__,
            elapsed_ms=max(0, int((time.monotonic() - self._startup_started) * 1000)))
        if _driver_code(error): startup["driver_code"] = _driver_code(error)
        if not isinstance(error, KeyboardInterrupt) and type(self.cid) is str and re.fullmatch(r"[0-9a-f]{64}", self.cid):
            try:
                # Validate the same exact owner/image/mount contract before
                # trusting any state; never retain Config, argv, logs or Error.
                state = self.inspect(timeout=10).get("State", {})
                if type(state) is dict:
                    startup["container_state"] = {key: state.get(source) for key, source in (
                        ("status", "Status"), ("running", "Running"), ("exit_code", "ExitCode"), ("oom_killed", "OOMKilled"))}
                startup["server_log"] = self._server_log_summary()
            except Exception:
                startup["container_state_unavailable"] = True
        self.receipt["startup"] = startup_diagnostic(self.receipt)
        self.save()

    def start(self, *, pull=False):
        self._startup_started = time.monotonic()
        self._startup_reason = None
        self.receipt["startup"] = {"phase": "engine_probe", "status": "running", "elapsed_ms": 0}
        self.save()
        try:
            self._start(pull=pull)
        except BaseException as error:
            try: self._startup_failure(error)
            except Exception: self.receipt["startup_diagnostic_unavailable"] = True
            raise
        self.receipt["startup"].update(status="ready")
        self._startup_phase("complete")

    def _start(self, *, pull=False):
        engine = json.loads(self.docker("info", "--format", "{{json .}}", timeout=15).stdout)
        version = engine.get("ServerVersion")
        self.receipt["engine_probe"] = {
            "Architecture": engine.get("Architecture") if engine.get("Architecture") in {"amd64", "x86_64", "arm64", "aarch64"} else None,
            "OSType": engine.get("OSType") if engine.get("OSType") in {"linux", "windows"} else None,
            "ServerVersion": version if type(version) is str and re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", version) else None,
            "ServerErrors": None if engine.get("ServerErrors") is None else [] if engine.get("ServerErrors") == [] else ["reported"]}
        self.save()
        self.architecture = native_architecture(engine)
        pin = PINS[self.architecture]
        self.image, self.image_config = pin["image"], pin["config"]
        platform = "linux/" + self.architecture
        self.receipt.update(image=self.image, image_config=self.image_config, platform=platform,
                            engine_architecture=engine["Architecture"], native_architecture=self.architecture)
        self.save()
        if pull:
            self._startup_phase("image_pull")
            self.docker("pull", "--platform", platform, self.image, timeout=600)
        self._startup_phase("image_inspect")
        local = json.loads(self.docker("image", "inspect", self.image).stdout)[0]
        assert local["Id"] == self.image_config and local["Architecture"] == self.architecture
        assert local["Os"] == "linux" and not local["Config"].get("Volumes")
        gate = ("i=0; while [ ! -s " + SECRET_FILE + " ]; do i=$((i+1)); "
                "[ \"$i\" -lt 120 ] || exit 67; sleep 1; done; exec container-entrypoint.sh")
        args = ["create", "--name", self.name, "--platform", platform, "--memory", "4g",
            "--cpus", "2", "--shm-size", "1g", "--publish", "127.0.0.1::1521", "--stop-timeout", "30",
            "--tmpfs", SECRET_DIR + ":rw,noexec,nosuid,size=65536,mode=1777",
            "--env", "ORACLE_PASSWORD_FILE=" + SECRET_FILE]
        for key, value in self.labels.items():
            args += ["--label", key + "=" + value]
        self._startup_phase("container_create")
        self.cid = self.docker(*args, "--entrypoint", "/bin/sh", self.image, "-c", gate).stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{64}", self.cid)
        self.receipt["container_id"] = self.cid
        self.save()
        self._startup_phase("ownership_check")
        data = self.inspect()
        caps = data["HostConfig"]
        assert caps["Memory"] == 4 * 1024 ** 3 and caps["NanoCpus"] == 2_000_000_000
        assert caps["ShmSize"] == 1024 ** 3
        self.receipt["limits"] = {k: caps[k] for k in ("Memory", "NanoCpus", "ShmSize")}
        self._startup_phase("container_start")
        self.docker("start", self.cid)
        self._startup_phase("password_delivery")
        self.docker("exec", "-i", self.cid, "/bin/sh", "-c", "umask 077; cat > " + SECRET_FILE,
                    input=self.admin_password)
        self._startup_phase("endpoint_check")
        data = self.inspect()
        ports = data["NetworkSettings"]["Ports"]["1521/tcp"]
        assert len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1"
        self.port = int(ports[0]["HostPort"])
        self.receipt["loopback_endpoint"] = {"host": "127.0.0.1", "port": self.port}
        self.save()
        self.receipt["startup"].update(readiness_budget_seconds=READINESS_SECONDS, readiness_attempts=0)
        self._startup_phase("readiness")
        deadline = time.monotonic() + READINESS_SECONDS
        while True:
            if time.monotonic() >= deadline:
                self._startup_reason = "readiness_deadline"
                raise RuntimeError("Owned Oracle readiness deadline") from None
            self.receipt["startup"]["readiness_attempts"] += 1
            try:
                self.admin = self.connect("system", self.admin_password)
                if time.monotonic() >= deadline:
                    self._startup_reason = "readiness_deadline"
                    raise RuntimeError("Owned Oracle readiness deadline") from None
                break
            except oracledb.Error as error:
                code = _driver_code(error)
                if code: self.receipt["startup"]["last_driver_code"] = code
                else: self.receipt["startup"].pop("last_driver_code", None)
                if time.monotonic() >= deadline:
                    self._startup_reason = "readiness_deadline"
                    raise RuntimeError("Owned Oracle readiness deadline") from None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._startup_reason = "readiness_deadline"
                    raise RuntimeError("Owned Oracle readiness deadline") from None
                state = self.inspect(timeout=min(5, remaining)).get("State", {})
                if (type(state) is dict and state.get("Status") in {"exited", "dead"}
                        and state.get("Running") is False):
                    self._startup_reason = "container_exited"
                    raise RuntimeError("Owned Oracle container exited before readiness") from None
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        self._startup_phase("password_removal")
        self.docker("exec", self.cid, "rm", "--", SECRET_FILE)
        self._startup_phase("architecture_check")
        uname = self.docker("exec", self.cid, "uname", "-m").stdout.strip()
        assert uname == pin["uname"]
        self.receipt.update(actual_architecture=uname, server_version=self.admin.version,
                            tmpfs_password_file_removed=True)
        self._startup_phase("seed")
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


def cleanup_interrupted(out, *, docker_host=None):
    """Exact ledger-only teardown. Never start a container or connect to a DB."""
    out = Path(out)
    ledger = out / "owned-fixture" / "fixture.json"
    result = {"cleanup_only": True, "no_start": True, "no_database_connection": True,
              "ledger_found": ledger.is_file(), "passed": False}
    if not ledger.is_file():
        result.update(passed=True, no_owned_ledger=True)
        return result
    data = json.loads(ledger.read_text())
    run_id = data.get("run_id")
    if type(run_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise RuntimeError("Invalid Oracle cleanup run ledger")
    labels = {"owner": "opennavicat-codex", "task": "oracle-basic-backend-test", "run": run_id}
    name = "opennavicat-oracle-test-" + run_id[:12]
    if data.get("owner_labels") != labels or data.get("container_name") != name or data.get("data_volumes") != []:
        raise RuntimeError("Oracle cleanup ownership ledger does not match")
    cid = data.get("container_id")
    if cid is not None and (type(cid) is not str or not re.fullmatch(r"[a-f0-9]{64}", cid)):
        raise RuntimeError("Invalid Oracle cleanup exact container ID")
    pin = next((p for p in PINS.values() if p["image"] == data.get("image") and p["config"] == data.get("image_config")), None)
    # If image selection was never completed, no create command was authorized.
    # A precise name inspection is still needed before asserting absence.
    anonymous = out / "cleanup-docker-anonymous"
    anonymous.mkdir(parents=True, exist_ok=True)
    (anonymous / "config.json").write_text("{}\n")
    def docker(*args, timeout=30):
        cmd = ["docker"] + (["--host", docker_host] if docker_host else [])
        return subprocess.run(cmd + ["--config", str(anonymous), *args],
                              capture_output=True, text=True, timeout=timeout)
    observed = docker("inspect", cid or name)
    if observed.returncode == 1 and "no such" in observed.stderr.lower():
        result.update(passed=True, exact_id_absent=True, container_id=cid, container_name=name)
        return result
    if observed.returncode or pin is None:
        raise RuntimeError("Oracle cleanup could not verify the exact owned container")
    container = json.loads(observed.stdout)[0]
    actual_cid = container.get("Id")
    if (not re.fullmatch(r"[a-f0-9]{64}", str(actual_cid)) or (cid is not None and cid != actual_cid)
            or container.get("Image") != pin["config"] or container.get("Name") != "/" + name
            or any(container.get("Config", {}).get("Labels", {}).get(k) != v for k, v in labels.items())):
        raise RuntimeError("Oracle cleanup refused an unverified container owner/image")
    host = container.get("HostConfig", {})
    if host.get("Binds") or host.get("VolumesFrom") or any(
            m.get("Type") != "tmpfs" or m.get("Destination") != SECRET_DIR for m in container.get("Mounts", [])):
        raise RuntimeError("Oracle cleanup refused unexpected external data mounts")
    try:
        docker("stop", "--time", "30", actual_cid, timeout=45)
    except subprocess.TimeoutExpired:
        result["graceful_stop_timed_out"] = True
    removed = docker("rm", "--force", actual_cid)
    if removed.returncode:
        raise RuntimeError("Oracle cleanup exact removal failed")
    absent = docker("inspect", actual_cid)
    if absent.returncode != 1 or "no such" not in absent.stderr.lower():
        raise RuntimeError("Oracle cleanup exact absence was not confirmed")
    result.update(passed=True, container_id=actual_cid, exact_id_absent=True, removed=True)
    return result
