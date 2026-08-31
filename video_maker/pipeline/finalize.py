"""FinalizeStage — {output}/{audio_name}/wide|vertical|shorts/..."""
from __future__ import annotations

import logging
import os
import re
import shutil

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    name = (name or "output").strip()
    name = os.path.splitext(os.path.basename(name))[0]
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip() or "output"
    return name[:120]


class FinalizeStage(Stage):
    def name(self) -> str:
        return "Финализация"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[ФИНАЛ] Копирование результатов...")
        base_name = _safe_name(ctx.series_name) if ctx.series_name else _safe_name(ctx.audio_path)
        root = os.path.join(ctx.output_folder, base_name)
        wide_dir = os.path.join(root, "wide")
        vert_dir = os.path.join(root, "vertical")
        shorts_dir = os.path.join(root, "shorts")
        os.makedirs(wide_dir, exist_ok=True)
        os.makedirs(vert_dir, exist_ok=True)
        os.makedirs(shorts_dir, exist_ok=True)

        from ..engines.audio import apply_loudnorm

        def _place_video(src: str, dst: str, already_norm: bool, label: str) -> None:
            """Copy if audio already loudnormed in BGM; else two-pass loudnorm."""
            if not src or not os.path.exists(src):
                return
            if already_norm:
                ctx.log(f"[ФИНАЛ] {label}: аудио уже ≈ target → copy (без LUFS-measure)")
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                return
            ln = dst + ".loudnorm.mp4"
            ctx.log(f"[ФИНАЛ] loudnorm → {label}")
            apply_loudnorm(src, ln, target_lufs=ctx.target_lufs, log_fn=ctx.log)
            shutil.move(ln, dst)
            self._measure(dst, ctx)

        if ctx.final_horizontal and os.path.exists(ctx.final_horizontal):
            _place_video(
                ctx.final_horizontal,
                os.path.join(wide_dir, "final_16x9.mp4"),
                bool(getattr(ctx, "horizontal_audio_normalized", False)),
                "wide/final_16x9.mp4",
            )
        if ctx.master_horizontal and os.path.exists(ctx.master_horizontal):
            shutil.copy2(ctx.master_horizontal, os.path.join(wide_dir, "master_16x9.mp4"))

        if ctx.final_vertical and os.path.exists(ctx.final_vertical):
            _place_video(
                ctx.final_vertical,
                os.path.join(vert_dir, "final_9x16.mp4"),
                bool(getattr(ctx, "vertical_audio_normalized", False)),
                "vertical/final_9x16.mp4",
            )
        if ctx.master_vertical and os.path.exists(ctx.master_vertical):
            shutil.copy2(ctx.master_vertical, os.path.join(vert_dir, "master_9x16.mp4"))

        clips = ctx.analysis.get("clips_for_shorts", []) if ctx.analysis else []
        shorts_norm = bool(getattr(ctx, "shorts_audio_normalized", False))
        for i, short_path in enumerate(ctx.shorts, 1):
            if not os.path.exists(short_path):
                continue
            sdir = os.path.join(shorts_dir, f"short_{i:03d}")
            os.makedirs(sdir, exist_ok=True)
            final_path = os.path.join(sdir, f"short_{i:03d}.mp4")
            _place_video(short_path, final_path, shorts_norm, f"shorts/short_{i:03d}/")
            clip = clips[i - 1] if i - 1 < len(clips) else {}
            self._write_packaging_files(
                dir_path=sdir,
                base_name=f"short_{i:03d}",
                title=str(clip.get("title") or ""),
                description=str(clip.get("description") or ""),
                hook=str(clip.get("hook") or (ctx.analysis.get("hook") or {}).get("text") or ""),
                hashtags=str(clip.get("hashtags") or ""),
                log_fn=ctx.log,
            )

        # Packaging полного видео: одинаково в wide/ и vertical/
        pkg_title = str(ctx.analysis.get("package_title") or "")
        pkg_desc = str(ctx.analysis.get("package_description") or "")
        pkg_hook = str(ctx.analysis.get("package_hook") or (ctx.analysis.get("hook") or {}).get("text") or "")
        pkg_tags = str(ctx.analysis.get("package_hashtags") or "")
        for d in (wide_dir, vert_dir):
            self._write_packaging_files(
                dir_path=d,
                base_name=base_name,
                title=pkg_title,
                description=pkg_desc,
                hook=pkg_hook,
                hashtags=pkg_tags,
                log_fn=ctx.log,
            )

        self._write_root_meta(ctx, root)
        self._copy_covers(ctx, wide_dir, vert_dir)

        if not ctx.keep_temp_files:
            tmp = os.path.join(ctx.output_folder, "_tmp")
            if os.path.exists(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
                ctx.log("[ФИНАЛ] _tmp удалён")

        ctx.log(f"[ФИНАЛ] Готово → {root}")
        ctx.progress = 100.0
        return ctx

    @staticmethod
    def _write_packaging_files(
        dir_path: str,
        base_name: str,
        title: str,
        description: str,
        hook: str,
        hashtags: str,
        log_fn=None,
    ) -> None:
        """4 отдельных файла: {base}_title / _description / _hook / _hashtags."""
        _log = log_fn or (lambda *a, **k: None)
        safe = re.sub(r'[<>:"/\\|?*]', "_", (base_name or "output").strip()) or "output"
        mapping = {
            "title": (title or "").strip(),
            "description": (description or "").strip(),
            "hook": (hook or "").strip(),
            "hashtags": (hashtags or "").strip(),
        }
        for kind, content in mapping.items():
            path = os.path.join(dir_path, f"{safe}_{kind}.txt")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content + ("\n" if content else ""))
            except OSError as e:
                _log(f"[ФИНАЛ] packaging {kind}: {e}")
        _log(f"[ФИНАЛ] packaging → {dir_path}: {safe}_{{title,description,hook,hashtags}}.txt")

    def _write_root_meta(self, ctx: PipelineContext, root: str) -> None:
        clips = ctx.analysis.get("clips_for_shorts", [])
        lines = [
            f"series: {ctx.series_name or '—'}",
            f"audio: {ctx.audio_path}",
            f"package_title: {ctx.analysis.get('package_title') or '—'}",
            f"package_hook: {ctx.analysis.get('package_hook') or (ctx.analysis.get('hook') or {}).get('text', '—')}",
            f"package_hashtags: {ctx.analysis.get('package_hashtags') or '—'}",
            "",
            "=== Shorts ===",
        ]
        for i, clip in enumerate(clips, 1):
            lines += [
                f"short_{i:03d}:",
                f"  title: {clip.get('title', '—')}",
                f"  description: {clip.get('description', '—')}",
                f"  hashtags: {clip.get('hashtags', '—')}",
                f"  start: {clip.get('start', 0):.1f}",
                f"  end: {clip.get('end', 0):.1f}",
            ]
        try:
            with open(os.path.join(root, "info_metadata.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except OSError as e:
            ctx.log(f"[ФИНАЛ] root meta: {e}")

    def _copy_covers(self, ctx: PipelineContext, wide_dir: str, vert_dir: str) -> None:
        if ctx.cover_horizontal and os.path.exists(ctx.cover_horizontal):
            ext = os.path.splitext(ctx.cover_horizontal)[1] or ".jpg"
            shutil.copy2(ctx.cover_horizontal, os.path.join(wide_dir, f"cover_16x9{ext}"))
        if ctx.cover_vertical and os.path.exists(ctx.cover_vertical):
            ext = os.path.splitext(ctx.cover_vertical)[1] or ".jpg"
            shutil.copy2(ctx.cover_vertical, os.path.join(vert_dir, f"cover_9x16{ext}"))

    def _measure(self, video_path: str, ctx: PipelineContext) -> None:
        from ..engines.audio import judge_loudness, measure_loudness
        loudness = measure_loudness(video_path)
        if loudness:
            ctx.log(
                f"[LUFS] {os.path.basename(video_path)}: "
                f"{loudness['i_lufs']:.1f} LUFS — {judge_loudness(loudness['i_lufs'])}"
            )
