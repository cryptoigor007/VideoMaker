"""Optional browser-level viewport smoke test for CP23.

Uses installed Chromium + Playwright if available; does not add a runtime
application dependency. Run: python tests/verify_webui_visual_cp23.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOT_DIR = ROOT / "checkpoints"
sys.path.insert(0, str(ROOT))
from video_maker.api.webui import render

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover - optional validation helper
    raise SystemExit(f"Playwright is not installed: {exc}")

STATUS = {
    "running": True, "progress": 43, "series": "Серия — психологический разбор",
    "stage": {"name": "Субтитры", "hint": "Проверка вертикальной версии"},
    "elapsed_sec": 92, "eta_sec": 125, "activity": "Обрабатывается вертикальный ролик — тестовый статус",
}
QUEUE = {
    "pending": 2, "done": 4, "failed": 1, "paused": False,
    "items": [
        {"id": "a1", "series": "Новая серия", "audio": "/Volumes/Media/long narration.m4a", "status": "pending"},
        {"id": "b2", "series": "Очень длинное название серии для проверки обрезки строки", "audio": "input.wav", "status": "failed", "error": "Ошибка кодирования тестового задания"},
    ],
}

with sync_playwright() as p:
    exe = os.environ.get("CHROMIUM_EXECUTABLE", "/usr/bin/chromium")
    browser = p.chromium.launch(headless=True, executable_path=exe, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1365, "height": 900}, device_scale_factor=1)

    # Render HTML in a hermetic browser context and mock API responses. This
    # verifies actual Chromium layout without binding a port or depending on a live API.
    html = render().replace(
        'var KEY = new URLSearchParams(location.search).get("key") || "";',
        'var KEY = "cp23-browser-test-token";'
    )
    mock_script = """<script>
      window.fetch = async (url, opts) => {
        const path = String(url).split('?')[0];
        let data = {};
        if (path.endsWith('/api/status')) data = {running:true,progress:43,series:'Серия — психологический разбор',stage:{name:'Субтитры',hint:'Проверка вертикальной версии'},elapsed_sec:92,eta_sec:125,activity:'Обрабатывается вертикальный ролик — тестовый статус'};
        else if (path.endsWith('/api/queue')) data = {pending:2,done:4,failed:1,paused:false,items:[{id:'a1',series:'Новая серия',audio:'/Volumes/Media/long narration.m4a',status:'pending'},{id:'b2',series:'Очень длинное название серии для проверки обрезки строки',audio:'input.wav',status:'failed',error:'Ошибка кодирования тестового задания'}]};
        else if (path.endsWith('/api/log')) data = ['[INFO] Stage ready','[INFO] Browser layout smoke'];
        return new Response(JSON.stringify(data), {status:200,headers:{'Content-Type':'application/json'}});
      };
    </script>"""
    html = html.replace("<script>\n(function(){", mock_script + "\n<script>\n(function(){")
    page.set_content(html, wait_until="domcontentloaded")
    page.wait_for_function("document.querySelector('#pct').textContent === '43%'")
    page.wait_for_timeout(100)
    desktop = page.evaluate("""() => {
      const b = e => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}; };
      return {vw:innerWidth, docW:document.documentElement.scrollWidth, status:b(document.querySelector('#statusCard')),
        queue:b(document.querySelectorAll('body > .card')[1]), add:b(document.querySelector('[data-act=add]')),
        start:b(document.querySelector('#btnStart')), pct:document.querySelector('#progressBar').getAttribute('aria-valuenow'),
      };
    }""")
    assert desktop["docW"] <= desktop["vw"], desktop
    assert desktop["queue"]["x"] > desktop["status"]["x"], desktop
    assert desktop["pct"] == "43", desktop
    assert desktop["add"]["right"] <= desktop["vw"], desktop
    page.screenshot(path=str(SHOT_DIR / "CP23_WEBUI_DESKTOP_1365x900.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(200)
    mobile = page.evaluate("""() => {
      const b = e => { const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}; };
      return {vw:innerWidth, docW:document.documentElement.scrollWidth, status:b(document.querySelector('#statusCard')),
        queue:b(document.querySelectorAll('body > .card')[1]), add:b(document.querySelector('[data-act=add]')),
        xButton:b(document.querySelector('.x')), start:b(document.querySelector('#btnStart'))};
    }""")
    assert mobile["docW"] <= mobile["vw"], mobile
    assert mobile["queue"]["y"] >= mobile["status"]["bottom"], mobile
    assert mobile["xButton"]["w"] >= 44 and mobile["xButton"]["h"] >= 44, mobile
    for name in ("add", "start", "xButton"):
        assert mobile[name]["right"] <= mobile["vw"] + 1, (name, mobile)
    page.screenshot(path=str(SHOT_DIR / "CP23_WEBUI_MOBILE_390x844.png"), full_page=True)
    print(json.dumps({"status":"PASS", "desktop":desktop, "mobile":mobile}, ensure_ascii=False, indent=2))
    browser.close()
