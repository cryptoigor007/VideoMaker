#!/usr/bin/env python3
"""Пройти по всем проектам в фильтре AI Studio и собрать ключи каждого проекта."""

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
    opts = json.loads(c.ev("""(() => {
      const sel = document.querySelector('mat-select');
      const r = sel.getBoundingClientRect();
      return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
    })()"""))
    for _ in range(4):
        c.click_xy(opts["x"], opts["y"])
        for _ in range(10):
            time.sleep(0.5)
            if c.ev("document.querySelectorAll('.cdk-overlay-pane mat-option').length") or 0:
                return True
    return False


def main() -> int:
    t = find_tab(PORT, "aistudio")
    c = CDP(t["webSocketDebuggerUrl"])
    out = {}
    try:
        # список опций: открываем один раз
        if not open_select(c):
            raise SystemExit("не удалось открыть селект проектов")
        names = json.loads(c.ev("""(() => {
          const os = [...document.querySelectorAll('.cdk-overlay-pane mat-option')];
          return JSON.stringify(os.map((o,i) => ({i, text: (o.innerText||'').replace(/\\s+/g,' ').trim()})));
        })()"""))
        print("проекты:", json.dumps(names, ensure_ascii=False), file=sys.stderr)
        # закрыть оверлей
        c.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        c.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.8)

        for n in names:
            label = n["text"]
            # открыть селект
            if not open_select(c):
                print(f"{label}: селект не открылся", file=sys.stderr)
                continue
            hit = c.ev(f"""(() => {{
              const os = [...document.querySelectorAll('.cdk-overlay-pane mat-option')];
              const o = os[{n['i']}];
              if (!o) return null;
              const r = o.getBoundingClientRect();
              return {{x: r.left + r.width/2, y: r.top + r.height/2}};
            }})()""")
            if not hit:
                print(f"{label}: опция не найдена", file=sys.stderr)
                continue
            c.click_xy(hit["x"], hit["y"])
            time.sleep(2.5)
            cnt = c.ev("document.querySelectorAll('tr[mat-row]').length") or 0
            keys = []
            for i in range(int(cnt)):
                xy = row_key_button_xy(c, i)
                if not xy:
                    continue
                c.click_xy(xy["x"], xy["y"])
                txt = wait_dialog(c)
                if not txt:
                    continue
                m = KEY_RE.search(txt)
                nm = NAME_RE.search(txt)
                keys.append({"tail": (m.group(1)[-6:] if m else ""), "full": m.group(1) if m else "",
                             "name": nm.group(1) if nm else ""})
                close_dialog(c)
                wait_closed(c)
                time.sleep(0.3)
            out[label] = keys
            print(f"{label}: {len(keys)} ключей", file=sys.stderr)
            for k in keys:
                print(f"    {k['tail']}  {k['name']}", file=sys.stderr)

        Path("/private/var/folders/x0/5ntfc1n91rlgy269k5lygbpc0000gn/T/opencode/aistudio_by_project.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1))
        print(json.dumps({k: [x["tail"] for x in v] for k, v in out.items()}, ensure_ascii=False, indent=1))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
