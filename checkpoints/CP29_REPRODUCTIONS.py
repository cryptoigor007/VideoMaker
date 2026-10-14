#!/usr/bin/env python3
"""CP29 audit repros for CP28. Safe: uses temp directories and loopback only.

This is an audit harness, not a production test suite. Findings are printed as
observed behavior so the baseline can be repeated before and after fixes.
Run from the project root: python checkpoints/CP29_REPRODUCTIONS.py
"""
from __future__ import annotations

import json
import socket
import tempfile
import shutil
import subprocess
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from video_maker.api.bridge import TkBridge  # noqa: E402
from video_maker.api.control import apply_settings, find_results  # noqa: E402
from video_maker.api.queue import SeriesQueue  # noqa: E402
from video_maker.api.server import VideoMakerAPI  # noqa: E402
from video_maker.config.settings import Settings  # noqa: E402
from video_maker.pipeline import checkpoint as cp  # noqa: E402
import video_maker.api.server as server_module  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def emit(name: str, data: dict) -> None:
    print(f"[{name}] {json.dumps(data, ensure_ascii=False, sort_keys=True)}")


class FakeVar:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class FakeBridge:
    def call(self, fn, timeout=30):
        return fn()


class FakeStatus:
    def snapshot(self, log_tail=0):
        return {"running": False, "activity": "idle", "log": []}


class FakeRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, ms, fn):
        self.callbacks.append(fn)
        return len(self.callbacks)


def repro_disabled_api_still_binds() -> None:
    port = free_port()
    api = VideoMakerAPI(
        app=None, bridge=None, status=None,
        queue=SimpleNamespace(on_finish=None),
        conf={"enabled": False, "port": port, "lan": False,
              "token": "a" * 32, "webhook_url": ""},
    )
    url = api.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
            payload = json.load(response)
            emit("api_disabled_still_listens", {
                "enabled_conf": False,
                "start_returned_url": bool(url),
                "health_status": response.status,
                "listener_alive": api._httpd is not None,
                "health_reports_enabled": payload.get("api", {}).get("enabled"),
            })
    finally:
        api.stop()


