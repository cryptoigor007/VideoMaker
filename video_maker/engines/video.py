"""Движок видео — ffmpeg-операции: склейка, vstack, обрезка, интро/аутро."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

log = logging.getLogger(__name__)


def collect_video_files(folder: str) -> list[str]:
    """Собрать все видеофайлы из папки."""
    exts = (".mp4", ".mov", ".avi", ".mkv", ".webm")
    files = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(exts):
            files.append(os.path.join(folder, f))
    return files


def fit_video_to_duration(
    video_files: list[str],
    target_duration: float,
    output_path: str,
    audio_file: str = "",
    log_fn=None,
) -> str:
    """Нарезать и склеить видео под целевую длительность."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Склейка {len(video_files)} файлов под {target_duration:.1f} сек")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    # Простой fallback: берём первый файл и обрезаем
    cmd = [
        "ffmpeg", "-y",
        "-i", video_files[0],
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast",
        "-an",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    # Накладываем аудио если есть
    if audio_file and os.path.exists(audio_file):
        from .audio import replace_audio
        tmp_out = output_path + ".tmp.mp4"
        os.rename(output_path, tmp_out)
        replace_audio(tmp_out, audio_file, output_path, log_fn=_log)
        os.remove(tmp_out)

    return output_path


def vstack_video_image(
    video_path: str,
    background_path: str,
    output_path: str,
    log_fn=None,
) -> str:
    """vstack: видео сверху + фон/изображение снизу (9:16)."""
    _log = log_fn or log.info
    _log("[ВИДЕО] Создание вертикального видео (vstack)")

    # Проверяем расширение фона
    ext = os.path.splitext(background_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif"):
        # Изображение → конвертируем в видео нужной длительности
        from .audio import probe_duration
        dur = probe_duration(video_path)

        filter_complex = (
            f"[0:v]scale=1080:-2[v0];"
            f"[1:v]scale=1080:1080,setsar=1,loop=loop=-1:size=1:start,"
            f"trim=duration={dur}[v1];"
            f"[v0][v1]vstack=inputs=2[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast",
            "-t", str(dur),
            output_path,
        ]
    else:
        # Видео → vstack напрямую
        filter_complex = (
            "[0:v]scale=1080:-2[v0];"
            "[1:v]scale=1080:1080,setsar=1[v1];"
            "[v0][v1]vstack=inputs=2[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast",
            "-shortest",
            output_path,
        ]

    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def cut_segment(
    video_path: str,
    start: float,
    duration: float,
    output_path: str,
    log_fn=None,
) -> str:
    """Обрезать сегмент из видео."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Обрезка: {start:.1f} — {start + duration:.1f}")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def add_intro_outro_mid(
    video_path: str,
    intro_outro_folder: str,
    enable_intro: bool = False,
    enable_middle: bool = False,
    enable_outro: bool = False,
    output_dir: str = "",
    log_fn=None,
) -> str:
    """Добавить интро/аутро/мидл к видео."""
    _log = log_fn or log.info
    _log("[ВИДЕО] Добавление интро/аутро/мидл...")

    # Пока заглушка — просто возвращаем исходное видео
    # TODO: реализовать склейку с интро/аутро/мидл
    return video_path
