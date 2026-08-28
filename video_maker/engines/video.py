"""Движок видео — ffmpeg-операции: склейка, vstack, обрезка, интро/аутро (4K + Apple Silicon VideoToolbox)."""
from __future__ import annotations

import logging
import os
import random
import subprocess
import uuid

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
    
    streams = data.get("streams")
    if not streams:
        raise ValueError(f"Не удалось найти видеопоток в файле {video_path}")
        
    stream = streams[0]
    width = stream.get("width", 3840)
    height = stream.get("height", 2160)
    
    fps_str = stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = map(int, fps_str.split("/"))
        fps = num / den if den != 0 else 30.0
    else:
        try:
            fps = float(fps_str)
        except ValueError:
            fps = 30.0

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
    """Нарезать и склеить видео под целевую длительность в 4K (3840x2160) с ускорением M1."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО 4K M1] Склейка {len(video_files)} файлов под {target_duration:.1f} сек")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    ffmpeg = _ffmpeg_bin()

    shuffled = video_files.copy()
    random.shuffle(shuffled)

    selected = []
    total_dur = 0.0
    for vf in shuffled:
        dur = probe_duration(vf)
        selected.append(vf)
        total_dur += dur
        if total_dur >= target_duration:
            break

    while total_dur < target_duration:
        for vf in shuffled:
            dur = probe_duration(vf)
            selected.append(vf)
            total_dur += dur
            if total_dur >= target_duration:
                break

    normalized_files = []
    tmp_dir = os.path.join(os.path.dirname(output_path), "_concat_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    
    for i, vf in enumerate(selected):
        norm_path = os.path.join(tmp_dir, f"norm_{i:03d}.mp4")
        cmd_norm = [
            ffmpeg, "-y",
            "-i", vf,
            "-vf", "scale=3840:2160:force_original_aspect_ratio=decrease,pad=3840:2160:(ow-iw)/2:(oh-ih)/2,fps=30",
            "-c:v", "h264_videotoolbox", "-b:v", "20M",
            "-pix_fmt", "yuv420p",
            "-an",
            norm_path,
        ]
        res = subprocess.run(cmd_norm, capture_output=True, text=True)
        if res.returncode != 0:
            _log(f"[ВИДЕО] Ошибка нормализации клипа {vf}: {res.stderr}")
            raise RuntimeError(f"Norm failed: {res.stderr}")
        normalized_files.append(norm_path)

    list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(list_path, "w") as f:
        f.writelines(f"file '{nf}'\n" for nf in normalized_files)

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-t", str(target_duration),
        "-c:v", "h264_videotoolbox", "-b:v", "20M",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-an",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        _log(f"[ВИДЕО] Ошибка concat: {res.stderr}")
        raise RuntimeError(f"Concat failed: {res.stderr}")

    for p in [list_path] + normalized_files:
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass

    if audio_file and os.path.exists(audio_file):
        from .audio import replace_audio
        tmp_out = output_path + ".tmp.mp4"
        os.rename(output_path, tmp_out)
        replace_audio(tmp_out, audio_file, output_path, log_fn=_log)
        os.remove(tmp_out)

    return output_path


def _even(n: int) -> int:
    """Округлить до чётного (требуется для yuv420p / libx264)."""
    n = int(n)
    return n if n % 2 == 0 else n - 1


def vstack_video_image(
    video_path: str,
    background_path: str,
    output_path: str,
    log_fn=None,
    top_ratio: float = 0.6,
) -> str:
    """vstack: видео сверху + фон/изображение снизу (4K 2160x3840) с ускорением M1."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО 4K M1] Создание вертикального видео (vstack), top_ratio={top_ratio}")

    ffmpeg = _ffmpeg_bin()

    target_w = 2160
    target_h = 3840
    top_h = _even(int(target_h * top_ratio))
    bottom_h = _even(target_h - top_h)
    target_h = top_h + bottom_h

    _log(f"[ВИДЕО 4K] vstack size={target_w}x{target_h} top={top_h} bottom={bottom_h}")

    ext = os.path.splitext(background_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif"):
        from .audio import probe_duration
        dur = probe_duration(video_path)

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-loop", "1", "-i", background_path,
            "-filter_complex", (
                f"[0:v]scale={target_w}:{top_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{top_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
                f"[1:v]scale={target_w}:{bottom_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{bottom_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
                f"[v0][v1]vstack=inputs=2[out]"
            ),
            "-map", "[out]",
            "-c:v", "h264_videotoolbox", "-b:v", "20M",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-t", str(dur),
            output_path,
        ]
    else:
        filter_complex = (
            f"[0:v]scale={target_w}:{top_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{top_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
            f"[1:v]scale={target_w}:{bottom_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{bottom_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
            f"[v0][v1]vstack=inputs=2[out]"
        )

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "h264_videotoolbox", "-b:v", "20M",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-shortest",
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "")[-800:]
        _log(f"[ВИДЕО] vstack ffmpeg error: {err}")
        raise RuntimeError(f"vstack failed (exit {result.returncode}): {err}")

    from .audio import replace_audio
    tmp_out = output_path + ".tmp.mp4"
    os.rename(output_path, tmp_out)
    replace_audio(tmp_out, video_path, output_path, log_fn=_log)
    os.remove(tmp_out)

    return output_path


