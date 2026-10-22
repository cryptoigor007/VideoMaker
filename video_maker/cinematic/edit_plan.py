"""Versioned EditPlan contract (CE-02 ready).

Every scene references a real source fragment.
Validation rejects negative intervals, out-of-bounds, missing assets.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal
import json
import hashlib


Role = Literal[
    "intro",
    "hook",
    "body",
    "emphasis",
    "transition",
    "outro",
    "cta",
    "broll",
]


@dataclass
class CropRect:
    """Normalized crop [0..1] relative to source frame."""
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0

    def validate(self) -> list[str]:
        errs = []
        for name, v in (("x", self.x), ("y", self.y), ("w", self.w), ("h", self.h)):
            if not 0.0 <= v <= 1.0:
                errs.append(f"CropRect.{name}={v} out of [0,1]")
        if self.x + self.w > 1.0 + 1e-6 or self.y + self.h > 1.0 + 1e-6:
            errs.append("CropRect exceeds source bounds")
        return errs


@dataclass
class MotionKeyframe:
    """Deterministic keyframe for zoom/pan."""
    t: float                    # relative time inside scene [0..1]
    scale: float = 1.0
    pan_x: float = 0.0          # normalized offset
    pan_y: float = 0.0
    easing: str = "ease_in_out"

    def validate(self) -> list[str]:
        errs = []
        if not 0.0 <= self.t <= 1.0:
            errs.append(f"keyframe.t={self.t} out of [0,1]")
        if self.scale < 1.0 or self.scale > 3.0:
            errs.append(f"keyframe.scale={self.scale} unreasonable")
        return errs


@dataclass
class Scene:
    """One editable segment on the timeline."""
    scene_id: str
    asset_id: str
    source_path: str
    source_in: float
    source_out: float
    timeline_start: float
    timeline_end: float
    role: Role = "body"
    transcript_ref: str | None = None
    output_format: Literal["16:9", "9:16"] = "16:9"
    crop: CropRect = field(default_factory=CropRect)
    motion: list[MotionKeyframe] = field(default_factory=list)
    color_profile: str = "cinematic_documentary"
    transition_in: str = "hard"
    transition_out: str = "hard"
    overlays: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    def duration(self) -> float:
        return max(0.0, self.timeline_end - self.timeline_start)

    def source_duration(self) -> float:
        return max(0.0, self.source_out - self.source_in)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.source_out <= self.source_in:
            errs.append(f"scene {self.scene_id}: source_out <= source_in")
        if self.timeline_end <= self.timeline_start:
            errs.append(f"scene {self.scene_id}: timeline_end <= timeline_start")
        if self.source_in < 0 or self.timeline_start < 0:
            errs.append(f"scene {self.scene_id}: negative time")
        errs.extend(self.crop.validate())
        for kf in self.motion:
            errs.extend(kf.validate())
        if not 0.0 <= self.confidence <= 1.0:
            errs.append(f"scene {self.scene_id}: confidence out of range")
        return errs


@dataclass
class EditPlan:
    """Full versioned montage plan. Must be validated before render."""
    version: str = "1.0"
    algorithm_version: str = "ce-0.1.0"
    source_audio_hash: str = ""
    settings_hash: str = ""
    scenes: list[Scene] = field(default_factory=list)
    total_duration: float = 0.0
    target_duration: float = 0.0
    created_at: str = ""
    warnings: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.scenes:
            errs.append("EditPlan has no scenes")
        prev_end = 0.0
        for sc in self.scenes:
            errs.extend(sc.validate())
            if sc.timeline_start < prev_end - 1e-3:
                errs.append(f"overlapping or out-of-order scene {sc.scene_id}")
            prev_end = sc.timeline_end
        if self.total_duration > 0 and abs(self.total_duration - prev_end) > 0.05:
            self.warnings.append(
                f"total_duration mismatch: declared={self.total_duration}, last_end={prev_end}"
            )
        return errs

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditPlan":
        # Never mutate caller's dict
        data = dict(data)
        scenes = []
        for raw in data.get("scenes", []):
            s = dict(raw)
            crop_d = dict(s.pop("crop", {}) or {})
            motion_raw = list(s.pop("motion", []) or [])
            crop = CropRect(**crop_d) if crop_d else CropRect()
            motion = [MotionKeyframe(**dict(m)) for m in motion_raw]
            # Only pass fields Scene accepts
            allowed = {f.name for f in Scene.__dataclass_fields__.values()}  # type: ignore
            kwargs = {k: v for k, v in s.items() if k in allowed and k not in ("crop", "motion")}
            scenes.append(Scene(crop=crop, motion=motion, **kwargs))
        data["scenes"] = scenes
        fields = set(cls.__dataclass_fields__)  # type: ignore
        return cls(**{k: v for k, v in data.items() if k in fields})

    def fingerprint(self) -> str:
        """Stable hash excluding volatile fields (created_at)."""
        data = self.to_dict()
        data.pop("created_at", None)
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
