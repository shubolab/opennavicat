"""Owned SQL Server CI lifecycle contracts; no emulation or user endpoints."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import stat
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location("owned_sqlserver_ci", Path(__file__).resolve().parents[1]/"scripts/prepare-test-sqlserver.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_generated_ca_san_key_and_endpoint_material_are_private(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.x509.oid import ExtendedKeyUsageOID
    directory = tmp_path/"certs"
    material = fixture.make_certificates(directory)
    ca = x509.load_pem_x509_certificate(Path(material["ca"]).read_bytes())
    wrong = x509.load_pem_x509_certificate(Path(material["wrong_ca"]).read_bytes())
    server = x509.load_pem_x509_certificate((directory/"server.pem").read_bytes())
    ca.public_key().verify(server.signature, server.tbs_certificate_bytes, padding.PKCS1v15(), server.signature_hash_algorithm)
    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        wrong.public_key().verify(server.signature, server.tbs_certificate_bytes, padding.PKCS1v15(), server.signature_hash_algorithm)
    names = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["localhost"]
    assert str(names.get_values_for_type(x509.IPAddress)[0]) == "127.0.0.1"
    assert ExtendedKeyUsageOID.SERVER_AUTH in server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert "tlsprotocols = 1.2" in (directory/"mssql.conf").read_text()
    assert "forceencryption = 0" in (directory/"mssql.conf").read_text()
    if __import__("os").name != "nt":
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in directory.iterdir())


@pytest.mark.parametrize("os_type,arch", [("linux","aarch64"),("windows","amd64")])
def test_native_guard_rejects_unsupported_server_without_run(monkeypatch, os_type, arch):
    calls=[]
    def docker(*arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(stdout=json.dumps(dict(OSType=os_type,Architecture=arch)))
    monkeypatch.setattr(fixture,"_docker",docker)
    with pytest.raises(RuntimeError,match="native Linux amd64"):
        fixture.require_native_docker()
    assert len(calls)==1 and calls[0][0]=="info"


def docker_double(monkeypatch, *, wrong_owner=False):
    calls, state = [], {}
    def docker(*arguments, **kwargs):
        calls.append(arguments)
        output=""
        if arguments[0]=="run":
            state["name"]=arguments[arguments.index("--name")+1]
            state["token"]=next(arg.split("=",1)[1] for arg in arguments if arg.startswith(fixture.LABEL+"="))
            state["password"]=next(line.split("=",1)[1] for line in Path(arguments[arguments.index("--env-file")+1]).read_text().splitlines() if line.startswith("MSSQL_SA_PASSWORD="))
            output="owned-container-id"
        elif arguments[0]=="port":output="127.0.0.1:32123\n"
        elif arguments[0]=="logs":output="Synthetic startup failure with fixture password="+state["password"]
        elif arguments[0]=="inspect" and len(arguments)>2 and ".Config.Labels" in arguments[2]:output="different-owner" if wrong_owner else state["token"]
        elif arguments[0]=="inspect" and "--format" not in arguments:return SimpleNamespace(stdout="",returncode=1)
        elif arguments[0]=="inspect":output="sha256:owned-image"
        return SimpleNamespace(stdout=output,returncode=0)
    monkeypatch.setattr(fixture,"require_native_docker",lambda:None)
    monkeypatch.setattr(fixture,"_docker",docker)
    monkeypatch.setattr(fixture,"probe_tls",lambda endpoint,**kwargs:dict(external_tls_select=True,encrypted=True))
    return calls,state


def test_owned_fixture_binds_only_loopback_and_cleans_its_exact_token_after_child_failure(monkeypatch):
    calls,state=docker_double(monkeypatch)
    evidence={}
    endpoint_path=None
    with pytest.raises(RuntimeError,match="synthetic child failure"):
        with fixture.owned_sqlserver(evidence) as path:
            endpoint_path=path
            endpoint=json.loads(path.read_text())
            assert endpoint["host"]=="localhost" and endpoint["port"]==32123
            assert endpoint["ssl_mode"]=="disable" and Path(endpoint["tls"]["ca"]).is_file()
            assert endpoint["password"]
            if os.name != "nt":assert stat.S_IMODE(path.stat().st_mode)==0o600
            raise RuntimeError("synthetic child failure")
    run=next(call for call in calls if call[0]=="run")
    assert "127.0.0.1::1433" in run and "--platform" not in run
    assert run[run.index("--user")+1]=="0:0"
    assert not any("MSSQL_SA_PASSWORD=" in argument for argument in run)
    assert next(call for call in calls if call[0]=="rm")==("rm","--force","--volumes",state["name"])
    assert evidence["cleanup_removed"] and not endpoint_path.exists()
    assert state["password"] not in json.dumps(evidence)
    assert "<redacted>" in evidence["server_log_tail"]


def test_cleanup_refuses_foreign_owner_even_with_matching_container_name(monkeypatch):
    calls,_=docker_double(monkeypatch,wrong_owner=True)
    with pytest.raises(RuntimeError,match="exact owner token"):
        with fixture.owned_sqlserver({}):pass
    assert not any(call[0]=="rm" for call in calls)


def junit(path, *, skip=False, count=17):
    suite=ET.Element("testsuite")
    for number in range(count):
        case=ET.SubElement(suite,"testcase",classname="tests.test_sqlserver_session",name="test_real_owned_"+str(number))
        if skip and number==0:ET.SubElement(case,"skipped")
    ET.ElementTree(suite).write(path)


def test_junit_gate_rejects_silent_live_case_skips_or_incomplete_selection(tmp_path):
    path=tmp_path/"results.xml"
    junit(path,skip=True)
    with pytest.raises(RuntimeError,match="zero skips"):fixture.check_junit(path)
    junit(path,count=16)
    with pytest.raises(RuntimeError,match="all 17"):fixture.check_junit(path)
    junit(path)
    assert fixture.check_junit(path)==dict(tests=17,sqlserver_real_tests=17,skipped=0)


def test_child_timeout_terminates_only_its_owned_process_group(tmp_path, monkeypatch):
    processes = []
    real_popen = subprocess.Popen
    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(fixture.subprocess, "Popen", capture)
    with pytest.raises(subprocess.TimeoutExpired):
        fixture.run_tests([sys.executable, "-c", "import time;time.sleep(60)"], tmp_path/"owned.json", 0.05)
    assert len(processes) == 1 and processes[0].poll() is not None
