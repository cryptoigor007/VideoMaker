"""Быстрое превью веб-пульта в headless Chrome (дизайн-проверка без Playwright).

Собирает страницу пульта с мок-API (статус/очередь/журнал/схема/настройки) и
снимает скриншоты в /tmp: vm_panel_top.png (десктоп), vm_panel_mobile.png
(телефон). Данные — из живого API (/tmp/vm_live_settings.json и
/tmp/vm_live_schema.json, можно снять curl'ом), иначе — из файла настроек.

Требует установленный Chrome; без него тихо завершается.
Запуск: python tests/verify_webui_preview.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from video_maker.api.control import get_settings_schema  # noqa: E402
from video_maker.api.webui import render  # noqa: E402

CHROME_CANDIDATES = [
    os.environ.get("CHROME_EXECUTABLE", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


def _chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if candidate and pathlib.Path(candidate).exists():
            return candidate
    raise SystemExit("Chrome не найден — превью пропущено")


def _settings_and_schema() -> tuple[dict, dict]:
    live_settings = pathlib.Path("/tmp/vm_live_settings.json")
    live_schema = pathlib.Path("/tmp/vm_live_schema.json")
    if live_settings.exists() and live_schema.exists():
        settings = json.loads(live_settings.read_text(encoding="utf-8")).get("settings", {})
        schema = json.loads(live_schema.read_text(encoding="utf-8")).get("schema") or {}
        if schema.get("sections"):
            return settings, schema
    settings = json.loads(
        pathlib.Path.home().joinpath(".video_maker_settings.json").read_text(encoding="utf-8")
    )
    for key in list(settings):
        if any(hint in key.lower() for hint in ("key", "token", "secret", "password")):
            settings[key] = "***" if settings[key] else ""
    return settings, get_settings_schema()


def main() -> int:
    chrome = _chrome()
    settings, schema = _settings_and_schema()

    html = render().replace(
        'var KEY = new URLSearchParams(location.search).get("key") || "";',
        'var KEY = "preview";',
    )
    # Форму раскрываем для скриншота (в приложении карточка свёрнута).
    html = html.replace(
        '<div class="card" id="settingsCard">\n  <details>',
        '<div class="card" id="settingsCard">\n  <details open>',
    )
    mock = """<script>
window.fetch = async (url, opts) => {
  const path = String(url).split('?')[0];
  let data = {};
  if (path.endsWith('/api/status')) data = {running:false,progress:0,series:'проба_40сек',stage:{name:'',hint:''},elapsed_sec:0,eta_sec:0,activity:''};
  else if (path.endsWith('/api/queue')) data = {pending:0,done:2,failed:0,paused:false,items:[{id:'a1',series:'проба_40сек',audio:'/Volumes/SSD/…/проба_40сек.m4a',status:'done',took_sec:185}]};
  else if (path.endsWith('/api/log')) data = ['[INFO] [PIPELINE] ВСЕ ФАЙЛЫ ЗАВЕРШЕНЫ УСПЕШНО (1)'];
  else if (path.endsWith('/api/settings/schema')) data = {schema: SCHEMA};
  else if (path.endsWith('/api/settings')) data = {settings: SETTINGS};
  return new Response(JSON.stringify(data), {status:200, headers:{'Content-Type':'application/json'}});
};
</script>"""
    bootstrap = (
        "<script>var SCHEMA=" + json.dumps(schema, ensure_ascii=False)
        + "; var SETTINGS=" + json.dumps(settings, ensure_ascii=False) + ";</script>"
    )
    html = html.replace("<script>\n(function(){", bootstrap + mock + "\n<script>\n(function(){")

    preview = pathlib.Path("/tmp/vm_panel_preview.html")
    preview.write_text(html, encoding="utf-8")

    shots = [
        ("/tmp/vm_panel_top.png", 1440, 1500, False),
        ("/tmp/vm_panel_laptop.png", 1024, 1700, False),
        ("/tmp/vm_panel_tablet.png", 768, 2000, False),
        ("/tmp/vm_panel_mobile.png", 430, 2400, True),
        ("/tmp/vm_panel_small.png", 360, 2400, True),
    ]
    for path, width, height, framed in shots:
        # Chrome на macOS зажимает окно (~500px), поэтому узкие экраны
        # снимаем через iframe точной ширины — там media-запросы честные.
        target = _wrap_in_frame(preview, width, height) if framed else preview
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", f"--screenshot={path}",
             f"--window-size={width},{height}", "--hide-scrollbars", "--virtual-time-budget=6000",
             target.as_uri()],
            capture_output=True, timeout=120, check=False,
        )
        print("скриншот:", path, f"({width}px{', iframe' if framed else ''})")
    return 0


def _wrap_in_frame(preview: pathlib.Path, width: int, height: int) -> pathlib.Path:
    frame = pathlib.Path("/tmp/vm_panel_frame.html")
    frame.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;background:#000}iframe{display:block;border:0}</style></head>"
        f"<body><iframe src='{preview.name}' style='width:{width}px;height:{height}px'></iframe></body></html>",
        encoding="utf-8",
    )
    return frame


if __name__ == "__main__":
    raise SystemExit(main())
