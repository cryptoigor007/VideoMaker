"""Регрессии для партии исправлений r58: API-защита, очередь, чекпоинт,
порядок кодирования, чистка промежуточных файлов, питание и защита шортов.

Каждый тест сформулирован на желаемое поведение после исправления.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]


# ── API: устойчивость авторизации ─────────────────────────────────────────
def test_check_key_rejects_non_ascii_without_crashing():
    from video_maker.api.server import VideoMakerAPI

    api = VideoMakerAPI(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
        {"enabled": True, "lan": False, "token": "T" * 32},
    )
    ok, _ = api.check_key("T" * 32, "127.0.0.1")
    assert ok is True
    ok, why = api.check_key("ключ-тест", "127.0.0.1")
    assert ok is False and why


# ── Очередь: область действия отмены и тип overrides ─────────────────────
def _make_queue(tmp_path: Path):
    from video_maker.api.queue import SeriesQueue

    return SeriesQueue(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
                       str(tmp_path / "queue.json"))


def _running_item():
    return {
        "id": "item-1", "audio": "/tmp/a.m4a", "output": str(Path("/tmp/out")),
        "series": "s", "note": "", "overrides": {},
        "status": "running", "started_at": time.time(), "finished_at": 0.0,
        "attempts": 1, "error": "", "result": {},
    }


def test_queue_cancel_is_scoped_to_the_running_item(tmp_path):
    import video_maker.api.queue as q

    queue = _make_queue(tmp_path)
    item = _running_item()
    queue._cancel_pending = True
    queue._cancel_target = "another-item"
    original = q.find_results
    q.find_results = lambda output, series, since: {"ready": False, "total_bytes": 0}
    try:
        queue._finish(item, {"running": False, "activity": "x", "cancel_requested": False})
        assert item["status"] == q.FAILED, "чужая отмена не должна помечать серию отменённой"
        assert queue._cancel_pending is False
    finally:
        q.find_results = original


def test_queue_cancel_targeting_this_item_is_honored(tmp_path):
    import video_maker.api.queue as q

    queue = _make_queue(tmp_path)
    item = _running_item()
    queue._cancel_pending = True
    queue._cancel_target = "item-1"
    original = q.find_results
    q.find_results = lambda output, series, since: {"ready": False, "total_bytes": 0}
    try:
        queue._finish(item, {"running": False, "activity": "x", "cancel_requested": False})
        assert item["status"] == q.CANCELED
    finally:
        q.find_results = original


def test_queue_done_clears_pending_cancellation(tmp_path):
    import video_maker.api.queue as q

    queue = _make_queue(tmp_path)
    item = _running_item()
    queue._cancel_pending = True
    queue._cancel_target = "item-1"
    original = q.find_results
    q.find_results = lambda output, series, since: {"ready": True, "total_bytes": 123}
    try:
        queue._finish(item, {"running": False, "activity": "x", "cancel_requested": False})
        assert item["status"] == q.DONE
        assert queue._cancel_pending is False
    finally:
        q.find_results = original


def test_queue_rejects_non_object_overrides(tmp_path):
    queue = _make_queue(tmp_path)
    with pytest.raises(ValueError):
        queue.add("/tmp/a.m4a", overrides=[1, 2])


# ── API: отдельный токен вебхука не равен токену пульта ──────────────────
def test_load_config_generates_separate_webhook_token(tmp_path, monkeypatch):
    import json

    import video_maker.api.server as server

    monkeypatch.setattr(server, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH", str(tmp_path / "api.json"))
    conf = server.load_config()
    assert conf["webhook_token"] and conf["webhook_token"] != conf["token"]
    saved = json.loads((tmp_path / "api.json").read_text(encoding="utf-8"))
    assert saved["webhook_token"] == conf["webhook_token"]
    again = server.load_config()
    assert again["webhook_token"] == conf["webhook_token"], "токен вебхука стабилен между запусками"


# ── Публикация временных файлов через границу дисков ─────────────────────
def test_publish_work_file_handles_cross_device_link(tmp_path, monkeypatch):
    import video_maker.engines.video as video

    src = tmp_path / "vstack_tmp.mp4"
    src.write_bytes(b"vertical-media" * 100)
    dst = tmp_path / "final_9x16.mp4"

    def exdev(_source, _destination):
        raise OSError(18, "Cross-device link")

    monkeypatch.setattr(video, "atomic_replace", exdev)
    video._publish_work_file(str(src), str(dst), log_fn=lambda m: None)
    assert dst.is_file() and dst.read_bytes().startswith(b"vertical-media"), \
        "через границу дисков файл должен доехать копированием"
    assert not src.exists(), "временный файл не должен оставаться"


def test_publish_work_file_normal_path_is_atomic(tmp_path, monkeypatch):
    import video_maker.engines.video as video

    src = tmp_path / "vstack_tmp.mp4"
    src.write_bytes(b"media" * 100)
    dst = tmp_path / "final_9x16.mp4"
    calls = []
    real_replace = os.replace

    def spy(source, destination):
        calls.append((str(source), str(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(video, "atomic_replace", spy)
    video._publish_work_file(str(src), str(dst), log_fn=lambda m: None)
    assert calls and dst.is_file() and not src.exists()


# ── atomic_replace: перенос через границу файловых систем ────────────────
def test_atomic_replace_falls_back_to_move_on_cross_device(tmp_path, monkeypatch):
    import video_maker.engines.ffmpeg_resilient as fr

    src = tmp_path / "tmp.mp4"
    src.write_bytes(b"media" * 100)
    dst = tmp_path / "final.mp4"

    def exdev(_a, _b):
        raise OSError(18, "Cross-device link")

    monkeypatch.setattr(fr.os, "replace", exdev)
    fr.atomic_replace(str(src), str(dst))
    assert dst.is_file() and dst.read_bytes().startswith(b"media"), \
        "при EXDEV файл обязан доехать копированием"
    assert not src.exists(), "временный файл не должен оставаться"


# ── API: запрос статуса в момент закрытия — не traceback, а 503 ──────────
def test_status_request_during_shutdown_raises_503():
    from types import SimpleNamespace

    from video_maker.api.server import ApiRequestError, VideoMakerAPI

    class ClosingStatus:
        def snapshot(self, log_tail: int = 0):
            raise RuntimeError("TkBridge остановлен: окно закрывается")

    api = VideoMakerAPI(
        SimpleNamespace(), SimpleNamespace(), ClosingStatus(), SimpleNamespace(),
        {"enabled": True, "token": "T" * 24},
    )
    with pytest.raises(ApiRequestError) as excinfo:
        api.status_payload(0)
    assert excinfo.value.status == 503


# ── Checkpoint: инвентаризация не считает used/ изменением ────────────────
def test_path_inventory_ignores_used_folder(tmp_path):
    from video_maker.pipeline.checkpoint import _path_inventory

    broll = tmp_path / "broll"
    (broll / "used").mkdir(parents=True)
    (broll / "a.mp4").write_bytes(b"x" * 4096)
    before = _path_inventory(str(broll))
    (broll / "used" / "b.mp4").write_bytes(b"y" * 4096)
    after = _path_inventory(str(broll))
    assert before == after, "перенос клипов в used/ не должен менять отпечаток"
    (broll / "a.mp4").write_bytes(b"z" * 4096)
    changed = _path_inventory(str(broll))
    assert changed != before, "реальное изменение клипа должно менять отпечаток"


# ── Vstack: защита от пустого результата ─────────────────────────────────
def test_vstack_rejects_unknown_duration_and_verifies(tmp_path, monkeypatch):
    import video_maker.engines.video as video

    monkeypatch.setattr(video, "_cached_bg_half", lambda *a, **k: "bg.png")
    monkeypatch.setattr(video, "probe_duration", lambda path: 0.0)
    calls = []
    monkeypatch.setattr(video.subprocess, "run", lambda *a, **k: calls.append(a) or
                        subprocess.CompletedProcess(a, 1, "", "should not run"))
    with pytest.raises(RuntimeError) as excinfo:
        video.vstack_video_image("v.mp4", "bg.png", str(tmp_path / "out.mp4"))
    assert "длительность" in str(excinfo.value).lower()
    assert calls == [], "ffmpeg не должен запускаться при неизвестной длительности"


# ── Хуки шортов: без CTA-зоны хук обязан остаться ────────────────────────
def test_short_hook_is_not_dropped_when_clip_has_no_cta():
    from video_maker.engines.subtitles import _build_hook_events

    clip = {"start": 120.0, "end": 150.0, "hook": "ТЕСТ ХУКА", "cta": ""}
    events = _build_hook_events(
        {}, 1080, 1920, False, 60, hook_style="marker",
        clip=clip, cta_start=0, video_duration=6.0, log_fn=lambda m: None,
    )
    assert events, "хук шорта пропал при отсутствии CTA"
    assert events[0]["start"] == pytest.approx(120.0)
    assert events[0]["end"] <= 126.0


# ── Gemini middle: невалидные значения не роняют рендер ──────────────────
def test_normalize_analysis_sanitizes_middle():
    from video_maker.engines.analysis import _normalize_analysis

    out = _normalize_analysis(
        {"middle": ["15", {"start": "7.5", "end": 9, "path": "x.mp4"}, 5, {"start": "bad"}]},
        [],
        {},
    )
    assert out["middle"] == [{"start": 7.5, "end": 9.0, "path": "x.mp4"}]


# ── Настройки: галочка выравнивания темпа (по умолчанию выключена) ───────
def test_align_speech_rate_defaults_to_off_like_before_and_is_wired():
    from video_maker.config.settings import Settings
    from video_maker.api.control import SETTINGS_VAR_MAP

    assert Settings().align_speech_rate is False, "по умолчанию — как в прежних версиях"
    assert Settings(align_speech_rate=True).align_speech_rate is True
    assert SETTINGS_VAR_MAP.get("align_speech_rate") == "align_speech_rate_var"


def test_transcribe_stage_respects_disabled_alignment(tmp_path, monkeypatch):
    import video_maker.engines.transcription as transcription
    from video_maker.pipeline.stages import PipelineContext, TranscribeStage

    wav = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "sine=frequency=200:duration=1",
        "-c:a", "pcm_s16le", str(wav),
    ], check=True)
    monkeypatch.setattr(transcription, "transcribe", lambda *a, **k: {
        "segments": [{"start": 0.0, "end": 0.5, "text": "ок", "words": [
            {"word": "ок", "start": 0.0, "end": 0.5, "probability": 1.0}]}],
        "language": "ru", "text": "ок", "engine": "mock", "model": "mock",
    })
    out = tmp_path / "out"
    out.mkdir()
    ctx = PipelineContext(
        audio_path=str(wav), output_folder=str(out),
        align_speech_rate=False, voice_enhance=False,
        log_callback=lambda m: None,
    )
    ctx = TranscribeStage().run(ctx)
    assert ctx.narration_path == "", "при выключенном выравнивании рендер идёт из исходного аудио"
    assert ctx.speech_metrics.get("status") == "disabled_by_setting"
    assert not list((out / "_tmp").glob("narration_prepared_*.wav")) if (out / "_tmp").exists() else True


# ── Шорты: сбой записи meta.txt не должен выбрасывать готовый шорт ───────
def test_short_metadata_write_failure_is_non_fatal(tmp_path):
    from video_maker.pipeline.shorts import ShortsCutter

    cutter = ShortsCutter()
    ctx = SimpleNamespace(series_name="S", log=lambda m: None)
    cutter._write_metadata({"hook": "h", "start": 1, "end": 5}, ctx,
                           "/нет_такой_папки_12345/meta.txt")


# ── Питание: задержка выключения соблюдается ─────────────────────────────
def test_shutdown_computer_honors_delay(monkeypatch):
    import video_maker.engines.power as power

    sleeps: list[float] = []
    monkeypatch.setattr(power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(power.time, "sleep", lambda s: sleeps.append(float(s)))
    runs: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        runs.append([str(x) for x in cmd])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(power.subprocess, "run", fake_run)
    messages: list[str] = []
    assert power.shutdown_computer(delay_sec=30, log_fn=messages.append) is True
    assert sum(sleeps) >= 30, f"выключение должно ждать полные 30 с, получили {sum(sleeps)}"
    assert runs and "osascript" in runs[0][0], "после паузы выключение запускается"
    assert any("30" in m for m in messages)


# ── Wide-финал не должен забирать мастер ─────────────────────────────────
def test_final_horizontal_without_burn_keeps_master(tmp_path):
    from video_maker.pipeline.branches import FinalHorizontal
    from video_maker.pipeline.stages import PipelineContext

    root = tmp_path / "out"
    tmp = root / "_tmp"
    tmp.mkdir(parents=True)
    master = tmp / "master_16x9.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=0.4",
        "-pix_fmt", "yuv420p", str(master),
    ], check=True)
    ctx = PipelineContext(
        output_folder=str(root), master_horizontal=str(master),
        analysis={}, transcription={},
        voice_enhance=False, log_callback=lambda m: None,
    )
    ctx = FinalHorizontal().run(ctx)
    assert os.path.isfile(master), "мастер должен остаться на месте для вертикальной ветки"
    assert ctx.final_horizontal and os.path.isfile(ctx.final_horizontal)


# ── Чистка: intermediates в output/_tmp удаляются при keep_temp_files=False ─
def test_cleanup_output_staging_removes_intermediates_only_when_allowed(tmp_path):
    from video_maker.pipeline.finalize import _cleanup_output_staging

    for keep in (False, True):
        out = tmp_path / f"out_{keep}"
        tmp = out / "_tmp"
        tmp.mkdir(parents=True)
        (tmp / "master_16x9.mp4").write_bytes(b"x" * 1000)
        (tmp / "sub").mkdir()
        (tmp / "sub" / "junk.tmp").write_bytes(b"x")
        _cleanup_output_staging(str(out), keep, log_fn=lambda m: None)
        remaining = list(tmp.rglob("*")) if tmp.exists() else []
        if keep:
            assert remaining, "keep_temp_files=True обязан сохранять промежуточные файлы"
        else:
            assert not remaining and not tmp.exists(), "при выключенном флаге _tmp очищается"


def test_cleanup_output_staging_refuses_symlinks(tmp_path):
    from video_maker.pipeline.finalize import _cleanup_output_staging

    out = tmp_path / "out"
    out.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "keep.txt").write_text("data")
    (out / "_tmp").symlink_to(real, target_is_directory=True)
    _cleanup_output_staging(str(out), False, log_fn=lambda m: None)
    assert (real / "keep.txt").is_file(), "чистка не должна ходить через симлинк"
