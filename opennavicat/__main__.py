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
        import pytds
        import redis
        import OpenSSL
        import certifi
        import pymongo
        import bson
        import importlib.metadata
        import ssl
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
        # A fixed public test vector exercises the dynamically loaded legacy
        # provider. Imports alone can succeed while this module is missing.
        probe = Cipher(Blowfish(bytes(16)), modes.ECB()).encryptor()
        probe_bytes = probe.update(bytes(8)) + probe.finalize()
        if probe_bytes.hex() != "4ef997456198dd78":
            raise RuntimeError("OpenSSL legacy provider cipher probe failed")
        from .database import ConnectionConfig, DatabaseSession
        sqlserver = DatabaseSession(ConnectionConfig("native-smoke-sqlserver", "Generated smoke", driver="sqlserver",
            host="localhost", database="master", username="smoke", ssl_mode="verify-full"))
        redis_session = DatabaseSession(ConnectionConfig("native-smoke-redis", "Generated smoke", driver="redis",
            database="0", ssl_mode="disable"))
        mongo_session = DatabaseSession(ConnectionConfig("native-smoke-mongo", "Generated smoke", driver="mongodb",
            database="admin", ssl_mode="verify-full"))
        adapters_constructed = (sqlserver.__class__.__module__ == "opennavicat.sqlserver_session"
            and redis_session.__class__.__module__ == "opennavicat.redis_session")
        if not adapters_constructed:
            raise RuntimeError("Native SQL Server / Redis factory did not construct the actual adapters")
        mongo_constructed = mongo_session.__class__.__module__ == "opennavicat.mongodb_session"
        if not mongo_constructed:
            raise RuntimeError("Native MongoDB factory did not construct its actual adapter")
        from .mongodb_session import BSON_CODEC, canonical_dumps, canonical_loads
        document = {"long": bson.Int64(9007199254740993), "decimal": bson.Decimal128("1.00"),
                    "binary": bson.Binary(b"\x00\xff\r\n", 128)}
        expected_bson = bson.BSON.encode(document, codec_options=BSON_CODEC)
        bson_probe = (bson.BSON.encode(canonical_loads(canonical_dumps(document)), codec_options=BSON_CODEC) == expected_bson
                      and bson.has_c() and pymongo.has_c())
        if not bson_probe:
            raise RuntimeError("The packaged native BSON codec did not preserve its typed test document")
        # connect=False initializes PyMongo's actual TLS configuration without
        # starting server discovery or connecting to an external database.
        with pymongo.MongoClient(**mongo_session._connection_options()) as probe_client:
            mongo_tls_ready = probe_client.options.pool_options._ssl_context is not None
        if not mongo_tls_ready:
            raise RuntimeError("The packaged MongoDB TLS context did not initialize")
        tls_options = sqlserver._connection_options()
        import pytds.tls
        tls_context = pytds.tls.create_context(tls_options["cafile"])
        if tls_context is None:
            raise RuntimeError("Native SQL Server TLS context did not initialize")
        sqlserver.close()
        redis_session.close()
        mongo_session.close()
        runtime = {"version": __version__, "os": platform.system(),
            "machine": platform.machine(), "python": platform.python_version(),
            "qt": QLibraryInfo.version().toString(), "psycopg": psycopg.__version__,
            "libpq": psycopg.pq.version(), "psycopg_impl": psycopg.pq.__impl__,
            "pymysql": pymysql.VERSION_STRING, "paramiko": paramiko.__version__,
            "python_tds": importlib.metadata.version("python-tds"), "redis": redis.__version__,
            "pyopenssl": OpenSSL.__version__, "certifi": certifi.__version__,
            "pymongo": pymongo.version,
            "mongodb_adapter_constructed": mongo_constructed, "mongodb_bson_probe": bool(bson_probe),
            "mongodb_tls_context_ready": mongo_tls_ready,
            "sqlserver_default_ca_present": Path(certifi.where()).is_file(),
            "extra_adapters_constructed": adapters_constructed,
            "sqlserver_tls_context_ready": tls_context is not None,
            "cryptography": cryptography.__version__, "crypto_legacy_probe": True,
            "crypto_openssl": default_backend().openssl_version_text(),
            "stdlib_openssl": ssl.OPENSSL_VERSION,
            "native_openssl_bootstrap": getattr(sys, "_opennavicat_native_openssl", None)}
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
    if not (args.demo or args.smoke_test or args.screenshot) and store.state.get("navicat_auto_discover", True):
        QTimer.singleShot(0, window.start_navicat_discovery)
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
