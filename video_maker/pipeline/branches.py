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
                log_fn=ctx.log,
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
                log_fn=ctx.log,
            )

        # Аудио пост-обработка: voice_enhance и BGM (только если включены чекбоксы)
        if ctx.voice_enhance or (ctx.add_bgm and ctx.bgm_folder):
            current = self._apply_audio_post(current, ctx, output_path)

        ctx.final_horizontal = current
        ctx.log(f"[HORIZONTAL] final_16x9.mp4: {ctx.final_horizontal}")
        ctx.progress = 65.0
        return ctx

    def _apply_audio_post(self, video_path: str, ctx: PipelineContext, output_path: str) -> str:
        """Применить voice_enhance и BGM к финальному видео."""
        current = video_path

        # Voice enhance — применяем к аудиодорожке
        if ctx.voice_enhance:
            from ..engines.audio import voice_enhance_filter
            tmp_audio = os.path.join(ctx.output_folder, "_tmp", "voice_enhanced.aac")
            os.makedirs(os.path.dirname(tmp_audio), exist_ok=True)
            # Извлекаем аудио, улучшаем, кладем обратно
            current = self._extract_enhance_replace_audio(current, tmp_audio, ctx)

        # BGM — накладываем фоновую музыку
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log)

        return current

    def _extract_enhance_replace_audio(self, video_path: str, enhanced_audio_path: str, ctx: PipelineContext) -> str:
        """Извлечь аудио, улучшить, заменить в видео."""
        from ..engines.audio import voice_enhance_filter, replace_audio
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tmp:
            orig_audio = tmp.name

        try:
            # Извлекаем оригинальное аудио
            import subprocess
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                orig_audio,
            ]
            subprocess.run(cmd, capture_output=True, check=True)

            # Улучшаем аудио
            voice_enhance_filter(orig_audio, enhanced_audio_path, log_fn=ctx.log)

            # Заменяем аудио в видео
            return replace_audio(video_path, enhanced_audio_path, video_path + ".ve.mp4", log_fn=ctx.log)
        finally:
            for p in (orig_audio, enhanced_audio_path):
                try:
                    os.remove(p)
                except OSError:
                    pass


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
                log_fn=ctx.log,
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
                log_fn=ctx.log,
            )

        # Аудио пост-обработка: voice_enhance и BGM (только если включены чекбоксы)
        if ctx.voice_enhance or (ctx.add_bgm and ctx.bgm_folder):
            current = self._apply_audio_post(current, ctx, output_path)

        ctx.final_vertical = current
        ctx.log(f"[VERTICAL] final_9x16.mp4: {ctx.final_vertical}")
        ctx.progress = 75.0
        return ctx

    def _apply_audio_post(self, video_path: str, ctx: PipelineContext, output_path: str) -> str:
        """Применить voice_enhance и BGM к финальному видео."""
        current = video_path

        # Voice enhance
        if ctx.voice_enhance:
            from ..engines.audio import voice_enhance_filter
            tmp_audio = os.path.join(ctx.output_folder, "_tmp", "voice_enhanced_v.aac")
            os.makedirs(os.path.dirname(tmp_audio), exist_ok=True)
            current = self._extract_enhance_replace_audio(current, tmp_audio, ctx)

        # BGM
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log)

        return current

    def _extract_enhance_replace_audio(self, video_path: str, enhanced_audio_path: str, ctx: PipelineContext) -> str:
        """Извлечь аудио, улучшить, заменить в видео."""
        from ..engines.audio import voice_enhance_filter, replace_audio
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tmp:
            orig_audio = tmp.name

        try:
            import subprocess
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                orig_audio,
            ]
            subprocess.run(cmd, capture_output=True, check=True)

            voice_enhance_filter(orig_audio, enhanced_audio_path, log_fn=ctx.log)

            return replace_audio(video_path, enhanced_audio_path, video_path + ".ve.mp4", log_fn=ctx.log)
        finally:
            for p in (orig_audio, enhanced_audio_path):
                try:
                    os.remove(p)
                except OSError:
                    pass