# VideoMaker FIX | 2026.09.05-r31 | 2026-09-05
# CHANGED: cache HIT без words → re-transcribe
"""Движок транскрибации — ТОЛЬКО MLX Whisper (без WhisperX).

Зафиксировано:
  path_or_hf_repo = mlx-community/whisper-large-v3-turbo
  temperature = 0.0
  language = ru
  condition_on_previous_text = False
  verbose = False
  word_timestamps = True
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MLX_REPO = "mlx-community/whisper-large-v3-turbo"
DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_LANGUAGE = "ru"


def _transcription_cache_key(audio_path: str, model_name: str, language: str) -> str:
    st = os.stat(audio_path)
    raw = f"{os.path.abspath(audio_path)}|{st.st_mtime_ns}|{st.st_size}|{model_name}|{language}|mlx"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _transcription_cache_dir() -> Path:
    d = Path.home() / "video_maker" / "cache" / "whisper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_segments(raw_segments: list) -> list[dict]:
    out: list[dict] = []
    for seg in raw_segments or []:
        if not isinstance(seg, dict):
            continue
        words_out = []
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            word = (w.get("word") or w.get("text") or "").strip()
            if not word:
                continue
            words_out.append({
                "word": word,
                "start": float(w.get("start", 0) or 0),
                "end": float(w.get("end", 0) or 0),
                "probability": float(w.get("probability", w.get("score", 1.0)) or 1.0),
            })
        out.append({
            "start": float(seg.get("start", 0) or 0),
            "end": float(seg.get("end", 0) or 0),
            "text": (seg.get("text") or "").strip(),
            "words": words_out,
        })
    return out


def transcribe(
    audio_path: str,
    model_name: str = DEFAULT_MODEL,
    whisperx_path: str = "",
    language: str = DEFAULT_LANGUAGE,
    device: str = "auto",
    compute_type: str = "auto",
    log_fn=None,
) -> dict:
    """Только mlx_whisper. Параметры whisperx_path/device/compute_type игнорируются."""
    _log = log_fn or log.info
    language = language or DEFAULT_LANGUAGE

    # кэш
    try:
        ck = _transcription_cache_key(audio_path, MLX_REPO, language)
        cpath = _transcription_cache_dir() / f"{ck}.json"
        if cpath.is_file():
            data = json.loads(cpath.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("segments") is not None:
                segs = data.get("segments") or []
                n_w = sum(len(s.get("words") or []) for s in segs if isinstance(s, dict))
                # karaoke нужен word-level; кэш без words — пересчитать
                if n_w == 0 and segs:
                    _log(
                        f"[WHISPER] Кэш HIT но words=0 ({cpath.name}, "
                        f"{len(segs)} seg) → пересчёт с word_timestamps"
                    )
                else:
                    _log(
                        f"[WHISPER] Кэш HIT → {cpath.name} "
                        f"({len(segs)} сегментов, words={n_w})"
                    )
                    return data
    except Exception as e:
        _log(f"[WHISPER] Кэш пропуск: {e}")

    _log("[WHISPER] Движок: MLX Whisper (WhisperX НЕ используется)")
    _log(f"[WHISPER] Модель: {MLX_REPO}")
    _log("[WHISPER] temperature=0.0 language=ru condition_on_previous_text=False word_timestamps=True")
    _log(f"[WHISPER] Файл: {audio_path}")

    try:
        import mlx_whisper
    except ImportError as e:
        raise RuntimeError(
            "mlx_whisper не установлен. Выполните: pip install mlx-whisper\n"
            "WhisperX больше не используется."
        ) from e

    t0 = time.time()
    try:
        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=MLX_REPO,
            temperature=0.0,
            language=language,
            condition_on_previous_text=False,
            verbose=False,
            word_timestamps=True,
        )
    except Exception as e:
        raise RuntimeError(
            f"MLX Whisper ошибка: {e}\n"
            f"Проверьте: pip install -U mlx-whisper\n"
            f"Модель: {MLX_REPO}"
        ) from e

    elapsed = time.time() - t0
    if not isinstance(result, dict):
        raise RuntimeError(f"mlx_whisper вернул неожиданный тип: {type(result)}")

    segments = _normalize_segments(result.get("segments") or [])
    text = (result.get("text") or "").strip()
    lang = result.get("language") or language
    n_words = sum(len(s.get("words") or []) for s in segments)

    _log(
        f"[WHISPER] MLX OK за {elapsed:.1f}s | lang={lang} | "
        f"segments={len(segments)} words={n_words}"
    )

    out = {
        "segments": segments,
        "language": lang,
        "text": text,
        "engine": "mlx_whisper",
        "model": MLX_REPO,
    }

    try:
        ck = _transcription_cache_key(audio_path, MLX_REPO, language)
        cpath = _transcription_cache_dir() / f"{ck}.json"
        cpath.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        _log(f"[WHISPER] Кэш SAVE → {cpath.name}")
    except Exception:
        pass

    _log(f"[WHISPER] Готово: {len(segments)} сегментов")
    return out
