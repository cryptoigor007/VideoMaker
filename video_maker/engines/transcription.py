"""Движок транскрибации — WhisperX (внешний CLI)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _find_whisperx() -> str | None:
    """Найти бинарник whisperx."""
    candidates = [
        shutil.which("whisperx"),
        str(Path.home() / "whisperx" / "bin" / "whisperx"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def transcribe(
    audio_path: str,
    model_name: str = "large-v3",
    whisperx_path: str = "",
    log_fn=None,
) -> dict:
    """Транскрибация аудио через WhisperX CLI с пословными таймкодами."""
    _log = log_fn or log.info
    _log(f"[WHISPER] Модель: {model_name}")

    whisper_bin = whisperx_path or _find_whisperx()
    if not whisper_bin:
        raise RuntimeError(
            "WhisperX не найден. Установите whisperx или укажите whisperx_path."
        )

    _log(f"[WHISPER] Бинарник: {whisper_bin}")

    # Конвертируем аудио в чистый WAV 16kHz mono
    tmp_dir = tempfile.mkdtemp(prefix="videomeyker_whisper_")
    cleaned_path = Path(tmp_dir) / "cleaned.wav"

    clean_filter = (
        "highpass=f=80,lowpass=f=14000,adeclick=w=50,"
        "afftdn=nf=-30,"
        "equalizer=f=50:t=q:w=1:g=-20,equalizer=f=60:t=q:w=1:g=-20,"
        "deesser=i=0.4:m=0.4:f=0.5,"
        "alimiter=limit=0.9"
    )

    _log("[WHISPER] Очистка аудио для распознавания...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-af", clean_filter,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(cleaned_path),
        ],
        capture_output=True, check=True,
    )

    # Запускаем whisperx
    cmd = [
        whisper_bin,
        str(cleaned_path),
        "--model", model_name,
        "--language", "ru",
        "--output_format", "json",
        "--output_dir", str(cleaned_path.parent),
        "--device", "cpu",
        "--compute_type", "int8",
        "--batch_size", "4",
    ]

    wx_env = os.environ.copy()
    suppress = "ignore::UserWarning:pyannote.audio.core.io"
    existing_warn = wx_env.get("PYTHONWARNINGS")
    wx_env["PYTHONWARNINGS"] = (
        f"{existing_warn},{suppress}" if existing_warn else suppress
    )

    _log("[WHISPER] Запуск распознавания...")
    result = subprocess.run(cmd, capture_output=True, text=True, env=wx_env)

    if result.returncode != 0:
        _log(f"[WHISPER] Ошибка: {result.stderr[:500]}")
        raise RuntimeError(f"WhisperX завершился с ошибкой: {result.stderr[:200]}")

    # Читаем результат
    json_path = cleaned_path.with_suffix(".json")
    if not json_path.exists():
        raise RuntimeError(f"WhisperX не создал JSON: {json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    language = data.get("language", "ru")

    _log(f"[WHISPER] Язык: {language} | Сегментов: {len(segments)}")

    # Очищаем temp
    try:
        cleaned_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    return {
        "segments": segments,
        "language": language,
    }
