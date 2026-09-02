# VideoMaker FIX | 2026.09.02-r19 | 2026-09-02
# CHANGED: BGM once; clean geo for shorts cuts when clips exist; 1 Hook long
# PREV: 2026.09.02-r17
# REPLACE: video_maker/pipeline/branches.py
"""Branches — создание финальных видео (final_16x9 и final_9x16)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class FinalHorizontal(Stage):
    """Финальное горизонтальное видео (16:9) = master_16x9 + обработка.

    r16: после FAST IMO единственный полный encode wide — burn_subtitles (если нужен).
    Если burn/audio-post не нужны — только stream-copy в final_16x9.mp4.
    """

    def name(self) -> str:
        return "Final Horizontal"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[HORIZONTAL] Создание финального горизонтального видео...")

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "final_16x9.mp4")

        # Начинаем с master
        current = ctx.master_horizontal
        did_imo = False
        did_burn = False

        # Добавляем интро/аутро/мидл (если выбрано) — FAST path, без полного mid encode
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
            did_imo = True

        # Единственный полный encode wide: burn ASS (hooks/subs/strong)
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
            did_burn = True

        # Аудио пост-обработка: voice_enhance и BGM (video stream copy)
        if ctx.voice_enhance or (ctx.add_bgm and ctx.bgm_folder):
            current = self._apply_audio_post(current, ctx, output_path)

        # Если не было burn — привести имя к final_16x9.mp4 без перекодирования
        if not did_burn and current != output_path and os.path.isfile(current):
            try:
                # prefer stream copy rename/move; fallback shutil.copy2
                if os.path.dirname(os.path.abspath(current)) == os.path.abspath(output_dir):
                    os.replace(current, output_path)
                else:
                    cmd = [
                        "ffmpeg", "-y", "-i", current,
                        "-c", "copy", output_path,
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if res.returncode != 0 or not os.path.isfile(output_path):
                        shutil.copy2(current, output_path)
                current = output_path
            except Exception as e:
                ctx.log(f"[HORIZONTAL] warn: could not place final_16x9: {e}")

        ctx.final_horizontal = current
        ctx.log(
            f"[HORIZONTAL] final_16x9.mp4: {ctx.final_horizontal} | "
            f"imo={did_imo} burn={did_burn} (target ≤1 full encode)"
        )
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

        # BGM — мешаем ОДИН раз на long wide (единственный mix в пайплайне)
        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log, loudnorm=True)
            ctx.horizontal_audio_normalized = True
            ctx.bgm_mixed = True
            ctx.bgm_source_video = current
            ctx.log("[BGM] mixed once on wide — vertical/shorts will reuse this audio")

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
    """Финальное вертикальное видео (9:16).

    r17: при need_vstack — один encode (geometry 9:16 + burn ASS).
    """

    def name(self) -> str:
        return "Final Vertical"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[VERTICAL] Создание финального вертикального видео...")

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "final_9x16.mp4")

        need_vstack = (not ctx.master_vertical) and bool(ctx.vertical_background)
        current = ctx.master_vertical or ""
        one_encode = False

        # --- r17/r19: vertical final = geometry [+ ASS]; clean geo for shorts cuts ---
        if need_vstack:
            import time
            from ..engines.video import vstack_video_image
            from ..engines.subtitles import burn_subtitles

            need_burn = (
                ctx.v_enable_hooks
                or ctx.v_enable_subtitles
                or ctx.v_enable_strong_words
            )
            shorts_clips = []
            if ctx.analysis:
                shorts_clips = list(ctx.analysis.get("clips_for_shorts") or [])
            need_clean_geo = bool(shorts_clips)

            ass_path = ""
            if need_burn:
                t_ass = time.time()
                ctx.log("[VERTICAL] generate ASS (ass_only) for 9:16...")
                ass_path = burn_subtitles(
                    video_path=ctx.master_horizontal,
                    analysis=ctx.analysis,
                    enable_hooks=ctx.v_enable_hooks,
                    enable_subtitles=ctx.v_enable_subtitles,
                    enable_strong_words=ctx.v_enable_strong_words,
                    output_path=os.path.join(output_dir, "vertical.ass"),
                    log_fn=ctx.log,
                    caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                    hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                    transcription=ctx.transcription,
                    use_aisie=True,
                    force_size=(2160, 3840),
                    ass_only=True,
                )
                ctx.log(f"[VERTICAL] ASS ready in {time.time()-t_ass:.1f}s → {ass_path}")

            geo_path = os.path.join(output_dir, "master_9x16_geo.mp4")

            if need_clean_geo and need_burn and ass_path:
                # Shorts need clean geo (stream copy) + final with ASS separately
                t0 = time.time()
                ctx.log("[VERTICAL] geometry-only for shorts master...")
                geo = vstack_video_image(
                    video_path=ctx.master_horizontal,
                    background_path=ctx.vertical_background,
                    output_path=geo_path,
                    log_fn=ctx.log,
                    top_ratio=getattr(ctx, "vstack_top_ratio", 0.6),
                    ass_path="",
                )
                ctx.master_vertical = geo
                ctx.log(f"[VERTICAL] geo for shorts OK in {time.time()-t0:.1f}s")
                t1 = time.time()
                ctx.log("[VERTICAL] one encode: geometry + ASS → final_9x16")
                current = vstack_video_image(
                    video_path=ctx.master_horizontal,
                    background_path=ctx.vertical_background,
                    output_path=output_path,
                    log_fn=ctx.log,
                    top_ratio=getattr(ctx, "vstack_top_ratio", 0.6),
                    ass_path=ass_path,
                )
                one_encode = False  # geo + final
                ctx.log(f"[VERTICAL] final+ASS elapsed={time.time()-t1:.1f}s")
            else:
                # Single pass: geo [+ ASS] → final; master_vertical = same
                t0 = time.time()
                ctx.log(
                    f"[VERTICAL] one encode: geometry"
                    f"{' + ASS' if ass_path else ''} → final_9x16"
                )
                current = vstack_video_image(
                    video_path=ctx.master_horizontal,
                    background_path=ctx.vertical_background,
                    output_path=output_path,
                    log_fn=ctx.log,
                    top_ratio=getattr(ctx, "vstack_top_ratio", 0.6),
                    ass_path=ass_path or "",
                )
                ctx.master_vertical = current
                one_encode = True
                ctx.log(f"[VERTICAL] one-encode elapsed={time.time()-t0:.1f}s")
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

        # BGM: не мешать повторно — копируем уже смешанную дорожку с wide
        if getattr(ctx, "bgm_mixed", False) and getattr(ctx, "final_horizontal", ""):
            src_a = ctx.final_horizontal
            if src_a and os.path.isfile(src_a):
                bgm_out = os.path.join(output_dir, "final_9x16_bgm.mp4")
                current = self._copy_audio_from(current, src_a, bgm_out, ctx)
                ctx.vertical_audio_normalized = True
                ctx.log("[BGM] vertical: reused mixed audio from wide (no re-mix)")
            elif ctx.voice_enhance:
                current = self._apply_audio_post(current, ctx, output_path)
        elif ctx.add_bgm and ctx.bgm_folder:
            # wide не мешал (нет H BGM) — единственный mix здесь
            from ..engines.audio import mix_bgm
            bgm_out = os.path.join(output_dir, "final_9x16_bgm.mp4")
            current = mix_bgm(current, ctx.bgm_folder, bgm_out, log_fn=ctx.log, loudnorm=True)
            ctx.vertical_audio_normalized = True
            ctx.bgm_mixed = True
            ctx.bgm_source_video = current
            ctx.log("[BGM] mixed once on vertical (wide had no BGM)")
        elif ctx.voice_enhance:
            current = self._apply_audio_post(current, ctx, output_path)

        ctx.final_vertical = current
        ctx.log(
            f"[VERTICAL] final_9x16.mp4: {ctx.final_vertical} | "
            f"one_encode={one_encode}"
        )
        ctx.progress = 75.0
        return ctx

    def _apply_audio_post(self, video_path: str, ctx: PipelineContext, output_path: str) -> str:
        current = video_path
        if ctx.voice_enhance:
            tmp_audio = os.path.join(ctx.output_folder, "_tmp", "voice_enhanced_v.aac")
            os.makedirs(os.path.dirname(tmp_audio), exist_ok=True)
            current = self._extract_enhance_replace_audio(current, tmp_audio, ctx)
        # BGM only if not already mixed upstream
        if ctx.add_bgm and ctx.bgm_folder and not getattr(ctx, "bgm_mixed", False):
            from ..engines.audio import mix_bgm
            current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log, loudnorm=True)
            ctx.bgm_mixed = True
            ctx.bgm_source_video = current
        return current

    def _copy_audio_from(self, video_path: str, audio_src_video: str, output_path: str, ctx: PipelineContext) -> str:
        """Подставить аудио из уже смешанного long-видео (без повторного mix BGM)."""
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_src_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy",
            "-c:a", "copy",
            "-shortest",
            output_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        # fallback: re-encode audio only
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_src_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-shortest",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if os.path.isfile(output_path):
            return output_path
        ctx.log("[BGM] copy audio failed — keep original video audio")
        return video_path

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

