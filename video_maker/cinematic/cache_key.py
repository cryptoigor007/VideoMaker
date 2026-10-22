"""Cache key construction for CE results.

Changing only visual profile must NOT invalidate transcription / Gemini / voice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import CinematicSettings
from .edit_plan import EditPlan


def _stable_hash(data: Any) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def visual_cache_key(
    *,
    source_hashes: list[str],
    settings: CinematicSettings,
    plan: EditPlan | None,
    output_format: str,
    width: int,
    height: int,
    fps: float,
) -> str:
    """
    Key for the visual layer only.
    Does not include transcript or Gemini results — those stay in existing caches.
    """
    material = {
        "ce_algo": settings.algorithm_version,
        "sources": sorted(source_hashes),
        "profile": settings.profile,
        "motion": settings.motion_intensity,
        "tempo": settings.plan_tempo,
        "color_i": settings.color_intensity,
        "transitions": settings.allowed_transitions,
        "framing": settings.framing_mode,
        "max_zoom": settings.max_zoom,
        "seed": settings.seed,
        "preview_range": settings.preview_range_sec,
        "reuse_subs": settings.reuse_existing_subs,
        "format": output_format,
        "w": width,
        "h": height,
        "fps": fps,
        "plan_fp": plan.fingerprint() if plan else "",
    }
    return _stable_hash(material)[:32]


def should_invalidate_transcript_cache(old: CinematicSettings, new: CinematicSettings) -> bool:
    """Visual-only changes never invalidate transcript/Gemini."""
    return False  # intentional: CE does not touch audio analysis
