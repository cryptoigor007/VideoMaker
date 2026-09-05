# tests/test_shorts_parity.py — r43
"""Тесты parity + optional Gemini strong (r42 каркас / r43 visual)."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from video_maker.engines.subtitles import (
    _build_shorts_parity_window,
    _group_words_with_boundaries,
    _parity_strong_lookup,
)


def _make_words(texts_and_times):
    return [
        {"text": t, "start": float(s), "end": float(e)}
        for t, s, e in texts_and_times
    ]


def test_grouping_2_3_words():
    words = _make_words([
        ("Привет", 0.0, 0.4),
        ("мир", 0.4, 0.7),
        ("это", 0.7, 0.9),
        ("тест", 0.9, 1.2),
        ("субтитров", 1.2, 1.8),
        ("для", 1.8, 2.0),
        ("шортсов", 2.0, 2.5),
        ("и", 2.5, 2.6),
        ("риллсов", 2.6, 3.1),
    ])
    groups = _group_words_with_boundaries(words)
    sizes = [len(g) for g in groups]
    assert all(1 <= s <= 4 for s in sizes)
    assert max(sizes) >= 2
    covered = sorted(i for g in groups for i in g)
    assert covered == list(range(len(words)))


def test_active_only_in_own_interval():
    words = _make_words([
        ("один", 0.0, 0.5),
        ("два", 0.5, 1.0),
        ("три", 1.0, 1.5),
    ])
    events = _build_shorts_parity_window(
        words, playres_x=1080, playres_y=1920, base_size=72, wide=False,
        analysis=None, honor_strong=False,
    )
    assert len(events) >= 3
    active_tag = "&H0000FFFF&"
    for ev in events:
        assert active_tag in ev["text"]


def test_hooks_zero_still_builds_events():
    words = _make_words([
        ("просто", 0.0, 0.4),
        ("текст", 0.4, 0.8),
        ("без", 0.8, 1.1),
        ("хуков", 1.1, 1.5),
    ])
    events = _build_shorts_parity_window(
        words, playres_x=1080, playres_y=1920, base_size=72, wide=False,
        analysis={"hooks": []}, honor_strong=True,
    )
    assert len(events) > 0
    for ev in events:
        assert ev["end"] > ev["start"]
        assert "CleanPro" in ev.get("style", "")


def test_no_lexicon_calls_in_parity():
    import inspect
    src = inspect.getsource(_build_shorts_parity_window)
    assert "highlight_lexicon" not in src
    assert "_strong_map(" not in src
    assert "from .highlight_lexicon" not in src
    assert "IDIOM_PHRASES" not in src
    assert "build_lexicon" not in src


def test_strong_empty_behaves_like_r42():
    words = _make_words([
        ("важно", 0.0, 0.5),
        ("слово", 0.5, 1.0),
    ])
    events = _build_shorts_parity_window(
        words, 1080, 1920, 72, wide=False,
        analysis={"strong_words": []}, honor_strong=True,
    )
    assert events
    for ev in events:
        assert "&H00005EFF&" not in ev["text"]
        assert "&H00FF00FF&" not in ev["text"]


def test_strong_active_gets_color_and_scale():
    words = _make_words([
        ("обычное", 0.0, 0.5),
        ("важное", 0.5, 1.0),
        ("слово", 1.0, 1.5),
    ])
    analysis = {
        "strong_words": [
            {"word": "важное", "visual_weight": "L3"},
        ]
    }
    events = _build_shorts_parity_window(
        words, 1080, 1920, 72, wide=False,
        analysis=analysis, honor_strong=True,
    )
    assert events
    orange = "&H00005EFF&"
    found = any("важное" in ev["text"] and orange in ev["text"] for ev in events)
    assert found, "strong L3 color not found on active word"


def test_strong_not_precolored():
    """До речи strong-слово в группе = base white, без neon."""
    words = _make_words([
        ("сначала", 0.0, 0.4),
        ("важное", 0.4, 0.9),
    ])
    analysis = {
        "strong_words": [{"word": "важное", "visual_weight": "L4"}]
    }
    events = _build_shorts_parity_window(
        words, 1080, 1920, 72, wide=False,
        analysis=analysis, honor_strong=True,
    )
    assert len(events) >= 2
    pink = "&H00FF00FF&"
    # event 0: active=сначала → pink не должно быть
    assert pink not in events[0]["text"], "pre-color neon before strong is active"
    # event 1: active=важное → pink есть
    assert pink in events[1]["text"]


def test_parity_strong_lookup_exact():
    analysis = {
        "strong_words": [
            {"word": "Деньги", "visual_weight": "L4"},
            {"word": "и", "visual_weight": "L3"},
            {"text": "рост", "weight": "L2"},
        ]
    }
    m = _parity_strong_lookup(analysis, honor_strong=True)
    assert "деньги" in m
    assert m["деньги"] == "L4"
    assert "рост" in m
    assert "и" not in m
    assert _parity_strong_lookup(analysis, honor_strong=False) == {}
