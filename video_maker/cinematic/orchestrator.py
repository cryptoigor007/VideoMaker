"""End-to-end CE orchestration for preview / integration demos.

Production VideoMaker should call the same steps from pipeline stages
instead of this helper; this exists to prove the full chain works.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CinematicSettings
from .edit_plan import EditPlan
from .pipeline_hooks import maybe_build_plan, resolve_settings
from .timeline import apply_rhythm_to_plan
from .render_graph import build_render_graph, graph_to_ffmpeg_cmd
from .safe_zones import overlay_report
from .staging import StagingDir, atomic_write_json, file_sha256
from .encode_contract import assert_ce_host_allowed
from .preview import _run_ffmpeg
from .validator import validate_plan

log = logging.getLogger(__name__)


@dataclass
class CEResult:
    enabled: bool
    plan: EditPlan | None
    output_path: Path | None
    overlay: dict | None
    source_hashes: dict[str, str]
    warnings: list[str]
    graph_filter: str | None = None


def run_cinematic_preview(
    *,
    project_settings: dict[str, Any],
    transcript_segments: list[dict] | None = None,
    broll_clips: list[dict] | None = None,
    audio_duration: float = 0.0,
    source_audio_hash: str = "",
    staging_parent: str | Path,
    job_id: str = "preview",
    out_w: int = 1280,
    out_h: int = 720,
    fps: float = 30.0,
    aspect: str = "16:9",
    encode_host: str = "MasterBuilder_H",
) -> CEResult:
    """
    Full CE chain for a preview render.
    If disabled → returns enabled=False and does nothing to sources.
    """
    settings = resolve_settings(project_settings)
    warnings: list[str] = []
    source_hashes: dict[str, str] = {}

    plan = maybe_build_plan(
        project_settings=project_settings,
        transcript_segments=transcript_segments,
        broll_clips=broll_clips,
        audio_duration=audio_duration,
        source_audio_hash=source_audio_hash,
        output_format=aspect,
    )
    if plan is None:
        return CEResult(
            enabled=False,
            plan=None,
            output_path=None,
            overlay=None,
            source_hashes={},
            warnings=["CE disabled — legacy path"],
        )

    assert_ce_host_allowed(encode_host)
    plan = apply_rhythm_to_plan(plan, settings, fps=fps)
    errs = validate_plan(plan, require_existing_sources=True)
    fatal = [e for e in errs if not e.startswith("WARN ")]
    advisory = [e for e in errs if e.startswith("WARN ")]
    warnings.extend(advisory)
    if fatal:
        warnings.extend(fatal)
        return CEResult(
            enabled=True,
            plan=plan,
            output_path=None,
            overlay=None,
            source_hashes={},
            warnings=warnings,
            graph_filter=None,
        )

    for sc in plan.scenes:
        p = Path(sc.source_path)
        if p.is_file():
            source_hashes[str(p)] = file_sha256(p)

    staging = StagingDir(staging_parent, job_id)
    plan_path = staging.path("edit_plan.json")
    atomic_write_json(plan_path, plan.to_dict())

    overlay = overlay_report(aspect if aspect in ("16:9", "9:16") else "16:9")  # type: ignore

    graph = build_render_graph(
        plan, settings, out_w=out_w, out_h=out_h, fps=fps, apply_timeline_rhythm=False
    )
    # rhythm already applied
    warnings.extend(graph.warnings)

    out = staging.path("ce_preview.mp4")
    cmd = graph_to_ffmpeg_cmd(graph, str(out))
    try:
        _run_ffmpeg(cmd, timeout=180)
    except Exception as e:
        warnings.append(str(e))
        staging.cleanup()
        return CEResult(
            enabled=True,
            plan=plan,
            output_path=None,
            overlay=overlay,
            source_hashes=source_hashes,
            warnings=warnings,
            graph_filter=graph.concat_or_xfade,
        )

    # verify sources unchanged
    from .staging import verify_sources_unchanged
    bad = verify_sources_unchanged([Path(p) for p in source_hashes], source_hashes)
    if bad:
        warnings.append(f"SOURCE MUTATION DETECTED: {bad}")

    return CEResult(
        enabled=True,
        plan=plan,
        output_path=out if out.is_file() else None,
        overlay=overlay,
        source_hashes=source_hashes,
        warnings=warnings,
        graph_filter=graph.concat_or_xfade,
    )
