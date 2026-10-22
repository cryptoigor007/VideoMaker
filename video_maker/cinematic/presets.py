"""User preset save/load for Cinematic settings.

Stored as JSON next to project settings; migration-safe defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import CinematicSettings, DEFAULT_CINEMATIC_SETTINGS
from .staging import atomic_write_json


PRESET_VERSION = 1


def save_preset(path: str | Path, settings: CinematicSettings, name: str = "default") -> Path:
    p = Path(path)
    payload = {
        "preset_version": PRESET_VERSION,
        "name": name,
        "cinematic": settings.to_dict(),
    }
    atomic_write_json(p, payload)
    return p


def load_preset(path: str | Path) -> CinematicSettings:
    p = Path(path)
    if not p.is_file():
        return DEFAULT_CINEMATIC_SETTINGS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        block = data.get("cinematic") or data
        return CinematicSettings.from_dict(block)
    except Exception:
        return DEFAULT_CINEMATIC_SETTINGS


def list_builtin_preset_names() -> list[str]:
    return [
        "psychological_dark",
        "cinematic_documentary",
        "dynamic_shorts",
        "clean_explainer",
        "custom",
        "mac_air_8gb",
        "mac_pro_16gb",
    ]


def builtin_preset(profile_name: str) -> CinematicSettings:
    s = CinematicSettings(enabled=True, profile=profile_name)  # type: ignore
    if profile_name == "dynamic_shorts":
        s.motion_intensity = "medium"
        s.allowed_transitions = "soft_dissolve"
        s.color_intensity = 0.7
    elif profile_name == "psychological_dark":
        s.motion_intensity = "low"
        s.color_intensity = 0.65
        s.allowed_transitions = "fade_to_black"
    elif profile_name == "clean_explainer":
        s.motion_intensity = "off"
        s.color_intensity = 0.3
        s.allowed_transitions = "hard_only"
    elif profile_name == "mac_air_8gb":
        return mac_air_8gb_preset()
    elif profile_name == "mac_pro_16gb":
        return mac_pro_16gb_preset()
    else:
        s.motion_intensity = "low"
        s.color_intensity = 0.5
        s.allowed_transitions = "hard_only"
    return s


def mac_air_8gb_preset() -> CinematicSettings:
    """Safe defaults for MacBook Air M1 8 GB — soft dissolve, light motion, moderate color."""
    return CinematicSettings(
        enabled=True,
        profile="cinematic_documentary",
        motion_intensity="low",
        plan_tempo=1.0,
        color_intensity=0.45,
        allowed_transitions="soft_dissolve",
        framing_mode="center",
        reuse_existing_subs=True,
        max_zoom=1.15,
        kinetic_typography="off",
        seed=42,
    ).normalize()


def mac_pro_16gb_preset() -> CinematicSettings:
    """More headroom: medium motion, richer color, all_safe transitions."""
    return CinematicSettings(
        enabled=True,
        profile="cinematic_documentary",
        motion_intensity="medium",
        plan_tempo=1.0,
        color_intensity=0.6,
        allowed_transitions="all_safe",
        framing_mode="center",
        reuse_existing_subs=True,
        max_zoom=1.22,
        kinetic_typography="subtle",
        seed=42,
    ).normalize()
