#!/usr/bin/env python3
"""Ремикс фоновой музыки в готовых сериях — без пересборки видео.

Меняется ТОЛЬКО звук: видео копируется (-c:v copy), музыка из настроенной
папки программы микшируется с голосом тем же фильтром, что в пайплайне
(приглушение под голос, громкость -14 LUFS).

Источники без музыки:   <серия>/_tmp/final_16x9.mp4
                        <серия>/shorts/short_NNN/<имя>_short_NNN.mp4
Приёмники (заменяются): <серия>/youtube/wide/<имя>_final_16x9.mp4
                        <серия>/youtube/shorts/short_NNN/<имя>_short_NNN.mp4

Мелодии подбираются политикой программы (pick_bgm_track): для каждого файла
свой трек, последние использованные не повторяются, история — общая с программой.

Пример:
  .venv/bin/python tools/remix_bgm.py "/Volumes/SSD/untitled folder/12" ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from video_maker.engines.audio import mix_bgm, probe_duration          # noqa: E402
from video_maker.engines.bgm_policy import list_bgm_tracks, pick_bgm_track  # noqa: E402

SETTINGS_FILE = os.path.expanduser("~/.video_maker_settings.json")


def _real_mp4s(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.glob("*.mp4")
        if not p.name.startswith(".") and not p.name.startswith("._")
    )


def _settings_bgm_folder() -> str:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as stream:
            data = json.load(stream)
        return str(data.get("bgm_youtube_folder") or data.get("bgm_folder") or "")
    except (OSError, ValueError):
        return ""


def build_pairs(series_dir: Path) -> list[tuple[str, Path, Path]]:
    """(вид, источник без музыки, приёмник для замены)"""
    pairs: list[tuple[str, Path, Path]] = []
    wide_src = series_dir / "_tmp" / "final_16x9.mp4"
    wide_files = [
        p for p in _real_mp4s(series_dir / "youtube" / "wide")
        if p.name.endswith("_final_16x9.mp4")
    ]
    if wide_src.is_file() and len(wide_files) == 1:
        pairs.append(("wide", wide_src, wide_files[0]))
    shorts_root = series_dir / "youtube" / "shorts"
    for short_dir in sorted(p for p in shorts_root.glob("short_*") if p.is_dir()):
        dsts = _real_mp4s(short_dir)
        if len(dsts) != 1:
            continue
        src = series_dir / "shorts" / short_dir.name / dsts[0].name
        if src.is_file():
            pairs.append((short_dir.name, src, dsts[0]))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Ремикс BGM в готовых сериях (видео не пересобирается)")
    parser.add_argument("series", nargs="+", help="папки серий (например: '12' '14 обновлённый')")
    parser.add_argument("--bgm-folder", default="", help="папка с музыкой (по умолчанию — из настроек программы)")
    parser.add_argument("--target-lufs", type=float, default=-14.0)
    parser.add_argument("--dry-run", action="store_true", help="только показать план, ничего не менять")
    parser.add_argument("--only", choices=["all", "wide", "shorts"], default="all")
    args = parser.parse_args()

    bgm_folder = args.bgm_folder.strip() or _settings_bgm_folder()
    if not bgm_folder or not os.path.isdir(bgm_folder):
        print(f"[ОШИБКА] папка музыки недоступна: {bgm_folder!r}")
        return 2
    tracks = [os.path.basename(t) for t in list_bgm_tracks(bgm_folder)]
    print(f"[BGM] папка: {bgm_folder}")
    print(f"[BGM] треков: {len(tracks)}")
    for name in tracks:
        print(f"   - {name}")
    if not tracks:
        return 2

    report: list[dict] = []
    failures = 0
    for series_arg in args.series:
        series_dir = Path(series_arg)
        if not series_dir.is_dir():
            print(f"[ОШИБКА] серия не найдена: {series_dir}")
            failures += 1
            continue
        pairs = build_pairs(series_dir)
        if args.only == "wide":
            pairs = [p for p in pairs if p[0] == "wide"]
        elif args.only == "shorts":
            pairs = [p for p in pairs if p[0] != "wide"]
        print(f"\n=== [{series_dir.name}] файлов к ремиксу: {len(pairs)} ===")
        for kind, src, dst in pairs:
            seed = f"remix|{series_dir.name}|{kind}"
            track = pick_bgm_track(bgm_folder, seed_key=seed)
            src_dur = probe_duration(str(src))
            dst_dur = probe_duration(str(dst))
            entry = {
                "series": series_dir.name,
                "kind": kind,
                "source": str(src),
                "target": str(dst),
                "track": os.path.basename(track),
                "source_duration": round(src_dur, 3),
                "target_duration_before": round(dst_dur, 3),
            }
            if abs(src_dur - dst_dur) > 0.3:
                entry["status"] = "skipped_duration_mismatch"
                failures += 1
                print(f"  ПРОПУСК {kind}: длительности расходятся "
                      f"({src_dur:.2f} vs {dst_dur:.2f}) — не трогаю")
                report.append(entry)
                continue
            if args.dry_run:
                entry["status"] = "planned"
                print(f"  ПЛАН  {kind:10s} → {os.path.basename(track)}  "
                      f"({dst_dur:.1f} c)")
                report.append(entry)
                continue
            t0 = time.time()
            try:
                mix_bgm(str(src), bgm_folder, str(dst), log_fn=None,
                        loudnorm=True, target_lufs=args.target_lufs, seed_key=seed)
            except Exception as exc:  # noqa: BLE001
                entry["status"] = f"failed: {type(exc).__name__}: {exc}"
                failures += 1
                print(f"  ОШИБКА {kind}: {exc}")
                report.append(entry)
                continue
            new_dur = probe_duration(str(dst))
            entry.update({
                "status": "remixed",
                "target_duration_after": round(new_dur, 3),
                "seconds": round(time.time() - t0, 1),
                "size_bytes": dst.stat().st_size,
            })
            print(f"  OK    {kind:10s} → {os.path.basename(track)}  "
                  f"{new_dur:.1f} c за {entry['seconds']} с")
            report.append(entry)

    out_dir = Path(args.series[0]).resolve().parent
    report_path = out_dir / f"_bgm_remix_report_{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nОтчёт: {report_path}")
    except OSError as exc:
        print(f"[WARN] не удалось записать отчёт: {exc}")
    used = {}
    for item in report:
        if item.get("status") in ("remixed", "planned"):
            used.setdefault(item["track"], 0)
            used[item["track"]] += 1
    print("Распределение треков:", ", ".join(f"{k} ×{v}" for k, v in sorted(used.items())))
    print(f"Итог: {len([r for r in report if r.get('status') in ('remixed', 'planned')])} файлов, "
          f"проблем: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
