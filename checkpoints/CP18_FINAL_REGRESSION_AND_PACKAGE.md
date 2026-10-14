# CP18 — финальная регрессия и полный архив

**Снимок:** `r57-api.5` · **Дата:** 2026-10-12.

## Проверки перед выдачей

- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **131 passed, 23 subtests passed**.
- `xvfb-run -a python tests/verify_api_settings.py`: **70 successful, 0 failed**.
- `xvfb-run -a python tests/verify_app_gui.py`: **33 successful, 0 failed**.
- Synthetic speech FFmpeg: PASS; output target 120 WPM and word timestamps preserved.
- Synthetic BGM FFmpeg: PASS; H.264/AAC output, audio stream present, full audio decode PASS.
- `target_lufs`: range −36…−5, non-finite/nonnumeric/out-of-range values rejected; OpenAPI scope verified.
- После сборки ZIP проверен после распаковки: дубли путей отсутствуют, `.env` отсутствует, размеры и SHA-256 каждого файла CP18-манифеста совпали; `compileall` и `pytest` повторно запущены из извлечённого архива. CLI `--help` завершился кодом 0. Отдельный повтор synthetic BGM smoke прошёл (H.264/AAC, аудио полностью декодируется).

## Восстановление

Распакуйте `VideoMaker_FULL_R57_CP18_LUFS_SCOPE.zip`, затем выполните `python -m compileall -q video_maker tests` и `python -m pytest tests/ -q`. Используйте `checkpoints/CP18_SOURCE_MANIFEST.json`; манифест сознательно не включает собственный хеш.

## Следующие задачи

2 из 5 follow-up фаз завершены (40%). CP19: безопасность API и токенов; CP20: пути, временные файлы, кэш и перезапись; CP21: full regression, live API harness и final source package.

`tests/verify_api_live.py` не автономен: ему требуется работающий экземпляр приложения, порт и тестовый токен. Его прямой запуск без аргументов/сервера не считается успешной проверкой. Для CP21 требуется поднять изолированный тестовый API и выполнить сценарий на нём.
