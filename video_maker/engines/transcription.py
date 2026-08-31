"""Движок транскрибации — WhisperX (внешний CLI)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .whisperx_resolve import resolve_whisperx

log = logging.getLogger(__name__)

# Жёсткие дефолты по требованию пользователя
DEFAULT_MODEL = "large-v3"
DEFAULT_LANGUAGE = "ru"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE = "int8"
DEFAULT_BATCH_SIZE = 4


def _resolve_device_compute(device: str, compute_type: str) -> tuple[str, str]:
    """Всегда cpu + int8 по умолчанию (стабильно на macOS)."""
    if device == "auto" or not device:
        device = DEFAULT_DEVICE
    if compute_type == "auto" or not compute_type:
        compute_type = DEFAULT_COMPUTE
    return device, compute_type


def transcribe(
    audio_path: str,
    model_name: str = DEFAULT_MODEL,
    whisperx_path: str = "",
    language: str = DEFAULT_LANGUAGE,
    device: str = "auto",
    compute_type: str = "auto",
    log_fn=None,
) -> dict:
    """Один вызов WhisperX с пословными таймкодами.

    Дефолты:
      model=large-v3, language=ru, device=cpu, compute_type=int8,
      batch_size = DEFAULT_BATCH_SIZE, threads=все ядра.
    """
    _log = log_fn or log.info

    if not model_name or model_name.strip().lower() in ("auto", "default", ""):
        model_name = DEFAULT_MODEL

    _log(f"[WHISPER] Модель: {model_name}")

    whisper_bin = resolve_whisperx(whisperx_path)
    if not whisper_bin:
        raise RuntimeError(
            "WhisperX не найден.\n"
            "Установите: pip install whisperx\n"
            "Или укажите whisperx_path в настройках."
        )

    _log(f"[WHISPER] Бинарник: {whisper_bin}")

    resolved_device, resolved_compute = _resolve_device_compute(device, compute_type)
    batch_size = DEFAULT_BATCH_SIZE
    n_threads = min(4, os.cpu_count() or 4)

    _log(
        f"[WHISPER] Устройство: {resolved_device}, compute_type: {resolved_compute}, "
        f"batch_size: {batch_size}, threads: {n_threads}"
    )

    with tempfile.TemporaryDirectory(prefix="videomeyker_whisper_") as tmp_dir:
        cleaned_path = Path(tmp_dir) / "cleaned.wav"

        _log("[WHISPER] Подготовка аудио 16kHz mono...")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-af", "highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(cleaned_path),
            ],
            capture_output=True,
            check=True,
        )

        cmd = [
            whisper_bin,
            str(cleaned_path),
            "--model", model_name,
            "--language", language or DEFAULT_LANGUAGE,
            "--output_format", "json",
            "--output_dir", str(cleaned_path.parent),
            "--device", resolved_device,
            "--compute_type", resolved_compute,
            "--batch_size", str(DEFAULT_BATCH_SIZE),
            "--threads", str(n_threads),
        ]

        wx_env = os.environ.copy()
        # Подавляем шумные warning’и
        suppress = "ignore::UserWarning:pyannote.audio.core.io,ignore::UserWarning:lightning"
        existing_warn = wx_env.get("PYTHONWARNINGS")
        wx_env["PYTHONWARNINGS"] = (
            f"{existing_warn},{suppress}" if existing_warn else suppress
        )
        wx_env["OMP_NUM_THREADS"] = str(n_threads)
        wx_env["MKL_NUM_THREADS"] = str(n_threads)

        _log("[WHISPER] Запуск распознавания (один раз)...")
        result = subprocess.run(cmd, capture_output=True, text=True, env=wx_env)

        json_path = cleaned_path.with_suffix(".json")
        err = (result.stderr or result.stdout or "").strip()

        # Успех = есть JSON (даже если returncode != 0 из-за warning’ов)
        if json_path.exists():
            if result.returncode != 0:
                # Известные безвредные сообщения
                if any(x in err for x in (
                    "Lightning automatically upgraded",
                    "leaked semaphore",
                    "resource_tracker",
                )):
                    _log("[WHISPER] Warning (Lightning/semaphore) — игнорируем, JSON есть")
                else:
                    _log(f"[WHISPER] returncode={result.returncode}, но JSON создан. stderr[:300]: {err[:300]}")
        else:
            # JSON нет — настоящая ошибка
            if model_name == "large-v3-turbo" and any(
                x in err.lower() for x in ("large-v3-turbo", "not found", "404", "does not exist")
            ):
                _log("[WHISPER] large-v3-turbo недоступна → fallback large-v3")
                return transcribe(
                    audio_path=audio_path,
                    model_name="large-v3",
                    whisperx_path=whisperx_path,
                    language=language,
                    device=device,
                    compute_type=compute_type,
                    log_fn=log_fn,
                )
            _log(f"[WHISPER] Ошибка: {err[:500]}")
            raise RuntimeError(f"WhisperX завершился с ошибкой: {err[:300]}")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        segments = data.get("segments", [])
        language = data.get("language", language or "ru")

        _log(f"[WHISPER] Язык: {language} | Сегментов: {len(segments)}")

        return {
            "segments": segments,
            "language": language,
        }
