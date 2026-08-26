# AISIE Phase 1.3 — Semantic Grouping & Timing Engine (разделы 12-15 ТЗ)
#
# Правила:
#   • Hook = 3-7 слов (предпочтительно 3-5), разбивка СЕМАНТИЧЕСКАЯ
#   • Никогда не показываем весь hook одновременно — группами
#   • Delay между группами: база 120 ms, диапазон 80-180,
#     драматические повороты («но», «однако») 150-220 ms
#   • Keyword delay: ключевое слово появляется ПОСЛЕДНИМ → anticipation

from __future__ import annotations

import re
from dataclasses import dataclass, field

BASE_DELAY_MS = 120          # рекомендованная отправная точка ТЗ
MIN_DELAY_MS, MAX_DELAY_MS = 80, 220

DRAMATIC_TURN_WORDS = {"но", "однако", "хотя", "а"}      # начало группы = драм. разворот
MAX_GROUP_WORDS = 4                                        # семантические группы 2-4 слова


@dataclass
class SemanticGroup:
    text: str
    start: float                       # секунды от начала hook'а
    end: float
    words: list[str] = field(default_factory=list)
    delay_ms: int = BASE_DELAY_MS      # задержка ПЕРЕД этой группой
    is_keyword_group: bool = False     # содержит ключевое слово финала


def _split_semantic(text: str, hook_mode: bool = True) -> list[list[str]]:
    """Семантическая разбивка: сначала по знакам препинания, затем ≤4 слова.
    Хук-режим: короткая фраза (3-5 слов) без знаков делится ПОПОЛАМ —
    эталон ТЗ: «МЫ РАЗУЧИЛИСЬ» → «ДРУЖИТЬ», а не всё сразу."""
    chunks: list[list[str]] = []
    for part in re.split(r"[,.;:!?\u2026]+", text.strip()):
        words = part.split()
        if not words:
            continue
        if hook_mode and len(chunks) == 0 and 3 <= len(words) <= 5:
            mid = 2 + (len(words) % 2 == 0 and len(words) >= 4)
            chunks.append(words[:mid])
            chunks.append(words[mid:])
            continue
        for i in range(0, len(words), MAX_GROUP_WORDS):
            group = words[i:i + MAX_GROUP_WORDS]
            if group:
                chunks.append(group)
    return chunks


def _pick_keyword(groups: list[list[str]]) -> int:
    """Индекс группы с ключевым словом — последняя содержательная группа."""
    if not groups:
        return -1
    return len(groups) - 1


