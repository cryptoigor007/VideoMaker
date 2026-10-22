#!/usr/bin/env python3
"""Собрать эталонный набор сцен (gold) для eval VideoMaker.

Источники:
  * ``smart_broll_manifest.json`` частей 1-2 (реальные сцены, запросы,
    выбранные клипы и уже полученные VLM-оценки);
  * база клипов ``~/video_maker/broll_library.db`` (кандидаты и описания).

Что делает:
  1. берёт сцены из манифестов, находит выбранный клип (в т.ч. через
     ``library_file``, если рабочая копия клипа уже удалена);
  2. классифицирует сцену эвристикой literal/associative/abstract;
  3. достаёт топ-кандидатов тем же поиском, что и пайплайн
     (``search_library_multi``, без скачивания и без stock-API);
  4. проверяет top-N кандидатов через ``verify.py`` (VLM, батчами, с кэшем
     в копии БД), а выбранный клип берёт готовую VLM-оценку из манифеста;
  5. предлагает метку accept / fallback_ok / must_graphic + confidence;
  6. пишет ``eval/gold.jsonl`` и ``eval/gold_meta.json``.

Примеры:
    .venv/bin/python3 eval/make_gold.py --limit 40 --verify-top 3
    .venv/bin/python3 eval/make_gold.py --dry-run          # только статистика
    .venv/bin/python3 eval/make_gold.py --verify-top 0     # без новых VLM-вызовов

Скрипт НЕ меняет код и настройки пайплайна. VLM-кэш пишется в копию БД
(``eval/.cache/broll_library.db``), production-база не мутируется.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.common import (  # noqa: E402
    CLASSES,
    apply_split,
    clip_id,
    write_json,
    write_jsonl,
)

DEFAULT_MANIFESTS = [
    "/Volumes/SSD/темп/компиляция/вывод_01/broll_smart/часть_01/smart_broll_manifest.json",
    "/Volumes/SSD/темп/компиляция/вывод_02/broll_smart/часть_02/smart_broll_manifest.json",
]
PROD_DB = Path.home() / "video_maker" / "broll_library.db"
EVAL_DIR = Path(__file__).resolve().parent
CACHE_DIR = EVAL_DIR / ".cache"
GOLD_PATH = EVAL_DIR / "gold.jsonl"
META_PATH = EVAL_DIR / "gold_meta.json"

# ---------------------------------------------------------------------------
# Классификация сцены (эвристика, без LLM)
# ---------------------------------------------------------------------------
_ACTION = (
    "идет", "идут", "сидит", "стоит", "смотрит", "держит", "свайпает",
    "наливает", "разговаривает", "печатает", "улыбается", "смеется",
    "обнимает", "лежит", "пьет", "работает", "играет", "курит", "плачет",
    "целует", "открывает", "закрывает", "снимает", "показывает", "крутит",
    "слушает", "проходит", "бежит", "танцует", "готовит", "ест", "спит",
    "просыпается", "одевается", "выходит", "входит", "садится", "встает",
    "берет", "кладет", "пишет", "читает", "листает", "скроллит",
    "нажимает", "глядя", "говорит", "молчит", "ждет", "встречает",
    "провожает", "общается", "смеясь", "уставившись", "уткнувшись",
    "разводит", "жестикулирует", "скрещивает", "бросает", "подпирает",
    "закрываясь", "отдаляясь", "раскладывают", "печатает",
)
_OBJECT = (
    "смартфон", "телефон", "экран", "чашк", "окн", "стол", "диван",
    "двер", "рук", "лиц", "человек", "люд", "пар", "машин", "улиц",
    "комнат", "кухн", "кафе", "офис", "метр", "поезд", "кроват",
    "книг", "ноутбук", "наушник", "кружк", "бутылк", "сигарет",
    "зеркал", "подарок", "фотографи", "рамк", "письм", "уведомлен",
    "сообщен", "профил", "анкет", "меню", "счет", "документ", "график",
)
_META = (
    "силуэт", "размыт", "отражен", "тень", "контраст", "метафор",
    "символ", "абстракт", "лабиринт", "песочн", "час", "календар",
    "дорог", "зеркал", "дым", "туман", "пуст", "толп", "одинок",
    "фон", "город", "окно", "дождь", "ноч", "закат", "свет", "тьм",
    "улиц", "поток", "сумерк", "неон",
)
_MENTAL = (
    "ощущен", "кажется", "внутри", "чувств", "страх", "тревог",
    "надежд", "дума", "мысл", "вспомина", "памят", "понима", "осозн",
    "представ", "воображ", "казалось", "странн", "сложн", "прост",
    "смысл", "выбор", "возможност", "отношен", "близост", "одиночеств",
    "любов", "жизн", "время", "технологи", "интернет", "привычн",
    "стратег", "проблем", "кажд", "большинств", "никогда", "всегда",
)
_GRAPHIC = (
    "абстракт", "метафор", "символ", "график", "схем", "инфографик",
    "титр", "заставк", "анимац", "цифр", "процент", "статистик",
    "исследован", "таблиц", "диаграм",
)
_QUERY_ACTION = (
    "walking", "sitting", "holding", "swiping", "scrolling", "talking",
    "drinking", "looking", "using", "typing", "texting", "standing",
    "running", "eating", "sleeping", "working", "reading", "crying",
)
_PORTRAIT = ("лицо", "взгляд", "смотрит в камеру", "смотрит в объектив",
             "портрет", "глаза", "выражением лица")
# Действия, которые сами по себе не делают кадр «literal»: это портрет/статика.
_WEAK_ACTION = frozenset({
    "смотрит", "глядя", "сидит", "стоит", "молчит", "слушает", "ждет",
    "подпирает", "улыбается", "смеется",
})

# Слова, которые почти ничего не говорят о буквальности клипа.
_GENERIC = frozenset({
    "person", "people", "man", "woman", "young", "shot", "close", "medium",
    "wide", "video", "clip", "lifestyle", "indoor", "outdoor", "background",
    "foreground", "scene", "view", "with", "from", "that", "this", "his",
    "her", "their", "looking", "person's", "candid", "slow", "motion",
})


def classify_scene(scene: dict) -> tuple[str, float, dict]:
    """literal / associative / abstract + confidence + сигналы (для notes)."""
    text = str(scene.get("text") or "")
    visual = str(scene.get("visual") or "")
    queries = [str(q) for q in (scene.get("queries") or []) if str(q).strip()]
    vl = visual.lower()
    tl = text.lower()
    ql = " ".join(queries).lower()

    act = [w for w in _ACTION if w in vl]
    obj = [w for w in _OBJECT if w in vl]
    meta = [w for w in _META if w in vl]
    ment = [w for w in _MENTAL if w in tl]
    graph = [w for w in _GRAPHIC if w in (vl + " " + ql)]
    text_obj = [w for w in _OBJECT if w in tl]
    query_action = [w for w in _QUERY_ACTION if re.search(r"\b" + re.escape(w), ql)]
    portrait = any(w in vl for w in _PORTRAIT) or (
        "смотрит" in vl and ("камер" in vl or "объектив" in vl)
    )
    major_action = [w for w in act if w not in _WEAK_ACTION]

    literal_score = 0.0
    literal_score += 2.0 if major_action else 0.0
    literal_score += 1.0 if obj else 0.0
    literal_score += 1.0 if text_obj else 0.0
    literal_score += 1.0 if (obj and text_obj) else 0.0
    literal_score += 0.8 if query_action else 0.0
    literal_score -= 1.5 * len(meta[:3]) + 1.0 * min(3, len(ment))

    abstract_score = 0.0
    abstract_score += 4.0 if not visual.strip() else 0.0
    abstract_score += 4.0 if not queries else 0.0
    abstract_score += 2.5 if graph else 0.0

    assoc_score = 0.0
    assoc_score += 1.5 * len(meta[:3]) + 0.7 * min(4, len(ment))
    assoc_score += 0.8 if graph else 0.0
    if portrait and not major_action:
        assoc_score += 2.0  # говорящая голова / портрет — не literal-действие

    if not visual.strip() or not queries:
        cls, conf = "abstract", 0.75
    elif graph and not major_action:
        cls, conf = "abstract", 0.7
    elif abstract_score >= 4.5:
        cls, conf = "abstract", 0.65
    elif literal_score >= 2.5 and assoc_score < 2.5:
        cls = "literal"
        conf = min(0.85, 0.55 + 0.06 * len(major_action) + 0.05 * len(obj))
    elif assoc_score >= 2.0:
        cls = "associative"
        conf = min(0.8, 0.5 + 0.05 * len(meta) + 0.03 * len(ment))
    elif literal_score >= 1.5:
        cls, conf = "literal", 0.55
    else:
        cls, conf = "associative", 0.5

    signals = {
        "action": act[:5],
        "major_action": major_action[:5],
        "object": obj[:5],
        "meta": meta[:5],
        "mental": ment[:5],
        "graphic": graph[:5],
        "query_action": query_action[:5],
        "portrait": bool(portrait),
        "literal_score": round(literal_score, 1),
        "assoc_score": round(assoc_score, 1),
        "abstract_score": round(abstract_score, 1),
    }
    return cls, round(float(conf), 2), signals


# ---------------------------------------------------------------------------
# Загрузка манифестов и выбранного клипа
# ---------------------------------------------------------------------------
def load_manifests(paths: list[str]) -> list[dict]:
    out: list[dict] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"[gold] манифест не найден: {p}")
            continue
        try:
            man = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[gold] манифест не прочитан {p}: {str(e)[:100]}")
            continue
        part = re.sub(r"[^0-9a-zA-Zа-яА-Я_]+", "_", str(man.get("series") or path.parent.name))
        orientation = str(man.get("orientation") or "wide")
        downloaded: dict[int, dict] = {}
        for item in man.get("downloaded") or []:
            try:
                downloaded[int(item.get("scene"))] = item
            except (TypeError, ValueError):
                continue
        for sc in man.get("scenes") or []:
            rec = dict(sc)
            rec["_manifest"] = str(path)
            rec["_part"] = part
            rec["_orientation"] = orientation
            rec["_downloaded"] = downloaded.get(int(sc.get("idx", -1)))
            out.append(rec)
    return out


def _db_clip_index(db: Path) -> dict[str, str]:
    """basename → актуальный путь в библиотеке."""
    idx: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        for (f,) in conn.execute("SELECT file FROM clips"):
            if f:
                idx[clip_id(f)] = str(f)
        conn.close()
    except Exception as e:
        print(f"[gold] индекс БД недоступен: {str(e)[:100]}")
    return idx


def resolve_selected(scene: dict, db_index: dict[str, str]) -> tuple[str, dict]:
    """Вернуть (путь к выбранному клипу, запись downloaded) или ('', {}).

    Приоритет — копия в библиотеке (``library_file``/``files``): её путь уже
    есть в индексе БД, поэтому не нужно делать медленный stat по SMB. Стоковые
    клипы, которых нет в библиотеке, в gold не берём — офлайн-recall по ним
    всё равно не измерить.
    """
    dl = scene.get("_downloaded") or {}
    names: list[str] = []
    for p in reversed(scene.get("files") or []):
        if p:
            names.append(clip_id(p))
    for key in ("library_file", "file"):
        p = dl.get(key)
        if p:
            names.append(clip_id(p))
    for name in names:
        path = db_index.get(name)
        if path:
            return path, dl
    return "", dl


def _install_fast_exists(db_index: dict[str, str]) -> None:
    """Кэш Path.exists для файлов библиотеки (SMB-stat стоит секунды).

    Eval не должен ходить в SMB на каждый из 1700 клипов при каждом варианте
    запроса. Индекс БД — источник истины; если файл удалён, это увидит VLM
    (кадры не извлекутся). Патч живёт только внутри процесса eval.
    """
    import pathlib

    lib_paths = set(db_index.values())
    cache: dict[str, bool] = {}
    orig = pathlib.Path.exists

    def _exists(self):  # type: ignore[no-untyped-def]
        s = str(self)
        if s in lib_paths:
            return True
        if "/broll_library/" in s:
            return False
        hit = cache.get(s)
        if hit is not None:
            return hit
        try:
            val = bool(orig(self))
        except Exception:
            val = False
        cache[s] = val
        return val

    pathlib.Path.exists = _exists  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Кандидаты и эвристика literal-совпадения
# ---------------------------------------------------------------------------
def _literal_overlap(scene: dict, cand: dict) -> tuple[int, list[str]]:
    try:
        from video_maker.semantic.matcher import tokenize
    except Exception:
        return 0, []
    query = " ".join(str(q) for q in (scene.get("queries") or []))
    visual = str(scene.get("visual") or "")
    core = {t for t in (tokenize(query) | tokenize(visual)) if t not in _GENERIC}
    hay = tokenize(
        " ".join([str(cand.get("description") or ""), str(cand.get("tags") or ""),
                  Path(str(cand.get("file") or "")).stem])
    )
    inter = sorted(core & hay)
    return len(inter), inter[:8]


def retrieve_candidates(scene: dict, *, limit: int = 10, log=None) -> tuple[list[dict], float]:
    from video_maker.semantic.library import search_library_multi

    queries = [str(q) for q in (scene.get("queries") or []) if str(q).strip()][:3]
    if not queries:
        return [], 0.0
    emo = [str(x) for x in (scene.get("emotions") or []) if str(x).strip()][:6]
    mood = str(scene.get("mood") or "").strip()
    shot = str(scene.get("shot") or "").strip()
    avoid = {str(x).lower() for x in (scene.get("avoid") or []) if str(x).strip()}
    hard = {str(x).lower() for x in (scene.get("hard") or []) if str(x).strip()}
    weights = scene.get("weights") or {}
    wt: dict[str, float] = {}
    for e in emo:
        wt[e.lower()] = float(weights.get("emotions", 2) or 2)
    if mood:
        wt[mood.lower()] = float(weights.get("mood", 1) or 1)
    if shot:
        wt[shot.lower()] = float(weights.get("shot", 1) or 1)
    extra = " ".join(emo + ([mood] if mood else []))

    t0 = time.monotonic()
    try:
        hits = search_library_multi(
            queries,
            orientation=scene.get("_orientation") or "wide",
            per_query=20,
            limit=max(int(limit), 12),
            avoid=avoid,
            hard=hard,
            weight_terms=wt,
            extra_terms=extra,
            log=log,
        )
    except Exception as e:
        print(f"[gold] поиск не удался (scene {scene.get('idx')}): {str(e)[:120]}")
        hits = []
    elapsed = (time.monotonic() - t0) * 1000.0

    out: list[dict] = []
    for rank, rec in enumerate(hits[: int(limit)], 1):
        f = str(rec.get("file") or "")
        if not f:
            continue
        c = {
            "id": clip_id(f),
            "file": f,
            "rank": rank,
            "description": str(rec.get("description") or "")[:400],
            "tags": rec.get("tags"),
            "db_score": rec.get("score"),
            "duration": rec.get("duration"),
        }
        c["literal_match"], c["literal_terms"] = _literal_overlap(scene, c)
        out.append(c)
    return out, elapsed


# ---------------------------------------------------------------------------
# VLM-проверка кандидатов (verify.py, батчами)
# ---------------------------------------------------------------------------
def _eval_db_copy() -> Path:
    """Копия production-БД для чтения и VLM-кэша (sqlite backup, безопасно)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dst = CACHE_DIR / "broll_library.db"
    if not PROD_DB.exists():
        return PROD_DB
    try:
        if dst.exists() and dst.stat().st_mtime >= PROD_DB.stat().st_mtime:
            return dst
        tmp = dst.with_suffix(".tmp.db")
        if tmp.exists():
            tmp.unlink()
        src = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True, timeout=20)
        out = sqlite3.connect(str(tmp))
        src.backup(out)
        src.close()
        out.close()
        tmp.replace(dst)
    except Exception as e:
        print(f"[gold] копия БД не сделана ({str(e)[:100]}), работаю с production")
        return PROD_DB
    return dst


