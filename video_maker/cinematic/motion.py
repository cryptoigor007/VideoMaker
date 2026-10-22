"""Deterministic zoom/pan expressions for FFmpeg zoompan filter.

Supports multi-keyframe scale + pan_x/pan_y with easing.
Normalized time is always clamped to [0,1]; scale never exceeds max_zoom.
"""

from __future__ import annotations

from typing import Sequence

from .edit_plan import MotionKeyframe


def _ease(t: float, kind: str) -> float:
    """Cinematic easing. Default: smootherstep (Ken Burns feel)."""
    t = max(0.0, min(1.0, t))
    if kind == "linear":
        return t
    if kind == "ease_in":
        return t * t * t
    if kind == "ease_out":
        u = 1.0 - t
        return 1.0 - u * u * u
    # ease_in_out / smootherstep — zero accel at endpoints, very filmic
    # Perlin smootherstep: 6t^5 - 15t^4 + 10t^3
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _interp(kfs: Sequence[MotionKeyframe], t: float) -> tuple[float, float, float]:
    """Interpolate scale, pan_x, pan_y at relative time t in [0,1]."""
    if not kfs:
        return 1.0, 0.0, 0.0
    ordered = sorted(kfs, key=lambda k: k.t)
    t = max(0.0, min(1.0, t))
    if t <= ordered[0].t:
        k = ordered[0]
        return k.scale, k.pan_x, k.pan_y
    if t >= ordered[-1].t:
        k = ordered[-1]
        return k.scale, k.pan_x, k.pan_y
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.t <= t <= b.t:
            span = max(1e-9, b.t - a.t)
            local = (t - a.t) / span
            e = _ease(local, b.easing or a.easing or "ease_in_out")
            scale = a.scale + (b.scale - a.scale) * e
            pan_x = a.pan_x + (b.pan_x - a.pan_x) * e
            pan_y = a.pan_y + (b.pan_y - a.pan_y) * e
            return scale, pan_x, pan_y
    k = ordered[-1]
    return k.scale, k.pan_x, k.pan_y


def clamp_scale(scale: float, max_zoom: float = 1.25) -> float:
    return max(1.0, min(float(max_zoom), float(scale)))


