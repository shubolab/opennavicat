"""Bounded Windows registry metadata, without secret-value enumeration.

The Servers families are declared by the official Windows18 shipped artifact.
Their live format has not been accepted: consumers MUST retain the review gate.
Only current-user keys are opened, with read/enumeration rights. No Navicat
process, database, credential store, arbitrary blob or recursive file scan runs.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
import hashlib
import os
import struct


FAMILIES = (
    ("Navicat", "mysql"), ("NavicatPG", "postgresql"), ("NavicatORA", "oracle"),
    ("NavicatSQLITE", "sqlite"), ("NavicatMSSQL", "sqlserver"),
    ("NavicatMARIADB", "mariadb"), ("NavicatMONGODB", "mongodb"),
    ("NavicatREDIS", "redis"), ("NavicatSnowflake", "snowflake"),
    ("NavicatDameng", "dameng"),
)
PREMIUMSOFT = r"Software\PremiumSoft"
REG_SZ, REG_DWORD, REG_QWORD = 1, 4, 11
# Candidate plain metadata literals, not accepted Windows authentication maps.
# Security enums, password flags, SSH/TLS material and serverSettings are absent.
PROFILE_FIELDS = frozenset({"host", "port", "username", "database", "defaultdatabase", "databasefile", "dbfilename"})
LOCATION_FIELDS = frozenset({"profilespath"})
_QUERY_FIELDS = PROFILE_FIELDS | LOCATION_FIELDS
_MAX_VALUE_BYTES = 64 * 1024
_MAX_FIELDS = 2048
_MAX_PROFILES = 100_000
_MAX_RECORD_METADATA = 1024 * 1024
_MAX_TOTAL_METADATA = 32 * 1024 * 1024


@dataclass(frozen=True)
class ValueInfo:
    kind: int
    size: int


@dataclass(frozen=True)
class RegistryProblem:
    code: str
    message: str
    blocking: bool = False


@dataclass(frozen=True)
class WindowsRegistryEntry:
    driver: str
    name: str = field(repr=False)
    source_key: str = field(repr=False)
    values: dict[str, object] = field(default_factory=dict, repr=False)
    value_types: tuple[tuple[str, int, int], ...] = field(default=(), repr=False)
    last_write: int = field(default=0, repr=False)
    view: int = 64
    errors: tuple[str, ...] = ()
    physical_id: str = field(default="", repr=False)
    # Only views observed to share this physical key and the same snapshot.
    alias_views: tuple[int, ...] = ()


@dataclass(frozen=True)
class WindowsRegistrySnapshot:
    entries: tuple[WindowsRegistryEntry, ...] = ()
    issues: tuple[RegistryProblem, ...] = ()
    sources_found: int = 0
    profiles_location: Path | None = field(default=None, repr=False)


class NativeRegistry:
    """OS-native read-only backend. The root override is for owned test keys.

    EnumValue is deliberately not used: winreg.EnumValue reads value data.
    RegEnumValueW receives a NULL data pointer, returning only names/types/sizes.
    The separate bounded QueryValueExW operation enforces its own whitelist.
    """
    def __init__(self, root=PREMIUMSOFT):
        if os.name != "nt":
            raise OSError("Windows registry is unavailable on this platform")
        import ctypes
        from ctypes import wintypes
        import winreg
        self.root = root
        self.winreg, self.ctypes, self.types = winreg, ctypes, wintypes
        library = ctypes.WinDLL("advapi32", use_last_error=True)
        self.enumerate_value = library.RegEnumValueW
        self.enumerate_value.argtypes = [wintypes.HKEY, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        self.enumerate_value.restype = wintypes.LONG
        self.query_value = library.RegQueryValueExW
        self.query_value.argtypes = [wintypes.HKEY, wintypes.LPCWSTR, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        self.query_value.restype = wintypes.LONG
        self.query_key_name = None
        try:
            query = ctypes.WinDLL("ntdll").NtQueryKey
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                              wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
            query.restype = wintypes.LONG
            self.query_key_name = query
        except (AttributeError, OSError):
            pass  # Without physical identity, preserve both view records.

    @contextmanager
    def open(self, relative, view):
        flags = self.winreg.KEY_QUERY_VALUE | self.winreg.KEY_ENUMERATE_SUB_KEYS
        flags |= self.winreg.KEY_WOW64_64KEY if view == 64 else self.winreg.KEY_WOW64_32KEY
        try:
            handle = self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.root + "\\" + relative, 0, flags)
        except FileNotFoundError:
            yield None
            return
        with handle:
            yield _NativeKey(self, handle)


class _NativeKey:
    def __init__(self, registry, handle):
        self.registry, self.handle = registry, handle

    def stamp(self):
        return self.registry.winreg.QueryInfoKey(self.handle)[2]

    def identity(self):
        """Hash native key-name metadata, not any registry value or user SID log.

        NtQueryKey(KeyNameInformation) is the documented user-mode counterpart
        of ZwQueryKey. An unavailable/failing call never authorizes aliasing.
        """
        query = self.registry.query_key_name
        if query is None:
            return ""
        c, t = self.registry.ctypes, self.registry.types
        data, size = c.create_string_buffer(65536), t.ULONG()
        status = query(int(self.handle), 3, data, len(data), c.byref(size))
        if status != 0 or not 4 <= size.value <= len(data):
            return ""
        length = struct.unpack("<I", data.raw[:4])[0]
        if not length or length % 2 or length > size.value - 4:
            return ""
        return hashlib.sha256(data.raw[4:4 + length]).hexdigest()

    def subkeys(self):
        for index in range(_MAX_PROFILES + 1):
            try:
                yield self.registry.winreg.EnumKey(self.handle, index)
            except OSError as error:
                if error.winerror == 259:
                    return
                raise
        raise ValueError("Registry profile count exceeds the discovery bound")

    def value_info(self):
        c, t = self.registry.ctypes, self.registry.types
        result, names_size = {}, 0
        for index in range(_MAX_FIELDS + 1):
            capacity = 256
            while True:
                name = c.create_unicode_buffer(capacity)
                length, kind, size = t.DWORD(capacity), t.DWORD(), t.DWORD()
                status = self.registry.enumerate_value(int(self.handle), index, name,
                    c.byref(length), None, c.byref(kind), None, c.byref(size))
                if status == 259:
                    return result
                if status == 234 and capacity < 16384:
                    capacity *= 2
                    continue
                if status:
                    raise OSError(status, "Registry field metadata enumeration failed")
                names_size += len(name.value.encode("utf-16-le"))
                if names_size > _MAX_RECORD_METADATA:
                    raise ValueError("Registry field metadata exceeds the discovery bound")
                result[name.value] = ValueInfo(kind.value, size.value)
                break
        raise ValueError("Registry field count exceeds the discovery bound")

    def query(self, name, expected_kind):
        if not name.isascii() or name.casefold() not in _QUERY_FIELDS or expected_kind not in {REG_SZ, REG_DWORD, REG_QWORD}:
            raise ValueError("Registry value is not on the plain metadata allowlist")
        c, t = self.registry.ctypes, self.registry.types
        data = c.create_string_buffer(_MAX_VALUE_BYTES)
        size, kind = t.DWORD(len(data)), t.DWORD()
        status = self.registry.query_value(int(self.handle), name, None, c.byref(kind), data, c.byref(size))
        if status or kind.value != expected_kind:
            raise OSError(status, "Registry metadata changed or cannot be read within bounds")
        raw = data.raw[:size.value]
        if kind.value == REG_SZ:
            value = raw.decode("utf-16-le").removesuffix("\0")
            if "\0" in value:
                raise ValueError("Registry text metadata contains an embedded terminator")
            return value
        if kind.value == REG_DWORD and len(raw) == 4:
            return struct.unpack("<I", raw)[0]
        if kind.value == REG_QWORD and len(raw) == 8:
            return struct.unpack("<Q", raw)[0]
        raise ValueError("Registry metadata has an invalid size")


def _plain_values(key, allowed):
    before = key.stamp()
    info = key.value_info()
    if len(info) > _MAX_FIELDS:
        raise ValueError("Registry field count exceeds the discovery bound")
    values, errors = {}, []
    if sum(len(name.encode("utf-16-le")) for name in info) > _MAX_RECORD_METADATA:
        raise ValueError("Registry field metadata exceeds the discovery bound")
    for name, description in info.items():
        if not name.isascii():
            continue
        normalized = name.casefold()
        if normalized not in allowed:
            continue
        if (description.kind not in {REG_SZ, REG_DWORD, REG_QWORD}
                or not 0 <= description.size <= _MAX_VALUE_BYTES):
            errors.append(normalized)
            continue
        try:
            values[normalized] = key.query(name, description.kind)
        except Exception:
            errors.append(normalized)
    if key.stamp() != before:
        errors.append("registry_snapshot_changed")
    signature = tuple(sorted((name, description.kind, description.size) for name, description in info.items()))
    return values, signature, before, tuple(sorted(set(errors)))


def read_windows_navicat_registry(registry=None) -> WindowsRegistrySnapshot:
    """Enumerate only known current-user families; preserve failures as review.

    An injected read-only backend supports cross-platform synthetic tests. Its
    interface is intentionally metadata-only; it never receives a secret name
    through query(). Native acceptance of Navicat18's values remains pending.
    """
    if registry is None:
        try:
            registry = NativeRegistry()
        except OSError:
            return WindowsRegistrySnapshot(issues=(RegistryProblem("windows_discovery_pending",
                "Native Windows registry metadata is unavailable on this platform."),))
    entries, issues, sources, locations = [], [], set(), []
    metadata_bytes = 0
    for view in (64, 32):
        for family, driver in FAMILIES:
            relative = family + r"\Servers"
            try:
                with registry.open(relative, view) as servers:
                    if servers is None:
                        continue
                    sources.add(family)
                    for index, name in enumerate(servers.subkeys()):
                        if index >= _MAX_PROFILES or len(entries) >= 2 * _MAX_PROFILES:
                            raise ValueError("Registry profile count exceeds the discovery bound")
                        source = registry.root + "\\" + relative + "\\" + name
                        values, signature, stamp, errors, physical_id = {}, (), 0, (), ""
                        try:
                            with registry.open(relative + "\\" + name, view) as key:
                                if key is None:
                                    raise OSError("Registry profile disappeared during discovery")
                                if metadata_bytes > _MAX_TOTAL_METADATA:
                                    raise ValueError("Registry metadata exceeds the discovery bound")
                                values, signature, stamp, errors = _plain_values(key, PROFILE_FIELDS)
                                physical_id = key.identity()
                                metadata_bytes += sum(len(name.encode("utf-16-le")) for name, _kind, _size in signature)
                                metadata_bytes += sum(len(value.encode("utf-16-le")) if isinstance(value, str) else 8 for value in values.values())
                        except Exception:
                            errors = ("registry_record_unavailable",)
                        if errors:
                            issues.append(RegistryProblem("registry_read_failed",
                                "Some registry metadata could not be read consistently; existing profiles must be preserved.", True))
                        entries.append(WindowsRegistryEntry(driver, name, source, values, signature, stamp, view, errors, physical_id))
            except Exception:
                issues.append(RegistryProblem("registry_read_failed",
                    "A registry connection family could not be enumerated safely; existing profiles must be preserved.", True))
        # Only this explicit plain option is recognized, not arbitrary blobs,
        # guessed default directories or a recursive user-profile search.
        try:
            with registry.open("NavicatPremium", view) as options:
                if options is not None:
                    values, signature, _stamp, errors = _plain_values(options, LOCATION_FIELDS)
                    value = values.get("profilespath")
                    kinds = {name.casefold(): kind for name, kind, _size in signature}
                    if isinstance(value, str) and value and kinds.get("profilespath") == REG_SZ and not errors:
                        path = Path(value)
                        if path.is_absolute() and not value.startswith(("\\\\", "//")):
                            locations.append(path)
                        else:
                            issues.append(RegistryProblem("windows_groups_location_review", "The configured Profiles Location is not a plain local absolute directory."))
                    elif value is not None or errors:
                        issues.append(RegistryProblem("windows_groups_location_review", "The configured Profiles Location has an unrecognized type or changed during discovery."))
        except Exception:
            issues.append(RegistryProblem("windows_groups_location_review", "The configured Profiles Location could not be read safely."))

    by_key = defaultdict(list)
    for entry in entries:
        by_key[(entry.driver, entry.source_key)].append(entry)
    retained = []
    for group in by_key.values():
        first = group[0]
        # No opaque values are compared. Their field types/sizes and the native
        # key timestamp participate, so a changed source cannot be called equal.
        equal = bool(first.physical_id) and all(row.physical_id == first.physical_id and
                    (row.values, row.value_types, row.last_write, row.errors) ==
                    (first.values, first.value_types, first.last_write, first.errors) for row in group)
        if len(group) == 1 or equal:
            retained.append(replace(first, alias_views=tuple(sorted({row.view for row in group}))))
        else:
            issues.append(RegistryProblem("windows_registry_view_conflict", "Registry views disagree; all versions are retained for review.", True))
            retained.extend(replace(row, errors=tuple(sorted(set(row.errors) | {"windows_registry_view_conflict"}))) for row in group)
    unique_locations = list(dict.fromkeys(locations))
    location = unique_locations[0] if len(unique_locations) == 1 else None
    if len(unique_locations) > 1:
        issues.append(RegistryProblem("windows_groups_location_review", "Registry views disagree about Profiles Location; groups were not read."))
    elif retained and location is None:
        issues.append(RegistryProblem("windows_groups_location_unavailable", "No recognized configured Profiles Location was found; connections remain ungrouped."))
    return WindowsRegistrySnapshot(tuple(retained), tuple(issues), len(sources), location)