def _patch_db(db: Path) -> None:
    import video_maker.semantic.library as lib

    lib.db_path = lambda root=None: Path(db)  # noqa: ARG005


def verify_candidates(payloads: list[dict], *, top_n: int, model: str,
                      db: Path) -> tuple[int, dict]:
    """Проверить top-N кандидатов каждой сцены. Возвращает (calls, stats)."""
    if top_n <= 0 or not payloads:
        return 0, {"calls": 0, "skipped": "verify-top=0"}
    try:
        from video_maker.semantic.smart_broll import _load_gemini_keys
        from video_maker.semantic.verify import verify_batch_gemini
    except Exception as e:
        return 0, {"calls": 0, "error": str(e)[:200]}

    key, keys = _load_gemini_keys()
    keys = [k for k in (keys or []) if k]
    if not key and not keys:
        return 0, {"calls": 0, "skipped": "нет Gemini-ключей"}

    entries: list[dict] = []
    for p in payloads:
        for c in p["candidates"][: int(top_n)]:
            entries.append({
                "file": c["file"],
                "scene": p["visual"] or p["scene_text"],
                "visual": p["visual"] or p["scene_text"],
                "scene_key": f"{p['scene_id']}::{p['visual'] or p['scene_text']}",
                "scene_idx": p["scene_idx"],
                "query": p["queries"][0] if p["queries"] else "",
                "duration": c.get("duration"),
                "_scene_id": p["scene_id"],
                "_candidate_id": c["id"],
            })
    if not entries:
        return 0, {"calls": 0, "skipped": "нет кандидатов"}

    tmp = CACHE_DIR / "frames"
    stats = {"calls": 0}
    print(f"[gold] VLM: кандидатов {len(entries)} (top-{top_n} × {len(payloads)} сцен), "
          f"батчами по 5…")
    try:
        verify_batch_gemini(
            entries, key=key, keys=keys, model=model, batch_size=5,
            min_score=6, delete_bad=False, log=print,
            use_cache=True, frames_count=2, tmp_dir=tmp, stats=stats,
        )
    except Exception as e:
        print(f"[gold] VLM-проверка прервана: {str(e)[:200]}")
    by_key: dict[tuple[str, str], dict] = {}
    for e in entries:
        by_key[(str(e.get("_scene_id")), str(e.get("_candidate_id")))] = {
            "score": e.get("score"),
            "what": e.get("what"),
            "literal_action": e.get("literal_action"),
            "veto": e.get("veto"),
            "verified": e.get("verified"),
            "cached": e.get("cached"),
            "prefilter": e.get("prefilter"),
        }
    for p in payloads:
        for c in p["candidates"][: int(top_n)]:
            r = by_key.get((p["scene_id"], c["id"]))
            if r:
                c["vlm_score"] = r["score"] if isinstance(r["score"], int) else None
                c["vlm_what"] = str(r["what"] or "")[:200]
                c["literal_action"] = r["literal_action"]
                c["veto"] = r["veto"]
                c["vlm_verified"] = bool(r["verified"]) and not r.get("prefilter")
                c["vlm_cached"] = bool(r["cached"])
    return int(stats.get("calls") or 0), stats


