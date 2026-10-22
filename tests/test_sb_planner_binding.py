"""Тесты по-сценной привязки B-roll в CE-плане (патч [VM-SB] в planner.build_from_broll)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS  # noqa: E402
from video_maker.cinematic.planner import PlanBuilder  # noqa: E402


def _segs_three_scenes() -> list[dict]:
    return [
        {"start": 0.0, "end": 2.0, "text": "друзья детства играли во дворе"},
        {"start": 2.0, "end": 4.0, "text": "лучший друг появлялся за неделю"},
        {"start": 4.0, "end": 6.0, "text": "телефон полон контактов и сообщений"},
        {"start": 6.0, "end": 8.0, "text": "позвонить некому, только уведомления"},
        {"start": 8.0, "end": 10.0, "text": "ночной город и свет в окнах"},
        {"start": 10.0, "end": 12.0, "text": "толпа на улице, огни витрин"},
    ]


def _plan(paths: list[str], segs: list[dict]):
    builder = PlanBuilder(DEFAULT_CINEMATIC_SETTINGS)
    return builder.build_from_broll(
        paths, target_duration=12.0, transcript_segments=segs, output_format="16:9",
    )


def test_per_scene_binding_reorders_paths():
    """Клипы идут в «неправильном» порядке — план должен привязать каждый к своей сцене."""
    segs = _segs_three_scenes()
    paths = [
        "night_city_lights_01.mp4",        # подходит сцене 3
        "phone_notification_typing_01.mp4",  # подходит сцене 2
        "childhood_friends_playing_01.mp4",  # подходит сцене 1
    ]
    plan = _plan(paths, segs)
    got = [s.source_path for s in plan.scenes]
    assert got == [
        "childhood_friends_playing_01.mp4",
        "phone_notification_typing_01.mp4",
        "night_city_lights_01.mp4",
    ]


def test_no_matches_keeps_order():
    segs = _segs_three_scenes()
    paths = ["soap_carving_01.mp4", "hydraulic_press_02.mp4", "kinetic_sand_03.mp4"]
    plan = _plan(paths, segs)
    assert [s.source_path for s in plan.scenes] == paths


def test_transcript_ref_uses_scene_window():
    """transcript_ref сцены — текст ЕЁ окна, а не первого сегмента."""
    segs = _segs_three_scenes()
    paths = ["childhood_friends_playing_01.mp4", "phone_notification_typing_01.mp4", "night_city_lights_01.mp4"]
    plan = _plan(paths, segs)
    refs = [s.transcript_ref or "" for s in plan.scenes]
    assert "друзья детства" in refs[0] or "друг появлялся" in refs[0]
    assert "телефон" in refs[1] or "позвонить" in refs[1]
    assert "город" in refs[2] or "толпа" in refs[2]


def test_smart_broll_off_keeps_order():
    """Галочка «Умный B-roll» выключена → порядок клипов не меняется."""
    import dataclasses
    segs = _segs_three_scenes()
    paths = [
        "night_city_lights_01.mp4",
        "phone_notification_typing_01.mp4",
        "childhood_friends_playing_01.mp4",
    ]
    off = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, smart_broll=False)
    builder = PlanBuilder(off)
    plan = builder.build_from_broll(
        paths, target_duration=12.0, transcript_segments=segs, output_format="16:9",
    )
    assert [s.source_path for s in plan.scenes] == paths


def test_tempo_caps_shot_length():
    """Темп = потолок длины плана: 9с/темп. При 2.0 — планы ≤ 4.5с."""
    import dataclasses
    segs = _segs_three_scenes()
    paths = ["childhood_friends_playing_01.mp4", "phone_notification_typing_01.mp4", "night_city_lights_01.mp4"]
    fast = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, plan_tempo=2.0)
    plan = PlanBuilder(fast).build_from_broll(
        paths, target_duration=12.0, transcript_segments=segs, output_format="16:9",
        weights=[1 / 3, 1 / 3, 1 / 3], clip_durations=[10.0, 10.0, 10.0],
    )
    assert len(plan.scenes) == 3
    for sc in plan.scenes:
        assert (sc.timeline_end - sc.timeline_start) <= 4.6  # потолок 9/2 = 4.5с
    total = sum(s.timeline_end - s.timeline_start for s in plan.scenes)
    assert abs(total - 12.0) < 0.5


def test_tempo_slow_allows_long_shots():
    """Темп 0.5 → потолок 18с: длинные спокойные планы."""
    import dataclasses
    segs = _segs_three_scenes()
    paths = ["a_scene.mp4", "b_scene.mp4", "c_scene.mp4"]
    slow = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, plan_tempo=0.5)
    plan = PlanBuilder(slow).build_from_broll(
        paths, target_duration=20.0, transcript_segments=segs, output_format="16:9",
        weights=[1 / 3, 1 / 3, 1 / 3], clip_durations=[30.0, 30.0, 30.0],
    )
    assert len(plan.scenes) == 3
    total = sum(s.timeline_end - s.timeline_start for s in plan.scenes)
    assert abs(total - 20.0) < 0.5


def test_tempo_redistributes_excess():
    """Излишек от «упёршегося в потолок» плана уходит другим — сумма сохраняется."""
    import dataclasses
    segs = _segs_three_scenes()
    paths = ["a.mp4", "b.mp4", "c.mp4"]
    plan = PlanBuilder(dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, plan_tempo=1.0)).build_from_broll(
        paths, target_duration=24.0, transcript_segments=segs, output_format="16:9",
        weights=[0.5, 0.25, 0.25], clip_durations=[30.0, 30.0, 30.0],
    )
    slices = [s.timeline_end - s.timeline_start for s in plan.scenes]
    assert all(x <= 9.05 for x in slices)  # потолок 9с
    assert abs(sum(slices) - 24.0) < 0.5   # сумма сохранена


def test_tempo_one_is_unchanged():
    import dataclasses
    segs = _segs_three_scenes()
    paths = ["a.mp4", "b.mp4", "c.mp4"]
    plan = PlanBuilder(dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, plan_tempo=1.0)).build_from_broll(
        paths, target_duration=12.0, transcript_segments=segs, output_format="16:9",
        weights=[1 / 3, 1 / 3, 1 / 3], clip_durations=[10.0, 10.0, 10.0],
    )
    assert len(plan.scenes) == 3
    assert all(s.source_in == 0.0 for s in plan.scenes)


def test_keyword_cuts_word_level():
    """Пословный монтаж: кадры меняются на ключевых словах, клип — под окно."""
    import dataclasses
    segs = [
        {"start": 0.0, "end": 5.0, "text": "мы сидим и говорим телефон", "words": [
            {"word": "мы", "start": 0.0, "end": 0.4}, {"word": "сидим", "start": 0.4, "end": 1.0},
            {"word": "и", "start": 1.0, "end": 1.2}, {"word": "говорим", "start": 1.2, "end": 2.0},
            {"word": "телефон", "start": 3.0, "end": 3.6}]},
        {"start": 5.0, "end": 10.0, "text": "потом выпивают", "words": [
            {"word": "потом", "start": 5.0, "end": 5.4}, {"word": "выпивают", "start": 8.0, "end": 8.8}]},
    ]
    paths = [
        "phone_notification_typing_01.mp4",
        "friends_party_celebration_01.mp4",
        "lonely_person_window_01.mp4",
    ]
    s = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, keyword_cuts=True, min_shot_sec=2.0)
    plan = PlanBuilder(s).build_from_broll(
        paths, target_duration=10.0, transcript_segments=segs,
        output_format="16:9", clip_durations=[20.0, 20.0, 20.0],
    )
    starts = [round(x.timeline_start, 1) for x in plan.scenes]
    assert 3.0 in starts, starts  # рез на «телефон»
    assert 8.0 in starts, starts  # рез на «выпивают»
    # клип окна 3–8с — про телефон
    i_phone = starts.index(3.0)
    assert "phone" in plan.scenes[i_phone].source_path


def test_keyword_cuts_off_by_default():
    import dataclasses
    segs = _segs_three_scenes()
    paths = ["a.mp4", "b.mp4", "c.mp4"]
    plan = PlanBuilder(dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, keyword_cuts=False)).build_from_broll(
        paths, target_duration=12.0, transcript_segments=segs, output_format="16:9",
        weights=[1 / 3, 1 / 3, 1 / 3], clip_durations=[10.0, 10.0, 10.0],
    )
    assert len(plan.scenes) == 3  # обычный режим — по сценам


def test_weights_follow_reordered_paths():
    """Веса (длительности слотов) переезжают вместе с клипами."""
    segs = _segs_three_scenes()
    paths = ["night_city_lights_01.mp4", "childhood_friends_playing_01.mp4"]
    builder = PlanBuilder(DEFAULT_CINEMATIC_SETTINGS)
    plan = builder.build_from_broll(
        paths, target_duration=10.0, transcript_segments=segs,
        output_format="16:9", weights=[0.7, 0.3], clip_durations=[7.0, 3.0],
    )
    # childhood-клип должен стать первым слотом и получить вес 0.3 → слот ~3с
    assert plan.scenes[0].source_path == "childhood_friends_playing_01.mp4"
    d0 = plan.scenes[0].timeline_end - plan.scenes[0].timeline_start
    d1 = plan.scenes[1].timeline_end - plan.scenes[1].timeline_start
    assert abs(d0 - 3.0) < 0.01 and abs(d1 - 7.0) < 0.01
