#!/usr/bin/env python3
"""Install mandatory Ubuntu CI libraries, with a scoped HTTPS mirror fallback.

The runner's mirror+http Azure source can stall before Qt libraries are present.
Keep its cached-index fast path, but never continue to tests with missing packages.
Fallback sources and indexes are temporary; system apt configuration is untouched.
Official Ubuntu HTTPS is tried before TUNA, whose transport was probed on each
native CI target. Both use the preinstalled Ubuntu archive signing key.
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
               "-o", "Acquire::https::Timeout=20", "-o", "Dpkg::Lock::Timeout=60",
               "-o", "APT::Get::AllowUnauthenticated=false",
               "-o", "Acquire::AllowInsecureRepositories=false",
               "-o", "Acquire::AllowDowngradeToInsecureRepositories=false",
               "-o", "Acquire::https::Verify-Peer=true",
               "-o", "Acquire::https::Verify-Host=true")


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
        # Preinstall every DEB Depends group, including libraries already on
        # most hosted images. Native installer verification must not discover
        # a missing library and fetch it through the runner's default mirror.
        # tests/test_linux_dependencies.py compares this with debian_control.
        return COMMON + (
            "libc6", "libstdc++6", "libgcc-s1", "libgl1", glib,
            "libdbus-1-3", "libxkbcommon0", "libxcb1", "libx11-xcb1",
            "libxcb-icccm4", "libxcb-keysyms1", "libxcb-image0", "libxcb-render-util0",
            "libxcb-shape0", "libxcb-randr0", "libxcb-xfixes0", "libxcb-sync1",
            "libxcb-xinerama0", "libxcb-util1", "libxcb-xkb1", "libxcb-glx0",
            "libfontconfig1", "libfreetype6", "fonts-noto-cjk", "xvfb", "xauth",
        )
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


def tuna_sources(codename, architecture):
    if architecture not in {"amd64", "arm64"}:
        raise RuntimeError(f"Unsupported native Ubuntu architecture: {architecture}")
    archive = "ubuntu" if architecture == "amd64" else "ubuntu-ports"
    return "".join(
        "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
        f"https://mirrors.tuna.tsinghua.edu.cn/{archive} {codename}{suffix} "
        "main restricted universe multiverse\n"
        for suffix in ("", "-updates", "-security")
    )


def install_from_sources(packages, sources):
    """Return a failed-attempt reason; never reuse another provider's indexes."""
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
                return "package-index refresh failed"
            missing = missing_packages(packages)
            if not missing:
                return None
            if not apt(["install", "-y", "--no-install-recommends", *missing], 240, options):
                return "dependency installation failed"
            missing = missing_packages(packages)
            if missing:
                return "dependencies are still missing: " + ", ".join(missing)
            return None
        finally:
            # apt creates root/_apt-owned index directories. Return only this
            # generated tree to the caller so TemporaryDirectory can remove it.
            subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}",
                            str(root)], check=True)


def install(profile):
    codename = ubuntu_codename()
    packages = packages_for(profile, codename)
    missing = missing_packages(packages)
    if not missing:
        print("All mandatory Linux dependencies are already installed", flush=True)
        return
    print("Installing mandatory Linux dependencies: " + ", ".join(missing), flush=True)
    if apt(["install", "-y", "--no-install-recommends", *missing], 90):
        if not missing_packages(packages):
            return
    architecture = subprocess.run(["dpkg", "--print-architecture"], check=True,
                                  capture_output=True, text=True).stdout.strip()
    attempts = (
        ("Official Ubuntu HTTPS", official_sources(codename, architecture)),
        ("TUNA HTTPS with Ubuntu archive signatures", tuna_sources(codename, architecture)),
    )
    failures = []
    for label, sources in attempts:
        print("Trying " + label + " in isolated temporary apt sources/indexes", flush=True)
        reason = install_from_sources(packages, sources)
        if reason is None:
            print("All mandatory Linux dependencies are installed", flush=True)
            return
        failures.append(label + ": " + reason)
        print(failures[-1], flush=True)
    raise RuntimeError("All signed Ubuntu HTTPS attempts failed; " + "; ".join(failures))


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
