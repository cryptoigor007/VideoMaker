#!/usr/bin/env python3
"""Аудит сертификатов: каждый файл — валидный? поля заполнены? привязан к своему видео?

Проверяет все клипы базы против licenses/*.json. Возвращает код 1, если есть проблемы.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path.home() / "video_maker" / "broll_library.db"
LIC = Path("/Volumes/video/broll_library/licenses")
COLLECTION = "коллекция"


def main() -> int:
    conn = sqlite3.connect(str(DB))
    clips = [r[0] for r in conn.execute("SELECT file FROM clips").fetchall()]
    conn.close()
    ok = 0
    problems: dict[str, list[str]] = {}
    for f in clips:
        name = Path(f).name
        jp = LIC / f"{name}.json"
        issues: list[str] = []
        d = None
        if not jp.exists():
            issues.append("нет сертификата")
        else:
            try:
                d = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                issues.append("битый JSON")
        if d is not None:
            is_coll = COLLECTION in str(d.get("provider") or "")
            if str(d.get("file") or "") != f:
                issues.append("привязан к другому файлу")
            if not str(d.get("license") or "").strip():
                issues.append("пустая лицензия")
            if not str(d.get("license_url") or "").strip():
                issues.append("пустая ссылка на условия")
            if not (str(d.get("source_url") or "").strip() or str(d.get("page_url") or "").strip()):
                if not is_coll:
                    issues.append("нет ссылки-источника")
            if not str(d.get("fetched_at") or "").strip():
                issues.append("нет даты")
            if not is_coll and not str(d.get("author") or "").strip():
                issues.append("пустой автор")
        if issues:
            for i in issues:
                problems.setdefault(i, []).append(name)
        else:
            ok += 1
    print(f"клипов: {len(clips)} | сертификаты в порядке: {ok} | с проблемами: {len(clips) - ok}")
    for issue, names in sorted(problems.items(), key=lambda x: -len(x[1])):
        print(f"  ✗ {issue}: {len(names)}")
        for n in names[:4]:
            print(f"      {n[:60]}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
