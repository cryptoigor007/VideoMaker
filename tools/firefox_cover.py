#!/usr/bin/env python3
"""Обложки через ChatGPT в Firefox (Marionette, порт 2828) → сразу на сервер.

Отправка — настоящие события (ElementSendKeys + Enter \\uE007), как у человека.

Пример:
    python3 tools/firefox_cover.py --prompt "Сгенерируй..." --name oblozhka_ff
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path

COVERS = Path("/Volumes/video/фото")
PORT = 2828


class Marionette:
    def __init__(self, port: int = PORT):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=120)
        self.s.settimeout(120)
        self._id = 0
        self._buf = b""

    def _recv(self):
        while b":" not in self._buf:
            self._buf += self.s.recv(1 << 16)
        ln, _, rest = self._buf.partition(b":")
        need = int(ln)
        while len(rest) < need:
            rest += self.s.recv(1 << 16)
        msg, self._buf = rest[:need], rest[need:]
        return json.loads(msg)

    def cmd(self, name: str, params: dict | None = None):
        self._id += 1
        body = json.dumps([0, self._id, name, params or {}]).encode()
        self.s.sendall(str(len(body)).encode() + b":" + body)
        while True:
            msg = self._recv()
            if not isinstance(msg, list):
                continue  # приветствие/события
            if msg[0] == 1 and msg[1] == self._id:
                err = msg[2] if len(msg) > 2 else None
                res = msg[3] if len(msg) > 3 else None
                if err:
                    raise RuntimeError(str(err)[:250])
                return res

    def close(self):
        try:
            self.cmd("WebDriver:DeleteSession")
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Обложки через ChatGPT (Firefox/Marionette)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--name", default="cover_ff")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    m = Marionette()
    try:
        m.cmd("WebDriver:NewSession", {})
        m.cmd("WebDriver:Navigate", {"url": "https://chatgpt.com/"})
        time.sleep(9)

        el = m.cmd("WebDriver:FindElement",
                   {"using": "css selector", "value": 'div[contenteditable="true"]'})
        ref = el.get("value") if isinstance(el, dict) else el
        if isinstance(ref, dict):
            ref = ref.get("element-6066-11e4-a52e-4f735466cecf") or next(iter(ref.values()), None)
        elid = str(ref)
        # НАСТОЯЩИЙ ввод текста + Enter (как человек)
        m.cmd("WebDriver:ElementSendKeys", {"id": elid, "text": args.prompt})
        time.sleep(0.6)
        m.cmd("WebDriver:ElementSendKeys", {"id": elid, "text": "\ue007"})
        time.sleep(5)

        st = m.cmd("WebDriver:ExecuteScript",
                   {"script": "return location.href;", "args": []})
        url = str(st.get("value") if isinstance(st, dict) else st)
        print("url:", url[:70])
        if "/c/" not in url:
            m.cmd("WebDriver:ElementSendKeys", {"id": elid, "text": "\ue007"})
            time.sleep(5)
            st = m.cmd("WebDriver:ExecuteScript", {"script": "return location.href;", "args": []})
            url = str(st.get("value") if isinstance(st, dict) else st)
            print("повтор url:", url[:70])
        if "/c/" not in url:
            print("❌ сообщение не ушло")
            return 1

        deadline = time.time() + args.timeout
        n = 0
        while time.time() < deadline:
            time.sleep(12)
            st = m.cmd("WebDriver:ExecuteScript",
                       {"script": "return [...document.querySelectorAll('img')]"
                                  ".filter(i => i.naturalWidth > 200 && i.src).length;", "args": []})
            try:
                n = int(st.get("value") if isinstance(st, dict) else st)
            except Exception:
                n = 0
            print(f"  …картинок: {n}", flush=True)
            if n:
                break
        if not n:
            print("❌ картинка не появилась (возможно, отказ)")
            return 1

        time.sleep(3)
        st = m.cmd("WebDriver:ExecuteAsyncScript",
                   {"script": ("const cb = arguments[arguments.length-1];"
                               "const imgs = [...document.querySelectorAll('img')].filter(i => i.naturalWidth > 200 && i.src);"
                               "if (!imgs.length) { cb('NOIMG'); return; }"
                               "fetch(imgs[imgs.length-1].src).then(r => r.blob()).then(b => {"
                               "const fr = new FileReader();"
                               "fr.onload = () => cb('DATA:' + fr.result);"
                               "fr.readAsDataURL(b);}).catch(e => cb('ERR:' + e));"),
                    "args": [], "scriptTimeout": 120000})
        data = str(st.get("value") if isinstance(st, dict) else st)
        if not data.startswith("DATA:"):
            print("❌ не забрал картинку:", data[:100])
            return 1
        b64 = data[5:]
        if "," in b64[:80]:
            b64 = b64.split(",", 1)[1]
        COVERS.mkdir(parents=True, exist_ok=True)
        out = COVERS / f"{args.name}_{time.strftime('%m%d_%H%M%S')}.jpg"
        out.write_bytes(base64.b64decode(b64))
        print(f"✅ НА СЕРВЕРЕ: {out} ({out.stat().st_size // 1024} КБ)")
        return 0
    finally:
        m.close()


if __name__ == "__main__":
    raise SystemExit(main())
