# VideoMaker — контроль версий

**Текущая сборка: r57-api.24 (2026-10-14), CP39.** Галочка «Вырезать повторы и лишние слова»: сравнение расшифровки с итоговым текстом Gemini, вырезание оговорок/повторов с кроссфейдами (проверено на серии 14: −3.49с, 2 повтора). Регрессия: 303 passed, 23 subtests.

## r57-api.18 — CP33: checkpoint integrity and first-run dependency readiness (2026-10-12)

- Atomic persistence for checkpoint sidecars and the main checkpoint; previous checkpoint preserved after a forced write failure.
- Resume fingerprints cover source audio and the relevant transcription, analysis, rendering and finalization settings/inventories.
- First-run setup checks real system `ffmpeg` and `ffprobe` needed by audio/checkpoint helpers and installs the Homebrew formula if missing; Homebrew must exist in advance.
- Regression: 214 passed, 1 skipped headless, 23 subtests; Xvfb: 215 passed, 23 subtests.

## r57-api.15 — CP30: API lifecycle and honest start status (2026-10-12)

- Disabled API now never binds a socket; runtime config disable closes the active listener.
- Config rebind uses rollback: on a failed new bind, the prior settings and listener are restored when possible.
- `/api/run` and the persistent queue no longer mark an item as started if the GUI refuses it during its own preflight checks.
- Non-object/malformed API config roots fail closed.
- Regression: 190 passed, 1 display-only skip, 23 subtests; CP30 target 7/7.

## r57-api.14 — CP28: финальный аудит и чистка legacy BGM (2026-10-12)

- Удалены четыре недостижимых `if False` блока старого BGM-пути; единственная действующая финализация BGM — `FinalizeStage`.
- Добавлен статический регрессионный тест политики timeout, отсутствия `shell=True` (аудит текущего дерева: 32 bounded subprocess calls, 0 missing timeout, 0 shell=True).
- Финальный набор: 183 passed, 1 headless display-only skip, 23 subtests. GUI 70/70, control panel 35/35, Chromium desktop/mobile и speech/BGM smoke PASS.

## r57-api.13 — CP27: кэш метаданных BGM-источников (2026-10-12)

- Добавлен ограниченный cache для ffprobe metadata в валидации BGM: canonical path + mtime_ns + size + binary.
- Повторный исходный MP4 для других площадок не запускает ffprobe повторно; каждый новый mixed output всё равно проверяется отдельно. Замена/изменение исходника инвалидирует кеш.
- Новые тесты проверяют повторное использование source probe и инвалидацию.
- Регрессия: 180 passed, 1 skipped, 23 subtests; GUI 70/70, панель 35/35, Chromium и FFmpeg smoke PASS.

## r57-api.12 — CP26: H/V аудио-паритет и удаление лишнего вызова (2026-10-12)

- Добавлены integration-level проверки, что горизонтальный и нативный вертикальный master получают одну и ту же lossless narration.
- Подтверждено, что финальные H/V не запускают повторное улучшение голоса при `voice_enhance_applied=True`.
- Убран устаревший триггер вызова audio-post помощника только из-за legacy BGM-флага; BGM по-прежнему обрабатывается финализатором для площадок.
- Регрессия: 178 passed, 1 skipped, 23 subtests; targeted H/V sharing 12/12, синтетические speech/BGM и браузерный smoke PASS.

## r57-api.11 — CP25: общая lossless-обработка голоса (2026-10-12)

- Опциональное улучшение голоса выполняется один раз до сборки горизонтального/вертикального мастеров; общая дорожка PCM S16LE WAV 48 kHz.
- Финальные ветви не повторяют обработку, если shared narration уже подготовлена. Сохранён fallback для прямых вызовов.
- Checkpoint сохраняет запрошенное/применённое состояние; изменение галочки при возобновлении инвалидирует зависимые стадии.
- Синтетический 8-секундный micro-benchmark: 6→1 FFmpeg-вызовов, 4→1 ffprobe-вызовов, 0.000s дрейфа; это метрика только для улучшения голоса, не всей серии.
- Проверки: 174 passed, 1 skipped, 23 subtests; speech/BGM smoke, GUI 70/70, GUI/API 35/35, live API 72/72, Chromium desktop/mobile PASS.

