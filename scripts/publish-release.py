"""Validate all six installed native packages before publishing a milestone."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "shubolab/opennavicat"
TARGETS = [(system, architecture) for system in ("macos", "windows", "linux")
           for architecture in ("amd64", "arm64")]


def asset_name(version, system, architecture):
    suffix = {"macos": ".dmg", "windows": "-setup.exe", "linux": ".deb"}[system]
    return f"OpenNavicat-{version}-{system}-{architecture}{suffix}"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_assets(directory, version):
    """Recheck downloaded bytes and native installation evidence, fail closed."""
    directory = Path(directory)
    expected = {asset_name(version, system, arch) for system, arch in TARGETS}
    actual = {path.name for path in directory.iterdir()
              if path.suffix in {".dmg", ".exe", ".deb"}}
    require(actual == expected, f"Installer set is incomplete or unexpected: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    manifests = []
    for system, arch in TARGETS:
        installer = directory / asset_name(version, system, arch)
        manifest = json.loads(installer.with_suffix(".manifest.json").read_text("utf-8"))
        require(manifest.get("name") == "OpenNavicat", "Unexpected application manifest")
        require((manifest.get("version"), manifest.get("os"), manifest.get("architecture")) == (version, system, arch), f"Wrong release identity: {installer.name}")
        require(manifest.get("native_host_architecture") == arch, f"Non-native build host: {installer.name}")
        require(manifest.get("installer") == installer.name, "Manifest points to a different asset")
        require(installer.stat().st_size > 0 and installer.stat().st_size == manifest.get("bytes"), f"Installer size mismatch: {installer.name}")
        require(digest(installer) == manifest.get("sha256"), f"Installer checksum mismatch: {installer.name}")
        payload = manifest.get("payload", {})
        binary_format = {"macos": "Mach-O", "windows": "PE", "linux": "ELF"}[system]
        main = payload.get("main", {})
        require(main.get("format") == binary_format and arch in main.get("architectures", []), f"Wrong executable architecture: {installer.name}")
        images = payload.get("native_images", [])
        require(bool(images) and len(images) == payload.get("native_image_count"), f"Missing native library inventory: {installer.name}")
        require(all(image.get("format") == binary_format and arch in image.get("architectures", []) for image in images), f"Non-native library payload: {installer.name}")
        verification = manifest.get("verification", {})
        require(verification.get("status") == "passed" and all(verification.get(field) is True for field in ("installed", "launched", "uninstalled")), f"Installation was not fully verified: {installer.name}")
        require(json.loads(installer.with_suffix(".installed-smoke.json").read_text("utf-8")) == verification, "Installation evidence does not match its manifest")
        smoke = verification.get("runtime", {})
        runtime = smoke.get("runtime", {})
        normalized = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(runtime.get("machine", "").lower())
        require(smoke.get("smoke") == "passed" and smoke.get("rows") == 6 and len(smoke.get("columns", [])) == 4, f"Installed SQL smoke did not pass: {installer.name}")
        require(runtime.get("version") == version and normalized == arch and runtime.get("os") == {"macos": "Darwin", "windows": "Windows", "linux": "Linux"}[system], f"Installed runtime is the wrong target: {installer.name}")
        require(isinstance(runtime.get("libpq"), int) and runtime["libpq"] >= 170000 and all(runtime.get(field) for field in ("qt", "psycopg", "pymysql", "paramiko", "cryptography")), f"Installed database/SSH dependencies are incomplete: {installer.name}")
        manifests.append(manifest)
    return manifests


def publish(tag, directory, verify_only=False):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    require(tag == f"v{version}" and re.fullmatch(r"v\d+\.\d+\.\d+", tag), "Tag must exactly match the project release version")
    manifests = validate_assets(directory, version)
    directory = Path(directory)
    installers = [directory / manifest["installer"] for manifest in manifests]
    checksums = directory / "SHA256SUMS"
    checksums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in sorted(installers)), "ascii")
    combined = directory / "build-manifests.json"
    combined.write_text(json.dumps({"version": version, "targets": manifests}, ensure_ascii=False, indent=2), "utf-8")
    if verify_only:
        return {"verified": len(manifests), "version": version}
    notes = ROOT / "docs" / "releases" / f"{tag}.md"
    require(notes.is_file(), f"Missing milestone release notes: {notes}")
    command = ["gh", "release"]
    existing = subprocess.run(command + ["view", tag, "--repo", REPOSITORY,
        "--json", "isDraft"], capture_output=True, text=True)
    if existing.returncode == 0:
        require(json.loads(existing.stdout)["isDraft"], "An already public release is immutable; publish a new version")
    else:
        subprocess.run(command + ["create", tag, "--repo", REPOSITORY,
            "--verify-tag", "--draft", "--prerelease", "--title", f"OpenNavicat {tag}",
            "--notes-file", str(notes)], check=True)
    # Upload into a draft only. A transient failure cannot expose a partial set.
    subprocess.run(command + ["upload", tag, "--repo", REPOSITORY, "--clobber",
        *map(str, installers), str(checksums), str(combined)], check=True)
    remote = subprocess.run(command + ["view", tag, "--repo", REPOSITORY,
        "--json", "assets"], check=True, capture_output=True, text=True)
    uploaded = {asset["name"]: asset["size"] for asset in json.loads(remote.stdout)["assets"]}
    for path in [*installers, checksums, combined]:
        require(uploaded.get(path.name) == path.stat().st_size, f"GitHub asset upload is incomplete: {path.name}")
    subprocess.run(command + ["edit", tag, "--repo", REPOSITORY, "--draft=false"], check=True)
    return {"published": tag, "url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args.tag, args.assets, args.verify_only)), flush=True)
