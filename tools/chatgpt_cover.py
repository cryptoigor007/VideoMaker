#!/usr/bin/env python3
"""Обложки через ChatGPT (Opera CDP) → сразу на сервер: /Volumes/video/фото.

НАДЁЖНАЯ отправка: CDP Input-события (настоящий ввод, как у человека):
  focus → Input.insertText → Input.dispatchKeyEvent(Enter)
+ проверка отправки (URL стал /c/...) + ожидание картинки + извлечение в base64.

Пример:
    python3 tools/chatgpt_cover.py --prompt "Сгенерируй..." --name oblozhka
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_agent import cdp_new_tab, find_tab  # noqa: E402

PORT = 9222
COVERS = Path("/Volumes/video/фото")


class CDP:
    def __init__(self, ws_url: str):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=90, suppress_origin=True)
        self._id = 0

    def cmd(self, method: str, params: dict | None = None, timeout: int = 60):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result")
        return None

    def ev(self, expr: str, timeout: int = 60):
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True, "awaitPromise": True},
                     timeout=timeout)
        return ((r or {}).get("result") or {}).get("value")

    def enter(self):
        for t in ("keyDown", "keyUp"):
            self.cmd("Input.dispatchKeyEvent",
                     {"type": t, "key": "Enter", "code": "Enter",
                      "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Обложки через ChatGPT (Opera) → на сервер")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--name", default="cover")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    t = find_tab(PORT, "chatgpt")
    if not t:
        print("открываю ChatGPT…")
        cdp_new_tab(PORT, "https://chatgpt.com")
        time.sleep(12)
        t = find_tab(PORT, "chatgpt")
    if not t:
        print("❌ нет вкладки ChatGPT в Opera")
        return 1

    c = CDP(t["webSocketDebuggerUrl"])
    try:
        # новый чат (надёжно — через навигацию)
        c.cmd("Page.navigate", {"url": "https://chatgpt.com/"})
        time.sleep(9)

        # фокус в поле
        focused = c.ev("(() => { const el = document.querySelector('div[contenteditable=\"true\"]');"
                       " if (!el) return 'нет'; el.focus(); return 'да'; })()")
        print("поле ввода:", focused)
        if focused != "да":
            return 1

        # НАСТОЯЩИЙ ввод текста + Enter (CDP Input)
        c.cmd("Input.insertText", {"text": args.prompt})
        time.sleep(0.6)
        c.enter()
        time.sleep(4)

        # проверка: ушло ли (URL должен стать /c/…)
        st = c.ev("JSON.stringify({url: location.href, field: "
                  "(document.querySelector('div[contenteditable=true]')||{innerText:''}).innerText.length})")
        print("после Enter:", st)
        if "/c/" not in str(st):
            print("…повторный Enter")
            c.enter()
            time.sleep(4)
            st = c.ev("JSON.stringify({url: location.href, field: "
                      "(document.querySelector('div[contenteditable=true]')||{innerText:''}).innerText.length})")
            print("повторно:", st)
        if "/c/" not in str(st):
            print("❌ сообщение не ушло (ChatGPT не принял ввод)")
            return 1

        # ждём картинку
        deadline = time.time() + args.timeout
        n = 0
        while time.time() < deadline:
            time.sleep(12)
            st = c.ev("JSON.stringify({imgs: [...document.querySelectorAll('img')]"
                      ".filter(i => i.naturalWidth > 200 && i.src).length, "
                      "txt: document.body.innerText.replace(/\\s+/g,' ').slice(-120)})")
            try:
                d = json.loads(st or "{}")
                n = d.get("imgs", 0)
            except Exception:
                n = 0
            print(f"  …картинок: {n}", flush=True)
            if n:
                break
        if not n:
            print("❌ картинка не появилась (возможно, ChatGPT отказался — проверь чат)")
            return 1

        time.sleep(3)
        data = c.ev("(async () => { const imgs = [...document.querySelectorAll('img')]"
                    ".filter(i => i.naturalWidth > 200 && i.src);"
                    " const resp = await fetch(imgs[imgs.length-1].src);"
                    " const blob = await resp.blob();"
                    " const b64 = await new Promise(res => { const fr = new FileReader();"
                    " fr.onload = () => res(fr.result); fr.readAsDataURL(blob); });"
                    " return 'DATA:' + b64; })()", timeout=180)
        if not data or not str(data).startswith("DATA:"):
            print("❌ не забрал картинку:", str(data)[:100])
            return 1
        b64 = str(data)[5:]
        if "," in b64[:80]:
            b64 = b64.split(",", 1)[1]
        COVERS.mkdir(parents=True, exist_ok=True)
        out = COVERS / f"{args.name}_{time.strftime('%m%d_%H%M%S')}.jpg"
        out.write_bytes(base64.b64decode(b64))
        print(f"✅ НА СЕРВЕРЕ: {out} ({out.stat().st_size // 1024} КБ)")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
