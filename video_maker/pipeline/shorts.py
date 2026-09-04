# VideoMaker FIX | 2026.09.03-r26 | 2026-09-03
# CHANGED: shorts → series_dir/shorts (no double nest / temp+copy)
# PREV: 2026.09.03-r24
# REPLACE: video_maker/pipeline/shorts.py

"""ShortsCutter — cut из final_9x16 (stream copy) → burn только Hook+CTA → BGM reuse."""
from __future__ import annotations

import logging
import os
import shutil

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)

REQUIRED_TAGS = ("#ТочкаНаблюдения", "#ТайныйКризисЧеловечества")


def _ensure_hashtags(raw: str) -> str:
    tags: list[str] = []
    for part in (raw or "").replace(",", " ").split():
        p = part.strip()
        if not p:
            continue
        if not p.startswith("#"):
            p = "#" + p.lstrip("#")
        if p not in tags:
            tags.append(p)
    for req in REQUIRED_TAGS:
        if req not in tags:
            tags.insert(0, req)
    return " ".join(tags[:12])


class ShortsCutter(Stage):
    def name(self) -> str:
        return "Shorts"

    def _create_short(
        self, ctx: PipelineContext, clip: dict, index: int, output_dir: str
    ) -> str | None:
        from ..engines.audio import probe_duration
        from ..engines.video import cut_segment
        from ..engines.subtitles import burn_subtitles

        start = float(clip.get("start", 0))
        end = float(clip.get("end", 0))
        duration = end - start

        # r20: cut из готового final_9x16 (один encode vertical).
        # master_vertical после one_encode == final (с ASS long). Stream copy.
        src = getattr(ctx, "final_vertical", "") or ""
        if not src or not os.path.isfile(src):
            src = getattr(ctx, "master_vertical", "") or ""
        if not src or not os.path.isfile(src):
            ctx.log(f"[SHORTS] Клип {index}: нет вертикального исходника (final/master)")
            return None

        video_duration = probe_duration(src)
        if start >= video_duration or duration <= 0:
            ctx.log(f"[SHORTS] Клип {index}: невалидный тайминг, пропускаем")
            return None
        if end > video_duration:
            end = video_duration
            duration = end - start
            clip = {**clip, "end": end}

        clip = dict(clip)
        clip["hashtags"] = _ensure_hashtags(str(clip.get("hashtags") or ""))
        desc = str(clip.get("description") or "").strip()
        if "Точка наблюдения" not in desc and "точке наблюдения" not in desc.lower():
            desc = (desc + " " if desc else "") + (
                "Полная серия «Тайный кризис человечества» на канале «Точка наблюдения»."
            )
        clip["description"] = desc.strip()

        short_dir = os.path.join(output_dir, f"short_{index:03d}")
        os.makedirs(short_dir, exist_ok=True)
        final_path = os.path.join(short_dir, f"short_{index:03d}.mp4")
        cut_path = os.path.join(short_dir, f"short_{index:03d}_cut.mp4")

        ctx.log(
            f"[SHORTS] #{index} copy from final_9x16: cut {start:.1f}-{end:.1f}s "
            f"(stream copy) → burn only Hook+CTA"
        )
        cut_segment(
            video_path=src,
            start=start,
            duration=duration,
            output_path=cut_path,
            log_fn=ctx.log,
        )
        current = cut_path

        # intro/middle/outro shorts (если включены)
        if getattr(ctx, "s_enable_intro", False) or getattr(ctx, "s_enable_middle", False) or getattr(ctx, "s_enable_outro", False):
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=bool(getattr(ctx, "s_enable_intro", False)),
                enable_middle=bool(getattr(ctx, "s_enable_middle", False)),
                enable_outro=bool(getattr(ctx, "s_enable_outro", False)),
                output_dir=short_dir,
                log_fn=ctx.log,
                analysis=ctx.analysis,
                explicit_intro=(getattr(ctx, "s_intro_path", "") or ""),
                explicit_middle=(getattr(ctx, "s_mid_path", "") or ""),
                explicit_outro=(getattr(ctx, "s_outro_path", "") or ""),
                intro_duration=float(getattr(ctx, "s_intro_duration", 3) or 3),
                middle_duration=float(getattr(ctx, "s_mid_duration", 1) or 1),
                outro_duration=float(getattr(ctx, "s_outro_duration", 3) or 3),
            )

        # r24: shorts = только Hook + CTA; skip если пересечение с long Hook/CTA зоной
        # (final_9x16 уже содержит long ASS → двойное наложение).
        s_hooks = bool(getattr(ctx, "s_enable_hooks", True))
        s_subs = bool(getattr(ctx, "s_enable_subtitles", False))
        s_strong = bool(getattr(ctx, "s_enable_strong_words", False))

        # Зоны long vertical Hook / CTA = то, что реально прожжено в final_9x16
        # (long always forces Hook 0..HOOK_DUR and CTA last CTA_DUR sec).
        try:
            from ..engines.subtitles import HOOK_DUR, CTA_DUR
        except Exception:
            HOOK_DUR, CTA_DUR = 4.5, 7.0
        long_hook_end = float(HOOK_DUR)
        full_dur = float(video_duration or 0)
        if full_dur > CTA_DUR:
            long_cta_start = full_dur - float(CTA_DUR)
        else:
            long_cta_start = max(0.0, full_dur * 0.5)

        # short_start/end уже в переменных start/end
        skip_hook = bool(s_hooks and start < long_hook_end)
        skip_cta = bool(s_hooks and end > long_cta_start)
        clip = dict(clip)
        if skip_hook:
            clip["_skip_hook"] = True
            ctx.log(
                f"[SHORTS] #{index} skip Hook: short_start={start:.2f} < "
                f"long_hook_end={long_hook_end:.2f} (уже в final_9x16)"
            )
        if skip_cta:
            clip["_skip_cta"] = True
            ctx.log(
                f"[SHORTS] #{index} skip CTA: short_end={end:.2f} > "
                f"long_cta_start={long_cta_start:.2f} (уже в final_9x16)"
            )

        if s_hooks or s_subs or s_strong:
            subtitled = os.path.join(short_dir, f"short_{index:03d}_subs.mp4")
            ctx.log(
                f"[SHORTS] #{index} burn only: hooks={s_hooks} subs={s_subs} strong={s_strong} "
                f"skip_hook={skip_hook} skip_cta={skip_cta}"
            )
            tr = ctx.transcription if (s_subs or s_strong) else None
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                clip=clip,
                enable_hooks=s_hooks,
                enable_subtitles=s_subs,
                enable_strong_words=s_strong,
                output_path=subtitled,
                log_fn=ctx.log,
                caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                transcription=tr,
                use_aisie=True,
            )

        # BGM: не мешать повторно — cut из vertical уже несёт смешанную дорожку
        if ctx.add_bgm and ctx.bgm_folder and not getattr(ctx, "bgm_mixed", False):
            from ..engines.audio import mix_bgm
            bgm_out = os.path.join(short_dir, f"short_{index:03d}_bgm.mp4")
            try:
                current = mix_bgm(
                    current, ctx.bgm_folder, bgm_out, log_fn=ctx.log, loudnorm=True
                )
                ctx.log(f"[SHORTS] #{index} BGM mixed (long had no BGM)")
            except Exception as e:
                ctx.log(f"[SHORTS] BGM skip: {e}")
        elif getattr(ctx, "bgm_mixed", False):
            ctx.log(f"[SHORTS] #{index} BGM reused from long (no re-mix)")

        if os.path.abspath(current) != os.path.abspath(final_path):
            shutil.copy2(current, final_path)

        self._write_sidecar_files(clip, short_dir, index)
        self._write_metadata(
            clip, ctx, os.path.join(short_dir, f"short_{index:03d}_meta.txt")
        )
        ctx.log(
            f"[SHORTS] #{index} готов → {final_path} | hashtags={clip.get('hashtags', '')}"
        )
        return final_path

    def _write_sidecar_files(self, clip: dict, short_dir: str, index: int) -> None:
        base = f"short_{index:03d}"
        fields = [
            ("title", str(clip.get("title") or "").strip()),
            ("description", str(clip.get("description") or "").strip()),
            ("hook", str(clip.get("hook") or "").strip()),
            ("cta", str(clip.get("cta") or "").strip()),
            ("hashtags", _ensure_hashtags(str(clip.get("hashtags") or ""))),
        ]
        desc = str(clip.get("description") or "").strip()
        tags = _ensure_hashtags(str(clip.get("hashtags") or ""))
        upload_block = desc
        if tags:
            upload_block = (desc + "\n\n" if desc else "") + tags
        fields.append(("upload", upload_block))
        for kind, content in fields:
            p = os.path.join(short_dir, f"{base}_{kind}.txt")
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write((content or "").strip() + ("\n" if (content or "").strip() else ""))
            except OSError as e:
                log.warning("[SHORTS] не записал %s: %s", p, e)

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[SHORTS] cut from final_9x16 (stream copy) + burn only Hook+CTA...")
        # Пишем сразу в финальную структуру series_dir/shorts (без лишнего copy в Finalize)
        from .finalize import resolve_series_dir
        series_dir, _ = resolve_series_dir(
            ctx.output_folder, ctx.series_name or "", ctx.audio_path or ""
        )
        output_dir = os.path.join(series_dir, "shorts")
        os.makedirs(output_dir, exist_ok=True)
        ctx.log(f"[SHORTS] series_dir={series_dir} → {output_dir}")

        clips = ctx.analysis.get("clips_for_shorts", []) if ctx.analysis else []
        if not clips:
            ctx.log("[SHORTS] Нет clips_for_shorts в analysis — пропуск")
            ctx.shorts = []
            return ctx

        results = []
        for i, clip in enumerate(clips, 1):
            if getattr(ctx, "cancel_event", None) is not None and ctx.cancel_event.is_set():
                break
            try:
                path = self._create_short(ctx, clip, i, output_dir)
                if path:
                    results.append(path)
            except Exception as e:
                ctx.log(f"[SHORTS] Ошибка клипа {i}: {e}")
                log.exception("Shorts clip %s failed", i)

        ctx.shorts = results
        # BGM+loudnorm уже в каждом short
        ctx.shorts_audio_normalized = bool(ctx.add_bgm and ctx.bgm_folder)
        ctx.log(f"[SHORTS] Готово: {len(results)} шт → {output_dir}")
        ctx.progress = 90.0
        return ctx

    def _write_metadata(self, clip: dict, ctx: PipelineContext, path: str) -> None:
        hook = clip.get("hook") or ""
        if isinstance(hook, dict):
            hook = hook.get("text", "")
        hashtags = _ensure_hashtags(str(clip.get("hashtags") or ""))
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Серия: {ctx.series_name or '—'}\n")
            f.write(f"Хук (on-screen начало): {hook}\n")
            f.write(f"CTA (on-screen конец): {clip.get('cta', '—')}\n")
            f.write(f"Заголовок: {clip.get('title', '—')}\n")
            f.write(f"Описание: {clip.get('description', '—')}\n")
            f.write(f"Хештеги: {hashtags}\n")
            f.write(
                f"Тайминг: {float(clip.get('start', 0)):.1f} — "
                f"{float(clip.get('end', 0)):.1f}\n"
            )
            f.write(f"hook_timing: {clip.get('hook_start')} — {clip.get('hook_end')}\n")
            f.write(f"cta_timing: {clip.get('cta_start')} — {clip.get('cta_end')}\n")
