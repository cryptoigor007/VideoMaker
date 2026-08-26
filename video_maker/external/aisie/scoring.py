# AISIE Phase 1.1 — Attention Score Engine (0-100)
#
# Спецификация (раздел 5 ТЗ):
#   Факторы: novelty, curiosity, contradiction, emotional_intensity,
#            personal_relevance, information_gap, prediction_error,
#            rhetorical_importance
#   Шкала:
#     0-30   обычная информация      → band "normal"
#     31-55  интересная информация   → band "interesting"
#     56-75  важная мысль            → band "important"
#     76-90  сильный акцент          → band "accent"
#     91-100 HOOK / CLIMAX/PUNCHLINE → band "hook"
#
# Модель: content_score = OR-комбинация лингвистических факторов
#         (один сильный фактор тянет балл вверх, слабые не разбавляют).
#         Позиция в видео (начало/конец/короткость) = буст-множитель ≤ 1.35,
#         потому что позиция усиливает контент, но не создаёт его.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

# ---------------------------------------------------------------
# Словари признаков (русский язык, кириллица)
# ---------------------------------------------------------------

QUESTION_MARKERS = [
    "почему", "зачем", "что если", "правда ли", "кто ", "как так",
]

CONTRADICTION_MARKERS = [
    "но ", "однако", "хотя", "несмотря на", "вопреки", "а ведь",
    "на самом деле", "наоборот", "в то же время", "при этом",
    "стало больше", "не стали", "казалось бы",
]

CURIOSITY_MARKERS = [
    "именно здесь", "вот что", "и вот", "оказывается", "выяснилось",
    "секрет", "тайна", "никто не знает", "мало кто знает",
    "проблема начинается", "всё изменилось",
]

IDENTITY_MARKERS = [
    "если тебе", "если вы", "каждый из нас", "мы все", "у тебя",
    "у вас", "твой", "ваш", "наше поколение", "люди старше",
]

LOSS_MARKERS = [
    "однажды ты заметишь", "потеряли", "разучились", "перестали",
    "уже не", "больше нет", "ушло", "пропало", "утрачено",
]

REVELATION_MARKERS = [
    "правда в том", "реальность такова", "скрытая", "истина",
]

EMOTIONAL_STEMS = [
    "страх", "боль", "любов", "смерт", "одиночеств", "счастлив",
    "счасть", "свобод", "дружб", "предательств", "надежд",
    "ужас", "шок", "невозможн", "деньг",
]

PERSONAL_PRONOUNS = {"я", "ты", "мы", "нам", "нас", "тебя", "вас", "меня"}

NEGATION_PUNCH_RE = re.compile(r"\b(нет|никогда|никто)\b[^a-zа-я]*$")
PREDICTION_ERROR_PATTERNS = [
    r"\bне .{1,15}, а\b",
    r"\bвместо .{1,20}\b",
    r"\bдумал[и]?, что\b",
]


def _stem(word: str) -> str:
    """Грубая русская стемминговая обрезка окончаний."""
    for suf in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
                "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ый", "ий",
                "ам", "ям", "ах", "ях", "ть", "ла", "ло", "ли", "ешь",
                "ишь", "ет", "ит", "ут", "ют", "ат", "ят", "у", "ю",
                "а", "я", "ы", "и", "е", "о"):
        if len(word) > len(suf) + 2 and word.endswith(suf):
            return word[: -len(suf)]
    return word


@dataclass
class AttentionResult:
    """Результат оценки важности фразы."""
    text: str
    total: int                      # итоговый балл 0-100
    novelty: float = 0.0
    curiosity: float = 0.0
    contradiction: float = 0.0
    emotional_intensity: float = 0.0
    personal_relevance: float = 0.0
    information_gap: float = 0.0
    prediction_error: float = 0.0
    rhetorical_importance: float = 0.0
    band: str = "normal"
    matched_markers: list = field(default_factory=list)

    BANDS: ClassVar[list] = [
        (91, "hook"),
        (76, "accent"),
        (56, "important"),
        (31, "interesting"),
        (0, "normal"),
    ]

    def classify_band(self) -> str:
        for threshold, name in self.BANDS:
            if self.total >= threshold:
                return name
        return "normal"


