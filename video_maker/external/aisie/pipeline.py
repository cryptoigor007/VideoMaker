# AISIE Phase 3 — Pipeline Orchestrator + Smart Subtitle JSON
# (разделы 3, 21, 22, 24, 25 ТЗ)
#
# ПОРЯДОК (раздел 25 — главный принцип):
#   СМЫСЛ → ВАЖНОСТЬ → ВНИМАНИЕ → КОМПОЗИЦИЯ → SAFE ZONE → МЕСТО → РАЗМЕР
#   → ГРУППИРОВКА → ЗАДЕРЖКА → АКЦЕНТ → АНИМАЦИЯ
#
# Структура первых 3 секунд (раздел 3):
#   0.00-0.30 VISUAL HOOK   — текста может не быть
#   0.30-0.80 HOOK ENTRY    — первая смысловая группа
#   0.80-1.50 DEVELOPMENT   — вторая группа (curiosity/tension)
#   1.50-2.30 OPEN LOOP
#   2.30-3.00 PROMISE SETUP
#
# Раздел 22: hook ≠ первый subtitle. Если первая фраза слабая (<56),
# текст молчит до 0.30s — работает визуал.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from aisie.classifier import HookClassification, HookClassifier
from aisie.placement import PROFILES, Obstacles, PlacementEngine
from aisie.scoring import AttentionResult, AttentionScorer
from aisie.timing import TimingEngine
from aisie.validate import StyleValidator

HOOK_THRESHOLD = 76          # accent и выше становятся хуками
WEAK_FIRST_PHRASE = 56       # раздел 22: ниже — «visual hook» окно
VISUAL_HOOK_DELAY = 0.30     # сек тишины текста на старте (раздел 3)

FONT_BY_WEIGHT = {"L0": .050, "L1": .060, "L2": .075, "L3": .085, "L4": .100}
BRAND_FONT = "TikTok Sans Black"
BRAND_COLOR = "#FF7A12"


@dataclass
class HookPlan:
    type: str
    attention_score: int
    band: str
    text: str
    start: float
    end: float
    visual_weight: str
    animation: str
    position_zone: str
    y_percent: float
    x_percent: float = 50.0
    color: str = BRAND_COLOR
    font: str = BRAND_FONT
    uppercase: bool = True
    semantic_groups: list = field(default_factory=list)
    placement_reason: str = ""
    weight_downgraded: bool = False


@dataclass
class SubtitlePlan:
    text: str
    start: float
    end: float
    visual_weight: str = "L0"
    animation: str = "fade_move"


@dataclass
class VisualLoad:
    """Раздел 21: перегрузка кадра ограничивает текст."""
    visual: str = "LOW"        # LOW|MEDIUM|HIGH — по движению/монтажу
    audio: str = "LOW"         # по темпу речи/эффектам
    text_allowed: str = "MEDIUM"

    @staticmethod
    def compute(motion_score: float, speech_rate: float) -> VisualLoad:
        visual = "HIGH" if motion_score > 18 else ("MEDIUM" if motion_score > 8 else "LOW")
        audio = "HIGH" if speech_rate > 2.6 else ("MEDIUM" if speech_rate > 1.9 else "LOW")
        if visual == "HIGH" and audio == "HIGH":
            allowed = "LOW"          # лицо+zoom+sfx → минимум текста
        elif visual == "HIGH" or audio == "HIGH":
            allowed = "MEDIUM"
        else:
            allowed = "MEDIUM/HIGH"
        return VisualLoad(visual, audio, allowed)


