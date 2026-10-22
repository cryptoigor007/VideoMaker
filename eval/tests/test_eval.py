"""Юнит-тесты eval-harness на синтетическом gold (без БД, VLM и сети)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.common import (  # noqa: E402
    apply_split,
    clip_id,
    compute_metrics,
    ensure_split,
    stratified_split,
)


def rec(sid: str, cls: str, label: str, acceptable: list[str],
        split: str = "train", **extra) -> dict:
    r = {
        "scene_id": sid,
        "class": cls,
        "label": label,
        "split": split,
        "acceptable_ids": acceptable,
        "recall_eligible": bool(acceptable),
        "selected_id": f"sel_{sid}",
    }
    r.update(extra)
    return r


@pytest.fixture()
def synthetic():
    records = [
        rec("A", "literal", "accept", ["a"], "train"),
        rec("B", "literal", "fallback_ok", ["b"], "train"),
        rec("C", "associative", "accept", ["c"], "train"),
        rec("D", "associative", "fallback_ok", [], "train"),
        rec("E", "abstract", "must_graphic", [], "holdout"),
        rec("F", "literal", "accept", ["f"], "holdout"),
    ]
    retrieved = {
        "A": ["a", "x"],
        "B": ["x", "b"],
        "C": ["x", "y"],
        "D": [],
        "E": [],
        "F": ["x", "y", "z", "f"],
    }
    timings = {
        "A": {"retrieval_ms": 100.0, "vlm_calls": 2},
        "B": {"retrieval_ms": 200.0, "vlm_calls": 1},
        "C": {"retrieval_ms": 300.0, "vlm_calls": 0},
        "D": {"retrieval_ms": 400.0, "vlm_calls": 0},
        "E": {"retrieval_ms": 500.0, "vlm_calls": 0},
        "F": {"retrieval_ms": 600.0, "vlm_calls": 3},
    }
    return records, retrieved, timings


def test_recall_and_literal_hit(synthetic):
    records, retrieved, timings = synthetic
    m = compute_metrics(records, retrieved, timings=timings)
    o = m["overall"]
    assert o["n_scenes"] == 6
    assert o["n_recall_scenes"] == 4  # A, B, C, F
    assert o["recall"]["@1"] == pytest.approx(1 / 4)   # A
    assert o["recall"]["@3"] == pytest.approx(2 / 4)   # A, B
    assert o["recall"]["@10"] == pytest.approx(3 / 4)  # A, B, F
    assert o["n_literal_scenes"] == 3  # A, B, F
    assert o["literal_hit_rate"]["@1"] == pytest.approx(1 / 3)   # A
    assert o["literal_hit_rate"]["@10"] == pytest.approx(3 / 3)  # A, B, F


def test_fallback_rate_and_labels(synthetic):
    records, retrieved, _ = synthetic
    o = compute_metrics(records, retrieved)["overall"]
    assert o["label_counts"] == {"accept": 3, "fallback_ok": 2, "must_graphic": 1}
    assert o["accept_rate"] == pytest.approx(0.5)
    assert o["fallback_rate"] == pytest.approx(0.5)
    assert o["fallback_ok_rate"] == pytest.approx(2 / 6)
    assert o["must_graphic_rate"] == pytest.approx(1 / 6)


def test_metrics_by_class_and_split(synthetic):
    records, retrieved, _ = synthetic
    m = compute_metrics(records, retrieved)
    lit = m["by_class"]["literal"]
    assert lit["n_scenes"] == 3
    assert lit["n_recall_scenes"] == 3
    assert lit["recall"]["@1"] == pytest.approx(1 / 3)
    assert lit["recall"]["@10"] == pytest.approx(3 / 3)
    assoc = m["by_class"]["associative"]
    assert assoc["n_recall_scenes"] == 1  # только C (D без приемлемого клипа)
    assert assoc["recall"]["@3"] == pytest.approx(0.0)
    abs_ = m["by_class"]["abstract"]
    assert abs_["n_recall_scenes"] == 0
    assert abs_["recall"]["@1"] is None

    tr = m["by_split"]["train"]
    assert tr["n_scenes"] == 4
    assert tr["n_recall_scenes"] == 3  # A, B, C
    assert tr["recall"]["@1"] == pytest.approx(1 / 3)
    assert tr["recall"]["@10"] == pytest.approx(2 / 3)
    ho = m["by_split"]["holdout"]
    assert ho["n_scenes"] == 2
    assert ho["n_recall_scenes"] == 1  # F
    assert ho["recall"]["@10"] == pytest.approx(1.0)
    assert ho["fallback_rate"] == pytest.approx(0.5)


def test_time_and_vlm_per_scene(synthetic):
    records, retrieved, timings = synthetic
    o = compute_metrics(records, retrieved, timings=timings)["overall"]
    assert o["retrieval_ms_total"] == pytest.approx(2100.0)
    assert o["retrieval_ms_mean"] == pytest.approx(350.0)
    assert o["vlm_calls"] == 6
    assert o["vlm_calls_per_scene"] == pytest.approx(1.0)


def test_recall_eligible_false_excluded():
    records = [
        rec("A", "literal", "accept", ["a"], "train"),
        rec("B", "literal", "accept", ["stock_only"], "train", recall_eligible=False),
    ]
    retrieved = {"A": ["a"], "B": ["stock_only"]}
    m = compute_metrics(records, retrieved)
    assert m["overall"]["n_recall_scenes"] == 1
    assert m["overall"]["recall"]["@1"] == pytest.approx(1.0)
    b = next(s for s in m["scenes"] if s["scene_id"] == "B")
    assert b["recall_eligible"] is False
    assert b["hit"]["@1"] is None


def test_selected_rank():
    records = [rec("A", "literal", "accept", ["a"], "train")]
    m = compute_metrics(records, {"A": ["x", "sel_A", "a"]})
    s = m["scenes"][0]
    assert s["selected_rank"] == 2


# ---------------------------------------------------------------------------
# Split 30/10
# ---------------------------------------------------------------------------
def _split_fixture() -> list[dict]:
    classes = ["literal"] * 16 + ["associative"] * 18 + ["abstract"] * 6
    return [
        {"scene_id": f"scene_{i:03d}", "class": c, "label": "accept",
         "acceptable_ids": [f"c{i}"], "split": ""}
        for i, c in enumerate(classes)
    ]


def test_split_is_30_10_and_disjoint():
    records = _split_fixture()
    split = stratified_split(records, holdout_n=10)
    assert len(split) == 40
    assert list(split.values()).count("train") == 30
    assert list(split.values()).count("holdout") == 10
    train = {k for k, v in split.items() if v == "train"}
    holdout = {k for k, v in split.items() if v == "holdout"}
    assert not (train & holdout)
    assert train | holdout == {r["scene_id"] for r in records}


def test_split_is_stratified_and_deterministic():
    records = _split_fixture()
    a = stratified_split(records, holdout_n=10, seed=42)
    b = stratified_split(records, holdout_n=10, seed=42)
    assert a == b
    # порядок строк не влияет
    c = stratified_split(list(reversed(records)), holdout_n=10, seed=42)
    assert a == c
    by_class = {r["scene_id"]: r["class"] for r in records}
    ho = [by_class[sid] for sid, sp in a.items() if sp == "holdout"]
    assert set(ho) == {"literal", "associative", "abstract"}
    assert len(ho) == 10
    assert ho.count("literal") >= 3
    assert ho.count("associative") >= 3
    assert ho.count("abstract") >= 1


def test_split_stratifies_labels():
    records = [
        {"scene_id": f"a{i}", "class": "literal", "label": "accept"}
        for i in range(6)
    ] + [
        {"scene_id": f"b{i}", "class": "associative", "label": "fallback_ok"}
        for i in range(10)
    ] + [
        {"scene_id": f"c{i}", "class": "associative", "label": "must_graphic"}
        for i in range(4)
    ]
    split = stratified_split(records, holdout_n=8, label_key="label")
    assert list(split.values()).count("holdout") == 8
    labels = {r["scene_id"]: r["label"] for r in records}
    ho_labels = [labels[sid] for sid, sp in split.items() if sp == "holdout"]
    assert "accept" in ho_labels
    assert "fallback_ok" in ho_labels


def test_apply_split_and_ensure_split():
    records = _split_fixture()
    out = apply_split(records, holdout_n=10, seed=42)
    assert sum(1 for r in out if r["split"] == "holdout") == 10
    # уже проставленный split не пересчитывается
    again = ensure_split(out, holdout_n=10, seed=999)
    assert [r["split"] for r in again] == [r["split"] for r in out]
    # исходные записи не мутированы
    assert all(r["split"] == "" for r in records)


def test_split_holdout_n_edges():
    records = _split_fixture()[:5]
    split = stratified_split(records, holdout_n=3)
    assert list(split.values()).count("holdout") == 3
    split0 = stratified_split(records, holdout_n=0)
    assert set(split0.values()) == {"train"}


def test_clip_id():
    assert clip_id("/Volumes/video/broll_library/solitude/man_1_pixabay.mp4") == "man_1_pixabay.mp4"
    assert clip_id("") == ""
