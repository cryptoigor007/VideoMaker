"""P0-5: memory-gate (RAM-first) и resume-предикат чанков без реального рендера.

Покрывает:
* безопасные пороги/парсинг vm_stat/swapusage и env;
* RAM < 300 → пауза, RAM < 80 → MemoryPauseError с инструкцией «Продолжить»;
* swap 85–100% при свободной RAM — только WARN, не пауза и не стоп;
* swap >= 99% критичен только вместе с RAM < 150 МБ;
* кнопка «Продолжить»: can_continue() по RAM, а не по swap;
* `_chunk_record_ready` / `_chunk_resume_index` — предикат resume;
* синтетика: память в норме → рендер идёт; RAM<80 → пауза; повтор после
  освобождения → продолжение с первого неготового чанка.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.engines import memory_guard as mg  # noqa: E402
from video_maker.engines import video  # noqa: E402


def _state(free: int, swap: float = 0.0) -> mg.MemoryState:
    return mg.MemoryState(free_mb=free, swap_used_mb=int(swap),
                          swap_total_mb=100, swap_pct=swap)


def _clean_mem_env(monkeypatch) -> None:
    for name in ("VIDEOMAKER_MEM_MIN_FREE_MB", "VIDEOMAKER_MEM_MAX_SWAP_PCT",
                 "VIDEOMAKER_MEM_CRITICAL_FREE_MB", "VIDEOMAKER_MEM_CRITICAL_SWAP_PCT",
                 "VIDEOMAKER_MEM_CRITICAL_SWAP_FREE_MB",
                 "VIDEOMAKER_MEM_PAUSE_SEC", "VIDEOMAKER_MEM_MAX_PAUSES",
                 "VIDEOMAKER_MEM_SIM_FREE_MB", "VIDEOMAKER_MEM_SIM_SWAP_PCT"):
        monkeypatch.delenv(name, raising=False)


def test_parse_vm_stat_and_swapusage():
    vm = (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               1000.\n"
        "Pages active:                             2000.\n"
        "Pages inactive:                            500.\n"
        "Pages speculative:                         200.\n"
    )
    free_mb, page = mg.parse_vm_stat(vm)
    assert page == 16384
    assert free_mb == int((1000 + 500 + 200) * 16384 / (1024 * 1024))

    used, total = mg.parse_swapusage("total = 4096,00M  used = 2695,19M  free = 1400,81M")
    assert (used, total) == (2695, 4096)


def test_thresholds_safe_float_env(monkeypatch):
    _clean_mem_env(monkeypatch)
    assert mg.thresholds()[0] == mg.DEFAULT_MIN_FREE_MB
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "мусор")
    assert mg.thresholds()[0] == mg.DEFAULT_MIN_FREE_MB
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "250,5")
    assert mg.thresholds()[0] == pytest.approx(250.5)
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_PAUSES", "3.9")
    assert mg.thresholds()[5] == 3


def test_read_memory_state_simulation(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_SIM_FREE_MB", "123")
    monkeypatch.setenv("VIDEOMAKER_MEM_SIM_SWAP_PCT", "42")
    st = mg.read_memory_state()
    assert st is not None
    assert st.free_mb == 123 and st.swap_pct == 42.0
    assert st.as_text().startswith("RAM 123")


def test_wait_for_memory_pauses_then_passes(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "300")
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_SWAP_PCT", "85")
    monkeypatch.setenv("VIDEOMAKER_MEM_PAUSE_SEC", "0.01")
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_PAUSES", "3")

    seq = [_state(100), _state(120), _state(900)]
    sleeps: list[float] = []
    res = mg.wait_for_memory(reader=lambda: seq.pop(0), sleep_fn=sleeps.append)
    assert res is not None and res.free_mb == 900
    assert sleeps == [0.01]


def test_wait_for_memory_critical_raises(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_FREE_MB", "80")
    sleeps: list[float] = []
    logs: list[str] = []
    with pytest.raises(mg.MemoryPauseError) as exc_info:
        mg.wait_for_memory(reader=lambda: _state(10), sleep_fn=sleeps.append,
                           log_fn=logs.append)
    assert sleeps == []
    message = str(exc_info.value)
    assert message.startswith("[MEM] Недостаточно памяти (RAM 10 МБ, swap 0%)")
    assert "Закройте все приложения и нажмите «Продолжить»" in message
    assert any(message in line for line in logs)


def test_swap_warn_is_not_a_pause_or_stop(monkeypatch):
    """swap 90–100% при свободной RAM — только WARN, рендер должен идти."""
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "300")
    logs: list[str] = []
    sleeps: list[float] = []
    state = _state(900, 96)
    res = mg.evaluate_memory(state)
    assert res.level == "ok"
    assert res.swap_warning == "[MEM] swap 96% (нормально для macOS)"
    assert mg.wait_for_memory(reader=lambda: state, sleep_fn=sleeps.append,
                              log_fn=logs.append) is state
    assert sleeps == []
    assert "[MEM] swap 96% (нормально для macOS)" in logs
    assert not any("пауза" in line.lower() for line in logs)


def test_critical_swap_only_with_low_ram(monkeypatch):
    """swap >= 99% критичен только при RAM < 150 МБ (а не сам по себе)."""
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_SWAP_PCT", "99")
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_SWAP_FREE_MB", "150")
    assert mg.evaluate_memory(_state(200, 99.5)).level == "low"
    assert mg.evaluate_memory(_state(149, 99.5)).level == "critical"
    assert mg.evaluate_memory(_state(900, 99.5)).level == "ok"


def test_ram_thresholds_are_env_overridable(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "500")
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_FREE_MB", "120")
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_SWAP_PCT", "70")
    assert mg.evaluate_memory(_state(400, 95)).level == "low"
    assert mg.evaluate_memory(_state(100, 95)).level == "critical"
    check = mg.evaluate_memory(_state(600, 75))
    assert check.level == "ok"
    assert check.swap_warning == "[MEM] swap 75% (нормально для macOS)"


def test_can_continue_checks_ram_not_swap(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "300")
    ok, message = mg.can_continue(_state(320, 97))
    assert ok is True
    assert "Память в норме" in message

    ok, message = mg.can_continue(_state(120, 97))
    assert ok is False
    assert message == ("[MEM] Недостаточно памяти: RAM 120 МБ, swap 97% — "
                       "закройте приложения и повторите")

    ok, message = mg.can_continue(_state(40, 99))
    assert ok is False
    assert "RAM 40 МБ" in message and "повторите" in message


def test_wait_for_memory_max_pauses_continues(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_MIN_FREE_MB", "300")
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_SWAP_PCT", "85")
    monkeypatch.setenv("VIDEOMAKER_MEM_PAUSE_SEC", "0")
    monkeypatch.setenv("VIDEOMAKER_MEM_MAX_PAUSES", "2")
    sleeps: list[float] = []
    res = mg.wait_for_memory(reader=lambda: _state(100), sleep_fn=sleeps.append)
    assert res is not None and res.free_mb == 100
    assert sleeps == [0.0, 0.0]


def test_wait_for_ffmpeg_idle(monkeypatch):
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_FFMPEG_WAIT_SEC", "5")
    seq = [[11, 12], []]
    mg.wait_for_ffmpeg_idle(pids_fn=lambda: seq.pop(0) if seq else [],
                            sleep_fn=lambda _s: None)
    monkeypatch.setenv("VIDEOMAKER_FFMPEG_WAIT_SEC", "0")
    with pytest.raises(mg.FFmpegBusyError):
        mg.wait_for_ffmpeg_idle(pids_fn=lambda: [42], sleep_fn=lambda _s: None)


def _chunk(tmp_path: Path, size: int = 4096) -> Path:
    p = tmp_path / "chunk00.mp4"
    p.write_bytes(b"x" * size)
    return p


def test_chunk_record_ready_resume_predicate(tmp_path: Path, monkeypatch):
    p = _chunk(tmp_path)
    monkeypatch.setattr(video, "verify_mp4", lambda *_a, **_k: True)
    monkeypatch.setattr(video, "_probe_video_duration", lambda _p: 10.0)

    good = {"status": "done", "file": str(p), "duration": 10.0}
    assert video._chunk_record_ready(good) is True

    # Не готов: статус, отсутствующий/битый файл, несовпадение длительности.
    assert video._chunk_record_ready({**good, "status": "pending"}) is False
    assert video._chunk_record_ready({**good, "file": str(tmp_path / "нет.mp4")}) is False
    assert video._chunk_record_ready({**good, "duration": 20.0}) is False
    tiny = _chunk(tmp_path, size=512)
    assert video._chunk_record_ready({**good, "file": str(tiny)}) is False


def test_chunk_record_ready_rejects_corrupt_video(tmp_path: Path, monkeypatch):
    p = _chunk(tmp_path)
    monkeypatch.setattr(video, "verify_mp4", lambda *_a, **_k: False)
    monkeypatch.setattr(video, "_probe_video_duration", lambda _p: 10.0)
    assert video._chunk_record_ready(
        {"status": "done", "file": str(p), "duration": 10.0}) is False


def test_chunk_resume_index_points_to_first_pending(tmp_path: Path):
    assert video._chunk_resume_index([]) == 0
    chunks = [
        {"index": 0, "status": "done"},
        {"index": 1, "status": "done"},
        {"index": 2, "status": "pending"},
        {"index": 3, "status": "done"},
    ]
    assert video._chunk_resume_index(chunks) == 2
    assert video._chunk_resume_index([{"status": "done"}] * 3) == 3


def test_render_memory_pause_is_special_not_generic_error():
    err = video.RenderMemoryPause(
        "[MEM] Недостаточно памяти (RAM 40 МБ, swap 96%). "
        "Закройте все приложения и нажмите «Продолжить».",
        done_chunks=2, total_chunks=5, next_chunk=2,
        state=_state(40, 96),
    )
    assert isinstance(err, mg.MemoryPauseError)
    assert isinstance(err, mg.MemoryPressureError)
    assert not isinstance(err, mg.FFmpegBusyError)
    assert (err.done_chunks, err.total_chunks, err.next_chunk) == (2, 5, 2)
    assert "Продолжить" in str(err)


def test_synthetic_memory_release_resumes_from_pending_chunk(monkeypatch, tmp_path: Path):
    """RAM<80 → пауза; после освобождения повтор идёт с первого неготового чанка."""
    _clean_mem_env(monkeypatch)
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_FREE_MB", "80")
    monkeypatch.setenv("VIDEOMAKER_MEM_SIM_FREE_MB", "40")
    monkeypatch.setenv("VIDEOMAKER_MEM_SIM_SWAP_PCT", "96")

    logs: list[str] = []
    with pytest.raises(mg.MemoryPauseError):
        mg.wait_for_memory(log_fn=logs.append, sleep_fn=lambda _s: None)
    assert any("нажмите «Продолжить»" in line for line in logs)

    # Файл первого чанка реальный (предикат готовности не подменяем).
    done = tmp_path / "chunk00.mp4"
    done.write_bytes(b"x" * 4096)
    monkeypatch.setattr(video, "verify_mp4", lambda *_a, **_k: True)
    monkeypatch.setattr(video, "_probe_video_duration", lambda _p: 10.0)
    chunks = [
        {"index": 0, "status": "done", "file": str(done), "duration": 10.0},
        {"index": 1, "status": "pending", "file": str(tmp_path / "chunk01.mp4")},
    ]
    assert video._chunk_record_ready(chunks[0]) is True
    assert video._chunk_record_ready(chunks[1]) is False
    assert video._chunk_resume_index(chunks) == 1

    # «Продолжить» после освобождения: память в норме, старт с чанка 2.
    monkeypatch.setenv("VIDEOMAKER_MEM_SIM_FREE_MB", "900")
    ok, message = mg.can_continue()
    assert ok is True and "продолжаем" in message
    assert video._chunk_resume_index(chunks) == 1


def test_render_chunked_plan_pauses_and_resumes_without_reencoding(
    monkeypatch, tmp_path: Path
):
    """RAM<80 на втором чанке → пауза; повтор кодирует только оставшиеся чанки.

    Полностью синтетический рендер: ffmpeg/assemble заменены заглушками,
    проверяется именно manifest-resume и специальное исключение.
    """
    from video_maker.cinematic import render_graph, timeline

    scenes = [
        SimpleNamespace(source_path=str(tmp_path / f"src{i}.mp4"),
                        source_in=0.0, source_out=10.0)
        for i in range(9)
    ]
    for scene in scenes:
        Path(scene.source_path).write_bytes(b"x")
    plan = SimpleNamespace(scenes=scenes, target_duration=80.0,
                           total_duration=80.0, warnings=[])
    transitions = [
        SimpleNamespace(spec=SimpleNamespace(kind="hard", duration=0.0))
        for _ in range(8)
    ]
    monkeypatch.setattr(
        timeline, "apply_rhythm",
        lambda _scenes, _settings, target_duration=0.0: SimpleNamespace(
            scenes=list(_scenes), transitions=transitions, warnings=[]),
    )
    monkeypatch.setattr(
        render_graph, "assemble_render_graph",
        lambda *_a, **_k: SimpleNamespace(
            concat_or_xfade="[0:v]concat=n=1:v=1:a=0[v]", total_duration=10.0),
    )
    monkeypatch.setattr(video, "_inject_fallback_treatments",
                        lambda fc, _scenes, log_fn=None: fc)
    monkeypatch.setattr(video, "ensure_storage", lambda *_a, **_k: None)
    monkeypatch.setattr(video, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(video, "_build_encode_cmd",
                        lambda _ff, _fc, _paths, out, **_k: ["ffmpeg", str(out)])
    monkeypatch.setattr(video, "verify_mp4", lambda *_a, **_k: True)
    monkeypatch.setattr(video, "_probe_video_duration", lambda _p: 10.0)
    monkeypatch.setattr(
        video, "run_ffmpeg",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )
    monkeypatch.setattr(video.memory_guard, "wait_for_ffmpeg_idle",
                        lambda *_a, **_k: None)
    encoded: list[str] = []

    def _encode(cmd, **_kwargs):
        encoded.append(cmd[-1])
        Path(cmd[-1]).write_bytes(b"x" * 2048)
        return "vt"

    monkeypatch.setattr(video, "_run_encode_cmd", _encode)
    calls = {"n": 0}

    def _memory(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise mg.MemoryPauseError(
                "[MEM] Недостаточно памяти (RAM 40 МБ, swap 96%). "
                "Закройте все приложения и нажмите «Продолжить».",
                state=_state(40, 96),
            )
        return _state(900, 96)

    monkeypatch.setattr(video.memory_guard, "wait_for_memory", _memory)
    out = tmp_path / "master.mp4"
    kwargs = dict(
        plan=plan, settings=SimpleNamespace(), target_duration=80.0,
        width=1920, height=1080, bitrate="12M", audio_file="", has_audio=False,
        output_path=str(out), tmp_path=str(tmp_path / "tmp.mp4"),
        log_fn=lambda _m: None, tag="TEST",
    )

    with pytest.raises(video.RenderMemoryPause) as exc_info:
        video._render_chunked_plan(**kwargs)
    assert exc_info.value.done_chunks == 1
    assert exc_info.value.total_chunks == 3
    assert exc_info.value.next_chunk == 1
    assert len(encoded) == 1

    # «Продолжить»: память освободилась; первый чанк берётся из манифеста.
    encoded.clear()
    result = video._render_chunked_plan(**kwargs)
    assert result["ok"] is True
    assert len(encoded) == 2, f"перекодированы лишние чанки: {encoded}"
    assert all(not p.endswith("chunk00.mp4") for p in encoded)
