"""Мини-БД и запуск CLI для тестов tools/*."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "tools" / "schema_v2.sql"


def make_legacy_db(path: Path, root: Path) -> dict:
    """Мини-копия production-схемы v1: 3 клипа, usage, scores, clip_perf."""
    root.mkdir(parents=True, exist_ok=True)
    media = root / "solitude"
    media.mkdir(parents=True, exist_ok=True)
    f1 = media / "lonely_window_40797_pixabay.mp4"
    f2 = media / "local_street.mp4"
    f3 = media / "lonely_window_duplicate_40797_pixabay.mp4"
    f1.write_bytes(b"same-video-bytes" * 100)
    f3.write_bytes(b"same-video-bytes" * 100)
    f2.write_bytes(b"different-local-bytes" * 90)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file TEXT UNIQUE, rel_path TEXT, provider TEXT, source_url TEXT,
            author TEXT, license TEXT, license_url TEXT, query TEXT,
            category TEXT, description TEXT, tags TEXT, neg_tags TEXT,
            score INTEGER, blur REAL, width INTEGER, height INTEGER,
            duration REAL, created_at TEXT, orientation TEXT,
            bitrate REAL
        );
        CREATE TABLE usage (file TEXT, series TEXT, scene INTEGER, used_at TEXT);
        CREATE TABLE scores (file TEXT, th TEXT, score INTEGER, what TEXT,
                             at TEXT, PRIMARY KEY(file, th));
        CREATE TABLE clip_perf (path TEXT PRIMARY KEY, checks INTEGER NOT NULL DEFAULT 0,
            sum_score INTEGER NOT NULL DEFAULT 0, passes INTEGER NOT NULL DEFAULT 0,
            fails INTEGER NOT NULL DEFAULT 0, last_seen TEXT, updated_at TEXT);
        CREATE INDEX idx_clips_url ON clips(source_url);
        CREATE INDEX idx_clips_category ON clips(category);
        CREATE INDEX idx_usage_file ON usage(file);
        """
    )
    rows = [
        (1, str(f1), "solitude/lonely_window_40797_pixabay.mp4", "pixabay",
         "https://pixabay.com/videos/id-40797/", "yogobunny",
         "Pixabay Content License", "https://pixabay.com/service/license-summary/",
         "lonely person window", "solitude",
         "A man standing in a dark room looking out of a window at night.",
         '["man", "window", "night"]', '["daytime"]', 8, 156.3, 3840, 2160, 16.0,
         "2026-10-19T01:04:04+00:00", "vertical", 21849752.0),
        (2, str(f2), "solitude/local_street.mp4", "local",
         "", "", "local archive", "", "street", "custom",
         "A local street shot from the archive.", '["street"]', "[]", 0, 0,
         0, 0, 0, "2026-10-18T12:00:00+00:00", None, 0),
        (3, str(f3), "solitude/lonely_window_duplicate_40797_pixabay.mp4", "pixabay",
         "https://pixabay.com/videos/id-40797/", "yogobunny",
         "Pixabay Content License", "https://pixabay.com/service/license-summary/",
         "lonely person window", "solitude",
         "A duplicate download of the lonely window scene.",
         '["man", "window"]', "[]", 7, 150.0, 3840, 2160, 16.0,
         "2026-10-19T01:05:00+00:00", "vertical", 21849752.0),
    ]
    db.executemany(
        "INSERT INTO clips (id,file,rel_path,provider,source_url,author,license,"
        "license_url,query,category,description,tags,neg_tags,score,blur,width,height,"
        "duration,created_at,orientation,bitrate) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO usage VALUES (?,?,?,?)",
               (str(f1), "series-1", 1, "2026-10-20T10:00:00+00:00"))
    db.execute("INSERT INTO usage VALUES (?,?,?,?)",
               ("/Volumes/SSD/series/lonely_window_40797_pixabay.mp4", "series-1", 2,
                "2026-10-20T10:01:00+00:00"))
    db.execute("INSERT INTO usage VALUES (?,?,?,?)",
               ("/Volumes/SSD/series/not_in_library.mp4", "series-1", 3,
                "2026-10-20T10:02:00+00:00"))
    db.execute("INSERT INTO scores VALUES (?,?,?,?,?)",
               (str(f1), "vlm", 8, "good", "2026-10-20T10:00:00+00:00"))
    db.execute("INSERT INTO clip_perf (path, checks, sum_score, passes) "
               "VALUES (?, 1, 8, 1)", (str(f1),))
    db.commit()
    db.close()
    return {"db": path, "root": root, "files": [str(f1), str(f2), str(f3)]}


def make_v2_db(path: Path, clip_files: list[str] | None = None) -> Path:
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for i, file in enumerate(clip_files or [], 1):
        db.execute(
            """INSERT INTO clips (asset_uuid, file, rel_path, status, provider,
                                  description, tags)
               VALUES (?,?,?,'active','pixabay',?, '[]')""",
            (f"test-{i}", file, f"rel/{Path(file).name}", f"clip {i}"))
    db.commit()
    db.close()
    return path


def run_tool(name: str, args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO_ROOT / "tools" / name), *args]
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=timeout)


def write_scenes(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path
