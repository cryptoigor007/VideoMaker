"""Unified RenderGraph for preview AND production.

Every scene is normalized to CFR/fps/format/size before concat or xfade.
xfade offsets come from TimelineResult (cumulative), not local scene duration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import CinematicSettings
from .edit_plan import EditPlan, Scene
from .motion import enhance_input_filter
from .profiles import build_eq_filter
from .timeline import TimelineResult, apply_rhythm, PlacedTransition
from .transitions import xfade_filter


@dataclass
class SceneFilter:
    scene_id: str
    source_path: str
    source_in: float
    source_out: float
    filter_chain: str          # after [i:v] label, before output label
    timeline_start: float
    timeline_end: float
    label: str                 # e.g. v0
    is_image: bool = False


@dataclass
class RenderGraph:
    scene_filters: list[SceneFilter]
    concat_or_xfade: str
    total_duration: float
    out_w: int
    out_h: int
    fps: int
    warnings: list[str] = field(default_factory=list)
    transitions: list[PlacedTransition] = field(default_factory=list)


def _scene_chain(
    scene: Scene,
    settings: CinematicSettings,
    *,
    out_w: int,
    out_h: int,
    fps: int,
    input_index: int,
    is_image: bool = False,
) -> tuple[str, str]:
    """Return (label, filter fragment ending with [label]).

    Video: trim source_in/out then motion/color.
    Image: input already looped to duration; no trim — Ken Burns via motion.
    """
    dur = max(0.05, scene.source_out - scene.source_in)
    eq = None
    if settings.color_intensity > 0:
        eq = build_eq_filter(scene.color_profile or settings.profile, settings.color_intensity)

    body = enhance_input_filter(
        scene.motion,
        eq,
        duration_sec=dur,
        fps=fps,
        out_w=out_w,
        out_h=out_h,
        max_zoom=settings.max_zoom,
        crop_x=scene.crop.x,
        crop_y=scene.crop.y,
        crop_w=scene.crop.w,
        crop_h=scene.crop.h,
    )
    label = f"v{input_index}"
    # [VM-FIX] Кадровая точность плана: ровно N кадров на выходе
    # (tpad клонирует хвост, trim=end_frame режет по кадрам). Без этого
    # накопленный дрейф «номинал vs факт» (±кадр на план) уводит xfade-offset
    # за конец цепочки — и ffmpeg молча обрывает ВЕСЬ дальнейший вывод
    # (видео короче озвучки, стоп-кадр).
    n_frames = max(1, int(round(dur * fps)))
    tail = (
        "tpad=stop_mode=clone:stop_duration=0.1,"
        f"trim=end_frame={n_frames},settb=AVTB"
    )
    if is_image:
        # Hold still for slot duration (Ken Burns via body), then CFR
        chain = (
            f"[{input_index}:v]"
            f"trim=duration={dur:.4f},"
            f"setpts=PTS-STARTPTS,"
            f"{body},"
            f"fps={fps},format=yuv420p,setsar=1,"
            f"{tail}"
            f"[{label}]"
        )
    else:
        chain = (
            f"[{input_index}:v]"
            f"trim=start={scene.source_in:.4f}:end={scene.source_out:.4f},"
            f"setpts=PTS-STARTPTS,"
            f"{body},"
            f"fps={fps},format=yuv420p,setsar=1,"
            f"{tail}"
            f"[{label}]"
        )
    return label, chain


def build_render_graph(
    plan: EditPlan,
    settings: CinematicSettings,
    *,
    out_w: int = 1920,
    out_h: int = 1080,
    fps: int = 30,
    timeline: TimelineResult | None = None,
    apply_timeline_rhythm: bool = True,
    **_kwargs,
) -> RenderGraph:
    warnings: list[str] = []
    if timeline is None and apply_timeline_rhythm:
        timeline = apply_rhythm(plan.scenes, settings, target_duration=plan.target_duration or plan.total_duration)
        warnings.extend(timeline.warnings)
    elif timeline is None:
        from .timeline import TimelineResult
        timeline = TimelineResult(scenes=list(plan.scenes), total_duration=plan.total_duration)

    scenes = timeline.scenes
    if not scenes:
        return RenderGraph([], "", 0.0, out_w, out_h, fps, warnings=["empty plan"])

    graph = assemble_render_graph(
        scenes, list(timeline.transitions), settings, out_w=out_w, out_h=out_h, fps=fps,
    )
    graph.warnings = warnings + graph.warnings
    return graph


def assemble_render_graph(
    scenes: list,
    transitions: list,
    settings: CinematicSettings,
    *,
    out_w: int = 1920,
    out_h: int = 1080,
    fps: int = 30,
) -> RenderGraph:
    """Собрать фильтр-граф из готовых сцен и переходов (без повторного ритма).

    Используется и для полного графа (build_render_graph), и для чанкового
    рендера мастера (большие графы съедали всю память — см. video.py).
    """
    scene_filters: list[SceneFilter] = []
    parts: list[str] = []
    labels: list[str] = []

    from .media_assets import is_image_path

    for i, sc in enumerate(scenes):
        img = is_image_path(sc.source_path)
        label, chain = _scene_chain(
            sc, settings, out_w=out_w, out_h=out_h, fps=fps, input_index=i, is_image=img,
        )
        parts.append(chain)
        labels.append(label)
        scene_filters.append(
            SceneFilter(
                scene_id=sc.scene_id,
                source_path=sc.source_path,
                source_in=sc.source_in,
                source_out=sc.source_out,
                filter_chain=chain,
                timeline_start=sc.timeline_start,
                timeline_end=sc.timeline_end,
                label=label,
                is_image=img,
            )
        )

    transitions = list(transitions)
    use_xfade = any(t.spec.duration > 0.01 and t.spec.kind != "hard" for t in transitions)

    # [VM-FIX] Длины планов — в кадрах (совпадает с trim=end_frame в цепочке):
    # цепочка xfade/concat считается точно, без накопленного дрейфа.
    _scene_frames = [
        max(1, int(round(max(0.05, float(sc.source_out - sc.source_in)) * fps)))
        for sc in scenes
    ]
    _chain_frames = sum(_scene_frames)

    if not use_xfade or len(labels) < 2:
        ins = "".join(f"[{l}]" for l in labels)
        n = len(labels)
        parts.append(f"{ins}concat=n={n}:v=1:a=0[vout]")
    else:
        current = labels[0]
        # [VM-FIX] оффсет xfade — от РЕАЛЬНОЙ длины собранной цепочки,
        # в КАДРАХ: xfade квантует offset до кадра, а дрейф «номинал vs факт»
        # раньше уводил offset за конец цепочки → ffmpeg обрывал весь хвост.
        _cum_f = _scene_frames[0] if _scene_frames else 1
        for i, tr in enumerate(transitions):
            nxt = labels[i + 1]
            out_lab = f"xf{i}" if i < len(transitions) - 1 else "vout"
            _dn_f = _scene_frames[i + 1] if i + 1 < len(_scene_frames) else 1
            if tr.spec.duration > 0.01 and tr.spec.kind != "hard":
                _dur_f = max(1, int(round(float(tr.spec.duration) * fps)))
                _off_f = max(0, _cum_f - _dur_f)
                xf = xfade_filter(tr.spec.kind, _dur_f / float(fps), _off_f / float(fps))
                if xf:
                    parts.append(f"[{current}][{nxt}]{xf}[{out_lab}]")
                    _cum_f = _off_f + _dn_f
                else:
                    parts.append(f"[{current}][{nxt}]concat=n=2:v=1:a=0[{out_lab}]")
                    _cum_f = _cum_f + _dn_f
            else:
                parts.append(f"[{current}][{nxt}]concat=n=2:v=1:a=0[{out_lab}]")
                _cum_f = _cum_f + _dn_f
            current = out_lab
        _chain_frames = _cum_f

    filter_complex = ";".join(parts)
    return RenderGraph(
        scene_filters=scene_filters,
        concat_or_xfade=filter_complex,
        total_duration=_chain_frames / float(fps) if fps else 0.0,
        out_w=out_w,
        out_h=out_h,
        fps=fps,
        warnings=[],
        transitions=transitions,
    )


def graph_to_ffmpeg_cmd(
    graph: RenderGraph,
    output_path: str,
    *,
    video_codec: str = "libx264",
    preset: str = "veryfast",
    crf: int = 23,
) -> list[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for sf in graph.scene_filters:
        if sf.is_image:
            hold = max(0.05, sf.source_out - sf.source_in)
            # loop still for hold duration at graph fps
            cmd += [
                "-loop", "1",
                "-framerate", str(graph.fps),
                "-t", f"{hold:.4f}",
                "-i", sf.source_path,
            ]
        else:
            cmd += ["-i", sf.source_path]
    cmd += [
        "-filter_complex", graph.concat_or_xfade,
        "-map", "[vout]",
        "-an",
        "-c:v", video_codec,
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(graph.fps),
        "-movflags", "+faststart",
        "-t", f"{graph.total_duration:.3f}",
        output_path,
    ]
    return cmd
