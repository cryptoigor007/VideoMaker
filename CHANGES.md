# VideoMaker — полный журнал изменений

**Текущая сборка: r57-api.1** (2026-09-23)

Полная история без пробелов. Кратко по файлам — `VERSION.md`.

---


# r57-api (2026-09-23)

## Пульт: управление сборкой без окна

### Зачем
Запускать сборку и следить за ней с телефона, из браузера и из Orchestrator,
не трогая окно Tkinter.

### Что появилось
| Часть | Файл | Суть |
|---|---|---|
| HTTP-сервер | `video_maker/api/server.py` | 14 маршрутов, токен, CORS, формат Orchestrator |
| Мост в окно | `video_maker/api/bridge.py` | очередь вызовов → Tk-поток, буфер лога, снимок статуса |
| Управление | `video_maker/api/control.py` | настройки, валидация, старт, отмена, поиск выдачи |
| Очередь серий | `video_maker/api/queue.py` | последовательный пакетный прогон, переживает перезапуск |
| Веб-страница | `video_maker/api/webui.py` | статус, прогресс, очередь, логи; `/embed` для iframe |
| Описание API | `video_maker/api/openapi.py` | OpenAPI 3.0.3, 14 путей |
| Сборка | `video_maker/api/__init__.py` | старт пульта, headless-диалоги, вебхук |
| Точка входа | `video_maker/main.py` | `--headless`, `--api-port`, `--no-api` |
| Окно | `video_maker/gui/app.py` | строка «▶ что идёт сейчас», блок «Пульт» |
| Запуск | `start.command`, `start-headless.command` | аргументы и режим без окна |
| Документация | `docs/API.md` | как пользоваться и как встроить в Orchestrator |

### Как устроено
- Пульт работает **внутри** приложения: Tk-поток остаётся владельцем виджетов,
  HTTP-потоки только кладут вызовы в очередь (`TkBridge`), ответ отдаётся из
  главного потока. Никаких гонок с Tkinter.
- Статус читается из окна: `stage_label` (`[5/7] Final H→V`), `progress_var`,
  `time_label`, `_pipeline_t0` → стадия, номер шага, прогресс, прошло, осталось.
- Лог: `logging` → буфер в памяти → `/api/log`, SSE `/api/log/stream` и строка
  «что идёт сейчас» в статус-баре.
- Headless: `tkinter.messagebox` заменяется на запись в лог, окно скрывается,
  `mainloop` продолжает крутиться (он нужен виджетам и пульту).
- Безопасность: токен из `~/video_maker/api.json` (`600`), доступ из сети
  выключен по умолчанию, не-localhost без `lan` — `403`.

### Форматы для Orchestrator
- `GET /api/job` → `{job:{id,kind,title,total,done,percent,status,message,elapsed,eta}}`
  — как у `jobs.py`, виджет прогресса подойдёт без переделки.
- `POST /webhook/videomaker/render` → `{id,url,state}` — как у `N8nEngine`.
- Вебхук наружу: `{"event":"series_finished","status":…,"series":…,"result":{…}}`.

### Проверено на живом приложении
403 без токена и с чужим токеном; `status`/`job`/`log`/`config`/`results`/`settings`;
очередь (добавление, пауза, удаление, авто-старт, отказ проверки с причиной,
сохранение на диск); вебхук по завершении серии; SSE-поток логов; веб-страница
(синтаксис JS через `node --check`, все 7 кнопок имеют обработчик, внешних
ресурсов нет).

### Не менялось
Пайплайн, кодирование, качество (`h264 -b:v` 28/30 Мбит/с), раскладка выдачи.

---

# r57-api.1 (2026-09-23)

## Сплошная проверка пульта: 18 правок

После сборки r57-api пульт проверен целиком — все маршруты, все способы
авторизации, все ошибки, сеть и нагрузка. Найдено и исправлено 18 дефектов.

### Что было сломано

