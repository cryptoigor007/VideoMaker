"""Тесты генератора графических карточек (B2).

Тяжёлый тест (3 рендера + ffprobe) идёт на 1080p и короткой длительности;
4K проверяется отдельно скриптом ``tools/graphic_cards_test.py``.
"""

from __future__ import annotations

import pytest

from tools.graphic_cards import (
    MAX_DURATION_S,
    _fit_lines,
    _missing_filters,
    _normalise_duration,
    _text_width,
    find_ffmpeg,
    make_card,
    make_number_card,
    make_quote_card,
)
from tools.graphic_cards_test import generate_and_check, probe_video


def test_ffmpeg_with_drawtext_available():
    binary = find_ffmpeg()
    assert binary
    assert not _missing_filters(binary), _missing_filters(binary)


def test_three_card_types_ffprobe(tmp_path):
    """Title/number/quote: h264, 1080p, 30 fps, длительность, yuv420p."""
    reports = generate_and_check(
        tmp_path, width=1920, height=1080, duration=1.5
    )
    assert [report["kind"] for report in reports] == ["title", "number", "quote"]
    for report in reports:
        assert report["codec"] == "h264"
        assert (report["width"], report["height"]) == (1920, 1080)
        assert report["pix_fmt"] == "yuv420p"
        assert report["fps"] == pytest.approx(30.0, abs=0.01)
        assert report["duration"] == pytest.approx(1.5, abs=0.2)
        assert report["size_bytes"] > 100_000
        assert not report["path"].endswith(".tmp.mp4")


def test_4k_short_card_and_clamping(tmp_path):
    """4K-рендер и обрезка длительности до 12 c (без 12-секундного файла)."""
    assert _normalise_duration(30) == MAX_DURATION_S
    assert _normalise_duration(3.5) == 3.5
    with pytest.raises(ValueError):
        _normalise_duration(0)

    path = make_card(
        "4K карточка", 1.0, tmp_path / "card_4k.mp4", width=3840, height=2160
    )
    report = probe_video(path)
    assert (report["width"], report["height"]) == (3840, 2160)
    assert report["codec"] == "h264"
    assert report["duration"] == pytest.approx(1.0, abs=0.2)


def test_balanced_wrapping_has_no_widow():
    text = "Проверка 4K: кириллица и качество текста"
    max_width = 1920 * 0.82
    lines, size = _fit_lines(
        text,
        max_width=max_width,
        max_height=1080 * 0.56,
        max_size=int(1080 * 0.14),
        min_size=28,
        max_lines=6,
    )
    assert len(lines) >= 2
    assert len(lines[-1].split()) >= 2, f"сирота в последней строке: {lines}"
    for line in lines:
        assert _text_width(line, size) <= max_width


def test_invalid_arguments(tmp_path):
    with pytest.raises(ValueError):
        make_card("", 2.0, tmp_path / "empty.mp4")
    with pytest.raises(ValueError):
        make_card("Текст", 2.0, tmp_path / "style.mp4", style="neon")
    with pytest.raises(ValueError):
        make_card("Текст", 2.0, tmp_path / "odd.mp4", width=1921)
    with pytest.raises(ValueError):
        make_number_card("1", "подпись", -1, tmp_path / "neg.mp4")
    with pytest.raises(ValueError):
        make_quote_card("Цитата", "Автор", 2.0, tmp_path / "x.mp4", style="")  # type: ignore[arg-type]
