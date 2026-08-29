"""Субтитры: karaoke (vertical/shorts) + classic YouTube (wide) + AISIE hooks."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid

log = logging.getLogger(__name__)

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
):
    """Karaoke-группы с РЕАЛЬНО разными пресетами.

    Почему раньше все стили выглядели одинаково:
    1) PrimaryColour у всех Style был белый, Secondary не использовался (без karaoke-тегов).
    2) На каждое слово вешался одинаковый inline \\c жёлтый + одинаковый \\t pop.
    3) Glow-слой всегда с жёлтым свечением поверх.
    4) Структура кадра (2–4 слова, dim+active) не менялась — глаз видел «тот же Hormozi».

    Сейчас у каждого caption_style свой пресет: цвет active/dim, box, pop, glow, размер.
    Тайминги стыкуются без дыр (end[i] = start[i+1] внутри группы).
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

    # ASS BGR. Пресеты специально контрастные.
    # active = цвет текущего слова; dim = остальные в группе;
    # style = имя Style из заголовка ASS; pop = scale-анимация; glow = второй слой.
    PRESETS = {
        # glow_* — мягкое свечение без жёсткой границы (большой bord + высокий be + alpha)
        "hormozi": {
            "style": "Hormozi",
            "active": "&H0000EBFF&",
            "dim": "&H00C8C8C8&",
            "glow": "&H0000EBFF&",
            "use_glow": True,
            "glow_be": 6,
            "glow_alpha": "60",
            "glow_bord": 16,
            "use_pop": True,
            "pop_pct": 118,
            "box": False,
            "active_scale": 1.22,
        },
        "hormozi_green": {
            "style": "HormoziGreen",
            "active": "&H004CFF00&",
            "dim": "&H00B0B0B0&",
            "glow": "&H004CFF00&",
            "use_glow": True,
            "glow_be": 6,
            "glow_alpha": "60",
            "glow_bord": 16,
            "use_pop": True,
            "pop_pct": 118,
            "box": False,
            "active_scale": 1.22,
        },
        "tiktok_box": {
            "style": "TikTokBox",
            "active": "&H0000EBFF&",
            "dim": "&H00FFFFFF&",
            "glow": None,
            "use_glow": False,
            "glow_be": 0,
            "glow_alpha": "FF",
            "glow_bord": 0,
            "use_pop": False,
            "pop_pct": 100,
            "box": True,
            "active_scale": 1.12,
        },
        "clean_pro": {
            "style": "CleanPro",
            "active": "&H00FFFFFF&",
            "dim": "&H00A0A0A0&",
            "glow": None,
            "use_glow": False,
            "glow_be": 0,
            "glow_alpha": "FF",
            "glow_bord": 0,
            "use_pop": False,
            "pop_pct": 100,
            "box": False,
            "active_scale": 1.08,
        },
        "bold_pop": {
            "style": "BoldPop",
            "active": "&H000000FF&",
            "dim": "&H00B8B8B8&",
            "glow": "&H0000A5FF&",
            "use_glow": True,
            "glow_be": 7,
            "glow_alpha": "55",
            "glow_bord": 18,
            "use_pop": True,
            "pop_pct": 125,
            "box": False,
            "active_scale": 1.35,
        },
        "cliffhanger": {
            "style": "Cliffhanger",
            "active": "&H000000FF&",
            "dim": "&H00666699&",
            "glow": "&H000000FF&",
            "use_glow": True,
            "glow_be": 8,
            "glow_alpha": "50",
            "glow_bord": 20,
            "use_pop": True,
            "pop_pct": 132,
            "box": False,
            "active_scale": 1.40,
        },
    }
    preset = PRESETS.get(key, PRESETS["hormozi"])
    style_name = preset["style"]
    col_active = preset["active"]
    col_dim = preset["dim"]
    col_glow = preset["glow"] or col_active
    use_glow = bool(preset["use_glow"])
    use_pop = bool(preset["use_pop"])
    pop_pct = int(preset["pop_pct"])
    active_scale = float(preset["active_scale"])
    is_box = bool(preset["box"])

    strong = _strong_map(analysis)
    # auto_aisie-подобное усиление только если исходный выбор был auto
    honor_strong = (caption_style or "").strip().lower() in ("", "auto_aisie", "auto")

    RED = "&H000000FF&"
    ORANGE = "&H0000A5FF&"
    YELLOW = "&H0000EBFF&"
    GREEN = "&H004CFF00&"

    def color_for_word(word_text: str) -> str:
        if not honor_strong:
            return col_active
        w = strong.get(word_text.lower().strip(".,!?;:«»\"'"), "")
        w = (w or "").upper()
        if w == "L4":
            return RED
        if w == "L3":
            return ORANGE
        if w == "L1":
            return GREEN
        if w == "L2":
            return YELLOW
        return col_active

    normal_size = max(40, int(base_size * 0.90))
    active_size = max(int(base_size * active_scale), normal_size + 4)
    active_size = min(active_size, int(base_size * 1.55))
    bord_a = max(5, int(base_size * (0.14 if key == "cliffhanger" else 0.11)))
    bord_d = max(3, int(base_size * 0.07))

    def tags_active(text: str, color: str) -> str:
        if is_box:
            # BorderStyle=3 из Style — не трогаем \\bord агрессивно
            return f"{{\\c{color}\\fs{active_size}\\b1}}{text}{{\\r}}"
        if use_pop and pop_pct > 100:
            return (
                f"{{\\c{color}\\fs{active_size}\\b1\\bord{bord_a}\\shad0\\be1"
                f"\\t(0,100,\\fscx{pop_pct}\\fscy{pop_pct})"
                f"\\t(100,220,\\fscx100\\fscy100)}}"
                f"{text}{{\\r}}"
            )
        return f"{{\\c{color}\\fs{active_size}\\b1\\bord{bord_a}}}{text}{{\\r}}"

    def tags_dim(text: str) -> str:
        if is_box:
            return f"{{\\c{col_dim}\\fs{normal_size}\\b0}}{text}{{\\r}}"
        return f"{{\\c{col_dim}\\fs{normal_size}\\b0\\bord{bord_d}}}{text}{{\\r}}"

    events: list[dict] = []
    groups = _group_words_static(words)

    for group in groups:
        if not group:
            continue
        # Точные стыки: интервалы [t0,t1), [t1,t2), ... без дыр
        edges = []
        for idx in group:
            edges.append(float(words[idx]["start"]))
        edges.append(float(words[group[-1]]["end"]))
        # монотонность
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + 0.05
        # чуть расширить последний хвост если слово короткое
        if edges[-1] - edges[-2] < 0.12:
            edges[-1] = edges[-2] + 0.18

        for gi, active in enumerate(group):
            t0, t1 = edges[gi], edges[gi + 1]
            parts = []
            for j in group:
                wt = words[j]["text"]
                if j == active:
                    parts.append(tags_active(wt, color_for_word(wt)))
                else:
                    parts.append(tags_dim(wt))
            line = pos + " ".join(parts)

            if use_glow and col_glow:
                g_be = int(preset.get("glow_be") or 6)
                g_alpha = str(preset.get("glow_alpha") or "60")
                g_bord = int(preset.get("glow_bord") or 16)
                glow_parts = []
                for j in group:
                    wt = words[j]["text"]
                    if j == active:
                        # Мягкое свечение цвета стиля: bord+be, без жёсткой кромки
                        glow_parts.append(
                            f"{{\\fs{int(active_size * 1.04)}\\b1"
                            f"\\bord{g_bord}\\be{g_be}\\shad0"
                            f"\\c{col_glow}\\3c{col_glow}\\4c{col_glow}"
                            f"\\alpha&H{g_alpha}&\\3a&H{g_alpha}&}}{wt}{{\\r}}"
                        )
                    else:
                        glow_parts.append(f"{{\\alpha&HFF&}}{wt}{{\\r}}")
                events.append({
                    "start": t0, "end": t1, "style": "Glow",
                    "text": pos + " ".join(glow_parts), "layer": 0,
                })

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
):
    """Хуки-маркеры сверху.

    - Первый хук: с 0.0 (сразу на первом кадре / обложка).
    - Остальные: строго по таймингу Gemini (start/end).
    - Без glow. Тонкий чёрный outline.
    """
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

    # Shorts: один packaging-хук клипа
    if clip and (clip.get("hook") or "").strip():
        hs = float(clip.get("hook_start", clip.get("start", 0)) or 0)
        he = float(clip.get("hook_end", hs + 2.8) or (hs + 2.8))
        hooks_list = [{
            "text": str(clip.get("hook")).strip(),
            "start": hs,
            "end": he,
            "timing": hs,
            "type": "CURIOSITY",
            "visual_weight": "L4",
        }]
    if not hooks_list:
        return []

    # Сортировка по времени
    def _hstart(h):
        return float(h.get("start", h.get("timing", 0)) or 0)
    hooks_list = sorted(
        [h for h in hooks_list if isinstance(h, dict) and (h.get("text") or "").strip()],
        key=_hstart,
    )

    MARKER_COLORS = (
        "&H00952DFF&",  # fuchsia
        "&H0000FFB8&",  # lime
        "&H00006BFF&",  # orange
        "&H00FFF000&",  # cyan
    )
    BLACK = "&H00000000&"

    events = []
    for idx, hook in enumerate(hooks_list):
        text_h = (hook.get("text") or "").strip()
        if not text_h:
            continue
        words = text_h.split()
        if len(words) > 7:
            text_h = " ".join(words[:7])
        text_h = text_h.upper()

        start_t = float(hook.get("start", hook.get("timing", 0)) or 0)
        end_t = float(hook.get("end", start_t + 2.8) or (start_t + 2.8))

        # Первый хук — всегда с нуля (сразу виден / обложка)
        if idx == 0:
            start_t = 0.0
            if end_t < 2.0:
                end_t = 2.8
            # не держать первый хук бесконечно
            if end_t > 4.0:
                end_t = 3.2
        else:
            # Второй+ — только Gemini, без сдвига в 0
            n_words = max(1, len(text_h.split()))
            min_dur = max(2.0, n_words * 0.45)
            if end_t - start_t < min_dur:
                end_t = start_t + min_dur

        size = max(int(base_size * 1.25), int(base_size * 1.15))
        size = min(size, int(base_size * 1.55))
        bord = max(2, int(base_size * 0.03))

        if hook.get("color") and str(hook.get("color")).startswith("#"):
            color = _ass_color(hook["color"])
        else:
            color = MARKER_COLORS[(idx + int(start_t * 10)) % len(MARKER_COLORS)]

        # Позиция: ВЕРХ (и хук, и CTA — одна зона)
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
        })
    return events


