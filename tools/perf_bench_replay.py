#!/usr/bin/env python3
"""Замер старого/нового режима на РЕАЛЬНЫХ оценках текущего пула части 1.

Берёт блоки `[VISION]` из лога приложения (12 кандидатов на сцену, реальные
оценки Gemini), затем проигрывает:
  * старый режим — все 12 кандидатов;
  * новый режим — pre-rank по истории + бюджет 8 + ранний выход + добор до 12
    + негативный кэш (checks>=3, avg<2.5).

Время нового режима = реальное время батчей старого × (вызовы_new/вызовы_old).
Никаких сетевых вызовов, квоты не тратятся.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

_RX_DONE = re.compile(r"\[SB\] сцена (\d+): оценено (\d+) кандидатов")
_RX_CAND = re.compile(r"\[VISION\][^\n]*?[·✓✗]\s+([^\s|]+)\s*\|\s*(-?\d+)/10")
_RX_BATCH = re.compile(r"\[VISION\] батч \d+/\d+:.*?(\d+(?:[.,]\d+)?)с")
_RX_CALLS = re.compile(r"→\s*(\d+)\s*вызов")


def parse(log_path: Path) -> dict[int, dict]:
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    blocks: dict[int, dict] = {}
    prev = 0
    for i, line in enumerate(lines):
        m = _RX_DONE.search(line)
        if not m:
            continue
        sc = int(m.group(1))
        seg = lines[prev:i]
        prev = i + 1
        cands: list[tuple[str, int]] = []
        seen: set[str] = set()
        calls = 0
        secs = 0.0
        for ln in seg:
            cm = _RX_CAND.search(ln)
            if cm:
                name = cm.group(1)
                if name not in seen:
                    seen.add(name)
                    cands.append((name, int(cm.group(2))))
            bm = _RX_BATCH.search(ln)
            if bm and "video_maker.pipeline.stages" in ln:
                secs += float(bm.group(1).replace(",", "."))
                cl = _RX_CALLS.search(ln)
                calls += int(cl.group(1)) if cl else 1
        if len(cands) >= 8:
            blocks[sc] = {"scene": sc, "cands": cands, "calls": max(1, calls), "secs": secs}
    return blocks


def _exit(vals: list[int]) -> bool:
    if not vals:
        return False
    vals = sorted(vals, reverse=True)
    if vals[0] >= 8:
        return True
    return vals[0] >= 6 and len(vals) >= 2 and vals[0] - vals[1] >= 1.5


def simulate_new(cands: list[tuple[str, int]], hist: dict[str, list[int]]) -> dict:
    # pre-rank: позитивная история вверх, негативная вниз (стабильно)
    def key(item):
        name, _ = item
        hs = hist.get(name) or []
        avg = sum(hs) / len(hs) if hs else 0.0
        if hs and len(hs) >= 1 and avg >= 7:
            return (-1, -avg)
        if len(hs) >= 3 and avg < 2.5:
            return (1, avg)
        return (0, -avg)

    ordered = sorted(cands, key=key)
    # негативный кэш
    kept: list[tuple[str, int]] = []
    skipped = 0
    for name, sc in ordered:
        hs = hist.get(name) or []
        avg = sum(hs) / len(hs) if hs else 0.0
        if len(hs) >= 3 and avg < 2.5:
            skipped += 1
            continue
        kept.append((name, sc))
    calls = 0
    checked: list[int] = []
    early = False
    for target in (4, 8, 12):
        if len(kept) <= len(checked):
            break
        want = min(target, len(kept))
        if want <= len(checked):
            continue
        calls += 1
        checked.extend(sc for _, sc in kept[len(checked):want])
        if _exit(checked):
            early = True
            break
        if len(checked) >= 8 and max(checked) >= 6:
            break
        if len(checked) >= 8 and max(checked) < 6:
            continue  # добор до 12
    best = max(checked) if checked else -1
    # средний балл выбранного = лучший клип сцены
    return {"calls": calls, "checked": len(checked), "skipped": skipped,
            "best": best, "early": early, "kept": len(kept)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/Volumes/SSD/темп/компиляция/вывод_01/videomaker.log")
    ap.add_argument("--scenes", type=int, default=12)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    blocks = parse(Path(args.log))
    nums = sorted(blocks)[-args.scenes:]
    hist: dict[str, list[int]] = {}
    rows = []
    for n in nums:
        b = blocks[n]
        old_best = max(s for _, s in b["cands"])
        new = simulate_new(b["cands"], hist)
        # пополняем историю после сцены (как реальный clip_perf)
        for name, sc in b["cands"]:
            hist.setdefault(name, []).append(sc)
        rows.append({
            "scene": n, "old_calls": b["calls"], "old_checked": len(b["cands"]),
            "old_secs": b["secs"], "old_best": old_best,
            "new_calls": new["calls"], "new_checked": new["checked"],
            "new_skipped": new["skipped"], "new_best": new["best"],
            "early": new["early"],
        })
    old_calls = sum(r["old_calls"] for r in rows)
    new_calls = sum(r["new_calls"] for r in rows)
    old_secs = sum(r["old_secs"] for r in rows)
    new_secs = sum(r["new_calls"] * (r["old_secs"] / r["old_calls"])
                   if r["old_calls"] else 0 for r in rows)
    old_ge = sum(1 for r in rows if r["old_best"] >= 6)
    new_ge = sum(1 for r in rows if r["new_best"] >= 6)
    old_avg = statistics.mean(r["old_best"] for r in rows)
    new_avg = statistics.mean(r["new_best"] for r in rows)

    print(f"{'сцена':>5} | {'old вызов':>9} {'old сек':>8} {'old best':>8} | "
          f"{'new вызов':>9} {'new check':>9} {'skip':>4} {'new best':>8}")
    print("-" * 78)
    for r in rows:
        print(f"{r['scene']:>5} | {r['old_calls']:>9} {r['old_secs']:>8.1f} "
              f"{r['old_best']:>8} | {r['new_calls']:>9} {r['new_checked']:>9} "
              f"{r['new_skipped']:>4} {r['new_best']:>8}")
    print("-" * 78)
    print(f"ИТОГО сцен {len(rows)} | старый: {old_calls} вызовов, {old_secs/60:.1f} мин, "
          f"≥6 {old_ge}/{len(rows)} ({old_ge/len(rows):.0%}), ср.балл {old_avg:.2f}")
    print(f"                новый: {new_calls} вызовов, ~{new_secs/60:.1f} мин, "
          f"≥6 {new_ge}/{len(rows)} ({new_ge/len(rows):.0%}), ср.балл {new_avg:.2f}")
    if old_calls:
        print(f"ЭКОНОМИЯ: вызовы ×{old_calls/new_calls:.2f}, время ×{old_secs/max(new_secs,1e-9):.2f}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"rows": rows, "old_calls": old_calls,
                                                   "new_calls": new_calls,
                                                   "old_secs": old_secs, "new_secs": new_secs,
                                                   "old_ge": old_ge, "new_ge": new_ge,
                                                   "old_avg": old_avg, "new_avg": new_avg},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
