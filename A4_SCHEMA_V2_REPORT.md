# A4 — база v2 для библиотеки B-roll

Дата: 2026-10-22. Рабочая копия: `/Users/dreamstore/VideoMaker-r57cp34`.
Production-БД (`~/video_maker/broll_library.db`) использовалась **только read-only**;
миграция и все записи выполнялись на копиях.

## 1. Что сделано

| Файл | Назначение | Состояние |
|---|---|---|
| `tools/schema_v2.sql` | схема v2: clips/sources/certificates/themes/moods(40)/fingerprints/embeddings/usage/demand_ledger/audit_log/health, WAL, индексы, views | готово |
| `tools/migrate_schema_v2.py` | v1 → v2 только на копии; dry-run по умолчанию, `--apply` только на копию | готово |
| `tools/audit_library.py` | read-only аудит: inventory, files, provenance, описания/эмбеддинги, mood, дубли-кандидаты | готово |
| `tools/mood_embeddings.py` | 40 mood-эмбеддингов (кэш), cosine top-2, порог 0.30, пилот, запись только с `--apply` | готово |
| `tools/demand_ledger.py` | импорт слабых сцен из `scenes.jsonl`, эмбеддинги запросов, недельные кластеры, топ-50 дефицитов, JSON для докачки | готово |
| `tests/tools/` | 27 лёгких тестов: схема, миграция на temp-копии, дедуп, mood-cosine, demand-кластеризация | 27 passed |

Ключевые изменения в схеме:

- `clips.status` поддерживает `active / quarantine / twin / retired / needs_review`
  (плюс `offline`, `missing`, `duplicate`, `archived` для совместимости);
  есть `twin_of_id` для связи пары twin.
- `demand_ledger` расширен полями `week`, `query_norm`, `query_embedding`,
  `embedding_model/dim`, `cluster_key`, `frequency`, `weak_reason`, `source`.
- `library_health_history` хранит `twins`/`retired`; добавлены view
  `v_provenance_gaps`, `v_needs_review`, `v_embedding_coverage`,
  `v_duplicate_candidates`, `v_demand_weekly`.
- WAL включён (`PRAGMA journal_mode = WAL`), внешние ключи и индексы — в схеме.
- Словарь 40 настроений зашит в `schema_v2.sql`; эмбеддинги заполняет
  `mood_embeddings.py` (таблица `moods`, назначения — `clip_moods`).

## 2. Реальный аудит production-библиотеки

Отчёт: `~/video_maker/audit/audit_library_20261022_232205.json` (+ `.md`,
`_fingerprints.json`). Режим `mode=read-only`, БД открыта через `mode=ro` и
`PRAGMA query_only=ON`.

### Inventory

- Клипов: **1731**; объём файлов: **71 005 969 185 B (66.1 GiB)**.
- Провайдеры: pixabay 958, local 683, series_pool 90.
- Ориентация: wide 1020, vertical 711.
- Длительности: 0 — 773, 5–10 c — 218, 10–20 c — 566, 20–30 c — 174.
- Файлы: missing **0**, zero-size **0** (все 1731 доступны на SMB).

### Провенанс

- Без `source_url`: **773** (44.7%); без `author`: **1138** (65.7%).
- Без `license`: 0; без `license_url`: 90.
- local/custom: **967** (55.9%).
- Сертификатов: **1667**, битых 0; без сертификата **64**, лишних 0.
- Восстановить из сертификатов: source_url 0, author 0 (у сертификатов пустые
  page_url/source_url: 845/709).
- Подозрительных provider↔URL: 0.

### Контент и mood

- Пустых описаний: **0**, коротких (<20 символов): **0**, пустых тегов: **0**.
- Эмбеддинги: **1572/1731**, без вектора **159**, лишних векторов 1
  (`broll_embeddings.json`, 66.5 MB).
- Mood-словарь: **40/40** векторов в кэше; в production-БД `clip_moods` нет
  (слой ещё не применялся).

### Дубли — только кандидаты, ничего не удалено

| Стадия | Результат |
|---|---|
| size-группы | 39 групп, 107 файлов-кандидатов (3.68 GB) |
| sha256 | 107 файлов; **39 exact-кластеров / 107 файлов**, лишних **2 392 537 187 B (~2.23 GiB)** |
| pHash (64-bit, Hamming ≤ 8, aspect ±0.08) | 64 кандидата → **16 кластеров / 62 файла**, 129 близких пар |
| semantic 0.92 | 1572 вектора → **175 пар / 83 кластера / 210 файлов** |

