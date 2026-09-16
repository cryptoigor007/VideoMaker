# VideoMaker — контроль версий

**Текущая сборка: r55-cover-hook-parity** (2026-09-16)

Единый номер сборки = максимальный **rN**. Подробности — `CHANGES.md`.

---

## Таблица актуальных файлов (r55)

| Файл | Версия / суть |
|------|----------------|
| `video_maker/engines/short_cover.py` | **r55** — визуал хука = on-screen; цвет от `clip.start` |
| `video_maker/engines/subtitles.py` | **r55** + r49 — `build_short_hook_cover_ass`, `HOOK_MARKER_COLORS` |
| `video_maker/pipeline/shorts.py` | **r55** — передаёт `clip_start` в cover (+ r50 desc) |
| `video_maker/engines/analysis.py` | r54 — sanitize control chars + JSON retry |
| `video_maker/gui/app.py` | r51 — вкладки Описания / Промпт |
| `video_maker/config/settings.py` | r51 — packaging fields |
| `video_maker/pipeline/stages.py` | r51 — packaging_opts в context |
| `video_maker/pipeline/finalize.py` | r50/r51 — footer в description.txt |
| `tests/test_shorts_parity.py` | r49 — density + isolation + hyphen |
| `start.command` | r43+ |

---

## История (новые → старые)

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
