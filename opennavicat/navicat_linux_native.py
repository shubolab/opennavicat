"""Linux18 PascalCase metadata projection, never a macOS format fallback.

The schema is grounded in owned offline 18.0.3 save/restart observations. Only
basic endpoint metadata is mapped. Every native record requires explicit local
settings review; active variants, transport/authentication and opaque options
are not guessed. Secret and opaque values are validated as JSON then discarded
without decoding or retaining them. No credential or database API is called.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
import uuid

from .database import ConnectionConfig
from .navicat_linux import LinuxSourceCandidate, linux_source_candidates, read_linux_projection
from .navicat_linux_json import ProjectedObject, SkippedValue
from .navicat_profiles import ImportedProfile, ImportIssue, NavicatImportReport, _group_metadata

_NAMESPACE = uuid.UUID("08e7a16e-7b76-553d-b62b-3cfe9f5aa342")
_DRIVERS = {"MYSQL": "mysql", "MARIADB": "mariadb", "PGSQL": "postgresql",
            "SQLITE": "sqlite", "MSSQL": "sqlserver", "MONGODB": "mongodb", "REDIS": "redis"}
# None means opaque skip, not json.loads followed by deleting private fields.
_SERVER = dict.fromkeys((
    "SettingsSavePath", "SavePassword", "Password", "EncryptedPassword", "EncryptPassword",
    "ClientDriverVersion", "ClientDriverEncoding", "Encoding", "ClientCharacterSet", "ClientEncoding",
    "UseKeepAlive", "KeepAliveInterval", "IsAutoConnect", "SessionLimit", "InitialSessionQueries",
    "ConnectionParameters", "CustomDBList", "SSL", "SSH", "HTTPTunnel", "Compatibility",
    "Profiles", "ActiveServerSettings", "CreateTime", "ModifyTime", "NSYCreatedUser", "NSYModifiedUser",
    "NSYModifiedUserUUID", "NSYCreatedUnixTimestamp", "NSYModifiedUnixTimestamp", "CommitUUID",
    "UnknownJsonPropertiesString", "CloudSyncStatus", "Starred", "Hidden", "Remarks",
    "ConnectionTimeoutSeconds", "ConnectionTimeoutMS", "ReadTimeoutSeconds", "WriteTimeoutSeconds",
    "ExecutionTimeoutSeconds", "ServerSelectionTimeoutMS", "SocketTimeoutMS", "SocketFile",
    "HostPortList", "HostTypePreference", "NativeClientDriver", "Domain", "KerberosServiceName",
    "KerberosPrincipal", "X509SubjectName", "MemberList", "ReplicaSetName", "SentinelAuthenticationMode",
    "SentinelUsername", "SentinelSavePassword", "SentinelPassword", "SentinelMasterName", "SentinelAddr",
    "SentinelSSL", "SaveEncryptPassword", "CipherName", "CipherLegacyPageSize", "AttachedDatabases",
))
_SERVER.update(dict.fromkeys(("ConnType", "Name", "Hostname", "Username", "InitialDatabase",
    "DatabaseFile", "ConnectionUUID", "ServiceProvider", "AuthType", "ConnMethod", "AuthMechanism",
    "AuthSource", "ReadPreference", "AuthenticationMode", "Type", "Role"), str))
_SERVER.update(dict.fromkeys(("UseSSL", "UseSSH", "UseHTTP", "IsEncrypted", "UseSocketFile",
    "UseCompatibilityMode", "UseCustomDBList", "UseCompression", "UseConnectionTimeout",
    "UseReadTimeout", "UseWriteTimeout", "UseExecutionTimeout", "UseServerSelectionTimeout",
    "UseSocketTimeout", "UseEncryption", "TrustServerCertificate", "UseSRVRecord", "RetryReads",
    "RetryWrites", "SentinelUseSSL"), bool))
_SERVER["Port"] = int
_SPEC = {"Users": [{"UserUUID": str, "Projects": [{"Name": str, "ProjectUUID": str, "Servers": [_SERVER]}]}]}
_GROUP_SPEC = {"version": str, "connections": None, "vgroups": [{"vgroup_name": str,
    "vgroup_type": str, "items": [{"name": str, "type": str, "server_type": str}]}]}


def _plain(value):
    return isinstance(value, str) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _empty(value, kind):
    return isinstance(value, SkippedValue) and value.kind == kind and not value.nonempty


def _source_id(parts):
    return "navicat:" + str(uuid.uuid5(_NAMESPACE, json.dumps(parts, ensure_ascii=False, separators=(",", ":"))))


def _unknown(value):
    if isinstance(value, ProjectedObject):
        return value.unknown_fields or any(_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(_unknown(item) for item in value)
    return False


def _records(data, candidate, issues):
    """Role-specific traversal; no recursive search for plausible profiles."""
    incomplete = False
    def invalid():
        nonlocal incomplete
        incomplete = True
    if not isinstance(data, dict) or not isinstance(data.get("Users"), list):
        return [], False
    rows = []
    root_complete = not getattr(data, "unknown_fields", False)
    if not root_complete:
        invalid()
    user_counts = Counter(user.get("UserUUID") for user in data["Users"]
                          if isinstance(user, dict) and _plain(user.get("UserUUID")) and user["UserUUID"])
    user_seen = Counter()
    for ui, user in enumerate(data["Users"]):
        if not isinstance(user, dict) or not isinstance(user.get("Projects"), list):
            invalid();continue
        uid = user.get("UserUUID")
        has_uid = _plain(uid) and bool(uid)
        uvalid = has_uid and user_counts[uid] == 1
        uscope = ["uuid", uid] if has_uid else ["position", ui]
        if has_uid:
            if user_seen[uid]:
                uscope.extend(("duplicate", user_seen[uid]))
            user_seen[uid] += 1
        user_complete = root_complete and uvalid and not getattr(user, "unknown_fields", False)
        if not user_complete:
            invalid()
        counts = Counter(project.get("ProjectUUID") for project in user["Projects"]
                         if isinstance(project, dict) and _plain(project.get("ProjectUUID")) and project["ProjectUUID"])
        project_seen = Counter()
        for pi, project in enumerate(user["Projects"]):
            if not isinstance(project, dict) or not isinstance(project.get("Servers"), list):
                invalid();continue
            pid = project.get("ProjectUUID")
            has_pid = _plain(pid) and bool(pid)
            pvalid = has_pid and counts[pid] == 1
            pscope = ["uuid", pid] if has_pid else ["position", pi]
            if has_pid:
                if project_seen[pid]:
                    pscope.extend(("duplicate", project_seen[pid]))
                project_seen[pid] += 1
            scope_complete = user_complete and pvalid and not getattr(project, "unknown_fields", False)
            if not scope_complete:
                invalid()
            base_counts = Counter()
            entries = []
            for si, server in enumerate(project["Servers"]):
                if not isinstance(server, dict):
                    invalid();continue
                typ, name, native = server.get("ConnType"), server.get("Name"), server.get("ConnectionUUID")
                if not _plain(name) or not name:
                    # A generated display label is not a source identity and
                    # cannot prove that group name-based links are unique.
                    invalid()
                # IDs are scoped to the exact file/user/project even when a
                # future source writes a nonempty UUID. No cross-scope alias.
                token = ["uuid", native] if _plain(native) and native else ["name", name if _plain(name) else ["position", si]]
                base = [candidate.source_id, uscope, pscope, typ if _plain(typ) else "unknown", token]
                encoded = json.dumps(base, ensure_ascii=False, separators=(",", ":"))
                occurrence = base_counts[encoded];base_counts[encoded] += 1
                identity = _source_id(base if occurrence == 0 else [*base, "duplicate", occurrence])
                entries.append((identity, server, encoded, scope_complete))
            for identity, server, key, complete in entries:
                duplicate = base_counts[key] > 1
                if duplicate:
                    invalid()
                    issues.append(ImportIssue("duplicate_native_identity", "Conflicting Linux records remain separate and require review.", identity, ("ConnectionUUID", "Name"), True))
                rows.append((identity, server, complete and not duplicate))
    return rows, not incomplete


def _profile(identity, record, scope_complete, candidate, issues):
    driver = _DRIVERS.get(record.get("ConnType"), "unknown")
    name = record.get("Name")
    config = ConnectionConfig("navicat-" + identity[8:], name if _plain(name) and name else "Imported Linux connection",
                              driver=driver, host="", ssl_mode="disable" if driver == "sqlite" else "verify-full")
    fields = {"linux_native_settings"}
    complete = scope_complete and not getattr(record, "unknown_fields", False)
    def uncertain(field):
        nonlocal complete
        complete = False;fields.add(field)
    def text(field, required=False):
        value = record.get(field)
        if _plain(value):
            if required and not value:
                uncertain(field)
            return value
        if value is not None or required:
            uncertain(field)
        return ""
    text("Name", True)
    if driver == "unknown":
        uncertain("ConnType")
    if not _plain(record.get("ConnectionUUID")):
        uncertain("ConnectionUUID")
    if not _empty(record.get("Profiles"), "array") or not _empty(record.get("ActiveServerSettings"), "string"):
        uncertain("active_profile")
    if getattr(record, "unknown_fields", False):
        uncertain("unknown_fields")
    for key in ("UnknownJsonPropertiesString", "InitialSessionQueries", "ConnectionParameters"):
        if key in record and not _empty(record[key], "string"):
            uncertain(key)
    provider = record.get("ServiceProvider")
    if driver != "sqlite" and provider != "Default":
        uncertain("ServiceProvider")
    if driver == "sqlite":
        value = text("DatabaseFile", True)
        if not value.startswith("/") or value.startswith("//") or record.get("IsEncrypted") is not False:
            uncertain("DatabaseFile" if not value.startswith("/") or value.startswith("//") else "IsEncrypted")
        else:
            config.database = value
        if "AttachedDatabases" in record and not _empty(record["AttachedDatabases"], "array"):
            uncertain("AttachedDatabases")
    else:
        host = text("Hostname", True)
        if any(part in host for part in ("://", "@", "/", "?", "#", "\\")):
            uncertain("Hostname")
        else:
            config.host = host
        config.username = text("Username", driver not in {"redis", "mongodb"})
        if not _plain(record.get("Username")):
            uncertain("Username")
        port = record.get("Port")
        if type(port) is int and 1 <= port <= 65535:
            config.port = port
        else:
            uncertain("Port")
        config.database = text("InitialDatabase") if driver in {"postgresql", "sqlserver"} else ""
        if driver in {"postgresql", "sqlserver"} and not _plain(record.get("InitialDatabase")):
            uncertain("InitialDatabase")
        # No Linux TLS/SSH auth or active-profile enum is inferred from macOS.
        if driver != "sqlserver":
            if record.get("UseSSL") is False:
                config.ssl_mode = "disable"
            else:
                uncertain("UseSSL")
        if record.get("UseSSH") is not False:
            uncertain("UseSSH")
    if driver in {"sqlite", "postgresql", "mysql", "mariadb"} and record.get("UseHTTP") is not False:
        uncertain("UseHTTP")
    for flag in ("UseSSL", "UseSSH", "UseHTTP"):
        # A field can be projected for another family without having accepted
        # active semantics here. Inactive opaque subtrees remain untouched.
        if flag in record and record[flag] is not False:
            uncertain(flag)
    if "InitialDatabase" in record and driver not in {"postgresql", "sqlserver"}:
        uncertain("driver_specific_settings")
    for flag in ("UseSocketFile", "UseCompatibilityMode", "UseCustomDBList"):
        if flag in record and record[flag] is not False:
            uncertain(flag)
    if driver == "sqlserver":
        # Exact saved enums are evidence of metadata, not native TLS policy.
        fields.update(("AuthType", "UseEncryption", "TrustServerCertificate"))
        if record.get("AuthType") != "SQLServer":
            uncertain("AuthType")
        if type(record.get("UseEncryption")) is not bool or type(record.get("TrustServerCertificate")) is not bool:
            uncertain("UseEncryption")
    elif driver == "mongodb":
        fields.update(("AuthMechanism", "ConnMethod", "ReadPreference"))
        if record.get("AuthMechanism") == "None":
            config.mongo_auth_mechanism = "NONE";config.username = ""
        else:
            uncertain("AuthMechanism")
        if record.get("ConnMethod") != "Standalone" or record.get("UseSRVRecord") is not False:
            uncertain("ConnMethod")
        if record.get("ReadPreference") != "Primary":
            uncertain("ReadPreference")
        config.mongo_auth_source = text("AuthSource")
        if not _plain(record.get("AuthSource")):
            uncertain("AuthSource")
        for field, target in (("RetryReads", "mongo_retry_reads"), ("RetryWrites", "mongo_retry_writes")):
            if type(record.get(field)) is bool:
                setattr(config, target, record[field])
            else:
                uncertain(field)
        for field in ("MemberList",):
            if not _empty(record.get(field), "array"):
                uncertain(field)
    elif driver == "redis":
        fields.update(("AuthenticationMode", "Type"))
        config.database = "0"
        if record.get("AuthenticationMode") == "None":
            config.username = ""
        else:
            uncertain("AuthenticationMode")
        if record.get("Type") != "Standalone":
            uncertain("Type")
    family_fields = {
        "sqlserver": {"AuthType", "Domain", "UseEncryption", "TrustServerCertificate"},
        "mongodb": {"AuthMechanism", "AuthSource", "ConnMethod", "UseSRVRecord", "ReadPreference",
                    "RetryReads", "RetryWrites", "MemberList", "ReplicaSetName", "KerberosServiceName", "KerberosPrincipal", "X509SubjectName"},
        "redis": {"AuthenticationMode", "Type", "Role", "SentinelAuthenticationMode", "SentinelUsername",
                  "SentinelSavePassword", "SentinelPassword", "SentinelMasterName", "SentinelAddr", "SentinelUseSSL", "SentinelSSL"},
        "sqlite": {"DatabaseFile", "IsEncrypted", "SaveEncryptPassword", "EncryptPassword", "CipherName", "CipherLegacyPageSize", "AttachedDatabases"},
    }
    for family, keys in family_fields.items():
        if family != driver and keys.intersection(record):
            uncertain("driver_specific_settings")
    if not complete:
        issues.append(ImportIssue("profile_read_failed", "Some Linux source settings are unknown, incomplete or ambiguous; existing configuration is preserved.", identity, tuple(sorted(fields)), True))
    issues.append(ImportIssue("linux_native_settings_review", "Linux native metadata is visible; review and save its settings before connecting.", identity, tuple(sorted(fields)), True))
    return ImportedProfile(identity, config, source_path=candidate.source_id,
        source_format="linux-native-json", unsupported_fields=tuple(sorted(fields)),
        requires_secrets=() if driver == "sqlite" or (driver in {"redis", "mongodb"} and config.username == "") else ("password",),
        blocked=True, groups_complete=False, config_complete=complete)


def _groups_for(candidate, entries, issues):
    path = candidate.path.parent.parent / "Premium/Profiles/vgroup.json"
    group_source = LinuxSourceCandidate(candidate.namespace + ":groups", path)
    state, exists, data, _ = read_linux_projection(group_source, _GROUP_SPEC)
    if state != "read":
        issues.append(ImportIssue("groups_read_failed", "The Linux virtual-group source is missing or could not be read safely."))
        return {}, False, int(exists is True)
    if not isinstance(data, dict) or data.get("version") != "1.1" or not _empty(data.get("connections"), "array"):
        issues.append(ImportIssue("unsupported_groups", "The Linux virtual-group version or structure needs review."))
        return {}, False, 1
    if getattr(data, "unknown_fields", False):
        issues.append(ImportIssue("unsupported_groups", "Additional Linux group-root semantics require review."))
        return {}, False, 1
    # This Linux checkpoint observed exact ConnType tokens in group members.
    # Do not widen native acceptance using the macOS reader's alias spellings.
    accepted = dict(data)
    complete = not _unknown(data)
    if not complete:
        issues.append(ImportIssue("unsupported_group_member", "Additional Linux group fields or nested group shapes need review."))
    if isinstance(data.get("vgroups"), list):
        accepted["vgroups"] = []
        for group in data["vgroups"]:
            if getattr(group, "unknown_fields", False):
                complete = False
                issues.append(ImportIssue("unsupported_group", "Additional Linux group semantics require review."))
                continue
            value = dict(group) if isinstance(group, dict) else group
            if isinstance(group, dict) and isinstance(group.get("items"), list):
                value["items"] = []
                for item in group["items"]:
                    if getattr(item, "unknown_fields", False):
                        complete = False
                        issues.append(ImportIssue("unsupported_group_member", "Additional Linux membership semantics require review."))
                    elif isinstance(item, dict) and item.get("server_type") not in _DRIVERS:
                        complete = False
                        issues.append(ImportIssue("unsupported_group_member", "A Linux group member uses an unaccepted native family token."))
                    else:
                        value["items"].append(item)
            accepted["vgroups"].append(value)
    groups, parsed_complete = _group_metadata(accepted, issues)
    complete &= parsed_complete
    counts = Counter((profile.config.driver, profile.config.name) for profile in entries)
    for key in tuple(groups):
        if counts[key] != 1:
            groups.pop(key);complete = False
            issues.append(ImportIssue("ambiguous_group_members" if counts[key] else "unresolved_group_members",
                "Some Linux group members cannot be assigned to exactly one discovered source."))
    return groups, complete, 1


def discover_native_linux_profiles(*, home=None, environ=None):
    candidates, problems = linux_source_candidates(home=home, environ=environ)
    issues = [ImportIssue(item.code, item.message, blocking=item.blocking) for item in problems]
    profiles, sources = [], 0
    if problems:
        issues.append(ImportIssue("unsupported_source_structure", "Some configured Linux source locations require review.", blocking=True))
    for candidate in candidates:
        state, exists, data, _ = read_linux_projection(candidate, _SPEC)
        sources += int(exists is True)
        if state == "missing":
            continue
        if state != "read":
            issues.append(ImportIssue("source_read_failed", "A Linux metadata source could not be read safely; existing profiles are preserved.", blocking=True))
            continue
        rows, complete = _records(data, candidate, issues)
        if not complete:
            issues.extend((ImportIssue("linux_native_format_review", "A Linux source contains an unaccepted or ambiguous structure.", blocking=True),
                           ImportIssue("unsupported_source_structure", "Incomplete Linux metadata is not an authoritative empty source.", blocking=True)))
        entries = [_profile(identity, record, scope, candidate, issues) for identity, record, scope in rows]
        if entries:
            groups, groups_complete, count = _groups_for(candidate, entries, issues)
            if not complete:
                # An unenumerated/ambiguous connection namespace can hide a
                # second same-name target. It cannot establish unique links
                # or authorize clearing any previous group memberships.
                groups, groups_complete = {}, False
                issues.append(ImportIssue("ambiguous_group_members", "The incomplete Linux connection source cannot establish unique group targets."))
            sources += count
            for profile in entries:
                paths = tuple(groups.get((profile.config.driver, profile.config.name), ()))
                profiles.append(replace(profile, group_path=paths[0] if paths else (), group_paths=paths,
                                        groups_complete=groups_complete))
    return NavicatImportReport(tuple(profiles), tuple(issues), sources, "linux")
