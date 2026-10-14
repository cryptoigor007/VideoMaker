"""Shared timeout and atomic-output helpers for external media processes.

Long encodes receive a duration-aware deadline. Probe and one-frame operations
should use explicit fixed deadlines at their call sites.
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger(__name__)
LogFn = Callable[[str], None] | None

# A 6-hour upper bound avoids an unbounded wait on pathological media, while
# leaving ample room for lengthy/high-resolution projects on slower machines.
MAX_MEDIA_TIMEOUT_SECONDS = 6 * 60 * 60


def media_timeout_for_duration(
    duration_seconds: float | int | None,
    *,
    minimum_seconds: float = 120.0,
    multiplier: float = 8.0,
    maximum_seconds: float = MAX_MEDIA_TIMEOUT_SECONDS,
) -> float:
    """Return a finite timeout scaled to media duration.

    Invalid or missing durations use the conservative minimum. Non-finite and
    negative values are never allowed to create an unbounded timeout.
    """
    try:
        duration = float(duration_seconds) if duration_seconds is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration < 0:
        duration = 0.0
    minimum = max(1.0, float(minimum_seconds))
    maximum = max(minimum, float(maximum_seconds))
    factor = max(1.0, float(multiplier))
    return min(maximum, max(minimum, duration * factor))


def run_ffmpeg_atomic(
    command: Sequence[str],
    output_path: str,
    *,
    timeout: float,
    log_fn: LogFn = None,
    require_nonempty: bool = True,
) -> str:
    """Run FFmpeg to a sibling temp file and atomically publish the output.

    The destination must be the final argument in the FFmpeg command, which
    ensures that container inference is preserved by the temporary suffix.
    Any failed/timeout attempt removes the partial temporary file and leaves a
    pre-existing destination untouched.
    """
    _log = log_fn or (lambda message: log.info(message))
    cmd = [str(item) for item in command]
    destination = os.path.abspath(output_path)
    if not cmd or os.path.abspath(cmd[-1]) != destination:
        raise ValueError("FFmpeg output_path must be the final command argument")
    parent = os.path.dirname(destination) or "."
    os.makedirs(parent, exist_ok=True)
    suffix = Path(destination).suffix or ".mp4"
    stem = Path(destination).stem
    temp_path = os.path.join(parent, f".{stem}.vm_tmp_{uuid.uuid4().hex[:12]}{suffix}")
    cmd[-1] = temp_path
    try:
        _log(f"[MEDIA] start deadline={float(timeout):.0f}s output={os.path.basename(destination)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=float(timeout), check=False)
        except subprocess.TimeoutExpired:
            _log(f"[MEDIA] TIMEOUT after {float(timeout):.0f}s; partial output will be removed")
            raise
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "")[-800:]
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout, stderr=tail
            )
        if not os.path.isfile(temp_path):
            raise RuntimeError(f"FFmpeg returned success but produced no output: {output_path}")
        if require_nonempty and os.path.getsize(temp_path) <= 0:
            raise RuntimeError(f"FFmpeg produced an empty output: {output_path}")
        os.replace(temp_path, destination)
        _log(f"[MEDIA] published {destination}")
        return destination
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
