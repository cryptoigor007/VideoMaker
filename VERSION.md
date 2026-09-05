# VideoMaker — полный контроль версий

**Текущая сборка: r41** (2026-09-05)  
**Коммит базы:** `c532f12` (2026-09-04 23:10:06 +0300)  
**Очистка / архив:** 2026-09-05 (r41)

Единый номер сборки = максимальный `rN` среди файлов с шапкой `VideoMaker FIX | …-rN`.  
При каждом релизе: обновить этот файл + `CHANGES.md` + шапку изменённых файлов.

### Ключевые шапки (актуально r34)
| Файл | rN |
|------|-----|
| video_maker/engines/subtitles.py | **r40** (phrase karaoke) |
| video_maker/pipeline/shorts.py | r33 |
| video_maker/engines/analysis.py | r32→r33 |
| video_maker/engines/transcription.py | r31 |
| video_maker/gui/app.py | r27 (heartbeat→debug в теле) |
| video_maker/engines/aisie_integration.py | r28 |

---

## Правила

1. Изменили файл → поднять его `rN` в шапке, дата = день правки.
2. Номер сборки (архив, VERSION.md, CHANGES) = max(rN) по всем файлам.
3. В CHANGES.md — краткий блок «rNN — …» со списком файлов.
4. Рабочий код **только** в `video_maker/`. Корневые `engines/`, `gui/`, `pipeline/` удалены (были устаревшими дубликатами).
5. Если у файла нет шапки `VideoMaker FIX` — в таблице указаны дата и хеш последнего коммита из git.

---

## Таблица ВСЕХ файлов (сборка r28)

### Документация и корень проекта

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| VERSION.md | **r28** | 2026-09-04 (этот архив) | Полный контроль версий всех файлов |
| CHANGES.md | r27 | 2026-09-04 23:10 c532f12 | История изменений сборок |
| README.md | r28 | 2026-09-04 (этот архив) | Актуальный README (MLX Whisper) |
| README_FIX.txt | r27 | 2026-09-04 23:10 c532f12 | Краткая шпаргалка по r27 |
| requirements.txt | r28 | 2026-09-04 (этот архив) | Зависимости; mlx-whisper отдельно |
| start.command | r28 | 2026-09-04 (этот архив) | Относительные пути, без hardcode |
| .env.example | — | 2026-08-26 23:29 0c99742 | Пример переменных окружения |
| .gitignore | r28 | 2026-09-04 (этот архив) | +cache/, +*.log |

### VideoMaker.app (macOS)

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| VideoMaker.app/Contents/Info.plist | — | 2026-08-28 12:21 9ff9e91 | Bundle metadata |
| VideoMaker.app/Contents/MacOS/VideoMaker | r28 | 2026-09-04 (этот архив) | Launcher с относительными путями |
| VideoMaker.app/Contents/Resources/icon.icns | — | 2026-08-31 02:37 b2c4f12 | Иконка приложения |

### video_maker/ — ядро

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/__init__.py | — | 2026-08-26 23:29 0c99742 | Пакет |
| video_maker/main.py | — | 2026-08-28 18:41 e0fff77 | Точка входа |
| video_maker/icons/app_icon.png | — | 2026-08-31 02:37 b2c4f12 | Иконка GUI |

### video_maker/config/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/config/__init__.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/config/settings.py | **2026.09.04-r27** | 2026-09-04 23:10 c532f12 | validate без блокировки WhisperX |

### video_maker/gui/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/gui/__init__.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/gui/app.py | **2026.09.04-r27** | 2026-09-04 23:10 c532f12 | Галочки IMO (isfile, ttk state) |

### video_maker/pipeline/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/pipeline/__init__.py | — | 2026-08-27 21:15 7de371d | Экспорт стадий |
| video_maker/pipeline/stages.py | — | 2026-09-03 01:29 0f81fb0 | Audio/Transcribe/Gemini + Context |
| video_maker/pipeline/master.py | — | 2026-09-01 02:57 2270eb4 | MasterBuilder (16:9) |
| video_maker/pipeline/branches.py | **2026.09.03-r22** | 2026-09-03 01:29 0f81fb0 | BGM mix once on wide |
| video_maker/pipeline/parallel_finals.py | **2026.09.02-r21** | 2026-09-03 01:29 0f81fb0 | Vertical one encode |
| video_maker/pipeline/shorts.py | **2026.09.03-r26** | 2026-09-04 23:10 c532f12 | series_dir/shorts; Hook+CTA only |
| video_maker/pipeline/finalize.py | **2026.09.03-r26** | 2026-09-04 23:10 c532f12 | Без double nesting |
| video_maker/pipeline/checkpoint.py | **2026.09.02-r18** | 2026-09-03 01:29 0f81fb0 | Resume + ffprobe validate |

