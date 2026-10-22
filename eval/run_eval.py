#!/usr/bin/env python3
"""Прогон eval-метрик на gold-наборе сцен.

Метрики:
  * Recall@1/3/10 — доля сцен, где приемлемый клип попал в топ-K поиска
    (по классам и по split train/holdout);
  * literal hit-rate@1/@10 — то же для literal-сцен;
  * fallback rate — доля сцен, где выбранный клип не принят
    (label = fallback_ok / must_graphic);
  * время по стадиям (retrieval) и VLM-вызовов на сцену (из сборки gold).

Два режима:
  * ``--retrieval gold`` (быстро) — использует порядок кандидатов, сохранённый
    в gold.jsonl при сборке (тот же search_library_multi, без повторного поиска);
  * ``--retrieval fresh`` — заново запускает поиск по текущей библиотеке.

Примеры:
    .venv/bin/python3 eval/run_eval.py --retrieval gold
    .venv/bin/python3 eval/run_eval.py --retrieval fresh
    .venv/bin/python3 eval/run_eval.py --gold eval/gold.jsonl --report eval/report.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.common import (  # noqa: E402
    CLASSES,
    K_VALUES,
    compute_metrics,
    ensure_split,
    read_jsonl,
    summarize_metrics,
    write_json,
)

EVAL_DIR = Path(__file__).resolve().parent
PROD_DB = Path.home() / "video_maker" / "broll_library.db"
CACHE_DIR = EVAL_DIR / ".cache"


# ---------------------------------------------------------------------------
# Свежий поиск
# ---------------------------------------------------------------------------
def _eval_db_copy() -> Path:
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
        return dst
    except Exception as e:
        print(f"[eval] копия БД не сделана: {str(e)[:100]}")
        return PROD_DB


def fresh_retrieval(records: list[dict], *, limit: int = 10) -> tuple[dict, dict, dict]:
    """Заново прогнать поиск. Возвращает (retrieved, timings, errors)."""
    db = _eval_db_copy()
    # Кэш Path.exists по индексу БД: иначе SMB-stat 1700 клипов × варианты
    # запроса занимает минуты на первую сцену.
    try:
        from eval.make_gold import _db_clip_index, _install_fast_exists
        _install_fast_exists(_db_clip_index(db))
    except Exception:
        pass
    import video_maker.semantic.library as lib

    lib.db_path = lambda root=None: Path(db)  # noqa: ARG005
    from video_maker.semantic.library import search_library_multi

    retrieved: dict[str, list[str]] = {}
    timings: dict[str, dict] = {}
    errors: dict[str, str] = {}
    n = len(records)
    for i, rec in enumerate(records, 1):
        sid = str(rec.get("scene_id") or "")
        queries = [str(q) for q in (rec.get("queries") or []) if str(q).strip()][:3]
        if not queries:
            retrieved[sid] = []
            timings[sid] = {"retrieval_ms": 0.0, "vlm_calls": 0}
            errors[sid] = "нет queries"
            continue
        emo = [str(x) for x in (rec.get("emotions") or []) if str(x).strip()][:6]
        mood = str(rec.get("mood") or "").strip()
        shot = str(rec.get("shot") or "").strip()
        weights = rec.get("weights") or {}
        wt: dict[str, float] = {}
        for e in emo:
            wt[e.lower()] = float(weights.get("emotions", 2) or 2)
        if mood:
            wt[mood.lower()] = float(weights.get("mood", 1) or 1)
        if shot:
            wt[shot.lower()] = float(weights.get("shot", 1) or 1)
        extra = " ".join(emo + ([mood] if mood else []))
        avoid = {str(x).lower() for x in (rec.get("avoid") or []) if str(x).strip()}
        hard = {str(x).lower() for x in (rec.get("hard") or []) if str(x).strip()}
        t0 = time.monotonic()
        try:
            hits = search_library_multi(
                queries,
                orientation=rec.get("orientation") or "wide",
                per_query=20,
                limit=max(12, int(limit)),
                avoid=avoid,
                hard=hard,
                weight_terms=wt,
                extra_terms=extra,
                log=None,
            )
            ids = [Path(str(h.get("file") or "")).name for h in hits if h.get("file")][: int(limit)]
        except Exception as e:
            ids = []
            errors[sid] = str(e)[:200]
        ms = (time.monotonic() - t0) * 1000.0
        retrieved[sid] = ids
        timings[sid] = {
            "retrieval_ms": round(ms, 2),
            "vlm_calls": int(rec.get("vlm_calls") or 0),
        }
        if i == 1 or i % 5 == 0 or i == n:
            print(f"[eval] fresh {i}/{n} {sid}: {len(ids)} кандидатов, {ms / 1000:.1f}с")
    return retrieved, timings, errors


def gold_retrieval(records: list[dict], *, limit: int = 10) -> tuple[dict, dict, dict]:
    """Взять сохранённый при сборке gold порядок кандидатов."""
    retrieved: dict[str, list[str]] = {}
    timings: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for rec in records:
        sid = str(rec.get("scene_id") or "")
        ids = [str(x) for x in (rec.get("candidate_ids") or [])][: int(limit)]
        retrieved[sid] = ids
        timings[sid] = {
            "retrieval_ms": float(rec.get("candidate_search_ms") or 0.0),
            "vlm_calls": int(rec.get("vlm_calls") or 0),
        }
        if not ids:
            errors[sid] = "нет кандидатов в gold"
    return retrieved, timings, errors


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------
def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _recall_cell(block: dict, k: int) -> str:
    r = block.get("recall", {}).get(f"@{k}")
    return _pct(r)


def _table_row(name: str, b: dict) -> str:
    return (
        f"| {name} | {b['n_scenes']} | {b['n_recall_scenes']} | "
        f"{_recall_cell(b, 1)} | {_recall_cell(b, 3)} | {_recall_cell(b, 10)} | "
        f"{_pct(b['literal_hit_rate']['@1'])} | {_pct(b['literal_hit_rate']['@10'])} | "
        f"{_pct(b['fallback_rate'])} | "
        f"{b['retrieval_ms_mean'] if b['retrieval_ms_mean'] is not None else 'n/a'} | "
        f"{b['vlm_calls_per_scene'] if b['vlm_calls_per_scene'] is not None else 'n/a'} |"
    )


def build_report(metrics: dict, *, gold_path: str, retrieval_mode: str,
                 meta: dict | None, errors: dict, records: list[dict]) -> str:
    o = metrics["overall"]
    lines: list[str] = []
    lines.append("# Eval: эталонные сцены VideoMaker")
    lines.append("")
    lines.append(f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append(f"Gold: `{gold_path}`  ")
    lines.append(f"Режим retrieval: `{retrieval_mode}`  ")
    lines.append(f"Сцен: **{o['n_scenes']}**, из них с приемлемым клипом в библиотеке: "
                 f"**{o['n_recall_scenes']}**")
    if meta:
        lines.append(f"VLM при сборке: **{meta.get('vlm_calls', 0)}** вызовов "
                     f"({o['vlm_calls_per_scene']:.2f}/сцену); "
                     f"на ручную проверку: **{meta.get('needs_human_review', '?')}**")
    lines.append("")
    lines.append("## Главное")
    lines.append("")
    lines.append("| Срез | Сцен | Recall-сцен | Recall@1 | Recall@3 | Recall@10 | literal@1 | literal@10 | fallback rate | retrieval, мс/сцену | VLM/сцену |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(_table_row("**всего**", o))
    for split in ("train", "holdout"):
        b = metrics["by_split"].get(split)
        if b:
            lines.append(_table_row(f"split: {split}", b))
    lines.append("")
    lines.append(f"literal-сцен в знаменателе literal hit-rate: **{o['n_literal_scenes']}** "
                 f"(только сцены с приемлемым клипом в библиотеке).")
    lines.append("")
    lines.append("## По классам сцен")
    lines.append("")
    lines.append("| Класс | Сцен | Recall-сцен | Recall@1 | Recall@3 | Recall@10 | literal@1 | literal@10 | fallback rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cls in CLASSES:
        b = metrics["by_class"].get(cls)
        if not b or not b["n_scenes"]:
            continue
        lines.append(
            f"| {cls} | {b['n_scenes']} | {b['n_recall_scenes']} | "
            f"{_recall_cell(b, 1)} | {_recall_cell(b, 3)} | {_recall_cell(b, 10)} | "
            f"{_pct(b['literal_hit_rate']['@1'])} | {_pct(b['literal_hit_rate']['@10'])} | "
            f"{_pct(b['fallback_rate'])} |"
        )
    lines.append("")
    lines.append("## Метки пригодности")
    lines.append("")
    lines.append("| Метка | Всего | train | holdout |")
    lines.append("|---|---:|---:|---:|")
    all_labels = Counter(str(r.get("label") or "") for r in records)
    for label in ("accept", "fallback_ok", "must_graphic"):
        tr = metrics["by_split"]["train"]["label_counts"].get(label, 0)
        ho = metrics["by_split"]["holdout"]["label_counts"].get(label, 0)
        lines.append(f"| {label} | {all_labels.get(label, 0)} | {tr} | {ho} |")
    lines.append("")
    lines.append("## Где приемлемый клип не найден в top-10")
    lines.append("")
    misses = [s for s in metrics.get("scenes", []) if s.get("recall_eligible") and not s["hit"]["@10"]]
    if not misses:
        lines.append("Нет: во всех recall-сценах приемлемый клип попал в top-10.")
    else:
        lines.append("| Сцена | split | класс | метка | выбранный | приемлемые | top-3 retrieval |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in misses[:20]:
            lines.append(
                f"| `{s['scene_id']}` | {s.get('split')} | {s.get('class')} | {s.get('label')} | "
                f"`{s.get('selected_id') or '—'}` | {', '.join(s.get('acceptable_ids') or []) or '—'} | "
                f"{', '.join((s.get('retrieved_ids') or [])[:3]) or '—'} |"
            )
        if len(misses) > 20:
            lines.append(f"\n…ещё {len(misses) - 20}.")
    if errors:
        lines.append("")
        lines.append(f"Ошибок retrieval: **{len(errors)}** (см. report.json).")
    lines.append("")
    lines.append("## Как читать")
    lines.append("")
    lines.append("- **Recall@K** — доля recall-сцен, где хотя бы один приемлемый клип "
                 "(VLM ≥ 6/10) оказался в топ-K. Сцены без приемлемого клипа в библиотеке "
                 "(чистый сток / must_graphic) в знаменатель не входят.")
    lines.append("- **literal@1** — доля literal-сцен, где top-1 сразу приемлемый. "
                 "Это самый чувствительный показатель к «красиво, но не то».")
    lines.append("- **fallback rate** — доля сцен, где выбранный клип не принят: нужен "
                 "другой клип (fallback_ok) или графика (must_graphic).")
    lines.append("- **VLM/сцену** — реальные вызовы Vision при сборке gold (top-3 кандидата "
                 "на сцену), а не при прогоне метрик.")
    lines.append("")
    lines.append("## Ограничения")
    lines.append("")
    lines.append("- Holdout — 10 сцен, поэтому доверительные интервалы широкие; "
                 "метрики holdout нельзя использовать для подбора порогов.")
    lines.append("- Кандидаты — только библиотека (без stock-API и скачивания). "
                 "Стоковые клипы в офлайн-recall не участвуют.")
    lines.append("- VLM проверяет только top-3 кандидата на сцену; Recall@10 — нижняя "
                 "оценка (приемлемый клип на ранге 4-10 мог не проверяться).")
    lines.append("- Классы и метки предварительные: см. `eval/README.md`, поля "
                 "`confidence` и `needs_human_review`.")
    lines.append("")
    lines.append("## Воспроизвести")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python3 eval/make_gold.py --limit 40 --verify-top 3")
    lines.append(".venv/bin/python3 eval/run_eval.py --retrieval gold   # быстро")
    lines.append(".venv/bin/python3 eval/run_eval.py --retrieval fresh  # честный текущий поиск")
    lines.append(".venv/bin/python3 -m pytest eval/tests -q")
    lines.append("```")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Метрики gold-набора сцен")
    ap.add_argument("--gold", default=str(EVAL_DIR / "gold.jsonl"))
    ap.add_argument("--report", default=str(EVAL_DIR / "report.md"))
    ap.add_argument("--json", default=str(EVAL_DIR / "report.json"))
    ap.add_argument("--retrieval", choices=("gold", "fresh"), default="gold",
                    help="gold = сохранённые кандидаты; fresh = заново искать")
    ap.add_argument("--limit", type=int, default=10, help="глубина retrieval")
    ap.add_argument("--holdout", type=int, default=10)
    ap.add_argument("--max-scenes", type=int, default=0,
                    help="0 = все; иначе первые N сцен (для smoke-прогона)")
    args = ap.parse_args()

    records = read_jsonl(args.gold)
    if not records:
        print(f"[eval] gold пуст или не найден: {args.gold}")
        return 2
    records = ensure_split(records, holdout_n=int(args.holdout))
    if args.max_scenes and args.max_scenes > 0:
        records = records[: int(args.max_scenes)]

    meta: dict | None = None
    meta_candidates = [
        Path(args.gold).with_name("gold_meta.json"),
        EVAL_DIR / "gold_meta.json",
    ]
    meta_path = next((p for p in meta_candidates if p.exists()), None)
    if meta_path is not None:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = None

    t0 = time.monotonic()
    if args.retrieval == "fresh":
        retrieved, timings, errors = fresh_retrieval(records, limit=int(args.limit))
    else:
        retrieved, timings, errors = gold_retrieval(records, limit=int(args.limit))
    elapsed = time.monotonic() - t0

    metrics = compute_metrics(records, retrieved, timings=timings)
    report = build_report(
        metrics, gold_path=args.gold, retrieval_mode=args.retrieval,
        meta=meta, errors=errors, records=records,
    )

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "gold": str(Path(args.gold).resolve()),
            "retrieval": args.retrieval,
            "limit": int(args.limit),
            "holdout": int(args.holdout),
            "run_seconds": round(elapsed, 2),
        },
        "gold_meta": meta,
        "metrics": {k: v for k, v in metrics.items() if k != "scenes"},
        "scenes": metrics["scenes"],
        "errors": errors,
        "label_counts": dict(Counter(str(r.get("label") or "") for r in records)),
        "class_counts": dict(Counter(str(r.get("class") or "") for r in records)),
        "split_counts": dict(Counter(str(r.get("split") or "") for r in records)),
    }
    write_json(args.json, payload)
    Path(args.report).write_text(report, encoding="utf-8")

    print("\n" + summarize_metrics(metrics))
    print(f"[eval] отчёт: {args.report}")
    print(f"[eval] JSON:   {args.json}")
    if errors:
        print(f"[eval] ошибок retrieval: {len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
