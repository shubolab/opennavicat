"""Record one GIF of live OpenNavicat widgets using an isolated generated fixture.

Example: .venv/bin/python scripts/record-demo.py
The default offscreen Qt platform avoids taking focus from the user's apps.
This records actual application rendering; it never reads Navicat or an existing
OpenNavicat workspace. Only the final GIF and a path-free JSON receipt survive.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
import uuid


ROOT = Path(__file__).resolve().parents[1]
CONNECTION_NAME = "演示数据库 · 示例数据"
CONNECTION_ID = "readme-generated-demo"
MONGO_CONNECTION_ID = "readme-generated-mongodb"
MONGO_CONNECTION_NAME = "MongoDB · 生成示例"
MONGO_DATABASE = "demo_catalog"
MONGO_COLLECTION = "products"
MONGO_BUCKET = "sample_files"
QUERY = """-- 以下客户、城市和订单均为程序生成的示例数据
SELECT
    c.name AS customer,
    c.city,
    COUNT(o.id) AS orders,
    ROUND(SUM(p.price * o.quantity), 2) AS revenue
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.id
JOIN products AS p ON p.id = o.product_id
GROUP BY c.id
ORDER BY revenue DESC;"""


def dependencies(include_mongodb=False):
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("ffmpeg is required; this script does not install software.")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe is required to verify the encoded GIF.")
    version = subprocess.run([executable, "-version"], check=True,
                             capture_output=True, text=True).stdout.splitlines()[0]
    filters = subprocess.run([executable, "-hide_banner", "-filters"], check=True,
                             capture_output=True, text=True).stdout
    if not all(name in filters for name in ("palettegen", "paletteuse")):
        raise RuntimeError("The installed ffmpeg lacks GIF palette filters.")
    if include_mongodb and shutil.which("docker") is None:
        raise RuntimeError("Docker is required for a new owned MongoDB demo service.")
    return executable, version


class OwnedMongoDemo:
    """No external endpoint input. Each service has a new identity and password."""

    def __init__(self):
        self.owner = uuid.uuid4().hex
        self.name = "opennavicat-readme-demo-" + self.owner[:16]
        self.container_id = None
        self.password = secrets.token_urlsafe(32)
        self.username = "readme_fixture"
        self.host = "127.0.0.1"
        self.port = None
        self.server_version = self.architecture = ""
        self.identity_verified = self.removed = False

    @staticmethod
    def _docker(*arguments, missing=False):
        try:
            result = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            # TimeoutExpired.__str__ includes argv, including the fixture secret.
            raise RuntimeError("The owned MongoDB demo Docker command timed out.") from None
        except OSError:
            raise RuntimeError("The owned MongoDB demo Docker command could not start.") from None
        if result.returncode:
            if missing and any(message in result.stderr.lower() for message in ("no such object", "no such container")):
                return None
            # Docker argv may contain this generated service's credential.
            raise RuntimeError("The owned MongoDB demo Docker command failed.")
        return result.stdout.strip()

    def inspect(self, *, missing=False):
        data = self._docker("inspect", self.container_id or self.name, missing=missing)
        if data is None:
            return None
        entries = json.loads(data)
        if len(entries) != 1:
            raise RuntimeError("The owned MongoDB container identity is ambiguous.")
        entry = entries[0]
        labels = entry.get("Config", {}).get("Labels", {})
        if (entry.get("Name") != "/" + self.name
                or labels.get("opennavicat.test") != "true"
                or labels.get("opennavicat.task") != "readme-demo"
                or labels.get("opennavicat.owner") != self.owner
                or (self.container_id is not None and entry.get("Id") != self.container_id)):
            raise RuntimeError("The demo refused to use or remove a container without its exact ownership identity.")
        self.container_id = entry["Id"]
        self.identity_verified = True
        return entry

    def start(self, *, readiness_seconds=45):
        from opennavicat.database import ConnectionConfig
        from opennavicat.mongodb_session import MongoSession
        from pymongo import timeout as mongo_timeout

        if not 0 < readiness_seconds <= 120:
            raise ValueError("The generated MongoDB readiness budget must be positive and at most 120 seconds.")

        identifier = self._docker("run", "--detach", "--rm", "--name", self.name,
            "--label", "opennavicat.test=true", "--label", "opennavicat.task=readme-demo",
            "--label", "opennavicat.owner=" + self.owner, "--publish", "127.0.0.1::27017",
            "-e", "MONGO_INITDB_ROOT_USERNAME=" + self.username,
            "-e", "MONGO_INITDB_ROOT_PASSWORD=" + self.password, "mongo:8.0", "--bind_ip_all")
        self.container_id = identifier
        entry = self.inspect()
        mappings = entry.get("NetworkSettings", {}).get("Ports", {}).get("27017/tcp") or []
        if len(mappings) != 1 or mappings[0].get("HostIp") != "127.0.0.1":
            raise RuntimeError("The MongoDB demo service must expose exactly one loopback-only port.")
        self.port = int(mappings[0]["HostPort"])
        config = ConnectionConfig(MONGO_CONNECTION_ID, MONGO_CONNECTION_NAME, driver="mongodb",
            database=MONGO_DATABASE, host=self.host, port=self.port, username=self.username,
            password=self.password, ssl_mode="disable", connect_timeout=3,
            mongo_auth_source="admin", mongo_auth_mechanism="SCRAM-SHA-256")
        deadline = time.monotonic()+readiness_seconds
        while True:
            session = MongoSession(config)
            try:
                with mongo_timeout(remaining_seconds(deadline)):
                    session.connect()
                    self.server_version = str(session.connection.server_info()["version"])
            except Exception:
                session.close()
                if time.monotonic() > deadline:
                    raise RuntimeError("The new owned MongoDB demo service did not become ready.") from None
                time.sleep(.1)
                continue
            try:
                # The same remaining native deadline also covers count/list,
                # inserts, image preparation and the complete GridFS upload.
                with mongo_timeout(remaining_seconds(deadline)):
                    self._seed(session,deadline=deadline)
                    remaining_seconds(deadline)
            finally:
                session.close()
            break
        self.architecture = json.loads(self._docker("image", "inspect", entry["Image"]))[0]["Architecture"]
        architecture = self._docker("info", "--format", "{{.Architecture}}")
        normalize = lambda value: {"aarch64":"arm64","x86_64":"amd64"}.get(value,value)
        if normalize(architecture) != normalize(self.architecture):
            raise RuntimeError("The MongoDB demo image must match the Docker engine's native architecture.")
        self.inspect()  # Recheck identity before handing a session to the GUI.
        return config

    @staticmethod
    def _seed(session, *, deadline):
        from bson import Binary, Decimal128, Int64, ObjectId
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter
        from tempfile import TemporaryDirectory

        remaining_seconds(deadline)
        collection = session.connection[MONGO_DATABASE][MONGO_COLLECTION]
        if collection.count_documents({}) or session.connection[MONGO_DATABASE].list_collection_names():
            raise RuntimeError("The generated demo database must start empty.")
        prices = ("1299.95", "349.50", "79.00", "459.90", "189.00", "39.95")
        products = [{"_id": ObjectId(f"{number:024x}"), "sku": f"DEMO-{number:03}",
            "name": f"生成商品 {number:02}", "price": Decimal128(price),
            "stock": Int64(9007199254740993+number),
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "attributes": {"origin": "GENERATED SAMPLE", "active": True, "rating": Decimal128("4.75")},
            "tags": ["generated", "sample"], "binary": Binary(b"\x00\x01\xfe\xff", 128)}
            for number, price in enumerate(prices, 1)]
        collection.insert_many(products)
        remaining_seconds(deadline)
        image = QImage(480, 300, QImage.Format.Format_RGB32)
        image.fill(QColor("#f0f4fa"))
        painter = QPainter(image)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (height, color) in enumerate(((90,"#459bd9"),(145,"#7bb75e"),(115,"#a78bce"),(200,"#459bd9"),(165,"#7bb75e"),(225,"#a78bce"))):
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(28+index*73, 270-height, 48, height, 5, 5)
        painter.end()
        with TemporaryDirectory(prefix="opennavicat-demo-image-") as temporary:
            path = Path(temporary) / "generated-chart.png"
            if not image.save(str(path), "PNG"):
                raise RuntimeError("Could not create the generated demo image.")
            remaining_seconds(deadline)
            session.upload_file(MONGO_DATABASE, MONGO_BUCKET, str(path), metadata={"origin":"GENERATED SAMPLE"})

    def cleanup(self):
        entry = self.inspect(missing=True)
        if entry is not None:
            self._docker("rm", "--force", self.container_id)
            if self.inspect(missing=True) is not None:
                raise RuntimeError("The exact owned MongoDB container still exists after cleanup.")
        self.removed = True

    def receipt(self):
        return {"image": "mongo:8.0", "server_version": self.server_version,
            "image_architecture": self.architecture, "loopback_only": True,
            "exact_container_identity_verified": self.identity_verified,
            "owned_container_removed": self.removed, "credentials_generated_in_memory": True}


def remaining_seconds(deadline):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise RuntimeError("The generated MongoDB readiness deadline expired.")
    return remaining


def visible_texts(window):
    """Inspect rendered text surfaces, not hidden credentials or UserRole data."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QAbstractButton, QAbstractItemView, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QTextEdit

    yield window.windowTitle()
    for widget in window.findChildren(QLabel)+window.findChildren(QLineEdit):
        if widget.isVisible():
            yield widget.text()
    for widget in window.findChildren(QPlainTextEdit)+window.findChildren(QTextEdit):
        if widget.isVisible():
            yield widget.toPlainText()
    for widget in window.findChildren(QComboBox):
        if widget.isVisible():
            yield widget.currentText()
    for widget in window.findChildren(QAbstractButton):
        if widget.isVisible():
            yield widget.text()
    for action in window.findChildren(QAction):
        if action.isVisible():
            yield action.text()
    for view in window.findChildren(QAbstractItemView):
        if not view.isVisible() or view.model() is None:
            continue
        model = view.model()
        for column in range(model.columnCount()):
            value = model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            if value is not None:
                yield str(value)
        def rows(parent):
            for row in range(model.rowCount(parent)):
                for column in range(model.columnCount(parent)):
                    index = model.index(row,column,parent)
                    value = model.data(index,Qt.ItemDataRole.DisplayRole)
                    if value is not None:
                        yield str(value)
                first = model.index(row,0,parent)
                if model.rowCount(first):
                    yield from rows(first)
        from PySide6.QtCore import QModelIndex
        yield from rows(QModelIndex())


