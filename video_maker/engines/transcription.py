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
    """Авто-определение устройства и типа вычислений для Apple Silicon."""
    if device != "auto" and compute_type != "auto":
        return device, compute_type

    system = platform.system()
    machine = platform.machine()

    if system == "Darwin" and machine.startswith("arm"):
        # Apple Silicon (M1/M2/M3)
        resolved_device = "mps" if device == "auto" else device
        resolved_compute = "float16" if compute_type == "auto" else compute_type
    else:
        resolved_device = "cpu" if device == "auto" else device
        resolved_compute = "int8" if compute_type == "auto" else compute_type

    return resolved_device, resolved_compute


def transcribe(
    audio_path: str,
    model_name: str = "large-v3",
    whisperx_path: str = "",
    language: str = "ru",
    device: str = "auto",
    compute_type: str = "auto",
    log_fn=None,
) -> dict:
    """Транскрибация аудио через WhisperX CLI с пословными таймкодами."""
    _log = log_fn or log.info
    _log(f"[WHISPER] Модель: {model_name}")

    # Resolve whisperx path: explicit -> saved -> auto-detect
    whisper_bin = resolve_whisperx(whisperx_path)
    if not whisper_bin:
        raise RuntimeError(
            "WhisperX не найден.\n"
            "Установите: pip install whisperx\n"
            "Или укажите whisperx_path в настройках."
        )

    _log(f"[WHISPER] Бинарник: {whisper_bin}")

    resolved_device, resolved_compute = _resolve_device_compute(device, compute_type)
    _log(f"[WHISPER] Устройство: {resolved_device}, compute_type: {resolved_compute}")

    # Конвертируем аудио в чистый WAV 16kHz mono
    with tempfile.TemporaryDirectory(prefix="videomeyker_whisper_") as tmp_dir:
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
            "--language", language,
            "--output_format", "json",
            "--output_dir", str(cleaned_path.parent),
            "--device", resolved_device,
            "--compute_type", resolved_compute,
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
            # Fallback на CPU если MPS упал
            if resolved_device == "mps":
                _log("[WHISPER] MPS ошибка, пробуем fallback на CPU...")
                return transcribe(
                    audio_path=audio_path,
                    model_name=model_name,
                    whisperx_path=whisperx_path,
                    language=language,
                    device="cpu",
                    compute_type="int8",
                    log_fn=log_fn,
                )
            raise RuntimeError(f"WhisperX завершился с ошибкой: {result.stderr[:200]}")

        # Читаем результат
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