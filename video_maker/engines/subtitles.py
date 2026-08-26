"""Движок субтитров — ASS через ffmpeg+libass."""
from __future__ import annotations

import logging
import os
import tempfile

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


def burn_subtitles(
    video_path: str,
    analysis: dict,
    enable_hooks: bool = True,
    enable_subtitles: bool = True,
    enable_strong_words: bool = True,
    clip: dict = None,
    output_path: str = "",
    log_fn=None,
) -> str:
    """Наложить субтитры и хуки на видео через ASS."""
    _log = log_fn or log.info
    _log("[СУБТИТРЫ] Наложение субтитров...")

    if not output_path:
        output_path = video_path + ".subtitled.mp4"

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
        hook = analysis.get("hook", {})
        hook_text = hook.get("text", "")
        hook_timing = hook.get("timing", 0)
        if hook_text:
            events.append({
                "type": "Dialog",
                "start": hook_timing,
                "end": hook_timing + 3.0,
                "text": f"{{\\an8}}{hook_text}",
                "style": "Hook",
            })

    if enable_strong_words:
        strong = analysis.get("strong_words", [])
        for sw in strong:
            word = sw.get("word", "")
            timing = sw.get("timing", 0)
            color = sw.get("color", "#FF6B00")
            if word:
                events.append({
                    "type": "Dialog",
                    "start": timing,
                    "end": timing + 1.5,
                    "text": f"{{\\an8\\c{_ass_color(color)}}}{word.upper()}",
                    "style": "Strong",
                })

    if not events:
        _log("[СУБТИТРЫ] Нет событий для наложения")
        return video_path

    # Генерируем .ass файл
    ass_path = _generate_ass(events, output_path)

    # Рендерим через ffmpeg+libass (imageio_ffmpeg)
    ffmpeg = _ffmpeg_bin()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]
    import subprocess
    subprocess.run(cmd, capture_output=True, check=True)

    return output_path


def _generate_ass(events: list[dict], output_path: str) -> str:
    """Сгенерировать .ass файл с субтитрами."""
    ass_path = output_path + ".ass"

    header = """[Script Info]
Title: VideoMaker Subtitles
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF&,&H000000FF&,&H00000000&,&H80000000&,0,0,0,0,100,100,0,0,1,2,1,2,10,10,50,1
Style: Hook,Arial,64,&H0000FFFF&,&H000000FF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,3,2,8,10,10,50,1
Style: Strong,Arial,56,&H000066FF&,&H000000FF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,2,1,8,10,10,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

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
