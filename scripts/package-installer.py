"""Create and optionally install-test a native Windows, macOS or Linux installer.

Run on the target operating system and CPU after scripts/build.py. This script
does not cross-compile or relabel an emulated application as a native build.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tomllib


ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "packaging"
CPU_TYPES = {0x8664: "amd64", 0xAA64: "arm64", 0x014C: "x86",
             62: "amd64", 183: "arm64", 3: "x86", 40: "arm",
             0x01000007: "amd64", 0x0100000C: "arm64", 7: "x86", 12: "arm"}


class PackagingError(RuntimeError):
    pass


def normalize_arch(value: str) -> str:
    value = value.lower().replace("-", "_")
    if value in {"amd64", "x86_64", "x64"}:
        return "amd64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    raise PackagingError(f"Unsupported native CPU architecture: {value}")


def host_os() -> str:
    try:
        return {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    except KeyError as exc:
        raise PackagingError(f"Unsupported installer host: {sys.platform}") from exc


def host_arch() -> str:
    if sys.platform == "win32":
        import ctypes
        process_machine = ctypes.c_ushort()
        native_machine = ctypes.c_ushort()
        kernel = ctypes.windll.kernel32
        if hasattr(kernel, "IsWow64Process2"):
            kernel.IsWow64Process2.argtypes = [ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
            kernel.IsWow64Process2.restype = ctypes.c_bool
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            if not kernel.IsWow64Process2(kernel.GetCurrentProcess(),
                    ctypes.byref(process_machine), ctypes.byref(native_machine)):
                raise PackagingError("Windows could not report its native CPU architecture")
            architecture = CPU_TYPES.get(native_machine.value)
            if architecture in {"amd64", "arm64"}:
                return architecture
            raise PackagingError(f"Unsupported native Windows machine: {native_machine.value:#x}")
    if sys.platform == "darwin":
        translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
            capture_output=True, text=True, check=False)
        if translated.returncode == 0 and translated.stdout.strip() == "1":
            raise PackagingError("Run the native Python build on macOS; a Rosetta build cannot produce this native release")
    return normalize_arch(platform.machine())


def validate_version(value: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9][A-Za-z0-9._+-]*)?", value):
        raise PackagingError(f"Unsafe or unsupported release version: {value!r}")
    return value


def binary_identity(path: Path) -> dict | None:
    """Read actual PE/ELF/Mach-O CPU headers without executing the artifact."""
    with path.open("rb") as handle:
        data = handle.read(64)
        if data[:2] == b"MZ":
            if len(data) < 64:
                raise PackagingError(f"Truncated PE DOS header: {path}")
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if offset < 64 or offset > path.stat().st_size - 6:
                raise PackagingError(f"Invalid PE header offset: {path}")
            handle.seek(offset)
            header = handle.read(6)
            if header[:4] != b"PE\0\0":
                raise PackagingError(f"Invalid PE signature: {path}")
            machine = struct.unpack_from("<H", header, 4)[0]
            return {"format": "PE", "architectures": [CPU_TYPES.get(machine, f"machine-{machine:#x}")]}
        if data[:4] == b"\x7fELF":
            if len(data) < 20 or data[5] not in (1, 2):
                raise PackagingError(f"Invalid ELF header: {path}")
            machine = struct.unpack_from("<H" if data[5] == 1 else ">H", data, 18)[0]
            return {"format": "ELF", "architectures": [CPU_TYPES.get(machine, f"machine-{machine:#x}")]}
        thin = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">",
                b"\xce\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xce": ">"}
        if data[:4] in thin:
            if len(data) < 8:
                raise PackagingError(f"Truncated Mach-O header: {path}")
            machine = struct.unpack_from(thin[data[:4]] + "I", data, 4)[0]
            return {"format": "Mach-O", "architectures": [CPU_TYPES.get(machine, f"machine-{machine:#x}")]}
        fat = {b"\xca\xfe\xba\xbe": (">", 20), b"\xbe\xba\xfe\xca": ("<", 20),
               b"\xca\xfe\xba\xbf": (">", 32), b"\xbf\xba\xfe\xca": ("<", 32)}
        if data[:4] in fat:
            endian, size = fat[data[:4]]
            count = struct.unpack_from(endian + "I", data, 4)[0]
            if not 1 <= count <= 32:
                raise PackagingError(f"Invalid universal Mach-O architecture count: {path}")
            handle.seek(8)
            records = handle.read(count * size)
            if len(records) != count * size:
                raise PackagingError(f"Truncated universal Mach-O table: {path}")
            architectures = []
            for index in range(count):
                machine = struct.unpack_from(endian + "I", records, index * size)[0]
                architecture = CPU_TYPES.get(machine, f"machine-{machine:#x}")
                if architecture not in architectures:
                    architectures.append(architecture)
            return {"format": "Mach-O", "architectures": architectures}
    return None


def payload_executable(payload: Path, operating_system: str) -> Path:
    if operating_system == "macos":
        return payload / "Contents" / "MacOS" / "OpenNavicat"
    return payload / ("OpenNavicat.exe" if operating_system == "windows" else "OpenNavicat")


def require_native(path: Path, operating_system: str, architecture: str) -> dict:
    identity = binary_identity(path)
    expected_format = {"windows": "PE", "linux": "ELF", "macos": "Mach-O"}[operating_system]
    if not identity or identity["format"] != expected_format or architecture not in identity["architectures"]:
        raise PackagingError(f"{path} is not a native {operating_system}/{architecture} image: {identity}")
    return identity


def validate_payload(payload: Path, operating_system: str, architecture: str) -> dict:
    executable = payload_executable(payload, operating_system)
    if not executable.is_file():
        raise PackagingError(f"Build the native application first; missing executable: {executable}")
    main = require_native(executable, operating_system, architecture)
    images, seen = [], set()
    root = payload.resolve()
    for path in sorted(payload.rglob("*")):
        if path.is_symlink():
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise PackagingError(f"Payload symlink escapes the distribution: {path}")
            if not path.exists():
                raise PackagingError(f"Payload symlink has a missing target: {path}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise PackagingError(f"Payload symlink escapes the distribution: {path}")
        if resolved in seen:
            continue
        seen.add(resolved)
        identity = binary_identity(path)
        if not identity:
            continue
        require_native(path, operating_system, architecture)
        images.append({"path": str(path.relative_to(payload)), **identity})
    return {"executable": str(executable.relative_to(payload)), "main": main,
            "executable_sha256": sha256(executable), "native_images": images,
            "native_image_count": len(images)}


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def run(command, **kwargs):
    print("Running: " + subprocess.list2cmdline([str(part) for part in command]), flush=True)
    return subprocess.run([str(part) for part in command], check=True, **kwargs)


def render_inno(payload: Path, output: Path, basename: str, version: str, architecture: str) -> str:
    values = {"VERSION": version, "SOURCE_DIR": str(payload.resolve()),
              "OUTPUT_DIR": str(output.resolve()), "OUTPUT_NAME": basename,
              "ARCH": architecture, "ALLOWED_ARCH": "arm64" if architecture == "arm64" else "x64os",
              "LICENSE": '"' + str(ROOT / "LICENSE").replace('"', '""') + '"'}
    text = (RESOURCES / "windows.iss").read_text("utf-8")
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            raise PackagingError("An installer path contains a newline")
        text = text.replace("@" + name + "@", value)
    return text


def build_windows(payload: Path, output: Path, version: str, architecture: str, iscc: str | None) -> Path:
    compiler = iscc or shutil.which("ISCC.exe") or shutil.which("iscc")
    if not compiler:
        for directory in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if directory:
                candidate = Path(directory) / "Inno Setup 6" / "ISCC.exe"
                if candidate.is_file():
                    compiler = str(candidate)
                    break
    if not compiler:
        raise PackagingError("Inno Setup ISCC.exe is required on the native Windows build host")
    basename = f"OpenNavicat-{version}-windows-{architecture}-setup"
    with tempfile.TemporaryDirectory(prefix="opennavicat-inno-") as temporary:
        source = Path(temporary) / "OpenNavicat.iss"
        source.write_text(render_inno(payload, output, basename, version, architecture), "utf-8-sig")
        run([compiler, "/Qp", source], timeout=600)
    return output / (basename + ".exe")


def build_macos(payload: Path, output: Path, version: str, architecture: str) -> Path:
    target = output / f"OpenNavicat-{version}-macos-{architecture}.dmg"
    with tempfile.TemporaryDirectory(prefix="opennavicat-dmg-") as temporary:
        stage = Path(temporary) / "volume"
        stage.mkdir()
        run(["/usr/bin/ditto", payload, stage / "OpenNavicat.app"], timeout=300)
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        (stage / "Install.txt").write_text(
            "Install OpenNavicat by dragging OpenNavicat.app to Applications.\n"
            "This distribution is not Developer ID signed or notarized.\n", "utf-8")
        run(["/usr/bin/hdiutil", "create", "-volname", "OpenNavicat", "-srcfolder", stage,
             "-format", "UDZO", "-fs", "HFS+", "-ov", target], timeout=600)
    return target


def debian_control(version: str, architecture: str, installed_kib: int, libc_version: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)+", libc_version):
        raise PackagingError("Could not determine the native Linux glibc baseline")
    dependencies = [f"libc6 (>= {libc_version})", "libegl1", "libopengl0",
        "libglib2.0-0t64 | libglib2.0-0", "libdbus-1-3", "libxkbcommon0",
        "libxkbcommon-x11-0", "libxcb1", "libx11-xcb1", "libxcb-cursor0",
        "libxcb-icccm4", "libxcb-keysyms1", "libxcb-image0", "libxcb-render-util0",
        "libxcb-shape0", "libxcb-randr0", "libxcb-xfixes0", "libxcb-sync1",
        "libxcb-xinerama0", "libxcb-util1", "libxcb-xkb1", "libxcb-glx0",
        "libfontconfig1", "libfreetype6"]
    return (f"Package: opennavicat\nVersion: {version}\nSection: database\nPriority: optional\n"
        f"Architecture: {architecture}\nMaintainer: OpenNavicat contributors\nInstalled-Size: {installed_kib}\n"
        "Homepage: https://github.com/shubolab/opennavicat\nDepends: " + ", ".join(dependencies) + "\n"
        "Description: Native database workbench\n"
        " Local SQLite, PostgreSQL, MySQL and MariaDB connections, SQL queries and data management.\n")


def build_linux(payload: Path, output: Path, version: str, architecture: str) -> Path:
    if not shutil.which("dpkg-deb"):
        raise PackagingError("dpkg-deb is required on the native Debian/Ubuntu build host")
    libc_name, libc_version = platform.libc_ver()
    if libc_name != "glibc":
        raise PackagingError("The Debian installer must be built on a native glibc Linux host")
    target = output / f"OpenNavicat-{version}-linux-{architecture}.deb"
    with tempfile.TemporaryDirectory(prefix="opennavicat-deb-") as temporary:
        stage = Path(temporary) / "package"
        destination = stage / "opt" / "opennavicat"
        destination.parent.mkdir(parents=True)
        shutil.copytree(payload, destination, symlinks=True)
        launcher = stage / "usr" / "bin" / "opennavicat"
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexec /opt/opennavicat/OpenNavicat "$@"\n', "utf-8")
        launcher.chmod(0o755)
        desktop = stage / "usr/share/applications/opennavicat.desktop"
        desktop.parent.mkdir(parents=True)
        shutil.copy2(RESOURCES / "opennavicat.desktop", desktop)
        picture = stage / "usr/share/icons/hicolor/scalable/apps/opennavicat.svg"
        picture.parent.mkdir(parents=True)
        shutil.copy2(RESOURCES / "opennavicat.svg", picture)
        copyright_file = stage / "usr/share/doc/opennavicat/copyright"
        copyright_file.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "LICENSE", copyright_file)
        control = stage / "DEBIAN/control"
        control.parent.mkdir()
        size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        control.write_text(debian_control(version, architecture, (size + 1023) // 1024, libc_version), "utf-8")
        run(["dpkg-deb", "--build", "--root-owner-group", stage, target], timeout=600)
    return target


def validate_smoke_report(path: Path, version: str, architecture: str, operating_system: str) -> dict:
    if not path.is_file():
        raise PackagingError("Installed application did not write its runtime smoke report")
    report = json.loads(path.read_text("utf-8"))
    runtime = report.get("runtime", {})
    if report.get("smoke") != "passed" or report.get("rows") != 6:
        raise PackagingError(f"Installed SQL smoke did not return six real rows: {report}")
    if runtime.get("version") != version:
        raise PackagingError(f"Installed application version {runtime.get('version')} differs from installer {version}")
    if normalize_arch(runtime.get("machine", "")) != architecture:
        raise PackagingError(f"Installed application ran with the wrong CPU architecture: {runtime}")
    expected_os = {"windows": "Windows", "macos": "Darwin", "linux": "Linux"}[operating_system]
    if runtime.get("os") != expected_os or not isinstance(runtime.get("libpq"), int) or runtime["libpq"] <= 0:
        raise PackagingError(f"Installed application did not load the native database runtime: {runtime}")
    if not all(runtime.get(name) for name in ("qt", "psycopg", "pymysql", "paramiko", "cryptography")):
        raise PackagingError(f"An installed database/SSH driver did not load: {runtime}")
    return report


def smoke_installed(executable: Path, directory: Path, version: str, architecture: str,
                    operating_system: str) -> dict:
    report = directory / "runtime-smoke.json"
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment["OPENNAVICAT_DATA_DIR"] = str(directory / "isolated-workspace")
    run([executable, "--smoke-test", "--smoke-report", report],
        cwd=directory, env=environment, timeout=90)
    return validate_smoke_report(report, version, architecture, operating_system)


def verify_macos(installer: Path, version: str, architecture: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="opennavicat-dmg-install-") as temporary:
        directory = Path(temporary)
        volume = directory / "volume"
        volume.mkdir()
        installed = directory / "Installed Applications" / "OpenNavicat.app"
        mounted = False
        try:
            result = run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-plist",
                          "-mountpoint", volume, installer], capture_output=True, timeout=90)
            plistlib.loads(result.stdout)  # Validate a real hdiutil mount receipt.
            mounted = True
            applications = volume / "Applications"
            if not applications.is_symlink() or applications.readlink() != Path("/Applications"):
                raise PackagingError("DMG does not provide its Applications installation link")
            require_native(payload_executable(volume / "OpenNavicat.app", "macos"), "macos", architecture)
            installed.parent.mkdir()
            run(["/usr/bin/ditto", volume / "OpenNavicat.app", installed], timeout=300)
            report = smoke_installed(payload_executable(installed, "macos"), directory, version, architecture, "macos")
            shutil.rmtree(installed)
            if installed.exists():
                raise PackagingError("Isolated macOS application removal failed")
        finally:
            if mounted or os.path.ismount(volume):
                run(["/usr/bin/hdiutil", "detach", volume], timeout=90)
    return {"status": "passed", "installed": True, "launched": True, "uninstalled": True,
            "scope": "read-only DMG and isolated Applications copy", "runtime": report}


def verify_windows(installer: Path, version: str, architecture: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="opennavicat-setup-install-") as temporary:
        directory = Path(temporary)
        installed = directory / "Application"
        uninstaller = installed / "unins000.exe"
        try:
            run([installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                 "/CURRENTUSER", "/NOICONS", "/VERIFY=1", f"/DIR={installed}"], timeout=300)
            executable = installed / "OpenNavicat.exe"
            require_native(executable, "windows", architecture)
            report = smoke_installed(executable, directory, version, architecture, "windows")
        finally:
            if uninstaller.is_file():
                run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=180)
                deadline = time.monotonic() + 15
                while installed.exists() and time.monotonic() < deadline:
                    time.sleep(0.2)
                if installed.exists():
                    raise PackagingError("Windows uninstaller did not remove its isolated installation")
            elif installed.exists():
                raise PackagingError("Windows installation did not create its uninstaller")
    return {"status": "passed", "installed": True, "launched": True, "uninstalled": True,
            "scope": "per-user temporary installation with separate verification AppId", "runtime": report}


def _privileged(command):
    if os.geteuid() == 0:
        return command
    if shutil.which("sudo"):
        return ["sudo", "-n", *command]
    raise PackagingError("Installed Debian verification requires root or passwordless CI sudo")


def _debian_present() -> bool:
    return subprocess.run(["dpkg-query", "-W", "-f=${Status}", "opennavicat"],
        capture_output=True, text=True, check=False).returncode == 0


def verify_linux(installer: Path, version: str, architecture: str) -> dict:
    installed = Path("/opt/opennavicat")
    launcher = Path("/usr/bin/opennavicat")
    desktop = Path("/usr/share/applications/opennavicat.desktop")
    if _debian_present() or any(path.exists() for path in (installed, launcher, desktop)):
        raise PackagingError("Installed-package verification requires a clean host; an OpenNavicat installation already exists")
    with tempfile.TemporaryDirectory(prefix="opennavicat-deb-install-") as temporary:
        directory = Path(temporary)
        try:
            run(_privileged(["apt-get", "install", "--yes", "--no-install-recommends", str(installer.resolve())]), timeout=600)
            require_native(installed / "OpenNavicat", "linux", architecture)
            if not launcher.is_file() or not desktop.is_file():
                raise PackagingError("Debian package did not install its launcher and desktop entry")
            report = smoke_installed(launcher, directory, version, architecture, "linux")
        finally:
            if _debian_present():
                run(_privileged(["dpkg", "--purge", "opennavicat"]), timeout=180)
            if any(path.exists() for path in (installed, launcher, desktop)):
                raise PackagingError("Debian uninstall left its application, launcher or desktop entry behind")
    return {"status": "passed", "installed": True, "launched": True, "uninstalled": True,
            "scope": "native Debian package installation and purge", "runtime": report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-assets")
    parser.add_argument("--version", help="Release version; defaults to pyproject.toml")
    parser.add_argument("--arch", choices=["amd64", "arm64"], help="Must match the native host and every application binary")
    parser.add_argument("--verify-installed", action="store_true", help="Install, launch and uninstall the created native installer")
    parser.add_argument("--iscc", help="Optional path to the Inno Setup compiler on Windows")
    args = parser.parse_args(argv)
    operating_system = host_os()
    native_arch = host_arch()
    architecture = args.arch or native_arch
    if architecture != native_arch:
        raise PackagingError(f"Native build host is {native_arch}; refusing a relabeled {architecture} installer")
    version = validate_version(args.version or tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"])
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = args.dist_dir.resolve() / ("OpenNavicat.app" if operating_system == "macos" else "OpenNavicat")
    identity = validate_payload(payload, operating_system, architecture)
    if operating_system == "windows":
        installer = build_windows(payload, output, version, architecture, args.iscc)
    elif operating_system == "macos":
        installer = build_macos(payload, output, version, architecture)
    else:
        installer = build_linux(payload, output, version, architecture)
    if not installer.is_file():
        raise PackagingError(f"Native installer builder did not produce: {installer}")
    manifest = {"name": "OpenNavicat", "version": version, "os": operating_system,
        "architecture": architecture, "native_host_architecture": native_arch,
        "installer": installer.name, "sha256": sha256(installer), "bytes": installer.stat().st_size,
        "publisher_signed": False, "notarized": False, "payload": identity,
        "verification": {"status": "not-run"}}
    if operating_system == "windows":
        manifest["installer_wrapper"] = binary_identity(installer)
        manifest["wrapper_note"] = "Inno Setup wrapper may use x86 emulation; the application payload is native and architecture-checked."
    manifest_path = installer.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    installer.with_suffix(installer.suffix + ".sha256").write_text(f"{manifest['sha256']}  {installer.name}\n", "ascii")
    if args.verify_installed:
        verification = {"macos": verify_macos, "windows": verify_windows, "linux": verify_linux}[operating_system]
        manifest["verification"] = verification(installer, version, architecture)
        report_path = installer.with_suffix(".installed-smoke.json")
        report_path.write_text(json.dumps(manifest["verification"], ensure_ascii=False, indent=2), "utf-8")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"installer": str(installer), "manifest": str(manifest_path),
                      "verification": manifest["verification"]["status"]}), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PackagingError, subprocess.SubprocessError, OSError) as exc:
        print(f"Native installer packaging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
