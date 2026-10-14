## r57-api.24 — CP39: вырезание повторов/лишних слов по тексту Gemini (2026-10-14)

- Новая галочка в настройках аудио: «Вырезать повторы и лишние слова (по тексту Gemini)» (по умолчанию выключена). Работает из уже имеющихся данных — никаких дополнительных запросов к Gemini.
- Движок: слова Whisper (с дефисной склейкой) сравниваются с corrected_text (итоговый текст Gemini): режутся только чистые «delete»-прогоны (повторы, оговорки, лишние слова); исправления распознавания («replace») и дефисные слова («кое что» ↔ «кое-что») не трогаются. Защита: прогоны длиннее 8 слов не режутся (вдруг Gemini потерял кусок текста — живое не режем).
- Вырезание — поверх подготовленной речи (паузы/темп), с микро-фейдами 15 мс на каждом стыке (без щелчков); таймкоды и субтитры пересчитываются, вырезанные слова выпадают из событий. Флаг входит в отпечаток checkpoint.
- Боевая проверка на серии 14: найдены и вырезаны 2 повтора («мы знакомимся» — повтор; «постражнее. Но есть вопрос» — оговорка перед «пострашнее»), −3.49с: 270.01с → 266.51с.
- Тесты: 6 движка (повтор/оговорка/дефис/исправление/защита/вырезка+пересчёт) + обвязка GeminiStage + галочка GUI. Регрессия: 303 passed, 23 subtests.

## r57-api.23 — CP38: прокрутка трекпада (Tk 9 TouchpadScroll) и «Правка» во всех окнах (2026-10-14)

- Прокрутка: Tk 9 на macOS шлёт двухпальцевый жест трекпада/Magic Mouse как <TouchpadScroll>, а не <MouseWheel> — приложение слушало только колесо, поэтому в панели настроек ничего не прокручивалось. Добавлен обработчик <TouchpadScroll> (Δx/Δy распаковываются из event.delta: старшие/младшие 16 бит, знаковые; прокрутка пиксельная — жест ~60 раз/с), маршрутизация — общая с колесом (виджет под курсором → координаты). Живая проверка: реальные жесты пользователя прокручивают панели во всех вкладках.
- «Правка» во всех окнах: на macOS фокус у каждого окна свой, root.focus_get() «не видел» поля диалогов — пункты меню (⌘C/⌘V/⌘X/⌘A/⌘Z) в окне «Настройки» не срабатывали. Последний сфокусированный виджет отслеживается через <FocusIn>; меню и ⌘W адресуют действия ему — из любого окна; ⌘W закрывает активный диалог, а не главное окно.
- Диагностика колеса — под флагом VIDEO_MAKER_SCROLL_DEBUG=1 (тихо по умолчанию).
- Тесты: 4 новых на TouchpadScroll, 2 на «Правку» из диалогов, 3 страховочных на нативные ⌘-привязки Tk 9. Регрессия: 295 passed, 23 subtests.

## r57-api.22 — CP37: ползунок скорости речи — «слова/мин» и «коэффициент» (2026-10-14)

- Настройки аудио: режим скорости («По словам/мин» / «По коэффициенту») и один ползунок — 100–200 слов/мин (шаг 1) или ×1.00–×1.50 (шаг 0.01); диапазон и значение переключаются вместе с режимом.
- Подсказка под ползунком показывает текущую цель и метрики прошлого прогона: количество слов, темп до/после (слов/мин) и коэффициент (из ~/video_maker/cache/speech_state.json).
- Движок: prepare_narration принимает tempo_coefficient (явный множитель, кламп 0.5–2.0); speed_params_from_settings строит параметры из настроек (wpm: цель=ползунок, полоса [target, target+30]); добавлена метрика speed_mode.
- Исправлен баг интеграции: поля скорости не передавались в PipelineContext — пайплайн молча брал дефолты (120 WPM), из-за чего ползунок «не работал». Добавлен регрессионный тест-ловушка (ctx-рекордер).
- Настройки скорости входят в отпечаток checkpoint — смена ползунка корректно пересобирает речь.
- Проверено сквозными прогонами: цель 130 → 129.9 слов/мин (tempo 1.1396); коэффициент ×1.15 → 131.1 слов/мин (tempo 1.15).
- Тесты: 4 движка + 3 GUI (включая ловушку ctx). Регрессия: 286 passed, 23 subtests.

