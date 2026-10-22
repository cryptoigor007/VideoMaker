"""B3: 2-3 шота на длинную сцену по аудиоструктуре + diversity в ранжировании.

Проверяем:
* длинная сцена (>6с) делится по паузам word-level на 2-3 шота;
* короткая сцена остаётся одним шотом (обратный тест);
* без пауз рез — по началу слова, а не посреди слова;
* планировщик ставит каждому шоту свой клип из пула сцены и не ломает
  непрерывность таймлайна;
* похожий на два предыдущих клип заменяется следующим кандидатом,
  в лог пишется ``[SB] diversity: заменён клип (похож на предыдущий)``.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS  # noqa: E402
from video_maker.cinematic.planner import PlanBuilder  # noqa: E402
from video_maker.semantic import smart_broll  # noqa: E402
from video_maker.semantic.scene_policy import (  # noqa: E402
    VisualDiversityTracker,
    select_scene,
    split_scene_into_shots,
)
from video_maker.semantic.smart_broll import _shots_for_scene  # noqa: E402


def _words(spec: list[tuple[str, float, float]]) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


def _long_scene_words() -> list[dict]:
    # 9с, две паузы: 1.5→3.0 (1.5с) и 4.2→6.5 (2.3с).
    return _words([
        ("Он", 0.0, 0.3), ("открывает", 0.3, 0.9), ("ноутбук", 0.9, 1.5),
        ("Потом", 3.0, 3.6), ("звонит", 3.6, 4.2),
        ("Она", 6.5, 6.8), ("не", 6.8, 7.0), ("отвечает", 7.0, 8.0),
    ])


# ---------------------------------------------------------------------------
# Декомпозиция сцены
# ---------------------------------------------------------------------------

def test_long_scene_splits_into_two_shots_by_pause():
    """7с с одной паузой → 2 шота, рез ровно на паузе."""
    words = _words([
        ("Он", 0.0, 0.4), ("работает", 0.4, 1.2), ("за", 1.2, 1.4),
        ("ноутбуком", 1.4, 2.2),
        ("Потом", 4.0, 4.5), ("звонит", 4.5, 5.2), ("другу", 5.2, 6.0),
    ])
    shots = split_scene_into_shots(0.0, 7.0, words, min_shot_sec=2.0)
    assert len(shots) == 2
    assert shots[0]["start"] == 0.0
    assert shots[1]["end"] == 7.0
    assert abs(shots[1]["start"] - 4.0) < 0.05  # граница паузы, не середина фразы
    assert shots[0]["end"] - shots[0]["start"] >= 2.0
    assert shots[1]["end"] - shots[1]["start"] >= 2.0


def test_long_scene_three_shots_with_two_pauses():
    shots = split_scene_into_shots(0.0, 9.0, _long_scene_words(), min_shot_sec=2.0)
    assert len(shots) == 3
    starts = [round(s["start"], 2) for s in shots]
    ends = [round(s["end"], 2) for s in shots]
    assert starts == [0.0, 3.0, 6.5]
    assert ends == [3.0, 6.5, 9.0]
    assert all(s["end"] > s["start"] for s in shots)


def test_short_scene_stays_single_shot_reverse():
    """Короткая фраза (<=6с) НЕ растягивается на несколько шотов."""
    shots = split_scene_into_shots(0.0, 5.5, _long_scene_words(), min_shot_sec=2.0)
    assert shots == [{"start": 0.0, "end": 5.5, "index": 0}]


def test_no_pauses_cut_on_word_boundary_not_mid_word():
    """Без пауз рез — по началу слова около идеальной доли."""
    words = _words([
        ("раз", 0.0, 1.0), ("два", 1.0, 2.0), ("три", 2.0, 3.0),
        ("четыре", 3.0, 4.0), ("пять", 4.0, 5.0), ("шесть", 5.0, 6.0),
        ("семь", 6.0, 7.0), ("восемь", 7.0, 8.0), ("девять", 8.0, 9.0),
    ])
    shots = split_scene_into_shots(0.0, 9.0, words, min_shot_sec=2.0)
    assert len(shots) >= 2
    word_starts = {round(w["start"], 3) for w in words}
    for sh in shots[1:]:
        assert round(sh["start"], 3) in word_starts
    assert shots[-1]["end"] == 9.0


def test_missing_units_does_not_split_long_scene():
    """Нет word-level/сегментов — не рвём сцену вслепую."""
    shots = split_scene_into_shots(0.0, 12.0, [], min_shot_sec=2.0)
    assert len(shots) == 1


# ---------------------------------------------------------------------------
# Планировщик: шоты + клипы из пула сцены
# ---------------------------------------------------------------------------

def _segments_long_then_short() -> list[dict]:
    return [
        {"start": 0.0, "end": 9.0,
         "text": "Он открывает ноутбук. Потом звонит. Она не отвечает.",
         "words": _long_scene_words()},
        {"start": 9.0, "end": 12.0, "text": "Тишина.",
         "words": _words([("Тишина", 9.0, 9.6)])},
    ]


def _pref() -> dict[int, list[str]]:
    return {
        1: [
            "01_phone/lonely_man_window_rain_01.mp4",
            "01_phone/lonely_man_window_rain_02.mp4",
            "01_phone/lonely_man_window_rain_03.mp4",
            "01_phone/city_street_crowd_04.mp4",
        ],
        2: ["02_window/quiet_room.mp4"],
    }


def _build(pref: dict[int, list[str]], logs: list[str] | None = None):
    paths = [p for files in pref.values() for p in files]
    return PlanBuilder(DEFAULT_CINEMATIC_SETTINGS).build_from_broll(
        paths,
        target_duration=12.0,
        transcript_segments=_segments_long_then_short(),
        scene_bound=True,
        scene_preferred=pref,
        clip_durations=[10.0] * len(paths),
        log=(logs.append if logs is not None else None),
    )


def test_planner_splits_long_scene_into_three_distinct_shots():
    plan = _build(_pref())
    # 3 шота длинной сцены + 1 короткая = 4 плана
    assert len(plan.scenes) == 4
    long_shots = plan.scenes[:3]
    assert [round(s.timeline_start, 2) for s in long_shots] == [0.0, 3.0, 6.5]
    assert len({s.source_path for s in long_shots}) == 3  # без дублей в сцене
    # Клипы — из пула ИМЕННО первой сцены
    assert all("01_phone/" in s.source_path for s in long_shots)
    # Непрерывность таймлайна
    for a, b in zip(plan.scenes, plan.scenes[1:]):
        assert abs(a.timeline_end - b.timeline_start) < 1e-6
    assert abs(plan.scenes[-1].timeline_end - 12.0) < 1e-6


def test_planner_short_scene_remains_one_shot():
    pref = {
        1: _pref()[1][:3],
        2: ["02_window/quiet_room.mp4", "02_window/quiet_room_02.mp4"],
    }
    plan = _build(pref)
    short = [s for s in plan.scenes if s.timeline_start >= 9.0 - 1e-6]
    assert len(short) == 1
    assert short[0].source_path.startswith("02_window/")


def test_planner_diversity_replaces_similar_third_shot():
    logs: list[str] = []
    plan = _build(_pref(), logs=logs)
    long_shots = plan.scenes[:3]
    # Третий похожий клип заменён на отличающийся (city), а не *_03
    assert "city_street_crowd" in long_shots[2].source_path
    assert any("diversity: заменён клип" in m for m in logs)


def test_shots_for_scene_no_duplicates_and_merge():
    sc = {
        "start": 0.0,
        "end": 9.0,
        "_shot_windows": [
            {"start": 0.0, "end": 3.0},
            {"start": 3.0, "end": 6.5},
            {"start": 6.5, "end": 9.0},
        ],
    }
    files = ["/pool/01_a.mp4", "/pool/01_b.mp4"]
    shots = _shots_for_scene(sc, files, [
        {"file": "/pool/01_a.mp4", "score": 8},
        {"file": "/pool/01_b.mp4", "score": 7},
    ])
    assert len(shots) == 2  # клипов меньше, чем резов — хвост слит
    assert [s["clip"] for s in shots] == files  # без дублей
    assert shots[0]["score"] == 8 and shots[1]["score"] == 7
    assert shots[0]["start"] == 0.0 and shots[-1]["end"] == 9.0


def test_shots_for_scene_empty_without_files():
    assert _shots_for_scene(
        {"start": 0.0, "end": 9.0, "_shot_windows": [{"start": 0.0, "end": 9.0}]},
        [],
    ) == []


def test_requested_clips_adds_diversity_spare():
    long_scene = {"_shot_windows": [{"start": 0, "end": 3}, {"start": 3, "end": 6},
                                    {"start": 6, "end": 9}]}
    short_scene = {"_shot_windows": [{"start": 0, "end": 4}]}
    assert smart_broll._requested_clips(long_scene, 1) == 4  # 3 шота + запас
    assert smart_broll._requested_clips(short_scene, 1) == 1


def test_manifest_and_progress_store_shots_without_downloads(tmp_path, monkeypatch):
    """Длинная сцена: манифест/_progress получают 3 шота с разными клипами."""
    segs = [{
        "start": 0.0, "end": 9.0,
        "text": "Он открывает ноутбук, потом звонит, она не отвечает.",
        "words": _long_scene_words(),
    }]
    seg_file = tmp_path / "segs.json"
    seg_file.write_text(json.dumps({"segments": segs}, ensure_ascii=False),
                        encoding="utf-8")
    srcs = []
    for i in range(3):
        p = tmp_path / f"lib_scene_{i}.mp4"
        p.write_bytes(b"x" * 4096)
        srcs.append(str(p))

    def fake_pool(sc, **_kwargs):
        sc["_pool_stats"] = {"candidates": 3, "checked": 3, "cached": 0,
                             "skipped": 0, "best": 9, "calls": 0,
                             "search_ms": 0.0, "vlm_ms": 0.0}
        return [{
            "kind": "lib", "file": srcs[i],
            "description": f"scene item {i}", "tags": "",
            "score": 9, "verified": True,
        } for i in range(3)]

    monkeypatch.setattr(smart_broll, "_scene_pool_candidates", fake_pool)
    out_root = tmp_path / "out"
    man = smart_broll.run_smart_broll(
        segments_json=str(seg_file), out_root=str(out_root), series="shots",
        per_scene=1, use_analysis=False, verify=False,
        library_first=False, log=lambda *_: None,
    )
    sc = man["scenes"][0]
    assert len(sc["files"]) == 3
    assert len(sc["shots"]) == 3
    assert [s["clip"] for s in sc["shots"]] == sc["files"]  # без дублей
    assert man["shots_total"] == 3
    prog = json.loads((out_root / "shots" / "_progress.json").read_text(encoding="utf-8"))
    rec = prog["scenes"]["0"]
    assert rec["shots_count"] == 3
    assert [s["clip"] for s in rec["shots"]] == sc["files"]
    summary = json.loads((out_root / "shots" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["shots_total"] == 3
    assert summary["multi_shot_scenes"] == 1


# ---------------------------------------------------------------------------
# Diversity в ранжировании
# ---------------------------------------------------------------------------

def _similar(i: int) -> dict:
    return {
        "kind": "lib",
        "file": f"/lib/lonely_man_window_rain_{i}.mp4",
        "description": "lonely man standing at the window in the rain",
        "score": 9,
    }


def test_select_scene_replaces_similar_candidate_and_logs():
    tracker = VisualDiversityTracker()
    tracker.record(_similar(1), 0.0)
    tracker.record(_similar(2), 1.0)
    cands = [
        _similar(3),  # похож на два предыдущих → должен уступить
        {
            "kind": "lib",
            "file": "/lib/city_street_crowd.mp4",
            "description": "crowded city street traffic at night",
            "score": 7,
        },
    ]
    logs: list[str] = []
    decision = select_scene(
        cands,
        {"idx": 2, "start": 30.0, "end": 36.0, "queries": ["window rain"]},
        per_scene=1,
        diversity=tracker,
        log=logs.append,
    )
    assert decision.primary is not None
    assert decision.primary["file"].endswith("city_street_crowd.mp4")
    assert any(
        "[SB] diversity: заменён клип (похож на предыдущий)" in m for m in logs
    )


def test_select_scene_keeps_similar_when_no_alternative():
    tracker = VisualDiversityTracker()
    tracker.record(_similar(1), 0.0)
    tracker.record(_similar(2), 1.0)
    cands = [_similar(3)]
    logs: list[str] = []
    decision = select_scene(
        cands,
        {"idx": 2, "start": 30.0, "end": 36.0, "queries": ["window rain"]},
        per_scene=1,
        diversity=tracker,
        log=logs.append,
    )
    assert decision.primary is not None
    assert decision.primary["file"].endswith("rain_3.mp4")
    assert not any("diversity" in m for m in logs)


def test_select_scene_does_not_replace_good_clip_with_weak():
    """Diversity не опускает 9/10 до 4/10: лучше повтор, чем слабый кадр."""
    tracker = VisualDiversityTracker()
    tracker.record(_similar(1), 0.0)
    tracker.record(_similar(2), 1.0)
    weak = {
        "kind": "lib", "file": "/lib/random_abstract.mp4",
        "description": "abstract gradient texture", "score": 4,
    }
    logs: list[str] = []
    decision = select_scene(
        [_similar(3), weak],
        {"idx": 2, "start": 30.0, "end": 36.0, "queries": ["window rain"]},
        per_scene=1,
        diversity=tracker,
        log=logs.append,
    )
    assert decision.primary is not None
    assert decision.primary["file"].endswith("rain_3.mp4")
    assert not any("diversity" in m for m in logs)


# ---------------------------------------------------------------------------
# P1: одна длинная сцена (один сегмент) тоже делится на шоты
# ---------------------------------------------------------------------------

def _single_long_segment() -> list[dict]:
    return _segments_long_then_short()[:1]  # один сегмент 0–9с


def test_planner_single_long_scene_splits_into_three_shots():
    """1 сегмент 9с + 3 клипа сцены → 3 плана (раньше привязка отключалась)."""
    import dataclasses
    pref = {1: _pref()[1][:3]}
    paths = pref[1]
    plan = PlanBuilder(DEFAULT_CINEMATIC_SETTINGS).build_from_broll(
        paths, target_duration=9.0,
        transcript_segments=_single_long_segment(),
        scene_bound=True, scene_preferred=pref,
        clip_durations=[10.0] * len(paths),
    )
    assert len(plan.scenes) == 3
    assert [round(s.timeline_start, 2) for s in plan.scenes] == [0.0, 3.0, 6.5]
    assert [s.source_path for s in plan.scenes] == paths
    assert abs(plan.scenes[-1].timeline_end - 9.0) < 1e-6


def test_planner_single_long_flat_pool_splits_into_shots():
    """Плоский пул без NN_папок: единственная сцена владеет всеми клипами."""
    paths = ["flat/a.mp4", "flat/b.mp4", "flat/c.mp4"]
    plan = PlanBuilder(DEFAULT_CINEMATIC_SETTINGS).build_from_broll(
        paths, target_duration=9.0,
        transcript_segments=_single_long_segment(),
        scene_bound=True, clip_durations=[10.0] * len(paths),
    )
    assert len(plan.scenes) == 3
    assert [s.source_path for s in plan.scenes] == paths


def test_planner_single_graphic_scene_gets_background_for_whole_window():
    import dataclasses
    s = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, enabled=True)
    s.scene_flags = {
        1: {"fallback": "graphic", "treated": False, "graphic_fallback": True},
    }
    pref = {1: _pref()[1][:3]}
    plan = PlanBuilder(s).build_from_broll(
        pref[1], target_duration=9.0,
        transcript_segments=_single_long_segment(),
        scene_bound=True, scene_preferred=pref,
        clip_durations=[10.0] * len(pref[1]),
    )
    assert len(plan.scenes) == 1
    assert plan.scenes[0].source_path.startswith("vm-graphic://")
    assert "graphic_fallback" in plan.scenes[0].warnings
    assert abs(plan.scenes[0].timeline_end - 9.0) < 1e-6


def test_planner_no_file_scene_does_not_borrow_foreign_clip():
    """P3: fallback=no_file → фон на своё окно, а не клип чужой сцены."""
    import dataclasses
    s = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, enabled=True)
    s.scene_flags = {
        1: {"fallback": "none", "treated": False, "graphic_fallback": False},
        2: {"fallback": "no_file", "treated": False, "graphic_fallback": False},
    }
    segs = _segments_long_then_short()
    p1 = "01_phone/lonely_man_window_rain_01.mp4"
    plan = PlanBuilder(s).build_from_broll(
        [p1], target_duration=12.0, transcript_segments=segs,
        scene_bound=True, scene_preferred={1: [p1]},
        clip_durations=[10.0],
    )
    assert len(plan.scenes) == 2
    assert plan.scenes[0].source_path == p1
    assert plan.scenes[1].source_path.startswith("vm-graphic://")
    assert any("graphic_fallback" in w for w in plan.scenes[1].warnings)


# ---------------------------------------------------------------------------
# P2: лог diversity в _diversify_clip_sequence
# ---------------------------------------------------------------------------

def test_diversify_sequence_replaces_third_similar_and_logs():
    from video_maker.cinematic.planner import _diversify_clip_sequence
    cands = [_similar(1), _similar(2), _similar(3), {
        "kind": "lib", "file": "/lib/city_street_crowd.mp4",
        "description": "crowded city street traffic at night", "score": 7,
    }]
    logs: list[str] = []
    out = _diversify_clip_sequence(cands, total_duration=40.0, log=logs.append)
    files = [c["file"] for c in out]
    assert files[:2] == ["/lib/lonely_man_window_rain_1.mp4",
                         "/lib/lonely_man_window_rain_2.mp4"]
    assert "city_street_crowd" in files[2]
    assert any("diversity: заменён клип" in m for m in logs)


def test_diversify_sequence_forced_repeat_logs_separately():
    from video_maker.cinematic.planner import _diversify_clip_sequence
    logs: list[str] = []
    out = _diversify_clip_sequence(
        [_similar(1), _similar(2), _similar(3)],
        total_duration=30.0, log=logs.append,
    )
    assert [c["file"] for c in out] == [
        "/lib/lonely_man_window_rain_1.mp4",
        "/lib/lonely_man_window_rain_2.mp4",
        "/lib/lonely_man_window_rain_3.mp4",
    ]
    assert any("forced repeat" in m for m in logs)
    assert not any("заменён клип" in m for m in logs)
