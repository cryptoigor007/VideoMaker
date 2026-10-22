"""Validated settings for Cinematic Enhancement.

OFF by default. JSON-serializable. Invalid values are normalized, not silently
wiping the whole object (except on total parse failure).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Literal
import json


ProfileName = Literal[
    "psychological_dark",
    "cinematic_documentary",
    "dynamic_shorts",
    "clean_explainer",
    "custom",
]
MotionIntensity = Literal["off", "low", "medium", "high"]
TransitionSet = Literal["hard_only", "modern_mix", "soft_dissolve", "fade_to_black", "all_safe"]
FramingMode = Literal["center", "safe_roi", "manual"]

_PROFILES = set(ProfileName.__args__)  # type: ignore
_MOTIONS = set(MotionIntensity.__args__)  # type: ignore
_TRANS = set(TransitionSet.__args__)  # type: ignore
_FRAMES = set(FramingMode.__args__)  # type: ignore


def _finite(x: float, default: float) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return v


@dataclass
class CinematicSettings:
    enabled: bool = False
    profile: ProfileName = "cinematic_documentary"
    motion_intensity: MotionIntensity = "low"
    plan_tempo: float = 1.0
    color_intensity: float = 0.5
    allowed_transitions: TransitionSet = "hard_only"
    framing_mode: FramingMode = "center"
    reuse_existing_subs: bool = True
    max_zoom: float = 1.25
    # Kinetic typography: off | subtle | bold | title
    kinetic_typography: str = "off"
    # [VM-SB] Умный B-roll: подбор клипов по смыслу сцен (псевдо-нейролинк)
    smart_broll: bool = True
    # [VM-SB] Пословный монтаж: смена кадра под ключевые слова озвучки
    keyword_cuts: bool = False
    min_shot_sec: float = 2.0
    preview_range_sec: float = 15.0
    seed: int = 42
    # Soft whoosh/impact under dissolves (procedural lavfi; OFF by default)
    transition_sfx: bool = False
    version: str = "1.0"
    algorithm_version: str = "ce-0.1.3"

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.profile not in _PROFILES:
            errs.append(f"unknown profile: {self.profile}")
        if self.motion_intensity not in _MOTIONS:
            errs.append(f"unknown motion_intensity: {self.motion_intensity}")
        if self.allowed_transitions not in _TRANS:
            errs.append(f"unknown allowed_transitions: {self.allowed_transitions}")
        if self.framing_mode not in _FRAMES:
            errs.append(f"unknown framing_mode: {self.framing_mode}")
        if not 0.5 <= self.plan_tempo <= 2.0:
            errs.append(f"plan_tempo out of range: {self.plan_tempo}")
        if not 0.0 <= self.color_intensity <= 1.0:
            errs.append(f"color_intensity out of range: {self.color_intensity}")
        if not 1.0 <= self.max_zoom <= 2.0:
            errs.append(f"max_zoom out of range: {self.max_zoom}")
        if not math.isfinite(self.plan_tempo) or not math.isfinite(self.color_intensity):
            errs.append("non-finite numeric setting")
        return errs

    def normalize(self) -> "CinematicSettings":
        """Clamp/enum-fix in place; returns self."""
        if self.profile not in _PROFILES:
            self.profile = "cinematic_documentary"  # type: ignore
        if self.motion_intensity not in _MOTIONS:
            self.motion_intensity = "low"  # type: ignore
        if self.allowed_transitions not in _TRANS:
            self.allowed_transitions = "hard_only"  # type: ignore
        if self.framing_mode not in _FRAMES:
            self.framing_mode = "center"  # type: ignore
        self.plan_tempo = min(2.0, max(0.5, _finite(self.plan_tempo, 1.0)))
        self.color_intensity = min(1.0, max(0.0, _finite(self.color_intensity, 0.5)))
        self.max_zoom = min(2.0, max(1.0, _finite(self.max_zoom, 1.25)))
        self.smart_broll = bool(getattr(self, "smart_broll", True))
        self.keyword_cuts = bool(getattr(self, "keyword_cuts", False))
        try:
            self.min_shot_sec = min(6.0, max(1.2, float(getattr(self, "min_shot_sec", 2.0) or 2.0)))
        except (TypeError, ValueError):
            self.min_shot_sec = 2.0
        self.preview_range_sec = min(60.0, max(1.0, _finite(self.preview_range_sec, 15.0)))
        try:
            self.seed = int(self.seed)
        except (TypeError, ValueError):
            self.seed = 42
        self.enabled = bool(self.enabled)
        self.reuse_existing_subs = bool(self.reuse_existing_subs)
        self.transition_sfx = bool(getattr(self, 'transition_sfx', False))
        k = str(getattr(self, 'kinetic_typography', 'off') or 'off').lower()
        if k not in ('off', 'subtle', 'bold', 'title'):
            k = 'off'
        self.kinetic_typography = k
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CinematicSettings":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        filtered = {k: v for k, v in data.items() if k in known}
        try:
            obj = cls(**filtered)
        except TypeError:
            return cls()
        return obj.normalize()

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, s: str) -> "CinematicSettings":
        try:
            return cls.from_dict(json.loads(s))
        except Exception:
            return cls()


DEFAULT_CINEMATIC_SETTINGS = CinematicSettings()