def assert_visible_privacy(window, temporary, mongo=None):
    """Check actual visible widgets before QWidget.grab or any frame write."""
    for text in visible_texts(window):
        if (any(value in text for value in (str(temporary),str(ROOT),str(Path.home())))
                or re.search(r"/(?:Users|home|private|tmp|var/folders|Volumes)/|[A-Za-z]:[\\/]|\\\\[^\\]+\\",text)):
            raise RuntimeError("A visible demo widget contains a local filesystem path.")
        if (re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\[::1\]|localhost|host\.docker\.internal|mongodb(?:\+srv)?://",text,re.I)
                or (mongo is not None and (any(value in text for value in (mongo.password,mongo.username,mongo.name,mongo.owner))
                    or re.search(r"(?<!\w)"+str(mongo.port)+r"(?!\w)",text)))):
            raise RuntimeError("A visible demo widget contains a service endpoint or credential field.")


def generated_sample(directory):
    from opennavicat.storage import create_sample

    path = create_sample(directory)
    with closing(sqlite3.connect(path)) as database, database:
        for number in range(1, 7):
            database.execute(
                "UPDATE customers SET name=?,email=?,city=?,joined_at=? WHERE id=?",
                (f"示例客户 {number:02}", f"demo{number:02}@example.invalid",
                 f"示例城市 {number:02}", "2026-01-01 09:00:00", number))
        database.execute("UPDATE orders SET ordered_at='2026-01-10 10:00:00'")
    return path


