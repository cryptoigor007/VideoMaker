# CP28 — final audit and dead-code cleanup

Removed four unreachable `if False` legacy BGM blocks from H/V/Shorts. Static AST audit: 32 subprocess run-style calls, 0 missing explicit timeouts, 0 `shell=True`; no high-confidence API-secret patterns matched. Full tests: 183 passed, 1 headless display-only skip, 23 subtests; GUI layout, settings/control panel, browser, and synthetic speech/BGM checks pass. Explicit unverified areas and speed-benchmark scope are recorded in `checkpoints/CP28_FINAL_AUDIT.md`.

---

# CP27 — BGM source metadata cache

A repeated source MP4 was previously re-probed by `verify_bgm_output` for each platform-specific mix. Added a bounded metadata cache keyed by real path, mtime_ns, file size, and ffprobe executable. The output of each mix is still probed and validated separately; modified files invalidate the cache. This reduces repeated metadata subprocesses only and does not remove required mixes or add processing passes. Full regression: 180 passed, 1 skipped, 23 subtests.

---

# CP26 — H/V shared narration parity

Added tests ensure both masters receive the same shared lossless enhanced narration and that FinalHorizontal/FinalVertical do not invoke the legacy post-render enhancer once the shared pass is complete. Also removed an obsolete BGM-only trigger for the old audio-post helper from FinalHorizontal; BGM still runs in FinalizeStage. Full regression: 178 passed, 1 skipped, 23 subtests. Synthetic 8-second voice-enhancement benchmark: 6→1 FFmpeg calls, 4→1 ffprobe calls, 0.000 s duration drift. This is subtask scope only, not a full-episode benchmark.

---

# CP25 — voice enhancement pass optimization

Voice enhancement is now performed once against the prepared narration before `MasterBuilder`; output is a 48 kHz PCM S16LE WAV shared by both horizontal and native-vertical master branches. Final branches preserve a fallback for direct API/integration calls but skip post-render extraction/enhance/replacement when `voice_enhance_applied` is true. Checkpoint metadata records requested and applied values; settings mismatch invalidates transcription and downstream work.

Measured synthetic 8-second sample: legacy path 6 FFmpeg + 4 ffprobe invocations; shared path 1 FFmpeg + 1 ffprobe; 0.000s duration drift. This is a micro-benchmark of the voice-enhancement stage, not a whole-episode runtime claim. Full regression 174 passed, 1 skipped, 23 subtests; GUI/API/browser and speech/BGM smoke passed. See `checkpoints/CP25_VOICE_ENHANCEMENT_SINGLE_PASS.md`.

---

# VideoMaker r57-api.3 — отчёт глубокого аудита

**Дата:** 2026-10-12  
**Финальный чекпоинт:** CP16  
**Статус:** основной набор автоматических проверок пройден; остаются явно описанные ограничения системной/реальной проверки.

## 1. Что проверено

- Целостность входных CP11 ZIP, перечень файлов и SHA-256 по встроенному манифесту.
- Точки входа CLI, коды завершения при ошибках, настройки порта и справка.
- GUI/worker boundary для настроек Tkinter.
- Генерация и финализация Shorts, дедупликация в рамках одной серии и между площадками, музыкальные варианты.
- Передача пользовательских хештегов в ролики и sidecar/metadata.
- Раскладка master 16:9 и 9:16 на несколько площадок.
- `keep_temp_files` и очистка staging.
- BGM-политика по площадкам, preflight пустых/невалидных источников, взаимодействие галочек.
- Опция сокращения длинных пауз, выключенная по умолчанию; пересчёт временной шкалы слова/субтитров; целевой темп 120 слов/мин.
- API с авторизацией/ошибочными запросами/сохранением настроек/OpenAPI, GUI bridge и очередь.
- Синтетические FFmpeg-пути речи/BGM, decode результата, компиляция Python, ZIP и манифест.
- Документация и требования установки.

## 2. Историческая проблема архивов

В ранее выданном комплекте `VideoMaker_FULL_R57_CP07_BASELINE.zip` и `VideoMaker_FULL_R57_CP10_CONTROLLED_SPEECH_SETTINGS.zip` фактический размер каждого файла был **0 байт**. Эти два файла не были рабочими ZIP и не могли восстановить проект, несмотря на прежнюю подпись «полный чекпоинт». Они не считаются валидными историческими снимками.

