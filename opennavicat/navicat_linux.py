"""Conservative Linux Navicat source discovery, without profile-value parsing.

Official paths and the shipped Linux18 ELF establish file candidates, not its
live authentication or record schema. This foundation checks only bounded file
metadata and a JSON container prefix. It does not read any connection value,
parse credentials, accept a native shape, launch Navicat, or contact a server.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import stat
import uuid

_MAX_SOURCE = 32 * 1024 * 1024
_MAX_PREFIX = 64 * 1024
_NAMESPACE = uuid.UUID("c0d14730-a7ed-57b0-bd59-7d8d4d9bcffb")
_FLATPAK_IDS = ("com.navicat.premium.en",)


@dataclass(frozen=True)
class LinuxSourceProblem:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class LinuxSourceCandidate:
    namespace: str
    path: Path = field(repr=False)

    @property
    def source_id(self):
        # Routing changes must not alter one exact logical file's identity.
        return "navicat-linux-source:" + str(uuid.uuid5(_NAMESPACE, str(self.path)))


@dataclass(frozen=True)
class LinuxSourceInspection:
    candidate: LinuxSourceCandidate
    state: str
    exists: bool | None
    container_prefix: str = "unknown"
    bytes_read: int = 0
    size: int | None = None


@dataclass(frozen=True)
class LinuxSourceReport:
    inspections: tuple[LinuxSourceInspection, ...] = ()
    issues: tuple[LinuxSourceProblem, ...] = ()

    @property
    def sources_found(self):
        return sum(item.exists is True for item in self.inspections)


def _absolute_local(value):
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("invalid_source_base")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or "\0" in str(path)
            or str(path).startswith(("//", "\\\\"))):
        raise ValueError("invalid_source_base")
    return path


def linux_source_candidates(*, home=None, environ=None):
    """Return only explicit official native/XDG and one known Flatpak app ID.

    No directory is enumerated. Unknown language/product app IDs, guessed Wine
    prefixes, logs, ui_connections.json and configurable ProfilesPath guesses
    are deliberately outside this route. Paths are never resolved via links.
    """
    issues = []
    try:
        base = _absolute_local(Path.home() if home is None else home)
    except (TypeError, ValueError):
        return (), (LinuxSourceProblem("linux_config_location_review", "The Linux home/config base is not an accepted absolute local path."),)
    roots = [("native-default", base / ".config")]
    setting = (os.environ if environ is None else environ).get("XDG_CONFIG_HOME")
    if setting not in (None, ""):
        try:
            roots.append(("native-xdg", _absolute_local(setting)))
        except (TypeError, ValueError):
            issues.append(LinuxSourceProblem("linux_config_location_review", "The configured XDG base is not an accepted absolute local path."))
    roots.extend(("flatpak:" + app_id, base / ".var/app" / app_id / "config") for app_id in _FLATPAK_IDS)
    result, seen = [], set()
    for namespace, root in roots:
        path = root / "navicat/Common/connections.json"
        if path not in seen:
            result.append(LinuxSourceCandidate(namespace, path))
            seen.add(path)
    return tuple(result), tuple(issues)


def _identity(info):
    return info.st_dev, info.st_ino


def _snapshot(info):
    return _identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _container_prefix(fd):
    # Read no JSON key or value. Even a malformed root string is not decoded.
    consumed = 0
    first = os.read(fd, 1)
    consumed += len(first)
    if first == b"\xef":
        rest = os.read(fd, 2)
        consumed += len(rest)
        if rest != b"\xbb\xbf":
            return "other", consumed
        first = os.read(fd, 1)
        consumed += len(first)
    while first in (b" ", b"\t", b"\r", b"\n"):
        if consumed >= _MAX_PREFIX:
            raise ValueError("prefix_bound")
        first = os.read(fd, 1)
        consumed += len(first)
    return {b"{": "object", b"[": "array", b"": "empty"}.get(first, "other"), consumed


def inspect_linux_source(candidate):
    """Probe a regular file through non-following directory descriptors.

    Prefix classification is not JSON validation or schema acceptance. Final
    and parent entries are rechecked; races/failures never become empty source
    authority. No database, credential, secret-value or JSON parser API runs.
    """
    path = candidate.path
    if (os.name != "posix" or os.open not in os.supports_dir_fd
            or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")):
        return LinuxSourceInspection(candidate, "unavailable", None)
    descriptors, chain = [], []
    exists = None
    try:
        _absolute_local(path)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        directory = os.open(path.anchor, flags | os.O_DIRECTORY)
        descriptors.append(directory)
        for name in path.parts[1:-1]:
            child = os.open(name, flags | os.O_DIRECTORY, dir_fd=directory)
            descriptors.append(child)
            chain.append((directory, name, _identity(os.fstat(child))))
            directory = child
        name = path.name
        before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        exists = True
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("non_regular_source")
        fd = os.open(name, flags | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        descriptors.append(fd)
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or _snapshot(opened) != _snapshot(before)
                or not 0 <= opened.st_size <= _MAX_SOURCE):
            raise ValueError("unsafe_or_changed_source")
        prefix, consumed = _container_prefix(fd)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if _snapshot(opened) != _snapshot(after) or _snapshot(after) != _snapshot(current):
            raise ValueError("changed_source")
        for parent, part, identity in chain:
            current = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or _identity(current) != identity:
                raise ValueError("changed_source_directory")
        return LinuxSourceInspection(candidate, "requires-review", True, prefix, consumed, opened.st_size)
    except FileNotFoundError:
        return LinuxSourceInspection(candidate, "missing" if exists is None else "unreadable", False if exists is None else True)
    except (OSError, ValueError, TypeError):
        return LinuxSourceInspection(candidate, "unreadable", exists)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def discover_linux_sources(*, home=None, environ=None):
    candidates, problems = linux_source_candidates(home=home, environ=environ)
    inspections, issues = [], list(problems)
    for candidate in candidates:
        result = inspect_linux_source(candidate)
        inspections.append(result)
        if result.state in {"unreadable", "unavailable"}:
            issues.append(LinuxSourceProblem("source_read_failed", "A Linux Navicat source could not be inspected safely; existing metadata must be preserved."))
        elif result.state == "requires-review":
            issues.append(LinuxSourceProblem("linux_native_format_review", "A Linux Navicat source is present; its native record and authentication format requires controlled verification before migration."))
    return LinuxSourceReport(tuple(inspections), tuple(issues))
