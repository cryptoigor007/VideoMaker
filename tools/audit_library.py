#!/usr/bin/env python3
"""Неразрушающий аудит библиотеки B-roll (production DB открывается read-only).

Что проверяет:
  * inventory: клипы, провайдеры, категории, разрешения, длительности, объём;
  * файлы: offline/missing, нулевой размер, файлы на диске вне индекса;
  * провенанс: нет source_url / author / лицензии / сертификата; что можно
    восстановить из licenses/*.json;
  * описания/теги/эмбеддинги: пустые, короткие, без вектора, лишние векторы;
  * дубли: size -> sha256 -> pHash -> semantic cosine (0.92), ТОЛЬКО кандидаты,
    без удаления и без изменения БД;
  * гигиена: две БД, JSON-зеркало library_index.json, orphan-строки usage/scores.

Пишет только отчёты в ~/video_maker/audit (JSON + MD) и fingerprints JSON,
который можно передать в migrate_schema_v2.py --fingerprints.

    python3 tools/audit_library.py
    python3 tools/audit_library.py --hash-mode none --no-scan-orphans
    python3 tools/audit_library.py --phash-max 24 --jobs 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / "video_maker" / "broll_library.db"
DEFAULT_ROOT = Path("/Volumes/video/broll_library")
DEFAULT_OUT = Path.home() / "video_maker" / "audit"
DEFAULT_EMB = Path.home() / "video_maker" / "broll_embeddings.json"
DEFAULT_ALT_DB = Path("/Volumes/video/broll_library/library.db")
MEDIA_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
SKIP_DIRS = {"licenses", ".frames", "__pycache__", ".git", ".Trash"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                b = fh.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def human_bytes(n: int | float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def pct(n: int, total: int) -> str:
    return f"{n} ({100.0 * n / total:.1f}%)" if total else f"{n} (0.0%)"


def top(counter: Counter, n: int = 15) -> list[dict]:
    return [{"key": str(k), "count": int(v)} for k, v in counter.most_common(n)]


def norm_json_list(value) -> str:
    if value is None:
        return "[]"
    if isinstance(value, (list, tuple, set)):
        return json.dumps([str(x) for x in value if str(x).strip()], ensure_ascii=False)
    s = str(value).strip()
    if not s:
        return "[]"
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return json.dumps([str(x) for x in parsed if str(x).strip()], ensure_ascii=False)
        if isinstance(parsed, str):
            return json.dumps([parsed], ensure_ascii=False)
    except Exception:
        pass
    return json.dumps([x.strip() for x in re.split(r"[,;]\s*", s) if x.strip()],
                      ensure_ascii=False)


def extract_asset_id(url: str, filename: str, provider: str) -> str:
    """Провайдерский id: год в CDN-пути (/video/2020/…) не должен победить id."""
    u = str(url or "")
    name = Path(str(filename or "")).name
    base = u.split("?", 1)[0].rsplit("/", 1)[-1]
    page_pats = [
        r"videos?/id-(\d{3,})",              # pixabay.com/videos/id-40797/
        r"pexels\.com/video/[\w-]+-(\d{3,})",
    ]
    file_pats = [
        r"_(\d{4,})_pixabay",
        r"pixabay[_-](\d{4,})",
        r"pexels[_-](\d{3,})",
        r"^(\d{4,})[-_]",
        r"-(\d{3,})\.(?:mp4|mov|webm)$",
    ]
    for p in page_pats:
        m = re.search(p, u, re.I)
        if m:
            return m.group(1)
    hay = name + " " + base
    for p in file_pats:
        m = re.search(p, hay, re.I)
        if m:
            return m.group(1)
    return ""


def derive_orientation(width, height, old: str | None = None) -> str:
    if old in ("vertical", "horizontal", "wide", "square"):
        return "vertical" if old == "vertical" else ("square" if old == "square" else "wide")
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return "unknown"
    if w <= 0 or h <= 0:
        return "unknown"
    if h > w:
        return "vertical"
    if abs(h - w) <= max(2, int(0.02 * max(w, h))):
        return "square"
    return "wide"


def stat_files(files: list[str], workers: int) -> dict[str, dict]:
    def one(f: str):
        try:
            st = os.stat(f)
            return f, {"size": int(st.st_size),
                       "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                       "exists": True}
        except OSError as exc:
            return f, {"size": None, "mtime": None, "exists": False, "error": str(exc)[:120]}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for f, info in ex.map(one, files):
            out[f] = info
    return out


def quick_digest(path: str, prefix_bytes: int) -> str | None:
    """Быстрый отпечаток: size + первые/последние N байт (без чтения всего файла)."""
    try:
        size = os.path.getsize(path)
        h = hashlib.sha256(str(size).encode())
        with open(path, "rb") as fh:
            h.update(fh.read(prefix_bytes))
            if size > prefix_bytes * 2:
                fh.seek(-prefix_bytes, os.SEEK_END)
                h.update(fh.read(prefix_bytes))
        return h.hexdigest()
    except OSError:
        return None


def load_hash_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def hash_candidates(files: list[str], sizes: dict[str, int], max_bytes: int,
                    workers: int, prefix_mb: float = 1.0,
                    cache_path: Path | None = None,
                    log=print) -> tuple[dict[str, str], dict]:
    """sha256 только для реальных кандидатов: size -> prefix -> full hash.

    Exact-дубль всегда имеет одинаковый size и prefix, поэтому ложных
    пропусков нет; полный sha256 читает только группы, совпавшие по prefix.
    """
    groups: dict[int, list[str]] = defaultdict(list)
    for f in files:
        s = sizes.get(f, 0)
        if s and s > 0:
            groups[int(s)].append(f)
    size_groups = [(s, g) for s, g in groups.items() if len(g) > 1]
    all_candidates = [f for _s, g in size_groups for f in g]
    prefix_bytes = max(0, int(prefix_mb * 1_000_000))
    t0 = time.time()
    quick: dict[str, str] = {}

    if prefix_bytes > 0:
        def qone(f: str):
            return f, quick_digest(f, prefix_bytes)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(qone, f) for f in all_candidates]
            for i, fut in enumerate(as_completed(futs), 1):
                f, q = fut.result()
                if q:
                    quick[f] = q
                if i % 25 == 0 or i == len(futs):
                    log(f"  prefix {i}/{len(futs)} ({time.time() - t0:.1f}s)")

    # полный hash — только файлы, у которых совпал (size, prefix)
    by_key: dict[tuple[int, str], list[str]] = defaultdict(list)
    for f in all_candidates:
        s = sizes.get(f, 0)
        key = (s, quick.get(f, ""))
        if prefix_bytes == 0 or quick.get(f):
            by_key[key].append(f)
    full_candidates = [f for g in by_key.values() if len(g) > 1 for f in g]

    cache = load_hash_cache(cache_path) if cache_path else {}
    out: dict[str, str] = {}
    reused = 0
    for f in full_candidates:
        ent = cache.get(f) or {}
        if ent.get("size") == sizes.get(f) and ent.get("sha256"):
            out[f] = str(ent["sha256"])
            reused += 1

    todo = [f for f in full_candidates if f not in out]
    skipped = 0
    planned = sum(sizes.get(f, 0) for f in todo)
    if planned > max_bytes:
        keep: list[str] = []
        used = 0
        for f in sorted(todo, key=lambda f: sizes.get(f, 0)):
            sz = sizes.get(f, 0)
            if used + sz > max_bytes:
                skipped += 1
                continue
            keep.append(f)
            used += sz
        todo = keep

    def one(f: str):
        return f, sha256_file(f)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(one, f) for f in todo]
            for i, fut in enumerate(as_completed(futs), 1):
                f, digest = fut.result()
                if digest:
                    out[f] = digest
                    if cache_path:
                        cache[f] = {"size": sizes.get(f, 0), "sha256": digest}
                if i % 10 == 0 or i == len(futs):
                    log(f"  sha256 {i}/{len(futs)} ({time.time() - t0:.1f}s)")
                    if cache_path:
                        try:
                            cache_path.write_text(json.dumps(cache, ensure_ascii=False),
                                                  encoding="utf-8")
                        except Exception:
                            pass
    if cache_path:
        try:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return out, {
        "size_duplicate_groups": len(size_groups),
        "candidate_files": len(all_candidates),
        "prefix_hashed": len(quick),
        "prefix_match_files": len(full_candidates),
        "full_candidates": len(full_candidates),
        "candidate_bytes": sum(sizes.get(f, 0) for f in full_candidates),
        "hashed": len(out),
        "reused_from_cache": reused,
        "skipped_bytes_cap": skipped,
        "seconds": round(time.time() - t0, 1),
    }


def phash_frame(path: str, ts: float, timeout: int = 35) -> str | None:
    """64-bit pHash первого доступного кадра (ffmpeg -> 32x32 gray -> DCT)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-v", "error", "-ss", f"{max(0.0, ts):.3f}", "-i", path,
           "-frames:v", "1", "-vf", "scale=32:32", "-f", "rawvideo",
           "-pix_fmt", "gray", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        raw = r.stdout
        if len(raw) < 1024:
            return None
        import cv2  # локальный импорт: аудит должен работать и без OpenCV
        import numpy as np
        img = np.frombuffer(raw[:1024], dtype=np.uint8).reshape(32, 32).astype(np.float32)
        dct = cv2.dct(img)
        low = dct[:8, :8].flatten()
        med = float(np.median(low[1:]))
        bits = "".join("1" if float(x) > med else "0" for x in low)
        return f"{int(bits, 2):016x}"
    except Exception:
        return None


