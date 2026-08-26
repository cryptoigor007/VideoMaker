"""Branches — создание финальных видео (final_16x9 и final_9x16)."""
from __future__ import annotations

import logging
import os

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class FinalHorizontal(Stage):
    """Финальное горизонтальное видео (16:9) = master_16x9 + обработка."""

    def name(self) -> str:
        return "Final Horizontal"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[HORIZONTAL] Создание финального горизонтального видео...")

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        output_path = os.path.join(output_dir, "final_16x9.mp4")

        # Начинаем с master
        current = ctx.master_horizontal

        # Добавляем интро/аутро/мидл (если выбрано)
        if ctx.h_enable_intro or ctx.h_enable_middle or ctx.h_enable_outro:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=ctx.h_enable_intro,
                enable_middle=ctx.h_enable_middle,
                enable_outro=ctx.h_enable_outro,
                output_dir=output_dir,
                log=ctx.log,
            )

        # Добавляем хуки + субтитры + сильные слова
        if ctx.h_enable_hooks or ctx.h_enable_subtitles or ctx.h_enable_strong_words:
            from ..engines.subtitles import burn_subtitles
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                enable_hooks=ctx.h_enable_hooks,
                enable_subtitles=ctx.h_enable_subtitles,
                enable_strong_words=ctx.h_enable_strong_words,
                output_path=output_path,
                log=ctx.log,
            )

        ctx.final_horizontal = current
        ctx.log(f"[HORIZONTAL] final_16x9.mp4: {ctx.final_horizontal}")
        ctx.progress = 65.0
        return ctx


class FinalVertical(Stage):
    """Финальное вертикальное видео (9:16) = master_9x16 + обработка."""

    def name(self) -> str:
        return "Final Vertical"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[VERTICAL] Создание финального вертикального видео...")

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        output_path = os.path.join(output_dir, "final_9x16.mp4")

        # Начинаем с master_vertical
        current = ctx.master_vertical

        # Добавляем интро/аутро/мидл (если выбрано)
        if ctx.v_enable_intro or ctx.v_enable_middle or ctx.v_enable_outro:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=ctx.v_enable_intro,
                enable_middle=ctx.v_enable_middle,
                enable_outro=ctx.v_enable_outro,
                output_dir=output_dir,
                log=ctx.log,
            )

        # Добавляем хуки + субтитры + сильные слова
        if ctx.v_enable_hooks or ctx.v_enable_subtitles or ctx.v_enable_strong_words:
            from ..engines.subtitles import burn_subtitles
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                enable_hooks=ctx.v_enable_hooks,
                enable_subtitles=ctx.v_enable_subtitles,
                enable_strong_words=ctx.v_enable_strong_words,
                output_path=output_path,
                log=ctx.log,
            )

        ctx.final_vertical = current
        ctx.log(f"[VERTICAL] final_9x16.mp4: {ctx.final_vertical}")
        ctx.progress = 75.0
        return ctx
