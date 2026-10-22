"""Mood-слой: 40 словарь, cosine top-2/порог, кэш, запись только в v2-копию."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.tools.helpers import make_v2_db

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import mood_embeddings as mood  # noqa: E402


def test_dictionary_has_40_unique_moods() -> None:
    assert len(mood.MOODS) == 40
    slugs = [m["slug"] for m in mood.MOODS]
    assert len(set(slugs)) == 40
    assert all(m["description_en"] for m in mood.MOODS)


def test_assign_moods_top2_and_threshold() -> None:
    mood_vecs = {
        "calm": [1.0, 0.0, 0.0],
        "joy": [0.9, 0.1, 0.0],
    }
    rows = [
        {"file": "/a.mp4", "rel_path": "a.mp4", "category": "x"},
        {"file": "/b.mp4", "rel_path": "b.mp4", "category": "x"},
    ]
    emb = {"/a.mp4": [1.0, 0.0, 0.0], "/b.mp4": [0.0, 1.0, 0.0]}
    out, stats = mood.assign_moods(rows, emb, mood_vecs, threshold=0.30,
                                   top_n=2, log=lambda *a: None)
    assert len(out) == 2
    a = next(x for x in out if x["file"] == "/a.mp4")
    b = next(x for x in out if x["file"] == "/b.mp4")
    assert [m["slug"] for m in a["moods"]] == ["calm", "joy"]
    assert all(m["score"] >= 0.30 for m in a["moods"])
    assert b["moods"] == []
    assert b["top1_below_threshold"] is True
    assert stats["with_mood"] == 1
    assert stats["below_threshold"] == 1


def test_assign_moods_dimension_mismatch() -> None:
    out, stats = mood.assign_moods(
        [{"file": "/a.mp4"}], {"/a.mp4": [1.0, 0.0]},
        {"calm": [1.0, 0.0, 0.0]}, threshold=0.3, log=lambda *a: None)
    assert out == []
    assert "размерности" in stats["error"]


def test_select_pilot_deterministic_and_embedded_only() -> None:
    rows = [
        {"file": f"/c{i}.mp4", "category": "a" if i % 2 else "b"}
        for i in range(6)
    ]
    emb = {f"/c{i}.mp4": [1.0] for i in range(5)}  # /c5 без вектора
    first = mood.select_pilot(rows, emb, 3)
    second = mood.select_pilot(rows, emb, 3)
    assert [r["file"] for r in first] == [r["file"] for r in second]
    assert len(first) == 3
    assert all(r["file"] in emb for r in first)


def test_mood_cache_roundtrip_without_network(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    items = {}
    for m in mood.MOODS:
        items[m["slug"]] = {
            "text": m["description_en"],
            "text_hash": mood.sha256_text(m["description_en"]),
            "vec": [1.0, float(len(items) % 3), 0.0],
            "dim": 3, "updated_at": now,
        }
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"schema": 1, "model": "test-model",
                                 "items": items}), encoding="utf-8")
    vecs, stats = mood.ensure_mood_vectors("test-model", cache, 180.0, False,
                                           log=lambda *a: None)
    assert len(vecs) == 40
    assert stats["cached"] == 40
    assert stats["computed"] == 0


def test_apply_writes_only_v2_copy(tmp_path: Path) -> None:
    db = make_v2_db(tmp_path / "v2.db", ["/x/a.mp4"])
    assignments = [{
        "file": "/x/a.mp4",
        "moods": [
            {"slug": "calm", "rank": 1, "score": 0.9, "margin": 0.2},
            {"slug": "joy", "rank": 2, "score": 0.5, "margin": None},
        ],
    }]
    vecs = {"calm": [1.0, 0.0, 0.0], "joy": [0.8, 0.2, 0.0]}
    res = mood.apply_to_db(db, assignments, vecs, "test-model", "mood-test",
                           tmp_path / "report.json", 0.30, mode="pilot")
    assert res["applied_moods"] == 2
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM clip_moods").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM moods WHERE embedding IS NOT NULL"
                            ).fetchone()[0] == 2
        run = conn.execute("SELECT mode, threshold FROM mood_runs").fetchone()
        assert run == ("pilot", 0.30)
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE tool='mood_embeddings.py'"
                            ).fetchone()[0] == 1
    finally:
        conn.close()


def test_apply_refuses_production_db() -> None:
    if not mood.PROD_DB.exists():
        pytest.skip("production DB отсутствует")
    with pytest.raises(RuntimeError):
        mood.apply_to_db(mood.PROD_DB, [], {}, "x", "run", Path("/tmp/x.json"), 0.3)
