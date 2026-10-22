"""Библиотека клипов с описаниями (Vision) и лицензиями — «большая база».

Файлы + SQLite-индекс живут на сервере (/Volumes/video/broll_library или
локальный путь) — доступны и основному приложению, и VideoMaker Next.

Записи содержат: файл, источник (URL), автора, лицензию (для защиты от
претензий), Vision-описание, теги, оценку, резкость, разрешение, длительность.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LIBRARY_ROOT = "/Volumes/video/broll_library"
# SQLite по SMB не работает (disk I/O error) — индекс держим ЛОКАЛЬНО,
# файлы — на сервере; рядом с файлами пишем JSON-зеркало для переносимости.
LOCAL_DB_PATH = Path.home() / "video_maker" / "broll_library.db"

# Лицензия Pixabay (свободно для коммерческого использования, атрибуция не обязательна)
PIXABAY_LICENSE = "Pixabay Content License"
PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"
PEXELS_LICENSE = "Pexels License"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT UNIQUE,
    orientation TEXT,
    rel_path TEXT,
    provider TEXT,
    source_url TEXT,
    author TEXT,
    license TEXT,
    license_url TEXT,
    query TEXT,
    category TEXT,
    description TEXT,
    tags TEXT,
    neg_tags TEXT,
    score INTEGER,
    blur REAL,
    width INTEGER,
    height INTEGER,
    duration REAL,
    bitrate REAL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_url ON clips(source_url);
CREATE INDEX IF NOT EXISTS idx_clips_category ON clips(category);
CREATE TABLE IF NOT EXISTS usage (
    file TEXT,
    series TEXT,
    scene INTEGER,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_file ON usage(file);
"""


def db_path(root: str | Path = DEFAULT_LIBRARY_ROOT) -> Path:
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return LOCAL_DB_PATH


