# CP19 — безопасность API и ограничение запросов

**Сборка:** `r57-api.6` · **Дата:** 2026-10-12 · **Основано на:** полный CP18.

## Исправленные риски

1. **Токен в HTTP-логах.** `BaseHTTPRequestHandler` по умолчанию журналирует request-target, в том числе `?key=...`. Access-log теперь редактирует распространённые имена секретных query-параметров. Прямые стартовые сообщения больше не печатают bootstrap URL с основным токеном.
2. **Web UI помещала токен в каждый запрос URL.** Bootstrap-параметр `?key=` поддержан для старых ссылок, но после извлечения токена страница вызывает `history.replaceState`, убирая `key` из адресной строки, а API-вызовы используют только `X-Api-Key`. Заголовок имеет приоритет над legacy query-token.
3. **Неограниченное тело запроса и молчаливый разбор мусора.** JSON-body ограничено 2 MiB. Некорректный `Content-Length`, усечённое тело, invalid UTF-8/JSON и корень, отличный от объекта, возвращают HTTP 400. Content-Length выше лимита отклоняется с HTTP 413 до чтения/выделения памяти под заявленное тело.
4. **Небезопасная truthiness при настройке LAN.** Ранее `bool("false")` давал `True`, и строковое значение могло включить LAN. `lan`/`enabled` принимают только JSON boolean и проверяются до изменений конфигурации. Ошибка в одном поле не оставляет частично изменённые значения.
5. **Повреждённый `api.json`.** Невалидный `lan` безопасно сбрасывается в `False`, невалидный `enabled` — в `False`, некорректный порт — на default, некорректный или короткий токен заменяется случайным; нестроковый webhook URL очищается.
6. **Секреты webhook URL.** При журналировании доставки редактируются query-ключи доступа и URL userinfo `user:password@host`.
7. **Заголовки ответов.** Добавлены `Referrer-Policy: no-referrer` и `X-Content-Type-Options: nosniff` к обычным HTTP-ответам и SSE.

## Регрессия

- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **149 passed, 23 subtests passed**.
- `python -m pytest tests/test_api_security_cp19.py -q`: **18 passed**.
- `xvfb-run -a python tests/verify_api_settings.py`: **70 passed, 0 failed**.
- `xvfb-run -a python tests/verify_app_gui.py`: **33 passed, 0 failed**.
- CP19 test suite включает реальный loopback HTTP сервер: публичный health не содержит токен; protected status требует токен; валидный header побеждает неправильный query-token; слишком длинный Content-Length отклоняется с 413; а malformed JSON/config не применяется.
- Наследуемый синтетический FFmpeg smoke CP18: речь 120 WPM с сохранёнными word timestamps; BGM H.264/AAC с полным декодированием. Свежий реальный пользовательский Mac/MLX-прогон не выполнялся.

## Остаточные ограничения

- HTTP не шифрует трафик. Не пробрасывать порт в открытый интернет; для удалённого доступа использовать VPN, LAN разрешать только в доверенной сети.
- Rate limiting не реализован. На текущем loopback/LAN-сценарии сохранена единая bearer-token авторизация.
- Legacy `?key=` остаётся ради совместимости первоначальных ссылок. WebUI очищает его из адресной строки, но сама скопированная исходная ссылка всё ещё содержит токен; для новых интеграций надо использовать заголовок.
- Полный self-contained HTTP/API harness и повторный независимый пакетный прогон остаются задачей CP21.

## Восстановление

Распаковать `VideoMaker_FULL_R57_CP19_API_SECURITY.zip`, сверить `checkpoints/CP19_SOURCE_MANIFEST.json`, затем запустить `python -m compileall -q video_maker tests` и `python -m pytest tests/ -q`.
