# VideoMaker — контроль версий

**Текущая сборка: r27** (2026-09-04)

Единый номер сборки = максимальный rN среди файлов с шапкой `VideoMaker FIX | …-rN`.
При каждом релизе: обновить этот файл + CHANGES.md + шапку изменённых файлов.

---

## Таблица версий файлов (r27)

| Файл | Версия | Дата | Примечание |
|------|--------|------|------------|
| video_maker/gui/app.py | **2026.09.04-r27** | 2026-09-04 | FIX: галочки IMO (intro не ломает outro) |
| video_maker/pipeline/shorts.py | 2026.09.03-r26 | 2026-09-03 | shorts → series_dir/shorts |
| video_maker/engines/video.py | 2026.09.03-r25 | 2026-09-03 | cache prepared intro/outro segs |
| video_maker/engines/subtitles.py | 2026.09.03-r24 | 2026-09-03 | Hook 4.5s / CTA 7.0s |
| video_maker/pipeline/branches.py | 2026.09.03-r22 | 2026-09-03 | BGM mix once on wide |
| video_maker/pipeline/parallel_finals.py | 2026.09.02-r21 | 2026-09-02 | vertical one encode |
| video_maker/pipeline/checkpoint.py | — | — | без пофайловой шапки |
| video_maker/pipeline/stages.py | — | — | без пофайловой шапки |
| video_maker/pipeline/finalize.py | — | — | без пофайловой шапки |
| video_maker/config/settings.py | **2026.09.04-r27** | 2026-09-04 | validate: без блокировки WhisperX |
| gui/app.py (корень) | 2026.09.01-r12 | 2026-09-01 | **устаревший дубликат**, не используется (import из video_maker.gui) |

---

## История сборок

### r27 — 2026-09-04
- **GUI** (`video_maker/gui/app.py`): галочки Intro/Middle/Outro — умная логика сохранена.
  - **Первопричина:** `os.access(R_OK)` на macOS (сеть/том/ACL) давал False на только что
    выбранном файле → галочка оставалась DISABLED. Убран access, оставлен `isfile`.
  - **ttk:** enable/disable через `.state(['!disabled'])` / `.state(['disabled'])`.
  - **browse:** sync до save (bool=True успевает в JSON).
  - **NFC/NFD** нормализация пути (macOS).
  - Логика: файл есть → ON+active (можно OFF); нет/пусто → OFF+disabled.
- **settings.py**: убрана ошибка validate по отсутствующему whisperx_path
  (транскрипция = MLX Whisper, путь WhisperX игнорируется).
- Добавлен `VERSION.md`.


### r26 — 2026-09-03
- shorts.py: shorts → series_dir/shorts (без double nest / temp+copy).

### r25 — 2026-09-03
- video.py: cache prepared intro/outro segs (scale+trim+encode) across runs.

### r24 — 2026-09-03
- subtitles.py: Hook 4.5s / CTA 7.0s; shorts skip Hook/CTA if overlap long zones.

### r22 — 2026-09-03
- branches.py: BGM always mix once on wide; vertical/shorts reuse.

### r21 — 2026-09-02
- parallel_finals / CHANGES: vertical one_encode + safe BGM + shorts only Hook+CTA.

### r4 (GUI) — 2026-09-01
- Первая версия авто-галочек IMO с проверкой доступа к файлу (баг исправлен в r27).

---

## Правила нумерации

1. Изменили файл → поднять его `rN` в шапке, дата = день правки.
2. Номер сборки (архив, VERSION.md, CHANGES) = max(rN) по всем файлам.
3. В CHANGES.md — краткий блок «rNN — …» со списком файлов.
4. Рабочий GUI только `video_maker/gui/app.py`. Корневой `gui/app.py` не править без синхронизации.
