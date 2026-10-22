#!/usr/bin/env python3
"""Собрать ПОЛНЫЕ значения существующих ключей из Google AI Studio (Opera CDP).

Механика: клик по кнопке ключа в строке -> открывается диалог "API key details"
с полным значением ключа. Читаем, закрываем, идём дальше.

Использование:
    python3 tools/collect_aistudio_keys.py            # собрать все строки текущей страницы
    python3 tools/collect_aistudio_keys.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aistudio_keys import CDP, PORT  # noqa: E402
from browser_agent import find_tab  # noqa: E402

KEY_RE = re.compile(r"API Key\s+((?:AQ\.|AIza)[A-Za-z0-9_\-\.]+)")
NAME_RE = re.compile(r"Name\s+(\S.*?)\s+content_copy")
PROJ_RE = re.compile(r"Project name\s+(\S+)\s+content_copy")
NUM_RE = re.compile(r"Project number\s+(\d+)")


def connect() -> CDP:
    t = find_tab(PORT, "aistudio")
    if not t:
        raise SystemExit("нет вкладки aistudio")
    return CDP(t["webSocketDebuggerUrl"])


def row_key_button_xy(c: CDP, i: int):
    return c.ev(f"""(() => {{
      const rows = [...document.querySelectorAll('tr[mat-row]')];
      const r = rows[{i}];
      if (!r) return null;
      const cell = r.querySelector('ms-api-key-key-string button');
      if (!cell) return null;
      const rect = cell.getBoundingClientRect();
      if (rect.width < 2) return null;
      return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2,
              shown: (cell.innerText||'').trim()}};
    }})()""")


def dialog_text(c: CDP):
    return c.ev("""(() => {
      const ov = document.querySelector('.cdk-overlay-pane');
      return ov ? (ov.innerText || '').replace(/\\s+/g,' ') : null;
    })()""")


def close_dialog(c: CDP) -> bool:
    info = c.ev("""(() => {
      const ov = document.querySelector('.cdk-overlay-pane');
      if (!ov) return 'нет диалога';
      const btn = [...ov.querySelectorAll('button')].find(b =>
        (b.getAttribute('aria-label')||'').toLowerCase() === 'close' ||
        (b.innerText||'').trim().toLowerCase() === 'close');
      if (!btn) return null;
      const r = btn.getBoundingClientRect();
      return {x: r.left + r.width/2, y: r.top + r.height/2};
    })()""")
    if isinstance(info, dict):
        c.click_xy(info["x"], info["y"])
        return True
    return False


def wait_dialog(c: CDP, timeout: float = 12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = dialog_text(c)
        if t:
            return t
        time.sleep(0.4)
    return None


def wait_closed(c: CDP, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not dialog_text(c):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    c = connect()
    results: list[dict] = []
    try:
        # если открыт старый диалог — закрыть
        if dialog_text(c):
            close_dialog(c)
            wait_closed(c)

        n = c.ev("document.querySelectorAll('tr[mat-row]').length") or 0
        print(f"строк в таблице: {n}", file=sys.stderr)
        for i in range(int(n)):
            xy = row_key_button_xy(c, i)
            if not xy:
                print(f"строка {i}: кнопка не найдена", file=sys.stderr)
                continue
            c.click_xy(xy["x"], xy["y"])
            txt = wait_dialog(c)
            if not txt:
                print(f"строка {i}: диалог не открылся", file=sys.stderr)
                continue
            m = KEY_RE.search(txt)
            full = m.group(1) if m else ""
            nm = NAME_RE.search(txt)
            pr = PROJ_RE.search(txt)
            pn = NUM_RE.search(txt)
            rec = {
                "row": i,
                "shown": xy["shown"],
                "tail": full[-6:] if full else "",
                "full": full,
                "name": nm.group(1) if nm else "",
                "project": pr.group(1) if pr else "",
                "project_number": pn.group(1) if pn else "",
            }
            results.append(rec)
            print(f"строка {i}: {xy['shown']} -> {rec['tail']} ({rec['name']})", file=sys.stderr)
            close_dialog(c)
            if not wait_closed(c):
                print(f"строка {i}: диалог не закрылся!", file=sys.stderr)
                break
            time.sleep(0.4)

        out = json.dumps(results, ensure_ascii=False, indent=1)
        if args.json:
            Path(args.json).write_text(out)
            print(f"записано: {args.json}", file=sys.stderr)
        print(out)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
