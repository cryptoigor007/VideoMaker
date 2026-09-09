# AISIE Phase 1.2 — Hook Type Classifier (раздел 4 ТЗ)
#
# 8 типов:
#   QUESTION       «ПОЧЕМУ МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ?»
#   CONTRADICTION  «СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕ.»
#   STATEMENT      «МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ.»
#   CURIOSITY      «ИМЕННО ЗДЕСЬ НАЧИНАЕТСЯ ПРОБЛЕМА.»
#   IDENTITY       «ЕСЛИ ТЕБЕ УЖЕ ЗА 30...»
#   LOSS           «ОДНАЖДЫ ТЫ ЗАМЕТИШЬ...»
#   REVELATION     «И ВОТ ЧТО МЫ НЕ ЗАМЕЧАЕМ.»
#   VISUAL_HOOK    текст минимален/отсутствует — вопрос создаёт визуал
#
# Приоритет разрешения конфликтов (фраза может бить в несколько семейств):
#   CONTRADICTION > REVELATION > QUESTION > LOSS > IDENTITY > CURIOSITY > STATEMENT
# LOSS требует 2 сигналов (маркер + местоимение/"однажды"), иначе это просто
# STATEMENT — по эталону ТЗ «МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ» = STATEMENT, не LOSS.

from __future__ import annotations

import re
from dataclasses import dataclass, field

HOOK_TYPES = [
    "QUESTION", "CONTRADICTION", "STATEMENT", "CURIOSITY",
    "IDENTITY", "LOSS", "REVELATION", "VISUAL_HOOK",
]

Q_MARKERS = ["почему", "зачем", "что если", "правда ли", "кто ", "как так"]
CONTRA_MARKERS = ["но", "однако", "хотя", "несмотря на", "вопреки",
                  "а ведь", "наоборот", "казалось бы", "стало больше", "не стали"]
CURIOSITY_MARKERS = ["именно здесь", "проблема начинается", "мало кто знает",
                     "никто не знает", "секрет", "тайна", "всё изменилось"]
IDENTITY_MARKERS = ["если тебе", "если вы", "каждый из нас", "мы все",
                    "у тебя", "у вас", "твой", "ваш", "наше поколение", "люди старше"]
LOSS_MARKERS = ["потеряли", "разучились", "перестали", "уже не",
                "больше нет", "ушло", "пропало", "утрачено",
                "однажды ты заметишь", "однажды вы заметите"]
REVELATION_MARKERS = ["правда в том", "реальность такова", "скрытая", "истина",
                      "не замечаем", "не замечаете", "и вот что", "оказывается",
                      "выяснилось", "на самом деле"]

PRONOUNS = {"я", "ты", "вы", "мы", "нам", "нас", "тебя", "вас", "меня"}


@dataclass
class HookClassification:
    text: str
    hook_type: str
    confidence: float                 # 0..1
    attention_score: int = 0          # прокинуть из scoring при желании
    family_hits: dict = field(default_factory=dict)


def _hits(norm: str, markers: list) -> list[str]:
    found = []
    for m in markers:
        pattern = r"(?<![a-zа-я0-9])" + re.escape(m) + r"(?![a-zа-я0-9])"
        if re.search(pattern, norm):
            found.append(m)
    return found


class HookClassifier:
    """Классифицирует фразу-хук по одному из 8 типов ТЗ."""

    def classify(
        self,
        text: str,
        attention_score: int = 0,
        visual_hook: bool = False,
    ) -> HookClassification:
        norm = text.lower().replace("ё", "е")
        words = re.findall(r"[a-zа-я0-9]+", norm)

        # VISUAL_HOOK: текст пуст / почти пуст / явно помечен как визуальный
        if visual_hook or len(words) <= 1:
            return HookClassification(text, "VISUAL_HOOK", 0.9,
                                      attention_score, {"visual": [text.strip()]})

        hits = {
            "CONTRADICTION": _hits(norm, CONTRA_MARKERS),
            "REVELATION": _hits(norm, REVELATION_MARKERS),
            "QUESTION": (_hits(norm, Q_MARKERS)
                         + (["?"] if "?" in text else [])),
            "LOSS": _hits(norm, LOSS_MARKERS),
            "IDENTITY": _hits(norm, IDENTITY_MARKERS),
            "CURIOSITY": _hits(norm, CURIOSITY_MARKERS),
        }

        # LOSS только при 2+ сигналах: маркер потери + предупреждение о будущем
        # («однажды» / многоточие). Простое прошедшее с местоимением — это
        # STATEMENT по эталону ТЗ: «МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ.» ≠ LOSS.
        loss_strong = bool(hits["LOSS"]) and (
            "однажды" in norm or text.rstrip().endswith("...")
        )
        if not loss_strong:
            hits["LOSS"] = []

        priority = ["CONTRADICTION", "REVELATION", "QUESTION",
                    "LOSS", "IDENTITY", "CURIOSITY"]
        for htype in priority:
            if hits[htype]:
                conf = min(0.95, 0.55 + 0.15 * len(hits[htype]))
                return HookClassification(text, htype, conf,
                                          attention_score, hits)

        return HookClassification(text, "STATEMENT",
                                  0.6 if attention_score >= 56 else 0.4,
                                  attention_score, {"statement": words[:5]})

    def classify_transcript(self, segments: list[dict],
                            scorer=None) -> list[HookClassification]:
        """Прогоняет транскрипт [{text,start,end}] через scoring+classification."""
        from .scoring import AttentionScorer  # локальный импорт — без цикла
        scorer = scorer or AttentionScorer()
        scored = scorer.score_transcript(segments)
        out = []
        for res in scored:
            is_hookish = res.total >= 76 or res.band == "hook"
            cls = self.classify(res.text, attention_score=res.total)
            if not is_hookish and cls.hook_type not in ("QUESTION",):
                cls.confidence *= 0.5     # слабая фраза — не уверенный хук
            out.append(cls)
        return out


if __name__ == "__main__":
    clf = HookClassifier()
    spec_examples = [
        ("ПОЧЕМУ МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ?", "QUESTION"),
        ("СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕР.".replace("ЕР", "Е"), "CONTRADICTION"),
        ("МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ.", "STATEMENT"),
        ("ИМЕННО ЗДЕСЬ НАЧИНАЕТСЯ ПРОБЛЕМА.", "CURIOSITY"),
        ("ЕСЛИ ТЕБЕ УЖЕ ЗА 30...", "IDENTITY"),
        ("ОДНАЖДЫ ТЫ ЗАМЕТИШЬ...", "LOSS"),
        ("И ВОТ ЧТО МЫ НЕ ЗАМЕЧАЕМ.", "REVELATION"),
        ("", "VISUAL_HOOK"),
        ("Сегодня мы поговорим о погоде.", "STATEMENT"),   # контроль
    ]
    ok = 0
    print(f"{'ФРАЗА':<52} {'ТИП':<14} {'CONF':>5}  ожидание")
    print("-" * 100)
    for text, expected in spec_examples:
        c = clf.classify(text)
        passed = c.hook_type == expected
        ok += passed
        print(f"{text or '<пусто>':<52} {c.hook_type:<14} {c.confidence:>5.2f}  "
              f"{expected:<14} {'✓' if passed else '✗'}")
    print(f"\nПройдено: {ok}/{len(spec_examples)}")
