"""Owned SSH TCP forwarding with explicit host identity validation.

Only loopback listeners are created. Host keys are checked before any user
credential is sent; unknown identities are returned to the UI for an explicit
trust decision, never accepted or written to known_hosts automatically.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import os
from pathlib import Path
import select
import socket
import threading
import time

import paramiko


class SSHHostKeyError(paramiko.SSHException):
    def __init__(self, hostname, port, key, expected=None):
        self.hostname, self.port = hostname, int(port)
        self.key_type, self.key_base64 = key.get_name(), key.get_base64()
        digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        self.fingerprint = "SHA256:" + digest
        self.expected = expected
        kind = "mismatch" if expected else "is not trusted"
        super().__init__(f"SSH host key {kind} for {hostname}:{port} ({self.key_type}, {self.fingerprint})")

    @property
    def pin(self):
        return f"{self.key_type} {self.key_base64}"


class TLSKeyPasswordError(ValueError):
    needs_passphrase = True

    def __init__(self):
        super().__init__("TLS private-key passphrase is required or invalid")


class SSHAuthenticationError(paramiko.AuthenticationException):
    def __init__(self, method, needs_passphrase=False, cause=None):
        self.method = method
        self.needs_passphrase = bool(needs_passphrase)
        self.cause = cause
        detail = "private-key passphrase is required" if needs_passphrase else "authentication failed"
        super().__init__(f"SSH {detail} ({method})")


def _public_pin(key):
    return f"{key.get_name()} {key.get_base64()}"


def known_hosts_paths():
    paths = [Path.home() / ".ssh" / "known_hosts", Path("/etc/ssh/ssh_known_hosts")]
    if os.name == "nt" and os.environ.get("PROGRAMDATA"):
        paths.append(Path(os.environ["PROGRAMDATA"]) / "ssh" / "ssh_known_hosts")
    return paths


def verify_host_key(hostname, port, key, pin="", paths=None):
    """Validate a configured public-key pin and existing OpenSSH known_hosts.

    A mismatch against either configured evidence is a hard failure. An explicit
    matching pin can trust an otherwise unknown host, without changing host files.
    ``paths=[]`` is a test/embedding seam that isolates system host-key files.
    """
    trusted_pin = False
    if pin:
        parts = pin.strip().split()
        if len(parts) != 2:
            raise ValueError("SSH host-key pin must contain an algorithm and base64 public key")
        try:
            public = paramiko.PKey.from_type_string(parts[0], base64.b64decode(parts[1], validate=True))
        except Exception as exc:
            raise ValueError("SSH host-key pin is not a valid public key") from exc
        normalized = _public_pin(public)
        if not hmac.compare_digest(normalized, _public_pin(key)):
            raise SSHHostKeyError(hostname, port, key, expected=normalized)
        trusted_pin = True
    known = paramiko.HostKeys()
    for filename in known_hosts_paths() if paths is None else paths:
        path = Path(filename).expanduser()
        if path.exists():
            known.load(str(path))
    names = [hostname, hostname.lower()] if int(port) == 22 else [f"[{hostname}]:{port}", f"[{hostname.lower()}]:{port}"]
    for name in dict.fromkeys(names):
        entry = known.lookup(name)
        if not entry:
            continue
        existing = entry.get(key.get_name())
        if existing is not None:
            if not hmac.compare_digest(existing.asbytes(), key.asbytes()):
                raise SSHHostKeyError(hostname, port, key, expected=_public_pin(existing))
            return
        if not trusted_pin:
            expected = _public_pin(next(iter(entry.values())))
            raise SSHHostKeyError(hostname, port, key, expected=expected)
    if not trusted_pin:
        raise SSHHostKeyError(hostname, port, key)


def _encrypted_private_key(path):
    """Read encryption declarations, not exception wording or file-name guesses."""
    data = Path(path).expanduser().read_bytes()
    if b"BEGIN ENCRYPTED PRIVATE KEY" in data or b"Proc-Type: 4,ENCRYPTED" in data:
        return True
    if b"BEGIN OPENSSH PRIVATE KEY" not in data:
        return False
    try:
        body = b"".join(line.strip() for line in data.splitlines() if not line.startswith(b"-----"))
        decoded = base64.b64decode(body, validate=True)
        magic = b"openssh-key-v1\x00"
        if not decoded.startswith(magic):
            return False
        message = paramiko.Message(decoded[len(magic):])
        cipher, kdf = message.get_text(), message.get_text()
        return bool(cipher and cipher != "none" and kdf == "bcrypt")
    except (ValueError, EOFError, UnicodeError):
        return False


class SSHTunnel:
    """One authenticated SSH connection and one ephemeral loopback TCP forward."""

    def __init__(self, config, remote_host, remote_port, known_hosts_paths=None):
        self.config, self.remote_host, self.remote_port = config, remote_host, int(remote_port)
        self.known_hosts_paths = known_hosts_paths
        self.local_host, self.local_port = "127.0.0.1", 0
        self.transport = None
        self._socket = self._listener = self._accept_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clients, self._channels, self._workers = set(), set(), set()

    @property
    def endpoint(self):
        if not self.local_port or self._stop.is_set():
            raise RuntimeError("SSH tunnel is not running")
        return self.local_host, self.local_port

    @property
    def active(self):
        return bool(self.transport and self.transport.is_active() and self.transport.is_authenticated() and not self._stop.is_set())

    def start(self):
        if self.active:
            return self
        c = self.config
        if not c.ssh_host or not c.ssh_username:
            raise ValueError("SSH hostname and username are required")
        if c.ssh_auth_method not in {"password", "key", "agent", "password+key"}:
            raise ValueError("Unsupported SSH authentication method")
        if not 1 <= int(c.ssh_port) <= 65535 or not 1 <= self.remote_port <= 65535:
            raise ValueError("SSH and remote database ports must be between 1 and 65535")
        timeout = int(c.connect_timeout)
        self._stop.clear()
        try:
            self._socket = socket.create_connection((c.ssh_host, int(c.ssh_port)), timeout)
            self.transport = paramiko.Transport(self._socket)
            self.transport.banner_timeout = self.transport.auth_timeout = timeout
            self.transport.use_compression(bool(c.ssh_compression))
            self.transport.start_client(timeout=timeout)
            verify_host_key(c.ssh_host, c.ssh_port, self.transport.get_remote_server_key(), c.ssh_host_key,
                            self.known_hosts_paths)
            try:
                self._authenticate()
            except SSHAuthenticationError:
                raise
            except paramiko.PasswordRequiredException as exc:
                raise SSHAuthenticationError(c.ssh_auth_method, True, exc) from exc
            except paramiko.AuthenticationException as exc:
                raise SSHAuthenticationError(c.ssh_auth_method, False, exc) from exc
            self.transport.set_keepalive(30)
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener.bind((self.local_host, 0))
            self._listener.listen(16)
            self._listener.settimeout(.2)
            self.local_port = self._listener.getsockname()[1]
            self._accept_thread = threading.Thread(target=self._accept, name="OpenNavicat-SSH-forward", daemon=True)
            self._accept_thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def _private_key(self):
        c = self.config
        if not c.ssh_key_file:
            raise ValueError("An SSH private-key file is required")
        # Empty passphrase is intentional: never invoke an interactive key prompt.
        try:
            password = c.ssh_key_passphrase.encode("utf-8") if c.ssh_key_passphrase else None
            return paramiko.PKey.from_path(Path(c.ssh_key_file).expanduser(), password=password)
        except (TypeError, ValueError, paramiko.PasswordRequiredException, paramiko.SSHException) as exc:
            if _encrypted_private_key(c.ssh_key_file):
                raise SSHAuthenticationError(c.ssh_auth_method, True, exc) from exc
            raise

    def _authenticate(self):
        c, transport = self.config, self.transport
        if c.ssh_auth_method == "password":
            transport.auth_password(c.ssh_username, c.ssh_password)
        elif c.ssh_auth_method == "key":
            transport.auth_publickey(c.ssh_username, self._private_key())
        elif c.ssh_auth_method == "agent":
            agent = paramiko.Agent()
            try:
                for key in agent.get_keys():
                    try:
                        transport.auth_publickey(c.ssh_username, key)
                        if transport.is_authenticated():
                            break
                    except paramiko.AuthenticationException:
                        continue
            finally:
                agent.close()
        else:
            key = self._private_key()
            completed = set()
            order = ["publickey", "password"]
            while order and not transport.is_authenticated():
                method = order.pop(0)
                try:
                    remaining = (transport.auth_publickey(c.ssh_username, key) if method == "publickey"
                                 else transport.auth_password(c.ssh_username, c.ssh_password))
                    completed.add(method)
                except paramiko.BadAuthenticationType as exc:
                    # Respect servers requiring password first, then the key.
                    alternative = "password" if method == "publickey" else "publickey"
                    if alternative in exc.allowed_types and alternative not in completed:
                        order = [alternative, method]
                        continue
                    raise
                if transport.is_authenticated() and completed != {"publickey", "password"}:
                    raise paramiko.AuthenticationException("SSH password+key requires the server to enforce both authentication factors")
                if remaining and not transport.is_authenticated():
                    order = [m for m in ("publickey", "password") if m in remaining and m not in completed]
            if completed != {"publickey", "password"}:
                raise paramiko.AuthenticationException("SSH server did not complete both password and public-key authentication")
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("SSH authentication did not complete")

    def _accept(self):
        while not self._stop.is_set():
            try:
                client, address = self._listener.accept()
            except socket.timeout:
                continue
            except (OSError, AttributeError):
                break
            with self._lock:
                if self._stop.is_set():
                    client.close()
                    break
                self._clients.add(client)
                worker = threading.Thread(target=self._forward, args=(client, address), name="OpenNavicat-SSH-channel", daemon=True)
                self._workers.add(worker)
            worker.start()

    def _forward(self, client, address):
        channel = None
        try:
            channel = self.transport.open_channel("direct-tcpip", (self.remote_host, self.remote_port), address,
                                                  timeout=self.config.connect_timeout)
            with self._lock:
                self._channels.add(channel)
            while not self._stop.is_set():
                readable, _, _ = select.select([client, channel], [], [], .2)
                if client in readable:
                    data = client.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    client.sendall(data)
        except (OSError, EOFError, paramiko.SSHException, AttributeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                client.close()
            if channel is not None:
                with contextlib.suppress(Exception):
                    channel.close()
            with self._lock:
                self._clients.discard(client)
                self._channels.discard(channel)
                self._workers.discard(threading.current_thread())

    def close(self):
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()
        with self._lock:
            clients, channels, workers = list(self._clients), list(self._channels), list(self._workers)
        for client in clients:
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                client.close()
        for channel in channels:
            with contextlib.suppress(Exception):
                channel.close()
        if self.transport is not None:
            self.transport.close()
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
        deadline = time.monotonic() + 2
        for worker in [self._accept_thread, *workers]:
            if worker is not None and worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
        self.transport = self._socket = self._accept_thread = None
        self.local_port = 0

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
