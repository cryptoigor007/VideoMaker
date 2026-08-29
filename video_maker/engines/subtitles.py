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
}
HOOK_STYLES = {
    "auto_aisie": "Auto (AISIE)",
    "hormozi": "Hormozi Yellow",
    "impact": "Impact Orange",
    "neon": "Neon Green",
    "soft": "Soft White",
    "bold": "Bold White",
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
Style: Hormozi,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: HormoziGreen,Arial Black,{sz},&H00FFFFFF&,&H004CFF00&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: TikTokBox,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&HD0000000&,-1,0,0,0,100,100,0,0,3,0,0,{align_k},{ml},{ml},{margin_v},1
Style: CleanPro,Arial,{sz},&H00FFFFFF&,&H00FFFFFF&,&H00222222&,&H80000000&,-1,0,0,0,100,100,0,0,1,{max(4,bord-2)},2,{align_k},{ml},{ml},{margin_v},1
Style: BoldPop,Arial Black,{sz},&H00FFFFFF&,&H000000FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,2,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: Karaoke,Arial Black,{sz},&H00FFFFFF&,&H0000EBFF&,&H00000000&,&H64000000&,-1,0,0,0,100,100,1,0,1,{bord},0,{align_k},{ml},{ml},{margin_v},1
Style: Glow,Arial Black,{sz},&H00000000&,&H00000000&,&H0000EBFF&,&H00000000&,-1,0,0,0,100,100,0,0,1,{bord+4},0,{align_k},{ml},{ml},{margin_v},1
Style: Hook,Arial Black,{hook_sz},&H00FFFFFF&,&H000000FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,1,0,1,{bord_h},0,{align_h},{ml},{ml},{margin_v},1
Style: HookHormozi,Arial Black,{hook_sz},&H0000EBFF&,&H000000FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,1,0,1,{bord_h},0,{align_h},{ml},{ml},{margin_v},1
Style: HookImpact,Arial Black,{hook_sz},&H0000A5FF&,&H000000FF&,&H00000000&,&H90000000&,-1,0,0,0,100,100,2,0,1,{bord_h},1,{align_h},{ml},{ml},{margin_v},1
Style: HookNeon,Arial Black,{hook_sz},&H004CFF00&,&H000000FF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,1,0,1,{bord_h},2,{align_h},{ml},{ml},{margin_v},1
Style: HookSoft,Arial,{max(int(hook_sz*0.88), sz)},&H00FFFFFF&,&H000000FF&,&H00333333&,&H80000000&,-1,0,0,0,100,100,0,0,1,{max(3,bord-2)},3,{align_h},{ml},{ml},{margin_v},1
Style: HookGlow,Arial Black,{hook_sz},&H00000000&,&H00000000&,&H0000EBFF&,&H00000000&,-1,0,0,0,100,100,0,0,1,{bord_h+6},0,{align_h},{ml},{ml},{margin_v},1
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


def _build_karaoke_window(
    words, analysis, playres_x, playres_y, base_size,
    wide: bool = False, caption_style: str = "auto_aisie",
):
    """Premium karaoke из WhisperX word-timestamps.
    Статичная группа 2–3 слова, активное: цвет + scale-pop + blur glow.
    """
    if not words:
        return []
    cx = playres_x // 2
    if wide:
        cy = int(playres_y * 0.90)
        pos = f"{{\\an2\\pos({cx},{cy})}}"
        default_base = "CleanPro"
    else:
        cy = int(playres_y * 0.54)
        pos = f"{{\\an5\\pos({cx},{cy})}}"
        default_base = "Hormozi"

    style_map = {
        "hormozi": "Hormozi",
        "hormozi_green": "HormoziGreen",
        "tiktok_box": "TikTokBox",
        "clean_pro": "CleanPro",
        "bold_pop": "BoldPop",
        "auto_aisie": default_base,
    }
    base_style = style_map.get(caption_style, default_base)
    use_box = caption_style == "tiktok_box" or base_style == "TikTokBox"

    strong = _strong_map(analysis)
    dim = "&H00B0B0B0&"
    normal_size = max(42, int(base_size * 0.86))
    YELLOW = "&H0000EBFF&"   # #FFEB00 Hormozi
    GREEN = "&H004CFF00&"    # #00FF4C
    ORANGE = "&H0000A5FF&"
    RED = "&H000000FF&"
    WHITE = "&H00FFFFFF&"

    def accent_for(weight: str) -> tuple[str, str]:
        w = (weight or "L2").upper()
        forced = base_style if caption_style != "auto_aisie" else None
        if w == "L4":
            return RED, forced or "BoldPop"
        if w == "L3":
            return ORANGE, forced or "Hormozi"
        if w == "L2":
            return YELLOW, forced or "Hormozi"
        if w == "L1":
            return GREEN, forced or "HormoziGreen"
        return YELLOW, forced or base_style

    def active_tags(color: str, size: int, text: str) -> str:
        # CapCut-like: blur edge + scale pop + bold
        return (
            f"{{\\c{color}\\fs{size}\\b1\\bord{max(6, int(base_size*0.12))}\\shad0\\be1"
            f"\\t(0,90,\\fscx122\\fscy122)\\t(90,170,\\fscx100\\fscy100)}}"
            f"{text}{{\\r}}"
        )

    def dim_tags(text: str) -> str:
        return f"{{\\c{dim}\\fs{normal_size}\\b0\\bord{max(4, int(base_size*0.08))}\\be0}}{text}{{\\r}}"

    events = []
    for group in _group_words_static(words):
        for active in group:
            parts = []
            style_name = "TikTokBox" if use_box else base_style
            for j in group:
                wt = words[j]["text"]
                if j == active:
                    weight = strong.get(wt.lower().strip(".,!?;:«»\""), "L2")
                    color, style_name = accent_for(weight)
                    if use_box:
                        style_name = "TikTokBox"
                    size = max(int(base_size * 1.25), int(base_size * float(
                        (VISUAL_WEIGHTS.get(weight) or {}).get("font_scale", 1.2)
                    )))
                    size = min(size, int(base_size * 1.60))
                    parts.append(active_tags(color, size, wt))
                else:
                    parts.append(dim_tags(wt))
            line = pos + " ".join(parts)
            start = words[active]["start"]
            end = words[active]["end"]
            # glow layer (behind) — мягкое свечение активного слова
            glow_parts = []
            for j in group:
                wt = words[j]["text"]
                if j == active:
                    glow_parts.append(
                        f"{{\\fs{int(base_size*1.3)}\\b1\\bord{max(10, int(base_size*0.18))}\\be2\\c&H0000EBFF&\\3c&H0000EBFF&\\alpha&H60&}}{wt}{{\\r}}"
                    )
                else:
                    glow_parts.append(f"{{\\alpha&HFF&}}{wt}{{\\r}}")  # hide non-active on glow layer
            events.append({
                "start": start, "end": end, "style": "Glow",
                "text": pos + " ".join(glow_parts), "layer": 0,
            })
            events.append({
                "start": start, "end": end, "style": style_name,
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


def _build_hook_events(
    analysis, playres_x, playres_y, wide, base_size,
    hook_style: str = "auto_aisie",
):
    """Premium hooks: glow layer + fade/scale — WhisperX/AISIE timings."""
    hooks_list = analysis.get("aisie", {}).get("hooks") or analysis.get("hooks") or []
    if not hooks_list:
        h = analysis.get("hook")
        if isinstance(h, dict) and h.get("text"):
            hooks_list = [h]
        else:
            return []

    YELLOW = "&H0000EBFF&"
    GREEN = "&H004CFF00&"
    ORANGE = "&H0000A5FF&"
    WHITE = "&H00FFFFFF&"

    force = {
        "hormozi": ("HookHormozi", YELLOW),
        "impact": ("HookImpact", ORANGE),
        "neon": ("HookNeon", GREEN),
        "soft": ("HookSoft", WHITE),
        "bold": ("Hook", WHITE),
    }

    def pick(h: dict) -> tuple[str, str]:
        if hook_style in force:
            return force[hook_style]
        weight = (h.get("visual_weight") or "L3").upper()
        htype = (h.get("type") or "").upper()
        if weight == "L4" or htype in ("REVELATION", "CONTRADICTION"):
            return "HookImpact", ORANGE
        if htype in ("QUESTION", "CURIOSITY"):
            return "HookHormozi", YELLOW
        if htype in ("IDENTITY", "LOSS"):
            return "HookSoft", WHITE
        if weight == "L3":
            return "HookHormozi", YELLOW
        return "HookNeon", GREEN

    events = []
    for hook in hooks_list:
        text = (hook.get("text") or "").strip()
        if not text:
            continue
        start = float(hook.get("start", hook.get("timing", 0)))
        end = float(hook.get("end", start + 3.0))
        weight = hook.get("visual_weight") or "L3"
        _, size = _weight_style(weight, base_size)
        size = max(size, int(base_size * 1.35))
        style, color = pick(hook)
        if hook.get("color"):
            color = _ass_color(hook["color"])

        anim = (
            f"\\fad(100,200)\\be1"
            f"\\t(0,140,\\fscx115\\fscy115)\\t(140,260,\\fscx100\\fscy100)"
        )
        if wide:
            main = f"{{\\an8\\c{color}\\fs{size}\\b1\\bord{max(7, int(base_size*0.12))}{anim}}}"
            glow = f"{{\\an8\\fs{int(size*1.05)}\\b1\\bord{max(14, int(base_size*0.22))}\\be3\\c{color}\\3c{color}\\alpha&H70&\\fad(100,200)}}"
        else:
            y_pct = float(hook.get("y_percent") or 12)
            cy = int(playres_y * (y_pct / 100.0))
            cx = int(playres_x * float(hook.get("x_percent", 50)) / 100.0)
            main = f"{{\\an8\\pos({cx},{cy})\\c{color}\\fs{size}\\b1\\bord{max(7, int(base_size*0.12))}{anim}}}"
            glow = f"{{\\an8\\pos({cx},{cy})\\fs{int(size*1.05)}\\b1\\bord{max(14, int(base_size*0.22))}\\be3\\c{color}\\3c{color}\\alpha&H70&\\fad(100,200)}}"

        events.append({"start": start, "end": end, "style": "HookGlow", "text": glow + text, "layer": 1})
        events.append({"start": start, "end": end, "style": style, "text": main + text, "layer": 2})
    return events


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
                cy = int(playres_y * (0.88 if wide else 0.53))
                an = "2" if wide else "5"
                events.append({
                    "start": float(sub.get("start", 0)),
                    "end": float(sub.get("end", 0) or 1),
                    "style": "Default",
                    "text": f"{{\\an{an}\\pos({cx},{cy})}}{t}",
                    "layer": 0,
                })

    if enable_hooks:
        events.extend(_build_hook_events(analysis, playres_x, playres_y, wide, base_size, hook_style=hook_style or "auto_aisie"))

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
