"""Оркестратор «умного B-roll»: транскрипт → сцены → запросы → скачивание.

Поток:
1. Сегменты: аудио → кэш whisper приложения (или SRT/JSON).
2. Сцены: сегменты группируются в окна ~9с (те же, что у матчера).
3. Запросы: русский текст сцены → EN-запросы (таксономия, без нейросетей).
4. Скачивание: Pixabay (+Pexels) → <out>/<series>/<NN_слаг>/<слаг>_NNN.mp4
   Файлы идут в порядке сцен — матчер приложения подхватит их по смыслу.
5. Манифест: <out>/<series>/smart_broll_manifest.json (сцены, запросы, файлы).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .matcher import queries_for_text, scene_slots, tokenize
from .stock import download_file, safe_slug, search_stock
from .taxonomy import themes_for_tokens


_LIB_PREVIEW_DIR = Path.home() / "video_maker" / "lib_previews"


def _pixabay_id_from_name(path: str) -> str:
    """Pixabay-ID из имени файла клипа базы (для дешёвого tiny-превью вместо 4K с SMB)."""
    name = Path(str(path)).name
    m = re.search(r"_(\d{4,})_pixabay\.mp4$", name, re.I) or re.search(r"pixabay[_-](\d{4,})", name, re.I)
    return m.group(1) if m else ""


def _lib_preview(file: str, log=print) -> str:
    """Локальное tiny-превью клипа базы (CDN Pixabay); '' — не удалось.

    [VM-CASCADE] Оценка кандидатов идёт по ~100КБ превью, а не по 4K с SMB:
    быстрее и не дёргает сетевой том. Кэш: ~/video_maker/lib_previews/.
    """
    try:
        pid = _pixabay_id_from_name(file)
        if not pid:
            return str(file) if Path(file).exists() else ""
        dest = _LIB_PREVIEW_DIR / f"lib_{pid}.mp4"
        if dest.exists() and dest.stat().st_size > 10_000:
            return str(dest)
        from .stock import download_file, load_keys
        _, key = load_keys()
        if not key:
            return str(file)
        import requests
        r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "id": pid}, timeout=20)
        if r.status_code != 200:
            return str(file)
        hits = r.json().get("hits") or []
        tiny = str((((hits[0].get("videos") or {}).get("tiny") or {}).get("url")) or "") if hits else ""
        if tiny and download_file(tiny, dest, timeout=45):
            return str(dest)
    except Exception:
        pass
    try:
        return str(file) if Path(file).exists() else ""
    except Exception:
        return ""


def _pre_rank_candidates(cands: list[dict], sc: dict, *, use_history: bool = True) -> list[dict]:
    """Pre-rank ДО Vision: семантика (порядок поиска) + история клипа + теги.

    Позитивная история (avg>=7, passes) поднимает клип, негативная — опускает.
    Внутри источника порядок поиска нормируется, чтобы база/сток сравнивались
    честно (у них разные шкалы RRF).
    """
    if not cands:
        return cands
    from .stock import tag_relevance
    q = " ".join(str(x) for x in (sc.get("queries") or [])[:2] if str(x).strip())
    hist_map: dict[str, dict] = {}
    if use_history:
        try:
            from . import perf_history
            hist_map = perf_history.get_many(
                [perf_history.path_of(c) for c in cands])
        except Exception:
            hist_map = {}
    by_kind: dict[str, int] = {}
    for c in cands:
        kind = str(c.get("kind") or "")
        i = by_kind.get(kind, 0)
        by_kind[kind] = i + 1
        sem = 1.0 / (1.0 + i * 0.35)
        tags = c.get("tags")
        if isinstance(tags, (list, tuple, set)):
            tags = " ".join(str(x) for x in tags)
        tag_txt = " ".join([str(tags or ""), str(c.get("description") or "")])
        tag_hits = tag_relevance(tag_txt, q) if q else 0
        tag_norm = min(1.0, tag_hits / 3.0)
        hist = 0.0
        if use_history:
            try:
                from . import perf_history
                hist = perf_history.rank_bonus_stats(
                    hist_map.get(perf_history.path_of(c)) or {})
            except Exception:
                hist = 0.0
        c["_pre_score"] = 0.45 * sem + 0.20 * tag_norm + 0.35 * hist
        c["_pre_sem"] = sem
        c["_pre_hist"] = hist
    return sorted(cands, key=lambda c: -float(c.get("_pre_score") or 0.0))


def _pool_entry(c: dict, scene_text: str, hard: set, avoid: set, log) -> dict | None:
    """Один кандидат → entry для Vision (превью tiny вместо 4K)."""
    e: dict = {"file": "", "verify_file": "", "scene": scene_text,
               "scene_key": scene_text, "query": c.get("query") or "",
               "tags": c.get("tags"), "description": c.get("description"),
               "duration": c.get("duration"), "hard": sorted(hard),
               "avoid": sorted(avoid), "_c": c}
    if c.get("kind") == "lib":
        pv = _lib_preview(str(c.get("file") or ""), log)
        if not pv:
            return None
        c["preview"] = pv
        e["file"] = c["file"]
        e["verify_file"] = pv
        return e
    tiny = str(c.get("tiny") or "")
    if not tiny:
        return None
    from .stock import download_file as _dl
    pv = _LIB_PREVIEW_DIR / f"{c.get('provider')}_{c.get('id')}.mp4"
    if not (pv.exists() and pv.stat().st_size > 10_000):
        if not _dl(tiny, pv, timeout=45):
            return None
    c["preview"] = str(pv)
    e["file"] = str(pv)
    e["verify_file"] = str(pv)
    return e


def _scene_pool_candidates(sc: dict, *, orientation: str, per_scene: int, log,
                           used_urls: set[str], library_only: bool = False,
                           vlm_budget: int = 12, use_stock: bool = True,
                           verify: bool = True, early_exit: bool = True,
                           use_history: bool = True, cand_budget: int = 8,
                           cand_chunk: int = 4) -> list[dict]:
    """[VM-CASCADE] Пул кандидатов сцены: веер базы + веер стока (tiny) → VLM → ранжирование.

    Возвращает список кандидатов с оценками (score/what) в порядке убывания качества:
    [{'kind': 'lib'|'stock', 'file'|'url', 'preview', 'score', 'what', ...}].
    """
    from .library import search_library_multi
    from .stock import search_stock_multi

    queries = [str(q) for q in (sc.get("queries") or []) if str(q).strip()][:3]
    if not queries:
        return []
    emo = [str(x) for x in (sc.get("emotions") or []) if str(x).strip()][:6]
    mood = str(sc.get("mood") or "").strip()
    shot = str(sc.get("shot") or "").strip()
    avoid = {str(x).lower() for x in (sc.get("avoid") or sc.get("must_not") or []) if str(x).strip()}
    hard = {str(x).lower() for x in (sc.get("hard") or []) if str(x).strip()}
    _w = sc.get("weights") or {}
    _wt: dict[str, float] = {}
    for _e in emo:
        _wt[str(_e).lower()] = float(_w.get("emotions", 2) or 2)
    if mood:
        _wt[mood.lower()] = float(_w.get("mood", 1) or 1)
    if shot:
        _wt[shot.lower()] = float(_w.get("shot", 1) or 1)
    extra = " ".join(emo + ([mood] if mood else []))

    cands: list[dict] = []
    # 1) база — веер запросов, RRF, штрафы hard/avoid после поиска, дедуп близнецов
    try:
        lib_hits = search_library_multi(queries, orientation=orientation, per_query=20,
                                        limit=max(12, per_scene * 4), exclude=set(),
                                        avoid=avoid, hard=hard, weight_terms=_wt,
                                        extra_terms=extra, log=log)
    except Exception as _e:
        log(f"[SB] веер базы не удался: {str(_e)[:80]}")
        lib_hits = []
    for rec in lib_hits:
        f = str(rec.get("file") or "")
        if not f:
            continue
        cands.append({"kind": "lib", "file": f, "query": queries[0],
                      "description": str(rec.get("description") or "")[:200],
                      "tags": rec.get("tags"), "provider": rec.get("provider"),
                      "source_url": rec.get("source_url"), "duration": rec.get("duration"),
                      "width": rec.get("width"), "height": rec.get("height"),
                      "db_score": rec.get("score")})
    # 2) сток — веер запросов × источников, tiny-превью для оценки
    if not library_only and use_stock:
        try:
            st_hits = search_stock_multi(queries, orientation=orientation, per_query=20,
                                         limit=max(10, per_scene * 3),
                                         exclude_urls=used_urls, log=log)
        except Exception as _e:
            log(f"[SB] веер стока не удался: {str(_e)[:80]}")
            st_hits = []
        for c in st_hits:
            cands.append({"kind": "stock", "provider": c.get("provider"), "id": c.get("id"),
                          "url": c.get("url"), "tiny": c.get("tiny"),
                          "duration": c.get("duration"), "width": c.get("width"),
                          "height": c.get("height"), "tags": str(c.get("tags") or ""),
                          "query": str(c.get("found_by") or queries[0]), "rrf": c.get("rrf"),
                          "is4k": c.get("is4k")})
    if not cands:
        return []

    # 2b) [VM-CASCADE] чередуем базу и сток в пуле: VLM-бюджет не должен уходить
    # целиком в один источник (иначе веер базы съедает все слоты оценки)
    _lib_only = [c for c in cands if c["kind"] == "lib"]
    _st_only = [c for c in cands if c["kind"] == "stock"]
    merged: list[dict] = []
    for _i in range(max(len(_lib_only), len(_st_only))):
        if _i < len(_lib_only):
            merged.append(_lib_only[_i])
        if _i < len(_st_only):
            merged.append(_st_only[_i])
    cands = merged

    # 2c) pre-rank: семантика + история клипа + теги (позитив вверх, негатив вниз)
    if use_history:
        try:
            cands = _pre_rank_candidates(cands, sc, use_history=True)
        except Exception as _pre:
            log(f"[SB] pre-rank пропущен: {str(_pre)[:80]}")

    # 3) превью и VLM-оценка: бюджет 8 кандидатов, ранний выход, добор до 12
    if verify and not sc.get("_no_verify"):
        stats = {"candidates": 0, "checked": 0, "cached": 0, "skipped": 0,
                 "early_exit": False, "best": -1, "calls": 0}
        try:
            from .verify import verify_batch_gemini
            _k, _ks = _load_gemini_keys()
            scene_text = str(sc.get("visual") or "").strip() or str(sc.get("text") or "")
            budget = max(1, min(int(cand_budget or 8), int(vlm_budget or 12)))
            topup = max(budget, int(vlm_budget or 12))
            chunk_n = max(2, min(6, int(cand_chunk or 4)))
            entries: list[dict] = []
            taken = 0

            def _take(target: int) -> None:
                nonlocal taken
                while len(entries) < target and taken < len(cands):
                    c = cands[taken]
                    taken += 1
                    e = _pool_entry(c, scene_text, hard, avoid, log)
                    if e is not None:
                        entries.append(e)

            def _valid_vals() -> list[int]:
                out: list[int] = []
                for e in entries:
                    if e.get("skipped_history") or e.get("prefilter"):
                        continue
                    v = e.get("score")
                    if isinstance(v, int) and v >= 0:
                        out.append(v)
                return sorted(out, reverse=True)

            def _why_exit(vals: list[int]) -> str | None:
                if not vals:
                    return None
                best = vals[0]
                if best >= 8:
                    return "≥8"
                if best >= 6 and len(vals) >= 2 and best - vals[1] >= 1.5:
                    return "≥6, отрыв ≥1.5"
                return None

            _has_vision = bool(_k or (_ks or []))
            if _has_vision:
                _take(min(budget, len(cands)))
            else:
                log(f"[SB] сцена {sc.get('idx', 0) + 1}: Vision без ключей — "
                    f"кандидаты не оцениваются")
            while _has_vision:
                todo = [e for e in entries if not e.get("verified")]
                if todo:
                    chunk = todo[:chunk_n]
                    stats["calls"] += 1
                    verify_batch_gemini(chunk, key=_k, keys=[x for x in (_ks or []) if x],
                                        model="gemini-3.1-flash-lite",
                                        batch_size=max(2, len(chunk)), min_score=6,
                                        delete_bad=False, log=log)
                    for e in chunk:
                        c = e.get("_c") or {}
                        c["score"] = e.get("score")
                        c["what"] = e.get("what")
                        c["verified"] = bool(e.get("verified"))
                        c["cached"] = bool(e.get("cached"))
                        c["skipped_history"] = bool(e.get("skipped_history"))
                        c["prefilter"] = e.get("prefilter")
                    if early_exit:
                        vals = _valid_vals()
                        why = _why_exit(vals)
                        if why:
                            stats["early_exit"] = True
                            checked_now = sum(1 for e in entries
                                              if e.get("verified") and not e.get("cached")
                                              and not e.get("skipped_history")
                                              and not e.get("prefilter"))
                            log(f"[SB] сцена {sc.get('idx', 0) + 1}: ранний выход "
                                f"({vals[0]}/10, проверено {checked_now}/{len(entries)})")
                            break
                    continue
                # все собранные проверены: добираем бюджет, затем добор до 12 при <6
                if len(entries) < min(budget, len(cands)) and taken < len(cands):
                    _take(min(budget, len(cands)))
                    continue
                vals = _valid_vals()
                best_now = vals[0] if vals else -1
                if (len(entries) < min(topup, len(cands)) and best_now < 6
                        and taken < len(cands)):
                    _take(min(topup, len(cands)))
                    continue
                break

            for e in entries:
                c = e.get("_c") or {}
                c["score"] = e.get("score")
                c["what"] = e.get("what")
                c["verified"] = bool(e.get("verified"))
                c["cached"] = bool(e.get("cached"))
                c["skipped_history"] = bool(e.get("skipped_history"))
                c["prefilter"] = e.get("prefilter")
            stats["candidates"] = len(entries)
            stats["checked"] = sum(1 for e in entries
                                   if e.get("verified") and not e.get("cached")
                                   and not e.get("skipped_history") and not e.get("prefilter"))
            stats["cached"] = sum(1 for e in entries if e.get("cached"))
            stats["skipped"] = sum(1 for e in entries if e.get("skipped_history"))
            vals = _valid_vals()
            stats["best"] = vals[0] if vals else -1
            n_ok = sum(1 for e in entries
                       if isinstance(e.get("score"), int) and e["score"] >= 6)
            if _has_vision:
                log(f"[SB] сцена {sc.get('idx', 0) + 1}: кандидатов {stats['candidates']} "
                    f"(проверено {stats['checked']}, кэш {stats['cached']}, "
                    f"пропущено {stats['skipped']})")
                log(f"[SB] сцена {sc.get('idx', 0) + 1}: оценено {stats['checked']} кандидатов, "
                    f"≥6: {n_ok} (пул: база {sum(1 for c in cands[:max(1, taken)] if c['kind'] == 'lib')} + "
                    f"сток {sum(1 for c in cands[:max(1, taken)] if c['kind'] == 'stock')})")
        except Exception as _ve:
            log(f"[SB] VLM-оценка пула пропущена: {str(_ve)[:100]}")
        sc["_pool_stats"] = stats
    # 4) мягкое ранжирование: сначала по баллу VLM, затем по происхождению/стоимости
    def _key(c: dict):
        sc_v = c.get("score")
        sc_v = sc_v if isinstance(sc_v, int) else -1
        return (-sc_v, 0 if c["kind"] == "lib" else 1)
    cands.sort(key=_key)
    return cands


def _load_gemini_keys() -> tuple[str, list[str]]:
    """Ключи Gemini: env или ~/.vm_gemini_keys.env."""
    import os as _os
    key = _os.environ.get("GEMINI_API_KEY", "").strip()
    keys = [k.strip() for k in (_os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    if not (key and keys):
        env_file = Path.home() / ".vm_gemini_keys.env"
        if env_file.exists():
            try:
                for raw in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip().removeprefix("export ").strip()
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    v = v.strip().strip("'\"")
                    if k == "GEMINI_API_KEY" and not key:
                        key = v
                    elif k == "GEMINI_API_KEYS" and not keys:
                        keys = [x.strip() for x in v.split(",") if x.strip()]
            except Exception:
                pass
    return key, keys


def ensure_analysis_queries(audio_path: str | None, *, log=None, force: bool = False) -> list[dict]:
    """Точные запросы сцен: из кэша анализа, иначе — свежий вызов Gemini (кэшируется).

    Свежий вызов делается один раз на серию; пайплайн потом переиспользует
    этот же кэш (без второго запроса).
    """
    if not force:
        q = find_analysis_queries(log=log)
        if q:
            return q
    if not audio_path:
        return []
    try:
        import os as _os
        from video_maker.engines.transcription import (
            DEFAULT_LANGUAGE, MLX_REPO, _transcription_cache_dir, _transcription_cache_key,
        )
        tkey = _transcription_cache_key(audio_path, MLX_REPO, DEFAULT_LANGUAGE)
        tpath = _transcription_cache_dir() / f"{tkey}.json"
        if not tpath.exists():
            if log:
                log("[SB] нет транскрипта для анализа — сначала прогони серию")
            return []
        tr = json.loads(tpath.read_text(encoding="utf-8"))
        st: dict = {}
        spath = Path.home() / ".video_maker_settings.json"
        if spath.exists():
            try:
                st = json.loads(spath.read_text(encoding="utf-8"))
            except Exception:
                st = {}
        key, keys = _load_gemini_keys()
        if not (key or keys):
            if log:
                log("[SB] ключей Gemini нет (env или ~/.vm_gemini_keys.env)")
            return []
        from video_maker.engines.analysis import analyze
        if log:
            log("[SB] свежий анализ Gemini (один раз на серию, дальше из кэша)…")
        a = analyze(
            tr, api_key=key, api_keys=keys or None,
            model_name=str(st.get("model") or "gemini-3.6-flash"),
            audio_path=audio_path, log_fn=log,
        )
        q = a.get("broll_queries") if isinstance(a, dict) else None
        out = [x for x in (q or []) if isinstance(x, dict)]
        if log:
            log(f"[SB] Gemini-описания сцен получены: {len(out)}")
        return out
    except Exception as e:
        if log:
            log(f"[SB] свежий анализ не удался: {e}")
        return []


def find_analysis_queries(*, log=None) -> list[dict]:
    """Запросы клипов из свежего Gemini-анализа (кэш приложения).

    После первого прогона серии анализ (с полем broll_queries) уже в кэше —
    берём его: точные описания сцен без лишних вызовов Gemini.
    """
    import glob as _glob
    try:
        from video_maker.engines.analysis import ANALYSIS_CACHE_DIR
        files = sorted(_glob.glob(str(ANALYSIS_CACHE_DIR / "*.json")), key=lambda f: -__import__("os").path.getmtime(f))
        for f in files[:40]:
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
            except Exception:
                continue
            q = d.get("broll_queries") if isinstance(d, dict) else None
            if isinstance(q, list) and q:
                if log:
                    log(f"[SB] Gemini-запросы найдены в кэше анализа: {len(q)} сцен")
                return [x for x in q if isinstance(x, dict)]
    except Exception:
        pass
    return []


def _match_analysis_item(text: str, items: list[dict]) -> dict | None:
    """Лучший элемент анализа под текст слота (по пересечению слов)."""
    from .matcher import tokenize
    tt = tokenize(text)
    if not tt:
        return None
    best, best_sc = None, 0
    for it in items:
        it_t = tokenize(str(it.get("text") or "") + " " + str(it.get("visual") or ""))
        sc = len(tt & it_t)
        if sc > best_sc:
            best, best_sc = it, sc
    return best if best_sc > 0 else None


def _series_name(audio_path: str | None, srt_path: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    for p in (audio_path, srt_path):
        if p:
            return Path(str(p)).stem
    return "smart_broll"


def load_segments(*, audio_path: str | None = None, srt_path: str | None = None,
                  segments_json: str | None = None) -> list[dict]:
    """Сегменты транскрипта из кэша приложения / SRT / JSON."""
    if segments_json:
        data = json.loads(Path(segments_json).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("segments") or []
        return list(data)
    if audio_path:
        try:
            from video_maker.engines.transcription import (
                DEFAULT_LANGUAGE, MLX_REPO, _transcription_cache_dir, _transcription_cache_key,
            )
            for lang in (DEFAULT_LANGUAGE, "ru", "auto", ""):
                key = _transcription_cache_key(audio_path, MLX_REPO, lang)
                p = _transcription_cache_dir() / f"{key}.json"
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    segs = data.get("segments") if isinstance(data, dict) else data
                    if segs:
                        return list(segs)
        except Exception:
            pass
    if srt_path:
        return _parse_srt(Path(srt_path))
    raise FileNotFoundError("Нет транскрипта: укажи --audio (кэш whisper), --srt или --segments")


def _parse_srt(path: Path) -> list[dict]:
    def _t(s: str) -> float:
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", s.strip())
        if not m:
            return 0.0
        h, mi, se, ms = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + se + ms / 1000.0

    segs: list[dict] = []
    block: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines() + [""]:
        line = raw.strip()
        if not line:
            if block:
                times = [b for b in block if "-->" in b]
                text = " ".join(b for b in block if "-->" not in b and not b.isdigit())
                if times and text:
                    a, _, b = times[0].partition("-->")
                    segs.append({"start": _t(a), "end": _t(b), "text": text.strip()})
                block = []
            continue
        block.append(line)
    return segs


def _sentence_slots(segments: list[dict], max_len: float = 6.5) -> list[dict]:
    """Сцены по ПРЕДЛОЖЕНИЯМ (один смысл = один визуал, как у топовых программ)."""
    slots: list[dict] = []
    cur_text, cur_start, cur_end = "", None, None
    for s in segments:
        t = str(s.get("text") or "").strip()
        if not t:
            continue
        st = float(s.get("start", 0.0))
        en = float(s.get("end", st))
        if cur_start is None:
            cur_start = st
        cur_text = (cur_text + " " + t).strip()
        cur_end = en
        if re.search(r"[.!?…]$", t) or (cur_end - cur_start) >= max_len:
            slots.append({"idx": len(slots), "start": cur_start, "end": cur_end, "text": cur_text})
            cur_text, cur_start = "", None
    if cur_text:
        slots.append({"idx": len(slots), "start": cur_start or 0.0, "end": cur_end or 0.0, "text": cur_text})
    return slots


def plan_scenes(segments: list[dict], target_duration: float | None = None,
                slot_seconds: float = 9.0, max_scenes: int | None = None,
                query_variant: int = 0, analysis_queries: list[dict] | None = None) -> list[dict]:
    """Сцены с запросами: [{idx,start,end,text,queries,themes}].

    analysis_queries (Gemini) в приоритете: точные описания сцен → точные клипы.
    """
    total = float(target_duration) if target_duration else (
        float(segments[-1].get("end", 0.0)) if segments else 0.0
    )
    slots = _sentence_slots(segments) if segments else scene_slots(segments, total, slot_seconds)
    out: list[dict] = []
    for slot in slots:
        text = str(slot.get("text") or "").strip()
        q: list[str] = []
        emo: list[str] = []
        avoid: list[str] = []
        hard: list[str] = []
        weights: dict = {}
        visual = ""
        shot = mood = ""
        extra_scenes: list[dict] = []
        if analysis_queries and text:
            item = _match_analysis_item(text, analysis_queries)
            if item is not None:
                q = [str(x) for x in (item.get("queries") or []) if str(x).strip()][:3]
                emo = [str(x) for x in (item.get("emotions") or []) if str(x).strip()][:6]
                avoid = [str(x) for x in (item.get("must_not") or []) if str(x).strip()][:6]
                shot = str(item.get("shot") or "").strip()
                mood = str(item.get("mood") or "").strip()
                weights = item.get("weights") if isinstance(item.get("weights"), dict) else {}
                hard = [str(x) for x in (item.get("hard") or []) if str(x).strip()][:6]
                visual = str(item.get("visual") or "").strip()
            # [VM-FIX] Перечисления: анализ разбил пункты на ОТДЕЛЬНЫЕ сцены
            # («очередь», «лифт», «завтрак») — добавляем пул-сцену на КАЖДЫЙ
            # пункт, чтобы в пуле была своя картинка на каждый пункт.
            try:
                _tt = tokenize(text)
                _extras = 0
                for _it in analysis_queries or []:
                    if _extras >= 6:
                        break
                    if _it is item or not isinstance(_it, dict):
                        continue
                    _it_t = tokenize(str(_it.get("text") or ""))
                    if not _it_t or len(_it_t) > 3 or len(_it_t) >= len(_tt):
                        continue
                    if not _it_t <= _tt:  # пункт перечисления — фрагмент фразы
                        continue
                    extra_scenes.append({
                        **slot,
                        "queries": [str(x) for x in (_it.get("queries") or []) if str(x).strip()][:3],
                        "emotions": [str(x) for x in (_it.get("emotions") or []) if str(x).strip()][:6],
                        "avoid": [str(x) for x in (_it.get("must_not") or []) if str(x).strip()][:6],
                        "hard": [str(x) for x in (_it.get("hard") or []) if str(x).strip()][:6],
                        "shot": str(_it.get("shot") or "").strip(),
                        "mood": str(_it.get("mood") or "").strip(),
                        "weights": _it.get("weights") if isinstance(_it.get("weights"), dict) else {},
                        "visual": str(_it.get("visual") or "").strip(),
                        "themes": themes_for_tokens(_it_t),
                    })
                    _extras += 1
            except Exception:
                pass
        if not q:
            # [VM-SB] разные сцены с одной темой берут РАЗНЫЕ фразы — пул разнообразнее
            _pi = (query_variant + int(slot.get("idx", 0))) % 3
            q = queries_for_text(text, max_queries=2, phrase_index=_pi) if text else []
        out.append({**slot, "queries": q, "emotions": emo, "avoid": avoid,
                    "hard": hard, "shot": shot, "mood": mood, "weights": weights,
                    "visual": visual,
                    "themes": themes_for_tokens(tokenize(text))})
        out.extend(extra_scenes)
    for _i, _sc in enumerate(out):  # уникальные idx после добавления пунктов перечислений
        _sc["idx"] = _i
    if max_scenes:
        out = out[: int(max_scenes)]
    return out


def _valid_mp4(p: Path) -> bool:
    try:
        if not p.is_file() or p.name.startswith("._"):
            return False
        if p.stat().st_size <= 20_000:
            return False
    except Exception:
        return False
    try:  # валидный видеопоток (частично скачанный .mp4 не принимаем)
        from .verify import _probe_video
        ok, _dur = _probe_video(str(p))
        return bool(ok)
    except Exception:
        return True


def _scenes_signature(scenes: list[dict]) -> str:
    payload = [{"i": int(sc.get("idx", 0)), "t": str(sc.get("text") or "")[:200],
                "v": str(sc.get("visual") or "")[:200],
                "q": [str(x) for x in (sc.get("queries") or [])[:3]]} for sc in scenes]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        .encode("utf-8")).hexdigest()[:16]


def _source_signature(path: str | None) -> tuple[str, str]:
    """(source, audio_hash): быстрый отпечаток файла (путь+размер+mtime)."""
    src = str(path or "")
    if not src:
        return "", ""
    try:
        st = os.stat(src)
        sig = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()[:16]
    except Exception:
        sig = hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]
    return src, sig


def _settings_signature(settings: dict) -> str:
    keys = ("per_scene", "orientation", "slot_seconds", "max_scenes",
            "library_only", "verify", "min_score")
    payload = {k: settings.get(k) for k in keys}
    payload["v"] = 2
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()[:16]


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _parse_scene_log(log_path: str | Path) -> dict[tuple[int, str], tuple[int, bool]]:
    """(scene 1-based, basename) → (score, is_star) из лога умного пула."""
    out: dict[tuple[int, str], tuple[int, bool]] = {}
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return out
    for line in lines:
        if "[SB]" not in line or "сцена" not in line:
            continue
        m = re.search(r"\[SB\][^\n]*?сцена\s+(\d+):\s*(\S+?\.mp4)\b", line)
        if not m:
            continue
        try:
            idx = int(m.group(1)) - 1
        except Exception:
            continue
        name = os.path.basename(m.group(2))
        star = ("★" in line) or ("лучший" in line)
        sm = re.search(r"\((\d{1,2})/10\b", line)
        if sm is None:
            sm = re.search(r"[—\-]\s*(\d{1,2})/10\b", line)
        if sm is None:
            continue
        try:
            score = int(sm.group(1))
        except Exception:
            continue
        key = (idx, name)
        out[key] = (score, star)
    return out


def adopt_progress(series_dir: str | Path, log_path: str | Path, *,
                   scenes: list[dict] | None = None, audio_path: str | None = None,
                   settings: dict | None = None, write: bool = False,
                   log=print) -> dict:
    """Принять УЖЕ собранный пул в новый _progress.json (без пересборки).

    Сканирует `NN_*/` с валидными mp4, оценки берёт из лога приложения.
    Возвращает {'progress', 'scenes_found', 'scenes_total', 'path', 'written'}.
    """
    series_path = Path(series_dir)
    scores = _parse_scene_log(log_path)
    found: dict[str, dict] = {}
    try:
        dirs = sorted([p for p in series_path.iterdir() if p.is_dir()])
    except Exception:
        dirs = []
    for d in dirs:
        m = re.match(r"^(\d{2,})_", d.name)
        if not m:
            continue
        try:
            idx = int(m.group(1)) - 1
        except Exception:
            continue
        files = sorted([str(p) for p in d.iterdir() if _valid_mp4(p)])
        if not files:
            continue
        downs: list[dict] = []
        for f in files:
            p = Path(f)
            try:
                is_link = p.is_symlink()
            except Exception:
                is_link = False
            sc = scores.get((idx, p.name))
            score = sc[0] if sc else None
            rec = {"scene": idx, "query": d.name.split("_", 1)[1] if "_" in d.name else "",
                   "file": f, "score": score, "verified": True}
            if is_link:
                try:
                    rec["library_file"] = str(p.resolve())
                except Exception:
                    rec["library_file"] = f
                rec["from_library"] = True
            downs.append(rec)
        best = max((r["score"] for r in downs if isinstance(r.get("score"), int)), default=None)
        found[str(idx)] = {
            "status": "done", "file": files[0], "files": files, "score": best,
            "checked": 0, "skipped_checks": 0, "downloaded": downs,
            "ts": datetime.now(timezone.utc).isoformat(), "adopted": True,
        }
    source, audio_hash = _source_signature(audio_path)
    cfg = dict(settings or {})
    total = int(len(scenes)) if scenes is not None else (
        max([int(k) + 1 for k in found] or [0]))
    prog = {
        "version": 1, "source": source, "audio_hash": audio_hash,
        "settings_hash": _settings_signature(cfg),
        "scenes_total": total,
        "scenes_sig": _scenes_signature(scenes) if scenes is not None else "",
        "scenes": found,
        "adopted_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = series_path / "_progress.json"
    if write and found:
        _atomic_write_json(path, prog)
    if log:
        log(f"[ADOPT] сцен распознано {len(found)}/{total} | файлов "
            f"{sum(len(v.get('files') or []) for v in found.values())} | "
            f"{'записан ' + str(path) if write and found else 'подготовлен (write=False)'}")
    return {"progress": prog, "scenes_found": len(found), "scenes_total": total,
            "path": str(path), "written": bool(write and found)}


def _progress_valid(prog: dict, *, source: str, audio_hash: str, settings_hash: str,
                    scenes_total: int, scenes_sig: str) -> tuple[bool, str]:
    if int(prog.get("version") or 0) != 1:
        return False, "версия прогресса не та"
    if str(prog.get("source") or "") != str(source or ""):
        return False, "другой source (аудио/субтитры)"
    if str(prog.get("audio_hash") or "") != str(audio_hash or ""):
        return False, "аудио изменилось (audio_hash)"
    if str(prog.get("settings_hash") or "") != str(settings_hash or ""):
        return False, "другие настройки (per_scene/порог/ориентация)"
    if int(prog.get("scenes_total") or 0) != int(scenes_total):
        return False, "другое число сцен"
    if str(prog.get("scenes_sig") or "") != str(scenes_sig or ""):
        return False, "изменился план сцен (текст/запросы)"
    for idx, rec in (prog.get("scenes") or {}).items():
        files = rec.get("files") or ([rec.get("file")] if rec.get("file") else [])
        for f in files:
            if f and not Path(f).exists():
                return False, f"нет файла сцены {int(idx) + 1}"
    return True, ""


def run_smart_broll(*, audio_path: str | None = None, srt_path: str | None = None,
                    segments_json: str | None = None, out_root: str = "/Volumes/video/broll_downloads/smart",
                    series: str | None = None, per_scene: int = 2, orientation: str = "wide",
                    slot_seconds: float = 9.0, max_scenes: int | None = None,
                    query_variant: int = 0, use_analysis: bool = True,
                    verify: bool = True, library_first: bool = True, library_only: bool = False,
                    dry_run: bool = False, log=print) -> dict:
    """Полный цикл: транскрипт → сцены → скачивание. Возвращает сводку."""
    segments = load_segments(audio_path=audio_path, srt_path=srt_path, segments_json=segments_json)
    series_name = _series_name(audio_path, srt_path, series)
    analysis_queries = ensure_analysis_queries(audio_path, log=log) if use_analysis else []
    scenes = plan_scenes(segments, slot_seconds=slot_seconds, max_scenes=max_scenes,
                         query_variant=query_variant, analysis_queries=analysis_queries)
    out_dir = Path(out_root) / series_name
    source_path = audio_path or srt_path or segments_json
    _source, _audio_hash = _source_signature(source_path)
    _settings = {"per_scene": per_scene, "orientation": orientation,
                 "slot_seconds": slot_seconds, "max_scenes": max_scenes,
                 "library_only": library_only, "verify": verify, "min_score": 6}
    _settings_hash = _settings_signature(_settings)
    _scenes_sig = _scenes_signature(scenes)
    progress_path = out_dir / "_progress.json"
    log(f"[SB] серия «{series_name}» | сегментов: {len(segments)} | сцен: {len(scenes)}")

    # [RESUME] Прогресс: свой _progress.json, либо АДОПТ уже собранного пула
    # (папки NN_* + оценки из лога). Валидный прогресс = не чистим папку.
    _prog: dict | None = None
    _resume = False
    _completed: set[int] = set()
    if not dry_run and out_dir.exists():
        _loaded: dict | None = None
        _reason = "нет _progress.json"
        if progress_path.exists():
            try:
                _cand = json.loads(progress_path.read_text(encoding="utf-8"))
                _ok, _why = _progress_valid(
                    _cand, source=_source, audio_hash=_audio_hash,
                    settings_hash=_settings_hash, scenes_total=len(scenes),
                    scenes_sig=_scenes_sig)
                if _ok:
                    _loaded = _cand
                else:
                    _reason = _why
            except Exception as _pe:
                _reason = f"битый _progress.json: {str(_pe)[:60]}"
        if _loaded is None:
            _log_path = out_dir.parent.parent / "videomaker.log"
            if not _log_path.exists():
                _log_path = out_dir.parent / "videomaker.log"
            try:
                _ad = adopt_progress(out_dir, _log_path, scenes=scenes,
                                     audio_path=source_path, settings=_settings,
                                     write=False, log=log)
                _ap = _ad.get("progress") or {}
                _ok, _why = _progress_valid(
                    _ap, source=_source, audio_hash=_audio_hash,
                    settings_hash=_settings_hash, scenes_total=len(scenes),
                    scenes_sig=_scenes_sig)
                if _ok and _ap.get("scenes"):
                    _loaded = _ap
                    _atomic_write_json(progress_path, _ap)
                    log("[SB] РЕЗЮМ: существующий пул принят (adopt) — сцена заново не собирается")
                elif _ok:
                    _reason = "в пуле нет готовых сцен"
                else:
                    _reason = _why
            except Exception as _ae:
                _reason = f"adopt не удался: {str(_ae)[:60]}"
        if _loaded is not None:
            _done = {int(k) for k, v in (_loaded.get("scenes") or {}).items()
                     if str(v.get("status")) in ("done", "skipped")}
            if _done:
                _prog = _loaded
                _completed = _done
                _resume = True
        if not _resume:
            log(f"[SB] прогресс не принят ({_reason}) — сборка с нуля")
    if not _resume and out_dir.exists() and not dry_run:  # пул прошлых прогонов не копим
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass
    for sc in scenes:
        log(f"[SB] сцена {sc['idx'] + 1}: {sc['start']:.1f}–{sc['end']:.1f}с | "
            f"темы: {','.join(sc['themes'].keys()) or '—'} | запросы: {sc['queries'] or '—'}")

    manifest: dict = {
        "series": series_name, "orientation": orientation, "per_scene": per_scene,
        "audio": audio_path, "out_dir": str(out_dir), "scenes": [], "downloaded": [],
    }
    if dry_run:
        manifest["dry_run"] = True
        for sc in scenes:
            manifest["scenes"].append({**sc, "files": []})
        return manifest

    if _prog is None:
        _prog = {"version": 1, "source": _source, "audio_hash": _audio_hash,
                 "settings_hash": _settings_hash, "scenes_total": len(scenes),
                 "scenes_sig": _scenes_sig, "scenes": {},
                 "updated_at": datetime.now(timezone.utc).isoformat()}
        _atomic_write_json(progress_path, _prog)

    def _write_progress() -> None:
        _prog["scenes_total"] = len(scenes)
        _prog["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(progress_path, _prog)

    used_urls: set[str] = set()
    _lib_used: set[str] = set()  # [LIB] файлы базы, уже взятые в этой серии
    if _resume:
        _done_n = len(_completed)
        _next = next((i for i in range(len(scenes)) if i not in _completed), None)
        log(f"[SB] РЕЗЮМ: сцен готово {_done_n}/{len(scenes)}, "
            f"продолжаю с {(_next + 1) if _next is not None else len(scenes)}")
        for _k in sorted(_completed, key=int):
            _rec = (_prog.get("scenes") or {}).get(str(_k)) or {}
            if _k >= len(scenes):
                continue
            _files = [str(f) for f in (_rec.get("files") or []) if f]
            manifest["scenes"].append({**scenes[_k], "files": _files})
            _downs = _rec.get("downloaded") or []
            manifest["downloaded"].extend(_downs)
            for _d in _downs:
                if _d.get("url"):
                    used_urls.add(str(_d["url"]))
                _lf = _d.get("library_file") or (
                    _d.get("file") if _d.get("from_library") else "")
                if _lf:
                    _lib_used.add(str(_lf))
    # [VM-SB] Реестр скачанных URL (по манифесту серии): не качаем повторы,
    # НО только если файл ещё лежит в пуле (после move_used можно докачать заново).
    _prev_manifest = out_dir / "smart_broll_manifest.json"
    _rejected_urls: set[str] = set()
    if _prev_manifest.exists():
        try:
            _prev = json.loads(_prev_manifest.read_text(encoding="utf-8"))
            for _d in _prev.get("downloaded", []):
                _f = str(_d.get("file") or "")
                _u = str(_d.get("url") or "")
                _sc = _d.get("score")
                if _u and isinstance(_sc, int) and 0 <= _sc < 5:
                    _rejected_urls.add(_u)  # отклонено vision — больше не качаем
                elif _u and _f and Path(_f).exists():
                    used_urls.add(_u)
        except Exception:
            pass
    used_urls |= _rejected_urls

    def _finish(sc: dict, files: list[str]) -> None:
        """Зафиксировать сцену: манифест + атомарный _progress.json."""
        idx = int(sc.get("idx", 0))
        manifest["scenes"].append({**sc, "files": list(files)})
        downs = [d for d in manifest["downloaded"]
                 if int(d.get("scene", -1)) == idx]
        scores = [d.get("score") for d in downs
                  if isinstance(d.get("score"), int) and d["score"] >= 0]
        st = sc.get("_pool_stats") or {}
        _prog["scenes"][str(idx)] = {
            "status": "done" if files else "skipped",
            "file": files[0] if files else "",
            "files": list(files),
            "score": max(scores) if scores else None,
            "checked": int(st.get("checked", 0)),
            "skipped_checks": int(st.get("skipped", 0)),
            "downloaded": downs,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        _write_progress()

    for sc in scenes:
        _idx = int(sc.get("idx", 0))
        if _resume and _idx in _completed:
            continue
        if _resume:  # недоделанная сцена могла оставить полу-папку — чистим её
            for _old in out_dir.glob(f"{_idx + 1:02d}_*"):
                if _old.is_dir():
                    shutil.rmtree(_old, ignore_errors=True)
        sc_files: list[str] = []
        # [VM-CASCADE] Новый путь: веер запросов (база+сток) → tiny-превью → VLM (чередование)
        # → мягкий отбор лучших. Старый путь остаётся фолбэком при любой ошибке.
        try:
            _cands = _scene_pool_candidates(
                sc, orientation=orientation, per_scene=per_scene, log=log,
                used_urls=used_urls, library_only=library_only,
                vlm_budget=int(sc.get("vlm_budget") or 12), use_stock=True, verify=verify)
        except Exception as _pe:
            log(f"[SB] веер-пул сцены не удался — старый путь: {str(_pe)[:90]}")
            _cands = []
        if _cands:
            for _c in _cands:
                if len(sc_files) >= per_scene:
                    break
                _slug = safe_slug(str(_c.get("query") or "broll"))
                folder = out_dir / f"{sc['idx'] + 1:02d}_{_slug}"
                if _c["kind"] == "lib":
                    src = str(_c.get("file") or "")
                    if not src or src in _lib_used:
                        continue
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        dest = folder / Path(src).name
                        if dest.exists() or dest.is_symlink():
                            dest = folder / f"{len(sc_files) + 1:02d}_{Path(src).name}"
                        dest.symlink_to(src)
                    except Exception:
                        continue
                    _lib_used.add(src)
                    sc_files.append(str(dest))
                    manifest["downloaded"].append({
                        "scene": sc["idx"], "query": _c.get("query") or "", "file": str(dest),
                        "library_file": src, "from_library": True,
                        "hard": sorted({str(x) for x in (sc.get("hard") or []) if str(x).strip()}),
                        "provider": _c.get("provider"), "duration": _c.get("duration"),
                        "width": _c.get("width"), "height": _c.get("height"),
                        "tags": _c.get("tags"), "url": _c.get("source_url"),
                        "description": _c.get("description"), "score": _c.get("score"),
                        "what": _c.get("what"), "verified": True, "verifier": "gemini-vlm3",
                    })
                    log(f"[SB] ⬡ сцена {sc['idx'] + 1}: {Path(src).name} — из базы "
                        f"({_c.get('score')}/10): {str(_c.get('description'))[:50]}")
                else:
                    url = str(_c.get("url") or "")
                    if not url or url in used_urls:
                        continue
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        fname = (f"{_slug}_{len(sc_files) + 1:02d}_"
                                 f"{_c.get('provider') or 'stock'}.mp4")
                        dest = folder / fname
                        if not download_file(url, dest):
                            continue
                    except Exception:
                        continue
                    used_urls.add(url)
                    sc_files.append(str(dest))
                    manifest["downloaded"].append({
                        "scene": sc["idx"], "query": _c.get("query") or "", "file": str(dest),
                        "hard": sorted({str(x) for x in (sc.get("hard") or []) if str(x).strip()}),
                        "provider": _c.get("provider"), "duration": _c.get("duration"),
                        "width": _c.get("width"), "height": _c.get("height"),
                        "tags": _c.get("tags"), "url": url, "score": _c.get("score"),
                        "what": _c.get("what"), "verified": True, "verifier": "gemini-vlm3",
                    })
                    log(f"[SB] ↓ сцена {sc['idx'] + 1}: {fname} ({_c.get('score')}/10, "
                        f"{(float(_c.get('duration') or 0)):.0f}с)")
            _finish(sc, sc_files)
            continue
        n_queries = max(1, len(sc["queries"]))
        per_query = max(1, -(-per_scene // n_queries))  # ceil: больше разных клипов
        for qi, query in enumerate(sc["queries"]):
            slug = safe_slug(query)
            folder = out_dir / f"{sc['idx'] + 1:02d}_{slug}"
            # [LIB] сначала — большая база: клип уже здесь, с описанием и лицензией
            if library_first:
                try:
                    from .library import search_library
                    _q_enriched = " ".join([query] + list(sc.get("emotions") or [])
                                           + ([sc.get("mood")] if sc.get("mood") else []))
                    _w = sc.get("weights") or {}
                    _wt: dict[str, float] = {}
                    for _e in (sc.get("emotions") or []):
                        _wt[str(_e).lower()] = float(_w.get("emotions", 2) or 2)
                    if sc.get("mood"):
                        _wt[str(sc["mood"]).lower()] = float(_w.get("mood", 1) or 1)
                    if sc.get("shot"):
                        _wt[str(sc["shot"]).lower()] = float(_w.get("shot", 1) or 1)
                    hits = search_library(_q_enriched, orientation=orientation,
                                          limit=max(2, per_scene * 2), exclude=_lib_used,
                                          avoid=set(sc.get("avoid") or []),
                                          hard=set(sc.get("hard") or []),
                                          weight_terms=_wt)
                except Exception:
                    hits = []
                for rec in hits:
                    if len(sc_files) >= per_scene:
                        break
                    src = str(rec.get("file") or "")
                    if not src or src in _lib_used:
                        continue
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        dest = folder / Path(src).name
                        if dest.exists() or dest.is_symlink():
                            dest = folder / f"{len(sc_files) + 1:02d}_{Path(src).name}"
                        dest.symlink_to(src)
                    except Exception:
                        continue
                    _lib_used.add(src)
                    sc_files.append(str(dest))
                    manifest["downloaded"].append({
                        "scene": sc["idx"], "query": query, "file": str(dest),
                        "library_file": src, "from_library": True,
                        "hard": sorted({str(x) for x in (sc.get("hard") or []) if str(x).strip()}),
                        "provider": rec.get("provider"), "duration": rec.get("duration"),
                        "width": rec.get("width"), "height": rec.get("height"),
                        "tags": rec.get("tags"), "url": rec.get("source_url"),
                        "description": rec.get("description"), "score": rec.get("score"),
                    })
                    log(f"[SB] ⬡ сцена {sc['idx'] + 1}: {Path(src).name} — из базы: "
                        f"{str(rec.get('description'))[:60]}")
            # [LIB] «как в серьёзных программах»: VL оценивает кандидатов ПОД СЦЕНУ (0-10),
            # берём лучших; ниже 7 — предупреждение. Оценки пишутся в манифест.
            if verify and len(sc_files) > 1:
                try:
                    from .verify import verify_pool
                    _k, _ks = _load_gemini_keys()
                    _entries = [d for d in manifest["downloaded"]
                                if d.get("from_library") and d.get("scene") == sc["idx"]]
                    if _k and len(_entries) > 1:
                        _base = str(sc.get("visual") or "").strip() or str(sc.get("text") or query)
                        for _e in _entries:
                            _e["query"] = (_base + " | " + query)[:300]
                        verify_pool(_entries, key=_k, keys=[x for x in (_ks or []) if x], min_score=0, delete_bad=False,
                                    log=log, batch_size=3)
                        _entries.sort(key=lambda d: -(d.get("score") if isinstance(d.get("score"), int) else -1))
                        for _rank, _e in enumerate(_entries):
                            _e["scene_rank"] = _rank + 1
                        _best = _entries[0].get("score") if isinstance(_entries[0].get("score"), int) else -1
                        if _best >= 7:
                            log(f"[SB] ★ сцена {sc['idx'] + 1}: лучший {Path(_entries[0]['file']).name[:38]} — {_best}/10")
                        else:
                            log(f"[SB] ⚠ сцена {sc['idx'] + 1}: лучший {_best}/10 — ниже 7, берём как есть")
                        if len(_entries) > per_scene:
                            for _d in _entries[per_scene:]:
                                try:
                                    Path(_d["file"]).unlink()
                                except Exception:
                                    pass
                                if _d in manifest["downloaded"]:
                                    manifest["downloaded"].remove(_d)
                                if _d["file"] in sc_files:
                                    sc_files.remove(_d["file"])
                except Exception as _se:
                    log(f"[SB] оценка кандидатов пропущена: {_se}")
            if len(sc_files) >= per_scene:
                break
            if library_only:
                continue
            cands = [c for c in search_stock(query, orientation=orientation) if c["url"] not in used_urls]
            # Приоритет: совпадение тегов с запросом → 4K → близость длительности к слоту
            from video_maker.semantic.stock import tag_relevance
            cands.sort(key=lambda c: (
                -tag_relevance(c.get("tags", ""), query),
                not c.get("is4k", False),
                -min(c["duration"], 12.0),
                abs(c["duration"] - 8.0),
            ))
            need = max(1, min(per_query, per_scene - len(sc_files)))
            q_count = 0
            for c in cands:
                if len(sc_files) >= per_scene or q_count >= need:
                    break
                if not c["url"] or c["url"] in used_urls:
                    continue
                fname = f"{slug}_{len(sc_files) + 1:02d}_{c['provider']}.mp4"
                dest = folder / fname
                ok = download_file(c["url"], dest)
                if ok:
                    used_urls.add(c["url"])
                    sc_files.append(str(dest))
                    q_count += 1
                    manifest["downloaded"].append({
                        "scene": sc["idx"], "query": query, "file": str(dest),
                        "hard": sorted({str(x) for x in (sc.get("hard") or []) if str(x).strip()}),
                        "provider": c["provider"], "duration": c["duration"],
                        "width": c["width"], "height": c["height"],
                        "tags": c.get("tags", ""),
                        "url": c.get("url", ""),
                    })
                    log(f"[SB] ↓ сцена {sc['idx'] + 1}: {fname} ({c['duration']:.0f}с, "
                        f"{c['width']}x{c['height']}{' 4K' if c.get('is4k') else ''})")
            if len(sc_files) >= per_scene:
                break
        _finish(sc, sc_files)

    # [VM-VISION] проверка контента: запросы хорошие — а что РЕАЛЬНО в клипах?
    # Клипы из базы уже проверены при приёмке (score в базе) — не тратим запросы.
    _to_verify = [d for d in manifest["downloaded"] if not d.get("from_library")]
    if verify and _to_verify:
        try:
            from .verify import verify_pool
            _key, _keys = _load_gemini_keys()
            if _key:
                verify_pool(_to_verify, key=_key, keys=[x for x in (_keys or []) if x], min_score=5, delete_bad=True, log=log)
                _bad = {
                    str(d.get("file"))
                    for d in manifest["downloaded"]
                    if isinstance(d.get("score"), int) and 0 <= d.get("score", 0) < 5
                }
                if _bad:
                    for _sc in manifest["scenes"]:
                        _sc["files"] = [f for f in (_sc.get("files") or []) if f not in _bad]
        except Exception as _v_e:
            log(f"[SB] vision-проверка пропущена: {_v_e}")

    # [LIB] скачанные и подтверждённые Vision (≥7/10) клипы — в большую базу
    if library_first and manifest["downloaded"]:
        try:
            from .library import ingest_file
            _ing = 0
            for d in manifest["downloaded"]:
                if d.get("from_library") or d.get("ingested"):
                    continue
                _sc = d.get("score")
                if isinstance(_sc, int) and _sc >= 7:
                    if ingest_file(d.get("file"), query=str(d.get("query") or ""), log=log):
                        d["ingested"] = True
                        _ing += 1
            if _ing:
                log(f"[SB] ⊕ в большую базу добавлено (Vision подтвердил): {_ing}")
        except Exception as _ie:
            log(f"[SB] добавление в базу пропущено: {str(_ie)[:80]}")

    # [SB] сводка для лога приложения: найдено/осталось/откуда
    try:
        _sc_all = manifest.get("scenes") or []
        _scored = [d for d in manifest["downloaded"] if isinstance(d.get("score"), int) and d["score"] >= 0]
        _okc = sum(1 for d in _scored if d["score"] >= 6)
        _libn = sum(1 for d in manifest["downloaded"] if d.get("from_library"))
        _dln = len(manifest["downloaded"]) - _libn
        _avg = (sum(d["score"] for d in _scored) / len(_scored)) if _scored else 0
        _need = max(0, len(_sc_all) - _okc)
        log(f"[SB] ИТОГ: сцен {len(_sc_all)} | подходящих (≥6) {_okc} | осталось добрать ~{_need} | "
            f"из базы {_libn} | скачано {_dln} | средний балл {_avg:.1f}/10")
    except Exception:
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    # [RESUME] финальная синхронизация прогресса: сцены/файлы после Vision-чистки
    try:
        manifest["scenes"].sort(key=lambda s: int(s.get("idx", 0)))
        _by_idx = {int(s.get("idx", 0)): s for s in manifest["scenes"]}
        for _k, _rec in (_prog.get("scenes") or {}).items():
            _s = _by_idx.get(int(_k))
            if not _s:
                continue
            _files = [str(f) for f in (_s.get("files") or []) if f]
            _rec["files"] = _files
            _rec["file"] = _files[0] if _files else ""
            _rec["status"] = "done" if _files else "skipped"
            _rec["downloaded"] = [d for d in manifest["downloaded"]
                                  if int(d.get("scene", -1)) == int(_k)]
        _write_progress()
    except Exception:
        pass
    man_path = out_dir / "smart_broll_manifest.json"
    _atomic_write_json(man_path, manifest)
    _lib_n = sum(1 for d in manifest["downloaded"] if d.get("from_library"))
    log(f"[SB] готово: всего {len(manifest['downloaded'])} клипов "
        f"(из базы: {_lib_n}, скачано: {len(manifest['downloaded']) - _lib_n}) | папка: {out_dir}")
    log(f"[SB] манифест: {man_path}")
    return manifest
