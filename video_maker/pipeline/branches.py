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
                analysis=ctx.analysis,
                explicit_intro=ctx.h_intro_path,
                explicit_middle=ctx.h_mid_path,
                explicit_outro=ctx.h_outro_path,
                intro_duration=float(getattr(ctx, "h_intro_duration", 3) or 3),
                middle_duration=float(getattr(ctx, "h_mid_duration", 1) or 1),
                outro_duration=float(getattr(ctx, "h_outro_duration", 3) or 3),
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
                caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                transcription=ctx.transcription,
                use_aisie=True,
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
            tmp_audio = os.path.join(ctx.output_folder, "_tmp", "voice_enhanced.aac")
            os.makedirs(os.path.dirname(tmp_audio), exist_ok=True)
            # Извлекаем аудио, улучшаем, кладем обратно
            current = self._extract_enhance_replace_audio(current, tmp_audio, ctx)

        # BGM — накладываем фоновую музыку
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log, loudnorm=True)
            ctx.horizontal_audio_normalized = True

        return current

    def _extract_enhance_replace_audio(self, video_path: str, enhanced_audio_path: str, ctx: PipelineContext) -> str:
        """Извлечь аудио, улучшить, заменить в видео."""
        import tempfile

        from ..engines.audio import replace_audio, voice_enhance_filter

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
    """Финальное вертикальное видео (9:16). VSTACK+subs — один encode, если нет native master."""

    def name(self) -> str:
        return "Final Vertical"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[VERTICAL] Создание финального вертикального видео...")

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "final_9x16.mp4")

        need_vstack = (not ctx.master_vertical) and bool(ctx.vertical_background)
        current = ctx.master_vertical or ""

        # --- один encode: vstack + subtitles из master_16x9 ---
        if need_vstack:
            import time
            from ..engines.video import vstack_video_image
            from ..engines.subtitles import burn_subtitles

            # 1) Только геометрия (без libass) — замер в логе
            geo_path = os.path.join(output_dir, "master_9x16_geo.mp4")
            t_geo = time.time()
            ctx.log("[VERTICAL] 1/2 geometry (без subtitles)...")
            geo = vstack_video_image(
                video_path=ctx.master_horizontal,
                background_path=ctx.vertical_background,
                output_path=geo_path,
                log_fn=ctx.log,
                top_ratio=getattr(ctx, "vstack_top_ratio", 0.6),
                ass_path="",  # намеренно без ASS
            )
            ctx.log(f"[VERTICAL] geometry elapsed={time.time()-t_geo:.1f}s")
            ctx.master_vertical = geo

            # 2) Subtitles отдельным VT encode (как на horizontal — там ~12с)
            current = geo
            if ctx.v_enable_hooks or ctx.v_enable_subtitles or ctx.v_enable_strong_words:
                t_sub = time.time()
                ctx.log("[VERTICAL] 2/2 burn subtitles...")
                current = burn_subtitles(
                    video_path=geo,
                    analysis=ctx.analysis,
                    enable_hooks=ctx.v_enable_hooks,
                    enable_subtitles=ctx.v_enable_subtitles,
                    enable_strong_words=ctx.v_enable_strong_words,
                    output_path=output_path,
                    log_fn=ctx.log,
                    caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                    hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                    transcription=ctx.transcription,
                    use_aisie=True,
                )
                ctx.log(f"[VERTICAL] burn elapsed={time.time()-t_sub:.1f}s")
            else:
                import shutil
                shutil.copy2(geo, output_path)
                current = output_path
        else:
            current = ctx.master_vertical
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
                    analysis=ctx.analysis,
                    explicit_intro=ctx.v_intro_path,
                    explicit_middle=ctx.v_mid_path,
                    explicit_outro=ctx.v_outro_path,
                    intro_duration=float(getattr(ctx, "v_intro_duration", 3) or 3),
                    middle_duration=float(getattr(ctx, "v_mid_duration", 1) or 1),
                    outro_duration=float(getattr(ctx, "v_outro_duration", 3) or 3),
                )
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
                    caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                    hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                    transcription=ctx.transcription,
                    use_aisie=True,
                )

        # BGM + loudnorm в одном audio-проходе (-c:v copy)
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            bgm_out = os.path.join(output_dir, "final_9x16_bgm.mp4")
            current = mix_bgm(current, ctx.bgm_folder, bgm_out, log_fn=ctx.log, loudnorm=True)
            ctx.vertical_audio_normalized = True
        elif ctx.voice_enhance:
            current = self._apply_audio_post(current, ctx, output_path)

        ctx.final_vertical = current
        ctx.log(f"[VERTICAL] final_9x16.mp4: {ctx.final_vertical}")
        ctx.progress = 75.0
        return ctx

    def _apply_audio_post(self, video_path: str, ctx: PipelineContext, output_path: str) -> str:
        current = video_path
        if ctx.voice_enhance:
            tmp_audio = os.path.join(ctx.output_folder, "_tmp", "voice_enhanced_v.aac")
            os.makedirs(os.path.dirname(tmp_audio), exist_ok=True)
            current = self._extract_enhance_replace_audio(current, tmp_audio, ctx)
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log, loudnorm=True)
        return current

    def _extract_enhance_replace_audio(self, video_path: str, enhanced_audio_path: str, ctx: PipelineContext) -> str:
        import tempfile
        import subprocess
        from ..engines.audio import replace_audio, voice_enhance_filter

        with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tmp:
            orig_audio = tmp.name
        try:
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                orig_audio,
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            voice_enhance_filter(orig_audio, enhanced_audio_path, log_fn=ctx.log)
            return replace_audio(video_path, enhanced_audio_path, video_path + ".ve.mp4", log_fn=ctx.log)
        finally:
            for pth in (orig_audio, enhanced_audio_path):
                try:
                    os.remove(pth)
                except OSError:
                    pass

