#!/usr/bin/env python3
"""Отметить использование клипов серии в базе (из манифеста).

    python3 tools/log_usage.py "/Volumes/SSD/темп/серия10/smart_broll_manifest.json"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_maker.semantic.library import init_db, log_usage, write_clip_credits  # noqa: E402

init_db()  # создаёт таблицу usage, если её ещё нет


def main() -> int:
    man = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    pairs: list[tuple[int, str]] = []
    for sc in man.get("scenes") or []:
        for f in sc.get("files") or []:
            pairs.append((int(sc.get("idx", 0)), f))
    n = log_usage(pairs, man.get("series") or "серия")
    print(f"отмечено использований: {n} (серия: {man.get('series')})")
    files = [f for _, f in pairs]
    cr = write_clip_credits(files, str(Path(sys.argv[1]).resolve().parent), log=print)
    print(f"сертификаты серии: {cr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
