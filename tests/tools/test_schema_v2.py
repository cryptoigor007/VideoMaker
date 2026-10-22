"""Схема v2: таблицы, словарь 40, WAL, статусы twin/retired, demand_ledger."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.tools.helpers import SCHEMA_PATH


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "v2.db")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def test_core_tables_and_views(db: sqlite3.Connection) -> None:
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"clips", "sources", "certificates", "themes", "moods",
                "clip_fingerprints", "clip_embeddings", "clip_usage",
                "demand_ledger", "audit_log", "library_health_history"}
    assert expected <= tables
    views = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='view'")}
    assert {"v_provenance_gaps", "v_needs_review", "v_embedding_coverage",
            "v_duplicate_candidates", "v_demand_weekly"} <= views


def test_mood_dictionary_40(db: sqlite3.Connection) -> None:
    assert db.execute("SELECT COUNT(*) FROM moods").fetchone()[0] == 40
    assert db.execute("SELECT COUNT(DISTINCT slug) FROM moods").fetchone()[0] == 40
    assert db.execute("SELECT COUNT(*) FROM moods WHERE length(description_en) > 20"
                      ).fetchone()[0] == 40


def test_wal_enabled(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_status_enum_has_twin_and_retired(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO clips (asset_uuid,file,rel_path,status) VALUES ('a','/a.mp4','a.mp4','active')")
    db.execute(
        "INSERT INTO clips (asset_uuid,file,rel_path,status,twin_of_id) "
        "VALUES ('b','/b.mp4','b.mp4','twin',1)")
    db.execute(
        "INSERT INTO clips (asset_uuid,file,rel_path,status) VALUES ('c','/c.mp4','c.mp4','retired')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO clips (asset_uuid,file,rel_path,status) "
            "VALUES ('d','/d.mp4','d.mp4','nonsense')")


def test_demand_ledger_columns_and_indexes(db: sqlite3.Connection) -> None:
    cols = {r[1] for r in db.execute("PRAGMA table_info(demand_ledger)")}
    assert {"week", "query", "query_norm", "query_embedding", "embedding_model",
            "embedding_dim", "cluster_key", "frequency", "weak_reason",
            "source"} <= cols
    indexes = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_demand_week", "idx_demand_query_norm", "idx_demand_cluster"} <= indexes
    db.execute(
        "INSERT INTO demand_ledger (week,query,query_norm,cluster_key,frequency) "
        "VALUES ('2026-W43','test query','test query','2026-W43-001-abcd',2)")
    assert db.execute("SELECT SUM(hits) FROM v_demand_weekly").fetchone()[0] == 1
