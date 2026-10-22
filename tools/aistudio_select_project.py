#!/usr/bin/env python3
"""Выбрать проект по индексу в селекте AI Studio и собрать ключи."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aistudio_keys import CDP, PORT  # noqa: E402
from browser_agent import find_tab  # noqa: E402
from collect_aistudio_keys import KEY_RE, NAME_RE, close_dialog, dialog_text, row_key_button_xy, wait_closed, wait_dialog  # noqa: E402


def open_select(c: CDP) -> bool:
    if c.ev("document.querySelectorAll('.cdk-overlay-pane mat-option').length") or 0:
        return True
    for _ in range(4):
        pos = json.loads(c.ev("""(() => {
          const s = document.querySelector('mat-select');
          const r = s.getBoundingClientRect();
          return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
        })()"""))
        c.click_xy(pos["x"], pos["y"])
        for _ in range(12):
            time.sleep(0.5)
            if c.ev("document.querySelectorAll('.cdk-overlay-pane mat-option').length") or 0:
                return True
    return False


def main() -> int:
    idx = int(sys.argv[1])
    t = find_tab(PORT, "aistudio")
    c = CDP(t["webSocketDebuggerUrl"])
    try:
        # закрыть возможные оверлеи
        if dialog_text(c):
            close_dialog(c)
            wait_closed(c)
        if not open_select(c):
            print("селект не открылся")
            return 1
        opts = json.loads(c.ev("""JSON.stringify([...document.querySelectorAll('.cdk-overlay-pane mat-option')]
          .map((o,i) => ({i, text: (o.innerText||'').replace(/\\s+/g,' ').trim()})))"""))
        print("опции:", json.dumps(opts, ensure_ascii=False))
        target = opts[idx]
        hit = c.ev(f"""(() => {{
          const o = [...document.querySelectorAll('.cdk-overlay-pane mat-option')][{idx}];
          if (!o) return null;
          const r = o.getBoundingClientRect();
          return {{x: r.left + r.width/2, y: r.top + r.height/2}};
        }})()""")
        c.click_xy(hit["x"], hit["y"])
        time.sleep(2.5)
        cnt = c.ev("document.querySelectorAll('tr[mat-row]').length") or 0
        print(f"проект: {target['text']} — строк {cnt}")
        keys = []
        for i in range(int(cnt)):
            xy = row_key_button_xy(c, i)
            if not xy:
                continue
            c.click_xy(xy["x"], xy["y"])
            txt = wait_dialog(c)
            if not txt:
                print(f"  строка {i}: диалог не открылся")
                continue
            m = KEY_RE.search(txt)
            nm = NAME_RE.search(txt)
            keys.append({"tail": m.group(1)[-6:] if m else "", "full": m.group(1) if m else "",
                         "name": nm.group(1) if nm else ""})
            close_dialog(c)
            if not wait_closed(c):
                print(f"  строка {i}: диалог не закрылся, Escape")
                c.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                c.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                wait_closed(c)
            time.sleep(0.4)
        print(json.dumps(keys, ensure_ascii=False, indent=1))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
