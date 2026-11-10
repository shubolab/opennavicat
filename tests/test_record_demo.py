"""Actual Qt privacy surfaces and recorder-owned timeout/error contracts."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
import traceback
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QPushButton, QToolButton, QVBoxLayout, QWidget


@pytest.fixture
def recorder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "record-demo.py"
    spec = importlib.util.spec_from_file_location("record_demo_tests",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("corruption", [None, "rounded_stock", "wrong_price", "wrong_sku", "wrong_display"])
def test_generated_javascript_demo_requires_exact_bson_and_actual_display(recorder, corruption):
    from bson import Decimal128, Int64
    from opennavicat.js_engine.codec import encode
    from opennavicat.js_engine.presentation import display_value

    values = [{"sku": "DEMO-001", "stock": Int64(9007199254740994), "price": Decimal128("1299.95")},
              {"sku": "DEMO-002", "stock": Int64(9007199254740995), "price": Decimal128("349.50")},
              {"sku": "DEMO-003", "stock": Int64(9007199254740996), "price": Decimal128("79.00")}]
    if corruption == "rounded_stock":
        values[1]["stock"] = float(values[1]["stock"])
    elif corruption == "wrong_price":
        values[0]["price"] = "1299.95"
    elif corruption == "wrong_sku":
        values[2]["sku"] = "WRONG-SAMPLE"
    tree = encode(values)
    rows = [list(display_value(child)) for child in tree[1]]
    if corruption == "wrong_display":
        altered = deepcopy(values[0]); altered["stock"] = 0
        rows[0] = list(display_value(encode(altered)))
    if corruption is None:
        recorder.verify_mongo_query_values(tree, rows)
    else:
        with pytest.raises(RuntimeError, match="exact generated BSON"):
            recorder.verify_mongo_query_values(tree, rows)


@pytest.mark.parametrize("button_class",[QPushButton,QToolButton])
def test_actual_visible_button_endpoint_is_rejected_before_capture(qtbot,recorder,tmp_path,button_class):
    window = QWidget()
    qtbot.addWidget(window)
    window.setWindowTitle("Generated sample")
    layout = QVBoxLayout(window)
    button = button_class()
    button.setText("192.0.2.51:54321")  # Reserved documentation address, no server.
    layout.addWidget(button)
    window.show()
    qtbot.waitExposed(window)
    assert button.isVisible() and button.text() in list(recorder.visible_texts(window))
    with pytest.raises(RuntimeError,match="endpoint or credential"):
        recorder.assert_visible_privacy(window,tmp_path)
    button.setText("生成示例")
    recorder.assert_visible_privacy(window,tmp_path)


def test_visible_button_local_path_is_rejected(qtbot,recorder,tmp_path):
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    button = QPushButton(str(tmp_path / "generated-private-path"))
    layout.addWidget(button)
    window.show()
    qtbot.waitExposed(window)
    with pytest.raises(RuntimeError,match="filesystem path"):
        recorder.assert_visible_privacy(window,tmp_path)


@pytest.mark.parametrize("failure",["timeout","oserror"])
def test_docker_transport_failures_do_not_put_generated_password_in_traceback(recorder,monkeypatch,failure):
    service = recorder.OwnedMongoDemo()
    arguments = ["docker","run","-e","MONGO_INITDB_ROOT_PASSWORD="+service.password]
    def unavailable(*args,**kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments,120)
        raise OSError("Generated launch failure: "+service.password)
    monkeypatch.setattr(recorder.subprocess,"run",unavailable)
    with pytest.raises(RuntimeError) as captured:
        service._docker(*arguments[1:])
    rendered = "".join(traceback.format_exception(captured.value))
    safe = service.password not in rendered and "MONGO_INITDB_ROOT_PASSWORD=" not in rendered
    assert safe, "Docker transport failure exposed its generated credential argv"
    assert captured.value.__suppress_context__ is True


def _fake_owned_start(recorder,monkeypatch,*,seed_delay=0):
    """No Docker process: isolate which native timeout scope each phase sees."""
    from pymongo import _csot
    import opennavicat.mongodb_session as module

    observed, closed = [], []
    class ReadySession:
        def __init__(self,config):
            self.connection = self
        def connect(self):
            observed.append(("connect",_csot.remaining()))
        def server_info(self):
            observed.append(("server_info",_csot.remaining()))
            return {"version":"generated-fixture"}
        def close(self):
            closed.append(True)
    monkeypatch.setattr(module,"MongoSession",ReadySession)
    service = recorder.OwnedMongoDemo()
    def docker(*args,**kwargs):
        if args[0] == "run":
            return "generated-container"
        if args[0] == "image":
            return json.dumps([{"Architecture":"arm64"}])
        if args[0] == "info":
            return "arm64"
        pytest.fail("No other Docker commands belong to the readiness test")
    monkeypatch.setattr(service,"_docker",docker)
    monkeypatch.setattr(service,"inspect",lambda **kwargs:{"Image":"generated-image",
        "NetworkSettings":{"Ports":{"27017/tcp":[{"HostIp":"127.0.0.1","HostPort":"54321"}]}}})
    def seed(session,*,deadline):
        observed.append(("seed",_csot.remaining()))
        assert 0 < recorder.remaining_seconds(deadline) <= .2
        if seed_delay:
            time.sleep(seed_delay)
    monkeypatch.setattr(service,"_seed",seed)
    return service, observed, closed


def test_same_remaining_native_deadline_covers_connect_server_info_and_seed(recorder,monkeypatch):
    service, observed, closed = _fake_owned_start(recorder,monkeypatch)
    service.start(readiness_seconds=.2)
    assert [phase for phase,_ in observed] == ["connect","server_info","seed"]
    assert all(remaining is not None and 0 < remaining <= .2 for _,remaining in observed)
    assert observed[-1][1] <= observed[0][1]
    assert closed == [True]


def test_seed_deadline_expiry_fails_and_closes_probe(recorder,monkeypatch):
    service, observed, closed = _fake_owned_start(recorder,monkeypatch,seed_delay=.08)
    with pytest.raises(RuntimeError,match="readiness deadline expired"):
        service.start(readiness_seconds=.04)
    assert observed[-1][0] == "seed" and closed == [True]


def test_real_pymongo_server_info_wait_is_bounded_by_remaining_budget(recorder):
    """Native TCP accepts MongoDB's hello but never answers; no external DB."""
    from pymongo import MongoClient, timeout
    from pymongo.errors import PyMongoError

    stop = threading.Event()
    server = socket.socket()
    server.bind(("127.0.0.1",0))
    server.listen()
    server.settimeout(.1)
    sockets = []
    def peer():
        while not stop.is_set():
            try:
                connection,_ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sockets.append(connection)
    thread = threading.Thread(target=peer,daemon=True)
    thread.start()
    client = MongoClient("127.0.0.1",server.getsockname()[1],connect=False,
                         connectTimeoutMS=60000,serverSelectionTimeoutMS=60000,timeoutMS=120000)
    started = time.monotonic()
    try:
        with pytest.raises(PyMongoError):
            with timeout(recorder.remaining_seconds(started+.15)):
                client.server_info()
        assert time.monotonic()-started < 3
        assert sockets  # The actual PyMongo client reached the owned TCP peer.
    finally:
        client.close()
        stop.set()
        server.close()
        thread.join(timeout=2)
        for connection in sockets:
            connection.close()
        assert not thread.is_alive()
