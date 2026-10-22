#!/usr/bin/env python3
"""Дозаполнение сертификатов: автор + официальная страница клипа (API Pixabay по ID).

Обновляет: licenses/<клип>.json (author, page_url, source_url=страница, note)
и базу (author — для кредитов серий).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

LIC = Path("/Volumes/video/broll_library/licenses")
DB = Path.home() / "video_maker" / "broll_library.db"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def main() -> int:
    key = os.environ.get("PIXABAY_API_KEY", "")
    if not key:
        print("нет PIXABAY_API_KEY")
        return 1
    conn = sqlite3.connect(str(DB))
    files = [r[0] for r in conn.execute("SELECT file FROM clips").fetchall()]
    ok = fail = skip = 0
    for f in files:
        name = Path(f).name
        m = re.search(r"_(\d+)_pixabay\.mp4$", name)
        jp = LIC / f"{name}.json"
        if not m or not jp.exists():
            skip += 1
            continue
        pid = m.group(1)
        try:
            url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode({"key": key, "id": pid})
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            h = (d.get("hits") or [None])[0]
            if not h:
                fail += 1
                continue
            author = str(h.get("user") or "")
            page = str(h.get("pageURL") or "")
            rec = json.loads(jp.read_text(encoding="utf-8"))
            rec["author"] = author
            rec["page_url"] = page
            rec["file_url"] = rec.get("source_url", "")
            if page:
                rec["source_url"] = page
            rec["note"] = ("Источник: официальная страница клипа на Pixabay + условия лицензии. "
                           "Pixabay Content License — свободно для коммерческого использования, "
                           "атрибуция не обязательна.")
            jp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            conn.execute("UPDATE clips SET author = ? WHERE file = ?", (author, f))
            conn.commit()
            ok += 1
            if ok % 50 == 0:
                print(f"[CRED] готово {ok}/{len(files)}", flush=True)
        except Exception:
            fail += 1
        time.sleep(0.65)
    print(f"\n[CRED] ИТОГ: готово {ok} | не найдено {fail} | пропущено {skip}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
