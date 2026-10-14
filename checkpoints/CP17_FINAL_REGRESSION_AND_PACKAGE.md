# CP17 — финальная проверка и архив

**Снимок:** `r57-api.4` · **Checkpoint:** CP17 · **Дата:** 2026-10-12

## Результаты перед упаковкой

- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **126 passed, 7 subtests passed**.
- `python tests/synthetic_ffmpeg_smoke.py`: PASS; тестовый сигнал, темп 120.0 WPM, word-level timestamps сохранены.
- `python tests/synthetic_bgm_mix_smoke.py`: PASS; итог H.264/AAC, audio stream найден, полное декодирование аудио PASS.
- `python -m video_maker.main --help`: PASS.
- `bash -n start.command start-headless.command`: PASS.
- AST subprocess scan: проверено 32 вызова, 0 вызовов без явного `timeout`.
- Манифест CP17 перечисляет исходники, документацию, тесты и отчёты чекпоинта; SHA-256/размер проверяются перед упаковкой.

## Восстановление

1. Распакуйте `VideoMaker_FULL_R57_CP17_TIMEOUT_POLICY.zip` в отдельную папку.
2. Перейдите в корень распакованного проекта.
3. Проверьте `checkpoints/CP17_SOURCE_MANIFEST.json` (манифест сознательно не хеширует сам себя).
4. Повторите `python -m compileall -q video_maker tests` и `python -m pytest tests/ -q`.
5. На целевом Mac сначала протестируйте короткую копию реальной записи: тайм-ауты ещё нужно оценить на реальных длительных 4K-файлах и внешнем диске.

## Статус аудита после CP17

1 из 5 дополнительных фаз завершена (20%). Дальше: CP18 — ясность смысла настройки LUFS без скрытого дополнительного прохода; CP19 — безопасность токенов/API; CP20 — пути, кэш, временные файлы и перезапись; CP21 — финальная полная регрессия и recovery archive.

Нативная MLX-транскрипция на Apple Silicon и полный пользовательский выпуск в этой среде не запускались.