| # | Дефект | Последствие | Правка |
|---|---|---|---|
| 1 | `GET /api/health` отдавал `api.urls()` с токеном | утечка ключа любому в сети | отдаёт `port/lan/enabled` |
| 2 | сервер всегда слушал `0.0.0.0` | порт открыт наружу даже при `lan: false` | пока сети нет — только `127.0.0.1`; смена сети/порта перезапускает сервер |
| 3 | CORS не разрешал `X-Webapp-Key`, `X-N8N-Token` | браузерная панель Orchestrator блокировалась preflight | заголовки добавлены, `Max-Age: 600` |
| 4 | мусор в параметрах (`?tail=abc`, `?since=abc`) | ответ `500` | значение по умолчанию + предупреждение в лог |
| 5 | `POST /api/config`: `port: "abc"`, порт вне 1..65535, чужие ключи | `500` или молчаливое принятие | `400` с причиной; неудачный перезапуск сообщается честно |
| 6 | очередь: пустой `audio`, пустой `items` | `500` | `400`; пачка возвращает `added` и `rejected` |
| 7 | `SETTINGS_VAR_MAP` не покрывал 25 полей | правка настроек через API **молча терялась** (18 галочек IMO и 7 полей упаковки) | все 62 поля привязаны |
| 8 | `set_series` писала только в виджет | серия расходилась между окном и настройками | пишет в виджет, переменную и настройки |
| 9 | `queue._finish` при пустом `output` | успешная серия помечалась ошибкой | папка и серия берутся из настроек приложения |
| 10 | `queue._load` с битой записью | пульт падал при старте | записи нормализуются, мусор пропускается |
| 11 | `LogBuffer.since` возвращал общий счётчик | при всплеске логов клиент терял строки | возвращается номер последней отданной строки |
| 12 | страница: запрос без таймаута | зависший вызов навсегда блокировал кнопки | `AbortController` (25 с, запуск 180 с), понятные надписи, подсказка при отсутствии токена |
| 13 | OpenAPI: 14 путей, без `/api/job` и вебхуков | контракт неполный | 17 путей; `/webhook/…/{id}` описан как GET |
| 14 | мёртвый дубль `/api/run-log` | лишний маршрут в документации | удалён |
| 15 | очередь соединений 5, трейсбек на каждый обрыв связи | телефон «зависал» при всплеске, лог забивался мусором | очередь 128; обрыв клиента — тихая запись в отладку |
| 16 | окно обновляло подпись через `root.after` из чужого потока | Tkinter не потокобезопасен: подпись и адрес для телефона могли не появиться | обновление идёт через мост (в потоке Tk) |
| 17 | нажатие галочки сети никак не отзывалось | «нажал — ничего не происходит» | сразу пишет «Переключаю доступ из сети…»; при неудаче галочка возвращается в настоящее состояние |
| 18 | `http.server` при старте зовёт `socket.getfqdn()` (обратный DNS) | на части сетей старт сервера висит секундами и «замораживает» переключатель | свой `server_bind` без обратного DNS |

### Проверки (лежат в проекте)

| Инструмент | Что делает | Результат |
|---|---|---|
| `tests/verify_api_live.py` | 74 проверки живого пульта: маршруты, 5 способов авторизации, отказы, мусорные параметры, очередь, сеть (включение/выключение), 36 параллельных запросов, SSE | 74 из 74 |
| `tests/verify_api_settings.py` | 69 проверок карты «настройка → переменная окна» на настоящем окне | 69 из 69 |
| `tests/verify_app_gui.py` | 33 проверки **окна**: подпись пульта, галочка сети в обе стороны, кнопка «Открыть пульт», строка «▶», снимок статуса из чужого потока | 33 из 33 |
| проверка очереди вживую | завершение серии без явной папки, битый файл очереди, пачка с мусором | 7 из 7 |
| обрыв связи | 5 оборванных соединений подряд: трейсбеков в логе 0, пульт жив | 5 из 5 |

Проверено также: `GET /api/health` без токена не содержит ключа; `lan: false` —
снаружи нет соединения, `lan: true` — есть, чужие без токена получают `403`;
`/api/log/stream` переживает мусорный `since`; `pytest tests/` — 40 passed;
`ruff` по `video_maker/api/`, `main.py` и новым инструментам — чисто.

Оконные проверки идут на **настоящем `mainloop`** (как приложение), а не на
`root.update()`: иначе межпоточные вызовы Tk ведут себя иначе и проверка врёт.

