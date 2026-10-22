# VideoMaker FIX | 2026.09.16-r56-platform-bgm | 2026-09-16
# CHANGED:
#   r43-place: same filesystem → os.replace/move (мгновенно);
#              разные диски → shutil.copy2; лог move|copy.
# PREV: 2026.09.03-r26
# REPLACE: video_maker/pipeline/finalize.py
"""FinalizeStage — {series_dir}/{youtube|tiktok|...}/wide|vertical|shorts/."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil

from ..paths import resolve_existing_path
from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)


def _safe_name(name: str, *, strip_extension: bool = False) -> str:
    """Return one safe path component without losing dots in series names.

    Only audio-file fallbacks should strip the final extension. A user-selected
    series named ``Episode.v2`` must not collide with ``Episode``.
    """
    name = (name or "output").strip()
    # Treat both Unix and Windows separators as separators, regardless of host OS.
    name = os.path.basename(name.replace("\\", "/"))
    if strip_extension:
        name = os.path.splitext(name)[0]
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Dot path components can escape the intended output directory when joined.
    if not name or name in {".", ".."}:
        name = "output"
    return name[:120]


def resolve_series_dir(output_folder: str, series_name: str, audio_path: str = "") -> tuple[str, str]:
    """Единый series_dir без двойной вложенности.

    Если basename(output_folder) уже == series_name → output_folder и есть series_dir.
    Иначе series_dir = output_folder / series_name.
    Returns (series_dir, base_name).
    """
    base_name = _safe_name(series_name) if series_name else _safe_name(audio_path, strip_extension=True)
    out = os.path.normpath(output_folder or ".")
    if base_name and os.path.basename(out) == base_name:
        return out, base_name
    if base_name:
        return os.path.join(out, base_name), base_name
    return out, base_name or "output"


def _normalize_platform_ids(platforms: list[dict], log_fn=None) -> list[dict]:
    """Sanitize directory identifiers and skip duplicates before output begins."""
    _log = log_fn or (lambda message: None)
    normalized: list[dict] = []
    seen: set[str] = set()
    for platform in platforms:
        if not isinstance(platform, dict):
            _log(f"[ФИНАЛ][WARNING] invalid platform entry skipped: {type(platform).__name__}")
            continue
        raw_id = str(platform.get("id") or "youtube")
        safe_id = _safe_name(raw_id)
        if safe_id in seen:
            _log(f"[ФИНАЛ][WARNING] duplicate/sanitized platform id skipped: {raw_id!r} → {safe_id!r}")
            continue
        seen.add(safe_id)
        if safe_id != raw_id:
            _log(f"[ФИНАЛ][WARNING] platform id sanitized for output path: {raw_id!r} → {safe_id!r}")
        item = dict(platform)
        item["id"] = safe_id
        normalized.append(item)
    return normalized


def _prepare_platform_dirs(series_root: str, platform_id: str) -> tuple[str, str, str, str]:
    """Create platform directories without traversing a symlink below series root."""
    root_abs = os.path.abspath(series_root)
    root_real = os.path.realpath(root_abs)
    plat_root = os.path.join(root_abs, platform_id)
    wide_dir = os.path.join(plat_root, "wide")
    vert_dir = os.path.join(plat_root, "vertical")
    shorts_dir = os.path.join(plat_root, "shorts")
    for directory in (plat_root, wide_dir, vert_dir, shorts_dir):
        if os.path.islink(directory):
            raise RuntimeError(f"Refusing to write through output symlink: {directory}")
        os.makedirs(directory, exist_ok=True)
        try:
            if os.path.commonpath([root_real, os.path.realpath(directory)]) != root_real:
                raise RuntimeError(f"Output directory escaped series root: {directory}")
        except ValueError as exc:
            raise RuntimeError(f"Output directory is on another volume: {directory}") from exc
    return plat_root, wide_dir, vert_dir, shorts_dir


def _cleanup_series_staging(root: str, keep_temp_files: bool, log_fn=None) -> None:
    """Clean only this series' reserved staging directory, never shared output data."""
    _log = log_fn or (lambda message: None)
    if keep_temp_files:
        return
    series_root = os.path.abspath(root)
    if os.path.islink(series_root):
        _log("[ФИНАЛ][WARNING] series root is a symlink; cleanup skipped for safety")
        return
    tmp_parent = os.path.join(series_root, "_tmp")
    if os.path.islink(tmp_parent):
        _log("[ФИНАЛ][WARNING] _tmp is a symlink; cleanup skipped for safety")
        return
    staging_path = os.path.abspath(os.path.join(tmp_parent, "shorts_staging"))
    if os.path.islink(staging_path):
        _log("[ФИНАЛ][WARNING] staging directory is a symlink; cleanup skipped")
        return
    real_root = os.path.realpath(series_root)
    real_staging = os.path.realpath(staging_path)
    try:
        if os.path.commonpath([real_root, real_staging]) != real_root:
            _log("[ФИНАЛ][WARNING] staging path escaped series root; cleanup skipped")
            return
    except ValueError:
        _log("[ФИНАЛ][WARNING] staging path on another drive; cleanup skipped")
        return
    if os.path.isdir(staging_path) and not os.path.islink(staging_path):
        shutil.rmtree(staging_path, ignore_errors=True)
        _log(f"[ФИНАЛ] staging удалён: {staging_path}")
    # Remove the reserved parent only when empty. rmdir is intentional: it cannot
    # delete unrelated master files or a concurrent job's temp files.
    tmp_parent = os.path.dirname(staging_path)
    try:
        os.rmdir(tmp_parent)
        _log(f"[ФИНАЛ] пустая временная папка удалена: {tmp_parent}")
    except OSError:
        pass


