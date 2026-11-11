#!/usr/bin/env python3
"""Install mandatory Ubuntu CI libraries, with a scoped HTTPS mirror fallback.

The runner's mirror+http Azure source can stall before Qt libraries are present.
Keep its cached-index fast path, but never continue to tests with missing packages.
Fallback sources and indexes are temporary; system apt configuration is untouched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


COMMON = ("libegl1", "libopengl0", "libxcb-cursor0", "libxkbcommon-x11-0")
APT_OPTIONS = ("-o", "Acquire::Retries=2", "-o", "Acquire::http::Timeout=20",
               "-o", "Acquire::https::Timeout=20", "-o", "Dpkg::Lock::Timeout=60")


def ubuntu_codename(path=Path("/etc/os-release")):
    release = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"\'')
    codename = release.get("VERSION_CODENAME", "")
    if release.get("ID") != "ubuntu" or not re.fullmatch(r"[a-z][a-z0-9]*", codename):
        raise RuntimeError("This CI dependency installer requires an identified Ubuntu release")
    return codename


def packages_for(profile, codename):
    glib = "libglib2.0-0" if codename == "jammy" else "libglib2.0-0t64"
    if profile == "desktop":
        return COMMON + ("libxcb-icccm4", "libxcb-keysyms1", "libxcb-shape0", glib,
                         "fonts-noto-cjk", "xvfb", "xauth")
    if profile == "database-security":
        return COMMON + (glib, "redis-server")
    raise ValueError("Unknown dependency profile")


def missing_packages(packages):
    missing = []
    for package in packages:
        status = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package],
                                capture_output=True, text=True, check=False)
        if status.returncode or status.stdout.strip() != "install ok installed":
            missing.append(package)
    return missing


def apt(arguments, seconds, source_options=()):
    # GNU timeout signals the whole process group and escalates after ten seconds,
    # so timing out sudo's caller cannot leave apt running and holding its lock.
    command = ["sudo", "timeout", "--kill-after=10s", f"{seconds}s", "env",
               "DEBIAN_FRONTEND=noninteractive", "apt-get", *APT_OPTIONS,
               *source_options, *arguments]
    return subprocess.run(command, check=False).returncode == 0


def official_sources(codename, architecture):
    signed = "[signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg]"
    components = "main restricted universe multiverse"
    if architecture == "amd64":
        suites = [("https://archive.ubuntu.com/ubuntu", codename),
                  ("https://archive.ubuntu.com/ubuntu", codename + "-updates"),
                  ("https://security.ubuntu.com/ubuntu", codename + "-security")]
    elif architecture == "arm64":
        suites = [("https://ports.ubuntu.com/ubuntu-ports", codename + suffix)
                  for suffix in ("", "-updates", "-security")]
    else:
        raise RuntimeError(f"Unsupported native Ubuntu architecture: {architecture}")
    return "".join(f"deb {signed} {uri} {suite} {components}\n" for uri, suite in suites)


def install(profile):
    codename = ubuntu_codename()
    packages = packages_for(profile, codename)
    missing = missing_packages(packages)
    if not missing:
        print("All mandatory Linux dependencies are already installed", flush=True)
        return
    print("Installing mandatory Linux dependencies: " + ", ".join(missing), flush=True)
    if apt(["install", "-y", "--no-install-recommends", *missing], 90):
        missing = missing_packages(packages)
        if not missing:
            return
    print("Runner mirror failed or left packages missing; using official Ubuntu HTTPS sources", flush=True)
    architecture = subprocess.run(["dpkg", "--print-architecture"], check=True,
                                  capture_output=True, text=True).stdout.strip()
    sources = official_sources(codename, architecture)
    with tempfile.TemporaryDirectory(prefix="opennavicat-apt-") as directory:
        root = Path(directory)
        # apt's _apt sandbox needs to traverse these generated public files.
        root.chmod(0o755)
        source_file = root / "ubuntu.list"
        source_file.write_text(sources)
        source_file.chmod(0o644)
        lists = root / "lists"
        lists.mkdir(mode=0o755)
        options = ("-o", f"Dir::Etc::sourcelist={source_file}",
                   "-o", "Dir::Etc::sourceparts=-", "-o", f"Dir::State::lists={lists}")
        # Error-Mode=any rejects partial/stale updates rather than treating warnings
        # as a usable new index. No external runner repositories are contacted.
        try:
            if not apt(["-o", "APT::Update::Error-Mode=any", "update"], 180, options):
                raise RuntimeError("Official Ubuntu HTTPS package-index refresh failed")
            if not apt(["install", "-y", "--no-install-recommends", *missing], 240, options):
                raise RuntimeError("Mandatory Linux dependency installation failed")
        finally:
            # apt creates root/_apt-owned index directories. Return only this
            # generated tree to the caller so TemporaryDirectory can remove it.
            subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}",
                            str(root)], check=True)
    missing = missing_packages(packages)
    if missing:
        raise RuntimeError("Mandatory Linux dependencies are still missing: " + ", ".join(missing))
    print("All mandatory Linux dependencies are installed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("desktop", "database-security"), required=True)
    arguments = parser.parse_args()
    try:
        install(arguments.profile)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Linux dependency setup failed: {error}\n")


if __name__ == "__main__":
    main()
