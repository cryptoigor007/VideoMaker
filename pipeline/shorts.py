# VideoMaker FIX | 2026.09.01-r12 | 2026-09-01
# CHANGED: Shorts IMO path=enable; удаление лишних short_XXX
# REPLACE: video_maker/pipeline/shorts.py

"""ShortsCutter — отдельный продукт: cut из geometry → burn (hooks/CTA shorts) → BGM."""
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

        # Только geometry (без vertical burn) — свой burn с clip/hooks/CTA shorts
        src = getattr(ctx, "master_vertical", "") or ""
        if not src or not os.path.isfile(src):
            # fallback: final_vertical (хуже — уже чужие хуки)
            src = getattr(ctx, "final_vertical", "") or ""
            if src and os.path.isfile(src):
                ctx.log(
                    f"[SHORTS] #{index}: нет master_vertical, fallback final_9x16 "
                    "(хуки vertical, не shorts)"
                )
            else:
                ctx.log(f"[SHORTS] Клип {index}: нет вертикального исходника")
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
        # Resume: уже готовый short — не пересобирать
        if os.path.isfile(final_path) and os.path.getsize(final_path) > 10_000:
            ctx.log(f"[SHORTS] #{index} уже есть → пропуск {final_path}")
            return final_path


        ctx.log(
            f"[SHORTS] #{index} отдельный рендер: cut {start:.1f}-{end:.1f}s "
            f"→ burn (hooks/CTA shorts) → BGM"
        )
        cut_segment(
            video_path=src,
            start=start,
            duration=duration,
            output_path=cut_path,
            log_fn=ctx.log,
        )
        current = cut_path

        # intro/middle/outro shorts (explicit → add_intro_outro_mid)
        # Путь с вкладки = включить (даже если галочка серая)
        def _sp(*names):
            for n in names:
                v = (getattr(ctx, n, None) or "").strip()
                if v:
                    return v
            return ""
        s_intro = _sp("s_intro_path", "h_intro_path", "v_intro_path")
        s_mid = _sp("s_mid_path", "h_mid_path", "v_mid_path")
        s_outro = _sp("s_outro_path", "h_outro_path", "v_outro_path")
        en_i = bool(getattr(ctx, "s_enable_intro", False)) or bool(s_intro)
        en_m = bool(getattr(ctx, "s_enable_middle", False)) or bool(s_mid)
        en_o = bool(getattr(ctx, "s_enable_outro", False)) or bool(s_outro)
        ctx.log(
            f"[IMO/S] #{index} enable intro={en_i} mid={en_m} outro={en_o} | outro={s_outro or '—'}"
        )
        if en_i or en_m or en_o:
            from ..engines.video import add_intro_outro_mid
            current = add_intro_outro_mid(
                current,
                ctx.intro_middle_outro_folder,
                enable_intro=en_i,
                enable_middle=en_m,
                enable_outro=en_o,
                output_dir=short_dir,
                log_fn=ctx.log,
                analysis=ctx.analysis,
                explicit_intro=s_intro,
                explicit_middle=s_mid,
                explicit_outro=s_outro,
                intro_duration=float(getattr(ctx, "s_intro_duration", 3) or 3),
                middle_duration=float(getattr(ctx, "s_mid_duration", 1) or 1),
                outro_duration=float(getattr(ctx, "s_outro_duration", 3) or 3),
            )

        # burn: clip= тайминги/хуки/CTA для shorts
        if (
            getattr(ctx, "s_enable_hooks", True)
            or getattr(ctx, "s_enable_subtitles", True)
            or getattr(ctx, "s_enable_strong_words", True)
        ):
            subtitled = os.path.join(short_dir, f"short_{index:03d}_subs.mp4")
            current = burn_subtitles(
                video_path=current,
                analysis=ctx.analysis,
                clip=clip,
                enable_hooks=bool(getattr(ctx, "s_enable_hooks", True)),
                enable_subtitles=bool(getattr(ctx, "s_enable_subtitles", True)),
                enable_strong_words=bool(getattr(ctx, "s_enable_strong_words", True)),
                output_path=subtitled,
                log_fn=ctx.log,
                caption_style=getattr(ctx, "caption_style", "auto_aisie"),
                hook_style=getattr(ctx, "hook_style", "auto_aisie"),
                transcription=ctx.transcription,
                use_aisie=True,
            )

        if ctx.add_bgm and ctx.bgm_folder:
            from ..engines.audio import mix_bgm
            bgm_out = os.path.join(short_dir, f"short_{index:03d}_bgm.mp4")
            try:
                current = mix_bgm(
                    current, ctx.bgm_folder, bgm_out, log_fn=ctx.log, loudnorm=True
                )
            except Exception as e:
                ctx.log(f"[SHORTS] BGM skip: {e}")

        if os.path.abspath(current) != os.path.abspath(final_path):
            shutil.copy2(current, final_path)

        # Убрать промежуточные файлы — в папке только финальное видео + данные ЭТОГО шорта
        for name in os.listdir(short_dir):
            p = os.path.join(short_dir, name)
            if not os.path.isfile(p):
                continue
            if os.path.abspath(p) == os.path.abspath(final_path):
                continue
            # оставляем только short_XXX.mp4 и *.txt sidecars
            if name.endswith(".txt"):
                continue
            try:
                os.remove(p)
                ctx.log(f"[SHORTS] cleanup intermediate: {name}")
            except OSError:
                pass

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
        ctx.log("[SHORTS] Отдельный рендер Shorts (не copy из Vertical)...")
        output_dir = os.path.join(ctx.output_folder, "shorts")
        os.makedirs(output_dir, exist_ok=True)

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
        ctx.shorts_audio_normalized = bool(ctx.add_bgm and ctx.bgm_folder)
        # Убрать лишние short_XXX от старых прогонов (индекс > текущего числа клипов)
        n = len(clips)
        try:
            for name in list(os.listdir(output_dir)):
                if not name.startswith("short_"):
                    continue
                # short_001 → 1
                try:
                    num = int(name.split("_")[1][:3])
                except Exception:
                    continue
                if num > n:
                    import shutil
                    p = os.path.join(output_dir, name)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                        ctx.log(f"[SHORTS] удалена старая папка {name}")
        except OSError as e:
            ctx.log(f"[SHORTS] cleanup dirs: {e}")
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
