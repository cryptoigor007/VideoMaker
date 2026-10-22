"""
Thin hooks that VideoMaker pipeline stages can call.

These functions are the ONLY integration surface required for CE-01..CE-03.
They never import VideoMaker internals; the host passes plain dicts/objects.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import CinematicSettings, DEFAULT_CINEMATIC_SETTINGS
from .edit_plan import EditPlan
from .feature_flag import is_enabled, should_skip_cinematic, load_from_project_settings
from .planner import PlanBuilder
from .validator import validate_plan
from .cache_key import visual_cache_key

log = logging.getLogger(__name__)


def resolve_settings(project_settings: dict[str, Any] | None) -> CinematicSettings:
    if not project_settings:
        return DEFAULT_CINEMATIC_SETTINGS
    return load_from_project_settings(project_settings)


def maybe_build_plan(
    *,
    project_settings: dict[str, Any] | None,
    transcript_segments: list[dict] | None = None,
    broll_clips: list[dict] | None = None,
    analysis: dict | None = None,
    audio_duration: float = 0.0,
    source_audio_hash: str = "",
    output_format: str = "16:9",
) -> EditPlan | None:
    """
    Call after GeminiStage / B-roll mapping.
    Returns None when CE is disabled (caller continues legacy path).
    """
    settings = resolve_settings(project_settings)
    if should_skip_cinematic(settings):
        log.debug("CE disabled — skipping plan build")
        return None

    plan = PlanBuilder(settings).build(
        transcript_segments=transcript_segments,
        broll_clips=broll_clips,
        analysis=analysis,
        audio_duration=audio_duration,
        source_audio_hash=source_audio_hash,
        output_format=output_format,
    )
    errs = validate_plan(plan)
    if errs:
        log.warning("CE EditPlan validation issues: %s", errs[:8])
        plan.warnings.extend(errs)
    log.info(
        "CE plan built: scenes=%d duration=%.1f algo=%s",
        len(plan.scenes),
        plan.total_duration,
        plan.algorithm_version,
    )
    return plan


def filter_graph_extras_for_scene(
    scene: dict[str, Any] | Any,
    settings: CinematicSettings,
    *,
    out_w: int,
    out_h: int,
    fps: float = 30.0,
) -> str:
    """
    Return additional vf fragments for one scene (crop/zoom/color).
    Empty string when nothing to apply.
    Host concatenates into existing filter_complex.
    """
    from .edit_plan import Scene, CropRect, MotionKeyframe
    from .motion import build_zoompan_expr
    from .profiles import build_eq_filter

    if isinstance(scene, Scene):
        sc = scene
    else:
        # duck-typed dict from host
        crop_d = scene.get("crop") or {}
        motion_d = scene.get("motion") or []
        sc = Scene(
            scene_id=str(scene.get("scene_id", "x")),
            asset_id=str(scene.get("asset_id", "")),
            source_path=str(scene.get("source_path", "")),
            source_in=float(scene.get("source_in", 0)),
            source_out=float(scene.get("source_out", 1)),
            timeline_start=float(scene.get("timeline_start", 0)),
            timeline_end=float(scene.get("timeline_end", 1)),
            crop=CropRect(**crop_d) if crop_d else CropRect(),
            motion=[MotionKeyframe(**m) for m in motion_d] if motion_d else [],
            color_profile=str(scene.get("color_profile", settings.profile)),
        )

    parts: list[str] = []
    zp = build_zoompan_expr(
        sc.motion, sc.crop, sc.duration() or 1.0,
        fps=fps, out_w=out_w, out_h=out_h,
    )
    if zp:
        parts.append(zp)
    eq = build_eq_filter(sc.color_profile, settings.color_intensity)
    if eq:
        parts.append(eq)
    return ",".join(parts)


def cache_key_for_visual(
    settings: CinematicSettings,
    plan: EditPlan | None,
    source_hashes: list[str],
    output_format: str,
    width: int,
    height: int,
    fps: float,
) -> str:
    return visual_cache_key(
        source_hashes=source_hashes,
        settings=settings,
        plan=plan,
        output_format=output_format,
        width=width,
        height=height,
        fps=fps,
    )


def maybe_render_transition_sfx(
    *,
    settings: CinematicSettings | None,
    timeline_transitions: list | None,
    total_duration: float,
    staging_dir: str | Any,
    narration_path: str | None = None,
) -> dict[str, Any]:
    """
    If transition_sfx enabled, render a whoosh track and optionally mix under narration.

    Returns dict:
      enabled, sfx_path, mixed_narration_path, warnings
    Host can replace narration with mixed_narration_path before master mux.
    """
    from pathlib import Path
    from .transition_sfx import collect_transition_sfx, render_transition_sfx_track, mix_sfx_under_narration

    result: dict[str, Any] = {
        "enabled": False,
        "sfx_path": None,
        "mixed_narration_path": None,
        "warnings": [],
    }
    if settings is None or not getattr(settings, "transition_sfx", False):
        return result
    if not timeline_transitions or total_duration <= 0:
        result["warnings"].append("no transitions or zero duration")
        return result

    specs = collect_transition_sfx(timeline_transitions, enabled=True)
    if not specs:
        result["warnings"].append("no soft transitions for SFX")
        return result

    staging = Path(str(staging_dir))
    staging.mkdir(parents=True, exist_ok=True)
    sfx_path = staging / "ce_transition_sfx.wav"
    rendered = render_transition_sfx_track(specs, total_duration=total_duration, output_path=sfx_path)
    if rendered is None:
        result["warnings"].append("SFX render failed")
        return result

    result["enabled"] = True
    result["sfx_path"] = str(rendered)
    log.info("CE transition SFX: %d events → %s", len(specs), rendered)

    if narration_path and Path(narration_path).is_file():
        mixed = staging / "ce_narration_with_sfx.wav"
        out = mix_sfx_under_narration(narration_path, rendered, mixed)
        if out is not None:
            result["mixed_narration_path"] = str(out)
            log.info("CE narration mixed with SFX → %s", out)
        else:
            result["warnings"].append("SFX mix under narration failed")
    return result


def maybe_build_kinetic_ass(
    *,
    settings: CinematicSettings | None,
    transcript_segments: list[dict] | None,
    output_path: str | Any,
    play_res_x: int = 3840,
    play_res_y: int = 2160,
    aspect: str = "16:9",
) -> str | None:
    """
    If kinetic_typography != off, write a kinetic ASS file and return its path.
    Host can burn via existing subtitle path or as extra overlay.
    Returns None when disabled or no segments.
    """
    if settings is None:
        return None
    mode = str(getattr(settings, "kinetic_typography", "off") or "off").lower()
    if mode in ("", "off", "none", "false", "0"):
        return None
    segs = list(transcript_segments or [])
    if not segs:
        log.debug("CE kinetic: no transcript segments")
        return None
    try:
        from .kinetic_typography import write_kinetic_ass, words_from_segments
        from pathlib import Path

        words = words_from_segments(segs)
        if not words:
            return None
        out = Path(str(output_path))
        out.parent.mkdir(parents=True, exist_ok=True)
        style = mode if mode in ("subtle", "bold", "title") else "subtle"
        write_kinetic_ass(
            out,
            segs,
            style=style,
            play_res_x=play_res_x,
            play_res_y=play_res_y,
        )
        if not out.is_file() or out.stat().st_size < 20:
            return None
        log.info("CE kinetic ASS → %s mode=%s words=%d", out, style, len(words))
        return str(out)
    except Exception as e:
        log.warning("CE kinetic failed: %s", e)
        return None


def burn_kinetic_onto_video(
    video_path: str | Any,
    ass_path: str | Any,
    output_path: str | Any,
    *,
    timeout: int = 600,
) -> str | None:
    """Burn kinetic ASS onto video via FFmpeg libass. Returns output path or None."""
    from pathlib import Path
    import subprocess
    import shutil

    video_path, ass_path, output_path = Path(video_path), Path(ass_path), Path(output_path)
    if not video_path.is_file() or not ass_path.is_file() or ass_path.stat().st_size < 20:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # [VM-SB] Используем ffmpeg приложения (с libass): системный может быть без ass-фильтра
    ffmpeg_bin = "ffmpeg"
    try:
        from video_maker.engines.video import _ffmpeg_bin as _app_ffmpeg_bin
        ffmpeg_bin = _app_ffmpeg_bin()
    except Exception:
        pass
    # Escape for filtergraph
    ap = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'").replace(" ", "\\ ")
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"ass={ap}",
        "-c:a", "copy",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    # Prefer VideoToolbox on Mac if available
    try:
        r = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        if "h264_videotoolbox" in (r.stdout or ""):
            cmd = [
                ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(video_path),
                "-vf", f"ass={ap}",
                "-c:a", "copy",
                "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "1",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ]
    except Exception:
        pass
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 1000:
            return str(output_path)
        log.warning("kinetic burn failed: %s", (r.stderr or "")[-300:])
        return None
    except Exception as e:
        log.warning("kinetic burn error: %s", e)
        return None