### Документация
`docs/API.md`: раздел «Что можно менять через API» (62 поля и особые случаи),
поведение сети, таблица ошибок, раздел «Как это проверено».

---


# r55-cover-hook-parity (2026-09-16)

## Обложка Shorts = визуал on-screen хука

### Было (r53)
Отдельный `CoverHook`: белый цвет, MarginV ~28%, fs 52–72, без UPPER — выглядело как субтитры.

### Стало
`subtitles.build_short_hook_cover_ass` — те же правила, что `_build_hook_events` (vertical):
- UPPERCASE, max 7 слов
- `\an8\pos(cx, 12% height)`, fs≈102, bord=2, shad0, fad(80,150)
- стиль `HookMarker` + override-теги (один источник правды с видео)

### Цвет 1:1 с видео
Не фиксированный fuchsia. Формула как в `_build_hook_events`:

```text
color_index = (0 + int(clip.start * 10)) % 4
→ HOOK_MARKER_COLORS[color_index]
```

`pipeline/shorts.py` передаёт `clip_start=clip["start"]` в `render_short_cover`.

### Файлы
| Файл | Изменение |
|------|-----------|
| `video_maker/engines/subtitles.py` | `HOOK_MARKER_COLORS`, `build_short_hook_cover_ass` |
| `video_maker/engines/short_cover.py` | вызов общего билдера; `clip_start` / `color_index` |
| `video_maker/pipeline/shorts.py` | передача `clip_start` |
| `VERSION.md`, `CHANGES.md` | r55 |

### Не менялось
Long `cover_*`, burn karaoke/CTA, GUI «Фон Shorts», skip при ошибке cover.

---

# r54-json-sanitize (2026-09-16)

## Gemini JSON parse — control characters

### Симптом
```
RuntimeError: Gemini JSON parse error: Invalid control character at: line 62 column 97 (char 2944)
```
Пайплайн падал на этапе анализа. Retry JSON не срабатывал.

### Причины
1. Gemini иногда отдаёт сырые control-символы (`\x00-\x1f`) внутри строк → `json.loads` падает.
2. `_parse_analysis` оборачивал `JSONDecodeError` в `RuntimeError`, а catch проверял только `isinstance(..., JSONDecodeError)` → retry никогда не запускался.

### Исправление
- `_sanitize_json_control_chars()`: control chars (кроме `\t\n\r`) → пробел, повторный `json.loads`.
- Catch расширен: `"JSON parse error" in err_str` + до 2 retry со строгим промптом.
- После исчерпания retry → следующая модель в цепочке.
- Каталог моделей: убран несуществующий `gemini-3.6-flash-lite` → `gemini-3.5-flash-lite` + `gemini-3.1-flash-lite`.

### Файлы
`video_maker/engines/analysis.py`, VERSION.md, CHANGES.md

---

# r53-short-covers (2026-09-16)

## Обложки Shorts
- GUI: путь «Фон Shorts»
- `short_cover.render_short_cover` (ffmpeg+ASS)
- вызов из ShortsCutter после каждого short
- пустой hook / нет фона → skip, short не падает

# r52-live-models (2026-09-16)

## Живой список моделей Gemini из API

### Задача
По кнопке в настройках запрашивать у API актуальные модели (не хардкод одной).

### Реализация
- `list_available_gemini_models()` — SDK `client.models.list()` или REST `v1beta/models`
- Фильтр: generateContent + имя gemini/gemma
- GUI: «Обновить с API» рядом с combobox (фоновый поток)
- Без ключа / ошибка → локальный каталог AVAILABLE_GEMINI_MODELS
- Выбранная модель всегда первая в fallback-цепочке

### Файлы
`analysis.py`, `app.py`, VERSION.md, CHANGES.md

---
# r51-gui-packaging (2026-09-16)

## GUI + packaging opts

### Задача
Менять плейлисты, хештеги и доп. промпт Gemini из настроек (описания для copy-paste в YouTube), без правки кода.

### GUI (окно Настройки)
1. **Модели** — Gemini, API keys, Whisper, WhisperX.
2. **Описания YouTube**
   - URL «Весь сезон»
   - URL блока + название блока
   - обязательные хештеги
   - чекбокс: дописывать плейлисты в description полного видео
   - чекбокс: шаблон Shorts (5 блоков)
   - max длина short description