CP08, CP09, CP11 открывались как ZIP. В CP11 найдено 104 архивных записи и embedded manifest для 103 файлов проекта; размеры и SHA-256 всех перечисленных файлов совпали. Старые CP07 и CP10 нельзя восстановить побайтно из пустых архивов; внутри пакета они не представлены как валидные restore points. Для продолжения работы нужно использовать полный CP16-снимок. Это не утверждает, что были восстановлены точные исторические состояния CP07/CP10.

## 3. Исправлено в CP12–CP16

- SHA-256 содержимого аудио/видео при идентификации повторных финальных Shorts; SHA-256 кешируется по пути/размеру/mtime на время одной финализации.
- Дедупликация одинаковой комбинации исходный Short + выбранный музыкальный файл + параметры звукового микширования. Разные треки не сливаются.
- Успешный `deduplicated` больше не попадает в ветку ошибки BGM.
- Master-файлы не теряются для второй платформы, если временные файлы не сохраняются.
- При `keep_temp_files=True` staging не очищается вопреки настройке.
- Custom `required_hashtags` не перезаписываются всегда одним брендовым списком.
- Настройки worker захватываются в GUI-потоке; фоновые операции не обращаются напрямую к Tk переменным/текстовым виджетам.
- BGM YouTube больше не включается как побочный эффект общей настройки BGM другой соцсети.
- Проверка источников BGM запускается до затратного рендера.
- CLI отклоняет неверные/неизвестные аргументы и выдаёт ненулевой статус при ошибке запуска.
- README и версия обновлены до `r57-api.3`; требования MLX исправлены.

## 4. Фактические результаты проверок CP16

| Проверка | Результат |
|---|---:|
| `python -m pytest tests/ -q` | **118 passed, 2 subtests passed** |
| `python -m compileall -q video_maker tests` | PASS |
| Settings/GUI mapping | **70 successful, 0 failed** |
| Живое GUI/API окно | **33 successful, 0 failed** |
| Живой API (тестовое окружение) | **79 successful, 0 failed** |
| Проверка отсутствия deadlock в логировании | PASS |
| Synthetic speech FFmpeg | PASS; тестовая длительность/темп 120 WPM; тайминги слова сохранены |
| Synthetic BGM FFmpeg | PASS; H.264/AAC, полное аудиодекодирование PASS |
| Shell syntax (`bash -n` на start scripts) | PASS |
| Final archive, duplicate names, embedded manifest | PASS |

Эти числа относятся к файлам CP16 в этой среде и повторены перед финальной упаковкой. Внешние live API/GUI скрипты запускались в тестовом окружении с Xvfb; эти сценарии не являются реальным социальным публикационным тестом.

## 5. Нерешённые/не полностью проверяемые пункты

### P1 — FFmpeg subprocess deadlines непоследовательны

Основной resilient-энкодер имеет ограничение времени и fallback для VideoToolbox, но некоторые вспомогательные вызовы FFmpeg/ffprobe в `audio.py`, `video.py`, `branches.py` и `subtitles.py` всё ещё вызываются без явного `timeout`. В случае зависания соответствующей внешней программы этап потенциально может ждать слишком долго. Добавлять один короткий таймаут на все задачи было бы некорректно: длительность операций зависит от размера исходника и 4K-кодирования. Нужно выделить политику timeout по операциям (probe/короткий аудиофильтр/длинное видео), обработку `TimeoutExpired` и тесты очистки/повторного fallback в отдельный тяжёлый пункт.

### P1 — LUFS при voice-only

`target_lufs` гарантированно попадает в loudnorm-цепочку BGM mix; когда музыка полностью отключена, финальная выдача не запускает дополнительный loudnorm pass по голосу. Это не обязательно приводит к испорченному аудио, но интерфейс «Громкость (LUFS)» может восприниматься как глобальная нормализация даже при voice-only. Автоматически применять двухпроходную нормализацию ко всем клипам увеличит число FFmpeg проходов и может замедлить серию, поэтому в рамках аудита не включено молча. Следующий шаг — решить продуктово: нормализовать voice-only по запросу отдельной галочкой или переименовать подпись в «Цель LUFS при BGM».

### P2 — Тестирование на целевой macOS/материале

