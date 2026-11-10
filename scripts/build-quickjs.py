#!/usr/bin/env python3
"""Build the pinned official engine and controlled helper, without installing it.

Only macOS ARM64 has completed the prototype qualification. The core engine is
portable C; the current helper process/control layer deliberately remains POSIX.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d7215b0ad3fc2c562e8317fd9bca058fd4863804"
ARCHIVE_SHA256 = "a49defe70cbcae9226d9fac025af116e87ebae2440dcff1ca3c80b79e1cdef0b"
URL = f"https://codeload.github.com/quickjs-ng/quickjs/tar.gz/{REVISION}"


def build(output, archive=None):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The controlled helper has only qualified native macOS ARM64; six-platform glue is pending")
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
        with log.open("w") as stream:
            for command in (
                ["cmake", "-S", str(source), "-B", str(build_dir), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 "-DQJS_BUILD_LIBC=OFF", "-DQJS_ENABLE_INSTALL=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_OSX_ARCHITECTURES=arm64"],
                ["cmake", "--build", str(build_dir), "--target", "qjs", "-j4"],
                ["/usr/bin/clang", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(source),
                 str(ROOT / "native/quickjs/runner.c"), str(build_dir / "libqjs.a"), "-lm", "-lpthread", "-o", str(output / "opennavicat-js")],
            ):
                subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
        shutil.copyfile(source / "LICENSE", output / "LICENSE.quickjs-ng")
    binary = output / "opennavicat-js"
    receipt = dict(engine="QuickJS-NG", version="0.18.0", source_revision=REVISION,
        official_source=URL, archive_sha256=ARCHIVE_SHA256,
        helper_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), machine=platform.machine(),
        native_file=subprocess.check_output(["file", str(binary)], text=True).strip(),
        six_platform_qualified=False, helper_standard_library=False)
    (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/js-engine")
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    print(build(args.output_dir.resolve(), args.source_archive.resolve() if args.source_archive else None))
