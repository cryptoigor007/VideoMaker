"""MasterBuilder — создание промежуточных видео (master_16x9 и master_9x16)."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class MasterBuilder(Stage):
    """Создание промежуточных видео: master_16x9.mp4 и master_9x16.mp4."""

    def name(self) -> str:
        return "Master"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[MASTER] Создание промежуточных видео...")

        # Шаг 1: Master Horizontal (16:9) — голый склей B-roll под аудио
        ctx.master_horizontal = self._build_master_horizontal(ctx)
        ctx.log(f"[MASTER] master_16x9.mp4: {ctx.master_horizontal}")
        ctx.progress = 40.0

        # Шаг 2: Master Vertical (9:16) — vstack Master + фон
        if ctx.vertical_background:
            ctx.master_vertical = self._build_master_vertical(ctx)
            ctx.log(f"[MASTER] master_9x16.mp4: {ctx.master_vertical}")
        else:
            ctx.master_vertical = ctx.master_horizontal
            ctx.log("[MASTER] Фон не задан, вертикальный master = горизонтальный")

        ctx.progress = 50.0
        return ctx

    def _build_master_horizontal(self, ctx: PipelineContext) -> str:
        """Голый склей B-roll под аудиодорожку. БЕЗ обработки."""
        from ..engines.video import collect_video_files, fit_video_to_duration
        from ..engines.audio import replace_audio

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "master_16x9.mp4")

        video_files = collect_video_files(ctx.broll_horizontal)
        if not video_files:
            raise FileNotFoundError(f"Нет видео в {ctx.broll_horizontal}")

        fit_video_to_duration(
            video_files=video_files,
            target_duration=ctx.audio_duration,
            output_path=output_path,
            audio_file=ctx.audio_path,
            log_fn=ctx.log,
        )
        return output_path

    def _build_master_vertical(self, ctx: PipelineContext) -> str:
        """vstack: Master (16:9) сверху + фон снизу. БЕЗ обработки."""
        from ..engines.video import vstack_video_image

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        output_path = os.path.join(output_dir, "master_9x16.mp4")

        vstack_video_image(
            video_path=ctx.master_horizontal,
            background_path=ctx.vertical_background,
            output_path=output_path,
            log_fn=ctx.log,
        )
        return output_path
