# VideoMaker

Автоматическое создание видео-контента (YouTube, Shorts, Reels) из аудио + B-roll.

## Быстрый старт

### Запуск через .app (двойной клик в Finder)
```
VideoMaker.app
```

### Запуск через терминал
```bash
./run.sh
# или
./VideoMaker.command
```

## Требования
- macOS 10.13+
- Python 3.10+
- ffmpeg (brew install ffmpeg)
- WhisperX (pip install whisperx)

## Установка зависимостей
```bash
pip install -r requirements.txt
brew install ffmpeg
pip install whisperx
```

## Структура проекта
```
VideoMaker/
├── VideoMaker.app/          # macOS приложение (двойной клик)
├── VideoMaker.command       # Запуск через двойной клик в Finder
├── run.sh                   # Запуск через терминал
├── video_maker/             # Исходный код
├── tests/                   # Тесты
├── run.sh                   # Запуск через терминал
├── start.sh                 # Запуск (алиас)
├── requirements.txt         # Python зависимости
├── .env                     # Переменные окружения (API ключи)
├── .env.example             # Пример .env
└── requirements.txt
```

## Настройка API ключей
Скопируйте `.env.example` в `.env` и добавьте ваши API ключи:
```bash
cp .env.example .env
# Отредактируйте .env и добавьте GEMINI_API_KEY
```

## Логи
Логи сохраняются в `~/video_maker/videomeyker.log`

## Тесты
```bash
python -m pytest tests/ -v
```