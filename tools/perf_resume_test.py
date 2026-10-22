#!/usr/bin/env python3
"""Проверка РЕЗЮМЕ пула: имитация сбоя после N сцен и продолжение.

Запускается дважды с общим --root:
  1) --phase first --kill-after 2  → SystemExit внутри 3-й сцены;
  2) --phase second               → должен продолжить с 3-й, готовые 1-2 не трогать.

`_scene_pool_candidates` подменён моком (без сети/квот), но весь путь
progress/манифест/симлинков — настоящий.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import smart_broll  # noqa: E402

PREVIEWS = sorted((Path.home() / "video_maker" / "lib_previews").glob("lib_*.mp4"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--phase", choices=("first", "second"), required=True)
    ap.add_argument("--calls", required=True)
    ap.add_argument("--kill-after", type=int, default=0)
    args = ap.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    audio = root / "part.m4a"
    if not audio.exists():
        audio.write_bytes(b"fake audio for resume test")
    seg_file = root / "segs.json"
    segs = [{"start": i * 3.0, "end": i * 3.0 + 3.0, "text": f"тестовая сцена {i + 1}."}
            for i in range(4)]
    seg_file.write_text(json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")

    calls = Path(args.calls)

    def fake_pool(sc, **kw):
        idx = int(sc.get("idx", 0))
        with open(calls, "a", encoding="utf-8") as f:
            f.write(f"{args.phase} vision scene {idx + 1}\n")
        if args.kill_after and idx >= args.kill_after:
            raise SystemExit(99)  # имитация kill/сбоя посреди 3-й сцены
        f = PREVIEWS[idx % len(PREVIEWS)]
        return [{"kind": "lib", "file": str(f), "query": "test query",
                 "provider": "pixabay", "duration": 5, "width": 640, "height": 360,
                 "score": 8, "what": "mock", "verified": True}]

    smart_broll._scene_pool_candidates = fake_pool
    smart_broll._load_gemini_keys = lambda: ("fake-key", [])

    def log(msg, *a, **k):
        print(msg)

    man = smart_broll.run_smart_broll(
        audio_path=str(audio), segments_json=str(seg_file), out_root=str(root / "out"),
        series="resume_test", per_scene=1, max_scenes=4, use_analysis=False,
        verify=True, library_first=True, log=log)
    print(f"[TEST] phase={args.phase} manifest_scenes={len(man.get('scenes') or [])} "
          f"downloaded={len(man.get('downloaded') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
