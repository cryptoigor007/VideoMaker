#!/usr/bin/env python3
"""CLI «умного B-roll»: скачивает сток под сцены по смыслу (псевдо-нейролинк).

Примеры:
    # сухой прогон (покажет сцены и запросы, ничего не качает)
    python3 tools/smart_broll.py --audio "/path/проба.m4a" --dry-run

    # скачать по 2 клипа на сцену в /Volumes/video/broll_downloads/smart/<серия>
    python3 tools/smart_broll.py --audio "/path/проба.m4a" --per-scene 2

    # вертикальный сток / больше сцен / своя папка
    python3 tools/smart_broll.py --audio ... --orientation vertical --max-scenes 6 --out /tmp/sb

После скачивания укажи папку как B-roll в пульте (или в настройках broll_h)
и включи «Кинематограф» — сцены подберутся по смыслу.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Умный B-roll: сцены → запросы → скачивание (псевдо-нейролинк)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio", help="аудио (транскрипт берётся из кэша whisper приложения)")
    src.add_argument("--srt", help="SRT с субтитрами")
    src.add_argument("--segments", help="JSON с сегментами (whisper-формат)")
    ap.add_argument("--out", default="/Volumes/video/broll_downloads/smart", help="корень папок скачивания")
    ap.add_argument("--series", default=None, help="имя серии (по умолчанию — имя файла аудио)")
    ap.add_argument("--per-scene", type=int, default=2, help="сколько клипов на сцену (по умолчанию 2)")
    ap.add_argument("--orientation", choices=["wide", "vertical"], default="wide")
    ap.add_argument("--slot-seconds", type=float, default=9.0, help="целевая длина сцены, сек")
    ap.add_argument("--max-scenes", type=int, default=None, help="ограничить число сцен")
    ap.add_argument("--variant", type=int, default=0,
                    help="вариант запросов: 0 — основные фразы тем, 1+ — альтернативные (другие клипы)")
    ap.add_argument("--no-analysis", action="store_true",
                    help="не брать Gemini-запросы из кэша анализа (только словарь тем)")
    ap.add_argument("--no-verify", action="store_true",
                    help="не проверять контент клипов через Gemini Vision")
    ap.add_argument("--no-library", action="store_true",
                    help="не брать клипы из большой базы (только сток)")
    ap.add_argument("--library-only", action="store_true",
                    help="только база (описания Vision), без скачивания из стока")
    ap.add_argument("--dry-run", action="store_true", help="только показать план, без скачивания")
    args = ap.parse_args()

    from video_maker.semantic.smart_broll import run_smart_broll

    try:
        manifest = run_smart_broll(
            audio_path=args.audio, srt_path=args.srt, segments_json=args.segments,
            out_root=args.out, series=args.series, per_scene=args.per_scene,
            orientation=args.orientation, slot_seconds=args.slot_seconds,
            max_scenes=args.max_scenes, query_variant=args.variant,
            use_analysis=not args.no_analysis, verify=not args.no_verify,
            library_first=not args.no_library, library_only=args.library_only,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as e:
        print(f"Ошибка: {e}")
        return 2

    if args.dry_run:
        print("\n(сухой прогон — ничего не скачано)")
    else:
        print(f"\nПапка для пульта: {manifest['out_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
