"""Тесты оркестратора «умного B-roll» (offline: сцены, запросы, сухой прогон, SRT)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic import smart_broll  # noqa: E402


SEGS = [
    {"start": 0.0, "end": 3.0, "text": "В детстве лучший друг появлялся за неделю."},
    {"start": 3.0, "end": 6.0, "text": "Сейчас люди годами сидят в одном офисе."},
    {"start": 6.0, "end": 9.0, "text": "Телефон полон контактов, а позвонить некому."},
    {"start": 9.0, "end": 12.0, "text": "Позвонить некому, только уведомления."},
    {"start": 12.0, "end": 15.0, "text": "Ночной город и свет в окнах."},
]


def test_plan_scenes_groups_and_queries():
    scenes = smart_broll.plan_scenes(SEGS, target_duration=15.0, slot_seconds=9.0)
    assert len(scenes) == 2
    assert scenes[0]["queries"] and "friends" in scenes[0]["queries"][0]
    assert any(("phone" in q or "smartphone" in q) for q in scenes[1]["queries"])
    assert scenes[0]["themes"]


def test_plan_scenes_uses_analysis_queries():
    """Gemini-запросы (broll_queries) в приоритете над словарём тем."""
    aq = [{"text": "Телефон полон контактов", "visual": "телефон и контакты",
           "queries": ["typing phone screen", "phone contacts"]}]
    scenes = smart_broll.plan_scenes(SEGS, target_duration=15.0, slot_seconds=9.0, analysis_queries=aq)
    assert any(sc["queries"] and sc["queries"][0] == "typing phone screen" for sc in scenes)


def test_run_smart_broll_dry_run(tmp_path):
    seg_file = tmp_path / "segs.json"
    seg_file.write_text(json.dumps({"segments": SEGS}), encoding="utf-8")
    manifest = smart_broll.run_smart_broll(
        segments_json=str(seg_file), out_root=str(tmp_path / "out"), series="тест",
        per_scene=1, dry_run=True, log=lambda *a, **k: None,
    )
    assert manifest["dry_run"] is True
    assert manifest["series"] == "тест"
    assert len(manifest["scenes"]) == 2
    assert manifest["downloaded"] == []
    # в сухом прогоне ничего не пишем на диск
    assert not (tmp_path / "out").exists()


def test_parse_srt(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nДрузья детства\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\nТелефон и контакты\n\n",
        encoding="utf-8",
    )
    segs = smart_broll.load_segments(srt_path=str(srt))
    assert len(segs) == 2
    assert segs[0]["text"] == "Друзья детства"
    assert abs(segs[1]["start"] - 3.0) < 0.001


def test_load_segments_from_json(tmp_path):
    seg_file = tmp_path / "s.json"
    seg_file.write_text(json.dumps(SEGS), encoding="utf-8")
    segs = smart_broll.load_segments(segments_json=str(seg_file))
    assert len(segs) == 5


def test_series_name_default():
    assert smart_broll._series_name("/a/проба_40сек.m4a", None, None) == "проба_40сек"
    assert smart_broll._series_name(None, "/b/серия.srt", None) == "серия"
    assert smart_broll._series_name(None, None, "моя") == "моя"
