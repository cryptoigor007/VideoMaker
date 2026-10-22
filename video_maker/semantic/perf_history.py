"""История клипов для подбора B-roll: негативный/позитивный кэш.

Таблица ``clip_perf`` в локальной базе библиотеки (broll_library.db):

    path, checks, sum_score, passes(>=6), fails, last_seen, updated_at

Зачем:
* негативный кэш — клип, который уже 3+ раза стабильно получал <2.5/10,
  не тратит Vision-вызовы повторно (кроме случая, когда его теги пересекаются
  с текущей сценой — тогда доверие истории ниже, проверяем);
* позитивный кэш — клипы со средним >=7 и хотя бы одним проходом
  поднимаются в pre-rank перед бюджетом кандидатов.

Модуль намеренно не зависит от verify.py, чтобы его можно было тестировать
и использовать в smart_broll без циклических импортов.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

_DDL = """
CREATE TABLE IF NOT EXISTS clip_perf (
    path TEXT PRIMARY KEY,
    checks INTEGER NOT NULL DEFAULT 0,
    sum_score INTEGER NOT NULL DEFAULT 0,
    passes INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    updated_at TEXT
)
"""

_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    from .library import db_path as _dbp

    conn = sqlite3.connect(str(_dbp()), timeout=15)
    conn.execute(_DDL)
    conn.commit()
    return conn


def path_of(entry: dict) -> str:
    """Стабильный путь клипа: library_file → file → verify_file (realpath)."""
    for k in ("library_file", "file", "verify_file"):
        raw = str(entry.get(k) or "").strip()
        if raw:
            try:
                return os.path.realpath(raw)
            except Exception:
                return raw
    return ""


def record_score(path: str, score: int) -> None:
    """Учесть одну Vision-оценку (0..10) для клипа."""
    if not path or not isinstance(score, int) or score < 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    ok = 1 if score >= 6 else 0
    bad = 1 if score < 6 else 0
    with _LOCK:
        try:
            conn = _connect()
            conn.execute(
                "INSERT INTO clip_perf (path, checks, sum_score, passes, fails, last_seen, updated_at) "
                "VALUES (?,1,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "checks=checks+1, sum_score=sum_score+excluded.sum_score, "
                "passes=passes+excluded.passes, fails=fails+excluded.fails, "
                "last_seen=excluded.last_seen, updated_at=excluded.updated_at",
                (path, int(score), ok, bad, now, now),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def record_entries(entries: list[dict]) -> int:
    """Записать в историю все новые оценки батча (ровно один раз на entry)."""
    updates: list[tuple[str, int]] = []
    for e in entries:
        if e.get("_perf_recorded"):
            continue
        if e.get("cached") or e.get("prefilter") or e.get("skipped_history"):
            continue
        sc = e.get("score")
        if not isinstance(sc, int) or sc < 0:
            continue
        p = path_of(e)
        e["_perf_recorded"] = True
        if p:
            updates.append((p, sc))
    if not updates:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        try:
            conn = _connect()
            for p, sc in updates:
                conn.execute(
                    "INSERT INTO clip_perf (path, checks, sum_score, passes, fails, last_seen, updated_at) "
                    "VALUES (?,1,?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "checks=checks+1, sum_score=sum_score+excluded.sum_score, "
                    "passes=passes+excluded.passes, fails=fails+excluded.fails, "
                    "last_seen=excluded.last_seen, updated_at=excluded.updated_at",
                    (p, int(sc), 1 if sc >= 6 else 0, 0 if sc >= 6 else 1, now, now),
                )
            conn.commit()
            conn.close()
        except Exception:
            return 0
    return len(updates)


def get(path: str) -> dict:
    """Статистика клипа: {checks, sum_score, passes, fails, avg}."""
    if not path:
        return {"checks": 0, "sum_score": 0, "passes": 0, "fails": 0, "avg": 0.0}
    try:
        with _LOCK:
            conn = _connect()
            row = conn.execute(
                "SELECT checks, sum_score, passes, fails FROM clip_perf WHERE path=?",
                (path,),
            ).fetchone()
            conn.close()
        if not row:
            raise LookupError
        checks, total, passes, fails = (int(x or 0) for x in row)
        return {
            "checks": checks,
            "sum_score": total,
            "passes": passes,
            "fails": fails,
            "avg": (total / checks) if checks else 0.0,
        }
    except Exception:
        return {"checks": 0, "sum_score": 0, "passes": 0, "fails": 0, "avg": 0.0}


def get_many(paths: list[str] | set[str]) -> dict[str, dict]:
    """Статистика пачки клипов одним запросом (pre-rank без N коннектов)."""
    wanted = [p for p in dict.fromkeys(paths or []) if p]
    empty = {"checks": 0, "sum_score": 0, "passes": 0, "fails": 0, "avg": 0.0}
    out: dict[str, dict] = {p: dict(empty) for p in wanted}
    if not wanted:
        return out
    try:
        with _LOCK:
            conn = _connect()
            marks = ",".join("?" for _ in wanted)
            rows = conn.execute(
                f"SELECT path, checks, sum_score, passes, fails FROM clip_perf "
                f"WHERE path IN ({marks})", wanted).fetchall()
            conn.close()
        for p, checks, total, passes, fails in rows:
            checks = int(checks or 0)
            out[str(p)] = {"checks": checks, "sum_score": int(total or 0),
                           "passes": int(passes or 0), "fails": int(fails or 0),
                           "avg": (float(total or 0) / checks) if checks else 0.0}
    except Exception:
        pass
    return out


def is_low(stats: dict) -> bool:
    """Негативный кэш: 3+ проверки и средний балл ниже 2.5."""
    return int(stats.get("checks") or 0) >= 3 and float(stats.get("avg") or 0.0) < 2.5


def _tokens(text: str) -> set[str]:
    try:
        from .matcher import tokenize

        return {t for t in tokenize(text) if len(t) >= 4}
    except Exception:
        return {w for w in str(text or "").lower().replace("-", " ").split() if len(w) >= 4}


def tags_intersect(entry: dict, scene_text: str | None = None) -> bool:
    """Есть ли пересечение тегов/описания клипа со сценой (запросом)."""
    parts: list[str] = []
    tags = entry.get("tags")
    if isinstance(tags, (list, tuple, set)):
        parts.extend(str(x) for x in tags)
    else:
        parts.append(str(tags or ""))
    parts.append(str(entry.get("description") or ""))
    parts.append(os.path.basename(str(entry.get("file") or "")))
    clip_t = _tokens(" ".join(parts))
    if not clip_t:
        return False
    scene_parts = [str(scene_text or ""), str(entry.get("scene") or ""),
                   str(entry.get("visual") or ""), str(entry.get("query") or "")]
    scene_t = _tokens(" ".join(scene_parts))
    return bool(clip_t & scene_t)


def should_skip(entry: dict, scene_text: str | None = None) -> bool:
    """Пропускать ли клип по негативной истории (с учётом тегов сцены)."""
    if entry.get("verified") or entry.get("cached"):
        return False
    p = path_of(entry)
    if not p:
        return False
    st = get(p)
    if not is_low(st):
        return False
    return not tags_intersect(entry, scene_text)


def apply_history_skips(entries: list[dict], *, log=print) -> int:
    """Пометить низкоисторические клипы как пропущенные (без Vision)."""
    n = 0
    for e in entries:
        if not should_skip(e):
            continue
        st = get(path_of(e))
        e["score"] = 0
        e["what"] = f"история: низкая ({st.get('avg', 0):.1f}/10, проверок {st.get('checks', 0)})"
        e["verified"] = True
        e["skipped_history"] = True
        n += 1
    if n and log:
        log(f"[VISION] пропущено (низкая история): {n}")
    return n


def rank_bonus_stats(st: dict) -> float:
    """Бонус к pre-rank по готовой статистике (−1..1)."""
    checks = int(st.get("checks") or 0)
    if checks <= 0:
        return 0.0
    avg = float(st.get("avg") or 0.0)
    conf = min(1.0, checks / 5.0)
    passes = int(st.get("passes") or 0)
    if avg >= 7.0 and passes > 0:
        return 0.5 + 0.5 * (avg / 10.0) * conf
    if avg >= 6.0:
        return 0.3 * conf
    if checks >= 3 and avg < 2.5:
        return -1.0
    if int(st.get("fails") or 0) > passes:
        return -0.3 * conf
    return 0.0


def rank_bonus(entry: dict) -> float:
    """Бонус к pre-rank: + за позитивную историю, − за негативную (−1..1)."""
    return rank_bonus_stats(get(path_of(entry)))


def reset(paths: list[str] | None = None) -> None:
    """Очистить историю (тесты/диагностика). paths=None — всю таблицу."""
    with _LOCK:
        try:
            conn = _connect()
            if paths:
                conn.executemany("DELETE FROM clip_perf WHERE path=?", [(p,) for p in paths])
            else:
                conn.execute("DELETE FROM clip_perf")
            conn.commit()
            conn.close()
        except Exception:
            pass


def summary(limit: int = 10) -> list[dict]:
    """Топ клипов по средней оценке (для отчётов)."""
    try:
        with _LOCK:
            conn = _connect()
            rows = conn.execute(
                "SELECT path, checks, sum_score, passes, fails FROM clip_perf "
                "WHERE checks > 0 ORDER BY (1.0*sum_score/checks) DESC, checks DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            conn.close()
        return [
            {"path": r[0], "checks": r[1], "avg": r[2] / r[1] if r[1] else 0,
             "passes": r[3], "fails": r[4]}
            for r in rows
        ]
    except Exception:
        return []
