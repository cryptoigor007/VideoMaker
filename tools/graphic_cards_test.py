#!/usr/bin/env python3
"""Smoke-тест генератора графических карточек.

Генерирует три карточки (title / number / quote) во временную папку,
проверяет их через ffprobe (кодек, разрешение, fps, длительность) и
удаляет файлы. Запуск вручную:

    .venv/bin/python3 tools/graphic_cards_test.py
    .venv/bin/python3 tools/graphic_cards_test.py --resolution 1080p --duration 2
    .venv/bin/python3 tools/graphic_cards_test.py --keep

Тест также импортируется из pytest (``tests/test_graphic_cards.py``):
``generate_and_check(tmp_path, width=..., height=..., duration=...)``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:  # запуск как скрипта из корня проекта
    from tools.graphic_cards import (
        MAX_DURATION_S,
        make_card,
        make_number_card,
        make_quote_card,
    )
except ImportError:  # pragma: no cover - на случай прямого запуска из tools/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.graphic_cards import (  # type: ignore[no-redef]
        MAX_DURATION_S,
        make_card,
        make_number_card,
        make_quote_card,
    )

TITLE_TEXT = "Графическая карточка: кириллица, переносы и авторазмер"
NUMBER_VALUE = "87%"
NUMBER_LABEL = "зрителей досматривают видео до конца"
QUOTE_TEXT = "Простота — это не отсутствие сложности, а её порядок."
QUOTE_AUTHOR = "Леонардо да Винчи"

RESOLUTIONS = {
    "4k": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}


def probe_video(path: str | Path) -> dict:
    """Прочитать основные параметры видео через ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe не найден в PATH — нужен для проверки карточек")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        (
            "stream=codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,"
            "bit_rate,duration,nb_frames"
        ),
        "-show_entries",
        "format=duration,bit_rate,size",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe упал на {path}:\n{proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    if not data.get("streams"):
        raise RuntimeError(f"ffprobe не нашёл видеопоток в {path}")

    stream = data["streams"][0]
    fmt = data.get("format", {})

    def _fps(value: str) -> float:
        try:
            num, den = value.split("/")
            return float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    duration = stream.get("duration") or fmt.get("duration") or 0
    bit_rate = stream.get("bit_rate") or fmt.get("bit_rate") or 0
    return {
        "path": str(path),
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "pix_fmt": stream.get("pix_fmt"),
        "fps": round(_fps(stream.get("r_frame_rate", "0/0")), 3),
        "duration": round(float(duration), 3),
        "bit_rate": int(bit_rate),
        "nb_frames": int(stream.get("nb_frames") or 0),
        "size_bytes": int(fmt.get("size") or 0),
    }


def _check(report: dict, *, width: int, height: int, duration: float) -> None:
    problems: list[str] = []
    if report["codec"] != "h264":
        problems.append(f"кодек {report['codec']!r}, ожидался h264")
    if (report["width"], report["height"]) != (width, height):
        problems.append(
            f"разрешение {report['width']}x{report['height']}, "
            f"ожидалось {width}x{height}"
        )
    if report["pix_fmt"] != "yuv420p":
        problems.append(f"pix_fmt {report['pix_fmt']!r}, ожидался yuv420p")
    if abs(report["fps"] - 30.0) > 0.01:
        problems.append(f"fps {report['fps']}, ожидалось 30")
    tolerance = max(0.2, 3.0 / 30.0)
    if abs(report["duration"] - duration) > tolerance:
        problems.append(
            f"длительность {report['duration']} c, ожидалось ~{duration} c"
        )
    if report["size_bytes"] <= 0:
        problems.append("пустой файл")
    if problems:
        raise AssertionError(
            f"{Path(report['path']).name}: " + "; ".join(problems)
        )


def generate_and_check(
    out_dir: str | Path,
    *,
    width: int = 3840,
    height: int = 2160,
    duration: float = 2.0,
) -> list[dict]:
    """Сгенерировать 3 карточки в ``out_dir`` и проверить их ffprobe."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    duration = min(max(float(duration), 1.0), MAX_DURATION_S)

    jobs = (
        (
            "title",
            folder / "card_title.mp4",
            lambda path: make_card(
                TITLE_TEXT, duration, path, width=width, height=height
            ),
        ),
        (
            "number",
            folder / "card_number.mp4",
            lambda path: make_number_card(
                NUMBER_VALUE,
                NUMBER_LABEL,
                duration,
                path,
                width=width,
                height=height,
            ),
        ),
        (
            "quote",
            folder / "card_quote.mp4",
            lambda path: make_quote_card(
                QUOTE_TEXT,
                QUOTE_AUTHOR,
                duration,
                path,
                width=width,
                height=height,
            ),
        ),
    )

    reports: list[dict] = []
    for kind, path, make in jobs:
        make(path)
        assert path.is_file(), f"{kind}: файл не создан: {path}"
        report = probe_video(path)
        _check(report, width=width, height=height, duration=duration)
        report["kind"] = kind
        reports.append(report)
    return reports


def _format_report(report: dict) -> str:
    mbit = report["bit_rate"] / 1_000_000 if report["bit_rate"] else 0
    return (
        f"  {report['kind']:<7} {report['width']}x{report['height']} "
        f"{report['codec']} {report['pix_fmt']} "
        f"{report['fps']:.0f} fps {report['duration']:.2f} c "
        f"{mbit:.1f} Мбит/с {report['size_bytes'] / 1_000_000:.1f} МБ"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-тест graphic_cards")
    parser.add_argument(
        "--resolution",
        choices=sorted(RESOLUTIONS),
        default="4k",
        help="разрешение тестовых карточек (по умолчанию 4k)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=2.0,
        help="длительность карточки, 1..12 c (по умолчанию 2)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="не удалять сгенерированные файлы",
    )
    args = parser.parse_args(argv)

    width, height = RESOLUTIONS[args.resolution]
    folder = Path(tempfile.mkdtemp(prefix="graphic_cards_test_"))
    print(
        f"Генерирую 3 карточки {width}x{height}, {args.duration:g} c "
        f"в {folder}"
    )
    try:
        reports = generate_and_check(
            folder,
            width=width,
            height=height,
            duration=args.duration,
        )
        print("OK: ffprobe подтвердил параметры")
        for report in reports:
            print(_format_report(report))
        if args.keep:
            print(f"Файлы сохранены: {folder}")
        return 0
    except (AssertionError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        print(f"Временная папка: {folder}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
