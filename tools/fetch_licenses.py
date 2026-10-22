#!/usr/bin/env python3
"""Документы-лицензии для каждого клипа базы: страница источника → сертификат.

Для каждого клипа скачивает страницу источника (Pixabay/Pexels) и сохраняет
рядом с базой:
    <root>/licenses/<имя>.html — копия страницы источника (доказательство)
    <root>/licenses/<имя>.json — структура: файл, источник, автор, лицензия, дата
Плюс общие документы условий провайдеров (одни на всех).

Пример:
    python3 tools/fetch_licenses.py
    python3 tools/fetch_licenses.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200)), r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"ОШИБКА: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Сертификаты-документы лицензий для клипов базы")
    ap.add_argument("--root", default="/Volumes/video/broll_library")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число клипов (0 — все)")
    ap.add_argument("--refetch", action="store_true", help="перекачать даже существующие")
    args = ap.parse_args()

    from video_maker.semantic.library import (
        PIXABAY_LICENSE_URL, PEXELS_LICENSE_URL, db_path,
    )
    import sqlite3

    root = Path(args.root)
    lic_dir = root / "licenses"
    lic_dir.mkdir(parents=True, exist_ok=True)

    # общие документы условий провайдеров (один раз)
    for prov, url, name in (
        ("pixabay", PIXABAY_LICENSE_URL, "PIXABAY_ЛИЦЕНЗИЯ.html"),
        ("pexels", PEXELS_LICENSE_URL, "PEXELS_ЛИЦЕНЗИЯ.html"),
    ):
        dst = lic_dir / name
        if not dst.exists():
            st, html = _fetch(url)
            if st == 200 and len(html) > 500:
                dst.write_text(html, encoding="utf-8")
                print(f"[LIC] условия {prov}: {dst.name} ({len(html)} симв.)")
            else:
                print(f"[LIC] условия {prov}: не скачалось ({str(html)[:80]})")

    conn = sqlite3.connect(str(db_path(root)))
    rows = conn.execute(
        "SELECT file, source_url, provider, author, license, license_url FROM clips ORDER BY id"
    ).fetchall()
    conn.close()

    done = failed = skipped = 0
    for i, (f, url, prov, author, lic, lic_url) in enumerate(rows, 1):
        name = Path(str(f)).name
        out_json = lic_dir / f"{name}.json"
        if out_json.exists() and not args.refetch:
            skipped += 1
            continue
        if args.limit and done >= args.limit:
            break
        page_status, page_html = (0, "")
        if url:
            page_status, page_html = _fetch(str(url))
        payload = {
            "file": str(f),
            "clip": name,
            "provider": prov or "",
            "source_url": url or "",
            "author": author or "",
            "license": lic or "",
            "license_url": lic_url or "",
            "page_http_status": page_status,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "note": "Копия страницы источника сохранена рядом (<имя>.html) — доказательство "
                    "происхождения и свободного использования (Pixabay/Pexels License).",
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if page_html and not page_html.startswith("ОШИБКА"):
            (lic_dir / f"{name}.html").write_text(page_html, encoding="utf-8")
        ok = page_status == 200
        done += 1
        if not ok:
            failed += 1
            print(f"[LIC] {i}/{len(rows)} | {name[:40]} — страница: {page_status}", flush=True)
        elif done % 25 == 0:
            print(f"[LIC] {done}/{len(rows)} | {name[:40]} — OK", flush=True)
    print(f"\n[LIC] сертификаты: готово {done} | не скачалась страница: {failed} | уже было: {skipped}")
    print(f"[LIC] папка: {lic_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
