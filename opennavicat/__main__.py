from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QLockFile, QTimer, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox

from .ui.theme import apply_theme
from .ui.window import MainWindow, QueryPage
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="OpenNavicat native database workbench")
    parser.add_argument("--demo", action="store_true", help="Open the local SQLite sample workspace")
    parser.add_argument("--smoke-test", action="store_true", help="Run an isolated sample query and exit")
    parser.add_argument("--smoke-report", type=Path, help="Write a runtime/driver report for --smoke-test")
    parser.add_argument("--screenshot", type=Path, help="Save the initial window as a PNG")
    parser.add_argument("--theme", choices=["system", "dark", "light"], default="system", help="Follow system appearance or select a comparison theme")
    args = parser.parse_args()
    if args.smoke_report and not args.smoke_test:
        parser.error("--smoke-report requires --smoke-test")
    runtime = None
    if args.smoke_test:
        # Exercise each bundled driver import too. A successful SQLite query
        # alone cannot prove that libpq or the SSH crypto extensions were shipped.
        import psycopg
        import pymysql
        import paramiko
        import cryptography
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
        # A fixed public test vector exercises the dynamically loaded legacy
        # provider. Imports alone can succeed while this module is missing.
        probe = Cipher(Blowfish(bytes(16)), modes.ECB()).encryptor()
        probe_bytes = probe.update(bytes(8)) + probe.finalize()
        if probe_bytes.hex() != "4ef997456198dd78":
            raise RuntimeError("OpenSSL legacy provider cipher probe failed")
        runtime = {"version": __version__, "os": platform.system(),
            "machine": platform.machine(), "python": platform.python_version(),
            "qt": QLibraryInfo.version().toString(), "psycopg": psycopg.__version__,
            "libpq": psycopg.pq.version(), "psycopg_impl": psycopg.pq.__impl__,
            "pymysql": pymysql.VERSION_STRING, "paramiko": paramiko.__version__,
            "cryptography": cryptography.__version__, "crypto_legacy_probe": True}
    app = QApplication(sys.argv[:1])
    app.setApplicationName("OpenNavicat")
    app.setOrganizationName("OpenNavicat")
    app.setApplicationVersion(__version__)
    translator = QTranslator(app)
    if translator.load(QLocale("zh_CN"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app._qt_translator = translator
    apply_theme(app, args.theme)
    temporary = None
    from .storage import Store
    if args.smoke_test:
        temporary = tempfile.TemporaryDirectory(prefix="opennavicat-smoke-")
        store = Store(Path(temporary.name))
    else:
        try:
            store = Store()
        except Exception as exc:
            QMessageBox.critical(None, "Cannot open workspace", str(exc))
            return 1
    lock = QLockFile(str(store.directory / "workspace.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "Workspace already open", "This OpenNavicat workspace is already open in another process.")
        return 1
    window = MainWindow(store)
    window.show()
    if args.demo or args.smoke_test:
        QTimer.singleShot(50, window.sample_workspace)
    if args.smoke_test:
        # Exercise the actual asynchronous UI/driver path, with a bounded deadline.
        state = {"ran": False, "attempts": 0}
        def verify():
            state["attempts"] += 1
            page = window.tabs.currentWidget()
            if isinstance(page, QueryPage) and page.connection_id in window.sessions:
                if not state["ran"]:
                    state["ran"] = True
                    window.run_query(page)
                elif not page.running and not window.tasks:
                    result = page.current_result()
                    if result and len(result.rows) == 6:
                        if args.screenshot:
                            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                            window.grab().save(str(args.screenshot))
                        report = {"smoke": "passed", "rows": len(result.rows),
                                  "columns": result.columns, "runtime": runtime}
                        if args.smoke_report:
                            args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
                            args.smoke_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
                        print(json.dumps(report), flush=True)
                        window.close()
                        app.exit(0)
                        return
                    print("Smoke query did not return expected rows", file=sys.stderr, flush=True)
                    app.exit(1)
                    return
            if state["attempts"] > 200:
                print("Smoke test timed out", file=sys.stderr, flush=True)
                app.exit(1)
                return
            QTimer.singleShot(100, verify)
        QTimer.singleShot(100, verify)
    elif args.screenshot:
        def screenshot():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
        QTimer.singleShot(1600, screenshot)
    status = app.exec()
    lock.unlock()
    if temporary:
        temporary.cleanup()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
