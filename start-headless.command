#!/bin/bash
# VideoMaker — пульт без окна (2026.09.23-r57-api)
# Приложение работает в фоне, окна нет. Управление: браузер/телефон/API.
# Адрес и токен: ~/video_maker/api.json  (или блок «Пульт» в обычном окне).
cd "$(dirname "$0")" || exit 1
exec ./start.command --headless
