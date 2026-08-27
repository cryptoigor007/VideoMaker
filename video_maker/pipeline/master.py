"""MasterBuilder — создание промежуточных видео (master_16x9 и master_9x16)."""
from __future__ import annotations

import logging
import os
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

        # Шаг 2: Master Vertical (9:16)
        # Приоритет: 1) нативный вертикальный B-roll, 2) vstack с фоном
        if ctx.broll_vertical:
            vertical_files = self._collect_vertical_files(ctx.broll_vertical)
            if vertical_files:
                ctx.master_vertical = self._build_master_vertical_native(ctx, vertical_files)
                ctx.log(f"[MASTER] master_9x16.mp4 (native): {ctx.master_vertical}")
            else:
                ctx.log("[MASTER] Вертикальный B-roll пуст, переключаемся на vstack")
                ctx.master_vertical = self._build_master_vertical_vstack(ctx)
                ctx.log(f"[MASTER] master_9x16.mp4 (vstack): {ctx.master_vertical}")
        elif ctx.vertical_background:
            ctx.master_vertical = self._build_master_vertical_vstack(ctx)
            ctx.log(f"[MASTER] master_9x16.mp4 (vstack): {ctx.master_vertical}")
        else:
            # Это не должно произойти — валидация в Settings должна перехватить
            raise RuntimeError(
                "Для вертикального видео нужен broll_vertical или vertical_background"
            )

        ctx.progress = 50.0
        return ctx

    def _collect_vertical_files(self, folder: str) -> list[str]:
        """Собрать вертикальные видеофайлы (9:16)."""
        from ..engines.video import collect_video_files
        all_files = collect_video_files(folder)
        # Фильтруем только вертикальные (9:16 или близкие)
        vertical = []
        for f in all_files:
            try:
                from ..engines.video import _ffprobe_video_info
                w, h, _ = _ffprobe_video_info(f)
                if h > w * 0.8:  # примерно 9:16 или вертикальнее
                    vertical.append(f)
            except Exception:
                pass
        return vertical

    def _build_master_horizontal(self, ctx: PipelineContext) -> str:
        """Голый склей B-roll под аудиодорожку. БЕЗ обработки."""
        from ..engines.video import collect_video_files, fit_video_to_duration

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

    def _build_master_vertical_native(self, ctx: PipelineContext, video_files: list[str]) -> str:
        """Нативный вертикальный мастер из 9:16 B-roll. БЕЗ обработки."""
        from ..engines.video import fit_video_to_duration

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "master_9x16.mp4")

        fit_video_to_duration(
            video_files=video_files,
            target_duration=ctx.audio_duration,
            output_path=output_path,
            audio_file=ctx.audio_path,
            log_fn=ctx.log,
        )
        return output_path

    def _build_master_vertical_vstack(self, ctx: PipelineContext) -> str:
        """vstack: Master (16:9) сверху + фон снизу. БЕЗ обработки."""
        from ..engines.video import vstack_video_image

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        output_path = os.path.join(output_dir, "master_9x16.mp4")

        vstack_video_image(
            video_path=ctx.master_horizontal,
            background_path=ctx.vertical_background,
            output_path=output_path,
            log_fn=ctx.log,
            top_ratio=getattr(ctx, 'vstack_top_ratio', 0.6),
        )
        return output_path