def _cleanup_output_staging(output_folder: str, keep_temp_files: bool, log_fn=None) -> None:
    """Удалить служебную папку <вывод>/_tmp с промежуточными мастер-файлами.

    Раньше чистка трогала только staging шортов, а мастера, финалы и WAV-и
    narration навсегда оставались в корне вывода (сотни МБ на серию).
    Симлинки и выход за пределы папки вывода запрещены.
    """
    _log = log_fn or (lambda message: None)
    if keep_temp_files:
        return
    base = os.path.abspath(output_folder or ".")
    if not base or not os.path.isdir(base) or os.path.islink(base):
        if os.path.islink(base):
            _log("[ФИНАЛ][WARNING] папка вывода — симлинк; очистка _tmp пропущена")
        return
    tmp_dir = os.path.join(base, "_tmp")
    if os.path.islink(tmp_dir):
        _log("[ФИНАЛ][WARNING] output/_tmp — симлинк; очистка пропущена")
        return
    if not os.path.isdir(tmp_dir):
        return
    real_base = os.path.realpath(base)
    try:
        if os.path.commonpath([real_base, os.path.realpath(tmp_dir)]) != real_base:
            _log("[ФИНАЛ][WARNING] output/_tmp вне папки вывода; очистка пропущена")
            return
    except ValueError:
        _log("[ФИНАЛ][WARNING] output/_tmp на другом диске; очистка пропущена")
        return
    shutil.rmtree(tmp_dir, ignore_errors=True)
    _log(f"[ФИНАЛ] промежуточные файлы удалены: {tmp_dir}")


def _place_file(src: str, dst: str, log_fn=None, prefer_copy: bool = False) -> str:
    """Переложить src → dst.

    По умолчанию: os.replace (один раздел — мгновенно);
    EXDEV / ошибка replace → copy2.
    prefer_copy=True (keep_temp_files): всегда copy — src в _tmp остаётся.
    Returns: 'skip' | 'move' | 'copy'
    """
    if not src or not os.path.isfile(src):
        return "skip"
    src_a = os.path.abspath(src)
    dst_a = os.path.abspath(dst)
    if src_a == dst_a:
        return "skip"
    os.makedirs(os.path.dirname(dst_a) or ".", exist_ok=True)
    if prefer_copy:
        shutil.copy2(src_a, dst_a)
        return "copy"
    try:
        os.replace(src_a, dst_a)
        return "move"
    except OSError:
        shutil.copy2(src_a, dst_a)
        return "copy"