def init_db(root: str | Path = DEFAULT_LIBRARY_ROOT) -> sqlite3.Connection:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path(root)))
    conn.executescript(_SCHEMA)
    try:  # миграция старой базы: добавить orientation
        conn.execute("ALTER TABLE clips ADD COLUMN orientation TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:  # миграция: битрейт
        conn.execute("ALTER TABLE clips ADD COLUMN bitrate REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:  # миграция: минус-теги (чего в клипе НЕТ)
        conn.execute("ALTER TABLE clips ADD COLUMN neg_tags TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return conn


def upsert_clip(conn: sqlite3.Connection, rec: dict, root: str | Path = DEFAULT_LIBRARY_ROOT) -> None:
    root = Path(root)
    f = str(rec.get("file") or "")
    rel = str(Path(f).relative_to(root)) if f.startswith(str(root)) else f
    if not str(rec.get("description") or "").strip():
        rec = dict(rec)
        rec["description"] = ", ".join(str(t) for t in (rec.get("tags") or []))[:200]
    conn.execute(
        """
        INSERT INTO clips (file, orientation, rel_path, provider, source_url, author, license, license_url,
                           query, category, description, tags, neg_tags, score, blur, width, height,
                           duration, bitrate, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(file) DO UPDATE SET
            description=excluded.description, tags=excluded.tags, neg_tags=excluded.neg_tags,
            score=excluded.score,
            blur=excluded.blur, source_url=excluded.source_url, author=excluded.author,
            license=excluded.license, license_url=excluded.license_url
        """,
        (
            f, str(rec.get("orientation") or ""), rel,
            str(rec.get("provider") or ""), str(rec.get("source_url") or ""),
            str(rec.get("author") or ""), str(rec.get("license") or ""),
            str(rec.get("license_url") or ""), str(rec.get("query") or ""),
            str(rec.get("category") or ""), str(rec.get("description") or ""),
            json.dumps(rec.get("tags") or [], ensure_ascii=False),
            json.dumps(rec.get("neg_tags") or [], ensure_ascii=False),
            int(rec.get("score") or -1), float(rec.get("blur") or -1),
            int(rec.get("width") or 0), int(rec.get("height") or 0),
            float(rec.get("duration") or 0), float(rec.get("bitrate") or 0),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    # [зеркало] JSON-индекс рядом с файлами (для PVE/Next) — не критично при ошибке
    try:
        rows = conn.execute(
            "SELECT file, orientation, rel_path, provider, source_url, author, license, license_url, "
            "query, category, description, tags, neg_tags, score, blur, width, height, duration, bitrate "
            "FROM clips"
        ).fetchall()
        cols = ["file", "orientation", "rel_path", "provider", "source_url", "author", "license",
                "license_url", "query", "category", "description", "tags", "neg_tags", "score",
                "blur", "width", "height", "duration", "bitrate"]
        mirror = root / "library_index.json"
        mirror.write_text(
            json.dumps([dict(zip(cols, r)) for r in rows], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def url_known(conn: sqlite3.Connection, url: str) -> bool:
    if not url:
        return False
    cur = conn.execute("SELECT 1 FROM clips WHERE source_url = ? LIMIT 1", (str(url),))
    return cur.fetchone() is not None


def ingest_file(path: str, *, query: str = "", category: str = "series",
                root: str | Path = DEFAULT_LIBRARY_ROOT, log=print) -> bool:
    """Скачанный для серии клип → в большую базу — ТОЛЬКО после подтверждения Vision.

    Копирует файл на сервер, описывает (Vision: 3-5 кадров, эмоции, +/−), пишет в БД и вектор.
    """
    try:
        p = Path(str(path))
        if not p.exists():
            return False
        conn = init_db(root)
        if conn.execute("SELECT 1 FROM clips WHERE file LIKE ? LIMIT 1", (f"%{p.name}%",)).fetchone():
            return False
        import subprocess as _sp
        r = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                    capture_output=True, text=True, timeout=25)
        w, h = (int(x) for x in (r.stdout or "0,0").strip().split(",")[:2])
        orient = "vertical" if h > w else "wide"
        import shutil as _sh
        folder = Path(root) / ("vertical" if orient == "vertical" else "horizontal") / "series"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / p.name
        _sh.copy2(p, dest)
        rec = {"file": str(dest), "orientation": orient, "provider": "series_pool",
               "query": str(query)[:80], "category": category, "license": "сток",
               "license_url": "", "tags": [], "neg_tags": [], "blur": -1,
               "width": w, "height": h, "duration": 0}
        try:  # ОБЯЗАТЕЛЬНО: Vision подтверждает, что в клипе (описание/эмоции/+/−)
            import importlib.util as _iu
            _spec = _iu.spec_from_file_location(
                "bl", str(Path(__file__).resolve().parents[2] / "tools" / "build_library.py"))
            _bl = _iu.module_from_spec(_spec)
            _spec.loader.exec_module(_bl)
            batch = [rec]
            if not _bl._describe_batch(batch, log=log):
                log(f"[LIB] Vision не подтвердил — в базу НЕ добавляем: {p.name[:40]}")
                try:
                    dest.unlink()
                except Exception:
                    pass
                return False
            rec = batch[0]
        except Exception as e:
            log(f"[LIB] Vision при добавлении не удался: {str(e)[:80]}")
            return False
        upsert_clip(conn, rec, root)
        try:
            from . import embeddings as _E
            v = _E.embed_text(_E.clip_text(rec))
            if v:
                emb = _E.load()
                emb[str(dest)] = v
                _E.save(emb)
        except Exception:
            pass
        log(f"[LIB] ⊕ в базу (Vision подтвердил): {dest.name} — {str(rec.get('description'))[:60]}")
        return True
    except Exception as e:
        try:
            log(f"[LIB] добавление в базу пропущено: {str(e)[:80]}")
        except Exception:
            pass
        return False


def find_by_path(path: str, root: str | Path = DEFAULT_LIBRARY_ROOT) -> dict | None:
    """Запись библиотеки по пути клипа (для сертификатов/кредитов).

    Понимает симлинки из серийных пулов: resolve → файл библиотеки.
    """
    try:
        conn = sqlite3.connect(str(db_path(root)))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM clips WHERE file = ? LIMIT 1", (str(path),))
        row = cur.fetchone()
        if not row:
            try:
                real = str(Path(str(path)).resolve())
            except Exception:
                real = str(path)
            if real != str(path):
                row = conn.execute("SELECT * FROM clips WHERE file = ? LIMIT 1", (real,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def search_library(query: str, *, orientation: str | None = None, limit: int = 6,
                   min_score: int = 5, exclude: set[str] | None = None,
                   avoid: set[str] | None = None, hard: set[str] | None = None,
                   weight_terms: dict[str, float] | None = None,
                   root: str | Path = DEFAULT_LIBRARY_ROOT) -> list[dict]:
    """Подбор клипов из большой базы по описанию/тегам/запросу (без скачивания).

    Ранжирование: число совпавших слов → оценка Vision → резкость.
    exclude — уже использованные файлы (в рамках серии).
    """
    from .matcher import tokenize  # локальный импорт: без циклической зависимости
    toks = [t for t in sorted(tokenize(query)) if len(t) >= 3]
    if not toks:
        return []
    try:
        conn = sqlite3.connect(str(db_path(root)))
        conn.row_factory = sqlite3.Row
        where = ["score >= ?"]
        args: list = [int(min_score)]
        if orientation:
            where.append("(orientation = ? OR orientation IS NULL OR orientation = '')")
            args.append(str(orientation))
        rows = conn.execute(
            "SELECT * FROM clips WHERE " + " AND ".join(where), args
        ).fetchall()
        conn.close()
    except Exception:
        return []
    excl = {str(p) for p in (exclude or set())}
    # [EMB] семантический слой «как у топовых»: dense retrieval по смыслу
    sem: dict[str, float] = {}
    try:
        from .embeddings import semantic_rank
        for _f, _s in semantic_rank(query, top_k=max(int(limit) * 4, 12), exclude=excl):
            if _s >= 0.5:
                sem[_f] = _s
    except Exception:
        pass
    by_file = {str(r["file"]): dict(r) for r in rows}
    import re as _re
    _pats = [(t, _re.compile(r"\b" + _re.escape(t) + r"\b")) for t in toks]  # границы слов (down ≠ pulldown)
    _av = {str(x).lower() for x in (avoid or set())}
    _hard = {str(x).lower() for x in (hard or set())}
    scored: list[tuple[float, dict]] = []
    for r in rows:
        rec = dict(r)
        f = str(rec.get("file") or "")
        if not f or f in excl:
            continue
        hay = " ".join(str(rec.get(k) or "") for k in ("description", "tags")).lower()
        if _hard and any(t in hay for t in _hard):
            continue  # жёсткий СТОП сцены (например, smiling для похорон) — клип не подходит
        hits = sum(1 for _t, _p in _pats if _p.search(hay))
        if hits <= 0 and not weight_terms:
            continue
        if hits <= 0:
            hits = 0  # без совпадений по содержанию — без бесплатных очков
        try:
            if not Path(f).exists():
                continue
        except Exception:
            continue
        sc = float(rec.get("score") or 0)
        bl = float(rec.get("blur") or 0)
        neg = str(rec.get("neg_tags") or "").lower()
        neg_hits = sum(1 for t in toks if t in neg)
        rank = hits * 10.0 + sc * 0.1 + min(bl, 3000) * 0.001 + sem.get(f, 0.0) * 15.0
        if neg_hits:
            rank *= 0.3  # минус-слова: клип хуже подходит под запрос
        if _av and any(t in hay or t in neg for t in _av):
            rank *= 0.15  # запретные слова сцены (например, smiling для грустной сцены)
        for _t, _w in (weight_terms or {}).items():
            if _t.lower() in hay:
                rank += float(_w) * 2.0  # вес — уточнитель, не драйвер (было x8 — перебивало содержание)
        scored.append((rank, rec))
    # смысловые совпадения без общих слов — тоже берём (ниже ключевых)
    have = {str(rec.get("file") or "") for _, rec in scored}
    for _f, _s in sem.items():
        if _f in have or _f in excl:
            continue
        _rec = by_file.get(_f)
        if not _rec:
            continue
        try:
            if not Path(_f).exists():
                continue
        except Exception:
            continue
        scored.append((_s * 15.0 - 1.0, _rec))
    scored.sort(key=lambda x: -x[0])
    return [rec for _, rec in scored[: int(limit)]]


# ======================================================================
# [VM-CASCADE] Программный веер запросов + RRF-слияние + дедуп близнецов.
# Пул кандидатов шире (top-20..30 вместо top-4) и штрафы hard/avoid применяются
# ПОСЛЕ поиска (кандидат не выбрасывается из пула, а уходит вниз) — VLM видит
# больше осмысленных вариантов. Никаких LLM: только правила и словарь синонимов.
# ======================================================================

# Синонимы для веера запросов (типовой B-roll словарь; RU-текст сюда не попадает —
# запросы сцен англоязычные).
_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "smartphone": ("phone", "mobile phone"),
    "phone": ("smartphone", "mobile phone"),
    "hand": ("hands", "palm"),
    "hands": ("hand", "palms"),
    "man": ("male", "person"),
    "woman": ("female", "person"),
    "person": ("man", "woman"),
    "people": ("crowd", "group of people"),
    "child": ("kid", "boy"),
    "children": ("kids", "child"),
    "walking": ("walk", "strolling"),
    "walk": ("walking", "strolling"),
    "running": ("run", "jogging"),
    "looking": ("look", "gazing"),
    "sitting": ("seated", "sit"),
    "standing": ("stand", "waiting"),
    "holding": ("hold", "carrying"),
    "using": ("use", "checking"),
    "scrolling": ("scroll", "swiping"),
    "reading": ("read", "book"),
    "thinking": ("thoughtful", "pensive"),
    "tired": ("exhausted", "fatigue"),
    "angry": ("frustrated", "mad"),
    "sad": ("upset", "unhappy"),
    "happy": ("joyful", "smiling"),
    "street": ("city street", "road"),
    "office": ("workplace", "work desk"),
    "night": ("dark", "evening"),
    "morning": ("sunrise", "early"),
    "crowd": ("people", "many people"),
    "alone": ("lonely", "solitary"),
    "table": ("desk", "surface"),
    "screen": ("display", "monitor"),
    "eyes": ("eye", "gaze"),
    "car": ("auto", "vehicle"),
    "food": ("meal", "eating"),
    "coffee": ("cup of coffee", "cafe"),
    "baby": ("infant", "toddler"),
    "elderly": ("old person", "senior"),
}

_QUERY_STOP: frozenset[str] = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "at", "with", "and", "or", "for", "from",
    "into", "over", "near", "while", "during", "his", "her", "their", "very", "close",
    "up", "front", "background", "foreground", "shot", "view", "scene", "clip", "video",
})


def norm_phrase(text: str) -> str:
    """Нормализация компаундов для матчинга тегов: smart phone ↔ smartphone и т.п."""
    t = " ".join(str(text or "").lower().replace("-", " ").split())
    for a, b in (("smart phone", "smartphone"), ("cell phone", "cellphone"),
                 ("mobile phone", "smartphone"), ("close up", "closeup"),
                 ("time lapse", "timelapse"), ("slow motion", "slowmotion"),
                 ("high five", "highfive"), ("work out", "workout")):
        t = t.replace(a, b)
    return t


def query_variants(query: str, *, max_variants: int = 4) -> list[str]:
    """Программный веер запроса: нормализация + синонимы + содержательные слова.

    Возвращает [базовый, ...] без дублей; первый элемент — всегда исходный запрос.
    """
    base = " ".join(str(query or "").lower().split())
    if not base:
        return []
    out: list[str] = [base]

    def _add(v: str) -> None:
        v = " ".join(v.split())
        if v and v not in out:
            out.append(v)

    words = base.replace("-", " ").split()
    # 1) склейка/разъединение компаундов (smart phone → smartphone и обратно)
    _add(norm_phrase(base))
    if "smartphone" in norm_phrase(base):
        _add(norm_phrase(base).replace("smartphone", "smart phone"))
    # 2) синонимичные замены (до 2 разных слов за вариант)
    swapped = 0
    for i, w in enumerate(words):
        syns = _QUERY_SYNONYMS.get(w)
        if not syns:
            continue
        v1 = " ".join(words[:i] + [syns[0]] + words[i + 1:])
        _add(v1)
        if len(syns) > 1:
            v2 = " ".join(words[:i] + [syns[1]] + words[i + 1:])
            _add(v2)
        swapped += 1
        if swapped >= 2:
            break
    # 3) только содержательные слова (упрощение фразы)
    content = [w for w in norm_phrase(base).split() if w not in _QUERY_STOP and len(w) > 2]
    if content and len(content) >= 2:
        _add(" ".join(content))
        _add(" ".join(content[:3]))
    return out[: max(1, int(max_variants))]


def _clip_source_id(rec: dict) -> str:
    """ID источника клипа (pixabay/pexels) — для отсева дублей по происхождению."""
    import re as _re
    u = str(rec.get("source_url") or "")
    m = _re.search(r"pixabay[^/]*?(\d{4,})", u) or _re.search(r"pexels[^/]*?(\d{3,})", u)
    if m:
        return f"src:{m.group(1)}"
    name = Path(str(rec.get("file") or "")).name
    m = _re.search(r"_(\d{4,})_pixabay", name, _re.I) or _re.search(r"pixabay[_-](\d{4,})", name, _re.I)
    return f"src:{m.group(1)}" if m else ""


def dedup_twins(records: list[dict], *, max_cos: float = 0.92) -> list[dict]:
    """Убрать близнецов: один источник (pixabay id) или cosine описаний > max_cos.

    Порядок сохраняется (побеждает первый, более высокий в ранжировании).
    """
    emb: dict = {}
    try:
        from .embeddings import load as _load_emb
        emb = _load_emb()
    except Exception:
        emb = {}
    seen_ids: set[str] = set()
    kept: list[dict] = []
    kept_vecs: list[tuple[str, list[float]]] = []

    def _cos(a: list[float], b: list[float]) -> float:
        try:
            s = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            return s / (na * nb + 1e-9)
        except Exception:
            return 0.0

    for rec in records:
        f = str(rec.get("file") or "")
        sid = _clip_source_id(rec)
        if sid and sid in seen_ids:
            continue
        v = emb.get(f)
        if v is not None and kept_vecs and max_cos < 1.0:
            if any(_cos(v, kv) > max_cos for _, kv in kept_vecs):
                continue
        if sid:
            seen_ids.add(sid)
        kept.append(rec)
        if v is not None:
            kept_vecs.append((f, v))
    return kept


def search_library_multi(queries: list[str], *, orientation: str | None = None,
                         per_query: int = 20, limit: int = 30,
                         exclude: set[str] | None = None, avoid: set[str] | None = None,
                         hard: set[str] | None = None,
                         weight_terms: dict[str, float] | None = None,
                         extra_terms: str = "", dedup_cos: float = 0.92,
                         rrf_k: int = 60, fanout: bool = True, max_variants: int = 4,
                         root: str | Path = DEFAULT_LIBRARY_ROOT,
                         log=None) -> list[dict]:
    """Поиск по базе веером запросов: RRF-слияние + штрафы hard/avoid + дедуп.

    hard/avoid НЕ выбрасывают клип из пула (как search_library), а штрафуют его
    ранжирование — VLM потом решает сам (осознанный отбор, а не слепая отсечка).
    """
    qs: list[str] = []
    for q in (queries or []):
        q = str(q or "").strip()
        if not q:
            continue
        variants = query_variants(q, max_variants=max_variants) if fanout else [q]
        for v in variants:
            if v not in qs:
                qs.append(v)
    if not qs:
        return []
    lists: list[list[str]] = []
    by_file: dict[str, dict] = {}
    for q in qs:
        enriched = " ".join([q] + ([extra_terms] if extra_terms else []))
        try:
            # [VM-CASCADE] weight_terms НЕ передаём: иначе search_library пропускает
            # клипы с НУЛЁМ ключевых совпадений (эмоции/настроение) — и веер наполнялся
            # «мусором из хвоста», который в RRF побеждал по консенсусу. Эмоции —
            # маленький бонус в слиянии (ниже), а не вход в пул.
            hits = search_library(enriched, orientation=orientation, limit=per_query,
                                  exclude=exclude, avoid=None, hard=None,
                                  weight_terms=None, root=root)
        except Exception:
            hits = []
        lst = []
        for h in hits:
            f = str(h.get("file") or "")
            if not f:
                continue
            by_file.setdefault(f, h)
            lst.append(f)
        if lst:
            lists.append(lst)
    if not lists:
        return []
    rrf: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for lst in lists:
        for rank, f in enumerate(lst, 1):
            rrf[f] = rrf.get(f, 0.0) + 1.0 / (rrf_k + rank)
            if f not in best_rank or rank < best_rank[f]:
                best_rank[f] = rank
    # [VM-CASCADE] бонус за лучший ранг (см. stock.search_stock_multi): гемы,
    # найденные одним запросом, не должны тонуть под консенсусом вариантов
    final = {k: rrf[k] + 1.5 / (rrf_k + best_rank[k]) for k in rrf}
    _av = {norm_phrase(x) for x in (avoid or set()) if str(x).strip()}
    _hd = {norm_phrase(x) for x in (hard or set()) if str(x).strip()}
    for f in list(final.keys()):
        rec = by_file.get(f) or {}
        hay = norm_phrase(" ".join(str(rec.get(k) or "") for k in ("description", "tags")))
        if _hd and any(t in hay for t in _hd):
            final[f] = final[f] * 0.05   # hard: в самый низ, но из пула не выкидываем
        elif _av and any(t in hay for t in _av):
            final[f] = final[f] * 0.30
        # эмоции/настроение — малый бонус (уточнитель, не драйвер)
        for _t, _w in (weight_terms or {}).items():
            if str(_t).lower() in hay:
                final[f] += 0.02 * float(_w)
    ranked = sorted(final.items(), key=lambda kv: -kv[1])
    records = [by_file[f] for f, _ in ranked]
    records = dedup_twins(records, max_cos=dedup_cos)
    if log:
        try:
            log(f"[LIB] веер {len(qs)} запросов → пул {len(by_file)} → дедуп {len(records)}")
        except Exception:
            pass
    return records[: int(limit)]


def log_usage(files_by_scene: list[tuple[int, str]], series: str,
              root: str | Path = DEFAULT_LIBRARY_ROOT) -> int:
    """Отметить использование клипов в серии (для повторного поиска/статистики)."""
    try:
        conn = sqlite3.connect(str(db_path(root)))
        conn.execute("DELETE FROM usage WHERE series = ?", (series,))
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        for scene_idx, f in files_by_scene:
            conn.execute("INSERT INTO usage (file, series, scene, used_at) VALUES (?,?,?,?)",
                         (str(f), series, int(scene_idx), now))
            n += 1
        conn.commit()
        conn.close()
        return n
    except Exception:
        return 0


def library_stats(root: str | Path = DEFAULT_LIBRARY_ROOT) -> dict:
    try:
        conn = sqlite3.connect(str(db_path(root)))
        n = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        cats = conn.execute(
            "SELECT category, COUNT(*) FROM clips GROUP BY category ORDER BY 2 DESC LIMIT 20"
        ).fetchall()
        conn.close()
        return {"total": n, "by_category": cats}
    except Exception:
        return {"total": 0, "by_category": []}


def _manifest_lookup(path: str) -> dict | None:
    """Ищем клип в манифестах smart_broll (URL/теги/провайдер) по имени файла."""
    name = Path(path).name
    if not name:
        return None
    try:
        import glob as _glob
        for man in _glob.glob("/tmp/vm_sb_test2/*/smart_broll_manifest.json") + _glob.glob(
            "/Volumes/video/broll_downloads/*/smart_broll_manifest.json"
        ):
            try:
                d = json.loads(Path(man).read_text(encoding="utf-8"))
            except Exception:
                continue
            for it in d.get("downloaded", []):
                if Path(str(it.get("file") or "")).name == name:
                    return it
    except Exception:
        pass
    return None


def write_clip_credits(used_paths: list[str], series_dir: str, *,
                       library_root: str | Path = DEFAULT_LIBRARY_ROOT, log=print) -> str | None:
    """Сертификаты: какие клипы использованы + откуда/лицензия — для защиты от претензий.

    Пишет <series_dir>/clip_credits.json и clip_credits.txt.
    """
    try:
        out_dir = Path(series_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        items: list[dict] = []
        seen: set[str] = set()
        for p in used_paths:
            p = str(p or "")
            if not p or p in seen:
                continue
            seen.add(p)
            rec = find_by_path(p, library_root)
            if rec:
                _page = ""
                try:  # официальная страница клипа из сертификата (убедительнее CDN-ссылки)
                    _cj = json.loads((Path(library_root) / "licenses" / f"{Path(p).name}.json")
                                     .read_text(encoding="utf-8"))
                    _page = str(_cj.get("page_url") or "")
                except Exception:
                    pass
                items.append({
                    "file": Path(p).name,
                    "path": p,
                    "provider": rec.get("provider"),
                    "source_url": _page or rec.get("source_url"),
                    "page_url": _page,
                    "author": rec.get("author"),
                    "license": rec.get("license"),
                    "license_url": rec.get("license_url"),
                    "description": rec.get("description"),
                    "score": rec.get("score"),
                })
                continue
            man = _manifest_lookup(p)
            if man:
                prov = str(man.get("provider") or "pixabay")
                items.append({
                    "file": Path(p).name,
                    "path": p,
                    "provider": prov,
                    "source_url": man.get("url") or "",
                    "author": man.get("author") or "",
                    "license": PIXABAY_LICENSE if prov == "pixabay" else PEXELS_LICENSE,
                    "license_url": PIXABAY_LICENSE_URL if prov == "pixabay" else PEXELS_LICENSE_URL,
                    "description": man.get("what") or (man.get("tags") or ""),
                    "score": man.get("score"),
                })
                continue
            items.append({
                "file": Path(p).name, "path": p, "provider": "unknown",
                "source_url": "", "author": "", "license": "своя запись (не сток)",
                "license_url": "", "description": "", "score": None,
            })
        payload = {
            "series": out_dir.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "clips_used": items,
            "note": "Сток: Pixabay/Pexels — свободно для коммерческого использования; "
                    "ссылки на источник сохранены для ответа на возможные претензии.",
        }
        (out_dir / "clip_credits.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [f"ИСПОЛЬЗОВАННЫЕ КЛИПЫ — {out_dir.name}", "=" * 60]
        for it in items:
            lines.append(f"\n• {it['file']}")
            if it.get("description"):
                lines.append(f"  что на видео: {it['description']}")
            lines.append(f"  источник: {it.get('provider')} {it.get('source_url') or ''}")
            if it.get("author"):
                lines.append(f"  автор: {it['author']}")
            lines.append(f"  лицензия: {it.get('license')} {it.get('license_url') or ''}")
        (out_dir / "clip_credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if log:
            log(f"[CREDITS] сертификаты клипов → {out_dir / 'clip_credits.txt'} ({len(items)} клипов)")
        return str(out_dir / "clip_credits.json")
    except Exception as e:
        if log:
            log(f"[CREDITS] пропуск: {e}")
        return None
