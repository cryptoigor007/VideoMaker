"""Strict EditPlan validation before any render.

Rejects negative intervals, missing files, unknown profiles/roles/formats,
empty scene_ids, non-finite floats, overlapping timeline ranges, and
scale that would exceed hard limits.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

from .edit_plan import EditPlan, Scene, Role
from .profiles import PROFILES

_ROLES = set(Role.__args__)  # type: ignore
_FORMATS = {"16:9", "9:16"}
_TRANSITIONS = {"hard", "dissolve", "fade", "fade_black", "soft_dissolve", "fade_to_black"}
_EASINGS = {"linear", "ease_in", "ease_out", "ease_in_out"}


class PlanValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _finite_ok(v: float, label: str) -> str | None:
    try:
        if not math.isfinite(float(v)):
            return f"{label} is not finite"
    except (TypeError, ValueError):
        return f"{label} is not a number"
    return None


def validate_plan(
    plan: EditPlan,
    *,
    require_existing_sources: bool = False,
    known_asset_ids: Sequence[str] | None = None,
    max_total_duration: float = 4 * 3600,
) -> list[str]:
    """Return list of errors. Empty list = plan is safe to render."""
    errs: list[str] = []

    if not plan.scenes:
        errs.append("EditPlan has zero scenes")
        return errs

    if e := _finite_ok(plan.total_duration, "total_duration"):
        errs.append(e)
    elif plan.total_duration < 0 or plan.total_duration > max_total_duration:
        errs.append(f"total_duration={plan.total_duration} out of bounds")

    if plan.target_duration and (e := _finite_ok(plan.target_duration, "target_duration")):
        errs.append(e)

    # Timeline should start near 0
    first = plan.scenes[0]
    if first.timeline_start > 0.05:
        errs.append(f"timeline does not start at 0 (first start={first.timeline_start})")

    prev_end = -1.0
    seen_ids: set[str] = set()

    for i, sc in enumerate(plan.scenes):
        prefix = f"scene[{i}] id={sc.scene_id!r}"

        if not sc.scene_id or not str(sc.scene_id).strip():
            errs.append(f"scene[{i}]: empty scene_id")
        if sc.scene_id in seen_ids:
            errs.append(f"{prefix}: duplicate scene_id")
        seen_ids.add(sc.scene_id)

        for label, val in (
            ("source_in", sc.source_in),
            ("source_out", sc.source_out),
            ("timeline_start", sc.timeline_start),
            ("timeline_end", sc.timeline_end),
            ("confidence", sc.confidence),
        ):
            if e := _finite_ok(val, f"{prefix}.{label}"):
                errs.append(e)

        if sc.source_out <= sc.source_in:
            errs.append(f"{prefix}: source_out <= source_in ({sc.source_in}..{sc.source_out})")
        if sc.timeline_end <= sc.timeline_start:
            errs.append(f"{prefix}: timeline_end <= timeline_start")
        if sc.source_in < 0 or sc.timeline_start < 0:
            errs.append(f"{prefix}: negative timestamp")
        if sc.timeline_start + 1e-4 < prev_end:
            errs.append(f"{prefix}: overlaps previous scene (prev_end={prev_end})")
        prev_end = sc.timeline_end

        if sc.role not in _ROLES:
            errs.append(f"{prefix}: unknown role '{sc.role}'")
        if sc.output_format not in _FORMATS:
            errs.append(f"{prefix}: unknown output_format '{sc.output_format}'")
        if sc.transition_in not in _TRANSITIONS and sc.transition_in:
            errs.append(f"{prefix}: unknown transition_in '{sc.transition_in}'")
        if sc.transition_out not in _TRANSITIONS and sc.transition_out:
            errs.append(f"{prefix}: unknown transition_out '{sc.transition_out}'")

        errs.extend(f"{prefix}: {e}" for e in sc.crop.validate())
        for j, kf in enumerate(sc.motion):
            errs.extend(f"{prefix}.motion[{j}]: {e}" for e in kf.validate())
            if kf.easing and kf.easing not in _EASINGS:
                errs.append(f"{prefix}.motion[{j}]: unknown easing '{kf.easing}'")

        if sc.color_profile not in PROFILES:
            errs.append(f"{prefix}: unknown color_profile '{sc.color_profile}'")

        if known_asset_ids is not None and sc.asset_id not in known_asset_ids:
            errs.append(f"{prefix}: unknown asset_id '{sc.asset_id}'")

        if require_existing_sources:
            if not sc.source_path:
                errs.append(f"{prefix}: empty source_path")
            elif not Path(sc.source_path).is_file():
                errs.append(f"{prefix}: source file missing: {sc.source_path}")

        max_scale = max((kf.scale for kf in sc.motion), default=1.0)
        if max_scale > 2.5:
            errs.append(f"{prefix}: scale {max_scale} exceeds hard limit 2.5")

    # Optional consistency: declared total vs last end
    if plan.total_duration > 0 and abs(plan.total_duration - prev_end) > 0.15:
        # advisory only — collected as warning-style error prefix so callers can filter
        errs.append(
            f"WARN total_duration mismatch: declared={plan.total_duration}, last_end={prev_end}"
        )

    return errs


def assert_valid(plan: EditPlan, **kwargs) -> None:
    errs = [e for e in validate_plan(plan, **kwargs) if not e.startswith("WARN ")]
    if errs:
        raise PlanValidationError(errs)
