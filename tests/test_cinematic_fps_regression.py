"""Регрессия: все цепочки сцен нормализуют fps (баг «видео обрывается на 7с»).

Без fps в статичной ветке в xfade-цепочку попадали клипы с разным fps
(23.976 / 25 / 29.97) → ломались таймстемпы → энкодер выбрасывал кадры
(drop=930) и видео замирало, хотя аудио продолжалось.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.cinematic.edit_plan import MotionKeyframe  # noqa: E402
from video_maker.cinematic.motion import build_zoompan_expr  # noqa: E402


def test_static_chain_has_fps():
    """Статичная сцена (без движения) обязана иметь fps= в цепочке."""
    static = [MotionKeyframe(t=0.0, scale=1.0), MotionKeyframe(t=1.0, scale=1.0)]
    chain = build_zoompan_expr(static, duration_sec=4.0, out_w=3840, out_h=2160)
    assert "fps=30" in chain, chain


def test_moving_chain_has_fps():
    moving = [
        MotionKeyframe(t=0.0, scale=1.0, easing="ease_in_out"),
        MotionKeyframe(t=1.0, scale=1.1, easing="ease_in_out"),
    ]
    chain = build_zoompan_expr(moving, duration_sec=4.0, out_w=3840, out_h=2160)
    assert "fps=30" in chain, chain
    # fps обязан идти ДО zoompan — иначе клипы 24/25fps сжимаются по времени
    assert chain.index("fps=30") < chain.index("zoompan="), chain


def test_none_motion_is_static_with_fps():
    chain = build_zoompan_expr(None, duration_sec=3.0, out_w=1920, out_h=1080)
    assert "fps=30" in chain, chain
