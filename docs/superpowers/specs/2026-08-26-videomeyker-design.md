# ВидеоМейкер — Design Specification

## Overview

Новое приложение для автоматического создания видео-контента:
горизонтальные (16:9), вертикальные (9:16) и Shorts — из аудио + B-roll.

**Цель:** Заменить монолит shorts_maker_full (10к строк) конструктором
из изолированных модулей с чистым пайплайном.

**Архитектура:** Классы-стадии пайплайна, Tkinter GUI, TDD.

**Tech Stack:** Python 3.14, Tkinter, ffmpeg, Whisper, Gemini API, ASS/libass.

## Продукты

| # | Продукт | Источник | Формат | Обработка |
|---|---------|----------|--------|-----------|
| 1 | master_16x9.mp4 | B-roll + аудио | 16:9 | Голый склей, БЕЗ обработки |
| 2 | master_9x16.mp4 | master_16x9 + фон | 9:16 | vstack, БЕЗ обработки |
| 3 | final_16x9.mp4 | master_16x9 | 16:9 | Хуки + субтитры + интро/аутро/мидл |
| 4 | final_9x16.mp4 | master_9x16 | 9:16 | Хуки + субтитры + интро/аутро/мидл |
| 5 | short_XXX.mp4 | master_9x16 | 9:16 | Обрезка + хуки + субтитры + интро/аутро/мидл |

## Ключевые правила

1. Промежуточные видео (master_16x9, master_9x16) — всегда чистые, без обработки
2. Финальные видео берутся из промежуточных, НЕ из исходников
3. Shorts берутся из промежуточного вертикального (master_9x16), НЕ из финального
4. Gemini — один вызов на шаге 1, пакет ANALYSIS переиспользуется всеми ветками
5. Чекбоксы отдельные на КАЖДОМ этапе (☐Intro ☐Middle ☐Outro ☐Хуки ☐Субтитры)
6. Конфиг: .env + Keychain

## Структура каталога

```
~/video_maker/
├── video_maker/
│   ├── main.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── stages.py        # AudioStage, TranscribeStage, GeminiStage
│   │   ├── master.py        # MasterBuilder (16:9 + 9:16)
│   │   ├── branches.py      # FinalHorizontal, FinalVertical
│   │   ├── shorts.py        # ShortsCutter
│   │   └── finalize.py      # Loudness, export, validation
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── transcription.py # Whisper
│   │   ├── analysis.py      # Gemini
│   │   ├── subtitles.py     # ASS
│   │   ├── audio.py         # 48kHz + BGM + loudnorm
│   │   └── video.py         # ffmpeg операции
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── settings_panel.py
│   │   └── progress.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py
│   └── external/
│       ├── aisie/           # Скопированный пакет
│       ├── gemini_analyzer.py
│       ├── utils.py
│       └── text_style_utils.py
├── tests/
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── run.sh
```

## Входные данные

- Аудио: один файл или папка (очередь)
- B-roll: две папки (горизонтальные + вертикальные)
- BGM: папка с файлами, ротация
- Intro/Middle/Outro: папка с готовыми видео
- Вертикальный фон: картинка/видео от пользователя
- Обложки: загрузка из файла
- Названия серий: ручной ввод в GUI

## GUI — Единое окно

Секции:
- Аудио, B-roll, Фон, BGM, Intro/Middle/Outro
- Обложки, Настройки, Прогресс
- Чекбоксы на каждом этапе (горизонталь/вертикаль/shorts)
- Прогресс-бар + обратный отсчёт

## Пайплайн — 6 стадий

### Шаг 1: Gemini ANALYSIS
- Вход: аудио + транскрибация
- Один вызов Gemini
- Выход: пакет ANALYSIS

### Шаг 2: MASTER (промежуточное горизонтальное)
- Вход: аудио + B-roll горизонтальный
- Голый склей, БЕЗ обработки
- Выход: master_16x9.mp4

### Шаг 3A: MASTER VERTICAL (промежуточное вертикальное)
- Вход: master_16x9 + фон
- vstack: Master сверху + фон снизу
- БЕЗ обработки
- Выход: master_9x16.mp4

### Шаг 3B: FINAL HORIZONTAL
- Вход: master_16x9 + ANALYSIS + чекбоксы
- Добавляем: интро/аутро/мидл, хуки, субтитры, сильные слова
- Выход: final_16x9.mp4

### Шаг 3C: FINAL VERTICAL
- Вход: master_9x16 + ANALYSIS + чекбоксы
- Добавляем: интро/аутро/мидл, хуки, субтитры, сильные слова
- Выход: final_9x16.mp4

### Шаг 4: SHORTS
- Вход: master_9x16 + ANALYSIS + чекбоксы
- Обрезка по таймингам Gemini
- Добавляем: интро/аутро/мидл, хуки, субтитры
- Gemini генерирует: название, описание, хештеги
- Выход: short_XXX.mp4 + metadata
