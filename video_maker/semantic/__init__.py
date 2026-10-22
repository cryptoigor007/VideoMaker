"""Семантический слой VideoMaker: подбор B-roll по смыслу («псевдо-нейролинк»).

Состав:
- taxonomy — темы + словарь RU↔EN (порт/расширение из VideoMaker Next);
- matcher — скор «клип ↔ сцена», окна сцен, смысловой порядок пула;
- stock — поиск/скачивание стока (Pixabay; Pexels — при рабочем ключе);
- smart_broll — оркестратор: транскрипт → сцены → запросы → скачивание.
"""

from __future__ import annotations

__all__ = ["taxonomy", "matcher", "stock", "smart_broll", "perf_history"]
