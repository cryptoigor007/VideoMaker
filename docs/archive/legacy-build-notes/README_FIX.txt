VideoMaker r43.1-place (2026-09-10)

Что в сборке
------------
• r43.1-place: финализация — move на одном диске, copy на разных / keep_temp
• r43-phrase: phrase-break субтитров
• r43-idlefix: idle + закрытие + logging
• 26/26 tests OK

Проверка
--------
  head -3 video_maker/pipeline/finalize.py  → r43.1-place
  head -3 video_maker/engines/subtitles.py → r43-phrase
  python -m pytest tests/ -q               → 26 passed