class AttentionScorer:
    """Оценивает фразу по шкале внимания 0-100 (OR-комбинация факторов)."""

    def __init__(self, weights: dict | None = None):
        # Веса оставлены для совместимости API; в OR-модели не используются.
        self.weights = weights or {}

    # ---------------- утилиты ----------------

    def _norm(self, text: str) -> str:
        return text.lower().replace("ё", "е")

    def _hits(self, norm: str, markers: list) -> list[str]:
        """Поиск маркеров по ГРАНИЦАМ СЛОВ.
        Простое `in` давало ложные срабатывания ('но ' внутри 'именно')."""
        found = []
        for m in markers:
            mm = m.strip()
            if not mm:
                continue
            pattern = r"(?<![a-zа-я0-9])" + re.escape(mm) + r"(?![a-zа-я0-9])"
            if re.search(pattern, norm):
                found.append(mm)
        return found

    @staticmethod
    def _or(*probs: float) -> float:
        """Вероятностное ИЛИ: 1 - Π(1-pᵢ). Сильные факторы доминируют."""
        result = 0.0
        for p in probs:
            result = result + p - result * p
        return min(1.0, result)

    # ---------------- факторы ----------------

    def score_novelty(self, norm: str) -> tuple[float, list]:
        hits = (
            self._hits(norm, CURIOSITY_MARKERS)
            + self._hits(norm, REVELATION_MARKERS)
            + self._hits(norm, LOSS_MARKERS)          # потеря = новизна/острота
        )
        rare = len(re.findall(r"\b\w{12,}\b", norm)) * 0.08
        return min(1.0, 0.55 * len(set(hits)) + rare), list(set(hits))

    def score_curiosity(self, norm: str) -> tuple[float, list]:
        hits = self._hits(norm, QUESTION_MARKERS + CURIOSITY_MARKERS)
        base = 0.55 * len(set(hits))
        if "?" in norm:
            base += 0.65
        neg_punch = bool(NEGATION_PUNCH_RE.search(norm))
        if neg_punch:
            hits.append("negation-punch")
            base += 0.55
        return min(1.0, base), list(set(hits))

    def score_contradiction(self, norm: str) -> tuple[float, list]:
        hits = self._hits(norm, CONTRADICTION_MARKERS)
        # √-шкала: 1 хит=0.5, 2 хита=0.71, 3+=0.87 — насыщение без скачка до 1.0
        return min(1.0, 0.5 * (len(set(hits)) ** 0.5)), list(set(hits))

    def score_emotional_intensity(self, norm: str, stems: set) -> tuple[float, list]:
        hits = [s for s in EMOTIONAL_STEMS if any(st.startswith(s[:6]) for st in stems)]
        score = 0.38 * len(set(hits))
        score += 0.25 * norm.count("!")
        if "..." in norm:
            score += 0.12
        return min(1.0, score), sorted(set(hits))

    def score_personal_relevance(self, norm: str, words: list[str]) -> tuple[float, list]:
        pronouns = [w for w in words if w in PERSONAL_PRONOUNS]
        identity = self._hits(norm, IDENTITY_MARKERS)
        score = min(0.5, 0.17 * len(pronouns)) + 0.5 * len(identity)
        return min(1.0, score), pronouns + identity

    def score_information_gap(self, norm: str) -> tuple[float, list]:
        gaps = ["почему", "зачем", "что произошло", "как так вышло",
                "никто не", "скрыто", "неизвестно"]
        hits = self._hits(norm, gaps)
        return min(1.0, 0.5 * len(hits)), hits

    def score_prediction_error(self, norm: str) -> tuple[float, list]:
        hits = []
        for pat in PREDICTION_ERROR_PATTERNS:
            hits.extend(re.findall(pat, norm))
        hits += self._hits(norm, ["стало больше", "не стали"])
        return min(1.0, 0.55 * len(set(hits))), list(set(hits))

    def _context_bonus(self, is_first: bool, is_last: bool, n_words: int) -> float:
        bonus = 0.0
        if is_first:
            bonus += 0.25
        if is_last:
            bonus += 0.20
        if 2 <= n_words <= 7:
            bonus += 0.05
        return min(0.35, bonus)

    # ---------------- публичный API ----------------

    def score_phrase(
        self,
        text: str,
        start_time: float = 0.0,
        video_duration: float = 60.0,
        is_first_phrase: bool = False,
        is_last_phrase: bool = False,
    ) -> AttentionResult:
        norm = self._norm(text)
        words = re.findall(r"[a-zа-я0-9]+", norm)
        stems = {_stem(w) for w in words}

        novelty, h1 = self.score_novelty(norm)
        curiosity, h2 = self.score_curiosity(norm)
        contradiction, h3 = self.score_contradiction(norm)
        emotion, h4 = self.score_emotional_intensity(norm, stems)
        personal, h5 = self.score_personal_relevance(norm, words)
        info_gap, h6 = self.score_information_gap(norm)
        pred_err, h7 = self.score_prediction_error(norm)

        # Контентная часть: OR по всем лингвистическим факторам
        content = self._or(novelty, curiosity, contradiction, emotion,
                           personal * 0.6, info_gap, pred_err)

        # Контекст (позиция в видео) — множитель, не слагаемое
        n_words = len(words)
        ctx = self._context_bonus(is_first_phrase, is_last_phrase, n_words)
        total = 100.0 * content * (1.0 + ctx)

        # Band "hook" (91+) достижим только со структурным сигналом:
        # вопросительный знак / противоречие / negation-punch.
        # Иначе потолок — "accent" (сильный акцент ≤90).
        structural = (
            "?" in norm
            or len(h3) > 0                      # противоречия
            or any(h == "negation-punch" for h in h2)
        )
        if not structural:
            total = min(total, 88.0)

        result = AttentionResult(
            text=text,
            total=round(min(100.0, total)),
            novelty=novelty, curiosity=curiosity, contradiction=contradiction,
            emotional_intensity=emotion, personal_relevance=personal,
            information_gap=info_gap, prediction_error=pred_err,
            rhetorical_importance=ctx,
        )
        result.band = result.classify_band()
        result.matched_markers = list(dict.fromkeys(h1 + h2 + h3 + h4 + h5 + h6 + h7))
        return result

    def score_transcript(self, segments: list[dict]) -> list[AttentionResult]:
        duration = max((s.get("end", 0) for s in segments), default=60.0)
        results = []
        n = len(segments)
        for i, seg in enumerate(segments):
            results.append(
                self.score_phrase(
                    seg["text"],
                    start_time=float(seg.get("start", 0)),
                    video_duration=float(duration),
                    is_first_phrase=(i == 0),
                    is_last_phrase=(i == n - 1),
                )
            )
        return results


