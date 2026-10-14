"""CP31 regressions for results readiness, preflight and strict JSON types."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from video_maker.api.control import apply_settings, find_results
from video_maker.api.queue import SeriesQueue
from video_maker.api.server import VideoMakerAPI
from video_maker.config.settings import Settings


class FakeVar:
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class FakeApp:
    def __init__(self):
        self.settings = Settings()
        self.remove_long_pauses_var = FakeVar(False)


class DummyBridge:
    def call(self, fn, timeout=30):
        return fn()


class DummyStatus:
    def snapshot(self, log_tail=0):
        return {"running": False}


class DummyQueue:
    def __init__(self):
        self.on_finish = None
        self.paused = False
    def pause(self, value):
        self.paused = value
    def snapshot(self):
        return {"items": [], "paused": self.paused}


def test_metadata_only_folder_is_not_ready(tmp_path):
    output = tmp_path / "output"
    series = output / "Episode 1"
    series.mkdir(parents=True)
    (series / "info_metadata.txt").write_text("series: Episode 1", encoding="utf-8")
    result = find_results(str(output), "Episode 1")
    assert result["series_dir"] == str(series)
    assert result["metadata"]
    assert result["ready"] is False
    assert result["video_files"] == []


def test_result_readiness_requires_nonempty_video_file(tmp_path):
    output = tmp_path / "output"
    series = output / "Episode 1"
    series.mkdir(parents=True)
    (series / "info_metadata.txt").write_text("series: Episode 1", encoding="utf-8")
    (series / "rendering.tmp").write_bytes(b"x" * 8000)
    result = find_results(str(output), "Episode 1")
    assert result["ready"] is False
    (series / "final_16x9.mp4").write_bytes(b"0" * 2048)
    result = find_results(str(output), "Episode 1")
    assert result["ready"] is True
    assert len(result["video_files"]) == 1


def test_api_bool_setting_rejects_string_false_without_mutation():
    app = FakeApp()
    app.settings.remove_long_pauses = False
    applied, unknown = apply_settings(app, {"remove_long_pauses": "false"})
    assert "remove_long_pauses" not in applied
    assert "remove_long_pauses" in unknown
    assert app.settings.remove_long_pauses is False
    assert app.remove_long_pauses_var.value is False


def test_api_bool_setting_accepts_json_boolean():
    app = FakeApp()
    applied, unknown = apply_settings(app, {"remove_long_pauses": True})
    assert "remove_long_pauses" in applied
    assert unknown == []
    assert app.settings.remove_long_pauses is True
    assert app.remove_long_pauses_var.value is True


def test_malformed_queue_roots_do_not_crash_startup(tmp_path):
    for payload in ("1", '"not-a-queue"', "null", '{"items": 1, "paused": true}'):
        path = tmp_path / "queue.json"
        path.write_text(payload, encoding="utf-8")
        queue = SeriesQueue(object(), DummyBridge(), DummyStatus(), str(path))
        assert queue.snapshot()["total"] == 0
        assert queue.snapshot()["paused"] is False


def test_queue_paused_string_is_not_truthy_coerced(tmp_path):
    path = tmp_path / "queue.json"
    path.write_text(json.dumps({"paused": "false", "items": []}), encoding="utf-8")
    queue = SeriesQueue(object(), DummyBridge(), DummyStatus(), str(path))
    assert queue.snapshot()["paused"] is False


def test_webhook_allow_duplicate_requires_boolean():
    queue = DummyQueue()
    api = VideoMakerAPI(object(), DummyBridge(), DummyStatus(), queue,
                        {"enabled": True, "port": 0, "lan": False, "token": "test"})
    code, payload = api.do_webhook_render({"audio": "/tmp/a.wav", "allow_duplicate": "false"})
    assert code == 400
    assert "boolean" in payload["error"]
    assert queue.snapshot()["items"] == []


def test_empty_vertical_broll_is_rejected_in_preflight(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    horizontal = tmp_path / "horizontal"
    vertical = tmp_path / "vertical"
    output = tmp_path / "output"
    horizontal.mkdir()
    vertical.mkdir()
    output.mkdir()
    (horizontal / "landscape.mp4").write_bytes(b"dummy")

    settings = Settings(
        gemini_api_key="test-key",
        audio_path=str(audio),
        broll_horizontal=str(horizontal),
        broll_vertical=str(vertical),
        output_folder=str(output),
        bgm_youtube_enable=False,
        bgm_tiktok_enable=False,
        bgm_instagram_enable=False,
        bgm_rutube_enable=False,
        bgm_vk_enable=False,
        bgm_facebook_enable=False,
        bgm_other_enable=False,
        vertical_background="",
    )
    errors = settings.validate()
    assert any("вертикального B-roll не содержит видео" in e for e in errors)


def test_vertical_broll_preflight_accepts_video_file(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    horizontal = tmp_path / "horizontal"
    vertical = tmp_path / "vertical"
    output = tmp_path / "output"
    for d in (horizontal, vertical, output): d.mkdir()
    (horizontal / "landscape.mp4").write_bytes(b"dummy")
    (vertical / "portrait.mp4").write_bytes(b"dummy")
    settings = Settings(
        gemini_api_key="test-key", audio_path=str(audio),
        broll_horizontal=str(horizontal), broll_vertical=str(vertical),
        output_folder=str(output), bgm_youtube_enable=False,
        bgm_tiktok_enable=False, bgm_instagram_enable=False,
        bgm_rutube_enable=False, bgm_vk_enable=False,
        bgm_facebook_enable=False, bgm_other_enable=False,
        vertical_background="",
    )
    errors = settings.validate()
    assert not any("вертикального B-roll не содержит видео" in e for e in errors)


def test_http_queue_pause_rejects_string_boolean():
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from video_maker.api.server import _make_handler

    queue = DummyQueue()
    api = VideoMakerAPI(object(), DummyBridge(), DummyStatus(), queue,
                        {"enabled": True, "port": 0, "lan": False,
                         "token": "cp31-test-token-0123456789abcdef", "webhook_url": ""})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(api))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        body = json.dumps({"paused": "false"})
        conn.request("POST", "/api/queue/pause", body=body,
                     headers={"Content-Type": "application/json",
                              "X-Api-Key": api.conf["token"]})
        response = conn.getresponse()
        payload = json.loads(response.read().decode())
        conn.close()
        assert response.status == 400
        assert queue.paused is False
        assert payload["ok"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
