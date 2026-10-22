"""Семантические эмбеддинги клипов — dense retrieval «как у топовых систем».

Каждому клипу — вектор смысла: описание + теги (мультиязычная модель Gemini,
бесплатная). Поиск: косинус между вектором запроса и векторами клипов;
в library.search_library работает гибрид: ключевые слова + смысл + VLM-оценка.

Файл: ~/video_maker/broll_embeddings.json  (путь клипа → вектор 3072 чисел)
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

EMB_PATH = Path.home() / "video_maker" / "broll_embeddings.json"
MODEL = "gemini-embedding-001"


def _keys() -> list[str]:
    out: list[str] = []
    env = Path.home() / ".vm_gemini_keys.env"
    try:
        for raw in env.read_text(encoding="utf-8").splitlines():
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
    return out


def embed_text(text: str, *, timeout: int = 30) -> list[float] | None:
    """Вектор текста (пробуем ключи по очереди)."""
    for k in _keys():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:embedContent"
        body = json.dumps({"model": f"models/{MODEL}",
                           "content": {"parts": [{"text": str(text)[:2000]}]}}).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": k}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            v = d.get("embedding", {}).get("values") or []
            if v:
                return [float(x) for x in v]
        except Exception:
            continue
    return None


def load() -> dict:
    try:
        return json.loads(EMB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(emb: dict) -> None:
    EMB_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMB_PATH.write_text(json.dumps(emb), encoding="utf-8")


def clip_text(rec: dict) -> str:
    tags = str(rec.get("tags") or "").replace('"', " ").replace("[", " ").replace("]", " ")
    parts = [str(rec.get("description") or ""), tags]
    return " ".join(p for p in parts if p.strip())[:1500]


def build(records: list[dict], *, log=print, pause: float = 0.3, force: bool = False) -> int:
    """Векторы для всех клипов (инкрементально; force — пересобрать всё)."""
    emb = {} if force else load()
    added = 0
    for i, rec in enumerate(records, 1):
        f = str(rec.get("file") or "")
        if not f or f in emb:
            continue
        v = embed_text(clip_text(rec))
        if v:
            emb[f] = v
            added += 1
            if added % 50 == 0:
                save(emb)
                log(f"[EMB] готово {added} (из {len(records)})", flush=True)
        time.sleep(pause)
    save(emb)
    return added


def cosine(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb + 1e-9)


# [VM-CACHE] Кэш векторов ЗАПРОСОВ: веер запросов делает до 8-10 embedding-вызовов
# на сцену (~3с каждый) — без кэша это десятки минут на серию. Вектор текста
# детерминирован, поэтому кэш не меняет результат, только скорость.
_QCACHE_PATH = Path.home() / "video_maker" / "query_emb_cache.json"
_QCACHE: dict | None = None
_QCACHE_DIRTY = 0
_EMB_COOLDOWN_UNTIL = 0.0  # [VM-CACHE] пауза после полного отказа (квота) — не долбить ключи


def _qcache_load() -> dict:
    global _QCACHE
    if _QCACHE is None:
        try:
            _QCACHE = json.loads(_QCACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _QCACHE = {}
    return _QCACHE


def embed_query_cached(text: str, *, timeout: int = 30) -> list[float] | None:
    """Вектор запроса с дисковым кэшем (текст → вектор) и паузой при отказе квоты."""
    global _QCACHE_DIRTY, _EMB_COOLDOWN_UNTIL
    import time as _t
    key = str(text)[:2000]
    cache = _qcache_load()
    hit = cache.get(key)
    if hit:
        return hit
    if _t.time() < _EMB_COOLDOWN_UNTIL:  # квота/ключи лежат — не тратим минуты на обход
        return None
    v = embed_text(key, timeout=timeout)
    if not v:
        _EMB_COOLDOWN_UNTIL = _t.time() + 120.0
        return None
    cache[key] = v
    _QCACHE_DIRTY += 1
    if _QCACHE_DIRTY % 10 == 0:
        try:
            if len(cache) > 20000:  # не растим бесконечно
                for k in list(cache.keys())[:5000]:
                    cache.pop(k, None)
            _QCACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _QCACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
            _QCACHE_DIRTY = 0
        except Exception:
            pass
    return v


def embed_texts_cached(texts: list[str], *, timeout: int = 90) -> int:
    """Векторы пачки текстов ОДНИМ batch-вызовом (batchEmbedContents) + кэш.

    Возвращает число полученных новых векторов. Для веера запросов: 4-8 вариантов
    за один сетевой вызов вместо 4-8 последовательных (~3с каждый).
    """
    import urllib.request as _u
    cache = _qcache_load()
    todo = []
    for t in texts:
        k = str(t)[:2000]
        if k and k not in cache and k not in todo:
            todo.append(k)
    if not todo:
        return 0
    got = 0
    for k in _keys():
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
               f":batchEmbedContents")
        body = json.dumps({"requests": [
            {"model": f"models/{MODEL}", "content": {"parts": [{"text": t}]}} for t in todo
        ]}).encode()
        req = _u.Request(url, data=body, headers={"Content-Type": "application/json",
                                                  "x-goog-api-key": k}, method="POST")
        try:
            with _u.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            embs = d.get("embeddings") or []
            for t, e in zip(todo, embs):
                v = e.get("values") or []
                if v:
                    cache[t] = [float(x) for x in v]
                    got += 1
            if got:
                try:
                    _QCACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                    _QCACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
                except Exception:
                    pass
                return got
        except Exception:
            continue
    return got


def semantic_rank(query: str, *, top_k: int = 20, exclude: set | None = None) -> list[tuple[str, float]]:
    """Топ клипов по смыслу (косинус). [] — если эмбеддингов ещё нет."""
    emb = load()
    if not emb:
        return []
    qv = embed_query_cached(query)
    if not qv:
        return []
    excl = {str(p) for p in (exclude or set())}
    out = [(f, cosine(qv, v)) for f, v in emb.items() if f not in excl]
    out.sort(key=lambda x: -x[1])
    return out[: int(top_k)]
