# VideoMaker FIX | 2026.09.02-r18 | 2026-09-02
# CHANGED: Stage D — resume only after valid ffprobe; invalidate missing/corrupt media
# PREV: 2026.09.01-r12
# REPLACE: video_maker/pipeline/checkpoint.py

"""Сохранение и восстановление прогресса пайплайна."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)


def _probe_ok(path: str, min_dur: float = 0.2) -> bool:
    """Resume только по валидному probe: файл есть, duration >= min_dur."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return False
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False
        dur = float((r.stdout or "").strip() or 0)
        return dur >= min_dur
    except Exception:
        return False

CHECKPOINT_NAME = "_vm_checkpoint.json"
STAGE_ORDER = [
    "AudioStage",
    "TranscribeStage",
    "GeminiStage",
    "MasterBuilder",
    "ParallelFinals",
    "ShortsCutter",
    "FinalizeStage",
]


def checkpoint_path(output_folder: str) -> str:
    return os.path.join(output_folder or ".", CHECKPOINT_NAME)


class CheckpointPersistenceError(RuntimeError):
    """Raised when a checkpoint stage/artifact cannot be persisted safely."""


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON atomically; preserve old file and remove partial temp files on failure."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # Persist the directory entry where supported (e.g. after sudden power loss).
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _stable_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_file_fingerprint(ctx: Any, path: str) -> dict:
    """Hash the source once per context; downstream checkpoint saves reuse the digest."""
    if not path:
        return {"path": "", "exists": False}
    real = os.path.realpath(os.path.abspath(path))
    try:
        st = os.stat(real)
    except OSError as exc:
        return {"path": real, "exists": False, "error": type(exc).__name__}
    cache = getattr(ctx, "_vm_source_hash_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(ctx, "_vm_source_hash_cache", cache)
    key = (real, int(st.st_size), int(st.st_mtime_ns))
    digest = cache.get(key)
    if digest is None:
        sha = hashlib.sha256()
        try:
            with open(real, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(block)
            digest = sha.hexdigest()
        except OSError as exc:
            return {"path": real, "exists": True, "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "error": type(exc).__name__}
        cache[key] = digest
    return {"path": real, "exists": True, "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "sha256": digest}


def _path_inventory(path: str) -> dict:
    """Cheap stat-based inventory of a media file/folder; do not hash large B-roll files."""
    raw = str(path or "").strip()
    if not raw:
        return {"path": "", "exists": False}
    real = os.path.realpath(os.path.abspath(raw))
    try:
        st = os.stat(real)
    except OSError as exc:
        return {"path": real, "exists": False, "error": type(exc).__name__}
    if not os.path.isdir(real):
        return {"path": real, "exists": True, "type": "file", "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}
    entries = []
    try:
        for base, dirs, files in os.walk(real, followlinks=False):
            # used/ (и «использованная») — ротация уже отработанных клипов:
            # её появление не является изменением входных данных.
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and d.casefold() not in ("used", "использованная")
            )
            for name in sorted(files):
                if name.startswith("."):
                    continue
                full = os.path.join(base, name)
                try:
                    item = os.stat(full, follow_symlinks=False)
                except OSError:
                    entries.append({"name": os.path.relpath(full, real), "missing": True})
                    continue
                edge_digest = ""
                # Small edge samples detect ordinary in-place content changes with
                # unchanged size/mtime without reading or hashing whole B-roll videos.
                if os.path.isfile(full) and not os.path.islink(full):
                    try:
                        edge = hashlib.sha256()
                        with open(full, "rb") as stream:
                            edge.update(stream.read(65536))
                            if item.st_size > 65536:
                                stream.seek(max(0, item.st_size - 65536))
                                edge.update(stream.read(65536))
                        edge_digest = edge.hexdigest()
                    except OSError:
                        edge_digest = "unreadable"
                entries.append({"name": os.path.relpath(full, real), "size": int(item.st_size), "mtime_ns": int(item.st_mtime_ns), "is_symlink": os.path.islink(full), "edge_sha256": edge_digest})
    except OSError as exc:
        return {"path": real, "exists": True, "type": "directory", "error": type(exc).__name__}
    return {"path": real, "exists": True, "type": "directory", "entries": entries}


def resume_fingerprints(ctx: Any) -> dict[str, str]:
    """Return per-context category fingerprints; expensive input inventory is computed once."""
    cached = getattr(ctx, "_vm_resume_fingerprints", None)
    if isinstance(cached, dict) and all(key in cached for key in ("source", "transcription", "analysis", "render", "finalize")):
        return dict(cached)
    audio = _source_file_fingerprint(ctx, getattr(ctx, "audio_path", "") or "")
    source = _stable_digest({"audio": audio})
    transcription = _stable_digest({
        "source": source,
        "model": getattr(ctx, "whisper_model", "large-v3-turbo"),
        "language": getattr(ctx, "whisper_language", "ru"),
        "device": getattr(ctx, "whisper_device", "auto"),
        "compute_type": getattr(ctx, "whisper_compute_type", "auto"),
        "remove_long_pauses": bool(getattr(ctx, "remove_long_pauses", False)),
        "align_speech_rate": bool(getattr(ctx, "align_speech_rate", False)),
        "speech_rate_mode": str(getattr(ctx, "speech_rate_mode", "wpm") or "wpm"),
        "speech_rate_target_wpm": float(getattr(ctx, "speech_rate_target_wpm", 120.0) or 120.0),
        "speech_rate_coefficient": float(getattr(ctx, "speech_rate_coefficient", 1.0) or 1.0),
        "cut_extra_words": bool(getattr(ctx, "cut_extra_words", False)),
        "voice_enhance": bool(getattr(ctx, "voice_enhance", False)),
        "engine": "mlx_whisper",
    })
    packaging = getattr(ctx, "packaging_opts", None) or {}
    analysis = _stable_digest({
        "transcription": transcription,
        "gemini_model": getattr(ctx, "gemini_model", ""),
        "intro_gemini": bool(getattr(ctx, "intro_gemini", False)),
        "series_name": getattr(ctx, "series_name", ""),
        "packaging_opts": packaging,
        "gemini_key_configured": bool(getattr(ctx, "gemini_api_key", "") or getattr(ctx, "gemini_api_keys", [])),
    })
    path_keys = (
        "broll_horizontal", "broll_vertical", "intro_middle_outro_folder", "vertical_background",
        "cover_horizontal", "cover_vertical", "shorts_cover_image",
        "h_intro_path", "h_mid_path", "h_outro_path", "v_intro_path", "v_mid_path", "v_outro_path",
        "s_intro_path", "s_mid_path", "s_outro_path",
    )
    path_state = {key: _path_inventory(getattr(ctx, key, "") or "") for key in path_keys}
    flags = {key: bool(value) for key, value in vars(ctx).items() if key.startswith(("h_enable_", "v_enable_", "s_enable_"))}
    render = _stable_digest({
        "analysis": analysis,
        "paths": path_state,
        "flags": flags,
        "durations": {key: getattr(ctx, key, None) for key in (
            "h_intro_duration", "h_mid_duration", "h_outro_duration",
            "v_intro_duration", "v_mid_duration", "v_outro_duration",
            "s_intro_duration", "s_mid_duration", "s_outro_duration",
        )},
        "caption_style": getattr(ctx, "caption_style", "clean_pro"),
        "hook_style": getattr(ctx, "hook_style", "auto_aisie"),
        "vstack_top_ratio": getattr(ctx, "vstack_top_ratio", 0.6),
        "series_name": getattr(ctx, "series_name", ""),
        "output_folder": os.path.abspath(getattr(ctx, "output_folder", "") or "."),
    })
    platforms = list(getattr(ctx, "bgm_platforms", None) or [])
    bgm_inventory = []
    for platform in platforms:
        if isinstance(platform, dict):
            item = dict(platform)
            folder = item.get("folder") or ""
            item["folder_inventory"] = _path_inventory(folder) if folder else {"path": "", "exists": False}
            bgm_inventory.append(item)
        else:
            bgm_inventory.append(str(platform))
    finalize = _stable_digest({
        "render": render,
        "bgm_platforms": bgm_inventory,
        "bgm_folder": _path_inventory(getattr(ctx, "bgm_folder", "") or ""),
        "add_bgm": bool(getattr(ctx, "add_bgm", False)),
        "target_lufs": getattr(ctx, "target_lufs", -14.0),
        "keep_temp_files": bool(getattr(ctx, "keep_temp_files", False)),
    })
    result = {"source": source, "transcription": transcription, "analysis": analysis, "render": render, "finalize": finalize}
    setattr(ctx, "_vm_resume_fingerprints", dict(result))
    return result


def _invalidate_ctx_from(ctx: Any, stage: str) -> None:
    """Clear stale in-memory outputs when changed inputs invalidate a stage and everything after it."""
    try:
        index = STAGE_ORDER.index(stage)
    except ValueError:
        return
    if index <= STAGE_ORDER.index("TranscribeStage"):
        for key, value in (("transcription", {}), ("analysis", {}), ("narration_path", ""), ("speech_metrics", {}), ("voice_enhance_applied", False), ("audio_duration", 0.0)):
            setattr(ctx, key, value)
    elif index <= STAGE_ORDER.index("GeminiStage"):
        ctx.analysis = {}
    if index <= STAGE_ORDER.index("MasterBuilder"):
        for key in ("master_horizontal", "master_vertical", "final_horizontal", "final_vertical"):
            setattr(ctx, key, "")
        ctx.shorts = []
        ctx.short_indices = []
    elif index <= STAGE_ORDER.index("ParallelFinals"):
        ctx.final_horizontal = ""
        ctx.final_vertical = ""
        ctx.shorts = []
        ctx.short_indices = []
    elif index <= STAGE_ORDER.index("ShortsCutter"):
        ctx.shorts = []
        ctx.short_indices = []
    if index <= STAGE_ORDER.index("FinalizeStage"):
        ctx.bgm_mixed = False
    if index <= STAGE_ORDER.index("MasterBuilder"):
        ctx.h_did_imo = False
        if hasattr(ctx, "audio_normalized"):
            ctx.audio_normalized = False


def save_checkpoint(
    ctx: Any,
    completed_stage: str,
    *,
    queue_index: int = 1,
    queue_total: int = 1,
    log_fn=None,
) -> str:
    """Persist sidecar data first; never record a stage complete if its artefact failed."""
    _log = log_fn or (lambda m: log.info(m))
    out = getattr(ctx, "output_folder", "") or "."
    path = checkpoint_path(out)
    tmp_dir = os.path.join(out, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tr_path = ""
    an_path = ""
    artifact_token = uuid.uuid4().hex[:16]
    previous_checkpoint = load_checkpoint(out) or {}
    newly_written_artifacts: list[str] = []
    completed_before = list(getattr(ctx, "_completed_stages", []) or [])
    candidate_completed = list(completed_before)
    if completed_stage and completed_stage in STAGE_ORDER and completed_stage not in candidate_completed:
        candidate_completed.append(completed_stage)

    stage_index = STAGE_ORDER.index(completed_stage) if completed_stage in STAGE_ORDER else -1
    needs_transcription = stage_index >= STAGE_ORDER.index("TranscribeStage")
    needs_analysis = stage_index >= STAGE_ORDER.index("GeminiStage")
    persist_transcription = needs_transcription or "TranscribeStage" in completed_before
    persist_analysis = needs_analysis or "GeminiStage" in completed_before
    transcription = getattr(ctx, "transcription", None)
    analysis = getattr(ctx, "analysis", None)
    if persist_transcription and not isinstance(transcription, dict):
        raise CheckpointPersistenceError(
            f"Cannot mark {completed_stage} complete: transcription is missing or not a JSON object"
        )
    if persist_analysis and not isinstance(analysis, dict):
        raise CheckpointPersistenceError(
            f"Cannot mark {completed_stage} complete: analysis is missing or not a JSON object"
        )

    # Persist any present sidecar first. If it fails, keep the old main checkpoint
    # intact and raise so the pipeline does not proceed as though this stage was saved.
    if persist_transcription and isinstance(transcription, dict) and transcription:
        tr_path = os.path.join(tmp_dir, f"checkpoint_transcription_{artifact_token}.json")
        try:
            _atomic_write(tr_path, transcription)
            newly_written_artifacts.append(tr_path)
        except Exception as exc:
            _log(f"[CHECKPOINT][ERROR] transcription save failed; previous checkpoint retained: {exc}")
            raise CheckpointPersistenceError(f"transcription checkpoint save failed: {exc}") from exc
    elif persist_transcription:
        raise CheckpointPersistenceError(
            f"Cannot mark {completed_stage} complete: transcription object is empty"
        )

    if persist_analysis and isinstance(analysis, dict) and analysis:
        an_path = os.path.join(tmp_dir, f"checkpoint_analysis_{artifact_token}.json")
        try:
            _atomic_write(an_path, analysis)
            newly_written_artifacts.append(an_path)
        except Exception as exc:
            for written in newly_written_artifacts:
                try:
                    os.remove(written)
                except OSError:
                    pass
            _log(f"[CHECKPOINT][ERROR] analysis save failed; previous checkpoint retained: {exc}")
            raise CheckpointPersistenceError(f"analysis checkpoint save failed: {exc}") from exc
    elif persist_analysis:
        for written in newly_written_artifacts:
            try:
                os.remove(written)
            except OSError:
                pass
        raise CheckpointPersistenceError(
            f"Cannot mark {completed_stage} complete: analysis object is empty"
        )

    resume_fp = resume_fingerprints(ctx)
    data = {
        "version": 2,
        "fingerprint_schema": 1,
        "resume_fingerprints": resume_fp,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completed_stages": candidate_completed,
        "last_stage": completed_stage,
        "queue_index": queue_index,
        "queue_total": queue_total,
        "audio_path": getattr(ctx, "audio_path", ""),
        "narration_path": getattr(ctx, "narration_path", "") or "",
        "voice_enhance_requested": bool(getattr(ctx, "voice_enhance", False)),
        "voice_enhance_applied": bool(getattr(ctx, "voice_enhance_applied", False)),
        "speech_metrics": getattr(ctx, "speech_metrics", {}) or {},
        "output_folder": out,
        "series_name": getattr(ctx, "series_name", ""),
        "audio_duration": float(getattr(ctx, "audio_duration", 0) or 0),
        "master_horizontal": getattr(ctx, "master_horizontal", "") or "",
        "master_vertical": getattr(ctx, "master_vertical", "") or "",
        "final_horizontal": getattr(ctx, "final_horizontal", "") or "",
        "final_vertical": getattr(ctx, "final_vertical", "") or "",
        "shorts": list(getattr(ctx, "shorts", []) or []),
        "short_indices": list(getattr(ctx, "short_indices", []) or []),
        "bgm_mixed": bool(getattr(ctx, "bgm_mixed", False)),
        "bgm_source_video": getattr(ctx, "bgm_source_video", "") or "",
        "h_did_imo": bool(getattr(ctx, "h_did_imo", False)),
        "transcription_file": tr_path if tr_path and os.path.isfile(tr_path) else "",
        "analysis_file": an_path if an_path and os.path.isfile(an_path) else "",
        "progress": float(getattr(ctx, "progress", 0) or 0),
    }
    try:
        _atomic_write(path, data)
    except Exception as exc:
        for written in newly_written_artifacts:
            try:
                os.remove(written)
            except OSError:
                pass
        _log(f"[CHECKPOINT][ERROR] main checkpoint save failed: {exc}")
        raise CheckpointPersistenceError(f"main checkpoint save failed: {exc}") from exc
    # Remove only sidecars referenced by the previous checkpoint, and only after
    # the main checkpoint points at the newly durable artifacts.
    for old_key in ("transcription_file", "analysis_file"):
        old_path = previous_checkpoint.get(old_key) or ""
        if not old_path or old_path in (tr_path, an_path):
            continue
        try:
            old_real = os.path.realpath(old_path)
            tmp_real = os.path.realpath(tmp_dir)
            if os.path.dirname(old_real) == tmp_real and os.path.basename(old_real).startswith("checkpoint_"):
                os.remove(old_real)
        except OSError:
            pass
    # Only mutate completion state after the complete checkpoint is durably published.
    ctx._completed_stages = candidate_completed
    _log(f"[CHECKPOINT] сохранён после «{completed_stage}» → {path}")
    return path


def load_checkpoint(output_folder: str) -> dict | None:
    path = checkpoint_path(output_folder)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        log.warning("[CHECKPOINT] load failed: %s", e)
        return None


def apply_checkpoint_to_ctx(ctx: Any, data: dict, log_fn=None) -> Any:
    """Восстановить пути и JSON в контекст.

    Resume только по валидному probe: битые/пустые медиа сбрасывают
    соответствующие completed_stages, чтобы стадия перезапустилась.
    """
    _log = log_fn or (lambda m: log.info(m))
    saved_fingerprints = data.get("resume_fingerprints")
    current_fingerprints = resume_fingerprints(ctx) if isinstance(saved_fingerprints, dict) else {}
    fingerprint_stage = None
    if isinstance(saved_fingerprints, dict):
        category_stages = (
            ("source", "AudioStage"),
            ("transcription", "TranscribeStage"),
            ("analysis", "GeminiStage"),
            ("render", "MasterBuilder"),
            ("finalize", "FinalizeStage"),
        )
        changed = []
        for category, stage in category_stages:
            if saved_fingerprints.get(category) != current_fingerprints.get(category):
                changed.append((category, stage))
        if changed:
            fingerprint_stage = min((stage for _category, stage in changed), key=STAGE_ORDER.index)
            categories_text = ", ".join(category for category, _stage in changed)
            _log(
                f"[CHECKPOINT] вход/настройки изменились ({categories_text}); "
                f"инвалидируем {fingerprint_stage} и последующие стадии"
            )

    for key in (
        "master_horizontal", "master_vertical",
        "final_horizontal", "final_vertical",
        "audio_duration", "narration_path", "bgm_source_video",
    ):
        if data.get(key) is not None:
            setattr(ctx, key, data[key])
    # New-format checkpoints compare series_name against the current UI settings;
    # legacy checkpoints keep their previous series for backwards compatibility.
    if not isinstance(saved_fingerprints, dict) and data.get("series_name") is not None:
        ctx.series_name = data["series_name"]
    if data.get("shorts"):
        ctx.shorts = list(data["shorts"])
    ctx.short_indices = list(data.get("short_indices") or [])
    ctx.speech_metrics = dict(data.get("speech_metrics") or {})
    ctx.voice_enhance_applied = bool(data.get("voice_enhance_applied", False))
    ctx.bgm_mixed = bool(data.get("bgm_mixed", False))
    ctx.h_did_imo = bool(data.get("h_did_imo", False))
    completed = list(data.get("completed_stages") or [])
    if fingerprint_stage:
        invalidate_index = STAGE_ORDER.index(fingerprint_stage)
        completed = [stage for stage in completed if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < invalidate_index]
        _invalidate_ctx_from(ctx, fingerprint_stage)
        stage_progress_floor = {
            "AudioStage": 0.0,
            "TranscribeStage": 5.0,
            "GeminiStage": 15.0,
            "MasterBuilder": 25.0,
            "ParallelFinals": 45.0,
            "ShortsCutter": 75.0,
            "FinalizeStage": 90.0,
        }
        ctx.progress = stage_progress_floor.get(fingerprint_stage, 0.0)
    else:
        ctx.progress = float(data.get("progress") or 0)

    # The narration cache and all downstream media depend on the enhancement
    # toggle. If the setting changed since this checkpoint, restart at transcription
    # instead of mixing enhanced/unprocessed narration with a stale render.
    saved_voice_enhance = data.get("voice_enhance_requested")
    if (
        not isinstance(saved_fingerprints, dict)
        and "TranscribeStage" in completed
        and saved_voice_enhance is not None
        and bool(saved_voice_enhance) != bool(getattr(ctx, "voice_enhance", False))
    ):
        _log("[CHECKPOINT] voice_enhance setting changed — invalidate TranscribeStage and later stages")
        start = STAGE_ORDER.index("TranscribeStage")
        completed = [stage for stage in completed if STAGE_ORDER.index(stage) < start]
        ctx.narration_path = ""
        ctx.voice_enhance_applied = False
        ctx.speech_metrics = {}
        ctx.transcription = {}
        ctx.analysis = {}

    trf = data.get("transcription_file") or ""
    if "TranscribeStage" not in completed:
        ctx.transcription = {}
    elif trf and os.path.isfile(trf):
        try:
            with open(trf, "r", encoding="utf-8") as f:
                ctx.transcription = json.load(f)
            if not isinstance(ctx.transcription, dict) or not ctx.transcription:
                raise ValueError("transcription sidecar must be a non-empty JSON object")
            _log(f"[CHECKPOINT] transcription восстановлен ({len(ctx.transcription)} keys)")
        except Exception as e:
            _log(f"[CHECKPOINT] transcription load: {e}")
            idx = STAGE_ORDER.index("TranscribeStage")
            completed = [stage for stage in completed if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < idx]
            _invalidate_ctx_from(ctx, "TranscribeStage")
    else:
        _log("[CHECKPOINT] transcription file missing — invalidate TranscribeStage and later stages")
        idx = STAGE_ORDER.index("TranscribeStage")
        completed = [stage for stage in completed if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < idx]
        _invalidate_ctx_from(ctx, "TranscribeStage")

    anf = data.get("analysis_file") or ""
    if "GeminiStage" not in completed:
        ctx.analysis = {}
    elif anf and os.path.isfile(anf):
        try:
            with open(anf, "r", encoding="utf-8") as f:
                ctx.analysis = json.load(f)
            if not isinstance(ctx.analysis, dict) or not ctx.analysis:
                raise ValueError("analysis sidecar must be a non-empty JSON object")
            _log("[CHECKPOINT] analysis восстановлен")
        except Exception as e:
            _log(f"[CHECKPOINT] analysis load: {e}")
            idx = STAGE_ORDER.index("GeminiStage")
            completed = [stage for stage in completed if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < idx]
            _invalidate_ctx_from(ctx, "GeminiStage")
    else:
        _log("[CHECKPOINT] analysis file missing — invalidate GeminiStage and later stages")
        idx = STAGE_ORDER.index("GeminiStage")
        completed = [stage for stage in completed if stage in STAGE_ORDER and STAGE_ORDER.index(stage) < idx]
        _invalidate_ctx_from(ctx, "GeminiStage")

    # A completed transcription stage is resumable only when its prepared audio
    # still exists. Never combine retimed word timestamps with the raw narration.
    if "TranscribeStage" in completed:
        prepared = getattr(ctx, "narration_path", "") or ""
        if not prepared or not os.path.isfile(prepared):
            _log("[CHECKPOINT] prepared narration missing — invalidate TranscribeStage and later stages")
            try:
                idx = STAGE_ORDER.index("TranscribeStage")
                completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
            except ValueError:
                completed = [s for s in completed if s != "TranscribeStage"]
            ctx.narration_path = ""
            ctx.speech_metrics = {}
            ctx.transcription = {}
            ctx.analysis = {}

    # Old checkpoints have no source-index map; re-render Shorts rather than pair
    # a later surviving video with the title/description of a skipped earlier clip.
    if "ShortsCutter" in completed and len(getattr(ctx, "short_indices", [])) != len(getattr(ctx, "shorts", [])):
        _log("[CHECKPOINT] Shorts index map missing/mismatched — invalidate ShortsCutter and FinalizeStage")
        try:
            idx = STAGE_ORDER.index("ShortsCutter")
            completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
        except ValueError:
            completed = [s for s in completed if s not in ("ShortsCutter", "FinalizeStage")]
        ctx.shorts = []
        ctx.short_indices = []

    # Resume только по валидному probe
    media_checks = (
        ("master_horizontal", "MasterBuilder"),
        ("master_vertical", "ParallelFinals"),
        ("final_horizontal", "ParallelFinals"),
        ("final_vertical", "ParallelFinals"),
    )
    for label, stage in media_checks:
        p = getattr(ctx, label, None) or data.get(label)
        if not p:
            continue
        if _probe_ok(p):
            _log(f"[CHECKPOINT] probe OK {label} → {os.path.basename(p)}")
            setattr(ctx, label, p)
        else:
            _log(
                f"[CHECKPOINT] probe FAIL {label}: {p} — invalidate {stage}"
            )
            setattr(ctx, label, "" if label.startswith("final") or label.startswith("master") else p)
            if stage in completed:
                # drop this and all later stages
                try:
                    idx = STAGE_ORDER.index(stage)
                    completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
                except ValueError:
                    if stage in completed:
                        completed.remove(stage)

    # shorts paths — keep original source indices synchronized with valid media.
    saved_shorts = list(getattr(ctx, "shorts", None) or [])
    saved_indices = list(getattr(ctx, "short_indices", None) or [])
    valid_shorts = []
    valid_indices = []
    for pos, sp in enumerate(saved_shorts):
        source_index = saved_indices[pos] if pos < len(saved_indices) else pos + 1
        if _probe_ok(sp):
            valid_shorts.append(sp)
            valid_indices.append(source_index)
        else:
            _log(f"[CHECKPOINT] probe FAIL short: {sp}")
    ctx.shorts = valid_shorts
    ctx.short_indices = valid_indices
    if saved_shorts and not valid_shorts and "ShortsCutter" in completed:
        _log("[CHECKPOINT] no valid Shorts remain — invalidate ShortsCutter and later stages")
        try:
            idx = STAGE_ORDER.index("ShortsCutter")
            completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
        except ValueError:
            completed = [s for s in completed if s not in ("ShortsCutter", "FinalizeStage")]

    # Половинчатое резюме: ParallelFinals без вертикального файла означает отмену
    # между H и V; ShortsCutter без шортсов при ожидаемых клипах — сбой отрисовки.
    # Без этого повторные запуски застревали на «Shorts не отрендерены».
    if ("ParallelFinals" in completed and "ShortsCutter" not in completed
            and not getattr(ctx, "final_vertical", "")):
        _log("[CHECKPOINT] final_vertical отсутствует — invalidate ParallelFinals и далее")
        try:
            idx = STAGE_ORDER.index("ParallelFinals")
            completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
        except ValueError:
            completed = [s for s in completed if s != "ParallelFinals"]
    expected_clips = (getattr(ctx, "analysis", None) or {}).get("clips_for_shorts") or []
    if "ShortsCutter" in completed and expected_clips and not getattr(ctx, "shorts", []):
        _log("[CHECKPOINT] шортсы отсутствуют при ожидаемых клипах — invalidate ShortsCutter")
        try:
            idx = STAGE_ORDER.index("ShortsCutter")
            completed = [s for s in completed if s not in STAGE_ORDER[idx:]]
        except ValueError:
            completed = [s for s in completed if s not in ("ShortsCutter", "FinalizeStage")]

    ctx._completed_stages = completed
    _log(f"[CHECKPOINT] completed after probe: {completed}")
    return ctx


def next_stage_index(completed: list[str]) -> int:
    """Индекс первой невыполненной стадии в STAGE_ORDER."""
    done = set(completed or [])
    for i, name in enumerate(STAGE_ORDER):
        if name not in done:
            return i
    return len(STAGE_ORDER)


def clear_checkpoint(output_folder: str, log_fn=None) -> None:
    _log = log_fn or (lambda m: log.info(m))
    path = checkpoint_path(output_folder)
    try:
        if os.path.isfile(path):
            os.remove(path)
            _log(f"[CHECKPOINT] удалён (успех) → {path}")
    except OSError as e:
        _log(f"[CHECKPOINT] clear: {e}")


def describe_checkpoint(data: dict) -> str:
    last = data.get("last_stage") or "?"
    completed = data.get("completed_stages") or []
    nxt = STAGE_ORDER[next_stage_index(completed)] if next_stage_index(completed) < len(STAGE_ORDER) else "ГОТОВО"
    return (
        f"Последняя успешная стадия: {last}\n"
        f"Пройдено: {', '.join(completed) or '—'}\n"
        f"Следующая: {nxt}\n"
        f"Сохранено: {data.get('saved_at', '?')}\n"
        f"Аудио: {os.path.basename(data.get('audio_path') or '')}"
    )
