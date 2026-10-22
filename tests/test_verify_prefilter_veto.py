"""P0-3: дешёвый префильтр, кэш и режим «вето» (без сети/ffmpeg).

Покрывает блокеры верификатора:
* B1 — `_SCENE_STATE` не течёт между прогонами;
* B2 — `vlm_skipped` (-1) не сохраняется в кэш как оценка;
* префильтр режет точные дубли / blur / тёмные / стоп-кадры;
* топ-2 + не более 2 одиночных доработок на сцену;
* повторный прогон того же пула = 0 Vision-вызовов.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import library, perf_history, verify  # noqa: E402


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _entry(tmp_path: Path, i: int, *, scene: str = "office workers meeting",
           scene_idx: int | None = None) -> dict:
    clip = _write(tmp_path / f"clip{i}.mp4", b"v" * (3000 + i * 17))
    frame = _write(tmp_path / f"frame{i}.jpg", b"jpeg" + bytes([i % 255]) * 64)
    e = {
        "file": str(clip),
        "verify_file": str(clip),
        "scene": scene,
        "scene_key": scene,
        "query": "office",
        "duration": 4.0,
        "_vision_frames": [str(frame)],
    }
    if scene_idx is not None:
        e["scene_idx"] = int(scene_idx)
    return e


def _n_candidates(parts: list[dict]) -> int:
    return sum(1 for p in parts
               if str(p.get("text") or "").startswith("Кандидат "))


def _fake_llm(*, score: int = 2, verdict: str = "нет"):
    calls: list[int] = []

    def fake(parts, keys, model, *, expect="dict", log=print, counter=None, **_kw):
        if counter is not None:
            counter[0] += 1
        n = _n_candidates(parts)
        calls.append(n)
        return [
            {"n": j + 1, "literal_action": verdict, "score": score, "what": "тест"}
            for j in range(n)
        ]

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


@pytest.fixture()
def isolated_cache(tmp_path: Path, monkeypatch):
    db = tmp_path / "scores.db"
    monkeypatch.setattr(library, "db_path", lambda: db)
    monkeypatch.setattr(perf_history, "apply_history_skips",
                        lambda entries, log=None: 0)
    monkeypatch.setattr(perf_history, "record_entries", lambda entries: 0)
    verify.reset_scene_state()
    return db


def test_prefilter_duplicates_blur_dark_static(tmp_path: Path, monkeypatch):
    # Точные дубли: одинаковый размер и одинаковое содержимое.
    d1 = _write(tmp_path / "dup_a.mp4", b"x" * 4000)
    d2 = _write(tmp_path / "dup_b.mp4", b"x" * 4000)
    dark = _write(tmp_path / "dark_clip.mp4", b"d" * 5000)
    blur = _write(tmp_path / "soft_clip.mp4", b"b" * 6000)
    static = _write(tmp_path / "still_clip.mp4", b"s" * 7000)
    ok = _write(tmp_path / "ok_clip.mp4", b"o" * 8000)
    entries = [
        {"file": str(d1), "verify_file": str(d1), "scene": "office", "duration": 10.0},
        {"file": str(d2), "verify_file": str(d2), "scene": "office", "duration": 10.0},
        {"file": str(dark), "verify_file": str(dark), "scene": "office", "duration": 10.0},
        {"file": str(blur), "verify_file": str(blur), "scene": "office", "duration": 10.0},
        {"file": str(static), "verify_file": str(static), "scene": "office", "duration": 10.0},
        {"file": str(ok), "verify_file": str(ok), "scene": "office", "duration": 10.0},
    ]

    monkeypatch.setattr(verify, "_probe_video", lambda _p: (True, 10.0))
    monkeypatch.setattr(verify, "_content_hash",
                        lambda p: "same" if Path(p).name.startswith("dup_") else str(p))

    def fake_quality(path, duration=0.0, **_kw):
        name = Path(path).name
        if name.startswith("dark_"):
            return {"blur": 200.0, "bright": 3.0, "motion": 1.0, "frames": []}
        if name.startswith("soft_"):
            return {"blur": 1.0, "bright": 180.0, "motion": 1.0, "frames": []}
        if name.startswith("still_"):
            return {"blur": 200.0, "bright": 180.0, "motion": 0.0, "frames": []}
        return {"blur": 200.0, "bright": 180.0, "motion": 1.0, "frames": []}

    monkeypatch.setattr(verify, "_quality_stats", fake_quality)

    dups = verify.prefilter_candidates(entries, log=None)
    assert len(dups) == 1
    assert entries[1]["prefilter"] == "dup"
    assert entries[1]["score"] == -1
    assert entries[2]["prefilter"] == "dark"
    assert entries[3]["prefilter"] == "blur"
    assert entries[4]["prefilter"] == "static"
    assert not entries[5].get("verified")


def test_env_float_thresholds_are_safe(monkeypatch):
    monkeypatch.setenv("VM_VISION_BLUR_MIN", "не-число")
    assert verify._env_float("VM_VISION_BLUR_MIN", 15.0) == 15.0
    monkeypatch.setenv("VM_VISION_BLUR_MIN", "7,5")
    assert verify._env_float("VM_VISION_BLUR_MIN", 15.0) == pytest.approx(7.5)
    monkeypatch.delenv("VM_VISION_BLUR_MIN", raising=False)
    assert verify._env_float("VM_VISION_BLUR_MIN", 15.0) == 15.0


def test_prefilter_log_silent_when_nothing_filtered():
    logs: list[str] = []
    assert verify.prefilter_candidates([], log=logs.append) == []
    assert logs == []


def test_scene_state_reset_between_runs(tmp_path: Path, monkeypatch, isolated_cache):
    verify._SCENE_STATE["старая сцена"] = {"batch_done": True}
    token = verify._SCENE_RUN_TOKEN
    verify.reset_scene_state()
    assert verify._SCENE_STATE == {}
    assert verify._SCENE_RUN_TOKEN == token + 1

    # verify_pool — точка входа инструментов/финальной проверки: тоже сбрасывает.
    verify._SCENE_STATE["x"] = {"batch_done": True}
    verify.verify_pool([], key="", use_opencode=False, log=None)
    assert verify._SCENE_STATE == {}


def test_cache_second_run_makes_zero_calls(tmp_path: Path, monkeypatch, isolated_cache):
    fake = _fake_llm(score=2, verdict="нет")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    entries = [_entry(tmp_path, i) for i in range(2)]

    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None)
    assert fake.calls == [2]  # только топ-2 одним вызовом; фолбэк не нужен

    con = sqlite3.connect(str(isolated_cache))
    rows = con.execute("SELECT file, score FROM scores").fetchall()
    con.close()
    assert len(rows) == 2 and all(int(sc) >= 0 for _f, sc in rows)

    # Второй прогон того же пула (новые dict'ы): всё из кэша, 0 вызовов.
    verify.reset_scene_state()
    fresh = [_entry(tmp_path, i) for i in range(2)]
    verify.verify_candidates_gemini(
        fresh, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None)
    assert fake.calls == [2]
    assert all(e.get("cached") for e in fresh)


def test_skipped_is_not_cached_and_gets_evaluated_next_run(
        tmp_path: Path, monkeypatch, isolated_cache):
    fake = _fake_llm(score=2, verdict="нет")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    entries = [_entry(tmp_path, i) for i in range(5)]
    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None)
    assert fake.calls == [2]
    skipped = [e["file"] for e in entries if e.get("vlm_skipped")]
    assert len(skipped) == 3

    con = sqlite3.connect(str(isolated_cache))
    cached_skips = [
        f for (f,) in con.execute(
            "SELECT file FROM scores WHERE score < 0").fetchall()
        if f in skipped
    ]
    con.close()
    assert cached_skips == []  # «пропуск» не сохранён как оценка

    # Пул изменился: бывшие пропущенные стали топ-2 — они обязаны оцениться.
    verify.reset_scene_state()
    rerun = [_entry(tmp_path, 3), _entry(tmp_path, 4)]
    verify.verify_candidates_gemini(
        rerun, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None)
    assert fake.calls[-1] == 2  # оба бывших пропуска
    assert all(e.get("score") == 2 for e in rerun[:2])
    assert not rerun[0].get("vlm_skipped") and not rerun[1].get("vlm_skipped")


def test_verify_pool_ignores_legacy_skip_rows(
        tmp_path: Path, monkeypatch, isolated_cache):
    fake = _fake_llm(score=2, verdict="нет")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    # Префильтр не нужен: проверяем именно кэш verify_pool.
    monkeypatch.setattr(verify, "prefilter_candidates", lambda *a, **k: [])
    entries = [_entry(tmp_path, i) for i in range(2)]

    # Эмулируем старый баг: «пропуск» записан в кэш как -1.
    con = sqlite3.connect(str(isolated_cache))
    con.execute("CREATE TABLE IF NOT EXISTS scores (file TEXT, th TEXT, score INTEGER, "
                "what TEXT, at TEXT, PRIMARY KEY(file, th))")
    for e in entries:
        con.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)",
                    (e["file"], verify._cache_th_for(e, "m"), -1,
                     verify._SKIP_WHAT, "2026-01-01T00:00:00+00:00"))
    con.commit()
    con.close()

    verify.verify_pool(entries, key="k", model="m", min_score=2,
                       log=None, use_opencode=False)
    assert fake.calls == [2]  # старый -1 не считается оценкой
    assert all(e.get("score") == 2 and not e.get("vlm_skipped") for e in entries)


def test_stats_counts_real_calls_not_cache(
        tmp_path: Path, monkeypatch, isolated_cache):
    fake = _fake_llm(score=2, verdict="нет")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    stats: dict = {"calls": 0}
    entries = [_entry(tmp_path, i) for i in range(2)]
    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None, stats=stats)
    assert stats["calls"] == 1  # ровно один HTTP-вызов батча

    verify.reset_scene_state()
    fresh = [_entry(tmp_path, i) for i in range(2)]
    verify.verify_candidates_gemini(
        fresh, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=None, stats=stats)
    assert stats["calls"] == 1  # кэш не инкрементит счётчик


def test_top2_and_at_most_two_fallbacks(tmp_path: Path, monkeypatch, isolated_cache):
    fake = _fake_llm(score=2, verdict="нет")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    entries = [_entry(tmp_path, i) for i in range(6)]

    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=6, log=None)

    assert fake.calls == [2, 1, 1]  # батч топ-2 + ровно 2 одиночных
    evaluated = [e for e in entries if isinstance(e.get("score"), int) and e["score"] >= 0]
    assert len(evaluated) == 4
    assert sum(1 for e in entries if e.get("vlm_skipped")) == 2


def test_uncertain_top2_gets_third_candidate(
        tmp_path: Path, monkeypatch, isolated_cache):
    """[PERF-BUDGET] Топ-2 «сомневаюсь» — оцениваем 3-го (фолбэк ≤2)."""
    fake = _fake_llm(score=6, verdict="сомневаюсь")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    entries = [_entry(tmp_path, i) for i in range(3)]

    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=6, log=None)

    assert fake.calls == [2, 1]  # батч топ-2 + 3-й одиночно
    assert all(e.get("score") == 6 for e in entries)
    assert not any(e.get("vlm_skipped") for e in entries)


def test_two_scenes_same_text_get_own_vlm_calls(
        tmp_path: Path, monkeypatch, isolated_cache):
    """[VM-FIX B3] Две сцены с ОДИНАКОВЫМ текстом в одном прогоне.

    Репро реверификации: ключ `_SCENE_STATE` был текст+run-token, поэтому
    вторая сцена получала batch_done=True и все её кандидаты уходили в
    vlm_skipped (-1) без вызова. Теперь у каждой сцены свой бюджет/вызовы.
    """
    fake = _fake_llm(score=3, verdict="да")
    monkeypatch.setattr(verify, "_gemini_json", fake)
    logs: list[str] = []
    same_text = "office workers meeting"
    entries = ([_entry(tmp_path, i, scene=same_text, scene_idx=0) for i in range(3)]
               + [_entry(tmp_path, i, scene=same_text, scene_idx=1) for i in range(3, 6)])
    assert verify._group_key(entries[0]) != verify._group_key(entries[3])

    verify.verify_candidates_gemini(
        entries, key="k", model="m", use_cache=True, prefilter=False,
        min_score=2, log=logs.append)

    # Каждая сцена реально оценена: по 2 кандидата (топ-2) в одном батче.
    assert sum(fake.calls) == 4 and len(fake.calls) == 1  # батч обеих сцен
    assert all(e.get("score") == 3 for e in entries[:2] + entries[3:5])
    assert [e.get("vlm_skipped") for e in (entries[2], entries[5])] == [True, True]
    # Итог по сценам: две строки, у каждой VLM 2 и вызовов > 0.
    scene_lines = [s for s in logs if "VLM" in s and "сцена" in s]
    assert len(scene_lines) == 2
    assert all("VLM 2" in s for s in scene_lines)
    assert all(int(s.split("вызовов ")[1].split()[0]) > 0 for s in scene_lines)