class TimingEngine:
    """Строит таймлайн последовательного появления смысловых групп."""

    def build_timeline(
        self,
        text: str,
        start_time: float = 0.0,
        hook_type: str = "STATEMENT",
        speech_rate: float = 1.0,       # >1 быстрая речь → короче задержки
        word_timings: list[dict] | None = None,   # [{word,start,end}] из WhisperX
        hook_mode: bool = True,          # последовательное появление группами
    ) -> list[SemanticGroup]:
        groups_words = _split_semantic(text, hook_mode=hook_mode)
        if not groups_words:
            return []

        # Базовая задержка по типу хука (раздел 14)
        type_delay = {
            "CONTRADICTION": 150,
            "QUESTION": 140,
            "LOSS": 150,
            "REVELATION": 130,
        }.get(hook_type, BASE_DELAY_MS)

        keyword_idx = _pick_keyword(groups_words)

        timeline: list[SemanticGroup] = []
        cursor = float(start_time)

        for i, words in enumerate(groups_words):
            delay = type_delay

            # Драматический разворот: следующая группа начинается с «но» и т.п.
            if i > 0 and words and words[0].lower() in DRAMATIC_TURN_WORDS:
                delay = max(delay, 180)

            # Ключевое слово в конце → anticipation (раздел 15): 150-220
            if i == keyword_idx and len(groups_words) > 1:
                delay = max(delay, min(220, 170))

            # Быстрая речь сжимает задержки к минимуму
            delay = int(delay / max(0.5, min(2.0, speech_rate)))
            delay = max(MIN_DELAY_MS, min(MAX_DELAY_MS, delay))

            is_keyword_group = (i == keyword_idx and len(groups_words) > 1)

            # Keyword Delay (раздел 15): ключевое слово появляется ПОСЛЕДНИМ
            # — разбиваем последнюю группу на "до ключа" + "ключ"
            if is_keyword_group and len(words) >= 2:
                before = words[:-1]
                keyword = words[-1]

                # Группа "до ключа"
                duration_before = self._group_duration(before, cursor, word_timings)
                timeline.append(SemanticGroup(
                    text=" ".join(before),
                    start=round(cursor, 3),
                    end=round(cursor + duration_before, 3),
                    words=before,
                    delay_ms=delay,
                    is_keyword_group=False,
                ))
                cursor += duration_before + delay / 1000.0

                # Keyword Delay: пауза перед ключевым словом (150-220мс)
                kw_delay = min(220, max(150, delay))
                kw_duration = self._group_duration([keyword], cursor, word_timings)
                timeline.append(SemanticGroup(
                    text=keyword,
                    start=round(cursor + kw_delay / 1000.0, 3),
                    end=round(cursor + kw_delay / 1000.0 + kw_duration, 3),
                    words=[keyword],
                    delay_ms=kw_delay,
                    is_keyword_group=True,
                ))
                cursor += kw_delay / 1000.0 + kw_duration + delay / 1000.0
            else:
                duration = self._group_duration(words, cursor, word_timings)
                timeline.append(SemanticGroup(
                    text=" ".join(words),
                    start=round(cursor, 3),
                    end=round(cursor + duration, 3),
                    words=words,
                    delay_ms=delay,
                    is_keyword_group=is_keyword_group,
                ))
                cursor += duration + delay / 1000.0

        return timeline

    @staticmethod
    def _group_duration(
        words: list[str], cursor: float, word_timings: list[dict] | None
    ) -> float:
        """Длительность группы: реальные тайминги WhisperX, иначе синтетика."""
        if word_timings:
            first_word = words[0].lower().strip(".,!?…")
            for idx, w in enumerate(word_timings):
                if w["word"].lower().startswith(first_word[:4]):
                    # нашли старт группы в реальном транскрипте
                    take = word_timings[idx:idx + len(words)]
                    if take:
                        return max(0.25, take[-1]["end"] - take[0]["start"])
                    break
        # Синтетика: ~160 мс на слово + пауза внутри группы
        return round(max(0.35, 0.16 * len(words)), 3)


if __name__ == "__main__":
    eng = TimingEngine()

    print("=== Эталон ТЗ: «МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ» ===")
    for g in eng.build_timeline("МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ", 0.30):
        print(f"  [{g.start:>5.2f}-{g.end:>5.2f}] +{g.delay_ms:>3}ms  "
              f"{'KEY ' if g.is_keyword_group else '    '}{g.text}")

    print("\n=== CONTRADICTION с драматическим разворотом ===")
    t = ("СВОБОДЫ СТАЛО БОЛЬШЕ. НО МЫ НЕ СТАЛИ СЧАСТЛИВЕЕР.".replace("ЕР.", "Е."), )
    for g in eng.build_timeline(t[0], 0.30, hook_type="CONTRADICTION"):
        print(f"  [{g.start:>5.2f}-{g.end:>5.2f}] +{g.delay_ms:>3}ms  "
              f"{'KEY ' if g.is_keyword_group else '    '}{g.text}")

    print("\n=== Реальные тайминги WhisperX ===")
    wt = [
        {"word": "МЫ", "start": 0.30, "end": 0.42},
        {"word": "РАЗУЧИЛИСЬ", "start": 0.44, "end": 0.86},
        {"word": "ДРУЖИТЬ", "start": 0.90, "end": 1.24},
    ]
    for g in eng.build_timeline("МЫ РАЗУЧИЛИСЬ ДРУЖИТЬ", 0.30, word_timings=wt):
        print(f"  [{g.start:>5.2f}-{g.end:>5.2f}] +{g.delay_ms:>3}ms  {g.text}")