## r57-api.21 — CP36: удаление пауз и выравнивание темпа: дефисные слова Whisper (2026-10-13)

- Корень: Whisper отдаёт дефисные слова двумя токенами («Что» + «-то», «когда» + «-либо,»), а проверка покрытия таймкодов сравнивала счётчики слов (74 ≠ 76) — и трим/удаление пауз молча отключались на всей серии («нет полного набора таймкодов всех слов»).
- Проверка покрытия теперь сравнивает нормализованные последовательности букв/цифр (нижний регистр, ё→е, без пунктуации/дефисов): полное покрытие распознаётся корректно, реальная неполнота по-прежнему fail-safe.
- Счёт слов для WPM склеивает дефисные хвосты («Что» + «-то» = одно слово, как count_words) — WPM считается по конвенции приложения и совпадает с независимым замером.
- Проверено сквозным прогоном 40-секундного теста: trim_method=word_timestamps, удалено 2 паузы (0.92с), tempo=1.0519, итог 37.01с = 120.0 WPM; независимая транскрибация готовой дорожки — 120.0 WPM.
- Тесты: 3 новых в tests/test_speech_rate.py (покрытие при дефисных токенах, пауза после дефисного слова, счёт слов в окне).
- Регрессия: 279 passed, 23 subtests.

## r57-api.20 — CP35: устойчивость Gemini-цепочки + запуск через API и изоляция тестов (2026-10-13)

- 503/500/таймауты и обрывы сети считаются перегрузкой МОДЕЛИ: 1 короткий retry (2с), затем сразу следующая модель. Ротация ключей на 503 убрана — все ключи получают один и тот же ответ «high demand», ротация только растягивала прогон на минуты (в реальном инциденте — 726с до результата).
- Сетевые таймауты (httpx ReadTimeout/ConnectError) больше не роняют весь прогон — обрабатываются как временный сбой наравне с 503.
- Каталог моделей: добавлены gemini-3.7-flash, gemini-3.8-flash, gemini-flash-latest; убраны давно отключённые 2.0/1.5; живучие lite-модели стоят первыми в fallback-цепочке (живая проверка: flash-latest → 3.6 → 3.5-lite дала результат за 30с вместо минут).
- Настройка по умолчанию переключена на gemini-flash-latest — авто-актуальный алиас стабильной flash (gemini-2.5-flash отключена для новых пользователей: 404 «no longer available to new users»).
- POST /api/run теперь синхронизирует app.settings из GUI-полей (как кнопка «Создать видео») ДО валидации: раньше API-запуск с частичными оверрайдами отклонялся («Не выбрана папка B-roll горизонтальный» и т.п.), хотя поля заполнены. Синхронизация — общая функция collect_settings_from_gui (App._start + API), терпима к урезанным app-дублям.
- Тесты: tests/test_fixes_gemini_resilience.py (5) + tests/test_fixes_api_settings_sync.py (3); tests/conftest.py изолирует SETTINGS_FILE в tmp — прогоны тестов больше не пишут pytest-пути в реальные ~/.video_maker_settings.json (исторический баг: output/broll_h/cover_h/last_browse_dir затирались).
- Регрессия: 276 passed, 23 subtests.

## r57-api.19 — CP34: отмена timeout-вызовов и штатное закрытие GUI (2026-10-12)

- TkBridge использует явную машину состояний pending/running/cancelled/done. Если sync-запрос истёк до старта callback, он отменяется и не сможет позже выполнить side effect при оживлении Tk.
- `stop()` больше не принимает новые задания, будит ожидающие HTTP-потоки исключением и отбрасывает callbacks, которые ещё не начали выполняться. Переход callback в running сериализован с остановкой моста.
- Закрытие GUI при работающем pipeline теперь запрашивает отмену и ждёт завершения worker/finally/checkpoint, прежде чем уничтожить Tk. После 120 секунд спрашивает, продолжать ли ожидание; выбор «Нет» оставляет окно открытым вместо принудительного завершения.
- Удалён `os._exit(0)` из обычного закрытия. Сохраняются настройки, выключаются prevent-sleep, очередь, HTTP-сервер и TkBridge.
- Тесты CP34: pending timeout callback не выполняется позднее; stop будит waiter; результат и исключение callback передаются корректно; GUI не уничтожает root до завершения worker.
- Регрессия: 219 passed, 1 display-only skip, 23 subtests; Xvfb 1024×768: 220 passed, 23 subtests.

