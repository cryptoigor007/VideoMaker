# VideoMaker — r43.3-stable (2026-09-10)

## Стабильные субтитры
- phrase-break только `.!?…` (запятая не рвёт)
- `\q2` без переноса строки
- deconflict таймингов (нет двух karaoke-строк сразу)
- пунктуация на экране сохранена

---

# VideoMaker — r43.2-punct (2026-09-10)

## Пунктуация снова на экране

`_display_word` больше не срезает знаки; pure-punct клеится к слову; phrase-break сохранён.

---

# VideoMaker — r43.1-place (2026-09-10)

## r43.1-place — быстрая финализация (move vs copy)

1. **finalize.py**: `os.replace` если один раздел; `copy2` при EXDEV или `keep_temp_files`.
2. Лог: `→ move` / `→ copy`. Shorts already-in-place без изменений.
3. Не трогает BGM, encode, субтитры, GUI.

---

# VideoMaker — r43-phrase (2026-09-10)

## r43-phrase — phrase-break субтитров + полная поставка idlefix

### Что сделано
1. **subtitles.py**: phrase-break после `, ; : . ! ? …` и хвостового дефиса/тире;
   leading-punct / pure-punct-барьер; `_display_word`; pure-punct не в ASS.
2. В архив входят все правки **r43-idlefix** (GUI close, logging, start.command).
3. **VERSION.md / README_FIX / шапки main.py + start.command** выровнены под алгоритм версий.
4. 26/26 unit-тестов зелёные.

---

# VideoMaker — r43-idlefix (2026-09-10)

## r43-idlefix — idle + надёжное закрытие

### Что сделано
1. **Idle**: IMO-rescan 60 с (было 5) + кэш isfile 30 с; heartbeat 30 с.
2. **Закрытие**: `_closing` flag, `after_cancel`, всегда `prevent_sleep_stop`, kill ffmpeg/ffprobe, `os._exit(0)`.
3. **Логи**: INFO по умолчанию; DEBUG только при `VIDEOMAKER_DEBUG=1`; log_text ≤ ~3000 строк.
4. **start.command**: без `tee` и блокирующего `read`.
5. Все 26 unit-тестов зелёные.

---

# VideoMaker — r43-clean (2026-09-09)

## r43-clean — уборка + default Clean Pro

### Что сделано
1. **Default стиль субтитров = Clean Pro / parity** (H + V + Shorts).
   - `auto_aisie` / `auto` / пусто → `_build_shorts_parity_window`.
   - Strong: только Gemini `strong_words` (L2/L3/L4), без lexicon на parity-пути.
2. **Удалён мёртвый код**
   - Корневые `engines/`, `gui/`, `pipeline/` (r12-копии).
   - `engines/placement.py` (stub без вызовов).
   - `external/gemini_analyzer.py` (дубль `analysis.analyze`).
   - `_build_clean_pro_window`, `_build_wide_subtitles`.
   - `reframe_horizontal_to_vertical`, `_encode_vt_args`, `probe_sample_rate`.
   - `get_word_color` / `get_word_ass_color` / `list_available_palettes`.
   - GUI `_choose_*` (кнопки уже на `_browse_*`).
   - Константы `CAPTION_STYLES` / `HOOK_STYLES` / `BRAND_COLOR` в subtitles (GUI держит свои lists).
3. **start.command** — `PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"`.
4. **Не тронуто (намеренно)**
   - `highlight_lexicon` + legacy hormozi/tiktok/… (доступны явным выбором в GUI).
   - AISIE hooks, pipeline, analysis, transcription.

### Как проверить
```bash
python -m py_compile video_maker/engines/subtitles.py video_maker/engines/colors.py video_maker/gui/app.py
python -m pytest tests/test_shorts_parity.py -v
# GUI default: Clean Pro; лог parity:
# [СУБТИТРЫ] style=shorts_parity words=N events=M strong=K strong_source=gemini|none
```

---

# VideoMaker — r43 (2026-09-05)

## r43 — Clean Pro visual поверх стабильного shorts_parity

### Что сделано
1. **Optional strong** поверх r42 parity (`_build_shorts_parity_window`):
   - Источник: **только** `analysis["strong_words"]` от Gemini (`word` + `visual_weight` L2/L3/L4).
   - Exact match через `_norm_word_key`, min length ≥ 2.
   - AISIE hooks **не** обязательны; hooks=0 → karaoke работает, strong может быть пустым.
   - `enable_strong_words=False` → strong пустой (поведение как r42).
2. **Визуал**:
   - Font: SF Pro Display (Style `CleanPro` в ASS header, как r36/r42).
   - non-active / non-strong: base white `#FFFFFF`
   - active non-strong: mild yellow `#FFFF00`, scale 1.0
   - strong L2/L3/L4: neon color + scale **только** когда слово active
   - До речи strong в группе = base white, тот же размер (нет pre-color neon)
3. **colors.py**: `get_strong_ass_color`, `get_strong_scale`, палитра L2/L3/L4.
4. **Лог**:
   ```
   [СУБТИТРЫ] style=shorts_parity words=N events=M strong=K strong_source=gemini|none
   ```

### Палитра + scale

| Роль | HEX | ASS BGR | Scale (active) |
|------|-----|---------|----------------|
| base / non-active | #FFFFFF | &H00FFFFFF& | 1.0 |
| active non-strong | #FFFF00 | &H0000FFFF& | 1.0 |
| strong L2 | #FFFF00 | &H0000FFFF& | 1.14 |
| strong L3 | #FF5E00 | &H00005EFF& | 1.22 |
| strong L4 | #FF00FF | &H00FF00FF& | 1.28 |

Cyan отсутствует.

### Что сознательно НЕ сделано
- highlight_lexicon **не** подключён в parity path
- Нет idiom-модуля, нет «один цвет = одна строка»
- Нет phrase-scale на всю группу
- Legacy стили (hormozi, bold_pop, …) не трогались
- Shorts / Gemini clips / stream-copy / checkpoint не менялись
- Полный split `subtitles/` — отложен

### Как включить
- GUI: **Clean Pro** (или «Clean Pro (Shorts)»)
- `caption_style="clean_pro"` / `"shorts_parity"`
- Strong появится, если Gemini вернул `strong_words` и `enable_strong_words=True`

### Как откатиться
- Другой стиль → legacy path
- Или `enable_strong_words=False` → parity без strong (как r42)

### Эталон
- Chunk/active: ShortsMaker karaoke pages + word-level is_active
- Визуал strong: исторический Clean Pro VideoMaker (neon + scale)
- Grouping: VM `_group_words_with_boundaries` (2–3 слова)

### Как проверить
```bash
python -m py_compile video_maker/engines/subtitles.py video_maker/engines/colors.py
python -m pytest tests/test_shorts_parity.py -v
python -m pytest tests/ -q   # весь набор у себя
# Лог Clean Pro:
# [СУБТИТРЫ] style=shorts_parity words=N events=M strong=K strong_source=gemini|none
```

### Не тронуто
- pipeline/shorts, Gemini clips_for_shorts, AISIE hooks/CTA placement
- legacy caption styles

---

# VideoMaker — r42 (2026-09-05)
… (см. предыдущий блок / git history)