class AISIEPipeline:
    """Полный конвейер WhisperX-транскрипта → Smart Subtitle JSON."""

    def __init__(self, platform: str = "youtube_shorts"):
        self.profile = PROFILES[platform]
        self.platform = platform
        self.scorer = AttentionScorer()
        self.classifier = HookClassifier()
        self.timer = TimingEngine()
        self.validator = StyleValidator()

    # ---------------- основной вход ----------------

    def process(
        self,
        segments: list[dict],                 # [{text,start,end}] из WhisperX/Gemini
        video_size: tuple[int, int] = (1080, 1920),
        obstacles: Obstacles | None = None,
        motion_score: float = 5.0,
        word_timings: list[dict] | None = None,
    ) -> dict:
        load = VisualLoad.compute(motion_score, self._speech_rate(segments))
        scored: list[AttentionResult] = self.scorer.score_transcript(segments)

        hooks = self._pick_hooks(scored, segments, video_size, obstacles,
                                 load, word_timings)
        subtitles = self._build_subtitles(scored, segments, hooks, load)
        issues = self._validate_output(hooks, subtitles)

        return {
            "schema": "aisie.smart_subtitle.v1",
            "platform": self.platform,
            "aspect": self.profile.aspect,
            "video_size": {"w": video_size[0], "h": video_size[1]},
            "safe_zones": asdict(self.profile),
            "visual_load": asdict(load),
            "hooks": [asdict(h) for h in hooks],
            "subtitles": [asdict(s) for s in subtitles],
            "validation_issues": [str(i) for i in issues],
        }

    # ---------------- этапы ----------------

    @staticmethod
    def _speech_rate(segments: list[dict]) -> float:
        words = sum(len(str(s.get("text", "")).split()) for s in segments)
        dur = max((s.get("end", 0) - s.get("start", 0) for s in segments), default=1)
        dur = max(sum((s.get("end", 0) - s.get("start", 0)) for s in segments), 0.1)
        return words / dur if dur else 2.0

    def _pick_hooks(self, scored, segments, video_size, obstacles,
                    load, word_timings) -> list[HookPlan]:
        engine = PlacementEngine(self.profile)
        hooks: list[HookPlan] = []
        taken_ranges: list[tuple[float, float]] = []

        for att, seg in zip(scored, segments):
            if att.total < HOOK_THRESHOLD or att.band == "normal":
                continue
            cls: HookClassification = self.classifier.classify(
                seg["text"], attention_score=att.total)
            if cls.hook_type == "VISUAL_HOOK":
                continue

            # Раздел 22: слабое начало → визуал работает первым 0.30s
            start = float(seg["start"])
            if start < VISUAL_HOOK_DELAY and att.total < WEAK_FIRST_PHRASE:
                start = VISUAL_HOOK_DELAY
            elif start < VISUAL_HOOK_DELAY and any(
                    a >= HOOK_THRESHOLD for a in
                    [x.total for x in scored[:scored.index(att)]]):
                pass                       # уже есть более ранний хук — не сдвигаем

            # Не дублируем перекрывающиеся хуки
            if any(s < e and start < e for s, e in taken_ranges):
                continue

            groups = self.timer.build_timeline(
                seg["text"], start, hook_type=cls.hook_type,
                word_timings=word_timings)
            if not groups:
                continue
            end = float(seg["end"])

            info_weight = {"CONTRADICTION": "L4", "REVELATION": "L4"}.get(
                cls.hook_type, "L3")
            dec = engine.choose(video_size, obstacles,
                                current_visual_weight=info_weight,
                                font_frac_by_weight=FONT_BY_WEIGHT)
            weight = dec.visual_weight_override or info_weight
            anim = {"CONTRADICTION": "quick_cut",
                    "CURIOSITY": "phrase_reveal",
                    "IDENTITY": "phrase_reveal",
                    "REVELATION": "hold_accent"}.get(
                        cls.hook_type, "fade_move")
            if load.text_allowed == "LOW":
                weight, anim = min(weight, "L1"), "fade_move"

            hooks.append(HookPlan(
                type=cls.hook_type,
                attention_score=att.total,
                band=att.band,
                text=seg["text"],
                start=start,
                end=end,
                visual_weight=weight,
                animation=anim,
                position_zone=dec.zone,
                y_percent=dec.y_percent,
                x_percent=dec.x_percent,
                semantic_groups=[asdict(g) for g in groups],
                placement_reason=dec.reason,
                weight_downgraded=bool(dec.visual_weight_override),
            ))
            taken_ranges.append((start, end))
        hooks.sort(key=lambda h: h.start)
        return hooks

    def _build_subtitles(self, scored, segments, hooks, load) -> list[SubtitlePlan]:
        subs: list[SubtitlePlan] = []
        for att, seg in zip(scored, segments):
            overlaps_hook = any(h["start"] <= float(seg["start"]) < h["end"]
                                for h in map(asdict, hooks))
            if overlaps_hook or att.total >= HOOK_THRESHOLD:
                continue                   # хук уже покрывает этот отрезок
            if load.text_allowed == "LOW" and att.band == "normal":
                continue                   # раздел 21: разгружаем кадр
            # Разделы 3+22: первые 0.30s — окно визуального хука,
            # обычный субтитр туда не ставим
            sub_start = max(float(seg["start"]), VISUAL_HOOK_DELAY) \
                if float(seg["start"]) < VISUAL_HOOK_DELAY else float(seg["start"])
            subs.append(SubtitlePlan(
                text=str(seg["text"]).upper(),
                start=sub_start,
                end=float(seg["end"]),
            ))
        return subs

    def _validate_output(self, hooks, subtitles):
        specs = [{"effect": h.animation,
                  "visual_weight": h.visual_weight,
                  "karaoke": False} for h in hooks]
        specs += [{"effect": s.animation, "visual_weight": s.visual_weight}
                  for s in subtitles]
        return self.validator.validate_sequence(specs)

    # ---------------- утилиты ----------------

    def save_json(self, plan: dict, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        return path


if __name__ == "__main__":
    demo_segments = [
        {"text": "Мы живём в мире бесконечного выбора.", "start": 0.00, "end": 2.10},
        {"text": "СВОБОДЫ СТАЛО БОЛЬШЕ.", "start": 2.20, "end": 3.60},
        {"text": "НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕ.", "start": 3.70, "end": 5.40},
        {"text": "И вот что мы обычно не замечаем.", "start": 5.50, "end": 7.60},
        {"text": "Каждый день мы выбираем из тысяч вещей.", "start": 7.70, "end": 10.2},
    ]
    pipe = AISIEPipeline(platform="youtube_shorts")
    plan = pipe.process(demo_segments, video_size=(1080, 1920),
                        motion_score=6.0)
    print(f"Платформа : {plan['platform']} ({plan['aspect']})")
    print(f"Загрузка  : {plan['visual_load']}")
    print(f"\nХУКИ ({len(plan['hooks'])}):")
    for h in plan["hooks"]:
        print(f"\n  [{h['start']:.2f}-{h['end']:.2f}] {h['type']} "
              f"score={h['attention_score']} band={h['band']}")
        print(f"    вес={h['visual_weight']} анимация={h['animation']} "
              f"зона={h['position_zone']} y={h['y_percent']}%")
        print(f"    причина: {h['placement_reason']}")
        for g in h["semantic_groups"]:
            key = "KEY" if g["is_keyword_group"] else "   "
            print(f"      +{g['delay_ms']:>3}ms {key} [{g['start']:.2f}] {g['text']}")
    print(f"\nСУБТИТРЫ ({len(plan['subtitles'])}):")
    for s in plan["subtitles"]:
        print(f"  [{s['start']:.2f}-{s['end']:.2f}] {s['text']}")
    out = pipe.save_json(plan, "/Users/dreamstore/shorts_maker_full/aisie/demo_plan.json")
    print(f"\nJSON сохранён: {out}")
