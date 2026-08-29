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
    """Последовательно набрать минимум клипов под целевую длительность.

    Логика (быстро и предсказуемо):
    1. Берём файлы в порядке сортировки папки (без shuffle).
    2. Смотрим первый: если его длительность >= audio → берём только его и обрезаем.
    3. Если короче — добавляем следующий, суммируем, и так далее.
    4. Как только сумма >= target — останавливаемся, склеиваем и обрезаем хвост.
    Никаких 50–100 лишних клипов.
    """
    _log = log_fn or log.info
    _log(f"[ВИДЕО 4K M1] Подбор B-roll под {target_duration:.1f} сек (последовательный)")

    if not video_files:
        raise FileNotFoundError("Нет видеофайлов для склейки")

    ffmpeg = _ffmpeg_bin()

    # Порядок как в папке (sorted уже в collect_video_files)
    ordered = list(video_files)

    selected: list[str] = []
    total_dur = 0.0
    for vf in ordered:
        dur = probe_duration(vf)
        if dur <= 0.05:
            continue
        selected.append(vf)
        total_dur += dur
        _log(f"[ВИДЕО] + {os.path.basename(vf)} ({dur:.1f}s) → сумма {total_dur:.1f}s")
        if total_dur >= target_duration:
            break

    # Если даже все файлы короче — повторяем цикл (редко, но нужно)
    if total_dur < target_duration and selected:
        _log(f"[ВИДЕО] Все клипы короче цели ({total_dur:.1f} < {target_duration:.1f}), повторяем")
        while total_dur < target_duration:
            for vf in ordered:
                dur = probe_duration(vf)
                if dur <= 0.05:
                    continue
                selected.append(vf)
                total_dur += dur
                if total_dur >= target_duration:
                    break

    if not selected:
        raise FileNotFoundError("Не удалось набрать ни одного валидного клипа")

    _log(f"[ВИДЕО] Выбрано клипов: {len(selected)} (сумма {total_dur:.1f}s ≥ {target_duration:.1f}s)")

    # Оптимизация: один клип и он длиннее цели → просто trim + scale, без concat
    if len(selected) == 1:
        vf = selected[0]
        tmp_dir = os.path.join(os.path.dirname(output_path), "_concat_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        norm_path = os.path.join(tmp_dir, "norm_000.mp4")
        cmd_norm = [
            ffmpeg, "-y",
            "-i", vf,
            "-t", str(target_duration),
            "-vf", "scale=3840:2160:force_original_aspect_ratio=decrease,pad=3840:2160:(ow-iw)/2:(oh-ih)/2,fps=30",
            "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
            "-pix_fmt", "yuv420p",
            "-an",
            norm_path,
        ]
        res = subprocess.run(cmd_norm, capture_output=True, text=True)
        if res.returncode != 0:
            _log(f"[ВИДЕО] Ошибка нормализации: {res.stderr[-400:]}")
            raise RuntimeError(f"Norm failed: {res.stderr[-400:]}")
        # Копируем как финальный видео-слой
        import shutil
        shutil.move(norm_path, output_path)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    else:
        normalized_files = []
        tmp_dir = os.path.join(os.path.dirname(output_path), "_concat_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        for i, vf in enumerate(selected):
            norm_path = os.path.join(tmp_dir, f"norm_{i:03d}.mp4")
            cmd_norm = [
                ffmpeg, "-y",
                "-i", vf,
                "-vf", "scale=3840:2160:force_original_aspect_ratio=decrease,pad=3840:2160:(ow-iw)/2:(oh-ih)/2,fps=30",
                "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
                "-pix_fmt", "yuv420p",
                "-an",
                norm_path,
            ]
            res = subprocess.run(cmd_norm, capture_output=True, text=True)
            if res.returncode != 0:
                _log(f"[ВИДЕО] Ошибка нормализации клипа {vf}: {res.stderr[-300:]}")
                raise RuntimeError(f"Norm failed: {res.stderr[-300:]}")
            normalized_files.append(norm_path)

        list_path = os.path.join(os.path.dirname(output_path), "concat_list.txt")
        with open(list_path, "w") as f:
            f.writelines(f"file '{nf}'\n" for nf in normalized_files)

        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-t", str(target_duration),
            "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-an",
            output_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            _log(f"[ВИДЕО] Ошибка concat: {res.stderr[-400:]}")
            raise RuntimeError(f"Concat failed: {res.stderr[-400:]}")

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



def _encode_vt_args(width: int = 3840, height: int = 2160) -> list[str]:
    """Apple VideoToolbox: быстро + высокий bitrate под 4K без soft x264."""
    pixels = max(1, int(width) * int(height))
    if pixels >= 3000 * 1600:
        br = "50M"
    elif pixels >= 1800 * 1000:
        br = "25M"
    else:
        br = "12M"
    return ["-c:v", "h264_videotoolbox", "-b:v", br, "-allow_sw", "1", "-pix_fmt", "yuv420p"]

def vstack_video_image(
    video_path: str,
    background_path: str,
    output_path: str,
    log_fn=None,
    top_ratio: float = 0.6,
) -> str:
    """Вертикаль 9:16 2160x3840:
    - видео +30%, нижняя грань ровно по середине кадра;
    - картинка только в НИЖНЕЙ половине, её верх — сразу под низом видео;
    - картинка увеличена на +20% относительно области низа, crop по центру.
    """
    _log = log_fn or log.info
    _log("[ВИДЕО 4K] vertical: video +30% (низ=середина), image bottom +20%")

    ffmpeg = _ffmpeg_bin()
    target_w, target_h = 2160, 3840
    mid_y = _even(target_h // 2)          # низ видео / верх картинки
    bottom_h = _even(target_h - mid_y)    # высота нижней зоны

    vid_w = _even(int(target_w * 1.30))
    # +20% к размеру нижней области, потом crop в bottom_h
    bg_w = _even(int(target_w * 1.20))
    bg_h = _even(int(bottom_h * 1.20))
    _log(
        f"[ВИДЕО 4K] canvas={target_w}x{target_h} mid_y={mid_y} "
        f"bottom={bottom_h} vid_w={vid_w} bg_scale={bg_w}x{bg_h}"
    )

    # [bg]: заполняет нижнюю половину (верх картинки = mid_y)
    # [vid]: overlay, низ видео = mid_y
    filter_complex = (
        f"[1:v]scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{bottom_h},setsar=1,"
        f"pad={target_w}:{target_h}:0:{mid_y}:black[bg];"
        f"[0:v]scale={vid_w}:-2:force_original_aspect_ratio=decrease,setsar=1[vid];"
        f"[bg][vid]overlay=x=(W-w)/2:y={mid_y}-h:shortest=1[out]"
    )

    ext = os.path.splitext(background_path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        from .audio import probe_duration
        dur = probe_duration(video_path)
        cmd = [
            ffmpeg, "-y", "-i", video_path,
            "-loop", "1", "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
            "-pix_fmt", "yuv420p", "-r", "30", "-t", str(dur),
            output_path,
        ]
    else:
        cmd = [
            ffmpeg, "-y", "-i", video_path, "-i", background_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
            "-pix_fmt", "yuv420p", "-r", "30", "-shortest",
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
        "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
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
    intro_duration: float = 3.0,
    middle_duration: float = 1.0,
    outro_duration: float = 3.0,
) -> str:
    """Добавить интро/аутро/мидл к видео (видеофайл или картинка→N сек)."""
    _log = log_fn or log.info
    _log("[ВИДЕО 4K M1] Добавление интро/аутро/мидл...")

    if not (enable_intro or enable_middle or enable_outro):
        _log("[IMO] все флаги выключены — пропуск")
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

    _log(
        f"[IMO] flags intro={enable_intro} middle={enable_middle} outro={enable_outro} | "
        f"folder={intro_outro_folder or '(пусто)'} | "
        f"explicit_outro={explicit_outro or '(нет)'}"
    )

    intro_path = explicit_intro if explicit_intro and os.path.isfile(explicit_intro) else ""
    middle_path = explicit_middle if explicit_middle and os.path.isfile(explicit_middle) else ""
    outro_path = explicit_outro if explicit_outro and os.path.isfile(explicit_outro) else ""
    if explicit_outro and not outro_path:
        _log(f"[IMO] explicit_outro указан, но файл не найден: {explicit_outro}")

    folder_files: list[str] = []
    if intro_outro_folder and os.path.isdir(intro_outro_folder):
        try:
            folder_files = os.listdir(intro_outro_folder)
            _log(f"[IMO] файлов в папке: {len(folder_files)}")
        except OSError as e:
            _log(f"[ВИДЕО] Не удалось прочитать папку intro/middle/outro: {e}")
    elif enable_outro and not outro_path:
        _log(f"[IMO] папка IMO отсутствует, outro не из чего взять: {intro_outro_folder}")

    _media = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".bmp", ".webp")

    if enable_intro and not intro_path and folder_files:
        intro_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("intro") and f.lower().endswith(_media)
        ]
        if intro_candidates:
            intro_path = intro_candidates[0]

    if enable_middle and not middle_path and folder_files:
        middle_candidates = [
            os.path.join(intro_outro_folder, f)
            for f in folder_files
            if f.lower().startswith("middle") and f.lower().endswith(_media)
        ]
        if middle_candidates:
            middle_path = middle_candidates[0]

    def _match_media(files: list[str], keywords: tuple[str, ...]) -> list[str]:
        """outro / outtro / ending / end / финал — гибкий поиск."""
        out = []
        for f in files:
            low = f.lower()
            if not low.endswith(_media):
                continue
            stem = os.path.splitext(low)[0]
            if any(k in stem for k in keywords):
                out.append(os.path.join(intro_outro_folder, f))
        return out

    if enable_outro and not outro_path and folder_files:
        # 1) по ключевым словам (если есть)
        outro_candidates = _match_media(
            folder_files, ("outro", "outtro", "ending", "endcard", "end_card", "финал", "концовка"),
        )
        outro_candidates = [c for c in outro_candidates if "intro" not in os.path.basename(c).lower()]
        # 2) имя может быть ЛЮБЫМ: любой media, не занятый intro/middle
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
                # не брать явный intro/middle по ключу, если они ещё не выбраны
                stem = os.path.splitext(low)[0]
                if any(k in stem for k in ("intro", "middle", "mid_")) and not intro_path:
                    continue
                full = os.path.abspath(os.path.join(intro_outro_folder, f))
                if full in used:
                    continue
                any_media.append(os.path.join(intro_outro_folder, f))
            outro_candidates = any_media
        if outro_candidates:
            # если несколько — берём последний по имени (часто "конец" кладут отдельно)
            outro_path = sorted(outro_candidates)[-1]
            _log(f"[IMO] outro (имя любое): {outro_path}")
        else:
            _log(
                f"[IMO] enable_outro=True, в папке нет media-файлов. "
                f"Файлы: {folder_files[:20]}. Или укажи путь на вкладке Intro/Outro — имя любое."
            )
    elif not enable_outro:
        _log("[IMO] outro выключен чекбоксом (Outro) для этого формата")

    def _ensure_video(path: str, label: str, duration: float = 1.0) -> str:
        """Картинка → видео N секунд; видеофайл — как есть."""
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
        out = os.path.join(output_dir, f"{label}_still_{uuid.uuid4().hex[:8]}.mp4")
        w, h = _even(main_w), _even(main_h)
        r = subprocess.run([
            ffmpeg, "-y", "-loop", "1", "-i", path, "-t", str(dur),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
            "-pix_fmt", "yuv420p", "-r", "30", out,
        ], capture_output=True, text=True)
        if r.returncode != 0:
            _log(f"[IMO] картинка→{dur}с fail {path}: {(r.stderr or '')[-300:]}")
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

    inputs = [video_path]

    if intro_path:
        inputs.append(intro_path)
    if middle_path:
        inputs.append(middle_path)
    if outro_path:
        inputs.append(outro_path)
    _log(f"[IMO] concat inputs={len(inputs)} (1=main + overlays)")

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
        "-c:v", "h264_videotoolbox", "-b:v", "50M", "-allow_sw", "1",
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
