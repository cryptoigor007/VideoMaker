#!/usr/bin/env python3
"""Живой smoke нового режима на РЕАЛЬНЫХ клипах/сценах (изолированная БД).

Копирует broll_library.db во временную, чистит scores/clip_perf — Vision-кэш
не маскирует замер. Рабочий пул/БД приложения не изменяются.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import library, smart_broll  # noqa: E402

PROD_DB = Path.home() / "video_maker" / "broll_library.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--scenes", type=int, default=2)
    ap.add_argument("--log-out", default="")
    args = ap.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    tmp_db = root / "perf_live.db"
    src = sqlite3.connect(str(PROD_DB))
    dst = sqlite3.connect(str(tmp_db))
    src.backup(dst)
    src.close()
    for tbl in ("scores", "clip_perf"):
        try:
            dst.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    dst.commit()
    dst.close()

    def _dbp(root_arg=None):
        return tmp_db

    library.db_path = _dbp  # type: ignore[assignment]
    lines: list[str] = []

    def log(msg, *a, **k):
        print(msg)
        lines.append(str(msg))

    t0 = time.time()
    man = smart_broll.run_smart_broll(
        audio_path=args.audio, out_root=str(root / "out"), series="perf_smoke",
        per_scene=1, max_scenes=args.scenes, use_analysis=True, verify=True,
        library_first=True, log=log)
    dt = time.time() - t0
    scored = [d for d in man.get("downloaded") or [] if isinstance(d.get("score"), int)]
    ge = sum(1 for d in scored if d["score"] >= 6)
    print(f"[SMOKE] сцен {len(man.get('scenes') or [])} | клипов {len(man.get('downloaded') or [])} | "
          f"оценено {len(scored)} | ≥6 {ge} | средний "
          f"{(sum(d['score'] for d in scored) / len(scored)) if scored else 0:.1f} | "
          f"время {dt:.1f}с")
    if args.log_out:
        Path(args.log_out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(root / "smoke_result.json").write_text(
        json.dumps({"seconds": dt, "scenes": len(man.get("scenes") or []),
                    "downloaded": len(man.get("downloaded") or []),
                    "scores": [d.get("score") for d in scored]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
