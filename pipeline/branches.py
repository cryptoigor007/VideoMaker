# VideoMaker FIX | 2026.09.01-r12 | 2026-09-01
# CHANGED: outro/intro — путь = включено; fallback H↔V↔S; жёсткий лог [IMO/H|V]
# REPLACE: video_maker/pipeline/branches.py

"""Branches — создание финальных видео (final_16x9 и final_9x16)."""
from __future__ import annotations

import logging
import os

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)

def _imo_flags_and_paths(ctx, prefix: str):
    """Галочка ИЛИ непустой explicit-путь = включать.
    Если путь для формата пуст — берём путь с другого формата (H/V/S).
    """
    def _p(*names):
        for n in names:
            v = (getattr(ctx, n, None) or "").strip()
            if v:
                return v
        return ""

    en_intro = bool(getattr(ctx, f"{prefix}_enable_intro", False))
    en_mid = bool(getattr(ctx, f"{prefix}_enable_middle", False))
    en_outro = bool(getattr(ctx, f"{prefix}_enable_outro", False))

    intro = _p(f"{prefix}_intro_path", "h_intro_path", "v_intro_path", "s_intro_path")
    mid = _p(f"{prefix}_mid_path", "h_mid_path", "v_mid_path", "s_mid_path")
    outro = _p(f"{prefix}_outro_path", "h_outro_path", "v_outro_path", "s_outro_path")

    # Путь указан → считаем включённым (галочка могла стать серой из-за SSD)
    if intro:
        en_intro = True
    if mid:
        en_mid = True
    if outro:
        en_outro = True

    dur_i = float(getattr(ctx, f"{prefix}_intro_duration", 3) or 3)
    dur_m = float(getattr(ctx, f"{prefix}_mid_duration", 1) or 1)
    dur_o = float(getattr(ctx, f"{prefix}_outro_duration", 3) or 3)
    return en_intro, en_mid, en_outro, intro, mid, outro, dur_i, dur_m, dur_o




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

        en_i, en_m, en_o, p_i, p_m, p_o, d_i, d_m, d_o = _imo_flags_and_paths(ctx, "h")
        ctx.log(
            f"[IMO/H] enable intro={en_i} mid={en_m} outro={en_o} | "
            f"paths intro={p_i or '—'} mid={p_m or '—'} outro={p_o or '—'}"
        )
        if en_i or en_m or en_o:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=en_i,
                enable_middle=en_m,
                enable_outro=en_o,
                output_dir=output_dir,
                log_fn=ctx.log,
                analysis=ctx.analysis,
                explicit_intro=p_i,
                explicit_middle=p_m,
                explicit_outro=p_o,
                intro_duration=d_i,
                middle_duration=d_m,
                outro_duration=d_o,
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
            current = geo

            # 1.5) Intro/Middle/Outro — всегда если путь или галочка
            en_i, en_m, en_o, p_i, p_m, p_o, d_i, d_m, d_o = _imo_flags_and_paths(ctx, "v")
            ctx.log(
                f"[IMO/V] enable intro={en_i} mid={en_m} outro={en_o} | "
                f"paths intro={p_i or '—'} mid={p_m or '—'} outro={p_o or '—'}"
            )
            if en_i or en_m or en_o:
                from ..engines.video import add_intro_outro_mid
                current = add_intro_outro_mid(
                    current,
                    ctx.intro_middle_outro_folder,
                    enable_intro=en_i,
                    enable_middle=en_m,
                    enable_outro=en_o,
                    output_dir=output_dir,
                    log_fn=ctx.log,
                    analysis=ctx.analysis,
                    explicit_intro=p_i,
                    explicit_middle=p_m,
                    explicit_outro=p_o,
                    intro_duration=d_i,
                    middle_duration=d_m,
                    outro_duration=d_o,
                )

            # 2) Subtitles отдельным VT encode
            if ctx.v_enable_hooks or ctx.v_enable_subtitles or ctx.v_enable_strong_words:
                t_sub = time.time()
                ctx.log("[VERTICAL] 2/2 burn subtitles...")
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
                ctx.log(f"[VERTICAL] burn elapsed={time.time()-t_sub:.1f}s")
            else:
                import shutil
                if current != output_path:
                    import shutil
                    try:
                        shutil.copy2(current, output_path)
                        current = output_path
                    except Exception:
                        pass

        else:
            current = ctx.master_vertical
            en_i, en_m, en_o, p_i, p_m, p_o, d_i, d_m, d_o = _imo_flags_and_paths(ctx, "v")
            ctx.log(
                f"[IMO/V-native] enable intro={en_i} mid={en_m} outro={en_o} | "
                f"outro_path={p_o or '—'}"
            )
            if en_i or en_m or en_o:
                from ..engines.video import add_intro_outro_mid
                current = add_intro_outro_mid(
                    current,
                    ctx.intro_middle_outro_folder,
                    enable_intro=en_i,
                    enable_middle=en_m,
                    enable_outro=en_o,
                    output_dir=output_dir,
                    log_fn=ctx.log,
                    analysis=ctx.analysis,
                    explicit_intro=p_i,
                    explicit_middle=p_m,
                    explicit_outro=p_o,
                    intro_duration=d_i,
                    middle_duration=d_m,
                    outro_duration=d_o,
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

