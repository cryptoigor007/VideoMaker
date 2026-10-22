"""Аудит read-only: дедуп-функции, pHash-кандидаты, JSON/MD без записи в БД."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.tools.helpers import make_legacy_db, run_tool

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_library as audit  # noqa: E402


def test_hamming64() -> None:
    assert audit.hamming64("0" * 16, "0" * 16) == 0
    assert audit.hamming64("0000000000000000", "0000000000000001") == 1
    assert audit.hamming64("0000000000000000", "ffffffffffffffff") == 64


def test_phash_duplicate_clusters_respects_aspect() -> None:
    phash = {
        "/a.mp4": {"value": "0000000000000000"},
        "/b.mp4": {"value": "0000000000000001"},
        "/c.mp4": {"value": "0000000000000002"},  # vertical: aspect mismatch
        "/d.mp4": {"value": "ffffffffffffffff"},
    }
    rows = {
        "/a.mp4": {"width": 1920, "height": 1080},
        "/b.mp4": {"width": 1920, "height": 1080},
        "/c.mp4": {"width": 1080, "height": 1920},
        "/d.mp4": {"width": 1920, "height": 1080},
    }
    clusters, stats = audit.phash_duplicate_clusters(phash, rows, max_distance=8)
    assert clusters == [["/a.mp4", "/b.mp4"]]
    assert stats["pairs"] == 1


def test_make_clusters_and_dsu() -> None:
    clusters = audit.make_clusters([
        ("/a", "x"), ("/b", "x"), ("/c", "y"), ("/d", ""),
    ])
    assert clusters == [["/a", "/b"]]
    dsu = audit.DSU()
    dsu.union("a", "b")
    dsu.union("b", "c")
    assert dsu.clusters() == [["a", "b", "c"]]


def test_semantic_duplicates() -> None:
    emb = {
        "/a.mp4": [1.0, 0.0, 0.0],
        "/b.mp4": [0.999, 0.001, 0.0],
        "/c.mp4": [0.0, 1.0, 0.0],
    }
    clusters, meta = audit.semantic_duplicates(
        emb, set(emb), 0.92, log=lambda *a: None)
    assert meta["pairs"] == 1
    assert len(clusters) == 1
    assert clusters[0]["files"] == ["/a.mp4", "/b.mp4"]


def test_audit_cli_is_read_only(tmp_path: Path) -> None:
    src = make_legacy_db(tmp_path / "legacy.db", tmp_path / "root")
    before = hashlib.sha256(src["db"].read_bytes()).hexdigest()
    mood_cache = tmp_path / "mood_cache.json"
    mood_cache.write_text(json.dumps({
        "model": "test-model",
        "items": {f"m{i}": {"vec": [0.1, 0.2], "text_hash": "x"} for i in range(40)},
    }), encoding="utf-8")
    out = tmp_path / "audit"
    proc = run_tool("audit_library.py", [
        "--db", str(src["db"]),
        "--root", str(src["root"]),
        "--alt-db", str(tmp_path / "none.db"),
        "--embeddings", str(tmp_path / "none_emb.json"),
        "--mood-cache", str(mood_cache),
        "--out-dir", str(out),
        "--no-files", "--no-scan-orphans",
        "--hash-mode", "none", "--phash-max", "0",
        "--quiet",
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = hashlib.sha256(src["db"].read_bytes()).hexdigest()
    assert before == after
    reports = [p for p in out.glob("audit_library_*.json")
               if not p.name.endswith("_fingerprints.json")]
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["mode"] == "read-only"
    assert report["inventory"]["total"] == 3
    assert report["provenance"]["no_source_url"] == 1
    assert report["provenance"]["no_author"] == 1
    assert report["content"]["without_embedding"] == 3
    assert report["mood"]["vectors"] == 40
    assert report["mood"]["applied"] is False
    assert (out / reports[0].name.replace(".json", ".md")).exists()
    fp = json.loads(Path(report["fingerprints_file"]).read_text(encoding="utf-8"))
    assert fp["db"] == str(src["db"])