## r57-api.10 — CP24: карта проходов обработки (2026-10-12)

- Зафиксирован фактический порядок этапов, число обязательных encode для wide/vertical/Shorts и отличие video encode от stream-copy remux при BGM.
- Подтверждено, что `ParallelFinals` выполняет ветви последовательно (намеренно, для предотвращения конкуренции за VideoToolbox/SSD).
- Найдена лишняя работа при `voice_enhance=True`: отдельная цепочка extract → enhance → replace для горизонтального и вертикального MP4 (6 запусков FFmpeg на выпуск). План устранения — CP25.
- Проверка CP24: compileall PASS; 163 passed, 1 skipped, 23 subtests; добавлено 3 архитектурных regression checks.

## r57-api.9 — CP23: адаптивный веб-пульт (2026-10-12)

- Двухколоночная компоновка состояния и очереди на широких экранах; одна колонка на мобильных без горизонтального переполнения.
- Контрастные состояния focus/hover/disabled, доступные названия полей и progressbar, `prefers-reduced-motion`.
- Кнопки повторить/удалить по 44×44 px; сохранены все существующие операции пульта.
- Исправлен `render(embed=True)`: заголовок визуально скрывается, но state-элемент остаётся в DOM для `renderStatus`/`toast`.
- Проверено Chromium при 1365×900 и 390×844; 0 горизонтального переполнения, 44×44 targets.
- `pytest` под Xvfb 1024×768: 164 passed + 23 subtests; browser smoke PASS; API security + webui static **20 passed**.
- Только `video_maker/api/webui.py`, веб-тесты и документация; `pipeline/` и `engines/` без изменений, дополнительных encode/transcribe/analysis passes нет.

## r57-api.8 — CP22: визуальная раскладка desktop GUI (2026-10-12)

- Главное окно теперь открывается пропорционально доступному экрану и центрируется; minsize тоже ограничен разрешением.
- Обе длинные страницы настроек используют прокручиваемый canvas с вертикальной полосой и маршрутизацией wheel по области под курсором.
- Поля B-roll размещены каждое на отдельной строке. На узких окнах две колонки складываются в одну, поле названия серии не растягивается во всю высоту.
- Кнопки создания/отмены и прогресс остаются в фиксированной нижней панели; журнал и HTTP-пульт вынесены на отдельную вкладку, чтобы не прятаться на дисплеях 1024×768.
- Окно настроек стало адаптивным; кнопка «Сохранить» закреплена отдельной grid-строкой. Prompt Text имеет scrollbar; API keys и prompt Text оформлены контрастной тёмной темой.
- Контраст ttk button/checkbox/radio/combobox, выбранных вкладок и scrollbar улучшен с явными состояниями active/disabled/focus.
- Проверено визуально скриншотами при 1024×768; primary action, API controls и «Сохранить» помещаются. UI integration test прошёл на default Xvfb и 1024×768; live GUI/API panel **35/35**.

### Дизайн-решение
Основные действия закреплены вне scroll area, длинные параметры прокручиваются. Часто используемые выходные настройки не скрыты в отдельном окне; только редко используемые IMO и monitor/log вынесены в собственные вкладки. Видеопроцессор/FFmpeg-конвейер CP22 не менялся, поэтому эта работа не добавляет проходов кодирования.

---

# VideoMaker — контроль версий

**Текущая сборка: r57-api.8 (2026-10-12)** — CP22: адаптивный desktop UI, прокручиваемые страницы настроек, отдельная вкладка журнала/пульта и видимая кнопка сохранения в modal settings. CP20 — безопасность путей/кэшей; CP19 — безопасность HTTP API; CP18 — диапазон и область LUFS; CP17 — deadlines процессов и атомарный вывод аудио. — CP20: безопасность путей вывода, безопасная очистка staging, атомарные метаданные/кэши и проверка сохранённых медиа перед cache HIT. CP19 — безопасность HTTP API; CP18 — диапазон и область LUFS; CP17 — deadlines процессов и атомарные аудиовыходы.

