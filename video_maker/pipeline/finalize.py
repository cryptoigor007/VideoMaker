"""FinalizeStage — финализация: копирование, замер громкости, очистка."""
from __future__ import annotations

import logging
import os
import shutil

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class FinalizeStage(Stage):
    """Финализация: копирование результатов, замер громкости, очистка."""

    def name(self) -> str:
        return "Финализация"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[ФИНАЛ] Копирование результатов...")

        # Копируем master_16x9
        if ctx.master_horizontal and os.path.exists(ctx.master_horizontal):
            final_path = os.path.join(ctx.output_folder, "master_16x9.mp4")
            shutil.copy2(ctx.master_horizontal, final_path)
            ctx.log(f"[ФИНАЛ] master_16x9.mp4 → {final_path}")

        # Копируем master_9x16
        if ctx.master_vertical and os.path.exists(ctx.master_vertical):
            final_path = os.path.join(ctx.output_folder, "master_9x16.mp4")
            shutil.copy2(ctx.master_vertical, final_path)
            ctx.log(f"[ФИНАЛ] master_9x16.mp4 → {final_path}")

        # Копируем final_16x9
        if ctx.final_horizontal and os.path.exists(ctx.final_horizontal):
            final_path = os.path.join(ctx.output_folder, "final_16x9.mp4")
            shutil.copy2(ctx.final_horizontal, final_path)
            ctx.log(f"[ФИНАЛ] final_16x9.mp4 → {final_path}")

        # Копируем final_9x16
        if ctx.final_vertical and os.path.exists(ctx.final_vertical):
            final_path = os.path.join(ctx.output_folder, "final_9x16.mp4")
            shutil.copy2(ctx.final_vertical, final_path)
            ctx.log(f"[ФИНАЛ] final_9x16.mp4 → {final_path}")

        # Копируем Shorts
        for i, short_path in enumerate(ctx.shorts, 1):
            if os.path.exists(short_path):
                final_path = os.path.join(ctx.output_folder, f"short_{i:03d}.mp4")
                shutil.copy2(short_path, final_path)
                ctx.log(f"[ФИНАЛ] short_{i:03d}.mp4 → {final_path}")

        ctx.log("[ФИНАЛ] Готово!")
        ctx.progress = 100.0
        return ctx
