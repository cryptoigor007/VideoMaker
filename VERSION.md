# VideoMaker — контроль версий

**Текущая сборка: r43** (2026-09-05)

Единый номер сборки = максимальный rN среди файлов с шапкой `VideoMaker FIX | …-rN`.

---

## Таблица версий файлов (r43)

| Файл | Версия | Дата | Примечание |
|------|--------|------|------------|
| video_maker/engines/subtitles.py | **2026.09.05-r43** | 2026-09-05 | parity + optional Gemini strong |
| video_maker/engines/colors.py | **2026.09.05-r43** | 2026-09-05 | L2/L3/L4 + scale |
| video_maker/engines/placement.py | 2026.09.05-r42 | 2026-09-05 | stub (без изменений) |
| video_maker/gui/app.py | 2026.09.04-r27 | 2026-09-04 | без изменений |
| video_maker/pipeline/shorts.py | 2026.09.03-r26 | 2026-09-03 | без изменений |

---

## История сборок

### r43 — 2026-09-05
- Clean Pro visual поверх стабильного r42 parity.
- Strong: Gemini `strong_words` only (L2 yellow / L3 orange / L4 pink).
- Scale + color только на active strong; pre-color neon нет.
- Font: SF Pro Display (Style CleanPro, как раньше).

### r42 — 2026-09-05
- shorts_parity: чистый karaoke без lexicon/strong/phrase-scale.
- Stubs colors.py / placement.py.
