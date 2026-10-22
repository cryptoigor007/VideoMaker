"""P0-1/P0-2: baseline-лог и fallback-политика на 10 сценах (без рендера/сети)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import smart_broll  # noqa: E402
from video_maker.semantic.baseline import REQUIRED_FIELDS  # noqa: E402
from video_maker.semantic.smart_broll import _bad_stock_files  # noqa: E402
from video_maker.semantic.scene_policy import (  # noqa: E402
    ClipUsageTracker,
    GraphicFallbackBudget,
    crop_color_applicable,
    select_scene,
)
from video_maker.pipeline.master import (  # noqa: E402
    _disable_dobor,
    _manifest_scene_flags,
    _run_fix_scenes_filtered,
)
from video_maker.cinematic.planner import _scene_flag_index  # noqa: E402


def _scene(idx: int, start: float, queries: list[str] | None = None) -> dict:
    return {
        "idx": idx, "start": start, "end": start + 3.5,
        "text": f"Сцена {idx + 1} про работу и город.",
        "queries": queries or ["city street traffic", "office people working"],
        "themes": {}, "visual": "",
    }


def _cand(path: Path, score: int | None, *, tags: str = "", width: int = 3840,
          height: int = 2160, db_score: int | None = None) -> dict:
    c = {
        "kind": "lib", "file": str(path), "query": "test",
        "tags": tags, "description": "", "what": "тест",
        "score": score, "width": width, "height": height, "duration": 10.0,
    }
    if db_score is not None:
        c["db_score"] = db_score
    return c


def test_fallback_table_on_ten_scenes(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    files = []
    for i in range(10):
        p = src / f"clip_{i}.mp4"
        p.write_bytes(b"x" * 2048)
        files.append(p)

    segs = [
        {"start": i * 4.0, "end": i * 4.0 + 3.5,
         "text": f"Сцена номер {i + 1} про работу и город."}
        for i in range(10)
    ]
    segs_json = tmp_path / "segments.json"
    segs_json.write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")

    scores = [8, 5, 5, 5, 4, 3, 3, 2, 0, 7]
    calls = {"n": 0}

    def fake_pool(sc, **_kwargs):
        calls["n"] += 1
        i = int(sc["idx"])
        sc["_pool_stats"] = {
            "candidates": 1, "checked": 1, "cached": 0, "skipped": 0,
            "best": scores[i], "calls": 1,
            "search_ms": 11.0, "vlm_ms": 2.0, "total_ms": 13.0,
        }
        if i == 1 or i == 2:
            tags = " ".join(sc.get("queries") or [])
        elif i == 3:
            tags = "ocean waves"  # ассоциативный, не literal
        elif i == 4:
            tags = "city street night"  # нейтральный фон
        elif i in (5, 6, 7):
            tags = "desert sand"
        else:
            tags = ""
        return [_cand(files[i], scores[i], tags=tags,
                      width=0 if i == 2 else 3840,
                      height=0 if i == 2 else 2160,
                      db_score=7 if i == 4 else None)]

    monkeypatch.setattr(smart_broll, "_scene_pool_candidates", fake_pool)
    out_root = tmp_path / "out"
    man = smart_broll.run_smart_broll(
        segments_json=str(segs_json), out_root=str(out_root), series="test",
        per_scene=1, use_analysis=False, verify=False, library_first=False, log=lambda *_: None,
    )
    out_dir = out_root / "test"

    # P0-2: таблица исходов на 10 сценах.
    got = [s.get("fallback") for s in man["scenes"]]
    assert got == [
        "none", "literal_crop", "literal_proxy", "associative", "neutral",
        "graphic", "graphic", "proxy_forced", "graphic", "none",
    ]
    assert [bool(s.get("graphic_fallback")) for s in man["scenes"]] == [
        False, False, False, False, False, True, True, False, True, False,
    ]
    # graphic-сцены действительно без клипа.
    assert man["scenes"][5]["files"] == []
    assert man["scenes"][6]["files"] == []
    assert man["scenes"][8]["files"] == []
    # treated проставлен у 5-х и proxy_forced.
    treated = [bool(s.get("treated")) for s in man["scenes"]]
    assert treated == [False, True, True, True, False, False, False, True, False, False]

    # P0-1: JSONL и обязательные поля.
    lines = (out_dir / "scenes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    recs = [json.loads(x) for x in lines]
    for r in recs:
        assert set(REQUIRED_FIELDS) <= set(r)
    assert [r["fallback_used"] for r in recs] == got
    assert recs[1]["candidate_scores"] == [5]
    assert recs[1]["search_ms"] == 11.0
    assert recs[1]["vlm_calls"] == 1
    assert recs[1]["cache_hits"] == 0
    assert recs[5]["selected_clip"] == ""
    assert recs[5]["selected_score"] is None
    assert recs[4]["selected_score"] == 4

    # run_summary.json.
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["scenes"] == 10
    assert summary["scenes_logged"] == 10
    assert summary["scenes_ge6"] == 2  # 8/10 и 7/10
    assert summary["avg_score"] == pytest.approx(5.14, abs=0.01)
    assert summary["vlm_per_scene"] == 1.0
    assert summary["fallback_counts"]["graphic"] == 3
    assert summary["fallback_counts"]["literal_crop"] == 1
    assert summary["fallback_counts"]["literal_proxy"] == 1
    assert summary["fallback_counts"]["associative"] == 1
    assert summary["fallback_counts"]["neutral"] == 1
    assert summary["fallback_counts"]["proxy_forced"] == 1
    assert summary["graphic_fallback"] == {
        "scenes": 3, "max_consecutive": 2, "max_duration_s": 7.0,
    }
    for key in ("analysis_ms", "planning_ms", "search_ms", "vlm_ms",
                "download_ms", "finalize_ms", "total_ms"):
        assert key in summary["time_by_stage"]

    # _progress.json не сломан и хранит флаги для resume.
    prog = json.loads((out_dir / "_progress.json").read_text(encoding="utf-8"))
    assert prog["version"] == 1 and prog["scenes_total"] == 10
    assert len(prog["scenes"]) == 10
    assert prog["scenes"]["5"]["graphic_fallback"] is True
    assert prog["scenes"]["1"]["fallback"] == "literal_crop"

    # Resume: сцены не пересобираются, JSONL не дублируется, флаги сохраняются.
    before = calls["n"]
    man2 = smart_broll.run_smart_broll(
        segments_json=str(segs_json), out_root=str(out_root), series="test",
        per_scene=1, use_analysis=False, verify=False, library_first=False, log=lambda *_: None,
    )
    assert calls["n"] == before  # ни одного нового поиска
    assert len((out_dir / "scenes.jsonl").read_text(encoding="utf-8").splitlines()) == 10
    assert [s.get("fallback") for s in man2["scenes"]] == got
    assert man2["scenes"][5]["graphic_fallback"] is True
    summary2 = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary2["scenes"] == 10
    assert summary2["fallback_counts"] == summary["fallback_counts"]


def test_repeat_clip_limit_and_graphic_budget():
    usage = ClipUsageTracker()
    budget = GraphicFallbackBudget()
    sc = _scene(0, 0.0)
    c = _cand(Path("/tmp/p0_2_repeat.mp4"), 8, tags="")
    # Два появления в окне 180с разрешены, третье — уже повтор.
    d1 = select_scene([c], _scene(0, 0.0), usage=usage, graphic_budget=budget)
    d2 = select_scene([c], _scene(1, 60.0), usage=usage, graphic_budget=budget)
    d3 = select_scene([c], _scene(2, 120.0), usage=usage, graphic_budget=budget)
    assert d1.fallback == "none" and d2.fallback == "none"
    assert d3.fallback == "repeat_forced" and d3.treated is True
    # Через 180с от предыдущих показов — снова можно.
    d4 = select_scene([c], _scene(3, 245.0), usage=usage, graphic_budget=budget)
    assert d4.fallback == "none"

    # graphic: 2 подряд, третий слабый — proxy_forced.
    b = GraphicFallbackBudget()
    weak = _cand(Path("/tmp/p0_2_weak.mp4"), 3, tags="desert")
    g1 = select_scene([weak], _scene(4, 300.0), graphic_budget=b)
    g2 = select_scene([weak], _scene(5, 304.0), graphic_budget=b)
    g3 = select_scene([weak], _scene(6, 308.0), graphic_budget=b)
    assert g1.graphic and g2.graphic and not g3.graphic
    assert g3.fallback == "proxy_forced"


def test_unscored_candidates_do_not_become_graphic():
    """Без Vision-оценок поведение прежнее: берём лучший pre-rank, не graphic."""
    c = _cand(Path("/tmp/p0_2_unscored.mp4"), None, tags="office")
    d = select_scene([c], _scene(0, 0.0))
    assert d.fallback == "unscored"
    assert d.graphic is False
    assert d.candidates == [c]


def test_skipped_minus_one_is_never_selected():
    """[VM-FIX B3] score=-1 = vlm_skipped: кандидат НЕ проверен — не ставим.

    Раньше `best_score is None` трактовался как «без оценки» и такой клип
    выбирался с fallback=unscored. Теперь -1 отсекается, а unscored — только
    для score is None (Vision недоступен).
    """
    skipped = _cand(Path("/tmp/p0_2_skipped.mp4"), -1, tags="office")
    d = select_scene([skipped], _scene(0, 0.0))
    assert d.primary is None and d.candidates == []
    assert d.graphic is True and d.fallback == "graphic"
    assert d.primary_score is None
    assert d.flags.get("vlm_skipped_only") is True

    # Явный маркер vlm_skipped без оценки — тоже не пригоден.
    marked = _cand(Path("/tmp/p0_2_marked.mp4"), None, tags="office")
    marked["vlm_skipped"] = True
    d2 = select_scene([marked], _scene(1, 10.0))
    assert d2.primary is None and d2.fallback == "graphic"

    # Рядом есть действительно неоценённый (score is None) — берём его.
    unscored = _cand(Path("/tmp/p0_2_unscored2.mp4"), None, tags="office")
    d3 = select_scene([skipped, unscored], _scene(2, 20.0))
    assert d3.primary is unscored and d3.fallback == "unscored"

    # Даже при исчерпанном graphic-бюджете skipped-клип не становится proxy.
    budget = GraphicFallbackBudget()
    assert select_scene([skipped], _scene(3, 30.0), graphic_budget=budget).graphic
    assert select_scene([skipped], _scene(4, 34.0), graphic_budget=budget).graphic
    d4 = select_scene([skipped], _scene(5, 38.0), graphic_budget=budget)
    assert d4.candidates == [] and d4.primary is None and d4.graphic is True
    assert d4.flags.get("graphic_limit_exceeded") is True


def test_neutral_requires_mood_and_crop_dimensions():
    sc = _scene(0, 0.0)
    neutral = _cand(Path("/tmp/p0_2_city.mp4"), 4, tags="city street", db_score=6)
    low_mood = _cand(Path("/tmp/p0_2_dark.mp4"), 4, tags="city street", db_score=4)
    d = select_scene([neutral], sc)
    assert d.fallback == "neutral" and d.treated is False
    d2 = select_scene([low_mood], sc)
    assert d2.fallback == "graphic"
    assert crop_color_applicable({"width": 3840, "height": 2160}, "wide")
    assert not crop_color_applicable({"width": 0, "height": 0}, "wide")
    assert not crop_color_applicable({"width": 720, "height": 1280}, "wide")
    # 5.9 — literal 5.x, а не «6+» (не округляем вверх).
    c59 = _cand(Path("/tmp/p0_2_59.mp4"), 5.9, tags="office people working city street")  # type: ignore[arg-type]
    d59 = select_scene([c59], _scene(0, 0.0))
    assert d59.fallback in ("literal_crop", "literal_proxy")


def test_fix_scenes_skips_graphic_fallback(tmp_path: Path):
    """fix_scenes не должен добивать сцены, помеченные graphic_fallback."""
    script = tmp_path / "fake_fix.py"
    script.write_text(
        "import json,sys\n"
        "p=sys.argv[1]\n"
        "m=json.load(open(p,encoding='utf-8'))\n"
        "for s in m.get('scenes',[]):\n"
        "    s.setdefault('files',[]).append('fixed_'+str(s.get('idx')))\n"
        "json.dump(m,open(p,'w',encoding='utf-8'),ensure_ascii=False)\n",
        encoding="utf-8",
    )
    mp = tmp_path / "smart_broll_manifest.json"
    man = {"scenes": [
        {"idx": 0, "files": [], "graphic_fallback": True, "fallback": "graphic"},
        {"idx": 1, "files": [], "fallback": "literal_proxy", "treated": True},
    ], "downloaded": []}
    mp.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")

    flags = _manifest_scene_flags(man)
    assert flags[1]["graphic_fallback"] is True
    assert flags[2]["treated"] is True

    _run_fix_scenes_filtered(str(script), str(mp), lambda *_: None)
    fixed = json.loads(mp.read_text(encoding="utf-8"))
    assert fixed["scenes"][0]["files"] == []
    assert fixed["scenes"][0]["graphic_fallback"] is True
    assert fixed["scenes"][1]["files"] == ["fixed_1"]
    assert not (tmp_path / "smart_broll_manifest.fix.json").exists()


def test_disable_dobor_setting(tmp_path: Path, monkeypatch):
    """[PERF-BUDGET] Настройка отключения добора: env важнее JSON, дефолт false."""
    monkeypatch.delenv("VM_DISABLE_DOBOR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _disable_dobor() is False
    (tmp_path / ".video_maker_settings.json").write_text(
        json.dumps({"disable_dobor": True}), encoding="utf-8")
    assert _disable_dobor() is True
    monkeypatch.setenv("VM_DISABLE_DOBOR", "0")
    assert _disable_dobor() is False


def _repeat_segments(tmp_path: Path, count: int = 3) -> Path:
    segs = [
        {"start": i * 4.0, "end": i * 4.0 + 3.5,
         "text": f"Сцена номер {i + 1} про работу и город."}
        for i in range(count)
    ]
    p = tmp_path / "segments_repeat.json"
    p.write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
    return p


def test_repeat_clip_gets_real_file_not_phantom(tmp_path: Path, monkeypatch):
    """B3: repeat_forced/2-е появление — symlink существующего клипа."""
    src = tmp_path / "lib_clip.mp4"
    src.write_bytes(b"x" * 4096)

    def fake_pool(sc, **_kwargs):
        sc["_pool_stats"] = {
            "candidates": 1, "checked": 1, "cached": 0, "skipped": 0,
            "best": 9, "calls": 1, "search_ms": 1.0, "vlm_ms": 0.0,
        }
        return [_cand(src, 9, tags="")]

    monkeypatch.setattr(smart_broll, "_scene_pool_candidates", fake_pool)
    seg_file = _repeat_segments(tmp_path, 3)
    out_root = tmp_path / "out"
    man = smart_broll.run_smart_broll(
        segments_json=str(seg_file), out_root=str(out_root), series="repeat",
        per_scene=1, use_analysis=False, verify=False, library_first=False,
        log=lambda *_: None,
    )
    assert [s.get("fallback") for s in man["scenes"]] == ["none", "none", "repeat_forced"]
    for sc in man["scenes"]:
        assert len(sc["files"]) == 1
        assert Path(sc["files"][0]).exists()
        assert sc["selected_score"] == 9
    assert len(man["downloaded"]) == 3


def test_missing_clip_is_no_file_without_phantom_score(tmp_path: Path, monkeypatch):
    """B3: файла нет — fallback=no_file, selected_score=None, без usage."""
    missing = tmp_path / "missing_clip.mp4"

    def fake_pool(sc, **_kwargs):
        sc["_pool_stats"] = {
            "candidates": 1, "checked": 1, "cached": 0, "skipped": 0,
            "best": 9, "calls": 0, "search_ms": 0.0, "vlm_ms": 0.0,
        }
        return [_cand(missing, 9, tags="")]

    monkeypatch.setattr(smart_broll, "_scene_pool_candidates", fake_pool)
    seg_file = _repeat_segments(tmp_path, 1)
    man = smart_broll.run_smart_broll(
        segments_json=str(seg_file), out_root=str(tmp_path / "out"), series="nofile",
        per_scene=1, use_analysis=False, verify=False, library_first=False,
        log=lambda *_: None,
    )
    sc = man["scenes"][0]
    assert sc["files"] == []
    assert sc["fallback"] == "no_file"
    assert sc["selected_score"] is None
    assert sc["graphic_fallback"] is False
    assert man["downloaded"] == []


def test_atomic_json_leaves_no_temp_files(tmp_path: Path):
    path = tmp_path / "manifest.json"
    smart_broll._atomic_write_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_scene_flag_index_prefers_scene_folder(tmp_path: Path):
    p = tmp_path / "05_office" / "clip.mp4"
    p.parent.mkdir()
    p.write_bytes(b"x")
    assert _scene_flag_index(str(p), 9) == 5
    assert _scene_flag_index(str(tmp_path / "plain" / "clip.mp4"), 4) == 4
    # Не сценный путь, но файл есть в приоритетах манифеста сцены 3.
    q = tmp_path / "copy" / "clip.mp4"
    q.parent.mkdir()
    q.write_bytes(b"x")
    assert _scene_flag_index(str(q), 8, {3: [str(q)]}) == 3


def test_postfilter_keeps_neutral_proxy_and_library():
    """B4: _bad_stock_files трогает только слабый обычный сток."""
    entries = [
        {"file": "/lib.mp4", "from_library": True, "score": 3},
        {"file": "/neutral.mp4", "score": 2, "fallback": "neutral"},
        {"file": "/proxy.mp4", "score": 2, "fallback": "proxy_forced", "treated": True},
        {"file": "/literal_crop.mp4", "score": 4, "fallback": "literal_crop", "treated": True},
        {"file": "/assoc.mp4", "score": 4, "fallback": "associative", "treated": True},
        {"file": "/bad.mp4", "score": 2, "fallback": "none"},
        {"file": "/good.mp4", "score": 7, "fallback": "none"},
    ]
    assert _bad_stock_files(entries) == {"/bad.mp4"}


_REAL_MANIFEST = (
    Path(os.environ["SB_REAL_MANIFEST"])
    if os.environ.get("SB_REAL_MANIFEST") else None
)


@pytest.mark.skipif(
    _REAL_MANIFEST is None or not _REAL_MANIFEST.exists(),
    reason="нет реального манифеста (задайте SB_REAL_MANIFEST)",
)
def test_real_manifest_sample_policy_readonly():
    """5-10 реальных сцен: политика не падает и не пишет в пул (read-only)."""
    assert _REAL_MANIFEST is not None
    man = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    usage = ClipUsageTracker()
    budget = GraphicFallbackBudget()
    allowed = {"none", "literal_crop", "literal_proxy", "associative",
               "neutral", "graphic", "proxy_forced", "repeat_forced", "unscored"}
    rows = []
    for sc in (man.get("scenes") or [])[:10]:
        downs = [d for d in (man.get("downloaded") or [])
                 if d.get("scene") == sc.get("idx")]
        cands = [{
            "kind": "lib", "file": d.get("file"), "score": d.get("score"),
            "tags": d.get("tags"), "description": d.get("description"),
            "width": d.get("width"), "height": d.get("height"),
            "duration": d.get("duration"), "db_score": d.get("score"),
        } for d in downs]
        dec = select_scene(cands, sc, usage=usage, graphic_budget=budget)
        rows.append(dec)
    assert 5 <= len(rows) <= 10
    assert all(d.fallback in allowed for d in rows)
    # Политика не трогает файлы: манифест остаётся тем же.
    man2 = json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    assert len(man2.get("scenes") or []) == len(man.get("scenes") or [])
