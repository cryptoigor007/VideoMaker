"""«Современный микс» переходов: в основном жёсткие склейки, редкие акценты."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.cinematic.transitions import MAX_TRANSITION_SEC, choose_transition  # noqa: E402


def test_modern_mix_mostly_hard_cuts():
    kinds = [choose_transition("modern_mix", scene_index=i).kind for i in range(10)]
    hard = sum(1 for k in kinds if k == "hard")
    assert hard >= 7, kinds  # минимум 70% — чистые склейки
    assert all(
        choose_transition("modern_mix", scene_index=i).duration <= MAX_TRANSITION_SEC
        for i in range(10)
    )


def test_modern_mix_deterministic():
    a = [choose_transition("modern_mix", scene_index=i) for i in range(10)]
    b = [choose_transition("modern_mix", scene_index=i) for i in range(10)]
    assert [(x.kind, x.duration) for x in a] == [(x.kind, x.duration) for x in b]


def test_hard_only_unchanged():
    assert all(choose_transition("hard_only", scene_index=i).kind == "hard" for i in range(6))
