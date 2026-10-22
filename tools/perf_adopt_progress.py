#!/usr/bin/env python3
"""Adopt существующего пула в новый _progress.json (READ-ONLY по пулу).

Сканирует `NN_*/` и лог приложения, формирует прогресс в формате run_smart_broll.
По умолчанию ничего не пишет в пул: сохраняет тестовый JSON в --out.

    python3 tools/perf_adopt_progress.py \
      --pool "/Volumes/SSD/темп/компиляция/вывод_01/broll_smart/часть_01" \
      --log  "/Volumes/SSD/темп/компиляция/вывод_01/videomaker.log" \
      --audio "/Volumes/SSD/темп/компиляция/часть_01.m4a" \
      --out /tmp/perf_progress_test.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import smart_broll  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--audio", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--per-scene", type=int, default=1)
    ap.add_argument("--orientation", default="wide")
    ap.add_argument("--slot-seconds", type=float, default=9.0)
    ap.add_argument("--max-scenes", type=int, default=0)
    args = ap.parse_args()

    audio = args.audio or None
    segments = smart_broll.load_segments(audio_path=audio)
    analysis = smart_broll.find_analysis_queries(log=lambda *a: None)
    scenes = smart_broll.plan_scenes(
        segments, slot_seconds=args.slot_seconds,
        max_scenes=(args.max_scenes or None), analysis_queries=analysis)
    settings = {"per_scene": args.per_scene, "orientation": args.orientation,
                "slot_seconds": args.slot_seconds,
                "max_scenes": args.max_scenes or None,
                "library_only": False, "verify": True, "min_score": 6}
    res = smart_broll.adopt_progress(args.pool, args.log, scenes=scenes,
                                     audio_path=audio, settings=settings,
                                     write=False, log=print)
    prog = res["progress"]
    if args.out:
        smart_broll._atomic_write_json(Path(args.out), prog)
        print(f"[ADOPT] тестовый прогресс: {args.out} (рабочий пул не изменён)")
    found = sorted((int(k) for k in (prog.get("scenes") or {}) if prog["scenes"][k].get("files")))
    nxt = next((i for i in range(res["scenes_total"]) if i not in set(found)), None)
    print(f"[ADOPT] готово {len(found)}/{res['scenes_total']} | "
          f"продолжение с {(nxt + 1) if nxt is not None else res['scenes_total']}")
    for k in found[:20]:
        rec = prog["scenes"][str(k)]
        print(f"  сцена {k + 1:02d}: {Path(rec['file']).name} | score={rec.get('score')} | "
              f"файлов {len(rec.get('files') or [])}")
    if len(found) > 20:
        print(f"  … ещё {len(found) - 20} сцен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
