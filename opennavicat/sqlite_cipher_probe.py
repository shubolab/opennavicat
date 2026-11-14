"""Execute the bundled SQLite3MC provider against new, owned temporary files."""
from __future__ import annotations

import argparse
import hashlib
from importlib.machinery import EXTENSION_SUFFIXES
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sqlite3
import sys
import tempfile

from . import sqlite_cipher_provider as provider


SQLITE_CIPHER_RUNTIME_FLAGS = (
    "sqlite_cipher_native_extension_loaded", "sqlite_cipher_encrypted_header",
    "sqlite_cipher_two_rows", "sqlite_cipher_reopen_verified",
    "sqlite_cipher_wrong_key_rejected", "sqlite_cipher_stdlib_rejected",
    "sqlite_cipher_read_only_preserved", "sqlite_cipher_temp_cleaned",
    "sqlite_cipher_license_files_verified",
)
SQLITE_CIPHER_LICENSE_HASHES = {
    "APSW-SQLite3MC-3.54.0.0.txt": "b85a7dc6463f286c14a638070a14f4888564ac7245dc99a17950ec24cf1a7b9d",
    "SQLite3MC-2.6.1.txt": "39205ec18e0f25f56c62d3bda9768e7509b63a74ba58beb7442903dfc81c247a",
    "Argon2-and-Blake2.txt": "ac36638bcfcedb75441a5daeeaf4ef75b565911712583c272830e9fa7fddb590",
    "Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    "SQLite3MC-third-party.txt": "de41ae8638bfab8f3b49c0db8c8e577bd973c6c61c7289a161b7a20a6a11dd01",
}


def validate_sqlite_cipher_runtime(value):
    """Require the same strict receipt in packaged and installed release gates."""
    if (not isinstance(value, dict)
            or value.get("sqlite_cipher_provider") != provider.PROVIDER_VERSION
            or value.get("sqlite_cipher_mc_version") != provider.MC_VERSION
            or value.get("sqlite_cipher_sqlite_version") != "3.54.0"
            or not isinstance(value.get("sqlite_cipher_extension_sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", value["sqlite_cipher_extension_sha256"])
            or any(value.get(flag) is not True for flag in SQLITE_CIPHER_RUNTIME_FLAGS)):
        raise ValueError("Encrypted SQLite native execution or license verification is missing")


def _verify_license_files():
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS) / "licenses"
        files = {name: root / name for name in SQLITE_CIPHER_LICENSE_HASHES}
    else:
        root = Path(__file__).resolve().parents[1] / "licenses"
        files = {name: root / name for name in SQLITE_CIPHER_LICENSE_HASHES}
        if not root.is_dir():
            distribution = metadata.distribution("opennavicat")
            files = {Path(member).name: Path(distribution.locate_file(member))
                     for member in distribution.files or []
                     if Path(member).name in SQLITE_CIPHER_LICENSE_HASHES}
    for name, expected in SQLITE_CIPHER_LICENSE_HASHES.items():
        path = files.get(name)
        if path is None or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("The SQLite cipher runtime is missing a verified third-party license")


def probe_sqlite_cipher_runtime():
    """Fail on an absent/wrong provider; never skip or open an existing workspace."""
    module = provider._provider()
    extension = Path(getattr(module, "__file__", ""))
    if (not extension.is_file()
            or not any(str(extension).endswith(suffix) for suffix in EXTENSION_SUFFIXES)):
        raise RuntimeError("The SQLite cipher native extension did not load")
    _verify_license_files()
    expected = [(1, "Generated cipher example"), (2, "Generated second row")]
    observations = {
        "sqlite_cipher_native_extension_loaded": True,
        "sqlite_cipher_license_files_verified": True,
    }
    with tempfile.TemporaryDirectory(prefix="opennavicat-cipher-smoke-") as owned:
        path = Path(owned) / "generated-encrypted.sqlite"
        database = provider.connect(path, key="generated-cipher-smoke-key", cipher="aes256cbc", create=True)
        try:
            database.execute("CREATE TABLE generated_facts(id INTEGER PRIMARY KEY,label TEXT)").close()
            database.execute("BEGIN").close()
            database.executemany("INSERT INTO generated_facts VALUES (?,?)", expected).close()
            database.commit()
            observations["sqlite_cipher_two_rows"] = database.execute(
                "SELECT id,label FROM generated_facts ORDER BY id").fetchall() == expected
        finally:
            database.close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        observations["sqlite_cipher_encrypted_header"] = path.read_bytes()[:16] != b"SQLite format 3\0"
        reopened = provider.connect(path, key="generated-cipher-smoke-key", cipher="aes256cbc", read_only=True)
        try:
            observations["sqlite_cipher_reopen_verified"] = (
                reopened.execute("PRAGMA quick_check").fetchone() == ("ok",)
                and reopened.execute("SELECT id,label FROM generated_facts ORDER BY id").fetchall() == expected)
        finally:
            reopened.close()
        observations["sqlite_cipher_wrong_key_rejected"] = False
        try:
            wrong = provider.connect(path, key="generated-incorrect-smoke-key", cipher="aes256cbc", read_only=True)
        except sqlite3.DatabaseError:
            observations["sqlite_cipher_wrong_key_rejected"] = True
        else:
            wrong.close()
        ordinary = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        observations["sqlite_cipher_stdlib_rejected"] = False
        try:
            ordinary.execute("SELECT * FROM sqlite_schema").fetchall()
        except sqlite3.DatabaseError:
            observations["sqlite_cipher_stdlib_rejected"] = True
        finally:
            ordinary.close()
        observations["sqlite_cipher_read_only_preserved"] = hashlib.sha256(path.read_bytes()).hexdigest() == before
    observations["sqlite_cipher_temp_cleaned"] = not Path(owned).exists()
    if any(observations.get(name) is not True for name in SQLITE_CIPHER_RUNTIME_FLAGS):
        raise RuntimeError("The isolated encrypted SQLite runtime probe did not pass")
    result = {
        "sqlite_cipher_provider": provider.PROVIDER_VERSION,
        "sqlite_cipher_mc_version": module.mc_version,
        "sqlite_cipher_sqlite_version": module.sqlitelibversion(),
        "sqlite_cipher_extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
        **observations,
    }
    validate_sqlite_cipher_runtime(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"schema": 1, "os": platform.system(), "machine": platform.machine(),
              "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)),
              "runtime": probe_sqlite_cipher_runtime()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
