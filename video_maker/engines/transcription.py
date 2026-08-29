"""Движок транскрибации — WhisperX (внешний CLI)."""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from .whisperx_resolve import resolve_whisperx

log = logging.getLogger(__name__)


def _resolve_device_compute(device: str, compute_type: str) -> tuple[str, str]:
    """Авто-определение устройства и типа вычислений.

    На Apple Silicon MPS для large-v3 часто падает → сразу CPU + int8
    (проверено: 10 мин аудио ≈ 6–7 мин, как в рабочих batch-скриптах).
    """
    if device != "auto" and compute_type != "auto":
        return device, compute_type

    # Надёжный путь для large-v3 на macOS — CPU int8
    resolved_device = "cpu" if device == "auto" else device
    resolved_compute = "int8" if compute_type == "auto" else compute_type
    return resolved_device, resolved_compute


def _smart_batch_size() -> int:
    """Динамический batch_size по свободной RAM (как в проверенных скриптах)."""
    try:
        import psutil
        free_gb = psutil.virtual_memory().available / (1024 ** 3)
        if free_gb > 12:
            return 24
        if free_gb > 6:
            return 16
        return 8
    except Exception:
        return 16


def transcribe(
    audio_path: str,
    model_name: str = "large-v3",
    whisperx_path: str = "",
    language: str = "ru",
    device: str = "auto",
    compute_type: str = "auto",
    log_fn=None,
) -> dict:
    """Транскрибация аудио через WhisperX CLI с пословными таймкодами.

    Один вызов. Дальше все хуки/субтитры/shorts режутся по уже готовым таймингам.
    """
    _log = log_fn or log.info
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
    batch_size = _smart_batch_size()
    n_threads = os.cpu_count() or 8
    _log(
        f"[WHISPER] Устройство: {resolved_device}, compute_type: {resolved_compute}, "
        f"batch_size: {batch_size}, threads: {n_threads}"
    )

    with tempfile.TemporaryDirectory(prefix="videomeyker_whisper_") as tmp_dir:
        cleaned_path = Path(tmp_dir) / "cleaned.wav"

        # Лёгкая очистка (тяжёлый deesser/afftdn замедляет без большого выигрыша)
        clean_filter = "highpass=f=80,lowpass=f=14000,alimiter=limit=0.95"

        _log("[WHISPER] Подготовка аудио 16kHz mono...")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-af", clean_filter,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(cleaned_path),
            ],
            capture_output=True, check=True,
        )

        cmd = [
            whisper_bin,
            str(cleaned_path),
            "--model", model_name,
            "--language", language,
            "--output_format", "json",
            "--output_dir", str(cleaned_path.parent),
            "--device", resolved_device,
            "--compute_type", resolved_compute,
            "--batch_size", str(batch_size),
            "--threads", str(n_threads),
        ]

        wx_env = os.environ.copy()
        suppress = "ignore::UserWarning:pyannote.audio.core.io"
        existing_warn = wx_env.get("PYTHONWARNINGS")
        wx_env["PYTHONWARNINGS"] = (
            f"{existing_warn},{suppress}" if existing_warn else suppress
        )

        _log("[WHISPER] Запуск распознавания (один раз)...")
        result = subprocess.run(cmd, capture_output=True, text=True, env=wx_env)

        if result.returncode != 0:
            _log(f"[WHISPER] Ошибка: {result.stderr[:500]}")
            raise RuntimeError(f"WhisperX завершился с ошибкой: {result.stderr[:300]}")

        json_path = cleaned_path.with_suffix(".json")
        if not json_path.exists():
            raise RuntimeError(f"WhisperX не создал JSON: {json_path}")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        segments = data.get("segments", [])
        language = data.get("language", "ru")

        _log(f"[WHISPER] Язык: {language} | Сегментов: {len(segments)}")

        return {
            "segments": segments,
            "language": language,
        }
