#!/usr/bin/env python3
"""CDP-агент браузеров (Opera/Chrome) — управление без разрешений macOS.

Примеры:
    python3 tools/browser_agent.py --port 9222 --tabs
    python3 tools/browser_agent.py --port 9222 --new-tab "https://chatgpt.com"
    python3 tools/browser_agent.py --port 9222 --eval "document.title" --url-filter chatgpt
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


def cdp_tabs(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return []


def cdp_new_tab(port: int, url: str) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def cdp_eval(ws_url: str, expression: str, timeout: int = 60):
    """Выполнить JS во вкладке (возвращает значение)."""
    import websocket
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    except Exception:
        return None
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expression, "returnByValue": True,
                                       "awaitPromise": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                res = msg.get("result", {}).get("result", {})
                if "value" in res:
                    return res["value"]
                return str(res.get("description") or res)[:300]
        return None
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def find_tab(port: int, url_filter: str = "") -> dict | None:
    for t in cdp_tabs(port):
        if t.get("type") == "page" and (not url_filter or url_filter in (t.get("url") or "")):
            return t
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="CDP-агент браузеров (Opera/Chrome)")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--tabs", action="store_true", help="список вкладок")
    ap.add_argument("--new-tab", default="", help="открыть вкладку с URL")
    ap.add_argument("--eval", default="", help="выполнить JS")
    ap.add_argument("--url-filter", default="", help="на какой вкладке (фильтр URL)")
    args = ap.parse_args()
    if args.tabs:
        for t in cdp_tabs(args.port):
            if t.get("type") == "page":
                print(f"  {t.get('title', '')[:40]} | {t.get('url', '')[:75]}")
        return 0
    if args.new_tab:
        t = cdp_new_tab(args.port, args.new_tab)
        print("открыл:", (t or {}).get("id", "не удалось"))
        return 0
    if args.eval:
        t = find_tab(args.port, args.url_filter)
        if not t:
            print("нет вкладки с фильтром:", args.url_filter)
            return 1
        print(cdp_eval(t["webSocketDebuggerUrl"], args.eval))
        return 0
    print("укажи --tabs / --new-tab / --eval")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
