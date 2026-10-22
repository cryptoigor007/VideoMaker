"""Safe cinematic transitions. Prefer short dissolves (0.3–0.55s) — pro montage range."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Extended kinds map to FFmpeg xfade names that look cinematic, not gimmicky
TransitionType = Literal[
    "hard",
    "dissolve",
    "fade_black",
    "fade",
    "smoothleft",
    "smoothright",
    "fadeslow",
]


@dataclass
class TransitionSpec:
    kind: TransitionType
    duration: float  # seconds; 0 for hard


MAX_TRANSITION_SEC = 0.55  # research: >1s reads as slideshow


def choose_transition(
    allowed: str,
    role_or_intensity: str | float = "body",
    next_role: str | None = None,
    intensity: float | None = None,
    role_a: str | None = None,
    role_b: str | None = None,
    scene_index: int = 0,
    **_kwargs,
) -> TransitionSpec:
    """
    hard_only → cut
    soft_dissolve → fade / dissolve (alternating)
    fade_to_black → fadeblack on chapter roles
    all_safe → dissolve / smoothleft / fadeslow variety
    """
    if isinstance(role_or_intensity, (int, float)) and intensity is None:
        intensity = float(role_or_intensity)
        ra = role_a or "body"
        rb = role_b or next_role
    else:
        ra = role_a or (role_or_intensity if isinstance(role_or_intensity, str) else "body")
        rb = role_b if role_b is not None else next_role
        if intensity is None:
            intensity = 0.5

    if allowed == "hard_only" or intensity < 0.15:
        return TransitionSpec("hard", 0.0)

    # [VM-SB] «Современный микс»: так монтируют сейчас — в основном жёсткие
    # склейки, изредка короткий smooth-слайд (как whip-pan) или мягкое
    # растворение. Детерминировано (без случайности).
    if allowed == "modern_mix":
        roll = (scene_index * 31 + 7) % 10
        if roll < 8:
            return TransitionSpec("hard", 0.0)
        if roll == 8:
            kind: TransitionType = "smoothleft" if scene_index % 2 else "smoothright"
            return TransitionSpec(kind, min(0.26, MAX_TRANSITION_SEC))
        return TransitionSpec("dissolve", min(0.30, MAX_TRANSITION_SEC))

    # duration: 0.28–0.55s — slightly longer on emphasis/outro, shorter on body montage
    base = 0.28 + 0.22 * max(0.0, min(1.0, float(intensity)))
    role_boost = 0.0
    if ra in ("intro", "hook"):
        role_boost = 0.04
    elif ra in ("emphasis", "outro", "cta"):
        role_boost = 0.08
    # micro-variation so consecutive dissolves don't feel metronomic
    micro = ((scene_index * 17) % 7) * 0.012
    dur = min(MAX_TRANSITION_SEC, base + role_boost + micro)

    if allowed == "fade_to_black" or ra in ("outro", "cta"):
        return TransitionSpec("fade_black", min(MAX_TRANSITION_SEC, dur + 0.05))

    if allowed == "soft_dissolve":
        # fade ↔ dissolve; every 5th is slightly longer fade (breath)
        if scene_index % 5 == 4:
            return TransitionSpec("fade", min(MAX_TRANSITION_SEC, dur + 0.06))
        return TransitionSpec("dissolve" if scene_index % 2 else "fade", dur)

    if allowed == "all_safe":
        palette: list[TransitionType] = ["fade", "dissolve", "smoothleft", "fadeslow", "smoothright"]
        kind = palette[scene_index % len(palette)]
        if ra in ("outro", "cta") or (rb and rb in ("outro", "cta")):
            kind = "fade_black"
        if kind in ("smoothleft", "smoothright"):
            dur = min(dur, 0.42)  # directional wipes feel heavy if long
        return TransitionSpec(kind, dur)

    return TransitionSpec("hard", 0.0)


def xfade_filter(kind: TransitionType, duration: float, offset: float) -> str:
    if kind == "hard" or duration <= 0:
        return ""
    # Map our kinds → FFmpeg xfade transition names
    name = {
        "dissolve": "dissolve",
        "fade": "fade",
        "fade_black": "fadeblack",
        "smoothleft": "smoothleft",
        "smoothright": "smoothright",
        "fadeslow": "fadeslow",
    }.get(kind, "fade")
    return f"xfade=transition={name}:duration={duration:.3f}:offset={offset:.3f}"
