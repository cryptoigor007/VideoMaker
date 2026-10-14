# CP20 — безопасность путей, staging и кэшей

**Сборка:** r57-api.7 · **Дата:** 2026-10-12 · **Основано на:** CP19 full snapshot.

## Изменения

- Безопасная нормализация имён серий и ID площадок; `.`/`..` не могут влиять на path join; `Episode.v2` сохраняется как имя серии.
- Выходные каталоги площадок отказываются проходить по вложенным symlink. Дубли platform ID после нормализации пропускаются до записи.
- Cleanup удаляет только зарезервированный `_tmp/shorts_staging`; shared `_tmp`/legacy `shorts` защищены; включённый `keep_temp_files` сохраняет промежуточные short renders.
- Whisper JSON, ffprobe metadata и фоновый PNG cache публикуются атомарно; PNG/MP4 persistent cache валидируются перед reuse.
- Gemini analysis cache учитывает полный транскрипт; ошибки model listing не показывают API key в сыром/URL-encoded виде.

## Результаты

- Полный набор: **161 passed, 23 subtests passed**.
- CP20 path/cache tests: **12 passed**.
- API security: **18 passed**. GUI mapping: **70 passed**. GUI/API panel: **33 passed**.
- Python compileall: PASS. Synthetic speech: PASS (120 WPM, word timestamps preserved). Synthetic BGM: PASS (H.264/AAC, full decode).
- ZIP integrity: after extraction, all manifest entries are verified by size and SHA-256; path count and excluded secret files are recorded in package report.

## Ограничения

Same-series reruns intentionally reuse output filenames, and there is not yet a cross-process lock for two separate app instances rendering into the same series directory. A native MLX transcription pass on the user's Mac and a full real-media end-to-end encode remain unverified in this environment.

## Возобновление

Unzip `VideoMaker_FULL_R57_CP20_PATH_CACHE_SAFETY.zip`, check `checkpoints/CP20_SOURCE_MANIFEST.json`, then run `python -m compileall -q video_maker tests` and `python -m pytest tests/ -q`.
