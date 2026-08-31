"""Final Horizontal (полный) → Final Vertical (geometry + burn)."""
from __future__ import annotations

import logging

from .branches import FinalHorizontal, FinalVertical
from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class ParallelFinals(Stage):
    """Имя историческое: H и V последовательно, без конкуренции VT/SSD.

    Wide всегда полный (intro/outro/subs/hooks/CTA/BGM).
    Vertical — оптимизированный: geometry → burn отдельно.
    """

    def name(self) -> str:
        return "Final H→V"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[FINAL] Sequential Horizontal (полный) → Vertical (geometry+burn)...")

        ctx = FinalHorizontal().run(ctx)
        if getattr(ctx, "cancel_event", None) is not None and ctx.cancel_event.is_set():
            return ctx

        ctx = FinalVertical().run(ctx)
        ctx.progress = 75.0
        return ctx
