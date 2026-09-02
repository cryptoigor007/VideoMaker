# VideoMaker FIX | 2026.09.03-r24 | 2026-09-03
# CHANGED: Hook 4.5s / CTA 7.0s; shorts skip Hook/CTA if overlap with long zones
# PREV: 2026.09.01-r5
# REPLACE: video_maker/engines/subtitles.py

"""Субтитры: karaoke (vertical/shorts) + classic YouTube (wide) + AISIE hooks."""
from __future__ import annotations

import logging
import re
import os
import shutil
import subprocess
import tempfile
import uuid

# r24: display durations (long + shorts)
HOOK_DUR = 4.5   # was ~2.5–3.2
CTA_DUR = 7.0    # was 5.0

from .ffmpeg_resilient import (
    SubtitleStageFailed,
    calculate_adaptive_bitrate,
    classify_ffmpeg_error,
    ensure_storage,
    path_is_readable,
    run_ffmpeg,
    atomic_replace,
    verify_mp4,
    inject_hwaccel,
)

log = logging.getLogger(__name__)

def _strip_ass_overrides(text: str) -> str:
    r"""Remove leaked ASS override tags from Gemini/hook/CTA plain text."""
    if not text:
        return ""
    t = str(text)
    # { ... } override blocks
    t = re.sub(r"\{[^}]*\}", "", t)
    # bare tags like \fad(100,150) \shad0 \be0
    t = re.sub(
        r"\\(?:fad|shad|be|bord|fs|fn|an|pos|c|3c|4c|alpha|a|b|i|u|s|r|q|move|org|clip|iclip|t)\b[^\\{]*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = t.replace("\\", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


try:
    from ..external.aisie.styles import VISUAL_WEIGHTS, HOOK_TYPES
except Exception:
    VISUAL_WEIGHTS = {
        "L0": {"font_scale": 0.85, "text_color": "#FFFFFF"},
        "L1": {"font_scale": 0.92, "text_color": "#FFFFFF"},
        "L2": {"font_scale": 1.00, "text_color": "#FFFF00"},
        "L3": {"font_scale": 1.08, "text_color": "#FF3B30"},
        "L4": {"font_scale": 1.15, "text_color": "#FF3B30"},
    }
    HOOK_TYPES = {}

BRAND_COLOR = "#FF7A12"

# Топ-пресеты 2025–2026 (выбор в GUI)
CAPTION_STYLES = {
    "auto_aisie": "Auto (AISIE)",
    "hormozi": "Hormozi Yellow",
    "hormozi_green": "Hormozi Green",
    "tiktok_box": "TikTok Box",
    "clean_pro": "Clean Pro (YouTube)",
    "bold_pop": "Bold Pop",
    "cliffhanger": "Cliffhanger (Tension)",
}
HOOK_STYLES = {
    # Один режим: неоновый маркер (ротация 4 цветов)
    "marker": "Neon Marker",
    "auto_aisie": "Neon Marker",
}



def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_color(hex_color: str, fallback: str = "&H00FFFFFF&") -> str:
    if not hex_color or not str(hex_color).startswith("#") or len(str(hex_color)) < 7:
        return fallback
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"&H00{b:02X}{g:02X}{r:02X}&"
    except (ValueError, IndexError):
        return fallback


def _ffmpeg_bin() -> str:
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"):
        path = candidate if candidate.startswith("/") else shutil.which(candidate)
        if not path or not os.path.exists(path):
            continue
        try:
            r = subprocess.run([path, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=8)
            out = (r.stdout or "") + (r.stderr or "")
            if "subtitles" in out.lower() or " ass " in out.lower():
                return path
        except Exception:
            return path
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return str(p)
    except Exception:
        pass
    return "ffmpeg"


def _probe_video_resolution(video_path: str) -> tuple[int, int]:
    import json
    cmd = ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "json", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _filter_and_shift_events(events: list[dict], clip: dict | None) -> list[dict]:
    if not clip:
        return events
    clip_start = float(clip.get("start", 0))
    clip_end = float(clip.get("end", 0))
    clip_dur = clip_end - clip_start
    out = []
    for ev in events:
        if ev["end"] <= clip_start or ev["start"] >= clip_end:
            continue
        ns = max(0.0, ev["start"] - clip_start)
        ne = min(clip_dur, ev["end"] - clip_start)
        if ne > ns:
            e = ev.copy()
            e["start"], e["end"] = ns, ne
            out.append(e)
    return out


def _escape_ass_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _is_wide(playres_x: int, playres_y: int) -> bool:
    return playres_x >= playres_y


def _weight_style(weight: str, base_size: int) -> tuple[str, int]:
    w = VISUAL_WEIGHTS.get(weight) or VISUAL_WEIGHTS.get("L1") or {}
    color = _ass_color(w.get("text_color", "#FFFFFF"))
    scale = float(w.get("font_scale", 1.0))
    size = max(36, int(base_size * scale))
    return color, size


def _generate_ass_header(playres_x: int, playres_y: int, wide: bool) -> str:
    """Premium CapCut-class styles via libass (WhisperX word timings → ASS)."""
    if wide:
        scale = max(playres_y / 1080.0, 1.0)
        sz = max(52, int(58 * scale))
        hook_sz = max(68, int(78 * scale))
        margin_v = max(56, int(playres_y * 0.07))
        align_k, align_h = 2, 8
    else:
        scale = max(playres_x / 1080.0, 1.0)
        sz = max(70, int(78 * scale))
        hook_sz = max(88, int(102 * scale))
        margin_v = int(playres_y * 0.028)
        align_k, align_h = 5, 8
    ml = int(playres_x * 0.035)
    # Outline 8–10px equivalent at 1080 → scale
    bord = max(6, int(8 * scale))
    bord_h = max(7, int(10 * scale))
    return f"""[Script Info]
Title: VideoMaker Premium Captions
ScriptType: v4.00+
PlayResX: {playres_x}
PlayResY: {playres_y}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hormozi,Arial Black,{sz},&H0000EBFF&,&H0000EBFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: HormoziGreen,Arial Black,{sz},&H004CFF00&,&H004CFF00&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: TikTokBox,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&HE0000000&,-1,0,0,0,100,100,0,0,3,0,0,{align_k},{ml},{ml},{margin_v},1
Style: CleanPro,Arial,{sz},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,0,0,1,0,4,{align_k},{ml},{ml},{margin_v},1
Style: BoldPop,Arial Black,{sz},&H000000FF&,&H0000A5FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,2,0,1,{bord+1},0,{align_k},{ml},{ml},{margin_v},1
Style: Cliffhanger,Arial Black,{sz},&H000000FF&,&H000000FF&,&H00000000&,&HA0000000&,-1,0,0,0,100,100,1,0,1,{bord+2},1,{align_k},{ml},{ml},{margin_v},1
Style: Karaoke,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: Glow,Arial Black,{sz},&H00FFFFFF&,&H00000000&,&H00FFFFFF&,&H00000000&,-1,0,0,0,100,100,0,0,1,0,0,{align_k},{ml},{ml},{margin_v},1
Style: HookMarker,Arial Black,{hook_sz},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,-1,0,0,0,100,100,1,0,1,{bord_h},0,{align_h},{ml},{ml},{margin_v},1
Style: Hook,Arial Black,{hook_sz},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,-1,0,0,0,100,100,1,0,1,{bord_h},0,{align_h},{ml},{ml},{margin_v},1
Style: Strong,Arial Black,{max(sz, int(sz*1.12))},&H0000EBFF&,&H000000FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_h},{ml},{ml},{margin_v},1
Style: Default,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _words_from_transcription(transcription: dict | None) -> list[dict]:
    if not transcription:
        return []
    words = []
    for seg in transcription.get("segments") or []:
        seg_words = seg.get("words") or []
        if seg_words:
            for w in seg_words:
                text = (w.get("word") or w.get("text") or "").strip()
                if not text:
                    continue
                start = float(w.get("start", seg.get("start", 0)))
                end = float(w.get("end", start + 0.25))
                if end <= start:
                    end = start + 0.2
                words.append({"text": text, "start": start, "end": end})
        else:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            parts = text.split()
            s0 = float(seg.get("start", 0))
            s1 = float(seg.get("end", s0 + 1))
            dur = max(0.05, (s1 - s0) / max(len(parts), 1))
            for i, p in enumerate(parts):
                words.append({"text": p, "start": s0 + i * dur, "end": s0 + (i + 1) * dur})
    return words


def _strong_map(analysis: dict) -> dict:
    m = {}
    for sw in analysis.get("strong_words") or []:
        w = (sw.get("word") or "").strip().lower()
        if w:
            m[w] = sw.get("weight") or sw.get("visual_weight") or "L2"
    for h in analysis.get("aisie", {}).get("hooks") or analysis.get("hooks") or []:
        for g in h.get("semantic_groups") or []:
            raw = g.get("words") or g.get("text") or ""
            if isinstance(raw, list):
                parts = raw
            else:
                parts = str(raw).split()
            for part in parts:
                key = str(part).strip().lower()
                if key:
                    m[key] = g.get("visual_weight") or h.get("visual_weight") or "L3"
    return m


def _group_words_static(words: list[dict]) -> list[list[int]]:
    """Статичные группы по 2–3 слова (до 4 если короткие). Не «бегущая строка»."""
    n = len(words)
    if n == 0:
        return []
    particles = {"и", "а", "но", "в", "на", "по", "к", "с", "у", "о", "из", "от", "до", "не", "ни", "же", "ли", "бы", "то", "это", "как"}
    groups: list[list[int]] = []
    i = 0
    while i < n:
        # целевой размер группы
        remain = n - i
        if remain <= 4:
            size = remain
        else:
            # смотрим длину следующих слов
            chunk = words[i:i + 4]
            avg = sum(len(w["text"]) for w in chunk) / len(chunk)
            size = 4 if avg <= 5 else 3
        # не начинать группу с частицы, если можно взять предыдущее (уже в группе — сдвиг)
        end = min(i + size, n)
        # если последнее слово группы — частица и есть ещё слова, захватить следующее
        if end < n and words[end - 1]["text"].lower().strip(".,!?;:") in particles:
            end = min(end + 1, n)
        # если первое — частица и группа > 2, ок; если одно — приклеить к следующей
        idxs = list(range(i, end))
        if len(idxs) == 1 and end < n:
            idxs = list(range(i, min(i + 2, n)))
            end = idxs[-1] + 1
        groups.append(idxs)
        i = end
    return groups



def _group_words_one_line(words: list[dict], max_chars: int = 28, max_words: int = 3) -> list[list[int]]:
    """Clean Pro: одна строка, по умолчанию 2–3 слова.

    4+ только если очень короткие ИЛИ нельзя оторвать (частица/предлог).
    """
    n = len(words)
    if n == 0:
        return []
    # слова, которые нельзя оставлять одними / отрывать от следующего
    sticky = {
        "и", "а", "но", "да", "или", "либо", "ни",
        "в", "на", "по", "к", "с", "у", "о", "об", "обо", "из", "от", "до",
        "для", "при", "без", "над", "под", "про", "через",
        "не", "ни", "же", "ли", "бы", "то", "это", "как", "что", "чтобы",
        "я", "ты", "он", "она", "мы", "вы", "они", "мой", "твой", "наш",
    }
    groups: list[list[int]] = []
    i = 0
    while i < n:
        idxs = [i]
        chars = len(words[i]["text"])
        j = i + 1
        while j < n:
            wj = words[j]["text"]
            add = len(wj) + 1
            last = words[idxs[-1]]["text"].lower().strip(".,!?;:«»\"")
            # всегда цепляем sticky к следующему
            must = last in sticky
            # можно взять 3-е (или 4-е) только если оба коротких
            short_ok = chars + add <= max_chars and len(wj) <= 4 and len(idxs) < 4
            if len(idxs) >= max_words and not must and not (len(idxs) == max_words and short_ok and len(idxs) < 4):
                if not must:
                    break
            if chars + add > max_chars and len(idxs) >= 2 and not must:
                break
            idxs.append(j)
            chars += add
            j += 1
            if len(idxs) >= max_words and not must:
                # ещё одно только если sticky
                break
        groups.append(idxs)
        i = idxs[-1] + 1
    return groups


def _build_clean_pro_window(
    words, analysis, playres_x, playres_y, base_size, wide: bool = False,
):
    """Clean Pro: одна строка, без контура, чёрная рассеянная тень, без scale.

    Активное слово только меняет цвет (белый).
    Увеличение/другой цвет — только если AISIE пометил strong word.
    Отступы 6% от краёв.
    """
    if not words:
        return []

    margin_pct = 0.06  # 6% ≈ середина 5–7%
    ml = int(playres_x * margin_pct)
    usable = playres_x - 2 * ml
    # Оценка max символов на строку (Arial ~0.55*fs width)
    fs = max(48, int(base_size * 1.02))
    max_chars = max(22, int(usable / (fs * 0.55)))
    max_words = 3

    cx = playres_x // 2
    if wide:
        cy = int(playres_y * 0.88)
        pos = f"{{\\an2\\pos({cx},{cy})}}"
    else:
        cy = int(playres_y * 0.56)  # ниже середины (~6%)
        pos = f"{{\\an2\\pos({cx},{cy})}}"

    WHITE = "&H00FFFFFF&"
    DIM = "&H00B4B4B4&"
    RED = "&H000000FF&"
    ORANGE = "&H0000A5FF&"
    YELLOW = "&H0000EBFF&"
    GREEN = "&H004CFF00&"

    strong = _strong_map(analysis)

    def active_color_and_size(word_text: str) -> tuple[str, int]:
        key = word_text.lower().strip(".,!?;:«»\"'")
        w = (strong.get(key) or "").upper()
        # По умолчанию: тот же размер, белый цвет
        size = fs
        color = WHITE
        if w == "L4":
            color, size = RED, int(fs * 1.12)
        elif w == "L3":
            color, size = ORANGE, int(fs * 1.08)
        elif w == "L2":
            color, size = YELLOW, fs
        elif w == "L1":
            color, size = GREEN, fs
        return color, size

    # bord0 + soft black shadow via \\shad + \\4c
    # \\bord0 \\shad3 \\4c&H000000& \\4a&H60&
    shadow = r"\bord0\shad3\4c&H000000&\4a&H60&"

    def tag_active(text: str) -> str:
        color, size = active_color_and_size(text)
        return f"{{\\c{color}\\fs{size}\\b0{shadow}}}{text}{{\\r}}"

    def tag_dim(text: str) -> str:
        return f"{{\\c{DIM}\\fs{fs}\\b0{shadow}}}{text}{{\\r}}"

    events: list[dict] = []
    for group in _group_words_one_line(words, max_chars=max_chars, max_words=max_words):
        edges = [float(words[i]["start"]) for i in group]
        edges.append(float(words[group[-1]]["end"]))
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + 0.05
        if edges[-1] - edges[-2] < 0.12:
            edges[-1] = edges[-2] + 0.18

        for gi, active in enumerate(group):
            t0, t1 = edges[gi], edges[gi + 1]
            parts = []
            for j in group:
                wt = words[j]["text"]
                parts.append(tag_active(wt) if j == active else tag_dim(wt))
            events.append({
                "start": t0,
                "end": t1,
                "style": "CleanPro",
                "text": pos + " ".join(parts),
                "layer": 1,
            })
    return events


def _build_karaoke_window(
    words, analysis, playres_x, playres_y, base_size,
    wide: bool = False, caption_style: str = "auto_aisie",
    honor_strong: bool = True,
):
    """Karaoke-группы с РЕАЛЬНО разными пресетами.

    Одна строка субтитров (ниже середины / низ для wide).
    Strong-слова (AISIE/Gemini) внутри строки: другой цвет + увеличение.
    Без второго слоя Glow — он давал визуальное «раздваивание» (увеличенный текст сверху).
    Karaoke-active (текущее слово) подсвечивается цветом, без агрессивного scale,
    чтобы не выглядеть как отдельный overlay.
    """
    if not words:
        return []

    key = (caption_style or "auto_aisie").strip().lower()
    # Clean Pro — отдельный путь (остальные стили не трогаем)
    if key == "clean_pro":
        return _build_clean_pro_window(
            words, analysis, playres_x, playres_y, base_size, wide=wide,
        )

    cx = playres_x // 2
    if wide:
        cy = int(playres_y * 0.90)
        pos = f"{{\\an2\\pos({cx},{cy})}}"
        default_key = "clean_pro"
    else:
        cy = int(playres_y * 0.54)
        pos = f"{{\\an5\\pos({cx},{cy})}}"
        default_key = "hormozi"

    key = (caption_style or "auto_aisie").strip().lower()
    if key in ("", "auto_aisie", "auto"):
        key = default_key

    # ASS BGR. Пресеты: active/dim цвет, box, лёгкий pop только для strong.
    # use_glow ВЫКЛЮЧЕН — отдельный glow-слой создавал «вторую» увеличенную копию слова.
    PRESETS = {
        "hormozi": {
            "style": "Hormozi",
            "active": "&H0000EBFF&",
            "dim": "&H00C8C8C8&",
            "use_pop": True,
            "pop_pct": 112,
            "box": False,
            "active_scale": 1.06,
            "strong_scale": 1.18,
        },
        "hormozi_green": {
            "style": "HormoziGreen",
            "active": "&H004CFF00&",
            "dim": "&H00B0B0B0&",
            "use_pop": True,
            "pop_pct": 112,
            "box": False,
            "active_scale": 1.06,
            "strong_scale": 1.18,
        },
        "tiktok_box": {
            "style": "TikTokBox",
            "active": "&H0000EBFF&",
            "dim": "&H00FFFFFF&",
            "use_pop": False,
            "pop_pct": 100,
            "box": True,
            "active_scale": 1.04,
            "strong_scale": 1.12,
        },
        "clean_pro": {
            "style": "CleanPro",
            "active": "&H00FFFFFF&",
            "dim": "&H00A0A0A0&",
            "use_pop": False,
            "pop_pct": 100,
            "box": False,
            "active_scale": 1.02,
            "strong_scale": 1.10,
        },
        "bold_pop": {
            "style": "BoldPop",
            "active": "&H000000FF&",
            "dim": "&H00B8B8B8&",
            "use_pop": True,
            "pop_pct": 118,
            "box": False,
            "active_scale": 1.08,
            "strong_scale": 1.28,
        },
        "cliffhanger": {
            "style": "Cliffhanger",
            "active": "&H000000FF&",
            "dim": "&H00666699&",
            "use_pop": True,
            "pop_pct": 120,
            "box": False,
            "active_scale": 1.08,
            "strong_scale": 1.32,
        },
    }
    preset = PRESETS.get(key, PRESETS["hormozi"])
    style_name = preset["style"]
    col_active = preset["active"]
    col_dim = preset["dim"]
    use_pop = bool(preset["use_pop"])
    pop_pct = int(preset["pop_pct"])
    active_scale = float(preset["active_scale"])
    strong_scale = float(preset.get("strong_scale") or (active_scale + 0.12))
    is_box = bool(preset["box"])

    strong = _strong_map(analysis) if honor_strong else {}

    RED = "&H000000FF&"
    ORANGE = "&H0000A5FF&"
    YELLOW = "&H0000EBFF&"
    GREEN = "&H004CFF00&"

    def strong_weight(word_text: str) -> str:
        if not honor_strong or not strong:
            return ""
        return (strong.get(word_text.lower().strip(".,!?;:«»\"'"), "") or "").upper()

    def color_for_word(word_text: str, is_active: bool) -> str:
        w = strong_weight(word_text)
        if w == "L4":
            return RED
        if w == "L3":
            return ORANGE
        if w == "L1":
            return GREEN
        if w == "L2":
            return YELLOW
        return col_active if is_active else col_dim

    normal_size = max(40, int(base_size * 0.90))
    karaoke_active_size = max(int(base_size * active_scale), normal_size + 2)
    karaoke_active_size = min(karaoke_active_size, int(base_size * 1.15))
    strong_size = max(int(base_size * strong_scale), karaoke_active_size + 4)
    strong_size = min(strong_size, int(base_size * 1.40))
    bord_a = max(5, int(base_size * (0.14 if key == "cliffhanger" else 0.11)))
    bord_d = max(3, int(base_size * 0.07))

    def tags_word(text: str, is_active: bool) -> str:
        w = strong_weight(text)
        is_strong = w in ("L2", "L3", "L4", "L1")
        color = color_for_word(text, is_active)
        if is_strong:
            size = strong_size
            do_pop = use_pop and pop_pct > 100
        elif is_active:
            size = karaoke_active_size
            do_pop = False  # лёгкая подсветка без scale — не путать со strong
        else:
            size = normal_size
            do_pop = False

        if is_box:
            bold = "1" if (is_active or is_strong) else "0"
            return f"{{\\c{color}\\fs{size}\\b{bold}}}{text}{{\\r}}"
        if do_pop:
            return (
                f"{{\\c{color}\\fs{size}\\b1\\bord{bord_a}\\shad0\\be1"
                f"\\t(0,100,\\fscx{pop_pct}\\fscy{pop_pct})"
                f"\\t(100,220,\\fscx100\\fscy100)}}"
                f"{text}{{\\r}}"
            )
        bold = "1" if (is_active or is_strong) else "0"
        bord = bord_a if (is_active or is_strong) else bord_d
        return f"{{\\c{color}\\fs{size}\\b{bold}\\bord{bord}}}{text}{{\\r}}"

    events: list[dict] = []
    groups = _group_words_static(words)
    strong_hit_count = 0

    for group in groups:
        if not group:
            continue
        # Точные стыки: интервалы [t0,t1), [t1,t2), ... без дыр
        edges = []
        for idx in group:
            edges.append(float(words[idx]["start"]))
        edges.append(float(words[group[-1]]["end"]))
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + 0.05
        if edges[-1] - edges[-2] < 0.12:
            edges[-1] = edges[-2] + 0.18

        for gi, active in enumerate(group):
            t0, t1 = edges[gi], edges[gi + 1]
            parts = []
            for j in group:
                wt = words[j]["text"]
                if strong_weight(wt):
                    strong_hit_count += 1
                parts.append(tags_word(wt, j == active))
            line = pos + " ".join(parts)
            # Один слой только — без Glow overlay (устраняет «раздваивание»)
            events.append({
                "start": t0, "end": t1, "style": style_name,
                "text": line, "layer": 1,
            })

    return events



def _build_wide_subtitles(transcription, analysis):
    events = []
    subs = list(analysis.get("subtitles") or [])
    if not subs and transcription:
        for seg in transcription.get("segments") or []:
            t = (seg.get("text") or "").strip()
            if t:
                subs.append({"start": seg.get("start", 0), "end": seg.get("end", 0), "text": t})
    for sub in subs:
        text = (sub.get("text") or "").strip()
        if not text:
            continue
        events.append({
            "start": float(sub.get("start", 0)),
            "end": float(sub.get("end", 0) or float(sub.get("start", 0)) + 1.5),
            "style": "Default", "text": text, "layer": 0,
        })
    return events


def _probe_video_duration(video_path: str) -> float:
    """Длительность видео в секундах (ffprobe)."""
    try:
        import json
        cmd = [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "json", video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _build_hook_events(
    analysis, playres_x, playres_y, wide, base_size,
    hook_style: str = "auto_aisie",
    clip: dict | None = None,
    cta_start: float | None = None,
    video_duration: float = 0.0,
    log_fn=None,
):
    """Хуки-маркеры сверху.

    - Первый хук: с 0.0 (сразу на первом кадре / обложка).
    - Остальные: строго по таймингу Gemini (start/end).
    - Последний хук отбрасывается, если пересекается с CTA или до CTA/конца < 5 с
      (предпочтительно 10 с). CTA в конце достаточно.
    - Без glow. Тонкий чёрный outline.
    """
    _log = log_fn or log.info
    if wide:
        hooks_list = (
            analysis.get("hooks_wide")
            or analysis.get("hooks")
            or analysis.get("aisie", {}).get("hooks")
            or []
        )
    else:
        hooks_list = (
            analysis.get("hooks_vertical")
            or analysis.get("hooks")
            or analysis.get("aisie", {}).get("hooks")
            or []
        )
    if not hooks_list:
        h = analysis.get("hook")
        if isinstance(h, dict) and h.get("text"):
            hooks_list = [h]
        else:
            hooks_list = []

    # Shorts: один packaging-хук клипа (абсолютные времена на полном ролике;
    # burn_subtitles потом сдвинет через _filter_and_shift_events)
    if clip is not None:
        c0 = float(clip.get("start", 0) or 0)
        c1 = float(clip.get("end", c0 + 15) or (c0 + 15))
        hook_text = (clip.get("hook") or "").strip()
        if not hook_text:
            # fallback: title / package-level hook, чтобы в начале шорта всегда был хук
            hook_text = (clip.get("title") or "").strip()
            if not hook_text:
                ph = analysis.get("package_hook") or analysis.get("hook")
                if isinstance(ph, dict):
                    hook_text = (ph.get("text") or "").strip()
                elif isinstance(ph, str):
                    hook_text = ph.strip()
        if clip.get("_skip_hook"):
            hooks_list = []
        elif hook_text:
            hs = float(clip.get("hook_start", c0) or 0)
            he = float(clip.get("hook_end", hs + HOOK_DUR) or (hs + HOOK_DUR))
            # относительные 0..dur → абсолютные
            if hs < c0 - 0.05:
                hs = c0 + max(0.0, hs)
            if he < c0 - 0.05:
                he = c0 + max(0.0, he)
            if hs < c0:
                hs = c0
            if he <= hs:
                he = hs + HOOK_DUR
            # хук только в начале клипа (не в конце — там CTA)
            if hs > c0 + HOOK_DUR:
                hs = c0
                he = min(c0 + HOOK_DUR, max(c0 + 2.0, c1 - CTA_DUR))
            # гарантируем начало на первом кадре клипа, длительность ~HOOK_DUR
            hs = c0
            he = min(max(he, c0 + HOOK_DUR), c0 + HOOK_DUR + 0.5, max(c0 + 2.5, c1 - CTA_DUR))
            if he <= hs:
                he = hs + HOOK_DUR
            hooks_list = [{
                "text": hook_text,
                "start": hs,
                "end": he,
                "timing": hs,
                "type": "CURIOSITY",
                "visual_weight": "L4",
            }]
    if not hooks_list:
        return []

    def _hstart(h):
        return float(h.get("start", h.get("timing", 0)) or 0)
    hooks_list = sorted(
        [h for h in hooks_list if isinstance(h, dict) and (h.get("text") or "").strip()],
        key=_hstart,
    )

    # Long video (no clip): STRICTLY 1 Hook. Shorts already force 1 packaging hook above.
    if clip is None and len(hooks_list) > 1:
        _log(
            f"[ХУКИ] long: limiting {len(hooks_list)} → 1 "
            f"(keep first «{(hooks_list[0].get('text') or '')[:40]}»)"
        )
        hooks_list = hooks_list[:1]

    # Граница CTA / конца ролика — для фильтра последнего хука
    cta_t = float(cta_start) if cta_start is not None and cta_start > 0 else None
    if cta_t is None and video_duration and video_duration > 3:
        cta_t = max(0.0, float(video_duration) - CTA_DUR)
    MIN_GAP_BEFORE_CTA = CTA_DUR  # не пересекаться с CTA-зоной

    MARKER_COLORS = (
        "&H00952DFF&",  # fuchsia
        "&H0000FFB8&",  # lime
        "&H00006BFF&",  # orange
        "&H00FFF000&",  # cyan
    )
    BLACK = "&H00000000&"

    # Предрасчёт таймингов, затем фильтр последнего vs CTA
    prepared: list[dict] = []
    for idx, hook in enumerate(hooks_list):
        text_h = (hook.get("text") or "").strip()
        if not text_h:
            continue
        words = text_h.split()
        if len(words) > 7:
            text_h = " ".join(words[:7])
        text_h = text_h.upper()

        start_t = float(hook.get("start", hook.get("timing", 0)) or 0)
        end_t = float(hook.get("end", start_t + HOOK_DUR) or (start_t + HOOK_DUR))

        if idx == 0:
            # Первый хук — сразу на первом кадре. Длительность ~HOOK_DUR (r24).
            # Для Shorts (clip=) времена АБСОЛЮТНЫЕ на полном ролике → start = clip.start.
            if clip:
                c0 = float(clip.get("start", 0) or 0)
                c1 = float(clip.get("end", c0 + 15) or (c0 + 15))
                start_t = c0
                end_t = min(c0 + HOOK_DUR, max(c0 + 3.0, c1 - CTA_DUR))
                if end_t <= start_t:
                    end_t = start_t + HOOK_DUR
            else:
                start_t = 0.0
                if end_t < HOOK_DUR - 0.5:
                    end_t = HOOK_DUR
                if end_t > HOOK_DUR + 0.5:
                    end_t = HOOK_DUR
        else:
            n_words = max(1, len(text_h.split()))
            min_dur = max(HOOK_DUR * 0.8, n_words * 0.5)
            if end_t - start_t < min_dur:
                end_t = start_t + min_dur

        prepared.append({
            "text": text_h,
            "start": start_t,
            "end": end_t,
            "raw": hook,
            "idx": idx,
        })

    if prepared and cta_t is not None:
        last = prepared[-1]
        # overlap или слишком близко к CTA
        if last["end"] > cta_t or (cta_t - last["start"]) < MIN_GAP_BEFORE_CTA:
            _log(
                f"[ХУКИ] последний хук отброшен (start={last['start']:.2f} end={last['end']:.2f} "
                f"cta_start={cta_t:.2f} gap={cta_t - last['start']:.2f}s < {MIN_GAP_BEFORE_CTA}s): "
                f"«{last['text'][:40]}»"
            )
            prepared = prepared[:-1]

    events = []
    for p in prepared:
        text_h = _strip_ass_overrides(p["text"])
        start_t = p["start"]
        end_t = p["end"]
        hook = p["raw"]
        idx = p["idx"]

        size = max(int(base_size * 1.25), int(base_size * 1.15))
        size = min(size, int(base_size * 1.55))
        bord = max(2, int(base_size * 0.03))

        if hook.get("color") and str(hook.get("color")).startswith("#"):
            color = _ass_color(hook["color"])
        else:
            color = MARKER_COLORS[(idx + int(start_t * 10)) % len(MARKER_COLORS)]

        if wide:
            cy = int(playres_y * 0.10)
            cx = playres_x // 2
            pos_t = f"\\an8\\pos({cx},{cy})"
        else:
            y_pct = float(hook.get("y_percent") or 12)
            y_pct = max(8.0, min(y_pct, 16.0))
            cy = int(playres_y * (y_pct / 100.0))
            cx = int(playres_x * float(hook.get("x_percent", 50)) / 100.0)
            pos_t = f"\\an8\\pos({cx},{cy})"

        main = (
            f"{{{pos_t}\\c{color}\\3c{BLACK}\\4c{BLACK}"
            f"\\fs{size}\\b1\\bord{bord}\\shad0\\be0"
            f"\\fad(80,150)}}"
            f"{text_h}"
        )
        events.append({
            "start": start_t,
            "end": end_t,
            "style": "HookMarker",
            "text": main,
            "layer": 2,
            "_hook_text": text_h,
        })
        _log(f"[ХУКИ] #{len(events)} «{text_h[:48]}» {start_t:.2f}–{end_t:.2f}s")

    return events


def _build_cta_events(
    analysis, playres_x, playres_y, wide, base_size,
    clip=None, video_duration: float = 0.0,
):
    """CTA только в конце: последние ~CTA_DUR секунд, позиция КАК У ХУКА (верх)."""
    if clip and clip.get("_skip_cta"):
        return []

    text_c = ""
    start_t = 0.0
    end_t = 0.0

    if clip and (clip.get("cta") or "").strip():
        text_c = str(clip.get("cta")).strip()
        c_end = float(clip.get("end", 0) or 0)
        c_start = float(clip.get("start", 0) or 0)
        # absolute times (burn later shifts by clip)
        end_t = float(clip.get("cta_end") or c_end or 0)
        start_t = float(clip.get("cta_start") or 0)
        if end_t <= 0:
            end_t = c_end
        if start_t <= 0 or (end_t - start_t) < 2.0 or start_t < (c_end - CTA_DUR - 1):
            # принудительно последние CTA_DUR сек клипа
            end_t = c_end
            start_t = max(c_start, c_end - CTA_DUR)
    else:
        key = "cta_wide" if wide else "cta_vertical"
        raw = analysis.get(key) or analysis.get("cta")
        if isinstance(raw, dict):
            text_c = (raw.get("text") or "").strip()
            start_t = float(raw.get("start") or 0)
            end_t = float(raw.get("end") or 0)
        elif isinstance(raw, str):
            text_c = raw.strip()

        dur = float(video_duration or 0)
        if dur <= 0:
            dur = float(analysis.get("duration") or 0)
        if dur <= 0:
            for h in (analysis.get("hooks_wide") or analysis.get("hooks_vertical")
                      or analysis.get("hooks") or []):
                dur = max(dur, float(h.get("end") or 0))
            for s in (analysis.get("subtitles") or []):
                dur = max(dur, float(s.get("end") or 0))
            for seg in (analysis.get("segments") or []):
                dur = max(dur, float(seg.get("end") or 0))

        if not text_c:
            return []

        # Всегда последние ~CTA_DUR секунд ролика (игнорируем start=0 от Gemini)
        if dur > 3:
            end_t = dur
            start_t = max(0.0, dur - CTA_DUR)
        else:
            # короткий ролик — вторая половина
            end_t = max(dur, 3.0)
            start_t = max(0.0, end_t - min(CTA_DUR, end_t * 0.5))

    if not text_c:
        return []

    words = text_c.split()
    if len(words) > 10:
        text_c = " ".join(words[:10])
    text_c = text_c.upper()

    if end_t <= start_t:
        end_t = start_t + CTA_DUR

    MARKER_COLORS = (
        "&H00952DFF&",
        "&H0000FFB8&",
        "&H00006BFF&",
        "&H00FFF000&",
    )
    BLACK = "&H00000000&"
    size = max(int(base_size * 1.15), int(base_size * 1.05))
    size = min(size, int(base_size * 1.45))
    bord = max(2, int(base_size * 0.03))
    color = MARKER_COLORS[2]  # orange — отличается от типичного первого хука

    # ТА ЖЕ зона, что и хук — ВЕРХ
    if wide:
        cy = int(playres_y * 0.10)
        cx = playres_x // 2
        pos_t = f"\\an8\\pos({cx},{cy})"
    else:
        cy = int(playres_y * 0.12)
        cx = playres_x // 2
        pos_t = f"\\an8\\pos({cx},{cy})"

    main = (
        f"{{{pos_t}\\c{color}\\3c{BLACK}"
        f"\\fs{size}\\b1\\bord{bord}\\shad0\\be0"
        f"\\fad(100,150)}}"
        f"{_strip_ass_overrides(text_c)}"
    )
    return [{
        "start": start_t,
        "end": end_t,
        "style": "HookMarker",
        "text": main,
        "layer": 2,
    }]


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
    caption_style: str = "auto_aisie",
    hook_style: str = "auto_aisie",
    bitrate: str | None = None,
    source_clips: list | None = None,
    force_size: tuple[int, int] | None = None,
    ass_only: bool = False,
) -> str:
    _log = log_fn or log.info
    _log("[СУБТИТРЫ] burn: karaoke in-line + hooks/CTA (без glow-overlay, strong только внутри строки)")
    if not output_path:
        output_path = video_path + ".subtitled.mp4"

    if force_size:
        playres_x, playres_y = int(force_size[0]), int(force_size[1])
    else:
        playres_x, playres_y = _probe_video_resolution(video_path)
    wide = _is_wide(playres_x, playres_y)
    platform = "youtube_16_9" if wide else "youtube_shorts"
    _log(
        f"[СУБТИТРЫ] caption={caption_style} hook={hook_style} aisie={use_aisie} "
        f"strong={enable_strong_words} subs={enable_subtitles} hooks={enable_hooks}"
    )

    if use_aisie and transcription:
        try:
            from .aisie_integration import enhance_analysis_with_aisie
            analysis = enhance_analysis_with_aisie(
                analysis=analysis,
                transcription=transcription,
                video_size=(playres_x, playres_y),
                platform=platform,
                log_fn=log_fn,
            )
            aisie_hooks = len((analysis.get("aisie") or {}).get("hooks") or [])
            aisie_subs = len((analysis.get("aisie") or {}).get("subtitles") or [])
            _log(f"[СУБТИТРЫ] AISIE: hooks={aisie_hooks} subtitles={aisie_subs}")
        except Exception as e:
            _log(f"[СУБТИТРЫ] AISIE failed: {e}")

    if wide:
        scale = max(playres_y / 1080.0, 1.0)
        base_size = max(52, int(60 * scale))
    else:
        scale = max(playres_x / 1080.0, 1.0)
        base_size = max(72, int(82 * scale))
    events = []
    n_sub_events = 0
    n_hook_events = 0
    n_cta_events = 0

    # Karaoke: одна строка; strong — только цвет+size внутри строки (если enable_strong_words)
    if enable_subtitles:
        words = _words_from_transcription(transcription)
        # Shorts/clip: только реально произнесённые слова внутри окна куска
        if clip and words:
            c0 = float(clip.get("start", 0) or 0)
            c1 = float(clip.get("end", 0) or 0)
            if c1 > c0:
                before = len(words)
                words = [
                    w for w in words
                    if float(w.get("end", 0)) > c0 and float(w.get("start", 0)) < c1
                ]
                # ужесточение: первое слово >= start, последнее <= end (с малым допуском)
                if words:
                    # trim leading words that mostly fall before c0
                    while words and float(words[0].get("start", 0)) < c0 - 0.05:
                        if float(words[0].get("end", 0)) <= c0:
                            words.pop(0)
                        else:
                            break
                    while words and float(words[-1].get("end", 0)) > c1 + 0.05:
                        if float(words[-1].get("start", 0)) >= c1:
                            words.pop()
                        else:
                            break
                _log(
                    f"[СУБТИТРЫ] clip speech window {c0:.2f}-{c1:.2f}s: "
                    f"words {before} → {len(words)}"
                )
        if words:
            strong_map = _strong_map(analysis) if enable_strong_words else {}
            _log(
                f"[СУБТИТРЫ] words={len(words)} strong_map={len(strong_map)} "
                f"pos={'bottom' if wide else 'mid(~54%)'}"
            )
            if strong_map:
                sample = list(strong_map.items())[:8]
                _log(f"[СУБТИТРЫ] strong sample: {sample}")
            sub_ev = _build_karaoke_window(
                words, analysis, playres_x, playres_y, base_size, wide=wide,
                caption_style=caption_style or "auto_aisie",
                honor_strong=bool(enable_strong_words),
            )
            events.extend(sub_ev)
            n_sub_events = len(sub_ev)
        else:
            for sub in analysis.get("subtitles") or []:
                t = (sub.get("text") or "").strip()
                if not t:
                    continue
                cx = playres_x // 2
                cy = int(playres_y * (0.88 if wide else 0.56))
                an = "2" if wide else "5"
                events.append({
                    "start": float(sub.get("start", 0)),
                    "end": float(sub.get("end", 0) or 1),
                    "style": "Default",
                    "text": f"{{\\an{an}\\pos({cx},{cy})}}{t}",
                    "layer": 0,
                })
                n_sub_events += 1
            _log(f"[СУБТИТРЫ] fallback segment subs={n_sub_events} (нет word-timings)")

    if enable_hooks:
        vid_dur = _probe_video_duration(video_path)
        if clip:
            try:
                vid_dur = max(vid_dur, float(clip.get("end", 0)) - float(clip.get("start", 0)))
            except Exception:
                pass
        # CTA start заранее, чтобы отфильтровать последний хук
        cta_start_guess = max(0.0, float(vid_dur) - CTA_DUR) if vid_dur > 3 else None
        if clip and (clip.get("cta") or "").strip():
            try:
                c_end = float(clip.get("end", 0) or 0)
                cta_start_guess = max(
                    float(clip.get("start", 0) or 0),
                    float(clip.get("cta_start") or (c_end - CTA_DUR)),
                )
            except Exception:
                pass

        hook_ev = _build_hook_events(
            analysis, playres_x, playres_y, wide, base_size,
            hook_style=hook_style or "auto_aisie",
            clip=clip,
            cta_start=cta_start_guess,
            video_duration=vid_dur,
            log_fn=_log,
        )
        events.extend(hook_ev)
        n_hook_events = len(hook_ev)

        cta_ev = _build_cta_events(
            analysis, playres_x, playres_y, wide, base_size,
            clip=clip, video_duration=vid_dur,
        )
        events.extend(cta_ev)
        n_cta_events = len(cta_ev)
        for ce in cta_ev:
            _log(f"[CTA] «{_strip_ass_overrides(ce.get('text') or '')[-60:]}» {ce['start']:.2f}–{ce['end']:.2f}s")

    # Отдельные top-aligned Strong events БОЛЬШЕ НЕ добавляем:
    # они и давали «увеличенные слова выше» поверх karaoke.
    # Strong только in-line через honor_strong в karaoke / clean_pro.
    if enable_strong_words and not _words_from_transcription(transcription):
        # fallback только если нет word-timings — и тогда ставим В ТУ ЖЕ зону субтитров, не наверх
        cx = playres_x // 2
        cy = int(playres_y * (0.88 if wide else 0.54))
        an = "2" if wide else "5"
        n_fb = 0
        for sw in analysis.get("strong_words") or []:
            word = (sw.get("word") or "").strip()
            if not word:
                continue
            timing = float(sw.get("timing", 0))
            weight = sw.get("visual_weight") or "L2"
            color, size = _weight_style(weight, base_size)
            events.append({
                "start": float(sw.get("start", timing)),
                "end": float(sw.get("end", timing + 1.2)),
                "style": "Strong",
                "text": f"{{\\an{an}\\pos({cx},{cy})\\c{color}\\fs{size}}}{word}",
                "layer": 1,
            })
            n_fb += 1
        if n_fb:
            _log(f"[СУБТИТРЫ] fallback strong (no words) n={n_fb} pos=subtitle-zone")

    if clip:
        events = _filter_and_shift_events(events, clip)
    if not events:
        _log("[СУБТИТРЫ] Нет событий")
        return video_path

    ass_path = os.path.join(tempfile.gettempdir(), f"vm_subs_{uuid.uuid4().hex[:10]}.ass")
    lines = [_generate_ass_header(playres_x, playres_y, wide)]
    for ev in events:
        layer = int(ev.get("layer", 0))
        text = ev["text"].replace("\n", "\\N")
        lines.append(
            f"Dialogue: {layer},{_ass_time(ev['start'])},{_ass_time(ev['end'])},"
            f"{ev.get('style', 'Default')},,0,0,0,,{text}"
        )
    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))
    _log(
        f"[СУБТИТРЫ] events total={len(events)} "
        f"(subs={n_sub_events} hooks={n_hook_events} cta={n_cta_events}) → ASS"
    )

    if ass_only:
        _log(f"[СУБТИТРЫ] ass_only → {ass_path}")
        return ass_path

    ffmpeg = _ffmpeg_bin()
    esc = _escape_ass_path(ass_path)

    # --- adaptive / explicit bitrate (4K: clamp 18–40M) ---
    pixels = playres_x * playres_y
    if bitrate:
        br = bitrate if str(bitrate).upper().endswith("M") else f"{bitrate}M"
    else:
        if pixels >= 3000 * 1600:
            br = calculate_adaptive_bitrate(
                clips=source_clips,
                video_path=video_path,
                target_w=playres_x,
                target_h=playres_y,
                min_mbps=18.0,
                max_mbps=40.0,
                safety=1.12,
                log_fn=_log,
            )
        elif pixels >= 1800 * 1000:
            br = calculate_adaptive_bitrate(
                clips=source_clips,
                video_path=video_path,
                target_w=playres_x,
                target_h=playres_y,
                min_mbps=10.0,
                max_mbps=25.0,
                safety=1.12,
                log_fn=_log,
            )
        else:
            br = "12M"
    _log(f"[СУБТИТРЫ] VT target bitrate={br} ({playres_x}x{playres_y})")

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_out = os.path.join(out_dir, f".vm_subs_{uuid.uuid4().hex[:10]}.mp4")

    vt = [
        "-c:v", "h264_videotoolbox", "-b:v", br, "-allow_sw", "0",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
    ]

    def _cleanup_tmp():
        for pth in (tmp_out, ass_path):
            try:
                if pth and os.path.exists(pth):
                    os.remove(pth)
            except OSError:
                pass

    def _run_vt(vf_expr: str):
        ensure_storage(video_path, tmp_out, log_fn=_log)
        cmd = [
            ffmpeg, "-y",
            "-hwaccel", "videotoolbox",
            "-i", video_path,
            "-vf", vf_expr, *vt, tmp_out,
        ]
        cmd = inject_hwaccel(cmd)
        return run_ffmpeg(cmd, log_fn=_log)

    filters_to_try = [
        f"subtitles='{esc}'",
        f"ass='{esc}'",
    ]
    last_err = ""
    max_io_rounds = 4

    try:
        for vf_expr in filters_to_try:
            filter_name = "subtitles" if "subtitles=" in vf_expr else "ass"
            for io_round in range(max_io_rounds):
                _log(
                    f"[СУБТИТРЫ] VT encode filter={filter_name} "
                    f"round={io_round + 1}/{max_io_rounds}"
                )
                result = None
                try:
                    result = _run_vt(vf_expr)
                except SubtitleStageFailed:
                    raise
                except Exception as e:
                    last_err = str(e)
                    _log(f"[СУБТИТРЫ] exception: {e}")

                if (
                    result is not None
                    and result.returncode == 0
                    and verify_mp4(tmp_out, log_fn=_log)
                ):
                    atomic_replace(tmp_out, output_path)
                    _cleanup_tmp()
                    _log(f"[СУБТИТРЫ] готово → {output_path}")
                    return output_path

                stderr = (result.stderr if result else last_err) or ""
                rc = result.returncode if result else -1
                reason = classify_ffmpeg_error(stderr, rc)
                last_err = stderr[-400:] if stderr else last_err
                _log(f"[СУБТИТРЫ] fail reason={reason} rc={rc}: {last_err[-200:]}")

                try:
                    if os.path.exists(tmp_out):
                        os.remove(tmp_out)
                except OSError:
                    pass

                if reason == "io":
                    try:
                        ensure_storage(video_path, tmp_out, log_fn=_log)
                    except SubtitleStageFailed:
                        _cleanup_tmp()
                        msg = (
                            "SUBTITLE_STAGE_FAILED: SSD недоступен во время "
                            "наложения субтитров. Подключите диск и повторите.\n"
                            + (last_err or "")
                        )
                        raise SubtitleStageFailed(msg)
                    continue

                if reason == "filter":
                    break

                if reason == "encoder":
                    _cleanup_tmp()
                    msg = (
                        "SUBTITLE_STAGE_FAILED: VideoToolbox encoder error "
                        "(без fallback на libx264 4K).\n" + (last_err or "")
                    )
                    raise SubtitleStageFailed(msg)

                if io_round < 1:
                    continue
                break

        _cleanup_tmp()
        msg = (
            "SUBTITLE_STAGE_FAILED: не удалось наложить субтитры через "
            "VideoToolbox.\n" + (last_err or "")
        )
        raise SubtitleStageFailed(msg)
    except SubtitleStageFailed:
        raise
    except Exception as e:
        _cleanup_tmp()
        raise SubtitleStageFailed(f"SUBTITLE_STAGE_FAILED: {e}") from e
