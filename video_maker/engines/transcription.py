"""Движок транскрибации — Whisper."""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def transcribe(
    audio_path: str,
    model_name: str = "base",
    log_fn=None,
) -> dict:
    """Транскрибация аудио через WhisperX."""
    _log = log_fn or log.info
    _log(f"[WHISPER] Модель: {model_name}")

    try:
        import whisperx
    except ImportError:
        _log("[WHISPER] whisperx не установлен, пробуем openai-whisper")
        return _transcribe_basic(audio_path, model_name, _log)

    device = "cpu"
    compute_type = "int8"

    model = whisperx.load_model(model_name, device, compute_type=compute_type)
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16, language="ru")

    # Выравнивание слов
    try:
        model_a, metadata = whisperx.load_align_model(
            language_code="ru", device=device
        )
        result = whisperx.align(
            result["segments"], model_a, metadata, audio, device
        )
    except Exception as e:
        _log(f"[WHISPER] Выравнивание недоступно: {e}")

    return {
        "segments": result.get("segments", []),
        "language": result.get("language", "ru"),
    }


def _transcribe_basic(
    audio_path: str, model_name: str, log_fn
) -> dict:
    """Базовая транскрибация через openai-whisper (fallback)."""
    try:
        import whisper
    except ImportError:
        log_fn("[WHISPER] Whisper не установлен")
        return {"segments": [], "language": "ru"}

    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path, language="ru")

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        })

    return {"segments": segments, "language": "ru"}
