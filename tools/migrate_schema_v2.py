#!/usr/bin/env python3
"""Миграция библиотеки B-roll в схему v2 — ТОЛЬКО НА КОПИИ БД.

По умолчанию dry-run: делается консистентная копия рабочей SQLite (backup API),
на ней применяется tools/schema_v2.sql и переносятся данные; после проверок
копия удаляется, остаётся JSON-отчёт. Production-БД открывается ТОЛЬКО read-only.

    python3 tools/migrate_schema_v2.py                    # dry-run на копии
    python3 tools/migrate_schema_v2.py --keep             # оставить копию для осмотра
    python3 tools/migrate_schema_v2.py --apply            # создать ~/video_maker/broll_library_v2.db
    python3 tools/migrate_schema_v2.py --apply --target /path/to/copy.db
    python3 tools/migrate_schema_v2.py --fingerprints ~/video_maker/audit/....json

Что переносится:
  clips -> clips (asset_uuid, duration_ms, status, needs_review/review_reasons, ...)
  provider/source_url -> sources (+ page_url/author из сертификатов)
  licenses/*.json -> certificates
  taxonomy.THEMES -> themes (30)
  moods: словарь 40 уже в schema_v2.sql (эмбеддинги заполняет mood_embeddings.py)
  broll_embeddings.json -> clip_embeddings (только метаданные, без 66 МБ векторов)
  usage -> clip_usage
  sha256/phash из audit_library.py --fingerprints -> clips + clip_fingerprints
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_PATH = Path(__file__).with_name("schema_v2.sql")
PROD_DB = Path.home() / "video_maker" / "broll_library.db"
DEFAULT_SOURCE = PROD_DB
DEFAULT_TARGET = Path.home() / "video_maker" / "broll_library_v2.db"
DEFAULT_MIGRATION_DIR = Path.home() / "video_maker" / "migration"
DEFAULT_ROOT = Path("/Volumes/video/broll_library")
DEFAULT_EMB = Path.home() / "video_maker" / "broll_embeddings.json"
EMB_MODEL_FALLBACK = "gemini-embedding-001"
NS_CLIP = uuid.NAMESPACE_URL

PROVENANCE_REASONS = {
    "missing_file",
    "missing_source_url",
    "missing_author",
    "local_collection",
    "missing_certificate",
    "missing_duration",
    "missing_orientation",
    "duplicate_sha256",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


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


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def copy_db(src: Path, dst: Path) -> None:
    """Консистентная копия живой SQLite через backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(dst) + suffix).unlink()
        except FileNotFoundError:
            pass
    s = connect_ro(src)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
        d.commit()
    finally:
        d.close()
        s.close()


def norm_json_list(value) -> str:
    """Приводит tags/neg_tags к валидному JSON-массиву строк."""
    if value is None:
        return "[]"
    if isinstance(value, (list, tuple, set)):
        items = [str(x) for x in value if str(x).strip()]
        return json.dumps(items, ensure_ascii=False)
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
    items = [x.strip() for x in re.split(r"[,;]\s*", s) if x.strip()]
    return json.dumps(items, ensure_ascii=False)


def derive_orientation(width: int, height: int, old: str | None = None) -> str:
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


def extract_asset_id(url: str, filename: str, provider: str) -> str:
    """Провайдерский id клипа: Pixabay video id / Pexels id.

    Важно: год в CDN-пути (/video/2020/06/01/…) — НЕ id. Сначала ищем id
    на странице клипа и в имени файла, и только потом — в basename CDN.
    """
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
        r"^(\d{4,})[-_]",              # 282995_large.mp4 / 40797-426939500_large.mp4
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


