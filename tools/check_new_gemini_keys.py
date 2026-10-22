#!/usr/bin/env python3
"""Живая проверка Gemini-ключей: model=gemini-3.7-flash, prompt «скажи ок»."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


MODEL = "gemini-3.7-flash"

def _load_candidates() -> dict[str, str]:
    """Ключи из env или ~/.vm_gemini_keys.env (в коде секретов нет)."""
    out: dict[str, str] = {}
    for k in [x.strip() for x in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if x.strip()]:
        out[k[-6:]] = k
    if not out:
        p = Path.home() / ".vm_gemini_keys.env"
        if p.exists():
            for m in re.finditer(r"AQ\.[A-Za-z0-9_\-\.]+|AIza[A-Za-z0-9_\-]+", p.read_text(encoding="utf-8")):
                out[m.group(0)[-6:]] = m.group(0)
    return out




def main() -> int:
    from google import genai
    out = {}
    for tail, key in _load_candidates().items():
        try:
            client = genai.Client(api_key=key)
            r = client.models.generate_content(model=MODEL, contents="скажи ок")
            text = (getattr(r, "text", "") or "").strip()
            out[tail] = {"status": "OK", "reply": text[:60]}
        except Exception as e:
            msg = str(e)
            code = ""
            for marker in ("429", "403", "400", "401", "404", "500"):
                if marker in msg:
                    code = marker
                    break
            out[tail] = {"status": "FAIL", "code": code, "error": msg[:220]}
        print(f"{tail}: {json.dumps(out[tail], ensure_ascii=False)}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