def encode(ffmpeg, frames, fps, output):
    palette = frames.parent / "palette.png"
    common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    sequence = ["-framerate", str(fps), "-i", str(frames / "frame-%05d.png")]
    commands = [
        [*common, *sequence, "-vf", "palettegen=max_colors=256:stats_mode=diff",
         "-frames:v", "1", str(palette)],
        [*common, *sequence, "-i", str(palette), "-lavfi",
         "paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
         "-gifflags", "+offsetting+transdiff", "-loop", "0", str(output)],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Never put an absolute capture/workspace path in a public receipt.
            error = result.stderr.replace(str(frames.parent), "<temporary-demo>")
            raise RuntimeError("GIF encoding failed: " + error[-1800:])


def verify_gif(path, receipt):
    result = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_read_frames,duration:format=duration,size",
        "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=30)
    probe = json.loads(result.stdout)
    stream = probe["streams"][0]
    count = int(stream["nb_read_frames"])
    duration = float(probe["format"]["duration"])
    if count != receipt["frames"] or [stream["width"], stream["height"]] != receipt["size"]:
        raise RuntimeError("Encoded GIF frames or dimensions differ from the Qt capture.")
    if duration <= 0 or abs(duration - count / receipt["fps"]) > 2 / receipt["fps"]:
        raise RuntimeError("Encoded GIF timing differs from the requested frame rate.")
    receipt["captured_nominal_duration_seconds"] = receipt["duration_seconds"]
    receipt["duration_seconds"] = duration
    receipt["gif_probe"] = {"frames": count, "duration_seconds": duration,
        "r_frame_rate": stream["r_frame_rate"], "avg_frame_rate": stream["avg_frame_rate"],
        "effective_frames_per_second": round(count / duration, 6),
        "note": "GIF delays are quantized to centiseconds; fps is the requested capture rate."}


def record(args, temporary, io_pool, mongo=None, mongo_config=None):
    from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator
    from PySide6.QtWidgets import QApplication

    from opennavicat import __version__
    from opennavicat.database import ConnectionConfig
    from opennavicat.storage import Store
    from opennavicat.ui.table_designer import TableDesignerPage
    from opennavicat.ui.theme import apply_theme
    from opennavicat.ui.window import MainWindow, TablePage

    io_jobs = []
    gui_thread = threading.get_ident()

    def background_io(function, *arguments):
        future = io_pool.submit(function, *arguments)
        io_jobs.append(future)
        return future

    class DemoStore(Store):
        def secret(self, config, field="password"):
            # No access to an OS keychain, even if the app adds a hydration call.
            if config.id == MONGO_CONNECTION_ID and field == "password" and mongo is not None:
                return mongo.password
            return getattr(config, field)

        def record(self, config, sql, elapsed, status):
            background_io(Store.record, self, deepcopy(config), sql, elapsed, status)

        def flush(self):
            # MainWindow's close path requests a flush synchronously. Keep its
            # immutable snapshot on our serial I/O owner and wait before exit.
            proxy = object.__new__(Store)
            proxy.path, proxy.state = self.path, deepcopy(self.state)
            background_io(Store.flush, proxy)

    denied_keyring = ModuleType("keyring")
    def deny_keyring(*_args, **_kwargs):
        raise RuntimeError("The generated demo forbids credential-vault access.")
    for name in ("get_password", "set_password", "delete_password", "get_keyring"):
        setattr(denied_keyring, name, deny_keyring)
    sys.modules["keyring"] = denied_keyring
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("OpenNavicat")
    app.setOrganizationName("OpenNavicat")
    app.setApplicationVersion(__version__)
    translator = QTranslator(app)
    if translator.load(QLocale("zh_CN"), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app._qt_translator = translator
    apply_theme(app, args.theme)
    workspace = temporary / "workspace"
    def prepare_workspace():
        workspace.mkdir()
        return generated_sample(workspace)
    sample = background_io(prepare_workspace).result()
    store = background_io(DemoStore, workspace).result()
    config = ConnectionConfig(CONNECTION_ID, CONNECTION_NAME, database=str(sample))
    store.state["connections"] = [asdict(config)]
    if mongo_config is not None:
        fields = asdict(mongo_config)
        fields["password"] = ""  # Session hydration uses only our memory-only secret.
        store.state["connections"].append(fields)
    store.state["info_visible"] = True
    store.state["navicat_auto_discover"] = False
    class DemoWindow(MainWindow):
        def start_navicat_discovery(self, *_args, **_kwargs):
            raise RuntimeError("The generated demo forbids existing-profile discovery.")
    window = DemoWindow(store)
    window.resize(args.width, args.height)
    window.show()
    frames = temporary / "frames"
    background_io(frames.mkdir).result()
    state = {"recording": False, "count": 0, "error": None,
             "phases": [], "query": None, "designer": None, "shutdown": False,
             "mongo_page": None, "gridfs_page": None, "sqlite_closed_in_worker": False}
    deadline = time.monotonic() + 120
    capture_timer = QTimer(app)
    capture_timer.setInterval(round(1000 / args.fps))

    def idle():
        return not window.tasks and not window.pool.activeThreadCount()

    def fail(error):
        if state["error"] is not None:
            return
        text = str(error).replace(str(temporary), "<temporary-demo>")
        if mongo is not None:
            for value in (mongo.password, mongo.username, mongo.host, str(mongo.port), mongo.name, mongo.owner):
                text = text.replace(value, "<generated-demo-service>")
        state["error"] = text
        capture_timer.stop()
        shutdown()

    window.error = fail

    def capture():
        try:
            allowed_ids = {CONNECTION_ID} | ({MONGO_CONNECTION_ID} if mongo is not None else set())
            if set(window.configs) != allowed_ids or not set(window.sessions) <= allowed_ids or store.state.get("navicat_imports"):
                raise RuntimeError("The demo encountered a profile outside its generated workspace.")
            # This is a guard, not pixel redaction. A leaking frame is never saved.
            assert_visible_privacy(window,temporary,mongo)
            for job in io_jobs:
                if job.done() and job.exception() is not None:
                    raise RuntimeError("The generated demo background I/O failed.") from job.exception()
            pixmap = window.grab()
            image = pixmap.toImage().scaled(args.width, args.height,
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            image.setDevicePixelRatio(1)
            destination = frames / f"frame-{state['count']:05d}.png"
            def save_frame(frame, path):
                if threading.get_ident() == gui_thread or not frame.save(str(path)):
                    raise RuntimeError("Could not save an application frame on its I/O owner.")
            background_io(save_frame, image.copy(), destination)
            state["count"] += 1
        except Exception as error:
            fail(error)

    capture_timer.timeout.connect(capture)

    def phase(name):
        state["phases"].append({"name": name, "first_frame": state["count"]})

    def wait_for(predicate, callback, description):
        def poll():
            if state["error"] is not None or state["shutdown"]:
                return
            try:
                if time.monotonic() > deadline:
                    raise RuntimeError("Demo timed out while " + description)
                if predicate():
                    callback()
                else:
                    QTimer.singleShot(40, poll)
            except Exception as error:
                fail(error)
        poll()

    def hold(seconds, callback):
        def guarded():
            if state["error"] is None and not state["shutdown"]:
                try:
                    callback()
                except Exception as error:
                    fail(error)
        QTimer.singleShot(round(seconds * 1000), guarded)

    info = {"id": CONNECTION_ID, "schema": "main", "table": "customers", "kind": "table"}

    def objects_ready():
        page = window.object_page
        for row in range(page.table.rowCount()):
            if page.table.item(row, 0).text() == "customers":
                page.table.setCurrentCell(row, 0)
                break
        else:
            raise RuntimeError("The generated customers object is missing.")
        phase("objects")
        state["recording"] = True
        capture()
        capture_timer.start()
        hold(2.0, open_table)

    def open_table():
        window.open_table(info)
        wait_for(lambda: isinstance(window.tabs.currentWidget(), TablePage)
                 and window.tabs.currentWidget().model is not None
                 and not window.tabs.currentWidget().loading and idle(), table_ready,
                 "opening generated table data")

    def table_ready():
        if window.tabs.currentWidget().model.rowCount() != 6:
            raise RuntimeError("The generated table must contain six customers.")
        phase("table_data")
        hold(2.4, open_query)

    def open_query():
        page = window.new_query(sql="", connection_id=CONNECTION_ID)
        state["query"] = page
        phase("query_editor")
        position = 0

        def type_query():
            nonlocal position
            if state["error"] is not None:
                return
            page.editor.insertPlainText(QUERY[position:position + 4])
            position += 4
            if position < len(QUERY):
                QTimer.singleShot(22, type_query)
            else:
                hold(0.6, run_query)
        type_query()

    def run_query():
        page = state["query"]
        window.run_query(page)
        wait_for(lambda: not page.running and idle() and page.current_result() is not None,
                 query_ready, "executing the generated query")

    def query_ready():
        result = state["query"].current_result()
        if len(result.rows) != 6 or len(result.columns) != 4:
            raise RuntimeError("The demo query did not return six rows and four columns.")
        phase("query_results")
        hold(2.6, open_designer)

    def open_designer():
        # This exercises the integrated workbench action. Do not construct an
        # unconnected designer only to make an attractive screenshot.
        window.design_object(info)
        wait_for(lambda: isinstance(window.tabs.currentWidget(), TableDesignerPage)
                 and window.tabs.currentWidget().definition is not None and idle(),
                 designer_ready, "opening the integrated table designer")

    def designer_ready():
        page = window.tabs.currentWidget()
        state["designer"] = page
        page.tabs.setCurrentIndex(0)
        page.fields.selectRow(1)
        page.fields.setFocus()
        phase("table_designer")
        hold(2.0, preview_rename)

    def preview_rename():
        page = state["designer"]
        # This edits only a detached draft. The final frame shows SQL generated
        # by the real planner; the rename is deliberately not applied.
        page.set_table_name("customers_demo")
        page.tabs.setCurrentIndex(6)
        wait_for(lambda: idle() and "RENAME" in page.sql_preview.toPlainText().upper(),
                 preview_ready, "generating a real schema-change preview")

    def preview_ready():
        phase("designer_sql_preview")
        hold(3.0, open_mongodb if mongo is not None else finish)

    def open_mongodb():
        from opennavicat.mongodb_session import MongoSession

        capture_timer.stop()
        state["designer"].load_snapshot(state["designer"].snapshot)
        # A runtime assertion protects new app code from accidentally moving
        # native MongoDB operations onto the GUI thread during this recording.
        for name in ("connect", "close", "schemas", "objects", "fetch_documents", "list_files", "download_file"):
            original = getattr(MongoSession, name)
            def owned_io(*arguments, _original=original, **keywords):
                if threading.get_ident() == gui_thread:
                    raise RuntimeError("A demo MongoDB operation escaped its background owner.")
                return _original(*arguments, **keywords)
            setattr(MongoSession, name, owned_io)
        def connect():
            window.connect_id(MONGO_CONNECTION_ID)
            wait_for(lambda: MONGO_CONNECTION_ID in window.sessions and idle(), open_products,
                     "connecting the generated MongoDB fixture")
        wait_for(idle, connect, "discarding the owned SQLite preview draft")

    def open_products():
        from opennavicat.ui.mongo_browser import MongoCollectionPage

        window.set_active(MONGO_CONNECTION_ID, MONGO_DATABASE)
        sqlite_root = window.connection_item(CONNECTION_ID)
        if sqlite_root is not None:
            sqlite_root.setExpanded(False)
        for schema in window._schema_items(MONGO_CONNECTION_ID, MONGO_DATABASE):
            schema.setExpanded(True)
            window.tree_expand(schema)
        page = window.open_table({"id":MONGO_CONNECTION_ID, "schema":MONGO_DATABASE,
            "name":MONGO_COLLECTION, "table":MONGO_COLLECTION, "kind":"mongo_collection"})
        if not isinstance(page, MongoCollectionPage):
            raise RuntimeError("The demo did not open a real MongoDB collection workspace.")
        state["mongo_page"] = page
        wait_for(lambda: page.page is not None and not page.loading and idle(), products_ready,
                 "reading generated BSON products")

    def products_ready():
        from bson import Decimal128, Int64

        page = state["mongo_page"]
        if (len(page.page.documents) != 6 or not isinstance(page.page.documents[0].document["stock"],Int64)
                or not isinstance(page.page.documents[0].document["price"],Decimal128)):
            raise RuntimeError("The generated product page did not retain its exact BSON types.")
        phase("mongodb_collection_grid")
        capture()
        capture_timer.start()
        hold(2.2, product_tree)

    def product_tree():
        page = state["mongo_page"]
        page.set_view(1)
        stock = None
        for row in range(page.tree.topLevelItemCount()):
            child = page.tree.topLevelItem(row)
            if child.text(0) == "attributes":
                child.setExpanded(True)
            elif child.text(0) == "stock":
                page.tree.setCurrentItem(child)
                stock = child
        if (stock is None or stock.text(1) != str(page.selected_document().document["stock"])
                or stock.text(2) != "Int64"):
            raise RuntimeError("The current-document tree did not render its exact BSON integer and type.")
        phase("mongodb_typed_tree")
        hold(2.0, open_gridfs_objects)

    def open_gridfs_objects():
        window.show_objects("mongo_bucket")
        wait_for(lambda: idle() and any(entry["name"] == MONGO_BUCKET for entry in window.object_page.records),
                 open_gridfs, "reading the generated GridFS bucket")

    def open_gridfs():
        from opennavicat.ui.mongo_gridfs import MongoGridFSPage

        page = window.object_page
        for row, entry in enumerate(page.records):
            if entry["name"] == MONGO_BUCKET:
                page.table.setCurrentCell(row,0)
                page.open_selected()
                break
        current = window.tabs.currentWidget()
        if not isinstance(current,MongoGridFSPage):
            raise RuntimeError("The demo did not open the integrated GridFS workspace.")
        state["gridfs_page"] = current
        wait_for(lambda: current.page is not None and not current.loading and idle(), preview_gridfs,
                 "reading the generated file metadata")

    def preview_gridfs():
        page = state["gridfs_page"]
        if len(page.page.documents) != 1 or page.page.documents[0].document["filename"] != "generated-chart.png":
            raise RuntimeError("The GridFS demo encountered an unexpected file.")
        page.preview_action.setChecked(True)
        wait_for(lambda: page._preview_image is not None and not page.loading and idle(), gridfs_ready,
                 "decoding the actual uploaded GridFS image")

    def gridfs_ready():
        phase("mongodb_gridfs_preview")
        hold(3.0, finish)

    def finish():
        capture()
        capture_timer.stop()
        if state["error"] is not None:
            return
        def verify_preview():
            with closing(sqlite3.connect(sample)) as database:
                names = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "customers" not in names or "customers_demo" in names:
                    raise RuntimeError("The preview unexpectedly changed the demo database.")
        window.submit(verify_preview, lambda _result: shutdown(), label="验证生成的演示数据")

    def shutdown():
        if state["shutdown"]:
            return
        state["shutdown"] = True
        capture_timer.stop()
        started = time.monotonic()
        discarded = False
        forced_cleanup = False

        def poll():
            nonlocal discarded, forced_cleanup
            try:
                if not idle():
                    current = state.get("gridfs_page")
                    if state["error"] and current is not None and current.loading:
                        current.cancel_transfer()
                elif not window._closed:
                    if not discarded:
                        page = state.get("designer")
                        if page is not None and page.snapshot is not None:
                            page.load_snapshot(page.snapshot)
                        discarded = True
                    elif CONNECTION_ID in window.sessions:
                        session = window.sessions[CONNECTION_ID]
                        def closed(_value):
                            if window.sessions.get(CONNECTION_ID) is session:
                                window.sessions.pop(CONNECTION_ID)
                            state["sqlite_closed_in_worker"] = True
                        window.submit(session.close, closed, label="关闭生成的 SQLite 连接")
                    else:
                        window.close()
                if (window._closed and idle() and all(job.done() for job in io_jobs)):
                    if any(job.exception() is not None for job in io_jobs):
                        state["error"] = state["error"] or "The generated demo background I/O failed."
                    app.exit(1 if state["error"] else 0)
                    return
                if time.monotonic()-started > 60 and not forced_cleanup and mongo is not None:
                    # If a wire call stalls, stop only this exact owned service.
                    # Keep Qt alive until native tasks and window close finish.
                    state["error"] = state["error"] or "The demo timed out while closing its owned service."
                    forced_cleanup = True
                    background_io(mongo.cleanup)
            except Exception as error:
                state["error"] = state["error"] or "The generated demo shutdown failed: " + type(error).__name__
            QTimer.singleShot(40,poll)
        poll()

    def start():
        window.set_active(CONNECTION_ID)
        window.connect_id(CONNECTION_ID)
        wait_for(lambda: CONNECTION_ID in window.sessions and idle(), show_objects,
                 "connecting to the isolated SQLite fixture")

    def show_objects():
        window.show_objects("table")
        wait_for(lambda: idle() and window.object_page.table.rowCount() >= 3,
                 objects_ready, "loading generated object metadata")

    hold(0.1, start)
    result = app.exec()
    capture_timer.stop()
    if result or state["error"]:
        raise RuntimeError(state["error"] or "Demo application failed.")
    if not window._closed or not idle():
        raise RuntimeError("The demo exited before its native sessions and window had closed.")
    if state["count"] < 2 or len(state["phases"]) != (9 if mongo is not None else 6):
        raise RuntimeError("The demo did not capture all required workflows.")
    for index, item in enumerate(state["phases"]):
        stop = state["phases"][index + 1]["first_frame"] if index + 1 < len(state["phases"]) else state["count"]
        item["frames"] = stop - item["first_frame"]
    return frames, {
        "application": "OpenNavicat", "version": __version__, "theme": args.theme,
        "capture": "Actual live Qt QWidget.grab frames; no reference screenshots, composited UI or image generation service",
        "qt_platform": app.platformName(), "qt_version": QLibraryInfo.version().toString(),
        "size": [args.width, args.height], "fps": args.fps, "frames": state["count"],
        "duration_seconds": round(state["count"] / args.fps, 3), "loop": "infinite",
        "data": "All profiles, customer/product data, email addresses, BSON, dates, SQL and images are generated for this demo",
        "connection_name": CONNECTION_NAME, "database": "Temporary isolated SQLite fixture",
        "user_workspace_accessed": False, "keychain_accessed": False,
        "reference_application_accessed": False, "schema_preview_applied": False,
        "info_pane_visible": True, "designer_selected_field": "name",
        "query_result": {"rows": 6, "columns": 4}, "phases": state["phases"],
        "mongodb_included": mongo is not None,
        "mongodb_products": 6 if mongo is not None else 0,
        "mongodb_bson_types_checked": ["Int64","Decimal128"] if mongo is not None else [],
        "mongodb_gridfs_image_previewed": state["gridfs_page"] is not None,
        "visible_text_privacy_guard": "labels, editors, titles, actions, combos, tree/table display fields; paths/endpoints/credentials rejected before frame save",
        "sqlite_closed_in_worker": state["sqlite_closed_in_worker"],
        "native_window_closed_before_quit": bool(window._closed),
        "background_io_completed_before_quit": all(job.done() and job.exception() is None for job in io_jobs),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/opennavicat-demo.gif")
    parser.add_argument("--theme", choices=["dark", "light", "system"], default="dark")
    parser.add_argument("--platform", choices=["offscreen", "cocoa", "windows", "xcb"], default="offscreen")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--fps", type=int, choices=range(3, 11), default=6)
    parser.add_argument("--include-mongodb", action="store_true",
        help="Add actual generated BSON and GridFS workflows using a new owned Docker service")
    parser.add_argument("--check-dependencies", action="store_true")
    args = parser.parse_args()
    ffmpeg, version = dependencies(args.include_mongodb)
    if args.check_dependencies:
        print(json.dumps({"ffmpeg": version, "palette_filters": True, "Pillow_required": False,
            "mongodb_requested":args.include_mongodb,"docker_available":shutil.which("docker") is not None}))
        return 0
    if args.width < 950 or args.height < 620:
        parser.error("The application needs at least 950 by 620 logical pixels.")
    if args.output.suffix.lower() != ".gif":
        parser.error("The demo output must be a .gif file.")
    if args.include_mongodb and not any(value == "--output" or value.startswith("--output=") for value in sys.argv[1:]):
        parser.error("A MongoDB capture requires an explicit --output; use an ignored development artifact before publishing.")
    os.environ["QT_QPA_PLATFORM"] = args.platform
    sys.path.insert(0, str(ROOT))
    # Qt's first application/thread identity belongs to the main thread, before
    # the fixture owner constructs a QImage for the synthetic GridFS upload.
    from PySide6.QtWidgets import QApplication
    application = QApplication(sys.argv[:1])
    mongo = OwnedMongoDemo() if args.include_mongodb else None
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix="demo-io") as io_pool:
        with tempfile.TemporaryDirectory(prefix="opennavicat-readme-demo-") as directory:
            temporary = Path(directory)
            try:
                mongo_config = io_pool.submit(mongo.start).result() if mongo is not None else None
                frames, receipt = record(args, temporary, io_pool, mongo, mongo_config)
                generated = temporary / "demo.gif"
                io_pool.submit(encode,ffmpeg,frames,args.fps,generated).result()
                io_pool.submit(verify_gif,generated,receipt).result()
                if mongo is not None:
                    io_pool.submit(mongo.cleanup).result()
                    receipt["mongodb_service"] = mongo.receipt()
                def publish_generated():
                    args.output.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(generated,args.output)
                    receipt["encoder"] = version
                    receipt["gif_bytes"] = args.output.stat().st_size
                    receipt["gif_sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
                io_pool.submit(publish_generated).result()
            finally:
                if mongo is not None:
                    io_pool.submit(mongo.cleanup).result()
        receipt["temporary_workspace_removed"] = not temporary.exists()
        receipt["temporary_frame_files_removed"] = not temporary.exists()
        text = json.dumps(receipt,ensure_ascii=False,indent=2)+"\n"
        forbidden = [str(ROOT),directory,str(Path.home())]
        if mongo is not None:
            forbidden.extend([mongo.password,mongo.username,mongo.host,mongo.name,mongo.owner])
        if any(value in text for value in forbidden):
            raise RuntimeError("The public demo receipt contains a local path or generated service identity.")
        io_pool.submit(args.output.with_suffix(".json").write_text,text,"utf-8").result()
    print(json.dumps({"recording": "passed", "file": args.output.name,
                      "frames": receipt["frames"], "bytes": receipt["gif_bytes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
