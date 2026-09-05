# VideoMaker FIX | 2026.09.05-r41 | 2026-09-05
# CHANGED:
#   r41: highlight_lexicon — полный перечень слов/идиом; «друг для друга» целиком;
#        один цвет = одна строка (≤2, ≤3 если короткие); разный цвет = разные строки
#   r40: phrase karaoke
#   r36: SF Pro Display
#   r35: full path:
#        • до речи все слова одинаковые (dim, один размер) — без предраскраски
#        • обычное активное слово: белый, БЕЗ scale
#        • strong L1–L4: цвет + scale 8–28% ТОЛЬКО в момент произнесения + pop
#   r34: CRITICAL — NameError strong_map is not defined в burn_subtitles
#   r31: CRITICAL — karaoke на full video (было только при clip)
#   r30: Clean Pro 2–3 слова; AISIE strong color/scale
# PREV: 2026.09.05-r40
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


def _caption_pos(playres_x: int, playres_y: int, wide: bool) -> str:
    """Vertical/shorts: центр текста на 56% (≈6% ниже середины), \an5.
    Wide: низ кадра, \an2.
    Важно: \an2 на mid поднимает тело строки ВЫШЕ середины — не использовать.
    """
    cx = playres_x // 2
    if wide:
        cy = int(playres_y * 0.90)
        return "{\\an2\\pos(%d,%d)}" % (cx, cy)
    cy = int(playres_y * 0.56)
    return "{\\an5\\pos(%d,%d)}" % (cx, cy)



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
Style: CleanPro,SF Pro Display,{sz},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,0,0,1,0,4,{align_k},{ml},{ml},{margin_v},1
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


def _norm_word_key(text: str) -> str:
    """Ключ для strong_map: lower, ё→е, без пунктуации."""
    import re
    t = (text or "").strip().lower().replace("ё", "е")
    t = re.sub(r"^[\s\.,!?;:«»\"\'()\[\]…—–\-]+", "", t)
    t = re.sub(r"[\s\.,!?;:«»\"\'()\[\]…—–\-]+$", "", t)
    return t


def _strong_lookup(strong: dict, word_text: str) -> str:
    """Точное совпадение ключа (без prefix — иначе красятся чужие слова)."""
    if not strong or not word_text:
        return ""
    key = _norm_word_key(word_text)
    if not key:
        return ""
    if key in strong:
        return str(strong[key] or "").upper()
    k2 = key.replace("-", "")
    if k2 and k2 in strong:
        return str(strong[k2] or "").upper()
    return ""


def _strong_map(analysis: dict, word_texts: list | None = None) -> dict:
    """Сильные слова: лексикон + идиомы + Gemini/AISIE (только если в лексиконе или идиома).

    «друг для друга» — только целиком (не одно «друг»).
    Цвета: L2 yellow, L3 orange, L4 pink.
    """
    from .highlight_lexicon import (
        build_lexicon_strong_map,
        lexicon_level,
        find_idioms_in_words,
        SINGLE_BLOCKLIST,
        IDIOM_PHRASES,
        _norm,
    )

    m: dict = {}
    rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}

    def put(key: str, weight: str):
        if not key or len(key) < 2:
            return
        w = (weight or "L2").upper()
        if w not in rank:
            w = "L2"
        prev = m.get(key)
        if prev is None or rank.get(w, 0) >= rank.get(str(prev).upper(), 0):
            m[key] = w

    texts = list(word_texts or [])
    if not texts:
        # собрать из strong_words / hooks тексты как fallback
        for sw in analysis.get("strong_words") or []:
            if isinstance(sw, dict):
                texts.extend(str(sw.get("word") or sw.get("text") or "").split())

    # 1) лексикон + идиомы по реальной цепочке слов
    if texts:
        for k, lv in build_lexicon_strong_map(texts).items():
            put(k, lv)

    # 2) Gemini strong_words — только если слово в лексиконе или часть идиомы
    for sw in analysis.get("strong_words") or []:
        if not isinstance(sw, dict):
            continue
        raw = sw.get("word") or sw.get("text") or ""
        wt = (sw.get("weight") or sw.get("visual_weight") or "L2").upper()
        parts = str(raw).split()
        if len(parts) >= 2:
            phrase = " ".join(_norm(x) for x in parts)
            if phrase in IDIOM_PHRASES:
                for part in parts:
                    put(_norm(part), IDIOM_PHRASES[phrase])
                continue
        for part in parts:
            k = _norm(part)
            if not k or k in SINGLE_BLOCKLIST:
                continue
            lv = lexicon_level(part)
            if lv:
                # Gemini может поднять вес не ниже лексикона
                put(k, wt if rank.get(wt, 0) >= rank.get(lv, 0) else lv)
            # иначе игнор — нет в перечне

    # 3) AISIE keyword groups — фильтр через лексикон; идиомы приоритетнее
    hooks = list(analysis.get("aisie", {}).get("hooks") or []) + list(analysis.get("hooks") or [])
    for h in hooks:
        if not isinstance(h, dict):
            continue
        for g in h.get("semantic_groups") or []:
            if not isinstance(g, dict) or not bool(g.get("is_keyword_group")):
                continue
            raw = g.get("words") or g.get("text") or ""
            parts = raw if isinstance(raw, list) else str(raw).split()
            parts = [str(x).strip() for x in parts if str(x).strip()]
            if not parts:
                continue
            phrase = " ".join(_norm(x) for x in parts)
            if phrase in IDIOM_PHRASES:
                for part in parts:
                    put(_norm(part), IDIOM_PHRASES[phrase])
                continue
            for part in parts:
                k = _norm(part)
                if k in SINGLE_BLOCKLIST:
                    continue
                lv = lexicon_level(part)
                if lv:
                    put(k, lv)

    return m