- Semantic-стадия — косинус **текстовых эмбеддингов Gemini** ≥ 0.92
  (CLIP-подобная стадия; визуальный CLIP в окружении не установлен). Это
  кандидаты «похожих описаний/сцен», а не приговор файлу.
- Объединение кандидатов: 286 файлов. Решение об удалении/переводе в
  `twin`/`retired` не автоматизировано.

### Гигиена

- `library_index.json`: 1731 запись, расхождений с БД 0.
- Вторая БД `.../broll_library/library.db`: 6 клипов — риск двух источников истины.
- `usage`: 198 строк, все пути не совпадают с библиотекой, но **191** привязывается
  по уникальному basename (это делает миграция).
- `scores`: 28 061 строка, 4567 orphan-путей; `clip_perf`: 2019 строк, 1594 orphan.
- Скан диска «файлы вне индекса» **не завершён**: обход SMB не уложился в 15 минут,
  поэтому в отчёте `orphans_on_disk = null`, а не 0. Это ограничение, не вывод.

### Индекс здоровья

**80.8/100**. Штрафы: провенанс 13.15, дубли 2.48, эмбеддинги 1.38,
метаданные 2.23, missing 0.

## 3. Миграция v2 — dry-run и apply на копии

Отчёты:

- dry-run: `~/video_maker/migration/A4_dryrun_report.json` (+ console log)
- apply: `~/video_maker/migration/A4_apply_report.json` (+ console log)
- копия v2: **`~/video_maker/migration/broll_library_v2_A4.db`** (не production)

Результат одинаков для dry-run и apply:

- Клипов: **1731 → 1731**; `source_unchanged=true`; integrity `ok`;
  FK violations **0**; пустых `asset_uuid` **0**; Δduration **0.000 c**.
- Sources: **958**; certificates: **1667**; themes: **30**; moods: **40**.
- Fingerprints: **171** (107 sha256 + 64 phash64).
- Эмбеддинги (только метаданные): **1572**; без эмбеддинга 159.
- `clip_usage`: 198 (7 orphan, 191 привязано по basename).
- `needs_review`: **1165 (67.3%)**, причины:
  missing_author 1138, missing_source_url 773, local_collection 773,
  missing_duration 773, duplicate_sha256 107, missing_certificate 64.
- Все 1731 клипов имеют `status=active`; дубли **не** переводились в `twin`
  автоматически (аудит даёт только кандидатов).
- В `library_health_history` записан снимок: `dup_clusters=39`, `provenance_gap=1138`,
  `no_embedding=159`, `needs_review=1165`.
- Индекс здоровья v2-копии: **85.5/100**.

Заполненность полей: source_url 958 (55.3%), author 593 (34.3%),
license 1731 (100%), sha256 107 (6.2%), phash 64 (3.7%), duration 958 (55.3%),
разрешение 1731 (100%), bitrate 1641 (94.8%), blur 956 (55.2%), motion 0,
description 1731 (100%), tags 1731 (100%), embedding 1572 (90.8%),
certificate 1667 (96.3%).

## 4. Mood-пилот (50 клипов)

Отчёты:

- report-only: `~/video_maker/audit/A4_mood_pilot_report.json` / `.md`
- apply: `~/video_maker/audit/A4_mood_pilot_apply.json` / `.md`
- запись: `broll_library_v2_A4.db` (только копия)

Параметры: модель `gemini-embedding-001`, порог **0.30**, top-2, `--limit 50`.
Словарь: **40/40** векторов из кэша, новых вызовов API 0.

- Пилот: **50** клипов; с mood **50/50 (100%)**; below threshold 0.
- top-1 cosine: avg **0.6431**, min 0.5745, max 0.7458.
- Распределение top-2: calm 14, serenity 11, hope 11, focus 10, excitement 10,
  awe 9, joy 6, freedom 5, determination 5, fatigue 3, остальные 1–2.
- Записано: **100** строк `clip_moods`, 50 клипов, 40 векторов mood,
  1 `mood_runs` (mode=pilot), audit_log.

**Важное ограничение:** порог 0.30 пропускает всё; у текстовых cosine высокий
базовый уровень и маленькие margin (часто 0.001–0.05). Для продакшн-отбора нужен
margin/z-score или калибровка на 100–200 размеченных клипах. Сейчас mood — это
ранжирование кандидатов, а не жёсткая метка.

## 5. Demand ledger

### Реальные данные

