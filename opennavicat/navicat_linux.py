"""Conservative Linux Navicat paths and non-following file I/O.

Prefix inspection remains a separate metadata-only API. The native adapter uses
the same file/parent binding to project only allowlisted JSON metadata, without
decoding or retaining credential/opaque values. Neither API launches Navicat,
reads a keychain, modifies its source, or contacts a database.
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


def _read_checked(candidate, read):
    """Bind a bounded reader to a stable regular file and directory chain.

    Final and parent entries are rechecked after the callback; any failure
    discards its result and cannot become authoritative empty-source metadata.
    """
    path = candidate.path
    if (os.name != "posix" or os.open not in os.supports_dir_fd
            or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")):
        return "unavailable", None, None, None
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
        value = read(fd)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if _snapshot(opened) != _snapshot(after) or _snapshot(after) != _snapshot(current):
            raise ValueError("changed_source")
        for parent, part, identity in chain:
            current = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or _identity(current) != identity:
                raise ValueError("changed_source_directory")
        return "read", True, value, opened.st_size
    except FileNotFoundError:
        return "missing" if exists is None else "unreadable", False if exists is None else True, None, None
    except (OSError, ValueError, TypeError):
        return "unreadable", exists, None, None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def inspect_linux_source(candidate):
    """Classify only the container prefix through a race-checked descriptor."""
    state, exists, value, size = _read_checked(candidate, _container_prefix)
    prefix, consumed = value if state == "read" else ("unknown", 0)
    return LinuxSourceInspection(candidate, "requires-review" if state == "read" else state,
                                 exists, prefix, consumed, size)


def read_linux_projection(candidate, spec):
    """Read an allowlisted projection, with the same before/after file binding.

    The projected parser never materializes unselected values. This API does
    not accept a record schema or authorize a database connection by itself.
    Errors return a redacted state; no exception can retain private source text.
    """
    from .navicat_linux_json import project_json
    class Stream:
        def __init__(self, fd):
            self.fd = fd
        def read(self, size):
            return os.read(self.fd, size)
    return _read_checked(candidate, lambda fd: project_json(Stream(fd), spec, max_bytes=_MAX_SOURCE))


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
