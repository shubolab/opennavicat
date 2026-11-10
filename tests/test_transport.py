"""Real local SSH protocol tests; every listener/agent/key belongs to a fixture."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import re
import select
import shutil
import socket
import subprocess
import threading
import time

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from opennavicat.database import ConnectionConfig, DatabaseError, DatabaseSession
from opennavicat.transport import SSHTunnel, SSHHostKeyError, SSHAuthenticationError


def pin(key):
    return f"{key.get_name()} {key.get_base64()}"


class FixtureSSHServer(paramiko.ServerInterface):
    def __init__(self, fixture):
        self.fixture = fixture
        self.key_ok = self.password_ok = False
        self.destinations = {}

    def get_allowed_auths(self, username):
        mode = self.fixture.mode
        if mode == "combined":
            return "password" if self.key_ok else "publickey"
        if mode == "reverse":
            return "publickey" if self.password_ok else "password"
        return "password" if mode == "password" else "publickey"

    def check_auth_password(self, username, password):
        self.fixture.auth_calls.append("password")
        if username != "fixture" or password != "ssh-test-only":
            return paramiko.AUTH_FAILED
        self.password_ok = True
        if self.fixture.mode in {"combined", "reverse"}:
            return paramiko.AUTH_SUCCESSFUL if self.key_ok else paramiko.AUTH_PARTIALLY_SUCCESSFUL
        return paramiko.AUTH_SUCCESSFUL if self.fixture.mode == "password" else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.fixture.auth_calls.append("publickey")
        if username != "fixture" or key != self.fixture.user_key:
            return paramiko.AUTH_FAILED
        self.key_ok = True
        if self.fixture.mode in {"combined", "reverse"}:
            return paramiko.AUTH_SUCCESSFUL if self.password_ok else paramiko.AUTH_PARTIALLY_SUCCESSFUL
        return paramiko.AUTH_SUCCESSFUL if self.fixture.mode == "key" else paramiko.AUTH_FAILED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.destinations[chanid] = destination
        return paramiko.OPEN_SUCCEEDED


class SSHFixture:
    def __init__(self, user_key):
        self.user_key = user_key
        self.host_key = paramiko.RSAKey.generate(2048)
        self.mode = "password"
        self.auth_calls = []
        self.transports = []
        self.stopped = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0)); self.sock.listen(); self.sock.settimeout(.1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self.serve, args=(client,), daemon=True).start()

    def serve(self, sock):
        transport = paramiko.Transport(sock)
        self.transports.append(transport)
        transport.add_server_key(self.host_key)
        server = FixtureSSHServer(self)
        try:
            transport.start_server(server=server)
            while transport.is_active() and not self.stopped.is_set():
                channel = transport.accept(.1)
                if channel is not None:
                    threading.Thread(target=self.bridge, args=(channel,server.destinations[channel.chanid]), daemon=True).start()
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def bridge(self, channel, destination):
        remote = None
        try:
            remote = socket.create_connection(destination, 2)
            while not self.stopped.is_set():
                ready,_,_ = select.select([channel,remote],[],[],.1)
                if channel in ready:
                    data=channel.recv(65536)
                    if not data:break
                    remote.sendall(data)
                if remote in ready:
                    data=remote.recv(65536)
                    if not data:break
                    channel.sendall(data)
        except (OSError,EOFError,paramiko.SSHException):
            pass
        finally:
            with contextlib.suppress(EOFError,OSError,paramiko.SSHException):channel.close()
            if remote is not None:remote.close()

    def close(self):
        self.stopped.set();self.sock.close()
        for transport in self.transports:transport.close()
        self.thread.join(2)


@pytest.fixture
def ssh_fixture(tmp_path,monkeypatch):
    from opennavicat import transport
    monkeypatch.setattr(transport,"known_hosts_paths",lambda:[])
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    key_path=tmp_path/"id_fixture"
    key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    key_path.chmod(0o600)
    server=SSHFixture(paramiko.RSAKey.from_private_key_file(str(key_path)))
    config=ConnectionConfig("ssh-fixture","Fixture","postgresql",host="127.0.0.1",ssh_enabled=True,
        ssh_host="127.0.0.1",ssh_port=server.port,ssh_username="fixture",ssh_password="ssh-test-only",ssh_key_file=str(key_path),connect_timeout=3)
    yield server,config,private
    server.close()


@pytest.fixture
def echo_server():
    stopped=threading.Event();listener=socket.socket();listener.bind(("127.0.0.1",0));listener.listen();listener.settimeout(.1)
    def echo(sock):
        try:
            with sock:
                while data:=sock.recv(65536):sock.sendall(data)
        except OSError:pass
    def accept():
        while not stopped.is_set():
            try:client,_=listener.accept()
            except socket.timeout:continue
            except OSError:return
            threading.Thread(target=echo,args=(client,),daemon=True).start()
    worker=threading.Thread(target=accept,daemon=True);worker.start()
    yield listener.getsockname()
    stopped.set();listener.close();worker.join(2)


def test_unknown_and_mismatched_hostkeys_precede_credentials(ssh_fixture,echo_server):
    server,config,_=ssh_fixture
    tunnel=SSHTunnel(config,*echo_server)
    with pytest.raises(SSHHostKeyError) as error:tunnel.start()
    assert error.value.hostname=="127.0.0.1" and error.value.port==server.port
    assert error.value.pin==pin(server.host_key) and error.value.fingerprint.startswith("SHA256:")
    assert error.value.expected is None and not server.auth_calls
    assert not tunnel.active and tunnel.local_port==0
    config.ssh_host_key=pin(paramiko.RSAKey.generate(2048))
    with pytest.raises(SSHHostKeyError) as mismatch:tunnel.start()
    assert mismatch.value.expected==config.ssh_host_key and not server.auth_calls


def test_approved_password_forward_is_loopback_only_and_closes(ssh_fixture,echo_server):
    server,config,_=ssh_fixture;config.ssh_host_key=pin(server.host_key);config.ssh_compression=True
    tunnel=SSHTunnel(config,*echo_server).start();endpoint=tunnel.endpoint
    assert endpoint[0]=="127.0.0.1" and endpoint[1]>0
    assert tunnel.transport.local_compression in {"zlib@openssh.com","zlib","none"}
    for payload in (b"real SSH forward",bytes(range(256))*128):
        with socket.create_connection(endpoint,2) as sock:
            sock.sendall(payload);received=b""
            while len(received)<len(payload):received+=sock.recv(65536)
            assert received==payload
    tunnel.close();tunnel.close()
    assert not tunnel.active and tunnel.local_port==0
    with socket.socket() as sock:assert sock.connect_ex(endpoint)!=0
    assert not any(t.is_alive() for t in tunnel._workers)


def test_known_hosts_trust_and_conflicting_known_hosts_hard_reject(ssh_fixture,echo_server,tmp_path):
    server,config,_=ssh_fixture
    hosts=tmp_path/"known_hosts";hosts.write_text(f"[127.0.0.1]:{server.port} {pin(server.host_key)}\n")
    with SSHTunnel(config,*echo_server,known_hosts_paths=[hosts]) as tunnel:assert tunnel.active
    hashed=paramiko.HostKeys.hash_host(f"[127.0.0.1]:{server.port}")
    hosts.write_text(f"{hashed} {pin(server.host_key)}\n")
    with SSHTunnel(config,*echo_server,known_hosts_paths=[hosts]) as tunnel:assert tunnel.active
    wrong=paramiko.RSAKey.generate(2048);hosts.write_text(f"[127.0.0.1]:{server.port} {pin(wrong)}\n")
    config.ssh_host_key=pin(server.host_key)
    with pytest.raises(SSHHostKeyError) as error:SSHTunnel(config,*echo_server,known_hosts_paths=[hosts]).start()
    assert error.value.expected==pin(wrong)


@pytest.mark.parametrize("mode",["key","combined","reverse"])
def test_encrypted_openssh_key_and_real_partial_multifactor(ssh_fixture,echo_server,tmp_path,mode):
    server,config,private=ssh_fixture;server.mode=mode;config.ssh_host_key=pin(server.host_key)
    config.ssh_auth_method="key" if mode=="key" else "password+key"
    encrypted=tmp_path/"encrypted_key"
    encrypted.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,
                                               serialization.BestAvailableEncryption(b"key-passphrase")))
    encrypted.chmod(0o600);config.ssh_key_file=str(encrypted)
    with pytest.raises(SSHAuthenticationError) as error:SSHTunnel(config,*echo_server).start()
    assert error.value.needs_passphrase and error.value.method==config.ssh_auth_method
    config.ssh_key_passphrase="wrong-passphrase"
    with pytest.raises(SSHAuthenticationError) as wrong:SSHTunnel(config,*echo_server).start()
    assert wrong.value.needs_passphrase and wrong.value.__cause__
    config.ssh_key_passphrase="key-passphrase"
    with SSHTunnel(config,*echo_server) as tunnel:
        with socket.create_connection(tunnel.endpoint,2) as sock:
            sock.sendall(b"encrypted-key");assert sock.recv(50)==b"encrypted-key"
    if mode!="key":assert "password" in server.auth_calls and "publickey" in server.auth_calls


def test_auth_failure_is_typed_and_combined_is_not_a_fallback(ssh_fixture,echo_server):
    server,config,_=ssh_fixture;config.ssh_host_key=pin(server.host_key);config.ssh_password="incorrect"
    with pytest.raises(SSHAuthenticationError) as error:SSHTunnel(config,*echo_server).start()
    assert not error.value.needs_passphrase and error.value.method=="password" and error.value.__cause__
    server.mode="key";config.ssh_auth_method="password+key"
    with pytest.raises(SSHAuthenticationError):SSHTunnel(config,*echo_server).start()


def test_actual_agent_key_authentication(ssh_fixture,echo_server,monkeypatch):
    if os.name == "nt":pytest.skip("Owned Unix SSH agent test; native Windows OpenSSH/Pageant acceptance remains separate")
    if not shutil.which("ssh-agent") or not shutil.which("ssh-add"):pytest.skip("OpenSSH agent utilities unavailable")
    server,config,_=ssh_fixture;server.mode="key";config.ssh_host_key=pin(server.host_key);config.ssh_auth_method="agent"
    output=subprocess.run(["ssh-agent","-s"],capture_output=True,text=True,check=True).stdout
    agent_env={"SSH_AUTH_SOCK":re.search(r"SSH_AUTH_SOCK=([^;]+)",output).group(1),
               "SSH_AGENT_PID":re.search(r"SSH_AGENT_PID=([^;]+)",output).group(1)}
    environment={**os.environ,**agent_env}
    for key,value in agent_env.items():monkeypatch.setenv(key,value)
    try:
        subprocess.run(["ssh-add",config.ssh_key_file],env=environment,capture_output=True,check=True)
        with SSHTunnel(config,*echo_server) as tunnel:assert tunnel.active
    finally:
        subprocess.run(["ssh-agent","-k"],env=environment,capture_output=True,check=False)


def test_database_failure_drains_owned_tunnel(ssh_fixture,monkeypatch):
    import opennavicat.transport as transport
    server,config,_=ssh_fixture;config.ssh_host_key=pin(server.host_key);config.ssl_mode="disable"
    # Reserve then release an unused destination; no existing DB is contacted.
    with socket.socket() as probe:probe.bind(("127.0.0.1",0));config.port=probe.getsockname()[1]
    instances=[];original=transport.SSHTunnel
    def capture(*args,**kwargs):
        tunnel=original(*args,**kwargs);instances.append(tunnel);return tunnel
    monkeypatch.setattr(transport,"SSHTunnel",capture)
    session=DatabaseSession(config)
    with pytest.raises(DatabaseError) as error:session.connect()
    assert not error.value.authentication_required
    assert session._tunnel is None and session.connection is None
    assert len(instances)==1 and not instances[0].active and instances[0].local_port==0
