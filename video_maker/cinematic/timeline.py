"""Rhythm-aware timeline with correct overlap and cumulative xfade offsets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import CinematicSettings
from .edit_plan import EditPlan, Scene
from .transitions import TransitionSpec, choose_transition


@dataclass
class PlacedTransition:
    between: tuple[str, str]
    spec: TransitionSpec
    timeline_offset: float


@dataclass
class TimelineResult:
    scenes: list[Scene]
    transitions: list[PlacedTransition] = field(default_factory=list)
    total_duration: float = 0.0
    warnings: list[str] = field(default_factory=list)


def apply_rhythm(
    scenes: Sequence[Scene],
    settings: CinematicSettings,
    *,
    target_duration: float | None = None,
    fps: int = 30,
    **_kwargs,
) -> TimelineResult:
    if hasattr(scenes, "scenes"):
        # EditPlan passed by mistake
        scenes = scenes.scenes  # type: ignore
    if not scenes:
        return TimelineResult(scenes=[], total_duration=0.0)

    tempo = max(0.5, min(2.0, float(settings.plan_tempo)))
    trans_intensity = 0.0 if settings.allowed_transitions == "hard_only" else 0.6

    retimed: list[Scene] = []
    placed: list[PlacedTransition] = []
    warnings: list[str] = []

    # 1) Determine per-scene content duration (no overlap yet)
    durations: list[float] = []
    for src in scenes:
        raw = max(0.05, src.source_out - src.source_in)
        desired = raw / tempo if tempo > 1e-6 else raw
        avail = max(0.05, src.source_out - src.source_in)
        if desired > avail + 1e-6:
            desired = avail
            warnings.append(f"{src.scene_id}: tempo limited by source {avail:.2f}s")
        durations.append(desired)

    # 2) Estimate overlaps first so we can scale content to hit *output* target
    # after xfade (master must match audio duration within tolerance).
    def _overlap_for(i: int, durs: list[float]) -> float:
        if i <= 0:
            return 0.0
        spec = choose_transition(
            settings.allowed_transitions,
            intensity=trans_intensity,
            role_a=scenes[i - 1].role,
            role_b=scenes[i].role,
            scene_index=i,
        )
        if spec.kind == "hard" or spec.duration <= 0.01:
            return 0.0
        return min(spec.duration, durs[i] * 0.35, durs[i - 1] * 0.35)

    def _output_len(durs: list[float]) -> float:
        total = 0.0
        for i, d in enumerate(durs):
            total = total + d - (_overlap_for(i, durs) if i > 0 else 0.0)
        return total

    content_sum = sum(durations)
    avail = [
        max(0.05, scenes[i].source_out - scenes[i].source_in) for i in range(len(scenes))
    ]
    if target_duration and target_duration > 0 and content_sum > 1e-6:
        out_len = _output_len(durations)
        # Iteratively match post-xfade length to target.
        # May shrink freely; grow only up to source availability.
        if abs(out_len - target_duration) > 0.05:
            for _ in range(6):
                if out_len <= 1e-6:
                    break
                scale = target_duration / out_len
                new_durs = []
                for i, d in enumerate(durations):
                    nd = d * scale
                    if nd > avail[i]:
                        nd = avail[i]
                    new_durs.append(max(0.05, nd))
                durations = new_durs
                out_len = _output_len(durations)
                if abs(out_len - target_duration) <= 0.08:
                    break
            warnings.append(
                f"timeline scaled for post-xfade target {target_duration:.2f}s "
                f"(output≈{out_len:.2f}s)"
            )

    # 3) Place transitions and build cumulative output length
    cumulative = 0.0  # output length after processing scene i (post-overlap)

    for i, src in enumerate(scenes):
        d = durations[i]
        sc = Scene(
            scene_id=src.scene_id,
            asset_id=src.asset_id,
            source_path=src.source_path,
            source_in=src.source_in,
            source_out=src.source_in + d,
            timeline_start=0.0,  # filled below
            timeline_end=0.0,
            role=src.role,
            transcript_ref=src.transcript_ref,
            output_format=src.output_format,
            crop=src.crop,
            motion=list(src.motion),
            color_profile=src.color_profile,
            transition_in=src.transition_in,
            transition_out=src.transition_out,
            reason=src.reason,
            confidence=src.confidence,
        )

        if i == 0:
            sc.timeline_start = 0.0
            sc.timeline_end = d
            cumulative = d
        else:
            prev = retimed[-1]
            overlap = _overlap_for(i, durations)
            spec = choose_transition(
                settings.allowed_transitions,
                intensity=trans_intensity,
                role_a=prev.role,
                role_b=sc.role,
                scene_index=i,
            )
            offset = max(0.0, cumulative - overlap) if overlap > 0 else cumulative
            placed.append(
                PlacedTransition(
                    between=(prev.scene_id, sc.scene_id),
                    spec=TransitionSpec(kind=spec.kind if overlap > 0 else "hard", duration=overlap),
                    timeline_offset=offset,
                )
            )
            sc.transition_in = spec.kind if overlap > 0 else "hard"
            prev.transition_out = sc.transition_in
            sc.timeline_start = prev.timeline_end
            sc.timeline_end = sc.timeline_start + d
            cumulative = cumulative + d - overlap

        retimed.append(sc)

    return TimelineResult(
        scenes=retimed,
        transitions=placed,
        total_duration=cumulative,
        warnings=warnings,
    )


def apply_rhythm_to_plan(plan: EditPlan, settings: CinematicSettings, **kwargs) -> EditPlan:
    result = apply_rhythm(
        plan.scenes,
        settings,
        target_duration=plan.target_duration or plan.total_duration or None,
    )
    plan.scenes = result.scenes
    plan.total_duration = result.total_duration
    plan.target_duration = result.total_duration
    plan.warnings = list(plan.warnings) + list(result.warnings)
    return plan


def duration_error_frames(actual: float, target: float, fps: int = 30) -> int:
    return int(round(abs(actual - target) * fps))
