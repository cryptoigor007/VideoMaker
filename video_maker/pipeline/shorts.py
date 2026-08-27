"""ShortsCutter — нарезка Shorts из промежуточного вертикального видео."""
from __future__ import annotations

import logging
import os

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class ShortsCutter(Stage):
    """Нарезка Shorts из master_9x16 (промежуточное)."""

    def name(self) -> str:
        return "Shorts"

    def _create_short(
        self, ctx: PipelineContext, clip: dict, index: int, output_dir: str
    ) -> str | None:
        """Создать один Short из промежуточного вертикального видео.
        Возвращает None если клип невалиден (пропуск)."""
        from ..engines.video import cut_segment
        from ..engines.subtitles import burn_subtitles
        from ..engines.audio import probe_duration

        start = clip.get("start", 0)
        end = clip.get("end", 0)
        duration = end - start

        # Проверяем длительность видео и ограничиваем клип
        video_duration = probe_duration(ctx.master_vertical)
        if start >= video_duration:
            ctx.log(f"[SHORTS] Клип {index}: start ({start:.1f}) >= длительность видео ({video_duration:.1f}), пропускаем")
            return None
        if duration <= 0:
            ctx.log(f"[SHORTS] Клип {index}: невалидная длительность ({duration:.1f}), пропускаем")
            return None
        if end > video_duration:
            end = video_duration
            duration = end - start

        # Обрезаем из master_9x16 (промежуточное!)
        cut_path = os.path.join(output_dir, f"short_{index:03d}_cut.mp4")
        cut_segment(
            video_path=ctx.master_vertical,
            start=start,
            duration=duration,
            output_path=cut_path,
            log_fn=ctx.log,
        )

        current = cut_path

        # Добавляем интро/аутро/мидл (если выбрано)
        if ctx.s_enable_intro or ctx.s_enable_middle or ctx.s_enable_outro:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=ctx.s_enable_intro,
                enable_middle=ctx.s_enable_middle,
                enable_outro=ctx.s_enable_outro,
                output_dir=output_dir,
                log_fn=ctx.log,
            )

        # Добавляем хуки + субтитры
        if ctx.s_enable_hooks or ctx.s_enable_subtitles or ctx.s_enable_strong_words:
            final_path = os.path.join(output_dir, f"short_{index:03d}.mp4")
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                clip=clip,
                enable_hooks=ctx.s_enable_hooks,
                enable_subtitles=ctx.s_enable_subtitles,
                enable_strong_words=ctx.s_enable_strong_words,
                output_path=final_path,
                log_fn=ctx.log,
            )

        # Сохраняем метаданные
        meta_path = os.path.join(output_dir, f"short_{index:03d}.txt")
        self._write_metadata(clip, meta_path)

        return current

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[SHORTS] Нарезка Shorts из промежуточного вертикального видео...")

        clips = ctx.analysis.get("clips_for_shorts", [])
        if not clips:
            ctx.log("[SHORTS] Нет клипов для Shorts, пропускаем")
            ctx.progress = 90.0
            return ctx

        output_dir = os.path.join(ctx.output_folder, "_tmp", "shorts")
        os.makedirs(output_dir, exist_ok=True)

        for i, clip in enumerate(clips, 1):
            short_path = self._create_short(ctx, clip, i, output_dir)
            if short_path:
                ctx.shorts.append(short_path)
                ctx.log(f"[SHORTS] short_{i:03d}.mp4: {short_path}")
            else:
                ctx.log(f"[SHORTS] Клип {i} пропущен")

        ctx.progress = 90.0
        return ctx

    def _write_metadata(self, clip: dict, path: str) -> None:
        """Записать метаданные Short (название, описание, хештеги)."""
        title = clip.get("title", "Без названия")
        description = clip.get("description", "")
        hashtags = clip.get("hashtags", "")

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Заголовок: {title}\n")
            f.write(f"Описание: {description}\n")
            f.write(f"Хештеги: {hashtags}\n")