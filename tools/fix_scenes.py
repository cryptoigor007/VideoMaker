#!/usr/bin/env python3
"""Авто-починка слабых сцен: <7/10 → докачка партиями по 3 → Vision (1 вызов на партию) → замена.

Ускорения: кэш оценок (verify_pool — не скорит повторно), батчи по 3, 3 параллельных потока.
Сцены НЕ пропускаются: нет ≥7 — берём лучшего доступного.

    python3 tools/fix_scenes.py "/Volumes/SSD/темп/серия10/smart_broll_manifest.json"
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_maker.semantic.smart_broll import _load_gemini_keys  # noqa: E402
from video_maker.semantic.stock import download_file, safe_slug, search_stock, tag_relevance  # noqa: E402
from video_maker.semantic.verify import verify_pool  # noqa: E402


def _gen_queries_gemini(text: str) -> list[str]:
    """Запросы из текста сцены (когда словарь тем не помог)."""
    import json as _j
    import re as _re
    import urllib.request as _u
    key, _ks = _load_gemini_keys()
    if not key:
        return []
    body = _j.dumps({"contents": [{"parts": [{"text":
            "Дай 2 коротких английских поисковых запроса для стока под этот текст сцены. "
            "Ответь ТОЛЬКО JSON-массивом строк: [\"query1\", \"query2\"]. Текст: " + text[:200]}]}],
        "generationConfig": {"temperature": 0.2}}).encode()
    req = _u.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key=" + key,
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _u.urlopen(req, timeout=30) as r:
            d = _j.loads(r.read())
        t = d["candidates"][0]["content"]["parts"][0]["text"]
        m = _re.search(r"\[.*\]", t, _re.DOTALL)
        if m:
            return [str(x) for x in _j.loads(m.group(0)) if str(x).strip()][:2]
    except Exception:
        pass
    return []


def _score(files: list[str], text: str, key: str, extra: str = "", keys: list | None = None) -> int:
    _q = (text + (" | " + extra if extra else ""))[:400]
    entries = [{"file": f, "query": _q} for f in files]
    verify_pool(entries, key=key, keys=keys or [], min_score=0, delete_bad=False, log=lambda *a: None)
    for e in entries:  # безответные — ретрай один раз
        if isinstance(e.get("score"), int) and e.get("score") < 0:
            e.pop("verified", None)
    if any(e.pop("__x", None) is None and not e.get("verified") for e in entries):
        verify_pool(entries, key=key, keys=keys or [], min_score=0, delete_bad=False, log=lambda *a: None)
    return max((e.get("score") if isinstance(e.get("score"), int) else -1) for e in entries) if entries else -1


def fix_scene(sc: dict, scenes_all: list[dict], args, key: str, out_dir: Path,
              keys: list | None = None) -> tuple[dict, str | None, int, str]:
    text = str(sc.get("text") or "")[:300]
    if not text.strip():  # окно без озвучки — текст взаймы у ближайшей сцены
        i0 = scenes_all.index(sc)
        for j in sorted(range(len(scenes_all)), key=lambda x: abs(x - i0)):
            t = str(scenes_all[j].get("text") or "").strip()
            if t:
                text = t[:300]
                break
    files = [f for f in (sc.get("files") or []) if Path(f).exists()]
    _vis = str(sc.get("visual") or "").strip()
    _qs0 = " ".join(str(q) for q in (sc.get("queries") or []) if str(q).strip())
    best = _score(files, _vis or text, key, _qs0, keys=keys) if files else -1
    if best >= args.min_score:
        return sc, None, best, "уже ок"

    queries = [str(q) for q in (sc.get("queries") or []) if str(q).strip()][:2]
    if not queries:
        from video_maker.semantic.matcher import queries_for_text
        queries = queries_for_text(text, max_queries=2)
    if not queries:
        i0 = scenes_all.index(sc)
        for j in sorted(range(len(scenes_all)), key=lambda x: abs(x - i0)):
            qq = [str(q) for q in (scenes_all[j].get("queries") or []) if str(q).strip()]
            if qq:
                queries = qq[:2]
                break
    if not queries:
        queries = _gen_queries_gemini(text)
    if not queries:
        return sc, None, best, "нет запросов (даже Gemini)"
    _extra = " ".join(x for x in (str(sc.get("shot") or ""), str(sc.get("mood") or "")) if x)
    if _extra:
        queries = [(q + " " + _extra).strip() for q in queries][:2]

    folder = out_dir / f"{sc['idx'] + 1:02d}_fix"
    folder.mkdir(parents=True, exist_ok=True)
    got, got_s, best_c, best_s, tried = None, -1, None, -1, 0
    pending: list[Path] = []

    def flush() -> None:
        nonlocal got, got_s, best_c, best_s
        if not pending:
            return
        _qe = " ".join(queries)
        _base = str(sc.get("visual") or "").strip() or text
        entries = [{"file": str(p), "query": (_base + (" | " + _qe if _qe else ""))[:400]} for p in pending]
        verify_pool(entries, key=key, keys=keys or [], min_score=0, delete_bad=False, log=lambda *a: None)
        for e, p in zip(entries, list(pending)):
            s = e.get("score") if isinstance(e.get("score"), int) else -1
            if s >= args.min_score and got is None:
                got, got_s = str(p), s
            elif s > best_s:
                if best_c:
                    try:
                        Path(best_c).unlink()
                    except Exception:
                        pass
                best_c, best_s = str(p), s
            else:
                try:
                    p.unlink()
                except Exception:
                    pass
        pending.clear()

    for q in queries:
        if got or tried >= args.tries:
            break
        cands = search_stock(q, orientation="wide")
        cands.sort(key=lambda c: (-tag_relevance(c.get("tags", ""), q),
                                  not c.get("is4k", False), -min(c["duration"], 12.0)))
        for c in cands:
            if got or tried >= args.tries:
                break
            if not c.get("is4k"):
                continue
            # [PRE] Vision смотрит ПРЕВЬЮ (~100КБ) до скачивания 4K — качаем только подходящее
            _tiny = str(c.get("tiny") or "")
            if _tiny:
                _tf = folder / f"tiny_{tried + 1}.mp4"
                if download_file(_tiny, _tf):
                    _ps = _score([str(_tf)], _vis or text, key, " ".join(queries), keys=keys)
                    try:
                        _tf.unlink()
                    except Exception:
                        pass
                    if _ps <= 1:  # превью низкого разрешения: отсеиваем только явный мусор
                        print(f"   (превью {_ps}/10 — пропуск без скачивания 4K)", flush=True)
                        continue
            dest = folder / f"{safe_slug(q)}_{tried + 1}_{c['provider']}.mp4"
            if not download_file(c["url"], dest):
                continue
            tried += 1
            pending.append(dest)
            if len(pending) >= 3:
                flush()
        flush()
    flush()

    if not got and best_c:  # ниже порога — но сцену НЕ пропускаем
        if best_s < best:  # скачанное хуже того, что уже есть в базе — не трогаем сцену
            return sc, None, best, f"оставляю базу {best}/10 (скачанное {best_s}/10 хуже)"
        got, got_s = best_c, best_s
        return sc, got, got_s, f"лучший доступный {best_s}/10"
    if got:
        try:
            from video_maker.semantic.library import ingest_file
            ingest_file(got, query=text[:80], log=lambda *a: None)
        except Exception:
            pass
        return sc, got, got_s, f"заменено {got_s}/10"
    return sc, None, best, f"не нашлось (попыток {tried})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--tries", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    mp = Path(args.manifest)
    man = json.loads(mp.read_text(encoding="utf-8"))
    out_dir = Path(man.get("out_dir") or mp.parent)
    key, _k = _load_gemini_keys()
    _keys = [k for k in (_k or []) if k]
    scenes_all = man.get("scenes") or []
    fixed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for sc, got, s, msg in ex.map(lambda x: fix_scene(x, scenes_all, args, key, out_dir, _keys), scenes_all):
            if got:
                sc.setdefault("files", []).append(got)
                sc["fix_score"] = s
                fixed += 1
            print(f"сцена {sc['idx'] + 1}: {msg}", flush=True)
    mp.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nисправлено сцен: {fixed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