def compute_phash(files: list[str], stats: dict[str, dict], durations: dict[str, float],
                  max_files: int, workers: int, log=print) -> tuple[dict[str, dict], dict]:
    todo = files[:max_files]
    out: dict[str, dict] = {}
    failed = 0
    t0 = time.time()

    def one(f: str):
        dur = float(durations.get(f) or 0)
        ts = min(1.0, max(0.5, dur * 0.1)) if dur > 0 else 1.0
        val = phash_frame(f, ts)
        return f, val, ts

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, f) for f in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            f, val, ts = fut.result()
            if val:
                out[f] = {"value": val, "frame_s": round(ts, 3)}
            else:
                failed += 1
            if i % 10 == 0 or i == len(futs):
                log(f"  phash {i}/{len(futs)} ({time.time() - t0:.1f}s)")
    return out, {
        "candidates": len(files), "computed": len(out), "failed": failed,
        "sampled": len(todo), "seconds": round(time.time() - t0, 1),
        "distance_threshold": 8,
    }


class DSU:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def clusters(self) -> list[list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for x in list(self.parent):
            groups[self.find(x)].append(x)
        return [sorted(v) for v in groups.values() if len(v) > 1]


def semantic_duplicates(emb: dict, db_files: set[str], threshold: float,
                        log=print) -> tuple[list[dict], dict]:
    files = sorted(f for f in emb if f in db_files)
    meta = {"vectors": len(files), "threshold": threshold, "pairs": 0,
            "clusters": 0, "members": 0, "seconds": 0.0}
    if len(files) < 2:
        return [], meta
    try:
        import numpy as np
    except Exception:
        meta["error"] = "numpy недоступен"
        return [], meta
    t0 = time.time()
    try:
        M = np.asarray([emb[f] for f in files], dtype=np.float32)
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        M = M / np.maximum(norms, 1e-9)
        sim = M @ M.T
        iu = np.triu_indices(len(files), 1)
        vals = sim[iu]
        idx = np.where(vals >= float(threshold))[0]
        pairs = [(files[int(iu[0][i])], files[int(iu[1][i])], float(vals[i])) for i in idx]
        pairs.sort(key=lambda x: -x[2])
    except Exception as exc:
        meta["error"] = str(exc)[:160]
        return [], meta
    dsu = DSU()
    for a, b, _s in pairs:
        dsu.union(a, b)
    clusters = dsu.clusters()
    by_file = {f: i for i, f in enumerate(files)}
    score_by_pair = {(a, b): s for a, b, s in pairs}
    out = []
    for cl in clusters:
        scores = []
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                s = score_by_pair.get((cl[i], cl[j])) or score_by_pair.get((cl[j], cl[i]))
                if s is not None:
                    scores.append(s)
        out.append({
            "files": cl,
            "size": len(cl),
            "score_min": round(min(scores), 4) if scores else None,
            "score_max": round(max(scores), 4) if scores else None,
            "score_avg": round(sum(scores) / len(scores), 4) if scores else None,
        })
    out.sort(key=lambda c: (-c["size"], -(c["score_avg"] or 0)))
    meta.update({"pairs": len(pairs), "clusters": len(out),
                 "members": sum(c["size"] for c in out),
                 "seconds": round(time.time() - t0, 2)})
    log(f"  semantic: {len(pairs)} пар >= {threshold} -> {len(out)} кластеров "
        f"({meta['seconds']}s)")
    return out, meta


def make_clusters(items: list[tuple[str, str]]) -> list[list[str]]:
    """items: (file, group_key) -> кластеры файлов с одинаковым ключом."""
    groups: dict[str, list[str]] = defaultdict(list)
    for f, k in items:
        if k:
            groups[k].append(f)
    return sorted([sorted(v) for v in groups.values() if len(v) > 1],
                  key=lambda x: (-len(x), x))


def walk_library(root: Path) -> set[str]:
    found: set[str] = set()
    if not root.exists():
        return found
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name in SKIP_DIRS or e.name.startswith("._"):
                                continue
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            if Path(e.name).suffix.lower() in MEDIA_EXT:
                                found.add(str(Path(e.path)))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def load_certs(root: Path, workers: int = 16) -> tuple[dict[str, dict], int, int]:
    lic = root / "licenses"
    if not lic.exists():
        return {}, 0, 0
    paths = [p for p in sorted(lic.glob("*.json")) if not p.name.startswith("._")]

    def one(p: Path):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return p, d, False
        except Exception:
            return p, None, True

    certs: dict[str, dict] = {}
    broken = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p, d, bad in ex.map(one, paths):
            if bad or not isinstance(d, dict):
                broken += 1
                continue
            key = ""
            for cand in (d.get("clip"), Path(str(d.get("file") or "")).name, p.name[:-5]):
                if cand:
                    key = Path(str(cand)).name
                    break
            if key:
                certs[key] = {"data": d, "path": str(p)}
    return certs, len(paths), broken


def load_embeddings(path: Path) -> tuple[dict, dict]:
    if not path.exists():
        return {}, {"exists": False}
    try:
        raw = path.read_bytes()
        d = json.loads(raw)
        return d, {"exists": True, "bytes": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest(),
                   "count": len(d), "path": str(path)}
    except Exception as exc:
        return {}, {"exists": True, "error": str(exc)[:160], "path": str(path)}


def health_score(total: int, missing: int, prov_gap: int, dup_members: int,
                 no_emb: int, short_desc: int, meta_gap: int, consistency: int) -> tuple[float, dict]:
    if not total:
        return 0.0, {}
    parts = {
        "missing_files": 30.0 * missing / total,
        "provenance": 20.0 * prov_gap / total,
        "duplicates": 15.0 * dup_members / total,
        "embeddings": 15.0 * no_emb / total,
        "descriptions": 10.0 * short_desc / total,
        "metadata": 5.0 * meta_gap / total,
        "consistency": 5.0 * min(1.0, consistency / max(1, total)),
    }
    score = max(0.0, 100.0 - sum(parts.values()))
    return round(score, 1), {k: round(v, 2) for k, v in parts.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Неразрушающий аудит B-roll библиотеки")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--alt-db", type=Path, default=DEFAULT_ALT_DB)
    ap.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=0, help="ограничить число клипов (пилот)")
    ap.add_argument("--check-files", dest="check_files", action="store_true", default=True)
    ap.add_argument("--no-files", dest="check_files", action="store_false")
    ap.add_argument("--scan-orphans", dest="scan_orphans", action="store_true", default=True)
    ap.add_argument("--no-scan-orphans", dest="scan_orphans", action="store_false")
    ap.add_argument("--hash-mode", choices=("none", "candidates", "all"), default="candidates")
    ap.add_argument("--hash-max-bytes", type=int, default=8_000_000_000)
    ap.add_argument("--hash-prefix-mb", type=float, default=1.0,
                    help="0 = без prefix-фильтра (читать всё)")
    ap.add_argument("--phash-max", type=int, default=48, help="0 = не считать pHash")
    ap.add_argument("--semantic-threshold", type=float, default=0.92)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--jobs", type=int, default=4, help="параллельные чтения файлов")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    def log(*a):
        if not args.quiet:
            print(*a, flush=True)

    if not args.db.exists():
        log(f"[AUDIT] нет БД: {args.db}")
        return 2
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    st = stamp()
    run_id = "audit-" + uuid.uuid4().hex[:12]
    started = now_iso()
    t_start = time.time()
    json_path = args.json or out_dir / f"audit_library_{st}.json"
    md_path = args.md or out_dir / f"audit_library_{st}.md"
    fp_path = out_dir / f"audit_library_{st}_fingerprints.json"

    log("=" * 78)
    log("АУДИТ БИБЛИОТЕКИ B-ROLL (read-only)")
    log("=" * 78)
    log(f"БД:   {args.db}")
    log(f"Root: {args.root}")

    conn = connect_ro(args.db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "clips" not in tables:
        log("[AUDIT] в БД нет таблицы clips")
        return 2
    rows = [dict(r) for r in conn.execute("SELECT * FROM clips ORDER BY id")]
    if args.limit:
        rows = rows[: int(args.limit)]
        log(f"[AUDIT] пилот: {len(rows)} клипов (--limit)")
    db_size = args.db.stat().st_size
    files = [str(r.get("file") or "") for r in rows]
    log(f"Клипов: {len(rows)} | размер БД: {human_bytes(db_size)}")

    # ---- файлы -----------------------------------------------------------
    stats: dict[str, dict] = {}
    if args.check_files:
        t0 = time.time()
        stats = stat_files(files, args.workers)
        log(f"stat файлов: {time.time() - t0:.1f}s")
    missing = [f for f in files if args.check_files and not (stats.get(f) or {}).get("exists")]
    zero_size = [f for f in files if args.check_files and (stats.get(f) or {}).get("size") == 0]
    sizes = {f: int((stats.get(f) or {}).get("size") or 0) for f in files}
    readable_files = [f for f in files if sizes.get(f, 0) > 0]
    total_bytes = sum(sizes.values())

    # ---- inventory -------------------------------------------------------
    providers = Counter(str(r.get("provider") or "unknown") for r in rows)
    categories = Counter(str(r.get("category") or "none") for r in rows)
    orientations = Counter(derive_orientation(r.get("width"), r.get("height"),
                                              r.get("orientation")) for r in rows)
    resolutions = Counter(f"{int(r.get('width') or 0)}x{int(r.get('height') or 0)}" for r in rows)
    durations = Counter()
    duration_zero = 0
    for r in rows:
        d = float(r.get("duration") or 0)
        if d <= 0:
            duration_zero += 1
            durations["0"] += 1
        elif d < 5:
            durations["<5s"] += 1
        elif d < 10:
            durations["5-10s"] += 1
        elif d < 20:
            durations["10-20s"] += 1
        elif d < 30:
            durations["20-30s"] += 1
        else:
            durations[">30s"] += 1
    db_tables = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(tables)}
    log(f"Провайдеры: {dict(providers)}")
    log(f"Файлы на диске: {human_bytes(total_bytes)} | missing: {len(missing)} | zero: {len(zero_size)}")

    # ---- провенанс -------------------------------------------------------
    no_source = [r for r in rows if not str(r.get("source_url") or "").strip()]
    no_author = [r for r in rows if not str(r.get("author") or "").strip()]
    no_license = [r for r in rows if not str(r.get("license") or "").strip()]
    no_license_url = [r for r in rows if not str(r.get("license_url") or "").strip()]
    localish = [r for r in rows if str(r.get("provider") or "") in ("local", "series_pool")
                or str(r.get("category") or "") in ("local", "custom")]
    suspicious_url = []
    for r in rows:
        u = str(r.get("source_url") or "").lower()
        p = str(r.get("provider") or "").lower()
        if not u:
            continue
        if ("pixabay" in p and "pixabay" not in u) or ("pexels" in p and "pexels" not in u):
            suspicious_url.append(r)

    certs, cert_total, cert_broken = load_certs(args.root)
    clip_names = {Path(f).name for f in files}
    cert_names = set(certs)
    no_cert = sorted(clip_names - cert_names)
    extra_certs = sorted(cert_names - clip_names)
    cert_empty_page = sum(1 for c in certs.values()
                          if not str((c.get("data") or {}).get("page_url") or "").strip())
    cert_empty_source = sum(1 for c in certs.values()
                            if not str((c.get("data") or {}).get("source_url") or "").strip())
    fixable_url = 0
    fixable_author = 0
    for r in rows:
        c = (certs.get(Path(str(r.get("file") or "")).name) or {}).get("data") or {}
        if not str(r.get("source_url") or "").strip():
            if str(c.get("page_url") or c.get("source_url") or "").strip():
                fixable_url += 1
        if not str(r.get("author") or "").strip():
            if str(c.get("author") or "").strip():
                fixable_author += 1
    log(f"Провенанс: без source_url {len(no_source)}, без author {len(no_author)}, "
        f"без сертификата {len(no_cert)}")

    # ---- описания/теги/эмбеддинги ---------------------------------------
    empty_desc = [r for r in rows if not str(r.get("description") or "").strip()]
    short_desc = [r for r in rows if len(str(r.get("description") or "").strip()) < 20]
    empty_tags = [r for r in rows if norm_json_list(r.get("tags")) == "[]"]
    emb, emb_meta = load_embeddings(args.embeddings)
    emb_files = set(emb)
    no_emb = [f for f in files if f not in emb_files]
    extra_emb = sorted(emb_files - set(files))
    no_emb_rows = [r for r in rows if str(r.get("file") or "") not in emb_files]
    log(f"Эмбеддинги: {len(files) - len(no_emb)}/{len(files)} | без вектора: {len(no_emb)}")

    # ---- дубли: sha256 ---------------------------------------------------
    sha_map: dict[str, str] = {}
    phash_map: dict[str, dict] = {}
    hash_stats: dict = {}
    # sha256/phash могли уже быть заполнены (v2-БД или прошлый аудит)
    for r in rows:
        f = str(r.get("file") or "")
        if r.get("sha256"):
            sha_map[f] = str(r["sha256"])
        if r.get("phash"):
            phash_map.setdefault(f, {"value": str(r["phash"]), "frame_s": 0.0})
    if args.hash_mode != "none" and readable_files:
        log(f"sha256: {args.hash_mode} (prefix {args.hash_prefix_mb} MB)...")
        computed_sha, hash_stats = hash_candidates(
            readable_files, sizes, args.hash_max_bytes, args.jobs,
            prefix_mb=args.hash_prefix_mb,
            cache_path=out_dir / "_hash_cache.json", log=log)
        sha_map.update(computed_sha)
    exact_clusters = make_clusters([(f, d) for f, d in sha_map.items()])
    exact_members = {f for cl in exact_clusters for f in cl}
    exact_waste = sum(sum(sizes.get(f, 0) for f in cl[1:]) for cl in exact_clusters)

    # source-id дубли (один и тот же ролик скачан под разными именами)
    src_items = []
    for r in rows:
        f = str(r.get("file") or "")
        aid = extract_asset_id(str(r.get("source_url") or ""), f, str(r.get("provider") or ""))
        if aid:
            src_items.append((f, f"{r.get('provider')}:{aid}"))
    source_clusters = make_clusters(src_items)

    # ---- дубли: semantic CLIP/текстовые эмбеддинги ----------------------
    sem_clusters, sem_meta = semantic_duplicates(emb, set(files),
                                                 args.semantic_threshold, log=log)
    sem_members = {f for cl in sem_clusters for f in cl["files"]}

    # ---- дубли: pHash ----------------------------------------------------
    phash_stats: dict = {}
    phash_clusters: list[list[str]] = []
    if args.phash_max > 0 and readable_files:
        prio: list[str] = []
        for cl in exact_clusters:
            prio.extend(cl)
        for cl in sem_clusters[:20]:
            prio.extend(cl["files"])
        for cl in source_clusters:
            prio.extend(cl)
        # добираем кандидатов из групп одинакового размера
        by_size: dict[int, list[str]] = defaultdict(list)
        for f in readable_files:
            by_size[sizes.get(f, 0)].append(f)
        for group in by_size.values():
            if len(group) > 1:
                prio.extend(group)
        seen: set[str] = set()
        ordered = []
        for f in prio:
            if f not in seen:
                seen.add(f)
                ordered.append(f)
        if len(ordered) < args.phash_max:
            ordered.extend(f for f in sorted(readable_files, key=lambda x: -sizes.get(x, 0))
                           if f not in seen)
        ordered = ordered[: args.phash_max]
        log(f"pHash: {len(ordered)} кандидатов...")
        durations_by_file = {str(r.get("file") or ""): float(r.get("duration") or 0) for r in rows}
        computed_ph, phash_stats = compute_phash(ordered, stats, durations_by_file,
                                                 args.phash_max, args.jobs, log=log)
        phash_map.update(computed_ph)
        pairs = []
        plist = sorted(phash_map)
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                a, b = plist[i], plist[j]
                ra = next((r for r in rows if str(r.get("file") or "") == a), None)
                rb = next((r for r in rows if str(r.get("file") or "") == b), None)
                if not ra or not rb:
                    continue
                wa, ha = int(ra.get("width") or 0), int(ra.get("height") or 0)
                wb, hb = int(rb.get("width") or 0), int(rb.get("height") or 0)
                aa = (wa / ha) if ha else 0
                ab = (wb / hb) if hb else 0
                if aa and ab and abs(aa - ab) > 0.08:
                    continue
                dist = (int(phash_map[a]["value"], 16) ^ int(phash_map[b]["value"], 16)).bit_count()
                if dist <= 8:
                    pairs.append((a, b, dist))
        dsu = DSU()
        for a, b, _d in pairs:
            dsu.union(a, b)
        phash_clusters = dsu.clusters()
        phash_stats["pairs"] = len(pairs)
        phash_stats["clusters"] = len(phash_clusters)
        phash_stats["members"] = sum(len(c) for c in phash_clusters)
        log(f"  pHash: {len(pairs)} близких пар -> {len(phash_clusters)} кластеров")

    # ---- файлы вне индекса / зеркало / вторая БД ------------------------
    orphan_disk: list[str] = []
    if args.scan_orphans and args.root.exists():
        t0 = time.time()
        on_disk = walk_library(args.root)
        orphan_disk = sorted(on_disk - set(files))
        log(f"Скан диска: {len(on_disk)} файлов, вне индекса {len(orphan_disk)} "
            f"({time.time() - t0:.1f}s)")

    index_meta: dict = {"exists": False}
    idx_path = args.root / "library_index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            idx_files = {str(x.get("file") or "") for x in idx if isinstance(x, dict)}
            index_meta = {
                "exists": True, "path": str(idx_path), "count": len(idx),
                "files_only_index": len(idx_files - set(files)),
                "files_only_db": len(set(files) - idx_files),
                "sample_only_index": sorted(idx_files - set(files))[:5],
                "sample_only_db": sorted(set(files) - idx_files)[:5],
            }
        except Exception as exc:
            index_meta = {"exists": True, "error": str(exc)[:160]}

    alt_meta: dict = {"exists": False}
    if args.alt_db.exists() and args.alt_db.resolve() != args.db.resolve():
        try:
            ac = connect_ro(args.alt_db)
            at = {r[0] for r in ac.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            alt_files: set[str] = set()
            if "clips" in at:
                try:
                    alt_files = {str(r[0]) for r in ac.execute("SELECT file FROM clips")}
                except Exception:
                    alt_files = set()
            alt_meta = {
                "exists": True, "path": str(args.alt_db),
                "size": args.alt_db.stat().st_size, "tables": sorted(at),
                "clips": len(alt_files),
                "files_only_alt": len(alt_files - set(files)),
                "files_only_primary": len(set(files) - alt_files),
            }
            ac.close()
        except Exception as exc:
            alt_meta = {"exists": True, "path": str(args.alt_db), "error": str(exc)[:160]}

    # orphan rows в служебных таблицах
    db_hygiene: dict = {"tables": db_tables}
    for tname, col in (("usage", "file"), ("scores", "file"), ("clip_perf", "path")):
        if tname in tables:
            try:
                vals = {str(r[0]) for r in conn.execute(f"SELECT {col} FROM {tname}")}
                db_hygiene[f"{tname}_rows"] = len(vals)
                db_hygiene[f"{tname}_orphans"] = len(vals - set(files))
            except Exception as exc:
                db_hygiene[f"{tname}_error"] = str(exc)[:120]
    conn.close()

    # ---- health ----------------------------------------------------------
    dup_union = exact_members | sem_members | {f for cl in phash_clusters for f in cl}
    meta_gap = sum(1 for r in rows
                   if float(r.get("duration") or 0) <= 0
                   or derive_orientation(r.get("width"), r.get("height"), r.get("orientation")) == "unknown")
    consistency_issues = (len(extra_emb) + len(extra_certs) + len(orphan_disk)
                          + int(index_meta.get("files_only_index", 0))
                          + int(index_meta.get("files_only_db", 0)))
    # провенанс-гэп считаем как уникальные клипы с любым из двух пропусков
    prov_gap = sum(1 for r in rows
                   if not str(r.get("source_url") or "").strip()
                   or not str(r.get("author") or "").strip())
    score, score_parts = health_score(len(rows), len(missing), prov_gap, len(dup_union),
                                      len(no_emb), len(short_desc), meta_gap, consistency_issues)
    log(f"Индекс здоровья: {score}/100 | дубли-кандидаты: {len(dup_union)} файлов")

    # ---- отчёт -----------------------------------------------------------
    cascade = {
        "stage1_size_duplicate_groups": hash_stats.get("size_duplicate_groups", 0),
        "stage2_sha256_candidate_files": hash_stats.get("candidate_files", 0),
        "stage2_sha256_hashed": hash_stats.get("hashed", 0),
        "stage3_exact_clusters": len(exact_clusters),
        "stage3_exact_members": len(exact_members),
        "stage4_phash_computed": phash_stats.get("computed", 0),
        "stage5_phash_clusters": len(phash_clusters),
        "stage5_phash_members": sum(len(c) for c in phash_clusters),
        "stage6_semantic_pairs": sem_meta.get("pairs", 0),
        "stage6_semantic_clusters": len(sem_clusters),
        "stage6_semantic_members": len(sem_members),
    }

    report = {
        "run_id": run_id,
        "tool": "audit_library.py",
        "mode": "read-only",
        "started_at": started,
        "finished_at": now_iso(),
        "duration_seconds": round(time.time() - t_start, 1),
        "db": str(args.db),
        "db_size": db_size,
        "root": str(args.root),
        "root_exists": args.root.exists(),
        "limit": args.limit,
        "inventory": {
            "total": len(rows),
            "total_bytes": total_bytes,
            "avg_bytes": round(total_bytes / len(rows), 1) if rows else 0,
            "by_provider": top(providers, 40),
            "by_category": top(categories, 60),
            "by_orientation": top(orientations, 10),
            "by_resolution": top(resolutions, 20),
            "by_duration": dict(durations),
            "duration_zero": duration_zero,
            "db_tables": db_tables,
        },
        "files": {
            "checked": args.check_files,
            "missing": len(missing),
            "missing_sample": missing[:50],
            "zero_size": len(zero_size),
            "disk_bytes": total_bytes,
            "orphans_on_disk": len(orphan_disk),
            "orphans_sample": orphan_disk[:50],
        },
        "provenance": {
            "no_source_url": len(no_source),
            "no_author": len(no_author),
            "no_license": len(no_license),
            "no_license_url": len(no_license_url),
            "local_or_custom": len(localish),
            "local_or_custom_pct": round(100.0 * len(localish) / len(rows), 1) if rows else 0,
            "suspicious_provider_url": len(suspicious_url),
            "certificates": cert_total,
            "cert_broken": cert_broken,
            "clips_without_certificate": len(no_cert),
            "clips_without_certificate_sample": no_cert[:20],
            "extra_certificates": len(extra_certs),
            "cert_empty_page_url": cert_empty_page,
            "cert_empty_source_url": cert_empty_source,
            "fixable_source_url_from_cert": fixable_url,
            "fixable_author_from_cert": fixable_author,
            "by_provider_missing_source": top(Counter(
                str(r.get("provider") or "unknown") for r in no_source), 20),
            "by_provider_missing_author": top(Counter(
                str(r.get("provider") or "unknown") for r in no_author), 20),
        },
        "content": {
            "empty_description": len(empty_desc),
            "short_description_lt20": len(short_desc),
            "empty_tags": len(empty_tags),
            "embeddings": emb_meta,
            "with_embedding": len(files) - len(no_emb),
            "without_embedding": len(no_emb),
            "without_embedding_sample": no_emb[:30],
            "extra_embeddings": len(extra_emb),
            "extra_embeddings_sample": extra_emb[:10],
        },
        "duplicates": {
            "cascade": cascade,
            "sha256": {"hashed": len(sha_map), **hash_stats},
            "exact": {
                "clusters": len(exact_clusters),
                "members": len(exact_members),
                "wasted_bytes": exact_waste,
                "top": [{"files": cl, "size": len(cl),
                         "bytes_each": sizes.get(cl[0], 0)} for cl in exact_clusters[:20]],
            },
            "source_id": {
                "clusters": len(source_clusters),
                "top": [{"files": cl, "size": len(cl)} for cl in source_clusters[:20]],
            },
            "phash": {
                **phash_stats,
                "clusters": len(phash_clusters),
                "top": [{"files": cl, "size": len(cl)} for cl in phash_clusters[:20]],
            },
            "semantic": {
                **sem_meta,
                "top": sem_clusters[:20],
            },
            "note": "Кандидаты. Аудит ничего не удаляет и не меняет БД; "
                    "semantic — похожие описания/сцены, не обязательно один файл.",
        },
        "hygiene": {
            "index_mirror": index_meta,
            "alt_db": alt_meta,
            "db": db_hygiene,
            "two_db_risk": bool(alt_meta.get("exists")),
        },
        "health": {
            "score": score,
            "penalties": score_parts,
            "formula": "100 -30*missing -20*provenance -15*duplicates -15*no_embedding "
                       "-10*short_desc -5*metadata -5*consistency (доли от total)",
        },
        "fingerprints_file": str(fp_path),
    }

    fp_payload = {
        "generated_at": now_iso(),
        "db": str(args.db),
        "root": str(args.root),
        "sha256": sha_map,
        "phash": phash_map,
        "sizes": {f: sizes.get(f, 0) for f in files},
        "stats": {"hash": hash_stats, "phash": phash_stats},
    }
    fp_path.write_text(json.dumps(fp_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- markdown --------------------------------------------------------
    def rows_table(items: list[dict], k1: str = "key", k2: str = "count") -> str:
        return "\n".join(f"| {x[k1]} | {x[k2]} |" for x in items)

    md = []
    md.append(f"# Аудит библиотеки B-roll — {st}")
    md.append("")
    md.append(f"- **БД:** `{args.db}` ({human_bytes(db_size)})")
    md.append(f"- **Root:** `{args.root}`")
    md.append(f"- **Режим:** read-only, без изменений БД")
    md.append(f"- **Клипов:** {len(rows)} | **объём:** {human_bytes(total_bytes)}")
    md.append(f"- **Индекс здоровья:** **{score}/100**")
    md.append(f"- **Длительность аудита:** {report['duration_seconds']} c")
    md.append("")
    md.append("## 1. Ключевые цифры")
    md.append("")
    md.append("| Метрика | Значение |")
    md.append("|---|---|")
    md.append(f"| Клипов | {len(rows)} |")
    md.append(f"| Файлов missing / zero | {len(missing)} / {len(zero_size)} |")
    md.append(f"| Без source_url | {pct(len(no_source), len(rows))} |")
    md.append(f"| Без author | {pct(len(no_author), len(rows))} |")
    md.append(f"| local/custom | {pct(len(localish), len(rows))} |")
    md.append(f"| Без сертификата | {pct(len(no_cert), len(rows))} |")
    md.append(f"| Без эмбеддинга | {pct(len(no_emb), len(rows))} |")
    md.append(f"| Пустые / короткие описания | {len(empty_desc)} / {len(short_desc)} |")
    md.append(f"| Дубли: exact / phash / semantic | {len(exact_clusters)} / {len(phash_clusters)} / {len(sem_clusters)} |")
    md.append(f"| Файлов вне индекса на диске | {len(orphan_disk)} |")
    md.append(f"| Вторая БД | {'да' if alt_meta.get('exists') else 'нет'} |")
    md.append("")
    md.append("## 2. Inventory")
    md.append("")
    md.append(f"Провайдеры: `{dict(providers)}`")
    md.append("")
    md.append("| Провайдер | Клипов |")
    md.append("|---|---|")
    md.append(rows_table(top(providers, 20)))
    md.append("")
    md.append("| Разрешение | Клипов |")
    md.append("|---|---|")
    md.append(rows_table(top(resolutions, 12)))
    md.append("")
    md.append(f"Длительности: `{dict(durations)}`; без длительности: {duration_zero}.")
    md.append("")
    md.append("## 3. Провенанс и лицензии")
    md.append("")
    md.append(f"- Без `source_url`: **{len(no_source)}**; без `author`: **{len(no_author)}**; "
              f"без `license`: {len(no_license)}.")
    md.append(f"- local/custom (в т.ч. старая коллекция): **{len(localish)}** "
              f"({report['provenance']['local_or_custom_pct']}%).")
    md.append(f"- Сертификатов: {cert_total} (битых {cert_broken}); без сертификата: **{len(no_cert)}**; "
              f"лишних сертификатов: {len(extra_certs)}.")
    md.append(f"- Можно восстановить из сертификатов: source_url **{fixable_url}**, author **{fixable_author}**.")
    md.append(f"- cert без page_url: {cert_empty_page}; без source_url: {cert_empty_source}; "
              f"подозрительный домен URL: {len(suspicious_url)}.")
    md.append("")
    md.append("Пропуски source_url по провайдерам:")
    md.append("")
    md.append("| Провайдер | Без source_url |")
    md.append("|---|---|")
    md.append(rows_table(top(Counter(str(r.get('provider') or 'unknown') for r in no_source), 20)))
    md.append("")
    md.append("## 4. Дубли (только кандидаты)")
    md.append("")
    md.append("| Стадия | Результат |")
    md.append("|---|---|")
    md.append(f"| size-группы (кандидаты) | {cascade['stage1_size_duplicate_groups']} |")
    md.append(f"| sha256 посчитано | {cascade['stage2_sha256_hashed']} файлов |")
    md.append(f"| exact-кластеры | {len(exact_clusters)} ({len(exact_members)} файлов, "
              f"{human_bytes(exact_waste)} лишних) |")
    md.append(f"| pHash посчитано | {phash_stats.get('computed', 0)} (sampled {phash_stats.get('sampled', 0)}) |")
    md.append(f"| pHash-кластеры | {len(phash_clusters)} |")
    md.append(f"| semantic-пары ≥ {args.semantic_threshold} | {sem_meta.get('pairs', 0)} |")
    md.append(f"| semantic-кластеры | {len(sem_clusters)} ({len(sem_members)} файлов) |")
    md.append(f"| одинаковый provider id | {len(source_clusters)} |")
    md.append("")
    md.append("Ничего не удалено. `semantic` — похожие описания/сцены, а не обязательно "
              "побайтовый дубль; решение принимает человек.")
    md.append("")
    if exact_clusters:
        md.append("### Exact-дубли (top)")
        md.append("")
        for cl in exact_clusters[:10]:
            md.append(f"- {len(cl)} файлов, по {human_bytes(sizes.get(cl[0], 0))}:")
            for f in cl[:6]:
                md.append(f"  - `{f}`")
        md.append("")
    if sem_clusters:
        md.append("### Semantic-кластеры (top)")
        md.append("")
        for cl in sem_clusters[:8]:
            md.append(f"- {cl['size']} файлов, cosine avg={cl.get('score_avg')}:")
            for f in cl["files"][:6]:
                md.append(f"  - `{f}`")
        md.append("")
    md.append("## 5. Файлы и гигиена")
    md.append("")
    md.append(f"- missing: {len(missing)}; zero-size: {len(zero_size)}; "
              f"вне индекса: {len(orphan_disk)}.")
    md.append(f"- `library_index.json`: {index_meta}.")
    md.append(f"- вторая БД: {alt_meta}.")
    md.append(f"- служебные таблицы: {db_tables}.")
    for tname in ("usage", "scores", "clip_perf"):
        if f"{tname}_orphans" in db_hygiene:
            md.append(f"- `{tname}`: строк {db_hygiene.get(f'{tname}_rows')}, "
                      f"orphan {db_hygiene.get(f'{tname}_orphans')}.")
    md.append("")
    md.append("## 6. Здоровье и приоритеты")
    md.append("")
    md.append(f"**{score}/100.** Штрафы: `{score_parts}`.")
    md.append("")
    md.append("1. Закрыть missing/offline файлы (30% веса).")
    md.append("2. Провенанс: source_url/author/сертификаты (20%).")
    md.append("3. Разобрать exact/semantic дубли (15%).")
    md.append("4. Добить эмбеддинги и описания (25%).")
    md.append("5. Убрать вторую БД/рассинхрон зеркала (5%).")
    md.append("")
    md.append("## 7. Артефакты")
    md.append("")
    md.append(f"- JSON: `{json_path}`")
    md.append(f"- Markdown: `{md_path}`")
    md.append(f"- Fingerprints (для миграции): `{fp_path}`")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    log("-" * 78)
    log(f"JSON: {json_path}")
    log(f"MD:   {md_path}")
    log(f"FP:   {fp_path}")
    log(f"Готово за {report['duration_seconds']} c")
    return 0


if __name__ == "__main__":
    sys.exit(main())
