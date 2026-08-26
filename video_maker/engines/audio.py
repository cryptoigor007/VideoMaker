"""Движок аудио — 48kHz, BGM, loudnorm."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile

log = logging.getLogger(__name__)


def probe_duration(path: str) -> float:
    """Получить длительность медиафайла в секундах."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def probe_sample_rate(path: str) -> int:
    """Получить частоту дискретизации аудио."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if streams:
        return int(streams[0].get("sample_rate", 0))
    return 0


def replace_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    log_fn=None,
) -> str:
    """Заменить аудиодорожку в видео, ресемпл в 48kHz."""
    _log = log_fn or log.info
    _log(f"[АУДИО] Замена аудио в {os.path.basename(video_path)}")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def mix_bgm(
    video_path: str,
    bgm_folder: str,
    output_path: str,
    log_fn=None,
) -> str:
    """Смешать голос с BGM (sidechain compression)."""
    _log = log_fn or log.info
    _log("[АУДИО] Смешивание с BGM...")

    bgm_files = [
        os.path.join(bgm_folder, f)
        for f in os.listdir(bgm_folder)
        if f.lower().endswith((".mp3", ".wav", ".flac", ".m4a"))
    ]
    if not bgm_files:
        _log("[АУДИО] BGM файлы не найдены, пропускаем")
        return video_path

    import random
    bgm_file = random.choice(bgm_files)

    filter_complex = (
        "[1:a]aloop=loop=-1:size=2e9,atrim=0:300,"
        "loudnorm=I=-18:TP=-3:LRA=11[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:"
        "weights=1 0.3:dropout_transition=3[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", bgm_file,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[out]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def measure_loudness(path: str) -> dict | None:
    """Замерить громкость (LUFS и true peak) через ebur128."""
    cmd = [
        "ffmpeg", "-i", path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    stderr = result.stderr

    i_lufs = None
    peak_dbtp = None

    lines = stderr.split("\n")
    for i, line in enumerate(lines):
        # Ищем "I:         -26.9 LUFS" после "Integrated loudness:"
        if "I:" in line and "LUFS" in line:
            parts = line.split("I:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    i_lufs = float(val)
                except (ValueError, IndexError):
                    pass
        # Ищем "Peak:      -22.9 dBFS" или "TPK: -22.9 dBFS" после "True peak:"
        if "Peak:" in line and "dBFS" in line:
            parts = line.split("Peak:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    peak_dbtp = float(val)
                except (ValueError, IndexError):
                    pass
        if "TPK:" in line and "dBFS" in line:
            parts = line.split("TPK:")
            if len(parts) >= 2:
                try:
                    val = parts[1].strip().split()[0]
                    peak_dbtp = float(val)
                except (ValueError, IndexError):
                    pass

    if i_lufs is None:
        return None

    return {"i_lufs": i_lufs, "peak_dbtp": peak_dbtp or 0.0}


def judge_loudness(i_lufs: float | None) -> str:
    """Оценить громкость по порогам."""
    if i_lufs is None:
        return "?"
    if -20 <= i_lufs <= -13:
        return "ok"
    if i_lufs < -20:
        return "тихо"
    return "громко"
