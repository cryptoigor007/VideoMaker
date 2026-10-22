"""Миграция v1 -> v2 только на копии: счётчики, провенанс, дубли, read-only source."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from tests.tools.helpers import make_legacy_db, run_tool


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migrate_copy_end_to_end(tmp_path: Path) -> None:
    src = make_legacy_db(tmp_path / "legacy.db", tmp_path / "root")
    target = tmp_path / "v2.db"
    report_path = tmp_path / "report.json"
    src_hash = _sha(src["db"])

    proc = run_tool("migrate_schema_v2.py", [
        "--source", str(src["db"]),
        "--target", str(target),
        "--root", str(src["root"]),
        "--embeddings", str(tmp_path / "no_embeddings.json"),
        "--apply",
        "--hash-mode", "candidates",
        "--workers", "2",
        "--report", str(report_path),
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _sha(src["db"]) == src_hash, "production/исходная БД изменилась"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "apply"
    assert report["source_unchanged"] is True
    assert report["legacy_clips"] == 3
    assert report["counts"]["clips"] == 3
    assert report["counts"]["moods"] == 40
    assert report["counts"]["themes"] == 30
    assert report["counts"]["clip_usage"] == 3
    assert report["validation"]["integrity_check"] == "ok"
    assert report["validation"]["foreign_key_violations"] == 0
    assert report["validation"]["asset_uuid_empty"] == 0
    assert report["field_fill"]["duration"] == 2
    assert report["field_fill"]["author"] == 2
    assert report["field_fill"]["source_url"] == 2
    # два одинаковых файла -> duplicate_sha256 у обоих
    assert report["needs_review"]["by_reason"].get("duplicate_sha256") == 2
    assert report["sha256_hashed"] == 2
    assert report["status_counts"] == {"active": 3}

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("SELECT COUNT(*) FROM clip_fingerprints "
                            "WHERE kind='sha256'").fetchone()[0] == 2
        dups = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE review_reasons LIKE '%duplicate_sha256%'"
        ).fetchone()[0]
        assert dups == 2
        assert conn.execute("SELECT COUNT(DISTINCT asset_uuid) FROM clips").fetchone()[0] == 3
        local = conn.execute(
            "SELECT * FROM clips WHERE provider='local'").fetchone()
        assert local["needs_review"] == 1
        assert "missing_source_url" in local["review_reasons"]
        assert "missing_duration" in local["review_reasons"]
        assert local["status"] == "active"
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM certificates").fetchone()[0] == 0
        # uri-схема + usage: точный путь и уникальный basename
        assert report["usage_linked_by_name"] == 1
        assert report["usage_orphans"] == 1
        assert conn.execute("SELECT COUNT(*) FROM clip_usage WHERE clip_id IS NULL"
                            ).fetchone()[0] == 1
        health = conn.execute(
            "SELECT dup_clusters FROM library_health_history").fetchone()
        assert health[0] == 1
    finally:
        conn.close()


def test_migrate_fingerprints_json(tmp_path: Path) -> None:
    src = make_legacy_db(tmp_path / "legacy.db", tmp_path / "root")
    target = tmp_path / "v2_fp.db"
    f1, f2, _f3 = src["files"]
    fp = tmp_path / "fingerprints.json"
    fp.write_text(json.dumps({
        "sha256": {f1: "a" * 64, f2: "b" * 64},
        "phash": {f1: {"value": "0123456789abcdef", "frame_s": 0.5}},
    }), encoding="utf-8")
    proc = run_tool("migrate_schema_v2.py", [
        "--source", str(src["db"]),
        "--target", str(target),
        "--root", str(src["root"]),
        "--embeddings", str(tmp_path / "no_embeddings.json"),
        "--fingerprints", str(fp),
        "--apply",
        "--report", str(tmp_path / "report_fp.json"),
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM clips WHERE sha256 IS NOT NULL"
                            ).fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM clips WHERE phash IS NOT NULL"
                            ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM clip_fingerprints "
                            "WHERE kind='phash64'").fetchone()[0] == 1
    finally:
        conn.close()


def test_migrate_refuses_source_equals_target(tmp_path: Path) -> None:
    src = make_legacy_db(tmp_path / "legacy.db", tmp_path / "root")
    proc = run_tool("migrate_schema_v2.py", [
        "--source", str(src["db"]),
        "--target", str(src["db"]),
        "--root", str(src["root"]),
        "--apply",
    ])
    assert proc.returncode == 2
    assert "совпадают" in proc.stdout
