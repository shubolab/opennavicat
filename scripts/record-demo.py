"""Record one GIF of live OpenNavicat widgets using an isolated generated fixture.

Example: .venv/bin/python scripts/record-demo.py
The default offscreen Qt platform avoids taking focus from the user's apps.
This records actual application rendering; it never reads Navicat or an existing
OpenNavicat workspace. Only the final GIF and a path-free JSON receipt survive.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
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
MONGO_STORAGE_COLLECTION = "generated_storage"
MONGO_BATCH_PIPELINE_NAME = "生成聚合管道"
MONGO_BATCH_NAME = "生成批处理作业"
MONGO_STREAM_COLLECTION = "generated_stream_events"
MONGO_IMAGE = "mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142"
MONGO_STORAGE_TEXT = '{ wiredTiger: { configString: "block_compressor=zlib" } }'
MONGO_INDEX_DEFAULTS_TEXT = '{ storageEngine: { wiredTiger: { configString: "prefix_compression=false" } } }'
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
MONGO_QUERY = '''// 此数据库与商品均为程序生成的示例数据
const products = db.getCollection("products").find({}).sort({sku: 1}).limit(3).toArray();
function sampleRow(product) {
    return {sku: product.sku, stock: product.stock, price: product.price};
}
products.map(sampleRow);'''


def mongo_storage_options():
    return {
        "storageEngine": {"wiredTiger": {"configString": "block_compressor=zlib"}},
        "indexOptionDefaults": {"storageEngine": {"wiredTiger": {"configString": "prefix_compression=false"}}},
    }


def mongo_collection_options():
    return {
        "clusteredIndex": {"key": {"_id": 1}, "name": "generated_cluster", "unique": True},
        "expireAfterSeconds": 86400,
        "changeStreamPreAndPostImages": {"enabled": True},
    }


def mongo_demo_collation():
    return {
        "locale": "en_US", "caseLevel": True, "caseFirst": "upper", "strength": 2,
        "numericOrdering": True, "alternate": "shifted", "maxVariable": "space",
        "backwards": True, "normalization": True,
    }


def verify_mongo_storage_values(catalog, statistics, document_count):
    """Check independent native-server facts, never a UI snapshot or echo."""
    from bson import BSON
    from opennavicat.mongodb_session import BSON_CODEC

    expected = mongo_storage_options()
    selected_options = mongo_collection_options()
    selected_collation = mongo_demo_collation()
    try:
        actual_options = catalog["options"]
        actual_storage = {key: actual_options[key] for key in expected}
        actual_cluster = actual_options["clusteredIndex"]
        actual_collation = actual_options["collation"]
        cluster_name = actual_cluster.get("name", "_id_")
        index_detail = statistics["indexDetails"].get(cluster_name, {})
        storage_matches = BSON.encode(
            actual_storage, codec_options=BSON_CODEC) == BSON.encode(expected, codec_options=BSON_CODEC)
        cluster_matches = (
            all(actual_cluster.get(key) == value
                for key, value in selected_options["clusteredIndex"].items())
            and index_detail.get("clustered") is True
            and index_detail.get("name") == cluster_name)
        plain_index_creation = index_detail.get("creationString", "")
        default_index_storage_verified = "prefix_compression=false" in plain_index_creation
        matches = (catalog["name"] == MONGO_STORAGE_COLLECTION and catalog["type"] == "collection"
            and storage_matches and cluster_matches
            and actual_options.get("expireAfterSeconds") == selected_options["expireAfterSeconds"]
            and actual_options.get("changeStreamPreAndPostImages")
                == selected_options["changeStreamPreAndPostImages"]
            and all(actual_collation.get(key) == value for key, value in selected_collation.items())
            and isinstance(actual_collation.get("version"), str) and bool(actual_collation["version"])
            and type(document_count) is int and document_count == 0
            and "block_compressor=zlib" in statistics["wiredTiger"]["creationString"])
    except Exception:
        matches = False
    if not matches:
        raise RuntimeError("The generated collection storage, options or collation was not independently verified.")
    return {"collection": MONGO_STORAGE_COLLECTION, "options_verified": True,
        "empty_collection_verified": True, "collection_storage_verified": True,
        "index_option_defaults_verified": actual_storage.get("indexOptionDefaults") == expected["indexOptionDefaults"],
        "default_index_storage_verified": default_index_storage_verified,
        "default_index_storage_observation": (
            "plain_index_creation_string" if default_index_storage_verified
            else "clustered_index_exposes_no_creation_string"),
        "clustered_index_metadata_verified": cluster_matches,
        "collection_options_verified": True,
        "collection_options_selected": selected_options,
        "collation_verified": True, "collation_selected": selected_collation}

def verify_mongo_query_values(tree, rows):
    from bson import BSON, Decimal128, Int64
    from opennavicat.js_engine.codec import BSON_CODEC, decode
    from opennavicat.mongodb_session import canonical_loads

    expected = [{"sku": f"DEMO-{number:03}", "stock": Int64(9007199254740993 + number),
                 "price": Decimal128(price)} for number, price in enumerate(("1299.95", "349.50", "79.00"), 1)]
    def raw(values):
        return BSON.encode({"values": values}, codec_options=BSON_CODEC)
    if (raw(decode(tree)) != raw(expected) or len(rows) != 3
            or any(len(row) != 2 or row[0] != "Canonical Extended JSON" for row in rows)
            or raw([canonical_loads(row[1]) for row in rows]) != raw(expected)):
        raise RuntimeError("The JavaScript demo results did not preserve the exact generated BSON values.")


def read_mongo_batch_source(database):
    """Fingerprint only the generated source with native reads on its owner."""
    from bson import BSON, Binary
    from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC

    if threading.get_ident() == threading.main_thread().ident:
        raise RuntimeError("Independent demo batch verification needs its background owner.")
    with database.list_collections(filter={"name": MONGO_COLLECTION}) as cursor:
        catalogs = []
        for record in cursor:
            if catalogs:
                raise RuntimeError("The generated batch source catalog is ambiguous.")
            catalogs.append(record)
    if len(catalogs) != 1:
        raise RuntimeError("The generated batch source collection is missing.")
    identity = catalogs[0].get("info", {}).get("uuid")
    if not isinstance(identity, Binary) or identity.subtype != 4 or len(identity) != 16:
        raise RuntimeError("The generated batch source has no exact collection identity.")
    digest, count, size = hashlib.sha256(), 0, 0
    cursor = database.get_collection(MONGO_COLLECTION, codec_options=RAW_CODEC).find({}).sort("_id", 1).limit(7)
    try:
        for document in cursor:
            raw = document.raw
            size += len(raw)
            if count >= 6 or size > 1024 * 1024:
                raise RuntimeError("The generated batch source exceeded its six-row/1MiB verification bound.")
            digest.update(len(raw).to_bytes(4, "big")); digest.update(raw)
            count += 1
    finally:
        cursor.close()
    if count != 6:
        raise RuntimeError("The generated batch source does not contain the six sample documents.")
    return {"documents": count, "document_bson_sha256": digest.hexdigest(),
            "source_uuid_bson": BSON.encode({"uuid": identity}, codec_options=BSON_CODEC)}


def verify_mongo_batch_values(result, history, before, after):
    """Require actual run/history facts and independent source readback."""
    import math

    try:
        counts = {"total": 2, "success": 2, "failed": 0, "skipped": 0, "cancelled": 0}
        logs = result["logs"]
        started = datetime.fromisoformat(result["start_time"])
        ended = datetime.fromisoformat(result["end_time"])
        duration = result["duration_ms"]
        matches = (result["status"] == "success" and result["execute"] == "Manual"
            and type(result["execution_id"]) is int and result["execution_id"] == 1
            and all(type(result[key]) is int and result[key] == expected for key, expected in counts.items())
            and type(result.get("event_delivery_failures")) is int and result["event_delivery_failures"] == 0
            and type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
            and started.tzinfo is not None and ended.tzinfo is not None and ended >= started
            and type(logs) is list
            and [(event["status"], event["job_index"]) for event in logs]
                == [("running", 0), ("success", 0), ("running", 1), ("success", 1)]
            and all(event["name"] == MONGO_BATCH_PIPELINE_NAME for event in logs)
            and all(event.get("execution_completed") is True and event.get("cancel_requested") is False
                    for event in logs if event["status"] == "success")
            and type(history) is list and len(history) == 1 and history[0] == result
            and before == after and before["documents"] == 6
            and isinstance(before["source_uuid_bson"], bytes) and bool(before["source_uuid_bson"])
            and re.fullmatch(r"[0-9a-f]{64}", before["document_bson_sha256"]) is not None)
    except Exception:
        matches = False
    if not matches:
        raise RuntimeError("The generated Batch Job execution, persisted history or exact source BSON was not verified.")
    return {"tasks": 2, "success": 2, "failed": 0, "skipped": 0, "cancelled": 0,
            "execution_id": 1, "execute": "Manual", "ordered_duplicate_references_verified": True,
            "history_persisted": True, "source_documents": 6,
            "source_exact_bson_unchanged": True, "source_collection_identity_unchanged": True}


def _batch_history_snapshot(store):
    from opennavicat.storage import Store

    proxy = object.__new__(Store)
    proxy.state, proxy.history_path = deepcopy(store.state), store.history_path
    return proxy


def mongo_stream_documents():
    from bson import Binary, Decimal128, Int64, ObjectId, Timestamp
    return [{"_id": ObjectId(f"{number:024x}"), "generated": True, "sequence": number,
             "stock": Int64(9007199254740993 + number), "price": Decimal128(price),
             "payload": Binary(bytes([number, 0, 255]), 128), "at": Timestamp(1700000000 + number, number)}
            for number, price in ((1, "1.25"), (2, "2.50"))]


def verify_mongo_stream_values(result, production, readback, before, after):
    """Private typed events must match actual independent source readback."""
    from bson import BSON
    from opennavicat.mongodb_pipeline_stream import FiniteStreamResult
    from opennavicat.mongodb_session import BSON_CODEC, canonical_loads
    try:
        expected = mongo_stream_documents()
        if (not isinstance(result, FiniteStreamResult) or result.completion != "empty_batch"
                or result.undelivered_event_count or len(result.events) != 2
                or production.get("exact_getmore_observed") is not True
                or production.get("inserted") != 2 or readback.get("marker_absent") is not True
                or before["documents"] != 6 or after["documents"] != 6
                or before["document_bson_sha256"] != after["document_bson_sha256"]
                or before["source_uuid_bson"] != after["source_uuid_bson"]
                or before["stream_uuid_bson"] != readback["stream_uuid_bson"]
                or list(readback["documents_raw"]) != [BSON.encode(doc, codec_options=BSON_CODEC) for doc in expected]):
            raise ValueError
        for event, document in zip(result.events, expected):
            decoded = canonical_loads(event.canonical_json)
            if (BSON.encode(decoded, codec_options=BSON_CODEC) != event.raw_bson
                    or decoded.get("operationType") != "insert"
                    or decoded.get("ns") != {"db": MONGO_DATABASE, "coll": MONGO_STREAM_COLLECTION}
                    or BSON.encode(decoded.get("fullDocument"), codec_options=BSON_CODEC)
                       != BSON.encode(document, codec_options=BSON_CODEC)
                    or BSON.encode(decoded.get("documentKey"), codec_options=BSON_CODEC)
                       != BSON.encode({"_id": document["_id"]}, codec_options=BSON_CODEC)):
                raise ValueError
    except Exception:
        raise RuntimeError("The generated finite stream's exact BSON, readback or source identity was not verified.") from None
    return {"events": 2, "completion": "empty_batch", "raw_bson_and_canonical_verified": True,
            "typed_full_documents_independently_read_back": True, "exact_marker_getmore_observed": True,
            "cursor_cleanup_verified": True, "source_exact_bson_unchanged": True,
            "source_collection_identity_unchanged": True, "stream_collection_identity_unchanged": True,
            "bson_types_checked": ["Int64", "Decimal128", "Binary", "Timestamp", "ObjectId"],
            "automatic_resume_used": False, "event_payload_rendered": False}


def verify_mongo_stream_page(page, *, busy=False, output=False):
    """Inspect real widgets; private typed events must never become a grid."""
    tabs = [page.tabs.tabText(index) for index in range(page.tabs.count()) if page.tabs.isTabVisible(index)]
    expected = ["管道", "脚本预览"] if busy else ["管道", "脚本预览", "消息", "输出"]
    if tabs != expected or page.last_rows or any(table.rowCount() for table in
            (page.results, page.input_panel.table, page.output_panel.table)) or page.export_action.isEnabled():
        raise RuntimeError("The generated finite stream did not expose its actual text-only tabs.")
    if busy:
        if (not page.running or not page._busy_overlay.isVisible()
                or not page._busy_spinner.timer.isActive() or page.operator_combo.isEnabled()
                or not page.stop_action.isEnabled() or page.tabs.currentWidget() is not page.pipeline_tab):
            raise RuntimeError("The actual finite stream busy overlay was not visible.")
    else:
        text = page.messages.toPlainText()
        if (page.running or "> OK\n" not in text or "> 查询时间:" not in text
                or text != page.stream_output.toPlainText()
                or page.tabs.currentWidget() is not (page.stream_output if output else page.messages)):
            raise RuntimeError("The actual finite stream completion text was not displayed.")


def _batch_history_io(store, job_id, result=None):
    """The recorder's local history SQLite stays on the existing I/O owner."""
    from opennavicat.storage import Store

    if threading.get_ident() == threading.main_thread().ident:
        raise RuntimeError("Generated demo batch history needs its background owner.")
    try:
        return (Store.batch_job_history(store, job_id) if result is None
                else Store.record_batch_job_run(store, job_id, result))
    except Exception:
        raise OSError("The generated demo batch history I/O failed.") from None


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

    def __init__(self, *, replica_set=False):
        self.owner = uuid.uuid4().hex
        self.name = "opennavicat-readme-demo-" + self.owner[:16]
        self.container_id = None
        self.password = secrets.token_urlsafe(32)
        self.username = "readme_fixture"
        self.host = "127.0.0.1"
        self.port = None
        self.server_version = self.architecture = ""
        self.identity_verified = self.removed = False
        self.replica_set = bool(replica_set)
        self.replica_key = secrets.token_hex(48) if self.replica_set else ""
        self.volumes = []
        self.volumes_removed = False

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
            if missing and any(message in result.stderr.lower() for message in ("no such object", "no such container", "no such volume")):
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
                or self.replica_set and labels.get("opennavicat.replica-set") != "generated_demo_rs"
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

        arguments = ["run", "--detach", "--rm", "--name", self.name,
            "--label", "opennavicat.test=true", "--label", "opennavicat.task=readme-demo",
            "--label", "opennavicat.owner=" + self.owner, "--publish", "127.0.0.1::27017",
            "-e", "MONGO_INITDB_ROOT_USERNAME=" + self.username,
            "-e", "MONGO_INITDB_ROOT_PASSWORD=" + self.password]
        if self.replica_set:
            script = ('umask 077; printf "%s" "$OWNED_REPLICA_KEY" > /data/generated-rs.key; '
                      'chown mongodb:mongodb /data/generated-rs.key; '
                      'exec /usr/local/bin/docker-entrypoint.sh mongod --bind_ip_all '
                      '--replSet generated_demo_rs --keyFile /data/generated-rs.key')
            arguments += ["--label", "opennavicat.replica-set=generated_demo_rs",
                          "-e", "OWNED_REPLICA_KEY=" + self.replica_key,
                          "--entrypoint", "/bin/bash", MONGO_IMAGE, "-c", script]
        else:
            arguments += [MONGO_IMAGE, "--bind_ip_all"]
        identifier = self._docker(*arguments)
        self.container_id = identifier
        entry = self.inspect()
        self.volumes = [mount["Name"] for mount in entry.get("Mounts", []) if mount["Type"] == "volume"]
        mappings = entry.get("NetworkSettings", {}).get("Ports", {}).get("27017/tcp") or []
        if len(mappings) != 1 or mappings[0].get("HostIp") != "127.0.0.1":
            raise RuntimeError("The MongoDB demo service must expose exactly one loopback-only port.")
        self.port = int(mappings[0]["HostPort"])
        config = ConnectionConfig(MONGO_CONNECTION_ID, MONGO_CONNECTION_NAME, driver="mongodb",
            database=MONGO_DATABASE, host=self.host, port=self.port, username=self.username,
            password=self.password, ssl_mode="disable", connect_timeout=3,
            mongo_auth_source="admin", mongo_auth_mechanism="SCRAM-SHA-256")
        deadline = time.monotonic()+readiness_seconds
        replica_initiated = False
        while True:
            session = MongoSession(config)
            try:
                with mongo_timeout(remaining_seconds(deadline)):
                    session.connect()
                    if self.replica_set:
                        if not replica_initiated:
                            session.connection.admin.command({"replSetInitiate": {"_id": "generated_demo_rs",
                                "members": [{"_id": 0, "host": "localhost:27017"}]}})
                            replica_initiated = True
                        if not session.connection.admin.command("hello").get("isWritablePrimary"):
                            raise RuntimeError("The generated replica set has not elected its primary.")
                    self.server_version = str(session.connection.server_info()["version"])
                    if self.replica_set and self.server_version != "8.0.34":
                        raise RuntimeError("The generated replica set has the wrong MongoDB version.")
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
        collection.create_index([("stock", -1), ("sku", 1)], name="demo_stock_sku")
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
            host = entry.get("HostConfig", {})
            volumes = [mount["Name"] for mount in entry.get("Mounts", []) if mount["Type"] == "volume"]
            if (host.get("Binds") or host.get("Mounts") or host.get("VolumesFrom")
                    or any(not re.fullmatch("[0-9a-f]{64}", name) for name in volumes)
                    or self.volumes and set(self.volumes) != set(volumes)):
                raise RuntimeError("The demo refused cleanup of unexpected or explicit storage.")
            self.volumes = volumes
            for name in self.volumes:
                users = self._docker("ps", "-a", "--no-trunc", "--filter", "volume=" + name, "--format", "{{.ID}}").split()
                if users != [self.container_id]:
                    raise RuntimeError("The demo refused cleanup of shared storage.")
            self._docker("rm", "--force", "--volumes", self.container_id)
            if self.inspect(missing=True) is not None:
                raise RuntimeError("The exact owned MongoDB container still exists after cleanup.")
        if any(self._docker("volume", "inspect", name, missing=True) is not None for name in self.volumes):
            raise RuntimeError("An exact owned MongoDB anonymous volume remains after cleanup.")
        self.volumes_removed = True
        self.removed = True

    def private_values(self):
        return tuple(value for value in (self.password, self.username, self.host, str(self.port) if self.port is not None else "",
                     self.name, self.owner, self.replica_key) if value)

    @contextmanager
    def _stream_client(self):
        from pymongo import MongoClient, timeout as mongo_timeout
        if threading.get_ident() == threading.main_thread().ident:
            raise RuntimeError("Independent generated stream I/O needs its background owner.")
        if not self.replica_set:
            raise RuntimeError("The generated stream requires its owned replica-set mode.")
        self.inspect()
        try:
            with MongoClient(host=self.host, port=self.port, username=self.username,
                    password=self.password, authSource="admin", authMechanism="SCRAM-SHA-256",
                    directConnection=True, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000) as client:
                with mongo_timeout(30):
                    yield client
            self.inspect()
        except Exception:
            raise RuntimeError("Independent generated finite-stream I/O failed.") from None

    @staticmethod
    def _stream_uuid(database):
        from bson import BSON
        from opennavicat.mongodb_session import BSON_CODEC
        with database.list_collections(filter={"name": MONGO_STREAM_COLLECTION}) as cursor:
            rows = list(cursor)
        if len(rows) != 1 or rows[0].get("info", {}).get("uuid") is None:
            raise RuntimeError("The generated stream collection identity is missing.")
        return BSON.encode({"uuid": rows[0]["info"]["uuid"]}, codec_options=BSON_CODEC)

    def prepare_stream(self):
        from opennavicat.mongodb_session import BSON_CODEC
        with self._stream_client() as client:
            database = client.get_database(MONGO_DATABASE, codec_options=BSON_CODEC)
            if MONGO_STREAM_COLLECTION in database.list_collection_names():
                raise RuntimeError("The generated stream collection must be new.")
            database.create_collection(MONGO_STREAM_COLLECTION)
            before = read_mongo_batch_source(database)
            before["stream_uuid_bson"] = self._stream_uuid(database)
        return before

    def produce_stream_events(self, session, comment, armed, active, release, abort):
        with self._stream_client() as client:
            client.admin.command("ping")
            armed.set()
            deadline, marker = time.monotonic() + 15, None
            while time.monotonic() < deadline and not abort.is_set():
                candidate = session.cancellation_marker()
                if isinstance(candidate, dict) and candidate.get("userComment") == comment:
                    operations = list(client.admin.aggregate([{"$currentOp": {"allUsers": False}},
                        {"$match": {"active": True, "ns": MONGO_DATABASE + "." + MONGO_STREAM_COLLECTION,
                            "command.getMore": {"$exists": True}, "command.comment": candidate}}]))
                    if operations:
                        marker = candidate
                        break
                time.sleep(.01)
            if marker is None:
                raise RuntimeError("The actual generated finite-stream getMore was not observed.")
            active.set()
            while not release.wait(.01):
                if abort.is_set() or time.monotonic() > deadline:
                    raise RuntimeError("The generated stream producer was cancelled before insertion.")
            if abort.is_set():
                raise RuntimeError("The generated stream producer was cancelled before insertion.")
            if session.cancellation_marker() != marker or not list(client.admin.aggregate([
                    {"$currentOp": {"allUsers": False}}, {"$match": {"active": True,
                        "ns": MONGO_DATABASE + "." + MONGO_STREAM_COLLECTION,
                        "command.getMore": {"$exists": True}, "command.comment": marker}}])):
                raise RuntimeError("The generated finite-stream getMore ended before event delivery.")
            client[MONGO_DATABASE][MONGO_STREAM_COLLECTION].insert_many(mongo_stream_documents())
            return {"marker": marker, "exact_getmore_observed": True, "inserted": 2}

    def stream_readback(self, marker):
        from opennavicat.mongodb_session import BSON_CODEC, RAW_CODEC
        with self._stream_client() as client:
            database = client.get_database(MONGO_DATABASE, codec_options=BSON_CODEC)
            with database.get_collection(MONGO_STREAM_COLLECTION, codec_options=RAW_CODEC).find({}).sort("_id", 1).limit(3) as cursor:
                documents = tuple(document.raw for document in cursor)
            operations = list(client.admin.aggregate([{"$currentOp": {"allUsers": False, "idleCursors": True}},
                {"$match": {"$or": [{"command.comment": marker}, {"originatingCommand.comment": marker},
                                     {"cursor.originatingCommand.comment": marker}]}}]))
            readback = {"documents_raw": documents, "marker_absent": not operations,
                        "stream_uuid_bson": self._stream_uuid(database)}
            after = read_mongo_batch_source(database)
        return readback, after

    def verify_storage_collection(self):
        """Read only this run's new collection using a separate native client."""
        from pymongo import MongoClient, timeout as mongo_timeout
        from opennavicat.mongodb_session import BSON_CODEC

        if threading.get_ident() == threading.main_thread().ident:
            raise RuntimeError("Independent demo storage verification needs its background owner.")
        self.inspect()
        try:
            with MongoClient(host=self.host, port=self.port, username=self.username,
                    password=self.password, authSource="admin", directConnection=True,
                    serverSelectionTimeoutMS=3000, connectTimeoutMS=3000) as client:
                with mongo_timeout(8):
                    database = client.get_database(MONGO_DATABASE, codec_options=BSON_CODEC)
                    with database.list_collections(filter={"name": MONGO_STORAGE_COLLECTION}) as cursor:
                        records = []
                        for row in cursor:
                            if records:
                                raise RuntimeError("The generated demo collection catalog is ambiguous.")
                            records.append(row)
                    if len(records) != 1:
                        raise RuntimeError("The generated demo collection is missing.")
                    statistics = database.command({"collStats": MONGO_STORAGE_COLLECTION})
                    count = database[MONGO_STORAGE_COLLECTION].count_documents({}, limit=1)
                    verification = verify_mongo_storage_values(records[0], statistics, count)
            self.inspect()
            return verification
        except Exception:
            # Native exception text can include the private loopback endpoint.
            raise RuntimeError("Independent generated collection storage verification failed.") from None

    def batch_source_snapshot(self):
        """Use a new native client, separate from the window and Batch sessions."""
        from pymongo import MongoClient, timeout as mongo_timeout
        from opennavicat.mongodb_session import BSON_CODEC

        if threading.get_ident() == threading.main_thread().ident:
            raise RuntimeError("Independent demo batch verification needs its background owner.")
        self.inspect()
        try:
            with MongoClient(host=self.host, port=self.port, username=self.username,
                    password=self.password, authSource="admin", directConnection=True,
                    serverSelectionTimeoutMS=3000, connectTimeoutMS=3000) as client:
                with mongo_timeout(8):
                    snapshot = read_mongo_batch_source(client.get_database(MONGO_DATABASE, codec_options=BSON_CODEC))
            self.inspect()
            return snapshot
        except Exception:
            raise RuntimeError("Independent generated batch source verification failed.") from None

    def receipt(self):
        return {"image": MONGO_IMAGE, "server_version": self.server_version,
            "image_architecture": self.architecture, "loopback_only": True,
            "exact_container_identity_verified": self.identity_verified,
            "owned_container_removed": self.removed, "anonymous_volumes_removed": self.volumes_removed,
            "replica_set": self.replica_set, "authentication": "SCRAM-SHA-256",
            "credentials_generated_in_memory": True}


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
                or (mongo is not None and (any(value in text for value in mongo.private_values())
                    or re.search(r"(?<!\w)"+str(mongo.port)+r"(?!\w)",text)))):
            raise RuntimeError("A visible demo widget contains a service endpoint or credential field.")