## r57-api.18 — CP33: надёжность checkpoint и установка первого запуска (2026-10-12)

- Checkpoint sidecar-файлы транскрипции/анализа пишутся атомарно под уникальными именами; если запись sidecar или основного checkpoint завершается ошибкой, предыдущий checkpoint сохраняется, новая стадия не отмечается выполненной, временные артефакты очищаются.
- Для resume добавлены fingerprints источника, настроек транскрипции/анализа/рендера/финализации. Изменение исходного аудио, опции сокращения пауз, каталога/содержимого B-roll и фоновой музыки инвалидирует только зависимые стадии.
- Инвентаризация медиа кэшируется в контексте задания, а edge-sample hash помогает замечать замену содержимого при сохранённых size/mtime без полного чтения каждого B-roll файла.
- Установщик проверяет реальные системные `ffmpeg` и `ffprobe`, потому что часть аудио/ checkpoint-путей вызывает их из PATH; если инструментов нет — использует Homebrew, а при недоступном Homebrew завершает подготовку понятной ошибкой вместо полурабочего запуска.
- Проверка обычного окружения: 214 passed, 1 display-only skip, 23 subtests; Xvfb 1024×768: 215 passed, 23 subtests. Native Apple Silicon/macOS запуск не выполнялся в Linux-контейнере.

## r57-api.15 / CP30 — API lifecycle fixes (2026-10-12)

- Fixed disabled API still binding after runtime settings changes.
- Made API config rebind transactional with rollback to the previous listener on bind failure.
- Fixed `/api/run` and queue launch reporting success when `App._start()` silently declines due to path/B-roll prechecks.
- API configuration with valid JSON but a non-object root now fails closed instead of raising `TypeError`.
- Added `tests/test_cp30_api_lifecycle.py`; full suite: 190 passed, 1 display-only skip, 23 subtests. Details: `checkpoints/CP30_API_LIFECYCLE.md`.

---

# r57-api.14 — CP28: финальный аудит / очистка отключённой BGM-логики (2026-10-12)

- Удалены четыре недостижимых BGM-блока старой схемы из H/V/Shorts ветвей. Внешнее поведение не меняется: BGM уже обрабатывается в финализаторе отдельно для публикационных файлов.
- Добавлены AST-тесты для тайм-аутов subprocess и запрета возвращения отключённых BGM-блоков. Результат аудита: 32 subprocess-вызова с явным timeout, 0 `shell=True`, 0 отключённых BGM-блоков.
- 183 passed, 1 skip только в headless GUI-layout, 23 subtests; Xvfb layout, GUI 70/70, control panel 35/35, browser, speech/BGM smoke PASS.
- Подробности: `checkpoints/CP28_FINAL_AUDIT.md`.

---

# r57-api.13 — CP27: меньше повторных ffprobe при BGM-финализации (2026-10-12)

- `verify_bgm_output` теперь кэширует метаданные неизменённого исходника по realpath/mtime_ns/size/ffprobe binary.
- Для повторных платформ проверка исходного MP4 не порождает новый процесс; каждая новая конечная копия по-прежнему независимо проверяется через ffprobe.
- Повреждённые/изменённые файлы не reuse-ятся: изменение размера или времени обновления приводит к новому probe.
- Не добавлены видеоэнкоды, аудиомиксы или транскрипция. Регрессия: 180 passed, 1 skipped, 23 subtests; синтетические speech/BGM и GUI/browser smoke PASS.
- Подробности: `checkpoints/CP27_BGM_PROBE_CACHE.md`.

---

# r57-api.12 — CP26: H/V parity и отказ от лишнего audio-post вызова (2026-10-12)

- Добавлены тесты на одинаковый shared narration WAV для H/V masters и отсутствие повторного voice enhancement в финальных ветвях.
- Горизонтальная ветвь больше не заходит в legacy audio-post helper только по старому BGM-флагу: реальный BGM продолжает обрабатываться в `FinalizeStage`.
- Никаких дополнительных encode/transcribe/Gemini passes не добавлено; legacy fallback для прямых вызовов ветви сохранён.
- 178 passed, 1 skipped, 23 subtests; 12 targeted tests passed; speech/BGM/Chromium smoke PASS.
- Подробности — `checkpoints/CP26_MASTER_PARITY.md`.

