"""ShortsCutter — нарезка Shorts + BGM + метаданные в отдельных папках."""
from __future__ import annotations

import logging
import os
import shutil

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


class ShortsCutter(Stage):
    def name(self) -> str:
        return "Shorts"

    def _create_short(
        self, ctx: PipelineContext, clip: dict, index: int, output_dir: str
    ) -> str | None:
        from ..engines.audio import probe_duration
        from ..engines.subtitles import burn_subtitles
        from ..engines.video import cut_segment

        start = float(clip.get("start", 0))
        end = float(clip.get("end", 0))
        duration = end - start

        video_duration = probe_duration(ctx.master_vertical)
        if start >= video_duration or duration <= 0:
            ctx.log(f"[SHORTS] Клип {index}: невалидный тайминг, пропускаем")
            return None
        if end > video_duration:
            end = video_duration
            duration = end - start

        short_dir = os.path.join(output_dir, f"short_{index:03d}")
        os.makedirs(short_dir, exist_ok=True)

        cut_path = os.path.join(short_dir, f"short_{index:03d}_cut.mp4")
        cut_segment(
            video_path=ctx.master_vertical,
            start=start,
            duration=duration,
            output_path=cut_path,
            log_fn=ctx.log,
        )
        current = cut_path

        if ctx.s_enable_intro or ctx.s_enable_middle or ctx.s_enable_outro:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=ctx.s_enable_intro,
                enable_middle=ctx.s_enable_middle,
                enable_outro=ctx.s_enable_outro,
                output_dir=short_dir,
                log_fn=ctx.log,
                analysis=ctx.analysis,
                explicit_intro=getattr(ctx, "s_intro_path", ""),
                explicit_middle=getattr(ctx, "s_mid_path", ""),
                explicit_outro=getattr(ctx, "s_outro_path", ""),
            )

        if ctx.s_enable_hooks or ctx.s_enable_subtitles or ctx.s_enable_strong_words:
            subtitled = os.path.join(short_dir, f"short_{index:03d}_subs.mp4")
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                clip=clip,
                enable_hooks=ctx.s_enable_hooks,
                enable_subtitles=ctx.s_enable_subtitles,
                enable_strong_words=ctx.s_enable_strong_words,
                output_path=subtitled,
                log_fn=ctx.log,
                caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                transcription=ctx.transcription,
                use_aisie=True,
            )

        final_path = os.path.join(short_dir, f"short_{index:03d}.mp4")
        if ctx.voice_enhance or (ctx.add_bgm and ctx.bgm_folder):
            current = self._apply_audio_post(current, ctx, final_path)
        else:
            if current != final_path:
                shutil.copy2(current, final_path)
                current = final_path

        # 4 packaging-файла (свои для шорта, не из full video)
        base = f"short_{index:03d}"
        hook = str(clip.get("hook") or (ctx.analysis.get("hook") or {}).get("text") or "")
        for kind, content in (
            ("title", str(clip.get("title") or "")),
            ("description", str(clip.get("description") or "")),
            ("hook", hook),
            ("hashtags", str(clip.get("hashtags") or "")),
        ):
            p = os.path.join(short_dir, f"{base}_{kind}.txt")
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write((content or "").strip() + ("\n" if (content or "").strip() else ""))
            except OSError as e:
                ctx.log(f"[SHORTS] packaging {kind}: {e}")
        return current

    def _apply_audio_post(self, video_path: str, ctx: PipelineContext, output_path: str) -> str:
        current = video_path
        tmp_dir = os.path.dirname(output_path)
        try:
            if ctx.voice_enhance:
                import subprocess
                import tempfile
                from ..engines.audio import replace_audio, voice_enhance_filter

                with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as tmp:
                    orig_audio = tmp.name
                enhanced = os.path.join(tmp_dir, "ve.aac")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", current, "-vn", "-c:a", "aac", "-b:a", "192k", orig_audio],
                        capture_output=True, check=True,
                    )
                    voice_enhance_filter(orig_audio, enhanced, log_fn=ctx.log)
                    ve_out = os.path.join(tmp_dir, "ve.mp4")
                    current = replace_audio(current, enhanced, ve_out, log_fn=ctx.log)
                finally:
                    for p in (orig_audio, enhanced):
                        try:
                            os.remove(p)
                        except OSError:
                            pass

            if ctx.add_bgm and ctx.bgm_folder:
                from ..engines.audio import mix_bgm
                current = mix_bgm(current, ctx.bgm_folder, output_path, log_fn=ctx.log)
            elif current != output_path:
                shutil.copy2(current, output_path)
                current = output_path
        except Exception as e:
            ctx.log(f"[SHORTS] Аудио-пост: {e}")
            if current != output_path:
                try:
                    shutil.copy2(current, output_path)
                    current = output_path
                except OSError:
                    pass
        return current

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[SHORTS] Нарезка Shorts...")
        clips = ctx.analysis.get("clips_for_shorts", [])
        if not clips:
            ctx.log("[SHORTS] Нет клипов, пропускаем")
            ctx.progress = 90.0
            return ctx

        output_dir = os.path.join(ctx.output_folder, "_tmp", "shorts")
        os.makedirs(output_dir, exist_ok=True)

        for i, clip in enumerate(clips, 1):
            short_path = self._create_short(ctx, clip, i, output_dir)
            if short_path:
                ctx.shorts.append(short_path)
                ctx.log(f"[SHORTS] short_{i:03d}: {short_path}")
            else:
                ctx.log(f"[SHORTS] Клип {i} пропущен")

        ctx.progress = 90.0
        return ctx

    def _write_metadata(self, clip: dict, ctx: PipelineContext, path: str) -> None:
        hook = clip.get("hook") or (ctx.analysis.get("hook") or {}).get("text", "")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Серия: {ctx.series_name or '—'}\n")
            f.write(f"Хук: {hook}\n")
            f.write(f"Заголовок: {clip.get('title', '—')}\n")
            f.write(f"Описание: {clip.get('description', '—')}\n")
            f.write(f"Хештеги: {clip.get('hashtags', '—')}\n")
            f.write(f"Тайминг: {clip.get('start', 0):.1f} — {clip.get('end', 0):.1f}\n")
