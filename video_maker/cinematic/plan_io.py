"""Serialize/deserialize EditPlan for debug and cache sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .edit_plan import CropRect, EditPlan, MotionKeyframe, Scene


def scene_to_dict(sc: Scene) -> dict[str, Any]:
    return {
        "scene_id": sc.scene_id,
        "asset_id": sc.asset_id,
        "source_path": sc.source_path,
        "source_in": sc.source_in,
        "source_out": sc.source_out,
        "timeline_start": sc.timeline_start,
        "timeline_end": sc.timeline_end,
        "role": sc.role,
        "transcript_ref": sc.transcript_ref,
        "output_format": sc.output_format,
        "crop": {
            "x": sc.crop.x, "y": sc.crop.y,
            "w": sc.crop.w, "h": sc.crop.h,
        },
        "motion": [
            {"t": m.t, "scale": m.scale, "pan_x": m.pan_x, "pan_y": m.pan_y, "easing": m.easing}
            for m in (sc.motion or [])
        ],
        "color_profile": sc.color_profile,
        "transition_in": sc.transition_in,
        "transition_out": sc.transition_out,
        "reason": sc.reason,
        "confidence": sc.confidence,
    }


def plan_to_dict(plan: EditPlan) -> dict[str, Any]:
    return {
        "scenes": [scene_to_dict(s) for s in plan.scenes],
        "total_duration": plan.total_duration,
        "target_duration": plan.target_duration,
        "seed": getattr(plan, "seed", None),
        "notes": getattr(plan, "notes", None) or getattr(plan, "warnings", None),
    }


def write_plan_json(plan: EditPlan, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _crop(d: dict | None) -> CropRect:
    if not d:
        return CropRect()
    return CropRect(
        x=float(d.get("x", 0)), y=float(d.get("y", 0)),
        w=float(d.get("w", 1)), h=float(d.get("h", 1)),
    )


def _motion(items: list | None) -> list[MotionKeyframe]:
    out: list[MotionKeyframe] = []
    for m in items or []:
        out.append(MotionKeyframe(
            t=float(m.get("t", 0)),
            scale=float(m.get("scale", 1)),
            pan_x=float(m.get("pan_x", 0.5)),
            pan_y=float(m.get("pan_y", 0.5)),
            easing=str(m.get("easing", "linear")),
        ))
    return out


def plan_from_dict(data: dict[str, Any]) -> EditPlan:
    scenes: list[Scene] = []
    for s in data.get("scenes") or []:
        scenes.append(Scene(
            scene_id=str(s.get("scene_id") or f"s{len(scenes)}"),
            asset_id=str(s.get("asset_id") or ""),
            source_path=str(s.get("source_path") or ""),
            source_in=float(s.get("source_in") or 0),
            source_out=float(s.get("source_out") or 0),
            timeline_start=float(s.get("timeline_start") or 0),
            timeline_end=float(s.get("timeline_end") or 0),
            role=s.get("role") or "body",  # type: ignore
            transcript_ref=s.get("transcript_ref") or "",
            output_format=s.get("output_format") or "16:9",
            crop=_crop(s.get("crop")),
            motion=_motion(s.get("motion")),
            color_profile=str(s.get("color_profile") or "cinematic_documentary"),
            transition_in=str(s.get("transition_in") or "hard"),
            transition_out=str(s.get("transition_out") or "hard"),
            reason=str(s.get("reason") or ""),
            confidence=float(s.get("confidence") or 1.0),
        ))
    return EditPlan(
        scenes=scenes,
        total_duration=float(data.get("total_duration") or 0),
        target_duration=float(data.get("target_duration") or 0) or None,
    )


def read_plan_json(path: str | Path) -> EditPlan:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return plan_from_dict(data)