def cut_segment(
    video_path: str,
    start: float,
    duration: float,
    output_path: str,
    log_fn=None,
) -> str:
    """Ообрезать сегмент из видео."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Обрезка: {start:.1f} — {start + duration:.1f}")

    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "h264_videotoolbox", "-b:v", "20M",
        "-c:a", "aac",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        _log(f"[ВИДЕО] Ошибка обрезки: {res.stderr}")
        raise RuntimeError(f"cut_segment failed: {res.stderr}")
    return output_path


def add_intro_outro_mid(
    video_path: str,
    intro_outro_folder: str,
    enable_intro: bool = False,
    enable_middle: bool = False,
    enable_outro: bool = False,
    output_dir: str = "",
    log_fn=None,
    analysis: dict | None = None,
    explicit_intro: str = "",
    explicit_middle: str = "",
    explicit_outro: str = "",
) -> str:
    """Добавить интро/аутро/мидл к видео."""
    _log = log_fn or log.info
    _log("[ВИДЕО 4K M1] Добавление интро/аутро/мидл...")

    if not (enable_intro or enable_middle or enable_outro):
        return video_path

    ffmpeg = _ffmpeg_bin()
    if not output_dir:
        output_dir = os.path.dirname(video_path)
    os.makedirs(output_dir, exist_ok=True)

    main_w, main_h, _ = _ffprobe_video_info(video_path)
    main_dur = probe_duration(video_path)

    if analysis and enable_middle:
        middle_timing = analysis.get("middle", [])
        if middle_timing and isinstance(middle_timing, list) and len(middle_timing) > 0:
            mid_point = float(middle_timing[0].get("start", main_dur / 2))
            _log(f"[ВИДЕО] Middle timing из analysis: {mid_point:.1f}s")
        else:
            mid_point = main_dur / 2
    else:
        mid_point = main_dur / 2

    intro_path = explicit_intro if explicit_intro and os.path.isfile(explicit_intro) else ""
    middle_path = explicit_middle if explicit_middle and os.path.isfile(explicit_middle) else ""
    outro_path = explicit_outro if explicit_outro and os.path.isfile(explicit_outro) else ""

    folder_files: list[str] = []
    if intro_outro_folder and os.path.isdir(intro_outro_folder):
        try:
            folder_files = os.listdir(intro_outro_folder)
        except OSError as e:
            _log(f"[ВИДЕО] Не удалось прочитать папку intro/middle/outro: {e}")

    if enable_intro and not intro_path and folder_files:
        intro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("intro") and f.lower().endswith((".mp4", ".mov"))
        ]
        if intro_candidates:
            intro_path = intro_candidates[0]

    if enable_middle and not middle_path and folder_files:
        middle_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("middle") and f.lower().endswith((".mp4", ".mov"))
        ]
        if middle_candidates:
            middle_path = middle_candidates[0]

    if enable_outro and not outro_path and folder_files:
        outro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("outro") and f.lower().endswith((".mp4", ".mov"))
        ]
        if outro_candidates:
            outro_path = outro_candidates[0]

    if not (intro_path or middle_path or outro_path):
        _log("[ВИДЕО] Файлы интро/мидл/аутро не найдены, пропускаем")
        return video_path

    inputs = [video_path]

    if intro_path:
        inputs.append(intro_path)
    if middle_path:
        inputs.append(middle_path)
    if outro_path:
        inputs.append(outro_path)

    scale_filter = f"scale={main_w}:{main_h}:force_original_aspect_ratio=decrease,pad={main_w}:{main_h}:(ow-iw)/2:(oh-ih)/2"

    if middle_path:
        filter_parts = []
        idx = 1
        if intro_path:
            filter_parts.append(f"[{idx}:v]{scale_filter}[v_intro];")
            idx += 1
        filter_parts.append(f"[0:v]trim=0:{mid_point},setpts=PTS-STARTPTS[v_main1];")
        if middle_path:
            filter_parts.append(f"[{idx}:v]{scale_filter}[v_mid];")
            idx += 1
        filter_parts.append(f"[0:v]trim={mid_point}:{main_dur},setpts=PTS-STARTPTS[v_main2];")
        concat_inputs = []
        if intro_path:
            concat_inputs.append("[v_intro]")
        concat_inputs.append("[v_main1]")
        if middle_path:
            concat_inputs.append("[v_mid]")
        concat_inputs.append("[v_main2]")
        if outro_path:
            filter_parts.append(f"[{idx}:v]{scale_filter}[v_outro];")
            concat_inputs.append("[v_outro]")
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[outv]")
        filter_complex = "".join(filter_parts)
    else:
        filter_parts = []
        idx = 1
        concat_inputs = []
        if intro_path:
            filter_parts.append(f"[{idx}:v]{scale_filter}[v_intro];")
            concat_inputs.append("[v_intro]")
            idx += 1
        concat_inputs.append("[0:v]")
        if outro_path:
            filter_parts.append(f"[{idx}:v]{scale_filter}[v_outro];")
            concat_inputs.append("[v_outro]")
        filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[outv]")
        filter_complex = "".join(filter_parts)

    output_path = os.path.join(output_dir, f"with_intro_outro_{uuid.uuid4().hex[:8]}.mp4")

    cmd = [
        ffmpeg, "-y",
        *[arg for inp in inputs for arg in ("-i", inp)],
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "h264_videotoolbox", "-b:v", "20M",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        _log(f"[ВИДЕО] Ошибка добавления intro/outro: {res.stderr}")
        raise RuntimeError(f"add_intro_outro_mid failed: {res.stderr}")

    from .audio import replace_audio
    tmp_out = output_path + ".tmp.mp4"
    os.rename(output_path, tmp_out)
    replace_audio(tmp_out, video_path, output_path, log_fn=_log)
    os.remove(tmp_out)

    return output_path


from .audio import probe_duration