## r57-api.7 — CP20: path/cache/temp safety

- `_safe_name` блокирует компоненты `.`/`..`, нейтрализует управляющие символы и не отрезает суффиксы пользовательских имён серий (`Episode.v2`). Расширение снимается только с fallback-имени исходного аудиофайла.
- ID ветвей публикации санитизируются до использования в пути; дубли после санитаризации пропускаются. Создание output-папок отклоняет вложенные symlink-каталоги, чтобы существующая ссылка `series/youtube → /внешняя/папка` не направляла запись за пределы серии.
- Финализация удаляет рекурсивно только зарезервированный `series/_tmp/shorts_staging`. Общий `output/_tmp`, `series/shorts` и посторонние файлы не удаляются; `_tmp` удаляется только если пуст. Symlink в цепочке cleanup приводит к пропуску очистки.
- `keep_temp_files=True` сохраняет персональные промежуточные каталоги Shorts для диагностики; обычный режим очищает только уникальную временную папку соответствующего клипа.
- Кэш транскрипции и persistent ffprobe metadata записываются во временный файл с flush/fsync и `os.replace`; при ошибке публикации старый cache остаётся цел. Фоновый PNG cache проверяет формат/размер/целостность, записывает уникальный temp и публикуется атомарно. Кэш intro/outro MP4 проверяется через ffprobe перед использованием/сохранением.
- Ключ Gemini analysis-cache включает полный транскрипт, а не только первые 50 сегментов. Ошибки SDK/REST model discovery маскируют сырые и URL-кодированные варианты API-ключа.
- CP20 целевые тесты: **12 passed**; основной набор — **161 passed, 23 subtests passed**; API security 18/18, настройки GUI 70/70, live GUI/API panel 33/33; compileall PASS. Синтетические speech и BGM FFmpeg smoke прошли отдельно.

### Остаточные ограничения CP20
Повторный ручной запуск той же серии намеренно использует те же пути итоговых файлов и может заменять результаты; CP20 защищает от path traversal, symlink-подкаталогов и дублей platform ID, но пока не вводит межпроцессный lock от двух отдельных экземпляров, рендерящих один и тот же `series_dir`. HTTP остаётся без TLS/rate limiting (см. CP19). MLX/длинное видео на реальном пользовательском Mac не запускались в данной среде.

---


**Текущая сборка: r57-api.6 (2026-10-12)** — CP19: redaction токенов в access-логах, header-first auth, очистка токена из URL webui, ограничение/валидация JSON-тела и строгая валидация LAN-конфига. CP18 уточнил область LUFS, CP17 добавил deadlines процессов и атомарный вывод.

## r57-api.6 — CP19: безопасность API и ограничения запросов

- API-токен больше не печатается startup-логами; secret-поля query-строки маскируются в access-логах, webhook URL редактируются перед журналированием.
- WebUI принимает legacy `?key=` только для bootstrap, затем удаляет его из адресной строки через `history.replaceState` и использует `X-Api-Key`. Заголовочная авторизация имеет приоритет над query-параметром.
- JSON request body ограничен 2 MiB; некорректный UTF-8/JSON, неверный `Content-Length`, усечённое тело и не-объектный JSON получают HTTP 400; слишком большой Content-Length — 413.
- API config принимает `lan`/`enabled` только как JSON boolean. Некорректные persisted настройки переходят в безопасное состояние; некорректный порт сбрасывается к default, короткий/некорректный токен заменяется криптографически случайным.
- Ответы имеют `Referrer-Policy: no-referrer` и `X-Content-Type-Options: nosniff`.
- CP19: **149 passed, 23 subtests passed**; GUI settings 70/70; GUI/API panel 33/33; 18 тестов CP19, включая реальный loopback HTTP, auth, health, body limit и redaction. `compileall` PASS.

