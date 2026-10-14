"""CP30 regression tests: API listener lifecycle and honest start results."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import pytest

from video_maker.api.server import VideoMakerAPI, load_config


class DummyQueue:
    def __init__(self):
        self.on_finish = None
    def snapshot(self):
        return {"items": []}


class DummyStatus:
    def snapshot(self, log_tail=0):
        return {"running": False, "log_tail": []}


class InlineBridge:
    def call(self, fn, timeout=30):
        return fn()


class DummyApp:
    def __init__(self, started=False):
        self.running = started
        self.settings = object()


def api_for(app=None, port=0, enabled=True):
    conf = {"enabled": enabled, "port": port, "lan": False,
            "token": "cp30-test-token-0123456789abcdef", "webhook_url": ""}
    return VideoMakerAPI(app or DummyApp(), InlineBridge(), DummyStatus(), DummyQueue(), conf)


def port_is_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.15)
        try:
            sock.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_for_port(port: int, expected: bool, timeout=2.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if port_is_listening(port) is expected:
            return
        time.sleep(0.02)
    assert port_is_listening(port) is expected


def reserve_port() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_start_does_not_bind_when_disabled():
    api = api_for(enabled=False)
    with patch("video_maker.api.server._PultServer") as server_type:
        assert api.start() == ""
    server_type.assert_not_called()
    assert api._httpd is None
    assert api._thread is None


def test_config_disable_closes_listener_and_persists_disabled_state():
    api = api_for()
    url = api.start()
    assert url
    port = api._httpd.server_port
    wait_for_port(port, True)
    with patch("video_maker.api.server.save_config"):
        code, payload = api.do_config_set({"enabled": False})
    assert code == 200
    assert payload["ok"] is True
    assert api.conf["enabled"] is False
    assert api._httpd is None
    wait_for_port(port, False)


def test_config_rebind_failure_rolls_back_old_listener():
    occupied, bad_port = reserve_port()
    api = api_for(port=0)
    try:
        assert api.start()
        old_port = api._httpd.server_port
        old_conf = dict(api.conf)
        with patch("video_maker.api.server.save_config"):
            code, payload = api.do_config_set({"port": bad_port})
        assert code == 500
        assert payload["rolled_back"] is True
        assert api.conf == old_conf
        assert payload["previous_listener_restored"] is True
        assert api._httpd is not None
        assert api._httpd.server_port == old_port
        wait_for_port(old_port, True)
    finally:
        api.stop()
        occupied.close()


def test_do_run_does_not_claim_started_when_app_rejects_start():
    from video_maker.api import server
    app = DummyApp(started=False)
    api = api_for(app)
    with patch.object(server, "apply_settings", return_value=(["audio_path"], [])), \
         patch.object(server, "validate", return_value=[]), \
         patch.object(server, "start_pipeline", side_effect=lambda a: None):
        code, payload = api.do_run({"audio": "/nonexistent.wav"})
    assert code == 409
    assert payload["ok"] is False
    assert payload["started"] is False


def test_load_config_rejects_non_object_json_root(tmp_path):
    path = tmp_path / "api.json"
    path.write_text("1", encoding="utf-8")
    with patch("video_maker.api.server.CONFIG_PATH", str(path)), \
         patch("video_maker.api.server.save_config"):
        conf = load_config()
    assert conf["enabled"] is False
    assert conf["lan"] is False
    assert 1 <= conf["port"] <= 65535
    assert len(conf["token"]) >= 24


def test_start_stays_idempotent_when_already_running():
    api = api_for()
    try:
        first = api.start()
        httpd = api._httpd
        second = api.start()
        assert first == second
        assert api._httpd is httpd
    finally:
        api.stop()


def test_queue_marks_failed_if_gui_returns_without_starting(tmp_path):
    from video_maker.api import queue as queue_module
    from video_maker.api.queue import SeriesQueue, FAILED

    app = DummyApp(started=False)
    queue_obj = SeriesQueue(app, InlineBridge(), DummyStatus(), str(tmp_path / "queue.json"))
    item = {"id": "cp30-no-start", "audio": "/tmp/not-used.wav", "output": "",
            "series": "", "note": "", "overrides": {}, "status": "pending",
            "attempts": 0, "added_at": time.time(), "started_at": 0.0,
            "finished_at": 0.0, "error": ""}
    with patch.object(queue_module, "apply_settings", return_value=([], [])), \
         patch.object(queue_module, "validate", return_value=[]), \
         patch.object(queue_module, "start_pipeline", side_effect=lambda _app: None):
        queue_obj._launch(item)
    assert item["status"] == FAILED
    assert "не запустило обработку" in item["error"]
