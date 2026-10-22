"""Kinetic typography for CE — ASS word animations (FFmpeg-safe, no GPU libs).

Styles inspired by modern short-form kinetic captions:
  - word pop (scale in + fade)
  - slide up
  - cascade (staggered words)

Output is standard ASS that burns with existing `ass=` / burn_subtitles path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


# ASS times: H:MM:SS.cc (centiseconds)
def _ass_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass(text: str) -> str:
    t = str(text or "").replace("\n", " ").strip()
    t = t.replace("{", "(").replace("}", ")")
    return t


@dataclass
class KineticWord:
    text: str
    start: float
    end: float


def words_from_segments(
    segments: Sequence[dict[str, Any]],
    *,
    max_words_per_line: int = 4,
) -> list[KineticWord]:
    """Flatten transcript segments into timed words (approx equal split if no word times)."""
    out: list[KineticWord] = []
    for seg in segments:
        text = _escape_ass(str(seg.get("text") or seg.get("word") or ""))
        if not text:
            continue
        t0 = float(seg.get("start") or seg.get("start_time") or 0.0)
        t1 = float(seg.get("end") or seg.get("end_time") or (t0 + 1.5))
        if t1 <= t0:
            t1 = t0 + 0.4
        tokens = [w for w in re.split(r"\s+", text) if w]
        if not tokens:
            continue
        # Prefer pre-timed words if present
        if "words" in seg and isinstance(seg["words"], list):
            for w in seg["words"]:
                wt = _escape_ass(str(w.get("word") or w.get("text") or ""))
                if not wt:
                    continue
                ws = float(w.get("start") or t0)
                we = float(w.get("end") or (ws + 0.35))
                out.append(KineticWord(wt, ws, max(ws + 0.12, we)))
            continue
        # Equal split across segment duration
        span = (t1 - t0) / max(1, len(tokens))
        for i, tok in enumerate(tokens):
            ws = t0 + i * span
            we = min(t1, ws + max(0.25, span * 1.15))
            out.append(KineticWord(tok, ws, we))
    return out


def _style_header(
    *,
    play_res_x: int = 3840,
    play_res_y: int = 2160,
    font: str = "Arial",
    font_size: int = 96,
    primary: str = "&H00FFFFFF",
    outline: str = "&H00000000",
    shadow: int = 2,
    margin_v: int = 180,
    alignment: int = 2,
) -> str:
    """ASS header. Alignment 2 = bottom-center (YouTube-safe)."""
    return f"""[Script Info]
Title: CE Kinetic Typography
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kinetic,{font},{font_size},{primary},&H000000FF&,{outline},&H80000000&,-1,0,0,0,100,100,0,0,1,4,{shadow},{alignment},80,80,{margin_v},1
Style: KineticTitle,{font},{int(font_size * 1.35)},{primary},&H000000FF&,{outline},&H80000000&,-1,0,0,0,100,100,0,0,1,5,{shadow},8,80,80,{margin_v + 40},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _anim_pop(text: str, *, fade_ms: int = 120) -> str:
    """Scale from 70→100 + fade in (kinetic pop)."""
    return (
        f"{{\\fad({fade_ms},80)\\t(0,{fade_ms},\\fscx70\\fscy70)\\t(0,{fade_ms},\\fscx100\\fscy100)}}"
        f"{text}"
    )


def _anim_slide_up(text: str, *, dy: int = 40, fade_ms: int = 150) -> str:
    """Slide up slightly while fading in (soft blur dissolve)."""
    # ASS \move needs absolute coords; approximate with \fad + mild \t ypos via clipping is hard.
    # Use fad + blur dissolve for slide-like feel without fixed resolution coords.
    return f"{{\\fad({fade_ms},100)\\blur2\\t(0,{fade_ms},\\blur0)}}{text}"


def _anim_cascade(text: str, index: int) -> str:
    delay = min(200, 40 * (index % 5))
    return (
        f"{{\\fad({120 + delay},90)\\t(0,{150 + delay},\\fscx80\\fscy80)"
        f"\\t(0,{150 + delay},\\fscx100\\fscy100)}}{text}"
    )


def build_kinetic_ass(
    words: Sequence[KineticWord],
    *,
    style: str = "subtle",  # off handled by caller; subtle | bold | title
    play_res_x: int = 3840,
    play_res_y: int = 2160,
    font: str = "Arial",
    font_size: int | None = None,
    max_words_on_screen: int = 4,
) -> str:
    """Build full ASS document with kinetic word events."""
    if style == "off" or not words:
        return ""

    # Scale font with resolution
    if font_size is None:
        font_size = 96 if play_res_x >= 3000 else (72 if play_res_x >= 1800 else 48)

    margin_v = int(play_res_y * 0.08) if play_res_y >= 1080 else 60
    header = _style_header(
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        font=font,
        font_size=font_size,
        margin_v=margin_v,
        alignment=2,
    )

    lines: list[str] = [header]
    # Group consecutive words into on-screen lines of max_words_on_screen
    i = 0
    group_idx = 0
    while i < len(words):
        chunk = list(words[i : i + max_words_on_screen])
        i += len(chunk)
        if not chunk:
            break
        t0 = chunk[0].start
        t1 = max(w.end for w in chunk)
        # Hold a bit after last word
        t1 = max(t1, t0 + 0.35)

        parts = []
        for j, w in enumerate(chunk):
            if style == "bold":
                parts.append(_anim_cascade(w.text, j))
            elif style == "title":
                parts.append(_anim_pop(w.text, fade_ms=180))
            else:  # subtle
                parts.append(_anim_slide_up(w.text) if j == 0 else _anim_pop(w.text, fade_ms=100))
            if j < len(chunk) - 1:
                parts.append(" ")

        body = "".join(parts)
        style_name = "KineticTitle" if style == "title" else "Kinetic"
        lines.append(
            f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},{style_name},,0,0,0,,{body}"
        )
        group_idx += 1

    return "\n".join(lines) + "\n"


def write_kinetic_ass(
    path: str | Path,
    segments: Sequence[dict[str, Any]],
    *,
    style: str = "subtle",
    play_res_x: int = 3840,
    play_res_y: int = 2160,
    **kwargs: Any,
) -> Path:
    """Write kinetic ASS from transcript segments. Returns path (may be empty file if off)."""
    path = Path(path)
    if style in ("off", "", "none"):
        path.write_text("", encoding="utf-8")
        return path
    words = words_from_segments(segments)
    ass = build_kinetic_ass(
        words,
        style=style,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        **kwargs,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ass, encoding="utf-8")
    return path


def kinetic_ass_filter(ass_path: str) -> str:
    """FFmpeg filter fragment to burn kinetic ASS (append after [v])."""
    # Escape path for filtergraph
    p = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"ass='{p}'"