В этой рабочей среде не запускалась полная MLX-транскрипция на Apple Silicon и не прогонялось пользовательское 8–10-минутное видео. Синтетические FFmpeg-тесты подтверждают технические свойства обработки, но не доказывают естественность темпа/интонации на реальном дикторе. Также тестовая среда не подтверждает успешную реальную публикацию в YouTube/TikTok и прочие сети.

### Ограниченная область дедупликации

Не объединяются два разных временных окна исходника только потому, что в них похожий текст. Система объединяет доказуемо идентичные media-варианты (source content + выбранный BGM/recipe), а не смысловые повторы фраз: semantic dedup может ошибочно удалять разные хорошие цитаты.

### BGM preflight

YouTube BGM по умолчанию включён. Если источник пуст или выбранная папка не содержит поддерживаемой музыки, старт будет остановлен до рендера; для voice-only нужно отключить BGM. Это сделано намеренно против потери времени, но при первом запуске требует внимательно проверить настройки.

## 6. Требования MLX и установка

README больше не утверждает macOS 10.13+ для MLX пути. Официальная документация MLX указывает Apple Silicon, native Python >=3.10, macOS >=14.0: <https://ml-explore.github.io/mlx/build/html/install.html>. На другой платформе общая GUI/API логика может быть доступна, но MLX-путь транскрипции не считается проверенным/поддерживаемым.

## 7. Возобновление

1. Распаковать полный `VideoMaker_FULL_R57_CP16_FINAL_SOURCE.zip` в отдельный каталог.
2. Прочитать `checkpoints/INDEX.md` и `checkpoints/ROADMAP_STATUS.json`.
3. Сверить SHA-256 файлов с `checkpoints/CP11_SOURCE_MANIFEST.json`.
4. Запустить `python -m compileall -q video_maker tests`, `python -m pytest tests/ -q` и synthetic FFmpeg smoke-тесты.
5. Перед production прогоном на Mac сделать короткий тест из реального голоса и одного выпуска, сначала с `remove_long_pauses=False`, затем включить настройку только для A/B сравнения.

**Вывод:** CP16 — лучший доступный для восстановления и проверенный полный исходный снимок. Это не гарантия отсутствия ошибок на любых реальных медиафайлах; два пункта P1 и нативный macOS/реальный медиапрогон явно вынесены в дальнейшую работу.


## Follow-up после CP16: CP17–CP18 (2026-10-12)

- **CP17**: внешние subprocess-вызовы получили явные конечные тайм-ауты; ключевые аудиовыходы создаются атомарно. Полный набор CP17: 126 passed, 7 subtests; synthetic speech/BGM PASS.
- **CP18**: область `target_lufs` явно документирована как цель финального BGM-микса; voice-only остаётся без дополнительного loudnorm-pass для сохранения скорости. Значения проверяются в диапазоне −36…−5 LUFS; нечисловые/NaN/Infinity/вне диапазона отклоняются. Набор: 131 passed, 23 subtests; GUI settings 70/70; GUI/API panel 33/33; synthetic speech/BGM PASS.
- Открытые фазы глубокого аудита после CP18: CP19 — токены/API; CP20 — пути/кэш/временные файлы/перезапись; CP21 — независимый полный прогон и сборка архива.


## CP19 — безопасность API (2026-10-12)

- HTTP access-логи редактируют secret query parameters; стартовые логи больше не печатают ссылку с токеном, webhook URL redacted before logging.
- WebUI получает `?key=` только для совместимости первичной ссылки, удаляет `key` из адресной строки и делает следующие запросы с `X-Api-Key`. Header token priority over query fallback.
- POST body capped at 2 MiB and strict JSON object parsing returns 400/413 instead of silently treating invalid bodies as `{}`.
- LAN/enable configuration is strict boolean; malformed on-disk values fail closed (`lan=False`, `enabled=False`), invalid port resets to 8765 and invalid/short token rotates to a generated secret. Config mutations validate types before changing any value.
- Added response `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`.
- Tests: 149 passed + 23 subtests; 18 CP19 security tests (including real loopback HTTP server), settings GUI mapping 70/70, GUI/API panel 33/33; compileall PASS. CP19 ZIP post-extraction verification: 185 ZIP entries, 159/159 source manifest entries matched, 0 duplicate paths, no `.env`; extracted-tree compileall and `149 passed, 23 subtests passed` verified. See `checkpoints/CP19_FINAL_REGRESSION_AND_PACKAGE.md`.

### Not closed by CP19