class FinalizeStage(Stage):
    def name(self) -> str:
        return "Финализация"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[ФИНАЛ] Копирование результатов...")
        resolved_output = resolve_existing_path(ctx.output_folder, kind="dir")
        if resolved_output:
            if resolved_output != ctx.output_folder:
                ctx.log(f"[ФИНАЛ] путь вывода нормализован: {resolved_output}")
            ctx.output_folder = resolved_output
        resolved_audio = resolve_existing_path(ctx.audio_path, kind="file")
        if resolved_audio:
            ctx.audio_path = resolved_audio
        root, base_name = resolve_series_dir(
            ctx.output_folder, ctx.series_name or "", ctx.audio_path or ""
        )
        ctx.log(f"[ФИНАЛ] series_dir={root} (output={ctx.output_folder}, series={base_name})")

        # One stable identity for all derivatives of this source episode; never use
        # final_16x9.mp4/short_001.mp4 as the music seed.
        source_identity = os.path.abspath(ctx.audio_path or "")
        try:
            source_stat = os.stat(source_identity)
            source_identity = f"{source_identity}|{source_stat.st_size}|{source_stat.st_mtime_ns}"
        except OSError:
            pass
        episode_bgm_key = hashlib.sha256(
            f"{source_identity}|{ctx.series_name or base_name}".encode("utf-8")
        ).hexdigest()
        bgm_report = {
            "version": 1,
            "series": ctx.series_name or base_name,
            "source_audio": ctx.audio_path or "",
            "items": [],
        }

        platforms = list(getattr(ctx, "bgm_platforms", None) or [])
        if not platforms:
            # fallback: один «youtube» без отдельного BGM
            platforms = [{"id": "youtube", "label": "YouTube", "folder": ""}]
        # Keep the canonical YouTube deliverables first so other platform variants
        # can refer to them if their actual media output is byte-identical.
        platforms.sort(key=lambda item: str(item.get("id") or "youtube") != "youtube")
        platforms = _normalize_platform_ids(platforms, ctx.log)
        canonical_pid = str(platforms[0].get("id") or "youtube")

        prefer_copy = bool(getattr(ctx, "keep_temp_files", False))
        clips = ctx.analysis.get("clips_for_shorts", []) if ctx.analysis else []

        def _cleanup_intermediates() -> None:
            """Clean only this series' reserved staging path; preserve shared/user files."""
            _cleanup_series_staging(root, prefer_copy, ctx.log)
            _cleanup_output_staging(ctx.output_folder, prefer_copy, ctx.log)

        # Packaging texts (shared)
        pkg_title = str(ctx.analysis.get("package_title") or "")
        pkg_desc = str(ctx.analysis.get("package_description") or "")
        try:
            from ..engines.analysis import _ensure_playlist_footer
            pkg_desc = _ensure_playlist_footer(
                pkg_desc,
                str(ctx.analysis.get("package_hashtags") or ""),
                packaging_opts=getattr(ctx, "packaging_opts", None) or {},
            )
        except Exception:
            pass
        pkg_hook = str(
            ctx.analysis.get("package_hook")
            or (ctx.analysis.get("hook") or {}).get("text")
            or ""
        )
        pkg_tags = str(ctx.analysis.get("package_hashtags") or "")

        from ..engines.audio import mix_bgm, pick_bgm_track
        from ..engines.upload_readiness import evaluate_upload_readiness, find_missing_short_indices

        # Avoid expensive duplicate Shorts renders when the same source clip and
        # exact BGM track/settings would produce the same publication asset.
        # Different tracks remain distinct valid platform variants.
        delivered_short_variants: dict[str, str] = {}

        # Hash each physical file once per finalization pass. Before this cache,
        # the same staged MP4 was read from disk again for every platform; with
        # many Shorts/platforms this could mean dozens of full-file reads per clip.
        # The digest itself is content-based so byte-identical BGM tracks copied into
        # platform-specific folders still count as the same output variant.
        file_sha256_cache: dict[tuple[str, int, int], str] = {}

        def _file_sha256(path: str) -> str:
            real_path = os.path.realpath(path)
            stat = os.stat(real_path)
            cache_key = (real_path, int(stat.st_size), int(stat.st_mtime_ns))
            cached = file_sha256_cache.get(cache_key)
            if cached is not None:
                return cached
            digest = hashlib.sha256()
            with open(real_path, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            result = digest.hexdigest()
            file_sha256_cache[cache_key] = result
            return result

        for plat in platforms:
            pid = str(plat.get("id") or "youtube")
            label = str(plat.get("label") or pid)
            folder = str(plat.get("folder") or "").strip()
            try:
                plat_root, wide_dir, vert_dir, shorts_dir = _prepare_platform_dirs(root, pid)
            except Exception:
                _cleanup_intermediates()
                raise
            ctx.log(
                f"[ФИНАЛ] {label} → {plat_root}"
                + (f" | BGM={folder}" if folder else " | BGM=нет (только голос)")
            )

            def _deliver(src: str, dst: str, tag: str, *, is_short: bool = False) -> dict:
                bgm_requested = bool(plat.get("bgm_enabled", bool(folder)))
                record = {
                    "platform": label,
                    "asset": tag,
                    "destination": dst,
                    "bgm_folder": folder,
                    "bgm_requested": bgm_requested,
                    "bgm_folder_configured": bool(plat.get("bgm_folder_configured", bool(folder))),
                    "bgm_available": bool(plat.get("bgm_available", bool(folder))),
                }
                if not src or not os.path.isfile(src):
                    record.update({"status": "source_missing", "error": "staging source file is missing"})
                    bgm_report["items"].append(record)
                    ctx.log(f"[ФИНАЛ][ERROR] {label}/{tag}: source file missing: {src!r}")
                    return record
                if bgm_requested and folder:
                    try:
                        # Unique per finalized asset: the full video, vertical copy,
                        # and each Short must not all inherit one episode-wide track.
                        asset_bgm_key = f"{episode_bgm_key}|platform={pid}|asset={tag}"
                        selected_track = pick_bgm_track(folder, seed_key=asset_bgm_key)
                        variant_key = ""
                        if is_short:
                            # Identity must describe the actual media, not file paths:
                            # users often copy the same BGM track into separate platform
                            # folders. Path-based identity made those identical variants
                            # render and save repeatedly despite byte-identical soundtracks.
                            variant_identity = (
                                f"source_sha256={_file_sha256(src)}|"
                                f"bgm_sha256={_file_sha256(selected_track)}|"
                                f"lufs={getattr(ctx, 'target_lufs', -14.0) or -14.0}|"
                                "mix=duck+loudnorm-v1|audio=aac-192k-48k|video=copy"
                            )
                            variant_key = hashlib.sha256(variant_identity.encode("utf-8")).hexdigest()
                            canonical = delivered_short_variants.get(variant_key)
                            if canonical:
                                record.update({
                                    "status": "deduplicated",
                                    "track": os.path.basename(selected_track),
                                    "canonical_destination": canonical,
                                    "error": "identical source Short and BGM track already finalized",
                                })
                                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                                _place_file(canonical, dst, log_fn=ctx.log, prefer_copy=True)
                                bgm_report["items"].append(record)
                                ctx.log(
                                    f"[SHORTS][DEDUP] {label}/{tag}: media совпадает с {canonical}; "
                                    "копия поставлена без повторного микса"
                                )
                                return record
                        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                        mixed_path = mix_bgm(
                            src, folder, dst,
                            log_fn=ctx.log,
                            loudnorm=True,
                            target_lufs=getattr(ctx, "target_lufs", -14.0) or -14.0,
                            seed_key=asset_bgm_key,
                        )
                        if (
                            os.path.abspath(mixed_path) != os.path.abspath(dst)
                            or not os.path.isfile(dst)
                            or os.path.getsize(dst) < 1000
                        ):
                            raise RuntimeError("BGM mixer did not produce the expected destination file")
                        record.update({"status": "mixed", "track": os.path.basename(selected_track)})
                        if is_short and variant_key:
                            delivered_short_variants[variant_key] = dst
                        ctx.log(f"[ФИНАЛ] {label}/{tag}: BGM mix OK | track={os.path.basename(selected_track)}")
                    except Exception as e:
                        record.update({"status": "mix_failed_no_final_file", "error": str(e)})
                        # Do not put voice-only media at a destination whose filename
                        # looks publication-ready. Remove any stale prior output if possible.
                        try:
                            if os.path.isfile(dst):
                                os.remove(dst)
                        except OSError as cleanup_error:
                            ctx.log(f"[BGM][WARNING] could not remove stale final target {dst}: {cleanup_error}")
                        ctx.log(
                            f"[BGM][ERROR] {label}/{tag}: MIX FAILED; no voice-only fallback is placed "
                            f"at the final upload path. DO NOT PUBLISH this item. Error: {e}"
                        )
                else:
                    if bgm_requested:
                        record.update({
                            "status": "bgm_unavailable_no_final_file",
                            "error": "BGM is enabled, but the selected source has no usable audio tracks",
                        })
                        try:
                            if os.path.isfile(dst):
                                os.remove(dst)
                        except OSError as cleanup_error:
                            ctx.log(f"[BGM][WARNING] could not remove stale final target {dst}: {cleanup_error}")
                        ctx.log(
                            f"[BGM][ERROR] {label}/{tag}: BGM is enabled but no usable track was found; "
                            "no voice-only fallback is placed at the final upload path."
                        )
                    else:
                        if is_short:
                            variant_key = hashlib.sha256(f"{_file_sha256(src)}|voice-only".encode("utf-8")).hexdigest()
                            canonical = delivered_short_variants.get(variant_key)
                            if canonical:
                                record.update({
                                    "status": "deduplicated",
                                    "canonical_destination": canonical,
                                    "error": "identical voice-only Short already finalized",
                                })
                                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                                _place_file(canonical, dst, log_fn=ctx.log, prefer_copy=True)
                                bgm_report["items"].append(record)
                                ctx.log(
                                    f"[SHORTS][DEDUP] {label}/{tag}: source идентичен {canonical}; "
                                    "копия поставлена без повторного сохранения"
                                )
                                return record
                        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                        record.update({"status": "voice_only_no_bgm_configured"})
                        ctx.log(f"[ФИНАЛ] {label}/{tag}: BGM intentionally disabled")
                        _place_file(src, dst, log_fn=ctx.log, prefer_copy=True)
                        if is_short:
                            delivered_short_variants[variant_key] = dst
                bgm_report["items"].append(record)
                return record

            # wide / vertical
            _deliver(
                getattr(ctx, "final_horizontal", "") or "",
                os.path.join(wide_dir, "final_16x9.mp4"),
                "wide/final_16x9",
            )
            if pid == canonical_pid and ctx.master_horizontal and os.path.isfile(ctx.master_horizontal):
                # Мастер — промежуточный файл: держим его в каноничной (первой)
                # папке площадки, чтобы не плодить идентичные копии по платформам.
                _place_file(
                    ctx.master_horizontal,
                    os.path.join(wide_dir, "master_16x9.mp4"),
                    log_fn=ctx.log,
                    prefer_copy=(prefer_copy or len(platforms) > 1),
                )
            if ctx.final_vertical and os.path.isfile(ctx.final_vertical):
                _deliver(
                    ctx.final_vertical,
                    os.path.join(vert_dir, "final_9x16.mp4"),
                    "vertical/final_9x16",
                )
            if pid == canonical_pid and ctx.master_vertical and os.path.isfile(ctx.master_vertical):
                # См. горизонтальный мастер выше: одна каноничная копия.
                _place_file(
                    ctx.master_vertical,
                    os.path.join(vert_dir, "master_9x16.mp4"),
                    log_fn=ctx.log,
                    prefer_copy=(prefer_copy or len(platforms) > 1),
                )

            # shorts; retain the source clip index when an earlier render failed.
            for i, short_path in enumerate(ctx.shorts, 1):
                if not short_path or not os.path.isfile(short_path):
                    continue
                short_idx = int(ctx.short_indices[i - 1]) if i - 1 < len(getattr(ctx, "short_indices", [])) else i
                sdir = os.path.join(shorts_dir, f"short_{short_idx:03d}")
                dst = os.path.join(sdir, f"short_{short_idx:03d}.mp4")
                _deliver(short_path, dst, f"shorts/short_{short_idx:03d}", is_short=True)
                os.makedirs(sdir, exist_ok=True)
                # cover and metadata sidecars match the original clip index.
                src_dir = os.path.dirname(short_path)
                for name in (
                    f"short_{short_idx:03d}_cover.jpg",
                    f"short_{short_idx:03d}_hook.txt",
                    f"short_{short_idx:03d}_title.txt",
                    f"short_{short_idx:03d}_description.txt",
                    f"short_{short_idx:03d}_hashtags.txt",
                    f"short_{short_idx:03d}_meta.txt",
                ):
                    sp = os.path.join(src_dir, name)
                    if os.path.isfile(sp):
                        try:
                            shutil.copy2(sp, os.path.join(sdir, name))
                        except OSError:
                            pass
                clip = clips[short_idx - 1] if 0 <= short_idx - 1 < len(clips) else {}
                self._write_packaging_files(
                    dir_path=sdir,
                    base_name=f"short_{short_idx:03d}",
                    title=str(clip.get("title") or ""),
                    description=str(clip.get("description") or ""),
                    hook=str(
                        clip.get("hook")
                        or (ctx.analysis.get("hook") or {}).get("text")
                        or ""
                    ),
                    hashtags=str(clip.get("hashtags") or ""),
                    log_fn=ctx.log,
                )

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
            self._copy_covers(ctx, wide_dir, vert_dir)

        # A machine-readable manifest distinguishes actual music mixes from the
        # current voice-only fallback path, which the UI otherwise marks as success.
        try:
            os.makedirs(root, exist_ok=True)
            report_path = os.path.join(root, "bgm_report.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(bgm_report, f, ensure_ascii=False, indent=2)
            mixed_count = sum(x.get("status") == "mixed" for x in bgm_report["items"])
            fallback_count = sum(x.get("status") in ("mix_failed_no_final_file", "bgm_unavailable_no_final_file") for x in bgm_report["items"])
            no_bgm_count = sum(x.get("status") == "voice_only_no_bgm_configured" for x in bgm_report["items"])
            ctx.log(
                f"[BGM SUMMARY] mixed={mixed_count}; requested but failed={fallback_count}; "
                f"BGM intentionally disabled={no_bgm_count}; report={report_path}"
            )
        except OSError as e:
            ctx.log(f"[BGM][WARNING] could not write bgm_report.json: {e}")

        self._write_root_meta(ctx, root)

        deduplication = (ctx.analysis or {}).get("shorts_deduplication") or []
        try:
            with open(os.path.join(root, "shorts_dedup_report.json"), "w", encoding="utf-8") as stream:
                json.dump({
                    "version": 1,
                    "input_short_count": len(clips) + len(deduplication),
                    "unique_short_count": len(clips),
                    "duplicates_removed": len(deduplication),
                    "duplicates": deduplication,
                    "cross_platform_duplicate_outputs": sum(
                        item.get("status") == "deduplicated" for item in bgm_report["items"]
                    ),
                }, stream, ensure_ascii=False, indent=2)
        except OSError as exc:
            ctx.log(f"[SHORTS][DEDUP][WARNING] не записан отчёт: {exc}")

        if getattr(ctx, "speech_metrics", None):
            try:
                with open(os.path.join(root, "speech_metrics.json"), "w", encoding="utf-8") as f:
                    json.dump(ctx.speech_metrics, f, ensure_ascii=False, indent=2)
            except OSError as e:
                ctx.log(f"[SPEECH RATE][WARNING] could not write speech_metrics.json: {e}")

        # Build the exact manual-upload list; do not infer it from temporary files.
        def _relative(path: str) -> str:
            return os.path.relpath(path, root)

        bgm_by_asset = {
            (str(item.get("platform") or ""), str(item.get("asset") or "")): item
            for item in bgm_report.get("items", [])
        }

        def _youtube_upload_item(path: str, asset: str, metadata_base: str, directory: str) -> dict:
            record = bgm_by_asset.get(("YouTube", asset), {})
            music_status = str(record.get("status") or "missing")
            title_path = os.path.join(directory, f"{metadata_base}_title.txt")
            description_path = os.path.join(directory, f"{metadata_base}_description.txt")
            hashtags_path = os.path.join(directory, f"{metadata_base}_hashtags.txt")
            readiness = evaluate_upload_readiness(
                video_path=path,
                title_path=title_path,
                description_path=description_path,
                hashtags_path=hashtags_path,
                bgm_status=music_status,
            )
            return {
                "video_file": _relative(path),
                "exists": readiness["video_exists"],
                "video_nonempty": readiness["video_nonempty"],
                "title_file": _relative(title_path),
                "description_file": _relative(description_path),
                "hashtags_file": _relative(hashtags_path),
                "metadata_files_exist": readiness["metadata_files_exist"],
                "metadata_content_valid": readiness["metadata_content_valid"],
                "bgm_status": music_status,
                "bgm_status_accepted": readiness["bgm_status_accepted"],
                "bgm_track": record.get("track", ""),
                "readiness_issues": readiness["issues"],
                "ready_for_manual_upload": readiness["ready_for_manual_upload"],
            }

        yt_wide_dir = os.path.join(root, "youtube", "wide")
        yt_long_path = os.path.join(yt_wide_dir, "final_16x9.mp4")
        youtube_shorts = []
        for ordinal, short_path in enumerate(ctx.shorts, 1):
            short_idx = int(ctx.short_indices[ordinal - 1]) if ordinal - 1 < len(getattr(ctx, "short_indices", [])) else ordinal
            short_dir = os.path.join(root, "youtube", "shorts", f"short_{short_idx:03d}")
            youtube_shorts.append(
                _youtube_upload_item(
                    os.path.join(short_dir, f"short_{short_idx:03d}.mp4"),
                    f"shorts/short_{short_idx:03d}",
                    f"short_{short_idx:03d}",
                    short_dir,
                )
            )
        available_short_indices = [
            int(ctx.short_indices[pos]) if pos < len(getattr(ctx, "short_indices", [])) else pos + 1
            for pos, path in enumerate(ctx.shorts)
            if path and os.path.isfile(path)
        ]
        missing_short_indices = find_missing_short_indices(len(clips), available_short_indices)
        missing_shorts = [
            {
                "short_index": index,
                "video_file": f"youtube/shorts/short_{index:03d}/short_{index:03d}.mp4",
                "ready_for_manual_upload": False,
                "reason": "selected clip did not produce a valid Short MP4",
            }
            for index in missing_short_indices
        ]

        upload_manifest = {
            "version": 1,
            "series": ctx.series_name or base_name,
            "platform": "YouTube",
            "manual_upload_only": True,
            "full_video": _youtube_upload_item(
                yt_long_path, "wide/final_16x9", base_name, yt_wide_dir
            ),
            "shorts": youtube_shorts,
            "missing_shorts": missing_shorts,
            "not_upload_candidates": [
                "youtube/wide/master_16x9.mp4",
                "youtube/vertical/master_9x16.mp4",
                "youtube/vertical/final_9x16.mp4 (only upload if separately intended)",
                "_tmp/**",
                "shorts/** (staging before per-platform BGM finalization)",
            ],
        }
        manifest_path = os.path.join(root, "youtube_upload_manifest.json")
        checklist_path = os.path.join(root, "YOUTUBE_UPLOAD_CHECKLIST.txt")
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump(upload_manifest, stream, ensure_ascii=False, indent=2)

        checklist_lines = [
            f"YouTube manual upload checklist — {ctx.series_name or base_name}",
            "Do not upload master files, _tmp files, or the staging shorts folder.",
            "",
            "FULL VIDEO",
            f"File: {upload_manifest['full_video']['video_file']}",
            f"Title: {upload_manifest['full_video']['title_file']}",
            f"Description: {upload_manifest['full_video']['description_file']}",
            f"Hashtags: {upload_manifest['full_video']['hashtags_file']}",
            f"Metadata files exist: {'YES' if upload_manifest['full_video']['metadata_files_exist'] else 'NO'}",
            f"Metadata content valid: {'YES' if upload_manifest['full_video']['metadata_content_valid'] else 'NO'}",
            f"BGM: {upload_manifest['full_video']['bgm_status']} ({upload_manifest['full_video']['bgm_track'] or 'no track recorded'})",
            f"Ready: {'YES' if upload_manifest['full_video']['ready_for_manual_upload'] else 'NO — inspect video, metadata values and BGM report'}",
            f"Blocking issues: {'; '.join(upload_manifest['full_video']['readiness_issues']) or 'none'}",
            "",
            "SHORTS",
        ]
        for number, item in enumerate(youtube_shorts, 1):
            checklist_lines.extend([
                f"#{number}",
                f"File: {item['video_file']}",
                f"Title: {item['title_file']}",
                f"Description: {item['description_file']}",
                f"Hashtags: {item['hashtags_file']}",
                f"Metadata files exist: {'YES' if item['metadata_files_exist'] else 'NO'}",
                f"Metadata content valid: {'YES' if item['metadata_content_valid'] else 'NO'}",
                f"BGM: {item['bgm_status']} ({item['bgm_track'] or 'no track recorded'})",
                f"Ready: {'YES' if item['ready_for_manual_upload'] else 'NO — inspect video, metadata values and BGM report'}",
                f"Blocking issues: {'; '.join(item['readiness_issues']) or 'none'}",
                "",
            ])
        if missing_short_indices:
            checklist_lines.extend(["", "MISSING SELECTED SHORTS — DO NOT MARK THE BATCH COMPLETE"])
            checklist_lines.extend(
                f"#{index}: youtube/shorts/short_{index:03d}/short_{index:03d}.mp4 — NOT CREATED/INVALID"
                for index in missing_short_indices
            )
        with open(checklist_path, "w", encoding="utf-8") as stream:
            stream.write("\n".join(checklist_lines) + "\n")
        ctx.log(f"[YOUTUBE UPLOAD LIST] {checklist_path}; machine manifest={manifest_path}")

        requested_bgm_failures = [
            x for x in bgm_report["items"]
            if x.get("bgm_requested") and x.get("status") not in ("mixed", "deduplicated")
        ]
        if requested_bgm_failures:
            _cleanup_intermediates()
            raise RuntimeError(
                f"BGM was requested but missing/failed for {len(requested_bgm_failures)} asset(s). "
                f"Inspect {os.path.join(root, 'bgm_report.json')}; do not publish voice-only fallback files."
            )

        missing_source_failures = [x for x in bgm_report["items"] if x.get("status") == "source_missing"]
        if missing_source_failures:
            _cleanup_intermediates()
            raise RuntimeError(
                f"Finalization is incomplete: {len(missing_source_failures)} expected source asset(s) are missing. "
                f"Inspect {os.path.join(root, 'bgm_report.json')} and {manifest_path}."
            )
        if missing_short_indices:
            _cleanup_intermediates()
            raise RuntimeError(
                f"Finalization is incomplete: selected Shorts not rendered: {missing_short_indices}. "
                f"Inspect {checklist_path} and {manifest_path}."
            )
        # [VM-CREDITS] сертификаты использованных клипов (источник/лицензия/описание)
        try:
            import glob as _glob
            _plans = _glob.glob(os.path.join(getattr(ctx, "output_folder", "") or "", "_tmp", "*.edit_plan.json"))
            _used: list[str] = []
            for _pl in _plans[:1]:
                try:
                    _pd = json.loads(open(_pl, encoding="utf-8").read())
                    _used += [str(s.get("source_path") or "") for s in (_pd.get("scenes") or [])]
                except Exception:
                    pass
            if not _used:
                _used = [str(p) for p in (getattr(ctx, "broll_selected", None) or [])]
            if _used:
                from video_maker.semantic.library import write_clip_credits
                write_clip_credits(_used, root, log=ctx.log)
        except Exception as _ce:
            ctx.log(f"[CREDITS] пропуск: {_ce}")

        _cleanup_intermediates()

        ctx.log(f"[ФИНАЛ] Готово → {root} | платформ: {len(platforms)}")
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
