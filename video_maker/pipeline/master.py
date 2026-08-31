"""MasterBuilder — только master_16x9; вертикаль собирается в Final (один encode)."""
from __future__ import annotations

import logging
import os

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class MasterBuilder(Stage):
    """Создаёт master_16x9. master_9x16 больше не кодируется здесь (экономия 1 encode)."""

    def name(self) -> str:
        return "Master"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[MASTER] Создание master_16x9 (вертикаль — в Final, один encode)...")

        ctx.master_horizontal = self._build_master_horizontal(ctx)
        ctx.log(f"[MASTER] master_16x9.mp4: {ctx.master_horizontal}")

        # Не кодируем master_9x16 здесь. Помечаем режим для FinalVertical.
        ctx.master_vertical = ""
        if ctx.broll_vertical:
            vertical_files = self._collect_vertical_files(ctx.broll_vertical)
            if vertical_files:
                # Нативный вертикальный B-roll — один fit (нужен как источник)
                ctx.master_vertical = self._build_master_vertical_native(ctx, vertical_files)
                ctx.log(f"[MASTER] master_9x16 native: {ctx.master_vertical}")
            else:
                ctx.log("[MASTER] VSTACK отложен → FinalVertical (вместе с субтитрами)")
        elif ctx.vertical_background:
            ctx.log("[MASTER] VSTACK отложен → FinalVertical (вместе с субтитрами)")
        else:
            raise RuntimeError(
                "Для вертикального видео нужен broll_vertical или vertical_background"
            )

        ctx.progress = 45.0
        return ctx

    def _collect_vertical_files(self, folder: str) -> list[str]:
        from ..engines.video import collect_video_files, _ffprobe_video_info
        all_files = collect_video_files(folder)
        vertical = []
        for f in all_files:
            try:
                w, h, _ = _ffprobe_video_info(f)
                if h > w * 0.8:
                    vertical.append(f)
            except Exception:
                pass
        return vertical

    def _build_master_horizontal(self, ctx: PipelineContext) -> str:
        from ..engines.video import collect_video_files, fit_video_to_duration

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "master_16x9.mp4")

        video_files = collect_video_files(ctx.broll_horizontal)
        if not video_files:
            raise FileNotFoundError(f"Нет видео в {ctx.broll_horizontal}")
        ctx.log(f"[MASTER] B-roll H: {len(video_files)} файлов")

        fit_video_to_duration(
            video_files=video_files,
            target_duration=ctx.audio_duration,
            output_path=output_path,
            audio_file=ctx.audio_path,
            log_fn=ctx.log,
            broll_root=ctx.broll_horizontal,
            move_used=True,
        )
        return output_path

    def _build_master_vertical_native(self, ctx: PipelineContext, video_files: list[str]) -> str:
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
            broll_root=ctx.broll_vertical,
            move_used=True,
        )
        return output_path