HTTP has no transport encryption and there is no rate limiting. API access from a LAN should only be enabled on a trusted network; remote access should use VPN. Legacy query token remains supported for the bootstrap URL, though it is redacted from logs and removed from the address bar after the UI loads. CP20 checks output paths/temp/collision behavior; CP21 provides the final independent recovery-package verification.



## CP20 — пути вывода, очистка временных файлов и целостность cache (2026-10-12)

### Риски, найденные и исправленные

1. Имя серии `.` или `..` могло проходить как path component, а series name с суффиксом вроде `Episode.v2` терял часть имени как расширение. Теперь опасные dot-компоненты превращаются в `output`; extension stripping применяется лишь к fallback имени аудиофайла.
2. Cleanup финализации рекурсивно удалял сразу `root/_tmp`, `output_folder/_tmp` и legacy `root/shorts`. Это могло удалить master-файлы, материалы другого процесса или пользовательские файлы. Теперь рекурсивно удаляется только `root/_tmp/shorts_staging`, общий `_tmp` удаляется только если пуст, legacy `shorts/` не удаляется; cleanup отказывается проходить через symlink.
3. Каталоги площадок создаются с санитизацией ID и проверкой дублей. Уже существующий symlink типа `series/youtube -> /somewhere` теперь приводит к ошибке, а не к записи вне каталога серии.
4. Флаг `keep_temp_files=True` раньше всё равно удалял `vm_short_*` промежуточные файлы. Теперь они сохраняются для диагностики; обычный режим удаляет только уникальный temp-dir конкретного Shorts.
5. Persistent ffprobe metadata и Whisper JSON-cache записывались напрямую и могли стать обрезанными при аварии. Теперь файлы сохраняются во временный путь, flush/fsync, затем `os.replace`.
6. PNG background cache доверял любому файлу размером >1 KB, а FFmpeg писал прямо в кэш-путь. Теперь валидируются PNG format/dimensions/data, рендер идёт в уникальный временный файл и только успешный PNG переносится атомарно; timeout/ошибка не оставляет partial cache. Intro/outro MP4 cache проверяется `ffprobe` перед reuse/store.
7. Gemini cache key учитывал лишь первые 50 сегментов. Теперь в fingerprint входит полный текст всех сегментов, поэтому различия в поздней части речи не могут дать тот же cache key. Ошибки Gemini SDK/REST также маскируют raw и URL-encoded API key.

### Проверка CP20

- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **161 passed, 23 subtests passed**.
- `tests/test_path_cache_safety_cp20.py`: **12 passed** — traversal, сохранение dot suffix, безопасная очистка, keep-temp, symlink, дубли platform id, late transcript segment, key redaction, атомарность metadata, инвалидный/тайм-аутный PNG cache.
- CP19 API security: **18 passed**. GUI settings→window map: **70 passed, 0 failed**. Live GUI/API panel: **33 passed, 0 failed**.
- Synthetic speech FFmpeg: PASS, итоговый темп 120 WPM, word timestamps сохранены. Synthetic BGM FFmpeg: PASS, H.264/AAC, полное декодирование звука PASS. BGM запускался отдельно с внешним лимитом процесса после одной задержки в объединённом прогоне.
- Полный архив на CP20 будет сверяться после распаковки по SHA-256 манифесту; см. `checkpoints/CP20_SOURCE_MANIFEST.json` и `checkpoints/CP20_TEST_LOG.txt`.

### Остаточные риски/ограничения

- Повторный запуск той же серии намеренно перезаписывает те же конечные имена файлов; для этого поведения нужен backup, если старый render следует сохранить. Межпроцессный lock пока не добавлен: два отдельно запущенных экземпляра, направленных на одинаковый `series_dir`, всё ещё могут конфликтовать.
- Не выполнялся транскрипт длительной реальной речи через MLX на пользовательском Apple Silicon Mac в этой Linux среде. Синтетический audio smoke проверяет тайминги/кодеки, но не субъективную естественность тембра и интонации.
- API HTTP не шифруется и не имеет rate limiting — ограничения описаны в CP19.


## 5. CP22 — повторный визуальный аудит desktop GUI