### Остаточные ограничения безопасности
HTTP остаётся незашифрованным и rate limiting не реализован. Доступ в LAN включайте только в доверенной сети; для удалённого использования используйте VPN. Query-token остаётся поддержанным legacy-механизмом начальной ссылки, но не используется новыми API-запросами.

---

## r57-api.5 — CP18: явная область действия LUFS и валидация

- В GUI теперь явно указано: `target_lufs` — цель финального микса с BGM; voice-only output не получает дополнительный loudnorm-проход, чтобы не замедлять пакетную обработку.
- Значение проверяется как конечное число в диапазоне −36…−5 LUFS; `NaN`, `Infinity`, нечисловые и выходящие за диапазон значения не принимаются в Settings/API.
- OpenAPI описывает `target_lufs`, допустимые границы и применение только для BGM-микса; документация синхронизирована.
- Исправлена живая карта API→GUI: старая тестовая величина `+1.5 LUFS` больше не подходит под валидный диапазон; теперь проверяется `−15.5`.
- Проверки: **131 passed, 23 subtests passed**; settings→GUI map 70/70; live GUI/API panel 33/33; compileall PASS.

---

## r57-api.4 — CP17: тайм-ауты процессов и атомарные аудиовыходы

- Добавлен `video_maker/engines/media_process.py`: конечная политика тайм-аутов по длительности медиа (не более 6 часов) и атомарный вывод FFmpeg во временный файл.
- Замена аудио, voice enhancement и loudnorm encode не повреждают существующий выходной файл при timeout/non-zero exit: временный файл удаляется, публикация делается через `os.replace` только после успешного завершения.
- ffprobe в модуле субтитров теперь имеет ограничение 30 с; построение фонового кадра — 120 с.
- Vstack и stream-copy trim используют duration-aware deadlines с fallback при timeout; извлечение аудио/копирование звуковых дорожек — адаптивный лимит, а не неизменные 180 с для любого материала.
- AppleScript для поднятия окна имеет лимит 15 с.
- Новый набор регрессии для timeout policy/atomic output; полный набор CP17: **126 passed, 7 subtests passed**; compileall и два синтетических FFmpeg smoke-теста прошли.

### Границы проверки
Лимиты ограничивают ожидание и не отменяют необходимости нативного прогона на macOS. Тайм-ауты рассчитаны как минимум 8× длительности с ограничением до 6 часов; предельный предел можно пересмотреть при реальной работе с часовыми видео.

---

## r57-api.3 — изменения глубокого аудита

| Область | Итог |
|---|---|
| `video_maker/pipeline/finalize.py` | SHA-256 содержимого для кросс-платформенной дедупликации одинаковых Shorts+BGM; корректная обработка статуса `deduplicated`; сохранение staging при `keep_temp_files`; копирование master-файлов на несколько площадок |
| `video_maker/pipeline/shorts.py` | Пользовательские `required_hashtags` больше не заменяются неизменяемым набором брендовых тегов; конфигурация проходит в sidecar/metadata |
| `video_maker/config/settings.py` | BGM YouTube не включается побочно агрегатным `add_bgm`; preflight проверяет источник включённой BGM до дорогого рендера |
| `video_maker/gui/app.py` | Worker использует снимок значений, полученный в GUI-потоке; не читает Tk `StringVar`/`Text` напрямую из worker |
| `video_maker/main.py` | `--help`, проверка `--api-port`, неизвестных параметров; ошибка старта больше не возвращает ложный код успеха |
| `tests/` | Новые регрессионные тесты дедупликации, повторных master-файлов, пользовательских хештегов, BGM preflight и CLI/потоковой границы |
| `README.md` | Исправлены требования MLX/macOS; обновлены команды и реальные цифры тестов; добавлено предупреждение о preflight BGM |

## Результаты тестов на CP16

