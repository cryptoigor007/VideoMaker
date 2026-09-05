# VideoMaker FIX | 2026.09.05-r43 | 2026-09-05
# CHANGED:
#   r43: strong levels L2/L3/L4 (neon yellow/orange/pink) + scale factors.
#        base / dim / active non-strong сохранены.
# PREV: 2026.09.05-r42
# REPLACE: video_maker/engines/colors.py

"""Палитры субтитров (parity / Clean Pro).

r43: base + active non-strong + optional strong L2/L3/L4.
Cyan намеренно отсутствует.
"""
from __future__ import annotations

# HEX
PARITY_PALETTE = {
    "base": "#FFFFFF",       # non-active / non-strong
    "dim": "#C8C8C8",        # reserved (r42/r43 не используется в parity)
    "active": "#FFFF00",     # active non-strong (mild yellow)
    "L2": "#FFFF00",         # strong neon yellow
    "L3": "#FF5E00",         # strong neon orange
    "L4": "#FF00FF",         # strong neon pink
}

# ASS BGR
PARITY_ASS = {
    "base": "&H00FFFFFF&",
    "dim": "&H00C8C8C8&",
    "active": "&H0000FFFF&",   # #FFFF00
    "L2": "&H0000FFFF&",       # #FFFF00 yellow
    "L3": "&H00005EFF&",       # #FF5E00 orange
    "L4": "&H00FF00FF&",       # #FF00FF pink
}

# Scale только для active strong
STRONG_SCALE = {
    "L2": 1.14,
    "L3": 1.22,
    "L4": 1.28,
}


def get_word_color(is_active: bool, mode: str = "auto") -> str:
    """HEX для non-strong слова."""
    if is_active:
        return PARITY_PALETTE["active"]
    return PARITY_PALETTE["base"]


def get_word_ass_color(is_active: bool, mode: str = "auto") -> str:
    r"""ASS-цвет для non-strong слова."""
    if is_active:
        return PARITY_ASS["active"]
    return PARITY_ASS["base"]


def get_strong_ass_color(level: str) -> str:
    """ASS-цвет strong по уровню (L2/L3/L4). Неизвестный → L2."""
    return PARITY_ASS.get(level, PARITY_ASS["L2"])


def get_strong_scale(level: str) -> float:
    """Множитель font size для active strong."""
    return float(STRONG_SCALE.get(level, 1.14))


def list_available_palettes() -> list[str]:
    return ["auto", "parity_clean"]
