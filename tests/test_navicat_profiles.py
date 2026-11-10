"""Synthetic metadata only: no original Navicat profiles or credentials."""
from __future__ import annotations

import copy
import json
import plistlib
from dataclasses import replace
from pathlib import Path

import pytest

from opennavicat.navicat_profiles import RegistryProfile, discover_navicat_profiles


@pytest.fixture
def source(tmp_path):
    base = tmp_path / "Navicat CC"
    (base / "Common").mkdir(parents=True)
    return base


def profile(native_id="native-1", **changes):
    row = dict(connection_uuid=native_id, host="synthetic.invalid", port=5432,
               username="example-user", defaultdatabase="example-db", savepassword=True,
               usessl=False, usetunnel=False, profiles=[], active_setting_name="")
    row.update(changes)
    return row


def write_source(base, records, *, binary=False):
    path = base / "Common/conn.plist"
    path.write_bytes(plistlib.dumps({"local": {"workspace": records}}, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
    return path


def discover(base, **kw):
    return discover_navicat_profiles(base, platform="darwin", **kw)


@pytest.mark.parametrize("binary", [False, True])
def test_read_only_metadata_no_secret_material_or_database_io(source, monkeypatch, binary):
    path = write_source(source, {"PostgreSQL": {"Example connection": profile(password="NOT-A-REAL-SECRET")}}, binary=binary)
    before = path.read_bytes()
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("metadata discovery must not open databases"))
    report = discover(source)
    assert len(report.profiles) == 1
    imported = report.profiles[0]
    assert imported.config.name == "Example connection"
    assert imported.config.host == "synthetic.invalid"
    assert imported.config.username == "example-user"
    assert imported.config.database == "example-db"
    assert imported.config.password == imported.config.ssh_password == imported.config.ssl_key_password == ""
    assert imported.requires_secrets == ("password",)
    assert not imported.blocked
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["conn.plist"]
    assert not any(value in repr(report) for value in ["NOT-A-REAL-SECRET", "synthetic.invalid", "example-user", "Example connection", str(source)])


def test_stable_native_identity_survives_rename_endpoint_and_directory_change(source, tmp_path):
    write_source(source, {"PostgreSQL": {"Before": profile()}})
    old = discover(source).profiles[0]
    other = tmp_path / "other"
    (other / "Common").mkdir(parents=True)
    write_source(other, {"PostgreSQL": {"After": profile(host="other.invalid", port=5544)}})
    new = discover(other).profiles[0]
    assert old.source_id == new.source_id
    assert old.config.id == new.config.id
    assert new.config.host == "other.invalid"


def test_group_memberships_are_bound_by_driver_and_name_and_all_retained(source):
    write_source(source, {"MySQL": {"Shared": profile("my", port=3306)}, "PostgreSQL": {"Shared": profile("pg")}})
    groups = {"version": "1", "connections": [], "vgroups": [
        {"vgroup_name": "Work", "vgroup_type": "CONNECTION", "items": [
            {"type": "CONNECTION", "server_type": "PGSQL", "name": "Shared"}]},
        {"vgroup_name": "Also", "vgroup_type": "CONNECTION", "items": [
            {"type": "CONNECTION", "server_type": "PGSQL", "name": "Shared"},
            {"type": "CONNECTION", "server_type": "MYSQL", "name": "Missing"}]}]}
    p = source / "Navicat Premium/Profiles/vgroup.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(groups))
    report = discover(source)
    bydriver = {r.config.driver: r for r in report.profiles}
    assert bydriver["postgresql"].group_path == ("Work",)
    assert bydriver["postgresql"].group_paths == (("Work",), ("Also",))
    assert bydriver["mysql"].group_path == ()
    assert report.sources_found == 2
    assert "unresolved_group_members" in {i.code for i in report.issues}
    assert "Shared" not in repr(report) and "Work" not in repr(report)


def test_active_variant_overlays_endpoint_auth_without_changing_source_identity(source):
    primary = profile(profiles=[dict(profilename="Alternate", host="alternate.invalid", port=6543, username="alternate", usessl=False)], active_setting_name="Alternate")
    write_source(source, {"PostgreSQL": {"Base": primary}})
    report = discover(source)
    assert report.profiles[0].config.host == "alternate.invalid"
    assert report.profiles[0].config.port == 6543
    assert not report.profiles[0].blocked
    primary["active_setting_name"] = "No match"
    write_source(source, {"PostgreSQL": {"Base": primary}})
    report = discover(source)
    assert report.profiles[0].blocked
    assert "unknown_active_profile" in {i.code for i in report.issues}


def test_profile_named_like_another_driver_retains_its_actual_family(source):
    write_source(source, {"PostgreSQL": {"MySQL": profile()}})
    assert discover(source).profiles[0].config.driver == "postgresql"


@pytest.mark.parametrize("family,expected", [("MySQL", "mysql"), ("MariaDB", "mariadb"), ("PostgreSQL", "postgresql"),
    ("SQLite", "sqlite"), ("SQLServer", "sqlserver"), ("Oracle", "oracle"), ("MongoDB", "mongodb"), ("REDIS", "redis"), ("Snowflake", "snowflake")])
def test_all_nine_metadata_families_are_preserved(source, family, expected):
    row = profile(dbfilename="/synthetic/example.sqlite", encrypted=0)
    write_source(source, {family: {"Synthetic": row}})
    imported = discover(source).profiles[0]
    assert imported.config.driver == expected
    if expected in {"oracle", "mongodb", "snowflake", "sqlserver"}:
        assert imported.blocked


def test_sqlite_encryption_is_not_mistaken_for_plain_database(source):
    write_source(source, {"SQLite": {"Encrypted": profile(dbfilename="/synthetic/encrypted.sqlite", encrypted=1, password="ciphertext")}})
    imported = discover(source).profiles[0]
    assert imported.config.database == "/synthetic/encrypted.sqlite"
    assert imported.blocked and "encrypted" in imported.unsupported_fields
    assert imported.config.password == ""


@pytest.mark.parametrize("state", [True, "unknown"])
def test_unknown_active_tls_is_blocked_with_secure_fallback(source, state):
    write_source(source, {"PostgreSQL": {"TLS": profile(usessl=state, ssl_param=dict(mode="unobserved-mode", pemclientkeypassword="ciphertext"))}})
    report = discover(source)
    p = report.profiles[0]
    assert p.blocked and p.config.ssl_mode == "verify-full"
    assert p.config.ssl_key_password == ""
    assert "ssl_key_password" in p.requires_secrets
    assert "ciphertext" not in repr(report)


def test_disabled_tls_ignores_inactive_unknown_options(source):
    write_source(source, {"PostgreSQL": {"Plain": profile(usessl=False, ssl_param=dict(mode="unknown", cipher="unknown"))}})
    p = discover(source).profiles[0]
    assert not p.blocked
    assert p.config.ssl_mode == "disable"


@pytest.mark.parametrize("mode", ["allow", "prefer", "require", "verify-ca", "verify-full"])
def test_explicit_known_tls_mode_and_paths(source, mode):
    ssl = dict(mode=mode, rootcert="/synthetic/ca.pem", clientcert="/synthetic/client.pem", clientkeyfile="/synthetic/key.pem", crlfile="/synthetic/crl.pem", saveclientkeypassword=True)
    write_source(source, {"PostgreSQL": {"TLS": profile(usessl=True, ssl_param=ssl)}})
    p = discover(source).profiles[0]
    assert not p.blocked
    assert p.config.ssl_mode == mode
    assert p.config.ssl_ca == "/synthetic/ca.pem" and p.config.ssl_cert == "/synthetic/client.pem"
    assert p.config.ssl_key == "/synthetic/key.pem" and p.config.ssl_crl == "/synthetic/crl.pem"


@pytest.mark.parametrize("method", ["password", "key", "password+key", "agent"])
def test_explicit_ssh_methods_and_private_secrets_not_imported(source, method):
    ssh = dict(authtype=method, host="jump.invalid", port=2222, username="example-ssh", pkeyfile="/synthetic/key", usecompression=True, password="SSH-CIPHERTEXT", pkeypassword="KEY-CIPHERTEXT")
    write_source(source, {"PostgreSQL": {"Tunnel": profile(usetunnel=True, ssh_param=ssh)}})
    report = discover(source)
    p = report.profiles[0]
    assert not p.blocked
    assert p.config.ssh_enabled and p.config.ssh_auth_method == method
    assert p.config.ssh_host == "jump.invalid" and p.config.ssh_port == 2222
    assert p.config.ssh_compression
    assert p.config.ssh_host_key == p.config.ssh_password == p.config.ssh_key_passphrase == ""
    assert not any(v in repr(report) for v in ["SSH-CIPHERTEXT", "KEY-CIPHERTEXT", "jump.invalid"])


def test_unobserved_ssh_enum_blocked_not_silently_password(source):
    write_source(source, {"PostgreSQL": {"Tunnel": profile(usetunnel=True, ssh_param=dict(authtype="99", host="jump.invalid", username="ssh"))}})
    p = discover(source).profiles[0]
    assert p.blocked and "ssh_param.authtype" in p.unsupported_fields


def test_redis_actual_standalone_no_auth_keeps_db_zero_and_no_password_requirement(source):
    write_source(source, {"REDIS": {"Redis": profile(type="Standalone", authenticationmode="None", username="inactive-user")}})
    p = discover(source).profiles[0]
    assert not p.blocked and p.config.database == "0"
    assert p.config.username == "" and p.requires_secrets == ()


@pytest.mark.parametrize("changes,field", [({"usehttptunnel": True}, "usehttptunnel"), ({"unknownjsonproperties": '{"private":"not-public"}'}, "unknownjsonproperties"), ({"usetunnel": "unknown"}, "usetunnel"), ({"port": "not-a-port"}, "port")])
def test_active_unmapped_security_fields_are_blocked_without_value_leaks(source, changes, field):
    write_source(source, {"MySQL": {"Bad": profile(**changes)}})
    r = discover(source)
    assert r.profiles[0].blocked and field in r.profiles[0].unsupported_fields
    assert "not-public" not in repr(r) and "not-a-port" not in repr(r)


@pytest.mark.parametrize("contents", [b"malformed secret bytes", b"<plist><dict><key>secret</key>"])
def test_parser_failures_are_redacted(source, contents):
    (source / "Common/conn.plist").write_bytes(contents)
    r = discover(source)
    assert not r.profiles and r.issues[0].code == "source_read_failed"
    assert "secret" not in repr(r)


def test_missing_source_empty_report_and_common_or_exact_file_roots(source):
    assert not discover(source).profiles and not discover(source).issues
    p = write_source(source, {"PostgreSQL": {"One": profile()}})
    expected = discover(source).profiles[0].source_id
    assert discover(source / "Common").profiles[0].source_id == expected
    assert discover(p).profiles[0].source_id == expected


def test_linux_documented_json_location_recognized_schema_and_unknown_shape(source):
    path = source / "Common/connections.json"
    path.write_text(json.dumps({"PostgreSQL": {"Linux": profile()}}))
    r = discover_navicat_profiles(source, platform="linux")
    assert r.profiles[0].source_format == "linux-json"
    path.write_text(json.dumps({"unknown": ["not a verified schema"]}))
    r = discover_navicat_profiles(source, platform="linux")
    assert not r.profiles and r.issues[0].code == "unsupported_source_structure"


def test_windows_explicit_adapter_and_unaccepted_native_mapping():
    r = discover_navicat_profiles(platform="win32")
    assert not r.profiles and r.issues[0].code == "windows_discovery_pending"
    rows = [RegistryProfile("PostgreSQL", "Synthetic Windows", profile(), "HKCU/synthetic", ("Examples",))]
    r = discover_navicat_profiles(platform="win32", registry_reader=lambda: rows)
    assert len(r.profiles) == 1 and r.profiles[0].source_format == "windows-registry"
    assert r.profiles[0].group_path == ("Examples",)
    assert "Synthetic Windows" not in repr(r) and "HKCU" not in repr(r)


def test_duplicate_uuid_does_not_make_duplicate_connections(source):
    write_source(source, {"PostgreSQL": {"First": profile(), "Second": profile()}})
    r = discover(source)
    assert len(r.profiles) == 1
    assert "duplicate_native_identity" in {i.code for i in r.issues}


def test_unknown_tls_mode_is_not_overridden_by_secondary_verify_flag(source):
    write_source(source, {"PostgreSQL": {"TLS": profile(usessl=True, ssl_param=dict(mode="unobserved", verifyca=True, allowinvalidhostname=True))}})
    p = discover(source).profiles[0]
    assert p.blocked and p.config.ssl_mode == "verify-full"
    assert "ssl_param.mode" in p.unsupported_fields


def test_malformed_type_values_fail_closed_without_throwing_or_showing_values(source):
    write_source(source, {"PostgreSQL": {"Malformed": profile(port=["PRIVATE-BAD-VALUE"], usessl="PRIVATE-BAD-VALUE", usetunnel="PRIVATE-BAD-VALUE")}})
    r = discover(source)
    assert len(r.profiles) == 1 and r.profiles[0].blocked
    assert "PRIVATE-BAD-VALUE" not in repr(r)


def test_missing_uuid_explicitly_reports_rename_identity_limit(source):
    write_source(source, {"PostgreSQL": {"Synthetic": profile(native_id="")}})
    r = discover(source)
    assert len(r.profiles) == 1 and not r.profiles[0].blocked
    assert any(i.code == "missing_native_identity" and not i.blocking for i in r.issues)
    assert discover(source).profiles[0].source_id == r.profiles[0].source_id


def test_source_limits_are_redacted_without_reading_payload(source, monkeypatch):
    p = write_source(source, {"PostgreSQL": {"Synthetic": profile()}})
    import opennavicat.navicat_profiles as module
    monkeypatch.setattr(module, "_MAX_SOURCE", 10)
    r = discover(source)
    assert not r.profiles and r.issues[0].code == "source_read_failed"


@pytest.mark.parametrize("extra,field", [({"serviceprovider": "unobserved-cloud-auth"}, "serviceprovider"), ({"authmethod": "unobserved-sso"}, "authmethod")])
def test_unknown_provider_or_database_authentication_is_not_silently_password(source, extra, field):
    write_source(source, {"PostgreSQL": {"Authentication": profile(**extra)}})
    p = discover(source).profiles[0]
    assert p.blocked and field in p.unsupported_fields


def test_unobserved_numeric_tls_mode_cannot_be_lost_as_empty_text(source):
    write_source(source, {"PostgreSQL": {"Numeric TLS": profile(usessl=True, ssl_param=dict(mode=99, verifyca=False))}})
    p = discover(source).profiles[0]
    assert p.blocked and p.config.ssl_mode == "verify-full"
    assert "ssl_param.mode" in p.unsupported_fields


def test_empty_native_uuid_does_not_merge_same_name_across_source_namespaces(source):
    path = source / "Common/conn.plist"
    content = {"workspace-a": {"PostgreSQL": {"Same display name": profile(native_id="", host="a.invalid")}},
               "workspace-b": {"PostgreSQL": {"Same display name": profile(native_id="", host="b.invalid")}}}
    path.write_bytes(plistlib.dumps(content))
    first = discover(source)
    assert len(first.profiles) == 2
    ids = {r.config.host: r.source_id for r in first.profiles}
    assert ids["a.invalid"] != ids["b.invalid"]
    # Another connection and a different dictionary order must not shift IDs.
    content["workspace-a"]["PostgreSQL"]["Added"] = profile(native_id="extra")
    path.write_bytes(plistlib.dumps(dict(reversed(list(content.items())))))
    later = discover(source)
    assert {r.config.host: r.source_id for r in later.profiles if r.config.name == "Same display name"} == ids
    assert "workspace-a" not in repr(first) and "a.invalid" not in repr(first)


def test_ambiguous_name_based_group_member_is_not_assigned_to_wrong_namespace(source):
    path = source / "Common/conn.plist"
    path.write_bytes(plistlib.dumps({"a": {"PostgreSQL": {"Same": profile(native_id="a")}},
                                    "b": {"PostgreSQL": {"Same": profile(native_id="b")}}}))
    groups = source / "Navicat Premium/Profiles/vgroup.json"
    groups.parent.mkdir(parents=True)
    groups.write_text(json.dumps({"vgroups": [{"vgroup_name": "Example", "vgroup_type": "CONNECTION", "items": [
        {"name": "Same", "server_type": "PGSQL", "type": "CONNECTION"}]}]}))
    r = discover(source)
    assert len(r.profiles) == 2 and all(not p.group_path for p in r.profiles)
    assert "ambiguous_group_members" in {i.code for i in r.issues}


@pytest.mark.parametrize("flag", ["usessl", "usetunnel"])
def test_missing_remote_transport_state_is_unknown_not_insecure_default(source, flag):
    row = profile()
    del row[flag]
    write_source(source, {"PostgreSQL": {"Incomplete transport": row}})
    p = discover(source).profiles[0]
    assert p.blocked and flag in p.unsupported_fields
    if flag == "usessl":
        assert p.config.ssl_mode == "verify-full"


@pytest.mark.parametrize("native,expected", [(0,"password"),(1,"key"),(2,"password+key"),("0","password"),("1","key"),("2","password+key")])
def test_observed_mac_ssh_numeric_enums_and_historic_text_numerals(source, native, expected):
    write_source(source, {"PostgreSQL": {"Synthetic": profile(usetunnel=True, ssh_param=dict(authtype=native,host="jump.invalid",username="probe",pkeyfile="/synthetic/unused-key",usecompression=True))}})
    p = discover(source).profiles[0]
    assert not p.blocked and p.config.ssh_auth_method == expected
    assert p.config.ssh_compression
    assert not p.config.ssh_password and not p.config.ssh_key_passphrase


@pytest.mark.parametrize("native,expected", [("允许","allow"),("prefer","prefer"),("require","require"),("verify-ca","verify-ca"),("verify-full","verify-full")])
def test_observed_mac_pg_tls_modes_override_inactive_generic_validation_flags(source, native, expected):
    ssl = dict(mode=native,verifyca=False,allowinvalidhostname=False,weakcertvalidation=True,useauth=False,
               rootcert="",cacert="",clientcert="",clientkeyfile="",crlfile="")
    write_source(source, {"PostgreSQL": {"Synthetic": profile(usessl=True,ssl_param=ssl)}})
    p = discover(source).profiles[0]
    assert not p.blocked and p.config.ssl_mode == expected


@pytest.mark.parametrize("enabled", [False,True])
def test_observed_mac_pg_custom_certificate_toggle_gates_retained_paths(source, enabled):
    ssl = dict(mode="verify-full",verifyca=False,weakcertvalidation=True,useauth=enabled,
               rootcert="/synthetic/ca.pem",clientcert="/synthetic/client.pem",clientkeyfile="/synthetic/key.pem",
               crlfile="/synthetic/crl.pem",saveclientkeypassword=True,pemclientkeypassword="CIPHERTEXT")
    write_source(source, {"PostgreSQL": {"Synthetic": profile(usessl=True,ssl_param=ssl)}})
    p = discover(source).profiles[0]
    assert not p.blocked
    assert (p.config.ssl_ca,p.config.ssl_cert,p.config.ssl_key,p.config.ssl_crl) == (
        ("/synthetic/ca.pem","/synthetic/client.pem","/synthetic/key.pem","/synthetic/crl.pem") if enabled else ("","","",""))
    assert ("ssl_key_password" in p.requires_secrets) is enabled
    assert p.config.ssl_key_password == ""


def test_numeric_ssh_mapping_is_not_invented_for_unaccepted_registry_or_json(source):
    row = profile(usetunnel=True,ssh_param=dict(authtype=1,host="jump.invalid",username="probe",pkeyfile="/synthetic/key"))
    reg = discover_navicat_profiles(platform="win32",registry_reader=lambda:[RegistryProfile("PostgreSQL","Example",row)])
    assert reg.profiles[0].blocked and "ssh_param.authtype" in reg.profiles[0].unsupported_fields
    (source/"Common/connections.json").write_text(json.dumps({"PostgreSQL":{"Example":row}}))
    linux = discover_navicat_profiles(source,platform="linux")
    assert linux.profiles[0].blocked and "ssh_param.authtype" in linux.profiles[0].unsupported_fields


def test_pg_tls_custom_file_toggle_unknown_state_remains_blocked(source):
    write_source(source, {"PostgreSQL": {"Synthetic": profile(usessl=True,ssl_param=dict(mode="verify-full",useauth="unknown",rootcert="/synthetic/ca.pem"))}})
    p = discover(source).profiles[0]
    assert p.blocked and "ssl_param.useauth" in p.unsupported_fields
    assert p.config.ssl_ca == ""


def test_generic_weak_validation_flag_is_not_assumed_inactive_for_other_drivers(source):
    write_source(source, {"MySQL": {"Synthetic": profile(usessl=True,ssl_param=dict(mode="verify-full",weakcertvalidation=True))}})
    p = discover(source).profiles[0]
    assert p.blocked and "ssl_param.weakcertvalidation" in p.unsupported_fields


@pytest.mark.parametrize("field,value", [("type",99),("type",None),("authenticationmode",99),("authenticationmode",None)])
def test_redis_unknown_typed_topology_or_auth_is_not_defaulted_to_single_no_auth(source, field, value):
    row = profile(type="Standalone",authenticationmode="None")
    row[field] = value
    if value is None:
        (source/"Common/connections.json").write_text(json.dumps({"REDIS":{"Synthetic":row}}))
        p = discover_navicat_profiles(source,platform="linux").profiles[0]
    else:
        write_source(source,{"REDIS":{"Synthetic":row}})
        p = discover(source).profiles[0]
    assert p.blocked and field in p.unsupported_fields


@pytest.mark.parametrize("database,expected", [(0,"0"),(2,"2"),("12","12")])
def test_redis_explicit_database_number_is_preserved(source,database,expected):
    write_source(source,{"REDIS":{"Synthetic":profile(type="Standalone",authenticationmode="None",database=database)}})
    p = discover(source).profiles[0]
    assert not p.blocked and p.config.database == expected


@pytest.mark.parametrize("database", [-1,True,"other",None])
def test_redis_invalid_database_is_blocked_before_default_database_can_be_used(source,database):
    row = profile(type="Standalone",authenticationmode="None",database=database)
    if database is None:
        (source/"Common/connections.json").write_text(json.dumps({"REDIS":{"Synthetic":row}}))
        p = discover_navicat_profiles(source,platform="linux").profiles[0]
    else:
        write_source(source,{"REDIS":{"Synthetic":row}})
        p = discover(source).profiles[0]
    assert p.blocked and "database" in p.unsupported_fields


def test_observed_mongo_password_standalone_fields_are_prefilled_without_unblocking_or_secrets(source):
    row = profile(port="27017", authmechanism="Password", authsource="admin", connmethod="Standalone",
                  readpreference="Primary", retryreads=False, retrywrites=True,
                  usesrvrecord=False, replicasetname="", memberlist=[], password="GENERATED-SECRET")
    write_source(source, {"MongoDB": {"Generated MongoDB": row}})
    report = discover(source)
    imported = report.profiles[0]
    assert imported.config.mongo_auth_mechanism == "DEFAULT"
    assert imported.config.mongo_auth_source == "admin"
    assert imported.config.mongo_connection_type == "standalone"
    assert imported.config.mongo_read_preference == "primary"
    assert imported.config.mongo_retry_reads is False and imported.config.mongo_retry_writes is True
    assert not imported.config.mongo_srv and imported.config.mongo_members == ""
    assert imported.blocked and "mongodb_source_settings_review" in {issue.code for issue in report.issues}
    assert "driver_not_available" not in {issue.code for issue in report.issues}
    assert imported.config.password == "" and "GENERATED-SECRET" not in repr(report)


@pytest.mark.parametrize("field", ["initialsessionqueries", "connectionparameters"])
def test_unmapped_source_session_options_require_review_instead_of_silent_omission(source, field):
    write_source(source, {"PostgreSQL": {"Generated": profile(**{field: "synthetic option"})}})
    report = discover(source)
    assert report.profiles[0].blocked and field in report.profiles[0].unsupported_fields
    assert "source_session_options_review" in {issue.code for issue in report.issues}
