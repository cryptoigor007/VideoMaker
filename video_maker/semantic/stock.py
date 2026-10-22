"""Поиск и скачивание стока (Pixabay; Pexels — если ключ рабочий).

Ключи: env (PEXELS_API_KEY / PIXABAY_API_KEY) или ~/.vm_broll_keys.env.
Фильтры: 4K/HD под ориентацию, длительность 4–25с.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests

_PIXABAY_URL = "https://pixabay.com/api/videos/"
_PEXELS_URL = "https://api.pexels.com/videos/search"

# Слова-исключения: не показываем животных/мультики, когда сцена про людей
_DEFAULT_EXCLUDE: tuple[str, ...] = (
    "dog", "puppy", "cat", "kitten", "pet", "animal", "wildlife", "cartoon",
    "bird", "squirrel", "insect", "reptile", "fish", "monkey",
)

# Слабые слова: не участвуют в оценке релевантности тегов
_REL_STOP: frozenset[str] = frozenset({
    "two", "old", "close", "using", "up", "the", "and", "new", "one", "big",
})


def tag_relevance(tags: str, query: str) -> int:
    """Сколько значимых слов запроса реально встречается в тегах (по границам слов).

    Слабые слова (two/old/close/...) игнорируются — иначе Pixabay матчит
    «two-tone» на «two» и «old forest» на «old». Компаунды нормализуются:
    «smart phone» ↔ «smartphone» (иначе тег «smart phone» не матчится на запрос
    «smartphone» — реальный промах ранжирования).
    """
    from .library import norm_phrase
    t = norm_phrase(tags)
    if not t:
        return 0
    n = 0
    for w in norm_phrase(query).split():
        if len(w) < 3 or w in _REL_STOP:
            continue
        cands = {w}
        if w.endswith("ing") and len(w) > 5:
            cands.add(w[:-3])
        if w.endswith("ed") and len(w) > 4:
            cands.add(w[:-2])
        if w.endswith("s") and len(w) > 3:
            cands.add(w[:-1])
        if any(re.search(rf"\b{re.escape(c)}", t) for c in cands):
            n += 1
    return n


def _tags_bad(tags: str, exclude: tuple[str, ...] = _DEFAULT_EXCLUDE) -> bool:
    t = str(tags or "").lower()
    if not t:
        return False
    return any(bad in t for bad in exclude)



def load_keys() -> tuple[str, str]:
    """(pexels, pixabay) — из env, иначе из ~/.vm_broll_keys.env."""
    pex = os.environ.get("PEXELS_API_KEY", "").strip()
    pix = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not (pex and pix):
        env_file = Path.home() / ".vm_broll_keys.env"
        if env_file.exists():
            try:
                for raw in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    line = line.removeprefix("export ").strip()
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "PEXELS_API_KEY" and not pex:
                        pex = v
                    elif k == "PIXABAY_API_KEY" and not pix:
                        pix = v
            except Exception:
                pass
    return pex, pix


def _fits(width: int, height: int, duration: float, orientation: str,
          min_duration: float, max_duration: float) -> bool:
    if not (min_duration <= duration <= max_duration):
        return False
    if orientation == "vertical":
        return height >= 1920 and height > width
    return width >= 1920 and width > height


def search_pixabay(query: str, *, orientation: str = "wide", per_page: int = 30,
                   min_duration: float = 4.0, max_duration: float = 25.0,
                   exclude: tuple[str, ...] = _DEFAULT_EXCLUDE) -> list[dict]:
    """Поиск видео на Pixabay. Возвращает [{provider,id,url,width,height,duration,tags,is4k}]."""
    _pex, key = load_keys()
    if not key:
        return []
    try:
        r = requests.get(_PIXABAY_URL, params={
            "key": key, "q": query, "per_page": max(3, min(200, per_page)),
            "video_type": "film", "safesearch": "true",
            "min_height" if orientation == "vertical" else "min_width":
                "2160" if orientation == "vertical" else "3840",
        }, timeout=30)
        if r.status_code != 200:
            return []
        hits = r.json().get("hits", []) or []
    except Exception:
        return []
    out: list[dict] = []
    for h in hits:
        v = h.get("videos") or {}
        pick = v.get("large") or v.get("medium") or v.get("small") or {}
        w, hgt = int(pick.get("width") or 0), int(pick.get("height") or 0)
        dur = float(h.get("duration") or 0)
        if not pick.get("url") or not _fits(w, hgt, dur, orientation, min_duration, max_duration):
            continue
        tags = str(h.get("tags") or "")
        if _tags_bad(tags, exclude):
            continue
        out.append({
            "provider": "pixabay", "id": h.get("id"), "url": pick["url"],
            "width": w, "height": hgt, "duration": dur,
            "tags": tags, "is4k": (w >= 3840 or hgt >= 2160),
            "tiny": str((v.get("tiny") or {}).get("url") or ""),  # превью ~100КБ для предпроверки
        })
    return out


def search_pexels(query: str, *, orientation: str = "wide", per_page: int = 30,
                  min_duration: float = 4.0, max_duration: float = 25.0) -> list[dict]:
    """Поиск видео на Pexels (best-effort — при нерабочем ключе вернёт [])."""
    key, _pix = load_keys()
    if not key:
        return []
    try:
        r = requests.get(_PEXELS_URL, headers={"Authorization": key}, params={
            "query": query,
            "orientation": "portrait" if orientation == "vertical" else "landscape",
            "per_page": max(3, min(80, per_page)),
        }, timeout=30)
        if r.status_code != 200:
            return []
        videos = r.json().get("videos", []) or []
    except Exception:
        return []
    out: list[dict] = []
    for v in videos:
        best = None
        for f in sorted(v.get("video_files", []), key=lambda x: -(x.get("width") or 0)):
            if (f.get("file_type") or "").startswith("video/mp4") and f.get("link"):
                best = f
                break
        if not best:
            continue
        w, hgt = int(best.get("width") or 0), int(best.get("height") or 0)
        dur = float(v.get("duration") or 0)
        if not _fits(w, hgt, dur, orientation, min_duration, max_duration):
            continue
        out.append({
            "provider": "pexels", "id": v.get("id"), "url": best["link"],
            "width": w, "height": hgt, "duration": dur,
            "tags": str(v.get("url") or ""),
        })
    return out


def search_stock(query: str, *, orientation: str = "wide", per_page: int = 30) -> list[dict]:
    """Поиск: Pexels (если живой) + Pixabay; порядок — сначала Pixabay (он рабочий)."""
    out = search_pixabay(query, orientation=orientation, per_page=per_page)
    if len(out) < per_page // 2:
        out += search_pexels(query, orientation=orientation, per_page=per_page)
    return out


# ======================================================================
# [VM-CASCADE] Сток веером: Pixabay + Pexels по вариантам запроса, RRF-слияние,
# дедуп по (provider,id)/URL. Бюджет VLM фиксирован, поэтому в пул попадают
# лучшие из ШИРОКОЙ выдачи, а не первые попавшиеся.
# ======================================================================
def search_stock_multi(queries: list[str], *, orientation: str = "wide",
                       per_query: int = 20, limit: int = 30,
                       exclude_urls: set[str] | None = None,
                       sources: tuple[str, ...] = ("pixabay", "pexels"),
                       fanout: bool = True, max_variants: int = 2, max_queries: int = 2,
                       rrf_k: int = 60, log=None) -> list[dict]:
    """Веер запросов × источников → RRF → дедуп → limit лучших кандидатов стока.

    [VM-CASCADE] Внутри списка сохраняем ПОРЯДОК PIXABAY API (его релевантность
    точнее нашего тег-хита: клип 40797 «силуэт у окна» стоит 1-м у API, а наш
    пересорт по тегам топил его на 7-е место). Наши эвристики — только бонус
    к исходным запросам (как в легаси-топ-2), а не замена порядка.
    """
    from .library import query_variants
    qlist: list[tuple[str, bool]] = []  # (запрос, исходный ли)
    for q in (queries or [])[: max(1, int(max_queries))]:
        q = str(q or "").strip()
        if not q:
            continue
        qlist.append((q, True))
        for v in (query_variants(q, max_variants=max_variants) if fanout else [q]):
            if v != q:
                qlist.append((v, False))
    if not qlist:
        return []
    excl = {str(u) for u in (exclude_urls or set())}
    lists: list[list[str]] = []
    orig_lists: list[list[str]] = []
    by_key: dict[str, dict] = {}
    for q, is_orig in qlist:
        for src in sources:
            try:
                cands = (search_pixabay(q, orientation=orientation, per_page=per_query)
                         if src == "pixabay"
                         else search_pexels(q, orientation=orientation, per_page=per_query))
            except Exception:
                cands = []
            cands = [c for c in cands if c.get("url") and str(c["url"]) not in excl]
            if src == "pixabay":
                cands = [c for c in cands if c.get("tiny")]  # без tiny нельзя оценить дёшево
            lst: list[str] = []
            for c in cands:  # ПОРЯДОК API сохраняется
                key = f"{c.get('provider')}:{c.get('id')}"
                c = dict(c)
                c["found_by"] = q
                by_key.setdefault(key, c)
                lst.append(key)
            if lst:
                lists.append(lst)
                if is_orig:
                    orig_lists.append(lst)
    if not lists:
        return []
    rrf: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for lst in lists:
        for rank, key in enumerate(lst, 1):
            rrf[key] = rrf.get(key, 0.0) + 1.0 / (rrf_k + rank)
            if key not in best_rank or rank < best_rank[key]:
                best_rank[key] = rank
    orig_rank: dict[str, int] = {}
    for lst in orig_lists:
        for rank, key in enumerate(lst, 1):
            if key not in orig_rank or rank < orig_rank[key]:
                orig_rank[key] = rank
    final: dict[str, float] = {}
    for key, s in rrf.items():
        s = s + 1.5 / (rrf_k + best_rank[key])
        if key in orig_rank:  # топ исходного запроса — приоритет (легаси-поведение)
            s += 1.0 / (rrf_k + orig_rank[key])
        final[key] = s
    ranked = sorted(final.items(), key=lambda kv: -kv[1])
    out: list[dict] = []
    seen_urls: set[str] = set()
    seen_tags: list[set[str]] = []
    for key, score in ranked:
        c = by_key[key]
        u = str(c.get("url") or "")
        if u in seen_urls:
            continue
        # [VM-CASCADE] дедуп близнецов по тегам (Pixabay отдаёт серии одного шута)
        _tg = {t.strip() for t in re.split(r"[,\s]+", str(c.get("tags") or "").lower()) if len(t) > 2}
        if _tg and seen_tags:
            if any(len(_tg & _st) / max(1, len(_tg | _st)) >= 0.85 for _st in seen_tags):
                continue
        seen_urls.add(u)
        if _tg:
            seen_tags.append(_tg)
        c["rrf"] = round(float(score), 6)
        out.append(c)
    if log:
        try:
            log(f"[STOCK] веер {len(qs)} запросов × {len(sources)} источника → пул {len(by_key)} → "
                f"top {len(out[:limit])}")
        except Exception:
            pass
    return out[: int(limit)]


def safe_slug(text: str, max_words: int = 4) -> str:
    """Слаг из английской фразы: только слова, максимум N слов."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(w) > 1]
    return "_".join(words[:max_words]) or "broll"


def download_file(url: str, dest: Path, *, timeout: int = 120) -> bool:
    """Скачать файл потоком. True при успехе."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=timeout) as r:
            if r.status_code != 200:
                return False
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)
        return dest.exists() and dest.stat().st_size > 100_000
    except Exception:
        return False
