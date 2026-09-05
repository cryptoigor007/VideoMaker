# VideoMaker FIX | 2026.09.05-r42 | 2026-09-05
# CHANGED:
#   r42: stub позиций captions / Hook / CTA.
#        Сейчас всегда auto (как Clean Pro).
# PREV: (new)
# REPLACE: video_maker/engines/placement.py

"""Позиционирование текстовых элементов.

Сейчас (r42): всё auto.
В будущем: отдельный выбор позиции captions / Hook / CTA
из GUI или settings без правки event-builder'ов.
"""
from __future__ import annotations


def get_caption_position(
    playres_x: int,
    playres_y: int,
    wide: bool = False,
    mode: str = "auto",
) -> str:
    r"""Вернуть ASS \pos(...) для captions.

    r42: mode игнорируется, используем текущую логику Clean Pro.
    Позже: mode = "bottom" | "center" | "custom_pct" и т.д.
    """
    cx = playres_x // 2
    if wide:
        cy = int(playres_y * 0.90)
        return "{\\an2\\pos(%d,%d)}" % (cx, cy)
    cy = int(playres_y * 0.56)
    return "{\\an5\\pos(%d,%d)}" % (cx, cy)


def get_hook_position(
    playres_x: int,
    playres_y: int,
    wide: bool = False,
    mode: str = "auto",
) -> str:
    """Заглушка позиции Hook. Сейчас не используется builder'ом."""
    # TODO: вынести реальную позицию хуков сюда
    cx = playres_x // 2
    cy = int(playres_y * 0.22) if not wide else int(playres_y * 0.18)
    return f"{{\\pos({cx},{cy})}}"


def get_cta_position(
    playres_x: int,
    playres_y: int,
    wide: bool = False,
    mode: str = "auto",
) -> str:
    """Заглушка позиции CTA. Сейчас не используется builder'ом."""
    # TODO: вынести реальную позицию CTA сюда
    cx = playres_x // 2
    cy = int(playres_y * 0.78) if not wide else int(playres_y * 0.82)
    return f"{{\\pos({cx},{cy})}}"


def list_placement_modes() -> list[str]:
    """Заглушка для будущего GUI."""
    return ["auto", "bottom", "center", "custom"]
