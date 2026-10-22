#!/usr/bin/env python3
"""Генерация изображений по API (Pollinations — бесплатно, без ключа).

Примеры:
    python3 tools/gen_image.py "lonely man by window at night, cinematic" -o /tmp/gen.jpg
    python3 tools/gen_image.py "empty office night" --w 1280 --h 720 --model flux

Для пайплайна: недостающие кадры (стоп-кадр + Кен Бёрнс), обложки, атмосфера.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def generate(prompt: str, out: str, *, width: int = 1280, height: int = 720,
             model: str = "flux", seed: int | None = None, timeout: int = 120) -> str | None:
    """Сгенерировать изображение → файл. Возвращает путь или None."""
    q = urllib.parse.quote(str(prompt)[:800], safe="")
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width={int(width)}&height={int(height)}&model={model}&nologo=true")
    if seed is not None:
        url += f"&seed={int(seed)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/124.0 Safari/537.36"})
        for attempt in (1, 2, 3):
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) >= 2000 and (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
                dst = Path(out)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                return str(dst)
            # бесплатный лимит: 1 картинка / 45с — ждём и пробуем снова
            try:
                txt = data.decode("utf-8", errors="ignore")
                import re as _re
                import time as _t
                m = _re.search(r"[Rr]etry after (\d+)s", txt)
                wait = int(m.group(1)) + 3 if m else 45
                _t.sleep(min(wait, 90))
            except Exception:
                pass
        return None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Генерация изображений (Pollinations API, бесплатно)")
    ap.add_argument("prompt", help="описание (английский лучше)")
    ap.add_argument("-o", "--out", default="/tmp/gen_image.jpg", help="куда сохранить")
    ap.add_argument("--w", type=int, default=1280)
    ap.add_argument("--h", type=int, default=720)
    ap.add_argument("--model", default="flux", help="flux | turbo | ...")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    p = generate(args.prompt, args.out, width=args.w, height=args.h,
                 model=args.model, seed=args.seed)
    if p:
        print(f"✅ {p} ({Path(p).stat().st_size // 1024} КБ)")
        return 0
    print("❌ не удалось сгенерировать")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