---

# r57-api.11 — CP25: общая lossless-обработка голоса (2026-10-12)

- Перенесено необязательное улучшение голоса на общую narration-дорожку перед созданием мастеров. Выход — PCM S16LE WAV 48 kHz; обе ветви H/V переиспользуют одну дорожку.
- Убраны повторные extract → enhance → replace операции из обычного полного конвейера, когда общая дорожка уже обработана; сохранён fallback для прямого вызова ветви.
- В checkpoint сохраняется состояние галочки и факт применения фильтра; изменение настройки при resume сбрасывает зависимые стадии.
- Синтетический 8-секундный benchmark: 6→1 FFmpeg-вызовов, 4→1 ffprobe-вызовов, без дрейфа длительности. Общая длительность выпуска в этой среде не измерялась.
- Регрессия: 174 passed, 1 skipped, 23 subtests passed; GUI/API/audio/browser smoke PASS. Подробности — `checkpoints/CP25_VOICE_ENHANCEMENT_SINGLE_PASS.md`.

---

## r57-api.10 / CP24 — карта проходов генерации и производительности

- Составлена `checkpoints/CP24_RENDER_PASS_MAP.md` с реальным порядком Audio → Whisper → Gemini → masters → H/V finals → Shorts → platform finalization.
- Отдельно обозначено, какие FFmpeg-команды перекодируют видео, какие работают только с аудио, а какие используют `-c:v copy`.
- Зафиксирована подтверждённая лишняя работа: улучшение голоса независимо после H и V, по три процесса FFmpeg на каждую ветку. CP25 переносит фильтр на общую lossless narration дорожку.
- Стадии H/V не распараллеливаются: это намеренная защита от конкуренции за аппаратный энкодер и SSD; параллелизм без Mac-замера не включается.
- Проверки: compileall PASS; 163 passed, 1 skipped, 23 subtests; 3 теста карты проходов.

## r57-api.9 / CP23 — Browser UI

- Improved responsive layout, contrast, focus indication and mobile touch targets.
- Added explicit accessible labels/roles and kept embedded status element alive for JS.
- Kept all queue/run/cancel/log actions and did not modify render pipeline or engines.
- Validated in Chromium desktop/mobile viewports; 164 tests + 23 subtests pass under Xvfb.

## r57-api.8 / CP22 — визуальная раскладка desktop GUI (2026-10-12)

- Убрано жёсткое `960 x screen_height-60`: размер окна подбирается по текущему экрану и центрируется; minsize не превышает полезную область небольших экранов.
- Две длинные вкладки настроек теперь прокручиваются; в главной вкладке действия «Создать видео»/«Отмена» и прогресс закреплены.
- B-roll horizontal/vertical разнесены по отдельным строкам; на ширине меньше ~1020 px настройки перестраиваются в одну колонку. Поле серии стало компактным.
- Журнал и HTTP-пульт перемещены на отдельную вкладку, поэтому API-кнопка/галочка больше не исчезают за большим log widget на низких экранах.
- Окно настроек стало screen-aware; кнопка «Сохранить» закреплена в нижней строке. Prompt Text получает вертикальный scrollbar и меньшее исходное height, API keys/prompts теперь в тёмных контрастных полях.
- Улучшен контраст состояний TButton, Accent.TButton, TNotebook.Tab, TCheckbutton, TRadiobutton и TScrollbar.
- Добавлены `tests/test_gui_layout_cp22.py` и расширена `tests/verify_app_gui.py` проверками видимости реальных кнопок.

Проверки CP22: GUI layout **1/1** на default Xvfb и **1/1** на Xvfb 1024×768; live GUI/API panel **35/35**. Backend/regression/FFmpeg проверки запускаются отдельно перед упаковкой.

---

# Изменения VideoMaker

## r57-api.7 / CP20 — глубокий аудит путей, staging и кэшей (2026-10-12)

