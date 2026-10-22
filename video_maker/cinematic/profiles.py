"""Color and look profiles. Controlled intensity — cinematic, not Instagram-heavy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ColorProfile:
    name: str
    description: str
    contrast: float = 1.0
    brightness: float = 0.0
    saturation: float = 1.0
    gamma: float = 1.0
    # colorbalance shadows/mids/highlights (−1..1), scaled by intensity
    cb_rs: float = 0.0
    cb_gs: float = 0.0
    cb_bs: float = 0.0
    cb_rm: float = 0.0
    cb_bm: float = 0.0
    cb_rh: float = 0.0
    cb_bh: float = 0.0
    # mild S-curve strength 0..1 (applied via curves=all)
    curve_s: float = 0.0
    # vignette angle (PI/n); 0 = off
    vignette: float = 0.0


PROFILES: dict[str, ColorProfile] = {
    "psychological_dark": ColorProfile(
        name="psychological_dark",
        description="Тёмная документальная эстетика, холодные тени, мягкая виньетка",
        contrast=1.12,
        brightness=-0.04,
        saturation=0.78,
        gamma=1.08,
        cb_bs=0.07,
        cb_rs=-0.04,
        cb_bm=-0.02,
        cb_bh=0.03,
        curve_s=0.42,
        vignette=0.40,
    ),
    "cinematic_documentary": ColorProfile(
        name="cinematic_documentary",
        description="Кино-док: тёплые света, холодноватые тени, лёгкий S-контраст",
        contrast=1.07,
        brightness=0.0,
        saturation=0.93,
        gamma=1.01,
        cb_rh=0.045,
        cb_bs=0.025,
        cb_rs=-0.015,
        curve_s=0.30,
        vignette=0.22,
    ),
    "dynamic_shorts": ColorProfile(
        name="dynamic_shorts",
        description="Яркий Shorts-look, punch contrast, тёплый акцент",
        contrast=1.16,
        brightness=0.025,
        saturation=1.14,
        gamma=0.96,
        cb_rh=0.06,
        cb_rm=0.025,
        cb_bs=-0.01,
        curve_s=0.45,
        vignette=0.12,
    ),
    "clean_explainer": ColorProfile(
        name="clean_explainer",
        description="Чистый, читаемый, почти нейтральный — без виньетки",
        contrast=1.03,
        brightness=0.015,
        saturation=0.95,
        gamma=1.0,
        curve_s=0.10,
        vignette=0.0,
    ),
    "custom": ColorProfile(
        name="custom",
        description="Как cinematic_documentary",
        contrast=1.07,
        brightness=0.0,
        saturation=0.93,
        gamma=1.01,
        cb_rh=0.045,
        cb_bs=0.025,
        curve_s=0.30,
        vignette=0.22,
    ),
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def build_eq_filter(profile_name: str, intensity: float = 0.5) -> str:
    """eq + colorbalance + mild S-curve + optional vignette. intensity 0 → empty."""
    p = PROFILES.get(profile_name) or PROFILES["cinematic_documentary"]
    if intensity < 0.05:
        return ""

    t = max(0.0, min(1.0, float(intensity)))
    contrast = _lerp(1.0, p.contrast, t)
    brightness = _lerp(0.0, p.brightness, t)
    saturation = _lerp(1.0, p.saturation, t)
    gamma = _lerp(1.0, p.gamma, t)

    parts: list[str] = [
        "eq="
        + ":".join(
            [
                f"contrast={contrast:.3f}",
                f"brightness={brightness:.3f}",
                f"saturation={saturation:.3f}",
                f"gamma={gamma:.3f}",
            ]
        )
    ]

    cb = {
        "rs": _lerp(0.0, p.cb_rs, t),
        "gs": _lerp(0.0, p.cb_gs, t),
        "bs": _lerp(0.0, p.cb_bs, t),
        "rm": _lerp(0.0, p.cb_rm, t),
        "bm": _lerp(0.0, p.cb_bm, t),
        "rh": _lerp(0.0, p.cb_rh, t),
        "bh": _lerp(0.0, p.cb_bh, t),
    }
    if any(abs(v) > 0.005 for v in cb.values()):
        parts.append(
            "colorbalance="
            + ":".join(f"{k}={v:.3f}" for k, v in cb.items() if abs(v) > 0.005)
        )

    # Filmic S-curve (shadow crush + highlight roll-off)
    s_strength = _lerp(0.0, p.curve_s, t)
    if s_strength > 0.10:
        # toe / shoulder
        toe_x = 0.04 + 0.04 * s_strength
        toe_y = toe_x * (0.55 - 0.1 * s_strength)
        mid_x = 0.50
        mid_y = 0.50 + 0.015 * s_strength
        sh_x = 1.0 - (0.04 + 0.04 * s_strength)
        sh_y = 1.0 - (toe_x * (0.55 - 0.1 * s_strength))
        parts.append(
            f"curves=all='0/0|{toe_x:.3f}/{toe_y:.3f}|"
            f"{mid_x:.3f}/{mid_y:.3f}|"
            f"{sh_x:.3f}/{sh_y:.3f}|1/1'"
        )

    vig = _lerp(0.0, p.vignette, t)
    if vig > 0.10:
        # Softer vignette: larger angle = subtler falloff
        angle = max(2.2, 5.0 - 2.8 * vig)
        parts.append(f"vignette=angle=PI/{angle:.2f}:mode=forward")

    # Very mild sharpen only at high intensity (crisp without crunchy)
    if t >= 0.65 and p.name in ("cinematic_documentary", "dynamic_shorts", "custom"):
        parts.append("unsharp=5:5:0.3:5:5:0.0")

    # [VM-SB] Плёночное зерно (лёгкое) — кино-текстура
    _grain = int(round(6 * t))
    if _grain >= 2:
        parts.append(f"noise=alls={_grain}:allf=t")

    return ",".join(parts)


def get_profile(name: str) -> ColorProfile:
    return PROFILES.get(name) or PROFILES["cinematic_documentary"]
