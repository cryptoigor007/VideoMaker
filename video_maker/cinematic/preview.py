"""CE-02: generate a short preview from an EditPlan using FFmpeg.

Applies one scene's crop + motion + optional color.
Does NOT touch original source files (reads only, writes to staging).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .config import CinematicSettings
from .edit_plan import EditPlan, Scene
from .motion import build_zoompan_expr
from .profiles import build_eq_filter
from .validator import assert_valid, validate_plan

log = logging.getLogger(__name__)


def _run_ffmpeg(cmd: list[str], timeout: int = 120) -> None:
    log.info("ffmpeg: %s", " ".join(cmd[:12]) + ("..." if len(cmd) > 12 else ""))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (code {proc.returncode}):\n"
            f"stdout: {proc.stdout[-800:]}\nstderr: {proc.stderr[-1200:]}"
        )


def render_scene_preview(
    scene: Scene,
    *,
    settings: CinematicSettings,
    output_path: str | Path,
    fps: int = 30,
    out_w: int = 1280,
    out_h: int = 720,
    timeout: int = 90,
) -> Path:
    """
    Render a single scene to output_path with crop/zoom/color.
    Source file is never modified.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not scene.source_path or not Path(scene.source_path).is_file():
        raise FileNotFoundError(f"Source missing: {scene.source_path}")

    duration = scene.source_duration()
    if duration <= 0:
        raise ValueError("Scene has zero source duration")

    # Limit preview length
    max_prev = min(duration, settings.preview_range_sec, 20.0)
    src_in = scene.source_in
    # clamp
    src_in = max(0.0, src_in)

    vf_parts: list[str] = []

    # Motion / crop
    dur = min(max_prev, max(0.1, scene.source_out - scene.source_in))
    zp = build_zoompan_expr(
        scene.motion,
        duration_sec=dur,
        fps=int(fps),
        out_w=out_w,
        out_h=out_h,
        max_zoom=settings.max_zoom,
        crop_x=scene.crop.x,
        crop_y=scene.crop.y,
        crop_w=scene.crop.w,
        crop_h=scene.crop.h,
    )
    if zp:
        vf_parts.append(zp)
    else:
        # plain scale to target
        vf_parts.append(f"scale={out_w}:{out_h}:flags=lanczos:force_original_aspect_ratio=decrease")
        vf_parts.append(f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2")

    # Color
    eq = build_eq_filter(scene.color_profile, settings.color_intensity)
    if eq:
        vf_parts.append(eq)

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{src_in:.3f}",
        "-t", f"{max_prev:.3f}",
        "-i", scene.source_path,
        "-vf", vf,
        "-an",  # video-only preview; audio stays in full pipeline
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-frames:v", str(max(1, int(max_prev * fps))),
        "-movflags", "+faststart",
        str(out),
    ]
    # Write to temp name, probe, then publish
    tmp = out.parent / (out.stem + "_partial" + out.suffix)
    cmd[-1] = str(tmp)
    try:
        _run_ffmpeg(cmd, timeout=timeout)
        if not tmp.is_file() or tmp.stat().st_size < 100:
            raise RuntimeError(f"Preview not written: {tmp}")
        # ffprobe: must have video stream and positive duration
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_type:format=duration",
                "-of", "default=nw=1:nk=1",
                str(tmp),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"Preview ffprobe failed: {probe.stderr[-400:]}")
        lines = [ln.strip() for ln in (probe.stdout or "").splitlines() if ln.strip()]
        if "video" not in lines:
            raise RuntimeError(f"Preview has no video stream: {lines}")
        # duration line (if present)
        for ln in lines:
            try:
                d = float(ln)
                if d <= 0:
                    raise RuntimeError(f"Preview duration <= 0: {d}")
            except ValueError:
                pass
        tmp.replace(out)
    finally:
        if tmp.exists() and tmp != out:
            try:
                tmp.unlink()
            except OSError:
                pass
    return out


def render_plan_preview(
    plan: EditPlan,
    *,
    settings: CinematicSettings,
    output_dir: str | Path,
    scene_index: int = 0,
    fps: int = 30,
) -> Path:
    """
    Validate plan, pick one scene, render preview.
    Returns path to the generated MP4.
    """
    assert_valid(plan, require_existing_sources=True)
    if scene_index < 0 or scene_index >= len(plan.scenes):
        raise IndexError(f"scene_index {scene_index} out of range (n={len(plan.scenes)})")

    scene = plan.scenes[scene_index]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ce_preview_{scene.scene_id}.mp4"
    return render_scene_preview(
        scene,
        settings=settings,
        output_path=out_path,
        fps=fps,
    )


def make_synthetic_source(
    path: str | Path,
    *,
    duration: float = 6.0,
    width: int = 1280,
    height: int = 720,
    color: str = "blue",
    fps: int = 30,
) -> Path:
    """Create a solid-color test video for CE-02 smoke tests. Never used in production."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    _run_ffmpeg(cmd, timeout=30)
    return out


def render_plan_multiscene(
    plan: EditPlan,
    *,
    settings: CinematicSettings,
    output_path: str | Path,
    out_w: int = 640,
    out_h: int = 360,
    fps: float = 30.0,
    timeout: int = 180,
) -> Path:
    """
    CE-04 smoke: render multiple scenes with concat (hard cuts).
    Uses render_graph + ffmpeg. Sources are read-only.
    """
    from .render_graph import build_render_graph, graph_to_ffmpeg_cmd
    from .validator import assert_valid

    # Soft validation: sources must exist for actual render
    assert_valid(plan, require_existing_sources=True)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    graph = build_render_graph(
        plan,
        settings,
        out_w=out_w,
        out_h=out_h,
        fps=fps,
        apply_timeline_rhythm=True,
    )
    cmd = graph_to_ffmpeg_cmd(graph, str(out))
    _run_ffmpeg(cmd, timeout=timeout)
    if not out.is_file() or out.stat().st_size < 100:
        raise RuntimeError(f"Multiscene preview not written: {out}")
    return out
