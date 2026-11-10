#!/usr/bin/env python3
"""Build the pinned official engine and controlled helper, without installing it.

Initial local qualification is macOS ARM64. The portable C/process layer now has
Windows/POSIX branches; other targets explicitly opt into independent native CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import struct
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d7215b0ad3fc2c562e8317fd9bca058fd4863804"
ARCHIVE_SHA256 = "a49defe70cbcae9226d9fac025af116e87ebae2440dcff1ca3c80b79e1cdef0b"
URL = f"https://codeload.github.com/quickjs-ng/quickjs/tar.gz/{REVISION}"


def machine(value):
    return {"arm64":"arm64","aarch64":"arm64","x86_64":"amd64","amd64":"amd64"}.get(value.lower())


def binary_architecture(binary):
    data=binary.read_bytes()
    if data[:2]==b"MZ":
        offset=struct.unpack_from("<I",data,0x3c)[0]
        if data[offset:offset+4]!=b"PE\0\0":raise RuntimeError("Invalid PE header")
        architecture={0x8664:"amd64",0xaa64:"arm64"}.get(struct.unpack_from("<H",data,offset+4)[0])
        kind="PE"
    elif data[:4]==b"\x7fELF":
        if data[4:6]!=b"\x02\x01":raise RuntimeError("Expected native 64-bit little-endian ELF")
        architecture={62:"amd64",183:"arm64"}.get(struct.unpack_from("<H",data,18)[0]);kind="ELF"
    elif data[:4]==b"\xcf\xfa\xed\xfe":
        architecture={0x1000007:"amd64",0x100000c:"arm64"}.get(struct.unpack_from("<I",data,4)[0]);kind="Mach-O"
    else:raise RuntimeError("Unrecognized controlled-helper native image")
    if not architecture:raise RuntimeError("Unsupported controlled-helper architecture")
    return {"format":kind,"machine":architecture}


def build(output, archive=None, allow_unqualified=False):
    if (platform.system() != "Darwin" or platform.machine() != "arm64") and not allow_unqualified:
        raise RuntimeError("The controlled helper has only qualified native macOS ARM64; six-platform glue is pending")
    if platform.system()not in {"Darwin","Windows","Linux"}or machine(platform.machine())is None:
        raise RuntimeError("Unsupported native qualification target")
    output.mkdir(parents=True, exist_ok=True)
    if archive is None:
        archive = output / "quickjs-ng-v0.18.0.tar.gz"
        if not archive.exists():
            with urllib.request.urlopen(URL, timeout=30) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise RuntimeError("Official source archive exceeded its limit")
            if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
                raise RuntimeError("Official source archive checksum mismatch")
            archive.write_bytes(data)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("Official source archive checksum mismatch")
    # Always use a fresh extraction; a previously edited checkout must never be
    # accepted just because an unrelated archive beside it has the right hash.
    with tempfile.TemporaryDirectory(prefix="quickjs-source-", dir=output) as temporary:
        source_root = Path(temporary)
        with tarfile.open(archive) as bundle:
            bundle.extractall(source_root, filter="data")
        source = source_root / ("quickjs-" + REVISION)
        build_dir = source_root / "build"
        log = output / "build.log"
        configure=["cmake","-S",str(ROOT/"native/quickjs"),"-B",str(build_dir),
            "-DQUICKJS_SOURCE_DIR="+str(source),"-DCMAKE_BUILD_TYPE=Release"]
        if platform.system()=="Windows":
            configure += ["-G","Visual Studio 18 2026" if machine(platform.machine())=="arm64" else "Visual Studio 17 2022",
                "-A","ARM64" if machine(platform.machine())=="arm64" else "x64"]
        else:
            configure += ["-G","Ninja"]
            if platform.system()=="Darwin":configure += ["-DCMAKE_OSX_ARCHITECTURES="+platform.machine()]
        with log.open("w") as stream:
            for command in (
                configure,
                ["cmake","--build",str(build_dir),"--target","opennavicat-js","--config","Release","-j4"],
            ):
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
        name="opennavicat-js.exe" if platform.system()=="Windows"else"opennavicat-js"
        binary=build_dir/("Release"if platform.system()=="Windows"else"")/name
        shutil.copyfile(binary,output/name);shutil.copymode(binary,output/name)
        shutil.copyfile(source / "LICENSE", output / "LICENSE.quickjs-ng")
    binary = output / name
    native=binary_architecture(binary)
    if native["machine"]!=machine(platform.machine()):raise RuntimeError("Helper image does not match the actual runner architecture")
    receipt = dict(engine="QuickJS-NG", version="0.18.0", source_revision=REVISION,
        official_source=URL, archive_sha256=ARCHIVE_SHA256,
        helper_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), machine=platform.machine(),
        native_image=native, operating_system=platform.system(),
        runner_source_sha256=hashlib.sha256((ROOT/"native/quickjs/runner.c").read_bytes()).hexdigest(),
        qualification="prototype-native-helper",six_platform_qualified=False, helper_standard_library=False)
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/js-engine")
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--allow-unqualified-platform",action="store_true",help="Opt into this development branch's native qualification; not release acceptance")
    args = parser.parse_args()
    print(build(args.output_dir.resolve(),args.source_archive.resolve()if args.source_archive else None,args.allow_unqualified_platform))