# ---------------------------------------------------------------------------
# Отбор сцен
# ---------------------------------------------------------------------------
def _even_pick(seq: list[dict], n: int) -> list[dict]:
    if n <= 0 or not seq:
        return []
    if n >= len(seq):
        return list(seq)
    step = len(seq) / float(n)
    return [seq[min(len(seq) - 1, int(i * step))] for i in range(n)]


def sample_scenes(scenes: list[dict], limit: int = 40, seed: int = 20261022) -> list[dict]:
    """Стратифицированная выборка: классы + обе части серии.

    Целевые квоты для 40 сцен: 16 literal / 18 associative / 6 abstract;
    каждая часть даёт половину. Если класса не хватает — добираем другими,
    сохраняя детерминированный порядок.
    """
    rng = random.Random(seed)
    by_part: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    seen_text: set[str] = set()
    for sc in scenes:
        text_key = " ".join(str(sc.get("text") or "").lower().split())[:300]
        if text_key and text_key in seen_text:
            continue
        if not sc.get("_selected_path"):
            continue
        if not (sc.get("queries") or []):
            continue
        seen_text.add(text_key)
        by_part[sc["_part"]][sc["class"]].append(sc)

    parts = sorted(by_part)
    if not parts:
        return []
    base = {"literal": 0.40, "associative": 0.45, "abstract": 0.15}
    target = {c: int(round(limit * base[c])) for c in CLASSES}
    while sum(target.values()) < limit:
        target["associative"] += 1
    while sum(target.values()) > limit:
        target["associative"] -= 1

    picked: list[dict] = []
    picked_ids: set[str] = set()

    def take(part: str, cls: str, n: int) -> None:
        pool = [s for s in by_part[part][cls] if s["scene_id"] not in picked_ids]
        for s in _even_pick(pool, n):
            if s["scene_id"] not in picked_ids:
                picked.append(s)
                picked_ids.add(s["scene_id"])

    # делим квоты по частям; остаток — первой части
    for cls in CLASSES:
        q = target[cls]
        per = q // max(1, len(parts))
        for i, part in enumerate(parts):
            take(part, cls, per + (q % len(parts) if i == 0 else 0))
    # добор, если где-то класса не хватило
    if len(picked) < limit:
        for cls in ("associative", "literal", "abstract"):
            for part in parts:
                need = limit - len(picked)
                if need <= 0:
                    break
                take(part, cls, need)
    # финальный добор любыми сценами
    if len(picked) < limit:
        for part in parts:
            for cls in CLASSES:
                need = limit - len(picked)
                if need <= 0:
                    break
                take(part, cls, need)
    rng.shuffle(picked)  # порядок не влияет на split (он по hash), но файл не по частям
    return picked[:limit]