# ---------------------------------------------------------------
# Самотестирование
# ---------------------------------------------------------------

if __name__ == "__main__":
    scorer = AttentionScorer()
    # (фраза, is_first, is_last, допустимые band'ы)
    tests = [
        ("ПОЧЕМУ МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ?", True, False, {"hook"}),
        ("СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕ.", True, False, {"hook", "accent"}),
        ("МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ.", True, False, {"important", "accent"}),
        ("ИМЕННО ЗДЕСЬ НАЧИНАЕТСЯ ПРОБЛЕМА.", True, False, {"accent"}),
        ("ЕСЛИ ТЕБЕ УЖЕ ЗА 30...", True, False, {"interesting", "important"}),
        ("ОДНАЖДЫ ТЫ ЗАМЕТИШЬ...", False, False, {"interesting", "important"}),
        ("И ВОТ ЧТО МЫ НЕ ЗАМЕЧАЕМ.", False, False, {"interesting", "important", "accent"}),
        ("Мы живём в мире бесконечного выбора.", True, False, {"normal", "interesting"}),
        ("А СЧАСТЬЯ — НЕТ.", False, True, {"accent", "hook"}),
        ("Сегодня мы поговорим о погоде.", False, False, {"normal"}),  # контроль
    ]
    print(f"{'ФРАЗА':<52} {'SCORE':>5}  {'BAND':<11} допустимо")
    print("-" * 100)
    ok = 0
    for text, first, last, allowed in tests:
        r = scorer.score_phrase(text, video_duration=30,
                                is_first_phrase=first, is_last_phrase=last)
        passed = r.band in allowed
        ok += passed
        print(f"{text:<52} {r.total:>5}  {r.band:<11} {','.join(sorted(allowed)):<28} "
              f"{'✓' if passed else '✗'}")
    print(f"\nПройдено: {ok}/{len(tests)}")