- Исправлена обработка опасных/пограничных имён серии (`.`, `..`, путь с разделителями); dot-suffix реального series name сохраняется, чтобы `Episode.v2` не конфликтовал с `Episode`.
- Перед созданием директорий соцсетей ID очищаются, дубли пропускаются; финализация отклоняет платформенные каталоги, являющиеся symlink, чтобы не записывать файлы за пределами папки серии.
- Удалено опасное рекурсивное удаление общих `_tmp` и `series/shorts`; очищается только зарезервированный staging текущей серии. Symlink-цепочки не обходятся. Включённый `keep_temp_files` теперь сохраняет локальные промежуточные файлы Shorts.
- Кэши транскрипции и ffprobe metadata публикуются атомарно. Кэшированный PNG и intro/outro MP4 проверяются перед повторным использованием; невалидный/частично сохранённый cache не считается HIT.
- Ключ Gemini cache теперь учитывает весь транскрипт; ошибка model discovery маскирует API-ключ даже если SDK/REST-исключение включает URL.
- Добавлено 12 целевых тестов: traversal, сохранение имён, удаление staging, защита symlink, дубли platform ID, изменение сегментов после 50-го, redaction API-ключа, атомарность metadata и восстановление/очистка PNG cache.

Проверки CP20: основной набор **161 passed, 23 subtests passed**; CP20 path/cache suite 12/12; API security 18/18; GUI settings 70/70; live GUI/API panel 33/33; compileall PASS; speech FFmpeg smoke (120 WPM, word timestamps) PASS; BGM FFmpeg smoke (H.264/AAC/full decode) PASS. См. `checkpoints/CP20_PATH_CACHE_SAFETY.md`.

---

# r57-api.6 — 2026-10-12 — CP19: API auth/logging/request-body hardening

- Основной токен не журналируется при старте. HTTP access-log редактирует `key`, `token`, `api_key`, `access_token`, `refresh_token`, `secret`, `password`, `signature`, `auth` и `credential`; webhook URL редактируются перед записью в лог.
- Встроенная webui удаляет bootstrap `?key=` из адресной строки через `history.replaceState`; дальнейшие запросы передают токен только через `X-Api-Key`. Headers имеют приоритет над legacy query key.
- В API добавлен лимит JSON-тела 2 MiB; некорректный/усечённый JSON, invalid Content-Length и не-объектный корень дают HTTP 400; превышение лимита — 413.
- `POST /api/config` проверяет строгие JSON boolean для `lan`/`enabled` до любых изменений, чтобы строка `"false"` не включала LAN и ошибка не частично меняла конфигурацию.
- `load_config()` fail-closed для повреждённых значений LAN/enable, некорректного порта и короткого/некорректного токена.
- Ответы добавляют `Referrer-Policy: no-referrer` и `X-Content-Type-Options: nosniff`.
- Проверки: **149 passed, 23 subtests passed**; CP19 security suite 18 passed; GUI settings 70/70; GUI/API panel 33/33; `compileall` PASS.

Остаток: нет TLS и rate limiting. Доступ из LAN не следует выставлять в публичный интернет; удалённый доступ — через VPN. Legacy query-token сохраняется для старого bootstrap-ссылочного сценария.

---

# r57-api.5 — 2026-10-12 — CP18: LUFS semantics and validation

- GUI label and hint now clarify that `target_lufs` applies to final BGM mixes only. Voice-only output intentionally avoids another full-media normalization pass to protect processing time.
- `Settings.validate()` rejects non-finite/non-numeric values and LUFS targets outside −36…−5.
- API settings application rejects invalid `target_lufs` without mutating the prior value; OpenAPI documents type, range and scope.
- Corrected the GUI/API mapping smoke fixture from the invalid `+1.5` to valid `−15.5` LUFS.
- CP18 validation: **131 passed, 23 subtests passed**; GUI settings map 70/70; actual GUI/API panel 33/33; compileall and synthetic speech/BGM checks PASS.

---

# r57-api.4 — 2026-10-12 — CP17: subprocess deadlines and atomic output

- Все статически обнаруженные вызовы `subprocess.run/check_call/check_output/call` внутри `video_maker` теперь имеют явный timeout.
- Новая политика `media_timeout_for_duration`: пропорциональный времени медиа лимит, минимум операции и верхняя граница 6 часов.
- Ключевые выходы аудиодвижка создаются во временном файле и атомарно заменяют destination только при успехе; timeout/ошибка удаляет неполный временный файл и сохраняет прежний destination.
- Исправлены таймауты ffprobe, фонового изображения, вертикального 4K-видеостека и быстрого trim.
- Проверки CP17: **126 passed, 7 subtests passed**; compileall PASS; synthetic speech PASS (120 WPM, timestamps retained); synthetic BGM PASS (full decode); AST subprocess timeout audit: 0 unbounded `subprocess.run/check_call/check_output/call` calls in `video_maker`.

