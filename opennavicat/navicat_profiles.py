"""Read-only Navicat connection metadata discovery.

The macOS reader is based on Navicat 18's observed ``Common/conn.plist`` and
``Navicat Premium/Profiles/vgroup.json``. It never opens a database, executes
Navicat, reads a keychain, decrypts a password, or writes to its source. Password
migration deliberately remains a separate, explicit operation. Reports and
profile reprs are redacted; callers must also avoid logging ``profile.config``.

Linux's documented connections.json location and injected Windows registry
records are accepted only when their structure can be mapped unambiguously to
this reader's known fields. Their native reference acceptance remains open.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .database import ConnectionConfig

# These are source format names, not a claim that every adapter exists.
DRIVER_NAMES = {
    "mysql": "mysql", "mariadb": "mariadb", "postgresql": "postgresql",
    "pgsql": "postgresql", "postgres": "postgresql", "sqlite": "sqlite",
    "sqlserver": "sqlserver", "mssql": "sqlserver", "oracle": "oracle",
    "mongodb": "mongodb", "mongo": "mongodb", "redis": "redis",
    "snowflake": "snowflake",
}
_NAMESPACE = uuid.UUID("eb185bfd-dc69-4f7f-8a82-c0c76b501796")
_MAX_SOURCE = 32 * 1024 * 1024
_MAX_PROFILES = 100_000
_MAX_DEPTH = 32
_FIELD = re.compile(r"[A-Za-z_][A-Za-z_0-9.-]{0,119}\Z")
_SECRET_KEYS = {"password", "encryptedpassword", "ssh_password", "passphrase",
                "pemclientkeypassword", "clientkeypassword", "pkeypassword"}


@dataclass(frozen=True)
class ImportIssue:
    code: str
    message: str
    source_id: str = ""
    fields: tuple[str, ...] = ()
    blocking: bool = False


@dataclass(frozen=True)
class ImportedProfile:
    source_id: str
    config: ConnectionConfig = field(repr=False, compare=True)
    group_path: tuple[str, ...] = field(default=(), repr=False)
    source_path: str = field(default="", repr=False)
    source_format: str = "macos-plist"
    unsupported_fields: tuple[str, ...] = ()
    requires_secrets: tuple[str, ...] = ()
    blocked: bool = False
    # Additional memberships are preserved because Navicat virtual groups can
    # reference one connection more than once. group_path is its first location.
    group_paths: tuple[tuple[str, ...], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class NavicatImportReport:
    profiles: tuple[ImportedProfile, ...] = ()
    issues: tuple[ImportIssue, ...] = ()
    sources_found: int = 0
    platform: str = ""

    @property
    def blocked_count(self) -> int:
        return sum(profile.blocked for profile in self.profiles)


@dataclass(frozen=True)
class RegistryProfile:
    """Injection boundary for an OS-native registry adapter.

    ``values`` use the observed lower-case Navicat metadata field names. Never
    include encrypted password values: this reader does not need them. Supplying
    this adapter is explicit; discovery does not enumerate a user's registry
    with undocumented field conversions.
    """
    driver: str
    name: str = field(repr=False)
    values: Mapping[str, object] = field(repr=False)
    source_key: str = field(default="", repr=False)
    group_path: tuple[str, ...] = field(default=(), repr=False)


def _driver(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return DRIVER_NAMES.get(re.sub(r"[ _-]", "", value).lower())


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _enabled(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"0", "1", "true", "false", "yes", "no"}:
        return value.lower() in {"1", "true", "yes"}
    return None


def _safe_field(value: object) -> str:
    return value if isinstance(value, str) and _FIELD.fullmatch(value) else "unknown_field"


def _load(path: Path, kind: str):
    # Size bounds precede parsing. Symlinks are read-only; no database or settings
    # directories are traversed. Parser exceptions must not expose source data.
    if path.stat().st_size > _MAX_SOURCE:
        raise ValueError("source_too_large")
    with path.open("rb") as stream:
        data = stream.read(_MAX_SOURCE + 1)
    if len(data) > _MAX_SOURCE:
        raise ValueError("source_too_large")
    return plistlib.loads(data) if kind == "plist" else json.loads(data)


def _records(data: object):
    """Yield primary records under recognized driver families, not SSH dicts."""
    found = 0
    def walk(value, driver=None, name="", depth=0, path=()):
        nonlocal found
        if depth > _MAX_DEPTH or found >= _MAX_PROFILES:
            raise ValueError("source_structure_limit")
        if isinstance(value, dict):
            if driver and "connection_uuid" in value:
                found += 1
                yield driver, name, value, path[:-1]
                return
            for key, child in value.items():
                drv = None if driver and isinstance(child, dict) and "connection_uuid" in child else _driver(key)
                # Source profile display names can happen to equal 'MySQL'. A
                # primary record is anchored by UUID before interpreting keys.
                yield from walk(child, drv or driver, str(key), depth + 1, path + (str(key),))
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict) and "connection_uuid" in child:
                    drv = _driver(child.get("server_type") or child.get("driver")) or driver
                    if drv:
                        found += 1
                        yield drv, _text(child.get("name")), child, path
                else:
                    yield from walk(child, driver, name, depth + 1, path)
    yield from walk(data)


def _groups(data: object, issues: list[ImportIssue]):
    memberships: dict[tuple[str, str], list[tuple[str, ...]]] = {}
    if not isinstance(data, dict) or not isinstance(data.get("vgroups"), list):
        issues.append(ImportIssue("unsupported_groups", "The virtual-group source structure is not supported."))
        return memberships
    def visit(rows, parents=(), depth=0):
        if depth > _MAX_DEPTH:
            raise ValueError("source_structure_limit")
        for group in rows:
            if not isinstance(group, dict):
                continue
            title = _text(group.get("vgroup_name"))
            if not title or group.get("vgroup_type") != "CONNECTION":
                issues.append(ImportIssue("unsupported_group", "A virtual-group entry cannot be mapped safely.", fields=("vgroup_type",)))
                continue
            path = parents + (title,)
            items = group.get("items", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "vgroup_name" in item:
                    visit([item], path, depth + 1)
                    continue
                drv = _driver(item.get("server_type"))
                name = _text(item.get("name"))
                if item.get("type") != "CONNECTION" or not drv or not name:
                    issues.append(ImportIssue("unsupported_group_member", "A virtual-group member cannot be mapped safely.", fields=("type", "server_type")))
                    continue
                paths = memberships.setdefault((drv, name), [])
                if path not in paths:
                    paths.append(path)
    visit(data["vgroups"])
    return memberships


def _convert(driver: str, name: str, original: Mapping, source: str, source_format: str,
             group_paths: tuple[tuple[str, ...], ...], issues: list[ImportIssue],
             identity_scope: tuple[str, ...] = ()) -> ImportedProfile:
    native_id = _text(original.get("connection_uuid"))
    identity = f"{driver}\0{native_id}" if native_id else json.dumps([driver, source, identity_scope, name], ensure_ascii=False, separators=(",", ":"))
    source_id = "navicat:" + str(uuid.uuid5(_NAMESPACE, identity))
    config = ConnectionConfig("navicat-" + source_id[8:], name or "Imported connection", driver=driver,
                              host="", ssl_mode="disable")
    unsupported: set[str] = set()
    secrets: set[str] = set()
    blocked = False
    def issue(code, message, fields=(), block=True):
        nonlocal blocked
        safe = tuple(_safe_field(f) for f in fields)
        unsupported.update(safe)
        blocked |= block
        issues.append(ImportIssue(code, message, source_id, safe, block))
    record = dict(original)
    variants = record.get("profiles", [])
    active = _text(record.get("active_setting_name"))
    if active:
        matches = [v for v in variants if isinstance(v, dict) and v.get("profilename") == active] if isinstance(variants, list) else []
        if len(matches) == 1:
            record.update(matches[0])
        else:
            issue("unknown_active_profile", "The active connection variant cannot be selected unambiguously.", ("active_setting_name", "profiles"))
    if not native_id:
        issue("missing_native_identity", "The source has no native stable connection identity; its name is used for deduplication.", ("connection_uuid",), False)
    config.host = _text(record.get("host"))
    config.username = _text(record.get("username"))
    config.database = _text(record.get("defaultdatabase") or record.get("database"))
    if driver == "sqlite":
        config.database = _text(record.get("dbfilename"))
        if not config.database:
            issue("missing_database_file", "The SQLite profile does not specify a database file.", ("dbfilename",))
        if _enabled(record.get("encrypted", False)) is not False:
            secrets.add("password")
            issue("encrypted_sqlite", "Encrypted SQLite profiles require an encryption-capable adapter.", ("encrypted",))
    else:
        port = record.get("port", 0)
        try:
            if isinstance(port, bool):
                raise ValueError
            config.port = int(port)
            if not 0 <= config.port <= 65535:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            config.port = 0
            issue("invalid_port", "The source database port is invalid.", ("port",))
        if not config.host:
            issue("missing_host", "The source profile does not specify a database host.", ("host",))
        if _enabled(record.get("savepassword", False)) or any(record.get(k) for k in _SECRET_KEYS):
            secrets.add("password")
    if record.get("useconnectiontimeout"):
        timeout = record.get("connectiontimeout")
        try:
            if isinstance(timeout, bool) or not 1 <= int(timeout) <= 31536000:
                raise ValueError
            config.connect_timeout = int(timeout)
        except (ValueError, TypeError, OverflowError):
            issue("unsupported_timeout", "The selected connection timeout cannot be mapped.", ("useconnectiontimeout", "connectiontimeout"))
    for flag in ("usehttptunnel", "usesrvrecord"):
        if _enabled(record.get(flag, False)) is not False:
            issue("unsupported_transport", "The profile requires an unsupported transport option.", (flag,))
    provider = record.get("serviceprovider", "")
    if provider not in ("", "0", 0, "Default", "default"):
        issue("unsupported_service_provider", "The source service-provider authentication settings need review.", ("serviceprovider",))
    if driver not in {"redis", "mongodb"}:
        for field_name in ("authenticationmode", "authmode", "authmethod", "authmechanism", "connmethod"):
            auth_value = record.get(field_name)
            if auth_value not in (None, "", 0, "0", "Default", "default", "password", "Password", "Normal", "normal"):
                issue("unsupported_database_authentication", "The database authentication mode needs review.", (field_name,))
    if record.get("unknownjsonproperties"):
        issue("unknown_connection_options", "The source contains additional connection options that need review.", ("unknownjsonproperties",))
    for key in ("initialsessionqueries", "connectionparameters"):
        if record.get(key):
            issue("source_session_options_review", "The source has additional session options that require explicit review.", (key,))
    tls = _enabled(record.get("usessl", False if driver == "sqlite" else None))
    if tls is None:
        issue("unknown_tls_state", "The source TLS enablement state is not recognized.", ("usessl",))
    if tls is not False:
        config.ssl_mode = "verify-full"  # Remain fail closed even if a caller ignores blocked.
        params = record.get("ssl_param", {})
        if not isinstance(params, dict):
            params = {}
        raw_mode = params.get("mode")
        mode = _text(raw_mode).lower().replace("_", "-")
        # Navicat 18.0.5/macOS saves the localized label for PG's allow mode.
        # The other four choices are stored as their libpq mode strings.
        if source_format == "macos-plist" and driver == "postgresql" and mode == "允许":
            mode = "allow"
        if mode in {"allow", "prefer", "require", "verify-ca", "verify-full"}:
            config.ssl_mode = mode
        elif raw_mode in (None, "") and "verifyca" in params and _enabled(params["verifyca"]) is not None:
            verify = _enabled(params["verifyca"])
            invalid = _enabled(params.get("allowinvalidhostname", False))
            if invalid is None:
                issue("unknown_tls_hostname", "The TLS hostname-verification setting is not recognized.", ("ssl_param.allowinvalidhostname",))
            config.ssl_mode = "require" if not verify else "verify-ca" if invalid else "verify-full"
        else:
            issue("unknown_tls_mode", "The selected TLS mode cannot be mapped safely.", ("ssl_param.mode",))
        files_enabled = True
        if source_format == "macos-plist" and driver == "postgresql" and "useauth" in params:
            # Observed on/off in the owned 18.0.5 PG profile: turning this off
            # disables all four inputs but leaves their old paths in the plist.
            selected = _enabled(params["useauth"])
            files_enabled = selected is True
            if selected is None:
                issue("unknown_tls_file_state", "The custom TLS file enablement state is not recognized.", ("ssl_param.useauth",))
        if files_enabled:
            ca_keys = ("rootcert",) if source_format == "macos-plist" and driver == "postgresql" else ("cacert", "rootcert")
            if source_format == "macos-plist" and driver == "postgresql" and params.get("cacert"):
                issue("unsupported_tls_option", "An additional TLS certificate option needs review.", ("ssl_param.cacert",))
            for target, keys in (("ssl_ca", ca_keys), ("ssl_cert", ("clientcert",)),
                                 ("ssl_key", ("clientkeyfile",)), ("ssl_crl", ("crlfile",))):
                values = {_text(params.get(k)) for k in keys} - {""}
                if len(values) > 1:
                    issue("ambiguous_tls_files", "Multiple incompatible TLS file settings need review.", tuple("ssl_param." + k for k in keys))
                elif values:
                    setattr(config, target, next(iter(values)))
            if bool(config.ssl_cert) != bool(config.ssl_key):
                issue("incomplete_client_certificate", "Both TLS client certificate and key are required.", ("ssl_param.clientcert", "ssl_param.clientkeyfile"))
            if params.get("pemclientkeypassword") or _enabled(params.get("saveclientkeypassword", False)):
                secrets.add("ssl_key_password")
        for flag in ("weakcertvalidation",):
            # The generic field remains True across all five observed PG modes,
            # including verify-full; PG's mode is the authoritative setting.
            if source_format == "macos-plist" and driver == "postgresql":
                continue
            if _enabled(params.get(flag, False)) is not False:
                issue("unsupported_tls_option", "A custom TLS validation option needs review.", ("ssl_param." + flag,))
        for flag in ("cipher", "servernameindication"):
            if params.get(flag):
                issue("unsupported_tls_option", "A custom TLS option needs review.", ("ssl_param." + flag,))
        if (driver == "redis" and config.ssl_mode in {"allow", "prefer"}) or (driver != "postgresql" and config.ssl_mode == "allow"):
            issue("unsupported_tls_mode", "This adapter cannot use opportunistic TLS.", ("ssl_param.mode",))
    tunnel = _enabled(record.get("usetunnel", False if driver == "sqlite" else None))
    if tunnel is None:
        issue("unknown_ssh_state", "The SSH enablement state is not recognized.", ("usetunnel",))
    if tunnel is not False:
        config.ssh_enabled = True
        ssh = record.get("ssh_param", {})
        if not isinstance(ssh, dict):
            ssh = {}
        config.ssh_host = _text(ssh.get("host"))
        config.ssh_username = _text(ssh.get("username"))
        config.ssh_key_file = _text(ssh.get("pkeyfile"))
        config.ssh_compression = _enabled(ssh.get("usecompression", False)) is True
        try:
            port = ssh.get("port", 22)
            if isinstance(port, bool) or not 1 <= int(port) <= 65535:
                raise ValueError
            config.ssh_port = int(port)
        except (ValueError, TypeError, OverflowError):
            issue("invalid_ssh_port", "The SSH port is invalid.", ("ssh_param.port",))
        method = str(ssh.get("authtype", "")).lower()
        # Numeric values were observed by saving the three choices in an owned
        # macOS 18.0.5 profile. Historic macOS records use text numerals instead
        # of ints. Do not assume an unaccepted native registry/JSON mapping.
        methods = {"password": "password", "key": "key", "publickey": "key",
                   "password+key": "password+key", "agent": "agent"}
        if source_format == "macos-plist":
            methods.update({"0": "password", "1": "key", "2": "password+key"})
        if method in methods:
            config.ssh_auth_method = methods[method]
        else:
            issue("unknown_ssh_authentication", "The SSH authentication method needs review.", ("ssh_param.authtype",))
        if not config.ssh_host or not config.ssh_username:
            issue("incomplete_ssh_endpoint", "The SSH endpoint or user is missing.", ("ssh_param.host", "ssh_param.username"))
        if config.ssh_auth_method in {"key", "password+key"} and not config.ssh_key_file:
            issue("missing_ssh_key", "The SSH private-key file is missing.", ("ssh_param.pkeyfile",))
        if config.ssh_auth_method in {"password", "password+key"}:
            secrets.add("ssh_password")
        if config.ssh_auth_method in {"key", "password+key"}:
            secrets.add("ssh_key_passphrase")
        # Host-key pins are never invented or copied from a different format.
        # The application's existing explicit trust workflow handles first use.
    if driver == "redis":
        raw_database = record.get("database", "0")
        if ((isinstance(raw_database, int) and not isinstance(raw_database, bool)) or
                (isinstance(raw_database, str) and re.fullmatch(r"[0-9]+", raw_database))):
            number = int(raw_database)
            if 0 <= number <= 2147483647:
                config.database = str(number)
            else:
                config.database = "0"
                issue("invalid_redis_database", "The source Redis database number is invalid.", ("database",))
        else:
            config.database = "0"
            issue("invalid_redis_database", "The source Redis database number is invalid.", ("database",))
        topology = _text(record.get("type")).lower()
        if topology not in {"standalone", "single"}:
            issue("unsupported_redis_topology", "The Redis topology requires an adapter that is not yet available.", ("type",))
        auth = _text(record.get("authenticationmode")).lower()
        if auth in {"none", "no authentication"}:
            config.username = ""
            secrets.discard("password")
        elif auth in {"password", "acl", "username and password"}:
            secrets.add("password")
        else:
            issue("unknown_redis_authentication", "The Redis authentication mode needs review.", ("authenticationmode",))
    if record.get("customdblist") and _enabled(record.get("usecustomdblist", False)):
        issue("database_filter_not_migrated", "The source database visibility filter is retained as an unsupported option.", ("usecustomdblist", "customdblist"), False)
    if driver in {"oracle", "snowflake"}:
        issue("driver_not_available", "This connection family is retained but its adapter is not yet available.", ("driver",))
    if driver == "mongodb":
        config.mongo_auth_source = _text(record.get("authsource"))
        mechanism = _text(record.get("authmechanism"))
        config.mongo_auth_mechanism = "DEFAULT" if mechanism == "Password" else mechanism or "UNKNOWN"
        config.mongo_connection_type = "standalone" if record.get("connmethod") == "Standalone" else _text(record.get("connmethod")) or "UNKNOWN"
        config.mongo_replica_set = _text(record.get("replicasetname"))
        members = record.get("memberlist", [])
        config.mongo_members = json.dumps(members, ensure_ascii=False) if members else ""
        config.mongo_read_preference = "primary" if record.get("readpreference") == "Primary" else _text(record.get("readpreference")) or "UNKNOWN"
        config.mongo_srv = _enabled(record.get("usesrvrecord", False)) is not False
        for source, target in (("retryreads", "mongo_retry_reads"), ("retrywrites", "mongo_retry_writes")):
            flag = _enabled(record.get(source))
            if flag is None:
                issue("mongodb_retry_options_review", "The MongoDB retry setting requires review.", (source,))
            else:
                setattr(config, target, flag)
        issue("mongodb_source_settings_review", "The MongoDB source authentication and topology settings require explicit review before opening.", ("authmechanism", "authsource", "connmethod"))
    if driver in {"oracle", "mongodb", "snowflake"}:
        for key in ("authmechanism", "authsource", "authkerberosprincipal", "authkerberosservicename",
                    "authx509subjectname", "replicasetname", "memberlist", "connmethod",
                    "readpreference", "role", "serviceprovider"):
            if record.get(key):
                unsupported.add(key)
    if driver == "sqlserver":
        # Authentication modes and instance/port semantics must be observed;
        # preserving metadata is preferable to silently selecting SQL password.
        issue("sqlserver_authentication_review", "The source SQL Server authentication settings need review.", ("authenticationmode",))
    return ImportedProfile(source_id, config, group_paths[0] if group_paths else (), source,
                           source_format, tuple(sorted(unsupported)), tuple(sorted(secrets)), blocked, group_paths)


def discover_navicat_profiles(root: str | os.PathLike | None = None, *, platform: str | None = None,
                              registry_reader: Callable[[], Iterable[RegistryProfile]] | None = None) -> NavicatImportReport:
    """Discover metadata without opening connections or reading stored secrets.

    An explicit root is a Navicat CC directory, its Common directory, or one
    exact conn.plist/connections.json file. It is never recursively searched.
    On Windows an injected native metadata reader is currently required; absent
    evidence is reported rather than guessed. All exceptions become redacted
    issues, including parser messages that might otherwise contain user values.
    """
    platform = platform or sys.platform
    issues: list[ImportIssue] = []
    profiles: list[ImportedProfile] = []
    sources = 0
    if platform == "win32" and root is None:
        if registry_reader is None:
            return NavicatImportReport(issues=(ImportIssue("windows_discovery_pending", "Native Windows registry metadata mapping has not been accepted yet."),), platform=platform)
        try:
            records = list(registry_reader())
            if len(records) > _MAX_PROFILES:
                raise ValueError("source_structure_limit")
            sources = 1
            for row in records:
                drv = _driver(row.driver)
                if not drv:
                    issues.append(ImportIssue("unknown_driver", "An unknown source connection family was not imported."))
                    continue
                profiles.append(_convert(drv, row.name, row.values, row.source_key, "windows-registry", (row.group_path,) if row.group_path else (), issues))
        except Exception:
            issues.append(ImportIssue("registry_read_failed", "The registry metadata source could not be read safely."))
        return _finish(profiles, issues, sources, platform)
    if root is None:
        if platform == "darwin":
            base = Path.home() / "Library/Application Support/PremiumSoft CyberTech/Navicat CC"
            path = base / "Common/conn.plist"
        elif platform.startswith("linux"):
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "navicat"
            path = base / "Common/connections.json"
        else:
            return NavicatImportReport(issues=(ImportIssue("unsupported_platform", "No native metadata source is established on this platform."),), platform=platform)
    else:
        supplied = Path(root).expanduser()
        if supplied.is_file() or supplied.suffix.lower() in {".plist", ".json"}:
            path = supplied
            base = path.parent.parent if path.parent.name == "Common" else path.parent
        elif supplied.name == "Common":
            base = supplied.parent
            path = supplied / ("conn.plist" if platform == "darwin" else "connections.json")
        else:
            base = supplied
            path = base / "Common" / ("conn.plist" if platform == "darwin" else "connections.json")
    if not path.exists():
        return NavicatImportReport(platform=platform)
    try:
        data = _load(path, "plist" if path.suffix.lower() == ".plist" else "json")
        sources += 1
        records = list(_records(data))
    except Exception:
        return NavicatImportReport(issues=(ImportIssue("source_read_failed", "The connection metadata source could not be read safely."),), sources_found=sources, platform=platform)
    if not records:
        issues.append(ImportIssue("unsupported_source_structure", "The connection source contains no recognized primary profile records."))
    groups = {}
    group_file = base / "Navicat Premium/Profiles/vgroup.json" if platform == "darwin" else base / "Premium/Profiles/vgroup.json"
    if group_file.exists():
        try:
            groups = _groups(_load(group_file, "json"), issues)
            sources += 1
        except Exception:
            issues.append(ImportIssue("groups_read_failed", "The virtual-group metadata source could not be read safely."))
    identity_counts: dict[tuple[str, str], int] = {}
    for drv, name, _, _ in records:
        identity_counts[(drv, name)] = identity_counts.get((drv, name), 0) + 1
    identities = set(identity_counts)
    for key in set(groups) & identities:
        if identity_counts[key] > 1:
            groups.pop(key)
            issues.append(ImportIssue("ambiguous_group_members", "A group member matches multiple source namespaces and was left ungrouped."))
    if set(groups) - identities:
        issues.append(ImportIssue("unresolved_group_members", "Some virtual-group members do not match a discovered connection."))
    for driver, name, record, scope in records:
        try:
            profiles.append(_convert(driver, name, record, str(path), "macos-plist" if path.suffix.lower() == ".plist" else "linux-json", tuple(groups.get((driver, name), ())), issues, scope))
        except Exception:
            # No raw exception text: malformed content can include credentials.
            issues.append(ImportIssue("profile_read_failed", "A connection metadata entry could not be converted safely."))
    return _finish(profiles, issues, sources, platform)


def _finish(profiles, issues, sources, platform):
    result = []
    seen = set()
    for profile in profiles:
        if profile.source_id in seen:
            issues.append(ImportIssue("duplicate_native_identity", "A repeated native connection identity was not imported twice.", profile.source_id))
            continue
        seen.add(profile.source_id)
        result.append(profile)
    return NavicatImportReport(tuple(result), tuple(issues), sources, platform)
