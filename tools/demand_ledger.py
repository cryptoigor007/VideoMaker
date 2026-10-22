#!/usr/bin/env python3
"""Журнал спроса библиотеки: слабые сцены -> дефициты -> JSON для докачки.

Что делает:
  * читает ``scenes.jsonl`` (baseline smart_broll: scene_text, queries,
    selected_score, selected_clip, fallback_used, ts);
  * отбирает слабые сцены (низкий балл / нет выбора / мало кандидатов /
    сработал fallback);
  * нормализует запрос, считает частоту, при наличии ключей — эмбеддинг
    запроса (кэш, без повторных вызовов);
  * кластеризует запросы внутри ISO-недели: cosine >= threshold, а без
    эмбеддингов — по нормализованному запросу;
  * формирует топ-50 дефицитов (JSON + MD) для дозакупки/докачки;
  * с ``--apply`` пишет строки в ``demand_ledger`` ТОЛЬКО v2-копии БД,
    production-БД запрещена.

    python3 tools/demand_ledger.py --scenes ~/video_maker/out/series1
    python3 tools/demand_ledger.py --scenes .../scenes.jsonl --embed auto
    python3 tools/demand_ledger.py --scenes .../scenes.jsonl \
        --apply --db ~/video_maker/broll_library_v2.db
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
import time
import unicodedata
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

PROD_DB = Path.home() / "video_maker" / "broll_library.db"
DEFAULT_APPLY_DB = Path.home() / "video_maker" / "broll_library_v2.db"
DEFAULT_CACHE = Path.home() / "video_maker" / "demand_emb_cache.json"
DEFAULT_OUT = Path.home() / "video_maker" / "audit"
DEFAULT_SEARCH_ROOT = Path.home() / "video_maker"
MODEL_FALLBACK = "gemini-embedding-001"
CACHE_SCHEMA = 1

# Лёгкие стоп-слова: не влияют на смысл дефицита, но склеивают частоты.
STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "with",
    "and", "or", "by", "as", "is", "are", "be", "this", "that", "into",
    "человек", "люди", "мужчина", "женщина", "парень", "девушка", "вид",
    "кадр", "сцена", "крупный", "план", "общий", "на", "в", "и", "с",
    "у", "о", "от", "до", "по", "за", "из", "к", "не", "что", "это",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def model_name() -> str:
    try:
        from video_maker.semantic.embeddings import MODEL
        return str(MODEL)
    except Exception:
        return MODEL_FALLBACK


def normalize_query(text: str, drop_stop: bool = True) -> str:
    """Нормализованный ключ запроса: lower, NFKC, без пунктуации, стоп-слов."""
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"[_]+", " ", s)
    tokens = [t for t in s.split() if t and not t.isdigit()]
    if drop_stop:
        kept = [t for t in tokens if t not in STOP_WORDS]
        if kept:
            tokens = kept
    return " ".join(tokens).strip()


def iso_week(ts: str | None, fallback: str | None = None) -> str:
    raw = str(ts or fallback or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            y, w, _ = dt.isocalendar()
            return f"{y}-W{w:02d}"
        except Exception:
            pass
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def as_list(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value or "").strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()]
    except Exception:
        pass
    return [x.strip() for x in re.split(r"[,;|]\s*", s) if x.strip()]


def primary_query(rec: dict) -> str:
    for key in ("query", "search_query", "query_text"):
        v = str(rec.get(key) or "").strip()
        if v:
            return v
    queries = as_list(rec.get("queries"))
    if queries:
        return queries[0]
    for key in ("scene_text", "visual", "text", "description"):
        v = str(rec.get(key) or "").strip()
        if v:
            return v
    return ""


def selected_score(rec: dict) -> float:
    for key in ("selected_score", "score"):
        try:
            v = rec.get(key)
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def candidate_count(rec: dict) -> int:
    ids = rec.get("candidate_ids")
    if isinstance(ids, list):
        return len(ids)
    try:
        return int(rec.get("candidates_n") or 0)
    except (TypeError, ValueError):
        return 0


def weak_reason(rec: dict, min_score: float, include_fallback: bool = True) -> str | None:
    """Причина слабости сцены или None, если сцена сильная."""
    reasons: list[str] = []
    score = selected_score(rec)
    if score < float(min_score):
        reasons.append(f"low_score:{score:g}")
    if not str(rec.get("selected_clip") or "").strip():
        reasons.append("no_selection")
    if candidate_count(rec) == 0:
        reasons.append("no_candidates")
    fb = str(rec.get("fallback_used") or "").strip().lower()
    if include_fallback and fb and fb not in ("none", "null", "false", "0"):
        reasons.append(f"fallback:{fb}")
    return ",".join(reasons) if reasons else None


def parse_scenes_file(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception as exc:
                    errors.append(f"{path}:{lineno}: {str(exc)[:100]}")
                    continue
                if not isinstance(rec, dict):
                    errors.append(f"{path}:{lineno}: не объект")
                    continue
                rec["_source_file"] = str(path)
                rec["_source_dir"] = str(path.parent)
                rows.append(rec)
    except OSError as exc:
        errors.append(f"{path}: {str(exc)[:120]}")
    return rows, errors


def find_scenes(paths: list[str], search_roots: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if any(ch in raw for ch in "*?["):
            found += [Path(x) for x in sorted(glob.glob(str(p), recursive=True))]
        elif p.is_dir():
            direct = p / "scenes.jsonl"
            if direct.exists():
                found.append(direct)
            else:
                found += sorted(p.rglob("scenes.jsonl"))
        elif p.exists():
            found.append(p)
    if not found:
        for raw in search_roots:
            root = Path(raw).expanduser()
            if root.exists():
                found += sorted(root.rglob("scenes.jsonl"))
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# Эмбеддинги запросов (кэш; сеть — только при --embed on/auto и наличии ключей)
# ---------------------------------------------------------------------------
def load_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), dict):
            return d
    except Exception:
        pass
    return {"schema": CACHE_SCHEMA, "model": None, "items": {}}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def cache_get(cache: dict, text: str, ttl_days: float = 180.0) -> list[float] | None:
    ent = (cache.get("items") or {}).get(sha256_text(text))
    if not isinstance(ent, dict):
        return None
    if ent.get("text_hash") != sha256_text(text):
        return None
    try:
        ts = datetime.fromisoformat(str(ent.get("updated_at") or "")).timestamp()
    except Exception:
        return None
    if ttl_days > 0 and (time.time() - ts) > ttl_days * 86400:
        return None
    vec = ent.get("vec")
    return [float(x) for x in vec] if isinstance(vec, list) and vec else None


def _api_keys() -> list[str]:
    out: list[str] = []
    env_file = Path.home() / ".vm_gemini_keys.env"
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ").strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("'\"")
            if k == "GEMINI_API_KEY" and v:
                out.append(v)
            elif k == "GEMINI_API_KEYS":
                out += [x.strip() for x in v.split(",") if x.strip()]
    except Exception:
        pass
    for env_name in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
        v = os.environ.get(env_name, "").strip()
        if v:
            out += [x.strip() for x in v.split(",") if x.strip()]
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))]


def _embed_batch(texts: list[str], model: str, timeout: int = 90) -> list[list[float]] | None:
    keys = _api_keys()
    if not keys:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":batchEmbedContents")
    body = json.dumps({"requests": [
        {"model": f"models/{model}", "content": {"parts": [{"text": str(t)[:2000]}]}}
        for t in texts
    ]}).encode()
    for key in keys:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            embs = data.get("embeddings") or []
            out = [[float(x) for x in (e.get("values") or [])] for e in embs]
            if len(out) == len(texts) and all(out):
                return out
        except Exception:
            continue
    return None


def ensure_embeddings(texts: list[str], cache_path: Path, model: str, mode: str,
                      ttl_days: float = 180.0, batch: int = 32,
                      log=print) -> tuple[dict[str, list[float]], dict]:
    """Возвращает {normalized_text: vector} и статистику. mode: off|auto|on."""
    cache = load_cache(cache_path)
    if str(cache.get("model") or "") and str(cache.get("model")) != model:
        cache = {"schema": CACHE_SCHEMA, "model": model, "items": {}}
    cache["model"] = model
    stats = {"mode": mode, "requested": len(set(texts)), "cached": 0,
             "computed": 0, "missing": 0, "cache_path": str(cache_path)}
    vectors: dict[str, list[float]] = {}
    todo: list[str] = []
    for text in dict.fromkeys(texts):
        v = cache_get(cache, text, ttl_days)
        if v:
            vectors[text] = v
        else:
            todo.append(text)
    stats["cached"] = len(vectors)
    if mode == "off" or not todo:
        stats["missing"] = len(todo)
        return vectors, stats
    if not _api_keys():
        stats["missing"] = len(todo)
        stats["error"] = "нет GEMINI_API_KEY — кластеризация по нормализованному запросу"
        return vectors, stats
    log(f"[DEMAND] эмбеддинги запросов: кэш {len(vectors)}, считаем {len(todo)}…")
    for i in range(0, len(todo), max(1, batch)):
        chunk = todo[i:i + max(1, batch)]
        got = _embed_batch(chunk, model)
        if got is None:
            stats["missing"] += len(chunk)
            continue
        now = now_iso()
        for text, vec in zip(chunk, got):
            if not vec:
                stats["missing"] += 1
                continue
            vectors[text] = vec
            (cache.setdefault("items", {}))[sha256_text(text)] = {
                "text": text, "text_hash": sha256_text(text), "vec": vec,
                "dim": len(vec), "updated_at": now,
            }
            stats["computed"] += 1
        save_cache(cache_path, cache)
    stats["missing"] = len(set(texts)) - len(vectors)
    cache["updated_at"] = now_iso()
    save_cache(cache_path, cache)
    return vectors, stats


# ---------------------------------------------------------------------------
# Кластеризация
# ---------------------------------------------------------------------------
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

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in list(self.parent):
            out[self.find(x)].append(x)
        return [sorted(v) for v in out.values()]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    return num / (na * nb + 1e-9)


def cluster_texts(texts: list[str], vectors: dict[str, list[float]] | None = None,
                  threshold: float = 0.82) -> list[list[str]]:
    """Кластеры нормализованных запросов: cosine, иначе точная группа."""
    uniq = sorted(set(t for t in texts if t))
    if not uniq:
        return []
    dsu = DSU()
    for t in uniq:
        dsu.find(t)
    vecs = vectors or {}
    if len(vecs) >= 2:
        with_vec = [t for t in uniq if t in vecs]
        for i in range(len(with_vec)):
            for j in range(i + 1, len(with_vec)):
                if cosine(vecs[with_vec[i]], vecs[with_vec[j]]) >= float(threshold):
                    dsu.union(with_vec[i], with_vec[j])
    return dsu.groups()


def aggregate_deficits(rows: list[dict], vectors: dict[str, list[float]],
                       threshold: float, top_n: int = 50) -> tuple[list[dict], dict, list[dict]]:
    """rows — слабые сцены с полями week/query_norm/query/score/..."""
    by_week: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_week[str(r["week"])].append(r)
    weeks: list[dict] = []
    global_top: list[dict] = []
    for week in sorted(by_week):
        items = by_week[week]
        freq = Counter(r["query_norm"] for r in items)
        clusters = cluster_texts(list(freq), vectors, threshold)
        out: list[dict] = []
        for ci, members in enumerate(clusters):
            member_rows = [r for r in items if r["query_norm"] in members]
            if not member_rows:
                continue
            rep = max(members, key=lambda t: (freq[t], -len(t)))
            total = sum(freq[m] for m in members)
            scores = [float(r.get("score") or 0.0) for r in member_rows]
            themes = Counter()
            moods = Counter()
            orientations = Counter()
            reasons = Counter()
            for r in member_rows:
                for t in r.get("themes") or []:
                    themes[str(t)] += 1
                if r.get("mood"):
                    moods[str(r["mood"])] += 1
                if r.get("orientation"):
                    orientations[str(r["orientation"])] += 1
                for part in str(r.get("weak_reason") or "").split(","):
                    if part:
                        reasons[part] += 1
            queries = Counter(r.get("query") or r["query_norm"] for r in member_rows)
            cluster_key = f"{week}-{ci + 1:03d}-" + hashlib.sha1(
                (week + "|" + rep).encode("utf-8")).hexdigest()[:8]
            avg_score = sum(scores) / len(scores) if scores else 0.0
            deficit = {
                "rank": 0,
                "cluster_key": cluster_key,
                "week": week,
                "query": rep,
                "query_norm": rep,
                "count": total,
                "avg_selected_score": round(avg_score, 3),
                "min_selected_score": round(min(scores), 3) if scores else None,
                "max_selected_score": round(max(scores), 3) if scores else None,
                "themes": [k for k, _ in themes.most_common(8)],
                "moods": [k for k, _ in moods.most_common(8)],
                "orientations": [k for k, _ in orientations.most_common(4)],
                "weak_reasons": dict(reasons.most_common(8)),
                "variants": [{"query": q, "count": c} for q, c in queries.most_common(8)],
                "members": len(member_rows),
                "has_embedding": bool(vectors and rep in vectors),
                "priority": round(total * 10.0 + max(0.0, 10.0 - avg_score), 2),
            }
            out.append(deficit)
        out.sort(key=lambda d: (-d["count"], d["avg_selected_score"], -d["priority"]))
        for i, d in enumerate(out, 1):
            d["rank"] = i
        weeks.append({"week": week, "weak_scenes": len(items),
                      "queries": len(freq), "clusters": len(out),
                      "top": out[:top_n], "all_clusters": out})
        global_top.extend(out[:top_n])
    # общий топ: сначала частота, затем низкий балл
    agg: dict[str, dict] = {}
    for d in global_top:
        key = d["query_norm"]
        if key not in agg:
            agg[key] = dict(d)
            agg[key]["weeks"] = [d["week"]]
            agg[key]["count"] = d["count"]
        else:
            a = agg[key]
            a["count"] += d["count"]
            a["weeks"].append(d["week"])
            a["priority"] = round(a["priority"] + d["priority"], 2)
    top = sorted(agg.values(), key=lambda d: (-d["count"], d["avg_selected_score"]))
    for i, d in enumerate(top[:top_n], 1):
        d["rank"] = i
    stats = {
        "weeks": len(weeks),
        "weak_scenes": len(rows),
        "unique_queries": len({r["query_norm"] for r in rows}),
        "clusters": sum(w["clusters"] for w in weeks),
        "with_embedding": sum(1 for r in rows if r["query_norm"] in vectors),
        "top": len(top[:top_n]),
    }
    return weeks, stats, top[:top_n]


# ---------------------------------------------------------------------------
# Запись в v2-копию
# ---------------------------------------------------------------------------
def apply_rows(db: Path, rows: list[dict], cluster_map: dict[str, str],
               vectors: dict[str, list[float]], model: str, run_id: str,
               report_path: Path, replace: bool = True, log=print) -> dict:
    if not db.exists():
        raise RuntimeError(f"apply-db не существует: {db}")
    if db.resolve() == PROD_DB.resolve():
        raise RuntimeError("apply-db указывает на production — запись запрещена")
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "demand_ledger" not in tables or "clips" not in tables:
            raise RuntimeError("apply-db не v2: нет demand_ledger/clips")
        clip_ids: dict[str, int] = {}
        for cid, file in conn.execute("SELECT id, file FROM clips"):
            clip_ids[str(file)] = int(cid)
        weeks = sorted({str(r["week"]) for r in rows})
        removed = 0
        if replace and weeks:
            marks = ",".join("?" for _ in weeks)
            cur = conn.execute(
                f"DELETE FROM demand_ledger WHERE source='scenes.jsonl' AND week IN ({marks})",
                weeks)
            removed = int(cur.rowcount or 0)
        inserted = 0
        for r in rows:
            vec = vectors.get(r["query_norm"])
            blob = struct.pack("<" + "f" * len(vec), *[float(x) for x in vec]) if vec else None
            selected = clip_ids.get(str(r.get("selected_clip") or ""))
            meta = dict(r.get("meta") or {})
            meta.update({"source_file": r.get("_source_file"),
                         "query_variants": r.get("queries") or [],
                         "candidate_ids": (r.get("candidate_ids") or [])[:20],
                         "scene_id": r.get("scene_id")})
            conn.execute(
                """INSERT INTO demand_ledger
                   (request_id, week, series, scene, query, query_norm,
                    query_embedding, embedding_model, embedding_dim, cluster_key,
                    frequency, themes, mood, orientation, candidates_n,
                    selected_clip_id, selected_rank, score, weak_reason, source, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'scenes.jsonl', ?)""",
                (r.get("request_id") or f"{run_id}:{inserted + 1}",
                 r["week"], r.get("series") or "", r.get("scene"),
                 r.get("query") or "", r["query_norm"], blob,
                 model if blob else None, len(vec) if vec else None,
                 cluster_map.get(r["query_norm"]), int(r.get("frequency") or 1),
                 json.dumps(r.get("themes") or [], ensure_ascii=False),
                 r.get("mood") or "", r.get("orientation") or "",
                 int(r.get("candidates_n") or 0), selected,
                 r.get("selected_rank"), float(r.get("score") or 0.0),
                 r.get("weak_reason") or "", json.dumps(meta, ensure_ascii=False)))
            inserted += 1
        conn.execute(
            """INSERT INTO audit_log
               (run_id, tool, mode, status, db_path, started_at, finished_at,
                summary, report_json)
               VALUES (?, 'demand_ledger.py', 'apply', 'ok', ?, ?, ?, ?, ?)""",
            (run_id, str(db), now_iso(), now_iso(),
             json.dumps({"rows": inserted, "removed": removed, "weeks": weeks},
                        ensure_ascii=False), str(report_path)))
        conn.commit()
        return {"db": str(db), "rows": inserted, "removed": removed,
                "weeks": weeks, "with_embedding": sum(
                    1 for r in rows if r["query_norm"] in vectors)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Слабые сцены -> demand_ledger -> топ-50 дефицитов")
    ap.add_argument("--scenes", action="append", default=[],
                    help="scenes.jsonl, папка или glob (можно несколько)")
    ap.add_argument("--search-root", action="append", default=[str(DEFAULT_SEARCH_ROOT)],
                    help="где искать scenes.jsonl, если --scenes не задан")
    ap.add_argument("--db", type=Path, default=DEFAULT_APPLY_DB,
                    help="v2-БД только для --apply")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-replace", action="store_true",
                    help="не удалять прежние импортированные строки недели")
    ap.add_argument("--min-score", type=float, default=6.0,
                    help="сцена слабая, если selected_score ниже порога")
    ap.add_argument("--no-fallback", action="store_true",
                    help="не считать fallback_used причиной слабости")
    ap.add_argument("--week", default="", help="только одна ISO-неделя, напр. 2026-W43")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--cluster-threshold", type=float, default=0.82)
    ap.add_argument("--embed", choices=("off", "auto", "on"), default="auto")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--ttl-days", type=float, default=180.0)
    ap.add_argument("--model", default="")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    def log(*a):
        if not args.quiet:
            print(*a, flush=True)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    st = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = "demand-" + uuid.uuid4().hex[:12]
    json_path = args.json or out_dir / f"demand_deficits_{st}.json"
    md_path = args.md or out_dir / f"demand_deficits_{st}.md"
    started = now_iso()
    model = args.model or model_name()

    files = find_scenes(args.scenes, args.search_root)
    log("=" * 78)
    log("DEMAND LEDGER: слабые сцены -> дефициты")
    log("=" * 78)
    log(f"Файлов scenes.jsonl: {len(files)}")
    if not files:
        log("[DEMAND] scenes.jsonl не найдены — импортировать нечего")
        report = {"run_id": run_id, "tool": "demand_ledger.py", "mode":
                  "apply" if args.apply else "report-only", "started_at": started,
                  "finished_at": now_iso(), "files": [], "error": "scenes.jsonl not found",
                  "stats": {"weak_scenes": 0}, "top_deficits": [], "weeks": []}
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        md_path.write_text(
            "# Demand ledger\n\n"
            "`scenes.jsonl` не найден ни в `--scenes`, ни в `--search-root`.\n\n"
            f"Поиск: `{', '.join(str(x) for x in args.search_root)}`\n\n"
            "Импорт не выполнялся; production-БД не изменялась.\n",
            encoding="utf-8")
        return 2

    records: list[dict] = []
    parse_errors: list[str] = []
    for p in files:
        rows, errors = parse_scenes_file(p)
        records += rows
        parse_errors += errors
    log(f"Сцен прочитано: {len(records)} | ошибок парсинга: {len(parse_errors)}")

    weak_rows: list[dict] = []
    skipped_strong = 0
    for rec in records:
        reason = weak_reason(rec, args.min_score, include_fallback=not args.no_fallback)
        if not reason:
            skipped_strong += 1
            continue
        q = primary_query(rec)
        qn = normalize_query(q)
        if not qn:
            qn = normalize_query(str(rec.get("scene_text") or "scene"))
        week = iso_week(str(rec.get("ts") or ""), str(rec.get("_source_file") or ""))
        if args.week and week != args.week:
            continue
        weak_rows.append({
            "week": week,
            "query": q,
            "query_norm": qn,
            "queries": as_list(rec.get("queries")),
            "series": str(rec.get("series") or Path(str(rec.get("_source_dir") or "")).name),
            "scene": rec.get("scene_index", rec.get("scene")),
            "scene_id": rec.get("scene_id"),
            "score": selected_score(rec),
            "selected_clip": str(rec.get("selected_clip") or ""),
            "selected_rank": rec.get("selected_rank"),
            "candidates_n": candidate_count(rec),
            "candidate_ids": rec.get("candidate_ids") or [],
            "themes": as_list(rec.get("themes") or rec.get("theme")),
            "mood": str(rec.get("mood") or ""),
            "orientation": str(rec.get("orientation") or ""),
            "weak_reason": reason,
            "fallback_used": rec.get("fallback_used"),
            "search_ms": rec.get("search_ms"),
            "vlm_calls": rec.get("vlm_calls"),
            "ts": rec.get("ts"),
            "_source_file": rec.get("_source_file"),
            "request_id": hashlib.sha1(
                (str(rec.get("_source_file")) + "|" + str(rec.get("scene_id")) + "|" +
                 str(rec.get("ts"))).encode("utf-8")).hexdigest()[:16],
            "meta": {
                "candidate_scores": (rec.get("candidate_scores") or [])[:12],
                "scene_text": str(rec.get("scene_text") or "")[:500],
                "fallback_used": rec.get("fallback_used"),
                "search_ms": rec.get("search_ms"),
                "vlm_calls": rec.get("vlm_calls"),
                "cache_hits": rec.get("cache_hits"),
                "ts": rec.get("ts"),
            },
        })
    log(f"Слабых сцен: {len(weak_rows)} | сильных пропущено: {skipped_strong} | "
        f"фильтр недели: {args.week or 'все'}")

    vectors, emb_stats = ensure_embeddings(
        [r["query_norm"] for r in weak_rows], args.cache, model, args.embed,
        args.ttl_days, log=log)
    log(f"Эмбеддинги запросов: {len(vectors)}/{len({r['query_norm'] for r in weak_rows})} "
        f"(кэш {emb_stats.get('cached', 0)}, новых {emb_stats.get('computed', 0)})")

    weeks, stats, top = aggregate_deficits(
        weak_rows, vectors, args.cluster_threshold, args.top)
    # cluster_map: query_norm -> cluster_key (по неделям; для строк недели)
    cluster_map: dict[str, str] = {}
    for w in weeks:
        for d in w.get("all_clusters") or w["top"]:
            for m in d.get("variants") or []:
                cluster_map[normalize_query(m["query"])] = d["cluster_key"]
            cluster_map[d["query_norm"]] = d["cluster_key"]
    # частота по неделе
    freq: Counter = Counter((r["week"], r["query_norm"]) for r in weak_rows)
    for r in weak_rows:
        r["frequency"] = freq[(r["week"], r["query_norm"])]
        r["cluster_key"] = cluster_map.get(r["query_norm"])

    log(f"Кластеров: {stats['clusters']} за {stats['weeks']} недель; "
        f"топ-{len(top)} дефицитов")

    report = {
        "run_id": run_id,
        "tool": "demand_ledger.py",
        "mode": "apply" if args.apply else "report-only",
        "started_at": started,
        "finished_at": now_iso(),
        "db": str(args.db),
        "files": [str(p) for p in files],
        "parse_errors": parse_errors[:50],
        "params": {
            "min_score": args.min_score,
            "include_fallback": not args.no_fallback,
            "week": args.week,
            "top": args.top,
            "cluster_threshold": args.cluster_threshold,
            "embed": args.embed,
            "model": model,
        },
        "stats": {**stats, "scenes_total": len(records),
                  "skipped_strong": skipped_strong, "parse_errors": len(parse_errors),
                  "embeddings": emb_stats},
        "weeks": [{k: v for k, v in w.items() if k != "all_clusters"} for w in weeks],
        "top_deficits": top,
        "apply": None,
    }
    if args.apply:
        try:
            applied = apply_rows(args.db, weak_rows, cluster_map, vectors, model,
                                 run_id, json_path, replace=not args.no_replace, log=log)
            report["apply"] = applied
            log(f"[DEMAND] записано в {applied['db']}: строк {applied['rows']}, "
                f"удалено прежних {applied['removed']}")
        except Exception as exc:
            report["apply"] = {"error": str(exc)}
            log(f"[DEMAND] apply не выполнен: {exc}")
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
            return 2

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# Demand ledger — {st}", ""]
    md.append(f"- **Файлов scenes.jsonl:** {len(files)} | **сцен:** {len(records)} | "
              f"**слабых:** {len(weak_rows)}")
    md.append(f"- **Недели:** {stats['weeks']} | **уникальных запросов:** "
              f"{stats['unique_queries']} | **кластеров:** {stats['clusters']}")
    md.append(f"- **Эмбеддинги запросов:** {stats['with_embedding']}/{stats['weak_scenes']} "
              f"(режим `{args.embed}`, модель `{model}`)")
    md.append(f"- **Порог слабости:** selected_score < {args.min_score}; fallback: "
              f"{'учитывается' if not args.no_fallback else 'нет'}")
    md.append("")
    md.append("## Топ дефицитов")
    md.append("")
    md.append("| # | Неделя | Запрос | Частота | Avg score | Причины |")
    md.append("|---|---|---|---|---|---|")
    for d in top:
        md.append(f"| {d['rank']} | {', '.join(d.get('weeks') or [d['week']])} | "
                  f"{d['query']} | {d['count']} | {d['avg_selected_score']} | "
                  f"{', '.join(f'{k}={v}' for k, v in (d.get('weak_reasons') or {}).items())} |")
    md.append("")
    md.append("## По неделям")
    md.append("")
    for w in weeks:
        md.append(f"### {w['week']} — слабых {w['weak_scenes']}, кластеров {w['clusters']}")
        md.append("")
        for d in w["top"][:10]:
            md.append(f"{d['rank']}. `{d['query']}` — {d['count']} сцен, "
                      f"avg {d['avg_selected_score']} (cluster `{d['cluster_key']}`)")
        md.append("")
    if report.get("apply"):
        md.append("## Запись в БД")
        md.append("")
        md.append(f"`{json.dumps(report['apply'], ensure_ascii=False)}`")
        md.append("")
    md.append(f"JSON: `{json_path}`")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"JSON: {json_path}")
    log(f"MD:   {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
