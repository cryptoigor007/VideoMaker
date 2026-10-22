#!/usr/bin/env python3
"""Mood-слой библиотеки: 40 настроений -> эмбеддинги -> cosine к клипам.

Логика:
  * словарь 40 настроений (slug/ru/en/description_en/valence/arousal);
  * эмбеддинги словаря считаются один раз и кэшируются в
    ~/video_maker/mood_emb_cache.json (TTL, модель, text_hash);
  * mood клипа = cosine между вектором описания клипа (broll_embeddings.json)
    и вектором настроения; берём top-2 с порогом 0.30;
  * по умолчанию — ТОЛЬКО отчёт (JSON+MD), БД не меняется;
  * --apply пишет пилот в v2-БД, production-БД запрещена без --allow-production.

    python3 tools/mood_embeddings.py --limit 50
    python3 tools/mood_embeddings.py --limit 50 --apply \
        --apply-db ~/video_maker/broll_library_v2.db
    python3 tools/mood_embeddings.py --all --apply --apply-db /path/to/copy.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROD_DB = Path.home() / "video_maker" / "broll_library.db"
DEFAULT_APPLY_DB = Path.home() / "video_maker" / "broll_library_v2.db"
DEFAULT_EMB = Path.home() / "video_maker" / "broll_embeddings.json"
DEFAULT_CACHE = Path.home() / "video_maker" / "mood_emb_cache.json"
DEFAULT_OUT = Path.home() / "video_maker" / "audit"
MODEL_FALLBACK = "gemini-embedding-001"
CACHE_SCHEMA = 1

# 40 настроений. Должны совпадать со словарём в tools/schema_v2.sql.
MOODS: list[dict] = [
    {"slug": "calm", "name_ru": "Спокойствие", "name_en": "Calm", "valence": 0.60, "arousal": 0.15,
     "description_en": "a calm, peaceful, quiet scene with slow gentle motion, soft light, no tension"},
    {"slug": "joy", "name_ru": "Радость", "name_en": "Joy", "valence": 0.90, "arousal": 0.70,
     "description_en": "a joyful, happy, bright scene with smiling people, laughter, warm sunlight, playful energy"},
    {"slug": "sadness", "name_ru": "Грусть", "name_en": "Sadness", "valence": -0.70, "arousal": 0.20,
     "description_en": "a sad, sorrowful scene with a downcast person, muted colors, rain, tears, quiet grief"},
    {"slug": "loneliness", "name_ru": "Одиночество", "name_en": "Loneliness", "valence": -0.60, "arousal": 0.20,
     "description_en": "a lonely person alone in a large empty space, isolation, silence, distance from other people"},
    {"slug": "hope", "name_ru": "Надежда", "name_en": "Hope", "valence": 0.70, "arousal": 0.40,
     "description_en": "a hopeful scene with sunrise, open horizon, a person looking forward, a new beginning"},
    {"slug": "nostalgia", "name_ru": "Ностальгия", "name_en": "Nostalgia", "valence": 0.20, "arousal": 0.25,
     "description_en": "a nostalgic memory scene with old photographs, warm faded light, childhood, past times"},
    {"slug": "tension", "name_ru": "Напряжённость", "name_en": "Tension", "valence": -0.50, "arousal": 0.80,
     "description_en": "a tense, suspenseful scene with narrow space, sharp shadows, waiting, conflict, unease"},
    {"slug": "fear", "name_ru": "Страх", "name_en": "Fear", "valence": -0.80, "arousal": 0.90,
     "description_en": "a frightening scene with darkness, threat, a scared person, shadows, danger"},
    {"slug": "anger", "name_ru": "Гнев", "name_en": "Anger", "valence": -0.80, "arousal": 0.90,
     "description_en": "an angry scene with a furious face, clenched fists, confrontation, red light, aggression"},
    {"slug": "love", "name_ru": "Любовь", "name_en": "Love", "valence": 0.90, "arousal": 0.50,
     "description_en": "a loving scene with a couple embracing, intimacy, warmth, care, romantic connection"},
    {"slug": "tenderness", "name_ru": "Нежность", "name_en": "Tenderness", "valence": 0.80, "arousal": 0.25,
     "description_en": "a tender, gentle scene with soft touch, a parent and child, delicate light, affection"},
    {"slug": "gratitude", "name_ru": "Благодарность", "name_en": "Gratitude", "valence": 0.80, "arousal": 0.35,
     "description_en": "a grateful scene with a warm smile, helping hands, giving thanks, heartfelt connection"},
    {"slug": "inspiration", "name_ru": "Вдохновение", "name_en": "Inspiration", "valence": 0.80, "arousal": 0.60,
     "description_en": "an inspiring scene with creative work, an artist, ideas, light through a window, discovery"},
    {"slug": "determination", "name_ru": "Решимость", "name_en": "Determination", "valence": 0.70, "arousal": 0.80,
     "description_en": "a determined person moving forward with purpose, sport, hard work, focused effort"},
    {"slug": "fatigue", "name_ru": "Усталость", "name_en": "Fatigue", "valence": -0.40, "arousal": 0.20,
     "description_en": "a tired exhausted person, slow movement, rubbing eyes, late night work, low energy"},
    {"slug": "confusion", "name_ru": "Растерянность", "name_en": "Confusion", "valence": -0.20, "arousal": 0.50,
     "description_en": "a confused person, uncertainty, crossroads, searching, unclear direction, hesitation"},
    {"slug": "curiosity", "name_ru": "Любопытство", "name_en": "Curiosity", "valence": 0.50, "arousal": 0.60,
     "description_en": "a curious person exploring, looking closely, discovery, wonder, investigation"},
    {"slug": "awe", "name_ru": "Восхищение", "name_en": "Awe", "valence": 0.80, "arousal": 0.70,
     "description_en": "an awe-inspiring vast landscape, mountains, ocean, starry sky, a person dwarfed by nature"},
    {"slug": "serenity", "name_ru": "Умиротворение", "name_en": "Serenity", "valence": 0.70, "arousal": 0.10,
     "description_en": "a serene scene with still water, soft mist, slow breathing, harmony, quiet nature"},
    {"slug": "melancholy", "name_ru": "Меланхолия", "name_en": "Melancholy", "valence": -0.40, "arousal": 0.20,
     "description_en": "a melancholic scene with autumn leaves, grey sky, a pensive person, bittersweet memory"},
    {"slug": "excitement", "name_ru": "Азарт", "name_en": "Excitement", "valence": 0.80, "arousal": 0.90,
     "description_en": "an exciting scene with fast movement, celebration, cheering crowd, adrenaline, bright lights"},
    {"slug": "pride", "name_ru": "Гордость", "name_en": "Pride", "valence": 0.70, "arousal": 0.60,
     "description_en": "a proud person standing tall after an achievement, medal, success, confident posture"},
    {"slug": "shame", "name_ru": "Стыд", "name_en": "Shame", "valence": -0.60, "arousal": 0.40,
     "description_en": "an ashamed person hiding their face, looking down, embarrassment, blushing, withdrawal"},
    {"slug": "guilt", "name_ru": "Вина", "name_en": "Guilt", "valence": -0.60, "arousal": 0.30,
     "description_en": "a guilty person alone with regret, head in hands, remorse, looking away"},
    {"slug": "jealousy", "name_ru": "Ревность", "name_en": "Jealousy", "valence": -0.50, "arousal": 0.60,
     "description_en": "a jealous person watching from a distance, suspicion, comparison, guarded eyes"},
    {"slug": "trust", "name_ru": "Доверие", "name_en": "Trust", "valence": 0.70, "arousal": 0.30,
     "description_en": "a trusting scene with open hands, a handshake, close supportive relationship, safety"},
    {"slug": "empathy", "name_ru": "Сопереживание", "name_en": "Empathy", "valence": 0.60, "arousal": 0.35,
     "description_en": "an empathetic scene with comforting a crying person, a gentle hand on a shoulder, care"},
    {"slug": "humor", "name_ru": "Юмор", "name_en": "Humor", "valence": 0.80, "arousal": 0.70,
     "description_en": "a funny playful scene, laughter, a silly moment, smiling friends, lighthearted joke"},
    {"slug": "grief", "name_ru": "Скорбь", "name_en": "Grief", "valence": -0.90, "arousal": 0.30,
     "description_en": "a grieving person at a funeral, mourning, tears, loss, black clothing, sorrow"},
    {"slug": "stress", "name_ru": "Стресс", "name_en": "Stress", "valence": -0.60, "arousal": 0.90,
     "description_en": "a stressed person with head in hands, overwhelming work, deadline, pressure, anxiety"},
    {"slug": "relief", "name_ru": "Облегчение", "name_en": "Relief", "valence": 0.70, "arousal": 0.30,
     "description_en": "a relieved person exhaling, tension released, safe after danger, closing eyes in calm"},
    {"slug": "anticipation", "name_ru": "Предвкушение", "name_en": "Anticipation", "valence": 0.50, "arousal": 0.60,
     "description_en": "a person waiting in anticipation, looking at a door, preparing, excited expectation"},
    {"slug": "disgust", "name_ru": "Отвращение", "name_en": "Disgust", "valence": -0.70, "arousal": 0.60,
     "description_en": "a disgusted reaction to something repulsive, grimace, turning away, dirty scene"},
    {"slug": "surprise", "name_ru": "Удивление", "name_en": "Surprise", "valence": 0.20, "arousal": 0.80,
     "description_en": "a surprised person with wide eyes, an unexpected moment, sudden discovery, shock"},
    {"slug": "boredom", "name_ru": "Скука", "name_en": "Boredom", "valence": -0.30, "arousal": 0.10,
     "description_en": "a bored person doing nothing, repetitive routine, staring blankly, grey office"},
    {"slug": "focus", "name_ru": "Сосредоточенность", "name_en": "Focus", "valence": 0.40, "arousal": 0.40,
     "description_en": "a focused person working deeply, concentration, quiet workspace, attention to detail"},
    {"slug": "freedom", "name_ru": "Свобода", "name_en": "Freedom", "valence": 0.80, "arousal": 0.70,
     "description_en": "a free person running in an open field, wind, open sky, breaking free, release"},
    {"slug": "belonging", "name_ru": "Принадлежность", "name_en": "Belonging", "valence": 0.80, "arousal": 0.50,
     "description_en": "a group of friends together, community, family dinner, inclusion, shared identity"},
    {"slug": "vulnerability", "name_ru": "Уязвимость", "name_en": "Vulnerability", "valence": -0.30, "arousal": 0.30,
     "description_en": "a vulnerable person exposed, crying, opening up, fragile, intimate honesty"},
    {"slug": "coziness", "name_ru": "Уют", "name_en": "Coziness", "valence": 0.70, "arousal": 0.15,
     "description_en": "a cozy warm interior, blanket, tea, fireplace, soft lamp light, home comfort"},
]


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


def api_keys() -> list[str]:
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


def embed_batch(texts: list[str], model: str, timeout: int = 90) -> list[list[float]] | None:
    """batchEmbedContents; None — если все ключи/попытки не удались."""
    keys = api_keys()
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
            out: list[list[float]] = []
            for item in embs:
                v = item.get("values") or []
                if v:
                    out.append([float(x) for x in v])
            if len(out) == len(texts):
                return out
        except Exception:
            continue
    return None


def embed_one(text: str, model: str, timeout: int = 30) -> list[float] | None:
    keys = api_keys()
    if not keys:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
    body = json.dumps({"model": f"models/{model}",
                       "content": {"parts": [{"text": str(text)[:2000]}]}}).encode()
    for key in keys:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            v = data.get("embedding", {}).get("values") or []
            if v:
                return [float(x) for x in v]
        except Exception:
            continue
    return None


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


def cache_get(cache: dict, slug: str, text: str, ttl_days: float) -> list[float] | None:
    ent = (cache.get("items") or {}).get(slug)
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


def ensure_mood_vectors(model: str, cache_path: Path, ttl_days: float,
                        refresh: bool, log=print) -> tuple[dict[str, list[float]], dict]:
    cache = load_cache(cache_path)
    cache_model = str(cache.get("model") or "")
    if cache_model and cache_model != model:
        log(f"[MOOD] модель кэша {cache_model} != {model} — пересчёт")
        cache = {"schema": CACHE_SCHEMA, "model": model, "items": {}}
    if refresh:
        cache = {"schema": CACHE_SCHEMA, "model": model, "items": {}}
    cache["model"] = model
    cache["ttl_days"] = ttl_days
    items = cache.setdefault("items", {})
    vectors: dict[str, list[float]] = {}
    todo: list[dict] = []
    for m in MOODS:
        text = m["description_en"]
        v = cache_get(cache, m["slug"], text, ttl_days)
        if v:
            vectors[m["slug"]] = v
        else:
            todo.append(m)
    stats = {"cached": len(vectors), "computed": 0, "requested": len(todo)}
    if todo:
        log(f"[MOOD] эмбеддинги словаря: кэш {len(vectors)}/40, считаем {len(todo)}…")
        got = embed_batch([m["description_en"] for m in todo], model)
        if got is None:
            got = []
            for m in todo:
                v = embed_one(m["description_en"], model)
                if v:
                    got.append(v)
                else:
                    got.append([])
        now = now_iso()
        for m, v in zip(todo, got):
            if not v:
                continue
            vectors[m["slug"]] = v
            items[m["slug"]] = {
                "text": m["description_en"],
                "text_hash": sha256_text(m["description_en"]),
                "vec": v,
                "dim": len(v),
                "updated_at": now,
            }
            stats["computed"] += 1
        cache["updated_at"] = now
        # чистим записи, которых больше нет в словаре
        valid = {m["slug"] for m in MOODS}
        for slug in list(items):
            if slug not in valid:
                items.pop(slug, None)
        save_cache(cache_path, cache)
    stats["ready"] = len(vectors)
    stats["cache_path"] = str(cache_path)
    return vectors, stats


def load_clips(db: Path) -> list[dict]:
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clips)")}
    select = ["id", "file", "rel_path", "category", "provider", "description", "tags",
              "score", "width", "height", "duration"]
    select += [c for c in ("orientation", "needs_review", "asset_uuid") if c in cols]
    rows = [dict(r) for r in conn.execute(
        "SELECT " + ", ".join(select) + " FROM clips ORDER BY id")]
    conn.close()
    return rows


def clip_text(rec: dict) -> str:
    tags = str(rec.get("tags") or "").replace('"', " ").replace("[", " ").replace("]", " ")
    parts = [str(rec.get("description") or ""), tags]
    return " ".join(p for p in parts if p.strip())[:1500]


def select_pilot(rows: list[dict], emb: dict, limit: int) -> list[dict]:
    eligible = [r for r in rows if str(r.get("file") or "") in emb]
    if limit <= 0 or limit >= len(eligible):
        return eligible
    groups: dict[str, list[dict]] = {}
    for r in eligible:
        key = str(r.get("category") or "none")
        groups.setdefault(key, []).append(r)
    # детерминированный порядок + перемешивание внутри категории по hash
    for key in groups:
        groups[key].sort(key=lambda r: hashlib.sha256(str(r.get("file")).encode()).hexdigest())
    order = sorted(groups, key=lambda k: (-len(groups[k]), k))
    out: list[dict] = []
    idx = 0
    while len(out) < limit:
        added = False
        for key in order:
            if idx < len(groups[key]):
                out.append(groups[key][idx])
                added = True
                if len(out) >= limit:
                    break
        if not added:
            break
        idx += 1
    return out[:limit]


def normalize_rows(matrix):
    import numpy as np
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-9)


def assign_moods(rows: list[dict], emb: dict, mood_vecs: dict[str, list[float]],
                 threshold: float, top_n: int = 2, log=print) -> tuple[list[dict], dict]:
    import numpy as np
    slugs = [m["slug"] for m in MOODS if m["slug"] in mood_vecs]
    by_slug = {m["slug"]: m for m in MOODS}
    if not slugs:
        return [], {"error": "нет векторов настроений", "slugs": 0}
    mm = normalize_rows(np.asarray([mood_vecs[s] for s in slugs], dtype=np.float32))
    clip_vecs = []
    used_rows = []
    for r in rows:
        v = emb.get(str(r.get("file") or ""))
        if v:
            clip_vecs.append(v)
            used_rows.append(r)
    if not clip_vecs:
        return [], {"error": "нет векторов клипов", "slugs": len(slugs)}
    cm = normalize_rows(np.asarray(clip_vecs, dtype=np.float32))
    sim = cm @ mm.T
    out: list[dict] = []
    for i, r in enumerate(used_rows):
        scores = sim[i]
        order = np.argsort(-scores)
        candidates = []
        for j in order[: max(top_n, 3)]:
            s = float(scores[int(j)])
            if s >= threshold:
                candidates.append((slugs[int(j)], s))
            if len(candidates) >= top_n:
                break
        # диагностика: top-1 всегда, даже ниже порога; z-score и margin
        top_idx = int(order[0])
        top1 = float(scores[top_idx])
        mean = float(scores.mean())
        std = float(scores.std()) or 1e-9
        second = float(scores[int(order[1])]) if len(order) > 1 else 0.0
        selected = []
        for rank, (slug, s) in enumerate(candidates, 1):
            selected.append({
                "slug": slug,
                "name_ru": by_slug[slug]["name_ru"],
                "name_en": by_slug[slug]["name_en"],
                "rank": rank,
                "score": round(s, 4),
                "z": round((s - mean) / std, 3),
                "margin": round(s - second, 4) if rank == 1 else None,
            })
        out.append({
            "file": str(r.get("file") or ""),
            "rel_path": str(r.get("rel_path") or ""),
            "category": str(r.get("category") or ""),
            "provider": str(r.get("provider") or ""),
            "top1": {"slug": slugs[top_idx], "score": round(top1, 4),
                     "z": round((top1 - mean) / std, 3)},
            "top1_below_threshold": top1 < threshold,
            "moods": selected,
        })
    below = sum(1 for x in out if not x["moods"])
    dist: dict[str, int] = {}
    for x in out:
        for m in x["moods"]:
            dist[m["slug"]] = dist.get(m["slug"], 0) + 1
    stats = {
        "slugs": len(slugs),
        "clips_scored": len(out),
        "with_mood": len(out) - below,
        "below_threshold": below,
        "coverage_pct": round(100.0 * (len(out) - below) / len(out), 1) if out else 0.0,
        "avg_top1": round(sum(x["top1"]["score"] for x in out) / len(out), 4) if out else 0.0,
        "min_top1": round(min(x["top1"]["score"] for x in out), 4) if out else 0.0,
        "max_top1": round(max(x["top1"]["score"] for x in out), 4) if out else 0.0,
        "distribution": dict(sorted(dist.items(), key=lambda kv: -kv[1])),
    }
    return out, stats


def apply_to_db(db: Path, assignments: list[dict], mood_vecs: dict[str, list[float]],
                model: str, run_id: str, report_path: Path, threshold: float,
                log=print) -> dict:
    if not db.exists():
        raise RuntimeError(f"apply-db не существует: {db}")
    if db.resolve() == PROD_DB.resolve():
        raise RuntimeError("apply-db указывает на production — запрещено")
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"moods", "clip_moods", "clips", "mood_runs"}
    missing = required - tables
    if missing:
        raise RuntimeError(f"apply-db не v2: нет таблиц {sorted(missing)}")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clips)")}
    if "asset_uuid" not in cols:
        raise RuntimeError("apply-db не v2: в clips нет asset_uuid")

    now = now_iso()
    try:
        conn.execute("BEGIN")
        # 1. словарь + векторы
        for m in MOODS:
            v = mood_vecs.get(m["slug"])
            blob = None
            if v:
                import struct
                blob = struct.pack("<" + "f" * len(v), *[float(x) for x in v])
            conn.execute(
                """INSERT INTO moods
                   (slug, name_ru, name_en, description_en, valence, arousal,
                    embedding, embedding_model, embedding_dim, embedding_updated_at,
                    active, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(slug) DO UPDATE SET
                     name_ru=excluded.name_ru, name_en=excluded.name_en,
                     description_en=excluded.description_en,
                     valence=excluded.valence, arousal=excluded.arousal,
                     embedding=excluded.embedding, embedding_model=excluded.embedding_model,
                     embedding_dim=excluded.embedding_dim,
                     embedding_updated_at=excluded.embedding_updated_at""",
                (m["slug"], m["name_ru"], m["name_en"], m["description_en"],
                 m["valence"], m["arousal"], blob, model, len(v) if v else None,
                 now if v else None, MOODS.index(m) + 1))
        mood_ids = {r[0]: int(r[1]) for r in conn.execute("SELECT slug, id FROM moods")}

        # 2. назначения пилота
        applied = 0
        unmatched = 0
        for a in assignments:
            row = conn.execute("SELECT id FROM clips WHERE file = ? LIMIT 1",
                               (a["file"],)).fetchone()
            if not row:
                unmatched += 1
                continue
            cid = int(row[0])
            conn.execute("DELETE FROM clip_moods WHERE clip_id = ? AND run_id LIKE 'mood-%'",
                         (cid,))
            for m in a["moods"]:
                mid = mood_ids.get(m["slug"])
                if not mid:
                    continue
                conn.execute(
                    """INSERT INTO clip_moods
                       (clip_id, mood_id, rank, score, margin, model, run_id)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(clip_id, mood_id) DO UPDATE SET
                         rank=excluded.rank, score=excluded.score,
                         margin=excluded.margin, model=excluded.model,
                         run_id=excluded.run_id, assigned_at=?""",
                    (cid, mid, m["rank"], m["score"], m.get("margin"), model, run_id, now))
                applied += 1
        conn.execute(
            """INSERT OR REPLACE INTO mood_runs
               (run_id, model, mood_count, clips_n, threshold, mode, report_json, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, model, len(MOODS), len(assignments), threshold, "pilot",
             str(report_path), "ok", now))
        conn.execute(
            """INSERT INTO audit_log
               (run_id, tool, mode, status, db_path, started_at, finished_at, summary, report_json)
               VALUES (?, 'mood_embeddings.py', 'apply', 'ok', ?, ?, ?, ?, ?)""",
            (run_id, str(db), now, now,
             json.dumps({"clips": len(assignments), "assigned_moods": applied,
                         "unmatched": unmatched}, ensure_ascii=False),
             str(report_path)))
        conn.commit()
        return {"applied_moods": applied, "clips": len(assignments),
                "unmatched": unmatched, "db": str(db)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Mood-эмбеддинги: словарь 40, cosine top-2, пилот")
    ap.add_argument("--db", type=Path, default=PROD_DB, help="БД для чтения клипов (read-only)")
    ap.add_argument("--apply-db", type=Path, default=DEFAULT_APPLY_DB)
    ap.add_argument("--embeddings", type=Path, default=DEFAULT_EMB)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=50, help="0 = все клипы")
    ap.add_argument("--threshold", type=float, default=0.30)
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--ttl-days", type=float, default=180.0)
    ap.add_argument("--refresh-moods", action="store_true")
    ap.add_argument("--apply", action="store_true")
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
    run_id = "mood-" + uuid.uuid4().hex[:12]
    json_path = args.json or out_dir / f"mood_pilot_{st}.json"
    md_path = args.md or out_dir / f"mood_pilot_{st}.md"
    started = now_iso()

    log("=" * 78)
    log("MOOD-СЛОЙ: 40 настроений -> cosine к клипам")
    log("=" * 78)
    model = model_name()
    log(f"Модель: {model} | порог: {args.threshold} | top: {args.top}")

    mood_vecs, cache_stats = ensure_mood_vectors(
        model, args.cache, args.ttl_days, args.refresh_moods, log=log)
    log(f"Векторы настроений: {cache_stats['ready']}/40 "
        f"(кэш {cache_stats['cached']}, новых {cache_stats['computed']})")
    if len(mood_vecs) < 40:
        log("[MOOD] ВНИМАНИЕ: не все 40 векторов получены (ключи/квота?)")

    if not args.embeddings.exists():
        log(f"[MOOD] нет эмбеддингов клипов: {args.embeddings}")
        return 2
    try:
        emb = json.loads(args.embeddings.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"[MOOD] эмбеддинги не читаются: {exc}")
        return 2
    rows = load_clips(args.db)
    with_emb = sum(1 for r in rows if str(r.get("file") or "") in emb)
    log(f"Клипов: {len(rows)} | с эмбеддингом: {with_emb} | в JSON: {len(emb)}")

    pilot = select_pilot(rows, emb, args.limit)
    log(f"Пилот: {len(pilot)} клипов (limit={args.limit})")
    assignments, stats = assign_moods(pilot, emb, mood_vecs, args.threshold,
                                       args.top, log=log)
    log(f"Mood назначен: {stats.get('with_mood', 0)}/{stats.get('clips_scored', 0)} "
        f"({stats.get('coverage_pct', 0)}%), avg top1={stats.get('avg_top1')}")

    warnings: list[str] = []
    if stats.get("avg_top1", 0) < args.threshold:
        warnings.append("средний top-1 ниже порога: словарь/эмбеддинги плохо разделяют клипы")
    if stats.get("coverage_pct", 0) > 99:
        warnings.append("порог 0.30 пропускает почти всё: cosine текстовых эмбеддингов "
                        "имеет высокий базовый уровень; для отбора нужен margin/z-score или калибровка")
    dist = stats.get("distribution") or {}
    if dist:
        top_slug, top_n = next(iter(dist.items()))
        if top_n >= 0.5 * max(1, stats.get("clips_scored", 1)):
            warnings.append(f"настроение «{top_slug}» доминирует ({top_n} клипов) — слабая различимость")

    report = {
        "run_id": run_id,
        "tool": "mood_embeddings.py",
        "mode": "apply" if args.apply else "report-only",
        "started_at": started,
        "finished_at": now_iso(),
        "db": str(args.db),
        "embeddings": str(args.embeddings),
        "model": model,
        "threshold": args.threshold,
        "top_n": args.top,
        "limit": args.limit,
        "clips_total": len(rows),
        "clips_with_embedding": with_emb,
        "selected": len(pilot),
        "mood_dictionary": len(MOODS),
        "mood_vectors": len(mood_vecs),
        "cache": cache_stats,
        "stats": stats,
        "warnings": warnings,
        "assignments": assignments,
        "apply": None,
    }

    if args.apply:
        if args.apply_db.resolve() == PROD_DB.resolve():
            log("[MOOD] apply-db = production; запись запрещена (используйте v2-копию)")
            return 2
        try:
            applied = apply_to_db(args.apply_db, assignments, mood_vecs, model, run_id,
                                  json_path, args.threshold, log=log)
            report["apply"] = applied
            log(f"[MOOD] записано в {applied['db']}: клипов {applied['clips']}, "
                f"mood-строк {applied['applied_moods']}, unmatched {applied['unmatched']}")
        except Exception as exc:
            report["apply"] = {"error": str(exc)}
            log(f"[MOOD] apply не выполнен: {exc}")
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return 2
    else:
        log("[MOOD] режим отчёта: БД не изменялась (для записи — --apply)")

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append(f"# Mood-пилот — {st}")
    md.append("")
    md.append(f"- **БД чтения:** `{args.db}`")
    md.append(f"- **Модель:** `{model}` | **порог:** {args.threshold} | **top:** {args.top}")
    md.append(f"- **Клипов в БД:** {len(rows)} | **с эмбеддингом:** {with_emb}")
    md.append(f"- **Пилот:** {len(pilot)} клипов | **с mood:** {stats.get('with_mood', 0)} "
              f"({stats.get('coverage_pct', 0)}%)")
    md.append(f"- **Средний top-1:** {stats.get('avg_top1')} "
              f"(min {stats.get('min_top1')}, max {stats.get('max_top1')})")
    md.append(f"- **Векторы словаря:** {cache_stats.get('ready')}/40 "
              f"(кэш {cache_stats.get('cached')}, новых {cache_stats.get('computed')})")
    md.append("")
    if warnings:
        md.append("## Предупреждения")
        md.append("")
        for w in warnings:
            md.append(f"- {w}")
        md.append("")
    md.append("## Распределение top-2")
    md.append("")
    md.append("| Настроение | Клипов |")
    md.append("|---|---|")
    for slug, n in (stats.get("distribution") or {}).items():
        md.append(f"| {slug} | {n} |")
    md.append("")
    md.append("## Пилот по клипам")
    md.append("")
    md.append("| Файл | Категория | top-1 | top-2 | margin |")
    md.append("|---|---|---|---|---|")
    for a in assignments:
        m1 = a["moods"][0] if a["moods"] else None
        m2 = a["moods"][1] if len(a["moods"]) > 1 else None
        t1 = f"{m1['slug']} {m1['score']:.3f}" if m1 else f"— (top1 {a['top1']['score']:.3f})"
        t2 = f"{m2['slug']} {m2['score']:.3f}" if m2 else "—"
        mg = f"{m1['margin']:.3f}" if m1 and m1.get("margin") is not None else "—"
        md.append(f"| `{Path(a['file']).name}` | {a['category']} | {t1} | {t2} | {mg} |")
    md.append("")
    md.append("## Интерпретация")
    md.append("")
    md.append("Mood — это **ранжирование**, а не жёсткая метка: top-2 с порогом 0.30 "
              "даёт кандидатов для сцены, финально выбирает VLM/редактор. "
              "Если порог пропускает всё — используйте margin/z-score или откалибруйте "
              "порог на 100–200 размеченных клипах.")
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
