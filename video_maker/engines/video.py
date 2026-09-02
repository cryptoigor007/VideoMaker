# VideoMaker FIX | 2026.09.02-r14 | 2026-09-02
# CHANGED: intro/outro/middle = REPLACE сегментов main (не удлиняют ролик)
#   intro → first N sec, outro → last M sec, middle @ Gemini mid_point
#   audio = master only, без pad/silence; тайминги субтитров не плывут
# PREV: 2026.09.02-r13 (append/pad — сломало звук и субтитры)
# REPLACE: video_maker/engines/video.py
"""Движок видео — ffmpeg-операции: склейка, vstack, обрезка, интро/аутро (4K + Apple Silicon VideoToolbox)."""
from __future__ import annotations

import logging
import os
import random
import subprocess
import uuid

from .ffmpeg_resilient import (
    VideoEncodeFailed,
    calculate_adaptive_bitrate,
    inject_hwaccel,
    run_vt_encode,
    vt_encode_args,
)

log = logging.getLogger(__name__)



def _work_tmp_dir(preferred: str = "") -> str:
    """Каталог для промежуточных файлов: предпочитаем внутренний диск, не /Volumes."""
    candidates = []
    if preferred and not str(preferred).startswith("/Volumes/"):
        candidates.append(preferred)
    home_tmp = os.path.join(os.path.expanduser("~"), "video_maker", "_tmp")
    candidates.append(home_tmp)
    candidates.append("/tmp/video_maker")
    if preferred:
        candidates.append(preferred)
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, f".w_{os.getpid()}")
            with open(test, "wb") as f:
                f.write(b"1")
            os.remove(test)
            return d
        except OSError:
            continue
    return preferred or "/tmp"


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
    """Получить ширину, высоту и fps видео. Устойчиво к битым файлам."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        raise ValueError(f"ffprobe failed for {video_path}: {e}") from e
    raw = (result.stdout or "").strip()
    if not raw:
        raise ValueError(f"Пустой ответ ffprobe для {video_path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Невалидный JSON ffprobe для {video_path}: {e}") from e

    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"Не удалось найти видеопоток в файле {video_path}")

    stream = streams[0]
    width = stream.get("width") or 3840
    height = stream.get("height") or 2160

    fps_str = stream.get("r_frame_rate") or "30/1"
    if "/" in str(fps_str):
        try:
            num, den = map(int, str(fps_str).split("/"))
            fps = num / den if den != 0 else 30.0
        except Exception:
            fps = 30.0
    else:
        try:
            fps = float(fps_str)
        except (TypeError, ValueError):
            fps = 30.0

    return int(width), int(height), float(fps)


# Папка использованных B-roll (исключается из ротации)
USED_DIR_NAME = "used"
_SKIP_SUBDIR_NAMES = frozenset({
    "used", "использованная", "использованные", "использовано",
    "_tmp", ".ds_store", "__macosx",
})
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")


def collect_video_files(folder: str, rotate_subfolders: bool = True) -> list[str]:
    """Собрать видео из папки B-roll.

    Если есть подпапки (кроме used) — ротация по подпапкам:
      1-й клип из подпапки A, 2-й из B, … interleave.
    Папка used / использованная никогда не сканируется.
    Если подпапок нет — плоский список файлов в folder.
    """
    if not folder or not os.path.isdir(folder):
        return []

    def _is_video(name: str) -> bool:
        # ._file.mp4 — AppleDouble/resource fork на macOS (не видео)
        if not name or name.startswith(".") or name.startswith("._"):
            return False
        return name.lower().endswith(_VIDEO_EXTS)

    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return []

    subdirs: list[str] = []
    for name in entries:
        if name.startswith("."):
            continue
        if name.lower() in _SKIP_SUBDIR_NAMES:
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            subdirs.append(path)

    # Файлы прямо в корне B-roll
    root_files: list[str] = []
    for name in entries:
        if name.startswith(".") or name.lower() in _SKIP_SUBDIR_NAMES:
            continue
        full = os.path.join(folder, name)
        if os.path.isfile(full) and _is_video(name):
            root_files.append(full)

    if rotate_subfolders and subdirs:
        queues: list[list[str]] = []
        for sd in subdirs:
            try:
                files = [
                    os.path.join(sd, f)
                    for f in sorted(os.listdir(sd))
                    if _is_video(f) and os.path.isfile(os.path.join(sd, f))
                ]
            except OSError:
                files = []
            if files:
                queues.append(files)
        # Если подпапки пустые — fallback на корень
        if not queues:
            return root_files
        result: list[str] = []
        max_len = max(len(q) for q in queues)
        for i in range(max_len):
            for q in queues:
                if i < len(q):
                    result.append(q[i])
        # Клипы из корня тоже участвуют (после ротации тем)
        result.extend(root_files)
        return result

    return root_files


def move_to_used(root_folder: str, paths: list[str], log_fn=None) -> int:
    """Перенести использованные клипы в {root_folder}/used/."""
    import shutil

    _log = log_fn or log.info
    if not root_folder or not paths:
        return 0
    root_abs = os.path.abspath(root_folder)
    used_dir = os.path.join(root_abs, USED_DIR_NAME)
    try:
        os.makedirs(used_dir, exist_ok=True)
    except OSError as e:
        _log(f"[B-ROLL] не удалось создать {used_dir}: {e}")
        return 0

    moved = 0
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        abs_p = os.path.abspath(p)
        if abs_p in seen:
            continue
        seen.add(abs_p)
        if not os.path.isfile(abs_p):
            continue
        try:
            common = os.path.commonpath([root_abs, abs_p])
        except ValueError:
            continue
        if common != root_abs:
            continue
        parts_lower = {x.lower() for x in abs_p.split(os.sep)}
        if USED_DIR_NAME in parts_lower or "использованная" in parts_lower:
            continue
        base = os.path.basename(abs_p)
        dest = os.path.join(used_dir, base)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(base)
            n = 1
            while os.path.exists(dest):
                dest = os.path.join(used_dir, f"{stem}_{n}{ext}")
                n += 1
        try:
            shutil.move(abs_p, dest)
            moved += 1
            _log(f"[B-ROLL] → used: {base}")
        except OSError as e:
            _log(f"[B-ROLL] move fail {base}: {e}")
    if moved:
        _log(f"[B-ROLL] в used перенесено: {moved}")
    return moved


def fit_video_to_duration(
    video_files: list[str],
    target_duration: float,
    output_path: str,
    audio_file: str = "",
    log_fn=None,
    broll_root: str = "",
    move_used: bool = True,
) -> str:
    """Одна команда 4K: scale на лету → concat → trim → твоё аудио.
    Без промежуточных norm_XXX.mp4. 3840x2160 @ 28M.
    """
    _log = log_fn or log.info
    width, height = 3840, 2160
    bitrate = "28M"
    _log(f"[ВИДЕО 4K] Быстрая склейка под {target_duration:.1f}с ({width}x{height} @ {bitrate})")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    ffmpeg = _ffmpeg_bin()

    selected: list[str] = []
    total_dur = 0.0
    skipped_bad = 0
    for vf in video_files:
        base = os.path.basename(vf)
        if base.startswith(".") or base.startswith("._"):
            skipped_bad += 1
            continue
        try:
            dur = probe_duration(vf)
        except Exception as e:
            skipped_bad += 1
            _log(f"[ВИДЕО] пропуск (ffprobe): {base} — {e}")
            continue
        if dur <= 0.05:
            skipped_bad += 1
            # не спамим лог на каждый битый/пустой — только итог
            continue
        selected.append(vf)
        total_dur += dur
        _log(f"[ВИДЕО] + {os.path.basename(vf)} ({dur:.1f}s) → сумма {total_dur:.1f}s")
        if total_dur >= target_duration:
            break

    if skipped_bad:
        _log(f"[ВИДЕО] Пропущено проблемных клипов: {skipped_bad}")

    if total_dur < target_duration and selected:
        _log(f"[ВИДЕО] Все клипы короче цели ({total_dur:.1f} < {target_duration:.1f}), повторяем")
        safety = 0
        while total_dur < target_duration and safety < 200:
            safety += 1
            for vf in list(selected):
                try:
                    dur = probe_duration(vf)
                except Exception:
                    continue
                if dur <= 0.05:
                    continue
                selected.append(vf)
                total_dur += dur
                if total_dur >= target_duration:
                    break

    if not selected:
        raise FileNotFoundError("Не удалось набрать ни одного валидного клипа")

    _log(f"[ВИДЕО] Выбрано клипов: {len(selected)} (сумма {total_dur:.1f}s ≥ {target_duration:.1f}s)")

    n = len(selected)
    inputs: list[str] = []
    for vf in selected:
        # decode B-roll на VideoToolbox (Apple Silicon)
        inputs.extend(["-hwaccel", "videotoolbox", "-i", vf])

    has_audio = bool(audio_file and os.path.exists(audio_file))
    if has_audio:
        # аудио — без hwaccel
        inputs.extend(["-i", audio_file])

    filters = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(n))
    filters.append(f"{concat_in}concat=n={n}:v=1:a=0[v]")
    filter_complex = ";".join(filters)

    cmd = [
        ffmpeg, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-t", str(target_duration),
        "-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-r", "30",
    ]
    if has_audio:
        cmd += [
            "-map", f"{n}:a",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
        ]
    else:
        cmd += ["-an"]
    cmd.append(output_path)
    cmd = inject_hwaccel(cmd)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    _log(f"[ВИДЕО 4K] Одна команда: {n} клипов → {os.path.basename(output_path)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "")[-600:]
        _log(f"[ВИДЕО] Ошибка: {err}")
        raise RuntimeError(f"Быстрая склейка failed: {err}")

    if move_used and broll_root:
        move_to_used(broll_root, selected, log_fn=_log)

    return output_path


def _even(n: int) -> int:
    """Округлить до чётного (требуется для yuv420p / libx264)."""
    n = int(n)
    return n if n % 2 == 0 else n - 1



def _encode_vt_args(width: int = 3840, height: int = 2160, bitrate: str | None = None) -> list[str]:
    """Apple VideoToolbox only (no allow_sw / no libx264). Default ~28M for 4K."""
    pixels = max(1, int(width) * int(height))
    if bitrate:
        br = bitrate if str(bitrate).upper().endswith("M") else f"{bitrate}M"
    elif pixels >= 3000 * 1600:
        br = "28M"
    elif pixels >= 1800 * 1000:
        br = "16M"
    else:
        br = "10M"
    return vt_encode_args(br)

def _cached_bg_half(background_path: str, target_w: int, half_h: int, log_fn=None) -> str:
    """Один раз отскейлить фон до 2160x1920 и кэшировать (статика)."""
    _log = log_fn or log.info
    import hashlib
    st = os.stat(background_path)
    key = hashlib.sha256(
        f"{os.path.abspath(background_path)}|{st.st_mtime_ns}|{st.st_size}|{target_w}x{half_h}".encode()
    ).hexdigest()[:20]
    cache_dir = os.path.join(os.path.expanduser("~"), "video_maker", "cache", "bg")
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"{key}.png")
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        return out

    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y", "-i", background_path,
        "-vf", f"scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
               f"crop={target_w}:{half_h},setsar=1,format=rgb24",
        "-frames:v", "1",
        out,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.isfile(out):
        _log("[ВИДЕО] bg cache fail — используем исходник")
        return background_path
    _log(f"[ВИДЕО] bg cache → {out}")
    return out


def vstack_video_image(
    video_path: str,
    background_path: str,
    output_path: str,
    log_fn=None,
    top_ratio: float = 0.6,
    ass_path: str = "",
    bitrate: str = "28M",
) -> str:
    """Геометрия 9:16 БЕЗ subtitles (subs — отдельный быстрый burn).

    Граф: scale+crop video → top | cached bg → bottom | vstack → VT.
    ass_path игнорируется здесь (совместимость API) — subs жжёт FinalVertical.
    """
    import time
    _log = log_fn or log.info
    t0 = time.time()
    _log("[ВИДЕО 4K] vertical geometry-only (subs отдельно)")

    ffmpeg = _ffmpeg_bin()
    target_w, target_h = 2160, 3840
    half_h = _even(target_h // 2)

    bg = _cached_bg_half(background_path, target_w, half_h, log_fn=_log)

    # Только video scale+crop; bg уже exact size — без второго scale
    fc = (
        f"[0:v]scale={target_w}:{half_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{half_h},setsar=1[top];"
        f"[1:v]setsar=1,format=yuv420p[bot];"
        f"[top][bot]vstack=inputs=2[outv]"
    )

    dur = probe_duration(video_path)
    local_dir = _work_tmp_dir(os.path.dirname(output_path) or ".")
    local_out = os.path.join(local_dir, f"vstack_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp4")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    def _run(use_hw: bool) -> subprocess.CompletedProcess:
        cmd = [ffmpeg, "-y"]
        if use_hw:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += [
            "-i", video_path,
            "-loop", "1", "-i", bg,
            "-filter_complex", fc,
            "-map", "[outv]",
            "-map", "0:a?",
            "-c:v", "h264_videotoolbox", "-b:v", bitrate,
            "-allow_sw", "0" if use_hw else "1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-c:a", "copy",
            "-t", str(dur),
            local_out,
        ]
        _log(f"[ВИДЕО 4K] geometry cmd hw={use_hw} bitrate={bitrate}")
        return subprocess.run(cmd, capture_output=True, text=True)

    res = _run(True)
    if res.returncode != 0:
        _log("[ВИДЕО] geometry hwaccel fail → retry")
        # cleanup partial
        try:
            if os.path.isfile(local_out):
                os.remove(local_out)
        except OSError:
            pass
        res = _run(False)
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "")[-900:]
        _log(f"[ВИДЕО] geometry ошибка: {err}")
        raise RuntimeError(f"VSTACK geometry failed: {err}")

    import shutil
    shutil.move(local_out, output_path)
    _log(f"[ВИДЕО 4K] geometry OK за {time.time()-t0:.1f}s → {os.path.basename(output_path)}")
    return output_path


def reframe_horizontal_to_vertical(
    video_path: str,
    output_path: str,
    log_fn=None,
    ass_path: str = "",
    bitrate: str = "28M",
) -> str:
    """16:9 → 9:16 cover-crop без фона. Без ASS (subs отдельно)."""
    import time
    _log = log_fn or log.info
    t0 = time.time()
    _log("[ВИДЕО 4K] reframe 16:9→9:16 geometry-only")

    ffmpeg = _ffmpeg_bin()
    w, h = 2160, 3840
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )
    dur = probe_duration(video_path)
    local_dir = _work_tmp_dir(os.path.dirname(output_path) or ".")
    local_out = os.path.join(local_dir, f"reframe_{os.getpid()}_{uuid.uuid4().hex[:8]}.mp4")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = [
        ffmpeg, "-y", "-hwaccel", "videotoolbox",
        "-i", video_path,
        "-vf", vf,
        "-map", "0:v", "-map", "0:a?",
        "-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "0",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "copy", "-t", str(dur),
        local_out,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        cmd = [
            ffmpeg, "-y", "-i", video_path, "-vf", vf,
            "-map", "0:v", "-map", "0:a?",
            "-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "1",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "copy", "-t", str(dur),
            local_out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "")[-600:]
            raise RuntimeError(f"reframe failed: {err}")

    import shutil
    shutil.move(local_out, output_path)
    _log(f"[ВИДЕО 4K] reframe OK за {time.time()-t0:.1f}s")
    return output_path


def cut_segment(
    video_path: str,
    start: float,
    duration: float,
    output_path: str,
    log_fn=None,
) -> str:
    """Обрезать сегмент. Сначала stream copy (быстро), fallback — VideoToolbox."""
    _log = log_fn or log.info
    _log(f"[ВИДЕО] Обрезка: {start:.1f} — {start + duration:.1f}")

    ffmpeg = _ffmpeg_bin()
    # Быстрый путь: copy (без перекодирования, качество = исходник)
    cmd_copy = [
        ffmpeg, "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    res = subprocess.run(cmd_copy, capture_output=True, text=True)
    if res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
        _log("[ВИДЕО] Обрезка: stream copy OK")
        return output_path

    _log("[ВИДЕО] Обрезка: copy не удался → VideoToolbox")
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "videotoolbox",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
    run_vt_encode(cmd, [video_path], output_path, log_fn=_log, stage_name="TRIM")
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
    intro_duration: float = 3.0,
    middle_duration: float = 1.0,
    outro_duration: float = 3.0,
) -> str:
    """Добавить интро/аутро/мидл к видео (видеофайл или картинка→N сек).

    Приоритет путей (строго):
    1. explicit_* — путь с вкладки Intro/Middle/Outro.
       Если указан — используется ТОЛЬКО он. Поиск по имени в папке НЕ выполняется.
    2. Папка intro_outro_folder — только если explicit_* пустой (не указан).
    """
    _log = log_fn or log.info
    _log("[ВИДЕО 4K M1] Добавление интро/аутро/мидл...")

    if not (enable_intro or enable_middle or enable_outro):
        _log("[IMO] все флаги выключены — пропуск")
        return video_path

    ffmpeg = _ffmpeg_bin()
    if not output_dir:
        output_dir = os.path.dirname(video_path) or "."
    work_dir = _work_tmp_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

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

    def _resolve_explicit(raw: str, label: str, enabled: bool) -> tuple[str, bool]:
        if not enabled:
            _log(f"[IMO] {label} выключен чекбоксом — путь/поиск игнорируются")
            return "", False
        raw = (raw or "").strip()
        if not raw:
            return "", False
        if os.path.isfile(raw):
            _log(f"[IMO] {label}: explicit OK → {raw}")
            return raw, True
        _log(f"[IMO] {label}: explicit указан, но файл НЕ найден: {raw}")
        return "", True

    intro_path, intro_explicit = _resolve_explicit(explicit_intro, "intro", enable_intro)
    middle_path, middle_explicit = _resolve_explicit(explicit_middle, "middle", enable_middle)
    outro_path, outro_explicit = _resolve_explicit(explicit_outro, "outro", enable_outro)

    _log(
        f"[IMO] flags intro={enable_intro} middle={enable_middle} outro={enable_outro} | "
        f"folder={intro_outro_folder or '(пусто)'} | "
        f"explicit_set intro={intro_explicit} middle={middle_explicit} outro={outro_explicit}"
    )

    need_folder = (
        (enable_intro and not intro_path and not intro_explicit)
        or (enable_middle and not middle_path and not middle_explicit)
        or (enable_outro and not outro_path and not outro_explicit)
    )

    folder_files: list[str] = []
    if need_folder and intro_outro_folder and os.path.isdir(intro_outro_folder):
        try:
            folder_files = os.listdir(intro_outro_folder)
            _log(f"[IMO] файлов в папке (fallback): {len(folder_files)}")
        except OSError as e:
            _log(f"[ВИДЕО] Не удалось прочитать папку intro/middle/outro: {e}")
    elif need_folder:
        _log(
            f"[IMO] папка IMO отсутствует/пустая, fallback недоступен: "
            f"{intro_outro_folder or '(не задана)'}"
        )

    _media = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".bmp", ".webp")

    if enable_intro and not intro_path and not intro_explicit and folder_files:
        intro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("intro") and f.lower().endswith(_media)
        ]
        if intro_candidates:
            intro_path = intro_candidates[0]
            _log(f"[IMO] intro (fallback папка): {intro_path}")

    if enable_middle and not middle_path and not middle_explicit and folder_files:
        middle_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("middle") and f.lower().endswith(_media)
        ]
        if middle_candidates:
            middle_path = middle_candidates[0]
            _log(f"[IMO] middle (fallback папка): {middle_path}")

    def _match_media(files: list[str], keywords: tuple[str, ...]) -> list[str]:
        out = []
        for f in files:
            low = f.lower()
            if not low.endswith(_media):
                continue
            stem = os.path.splitext(low)[0]
            if any(k in stem for k in keywords):
                out.append(os.path.join(intro_outro_folder, f))
        return out

    if enable_outro and not outro_path and not outro_explicit and folder_files:
        outro_candidates = _match_media(
            folder_files,
            ("outro", "outtro", "ending", "endcard", "end_card", "финал", "концовка"),
        )
        outro_candidates = [
            c for c in outro_candidates if "intro" not in os.path.basename(c).lower()
        ]
        if not outro_candidates:
            used = set()
            if intro_path:
                used.add(os.path.abspath(intro_path))
            if middle_path:
                used.add(os.path.abspath(middle_path))
            any_media = []
            for f in folder_files:
                low = f.lower()
                if not low.endswith(_media):
                    continue
                stem = os.path.splitext(low)[0]
                if any(k in stem for k in ("intro", "middle", "mid_")) and not intro_path:
                    continue
                full = os.path.abspath(os.path.join(intro_outro_folder, f))
                if full in used:
                    continue
                any_media.append(os.path.join(intro_outro_folder, f))
            outro_candidates = any_media
        if outro_candidates:
            outro_path = sorted(outro_candidates)[-1]
            _log(f"[IMO] outro (fallback папка): {outro_path}")
        else:
            _log(
                f"[IMO] enable_outro=True, explicit пуст, в папке нет media. "
                f"Файлы: {folder_files[:20]}"
            )
    elif enable_outro and not outro_path and outro_explicit:
        _log("[IMO] outro: explicit указан, файл отсутствует — поиск в папке ОТКЛЮЧЁН")

    def _ensure_video(path: str, label: str, duration: float = 1.0) -> str:
        if not path:
            return ""
        if not os.path.isfile(path):
            _log(f"[IMO] {label}: файл не существует: {path}")
            return ""
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"):
            _log(f"[IMO] {label}: видеофайл → {path}")
            return path
        dur = max(0.5, float(duration or 1.0))
        out = os.path.join(work_dir, f"{label}_still_{uuid.uuid4().hex[:8]}.mp4")
        w, h = _even(main_w), _even(main_h)
        try:
            img_cmd = [
                ffmpeg, "-y", "-loop", "1", "-i", path, "-t", str(dur),
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "0",
                "-pix_fmt", "yuv420p", "-r", "30", out,
            ]
            run_vt_encode(img_cmd, [path], out, log_fn=_log, stage_name="IMO")
        except Exception as e:
            _log(f"[IMO] картинка→{dur}с fail {path}: {e}")
            return ""
        _log(f"[IMO] {label}: картинка → {dur}с видео: {out}")
        return out

    intro_path = _ensure_video(intro_path, "intro", intro_duration) if intro_path else ""
    middle_path = _ensure_video(middle_path, "middle", middle_duration) if middle_path else ""
    outro_path = _ensure_video(outro_path, "outro", outro_duration) if outro_path else ""

    _log(
        f"[IMO] итог путей: intro={intro_path or '—'} | "
        f"middle={middle_path or '—'} | outro={outro_path or '—'}"
    )

    if not (intro_path or middle_path or outro_path):
        _log("[ВИДЕО] Файлы интро/мидл/аутро не найдены, пропускаем")
        return video_path


    # ============================================================
    # r14: intro/outro/middle НЕ удлиняют ролик — REPLACE сегментов main.
    #   intro  → первые intro_dur сек main
    #   outro  → последние outro_dur сек main
    #   middle → сегмент [mid_point : mid_point+middle_dur] (время из Gemini)
    # Аудио: только copy из master, без pad/silence.
    # ============================================================

    intro_real_dur = 0.0
    if intro_path:
        try:
            intro_real_dur = float(probe_duration(intro_path))
        except Exception as e:
            _log(f"[IMO] intro probe fail: {e}, fallback={intro_duration}")
            intro_real_dur = float(intro_duration or 3.0)
        if intro_real_dur <= 0.05:
            _log(f"[IMO] intro duration invalid — disable")
            intro_path, intro_real_dur = "", 0.0
        else:
            _log(f"[IMO] intro REPLACE first {intro_real_dur:.2f}s of main")

    outro_real_dur = 0.0
    if outro_path:
        try:
            outro_real_dur = float(probe_duration(outro_path))
        except Exception as e:
            _log(f"[IMO] outro probe fail: {e}, fallback={outro_duration}")
            outro_real_dur = float(outro_duration or 3.0)
        if outro_real_dur <= 0.05:
            _log(f"[IMO] outro duration invalid — disable")
            outro_path, outro_real_dur = "", 0.0
        else:
            _log(f"[IMO] outro REPLACE last {outro_real_dur:.2f}s of main")

    middle_real_dur = 0.0
    if middle_path:
        try:
            middle_real_dur = float(probe_duration(middle_path))
        except Exception as e:
            _log(f"[IMO] middle probe fail: {e}, fallback={middle_duration}")
            middle_real_dur = float(middle_duration or 1.0)
        if middle_real_dur <= 0.05:
            _log(f"[IMO] middle duration invalid — disable")
            middle_path, middle_real_dur = "", 0.0
        else:
            # clamp mid_point so middle fits inside main between intro and outro zones
            lo = intro_real_dur
            hi = main_dur - outro_real_dur - middle_real_dur
            if hi < lo:
                _log(f"[IMO] middle не влезает между intro/outro — disable")
                middle_path, middle_real_dur = "", 0.0
            else:
                if mid_point < lo:
                    mid_point = lo
                if mid_point > hi:
                    mid_point = hi
                _log(
                    f"[IMO] middle REPLACE at {mid_point:.2f}s "
                    f"dur={middle_real_dur:.2f}s (Gemini/clamp)"
                )

    # Safety: intro+outro+middle must fit
    used = intro_real_dur + outro_real_dur + middle_real_dur
    if used >= main_dur - 0.3:
        _log(f"[IMO] WARN: overlays {used:.1f}s >= main {main_dur:.1f}s — clamp outro")
        outro_real_dur = max(0.5, main_dur - intro_real_dur - middle_real_dur - 0.5)
        if outro_real_dur <= 0.05:
            outro_path, outro_real_dur = "", 0.0

    if not (intro_path or middle_path or outro_path):
        _log("[IMO] нечего накладывать — пропуск")
        return video_path

    scale_filter = (
        f"scale={main_w}:{main_h}:force_original_aspect_ratio=decrease,"
        f"pad={main_w}:{main_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )

    # Build ordered segments of main that remain (gaps filled by overlays)
    # Timeline of main [0, main_dur):
    #   [0, intro_dur)           → intro
    #   [intro_dur, mid)         → main  (if middle)
    #   [mid, mid+mid_dur)       → middle
    #   [mid+mid_dur, main-outro)→ main
    #   [main-outro, main)       → outro
    # Without middle:
    #   [0, intro_dur) → intro
    #   [intro_dur, main-outro) → main
    #   [main-outro, main) → outro

    inputs = [video_path]
    input_idx = { "main": 0 }
    if intro_path:
        inputs.append(intro_path)
        input_idx["intro"] = len(inputs) - 1
    if middle_path:
        inputs.append(middle_path)
        input_idx["middle"] = len(inputs) - 1
    if outro_path:
        inputs.append(outro_path)
        input_idx["outro"] = len(inputs) - 1

    _log(
        f"[IMO] REPLACE mode inputs={len(inputs)} | "
        f"intro={intro_real_dur:.2f}s middle={middle_real_dur:.2f}s@{'%.2f'%mid_point if middle_path else '-'} "
        f"outro={outro_real_dur:.2f}s | main={main_dur:.2f}s"
    )

    filter_parts = []
    concat_labels = []
    vcount = 0

    def add_overlay(key: str, dur_label: str):
        nonlocal vcount
        i = input_idx[key]
        lab = f"v{vcount}"
        vcount += 1
        filter_parts.append(f"[{i}:v]{scale_filter}[{lab}];")
        concat_labels.append(f"[{lab}]")

    def add_main_trim(t0: float, t1: float):
        nonlocal vcount
        if t1 - t0 <= 0.04:
            return
        lab = f"v{vcount}"
        vcount += 1
        filter_parts.append(
            f"[0:v]trim={t0:.4f}:{t1:.4f},setpts=PTS-STARTPTS[{lab}];"
        )
        concat_labels.append(f"[{lab}]")

    # --- assemble timeline ---
    t = 0.0
    if intro_path:
        add_overlay("intro", "intro")
        t = intro_real_dur
    else:
        t = 0.0

    if middle_path:
        # main before middle
        add_main_trim(t, mid_point)
        add_overlay("middle", "middle")
        t = mid_point + middle_real_dur
        # main after middle until outro zone
        end_main = main_dur - outro_real_dur
        add_main_trim(t, end_main)
        t = end_main
    else:
        end_main = main_dur - outro_real_dur
        add_main_trim(t, end_main)
        t = end_main

    if outro_path:
        add_overlay("outro", "outro")

    if not concat_labels:
        _log("[IMO] пустой concat — пропуск")
        return video_path

    filter_parts.append(
        f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=1:a=0[outv]"
    )
    filter_complex = "".join(filter_parts)

    output_path = os.path.join(output_dir, f"with_intro_outro_{uuid.uuid4().hex[:8]}.mp4")

    cmd = [
        ffmpeg, "-y",
        *[arg for inp in inputs for arg in ("-i", inp)],
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "h264_videotoolbox", "-b:v", "28M", "-allow_sw", "0",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        output_path,
    ]
    run_vt_encode(cmd, inputs, output_path, log_fn=_log, stage_name="INTRO")

    # --- AUDIO: только из master, без pad/silence, длительности совпадают ---
    from .audio import replace_audio
    tmp_out = output_path + ".tmp.mp4"
    os.rename(output_path, tmp_out)
    replace_audio(tmp_out, video_path, output_path, log_fn=_log)
    try:
        os.remove(tmp_out)
    except OSError:
        pass

    _log(
        f"[IMO] done REPLACE → {os.path.basename(output_path)} | "
        f"intro={intro_real_dur:.2f}s middle={middle_real_dur:.2f}s "
        f"outro={outro_real_dur:.2f}s | audio=master copy"
    )
    return output_path



from .audio import probe_duration