### Найдено до исправления
- Главное окно фиксировало ширину 960 и высоту почти по всему экрану, а поля настройки двух форматов B-roll набивались в одну строку.
- Главная вкладка была одной длинной непрокручиваемой колонкой: части настроек оказывались вне viewport.
- Лог с `expand=True` занимал свободную высоту и выталкивал панель API/кнопку открытия пульта ниже видимого края на экранах 1024×768.
- Окно настроек с высотой 640 и `Text(height=20)` могло вытолкнуть «Сохранить» ниже области окна; поле prompt не показывало scrollbar, а текстовые поля ключей/промпта оставались белыми по умолчанию.

### Исправлено в CP22
- Screen-aware/centered geometry и minsize, ограниченный экраном.
- Scrollable settings body с прокруткой мышью и scrollbar; action/progress закреплены вне неё.
- Responsive 2-column / single-column layout с breakpoint 1020 px; B-roll поля разделены, labels увеличены, series Text имеет ограниченную высоту.
- Журнал и пульт перенесены на третью вкладку; controls закреплены снизу на её странице.
- Modal Settings теперь использует `grid` для Notebook + постоянной нижней строки Save; prompt Text получает собственную полосу прокрутки, а Text-вводы styled тёмными цветами.
- Улучшены disabled/active/focus цвета TTK контролов.

### Фактические проверки
- Скриншоты 1024×768 показывают видимые Create/Cancel/Progress, на вкладке журнала видны log/API/LAN-toggle/Open-panel, в Settings видима кнопка «Сохранить».
- `tests/test_gui_layout_cp22.py`: 1 passed при стандартном Xvfb и 1 passed при разрешении 1024×768.
- `tests/verify_app_gui.py`: **35 successful, 0 failed** с живым loopback API и настоящим `mainloop`.
- CP22 не меняет `video_maker/pipeline` или `video_maker/engines`; значит количество FFmpeg/MLX проходов и длительность обработки видео не увеличиваются от этих изменений интерфейса.

Остаток: проверить web-пульт на реальных viewport (CP23) и провести измеримый audit числа read/encode операций/проходов генерации без включения медленных дополнительных проходов (CP24).


## 5. CP22 — визуальный аудит desktop GUI (2026-10-12)

Исправлены масштабирование/центрирование главного окна, прокрутка длинных настройкок, ширина строк B-roll, видимость основных действий, размещение журнала и HTTP-пульта на выделенной вкладке, пропорции правого блока, адаптивное окно настроек и закреплённая кнопка «Сохранить». Поля ключей и промптов приведены к единому тёмному контрастному оформлению; тело промпта получило собственную полосу прокрутки.

Подтверждения: полная регрессия под Xvfb 1024×768 — 162 passed + 23 subtests; отдельная проверка раскладки — PASS; карта GUI-настроек 70/70; живой GUI/API-пульт 35/35; синтетический speech и BGM smoke — PASS. Снимки находятся в `checkpoints/CP22_*_1024x768.png`.

В CP22 не изменялись `video_maker/pipeline/` и `video_maker/engines/`; визуальный рефакторинг не добавляет проходов транскрипции, анализа, нормализации или видеоэнкодинга. Перерасход времени рендера от изменений CP22 не ожидается, но измерение полного реального ролика от начала до конца требует одинакового входного файла и настроек на целевой машине. Следующая фаза CP23 — browser UI, затем CP24 — явная карта проходов обработки и подтверждение минимального количества дорогих операций.


## 6. CP23 — browser control panel visual/accessibility audit

The web console previously used a narrow single-column layout on desktop, had weak focus indication/disabled-state contrast, and icon-only retry/remove actions were too small for touch. CP23 introduces a responsive desktop two-column grid (status+queue) and a single-column mobile layout, higher-contrast palette, visible `:focus-visible` outlines, 44×44 px queue actions, semantic input labels, an accessible progressbar, and reduced-motion support. The embed renderer now hides the heading without removing the `#state` node that its JavaScript updates. Existing start/cancel/queue/pause/clear/retry/remove/log functions remain.

Verification: full Xvfb suite 164 passed + 23 subtests; two static web UI tests pass; API security and web UI static suites 20/20; actual Chromium browser smoke passes at 1365×900 and 390×844 with no horizontal overflow, correct progress state and action buttons within the viewport. Screenshots: `checkpoints/CP23_WEBUI_DESKTOP_1365x900.png` and `CP23_WEBUI_MOBILE_390x844.png`.

No changes were made to `video_maker/pipeline/` or `video_maker/engines/` in CP23. The web UI redesign does not add media processing passes or alter encode/transcription/analysis behavior.
