# VideoMaker

Автоматическое создание видео-контента (YouTube, Shorts, Reels) из аудио + B-roll.

**Сборка: r27-clean** (2026-09-04) — см. [VERSION.md](VERSION.md) и [CHANGES.md](CHANGES.md).

## Быстрый старт (macOS)

### 1. Зависимости системы
```bash
brew install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install mlx-whisper
```

### 2. API-ключ
```bash
cp .env.example .env
# Добавьте GEMINI_API_KEY (или используйте Keychain — см. GUI «Настройки»)
```

### 3. Запуск
```bash
# Двойной клик:
./start.command
# или
open VideoMaker.app

# Из терминала:
source .venv/bin/activate
python -m video_maker.main
```

## Требования
- macOS 10.13+ (Apple Silicon рекомендуется — MLX Whisper + VideoToolbox)
- Python 3.10+
- ffmpeg (`brew install ffmpeg`)
- mlx-whisper (`pip install mlx-whisper`)

## Структура проекта
```
VideoMaker/
├── VideoMaker.app/          # macOS-приложение (двойной клик)
├── start.command            # Запуск (относительные пути)
├── video_maker/             # Исходный код (единственный рабочий пакет)
│   ├── main.py
│   ├── config/settings.py
│   ├── gui/app.py
│   ├── pipeline/            # stages, master, branches, shorts, finalize…
│   ├── engines/             # video, audio, subtitles, transcription…
│   └── external/aisie/      # AISIE helpers
├── tests/
├── docs/
├── requirements.txt
├── VERSION.md               # Контроль версий КАЖДОГО файла
├── CHANGES.md
└── .env.example
```

> Корневые папки `engines/`, `gui/`, `pipeline/` **удалены** — это были устаревшие дубликаты. Весь код только в `video_maker/`.

## Логи
`~/video_maker/videomaker.log`

## Тесты
```bash
python -m pytest tests/ -v
```

## Контроль версий
Полная таблица всех файлов с ревизиями и датами git: **[VERSION.md](VERSION.md)**.
