# VideoMaker

Автоматическое создание видео-контента (YouTube, Shorts, Reels) из аудио + B-roll.

**Текущая сборка: r57-api.1** (2026-09-23) — см. [VERSION.md](VERSION.md) и [CHANGES.md](CHANGES.md).
Полная таблица версий каждого файла — в [VERSION.md](VERSION.md).

## Что умеет сейчас

- **HTTP-пульт (r57).** Сборку можно запускать и наблюдать без кликов по окну: из браузера,
  с телефона, из скриптов и из Orchestrator. Запуск, отмена, статус (стадия, прогресс,
  прошло, осталось, «что идёт сейчас»), живой лог, очередь серий, настройки, вебхуки.
  Пульт живёт внутри приложения — поднимается и умирает вместе с ним. Очередь строго
  последовательная: один рендер за раз. Как пользоваться — [docs/API.md](docs/API.md).
- **Режим без окна.** `--headless` (окно скрыто, пульт работает), `--api-port N`, `--no-api`.
- **Окно.** В статус-баре строка «▶ что идёт сейчас», плюс блок «Пульт» с адресом
  и галочкой «Доступ из локальной сети».
- **BGM по соцсетям (r56).** Своя фоновая музыка и своя папка вывода для каждой площадки
  (`bgm_*_enable` / `bgm_*_folder`).
- **Обложки Shorts (r55, r53).** Визуал обложки — тот же хук, что на первых кадрах ролика;
  цвет берётся от начала клипа, а не фиксированный.
- **Тексты для YouTube (r50).** `.txt` с описанием, футером плейлистов и хештегами.
- **Модели Gemini (r52, r54).** Живой список моделей кнопкой «Обновить с API», очистка
  управляющих символов в JSON и повтор при сбое разбора.

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
# Двойной клик: окно + пульт
./start.command

# Без окна: пульт и очередь работают в фоне
./start-headless.command

# Явный порт или совсем без пульта
./start.command --headless --api-port 8765
./start.command --no-api

# Из терминала:
source .venv/bin/activate
python -m video_maker.main
```

## Пульт

Токен и готовые ссылки показывает блок «Пульт» в окне и `GET /api/config`.

| Откуда | Адрес |
|---|---|
| С этого Mac | `http://127.0.0.1:8765/?key=ТОКЕН` |
| С телефона (та же Wi-Fi) | `http://<IP-Mac>:8765/?key=ТОКЕН` |

Пока доступ из сети выключен, порт слушает только `127.0.0.1` — снаружи он даже не открыт
(не «403», а «нет соединения»). Включается галочкой «Доступ из локальной сети» в окне или
`POST /api/config {"lan": true}`. Авторизация — токеном (`X-Api-Key`, `X-Webapp-Key`,
`X-N8N-Token` или `?key=`). Отдельный вид для встраивания в чужую панель: `GET /embed`.

## Требования
- macOS 10.13+ (Apple Silicon рекомендуется — MLX Whisper + VideoToolbox)
- Python 3.10+
- ffmpeg (`brew install ffmpeg`)
- mlx-whisper (`pip install mlx-whisper`)

## Структура проекта
```
VideoMaker/
├── start.command            # Запуск (пробрасывает аргументы в приложение)
├── start-headless.command   # Запуск без окна
├── video_maker/             # Исходный код (единственный рабочий пакет)
│   ├── main.py              # --headless / --api-port / --no-api
│   ├── api/                 # HTTP-пульт: server, control, queue, bridge, webui, openapi
│   ├── config/settings.py
│   ├── gui/app.py
│   ├── pipeline/            # stages, master, branches, shorts, finalize…
│   ├── engines/             # video, audio, subtitles, short_cover, analysis…
│   └── external/aisie/      # AISIE helpers
├── tests/                   # pytest + verify_api_live / verify_api_settings / verify_app_gui
├── docs/API.md              # как пользоваться пультом и встроить его в Orchestrator
├── requirements.txt
├── VERSION.md               # Контроль версий КАЖДОГО файла
├── CHANGES.md
└── .env.example
```

> Корневые папки `engines/`, `gui/`, `pipeline/` **удалены** — это были устаревшие дубликаты.
> Весь код только в `video_maker/`.

## Состояние и логи
| Файл | Что внутри |
|---|---|
| `~/video_maker/api.json` | порт, токен, доступ из сети, вебхук |
| `~/video_maker/queue.json` | очередь серий (переживает перезапуск) |
| `<папка вывода>/videomaker.log` | лог прогона |

## Тесты
```bash
source .venv/bin/activate
python -m pytest tests/ -q              # 40 passed
python tests/verify_api_live.py         # 74 проверки живого пульта
python tests/verify_api_settings.py     # 69 проверок карты настроек
python tests/verify_app_gui.py          # 33 проверки блока «Пульт» в окне
```

## Контроль версий
Полная таблица всех файлов с ревизиями и датами — **[VERSION.md](VERSION.md)**,
история по сборкам — **[CHANGES.md](CHANGES.md)**.
