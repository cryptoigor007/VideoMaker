"""A2: классы сцен (literal/associative/abstract + confidence) и их учёт.

Проверяем четыре стыка:
1. парсинг ответа Gemini (класс/confidence + дефолты 0.5/literal);
2. plan_scenes: низкая confidence → веер literal+associative, abstract → атмосфера;
3. fallback-политика учитывает класс сцены;
4. baseline пишет класс/confidence в scenes.jsonl и распределение в run_summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_maker.engines import analysis  # noqa: E402
from video_maker.semantic import smart_broll  # noqa: E402
from video_maker.semantic.baseline import SceneBaseline  # noqa: E402
from video_maker.semantic.scene_policy import (  # noqa: E402
    select_scene,
    scene_class_of,
    scene_confidence_of,
)


# ── 1. Парсинг ────────────────────────────────────────────────────────────

def test_parse_scenes_json_class_confidence_and_defaults():
    raw = """```json
    [
      {"text": "Телефон полон контактов", "visual": "рука листает контакты",
       "queries": ["hand scrolling phone contacts dark"],
       "scene_class": "associative", "confidence": 0.42},
      {"text": "Офис", "visual": "люди за мониторами", "queries": ["office workers"],
       "scene_class": "moody", "confidence": 85},
      {"text": "Обобщение", "queries": ["abstract background"],
       "scene_class": "ABSTRACT", "confidence": "0,7"},
      {"text": "Без полей", "queries": ["city street"]}
    ]
    ```"""
    scenes = analysis._parse_scenes_json(raw)
    assert len(scenes) == 4
    assert scenes[0]["scene_class"] == "associative"
    assert scenes[0]["confidence"] == pytest.approx(0.42)
    # неизвестный класс → literal, проценты модели → 0..1
    assert scenes[1]["scene_class"] == "literal"
    assert scenes[1]["confidence"] == pytest.approx(0.85)
    assert scenes[2]["scene_class"] == "abstract"
    assert scenes[2]["confidence"] == pytest.approx(0.7)
    # модель не дала поля → дефолт literal / 0.5
    assert scenes[3]["scene_class"] == "literal"
    assert scenes[3]["confidence"] == pytest.approx(0.5)


def test_parse_scenes_json_confidence_bounds():
    raw = json.dumps([
        {"text": "a", "queries": ["q1"], "confidence": -1},
        {"text": "b", "queries": ["q2"], "confidence": 120},
        {"text": "c", "queries": ["q3"], "confidence": "мусор"},
        {"text": "d", "queries": ["q4"], "confidence": True},
    ])
    scenes = analysis._parse_scenes_json(raw)
    assert [s["confidence"] for s in scenes] == [0.0, 1.0, 0.5, 0.5]


def test_norm_broll_scene_class_defaults():
    out = analysis._norm_broll([
        {"text": "a", "queries": ["q1"], "scene_class": "abstract", "confidence": 0.2},
        {"text": "b", "queries": ["q2"]},
        {"text": "c", "queries": ["q3"], "scene_class": "associative", "confidence": 0.9},
    ])
    assert [s["scene_class"] for s in out] == ["abstract", "literal", "associative"]
    assert [s["confidence"] for s in out] == [0.2, 0.5, 0.9]


# ── 2. plan_scenes: класс/confidence и веер запросов ──────────────────────

_SEG = [{"start": 0.0, "end": 3.0,
         "text": "Телефон полон контактов, а позвонить некому."}]


def _aq(**over):
    item = {
        "text": "Телефон полон контактов",
        "visual": "рука листает список контактов в темноте",
        "queries": ["hand scrolling phone contacts dark", "person alone smartphone night"],
        "emotions": ["sad", "lonely"],
        "mood": "melancholic",
    }
    item.update(over)
    return [item]


def test_plan_scenes_low_confidence_fans_out_literal_and_assoc():
    scene = smart_broll.plan_scenes(
        _SEG, analysis_queries=_aq(scene_class="associative", confidence=0.4))[0]
    assert scene["scene_class"] == "associative"
    assert scene["confidence"] == pytest.approx(0.4)
    # буквальный путь остаётся первым и не подменяется
    assert scene["queries"][:2] == [
        "hand scrolling phone contacts dark", "person alone smartphone night"]
    assoc = scene["assoc_queries"]
    assert assoc, "при confidence < 0.7 должен быть второй (ассоциативный) путь"
    assert "melancholic atmospheric scene" in assoc
    assert "sad mood cinematic" in assoc
    assert not (set(assoc) & set(scene["queries"]))


def test_plan_scenes_high_confidence_stays_literal():
    scene = smart_broll.plan_scenes(
        _SEG, analysis_queries=_aq(scene_class="literal", confidence=0.9))[0]
    assert scene["scene_class"] == "literal"
    assert scene["confidence"] == pytest.approx(0.9)
    assert scene["assoc_queries"] == []


def test_plan_scenes_abstract_prefers_atmosphere():
    scene = smart_broll.plan_scenes(
        _SEG, analysis_queries=_aq(scene_class="abstract", confidence=0.95))[0]
    assert scene["scene_class"] == "abstract"
    # атмосфера — первым ассоциативным запросом, буквальные не отменяются
    assert scene["assoc_queries"][0] == "melancholic atmosphere cinematic"
    assert scene["queries"], "literal-кандидаты не должны блокироваться"

    # Без mood атмосферу строим от первой эмоции, а не от выдуманного «calm».
    scene2 = smart_broll.plan_scenes(
        _SEG, analysis_queries=_aq(scene_class="abstract", confidence=0.95, mood=""))[0]
    assert scene2["assoc_queries"][0] == "sad atmosphere cinematic"


def test_plan_scenes_missing_fields_default_and_fan():
    scene = smart_broll.plan_scenes(_SEG, analysis_queries=_aq())[0]
    assert scene["scene_class"] == "literal"
    assert scene["confidence"] == pytest.approx(0.5)
    # дефолт 0.5 < 0.7 — веер из двух путей всё равно строится
    assert scene["assoc_queries"]


def test_scene_pool_candidates_uses_both_paths(monkeypatch):
    import video_maker.semantic.library as library
    import video_maker.semantic.stock as stock

    calls: dict = {}

    def fake_lib(queries, **kwargs):
        calls["lib"] = list(queries)
        return []

    def fake_stock(queries, **kwargs):
        calls["stock"] = list(queries)
        calls["max_queries"] = kwargs.get("max_queries")
        return []

    monkeypatch.setattr(library, "search_library_multi", fake_lib)
    monkeypatch.setattr(stock, "search_stock_multi", fake_stock)

    def _scene(**over):
        sc = {
            "idx": 0, "start": 0.0, "end": 3.0, "text": "Телефон и контакты",
            "visual": "рука листает телефон", "queries": ["literal one", "literal two"],
            "assoc_queries": ["mood atmospheric scene"], "emotions": [],
            "mood": "calm", "scene_class": "associative", "confidence": 0.4,
        }
        sc.update(over)
        return sc

    smart_broll._scene_pool_candidates(
        _scene(), orientation="wide", per_scene=1, log=lambda *_: None,
        used_urls=set(), verify=False)
    assert calls["lib"][:2] == ["literal one", "literal two"]
    assert "mood atmospheric scene" in calls["lib"]
    assert calls["stock"] == calls["lib"]
    assert calls["max_queries"] == 3

    # Высокая confidence + literal: второй путь не подмешивается.
    smart_broll._scene_pool_candidates(
        _scene(scene_class="literal", confidence=0.9), orientation="wide",
        per_scene=1, log=lambda *_: None, used_urls=set(), verify=False)
    assert calls["lib"] == ["literal one", "literal two"]


def test_scene_pool_skips_stock_for_strong_library(monkeypatch):
    """[PERF-BUDGET] Сильная библиотека (pre-rank ≥7) — сток не ищем."""
    import video_maker.semantic.library as library
    import video_maker.semantic.stock as stock
    from video_maker.semantic import perf_history

    calls: dict = {}

    def fake_lib(queries, **kwargs):
        return [{"file": "/tmp/lib_strong.mp4",
                 "description": "office workers meeting",
                 "tags": ["office", "workers", "meeting"],
                 "provider": "pixabay", "score": 8}]

    def fake_stock(queries, **kwargs):
        calls["stock"] = list(queries)
        return []

    monkeypatch.setattr(library, "search_library_multi", fake_lib)
    monkeypatch.setattr(stock, "search_stock_multi", fake_stock)
    monkeypatch.setattr(smart_broll, "_lib_preview",
                        lambda f, log: str(f))
    # Позитивная история клипа: pre-rank получает +hist и переходит 7/10.
    monkeypatch.setattr(
        perf_history, "get_many",
        lambda paths: {p: {"checks": 5, "avg": 9.0, "passes": 5, "fails": 0}
                       for p in paths})

    logs: list[str] = []
    sc = {"idx": 6, "text": "Офис", "visual": "люди работают в офисе",
          "queries": ["office workers meeting"], "emotions": [],
          "mood": "calm", "scene_class": "literal", "confidence": 0.9}
    smart_broll._scene_pool_candidates(
        sc, orientation="wide", per_scene=1, log=logs.append,
        used_urls=set(), verify=False, use_stock=True)

    assert "stock" not in calls
    assert sc["_pool_stats"].get("stock_skipped") is True
    assert any("библиотека ≥7 → сток пропущен" in x for x in logs)


def test_scene_pool_keeps_stock_for_low_relevance(monkeypatch):
    """[PERF-BUDGET] Высокий балл базы при низком pre-rank — сток НЕ пропускаем."""
    import video_maker.semantic.library as library
    import video_maker.semantic.stock as stock

    calls: dict = {}

    def fake_lib(queries, **kwargs):
        # Балл ingest высокий, но описание не про сцену → pre-rank < 5.
        return [{"file": "/tmp/lib_irrelevant.mp4",
                 "description": "desert sand dunes", "tags": ["desert", "sand"],
                 "provider": "pixabay", "score": 9}]

    def fake_stock(queries, **kwargs):
        calls["stock"] = list(queries)
        return []

    monkeypatch.setattr(library, "search_library_multi", fake_lib)
    monkeypatch.setattr(stock, "search_stock_multi", fake_stock)
    sc = {"idx": 7, "text": "Офис", "visual": "люди работают в офисе",
          "queries": ["office workers meeting"], "emotions": [],
          "mood": "calm", "scene_class": "literal", "confidence": 0.9}
    smart_broll._scene_pool_candidates(
        sc, orientation="wide", per_scene=1, log=lambda *_: None,
        used_urls=set(), verify=False, use_stock=True, use_history=False)

    assert calls.get("stock") == ["office workers meeting"]
    assert not sc["_pool_stats"].get("stock_skipped")


def test_scene_pool_budget_five_then_topup_eight(monkeypatch):
    """[PERF-BUDGET] Слабый пул: 5 кандидатов, добор до 8, не больше."""
    import video_maker.semantic.library as library
    import video_maker.semantic.stock as stock
    from video_maker.semantic import verify as verify_mod

    files = [f"/fake/clip_{i:02d}.mp4" for i in range(20)]
    monkeypatch.setattr(
        library, "search_library_multi",
        lambda *a, **k: [{"file": f, "description": "office", "tags": ["office"],
                          "provider": "pixabay", "score": 1.0} for f in files])
    monkeypatch.setattr(stock, "search_stock_multi", lambda *a, **k: [])
    monkeypatch.setattr(smart_broll, "_lib_preview", lambda f, log: str(f))
    calls: list[int] = []

    def fake_verify(entries, **kw):
        calls.append(len(entries))
        for e in entries:
            e["score"] = 2
            e["verified"] = True
            e["cached"] = False
        return entries

    monkeypatch.setattr(verify_mod, "verify_batch_gemini", fake_verify)
    sc = {"idx": 1, "text": "Офис", "visual": "офис",
          "queries": ["office"], "themes": {}}
    smart_broll._scene_pool_candidates(
        sc, orientation="wide", per_scene=1, log=lambda *_: None,
        used_urls=set(), use_history=False)

    stats = sc["_pool_stats"]
    assert stats["candidates"] == 8
    assert stats["checked"] == 8
    # 4 + 1 (добор бюджета 5) + 3 (добор до 8, т.к. ни один ≥6)
    assert calls == [4, 1, 3]



# ── 3. Fallback-политика по классам ───────────────────────────────────────

def _cand(score: int, *, tags: str = "", width: int = 3840, height: int = 2160,
          db_score: float | None = None) -> dict:
    c = {
        "kind": "lib", "file": f"/tmp/a2_{tags or 'clip'}.mp4", "query": "test",
        "tags": tags, "description": "", "what": "тест", "score": score,
        "width": width, "height": height, "duration": 10.0,
    }
    if db_score is not None:
        c["db_score"] = db_score
    return c


def _scene(cls: str, *, queries: list[str] | None = None) -> dict:
    return {
        "idx": 0, "start": 0.0, "end": 3.5, "text": "Тестовая сцена",
        "queries": queries or ["office people working"],
        "scene_class": cls, "confidence": 0.5,
    }


def test_scene_class_helpers():
    assert scene_class_of({"scene_class": "ABSTRACT"}) == "abstract"
    assert scene_class_of({}) == "literal"
    assert scene_confidence_of({"confidence": 85}) == pytest.approx(0.85)
    assert scene_confidence_of({}) == pytest.approx(0.5)


def test_associative_scene_prefers_treated_even_for_literal_clip():
    c = _cand(5, tags="office people working")  # лексически literal
    d = select_scene([c], _scene("associative"))
    assert d.fallback == "associative" and d.treated is True

    # Тот же клип в literal-сцене идёт в кроп/цвет, как раньше.
    d2 = select_scene([c], _scene("literal"))
    assert d2.fallback in ("literal_crop", "literal_proxy")


def test_associative_weak_clip_is_treated_proxy_not_graphic():
    weak = _cand(3, tags="desert sand")
    d = select_scene([weak], _scene("associative"))
    assert d.fallback == "proxy_forced" and d.treated is True
    assert d.graphic is False

    # literal-сцена с тем же клипом по-прежнему уходит в graphic.
    d2 = select_scene([weak], _scene("literal"))
    assert d2.fallback == "graphic" and d2.graphic is True


def test_abstract_uses_atmosphere_neutral_with_lower_mood_bar():
    weak = _cand(3, tags="city street night", db_score=4.6)
    d = select_scene([weak], _scene("abstract"))
    assert d.fallback == "neutral" and d.graphic is False

    # literal-сцене mood 4.6 мало (порог 5.0) — там graphic.
    d2 = select_scene([weak], _scene("literal"))
    assert d2.fallback == "graphic"


def test_abstract_5x_prefers_atmosphere_but_keeps_literal():
    neutral = _cand(5, tags="city street", db_score=7)
    d = select_scene([neutral], _scene("abstract"))
    assert d.fallback == "associative" and d.treated is True

    literal = _cand(5, tags="office people working")
    d2 = select_scene([literal], _scene("abstract"))
    assert d2.fallback in ("literal_crop", "literal_proxy")
    assert d2.primary is literal


# ── 4. Baseline: классы в JSONL и run_summary ─────────────────────────────

def test_baseline_records_scene_classes(tmp_path: Path):
    b = SceneBaseline(tmp_path)
    for i, (cls, conf) in enumerate([
        ("literal", 0.9), ("associative", 0.5), ("abstract", 0.2),
    ]):
        b.record(
            scene_index=i, scene_text=f"сцена {i}", queries=["q"],
            candidate_ids=[], candidate_scores=[], selected_clip="",
            selected_score=None, fallback_used="none",
            scene_class=cls, scene_confidence=conf,
        )
    recs = [json.loads(x) for x in
            (tmp_path / "scenes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["scene_class"] for r in recs] == ["literal", "associative", "abstract"]
    assert [r["confidence"] for r in recs] == [0.9, 0.5, 0.2]

    summary = b.finalize(series="test", total_scenes=3)
    assert summary["scene_class_counts"] == {
        "abstract": 1, "associative": 1, "literal": 1}
    assert summary["avg_scene_confidence"] == pytest.approx(0.533, abs=0.001)


def test_run_smart_broll_passes_scene_class_to_baseline(tmp_path: Path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)

    monkeypatch.setattr(
        smart_broll, "ensure_analysis_queries",
        lambda *a, **k: _aq(scene_class="abstract", confidence=0.8))
    monkeypatch.setattr(
        smart_broll, "_scene_pool_candidates",
        lambda sc, **k: [_cand(9, tags="")])
    seg_file = tmp_path / "seg.json"
    seg_file.write_text(json.dumps({"segments": _SEG}, ensure_ascii=False),
                        encoding="utf-8")
    out_root = tmp_path / "out"
    smart_broll.run_smart_broll(
        segments_json=str(seg_file), out_root=str(out_root), series="classes",
        per_scene=1, use_analysis=True, verify=False, library_first=False,
        log=lambda *_: None,
    )
    out_dir = out_root / "classes"
    rec = json.loads((out_dir / "scenes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["scene_class"] == "abstract"
    assert rec["confidence"] == pytest.approx(0.8)
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["scene_class_counts"]["abstract"] == 1
    assert summary["avg_scene_confidence"] == pytest.approx(0.8)
