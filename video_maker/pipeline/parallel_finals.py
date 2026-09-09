# VideoMaker FIX | 2026.09.02-r21 | 2026-09-02
# CHANGED: comment sync — vertical is one encode (geometry+ASS), not dual pass
# PREV: 2026.09.02-r17
# REPLACE: video_maker/pipeline/parallel_finals.py
"""Final Horizontal (полный) → Final Vertical (one encode geometry+ASS)."""
from __future__ import annotations

import logging

from .branches import FinalHorizontal, FinalVertical
from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class ParallelFinals(Stage):
    """Имя историческое: H и V последовательно, без конкуренции VT/SSD.

    Wide: полный (IMO/subs/hooks/CTA/BGM).
    Vertical: строго один encode (geometry 9:16 + ASS).
    """

    def name(self) -> str:
        return "Final H→V"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[FINAL] Sequential Horizontal → Vertical (one encode)...")

        ctx = FinalHorizontal().run(ctx)
        if getattr(ctx, "cancel_event", None) is not None and ctx.cancel_event.is_set():
            return ctx

        ctx = FinalVertical().run(ctx)
        ctx.progress = 75.0
        return ctx
