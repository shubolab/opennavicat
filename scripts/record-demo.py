"""Record one GIF of live OpenNavicat widgets using an isolated generated fixture.

Example: .venv/bin/python scripts/record-demo.py
The default offscreen Qt platform avoids taking focus from the user's apps.
This records actual application rendering; it never reads Navicat or an existing
OpenNavicat workspace. Only the final GIF and a path-free JSON receipt survive.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
CONNECTION_NAME = "演示数据库 · 示例数据"
CONNECTION_ID = "readme-generated-demo"
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


def dependencies():
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
    return executable, version


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


def record(args, temporary):
    from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTimer, QTranslator
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

    from opennavicat import __version__
    from opennavicat.database import ConnectionConfig
    from opennavicat.storage import Store
    from opennavicat.ui.table_designer import TableDesignerPage
    from opennavicat.ui.theme import apply_theme
    from opennavicat.ui.window import MainWindow, TablePage

    class DemoStore(Store):
        def secret(self, config, field="password"):
            # No access to an OS keychain, even if the app adds a hydration call.
            return getattr(config, field)

    app = QApplication(sys.argv[:1])
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
    workspace.mkdir()
    store = DemoStore(workspace)
    sample = generated_sample(workspace)
    config = ConnectionConfig(CONNECTION_ID, CONNECTION_NAME, database=str(sample))
    store.state["connections"] = [asdict(config)]
    store.state["info_visible"] = True
    window = MainWindow(store)
    window.resize(args.width, args.height)
    window.show()
    frames = temporary / "frames"
    frames.mkdir()
    state = {"recording": False, "count": 0, "error": None,
             "phases": [], "query": None, "designer": None}
    deadline = time.monotonic() + 90
    capture_timer = QTimer(app)
    capture_timer.setInterval(round(1000 / args.fps))

    def idle():
        return not window.tasks and not window.pool.activeThreadCount()

    def fail(error):
        if state["error"] is not None:
            return
        state["error"] = str(error).replace(str(temporary), "<temporary-demo>")
        capture_timer.stop()
        app.exit(1)

    window.error = fail

    def capture():
        try:
            # Visible labels/editors must not disclose a workstation path. This
            # is a guard, not redaction: a leaking frame fails the recording.
            widgets = [widget for cls in (QLabel, QLineEdit, QPlainTextEdit)
                       for widget in window.findChildren(cls)]
            for widget in widgets:
                if not widget.isVisible():
                    continue
                text = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
                if (any(value in text for value in (str(temporary), str(ROOT), str(Path.home())))
                        or re.search(r"/(?:Users|home|private|tmp|var/folders|Volumes)/", text)):
                    raise RuntimeError("A visible demo widget contains a local filesystem path.")
            pixmap = window.grab()
            image = pixmap.toImage().scaled(args.width, args.height,
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            image.setDevicePixelRatio(1)
            destination = frames / f"frame-{state['count']:05d}.png"
            if not image.save(str(destination)):
                raise RuntimeError("Could not capture an application frame.")
            state["count"] += 1
        except Exception as error:
            fail(error)

    capture_timer.timeout.connect(capture)

    def phase(name):
        state["phases"].append({"name": name, "first_frame": state["count"]})

    def wait_for(predicate, callback, description):
        def poll():
            if state["error"] is not None:
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
            if state["error"] is None:
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
        hold(3.0, finish)

    def finish():
        capture()
        capture_timer.stop()
        with closing(sqlite3.connect(sample)) as database:
            names = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "customers" not in names or "customers_demo" in names:
                raise RuntimeError("The preview unexpectedly changed the demo database.")
        page = state["designer"]
        page.load_snapshot(page.snapshot)  # Discard this owned preview draft.
        window.close()
        app.exit(0)

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
    if state["count"] < 2 or len(state["phases"]) != 6:
        raise RuntimeError("The demo did not capture all required workflows.")
    for index, item in enumerate(state["phases"]):
        stop = state["phases"][index + 1]["first_frame"] if index + 1 < len(state["phases"]) else state["count"]
        item["frames"] = stop - item["first_frame"]
    return frames, {
        "application": "OpenNavicat", "version": __version__, "theme": args.theme,
        "capture": "Actual live Qt QWidget.grab frames; no generated artwork or reference screenshots",
        "qt_platform": app.platformName(), "qt_version": QLibraryInfo.version().toString(),
        "size": [args.width, args.height], "fps": args.fps, "frames": state["count"],
        "duration_seconds": round(state["count"] / args.fps, 3), "loop": "infinite",
        "data": "All connection names, customer data, email addresses, dates and SQL generated for this demo",
        "connection_name": CONNECTION_NAME, "database": "Temporary isolated SQLite fixture",
        "user_workspace_accessed": False, "keychain_accessed": False,
        "reference_application_accessed": False, "schema_preview_applied": False,
        "info_pane_visible": True, "designer_selected_field": "name",
        "query_result": {"rows": 6, "columns": 4}, "phases": state["phases"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/opennavicat-demo.gif")
    parser.add_argument("--theme", choices=["dark", "light", "system"], default="dark")
    parser.add_argument("--platform", choices=["offscreen", "cocoa", "windows", "xcb"], default="offscreen")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--fps", type=int, choices=range(3, 11), default=6)
    parser.add_argument("--check-dependencies", action="store_true")
    args = parser.parse_args()
    ffmpeg, version = dependencies()
    if args.check_dependencies:
        print(json.dumps({"ffmpeg": version, "palette_filters": True, "Pillow_required": False}))
        return 0
    if args.width < 950 or args.height < 620:
        parser.error("The application needs at least 950 by 620 logical pixels.")
    if args.output.suffix.lower() != ".gif":
        parser.error("The demo output must be a .gif file.")
    os.environ["QT_QPA_PLATFORM"] = args.platform
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="opennavicat-readme-demo-") as directory:
        temporary = Path(directory)
        frames, receipt = record(args, temporary)
        generated = temporary / "demo.gif"
        encode(ffmpeg, frames, args.fps, generated)
        verify_gif(generated, receipt)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(generated, args.output)
        receipt["encoder"] = version
        receipt["gif_bytes"] = args.output.stat().st_size
        receipt["gif_sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    receipt["temporary_workspace_removed"] = not temporary.exists()
    receipt["temporary_frame_files_removed"] = not temporary.exists()
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if any(value in text for value in (str(ROOT), directory, str(Path.home()))):
        raise RuntimeError("The public demo receipt contains a local filesystem path.")
    args.output.with_suffix(".json").write_text(text, "utf-8")
    print(json.dumps({"recording": "passed", "file": args.output.name,
                      "frames": receipt["frames"], "bytes": receipt["gif_bytes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
