"""Параллельный запуск FinalHorizontal + FinalVertical (M1)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy

from .branches import FinalHorizontal, FinalVertical
from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class ParallelFinals(Stage):
    """Final H и Final V одновременно. Пишут в разные поля ctx и разные файлы."""

    def name(self) -> str:
        return "Final H∥V"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[FINAL] Параллельный запуск Horizontal + Vertical...")

        errors: list[str] = []

        def run_h() -> None:
            FinalHorizontal().run(ctx)

        def run_v() -> None:
            FinalVertical().run(ctx)

        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {
                ex.submit(run_h): "Horizontal",
                ex.submit(run_v): "Vertical",
            }
            for fut in as_completed(futs):
                label = futs[fut]
                try:
                    fut.result()
                    ctx.log(f"[FINAL] ветка {label} готова")
                except Exception as e:
                    msg = f"{label}: {type(e).__name__}: {e}"
                    errors.append(msg)
                    log.exception("Parallel final %s failed", label)
                    ctx.log(f"[FINAL] ОШИБКА {msg}")

        if errors:
            raise RuntimeError("Parallel finals failed: " + "; ".join(errors))

        if not ctx.final_horizontal and not ctx.final_vertical:
            raise RuntimeError("Parallel finals: нет ни final_horizontal, ни final_vertical")

        ctx.progress = 75.0
        return ctx
