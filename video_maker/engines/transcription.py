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

# Баланс скорость/качество на macOS CPU:
# large-v3-turbo ≈ в 3–5× быстрее large-v3 при близком качестве (особенно ru).
DEFAULT_MODEL = "large-v3-turbo"


def _resolve_device_compute(device: str, compute_type: str) -> tuple[str, str]:
    """На Apple Silicon MPS для large нестабилен → CPU + int8."""
    if device != "auto" and compute_type != "auto":
        return device, compute_type
    return (
        "cpu" if device == "auto" else device,
        "int8" if compute_type == "auto" else compute_type,
    )


def _smart_batch_size() -> int:
    """Крупный batch ускоряет int8 на CPU при достаточной RAM."""
    try:
        import psutil
        free_gb = psutil.virtual_memory().available / (1024 ** 3)
        if free_gb > 14:
            return 32
        if free_gb > 8:
            return 24
        if free_gb > 4:
            return 16
        return 8
    except Exception:
        return 16


def transcribe(
    audio_path: str,
    model_name: str = DEFAULT_MODEL,
    whisperx_path: str = "",
    language: str = "ru",
    device: str = "auto",
    compute_type: str = "auto",
    log_fn=None,
) -> dict:
    """Один вызов WhisperX с пословными таймкодами.

    По умолчанию: large-v3-turbo + cpu + int8 + batch 16–32 + все ядра.
    Макс. качество: model=large-v3 в GUI.
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
    batch_size = _smart_batch_size()
    n_threads = os.cpu_count() or 8
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
        wx_env.setdefault("OMP_NUM_THREADS", str(n_threads))
        wx_env.setdefault("MKL_NUM_THREADS", str(n_threads))

        _log("[WHISPER] Запуск распознавания (один раз)...")
        result = subprocess.run(cmd, capture_output=True, text=True, env=wx_env)

        if result.returncode != 0:
            err = result.stderr or result.stdout or ""
            if model_name == "large-v3-turbo" and (
                "large-v3-turbo" in err.lower()
                or "not found" in err.lower()
                or "404" in err
                or "does not exist" in err.lower()
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