def load_legacy_clips(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clips)")}
    base = ["id", "file", "rel_path", "provider", "source_url", "author", "license",
            "license_url", "query", "category", "description", "tags", "neg_tags",
            "score", "blur", "width", "height", "duration", "orientation", "bitrate",
            "created_at"]
    select = [c if c in cols else f"NULL AS {c}" for c in base]
    rows = conn.execute("SELECT " + ", ".join(select) + " FROM clips ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def stat_clips(files: list[str], workers: int = 32) -> dict[str, dict]:
    def one(f: str):
        try:
            st = os.stat(f)
            return f, {"size": int(st.st_size),
                       "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
        except OSError:
            return f, {"size": None, "mtime": None}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for f, info in ex.map(one, files):
            out[f] = info
    return out


def load_certs(root: Path, workers: int = 16) -> tuple[dict[str, dict], int, int]:
    """cert key (basename) -> {data, path, sha256}. Возвращает (map, total, broken)."""
    lic = root / "licenses"
    if not lic.exists():
        return {}, 0, 0
    paths = [p for p in sorted(lic.glob("*.json")) if not p.name.startswith("._")]

    def one(p: Path):
        try:
            raw = p.read_bytes()
            return p, json.loads(raw), hashlib.sha256(raw).hexdigest(), False
        except Exception:
            return p, None, None, True

    certs: dict[str, dict] = {}
    broken = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p, d, digest, bad in ex.map(one, paths):
            if bad or not isinstance(d, dict):
                broken += 1
                continue
            key = ""
            for cand in (d.get("clip"), Path(str(d.get("file") or "")).name, p.name[:-5]):
                if cand:
                    key = Path(str(cand)).name
                    break
            if key:
                certs[key] = {"data": d, "path": p, "sha256": digest}
    return certs, len(paths), broken


def load_themes() -> list[dict]:
    try:
        from video_maker.semantic.taxonomy import THEMES
    except Exception:
        return []
    out = []
    for slug, item in THEMES.items():
        out.append({
            "slug": slug,
            "title_ru": slug.replace("_", " "),
            "title_en": slug.replace("_", " "),
            "description": "",
            "ru_stems": list(item.get("ru") or []),
            "en_queries": list(item.get("en") or []),
        })
    return out


def embedding_model() -> str:
    try:
        from video_maker.semantic.embeddings import MODEL
        return str(MODEL)
    except Exception:
        return EMB_MODEL_FALLBACK


def clip_text(rec: dict) -> str:
    """Копия embeddings.clip_text — чтобы не тянуть сетевой модуль в миграцию."""
    tags = str(rec.get("tags") or "").replace('"', " ").replace("[", " ").replace("]", " ")
    parts = [str(rec.get("description") or ""), tags]
    return " ".join(p for p in parts if p.strip())[:1500]


def load_embeddings(path: Path) -> tuple[dict, str | None, int]:
    if not path.exists():
        return {}, None, 0
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        return data, hashlib.sha256(raw).hexdigest(), len(raw)
    except Exception:
        return {}, None, 0


def load_fingerprints(path: Path | None) -> dict:
    if not path:
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as exc:
        print(f"[MIGRATE] fingerprints не прочитаны: {exc}")
        return {}


def normalize_phash(value) -> tuple[str | None, float]:
    if value is None:
        return None, 0.0
    if isinstance(value, dict):
        v = value.get("value") or value.get("phash")
        ts = value.get("frame_s", value.get("frame_ms", 0))
        try:
            ts = float(ts)
            if ts > 1000:  # был frame_ms
                ts /= 1000.0
        except (TypeError, ValueError):
            ts = 0.0
        return (str(v) if v else None), ts
    return str(value), 0.0


def hash_candidates(files: list[str], sizes: dict[str, int], max_bytes: int,
                    workers: int, mode: str = "candidates") -> tuple[dict[str, str], dict]:
    groups: dict[int, list[str]] = {}
    for f in files:
        s = sizes.get(f)
        if s and s > 0:
            groups.setdefault(int(s), []).append(f)
    todo: list[str] = []
    if mode == "all":
        todo = [f for group in groups.values() for f in group]
    else:
        for s, group in sorted(groups.items()):
            if len(group) > 1:
                todo.extend(group)
    planned = sum(sizes.get(f, 0) for f in todo)
    skipped = 0
    if planned > max_bytes:
        keep: list[str] = []
        used = 0
        for f in sorted(todo, key=lambda x: sizes.get(x, 0)):
            if used + sizes.get(f, 0) > max_bytes:
                skipped += 1
                continue
            keep.append(f)
            used += sizes.get(f, 0)
        todo = keep
    out: dict[str, str] = {}
    t0 = time.time()

    def one(f: str):
        return f, sha256_file(f)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, f) for f in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            f, digest = fut.result()
            if digest:
                out[f] = digest
            if i % 25 == 0 or i == len(futs):
                print(f"[MIGRATE] sha256 {i}/{len(futs)} ({time.time() - t0:.1f}s)")
    return out, {"mode": mode, "candidates": len(todo), "hashed": len(out),
                 "skipped_bytes_cap": skipped,
                 "bytes": sum(sizes.get(f, 0) for f in todo), "seconds": round(time.time() - t0, 1)}


def needs_review(reasons: list[str]) -> int:
    return int(any(r in PROVENANCE_REASONS for r in reasons))


def human_pct(n: int, total: int) -> str:
    return f"{n} ({100.0 * n / total:.1f}%)" if total else f"{n} (0.0%)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Миграция B-roll библиотеки на схему v2 (только копия)")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--fingerprints", type=Path, default=None,
                    help="JSON от audit_library.py с sha256/phash")
    ap.add_argument("--apply", action="store_true", help="оставить мигрированную копию (не production)")
    ap.add_argument("--force", action="store_true", help="перезаписать target (старый будет .bak)")
    ap.add_argument("--keep", action="store_true", help="в dry-run оставить временную копию")
    ap.add_argument("--hash-mode", choices=("none", "candidates", "all"), default="none")
    ap.add_argument("--hash-max-bytes", type=int, default=8_000_000_000)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    if not SCHEMA_PATH.exists():
        print(f"[MIGRATE] нет схемы: {SCHEMA_PATH}")
        return 2
    if not args.source.exists():
        print(f"[MIGRATE] нет исходной БД: {args.source}")
        return 2
    if args.source.resolve() == args.target.resolve():
        print("[MIGRATE] source и target совпадают — запрещено")
        return 2
    if args.apply and args.target.resolve() == PROD_DB.resolve():
        print("[MIGRATE] target указывает на production-БД — запись запрещена")
        return 2

    started = now_iso()
    run_id = "mig-" + uuid.uuid4().hex[:12]
    mode = "apply" if args.apply else "dry-run"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    DEFAULT_MIGRATION_DIR.mkdir(parents=True, exist_ok=True)
    if args.apply:
        work = args.target
        report_path = args.report or DEFAULT_MIGRATION_DIR / f"migrate_schema_v2_{stamp}.json"
        if work.exists():
            if not args.force:
                print(f"[MIGRATE] target уже существует: {work}")
                print("[MIGRATE] укажите другой --target или --force (старый будет сохранён как .bak)")
                return 2
            bak = work.with_name(work.name + f".bak.{stamp}")
            shutil.move(str(work), str(bak))
            print(f"[MIGRATE] старый target сохранён: {bak}")
    else:
        work = DEFAULT_MIGRATION_DIR / "_schema_v2_dryrun.db"
        report_path = args.report or DEFAULT_MIGRATION_DIR / f"migrate_schema_v2_dryrun_{stamp}.json"

    source_size = args.source.stat().st_size
    source_sha_before = sha256_file(args.source)
    print("=" * 78)
    print(f"МИГРАЦИЯ СХЕМЫ V2 — {mode}")
    print("=" * 78)
    print(f"Источник (read-only): {args.source}")
    print(f"  размер: {source_size / 1e6:.1f} МБ | sha256: {(source_sha_before or '?')[:16]}…")
    print(f"Копия для работы:     {work}")

    copy_db(args.source, work)

    src = connect_ro(args.source)
    legacy = load_legacy_clips(src)
    usage_rows = [dict(r) for r in src.execute(
        "SELECT file, series, scene, used_at FROM usage ORDER BY used_at, file")]
    src.close()
    print(f"Прочитано legacy-клипов: {len(legacy)} | usage: {len(usage_rows)}")

    certs, cert_total, cert_broken = load_certs(args.root)
    themes = load_themes()
    emb, emb_file_hash, emb_bytes = load_embeddings(args.embeddings)
    fps = load_fingerprints(args.fingerprints)
    print(f"Сертификаты: {cert_total} (битых {cert_broken}) | темы: {len(themes)} | "
          f"эмбеддинги: {len(emb)} ({emb_bytes / 1e6:.1f} МБ)")

    stats = stat_clips([r["file"] for r in legacy], workers=args.workers)
    missing_files = sum(1 for f in stats if stats[f]["size"] is None)
    print(f"Файлы: {len(stats) - missing_files}/{len(stats)} доступны | отсутствуют: {missing_files}")

    sizes = {f: int(v["size"]) for f, v in stats.items() if v.get("size") is not None}
    sha_map: dict[str, str] = {}
    phash_map: dict[str, tuple[str, float]] = {}
    sha_counts: Counter = Counter()
    # fingerprints от аудита
    for f, h in (fps.get("sha256") or {}).items():
        if f in {r["file"] for r in legacy} and h:
            sha_map[str(f)] = str(h)
    for f, val in (fps.get("phash") or {}).items():
        if f in {r["file"] for r in legacy}:
            v, ts = normalize_phash(val)
            if v:
                phash_map[str(f)] = (v, ts)
    if args.hash_mode != "none":
        print(f"sha256: режим {args.hash_mode}…")
        all_files = list(sizes)
        cand = all_files if args.hash_mode == "all" else list(sizes)
        hashed, hstats = hash_candidates(cand, sizes, args.hash_max_bytes,
                                         max(1, args.workers // 8), mode=args.hash_mode)
        sha_map.update(hashed)
        print(f"  sha256: {hstats}")
    sha_counts = Counter(sha_map.values())

    conn = sqlite3.connect(str(work))
    # Копия — это legacy-БД. Старые таблицы переименовываем, чтобы schema_v2
    # создала чистые clips/clip_usage, а история осталась в legacy_*.
    work_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(clips)")} if "clips" in work_tables else set()
    if "asset_uuid" in old_cols:
        conn.close()
        print("[MIGRATE] source уже schema v2 — миграция не нужна")
        return 2
    conn.execute("PRAGMA foreign_keys = OFF")
    if "clips" in work_tables:
        if "legacy_clips" in work_tables:
            conn.close()
            print("[MIGRATE] в копии уже есть legacy_clips — неожиданное состояние")
            return 2
        conn.execute("ALTER TABLE clips RENAME TO legacy_clips")
    if "usage" in work_tables and "legacy_usage" not in work_tables:
        conn.execute("ALTER TABLE usage RENAME TO legacy_usage")
    # старые индексы могли занять имена новых (idx_clips_category)
    for idx in ("idx_clips_url", "idx_clips_category", "idx_usage_file"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")
    conn.commit()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.commit()

    # ---- sources ---------------------------------------------------------
    source_ids: dict[tuple[str, str], int] = {}
    source_meta: dict[tuple[str, str], dict] = {}
    src_created = 0
    for rec in legacy:
        provider = str(rec.get("provider") or "unknown").strip() or "unknown"
        url = str(rec.get("source_url") or "").strip()
        aid = extract_asset_id(url, rec["file"], provider)
        if not url and not aid:
            continue
        if not aid:
            aid = "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        key = (provider, aid)
        if key in source_ids:
            continue
        cert = certs.get(Path(rec["file"]).name, {}).get("data") or {}
        page = str(cert.get("page_url") or cert.get("source_url") or "").strip() or url
        meta = {
            "provider": provider,
            "asset_id": aid,
            "page_url": page,
            "download_url": url,
            "author": str(rec.get("author") or cert.get("author") or ""),
            "license": str(rec.get("license") or cert.get("license") or ""),
            "license_url": str(rec.get("license_url") or cert.get("license_url") or ""),
            "http_status": cert.get("page_http_status"),
            "fetched_at": cert.get("fetched_at"),
            "raw_json": json.dumps(cert, ensure_ascii=False) if cert else "{}",
        }
        cur = conn.execute(
            """INSERT OR IGNORE INTO sources
               (provider, provider_asset_id, page_url, download_url, author, license,
                license_url, http_status, fetched_at, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (meta["provider"], meta["asset_id"], meta["page_url"], meta["download_url"],
             meta["author"], meta["license"], meta["license_url"], meta["http_status"],
             meta["fetched_at"], meta["raw_json"]),
        )
        sid = cur.lastrowid if cur.rowcount else conn.execute(
            "SELECT id FROM sources WHERE provider=? AND provider_asset_id=?",
            (provider, aid)).fetchone()[0]
        source_ids[key] = int(sid)
        source_meta[key] = meta
        src_created += 1

    # ---- themes ----------------------------------------------------------
    for th in themes:
        conn.execute(
            """INSERT OR IGNORE INTO themes
               (slug, title_ru, title_en, description, ru_stems, en_queries)
               VALUES (?,?,?,?,?,?)""",
            (th["slug"], th["title_ru"], th["title_en"], th["description"],
             json.dumps(th["ru_stems"], ensure_ascii=False),
             json.dumps(th["en_queries"], ensure_ascii=False)),
        )

    # ---- clips -----------------------------------------------------------
    file_to_id: dict[str, int] = {}
    clip_source_key: dict[str, tuple[str, str] | None] = {}
    enriched_author = 0
    enriched_url = 0
    source_url_count = 0
    author_count = 0
    review_reasons_count: dict[str, int] = {}
    needs_review_n = 0
    missing_emb = 0
    seen_asset_ids: set[str] = set()
    model = embedding_model()

    for rec in legacy:
        f = str(rec["file"])
        rel = str(rec.get("rel_path") or "")
        if not rel:
            try:
                rel = str(Path(f).relative_to(args.root))
            except ValueError:
                rel = f
        st = stats.get(f) or {}
        status = "active" if st.get("size") is not None else "offline"
        provider = str(rec.get("provider") or "unknown").strip() or "unknown"
        url = str(rec.get("source_url") or "").strip()
        aid = extract_asset_id(url, f, provider)
        cert = certs.get(Path(f).name, {}).get("data") or {}
        page = str(cert.get("page_url") or cert.get("source_url") or "").strip()
        if not url and page:
            url = page
            enriched_url += 1
        author = str(rec.get("author") or "").strip()
        if not author and str(cert.get("author") or "").strip():
            author = str(cert.get("author") or "").strip()
            enriched_author += 1
        license_txt = str(rec.get("license") or cert.get("license") or "").strip()
        license_url = str(rec.get("license_url") or cert.get("license_url") or "").strip()
        if url:
            source_url_count += 1
        if author:
            author_count += 1

        source_key = None
        if url or aid:
            k_aid = aid or ("url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16])
            source_key = (provider, k_aid)
        if source_key is None and provider in ("local", "series_pool"):
            pass
        clip_source_key[f] = source_key
        sid = source_ids.get(source_key) if source_key else None

        reasons: list[str] = []
        if not url:
            reasons.append("missing_source_url")
        if not author:
            reasons.append("missing_author")
        if provider in ("local", "series_pool") and not url:
            reasons.append("local_collection")
        if not (rec.get("duration") or 0):
            reasons.append("missing_duration")
        orientation = derive_orientation(rec.get("width"), rec.get("height"), rec.get("orientation"))
        if orientation == "unknown":
            reasons.append("missing_orientation")
        if Path(f).name not in certs:
            reasons.append("missing_certificate")
        if f not in emb:
            missing_emb += 1
        if st.get("size") is None:
            reasons.append("missing_file")
        sha = sha_map.get(f)
        if sha and sha_counts.get(sha, 0) > 1:
            reasons.append("duplicate_sha256")
        for r in reasons:
            review_reasons_count[r] = review_reasons_count.get(r, 0) + 1
        nr = needs_review(reasons)
        needs_review_n += nr

        ph, ph_ts = phash_map.get(f, (None, 0.0))
        if sha and sha_counts.get(sha, 0) == 1:
            asset_key = "sha256:" + sha
        elif sha:
            # точный дубль: отдельный uuid, чтобы не нарушить UNIQUE asset_uuid
            asset_key = "sha256:" + sha + ":rel:" + rel
        else:
            asset_key = "rel:" + rel
        asset_uuid = str(uuid.uuid5(NS_CLIP, "videomaker:clip:" + asset_key))
        duration_ms = int(round(float(rec.get("duration") or 0) * 1000)) if rec.get("duration") else None
        tags = norm_json_list(rec.get("tags"))
        neg_tags = norm_json_list(rec.get("neg_tags"))
        desc = str(rec.get("description") or "")
        if not desc.strip():
            try:
                desc = ", ".join(json.loads(tags))[:200]
            except Exception:
                desc = ""

        cur = conn.execute(
            """INSERT INTO clips
               (asset_uuid, legacy_id, file, rel_path, file_size, file_mtime,
                sha256, phash, status, provider, source_id, source_url, author,
                license, license_url, orientation, category, query, description,
                tags, neg_tags, score, blur, width, height, duration_ms, bitrate,
                motion, caption_ver, embedding_ver, review_reasons, needs_review,
                created_at, updated_at, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (asset_uuid, rec.get("id"), f, rel, st.get("size"), st.get("mtime"),
             sha, ph, status, provider, sid, url, author, license_txt, license_url,
             orientation, str(rec.get("category") or ""), str(rec.get("query") or ""),
             desc, tags, neg_tags, int(rec.get("score") or 0), float(rec.get("blur") or 0),
             int(rec.get("width") or 0), int(rec.get("height") or 0), duration_ms,
             float(rec.get("bitrate") or 0), None, "legacy-v1",
             model if f in emb else None, json.dumps(reasons, ensure_ascii=False), nr,
             str(rec.get("created_at") or now_iso()), now_iso(), now_iso()),
        )
        clip_id = int(cur.lastrowid)
        file_to_id[f] = clip_id
        if sha:
            conn.execute(
                """INSERT OR IGNORE INTO clip_fingerprints (clip_id, kind, value, source)
                   VALUES (?, 'sha256', ?, 'audit_library/migrate')""", (clip_id, sha))
        if ph:
            conn.execute(
                """INSERT OR IGNORE INTO clip_fingerprints (clip_id, kind, value, frame_ms, source)
                   VALUES (?, 'phash64', ?, ?, 'audit_library')""",
                (clip_id, ph, int(ph_ts * 1000)))
        if sid:
            seen_asset_ids.add(str(sid))

        # сертификат
        cert_rec = certs.get(Path(f).name)
        if cert_rec:
            d = cert_rec["data"]
            cert_sha = cert_rec.get("sha256")
            conn.execute(
                """INSERT OR IGNORE INTO certificates
                   (clip_id, file_name, provider, page_url, source_url, author, license,
                    license_url, http_status, fetched_at, cert_path, cert_sha256, note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (clip_id, Path(f).name, str(d.get("provider") or ""),
                 str(d.get("page_url") or ""), str(d.get("source_url") or ""),
                 str(d.get("author") or ""), str(d.get("license") or ""),
                 str(d.get("license_url") or ""), d.get("page_http_status"),
                 str(d.get("fetched_at") or ""), str(cert_rec["path"]), cert_sha,
                 str(d.get("note") or "")),
            )

        # эмбеддинг: только метаданные
        if f in emb:
            try:
                vec = emb[f]
                norm = sum(float(x) * float(x) for x in vec) ** 0.5
                conn.execute(
                    """INSERT OR REPLACE INTO clip_embeddings
                       (clip_id, model, dim, norm, text_hash, source, source_hash,
                        vector_ref, is_stale, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,0,?)""",
                    (clip_id, model, len(vec), norm, sha256_text(clip_text(rec)),
                     "broll_embeddings.json", emb_file_hash, f, now_iso()),
                )
            except Exception:
                pass

    # ---- usage -----------------------------------------------------------
    # usage писал пути серийных копий (/Volumes/SSD/.../clip.mp4), а не пути
    # библиотеки: сначала точный путь, затем уникальный basename.
    name_to_ids: dict[str, list[int]] = {}
    for _f, _cid in file_to_id.items():
        name_to_ids.setdefault(Path(_f).name, []).append(_cid)
    usage_inserted = 0
    usage_orphans = 0
    usage_linked_by_name = 0
    for u in usage_rows:
        f = str(u.get("file") or "")
        cid = file_to_id.get(f)
        if cid is None:
            ids = name_to_ids.get(Path(f).name) or []
            if len(ids) == 1:
                cid = ids[0]
                usage_linked_by_name += 1
        if cid is None:
            usage_orphans += 1
        conn.execute(
            """INSERT INTO clip_usage (clip_id, file, series, scene, used_at, source)
               VALUES (?,?,?,?,?, 'legacy.usage')""",
            (cid, f, str(u.get("series") or ""), u.get("scene"), str(u.get("used_at") or "")),
        )
        usage_inserted += 1

    conn.commit()

    # ---- проверки --------------------------------------------------------
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk_bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    counts = {
        "clips": conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0],
        "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "certificates": conn.execute("SELECT COUNT(*) FROM certificates").fetchone()[0],
        "themes": conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0],
        "moods": conn.execute("SELECT COUNT(*) FROM moods").fetchone()[0],
        "clip_fingerprints": conn.execute("SELECT COUNT(*) FROM clip_fingerprints").fetchone()[0],
        "clip_embeddings": conn.execute("SELECT COUNT(*) FROM clip_embeddings").fetchone()[0],
        "clip_usage": conn.execute("SELECT COUNT(*) FROM clip_usage").fetchone()[0],
        "demand_ledger": conn.execute("SELECT COUNT(*) FROM demand_ledger").fetchone()[0],
    }
    status_counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM clips GROUP BY status ORDER BY 2 DESC").fetchall())
    dup_clusters = conn.execute(
        "SELECT COUNT(*) FROM (SELECT sha256 FROM clips WHERE sha256 IS NOT NULL "
        "GROUP BY sha256 HAVING COUNT(*) > 1)").fetchone()[0]
    dup_members = conn.execute(
        "SELECT COALESCE(SUM(n), 0) FROM (SELECT COUNT(*) AS n FROM clips "
        "WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING COUNT(*) > 1)").fetchone()[0]
    legacy_seconds = sum(float(r.get("duration") or 0) for r in legacy)
    new_seconds = (conn.execute("SELECT COALESCE(SUM(duration_ms),0) FROM clips").fetchone()[0] or 0) / 1000.0
    legacy_files = {str(r["file"]) for r in legacy}
    new_files = {r[0] for r in conn.execute("SELECT file FROM clips")}
    files_preserved = len(legacy_files & new_files)
    legacy_desc = sum(1 for r in legacy if str(r.get("description") or "").strip())
    new_desc = conn.execute(
        "SELECT COUNT(*) FROM clips WHERE TRIM(COALESCE(description,'')) <> ''").fetchone()[0]
    legacy_tags = sum(1 for r in legacy if norm_json_list(r.get("tags")) not in ("[]", ""))
    new_tags = conn.execute("SELECT COUNT(*) FROM clips WHERE tags <> '[]'").fetchone()[0]
    empty_uuid = conn.execute(
        "SELECT COUNT(*) FROM clips WHERE asset_uuid IS NULL OR asset_uuid = ''").fetchone()[0]
    offline = conn.execute("SELECT COUNT(*) FROM clips WHERE status='offline'").fetchone()[0]
    no_emb = counts["clips"] - counts["clip_embeddings"]
    prov_gap = conn.execute(
        "SELECT COUNT(*) FROM clips WHERE COALESCE(source_url,'')='' OR COALESCE(author,'')=''"
    ).fetchone()[0]
    total_bytes = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM clips").fetchone()[0] or 0
    health = 100.0
    if counts["clips"]:
        health -= 30.0 * offline / counts["clips"]
        health -= 20.0 * prov_gap / counts["clips"]
        health -= 15.0 * no_emb / counts["clips"]
        health -= 5.0 * (counts["clips"] - new_desc) / counts["clips"]
    health = round(max(0.0, health), 1)

    # первичный снимок здоровья + журнал миграции
    conn.execute(
        """INSERT INTO library_health_history
           (captured_at, run_id, total, active, missing, needs_review, dup_clusters,
            provenance_gap, no_embedding, total_bytes, health_score, details)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now_iso(), run_id, counts["clips"], counts["clips"] - offline, offline,
         needs_review_n, dup_clusters, prov_gap, no_emb, total_bytes, health,
         json.dumps({"formula": "100 -30*offline -20*prov_gap -15*no_emb -5*no_desc",
                     "source": str(args.source), "mode": mode,
                     "status_counts": status_counts,
                     "dup_members": dup_members}, ensure_ascii=False)),
    )
    conn.execute(
        """INSERT INTO audit_log
           (run_id, tool, mode, status, db_path, root, started_at, finished_at, summary, report_json)
           VALUES (?, 'migrate_schema_v2.py', ?, 'ok', ?, ?, ?, ?, ?, ?)""",
        (run_id, mode, str(args.source), str(args.root), started, now_iso(),
         json.dumps({"clips": counts["clips"], "offline": offline,
                     "needs_review": needs_review_n}, ensure_ascii=False),
         str(report_path)),
    )
    conn.commit()

    field_fill = {
        "source_url": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE COALESCE(source_url,'')<>''").fetchone()[0],
        "author": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE COALESCE(author,'')<>''").fetchone()[0],
        "license": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE COALESCE(license,'')<>''").fetchone()[0],
        "sha256": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE sha256 IS NOT NULL").fetchone()[0],
        "phash": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE phash IS NOT NULL").fetchone()[0],
        "duration": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE duration_ms IS NOT NULL AND duration_ms > 0").fetchone()[0],
        "resolution": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE width > 0 AND height > 0").fetchone()[0],
        "bitrate": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE bitrate IS NOT NULL AND bitrate > 0").fetchone()[0],
        "blur": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE blur IS NOT NULL AND blur > 0").fetchone()[0],
        "motion": conn.execute(
            "SELECT COUNT(*) FROM clips WHERE motion IS NOT NULL").fetchone()[0],
        "description": new_desc,
        "tags": new_tags,
        "embedding": counts["clip_embeddings"],
        "certificate": counts["certificates"],
    }

    source_sha_after = sha256_file(args.source)
    source_unchanged = bool(source_sha_before and source_sha_before == source_sha_after)
    report = {
        "run_id": run_id,
        "mode": mode,
        "started_at": started,
        "finished_at": now_iso(),
        "source": str(args.source),
        "work_db": str(work),
        "schema": str(SCHEMA_PATH),
        "source_size": source_size,
        "source_sha256_before": source_sha_before,
        "source_sha256_after": source_sha_after,
        "source_unchanged": source_unchanged,
        "legacy_clips": len(legacy),
        "legacy_usage": len(usage_rows),
        "counts": counts,
        "status_counts": status_counts,
        "field_fill": field_fill,
        "needs_review": {
            "total": needs_review_n,
            "by_reason": dict(sorted(review_reasons_count.items(), key=lambda kv: -kv[1])),
        },
        "enrichment": {"author_from_cert": enriched_author, "source_url_from_cert": enriched_url},
        "sources_created": src_created,
        "duplicates": {"exact_sha256_clusters": dup_clusters,
                       "exact_sha256_members": dup_members},
        "missing_files": missing_files,
        "usage_orphans": usage_orphans,
        "usage_linked_by_name": usage_linked_by_name,
        "embeddings_file": str(args.embeddings),
        "embeddings_file_bytes": emb_bytes,
        "embeddings_file_hash": emb_file_hash,
        "embeddings_unmatched": len(set(emb) - set(file_to_id)),
        "sha256_hashed": len(sha_map),
        "phash_imported": len(phash_map),
        "validation": {
            "integrity_check": integrity,
            "foreign_key_violations": len(fk_bad),
            "files_preserved": files_preserved,
            "files_expected": len(legacy_files),
            "files_new": len(new_files),
            "asset_uuid_empty": empty_uuid,
            "duration_seconds_legacy": round(legacy_seconds, 3),
            "duration_seconds_new": round(new_seconds, 3),
            "duration_delta_seconds": round(new_seconds - legacy_seconds, 3),
            "description_legacy": legacy_desc,
            "description_new": new_desc,
            "tags_legacy": legacy_tags,
            "tags_new": new_tags,
            "offline": offline,
            "provenance_gap": prov_gap,
            "no_embedding": no_emb,
        },
        "health": {"score": health,
                   "formula": "100 -30*offline -20*prov_gap -15*no_emb -5*no_desc"},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 78)
    print(f"Клипов: {len(legacy)} -> {counts['clips']} | offline: {offline} | "
          f"файлы сохранены: {files_preserved}/{len(legacy_files)}")
    print(f"Статусы: {status_counts}")
    print(f"Источники: {counts['sources']} | сертификаты: {counts['certificates']} | "
          f"темы: {counts['themes']} | mood-словарь: {counts['moods']}")
    print(f"Эмбеддинги (meta): {counts['clip_embeddings']} | без эмбеддинга: {no_emb} | "
          f"usage: {counts['clip_usage']} (orphan {usage_orphans})")
    print(f"Точные дубли (sha256): {dup_clusters} кластеров / {dup_members} файлов")
    print("Заполненность: " + " | ".join(f"{k}={human_pct(v, counts['clips'])}"
                                        for k, v in field_fill.items()))
    print(f"needs_review: {human_pct(needs_review_n, counts['clips'])}")
    for reason, n in sorted(review_reasons_count.items(), key=lambda kv: -kv[1]):
        print(f"  - {reason}: {n}")
    print(f"Проверки: integrity={integrity} | FK violations={len(fk_bad)} | "
          f"uuid пустых={empty_uuid} | duration Δ={new_seconds - legacy_seconds:+.3f}s")
    print(f"Индекс здоровья: {health}/100")
    print(f"Отчёт: {report_path}")

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()

    if mode == "dry-run" and not args.keep:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(work) + suffix).unlink()
            except FileNotFoundError:
                pass
        print(f"Dry-run: временная копия удалена ({work})")
    else:
        print(f"Копия сохранена: {work}")
    if not source_unchanged:
        print("[MIGRATE] ВНИМАНИЕ: sha256 исходной БД изменился во время прогона "
              "(возможна запись приложения, не миграции)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
