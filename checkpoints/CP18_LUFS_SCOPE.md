# CP18 — LUFS semantics and validation

**Статус:** завершён; полный snapshot сохранён после регрессии.

## Что найдено
В GUI написано «Целевой LUFS» и в скобках приведены платформы, что может восприниматься как нормализация всей финальной аудиовыдачи. Фактически `target_lufs` применяется в BGM mix (`FinalizeStage` передаёт его в `mix_bgm`), а voice-only ветка не делает отдельный loudnorm-проход. Это быстрее, но подпись должна обещать именно то, что программа делает.

Дополнительно API/Settings ранее принимали `NaN`, бесконечность, строки и значения вне реального диапазона, которые могли дойти до FFmpeg filter string или провалить рендер поздно.

## Исправления
- GUI теперь указывает «Целевой LUFS для микса с BGM» и объясняет, что voice-only не нормализуется отдельным проходом — без скрытого увеличения времени обработки.
- `Settings.validate()` принимает конечное числовое значение от −36 до −5 LUFS.
- API `apply_settings()` отклоняет invalid `target_lufs` без изменения сохранённого значения; OpenAPI указывает minimum/maximum/default/scope.
- GUI ввод очищается до `-14.0`, если пользователь вводит некорректный/бесконечный/выходящий за диапазон target.
- Обновлён тест карты настроек, который раньше ожидал `+1.5 LUFS` и теперь законно отклоняется.

## Проверки
- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **131 passed, 23 subtests passed**.
- `xvfb-run -a python tests/verify_api_settings.py`: **70 successful, 0 failed**.
- `xvfb-run -a python tests/verify_app_gui.py`: **33 successful, 0 failed**.
- Synthetic speech FFmpeg: PASS, WPM 120, timestamps preserved.
- Synthetic BGM FFmpeg: PASS, H.264/AAC and full audio decode.

`tests/verify_api_live.py` requires a separately running live app and token; a direct run without those arguments/host is invalid and is not counted as a regression failure. A self-contained API harness is planned for CP21.

## Решение по времени обработки
Новый voice-only loudnorm-pass не включался: это добавило бы минимум один полный анализ/encode к каждой версии и конфликтовало бы с приоритетом скорости. Пользователь может ожидать что −14 применяется к смеси с музыкой; это теперь явно написано в GUI и документации.
