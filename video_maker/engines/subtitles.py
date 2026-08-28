"""Движок субтитров — ASS через ffmpeg+libass."""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)

# ASS time format: H:MM:SS.cc
def _ass_time(seconds: float) -> str:
    """Конвертировать секунды в ASS время."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(hex_color: str, fallback: str = "&H00FFFFFF&") -> str:
    """Конвертировать #RRGGBB в ASS BGR формат."""
    if not hex_color or not hex_color.startswith("#"):
        return fallback
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}&"
    except (ValueError, IndexError):
        return fallback


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


def _probe_video_resolution(video_path: str) -> tuple[int, int]:
    """Получить разрешение видео."""
    import json
    cmd = [
        "ffprobe", "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return stream["width"], stream["height"]


def _filter_and_shift_events(events: list[dict], clip: dict | None) -> list[dict]:
    """Фильтровать события по clip диапазону и сдвинуть тайминги."""
    if not clip:
        return events

    clip_start = clip.get("start", 0)
    clip_end = clip.get("end", 0)
    clip_dur = clip_end - clip_start

    filtered = []
    for ev in events:
        ev_start = ev["start"]
        ev_end = ev["end"]

        # Проверяем пересечение с clip
        if ev_end <= clip_start or ev_start >= clip_end:
            continue  # Событие полностью вне клипа

        # Сдвигаем тайминги относительно начала клипа
        new_start = max(0, ev_start - clip_start)
        new_end = min(clip_dur, ev_end - clip_start)

        if new_end > new_start:
            new_ev = ev.copy()
            new_ev["start"] = new_start
            new_ev["end"] = new_end
            filtered.append(new_ev)

    return filtered


def _generate_ass_header(playres_x: int, playres_y: int) -> str:
    """Сгенерировать заголовок ASS с динамическим разрешением."""
    # Масштабируем размеры шрифтов относительно базового 1920x1080
    scale_x = playres_x / 1920
    scale_y = playres_y / 1080
    scale = min(scale_x, scale_y)

    default_size = int(48 * scale)
    hook_size = int(64 * scale)
    strong_size = int(56 * scale)
    margin_v = int(50 * scale_y)
    margin_lr = int(10 * scale_x)

    return f"""[Script Info]
Title: VideoMaker Subtitles
ScriptType: v4.00+
PlayResX: {playres_x}
PlayResY: {playres_y}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{default_size},&H00FFFFFF&,&H000000FF&,&H00000000&,&H80000000&,0,0,0,0,100,100,0,0,1,2,1,2,{margin_lr},{margin_lr},{margin_v},1
Style: Hook,Arial,{hook_size},&H0000FFFF&,&H000000FF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,3,2,8,{margin_lr},{margin_lr},{margin_v},1
Style: Strong,Arial,{strong_size},&H000066FF&,&H000000FF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,2,1,8,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _escape_ass_path(path: str) -> str:
    """Экранировать путь для ASS фильтра ffmpeg."""
    # ASS фильтр требует экранирования двоеточия, обратного слэша и запятых
    # На Windows пути могут содержать двоеточие (C:\...)
    # Заменяем \ на \\ и : на \:
    return path.replace("\\", "\\\\").replace(":", "\\:")


def burn_subtitles(
    video_path: str,
    analysis: dict,
    enable_hooks: bool = True,
    enable_subtitles: bool = True,
    enable_strong_words: bool = True,
    clip: dict = None,
    output_path: str = "",
    log_fn=None,
    transcription: dict = None,
    use_aisie: bool = True,
) -> str:
    """Наложить субтитры и хуки на видео через ASS.
    Поддерживает динамическое разрешение, хуки с явными таймингами, strong_words без forced upper.
    AISIE pipeline включён по умолчанию (требует transcription)."""
    _log = log_fn or log.info
    _log("[СУБТИТРЫ] Наложение субтитров...")

    if not output_path:
        output_path = video_path + ".subtitled.mp4"

    # AISIE enhancement (всегда, если есть transcription)
    if use_aisie and transcription:
        try:
            from .aisie_integration import enhance_analysis_with_aisie
            analysis = enhance_analysis_with_aisie(
                analysis=analysis,
                transcription=transcription,
                log_fn=log_fn,
            )
            _log("[СУБТИТРЫ] AISIE enhancement applied")
        except Exception as e:
            _log(f"[СУБТИТРЫ] AISIE enhancement failed: {e}")

    # Получаем разрешение видео для динамического ASS
    playres_x, playres_y = _probe_video_resolution(video_path)
    _log(f"[СУБТИТРЫ] Разрешение видео: {playres_x}x{playres_y}")

    # Собираем события для ASS
    events = []

    if enable_subtitles:
        subtitles = analysis.get("subtitles", [])
        for sub in subtitles:
            start = sub.get("start", 0)
            end = sub.get("end", 0)
            text = sub.get("text", "")
            if text:
                events.append({
                    "type": "Dialog",
                    "start": start,
                    "end": end,
                    "text": text,
                    "style": "Default",
                })

    if enable_hooks:
        # Используем AISIE hooks если доступны, иначе fallback на Gemini
        hooks = analysis.get("aisie", {}).get("hooks", [])
        if not hooks:
            hook = analysis.get("hook", {})
            if hook:
                hooks = [hook]

        for hook in hooks:
            hook_text = hook.get("text", "")
            hook_start = hook.get("start", hook.get("timing", 0))
            hook_end = hook.get("end", hook_start + 3.0)
            if hook_text:
                events.append({
                    "type": "Dialog",
                    "start": hook_start,
                    "end": hook_end,
                    "text": f"{{\\an8}}{hook_text}",
                    "style": "Hook",
                })

    if enable_strong_words:
        strong = analysis.get("strong_words", [])
        for sw in strong:
            word = sw.get("word", "")
            timing = sw.get("timing", 0)
            sw_start = sw.get("start", timing)
            sw_end = sw.get("end", timing + 1.5)
            color = sw.get("color", "#FF6B00")
            caps = sw.get("caps", False)
            display_word = word.upper() if caps else word
            if word:
                events.append({
                    "type": "Dialog",
                    "start": sw_start,
                    "end": sw_end,
                    "text": f"{{\\an8\\c{_ass_color(color)}}}{display_word}",
                    "style": "Strong",
                })

    # Фильтруем и сдвигаем события если передан clip
    if clip:
        events = _filter_and_shift_events(events, clip)

    if not events:
        _log("[СУБТИТРЫ] Нет событий для наложения")
        return video_path

    # Получаем разрешение видео для динамического ASS
    playres_x, playres_y = _probe_video_resolution(video_path)
    _log(f"[СУБТИТРЫ] Разрешение видео: {playres_x}x{playres_y}")

    # Генерируем .ass файл с динамическим заголовком
    ass_path = _generate_ass(events, output_path, playres_x, playres_y)

    # Рендерим через ffmpeg+libass (imageio_ffmpeg) с экранированным путем
    ffmpeg = _ffmpeg_bin()
    escaped_ass_path = _escape_ass_path(ass_path)
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", f"ass='{escaped_ass_path}'",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)

    return output_path


def _generate_ass(events: list[dict], output_path: str, playres_x: int, playres_y: int) -> str:
    """Сгенерировать .ass файл с субтитрами и динамическим разрешением."""
    ass_path = output_path + ".ass"

    header = _generate_ass_header(playres_x, playres_y)

    lines = [header]
    for ev in events:
        start = _ass_time(ev["start"])
        end = _ass_time(ev["end"])
        style = ev.get("style", "Default")
        text = ev["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},{style},,0,0,0,,{text}")

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))

    return ass_path