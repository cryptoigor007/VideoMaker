# VideoMaker — контроль версий

**Текущая сборка: r43-clean** (2026-09-09)

Единый номер сборки = максимальный rN среди файлов с шапкой `VideoMaker FIX | …-rN`.

---

## Таблица версий файлов (r43-clean)

| Файл | Версия | Дата | Примечание |
|------|--------|------|------------|
| video_maker/engines/subtitles.py | **2026.09.09-r43-clean** | 2026-09-09 | default Clean Pro; dead builders removed |
| video_maker/engines/colors.py | **2026.09.09-r43-clean** | 2026-09-09 | only strong helpers |
| video_maker/gui/app.py | 2026.09.09-r43-clean | 2026-09-09 | default clean_pro; dead _choose_* removed |
| video_maker/pipeline/stages.py | 2026.09.09-r43-clean | 2026-09-09 | caption_style default clean_pro |
| video_maker/engines/placement.py | — | — | **удалён** (stub, не использовался) |
| video_maker/external/gemini_analyzer.py | — | — | **удалён** (дубль analysis.py) |

---

## История сборок

### r43-clean — 2026-09-09
- Default caption = Clean Pro (parity) для H и V/Shorts (`auto_aisie` → parity).
- Удалены корневые engines/gui/pipeline (старые r12).
- Удалены dead: placement stub, GeminiAnalyzer, `_build_clean_pro_window`, `_build_wide_subtitles`, `reframe_*`, `_encode_vt_args`, `probe_sample_rate`, color helpers без вызовов, GUI `_choose_*`.
- `start.command`: относительный `PROJECT_DIR`.
- Lexicon/legacy-пресеты сохранены (явный выбор hormozi и т.д. в GUI).

### r43 — 2026-09-05
- Clean Pro visual поверх r42 parity (Gemini strong).

### r42 — 2026-09-05
- shorts_parity каркас.
