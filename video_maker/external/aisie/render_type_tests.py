# AISIE Phase 4.3 — Render test videos для всех 8 типов хуков
# через полную цепочку: HOOK_TYPES → VISUAL_WEIGHTS → TimingEngine
# → PlacementEngine → AdvancedTextEngine.create_styled_text_clip.
# Отличие от старых hook_*.mp4: группы появляются ПОСЛЕДОВАТЕЛЬНО
# по таймингам TimingEngine, позиция — от PlacementEngine, вес — L0..L4.

import sys
from pathlib import Path

from advanced_text_features import AdvancedTextEngine
from moviepy import ColorClip, CompositeVideoClip
from text_overlay_engine import _resolve_font_path
from text_style_utils import HOOK_TYPES, VISUAL_WEIGHTS

from .classifier import HookClassifier
from .placement import PROFILES, Obstacles, PlacementEngine
from .timing import TimingEngine
from .validate import StyleValidator

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "aisie_tests"
OUT.mkdir(exist_ok=True)

VW, VH = 1080, 1920
DUR = 4.6
FPS = 24

EXAMPLES = {
    "QUESTION":      "ПОЧЕМУ МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ?",
    "CONTRADICTION": "СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕ.",
    "STATEMENT":     "МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ.",
    "CURIOSITY":     "ИМЕННО ЗДЕСЬ НАЧИНАЕТСЯ ПРОБЛЕМА.",
    "IDENTITY":      "ЕСЛИ ТЕБЕ УЖЕ ЗА 30...",
    "LOSS":          "ОДНАЖДЫ ТЫ ЗАМЕТИШЬ...",
    "REVELATION":    "И ВОТ ЧТО МЫ НЕ ЗАМЕЧАЕМ.",
    "VISUAL_HOOK":   "",
}


def build_style(htype: str) -> dict:
    info = HOOK_TYPES[htype]
    weight = info["visual_weight"]
    scale = VISUAL_WEIGHTS[weight]["font_scale"]
    return {
        "font_size": int(75 * scale),
        "effect": info["preset_effect"],
        "visual_weight": weight,
        "position": "top",
        "top_offset_percent": 0.15,
        "text_color": "#FF7A12",          # фирменный оранжевый (раздел 18)
        "stroke_color": "#000000",
        "stroke_width": 1,
        "uppercase": True,
        "align": "center",
    }


def main() -> int:
    engine = AdvancedTextEngine()
    timer = TimingEngine()
    placer = PlacementEngine(PROFILES["youtube_shorts"])
    validator = StyleValidator()
    clf = HookClassifier()

    results = []
    for htype, text in EXAMPLES.items():
        style = build_style(htype)
        font = _resolve_font_path(None, style["font_size"], "great vibes")

        clips = []
        groups = []
        if text:
            dec = placer.choose((VW, VH), Obstacles(),
                                current_visual_weight=style["visual_weight"],
                                font_frac_by_weight={"L0": .050, "L1": .060,
                                                     "L2": .075, "L3": .085, "L4": .100})
            style["top_offset_percent"] = float(dec.y_percent) / 100.0
            if dec.visual_weight_override:
                style["visual_weight"] = dec.visual_weight_override
                style["font_size"] = int(75 * VISUAL_WEIGHTS[
                    style["visual_weight"]]["font_scale"])

            groups = timer.build_timeline(text, 0.30,
                                          hook_type=htype, speech_rate=1.0)
            for g in groups:
                g_style = dict(style)
                g_style["text"] = g.text          # для NOT_UPPERCASE-проверки
                clip = engine.create_styled_text_clip(
                    g.text.upper(), g.start, min(g.end + 0.45, DUR),
                    (VW, VH), g_style, font)
                clips.append(clip)

        bg = ColorClip(size=(VW, VH), color=(10, 10, 14), duration=DUR)
        final = CompositeVideoClip([bg] + clips, size=(VW, VH)) \
            .with_duration(DUR)
        out_path = OUT / f"aisie_{htype}.mp4"
        final.write_videofile(str(out_path), codec="libx264",
                              audio_codec="aac", fps=FPS, logger=None)
        final.close()
        for c in clips:
            c.close()

        _ = clf.classify(text, visual_hook=(not text))
        issues = validator.validate_clip(dict(style, text=text),
                                         effect=style["effect"],
                                         visual_weight=style["visual_weight"])
        results.append((htype, style["visual_weight"], style["effect"],
                        len(groups), bool(issues)))
        grp_txt = " -> ".join(g.text for g in groups) or "(без текста)"
        flag = "" if not issues else f"  [{'; '.join(i.code for i in issues)}]"
        print(f"{htype:<13} L={style['visual_weight']:<3} "
              f"fx={style['effect']:<13} groups={len(groups)}  {grp_txt}{flag}")

    ok = sum(1 for r in results if not r[4])
    print(f"\nГотово: {len(results)} видео в {OUT}/ "
          f"(чисто по валидатору: {ok}/{len(results)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
