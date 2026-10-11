# VideoMaker — контроль версий

**Текущая сборка: r57-api.1** (2026-09-23)

Единый номер сборки = максимальный **rN**. Подробности — `CHANGES.md`.

---

## Таблица актуальных файлов (r57)

| Файл | Версия / суть |
|------|----------------|
| `video_maker/api/` (6 файлов) | **r57.1** — HTTP-пульт: статус, очередь, настройки, вебхуки |
| `video_maker/main.py` | **r57** — `--headless` / `--api-port` / `--no-api` |
| `video_maker/gui/app.py` | **r57** — строка «▶ что идёт сейчас» + блок «Пульт» (r56: BGM по соцсетям) |
| `start-headless.command` | **r57** — запуск без окна |
| `start.command` | **r57** — пробрасывает аргументы в приложение |
| `docs/API.md` | **r57.1** — как пользоваться, что менять, как встроить в Orchestrator |
| `tests/verify_api_live.py` | **r57.1** — 74 проверки живого пульта |
| `tests/verify_api_settings.py` | **r57.1** — 69 проверок карты настроек |
| `tests/verify_app_gui.py` | **r57.1** — 33 проверки блока «Пульт» в окне |
| `video_maker/pipeline/finalize.py` | **r56** — папки соцсетей + BGM mix |
| `video_maker/config/settings.py` | **r56** — bgm_*_enable / folder |
| `video_maker/engines/short_cover.py` | **r55** — визуал хука = on-screen; цвет от `clip.start` |
| `video_maker/engines/subtitles.py` | **r55** + r49 — `build_short_hook_cover_ass`, `HOOK_MARKER_COLORS` |
| `video_maker/pipeline/shorts.py` | **r55** — передаёт `clip_start` в cover (+ r50 desc) |
| `video_maker/engines/analysis.py` | r54 — sanitize control chars + JSON retry |
| `video_maker/pipeline/stages.py` | r51 — packaging_opts в context |
| `video_maker/pipeline/finalize.py` | r50/r51 — footer в description.txt |
| `tests/test_shorts_parity.py` | r49 — density + isolation + hyphen |

---

## История (новые → старые)

### r57-api.1 — 2026-09-23

**Сплошная проверка пульта, 18 правок.** Закрыта утечка токена через
`/api/health`; порт больше не открыт наружу, пока не включён доступ из сети;
CORS пускает `X-Webapp-Key`/`X-N8N-Token`; мусорные параметры и тела дают `400`,
а не `500`; **карта настроек дополнена 25 полями** — правка галочек IMO и полей
упаковки через API больше не теряется; завершение серии без явной папки вывода
считается успешным; битый файл очереди не роняет пульт; обрыв связи с телефоном больше не засоряет лог; OpenAPI — 17 путей.

Проверки: `tests/verify_api_live.py` (74 из 74), `tests/verify_api_settings.py`
(69 из 69), `tests/verify_app_gui.py` (33 из 33), `pytest tests/` — 40 passed.

Оконный путь отдельно: подпись пульта и кнопка «Открыть пульт» обновляются
через мост (из чужого потока Tk дёргать нельзя), нажатие галочки сети сразу
отзывается надписью, сервер стартует без обратного DNS.

Файлы: `video_maker/api/*`, `video_maker/gui/app.py`, `docs/API.md`,
`tests/verify_api_*.py`, `tests/verify_app_gui.py`.

### r57-api — 2026-09-23
HTTP-пульт внутри приложения: запуск, отмена, статус (стадия, прогресс, прошло,
осталось, «что идёт сейчас»), живой лог, очередь серий, настройки, вебхуки.
Режим без окна `--headless`; аргументы `--api-port`, `--no-api`.
Авторизация токеном (`X-Api-Key` / `X-Webapp-Key` / `X-N8N-Token` / `?key=`),
доступ из сети по умолчанию выключен. `/api/job` отдаёт форму Orchestrator,
`POST /webhook/videomaker/render` — форму n8n-движка. Окно: строка «▶ что идёт
сейчас» в статус-баре и блок «Пульт» с адресом и галочкой сети.
Файлы: `video_maker/api/*`, `main.py`, `gui/app.py`, `start*.command`, `docs/API.md`.

### r55-cover-hook-parity — 2026-09-16
Обложка Shorts = тот же визуал, что хук на первых кадрах ролика:
UPPER, pos y≈12%, fs/bord/shad как `_build_hook_events`.
Цвет: `color_index = int(clip.start * 10) % 4` (как в видео), не фиксированный fuchsia.
Файлы: `subtitles.py`, `short_cover.py`, `pipeline/shorts.py`.

### r54-json-sanitize — 2026-09-16
Gemini JSON: очистка control characters перед `json.loads`.
Фикс retry: RuntimeError из `_parse_analysis` теперь ловится.
До 2 повторных запросов + fallback на следующую модель.
Каталог моделей: `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` вместо несуществующего `3.6-lite`.

### r53-short-covers — 2026-09-16
Обложки Shorts: фон (картинка) + hook Gemini → short_XXX_cover.jpg (ffmpeg+ASS).
GUI: «Фон Shorts». Без чекбокса. Long cover_* не тронуты.

### r52-live-models — 2026-09-16
Кнопка «Обновить с API» в ⚙→Модели: живой список моделей Gemini (SDK/REST).
Выбранная — первая в цепочке, остальное fallback.

### r51-gui-packaging — 2026-09-16
GUI: вкладки Модели / Описания YouTube / Промпт.
Packaging opts → analyze/finalize.

### r50-desc — 2026-09-16
Тексты .txt для YouTube copy-paste (footer плейлистов + хештеги; шаблон Shorts).

### r49-hyphen — 2026-09-16
Склейка ASR-дефисов (кто-то, по-моему).

### r48-isolate — 2026-09-10
Strong и ordinary не в одной группе.

### r47-density — 2026-09-10
max_words=2, max_chars=13.

### r43.3-stable — 2026-09-10
\q2, deconflict, phrase-break только .!?…, пунктуация на экране.

### r43.2 / r43.1 / r43 — 2026-09-09…10
punct display; finalize move/copy; Clean Pro default; idle GUI; strong Gemini.

---

## Проверка

```bash
python -m pytest tests/ -q
head -25 VERSION.md
```
