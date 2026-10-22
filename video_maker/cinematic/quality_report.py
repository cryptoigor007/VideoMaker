"""Technical quality report for CE renders (CE-08 helper)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StreamInfo:
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    nb_frames: int = 0


@dataclass
class QualityReport:
    path: str
    ok: bool
    size_bytes: int = 0
    video: StreamInfo = field(default_factory=StreamInfo)
    warnings: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _ffprobe_json(path: str | Path) -> dict:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:] or "ffprobe failed")
    return json.loads(r.stdout or "{}")


def probe_media(path: str | Path) -> QualityReport:
    p = Path(path)
    warnings: list[str] = []
    if not p.is_file():
        return QualityReport(path=str(p), ok=False, warnings=["file missing"])
    size = p.stat().st_size
    if size < 200:
        return QualityReport(path=str(p), ok=False, size_bytes=size, warnings=["file too small"])
    try:
        data = _ffprobe_json(p)
    except Exception as e:
        return QualityReport(path=str(p), ok=False, size_bytes=size, warnings=[str(e)])

    v = StreamInfo()
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            v.codec = str(s.get("codec_name") or "")
            v.width = int(s.get("width") or 0)
            v.height = int(s.get("height") or 0)
            fr = s.get("r_frame_rate") or s.get("avg_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                v.fps = float(num) / max(1.0, float(den))
            except Exception:
                v.fps = 0.0
            try:
                v.nb_frames = int(s.get("nb_frames") or 0)
            except Exception:
                v.nb_frames = 0
            break
    fmt = data.get("format") or {}
    try:
        v.duration = float(fmt.get("duration") or 0)
    except Exception:
        v.duration = 0.0

    if not v.codec:
        warnings.append("no video stream")
    if v.duration <= 0:
        warnings.append("duration unknown/zero")
    if v.width <= 0 or v.height <= 0:
        warnings.append("invalid resolution")

    return QualityReport(
        path=str(p),
        ok=len(warnings) == 0,
        size_bytes=size,
        video=v,
        warnings=warnings,
    )


def compare_off_on(
    path_off: str | Path,
    path_on: str | Path,
    *,
    target_duration: float | None = None,
    tolerance_sec: float = 0.15,
) -> dict[str, Any]:
    """Compare CE OFF vs ON technical metrics."""
    a = probe_media(path_off)
    b = probe_media(path_on)
    notes: list[str] = []
    if a.ok and b.ok:
        if a.video.width != b.video.width or a.video.height != b.video.height:
            notes.append(
                f"resolution differs: {a.video.width}x{a.video.height} vs "
                f"{b.video.width}x{b.video.height}"
            )
        dd = abs(a.video.duration - b.video.duration)
        if dd > tolerance_sec:
            notes.append(f"duration delta {dd:.3f}s > {tolerance_sec}")
        if target_duration is not None:
            if abs(b.video.duration - target_duration) > tolerance_sec:
                notes.append(
                    f"ON duration {b.video.duration:.3f} vs target {target_duration:.3f}"
                )
    return {
        "off": a.to_dict(),
        "on": b.to_dict(),
        "notes": notes,
        "both_ok": a.ok and b.ok,
    }


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def assert_duration_close(
    path: str | Path,
    expected: float,
    *,
    tol: float = 0.35,
) -> tuple[bool, float, str]:
    """Return (ok, actual_duration, message)."""
    rep = probe_media(path)
    actual = float(rep.video.duration or 0.0)
    if not rep.ok:
        return False, actual, "; ".join(rep.warnings) or "probe failed"
    if expected <= 0:
        return True, actual, "no expected duration"
    drift = abs(actual - expected)
    ok = drift <= tol
    msg = f"duration {actual:.2f}s vs expected {expected:.2f}s (drift {drift:.2f}s)"
    return ok, actual, msg


def cinematic_look_summary(settings_dict: dict) -> str:
    """One-line human summary for logs / quality side-car."""
    en = settings_dict.get("enabled", False)
    if not en:
        return "CE OFF"
    return (
        f"CE ON profile={settings_dict.get('profile')} "
        f"motion={settings_dict.get('motion_intensity')} "
        f"trans={settings_dict.get('allowed_transitions')} "
        f"color={settings_dict.get('color_intensity')} "
        f"zoom≤{settings_dict.get('max_zoom')}"
    )