def build_zoompan_expr(
    motion: Sequence[MotionKeyframe] | None,
    *,
    duration_sec: float,
    fps: int = 30,
    out_w: int = 1920,
    out_h: int = 1080,
    max_zoom: float = 1.25,
    crop_x: float = 0.0,
    crop_y: float = 0.0,
    crop_w: float = 1.0,
    crop_h: float = 1.0,
) -> str:
    """
    Build filter chain: crop (source ROI) → zoompan with interpolated z/x/y.

    pan_x/pan_y are normalized offsets in [-1,1] relative to available
    headroom after zoom. Time is driven by on/frames so it never runs past
    the planned scene duration even if the source file is longer.
    """
    frames = max(1, int(round(duration_sec * fps)))
    # Source crop in pixels relative to input (iw/ih)
    # Use fractions for FFmpeg expressions
    cx = max(0.0, min(1.0, crop_x))
    cy = max(0.0, min(1.0, crop_y))
    cw = max(0.05, min(1.0 - cx, crop_w))
    ch = max(0.05, min(1.0 - cy, crop_h))

    crop = (
        f"crop=w=iw*{cw:.6f}:h=ih*{ch:.6f}:"
        f"x=iw*{cx:.6f}:y=ih*{cy:.6f}"
    )

    if not motion or all(abs(k.scale - 1.0) < 1e-6 and abs(k.pan_x) < 1e-6 and abs(k.pan_y) < 1e-6 for k in motion):
        # Static: scale+pad without distortion.
        # [VM-FIX] fps обязателен и здесь: без него в xfade-цепочку попадают
        # клипы с разным fps (23.976/25/29.97) → ломаются таймстемпы → кадры
        # выбрасываются и видео обрывается (баг «замирания на 7 секунде»).
        return (
            f"{crop},"
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1"
        )

    ordered = sorted(motion, key=lambda k: k.t)
    z0 = clamp_scale(ordered[0].scale, max_zoom)
    z1 = clamp_scale(ordered[-1].scale, max_zoom)
    px0, py0 = ordered[0].pan_x, ordered[0].pan_y
    px1, py1 = ordered[-1].pan_x, ordered[-1].pan_y

    # Linear blend in normalized time on/frames (clamped by d=frames)
    # z = z0 + (z1-z0)*min(1, on/frames)
    z_expr = f"min({max_zoom:.4f},max(1,{z0:.6f}+({z1:.6f}-{z0:.6f})*min(1\\,on/{frames})))"

    # Available pan range after zoom: roughly (1 - 1/z) of frame
    # x = (iw - iw/zoom)/2 * (1 + pan_x)  — center + offset
    # FFmpeg zoompan: x/y are pixel offsets into input
    x_expr = (
        f"(iw-iw/zoom)/2*(1+({px0:.6f}+({px1:.6f}-{px0:.6f})*min(1\\,on/{frames})))"
    )
    y_expr = (
        f"(ih-ih/zoom)/2*(1+({py0:.6f}+({py1:.6f}-{py0:.6f})*min(1\\,on/{frames})))"
    )

    # Multi-keyframe: sample at ends only for expression simplicity;
    # full piecewise would need a long expr. For >2 kfs we approximate
    # with first→last which still applies pan.
    if len(ordered) > 2:
        # Use midpoint keyframe to refine if present
        mid = ordered[len(ordered) // 2]
        z_mid = clamp_scale(mid.scale, max_zoom)
        # piecewise linear via if(lt(on, mid_frame), ...)
        mid_f = max(1, int(mid.t * frames))
        z_expr = (
            f"min({max_zoom:.4f},max(1,"
            f"if(lt(on\\,{mid_f}),"
            f"{z0:.6f}+({z_mid:.6f}-{z0:.6f})*on/{mid_f},"
            f"{z_mid:.6f}+({z1:.6f}-{z_mid:.6f})*(on-{mid_f})/{max(1, frames - mid_f)}"
            f")))"
        )
        x_expr = (
            f"(iw-iw/zoom)/2*(1+"
            f"if(lt(on\\,{mid_f}),"
            f"{px0:.6f}+({mid.pan_x:.6f}-{px0:.6f})*on/{mid_f},"
            f"{mid.pan_x:.6f}+({px1:.6f}-{mid.pan_x:.6f})*(on-{mid_f})/{max(1, frames - mid_f)}"
            f"))"
        )
        y_expr = (
            f"(ih-ih/zoom)/2*(1+"
            f"if(lt(on\\,{mid_f}),"
            f"{py0:.6f}+({mid.pan_y:.6f}-{py0:.6f})*on/{mid_f},"
            f"{mid.pan_y:.6f}+({py1:.6f}-{mid.pan_y:.6f})*(on-{mid_f})/{max(1, frames - mid_f)}"
            f"))"
        )

    zp = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d=1:s={out_w}x{out_h}:fps={fps}"
    )
    # [VM-FIX] fps ДО zoompan: zoompan не пересэмплирует, а лишь переклеивает
    # интервалы — без предварительного fps=30 клипы 24/25fps сжимаются по
    # времени (5с → 4с), таймлайн рвётся, энкодер выбрасывает кадры
    # («видео замирает, звук идёт»). Проверено: fps до zoompan → тайминг верный.
    return f"{crop},fps={fps},{zp},setsar=1"


def enhance_input_filter(
    motion: Sequence[MotionKeyframe] | None,
    eq_filter: str | None,
    *,
    duration_sec: float = 2.0,
    fps: int = 30,
    out_w: int = 1920,
    out_h: int = 1080,
    width: int | None = None,
    height: int | None = None,
    max_zoom: float = 1.25,
    crop_x: float = 0.0,
    crop_y: float = 0.0,
    crop_w: float = 1.0,
    crop_h: float = 1.0,
    **_kwargs,
) -> str:
    """Full per-input video filter: motion + optional color eq + format normalize."""
    if width is not None:
        out_w = width
    if height is not None:
        out_h = height
    parts = [
        build_zoompan_expr(
            motion,
            duration_sec=duration_sec,
            fps=fps,
            out_w=out_w,
            out_h=out_h,
            max_zoom=max_zoom,
            crop_x=crop_x,
            crop_y=crop_y,
            crop_w=crop_w,
            crop_h=crop_h,
        )
    ]
    if eq_filter:
        parts.append(eq_filter)
    # fps/format applied once in render_graph _scene_chain to avoid duplicates
    return ",".join(parts)