3. **Промпт** — доп. инструкции в конец встроенного промпта (не full override).

### Файлы
| Файл | Изменение |
|------|-----------|
| `video_maker/config/settings.py` | поля packaging + defaults |
| `video_maker/gui/app.py` | 3 вкладки; load/save merge; `_packaging_opts_from_settings` |
| `video_maker/pipeline/stages.py` | `packaging_opts` в PipelineContext → analyze |
| `video_maker/engines/analysis.py` | opts → footer, tags, prompt_extra, cache key |
| `video_maker/pipeline/finalize.py` | footer с opts |

### Багфиксы r51
- packaging_opts не доходил до `_normalize_analysis` (NameError) — исправлено
- `_save_settings` затирал packaging в JSON — merge + packaging всегда
- разбор API-ключей по строкам — исправлено
- кастомные required hashtags из GUI не дополняются брендовыми принудительно
- чекбокс шаблона Shorts влияет на промпт

### Не менялось
Субтитры, нарезка, encode, YouTube Upload API.

---

# r50-desc (2026-09-16)

## Тексты описаний для copy-paste

### Полное видео → `*_description.txt`
В конец (идемпотентно):

```
→ Весь сезон: https://www.youtube.com/playlist?list=PLOsH4yc61thU
→ Блок «Одиночество и отношения»: https://www.youtube.com/playlist?list=PLfOYg9HTzi0s

#хештеги…
```

Функции: `_ensure_playlist_footer`, сбор # из текста.

### Shorts
- Промпт: крючок → интрига → CTA комментарии → CTA действие → теги (~200 символов)
- Убран хвост «Полная серия…» из `shorts.py`
- `_format_short_description`; upload.txt без дубля #

### Файлы
`analysis.py`, `shorts.py`, `finalize.py`

### Кэш
ANALYSIS_SCHEMA_VERSION обновлён (v2/v3).

---

# r49-hyphen (2026-09-16)

## Дефисы ASR

### Проблема
«кто-то» / «по-моему» / «из-за» — пробел после дефиса или вторая часть на другой строке.

### Решение (`subtitles.py`)
1. `_merge_hyphenated_tokens` до группировки: кто+-+то | кто-+то | кто+-то
2. `_assemble_group_display` — страховка
3. `_join_ass_parts` — без пробела после дефиса в ASS
4. Тире-пауза «—» не склеивается как составное слово

### Тесты
test_merge_hyphen_*; test_join_ass_parts_*; test_hyphen_not_split_across_groups

---

# r48-isolate (2026-09-10)

## Strong не смешивается с ordinary

### Правила
- Runs ordinary | strong | ordinary
- Внутри run: max_words=2, max_chars=13
- Isolated strong → solo; short strong pair → вместе если влезают

### Код
`_group_by_strong_runs` в `_build_shorts_parity_window`

### Пример
это ВАЖНО сейчас → [это] · [ВАЖНО] · [сейчас]

---

# r47-density (2026-09-10)

## Плотность субтитров

- max_words=2, max_chars=13 (hard cap)
- «большой риск» вместе; «главный секрет» / «проблема серьёзная» — врозь
- max_words реально соблюдается в теле группировки

Файлы: `subtitles.py`, `tests/test_shorts_parity.py`

---

# r43.3-stable (2026-09-10)

- \q2 no-wrap
- deconflict karaoke
- phrase-break только .!?…
- пунктуация на экране сохранена

# r43.2-punct (2026-09-10)

- `_display_word` сохраняет знаки; pure-punct клеится к слову

# r43.1-place (2026-09-10)

- finalize: os.replace / copy при EXDEV

# r43-phrase / r43-idlefix / r43-clean (2026-09-09…10)

- phrase-break; GUI idle/close; Clean Pro default
- strong: Gemini strong_words L2/L3/L4 на parity
- cleanup мёртвого кода

---

# Как пользоваться

1. Номер сборки — шапка `VERSION.md`
2. Детали релиза — секция rN в этом файле
3. После обновления:

```bash
python -m pytest tests/ -q
head -25 VERSION.md
```