def _shorts_phrase_boundaries(words: list[dict], clips: list | None) -> tuple[set[int], set[int]]:
    """Индексы слов: must_start / must_end по клипам Gemini (clips_for_shorts).

    Для каждого шорта: первое слово окна = начало словосочетания на экране,
    последнее слово окна = конец словосочетания. Так cut с vertical не режет
    фразу посередине.
    """
    must_start: set[int] = set()
    must_end: set[int] = set()
    if not words or not clips:
        return must_start, must_end
    n = len(words)
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        try:
            c0 = float(clip.get("start", 0) or 0)
            c1 = float(clip.get("end", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c1 <= c0:
            continue
        # Слова, пересекающие окно шорта (не только «полностью внутри»)
        idxs = [
            i for i, w in enumerate(words)
            if float(w.get("end", 0)) > c0
            and float(w.get("start", 0)) < c1
        ]
        if not idxs:
            continue
        # Убрать слово, почти целиком ДО start (overlap < 30% длительности слова)
        while idxs:
            w = words[idxs[0]]
            ws, we = float(w.get("start", 0)), float(w.get("end", 0))
            dur = max(we - ws, 1e-3)
            overlap = max(0.0, min(we, c1) - max(ws, c0))
            if ws < c0 and overlap / dur < 0.30:
                idxs.pop(0)
                continue
            break
        while idxs:
            w = words[idxs[-1]]
            ws, we = float(w.get("start", 0)), float(w.get("end", 0))
            dur = max(we - ws, 1e-3)
            overlap = max(0.0, min(we, c1) - max(ws, c0))
            if we > c1 and overlap / dur < 0.30:
                idxs.pop()
                continue
            break
        if not idxs:
            continue
        must_start.add(idxs[0])
        must_end.add(idxs[-1])
    return must_start, must_end


def _group_words_with_boundaries(
    words: list[dict],
    must_start: set[int] | None = None,
    must_end: set[int] | None = None,
    max_words: int = 3,
    max_chars: int = 28,
) -> list[list[int]]:
    """Группы 2–3 слова (макс. 4 — только если все слова короткие).

    Жёсткие правила (AISIE-совместимо):
    - по умолчанию 2–3 слова на экране;
    - 4 слова только если каждое ≤ 4 символов И суммарно ≤ max_chars;
    - must_start / must_end — границы шортов Gemini (не раздувают группу!).
    """
    n = len(words)
    if n == 0:
        return []
    must_start = set(must_start or ())
    must_end = set(must_end or ())
    sticky = {
        "и", "а", "но", "да", "или", "либо", "ни",
        "в", "на", "по", "к", "с", "у", "о", "об", "обо", "из", "от", "до",
        "для", "при", "без", "над", "под", "про", "через",
        "не", "же", "ли", "бы", "то", "это", "как", "что", "чтобы",
        "я", "ты", "он", "она", "мы", "вы", "они",
    }
    # max_chars потолок на длину фразы (не зависит от 4K ширины)
    max_chars = min(int(max_chars or 28), 28)

    groups: list[list[int]] = []
    i = 0
    while i < n:
        hard_end = n
        for e in sorted(must_end):
            if e >= i:
                hard_end = min(hard_end, e + 1)
                break
        for s in sorted(must_start):
            if s > i:
                hard_end = min(hard_end, s)
                break

        remain = hard_end - i
        if remain <= 0:
            groups.append([i])
            i += 1
            continue

        # целевой размер: 2 или 3
        target = 2 if remain >= 2 else 1
        if remain >= 3:
            # 3 слова, если не слишком длинные
            chunk3 = words[i:i + 3]
            chars3 = sum(len((w.get("text") or "")) for w in chunk3) + 2
            if chars3 <= max_chars:
                target = 3
        # 4 только если remain>=4 и ВСЕ слова короткие
        if remain >= 4 and target == 3:
            chunk4 = words[i:i + 4]
            if all(len((w.get("text") or "")) <= 4 for w in chunk4):
                chars4 = sum(len((w.get("text") or "")) for w in chunk4) + 3
                if chars4 <= max_chars:
                    target = 4

        end = min(i + target, hard_end)

        # sticky: частица в конце → захватить ещё одно (но не > 4 и не > hard_end)
        if end < hard_end and (end - i) < 4:
            last = _norm_word_key(words[end - 1].get("text") or "")
            if last in sticky:
                end = min(end + 1, hard_end, i + 4)

        # не оставлять одно слово, если можно 2
        if end - i == 1 and end < hard_end:
            end = min(i + 2, hard_end)

        # сирота: забрать только если новое число слов ≤ 3
        # (4 — только отдельной веткой «все короткие» выше)
        if hard_end - end == 1 and (end - i + 1) <= 3:
            end = hard_end

        groups.append(list(range(i, end)))
        i = end
    return groups



def _group_words_static(
    words: list[dict],
    must_start: set[int] | None = None,
    must_end: set[int] | None = None,
) -> list[list[int]]:
    """Статичные группы 2–3 слова + границы шортов Gemini."""
    return _group_words_with_boundaries(
        words, must_start=must_start, must_end=must_end, max_words=3, max_chars=28,
    )


def _group_words_one_line(
    words: list[dict],
    max_chars: int = 28,
    max_words: int = 3,
    must_start: set[int] | None = None,
    must_end: set[int] | None = None,
    strong: dict | None = None,
) -> list[list[int]]:
    """Clean Pro: фразы 2–3 слова; strong-словосочетания не рвутся.

    Phrase karaoke: важное слово + соседняя частица/слово держатся в одной группе.
    """
    groups = _group_words_with_boundaries(
        words, must_start=must_start, must_end=must_end,
        max_words=max_words, max_chars=max_chars,
    )
    if not strong or not words:
        return groups

    sticky = {"не", "ни"}  # только отрицание перед strong; «для» уже в идиоме
    rank = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

    def level_idx(i: int) -> str:
        k = _norm_word_key(words[i].get("text") or "")
        if not k or k not in strong:
            return ""
        return str(strong[k] or "").upper()

    def is_strong_idx(i: int) -> bool:
        return rank.get(level_idx(i), 0) >= 2

    def is_sticky_idx(i: int) -> bool:
        return _norm_word_key(words[i].get("text") or "") in sticky

    n = len(words)
    out: list[list[int]] = []
    i = 0
    while i < n:
        lv = level_idx(i)
        # Strong: только одинаковый цвет в одной строке; max 2, или 3 если все короткие
        if rank.get(lv, 0) >= 2 or (is_sticky_idx(i) and i + 1 < n and rank.get(level_idx(i + 1), 0) >= 2):
            cluster = []
            # ведущая частица только если следующий strong
            if is_sticky_idx(i) and rank.get(lv, 0) < 2:
                cluster.append(i)
                i += 1
                lv = level_idx(i) if i < n else ""
            base_lv = lv
            while i < n and rank.get(level_idx(i), 0) >= 2:
                cur = level_idx(i)
                # разный цвет → другая строка
                if cluster and cur != base_lv and rank.get(base_lv, 0) >= 2:
                    break
                if not base_lv:
                    base_lv = cur
                # один цвет: до 3 слов (идиомы вроде «друг для друга»)
                if len(cluster) >= 3:
                    break
                cluster.append(i)
                i += 1
            if cluster:
                out.append(cluster)
                continue
        # Ordinary: 2–3, не забирать strong
        take = 2 if i + 1 < n else 1
        if i + 2 < n:
            take = 3
        end = min(i + take, n)
        while end > i + 1 and is_sticky_idx(end - 1) and end < n and is_strong_idx(end):
            end -= 1
        while end > i and is_strong_idx(end - 1):
            end -= 1
        if end <= i:
            end = i + 1
        out.append(list(range(i, end)))
        i = end
    return out if out else groups



def _build_clean_pro_window(
    words, analysis, playres_x, playres_y, base_size, wide: bool = False,
    must_start: set | None = None, must_end: set | None = None,
):
    """Phrase karaoke (как ShortsMaker chunks).

    • Группа 2–3 слова на экране вместе (strong-словосочетание не рвётся).
    • Strong-фраза: все слова уже чуть крупнее, цвет БЕЛЫЙ до речи.
    • В момент речи: вся фраза ещё крупнее; neon только на текущем слове.
    • Ordinary: dim → white, без scale.
    """
    if not words:
        return []

    fs = max(52, int(base_size * 1.05))
    max_chars = 28
    max_words = 3
    pos = _caption_pos(playres_x, playres_y, wide)

    # Phrase karaoke (ShortsMaker-style chunks):
    # • группа 2–3 слова на экране вместе
    # • strong-фраза: вся группа уже чуть крупнее, цвет БЕЛЫЙ до речи
    # • при речи: вся группа ещё крупнее; neon только на текущем слове
    # • ordinary: dim → white, без scale
    WHITE = "&H00FFFFFF&"
    DIM = "&H00C8C8C8&"
    NEON_YELLOW = "&H0000FFFF&"       # L2 #FFFF00
    NEON_ORANGE = "&H00005EFF&"       # L3 #FF5E00
    NEON_PINK = "&H00FF00FF&"         # L4 #FF00FF
    NEON_ORANGE_SOFT = "&H0000A5FF&"  # L1 #FFA500

    PHRASE_BASE = 1.08    # strong-фраза до речи (чуть больше, цвет стандартный)
    PHRASE_ACTIVE = 1.22  # вся фраза в момент речи
    WORD_POP = {"L4": 1.32, "L3": 1.26, "L2": 1.18, "L1": 1.12}  # текущее strong-слово
    STRONG_COLOR = {
        "L4": NEON_PINK,
        "L3": NEON_ORANGE,
        "L2": NEON_YELLOW,
        "L1": NEON_ORANGE_SOFT,
    }

    strong = _strong_map(analysis, [str(w.get("text") or "") for w in words])
    rank = {"L1": 1, "L2": 2, "L3": 3, "L4": 4}

    def word_level(t: str) -> str:
        w = _strong_lookup(strong, t)
        return w if w in STRONG_COLOR else ""

    def group_is_phrase(g: list[int]) -> bool:
        return any(word_level(words[i]["text"]) for i in g)

    def tag_word(text: str, is_active: bool, phrase: bool, any_active: bool) -> str:
        """phrase=True → группа strong: base/active scale на всех; color только active strong."""
        bs = chr(92)
        lvl = word_level(text)

        if phrase:
            # вся фраза одного «веса» по размеру
            if any_active:
                size = int(fs * PHRASE_ACTIVE)
            else:
                size = int(fs * PHRASE_BASE)
            # цвет: только проговариваемое strong-слово → neon; иначе белый
            if is_active and lvl:
                color = STRONG_COLOR[lvl]
                # доп. pop на текущем слове
                size = int(fs * WORD_POP.get(lvl, PHRASE_ACTIVE))
                open_t = (
                    "{" + bs + "c" + color
                    + bs + "fs" + str(size)
                    + bs + "b1"
                    + bs + "bord0" + bs + "shad3" + bs + "4c&H000000&" + bs + "4a&H70&"
                    + bs + "t(0,100," + bs + "fscx115" + bs + "fscy115)"
                    + bs + "t(100,220," + bs + "fscx100" + bs + "fscy100)"
                    + "}"
                )
            elif is_active:
                color = WHITE
                open_t = (
                    "{" + bs + "c" + color
                    + bs + "fs" + str(size)
                    + bs + "b1"
                    + bs + "bord0" + bs + "shad3" + bs + "4c&H000000&" + bs + "4a&H70&"
                    + "}"
                )
            else:
                # до речи и соседние в фразе — стандартный белый, чуть крупнее
                color = WHITE
                open_t = (
                    "{" + bs + "c" + color
                    + bs + "fs" + str(size)
                    + bs + "b1"
                    + bs + "bord0" + bs + "shad3" + bs + "4c&H000000&" + bs + "4a&H70&"
                    + "}"
                )
            return open_t + text + "{" + bs + "r}"

        # ordinary group
        if is_active:
            color, size, bold = WHITE, fs, "1"
        else:
            color, size, bold = DIM, fs, "0"
        open_t = (
            "{" + bs + "c" + color
            + bs + "fs" + str(size)
            + bs + "b" + bold
            + bs + "bord0" + bs + "shad3" + bs + "4c&H000000&" + bs + "4a&H70&"
            + "}"
        )
        return open_t + text + "{" + bs + "r}"

    events: list[dict] = []
    for group in _group_words_one_line(
        words, max_chars=max_chars, max_words=max_words,
        must_start=must_start, must_end=must_end, strong=strong,
    ):
        edges = [float(words[i]["start"]) for i in group]
        edges.append(float(words[group[-1]]["end"]))
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + 0.05
        if edges[-1] - edges[-2] < 0.12:
            edges[-1] = edges[-2] + 0.18

        phrase = group_is_phrase(group)
        for gi, active in enumerate(group):
            t0, t1 = edges[gi], edges[gi + 1]
            parts = []
            for j in group:
                wt = words[j]["text"]
                parts.append(tag_word(wt, j == active, phrase, any_active=True if j == active else False))
            # any_active for size: when building event for active word, whole phrase is "in speech"
            # re-tag with consistent any_active for this event
            parts = []
            for j in group:
                wt = words[j]["text"]
                parts.append(tag_word(wt, j == active, phrase, any_active=True))
            events.append({
                "start": t0,
                "end": t1,
                "style": "CleanPro",
                "text": pos + " ".join(parts),
                "layer": 1,
            })
        # events before first word of group: show phrase white larger without neon
        # (optional lead-in) — group appears at first word start already handled

    return events



def _build_karaoke_window(
    words, analysis, playres_x, playres_y, base_size,
    wide: bool = False, caption_style: str = "auto_aisie",
    honor_strong: bool = True,
    must_start: set | None = None, must_end: set | None = None,
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
            must_start=must_start, must_end=must_end,
        )

    pos = _caption_pos(playres_x, playres_y, wide)
    default_key = "clean_pro" if wide else "hormozi"

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

    strong = (_strong_map(analysis, [str(w.get("text") or "") for w in words]) if honor_strong else {})

    # Neon yellow / orange / pink (без cyan)
    NEON_YELLOW = "&H0000FFFF&"
    NEON_ORANGE = "&H00005EFF&"
    NEON_PINK = "&H00FF00FF&"
    NEON_ORANGE_SOFT = "&H0000A5FF&"

    def strong_weight(word_text: str) -> str:
        if not honor_strong or not strong:
            return ""
        return _strong_lookup(strong, word_text)

    BASE_SCALE_K = {"L4": 1.12, "L3": 1.10, "L2": 1.06, "L1": 1.04}
    ACTIVE_SCALE_K = {"L4": 1.32, "L3": 1.24, "L2": 1.16, "L1": 1.10}
    STRONG_COLOR_K = {
        "L4": NEON_PINK,
        "L3": NEON_ORANGE,
        "L2": NEON_YELLOW,
        "L1": NEON_ORANGE_SOFT,
    }

    normal_size = max(40, int(base_size * 0.90))
    bord_a = max(5, int(base_size * (0.14 if key == "cliffhanger" else 0.11)))
    bord_d = max(3, int(base_size * 0.07))

    def tags_word(text: str, is_active: bool) -> str:
        w = strong_weight(text)
        is_strong = w in STRONG_COLOR_K
        bs = chr(92)

        if is_strong:
            color = STRONG_COLOR_K[w]
            if is_active:
                size = max(int(base_size * ACTIVE_SCALE_K[w]), normal_size + 4)
                size = min(size, int(base_size * 1.40))
                if is_box:
                    return (
                        "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b1}"
                        + text + "{" + bs + "r}"
                    )
                return (
                    "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b1"
                    + bs + "bord" + str(bord_a) + bs + "shad0" + bs + "be1"
                    + bs + "t(0,100," + bs + "fscx118" + bs + "fscy118)"
                    + bs + "t(100,220," + bs + "fscx100" + bs + "fscy100)}"
                    + text + "{" + bs + "r}"
                )
            size = max(int(base_size * BASE_SCALE_K[w]), normal_size + 2)
            if is_box:
                return (
                    "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b1}"
                    + text + "{" + bs + "r}"
                )
            return (
                "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b1"
                + bs + "bord" + str(bord_d) + "}"
                + text + "{" + bs + "r}"
            )

        # ordinary: active white, no scale (ShortsMaker: color-only karaoke;
        # у нас без rainbow на каждое слово — только white/dim)
        if is_active:
            color, size, bold, bord = col_active, normal_size, "1", bord_a
        else:
            color, size, bold, bord = col_dim, normal_size, "0", bord_d
        if is_box:
            return (
                "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b" + bold + "}"
                + text + "{" + bs + "r}"
            )
        return (
            "{" + bs + "c" + color + bs + "fs" + str(size) + bs + "b" + bold
            + bs + "bord" + str(bord) + "}"
            + text + "{" + bs + "r}"
        )



    events: list[dict] = []
    groups = _group_words_static(words, must_start=must_start, must_end=must_end)
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

    # Karaoke: всегда при наличии words (full + clip). Fallback — только если words пусто.
    if enable_subtitles:
        words = _words_from_transcription(transcription)
        _wt = [str(w.get("text") or "") for w in (_words_from_transcription(transcription) or [])]
        strong_map = _strong_map(analysis, _wt) if enable_strong_words else {}
        if words:
            # Shorts/clip: окно только реально произнесённых слов
            if clip:
                c0 = float(clip.get("start", 0) or 0)
                c1 = float(clip.get("end", 0) or 0)
                if c1 > c0:
                    before = len(words)
                    eps = 0.02
                    inside = [
                        w for w in words
                        if float(w.get("end", 0)) > c0 + eps * 0.5
                        and float(w.get("start", 0)) < c1 - eps * 0.5
                    ]
                    while inside and float(inside[0].get("end", 0)) <= c0 + 0.08 and float(inside[0].get("start", 0)) < c0:
                        inside.pop(0)
                    while inside and float(inside[-1].get("start", 0)) >= c1 - 0.08 and float(inside[-1].get("end", 0)) > c1:
                        inside.pop()
                    while inside and float(inside[0].get("start", 0)) < c0 - 0.12:
                        inside.pop(0)
                    while inside and float(inside[-1].get("end", 0)) > c1 + 0.12:
                        inside.pop()
                    words = inside
                    _log(
                        f"[СУБТИТРЫ] clip speech window {c0:.2f}-{c1:.2f}s: "
                        f"words {before} → {len(words)}"
                        + (f" first=«{words[0]['text']}» last=«{words[-1]['text']}»" if words else "")
                    )
            if strong_map:
                sample = list(strong_map.items())[:8]
                _log(f"[СУБТИТРЫ] strong sample: {sample}")
            _log(
                f"[СУБТИТРЫ] words={len(words)} strong_map={len(strong_map)} "
                f"pos={'bottom' if wide else 'mid+6%(an5@56%)'}"
            )
            # Границы словосочетаний: clip → first/last; full → shorts Gemini
            must_start: set = set()
            must_end: set = set()
            if clip and words:
                must_start.add(0)
                must_end.add(len(words) - 1)
                _log(
                    f"[СУБТИТРЫ] phrase bounds (clip): first=«{words[0].get('text')}» "
                    f"last=«{words[-1].get('text')}»"
                )
            else:
                clips = (analysis or {}).get("clips_for_shorts") or []
                must_start, must_end = _shorts_phrase_boundaries(words, clips)
                if must_start or must_end:
                    _log(
                        f"[СУБТИТРЫ] phrase bounds (shorts×{len(clips)}): "
                        f"starts={sorted(must_start)[:12]} ends={sorted(must_end)[:12]}"
                    )
            sub_ev = _build_karaoke_window(
                words, analysis, playres_x, playres_y, base_size, wide=wide,
                caption_style=caption_style or "auto_aisie",
                honor_strong=bool(enable_strong_words),
                must_start=must_start, must_end=must_end,
            )
            events.extend(sub_ev)
            n_sub_events = len(sub_ev)
        else:
            # Нет words совсем — сегменты analysis (редкий fallback)
            for sub in analysis.get("subtitles") or []:
                t = (sub.get("text") or "").strip()
                if not t:
                    continue
                pos = _caption_pos(playres_x, playres_y, wide)
                events.append({
                    "start": float(sub.get("start", 0)),
                    "end": float(sub.get("end", 0) or 1),
                    "style": "Default",
                    "text": f"{pos}{t}",
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
        pos = _caption_pos(playres_x, playres_y, wide)
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
                "text": ("{0}{{\\c{1}\\fs{2}}}{3}").format(pos, color, size, word),
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
