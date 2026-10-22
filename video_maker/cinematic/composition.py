"""16:9 and 9:16 composition helpers.

Shared semantic plan, separate geometry. Never stretch. Protect ROI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .edit_plan import CropRect, Scene


Aspect = Literal["16:9", "9:16"]


@dataclass(frozen=True)
class OutputSpec:
    aspect: Aspect
    width: int
    height: int
    fps: float = 30.0


OUTPUT_SPECS: dict[Aspect, OutputSpec] = {
    "16:9": OutputSpec("16:9", 1920, 1080),
    "9:16": OutputSpec("9:16", 1080, 1920),
}

# 4K masters used by VideoMaker production
OUTPUT_SPECS_4K: dict[Aspect, OutputSpec] = {
    "16:9": OutputSpec("16:9", 3840, 2160),
    "9:16": OutputSpec("9:16", 2160, 3840),
}


def target_size(aspect: Aspect, max_side: int | None = None) -> tuple[int, int]:
    spec = OUTPUT_SPECS[aspect]
    w, h = spec.width, spec.height
    if max_side:
        scale = max_side / max(w, h)
        w, h = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    return w, h


def target_size_4k(aspect: Aspect) -> tuple[int, int]:
    """Production master sizes used by VideoMaker."""
    spec = OUTPUT_SPECS_4K[aspect]
    return spec.width, spec.height


def fit_crop_for_aspect(
    source_w: int,
    source_h: int,
    target_aspect: Aspect,
    roi: CropRect | None = None,
) -> CropRect:
    """
    Compute a crop rectangle that fills target aspect without stretching.
    Prefer ROI center if provided and confident; else center of source.
    """
    if source_w <= 0 or source_h <= 0:
        return CropRect()

    src_ar = source_w / source_h
    tgt = OUTPUT_SPECS[target_aspect]
    tgt_ar = tgt.width / tgt.height

    if abs(src_ar - tgt_ar) < 0.02:
        # already matching — full frame or ROI
        return roi if roi and not roi.validate() else CropRect()

    if src_ar > tgt_ar:
        # source wider → crop sides
        new_w = tgt_ar / src_ar  # normalized width
        cx = 0.5
        if roi and not roi.validate():
            cx = roi.x + roi.w / 2
        x = max(0.0, min(1.0 - new_w, cx - new_w / 2))
        return CropRect(x=x, y=0.0, w=new_w, h=1.0)
    else:
        # source taller → crop top/bottom
        new_h = src_ar / tgt_ar
        cy = 0.5
        if roi and not roi.validate():
            cy = roi.y + roi.h / 2
        y = max(0.0, min(1.0 - new_h, cy - new_h / 2))
        return CropRect(x=0.0, y=y, w=1.0, h=new_h)


def adapt_scene_for_format(scene: Scene, target_aspect: Aspect) -> Scene:
    """
    Return a copy of scene with crop adjusted for target aspect.
    Does not mutate the original.
    """
    # Without real source resolution we keep existing crop and set format tag.
    # Real integration will probe source and call fit_crop_for_aspect.
    new_crop = scene.crop
    return Scene(
        scene_id=f"{scene.scene_id}_{target_aspect.replace(':', 'x')}",
        asset_id=scene.asset_id,
        source_path=scene.source_path,
        source_in=scene.source_in,
        source_out=scene.source_out,
        timeline_start=scene.timeline_start,
        timeline_end=scene.timeline_end,
        role=scene.role,
        transcript_ref=scene.transcript_ref,
        output_format=target_aspect,
        crop=new_crop,
        motion=list(scene.motion),
        color_profile=scene.color_profile,
        transition_in=scene.transition_in,
        transition_out=scene.transition_out,
        overlays=list(scene.overlays),
        reason=scene.reason + f" [adapted {target_aspect}]",
        confidence=scene.confidence,
        warnings=list(scene.warnings),
    )


def dual_format_plan_scenes(
    scenes: list[Scene],
) -> tuple[list[Scene], list[Scene]]:
    """Produce H and V scene lists sharing semantic timing."""
    h = [adapt_scene_for_format(s, "16:9") for s in scenes]
    v = [adapt_scene_for_format(s, "9:16") for s in scenes]
    return h, v
