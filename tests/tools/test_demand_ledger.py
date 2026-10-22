"""Demand ledger: слабые сцены, нормализация, кластеры, импорт в v2-копию."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from tests.tools.helpers import make_v2_db, run_tool, write_scenes

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import demand_ledger as demand  # noqa: E402


def test_normalize_query() -> None:
    assert demand.normalize_query("The  DEBT-collector, office!") == "debt collector office"
    assert demand.normalize_query("  ...  ") == ""
    assert demand.normalize_query("в офисе человек") == "офисе"


def test_iso_week() -> None:
    dt = datetime.fromisoformat("2026-10-22T12:00:00+00:00")
    y, w, _ = dt.isocalendar()
    assert demand.iso_week("2026-10-22T12:00:00+00:00") == f"{y}-W{w:02d}"


def test_weak_reason() -> None:
    strong = {"selected_score": 8, "selected_clip": "/a.mp4",
              "candidate_ids": ["1", "2"], "fallback_used": "none"}
    assert demand.weak_reason(strong, 6.0) is None
    assert "low_score" in demand.weak_reason({**strong, "selected_score": 4}, 6.0)
    assert "no_selection" in demand.weak_reason({**strong, "selected_clip": ""}, 6.0)
    assert "no_candidates" in demand.weak_reason({**strong, "candidate_ids": []}, 6.0)
    assert "fallback:legacy_resume" in demand.weak_reason(
        {**strong, "fallback_used": "legacy_resume"}, 6.0)
    assert demand.weak_reason({**strong, "fallback_used": "legacy_resume"}, 6.0,
                              include_fallback=False) is None


def test_cluster_texts_cosine_and_fallback() -> None:
    vectors = {
        "debt collector office": [1.0, 0.0],
        "debt collector agency": [0.99, 0.01],
        "empty bus stop": [0.0, 1.0],
    }
    clusters = demand.cluster_texts(list(vectors), vectors, threshold=0.9)
    assert ["debt collector agency", "debt collector office"] in clusters
    assert ["empty bus stop"] in clusters
    # без эмбеддингов — только точное совпадение
    assert demand.cluster_texts(["a", "a", "b"], {}, 0.9) == [["a"], ["b"]]


def test_aggregate_deficits_frequency() -> None:
    rows = [
        {"week": "2026-W43", "query": "debt collector office",
         "query_norm": "debt collector office", "score": 3.0,
         "themes": ["work_money"], "mood": "stress", "orientation": "vertical",
         "weak_reason": "low_score:3", "candidate_ids": ["1"]},
        {"week": "2026-W43", "query": "debt collector office",
         "query_norm": "debt collector office", "score": 4.0,
         "themes": ["work_money"], "mood": "stress", "orientation": "vertical",
         "weak_reason": "low_score:4", "candidate_ids": []},
        {"week": "2026-W43", "query": "empty bus stop",
         "query_norm": "empty bus stop", "score": 5.0,
         "themes": [], "mood": "", "orientation": "",
         "weak_reason": "low_score:5", "candidate_ids": ["2"]},
    ]
    weeks, stats, top = demand.aggregate_deficits(rows, {}, 0.82, top_n=10)
    assert stats["weak_scenes"] == 3
    assert weeks[0]["weak_scenes"] == 3
    assert top[0]["query"] == "debt collector office"
    assert top[0]["count"] == 2
    assert top[0]["avg_selected_score"] == 3.5
    assert "stress" in top[0]["moods"]


def _scene(i: int, text: str, score: float, selected: str = "/lib/selected.mp4",
           fallback: str = "none", **extra) -> dict:
    rec = {
        "scene_id": f"s{i}",
        "scene_index": i,
        "scene_text": text,
        "queries": [text],
        "candidate_ids": ["c1", "c2"],
        "candidate_scores": [5, 4],
        "selected_clip": selected,
        "selected_score": score,
        "fallback_used": fallback,
        "search_ms": 12,
        "vlm_calls": 1,
        "cache_hits": 0,
        "ts": "2026-10-20T10:00:00+00:00",
    }
    rec.update(extra)
    return rec


def test_cli_import_and_replace(tmp_path: Path) -> None:
    v2 = make_v2_db(tmp_path / "v2.db", ["/lib/selected.mp4"])
    scenes = write_scenes(tmp_path / "out" / "scenes.jsonl", [
        _scene(1, "debt collector office", 3.0),
        _scene(2, "debt collector office", 4.0),
        _scene(3, "debt collector office", 2.0, fallback="legacy_resume"),
        _scene(4, "empty bus stop rain", 5.0),
        _scene(5, "empty bus stop rain", 4.5),
        _scene(6, "lonely man window", 3.5, fallback="legacy_resume"),
        _scene(7, "strong scene one", 8.0),
        _scene(8, "strong scene two", 9.0),
    ])
    out = tmp_path / "out_reports"
    json_path = tmp_path / "deficits.json"
    md_path = tmp_path / "deficits.md"
    args = [
        "--scenes", str(scenes),
        "--db", str(v2),
        "--apply",
        "--embed", "off",
        "--min-score", "6",
        "--top", "10",
        "--out-dir", str(out),
        "--json", str(json_path),
        "--md", str(md_path),
        "--quiet",
    ]
    proc = run_tool("demand_ledger.py", args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["stats"]["weak_scenes"] == 6
    assert report["stats"]["skipped_strong"] == 2
    assert report["top_deficits"][0]["query"] == "debt collector office"
    assert report["top_deficits"][0]["count"] == 3
    assert md_path.exists()

    conn = sqlite3.connect(v2)
    try:
        assert conn.execute("SELECT COUNT(*) FROM demand_ledger").fetchone()[0] == 6
        freq = conn.execute(
            "SELECT frequency FROM demand_ledger WHERE query_norm='debt collector office'"
        ).fetchall()
        assert [r[0] for r in freq] == [3, 3, 3]
        assert conn.execute("SELECT COUNT(*) FROM demand_ledger "
                            "WHERE query_embedding IS NULL").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM demand_ledger "
                            "WHERE selected_clip_id IS NOT NULL").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM audit_log "
                            "WHERE tool='demand_ledger.py'").fetchone()[0] == 1
    finally:
        conn.close()

    # повторный импорт той же недели не дублирует строки
    proc2 = run_tool("demand_ledger.py", args)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    conn = sqlite3.connect(v2)
    try:
        assert conn.execute("SELECT COUNT(*) FROM demand_ledger").fetchone()[0] == 6
    finally:
        conn.close()


def test_apply_refuses_production(tmp_path: Path) -> None:
    if not demand.PROD_DB.exists():
        return
    try:
        demand.apply_rows(demand.PROD_DB, [], {}, {}, "m", "run", tmp_path / "r.json")
    except RuntimeError as exc:
        assert "production" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("production apply не был запрещён")