def _build_cta_events(
    analysis, playres_x, playres_y, wide, base_size,
    clip=None, video_duration: float = 0.0,
):
    """CTA только в конце: последние ~5 секунд, позиция КАК У ХУКА (верх)."""
    CTA_DUR = 5.0

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
            # принудительно последние 5с клипа
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

        # Всегда последние ~5 секунд ролика (игнорируем start=0 от Gemini)
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
        f"{text_c}"
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
) -> str:
    _log = log_fn or log.info
    _log("[СУБТИТРЫ] burn (AISIE + karaoke/classic)...")
    if not output_path:
        output_path = video_path + ".subtitled.mp4"

    playres_x, playres_y = _probe_video_resolution(video_path)
    wide = _is_wide(playres_x, playres_y)
    platform = "youtube_16_9" if wide else "youtube_shorts"
    _log(f"[СУБТИТРЫ] styles caption={caption_style} hook={hook_style} aisie={use_aisie}")
    _log("[СУБТИТРЫ] источник: WhisperX word-timestamps → premium ASS (libass)")
    _log(f"[СУБТИТРЫ] {playres_x}x{playres_y} mode={'wide/karaoke-bottom' if wide else 'vertical/karaoke-center'}")

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
            _log("[СУБТИТРЫ] AISIE applied")
        except Exception as e:
            _log(f"[СУБТИТРЫ] AISIE failed: {e}")

    if wide:
        scale = max(playres_y / 1080.0, 1.0)
        base_size = max(52, int(60 * scale))
    else:
        scale = max(playres_x / 1080.0, 1.0)
        base_size = max(72, int(82 * scale))
    events = []

    # Karaoke везде: wide — низ (как YouTube), vertical — чуть ниже середины
    if enable_subtitles:
        words = _words_from_transcription(transcription)
        if words:
            events.extend(_build_karaoke_window(
                words, analysis, playres_x, playres_y, base_size, wide=wide,
                caption_style=caption_style or "auto_aisie",
            ))
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

    if enable_hooks:
        vid_dur = _probe_video_duration(video_path)
        if clip:
            # длительность куска шортса
            try:
                vid_dur = max(vid_dur, float(clip.get("end", 0)) - float(clip.get("start", 0)))
            except Exception:
                pass
        events.extend(_build_hook_events(
            analysis, playres_x, playres_y, wide, base_size,
            hook_style=hook_style or "auto_aisie",
            clip=clip,
        ))
        events.extend(_build_cta_events(
            analysis, playres_x, playres_y, wide, base_size,
            clip=clip, video_duration=vid_dur,
        ))

    if enable_strong_words and not _words_from_transcription(transcription):
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
                "text": f"{{\\an8\\c{color}\\fs{size}}}{word}",
                "layer": 1,
            })

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
    _log(f"[СУБТИТРЫ] {len(events)} events → {ass_path}")

    ffmpeg = _ffmpeg_bin()
    esc = _escape_ass_path(ass_path)
    # 4K: VideoToolbox + высокий bitrate, без soft libx264 «fast»
    pixels = playres_x * playres_y
    if pixels >= 3000 * 1600:
        bitrate = "50M"
    elif pixels >= 1800 * 1000:
        bitrate = "25M"
    else:
        bitrate = "12M"
    vt = [
        "-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "1",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
    ]
    _log(f"[СУБТИТРЫ] encode VT bitrate={bitrate} size={playres_x}x{playres_y}")
    cmd = [ffmpeg, "-y", "-i", video_path, "-vf", f"subtitles='{esc}'", *vt, output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _log(f"[СУБТИТРЫ] VT/subtitles fail → ass+libx264: {(result.stderr or '')[-300:]}")
        cmd2 = [
            ffmpeg, "-y", "-i", video_path, "-vf", f"ass='{esc}'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy", output_path,
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        try:
            os.remove(ass_path)
        except OSError:
            pass
        if result2.returncode != 0:
            _log(f"[СУБТИТРЫ] ffmpeg error: {(result2.stderr or '')[-400:]}")
            return video_path
    else:
        try:
            os.remove(ass_path)
        except OSError:
            pass
    return output_path