def repro_metadata_only_looks_ready() -> None:
    with tempfile.TemporaryDirectory(prefix="cp29-results-") as temp:
        series = Path(temp) / "series-x"
        series.mkdir()
        (series / "info_metadata.txt").write_text("metadata only", encoding="utf-8")
        result = find_results(temp, "series-x", 0)
        media = [p.name for p in series.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
        emit("metadata_only_is_ready", {
            "ready": result["ready"],
            "total_bytes": result["total_bytes"],
            "metadata_exists": bool(result["metadata"]),
            "video_files": media,
        })


def repro_run_reports_started_without_running() -> None:
    app = SimpleNamespace(settings=SimpleNamespace(validate=lambda: []), _start=lambda: None)
    api = VideoMakerAPI(
        app=app, bridge=FakeBridge(), status=FakeStatus(),
        queue=SimpleNamespace(on_finish=None, snapshot=lambda: {"pending": 0, "items": []}),
        conf={"enabled": True, "port": 8765, "lan": False,
              "token": "b" * 32, "webhook_url": ""},
    )
    code, payload = api.do_run({})
    emit("run_claims_started_without_running", {
        "http_code": code,
        "response_started": payload.get("started"),
        "status_running": payload.get("status", {}).get("running"),
    })


def repro_string_false_coercion() -> None:
    settings = Settings()
    app = SimpleNamespace(settings=settings, remove_long_pauses_var=FakeVar())
    applied, unknown = apply_settings(app, {"remove_long_pauses": "false"})
    emit("string_false_coerced", {
        "applied": applied,
        "unknown": unknown,
        "settings_value": settings.remove_long_pauses,
        "gui_var_value": app.remove_long_pauses_var.value,
    })


def repro_bridge_executes_after_timeout() -> None:
    root = FakeRoot()
    bridge = TkBridge(root, interval_ms=100000)
    side_effects = []
    try:
        try:
            bridge.call(lambda: side_effects.append("late side effect"), timeout=0.01)
            raised = False
        except TimeoutError:
            raised = True
        before_pump = list(side_effects)
        root.callbacks[-1]()
        emit("bridge_late_callback", {
            "caller_received_timeout": raised,
            "side_effects_before_pump": before_pump,
            "side_effects_after_pump": side_effects,
        })
    finally:
        bridge.stop()


def repro_failed_checkpoint_artifact_save() -> None:
    with tempfile.TemporaryDirectory(prefix="cp29-checkpoint-") as temp:
        out = Path(temp)
        narration = out / "narration.wav"
        narration.write_bytes(b"prepared-audio-placeholder")
        ctx = SimpleNamespace(
            output_folder=temp, transcription={"text": "test", "segments": [{"text": "x", "words": [{"word": "x"}]}]},
            analysis={"ok": True}, _completed_stages=["AudioStage", "TranscribeStage", "GeminiStage"],
            audio_path="/tmp/source.wav", narration_path=str(narration), voice_enhance=False,
            voice_enhance_applied=False, speech_metrics={}, series_name="series", audio_duration=1.0,
            master_horizontal="", master_vertical="", final_horizontal="", final_vertical="",
            shorts=[], short_indices=[], bgm_mixed=False, bgm_source_video="", h_did_imo=False,
            progress=50, log=lambda _message: None,
        )
        original = cp._atomic_write

        def fail_transcription(path, data):
            if "checkpoint_transcription.json" in path:
                raise OSError("simulated disk full")
            return original(path, data)

        with patch.object(cp, "_atomic_write", side_effect=fail_transcription):
            cp.save_checkpoint(ctx, "GeminiStage", log_fn=lambda _message: None)
        data = cp.load_checkpoint(temp)
        restored = SimpleNamespace(
            output_folder=temp, voice_enhance=False, narration_path="", transcription={}, analysis={},
            shorts=[], short_indices=[], progress=0,
        )
        cp.apply_checkpoint_to_ctx(restored, data, log_fn=lambda _message: None)
        next_i = cp.next_stage_index(restored._completed_stages)
        emit("failed_checkpoint_artifact_still_completed", {
            "transcription_file": data.get("transcription_file"),
            "completed_stages": restored._completed_stages,
            "restored_transcription": restored.transcription,
            "next_stage": cp.STAGE_ORDER[next_i] if next_i < len(cp.STAGE_ORDER) else "DONE",
        })


def repro_checkpoint_ignores_pause_setting() -> None:
    with tempfile.TemporaryDirectory(prefix="cp29-resume-settings-") as temp:
        out = Path(temp)
        narration = out / "prepared.wav"
        narration.write_bytes(b"audio")
        tr = out / "transcription.json"
        tr.write_text(json.dumps({"text": "hello", "segments": [{"text": "hello", "words": [{"word": "hello"}]}]}), encoding="utf-8")
        an = out / "analysis.json"
        an.write_text(json.dumps({"ok": True}), encoding="utf-8")
        data = {
            "completed_stages": ["AudioStage", "TranscribeStage", "GeminiStage"],
            "voice_enhance_requested": False,
            "narration_path": str(narration),
            "transcription_file": str(tr), "analysis_file": str(an),
            "shorts": [], "short_indices": [],
        }
        restored = SimpleNamespace(
            output_folder=temp, voice_enhance=False, remove_long_pauses=True,
            narration_path="", transcription={}, analysis={}, shorts=[], short_indices=[], progress=0,
        )
        cp.apply_checkpoint_to_ctx(restored, data, log_fn=lambda _message: None)
        emit("checkpoint_ignores_pause_toggle", {
            "current_remove_long_pauses": restored.remove_long_pauses,
            "saved_pause_setting": data.get("remove_long_pauses"),
            "completed_stages": restored._completed_stages,
        })


def repro_malformed_config_and_queue_roots() -> None:
    with tempfile.TemporaryDirectory(prefix="cp29-bad-config-") as temp:
        config = Path(temp) / "api.json"
        config.write_text("1", encoding="utf-8")
        try:
            with patch.object(server_module, "CONFIG_PATH", str(config)):
                server_module.load_config()
            config_error = "no exception"
        except Exception as exc:  # expected reproduction
            config_error = f"{type(exc).__name__}: {exc}"
        queue = Path(temp) / "queue.json"
        queue.write_text("1", encoding="utf-8")
        try:
            SeriesQueue(app=None, bridge=None, status=None, path=str(queue))
            queue_error = "no exception"
        except Exception as exc:
            queue_error = f"{type(exc).__name__}: {exc}"
        emit("malformed_json_roots", {"api_config": config_error, "queue_json": queue_error})


def repro_empty_vertical_broll_preflight() -> None:
    with tempfile.TemporaryDirectory(prefix="cp29-broll-") as temp:
        root = Path(temp)
        audio = root / "source.wav"
        audio.write_bytes(b"x" * 2048)
        horizontal = root / "horizontal"
        vertical = root / "vertical"
        output = root / "output"
        horizontal.mkdir()
        vertical.mkdir()
        output.mkdir()
        # Put one valid horizontal video in the folder so the GUI's H-only count
        # precheck would pass; leave the configured vertical folder empty.
        clip = horizontal / "horizontal_sample.mp4"
        ffmpeg = shutil.which("ffmpeg")
        made_real_clip = False
        if ffmpeg:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "color=c=black:s=640x360:d=1",
                   "-an", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                made_real_clip = proc.returncode == 0 and clip.is_file() and clip.stat().st_size > 1000
            except Exception:
                made_real_clip = False
        if not made_real_clip:
            clip.write_bytes(b"synthetic placeholder; horizontal video probe not run")
        output.mkdir(exist_ok=True)
        # Settings.validate only checks the vertical directory path, not that the
        # configured vertical directory contains readable portrait media.
        settings = Settings(
            gemini_api_key="test-key", audio_path=str(audio),
            broll_horizontal=str(horizontal), broll_vertical=str(vertical), output_folder=str(output),
            bgm_youtube_enable=False, bgm_tiktok_enable=False, bgm_instagram_enable=False,
            bgm_rutube_enable=False, bgm_vk_enable=False, bgm_facebook_enable=False, bgm_other_enable=False,
        )
        from video_maker.engines.video import collect_video_files
        emit("empty_vertical_broll_passes_settings_validate", {
            "errors": settings.validate(),
            "horizontal_collect_video_count": len(collect_video_files(str(horizontal))),
            "horizontal_sample_real_mp4": made_real_clip,
            "vertical_collect_video_count": len(collect_video_files(str(vertical))),
            "vertical_processing_requested": bool(settings.v_enable_hooks or settings.v_enable_subtitles or settings.s_enable_hooks),
        })


def repro_rebind_failure_loses_old_listener() -> None:
    old_port = free_port()
    occupied = socket.socket()
    occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    busy_port = int(occupied.getsockname()[1])
    api = VideoMakerAPI(
        app=None, bridge=None, status=None, queue=SimpleNamespace(on_finish=None),
        conf={"enabled": True, "port": old_port, "lan": False,
              "token": "c" * 32, "webhook_url": ""},
    )
    started_url = api.start()
    try:
        with patch("video_maker.api.server.save_config", lambda _conf: None):
            status, payload = api.do_config_set({"port": busy_port})
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{old_port}/api/health", timeout=0.4)
            old_port_alive = True
        except Exception:
            old_port_alive = False
        emit("rebind_failure_loses_old_listener", {
            "initial_listener_started": bool(started_url), "requested_busy_port": busy_port,
            "config_http_code": status, "configured_port_after_failure": api.conf["port"],
            "old_port_alive_after_failure": old_port_alive,
            "returned_error": payload.get("error"),
        })
    finally:
        api.stop()
        occupied.close()


def main() -> None:
    repro_disabled_api_still_binds()
    repro_metadata_only_looks_ready()
    repro_run_reports_started_without_running()
    repro_string_false_coercion()
    repro_bridge_executes_after_timeout()
    repro_failed_checkpoint_artifact_save()
    repro_checkpoint_ignores_pause_setting()
    repro_malformed_config_and_queue_roots()
    repro_empty_vertical_broll_preflight()
    repro_rebind_failure_loses_old_listener()
    print("CP29 repro harness finished; observed behavior is documented in CP29_DEEP_AUDIT.md")


if __name__ == "__main__":
    main()
