# VideoMaker — полный журнал изменений

**Текущая сборка: r55-cover-hook-parity** (2026-09-16)

Полная история без пробелов. Кратко по файлам — `VERSION.md`.

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
