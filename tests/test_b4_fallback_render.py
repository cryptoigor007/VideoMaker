"""B4: treated-обработка + нейтральный фон для graphic_fallback.

Тест синтетический: 3 сцены (обычная / graphic / treated), 4K-путь CE.
Тяжёлые рендеры — короткие (≈2 c на сцену), чтобы тест шёл минуту, не больше.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from video_maker.cinematic.config import DEFAULT_CINEMATIC_SETTINGS
from video_maker.cinematic.edit_plan import Scene
from video_maker.cinematic.fallback_render import (
    GRAPHIC_PATH_PREFIX,
    inject_scene_treatments,
    materialize_graphic_scenes,
    probe_media,
    scene_flags,
    treatment_filter,
)
from video_maker.cinematic.planner import PlanBuilder
from video_maker.cinematic.vm_bridge import build_ce_filter_complex


def _scene(reason: str = "", warnings: list[str] | None = None,
           source: str = "clip.mp4") -> Scene:
    return Scene(
        scene_id="sc_test",
        asset_id="a",
        source_path=source,
        source_in=0.0,
        source_out=2.0,
        timeline_start=0.0,
        timeline_end=2.0,
        reason=reason,
        warnings=list(warnings or []),
    )


def test_treatment_flags_light_proxy_and_normal():
    light = _scene("scene-bound; treated=1; fallback=associative")
    proxy = _scene("scene-bound; treated=1; fallback=proxy_forced")
    normal = _scene("scene-bound pool order")
    assert scene_flags(light).treated is True
    assert "sigma=5" in treatment_filter(light)
    assert "sigma=9" in treatment_filter(proxy)
    assert "eq=contrast=1.05:saturation=0.95" in treatment_filter(light)
    assert treatment_filter(normal) == ""


def test_inject_only_treated_segment():
    fc = (
        "[0:v]trim=start=0:end=2,setpts=PTS-STARTPTS,scale=3840:2160,"
        "format=yuv420p,setsar=1,trim=end_frame=60[v0];"
        "[1:v]trim=start=0:end=2,setpts=PTS-STARTPTS,scale=3840:2160,"
        "format=yuv420p,setsar=1,trim=end_frame=60[v1];"
        "[2:v]trim=start=0:end=2,setpts=PTS-STARTPTS,scale=3840:2160,"
        "format=yuv420p,setsar=1,trim=end_frame=60[v2];"
        "[v0][v1][v2]concat=n=3:v=1:a=0[vout]"
    )
    scenes = [
        _scene("scene-bound pool order"),
        _scene("x; treated=1; fallback=literal_proxy"),
        _scene("x; treated=1; fallback=associative"),
    ]
    out = inject_scene_treatments(fc, scenes).replace("[vout]", "[v]")
    segs = out.split(";")
    assert "gblur" not in segs[0]
    assert "sigma=9" in segs[1]
    assert "sigma=5" in segs[2]
    # идемпотентность: второй проход не добавляет второй blur
    assert inject_scene_treatments(out, scenes).count("gblur") == 2
    # обычные сцены не меняются вовсе
    normal = [_scene("scene-bound pool order") for _ in range(3)]
    assert inject_scene_treatments(fc, normal) == fc


def _ffmpeg() -> str:
    return "ffmpeg"


def _make_clip(path: Path, duration: float = 2.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            _ffmpeg(), "-hide_banner", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=s=1280x720:r=30:d={duration}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-an", str(path),
        ],
        check=True, timeout=120,
    )


def test_planner_graphic_scene_marker_not_foreign_clip(tmp_path: Path):
    p1 = tmp_path / "01_scene" / "a.mp4"
    p3 = tmp_path / "03_scene" / "c.mp4"
    _make_clip(p1)
    _make_clip(p3)
    segs = [
        {"start": 0.0, "end": 2.0, "text": "первая сцена"},
        {"start": 2.0, "end": 4.0, "text": "вторая сцена без клипа"},
        {"start": 4.0, "end": 6.0, "text": "третья сцена"},
    ]
    s = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, enabled=True)
    s.scene_flags = {
        1: {"fallback": "none", "treated": False, "graphic_fallback": False},
        2: {"fallback": "graphic", "treated": False, "graphic_fallback": True},
        3: {"fallback": "associative", "treated": True, "graphic_fallback": False},
    }
    plan = PlanBuilder(s).build_from_broll(
        [str(p1), str(p3)], target_duration=6.0,
        transcript_segments=segs, output_format="16:9",
        scene_bound=True,
        scene_preferred={1: [str(p1)], 3: [str(p3)]},
        clip_durations=[2.4, 2.4],
    )
    assert len(plan.scenes) == 3
    assert plan.scenes[0].source_path == str(p1)
    assert plan.scenes[1].source_path.startswith(GRAPHIC_PATH_PREFIX)
    assert plan.scenes[2].source_path == str(p3)
    assert "graphic_fallback" in plan.scenes[1].warnings
    assert "treated" in plan.scenes[2].warnings
    # treated получил push-in ≥4%
    m = plan.scenes[2].motion
    assert m and m[-1].scale - m[0].scale >= 0.039


def test_graphic_background_4k_and_filter_graph(tmp_path: Path):
    """Маркер → реальный 4K-фон + ffmpeg-команда с blur только у treated."""
    p1 = tmp_path / "01_scene" / "a.mp4"
    p3 = tmp_path / "03_scene" / "c.mp4"
    _make_clip(p1)
    _make_clip(p3)
    segs = [
        {"start": 0.0, "end": 2.0, "text": "первая сцена"},
        {"start": 2.0, "end": 4.0, "text": "вторая сцена без клипа"},
        {"start": 4.0, "end": 6.0, "text": "третья сцена"},
    ]
    s = dataclasses.replace(DEFAULT_CINEMATIC_SETTINGS, enabled=True)
    s.scene_flags = {
        1: {"fallback": "none", "treated": False, "graphic_fallback": False},
        2: {"fallback": "graphic", "treated": False, "graphic_fallback": True},
        3: {"fallback": "associative", "treated": True, "graphic_fallback": False},
    }
    plan = PlanBuilder(s).build_from_broll(
        [str(p1), str(p3)], target_duration=6.0,
        transcript_segments=segs, output_format="16:9",
        scene_bound=True,
        scene_preferred={1: [str(p1)], 3: [str(p3)]},
        clip_durations=[2.4, 2.4],
    )
    cache = tmp_path / "graphic_cards"
    n = materialize_graphic_scenes(
        plan, cache_dir=cache, width=3840, height=2160, fps=30,
    )
    assert n == 1
    bg = plan.scenes[1].source_path
    assert not bg.startswith(GRAPHIC_PATH_PREFIX)
    w, h, dur = probe_media(bg)
    assert (w, h) == (3840, 2160)
    assert dur >= plan.scenes[1].source_out - plan.scenes[1].source_in

    selected = [sc.source_path for sc in plan.scenes]
    fc = build_ce_filter_complex(
        selected, width=3840, height=2160, target_duration=6.0,
        settings=s, plan=plan,
    ).replace("[vout]", "[v]")
    fc = inject_scene_treatments(fc, plan.scenes)
    segs_fc = fc.split(";")
    assert "gblur" not in segs_fc[0]
    assert "gblur" not in segs_fc[1]          # graphic-фон уже нейтральный
    assert "gblur=sigma=5.00" in segs_fc[2]   # treated associative
    assert "eq=contrast=1.05:saturation=0.95" in segs_fc[2]

    # Реальная ffmpeg-команда по этому графу (4K, 6 c) — проверяем, что валидна.
    out = tmp_path / "master_check.mp4"
    cmd = [_ffmpeg(), "-hide_banner", "-y", "-loglevel", "error"]
    for path in selected:
        cmd += ["-i", path]
    cmd += ["-filter_complex", fc, "-map", "[v]", "-t", "6.0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", "-an", str(out)]
    subprocess.run(cmd, check=True, timeout=300)
    w, h, dur = probe_media(out)
    assert (w, h) == (3840, 2160)
    assert 5.7 <= dur <= 6.4


def test_procedural_background_without_text(tmp_path: Path):
    """Без транскрипта — процедурный 4K-фон без текста, валидный ffprobe."""
    sc = _scene("x; graphic_fallback=1; fallback=graphic",
                source=f"{GRAPHIC_PATH_PREFIX}scene/7")
    from video_maker.cinematic.edit_plan import EditPlan

    plan = EditPlan(scenes=[sc], total_duration=1.5, target_duration=1.5)
    logs: list[str] = []
    n = materialize_graphic_scenes(
        plan, cache_dir=tmp_path / "g", width=3840, height=2160, fps=30,
        log_fn=logs.append,
    )
    assert n == 1
    assert any("[РЕНДЕР] graphic-сцена 1: нейтральный фон" in m for m in logs)
    assert not sc.source_path.startswith(GRAPHIC_PATH_PREFIX)
    w, h, dur = probe_media(sc.source_path)
    assert (w, h) == (3840, 2160)
    assert dur >= 1.5


def test_conform_4k_materializes_graphic_marker_direct_call(tmp_path: Path):
    """P2: прямой conform_4k(plan=...) с vm-graphic:// не падает, а рендерит фон."""
    from video_maker.cinematic.edit_plan import EditPlan
    from video_maker.engines.video import conform_4k

    p1 = tmp_path / "01_scene" / "a.mp4"
    _make_clip(p1)
    sc1 = _scene(source=str(p1))
    sc2 = _scene("scene-bound; fallback=no_file",
                 source=f"{GRAPHIC_PATH_PREFIX}scene/2")
    sc2.scene_id = "sc_graphic_p2"
    sc2.timeline_start = 2.0
    sc2.timeline_end = 4.0
    plan = EditPlan(scenes=[sc1, sc2], total_duration=4.0, target_duration=4.0)

    out = tmp_path / "conform_light.mp4"
    conform_4k(
        plan=plan, output_path=str(out), target_duration=4.0,
        width=640, height=360, fps=30,
        cinematic_settings=DEFAULT_CINEMATIC_SETTINGS,
    )
    assert not plan.scenes[1].source_path.startswith(GRAPHIC_PATH_PREFIX)
    assert plan.scenes[1].source_path
    w, h, dur = probe_media(out)
    assert (w, h) == (640, 360)
    assert 3.6 <= dur <= 4.6
