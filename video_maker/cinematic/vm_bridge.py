"""Bridge between Cinematic Enhancement and VideoMaker production encode.

Production and preview share build_render_graph semantics.
When CE is OFF, filter graph matches legacy scale+pad+concat.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from .config import CinematicSettings, DEFAULT_CINEMATIC_SETTINGS
from .edit_plan import EditPlan, Scene, CropRect, MotionKeyframe
from .feature_flag import is_enabled, is_enabled_for_geometry, is_4k_geometry
from .planner import PlanBuilder
from .render_graph import build_render_graph
from .motion import build_zoompan_expr
from .profiles import build_eq_filter
from .timeline import apply_rhythm_to_plan
from .validator import validate_plan

log = logging.getLogger(__name__)


def _default(value, default):
    """Use default only when value is None (0 / 0.0 / False are valid)."""
    return default if value is None else value


def settings_from_vm(settings_obj: Any) -> CinematicSettings:
    """Read cinematic_* from Settings, dict, or PipelineContext."""
    def g(name: str, default=None):
        if isinstance(settings_obj, dict):
            return settings_obj.get(name, default)
        # Prefer cinematic_settings object if present
        block = getattr(settings_obj, "cinematic_settings", None)
        if block is not None and hasattr(block, name.replace("cinematic_", "")):
            pass
        if isinstance(block, CinematicSettings):
            return block
        return getattr(settings_obj, name, default)

    block = g("cinematic_settings")
    if isinstance(block, CinematicSettings):
        return block
    if isinstance(block, dict):
        return CinematicSettings.from_dict(block)

    nested = g("cinematic")
    if isinstance(nested, dict):
        return CinematicSettings.from_dict(nested)

    raw_enabled = g("cinematic_enabled", False)
    raw_color = g("cinematic_color", None)
    raw_tempo = g("cinematic_tempo", None)
    raw_zoom = g("cinematic_max_zoom", None)
    raw_seed = g("cinematic_seed", None)

    return CinematicSettings(
        enabled=bool(_default(raw_enabled, False)),
        profile=str(g("cinematic_profile", None) or "cinematic_documentary"),
        motion_intensity=str(g("cinematic_motion", None) or "low"),  # type: ignore
        plan_tempo=float(_default(raw_tempo, 1.0)),
        color_intensity=float(_default(raw_color, 0.5)),
        allowed_transitions=str(g("cinematic_transitions", None) or "hard_only"),  # type: ignore
        framing_mode=str(g("cinematic_framing", None) or "center"),  # type: ignore
        reuse_existing_subs=bool(_default(g("cinematic_reuse_subs", None), True)),
        max_zoom=float(_default(raw_zoom, 1.25)),
        kinetic_typography=str(g("cinematic_kinetic", None) or "off"),
        smart_broll=bool(_default(g("cinematic_smart_broll", None), True)),
        keyword_cuts=bool(_default(g("cinematic_keyword_cuts", None), False)),
        min_shot_sec=float(_default(g("cinematic_min_shot", None), 2.0)),
        seed=int(_default(raw_seed, 42)),
        transition_sfx=bool(_default(g("cinematic_transition_sfx", None), False)),
    ).normalize()


def plan_from_selected_clips(
    selected: Sequence[str],
    *,
    target_duration: float,
    settings: CinematicSettings,
    transcript_segments: list | None = None,
    output_format: str = "16:9",
    clip_durations: Sequence[float] | None = None,
    scene_bound: bool = False,
    scene_preferred: dict[int, list[str]] | None = None,
) -> EditPlan:
    """
    Build EditPlan from the FINAL selected list (same list FFmpeg will use).

    If clip_durations is provided (from ffprobe), source intervals respect
    real media length. Otherwise equal slices of target_duration.
    """
    n = max(1, len(selected))
    from .media_assets import is_image_path
    if clip_durations and len(clip_durations) == len(selected):
        # Weight by usable media length; stills get equal share (not infinite)
        effective = []
        for path, d in zip(selected, clip_durations):
            if is_image_path(path):
                effective.append(1.0)  # equal weight among stills
            else:
                effective.append(max(0.05, float(d)))
        total_src = sum(effective) or 1.0
        weights = [e / total_src for e in effective]
    else:
        weights = [1.0 / n] * n

    builder = PlanBuilder(settings)
    plan = builder.build_from_broll(
        list(selected),
        target_duration=target_duration,
        transcript_segments=transcript_segments or [],
        output_format=output_format,  # type: ignore
        weights=weights,
        clip_durations=list(clip_durations) if clip_durations else None,
        scene_bound=scene_bound,
        scene_preferred=scene_preferred,
    )
    # [VM-FIX] Целевая длина = озвучка + 0.35с запаса: рендер кадрово-точный,
    # но округление кадров может укоротить цепочку на доли кадра. Лишнее
    # срежет -t по длине озвучки; недостаток давал бы стоп-кадр в финале.
    _tgt = float(target_duration or 0.0)
    if _tgt > 1.0:
        try:
            from .timeline import apply_rhythm as _apply_rhythm

            _tl = _apply_rhythm(plan.scenes, settings, target_duration=_tgt + 0.35)
            plan.scenes = _tl.scenes
            plan.total_duration = _tl.total_duration
            plan.target_duration = _tl.total_duration
            plan.warnings = list(plan.warnings) + list(_tl.warnings)
        except Exception:
            plan = apply_rhythm_to_plan(plan, settings)
    else:
        plan = apply_rhythm_to_plan(plan, settings)
    errs = validate_plan(plan)
    if errs:
        log.warning("CE plan validation: %s", errs[:5])
    return plan


def legacy_filter_complex(
    n: int,
    width: int,
    height: int,
) -> str:
    """Exact baseline shape: scale+pad+fps+format → concat."""
    filters = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(n))
    filters.append(f"{concat_in}concat=n={n}:v=1:a=0[v]")
    return ";".join(filters)


def build_ce_filter_complex(
    selected: Sequence[str],
    *,
    width: int = 3840,
    height: int = 2160,
    target_duration: float = 0.0,
    settings: CinematicSettings | None = None,
    plan: EditPlan | None = None,
    fps: int = 30,
) -> str:
    """
    Production filter_complex for fit_video_to_duration.

    OFF / no plan → legacy graph (label [v] at end).
    ON → unified RenderGraph (trim + motion + color + concat/xfade → [vout]),
    then rename [vout]→[v] for host compatibility.
    """
    settings = settings or DEFAULT_CINEMATIC_SETTINGS
    n = len(selected)
    if n == 0:
        return legacy_filter_complex(1, width, height)

    # CE only at 4K UHD (3840×2160 or 2160×3840)
    if not is_enabled_for_geometry(settings, width, height) or plan is None or not plan.scenes:
        if is_enabled(settings) and not is_4k_geometry(width, height):
            log.info(
                "[CE] skip non-4K geometry %sx%s → legacy (only 3840x2160 / 2160x3840)",
                width, height,
            )
        return legacy_filter_complex(n, width, height)

    # Align plan scenes to selected list length
    scenes = list(plan.scenes)
    if len(scenes) != n:
        log.warning("CE plan scenes=%d selected=%d — pad/trim to match", len(scenes), n)
        while len(scenes) < n:
            last = scenes[-1] if scenes else None
            if last is None:
                break
            scenes.append(last)
        scenes = scenes[:n]
        plan.scenes = scenes

    try:
        graph = build_render_graph(plan, settings, out_w=width, out_h=height, fps=fps)
        fc = graph.concat_or_xfade
        # Host maps [v]; accept either
        if "[vout]" in fc and "[v]" not in fc.split("[vout]")[-1]:
            fc = fc.replace("[vout]", "[v]")
        return fc
    except Exception as e:
        log.warning("CE graph failed (%s) — legacy fallback", e)
        return legacy_filter_complex(n, width, height)


def enhance_input_filter(index_or_motion, eq_filter=None, **kwargs):
    """Compatible wrapper.

    Styles:
      enhance_input_filter(motion, eq, duration_sec=..., out_w=...)
      enhance_input_filter(0, width=1920, height=1080, settings=s)  # legacy test
    """
    from .motion import enhance_input_filter as _enh, build_zoompan_expr
    from .profiles import build_eq_filter as _eq

    # Legacy: first arg is input index int
    if isinstance(index_or_motion, int):
        idx = index_or_motion
        settings = kwargs.get("settings")
        w = kwargs.get("width") or kwargs.get("out_w") or 1920
        h = kwargs.get("height") or kwargs.get("out_h") or 1080
        body = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
        )
        if settings is not None and getattr(settings, "enabled", False):
            # optional eq
            ci = float(getattr(settings, "color_intensity", 0) or 0)
            if ci > 0:
                eq = _eq(getattr(settings, "profile", "cinematic_documentary"), ci)
                if eq:
                    body = f"{body},{eq}"
        return f"[{idx}:v]{body}[v{idx}]"
    return _enh(index_or_motion, eq_filter, **kwargs)


def probe_clip_duration(path: str) -> float:
    """Best-effort duration via ffprobe; 0.0 on failure."""
    import json
    import subprocess
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", path,
            ],
            capture_output=True, text=True, timeout=15, check=True,
        )
        data = json.loads(r.stdout or "{}")
        return float(data.get("format", {}).get("duration") or 0.0)
    except Exception:
        return 0.0
