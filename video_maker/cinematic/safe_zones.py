"""CE-06: safe zones for subtitles, hooks, CTA and faces.

Does NOT create a second subtitle system — only geometry helpers so that
existing ASS/hooks/CTA stay readable and do not collide with ROI / motion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .edit_plan import CropRect


Aspect = Literal["16:9", "9:16"]


@dataclass(frozen=True)
class SafeZone:
    """Normalized rect [0..1] that is safe for text overlays."""
    x: float
    y: float
    w: float
    h: float
    region: str  # "bottom" | "top" | "center" | "lower_third"


# Platform-ish margins (fraction of frame)
MARGIN_16_9 = 0.05
MARGIN_9_16 = 0.08  # vertical UI chrome is thicker


def subtitle_safe_zone(aspect: Aspect, *, prefer: str = "bottom") -> SafeZone:
    """
    Primary subtitle band.
    16:9 → lower third with side margins.
    9:16 → lower band above typical platform UI (~15% from bottom).
    """
    if aspect == "9:16":
        # Keep clear of TikTok/Reels bottom UI and top status
        if prefer == "top":
            return SafeZone(0.06, 0.12, 0.88, 0.12, "top")
        return SafeZone(0.06, 0.72, 0.88, 0.14, "bottom")
    # 16:9
    if prefer == "top":
        return SafeZone(MARGIN_16_9, 0.06, 1.0 - 2 * MARGIN_16_9, 0.12, "top")
    return SafeZone(MARGIN_16_9, 0.78, 1.0 - 2 * MARGIN_16_9, 0.14, "bottom")


def hook_cta_zone(aspect: Aspect) -> SafeZone:
    """Hook/CTA usually sits above subtitles or at top for Shorts."""
    if aspect == "9:16":
        return SafeZone(0.08, 0.08, 0.84, 0.10, "top")
    return SafeZone(0.15, 0.08, 0.70, 0.10, "top")


def intersects(a: SafeZone | CropRect, b: SafeZone | CropRect) -> bool:
    ax2 = a.x + a.w
    ay2 = a.y + a.h
    bx2 = b.x + b.w
    by2 = b.y + b.h
    return not (ax2 <= b.x or bx2 <= a.x or ay2 <= b.y or by2 <= a.y)


def text_collides_with_roi(
    text_zone: SafeZone,
    roi: CropRect,
    *,
    motion_scale: float = 1.0,
) -> bool:
    """
    True if after zoom the effective ROI is likely to cover the text band.
    Conservative: inflate ROI by scale.
    """
    if motion_scale <= 1.01:
        return intersects(text_zone, roi)
    # Inflate ROI toward center
    cx = roi.x + roi.w / 2
    cy = roi.y + roi.h / 2
    nw = min(1.0, roi.w * motion_scale)
    nh = min(1.0, roi.h * motion_scale)
    inflated = CropRect(
        x=max(0.0, cx - nw / 2),
        y=max(0.0, cy - nh / 2),
        w=nw,
        h=nh,
    )
    return intersects(text_zone, inflated)


def recommend_subtitle_placement(
    aspect: Aspect,
    roi: CropRect | None = None,
    max_scale: float = 1.0,
) -> SafeZone:
    """
    Prefer bottom; if ROI + motion would cover it, fall back to top.
    """
    bottom = subtitle_safe_zone(aspect, prefer="bottom")
    if roi is None:
        return bottom
    if text_collides_with_roi(bottom, roi, motion_scale=max_scale):
        return subtitle_safe_zone(aspect, prefer="top")
    return bottom


def ass_margin_v(aspect: Aspect, zone: SafeZone, frame_h: int) -> int:
    """Rough ASS MarginV in pixels from bottom (libass convention)."""
    # Distance from bottom edge to top of zone, in pixels
    from_bottom = (1.0 - (zone.y + zone.h)) * frame_h
    return max(20, int(from_bottom))


def overlay_report(
    aspect: Aspect,
    roi: CropRect | None = None,
    max_scale: float = 1.0,
    *,
    frame_w: int | None = None,
    frame_h: int | None = None,
) -> dict:
    """Machine-readable placement advice for host subtitle engine."""
    sub = recommend_subtitle_placement(aspect, roi, max_scale)
    hook = hook_cta_zone(aspect)
    fw = frame_w or (1080 if aspect == "9:16" else 1920)
    fh = frame_h or (1920 if aspect == "9:16" else 1080)
    return {
        "aspect": aspect,
        "subtitle": {"x": sub.x, "y": sub.y, "w": sub.w, "h": sub.h, "region": sub.region},
        "hook_cta": {"x": hook.x, "y": hook.y, "w": hook.w, "h": hook.h, "region": hook.region},
        "roi_collision_bottom": bool(
            roi and text_collides_with_roi(subtitle_safe_zone(aspect, prefer="bottom"), roi, motion_scale=max_scale)
        ),
        "ass": ass_style_hints(aspect, frame_w=fw, frame_h=fh, roi=roi, max_scale=max_scale),
        "reuse_existing_subs": True,
        "note": "Do not burn a second subtitle track; adjust MarginV/alignment only.",
    }


def ass_style_hints(
    aspect: Aspect,
    *,
    frame_w: int = 1920,
    frame_h: int = 1080,
    roi: CropRect | None = None,
    max_scale: float = 1.0,
) -> dict:
    """
    Concrete parameters for an existing ASS writer / subtitle stage.

    Returns MarginV, Alignment, and optional PlayRes — host should apply
    these to the *existing* style, not create a second subtitle track.
    """
    zone = recommend_subtitle_placement(aspect, roi=roi, max_scale=max_scale)
    margin_v = ass_margin_v(aspect, zone, frame_h)
    # libass Alignment: 2 = bottom-center, 8 = top-center
    alignment = 8 if zone.region == "top" else 2
    margin_l = int(zone.x * frame_w)
    margin_r = int((1.0 - (zone.x + zone.w)) * frame_w)
    return {
        "PlayResX": frame_w,
        "PlayResY": frame_h,
        "Alignment": alignment,
        "MarginV": margin_v,
        "MarginL": max(10, margin_l),
        "MarginR": max(10, margin_r),
        "region": zone.region,
        "reuse_existing_subs": True,
        "apply_to": "existing_ASS_style_only",
    }