Открытые P1 от CP16 по зависающим внешним процессам закрыты в коде и регрессии. Остальные пункты глубокого аудита продолжаются в CP20–CP21 после завершения CP19.

---

# VideoMaker — журнал изменений

# r57-api.3 — 2026-10-12 — Deep audit CP12–CP16

## Исправленные дефекты

1. **Повторные Shorts между площадками.** Сравнение BGM-версий теперь основано на SHA-256 содержимого, а не пути. Идентичная дорожка в разных папках больше не создаёт повторный дорогой микс. Кеш SHA-256 избегает повторного чтения одинакового медиафайла в рамках финализации.
2. **Ошибка после успешного dedup.** Статус `deduplicated` трактуется как ожидаемый успешный результат, а не как ошибка BGM.
3. **Несколько площадок и master 16:9/9:16.** При двух и более площадках staging-источник не теряется после перемещения в первую папку; master доставляется на каждую площадку.
4. **keep_temp_files.** При включении временные staging и промежуточные файлы теперь действительно сохраняются для диагностики; по умолчанию очистка по-прежнему выполняется.
5. **Пользовательские хештеги.** `required_hashtags` применяются к Shorts и их sidecar/metadata; дефолтные брендовые теги остаются резервом, когда настройка пуста.
6. **GUI-поток.** Рабочий поток использует снимок настроек/полей, захваченный в Tk main thread, вместо чтения Tk variables/text widgets из фонового потока.
7. **BGM настройки.** `add_bgm` больше не включает музыку YouTube побочно, когда пользователь включил BGM для другой площадки. Включённые, но неконфигурированные/пустые источники проверяются до длительного рендера.
8. **CLI.** Неизвестные параметры, неверный порт и ошибки старта больше не представляются как успешный запуск; `--help` формирует корректную справку.
9. **Инструкция установки.** Удалено неверное заявление macOS 10.13+ для MLX-пути; указаны Apple Silicon, нативный Python и macOS 14+ согласно официальной документации MLX.

## Проверки CP16

- Unit/regression suite: 118 passed, 2 subtests passed.
- `compileall`: PASS.
- Live API: 79/79; settings-to-GUI map: 70/70; GUI/API panel: 33/33.
- Synthetic speech FFmpeg and synthetic BGM FFmpeg: PASS; deadlock smoke: PASS.
- Final ZIP and embedded manifest: PASS.

Оставшиеся ограничения и открытые пункты перечислены в `AUDIT_DEEP_REVIEW.md`.

---

# Историческая часть журнала изменений (версии до r57-api.3)

Текущая сборка описана в первом разделе выше; этот блок сохранён как история.

---

# r57-api.2 (2026-10-12)

## Повторные Shorts, длинные паузы, целевой темп речи

- Дедупликация идентичных финальных Shorts до повторной записи в папки площадок;
  отчёт `shorts_dedup_report.json` фиксирует пропущенные версии.
- Промежуточные Shorts хранятся во временной папке, а не в каталоге готовой выдачи.
- Добавлен выключенный по умолчанию флаг `remove_long_pauses`: осторожно сокращает
  длинные паузы, сохраняет контекстные интервалы пунктуации и пересчитывает
  временные метки слов/субтитров.
- Общий дефолт аудиодвижка и главный pipeline используют нижнюю границу целевого
  темпа 120 слов/мин при верхней границе 150; речь внутри диапазона не ускоряется.
- Настройка связана между GUI, сохранением JSON и REST API/OpenAPI.
- На CP10: `compileall` PASS; `pytest`: 94 passed, 2 subtests passed.

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


## CP32 — first-run installation

- Fresh ZIP can be launched without manually creating `.venv` or running pip commands; `start.command` invokes `setup.command` if setup is missing/out of date.
- Installer tries compatible native Python candidates and restores the prior `.venv` if installation cannot complete.
- Runtime dependencies split from testing dependencies.
- Added read-only `doctor.command` and shell syntax/contract tests.
