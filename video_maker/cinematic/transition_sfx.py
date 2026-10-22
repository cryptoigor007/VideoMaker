"""Optional soft transition SFX (whoosh / soft impact) mixed under dissolves.

Generated procedurally via FFmpeg lavfi — no external asset files required.
Disabled by default; enable via CinematicSettings.transition_sfx or GUI.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


@dataclass
class TransitionSfxSpec:
    kind: str  # whoosh | soft_impact | none
    duration: float
    gain_db: float = -18.0
    timeline_offset: float = 0.0  # when to place on master timeline


def sfx_for_transition(kind: str, duration: float, *, enabled: bool = False) -> TransitionSfxSpec:
    if not enabled or duration < 0.05:
        return TransitionSfxSpec("none", 0.0)
    k = (kind or "").lower()
    if k in ("dissolve", "fade", "soft_dissolve", "fadeslow", "smoothleft", "smoothright"):
        return TransitionSfxSpec("whoosh", min(0.45, max(0.15, duration)), gain_db=-20.0)
    if k in ("fade_black", "fadeblack"):
        return TransitionSfxSpec("soft_impact", min(0.35, max(0.12, duration * 0.8)), gain_db=-16.0)
    return TransitionSfxSpec("none", 0.0)


def lavfi_whoosh(duration: float, gain_db: float = -20.0) -> str:
    d = max(0.08, float(duration))
    return (
        f"anoisesrc=d={d:.3f}:c=pink:r=48000:a=0.4,"
        f"highpass=f=400,lowpass=f=6000,"
        f"afade=t=in:st=0:d={min(0.08, d * 0.25):.3f},"
        f"afade=t=out:st={max(0.0, d - min(0.12, d * 0.4)):.3f}:d={min(0.12, d * 0.4):.3f},"
        f"volume={gain_db}dB"
    )


def lavfi_soft_impact(duration: float, gain_db: float = -16.0) -> str:
    d = max(0.08, float(duration))
    return (
        f"sine=f=80:d={d:.3f},"
        f"afade=t=in:st=0:d=0.02,"
        f"afade=t=out:st={max(0.0, d * 0.15):.3f}:d={max(0.05, d * 0.85):.3f},"
        f"lowpass=f=300,volume={gain_db}dB"
    )


def build_sfx_filter_fragment(spec: TransitionSfxSpec, label: str = "sfx0") -> tuple[str, str]:
    if spec.kind == "none" or spec.duration <= 0:
        return "", ""
    if spec.kind == "soft_impact":
        expr = lavfi_soft_impact(spec.duration, spec.gain_db)
    else:
        expr = lavfi_whoosh(spec.duration, spec.gain_db)
    return expr, label


def collect_transition_sfx(
    transitions: Sequence,
    *,
    enabled: bool,
) -> list[TransitionSfxSpec]:
    """Build placed SFX list from TimelineResult.transitions (PlacedTransition-like)."""
    out: list[TransitionSfxSpec] = []
    if not enabled:
        return out
    for tr in transitions:
        spec = getattr(tr, "spec", None)
        if spec is None:
            continue
        kind = getattr(spec, "kind", "hard")
        dur = float(getattr(spec, "duration", 0) or 0)
        offset = float(getattr(tr, "timeline_offset", 0) or 0)
        s = sfx_for_transition(kind, dur, enabled=True)
        if s.kind == "none":
            continue
        s.timeline_offset = max(0.0, offset)
        out.append(s)
    return out


def render_transition_sfx_track(
    specs: Sequence[TransitionSfxSpec],
    *,
    total_duration: float,
    output_path: str | Path,
    sample_rate: int = 48000,
    timeout: int = 60,
) -> Path | None:
    """
    Render a mono WAV of length total_duration with whooshes at timeline offsets.
    Returns path or None if nothing to render / ffmpeg failed.
    """
    specs = [s for s in specs if s.kind != "none" and s.duration > 0]
    if not specs or total_duration <= 0.05:
        return None

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Build filter: anullsrc base + adelay each sfx + amix
    inputs: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono:d={total_duration:.3f}",
    ]
    filter_parts: list[str] = []
    mix_labels = ["[0:a]"]
    for i, s in enumerate(specs):
        if s.kind == "soft_impact":
            expr = lavfi_soft_impact(s.duration, s.gain_db)
        else:
            expr = lavfi_whoosh(s.duration, s.gain_db)
        inputs += ["-f", "lavfi", "-i", expr]
        delay_ms = int(round(s.timeline_offset * 1000))
        lab = f"s{i}"
        # adelay needs channel count; mono → one value
        filter_parts.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms},apad=whole_dur={total_duration:.3f}[{lab}]")
        mix_labels.append(f"[{lab}]")

    n = len(mix_labels)
    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={n}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    fc = ";".join(filter_parts)
    cmd = inputs + [
        "-filter_complex", fc,
        "-map", "[aout]",
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-t", f"{total_duration:.3f}",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not out.is_file() or out.stat().st_size < 100:
            log.warning("CE SFX render failed: %s", (r.stderr or "")[-400:])
            return None
        return out
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("CE SFX render error: %s", e)
        return None


def mix_sfx_under_narration(
    narration_path: str | Path,
    sfx_path: str | Path,
    output_path: str | Path,
    *,
    sfx_weight: float = 0.35,
    timeout: int = 60,
) -> Path | None:
    """amix narration + sfx (sfx quieter). Returns output or None."""
    narration_path, sfx_path, output_path = Path(narration_path), Path(sfx_path), Path(output_path)
    if not narration_path.is_file() or not sfx_path.is_file():
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w = max(0.05, min(1.0, float(sfx_weight)))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(narration_path),
        "-i", str(sfx_path),
        "-filter_complex",
        f"[1:a]volume={w:.3f}[s];[0:a][s]amix=inputs=2:duration=first:dropout_transition=0[aout]",
        "-map", "[aout]",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not output_path.is_file():
            log.warning("CE SFX mix failed: %s", (r.stderr or "")[-300:])
            return None
        return output_path
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("CE SFX mix error: %s", e)
        return None
