"""FinalizeStage — финализация: копирование, замер громкости, метаданные, очистка."""
from __future__ import annotations

import json
import logging
import os
import shutil

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class FinalizeStage(Stage):
    """Финализация: копирование результатов, замер громкости, метаданные, очистка."""

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
            self._measure_and_log_lufs(final_path, ctx)

        # Копируем final_9x16
        if ctx.final_vertical and os.path.exists(ctx.final_vertical):
            final_path = os.path.join(ctx.output_folder, "final_9x16.mp4")
            shutil.copy2(ctx.final_vertical, final_path)
            ctx.log(f"[ФИНАЛ] final_9x16.mp4 → {final_path}")
            self._measure_and_log_lufs(final_path, ctx)

        # Копируем Shorts
        for i, short_path in enumerate(ctx.shorts, 1):
            if os.path.exists(short_path):
                final_path = os.path.join(ctx.output_folder, f"short_{i:03d}.mp4")
                shutil.copy2(short_path, final_path)
                ctx.log(f"[ФИНАЛ] short_{i:03d}.mp4 → {final_path}")
                self._measure_and_log_lufs(final_path, ctx)

        # Метаданные и обложки
        self._write_metadata(ctx)
        self._copy_covers(ctx)

        # Очистка временных файлов
        if not ctx.keep_temp_files:
            self._cleanup_temp(ctx)

        ctx.log("[ФИНАЛ] Готово!")
        ctx.progress = 100.0
        return ctx

    def _measure_and_log_lufs(self, video_path: str, ctx: PipelineContext) -> None:
        """Замерить LUFS и залогировать предупреждение при отклонении."""
        from ..engines.audio import measure_loudness, judge_loudness

        loudness = measure_loudness(video_path)
        if loudness:
            i_lufs = loudness["i_lufs"]
            peak = loudness["peak_dbtp"]
            status = judge_loudness(i_lufs)
            ctx.log(f"[LUFS] {os.path.basename(video_path)}: {i_lufs:.1f} LUFS, peak {peak:.1f} dBTP — {status}")

            if i_lufs < ctx.target_lufs - 2:
                ctx.log(f"[LUFS] ВНИМАНИЕ: {i_lufs:.1f} LUFS ниже цели {ctx.target_lufs} LUFS")
            elif i_lufs > ctx.target_lufs + 2:
                ctx.log(f"[LUFS] ВНИМАНИЕ: {i_lufs:.1f} LUFS выше цели {ctx.target_lufs} LUFS")
        else:
            ctx.log(f"[LUFS] {os.path.basename(video_path)}: не удалось замерить")

    def _write_metadata(self, ctx: PipelineContext) -> None:
        """Создать файл метаданных info_metadata.txt."""
        meta_path = os.path.join(ctx.output_folder, "info_metadata.txt")

        # Берём данные из первого клипа Shorts или из анализа
        clips = ctx.analysis.get("clips_for_shorts", [])
        first_clip = clips[0] if clips else {}

        lines = [
            f"series: {ctx.series_name or '—'}",
            f"title: {first_clip.get('title', '—')}",
            f"description: {first_clip.get('description', '—')}",
            f"hashtags: {first_clip.get('hashtags', '—')}",
            f"hook: {ctx.analysis.get('hook', {}).get('text', '—')}",
            "",
            "=== Shorts ===",
        ]

        for i, clip in enumerate(clips, 1):
            lines.append(f"short_{i:03d}:")
            lines.append(f"  title: {clip.get('title', '—')}")
            lines.append(f"  start: {clip.get('start', 0):.1f}")
            lines.append(f"  end: {clip.get('end', 0):.1f}")
            lines.append(f"  description: {clip.get('description', '—')}")
            lines.append(f"  hashtags: {clip.get('hashtags', '—')}")

        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            ctx.log(f"[ФИНАЛ] Метаданные → {meta_path}")
        except OSError as e:
            ctx.log(f"[ФИНАЛ] Ошибка записи метаданных: {e}")

    def _copy_covers(self, ctx: PipelineContext) -> None:
        """Скопировать обложки в выходную папку."""
        if ctx.cover_horizontal and os.path.exists(ctx.cover_horizontal):
            dst = os.path.join(ctx.output_folder, "cover_16x9.jpg")
            shutil.copy2(ctx.cover_horizontal, dst)
            ctx.log(f"[ФИНАЛ] Обложка 16:9 → {dst}")

        if ctx.cover_vertical and os.path.exists(ctx.cover_vertical):
            dst = os.path.join(ctx.output_folder, "cover_9x16.jpg")
            shutil.copy2(ctx.cover_vertical, dst)
            ctx.log(f"[ФИНАЛ] Обложка 9:16 → {dst}")

    def _cleanup_temp(self, ctx: PipelineContext) -> None:
        """Удалить папку _tmp."""
        tmp_dir = os.path.join(ctx.output_folder, "_tmp")
        if os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                ctx.log(f"[ФИНАЛ] Временные файлы удалены: {tmp_dir}")
            except OSError as e:
                ctx.log(f"[ФИНАЛ] Ошибка удаления временных файлов: {e}")