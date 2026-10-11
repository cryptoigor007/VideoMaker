# VideoMaker | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api.1: проверка карты «настройка → переменная окна» на живом окне
#              (69 проверок). Запуск: python tests/verify_api_settings.py
# PREV: (нет файла)
# NEW FILE: tests/verify_api_settings.py

"""Проверка карты настроек API на живом окне VideoMaker.

Для каждой привязки «поле → Tk-переменная» проверяем, что apply_settings
меняет И переменную окна, И объект настроек. Именно расхождение этих двух
мест было главным дефектом: кнопка «Создать видео» читает переменные.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # корень проекта

from video_maker.api.control import (
    SETTINGS_VAR_MAP,
    apply_settings,
    read_settings,
    set_series,
)
from video_maker.gui.app import App

root = tk.Tk()
root.withdraw()
app = App(root)
settings = app.settings

OK, BAD = [], []
STAMP = "ПРОВЕРКА-123"

print("=" * 74)
print("КАРТА НАСТРОЙКИ → ПЕРЕМЕННАЯ ОКНА (всего", len(SETTINGS_VAR_MAP), "полей)")
print("=" * 74)

missing_attr = []
for field, var_name in sorted(SETTINGS_VAR_MAP.items()):
    var = getattr(app, var_name, None)
    if var is None or not hasattr(var, "get"):
        missing_attr.append((field, var_name))
        continue
    # caption_style/hook_style живут ТОЛЬКО в окне (их читает _start, а не Settings)
    if not hasattr(settings, field) and field not in ("caption_style", "hook_style"):
        missing_attr.append((field, var_name + " (нет поля в Settings)"))
if missing_attr:
    for f, v in missing_attr:
        BAD.append(f"нет переменной: {f} → {v}")
        print(f" ПЛОХО {f:28} → {v}")
else:
    OK.append("все переменные карты существуют в окне")
    print("  ok   все переменные карты существуют в окне")

print()
print("=" * 74)
print("ПРИМЕНЕНИЕ ЗНАЧЕНИЙ (переменная окна И объект настроек)")
print("=" * 74)

for field, var_name in sorted(SETTINGS_VAR_MAP.items()):
    var = getattr(app, var_name, None)
    if var is None or not hasattr(var, "get") or not hasattr(settings, field):
        continue
    if not hasattr(settings, field):  # поле только в окне
        current = var.get()
    else:
        current = getattr(settings, field)
    if isinstance(current, bool):
        want = not current
    elif isinstance(current, float):
        want = 1.5
    elif isinstance(current, int):
        want = 123
    else:
        want = STAMP

    apply_settings(app, {field: want})

    got_var = var.get()
    got_set = getattr(settings, field) if hasattr(settings, field) else want
    if isinstance(want, bool):
        var_ok = bool(got_var) is want
    else:
        var_ok = str(got_var).strip() == str(want)
    set_ok = got_set == want

    if var_ok and set_ok:
        OK.append(field)
        print(f"  ok   {field:28} → {var_name:26} = {str(got_var)[:18]}")
    else:
        BAD.append(field)
        print(f" ПЛОХО {field:28} → {var_name:26} "
              f"переменная={str(got_var)[:18]!r} (ждали {want!r}), настройка={got_set!r}")

print()
print("=" * 74)
print("ОСОБЫЕ СЛУЧАИ")
print("=" * 74)
applied, unknown = apply_settings(app, {"поля_нет": 1, "series_name": "Серия-77"})
ok = unknown == ["поля_нет"] and "series_name" in applied
(OK if ok else BAD).append("неизвестное поле отбито, series_name принят")
print(f"{'  ok  ' if ok else ' ПЛОХО'} неизвестное поле отбито: {unknown}")

ok = app.series_entry.get("1.0", "end").strip() == "Серия-77"
(OK if ok else BAD).append("серия записана в Text-виджет")
print(f"{'  ok  ' if ok else ' ПЛОХО'} серия в Text-виджете: {app.series_entry.get('1.0', 'end').strip()!r}")

ok = settings.series_name == "Серия-77"
(OK if ok else BAD).append("серия записана в настройки")
print(f"{'  ok  ' if ok else ' ПЛОХО'} серия в настройках: {settings.series_name!r}")

set_series(app, "")
ok = app.series_entry.get("1.0", "end").strip() == "" and settings.series_name == ""
(OK if ok else BAD).append("серия очищается")
print(f"{'  ok  ' if ok else ' ПЛОХО'} серия очищается")

data = read_settings(app)
secret_mask = all(str(v) in ("***", "") for k, v in data.items()
                  if any(h in k.lower() for h in ("key", "token", "secret", "password")))
ok = secret_mask and data.get("gemini_api_key") == "***"
(OK if ok else BAD).append("секреты замаскированы в read_settings")
print(f"{'  ok  ' if ok else ' ПЛОХО'} секреты замаскированы: gemini_api_key={data.get('gemini_api_key')!r}")

# ключевое поле: папка вывода должна попасть и в переменную, и в настройки
apply_settings(app, {"output_folder": "/tmp/вывод-проверка"})
ok = app.output_var.get() == "/tmp/вывод-проверка" and settings.output_folder == "/tmp/вывод-проверка"
(OK if ok else BAD).append("папка вывода пишется в переменную окна")
print(f"{'  ok  ' if ok else ' ПЛОХО'} output_folder в переменной окна: {app.output_var.get()!r}")

# галочки IMO — тот самый дефект: _start читает их и перезаписывает настройки
apply_settings(app, {"h_enable_intro": False, "s_enable_subtitles": False})
ok = app.h_intro.get() is False and app.s_subs.get() is False
(OK if ok else BAD).append("галочки IMO пишутся в переменные окна")
print(f"{'  ok  ' if ok else ' ПЛОХО'} галочки IMO: h_intro={app.h_intro.get()} s_subs={app.s_subs.get()}")

# поля упаковки — их _save_settings пишет ИЗ переменных
apply_settings(app, {"required_hashtags": "#проверка", "shorts_desc_max_len": 123})
ok = app.required_hashtags_var.get() == "#проверка" and settings.shorts_desc_max_len == 123
(OK if ok else BAD).append("поля упаковки пишутся в переменные окна")
print(f"{'  ok  ' if ok else ' ПЛОХО'} упаковка: hashtags={app.required_hashtags_var.get()!r} "
      f"max_len={app.shorts_desc_max_len_var.get()!r}")

# все поля Settings охвачены картой или осознанно не охвачены
fields = {f.name for f in dataclasses.fields(settings)}
mapped = set(SETTINGS_VAR_MAP)
unmapped = sorted(fields - mapped)
print()
print("полей Settings:", len(fields), "| связаны с окном:", len(mapped),
      "| только в настройках:", len(unmapped))
print("   ", ", ".join(unmapped))

print()
print("=" * 74)
print(f"ИТОГ: успешно {len(OK)}, провалено {len(BAD)}")
for name in BAD:
    print("   -", name)
print("=" * 74)

root.destroy()
sys.exit(1 if BAD else 0)