- `pytest tests/ -q`: **118 passed, 2 subtests passed**.
- Python `compileall`: PASS.
- Live API: **79/79**, settings GUI map: **70/70**, Tk API panel: **33/33**.
- Synthetic FFmpeg speech and BGM: PASS; log deadlock test: PASS.
- ZIP structural integrity and all embedded manifest entries: PASS.

Тесты среды Linux/Xvfb не являются нативным прогоном macOS. Модель MLX и длинное пользовательское видео в этой среде не запускались.

---

## Историческая таблица файлов (до глубокого аудита CP16)

| Файл | Версия / суть |
|------|----------------|
| `video_maker/api/` (6 файлов) | **r57.2** — HTTP-пульт, включая `remove_long_pauses` |
| `video_maker/main.py` | **r57** — `--headless` / `--api-port` / `--no-api` |
| `video_maker/gui/app.py` | **r57.2** — чекбокс необязательного сокращения длинных пауз + блок «Пульт» |
| `start-headless.command` | **r57** — запуск без окна |
| `start.command` | **r57** — пробрасывает аргументы в приложение |
| `docs/API.md` | **r57.1** — как пользоваться, что менять, как встроить в Orchestrator |
| `tests/verify_api_live.py` | **r57.1** — 74 проверки живого пульта |
| `tests/verify_api_settings.py` | **r57.2** — проверка карты из 63 настроек + сценарии окна |
| `tests/verify_app_gui.py` | **r57.1** — 33 проверки блока «Пульт» в окне |
| `video_maker/pipeline/finalize.py` | **r57.2** — дедупликация одинаковых финальных Shorts между площадками |
| `video_maker/config/settings.py` | **r57.2** — настройка `remove_long_pauses` выключена по умолчанию |
| `video_maker/engines/short_cover.py` | **r55** — визуал хука = on-screen; цвет от `clip.start` |
| `video_maker/engines/subtitles.py` | **r55** + r49 — `build_short_hook_cover_ass`, `HOOK_MARKER_COLORS` |
| `video_maker/pipeline/shorts.py` | **r55** — передаёт `clip_start` в cover (+ r50 desc) |
| `video_maker/engines/analysis.py` | r54 — sanitize control chars + JSON retry |
| `video_maker/pipeline/stages.py` | r51 — packaging_opts в context |
| `video_maker/pipeline/finalize.py` | r50/r51 — footer в description.txt |
| `tests/test_shorts_parity.py` | r49 — density + isolation + hyphen |

---

## История (новые → старые)

### r57-api.2 — 2026-10-12

**Скорость речи, паузы и устранение дубликатов Shorts.**

- Одинаковые финальные Shorts с тем же видеоисточником и тем же аудиомиксом не
  рендерятся/копируются повторно в папки других площадок; создаётся отчёт
  `shorts_dedup_report.json`. Отличающиеся версии с разными музыкальными треками
  сохраняются отдельно.
- Добавлен чекбокс `Сокращать длинные паузы между словами`, по умолчанию выключен.
  При включении сокращаются только подходящие длинные паузы, а тайминги
  транскрипции и субтитров сдвигаются на новую временную шкалу.
- Целевой темп по умолчанию унифицирован на 120 слов/мин (нижняя граница);
  максимальная граница — 150 слов/мин. Речь внутри допустимого диапазона не
  ускоряется только ради попадания в середину диапазона.
- `POST /api/settings` и `GET /api/settings` поддерживают новое поле
  `remove_long_pauses`; оно сохраняется вместе с настройками приложения.
- Регрессионный набор: 94 passed, 2 subtests passed на момент подготовки CP10.

---

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


# CP32 — first-run bootstrap and no-manual-venv launcher (2026-10-12)

- `start.command` now prepares the `.venv` automatically on first run, then launches the application.
- Added `setup.command`, `doctor.command`, and runtime-only dependencies.
- Setup validates native Apple Silicon macOS 14+, compatible Python, MLX Whisper and the packaged FFmpeg executable.
- No API credentials, model weights or user media are included in the archive.
