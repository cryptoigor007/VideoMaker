VideoMaker r27-clean (2026-09-04) — итоговый дистрибутив
========================================================

Сборка: r27-clean  |  VERSION.md (все файлы)  |  CHANGES.md

Что в этом архиве
-----------------
• Только рабочий код в video_maker/
• Корневые engines/ gui/ pipeline/ УДАЛЕНЫ (были старыми копиями)
• Логи и cache/ не входят
• start.command и .app — относительные пути
• VERSION.md: версия или дата git для КАЖДОГО файла

Установка
---------
  1. Распаковать в любую папку
  2. cd VideoMaker
  3. python3 -m venv .venv && source .venv/bin/activate
  4. pip install -r requirements.txt && pip install mlx-whisper
  5. brew install ffmpeg   # если ещё нет
  6. cp .env.example .env  # + GEMINI_API_KEY
  7. ./start.command

Проверка версий
---------------
  head -5 video_maker/gui/app.py           → 2026.09.04-r27
  head -5 video_maker/config/settings.py   → 2026.09.04-r27
  head -5 video_maker/engines/video.py     → 2026.09.03-r25
  head -5 video_maker/pipeline/shorts.py   → 2026.09.03-r26
  ls engines gui pipeline 2>/dev/null      → пусто (удалены)
