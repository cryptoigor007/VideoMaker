#!/usr/bin/env python3
"""Проверка сцен серии: кадры по секундам — сходится ли клип с текстом сцены.

Многопоточная (6 воркеров). Скор — против visual («что показать»), не нарратива.
Итог: ✓ (≥ порога) / ✗ / ? (нет оценки).

    python3 tools/verify_scenes.py "/Volumes/SSD/темп/серия10_v3/smart_broll_manifest.json"
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_maker.semantic.smart_broll import _load_gemini_keys  # noqa: E402
from video_maker.semantic.verify import verify_pool  # noqa: E402


def _verify_one(sc: dict, scenes: list[dict], args, key: str, keys: list[str]) -> dict:
    text = str(sc.get("text") or "")[:300]
    if not text.strip():
        i0 = scenes.index(sc)
        for j in sorted(range(len(scenes)), key=lambda x: abs(x - i0)):
            t = str(scenes[j].get("text") or "").strip()
            if t:
                text = t[:300]
                break
    files = [f for f in (sc.get("files") or []) if Path(f).exists()]
    rng = f"{float(sc.get('start', 0)):.1f}–{float(sc.get('end', 0)):.1f}с"
    if not files:
        return {"idx": sc.get("idx", 0), "mark": "✗", "best": -1,
                "line": f"✗ сцена {sc['idx'] + 1} [{rng}]: нет клипов"}
    _vis = str(sc.get("visual") or "").strip()
    _qe = " ".join(str(q) for q in (sc.get("queries") or []) if str(q).strip())
    _q = ((_vis or text) + (" | " + _qe if _qe else ""))[:400]
    entries = [{"file": f, "query": _q} for f in files]
    try:
        verify_pool(entries, key=key, keys=keys, min_score=0, delete_bad=False, log=lambda *a: None)
        for e in entries:  # безответные — ретрай один раз
            if isinstance(e.get("score"), int) and e.get("score") < 0:
                e.pop("verified", None)
        if any(not e.get("verified") for e in entries):
            verify_pool(entries, key=key, keys=keys, min_score=0, delete_bad=False, log=lambda *a: None)
    except Exception as e:
        return {"idx": sc.get("idx", 0), "mark": "?", "best": -1,
                "line": f"? сцена {sc['idx'] + 1} [{rng}]: сбой VLM ({str(e)[:40]})"}
    best = max((e.get("score") if isinstance(e.get("score"), int) else -1) for e in entries)
    if best < 0:
        return {"idx": sc.get("idx", 0), "mark": "?", "best": -1,
                "line": f"? сцена {sc['idx'] + 1} [{rng}]: нет оценки — «{text[:40]}»"}
    mark = "✓" if best >= args.min_score else "✗"
    what = next((str(e.get("what") or "")[:60] for e in entries if e.get("score") == best), "")
    return {"idx": sc.get("idx", 0), "mark": mark, "best": best,
            "line": f"{mark} сцена {sc['idx'] + 1} [{rng}]: {best}/10 — «{text[:45]}» | на кадре: {what}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    scenes = man.get("scenes") or []
    key, _keys = _load_gemini_keys()
    keys = [k for k in (_keys or []) if k]
    print(f"сцен: {len(scenes)} | порог: {args.min_score}/10 | потоков: {args.workers}")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for r in ex.map(lambda sc: _verify_one(sc, scenes, args, key, keys), scenes):
            results.append(r)
    results.sort(key=lambda r: r["idx"])
    ok = sum(1 for r in results if r["mark"] == "✓")
    bad = sum(1 for r in results if r["mark"] == "✗")
    q = sum(1 for r in results if r["mark"] == "?")
    print("\n".join(r["line"] for r in results))
    print(f"\nИТОГ: ✓ {ok} | ✗ {bad} | ? {q} из {len(scenes)}")
    out = Path(args.manifest).parent / "проверка_сцен.txt"
    out.write_text("\n".join(r["line"] for r in results)
                   + f"\n\nИТОГ: ✓ {ok} | ✗ {bad} | ? {q} из {len(scenes)}\n", encoding="utf-8")
    print("отчёт:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
