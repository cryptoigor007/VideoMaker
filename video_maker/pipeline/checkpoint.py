# VideoMaker FIX | 2026.09.02-r18 | 2026-09-02
# CHANGED: Stage D — resume only after valid ffprobe; invalidate missing/corrupt media
# PREV: 2026.09.01-r12
# REPLACE: video_maker/pipeline/checkpoint.py

"""Сохранение и восстановление прогресса пайплайна."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
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


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_checkpoint(
    ctx: Any,
    completed_stage: str,
    *,
    queue_index: int = 1,
    queue_total: int = 1,
    log_fn=None,
) -> str:
    """После успешной стадии — записать checkpoint в output_folder."""
    _log = log_fn or (lambda m: log.info(m))
    out = getattr(ctx, "output_folder", "") or "."
    path = checkpoint_path(out)

    # Тяжёлые dict — отдельными файлами рядом
    tmp_dir = os.path.join(out, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tr_path = os.path.join(tmp_dir, "checkpoint_transcription.json")
    an_path = os.path.join(tmp_dir, "checkpoint_analysis.json")
    try:
        if getattr(ctx, "transcription", None):
            _atomic_write(tr_path, ctx.transcription if isinstance(ctx.transcription, dict) else {})
    except Exception as e:
        _log(f"[CHECKPOINT] transcription save: {e}")
        tr_path = ""
    try:
        if getattr(ctx, "analysis", None):
            _atomic_write(an_path, ctx.analysis if isinstance(ctx.analysis, dict) else {})
    except Exception as e:
        _log(f"[CHECKPOINT] analysis save: {e}")
        an_path = ""

    completed = list(getattr(ctx, "_completed_stages", []) or [])
    if completed_stage and completed_stage not in completed:
        completed.append(completed_stage)
    ctx._completed_stages = completed

    data = {
        "version": 1,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "completed_stages": completed,
        "last_stage": completed_stage,
        "queue_index": queue_index,
        "queue_total": queue_total,
        "audio_path": getattr(ctx, "audio_path", ""),
        "output_folder": out,
        "series_name": getattr(ctx, "series_name", ""),
        "audio_duration": float(getattr(ctx, "audio_duration", 0) or 0),
        "master_horizontal": getattr(ctx, "master_horizontal", "") or "",
        "master_vertical": getattr(ctx, "master_vertical", "") or "",
        "final_horizontal": getattr(ctx, "final_horizontal", "") or "",
        "final_vertical": getattr(ctx, "final_vertical", "") or "",
        "shorts": list(getattr(ctx, "shorts", []) or []),
        "bgm_mixed": bool(getattr(ctx, "bgm_mixed", False)),
        "bgm_source_video": getattr(ctx, "bgm_source_video", "") or "",
        "h_did_imo": bool(getattr(ctx, "h_did_imo", False)),
        "transcription_file": tr_path if tr_path and os.path.isfile(tr_path) else "",
        "analysis_file": an_path if an_path and os.path.isfile(an_path) else "",
        "progress": float(getattr(ctx, "progress", 0) or 0),
    }
    try:
        _atomic_write(path, data)
        _log(f"[CHECKPOINT] сохранён после «{completed_stage}» → {path}")
    except Exception as e:
        _log(f"[CHECKPOINT] не удалось сохранить: {e}")
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
    for key in (
        "master_horizontal", "master_vertical",
        "final_horizontal", "final_vertical",
        "series_name", "audio_duration",
        "bgm_source_video",
    ):
        if data.get(key) is not None:
            setattr(ctx, key, data[key])
    if data.get("shorts"):
        ctx.shorts = list(data["shorts"])
    ctx.bgm_mixed = bool(data.get("bgm_mixed", False))
    ctx.h_did_imo = bool(data.get("h_did_imo", False))
    completed = list(data.get("completed_stages") or [])
    ctx.progress = float(data.get("progress") or 0)

    trf = data.get("transcription_file") or ""
    if trf and os.path.isfile(trf):
        try:
            with open(trf, "r", encoding="utf-8") as f:
                ctx.transcription = json.load(f)
            _log(f"[CHECKPOINT] transcription восстановлен ({len(ctx.transcription)} keys)")
        except Exception as e:
            _log(f"[CHECKPOINT] transcription load: {e}")
            if "TranscribeStage" in completed:
                completed.remove("TranscribeStage")
    elif data.get("transcription_file"):
        _log("[CHECKPOINT] transcription file missing — invalidate TranscribeStage")
        if "TranscribeStage" in completed:
            completed.remove("TranscribeStage")

    anf = data.get("analysis_file") or ""
    if anf and os.path.isfile(anf):
        try:
            with open(anf, "r", encoding="utf-8") as f:
                ctx.analysis = json.load(f)
            _log("[CHECKPOINT] analysis восстановлен")
        except Exception as e:
            _log(f"[CHECKPOINT] analysis load: {e}")
            if "GeminiStage" in completed:
                completed.remove("GeminiStage")
    elif data.get("analysis_file"):
        _log("[CHECKPOINT] analysis file missing — invalidate GeminiStage")
        if "GeminiStage" in completed:
            completed.remove("GeminiStage")

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

    # shorts paths
    valid_shorts = []
    for sp in list(getattr(ctx, "shorts", None) or []):
        if _probe_ok(sp):
            valid_shorts.append(sp)
        else:
            _log(f"[CHECKPOINT] probe FAIL short: {sp}")
    if valid_shorts:
        ctx.shorts = valid_shorts
    elif getattr(ctx, "shorts", None):
        ctx.shorts = []
        if "ShortsCutter" in completed:
            completed.remove("ShortsCutter")

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
