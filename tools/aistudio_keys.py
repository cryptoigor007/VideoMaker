#!/usr/bin/env python3
"""Помощник для AI Studio (Opera CDP): клики по тексту, ввод, чтение DOM.

Подкоманды:
    text                        — текст страницы (сжатый)
    click "Create API key"      — клик по элементу с таким текстом (реальная мышь)
    click-aria "Copy API key"   — клик по aria-label
    eval "<js>"                 — выполнить JS
    wait "Create API key"       — ждать появления элемента с текстом
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_agent import find_tab  # noqa: E402

PORT = 9222


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

    def click_xy(self, x: float, y: float):
        for t in ("mousePressed", "mouseReleased"):
            self.cmd("Input.dispatchMouseEvent",
                     {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


FIND_JS = """(() => {
  const want = %s;
  const els = [...document.querySelectorAll('button,a,[role=button],[role=menuitem],li,div,span')];
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const w = norm(want);
  let best = null;
  for (const el of els) {
    const t = norm(el.innerText || el.textContent);
    if (!t || !t.includes(w)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const area = r.width * r.height;
    if (!best || area < best.area) best = {el, area, r};
  }
  if (!best) return null;
  const r = best.r;
  return {x: r.left + r.width / 2, y: r.top + r.height / 2,
          text: (best.el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 60)};
})()"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["text", "click", "click-aria", "eval", "wait", "shot", "type"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--filter", default="aistudio")
    args = ap.parse_args()

    t = find_tab(PORT, args.filter)
    if not t:
        print("нет вкладки", args.filter)
        return 1
    c = CDP(t["webSocketDebuggerUrl"])
    try:
        if args.cmd == "text":
            print(c.ev("document.body.innerText.replace(/\\n{2,}/g,'\\n').slice(0,4000)"))
        elif args.cmd == "eval":
            print(c.ev(args.arg))
        elif args.cmd == "wait":
            deadline = time.time() + 60
            while time.time() < deadline:
                hit = c.ev(FIND_JS % json.dumps(args.arg))
                if hit:
                    print("есть:", hit)
                    return 0
                time.sleep(2)
            print("не дождался:", args.arg)
            return 1
        elif args.cmd in ("click", "click-aria"):
            if args.cmd == "click-aria":
                js = ("(() => { const el = [...document.querySelectorAll('[aria-label]')]"
                      ".find(e => e.getAttribute('aria-label') === %s);"
                      " if (!el) return null; const r = el.getBoundingClientRect();"
                      " return {x: r.left + r.width/2, y: r.top + r.height/2}; })()"
                      % json.dumps(args.arg))
            else:
                js = FIND_JS % json.dumps(args.arg)
            hit = c.ev(js)
            print("нашёл:", hit)
            if not hit:
                return 1
            c.click_xy(hit["x"], hit["y"])
            print("кликнул")
        elif args.cmd == "type":
            # arg = "aria|текст" — фокус на input по aria-label и ввод текста
            aria, _, text = args.arg.partition("|")
            ok = c.ev("(() => { const el = [...document.querySelectorAll('input')]"
                      ".filter(e => e.offsetParent !== null)"
                      ".find(e => (e.getAttribute('aria-label')||'') === %s);"
                      " if (!el) return false; el.focus(); el.select(); return true; })()"
                      % json.dumps(aria))
            print("фокус:", ok)
            if not ok:
                return 1
            # выделить всё (Cmd+A) и заменить
            c.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                             "modifiers": 4, "windowsVirtualKeyCode": 65})
            c.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                             "modifiers": 4, "windowsVirtualKeyCode": 65})
            c.cmd("Input.insertText", {"text": text})
            print("ввёл:", c.ev("(() => { const el = [...document.querySelectorAll('input')]"
                                ".filter(e => e.offsetParent !== null)"
                                ".find(e => (e.getAttribute('aria-label')||'') === %s);"
                                " return el ? el.value : 'нет'; })()" % json.dumps(aria)))
        elif args.cmd == "shot":
            import base64
            r = c.cmd("Page.captureScreenshot", {"format": "png"})
            data = (r or {}).get("data", "")
            out = Path("/private/var/folders/x0/5ntfc1n91rlgy269k5lygbpc0000gn/T/opencode/shot.png")
            out.write_bytes(base64.b64decode(data))
            print("скриншот:", out, len(data) // 1024, "КБ")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
