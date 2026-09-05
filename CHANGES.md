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
