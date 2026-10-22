"""Общие утилиты eval-harness VideoMaker (без импорта пайплайна на уровне модуля).

Здесь только чистые функции: идентификаторы клипов, чтение/запись gold,
жёсткое train/holdout-разделение и расчёт метрик. Модуль можно тестировать
на синтетических данных без базы и без VLM.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SPLIT_SEED = 20261022
HOLDOUT_N = 10
CLASSES = ("literal", "associative", "abstract")
LABELS = ("accept", "fallback_ok", "must_graphic")
K_VALUES = (1, 3, 10)


# --------------------------------------------------------------------------
# Идентификаторы
# --------------------------------------------------------------------------
def clip_id(path_or_name: str) -> str:
    """Стабильный id клипа: имя файла (в библиотеке оно уникально).

    Полный путь хранится отдельно в ``selected_clip``/``candidate_meta.file``.
    Имя файла содержит id источника (pixabay/pexels) и не меняется при
    переносе библиотеки между дисками.
    """
    return Path(str(path_or_name or "")).name


def is_library_path(path: str) -> bool:
    p = str(path or "")
    return "/broll_library/" in p


# --------------------------------------------------------------------------
# JSONL
# --------------------------------------------------------------------------
def read_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    p = Path(path)
    if not p.exists():
        return rows
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
    tmp.replace(p)


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# --------------------------------------------------------------------------
# Split: 30 train / 10 holdout, жёстко и стратифицированно по классам
# --------------------------------------------------------------------------
def _hash_key(scene_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{scene_id}".encode("utf-8")).hexdigest()


def _allocate_holdout(counts: dict[str, int], holdout_n: int) -> dict[str, int]:
    """Пропорциональная аллокация holdout по классам (largest remainder)."""
    total = sum(counts.values())
    if total <= 0 or holdout_n <= 0:
        return {c: 0 for c in counts}
    holdout_n = min(int(holdout_n), total)
    raw = {c: holdout_n * n / total for c, n in counts.items()}
    alloc = {c: int(math.floor(v)) for c, v in raw.items()}
    # не больше, чем есть в классе
    alloc = {c: min(alloc[c], counts[c]) for c in counts}
    left = holdout_n - sum(alloc.values())
    order = sorted(
        counts,
        key=lambda c: (-(raw[c] - math.floor(raw[c])), c),
    )
    while left > 0:
        moved = False
        for c in order:
            if left <= 0:
                break
            if alloc[c] < counts[c]:
                alloc[c] += 1
                left -= 1
                moved = True
        if not moved:
            break
    # если классы маленькие и остаток не разложился — добираем по любым
    if left > 0:
        for c in sorted(counts):
            take = min(left, counts[c] - alloc[c])
            alloc[c] += take
            left -= take
            if left <= 0:
                break
    return alloc


def stratified_split(
    records: list[dict],
    *,
    holdout_n: int = HOLDOUT_N,
    seed: int = SPLIT_SEED,
    class_key: str = "class",
    id_key: str = "scene_id",
    label_key: str | None = None,
) -> dict[str, str]:
    """Вернуть {scene_id: 'train'|'holdout'}.

    Разделение детерминированное: одинаковый gold + seed → одинаковый split.
    Внутри группы порядок задаёт sha256(seed:scene_id), поэтому holdout не
    зависит от порядка строк в файле. Группы — класс сцены, а при
    ``label_key`` — ещё и метка пригодности: это гарантирует, что accept не
    окажутся все в train (иначе holdout не проверяет главный сценарий).
    """
    by_group: dict[str, list[dict]] = defaultdict(list)

    def _group(rec: dict) -> str:
        c = str(rec.get(class_key) or "associative")
        if label_key:
            return f"{c}|{rec.get(label_key) or ''}"
        return c

    for rec in records:
        by_group[_group(rec)].append(rec)
    counts = {g: len(v) for g, v in by_group.items()}
    alloc = _allocate_holdout(counts, holdout_n)
    out: dict[str, str] = {}
    for g, recs in by_group.items():
        ordered = sorted(recs, key=lambda r: _hash_key(str(r.get(id_key) or ""), seed))
        h = min(alloc.get(g, 0), len(ordered))
        for i, rec in enumerate(ordered):
            sid = str(rec.get(id_key) or "")
            out[sid] = "holdout" if i < h else "train"
    return out


def apply_split(records: list[dict], *, holdout_n: int = HOLDOUT_N,
                seed: int = SPLIT_SEED, label_key: str | None = None) -> list[dict]:
    """Проставить split в записи. Уже размеченный split сохраняется.

    Возвращает новый список (не мутирует вход) — удобно для тестов.
    """
    assigned = stratified_split(
        records, holdout_n=holdout_n, seed=seed, label_key=label_key)
    out: list[dict] = []
    for rec in records:
        r = dict(rec)
        sid = str(r.get("scene_id") or "")
        if r.get("split") not in ("train", "holdout"):
            r["split"] = assigned.get(sid, "train")
        out.append(r)
    return out


def ensure_split(records: list[dict], *, holdout_n: int = HOLDOUT_N,
                 seed: int = SPLIT_SEED) -> list[dict]:
    """Как apply_split, но сохраняет корректный готовый split без пересчёта."""
    if all(r.get("split") in ("train", "holdout") for r in records):
        return [dict(r) for r in records]
    return apply_split(records, holdout_n=holdout_n, seed=seed)


# --------------------------------------------------------------------------
# Метрики
# --------------------------------------------------------------------------
def _hit(retrieved: list[str], acceptable: set[str], k: int) -> bool:
    return bool(acceptable.intersection(retrieved[: int(k)]))


def _new_block() -> dict:
    return {
        "n_scenes": 0,
        "n_recall_scenes": 0,
        "n_literal_scenes": 0,
        "hits": {k: 0 for k in K_VALUES},
        "literal_hits": {1: 0, 10: 0},
        "labels": {lab: 0 for lab in LABELS},
        "retrieval_ms_total": 0.0,
        "vlm_calls": 0,
    }


def _finalize_block(b: dict) -> dict:
    n = b["n_scenes"]
    nr = b["n_recall_scenes"]
    nl = b["n_literal_scenes"]
    labels = b["labels"]
    return {
        "n_scenes": n,
        "n_recall_scenes": nr,
        "n_literal_scenes": nl,
        "recall": {f"@{k}": (b["hits"][k] / nr if nr else None) for k in K_VALUES},
        "literal_hit_rate": {
            "@1": (b["literal_hits"][1] / nl if nl else None),
            "@10": (b["literal_hits"][10] / nl if nl else None),
        },
        "accept_rate": (labels["accept"] / n if n else None),
        "fallback_rate": ((labels["fallback_ok"] + labels["must_graphic"]) / n if n else None),
        "fallback_ok_rate": (labels["fallback_ok"] / n if n else None),
        "must_graphic_rate": (labels["must_graphic"] / n if n else None),
        "label_counts": dict(labels),
        "retrieval_ms_total": round(b["retrieval_ms_total"], 2),
        "retrieval_ms_mean": (round(b["retrieval_ms_total"] / n, 2) if n else None),
        "vlm_calls": b["vlm_calls"],
        "vlm_calls_per_scene": (round(b["vlm_calls"] / n, 3) if n else None),
    }


def compute_metrics(
    records: list[dict],
    retrieved: dict[str, list[str]],
    *,
    timings: dict[str, dict] | None = None,
    vlm_calls: dict[str, int] | None = None,
) -> dict:
    """Посчитать recall/literal-hit/fallback по gold + результатам поиска.

    ``records`` — строки gold. Для recall используются только сцены с
    непустым ``acceptable_ids`` и ``recall_eligible != False``: если
    приемлемый клип не лежит в библиотеке (сток), офлайн-recall по нему
    неопределён и сцена не входит в знаменатель (это фиксируется отдельно).

    ``retrieved`` — {scene_id: [clip_id, ...]} в порядке ранжирования.
    ``timings`` — {scene_id: {"retrieval_ms": float, "vlm_calls": int}}.
    """
    timings = timings or {}
    vlm_calls = vlm_calls or {}

    overall = _new_block()
    blocks = {
        "train": _new_block(),
        "holdout": _new_block(),
    }
    by_class = {c: _new_block() for c in CLASSES}

    def add(block: dict, rec: dict) -> None:
        sid = str(rec.get("scene_id") or "")
        acceptable = {str(x) for x in (rec.get("acceptable_ids") or []) if str(x)}
        eligible = bool(acceptable) and rec.get("recall_eligible", True) is not False
        got = [str(x) for x in (retrieved.get(sid) or [])]
        label = str(rec.get("label") or "")
        cls = str(rec.get("class") or "")

        block["n_scenes"] += 1
        if label in block["labels"]:
            block["labels"][label] += 1
        if eligible:
            block["n_recall_scenes"] += 1
            for k in K_VALUES:
                if _hit(got, acceptable, k):
                    block["hits"][k] += 1
            if cls == "literal":
                block["n_literal_scenes"] += 1
                if _hit(got, acceptable, 1):
                    block["literal_hits"][1] += 1
                if _hit(got, acceptable, 10):
                    block["literal_hits"][10] += 1

        t = timings.get(sid) or {}
        try:
            block["retrieval_ms_total"] += float(t.get("retrieval_ms") or 0.0)
        except (TypeError, ValueError):
            pass
        block["vlm_calls"] += int(t.get("vlm_calls") or vlm_calls.get(sid) or 0)

    for rec in records:
        add(overall, rec)
        split = str(rec.get("split") or "train")
        if split not in blocks:
            split = "train"
        add(blocks[split], rec)
        cls = str(rec.get("class") or "")
        if cls in by_class:
            add(by_class[cls], rec)

    result = {
        "overall": _finalize_block(overall),
        "by_split": {k: _finalize_block(v) for k, v in blocks.items()},
        "by_class": {k: _finalize_block(v) for k, v in by_class.items()},
    }

    # per-scene детали (для отчёта и разбора промахов)
    scenes: list[dict] = []
    for rec in records:
        sid = str(rec.get("scene_id") or "")
        acceptable = {str(x) for x in (rec.get("acceptable_ids") or []) if str(x)}
        eligible = bool(acceptable) and rec.get("recall_eligible", True) is not False
        got = [str(x) for x in (retrieved.get(sid) or [])]
        scenes.append({
            "scene_id": sid,
            "split": rec.get("split"),
            "class": rec.get("class"),
            "label": rec.get("label"),
            "confidence": rec.get("confidence"),
            "needs_human_review": rec.get("needs_human_review"),
            "acceptable_ids": sorted(acceptable),
            "recall_eligible": eligible,
            "selected_id": rec.get("selected_id") or clip_id(rec.get("selected_clip") or ""),
            "selected_rank": (
                got.index(str(rec.get("selected_id"))) + 1
                if (rec.get("selected_id") or clip_id(rec.get("selected_clip") or "")) in got
                else None
            ),
            "retrieved_ids": got,
            "hit": {f"@{k}": (_hit(got, acceptable, k) if eligible else None) for k in K_VALUES},
        })
    result["scenes"] = scenes
    return result


def summarize_metrics(metrics: dict) -> str:
    """Короткая консольная сводка."""
    o = metrics["overall"]
    r = o["recall"]
    fmt = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    return (
        f"сцен {o['n_scenes']} | recall@1/3/10 "
        f"{fmt(r['@1'])}/{fmt(r['@3'])}/{fmt(r['@10'])} | "
        f"literal@1 {fmt(o['literal_hit_rate']['@1'])} | "
        f"fallback {fmt(o['fallback_rate'])} | "
        f"VLM/сцену {o['vlm_calls_per_scene']:.2f}"
    )