# ---------------------------------------------------------------------------
# Сборка gold
# ---------------------------------------------------------------------------
def build(args: argparse.Namespace) -> tuple[list[dict], dict]:
    manifests = args.manifest or [p for p in DEFAULT_MANIFESTS if Path(p).exists()]
    scenes = load_manifests(manifests)
    if not scenes:
        raise SystemExit("нет сцен: проверь пути к smart_broll_manifest.json")
    print(f"[gold] манифестов: {len(manifests)}, сцен: {len(scenes)}")

    db = Path(args.db)
    if args.no_db_copy:
        db_read = PROD_DB if not args.db else db
    else:
        db_read = _eval_db_copy()
        _patch_db(db_read)
    print(f"[gold] БД: {db_read}")
    db_index = _db_clip_index(db_read if db_read.exists() else PROD_DB)
    _install_fast_exists(db_index)

    prepared: list[dict] = []
    no_clip = 0
    for sc in scenes:
        selected, dl = resolve_selected(sc, db_index)
        if not selected:
            no_clip += 1
            continue
        cls, cls_conf, signals = classify_scene(sc)
        rec = dict(sc)
        rec["scene_id"] = f"{sc['_part']}_{int(sc.get('idx', 0)):03d}"
        rec["_selected_path"] = selected
        rec["class"] = cls
        rec["class_confidence"] = cls_conf
        rec["class_signals"] = signals
        rec["selected_score"] = (
            int(dl.get("score")) if isinstance(dl.get("score"), int) and dl.get("score") >= 0 else None
        )
        rec["selected_what"] = str(dl.get("what") or "")[:300]
        rec["vlm_source"] = "manifest_vlm" if rec["selected_score"] is not None else ""
        prepared.append(rec)

    print(f"[gold] сцен с клипом: {len(prepared)} (без клипа: {no_clip})")
    cls_counts = Counter(r["class"] for r in prepared)
    print(f"[gold] классы доступных: {dict(cls_counts)}")

    picked = sample_scenes(prepared, limit=int(args.limit), seed=int(args.seed))
    print(f"[gold] отобрано: {len(picked)} | классы: {dict(Counter(r['class'] for r in picked))}")
    if args.dry_run:
        for r in picked:
            print(f"  {r['scene_id']} {r['class']:11s} conf={r['class_confidence']:.2f} "
                  f"sel={r['selected_score']} | {r['text'][:70]}")
        meta = {
            "dry_run": True,
            "available_scenes": len(prepared),
            "selected": len(picked),
            "class_counts": dict(Counter(r["class"] for r in picked)),
        }
        return [], meta

    # 1) поиск кандидатов
    payloads: list[dict] = []
    for i, r in enumerate(picked):
        cands, ms = retrieve_candidates(r, limit=int(args.candidates), log=None)
        payloads.append({
            "scene_id": r["scene_id"],
            "scene_idx": 10_000 + i,
            "scene_text": r["text"],
            "visual": str(r.get("visual") or ""),
            "queries": [str(q) for q in (r.get("queries") or [])],
            "candidates": cands,
            "search_ms": round(ms, 2),
        })
        print(f"[gold] {i + 1}/{len(picked)} {r['scene_id']}: кандидатов {len(cands)}, "
              f"поиск {ms / 1000:.1f}с")

    # 2) VLM top-N (если есть ключи и не отключено)
    vlm_calls, vlm_stats = verify_candidates(
        payloads, top_n=int(args.verify_top), model=args.vlm_model, db=db_read)
    print(f"[gold] VLM-вызовов: {vlm_calls} | {vlm_stats}")

    # 3) метки и gold-строки
    rows: list[dict] = []
    by_id = {r["scene_id"]: r for r in picked}
    vlm_entries_by_scene: dict[str, int] = defaultdict(int)
    for p in payloads:
        for c in p["candidates"]:
            if c.get("vlm_verified") and not c.get("vlm_cached"):
                vlm_entries_by_scene[p["scene_id"]] += 1

    # Реальные VLM-вызовы батчей нельзя один-к-одному привязать к сцене.
    # Распределяем их пропорционально числу проверенных кандидатов: сумма
    # по gold равна фактическому числу вызовов (важно для VLM/сцену).
    vlm_calls_by_scene: dict[str, int] = {p["scene_id"]: 0 for p in payloads}
    total_entries = sum(vlm_entries_by_scene.values())
    if total_entries and vlm_calls:
        raw = {sid: vlm_calls * n / total_entries for sid, n in vlm_entries_by_scene.items()}
        alloc = {sid: int(v) for sid, v in raw.items()}
        left = int(vlm_calls) - sum(alloc.values())
        for sid in sorted(raw, key=lambda s: -(raw[s] - int(raw[s]))):
            if left <= 0:
                break
            alloc[sid] += 1
            left -= 1
        vlm_calls_by_scene.update(alloc)

    for p in payloads:
        r = by_id[p["scene_id"]]
        cands = p["candidates"]
        selected_id = clip_id(r["_selected_path"])
        selected_score = r.get("selected_score")

        # кэш выбранного клипа, если в манифесте оценки нет
        if selected_score is None and db_read.exists():
            try:
                from video_maker.semantic.verify import _cache_th_for
                th = _cache_th_for(
                    {"scene_key": str(r.get("visual") or r.get("text") or ""),
                     "query": (p["queries"][0] if p["queries"] else "")},
                    args.vlm_model,
                )
                conn = sqlite3.connect(f"file:{db_read}?mode=ro", uri=True, timeout=10)
                row = conn.execute(
                    "SELECT score, what FROM scores WHERE file=? AND th=?",
                    (str(r["_selected_path"]), th),
                ).fetchone()
                conn.close()
                if row and int(row[0]) >= 0:
                    selected_score = int(row[0])
                    r["selected_what"] = str(row[1] or "")[:300]
                    r["vlm_source"] = "db_cache"
            except Exception:
                pass

        acceptable: list[str] = []
        verified_acceptable = 0
        for c in cands:
            sc = c.get("vlm_score")
            ok = isinstance(sc, int) and sc >= 6
            c["acceptable"] = bool(ok)
            if ok:
                acceptable.append(c["id"])
                if c.get("vlm_verified"):
                    verified_acceptable += 1

        selected_in_lib = bool(r["_selected_path"]) and Path(str(r["_selected_path"])).exists() \
            and ("/broll_library/" in str(r["_selected_path"]) or selected_id in db_index)
        recall_eligible = bool(acceptable) and any(cid in db_index for cid in acceptable)
        selected_ok = isinstance(selected_score, int) and selected_score >= 6
        if selected_ok:
            if selected_id not in acceptable:
                acceptable.append(selected_id)
            if selected_in_lib or selected_id in db_index:
                recall_eligible = True

        literal_candidate = any(int(c.get("literal_match") or 0) >= 2 for c in cands)
        literal_candidate_ids = [c["id"] for c in cands if int(c.get("literal_match") or 0) >= 2]

        if selected_ok:
            label = "accept"
            base_conf = 0.95 if int(selected_score) >= 8 else 0.88
        elif verified_acceptable:
            label = "fallback_ok"
            base_conf = 0.82
        elif literal_candidate:
            label = "fallback_ok"
            base_conf = 0.55
        elif r["class"] == "abstract":
            label = "must_graphic"
            base_conf = 0.72
        elif isinstance(selected_score, int) and selected_score <= 3:
            label = "must_graphic"
            base_conf = 0.6
        else:
            label = "fallback_ok"
            base_conf = 0.45

        confidence = min(base_conf, float(r["class_confidence"]) + 0.15)
        if selected_score is None:
            confidence = min(confidence, 0.5)
        confidence = round(max(0.2, min(0.95, confidence)), 2)
        needs_review = bool(
            confidence < 0.7
            or selected_score is None
            or (label == "fallback_ok" and not verified_acceptable)
            or (label == "must_graphic" and r["class"] != "abstract")
        )

        notes: list[str] = [f"класс {r['class']} (conf {r['class_confidence']:.2f})"]
        notes.append(
            f"выбранный клип VLM {selected_score if selected_score is not None else '?'}/10"
            + (f": {r.get('selected_what')[:90]}" if r.get("selected_what") else "")
        )
        if cands:
            notes.append(
                f"top-{len(cands)}: VLM проверено {sum(1 for c in cands if c.get('vlm_verified'))}, "
                f"приемлемых {verified_acceptable}"
            )
        else:
            notes.append("кандидатов в базе не найдено")
        if literal_candidate:
            notes.append(f"literal-кандидатов по метаданным: {len(literal_candidate_ids)}")
        if not selected_ok:
            if verified_acceptable:
                notes.append("есть VLM-подтверждённый fallback")
            elif literal_candidate:
                notes.append("fallback только по эвристике — нужна проверка человеком")
            else:
                notes.append("пригодного клипа не подтверждено")
        if selected_score is not None and not selected_in_lib:
            notes.append("выбранный клип вне библиотеки — офлайн-recall не считается")

        row = {
            "scene_id": r["scene_id"],
            "scene_text": str(r.get("text") or ""),
            "class": r["class"],
            "label": label,
            "confidence": confidence,
            "candidate_ids": [c["id"] for c in cands],
            "candidate_scores": [c.get("vlm_score") for c in cands],
            "candidate_meta": [
                {
                    "id": c["id"],
                    "rank": c["rank"],
                    "file": c["file"],
                    "db_score": c.get("db_score"),
                    "vlm_score": c.get("vlm_score"),
                    "vlm_what": c.get("vlm_what"),
                    "literal_action": c.get("literal_action"),
                    "literal_match": c.get("literal_match"),
                    "literal_terms": c.get("literal_terms"),
                    "acceptable": c.get("acceptable", False),
                    "vlm_verified": c.get("vlm_verified", False),
                }
                for c in cands
            ],
            "acceptable_ids": acceptable,
            "recall_eligible": bool(recall_eligible),
            "selected_clip": str(r["_selected_path"]),
            "selected_id": selected_id,
            "selected_score": selected_score,
            "selected_what": r.get("selected_what") or "",
            "selected_in_library": selected_in_lib,
            "vlm_source": r.get("vlm_source") or "",
            "needs_human_review": needs_review,
            "notes": "; ".join(notes),
            "split": "",  # проставит apply_split
            "part": r["_part"],
            "scene_index": int(r.get("idx", 0)),
            "start": r.get("start"),
            "end": r.get("end"),
            "queries": p["queries"],
            "visual": p["visual"],
            "orientation": r.get("_orientation") or "wide",
            "emotions": list(r.get("emotions") or []),
            "mood": r.get("mood") or "",
            "shot": r.get("shot") or "",
            "weights": r.get("weights") or {},
            "avoid": list(r.get("avoid") or []),
            "hard": list(r.get("hard") or []),
            "class_confidence": r["class_confidence"],
            "class_signals": r["class_signals"],
            "candidate_search_ms": p["search_ms"],
            "vlm_calls": int(vlm_calls_by_scene.get(r["scene_id"], 0)),
            "source_manifest": r["_manifest"],
        }
        rows.append(row)

    rows = apply_split(
        rows, holdout_n=int(args.holdout), seed=int(args.seed), label_key="label")

    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifests": manifests,
        "db_read": str(db_read),
        "db_production": str(PROD_DB),
        "available_scenes": len(prepared),
        "gold_scenes": len(rows),
        "limit": int(args.limit),
        "verify_top": int(args.verify_top),
        "vlm_model": args.vlm_model,
        "vlm_calls": int(vlm_calls),
        "vlm_stats": vlm_stats,
        "class_counts": dict(Counter(r["class"] for r in rows)),
        "label_counts": dict(Counter(r["label"] for r in rows)),
        "split_counts": dict(Counter(r["split"] for r in rows)),
        "needs_human_review": sum(1 for r in rows if r.get("needs_human_review")),
        "selected_score_sources": dict(Counter(r.get("vlm_source") or "missing" for r in rows)),
        "candidate_search_ms_total": round(sum(float(r.get("candidate_search_ms") or 0) for r in rows), 2),
        "notes": [
            "Метки предварительные: selected VLM-оценка из манифеста, top-N кандидатов — свежий VLM.",
            "recall_eligible=False, если приемлемый клип не лежит в библиотеке (сток) — офлайн recall не считается.",
            "Split 30/10 стратифицирован по классам и меткам, фиксируется в gold.jsonl.",
        ],
    }
    return rows, meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать eval/gold.jsonl из реальных сцен")
    ap.add_argument("--manifest", action="append", help="путь к smart_broll_manifest.json (можно несколько)")
    ap.add_argument("--limit", type=int, default=40, help="сколько сцен в gold (30-40)")
    ap.add_argument("--holdout", type=int, default=10, help="размер holdout")
    ap.add_argument("--candidates", type=int, default=10, help="топ кандидатов на сцену")
    ap.add_argument("--verify-top", type=int, default=3, help="сколько кандидатов проверять VLM (0 = без вызовов)")
    ap.add_argument("--vlm-model", default="gemini-3.1-flash-lite")
    ap.add_argument("--db", default=str(PROD_DB), help="production-БД (по умолчанию ~/video_maker/broll_library.db)")
    ap.add_argument("--no-db-copy", action="store_true", help="не копировать БД (VLM-кэш пойдёт в production)")
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--out", default=str(GOLD_PATH))
    ap.add_argument("--meta-out", default=str(META_PATH))
    ap.add_argument("--dry-run", action="store_true", help="только показать выборку, ничего не писать")
    args = ap.parse_args()

    if not (30 <= int(args.limit) <= 40):
        print(f"[gold] limit={args.limit}: для честного split нужен 30-40 (по умолчанию 40)")

    t0 = time.monotonic()
    rows, meta = build(args)
    meta["duration_s"] = round(time.monotonic() - t0, 1)
    if args.dry_run:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("[gold] пусто — gold не записан")
        return 1
    write_jsonl(args.out, rows)
    meta["out"] = str(Path(args.out))
    meta_out = args.meta_out
    if str(meta_out) == str(META_PATH) and Path(args.out) != GOLD_PATH:
        meta_out = str(Path(args.out).with_name("gold_meta.json"))
    write_json(meta_out, meta)
    print(f"\n[gold] записано {len(rows)} сцен → {args.out}")
    print(f"[gold] классы: {meta['class_counts']} | метки: {meta['label_counts']} | "
          f"split: {meta['split_counts']}")
    print(f"[gold] на ручную проверку: {meta['needs_human_review']} | VLM-вызовов: {meta['vlm_calls']} | "
          f"время {meta['duration_s']}с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
