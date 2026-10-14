# CP19 — регрессия и выпуск полного архива

**Снимок:** `r57-api.6` · **Дата:** 2026-10-12.

## Содержимое

Полный снимок проекта, основанный на CP18: GUI-клиент, HTTP API/сервер, очередь, конвейер, движки, настройки, документация, тесты, отчёты CP12–CP19 и инструкции восстановления. Это не patch/diff.

## Проверки до выпуска

- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **149 passed, 23 subtests passed**.
- CP19 security tests: **18 passed**; включён живой HTTP test через loopback-сокет.
- Settings/GUI mapping: **70/70**, actual GUI/API panel: **33/33**.
- API token/query redaction, header auth, malformed JSON, Content-Length limit, LAN config strict type, fail-closed config: PASS.

## Проверки после упаковки

- ZIP test: PASS (`unzip -t`, CRC для всех записей).
- В архиве 185 записей; повторяющиеся пути отсутствуют; `.env` отсутствует.
- Source manifest содержит 159 файлов; после распаковки все 159 существуют и совпали по размеру и SHA-256: 0 отсутствующих, 0 несовпадений.
- `compileall` после распаковки: PASS.
- `pytest` из извлечённого архива: **149 passed, 23 subtests passed**.

## Прогресс

3 из 5 дополнительных фаз завершены (60%): CP17 timeout/atomic outputs, CP18 LUFS scope, CP19 API security. Дальше CP20 — пути/кэш/временные файлы/коллизии вывода; CP21 — заключительный независимый прогон и полный финальный архив.