### video_maker/engines/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/engines/__init__.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/engines/video.py | **2026.09.03-r25** | 2026-09-04 23:10 c532f12 | Cache intro/outro segs |
| video_maker/engines/subtitles.py | **2026.09.03-r24** | 2026-09-03 01:29 0f81fb0 | Hook 4.5s / CTA 7.0s |
| video_maker/engines/ffmpeg_resilient.py | **2026.09.01-r5** | 2026-09-02 00:27 9940c65 | hwaccel videotoolbox |
| video_maker/engines/audio.py | — | 2026-09-01 02:57 2270eb4 | BGM, loudnorm, 48 kHz |
| video_maker/engines/transcription.py | — | 2026-09-01 02:57 2270eb4 | **Только MLX Whisper** |
| video_maker/engines/analysis.py | — | 2026-09-02 19:27 2931b48 | Gemini API |
| video_maker/engines/aisie_integration.py | — | 2026-08-29 11:00 2e64b97 | AISIE pipeline |
| video_maker/engines/power.py | — | 2026-08-31 12:28 9bdeea5 | Не засыпать во время обработки |
| video_maker/engines/whisperx_resolve.py | — | 2026-08-28 17:27 bb9d9d0 | Legacy path resolver (не используется) |

### video_maker/external/ (AISIE + Gemini helpers)

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| video_maker/external/__init__.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/utils.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/gemini_analyzer.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/__init__.py | — | 2026-08-28 18:41 e0fff77 | |
| video_maker/external/aisie/classifier.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/pipeline.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/placement.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/scoring.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/styles.py | — | 2026-08-28 18:41 e0fff77 | |
| video_maker/external/aisie/timing.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/validate.py | — | 2026-08-26 23:29 0c99742 | |
| video_maker/external/aisie/demo_plan.json | — | 2026-08-26 23:29 0c99742 | |

### tests/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| tests/__init__.py | — | 2026-08-26 23:29 0c99742 | |
| tests/test_pipeline.py | — | 2026-08-27 17:24 7e37c8e | |

### docs/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| docs/superpowers/specs/2026-08-26-videomeyker-design.md | — | 2026-08-26 23:29 0c99742 | Design spec |

### .vscode/

| Файл | Версия / ревизия | Последнее изменение (git) | Примечание |
|------|------------------|---------------------------|------------|
| .vscode/launch.json | — | 2026-08-27 22:17 fc00b32 | Debug config |

---

## Что удалено в r28 (не входит в архив)

| Удалённый путь | Причина |
|----------------|---------|
| `engines/` (корень) | Устаревшие дубликаты (r12–r13), код только в `video_maker/engines/` |
| `gui/` (корень) | Устаревший дубликат (`gui/app.py` r12), рабочий — `video_maker/gui/app.py` r27 |
| `pipeline/` (корень) | Устаревшие дубликаты; рабочий — `video_maker/pipeline/` |
| `videomaker.log`, `videomeyker.log` | Логи не должны быть в git |
| `cache/` | Кэш runtime (bg, gemini_state) — не исходники |

---

## История сборок (кратко)

### r27 — 2026-09-04
- GUI: галочки Intro/Middle/Outro (isfile вместо os.access; ttk state)
- settings.py: validate не блокирует из‑за WhisperX
- Добавлен VERSION.md

### r26 — 2026-09-03
- shorts.py, finalize.py: series_dir без double nest

### r25 — 2026-09-03
- video.py: cache prepared intro/outro segments

### r24 — 2026-09-03
- subtitles.py: Hook 4.5s / CTA 7.0s

### r22 — 2026-09-03
- branches.py: BGM mix once on wide

### r21 — 2026-09-02
- parallel_finals: vertical one_encode

### r18 — 2026-09-02
- checkpoint: resume + ffprobe

### r5 — 2026-09-01
- ffmpeg_resilient: inject_hwaccel videotoolbox

### r33 — 2026-09-05
- Short папка: только short_00N.mp4 (+txt); cut/burn во temp
- 4–5 shorts Gemini восстановлены

### r32 — 2026-09-05
- Shorts: ровно 1 файл (Gemini + hard limit)
- Heartbeat: log.debug (не спамит INFO)

### r31 — 2026-09-05
- **CRITICAL**: karaoke на full vertical/horizontal (баг: работал только при clip)
- Whisper cache без words → принудительный пересчёт

### r30 — 2026-09-05
- Clean Pro: строго 2–3 слова (4 только если все ≤4 символов); max_chars не от 4K
- AISIE strong: L2/L3/L4 заметный цвет + scale (1.12–1.28) + pop
- Устойчивый match strong_map (ё→е, prefix)

### r29 — 2026-09-04
- **subtitles.py**: словосочетания на vertical стыкуются с границами шортов Gemini
  (первое слово шорта = начало группы на экране; последнее = конец группы).
  AISIE / strong / стили karaoke не ломаются.

### r28 — 2026-09-04 (этот архив)
- Удалены корневые дубликаты engines/gui/pipeline
- Удалены логи и cache из дистрибутива
- start.command и .app launcher — относительные пути
- requirements.txt и README актуализированы под MLX Whisper
- VERSION.md покрывает **каждый** файл проекта

---

## Проверка после распаковки

```bash
head -5 video_maker/gui/app.py          # → 2026.09.04-r27
head -5 video_maker/config/settings.py  # → 2026.09.04-r27
head -5 video_maker/engines/video.py    # → 2026.09.03-r25
head -5 video_maker/pipeline/shorts.py  # → 2026.09.03-r26
ls engines gui pipeline 2>/dev/null     # → не должно быть (удалены)
```