def fit_capture_image(source, width, height, *, save_sheet=False):
    """Keep the actual modal's proportions on a plain neutral GIF canvas."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    if source.isNull():
        raise RuntimeError("The actual demo widget did not produce a capture.")
    if not save_sheet:
        image = source.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        image.setDevicePixelRatio(1)
        return image.convertToFormat(QImage.Format.Format_RGB32)
    image = source.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    image.setDevicePixelRatio(1)
    # A native main-window grab can be RGB while a modal is RGBA. Keep every
    # PNG in the same opaque format so palette filters never reinitialize when
    # the recorded surface changes. Both surfaces already have a background.
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    canvas.fill(QColor('#202124'))
    painter = QPainter(canvas)
    painter.drawImage((width - image.width()) // 2, (height - image.height()) // 2, image)
    painter.end()
    return canvas


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

    include_stream = bool(getattr(args, "include_stream", False))
    if include_stream and (mongo is None or not mongo.replica_set):
        raise RuntimeError("A generated stream recording requires its new owned replica set.")
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

        def record(self, config, sql, elapsed, status, **metadata):
            captured = deepcopy(config), deepcopy(metadata)
            background_io(lambda: Store.record(self, captured[0], sql, elapsed, status, **captured[1]))

        def flush(self):
            # MainWindow's close path requests a flush synchronously. Keep its
            # immutable snapshot on our serial I/O owner and wait before exit.
            proxy = object.__new__(Store)
            proxy.path, proxy.state = self.path, deepcopy(self.state)
            background_io(Store.flush, proxy)

        def record_batch_job_run(self, job_id, result):
            return background_io(_batch_history_io, _batch_history_snapshot(self), job_id, deepcopy(result)).result()

        def batch_job_history(self, job_id):
            return background_io(_batch_history_io, _batch_history_snapshot(self), job_id).result()

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
             "mongo_page": None, "gridfs_page": None, "mongo_query": None,
             "mongo_index_page": None, "sqlite_closed_in_worker": False,
             "mongo_storage_page": None, "mongo_storage_verification": None,
             "mongo_batch_page": None, "mongo_batch_verification": None,
             "mongo_stream_page": None, "mongo_stream_verification": None,
             "stream_abort": threading.Event(), "stream_release": threading.Event(),
             "capture_target": window}
    deadline = time.monotonic() + (150 if include_stream else 120)
    capture_timer = QTimer(app)
    capture_timer.setInterval(round(1000 / args.fps))

    def idle():
        return not window.tasks and not window.pool.activeThreadCount()

    def fail(error):
        if state["error"] is not None:
            return
        text = str(error).replace(str(temporary), "<temporary-demo>")
        if mongo is not None:
            for value in mongo.private_values():
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
            target = state["capture_target"]
            if target is not window:
                # A real app sheet is grabbed directly, never composited into a
                # synthetic main-window image or sampled from the desktop.
                if not target.isVisible():
                    raise RuntimeError("The generated demo sheet closed before its capture.")
                assert_visible_privacy(target,temporary,mongo)
            for job in io_jobs:
                if job.done() and job.exception() is not None:
                    raise RuntimeError("The generated demo background I/O failed.") from job.exception()
            pixmap = target.grab()
            image = fit_capture_image(pixmap.toImage(), args.width, args.height, save_sheet=target is not window)
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
        target = state["capture_target"]
        state["phases"].append({"name": name, "first_frame": state["count"],
            "surface": "main_window" if target is window else "save_sheet",
            "source_size_logical": [target.width(), target.height()],
            "fit": "existing_main_window_scale" if target is window else "keep_aspect_centered_neutral_letterbox"})

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
        hold(2.5, open_mongo_query)

    def open_mongo_query():
        from opennavicat.ui.mongo_native_query import NativeMongoQueryPage

        page = window.new_query(sql=MONGO_QUERY, connection_id=MONGO_CONNECTION_ID)
        if not isinstance(page, NativeMongoQueryPage):
            raise RuntimeError("The demo did not open the integrated JavaScript query page.")
        state["mongo_query"] = page
        phase("mongodb_javascript_editor")
        hold(2.2, run_mongo_query)

    def run_mongo_query():
        page = state["mongo_query"]
        if not page.run_native():
            raise RuntimeError("The generated JavaScript query did not start.")
        wait_for(lambda: not page.running and idle(), mongo_query_ready,
                 "running JavaScript on the generated MongoDB fixture")

    def mongo_query_ready():
        page = state["mongo_query"]
        execution = page.last_execution
        result = page.current_result()
        if (execution is None or execution.error or not execution.child_reaped
                or result is None or len(result.rows) != 3 or execution.has_confirmed_writes):
            raise RuntimeError("The generated JavaScript read did not complete with three exact results.")
        verify_mongo_query_values(execution.value_tree, result.rows)
        phase("mongodb_javascript_results")
        hold(2.5, open_mongo_index)

    def open_mongo_index():
        from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage

        page = window.open_mongo_collection_designer(
            MONGO_CONNECTION_ID, MONGO_DATABASE, MONGO_COLLECTION)
        if not isinstance(page, MongoCollectionDesignerPage):
            raise RuntimeError("The demo did not open the embedded collection index table.")
        state["inline_index_page"] = page
        wait_for(lambda: page.snapshot is not None and not page.loading and idle(),
                 mongo_index_ready, "reading the generated embedded index table")

    def mongo_index_ready():
        page = state["inline_index_page"]
        page.tabs.setCurrentWidget(page.indexes)
        names = [page.index_table.cellWidget(row, 1).text()
                 for row in range(page.index_table.rowCount())]
        if names != ["demo_stock_sku"]:
            raise RuntimeError("The generated embedded index table has unexpected rows.")
        page.index_table.selectRow(0)
        phase("mongodb_collection_inline_index")
        capture()
        capture_timer.start()
        hold(2.2, open_mongo_inline_key)

    def open_mongo_inline_key():
        page = state["inline_index_page"]
        page.index_table.cellWidget(0, 2).click()
        wait_for(lambda: page._inline_key_dialog is not None,
                 mongo_inline_key_ready, "opening the generated embedded index key editor")

    def mongo_inline_key_ready():
        page = state["inline_index_page"]
        dialog = page._inline_key_dialog
        if [(key.field, key.order) for key in dialog.definition.keys] != [("stock", -1), ("sku", 1)]:
            raise RuntimeError("The generated embedded index key order changed.")
        state["inline_key_editor_opened"] = True
        state["capture_target"] = dialog
        phase("mongodb_collection_inline_key_editor")
        capture()
        capture_timer.start()
        hold(2.4, close_mongo_inline_key)

    def close_mongo_inline_key():
        page = state["inline_index_page"]
        dialog = page._inline_key_dialog
        capture(); capture_timer.stop()
        state["capture_target"] = window
        dialog.reject()
        wait_for(lambda: page._inline_key_dialog is None and not page.dirty,
                 close_mongo_inline_index, "closing the generated embedded index key editor without changes")

    def close_mongo_inline_index():
        page = state["inline_index_page"]
        state["capture_target"] = window
        capture()
        capture_timer.start()
        window.close_tab(window.tabs.indexOf(page))
        state["inline_index_page"] = None
        wait_for(idle, open_mongo_storage, "closing the generated embedded index table")

    def open_mongo_storage():
        from opennavicat.ui.mongo_collection_designer import MongoCollectionDesignerPage

        page = window.open_mongo_collection_designer(MONGO_CONNECTION_ID, MONGO_DATABASE)
        if not isinstance(page, MongoCollectionDesignerPage):
            raise RuntimeError("The demo did not open the integrated collection designer.")
        state["mongo_storage_page"] = page
        wait_for(lambda: page.snapshot is not None and not page.loading and idle(), mongo_storage_ready,
                 "opening a new generated collection draft")

    def mongo_storage_ready():
        page = state["mongo_storage_page"]
        if page.snapshot.exists or page.definition.name:
            raise RuntimeError("The storage demo must start with a new unnamed draft.")
        page.tabs.setCurrentWidget(page.storage)
        page.storage_editor.setPlainText(MONGO_STORAGE_TEXT)
        if page.draft().options.get("storageEngine") != mongo_storage_options()["storageEngine"]:
            raise RuntimeError("The generated collection storage editor did not parse its actual object literal.")
        phase("mongodb_collection_storage_engine")
        hold(1.8, mongo_index_defaults)

    def mongo_index_defaults():
        page = state["mongo_storage_page"]
        page.tabs.setCurrentWidget(page.index_options)
        page.index_options_editor.setPlainText(MONGO_INDEX_DEFAULTS_TEXT)
        if {key: page.draft().options[key] for key in mongo_storage_options()} != mongo_storage_options():
            raise RuntimeError("The generated collection index defaults do not match the demo draft.")
        phase("mongodb_collection_index_defaults")
        hold(1.8, mongo_collection_options_page)

    def mongo_collection_options_page():
        page = state["mongo_storage_page"]
        selected = mongo_collection_options()
        page.tabs.setCurrentWidget(page.general)
        page.clustered_check.setChecked(True)
        page.cluster_name_edit.setText(selected["clusteredIndex"]["name"])
        page.cluster_expire_check.setChecked(True)
        page.cluster_expire_edit.setText(str(selected["expireAfterSeconds"]))
        page.change_images_check.setChecked(True)
        actual = page.draft().options
        if (actual.get("clusteredIndex") != selected["clusteredIndex"]
                or actual.get("expireAfterSeconds") != selected["expireAfterSeconds"]
                or actual.get("changeStreamPreAndPostImages")
                    != selected["changeStreamPreAndPostImages"]):
            raise RuntimeError("The generated collection-option controls did not produce the demo draft.")
        phase("mongodb_collection_options")
        hold(2.2, mongo_collection_collation)

    def mongo_collection_collation():
        page = state["mongo_storage_page"]
        page.tabs.setCurrentWidget(page.collation)
        editor = page.collation_editor
        editor.locale_combo.setCurrentText("en_US")
        for key, value in mongo_demo_collation().items():
            if key == "locale":
                continue
            combo = editor.combos[key]
            combo.setCurrentIndex(combo.findData(value))
        if editor.collation() != mongo_demo_collation():
            raise RuntimeError("The structured collation controls did not produce the generated draft.")
        phase("mongodb_collection_collation")
        hold(2.0, open_mongo_storage_sheet)

    def open_mongo_storage_sheet():
        page = state["mongo_storage_page"]
        page.request_save()
        dialog = page._save_dialog
        if dialog is None or dialog.name_edit.text() or dialog.save_button.isEnabled():
            raise RuntimeError("The new collection did not open its unnamed save sheet.")
        dialog.name_edit.setText(MONGO_STORAGE_COLLECTION)
        wait_for(lambda: page._save_dialog is dialog and dialog.save_button.isEnabled() and idle(),
                 mongo_storage_preview_ready, "planning the generated collection's actual save sheet")

    def mongo_storage_preview_ready():
        page = state["mongo_storage_page"]
        dialog = page._save_dialog
        actual = dialog.plan.definition.options if dialog.plan is not None else {}
        selected_options = mongo_collection_options()
        if (dialog.plan is None or dialog.definition.name != MONGO_STORAGE_COLLECTION
                or {key: actual[key] for key in mongo_storage_options()} != mongo_storage_options()
                or actual.get("clusteredIndex") != selected_options["clusteredIndex"]
                or actual.get("expireAfterSeconds") != selected_options["expireAfterSeconds"]
                or actual.get("changeStreamPreAndPostImages")
                    != selected_options["changeStreamPreAndPostImages"]
                or actual.get("collation") != mongo_demo_collation()
                or dialog.plan.commands[0].get("create") != MONGO_STORAGE_COLLECTION
                or "block_compressor=zlib" not in dialog.preview.toPlainText()
                or "prefix_compression=false" not in dialog.preview.toPlainText()
                or "en_US" not in dialog.preview.toPlainText()):
            raise RuntimeError("The collection save sheet did not render its confirmed generated options.")
        state["capture_target"] = dialog
        phase("mongodb_collection_storage_save_sheet")
        hold(2.2, save_mongo_storage)

    def save_mongo_storage():
        page = state["mongo_storage_page"]
        dialog = page._save_dialog
        if dialog is None or dialog.definition.name != MONGO_STORAGE_COLLECTION or not dialog.save_button.isEnabled():
            raise RuntimeError("The generated collection save sheet changed before saving.")
        capture()
        capture_timer.stop()
        state["capture_target"] = window
        dialog.save_button.click()
        wait_for(lambda: page.snapshot.exists and not page.dirty and not page.loading and idle(),
                 verify_mongo_storage, "saving the new generated collection through the application")

    def verify_mongo_storage():
        page = state["mongo_storage_page"]
        actual = page.snapshot.definition.options
        selected_options = mongo_collection_options()
        actual_cluster = actual.get("clusteredIndex", {})
        if (page.snapshot.name != MONGO_STORAGE_COLLECTION
                or {key: actual[key] for key in mongo_storage_options()} != mongo_storage_options()
                or not all(actual_cluster.get(key) == value
                           for key, value in selected_options["clusteredIndex"].items())
                or actual.get("expireAfterSeconds") != selected_options["expireAfterSeconds"]
                or actual.get("changeStreamPreAndPostImages")
                    != selected_options["changeStreamPreAndPostImages"]):
            raise RuntimeError("The application saved unexpected generated collection options.")
        window.submit(mongo.verify_storage_collection, mongo_storage_verified, fail,
                      label="独立核验生成集合的存储设置")

    def mongo_storage_verified(verification):
        state["mongo_storage_verification"] = verification
        # MainWindow refuses to close a tab while the callback's submitting
        # task is still registered. Let that exact task finish before reopening.
        wait_for(idle, reopen_mongo_storage, "finishing independent collection verification")

    def reopen_mongo_storage():
        page = state["mongo_storage_page"]
        window.close_tab(window.tabs.indexOf(page))
        reopened = window.open_mongo_collection_designer(MONGO_CONNECTION_ID, MONGO_DATABASE, MONGO_STORAGE_COLLECTION)
        if reopened is page:
            raise RuntimeError("The generated collection designer did not actually reopen.")
        state["mongo_storage_page"] = reopened
        wait_for(lambda: reopened.snapshot is not None and not reopened.loading and idle(),
                 mongo_storage_reopened, "reopening the saved generated collection")

    def mongo_storage_reopened():
        page = state["mongo_storage_page"]
        actual = page.snapshot.definition.options
        selected_options = mongo_collection_options()
        if ({key: actual[key] for key in mongo_storage_options()} != mongo_storage_options()
                or actual.get("clusteredIndex") != {**selected_options["clusteredIndex"], "v": 2}
                or actual.get("expireAfterSeconds") != selected_options["expireAfterSeconds"]
                or actual.get("changeStreamPreAndPostImages")
                    != selected_options["changeStreamPreAndPostImages"]
                or not all(actual.get("collation", {}).get(key) == value
                           for key, value in mongo_demo_collation().items())
                or "version" not in actual.get("collation", {})
                or page.dirty or not page.storage_editor.isReadOnly()
                or not page.index_options_editor.isReadOnly()
                or page.clustered_check.isEnabled() or page.cluster_name_edit.isEnabled()
                or page.cluster_expire_check.isEnabled() or page.auto_index_id_check.isEnabled()
                or not page.change_images_check.isEnabled()
                or page.collation_editor.locale_combo.isEnabled()):
            raise RuntimeError("The reopened collection did not preserve its read-only options or collation settings.")
        page.tabs.setCurrentWidget(page.general)
        phase("mongodb_collection_options_reopened")
        capture()
        capture_timer.start()
        hold(1.8, open_mongo_pipeline)

    def open_mongo_pipeline():
        page = window.open_mongo_pipeline(MONGO_CONNECTION_ID, MONGO_DATABASE)
        if page is None:
            raise RuntimeError("The demo did not open a native aggregation-pipeline workspace.")
        state["mongo_pipeline_page"] = page
        wait_for(lambda: MONGO_COLLECTION in page._sources and idle(), mongo_pipeline_ready,
                 "reading generated aggregation-pipeline sources")

    def mongo_pipeline_ready():
        from opennavicat.mongodb_session import canonical_loads

        page = state["mongo_pipeline_page"]
        page.source_combo.setCurrentText(MONGO_COLLECTION)
        page.match_tool.add_row('attributes.active', '$eq', 'true')
        page.add_stage()
        page.operator_combo.setCurrentText("$group")
        if "Separate documents into groups" not in page.stage_editor.toPlainText():
            raise RuntimeError("The generated $group reference editor template is missing.")
        phase("mongodb_aggregation_pipeline_stage_template")
        hold(2.2, configure_mongo_pipeline)

    def configure_mongo_pipeline():
        page = state["mongo_pipeline_page"]
        page.stage_editor.setPlainText(
            '{"_id":"$attributes.origin","count":{"$sum":1}}')
        options = page.options_editor
        options.allow_disk_combo.setCurrentIndex(options.allow_disk_combo.findData(1))
        options.batch_size_edit.setText("25")
        options.max_time_edit.setText("5000")
        options.read_concern_combo.setCurrentIndex(options.read_concern_combo.findData(1))
        options.locale_combo.setCurrentText("en")
        options.strength_combo.setCurrentIndex(options.strength_combo.findData(3))
        options.comment_edit.setText("generated demo options")
        page.options_button.setChecked(True)
        phase("mongodb_aggregation_pipeline_options")
        hold(2.2, start_mongo_pipeline_preview)

    def start_mongo_pipeline_preview():
        page = state["mongo_pipeline_page"]
        if not window.preview_mongo_pipeline(page, page.draft()):
            raise RuntimeError("The generated aggregation-pipeline preview did not start.")
        wait_for(lambda: page._preview is not None and not page.running and idle(), mongo_pipeline_preview,
                 "previewing generated aggregation-pipeline stages")

    def mongo_pipeline_preview():
        from opennavicat.mongodb_session import canonical_loads

        page = state["mongo_pipeline_page"]
        if (len(page._preview.source_rows) != 6 or len(page._preview.final_rows) != 1
                or canonical_loads(page._preview.final_rows[0]).get("count") != 6):
            raise RuntimeError("The generated aggregation-pipeline preview returned unexpected rows.")
        state["mongo_pipeline_preview"] = {
            "source_rows": len(page._preview.source_rows),
            "stage_rows": [len(stage["rows"]) for stage in page._preview.stages],
            "final_rows": len(page._preview.final_rows),
        }
        page.select_stage(0)
        phase("mongodb_aggregation_pipeline")
        hold(2.2, mongo_pipeline_script)

    def mongo_pipeline_script():
        page = state["mongo_pipeline_page"]
        page.tabs.setCurrentWidget(page.script_tab)
        if "db.getCollection" not in page.script_editor.toPlainText() or "$group" not in page.script_editor.toPlainText():
            raise RuntimeError("The generated aggregation-pipeline script is incomplete.")
        phase("mongodb_aggregation_pipeline_script")
        hold(2.0, explain_mongo_pipeline)

    def explain_mongo_pipeline():
        page = state["mongo_pipeline_page"]
        if not window.explain_mongo_pipeline(page, page.draft()):
            raise RuntimeError("The generated aggregation-pipeline Explain did not start.")
        wait_for(lambda: page._explain is not None and not page.running and idle(),
                 mongo_pipeline_explain, "explaining the generated aggregation pipeline")

    def mongo_pipeline_explain():
        page = state["mongo_pipeline_page"]
        page.explain_panel.view_tabs.setCurrentIndex(0)
        if (page.tabs.currentWidget() is not page.explain_panel
                or len(page.explain_panel.explain_canvas._items) < 1
                or "executionStats" not in page._explain.raw):
            raise RuntimeError("The generated aggregation-pipeline Explain did not produce a visual plan.")
        for index, item in enumerate(page.explain_panel.explain_canvas._items):
            if item.node.stage.lower() == "group":
                page.explain_panel.explain_canvas.select_index(index)
                break
        state["mongo_pipeline_explain"] = {
            "nodes": len(page._explain.nodes()),
            "root": page._explain.root.stage if page._explain.root is not None else None,
            "raw_canonical_ejson": True,
        }
        phase("mongodb_aggregation_pipeline_explain")
        hold(2.4, run_mongo_pipeline)

    def run_mongo_pipeline():
        from opennavicat.mongodb_session import canonical_loads

        page = state["mongo_pipeline_page"]
        if not window.run_mongo_pipeline(page, page.draft()):
            raise RuntimeError("The generated aggregation pipeline did not start.")
        wait_for(lambda: bool(page.last_rows) and not page.running and idle(), mongo_pipeline_results,
                 "running the generated aggregation pipeline")

    def mongo_pipeline_results():
        from opennavicat.mongodb_session import canonical_loads

        page = state["mongo_pipeline_page"]
        page.tabs.setCurrentWidget(page.results)
        if len(page.last_rows) != 1 or canonical_loads(page.last_rows[0]).get("count") != 6:
            raise RuntimeError("The generated aggregation pipeline returned unexpected results.")
        state["mongo_pipeline_results"] = len(page.last_rows)
        phase("mongodb_aggregation_pipeline_results")
        hold(2.2, open_mongo_batch_job)

    def open_mongo_batch_job():
        from opennavicat.ui.batch_jobs import BatchJobPage

        capture(); capture_timer.stop()
        pipeline_page = state["mongo_pipeline_page"]
        pipeline = pipeline_page.draft()
        pipeline.name = MONGO_BATCH_PIPELINE_NAME
        entry = window.save_mongo_pipeline(pipeline_page, pipeline)
        if entry is None or entry["name"] != MONGO_BATCH_PIPELINE_NAME:
            raise RuntimeError("The generated Batch Job pipeline was not actually saved.")
        page = window.batch_job_controller.new_job()
        if not isinstance(page, BatchJobPage):
            raise RuntimeError("The actual Batch Job editor did not open.")
        state["mongo_batch_page"] = page
        state["mongo_batch_main_session"] = window.sessions[MONGO_CONNECTION_ID]
        page.select_job_type("aggregationpipeline")
        matches = [row for row in range(page.available_table.rowCount())
                   if page.available_table.item(row, 0).text() == MONGO_BATCH_PIPELINE_NAME]
        if len(matches) != 1:
            raise RuntimeError("The generated saved pipeline is missing or ambiguous in the Batch Job editor.")
        page.available_table.selectRow(matches[0])
        page.add_selected_jobs(); page.add_selected_jobs()
        selected = page.draft()
        if (len(selected.jobs) != 2 or any(reference.reference_name != MONGO_BATCH_PIPELINE_NAME
                or reference.server != MONGO_CONNECTION_NAME or reference.schema != MONGO_DATABASE
                for reference in selected.jobs)):
            raise RuntimeError("The Batch Job did not retain the two ordered generated references.")
        page.name = MONGO_BATCH_NAME
        saved = window.batch_job_controller.save(page, selected)
        if saved is None or page.job_id != saved["id"] or page.dirty:
            raise RuntimeError("The generated Batch Job was not actually saved.")
        page.tabs.setCurrentWidget(page.general_page)
        phase("mongodb_batch_job_general")
        capture(); capture_timer.start()
        hold(2.4, prepare_mongo_batch_run)

    def prepare_mongo_batch_run():
        capture(); capture_timer.stop()
        window.submit(mongo.batch_source_snapshot, mongo_batch_baseline, fail,
                      label="独立读取批处理示例源 BSON")

    def mongo_batch_baseline(snapshot):
        state["mongo_batch_before"] = snapshot
        wait_for(idle, run_mongo_batch, "finishing the independent generated Batch Job baseline")

    def run_mongo_batch():
        page = state["mongo_batch_page"]
        if window.sessions.get(MONGO_CONNECTION_ID) is not state["mongo_batch_main_session"]:
            raise RuntimeError("The Batch Job replaced the generated main-window session before execution.")
        page.request_start()
        wait_for(lambda: page.last_result is not None and not page.running and idle()
                 and not window.batch_job_controller.runs,
                 mongo_batch_executed, "executing both generated saved-pipeline references")

    def mongo_batch_executed():
        page = state["mongo_batch_page"]
        if page.history_pending or window.batch_job_controller.unsaved_history.get(id(page)):
            raise RuntimeError("The generated Batch Job history was not persisted.")
        state["mongo_batch_result"] = deepcopy(page.last_result)
        state["mongo_batch_history"] = store.batch_job_history(page.job_id)
        session = state["mongo_batch_main_session"]
        def verify_source():
            snapshot = mongo.batch_source_snapshot()
            reopened = session.fetch_documents(MONGO_DATABASE, MONGO_COLLECTION, limit=7)
            if len(reopened.documents) != 6 or reopened.has_more:
                raise RuntimeError("The original generated main-window session no longer reads the source documents.")
            return snapshot
        window.submit(verify_source, mongo_batch_verified, fail,
                      label="独立核验批处理结果与未变的示例源 BSON")

    def mongo_batch_verified(snapshot):
        if window.sessions.get(MONGO_CONNECTION_ID) is not state["mongo_batch_main_session"]:
            raise RuntimeError("The Batch Job replaced the generated main-window session.")
        verification = verify_mongo_batch_values(state["mongo_batch_result"], state["mongo_batch_history"],
                                                state["mongo_batch_before"], snapshot)
        verification["main_window_session_preserved_and_readable"] = True
        state["mongo_batch_verification"] = verification
        wait_for(idle, show_mongo_batch_messages, "finishing independent generated Batch Job verification")

    def show_mongo_batch_messages():
        page = state["mongo_batch_page"]
        page.tabs.setCurrentWidget(page.messages)
        if "Success: 2" not in page.messages.toPlainText():
            raise RuntimeError("The actual Batch Job messages did not display the successful run counts.")
        phase("mongodb_batch_job_messages")
        capture(); capture_timer.start()
        hold(2.4, show_mongo_batch_history)

    def show_mongo_batch_history():
        page = state["mongo_batch_page"]
        page.tabs.setCurrentWidget(page.history_page)
        if page.history_table.rowCount() != 1 or page.history_table.item(0, 0).text() != "1":
            raise RuntimeError("The actual Batch Job history did not display its persisted execution.")
        page.history_table.selectRow(0)
        if page.history_log.toPlainText().count("[success]") != 2:
            raise RuntimeError("The persisted Batch Job history did not display both successful tasks.")
        phase("mongodb_batch_job_history")
        capture(); capture_timer.start()
        hold(2.4, prepare_mongo_stream if include_stream else finish)

    def prepare_mongo_stream():
        capture(); capture_timer.stop()
        window.submit(mongo.prepare_stream, mongo_stream_prepared, fail,
                      label="准备独立生成的有限流示例")

    def mongo_stream_prepared(before):
        state["mongo_stream_before"] = before
        page = window.open_mongo_pipeline(MONGO_CONNECTION_ID, MONGO_DATABASE)
        if page is None:
            raise RuntimeError("The actual finite-stream pipeline workspace did not open.")
        state["mongo_stream_page"] = page
        wait_for(lambda: MONGO_STREAM_COLLECTION in page._sources and idle(),
                 arm_mongo_stream_producer, "reading the generated finite-stream source")

    def arm_mongo_stream_producer():
        page = state["mongo_stream_page"]
        page.source_combo.setCurrentText(MONGO_STREAM_COLLECTION)
        page.operator_combo.setCurrentText("$changeStream")
        page.stage_editor.setPlainText("{}")
        comment = "generated finite stream " + uuid.uuid4().hex
        page.options_editor.comment_edit.setText(comment)
        page.select_stage(0)
        session = window.sessions[MONGO_CONNECTION_ID]
        state["mongo_stream_session"] = session
        state["mongo_stream_original_icon"] = window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey()
        armed, active = threading.Event(), threading.Event()
        state["stream_active"] = active
        state["stream_producer"] = background_io(mongo.produce_stream_events,
            session, comment, armed, active, state["stream_release"], state["stream_abort"])
        def producer_armed():
            if state["stream_producer"].done():
                state["stream_producer"].result()
            return armed.is_set()
        wait_for(producer_armed, run_mongo_stream, "arming the independent generated event producer")

    def run_mongo_stream():
        page = state["mongo_stream_page"]
        page.tabs.setCurrentWidget(page.pipeline_tab)
        page.run_action.trigger()  # Exercise the real Toolbar Run signal/queue.
        if not page.running:
            raise RuntimeError("The actual finite-stream Toolbar Run did not dispatch.")
        def getmore_active():
            if state["stream_producer"].done():
                state["stream_producer"].result()
            return state["stream_active"].is_set()
        wait_for(getmore_active, capture_mongo_stream_busy, "observing this run's exact generated getMore")

    def capture_mongo_stream_busy():
        page = state["mongo_stream_page"]
        verify_mongo_stream_page(page, busy=True)
        if window.tabs.tabIcon(window.tabs.indexOf(page)).cacheKey() == state["mongo_stream_original_icon"]:
            raise RuntimeError("The actual finite-stream tab busy indicator did not change.")
        phase("mongodb_finite_stream_busy")
        capture(); capture_timer.start()
        # Deliver events before the real 1-second getMore wait can return empty,
        # while retaining actual overlay/tab-ring frames rather than a fake hold.
        hold(.45, release_mongo_stream_events)

    def release_mongo_stream_events():
        state["stream_release"].set()
        page = state["mongo_stream_page"]
        wait_for(lambda: not page.running and page._last_stream_result is not None
                 and state["stream_producer"].done() and idle(),
                 verify_mongo_stream, "finishing the generated finite stream and cursor cleanup")

    def verify_mongo_stream():
        capture(); capture_timer.stop()
        page, session = state["mongo_stream_page"], state["mongo_stream_session"]
        if window.sessions.get(MONGO_CONNECTION_ID) is not session:
            raise RuntimeError("The finite stream replaced the generated main-window session.")
        production = state["stream_producer"].result()
        result = page._last_stream_result
        def verify_owned_stream():
            readback, after = mongo.stream_readback(production["marker"])
            verified = verify_mongo_stream_values(result, production, readback,
                                                   state["mongo_stream_before"], after)
            source = session.fetch_documents(MONGO_DATABASE, MONGO_COLLECTION, limit=7)
            if len(source.documents) != 6 or source.has_more:
                raise RuntimeError("The original generated session no longer reads its source after the stream.")
            verified["main_window_session_preserved_and_readable"] = True
            return verified
        window.submit(verify_owned_stream, mongo_stream_verified, fail,
                      label="独立核验有限流 BSON、游标清理与未变的示例源")

    def mongo_stream_verified(verification):
        state["mongo_stream_verification"] = verification
        wait_for(idle, show_mongo_stream_messages, "finishing independent finite-stream verification")

    def show_mongo_stream_messages():
        page = state["mongo_stream_page"]
        verify_mongo_stream_page(page)
        phase("mongodb_finite_stream_messages")
        capture(); capture_timer.start()
        hold(2.4, show_mongo_stream_output)

    def show_mongo_stream_output():
        page = state["mongo_stream_page"]
        page.tabs.setCurrentWidget(page.stream_output)
        verify_mongo_stream_page(page, output=True)
        phase("mongodb_finite_stream_output")
        capture(); capture_timer.start()
        hold(2.4, finish)

    def finish():
        capture()
        capture_timer.stop()
        if state["error"] is not None:
            return
        mongo_session = window.sessions.get(MONGO_CONNECTION_ID)
        def verify_preview():
            with closing(sqlite3.connect(sample)) as database:
                names = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "customers" not in names or "customers_demo" in names:
                    raise RuntimeError("The preview unexpectedly changed the demo database.")
            if mongo is not None:
                entry = mongo_session.connection[MONGO_DATABASE][MONGO_COLLECTION]
                actual = entry.index_information().get("demo_stock_sku", {})
                if actual.get("sparse") or list(actual.get("key", ())) != [("stock", -1), ("sku", 1)]:
                    raise RuntimeError("The index preview unexpectedly changed the generated database.")
                if not state["mongo_storage_verification"]:
                    raise RuntimeError("The generated collection's actual storage save was not verified.")
                if not state["mongo_batch_verification"]:
                    raise RuntimeError("The generated Batch Job's actual execution and persisted history were not verified.")
                if include_stream and not state["mongo_stream_verification"]:
                    raise RuntimeError("The generated finite stream was not independently verified.")
        window.submit(verify_preview, lambda _result: shutdown(), label="验证生成的演示数据")

    def shutdown():
        if state["shutdown"]:
            return
        state["shutdown"] = True
        state["stream_abort"].set()
        state["stream_release"].set()
        capture_timer.stop()
        started = time.monotonic()
        discarded = False
        forced_cleanup = False

        def poll():
            nonlocal discarded, forced_cleanup
            try:
                if not idle():
                    query = state.get("mongo_query")
                    if state["error"] and query is not None and query.running:
                        query.request_cancel()
                    current = state.get("gridfs_page")
                    if state["error"] and current is not None and current.loading:
                        current.cancel_transfer()
                    batch_page = state.get("mongo_batch_page")
                    if state["error"] and batch_page is not None and batch_page.running:
                        batch_page.request_cancel()
                    stream_page = state.get("mongo_stream_page")
                    if state["error"] and stream_page is not None and stream_page.running:
                        window.stop_mongo_pipeline(stream_page)
                elif not window._closed:
                    if not discarded:
                        page = state.get("designer")
                        if page is not None and page.snapshot is not None:
                            page.load_snapshot(page.snapshot)
                        page = state.get("mongo_index_page")
                        if page is not None and page.snapshot is not None:
                            page.load_snapshot(page.snapshot)
                        page = state.get("mongo_storage_page")
                        if page is not None and page.snapshot is not None:
                            if page._save_dialog is not None:
                                page._save_dialog.reject()
                            if page.snapshot.exists:
                                page.load_snapshot(page.snapshot)
                            else:
                                # Explicitly discard only this recorder-owned,
                                # unnamed failed draft before closing its app.
                                index = window.tabs.indexOf(page)
                                if index >= 0:
                                    window.tabs.removeTab(index)
                                    page.deleteLater()
                        for key in ("mongo_pipeline_page", "mongo_stream_page"):
                            page = state.get(key)
                            if page is not None:
                                index = window.tabs.indexOf(page)
                                if index >= 0:
                                    window.tabs.removeTab(index)
                                    page.deleteLater()
                        page = state.get("mongo_batch_page")
                        if page is not None:
                            index = window.tabs.indexOf(page)
                            if index >= 0:
                                window.tabs.removeTab(index)
                                window.batch_job_controller.unsaved_history.pop(id(page), None)
                                page.deleteLater()
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
    if state["count"] < 2 or len(state["phases"]) != (31 if include_stream else 28 if mongo is not None else 6):
        raise RuntimeError("The demo did not capture all required workflows.")
    for index, item in enumerate(state["phases"]):
        stop = state["phases"][index + 1]["first_frame"] if index + 1 < len(state["phases"]) else state["count"]
        item["frames"] = stop - item["first_frame"]
        if item["frames"] <= 0:
            raise RuntimeError("A required demo phase did not contain a privacy-checked frame: " + item["name"])
        item["privacy_checked_frames"] = item["frames"]
    return frames, {
        "application": "OpenNavicat", "version": __version__, "theme": args.theme,
        "capture": "Actual live Qt QWidget.grab frames; save-sheet phase keeps the actual modal's aspect ratio on a centered neutral letterbox canvas; no reference screenshots, synthetic controls or image generation service",
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
        "mongodb_javascript_read_completed": state["mongo_query"] is not None,
        "mongodb_inline_index_preview_applied": False,
        "mongodb_inline_key_editor_opened": state["inline_key_editor_opened"],
        "mongodb_collection_storage_save": state["mongo_storage_verification"],
        "mongodb_collection_collation_save": bool(state["mongo_storage_verification"]),
        "mongodb_collection_storage_reopened_readonly": state["mongo_storage_verification"] is not None,
        "mongodb_collection_options_reopened_readonly": state["mongo_storage_verification"] is not None,
        "mongodb_collection_options_verified": bool(state["mongo_storage_verification"]),
        "mongodb_aggregation_pipeline_stage_template": bool(mongo is not None),
        "mongodb_aggregation_pipeline_options": ({
            "allowDiskUse": True,
            "cursorBatchSize": 25,
            "maxTimeMS": 5000,
            "readConcernLevel": "local",
            "locale": "en",
            "strength": 3,
        } if mongo is not None else None),
        "mongodb_aggregation_pipeline_preview": state.get("mongo_pipeline_preview"),
        "mongodb_aggregation_pipeline_explain": state.get("mongo_pipeline_explain"),
        "mongodb_aggregation_pipeline_results": state.get("mongo_pipeline_results"),
        "mongodb_batch_job": state["mongo_batch_verification"],
        "mongodb_finite_stream": state["mongo_stream_verification"],
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
    parser.add_argument("--include-stream", action="store_true",
        help="Add real finite-stream busy/messages/output using an authenticated owned replica set; requires --include-mongodb")
    parser.add_argument("--check-dependencies", action="store_true")
    args = parser.parse_args()
    if args.include_stream and not args.include_mongodb:
        parser.error("--include-stream requires --include-mongodb")
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
    mongo = OwnedMongoDemo(replica_set=args.include_stream) if args.include_mongodb else None
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
            forbidden.extend(mongo.private_values())
        if any(value in text for value in forbidden):
            raise RuntimeError("The public demo receipt contains a local path or generated service identity.")
        io_pool.submit(args.output.with_suffix(".json").write_text,text,"utf-8").result()
    print(json.dumps({"recording": "passed", "file": args.output.name,
                      "frames": receipt["frames"], "bytes": receipt["gif_bytes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
