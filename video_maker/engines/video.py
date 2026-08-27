"""Движок видео — ffmpeg-операции: склейка, vstack, обрезка, интро/аутро."""
from __future__ import annotations

import logging
import os
import random
import subprocess
import tempfile

log = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    """ffmpeg с поддержкой libass."""
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.exists(path):
            return str(path)
    except Exception:
        pass
    return "ffmpeg"


def _ffprobe_video_info(video_path: str) -> tuple[int, int, float]:
    """Получить ширину, высоту и fps видео."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    width = stream["width"]
    height = stream["height"]
    # Парсим fps из строки типа "30000/1001"
    fps_str = stream.get("r_frame_rate", "30/1")
    num, den = map(int, fps_str.split("/"))
    fps = num / den if den else 30.0
    return width, height, fps


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
    """Нарезать и склеить видео под целевую длительность через concat demuxer."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Склейка {len(video_files)} файлов под {target_duration:.1f} сек")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    ffmpeg = _ffmpeg_bin()

    # Перемешиваем файлы для случайного порядка
    shuffled = video_files.copy()
    random.shuffle(shuffled)

    # Собираем файлы до покрытия target_duration
    selected = []
    total_dur = 0.0
    for vf in shuffled:
        dur = probe_duration(vf)
        selected.append(vf)
        total_dur += dur
        if total_dur >= target_duration:
            break

    # Если одного прохода не хватило — зацикливаем список
    while total_dur < target_duration:
        for vf in shuffled:
            dur = probe_duration(vf)
            selected.append(vf)
            total_dur += dur
            if total_dur >= target_duration:
                break

    # Создаем файл списка для concat demuxer
    list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(list_path, "w") as f:
        for vf in selected:
            f.write(f"file '{vf}'\n")

    # Concat demuxer с перекодированием для нормализации
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-an",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    # Удаляем временный файл списка
    try:
        os.remove(list_path)
    except OSError:
        pass

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
    """vstack: видео сверху + фон/изображение снизу (9:16) с динамическим разрешением."""
    _log = log_fn or log.info
    _log("[ВИДЕО] Создание вертикального видео (vstack)")

    ffmpeg = _ffmpeg_bin()

    # Получаем разрешение основного видео
    main_w, main_h, _ = _ffprobe_video_info(video_path)

    # Целевое разрешение 9:16 на основе ширины основного видео
    target_w = main_w
    target_h = int(main_w * 16 / 9)  # 9:16 aspect ratio

    # Высота верхней части (основное видео) и нижней (фон)
    # Делим пропорционально: верх ~ 60%, низ ~ 40% (можно настроить)
    top_h = int(target_h * 0.6)
    bottom_h = target_h - top_h

    # Проверяем расширение фона
    ext = os.path.splitext(background_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif"):
        # Изображение → конвертируем в видео нужной длительности
        from .audio import probe_duration
        dur = probe_duration(video_path)

        filter_complex = (
            f"[0:v]scale={target_w}:{top_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{top_h}:(ow-iw)/2:(oh-ih)/2[v0];"
            f"[1:v]scale={target_w}:{bottom_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{bottom_h}:(ow-iw)/2:(oh-ih)/2,"
            f"loop=loop=-1:size=1:start,trim=duration={dur}[v1];"
            f"[v0][v1]vstack=inputs=2[out]"
        )

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", str(dur),
            output_path,
        ]
    else:
        # Видео → vstack напрямую
        filter_complex = (
            f"[0:v]scale={target_w}:{top_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{top_h}:(ow-iw)/2:(oh-ih)/2[v0];"
            f"[1:v]scale={target_w}:{bottom_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{bottom_h}:(ow-iw)/2:(oh-ih)/2[v1];"
            f"[v0][v1]vstack=inputs=2[out]"
        )

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", "30",
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

    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y",
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
    """Добавить интро/аутро/мидл к видео (умная вставка middle)."""
    _log = log_fn or log.info
    _log("[ВИДЕО] Добавление интро/аутро/мидл...")

    if not (enable_intro or enable_middle or enable_outro):
        return video_path

    ffmpeg = _ffmpeg_bin()
    if not output_dir:
        output_dir = os.path.dirname(video_path)
    os.makedirs(output_dir, exist_ok=True)

    # Получаем длительность основного видео
    main_dur = probe_duration(video_path)

    # Собираем пути к файлам
    intro_path = ""
    middle_path = ""
    outro_path = ""

    if enable_intro:
        intro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in os.listdir(intro_outro_folder)
            if f.lower().startswith("intro") and f.lower().endswith((".mp4", ".mov"))
        ]
        if intro_candidates:
            intro_path = intro_candidates[0]

    if enable_middle:
        middle_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in os.listdir(intro_outro_folder)
            if f.lower().startswith("middle") and f.lower().endswith((".mp4", ".mov"))
        ]
        if middle_candidates:
            middle_path = middle_candidates[0]

    if enable_outro:
        outro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in os.listdir(intro_outro_folder)
            if f.lower().startswith("outro") and f.lower().endswith((".mp4", ".mov"))
        ]
        if outro_candidates:
            outro_path = outro_candidates[0]

    # Если нет файлов для добавления — возвращаем исходное
    if not (intro_path or middle_path or outro_path):
        _log("[ВИДЕО] Файлы интро/мидл/аутро не найдены, пропускаем")
        return video_path

    # Строим filter_complex для склейки
    # Middle вставляется в середину основного видео
    parts = []
    inputs = [video_path]
    input_idx = 1

    if intro_path:
        parts.append(f"[{input_idx}:v]")
        inputs.append(intro_path)
        input_idx += 1

    # Основное видео разбиваем на две части для вставки middle
    if middle_path:
        mid_point = main_dur / 2
        parts.append(f"[0:v]trim=0:{mid_point},setpts=PTS-STARTPTS[v_main1]")
        parts.append(f"[{input_idx}:v]")
        inputs.append(middle_path)
        input_idx += 1
        parts.append(f"[0:v]trim={mid_point}:{main_dur},setpts=PTS-STARTPTS[v_main2]")
        parts.append("[v_main1][v_main2]concat=n=2:v=1:a=0[v_main]")
    else:
        parts.append("[0:v]")

    if outro_path:
        parts.append(f"[{input_idx}:v]")
        inputs.append(outro_path)
        input_idx += 1

    # Собираем итоговый filter_complex
    if middle_path:
        # Сложная схема с middle в середине
        filter_parts = []
        idx = 1
        if intro_path:
            filter_parts.append(f"[{idx}:v]scale={1920}:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_intro];")
            idx += 1
        filter_parts.append(f"[0:v]trim=0:{main_dur/2},setpts=PTS-STARTPTS[v_main1];")
        if middle_path:
            filter_parts.append(f"[{idx}:v]scale={1920}:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_mid];")
            idx += 1
        filter_parts.append(f"[0:v]trim={main_dur/2}:{main_dur},setpts=PTS-STARTPTS[v_main2];")
        concat_inputs = []
        if intro_path:
            concat_inputs.append("[v_intro]")
        concat_inputs.append("[v_main1]")
        if middle_path:
            concat_inputs.append("[v_mid]")
        concat_inputs.append("[v_main2]")
        if outro_path:
            filter_parts.append(f"[{idx}:v]scale={1920}:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_outro];")
            concat_inputs.append("[v_outro]")
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[outv]")
        filter_complex = "".join(filter_parts)
    else:
        # Простая схема: intro + main + outro
        filter_parts = []
        idx = 1
        concat_inputs = []
        if intro_path:
            filter_parts.append(f"[{idx}:v]scale={1920}:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_intro];")
            concat_inputs.append("[v_intro]")
            idx += 1
        concat_inputs.append("[0:v]")
        if outro_path:
            filter_parts.append(f"[{idx}:v]scale={1920}:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_outro];")
            concat_inputs.append("[v_outro]")
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[outv]")
        filter_complex = "".join(filter_parts)

    output_path = os.path.join(output_dir, "with_intro_outro.mp4")

    cmd = [
        ffmpeg, "-y",
        *[arg for inp in inputs for arg in ("-i", inp)],
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-an",  # аудио наложим отдельно
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    return output_path


def probe_duration(path: str) -> float:
    """Получить длительность медиафайла в секундах."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])