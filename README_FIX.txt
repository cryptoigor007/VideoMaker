VideoMaker r27 (2026-09-04) — итоговый
======================================

Сборка: r27  |  VERSION.md  |  CHANGES.md

Что исправлено в r27
--------------------
1) Галочки Intro/Middle/Outro на вкладке «Основные»
   • Умная логика: файл есть → ON+active (можно OFF); нет/пусто → OFF+disabled
   • Первопричина «серой» галочки: os.access(R_OK) на macOS + ttk state
   • Файл: video_maker/gui/app.py

2) Старт не блокируется из‑за WhisperX
   • Транскрипция = MLX Whisper, WhisperX не используется
   • Старый путь в настройках больше не даёт ошибку validate
   • Файл: video_maker/config/settings.py
   • Можно очистить whisperx_path в Настройках (необязательно)

Установка
---------
  Распаковать поверх папки VideoMaker
  head -5 video_maker/gui/app.py          → 2026.09.04-r27
  head -8 video_maker/config/settings.py  → r27 / MLX Whisper

Проверка
--------
  A) IMO: выбрать реальный Intro → галочка на Основных активна
  B) Старт без WhisperX: не должно быть «WhisperX бинарник не найден»
