VideoMaker r43-clean (2026-09-09)

Что сделано в этой сборке
-------------------------
• Default субтитров = Clean Pro (parity) для horizontal и vertical/shorts
• auto_aisie → parity (не hormozi)
• Удалены корневые engines/ gui/ pipeline/ (старые r12)
• Удалён мёртвый код: placement stub, GeminiAnalyzer, dead builders/helpers
• start.command — относительные пути
• Legacy-стили (hormozi и др.) доступны явным выбором в GUI

Установка
---------
  1. Распаковать в любую папку
  2. cd VideoMaker
  3. python3 -m venv .venv && source .venv/bin/activate
  4. pip install -r requirements.txt && pip install mlx-whisper
  5. brew install ffmpeg
  6. cp .env.example .env  # + GEMINI_API_KEY (если есть example)
  7. ./start.command

Проверка
--------
  head -3 video_maker/engines/subtitles.py   → 2026.09.09-r43-clean
  head -3 start.command                      → PROJECT_DIR="$(cd ...
  ls engines gui pipeline 2>/dev/null        → пусто (удалены)
