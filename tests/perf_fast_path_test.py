#!/usr/bin/env python3
"""Быстрые тесты ускорения подбора: история, pre-rank, ранний выход, бюджет."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import library, perf_history, smart_broll, verify  # noqa: E402


def _tmp_db(tmp: Path) -> None:
    def _dbp(root=None):
        tmp.parent.mkdir(parents=True, exist_ok=True)
        return tmp
    library.db_path = _dbp  # type: ignore[assignment]


def test_history_and_rank(tmp_path: Path) -> None:
    _tmp_db(tmp_path / "perf.db")
    perf_history.reset()
    good = {"file": "/x/good.mp4"}
    bad = {"file": "/x/bad.mp4"}
    for _ in range(5):
        perf_history.record_entries([{**good, "score": 9, "verified": True}])
    for _ in range(3):
        perf_history.record_entries([{**bad, "score": 1, "verified": True}])
    sg = perf_history.get(perf_history.path_of(good))
    sb = perf_history.get(perf_history.path_of(bad))
    assert (sg["checks"], round(sg["avg"], 1), sg["passes"]) == (5, 9.0, 5), sg
    assert perf_history.rank_bonus(good) > 0.5
    assert perf_history.rank_bonus(bad) < 0
    assert perf_history.is_low(sb)

    cands = [{"kind": "lib", "file": "/x/good.mp4", "tags": "office", "description": ""},
             {"kind": "lib", "file": "/x/bad.mp4", "tags": "office", "description": ""},
             {"kind": "lib", "file": "/x/new.mp4", "tags": "office", "description": ""}]
    ranked = smart_broll._pre_rank_candidates(cands, {"queries": ["office"]})
    assert ranked[0]["file"] == "/x/good.mp4", [c["file"] for c in ranked]
    assert ranked[-1]["file"] == "/x/bad.mp4", [c["file"] for c in ranked]

    # негативный кэш: без пересечения тегов — skip, с пересечением — проверяем
    skip_e = {"file": "/x/bad.mp4", "tags": "mountains", "query": "office phone"}
    keep_e = {"file": "/x/bad.mp4", "tags": "office desk", "query": "office phone"}
    assert perf_history.should_skip(skip_e, "phone call")
    assert not perf_history.should_skip(keep_e, "office phone")
    logs: list[str] = []
    n = perf_history.apply_history_skips([skip_e, keep_e], log=logs.append)
    assert n == 1 and skip_e.get("skipped_history") is True, (n, skip_e)
    assert any("низкая история" in x for x in logs)
    print("[TEST] история/негативный кэш/pre-rank: OK")


def _fake_search(n: int = 12):
    files = [f"/fake/clip_{i:02d}.mp4" for i in range(n)]
    return [{"file": f, "description": "office person", "tags": ["office"],
             "provider": "pixabay", "score": 1.0} for f in files]


def test_early_exit(tmp_path: Path) -> None:
    _tmp_db(tmp_path / "perf.db")
    perf_history.reset()
    library.search_library_multi = lambda *a, **k: _fake_search(12)  # type: ignore
    library.search_stock_multi = lambda *a, **k: []  # type: ignore
    smart_broll._lib_preview = lambda f, log: f  # type: ignore

    calls: list[int] = []

    def fake_verify(entries, **kw):
        calls.append(len(entries))
        for i, e in enumerate(entries):
            e["score"] = 9 if i == 0 else 2
            e["verified"] = True
            e["cached"] = False
        return entries

    verify.verify_batch_gemini = fake_verify  # type: ignore
    sc = {"idx": 4, "text": "офис", "visual": "офис", "queries": ["office"], "themes": {}}
    cands = smart_broll._scene_pool_candidates(
        sc, orientation="wide", per_scene=2, log=lambda *a: None,
        used_urls=set(), vlm_budget=12, use_history=False)
    st = sc.get("_pool_stats") or {}
    assert st.get("early_exit") is True, st
    assert st.get("checked") == 4, st
    assert len(calls) == 1, calls
    assert cands[0].get("score") == 9
    print(f"[TEST] ранний выход: 1 вызов, проверено {st['checked']}/5: OK")


def test_budget_and_topup(tmp_path: Path) -> None:
    _tmp_db(tmp_path / "perf.db")
    perf_history.reset()
    library.search_library_multi = lambda *a, **k: _fake_search(20)  # type: ignore
    library.search_stock_multi = lambda *a, **k: []  # type: ignore
    smart_broll._lib_preview = lambda f, log: f  # type: ignore
    calls: list[int] = []

    def fake_verify(entries, **kw):
        calls.append(len(entries))
        for e in entries:
            e["score"] = 2
            e["verified"] = True
            e["cached"] = False
        return entries

    verify.verify_batch_gemini = fake_verify  # type: ignore
    sc = {"idx": 0, "text": "офис", "visual": "офис", "queries": ["office"], "themes": {}}
    smart_broll._scene_pool_candidates(
        sc, orientation="wide", per_scene=2, log=lambda *a: None,
        used_urls=set(), vlm_budget=12, use_history=False)
    st = sc.get("_pool_stats") or {}
    assert st.get("candidates") == 8, st
    assert st.get("checked") == 8, st
    assert calls == [4, 1, 3], calls  # 4 + 1 (бюджет 5) + 3 (добор до 8)
    print(f"[TEST] бюджет 5 + добор до 8: вызовы {calls}, проверено 8: OK")


def test_settings_invalidation() -> None:
    base = {"per_scene": 1, "orientation": "wide", "slot_seconds": 9.0,
            "max_scenes": None, "library_only": False, "verify": True, "min_score": 6}
    h1 = smart_broll._settings_signature(base)
    h2 = smart_broll._settings_signature({**base, "per_scene": 2})
    assert h1 != h2
    ok, why = smart_broll._progress_valid(
        {"version": 1, "source": "/a.m4a", "audio_hash": "h", "settings_hash": h1,
         "scenes_total": 4, "scenes_sig": "s", "scenes": {}},
        source="/a.m4a", audio_hash="h", settings_hash=h2,
        scenes_total=4, scenes_sig="s")
    assert not ok and "настройки" in why, (ok, why)
    print(f"[TEST] смена настроек сбрасывает прогресс: {why}: OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.db"
        test_history_and_rank(p)
        test_early_exit(p)
        test_budget_and_topup(p)
    test_settings_invalidation()
    print("ALL PERF TESTS PASSED")
