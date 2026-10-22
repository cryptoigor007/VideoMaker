"""P0-4: memory-gate и resume-предикат чанков (без реального рендера).

Покрывает:
* безопасные пороги/парсинг vm_stat/swapusage и env;
* паузу при низкой памяти, критический уровень → MemoryPressureError;
* лимит пауз и мягкое продолжение;
* interlock «другой ffmpeg этой сессии»;
* `_chunk_record_ready` — предикат resume (done + файл + duration).
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    monkeypatch.setenv("VIDEOMAKER_MEM_CRITICAL_SWAP_PCT", "90")
    sleeps: list[float] = []
    with pytest.raises(mg.MemoryPressureError):
        mg.wait_for_memory(reader=lambda: _state(10), sleep_fn=sleeps.append)
    assert sleeps == []


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