`scenes.jsonl` в доступных местах **не найдено** (проверены `~/video_maker`,
`/Volumes` до глубины 4, проектные `_tmp`/outputs). Реальный импорт выполнить
не из чего: `A4_demand_real.json` — 0 файлов, 0 слабых сцен, production не
изменялась.

### Smoke на синтетическом scenes.jsonl (не реальный спрос)

Файл: `~/video_maker/audit/A4_demand_demo_scenes.jsonl`; копия БД:
`~/video_maker/migration/broll_library_v2_A4_demand_demo.db`.
10 слабых + 2 сильных сцены, 5 уникальных запросов, 5 эмбеддингов (3072)
через `--embed auto`, 5 кластеров, топ-дефицит «debt collector office» ×3.
В `demand_ledger` записано 10 строк; у всех есть `frequency`, `cluster_key`,
`selected_clip_id`; повторный импорт заменяет строки недели, не дублирует.

### Готовность

`demand_ledger.py` умеет: искать `scenes.jsonl` по файлам/папкам/glob,
отбирать слабые сцены (`selected_score < 6`, нет выбора/кандидатов, fallback),
нормализовать запрос, считать частоту, кэшировать эмбеддинги, кластеризовать
по неделям (cosine 0.82 или точная нормализованная группа), отдавать топ-50
в JSON/MD и писать в v2-копию только с `--apply`.

## 6. Тесты и проверки

```
./.venv/bin/python3 -m compileall -q tools tests/tools   # OK
./.venv/bin/python3 -m pytest -q tests/tools             # 27 passed
```

Покрыто:

- схема: таблицы/views, 40 mood, WAL, `twin`/`retired`, demand-колонки;
- миграция: temp-копия v1 → v2, сохранение исходника по sha256, дубли sha256,
  usage по basename, fingerprints JSON, запрет source==target;
- аудит: hamming/pHash-кандидаты с aspect-фильтром, DSU, semantic 0.92,
  read-only CLI (sha256 БД до/после), mood-метаданные;
- mood: 40 словарь, top-2/порог, dimension mismatch, детерминированный пилот,
  кэш без сети, запись в v2 и запрет production;
- demand: нормализация, ISO-недели, weak_reason, cosine/exact-кластеры,
  частота/агрегация, CLI apply + идемпотентный повтор, запрет production.

## 7. Что готово к внедрению

1. **Схема и миграция** — готовы; v2-копия собрана и проверена. Нужен отдельный
   шаг cutover (переключение приложения на копию) — в этой задаче не делался.
2. **Аудит** — можно запускать регулярно; отчёт и fingerprints уже пригодны
   для следующей миграции/дедупа.
3. **Mood-слой** — технически готов; перед включением в отбор откалибровать
   порог/margin (текущий 0.30 слишком мягкий).
4. **Demand ledger** — готов к работе, как только baseline начнёт писать
   `scenes.jsonl`; демо подтверждает весь путь, включая эмбеддинги и запись.
5. **Дубли** — 39 exact-кластеров (2.23 GiB) и 16 pHash-кластеров ждут решения
   человека; автоматического удаления нет.

## 8. Ограничения

- Скан «файлы вне индекса» по SMB не завершён (обход >15 мин); в отчёте
  `orphans_on_disk=null`.
- Визуальный CLIP не установлен; semantic-стадия — текстовые эмбеддинги Gemini.
- `motion` в v2 не заполнен (в legacy-БД его не было).
- Production-БД живая: её sha256 меняется от работы приложения; в отчётах
  миграции зафиксировано, что **во время прогона** она не менялась.
- `scenes.jsonl` реального спроса пока нет; demand-отчёт по реальным данным пуст.
- Секреты не хардкодились: ключи читаются из `~/.vm_gemini_keys.env`/env.

## 9. Артефакты

- Схема: `tools/schema_v2.sql`
- Миграция: `~/video_maker/migration/A4_dryrun_report.json`,
  `~/video_maker/migration/A4_apply_report.json`,
  `~/video_maker/migration/broll_library_v2_A4.db`
- Аудит: `~/video_maker/audit/audit_library_20261022_232205.json` / `.md` /
  `_fingerprints.json`
- Mood: `~/video_maker/audit/A4_mood_pilot_report.*`,
  `~/video_maker/audit/A4_mood_pilot_apply.*`
- Demand: `~/video_maker/audit/A4_demand_real.json`,
  `~/video_maker/audit/A4_demand_demo_embed.json` / `.md`
- Тесты: `tests/tools/` (27)
