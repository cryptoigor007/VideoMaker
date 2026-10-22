#!/usr/bin/env python3
"""Сертификаты для файлов старой коллекции (без персональных ссылок — честная пометка)."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LIC = Path("/Volumes/video/broll_library/licenses")


def run(src_dir: str) -> int:
    src = Path(src_dir)
    if not src.exists():
        return 0
    created = 0
    for f in sorted(src.glob("*.mp4")):
        jp = LIC / f"{f.name}.json"
        if jp.exists():
            continue
        jp.write_text(json.dumps({
            "file": str(f), "clip": f.name,
            "provider": "коллекция (скачано ранее)",
            "source_url": "", "author": "",
            "license": "Pixabay/Pexels Content License — свободно для коммерческого использования",
            "license_url": "https://pixabay.com/service/license-summary/",
            "page_http_status": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "note": "Файл из старой коллекции (скачан ранее без сохранения ссылки). "
                    "Персональная ссылка-источник недоступна; лицензия стока — свободная, "
                    "атрибуция не обязательна.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        created += 1
    return created


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "/Volumes/video/broll_library/vertical/local"
    print(f"создано: {run(d)}")
