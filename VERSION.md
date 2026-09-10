# VideoMaker — контроль версий

**Текущая сборка: r43.3-stable** (2026-09-10)

Единый номер сборки = максимальный **rN** (или **rN.M**) среди файлов с шапкой  
`VideoMaker FIX | YYYY.MM.DD-rN[-suffix]`.

Минор **.1** = безопасная оптимизация финализации без смены логики encode/BGM/субтитров.

---

## Таблица версий файлов (сборка r43.1-place)

| Файл | Версия в шапке | Дата | Примечание |
|------|----------------|------|------------|
| video_maker/pipeline/finalize.py | **2026.09.10-r43.1-place** | 2026-09-10 | same FS → move; EXDEV → copy; keep_temp → copy |
| video_maker/engines/subtitles.py | 2026.09.10-r43-phrase | 2026-09-10 | phrase-break + display clean |
| video_maker/gui/app.py | 2026.09.10-r43-idlefix | 2026-09-10 | idle + close |
| video_maker/main.py | 2026.09.10-r43-idlefix | 2026-09-10 | logging INFO |
| start.command | 2026.09.10-r43-idlefix | 2026-09-10 | без tee/read |
| video_maker/engines/colors.py | 2026.09.09-r43-clean | 2026-09-09 | strong palette |
| video_maker/pipeline/stages.py | 2026.09.09-r43-clean | 2026-09-09 | caption default |

---

## История сборок

### r43.1-place — 2026-09-10
- Finalize: `os.replace` на одном разделе; `copy2` при EXDEV / keep_temp_files.
- Лог: `→ move` или `→ copy`. Shorts «already in place» без изменений.
- Encode, BGM, subtitles, idlefix — без изменений.
- 26/26 unit-тестов зелёные.

### r43-phrase — 2026-09-10
- Phrase-break субтитров + idlefix в одной поставке.

### r43-idlefix — 2026-09-10
- Idle timers, close, logging, start.command.

### r43-clean — 2026-09-09
- Clean Pro default, dead-code cleanup.

---

## Проверка

```bash
head -3 video_maker/pipeline/finalize.py     # → 2026.09.10-r43.1-place
head -3 video_maker/engines/subtitles.py     # → 2026.09.10-r43-phrase
python -m pytest tests/ -q                   # → 26 passed
```
