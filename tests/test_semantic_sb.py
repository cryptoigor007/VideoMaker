"""Тесты семантического слоя («псевдо-нейролинк»): таксономия + матчер."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.semantic.matcher import (  # noqa: E402
    order_pool_for_scenes, queries_for_text, score_clip_vs_text, scene_slots, tokenize,
)
from video_maker.semantic.taxonomy import (  # noqa: E402
    expand_tokens, theme_for_token, themes_for_tokens,
)


# ── Таксономия ────────────────────────────────────────────────────────────

def test_theme_ru_inflections():
    assert theme_for_token("телефоне") == "phone_digital"
    assert theme_for_token("друзей") == "friendship"
    assert theme_for_token("одиночество") == "solitude"
    assert theme_for_token("людей") == "society_crowd"
    assert theme_for_token("переписываешься") == "phone_digital"
    assert theme_for_token("позвонить") == "phone_digital"


def test_theme_en_forms():
    assert theme_for_token("friends") == "friendship"
    assert theme_for_token("friend") == "friendship"
    assert theme_for_token("phone") == "phone_digital"
    assert theme_for_token("night") == "night_light"


def test_theme_exceptions():
    assert theme_for_token("рекламу") is None
    assert theme_for_token("одинаково") is None
    assert theme_for_token("часто") is None
    assert theme_for_token("деталь") is None


def test_theme_priority_collisions():
    # «телефон» — phone, а не health («тел»); «город» — city, а не nature («гор»)
    assert theme_for_token("телефон") == "phone_digital"
    assert theme_for_token("городе") == "city_street"
    assert theme_for_token("горы") == "nature"


def test_expand_tokens_cross_language():
    ex = expand_tokens({"одиночество"})
    assert "lonely" in ex and "alone" in ex
    ex2 = expand_tokens({"phone"})
    assert any(t.startswith("телефон") for t in ex2)


def test_queries_for_text():
    q = queries_for_text("Телефон полон контактов, а позвонить некому")
    assert q and ("phone" in q[0] or "smartphone" in q[0])
    q2 = queries_for_text("Лучший друг появлялся за неделю")
    assert q2 and "friends" in q2[0]
    assert queries_for_text("квантовый интеграл") == []


# ── Матчер ────────────────────────────────────────────────────────────────

def test_score_matching_vs_unrelated():
    text = "Телефон полон контактов, а позвонить некому"
    assert score_clip_vs_text("phone_notification_typing_01.mp4", text) > 0.4
    assert score_clip_vs_text("04_Soap_Carving_1787387644.mp4", text) == 0.0
    assert score_clip_vs_text("lonely_person_window.mp4", "друзья детства играли") == 0.0


def test_score_no_metadata():
    assert score_clip_vs_text("", "любой текст") == 0.0
    assert score_clip_vs_text("clip.mp4", "") == 0.0


def test_scene_slots_basic():
    segs = [
        {"start": 0.0, "end": 3.0, "text": "друзья детства"},
        {"start": 3.0, "end": 6.0, "text": "телефон и контакты"},
        {"start": 6.0, "end": 9.0, "text": "город ночью"},
        {"start": 9.0, "end": 12.0, "text": "свет в окнах"},
    ]
    slots = scene_slots(segs, 12.0, slot_seconds=6.0)
    assert len(slots) == 2
    assert "друзья" in slots[0]["text"] and "телефон" in slots[0]["text"]
    assert "город" in slots[1]["text"]


def test_order_pool_for_scenes_reorders_and_preserves_rest():
    segs = [
        {"start": 0.0, "end": 3.0, "text": "друзья детства играли во дворе"},
        {"start": 3.0, "end": 6.0, "text": "телефон полон контактов и сообщений"},
        {"start": 6.0, "end": 9.0, "text": "ночной город и огни"},
    ]
    pool = [
        "kinetic_sand_01.mp4",
        "night_city_lights.mp4",
        "hydraulic_press_02.mp4",
        "childhood_friends_playing.mp4",
        "phone_notification_typing.mp4",
        "soap_carving_03.mp4",
    ]
    ordered = order_pool_for_scenes(pool, segs, 9.0, slot_seconds=3.0)
    assert ordered[:3] == [
        "childhood_friends_playing.mp4",
        "phone_notification_typing.mp4",
        "night_city_lights.mp4",
    ]
    # остальные — в исходном порядке
    assert ordered[3:] == ["kinetic_sand_01.mp4", "hydraulic_press_02.mp4", "soap_carving_03.mp4"]


def test_order_pool_no_matches_keeps_order():
    pool = ["soap_carving_01.mp4", "hydraulic_press_02.mp4", "kinetic_sand_03.mp4"]
    segs = [{"start": 0.0, "end": 3.0, "text": "квантовый интеграл"}] * 3
    assert order_pool_for_scenes(pool, segs, 9.0, slot_seconds=3.0) == pool


def test_tokenize_stopwords_and_digits():
    toks = tokenize("И вот это 123 телефон, и ещё что-то")
    assert "телефон" in toks
    assert "123" not in toks and "и" not in toks and "это" not in toks
