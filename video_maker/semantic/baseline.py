"""P0-1: baseline-логирование выбора клипов по сценам.

Пишет в папку пула (рядом с ``smart_broll_manifest.json``):

* ``scenes.jsonl`` — одна JSON-строка на сцену, поля:
  scene_id, scene_text, queries, candidate_ids, candidate_scores,
  selected_clip, selected_score, fallback_used, search_ms, vlm_calls,
  cache_hits, ts;
* ``run_summary.json`` — сцены, ≥6, средний балл, время по стадиям, VLM/сцену.

Модуль намеренно не зависит от smart_broll, чтобы его можно было тестировать
отдельно. Все ошибки записи логируются и не валят рендер.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REQUIRED_FIELDS = (
    "scene_id",
    "scene_text",
    "queries",
    "candidate_ids",
    "candidate_scores",
    "selected_clip",
    "selected_score",
    "fallback_used",
    "search_ms",
    "vlm_calls",
    "cache_hits",
    "ts",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line.rstrip("\n") + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _scene_index(rec: dict) -> int:
    v = rec.get("scene_index")
    if isinstance(v, int):
        return v
    sid = str(rec.get("scene_id") or "")
    tail = sid.rsplit("_", 1)[-1]
    try:
        return int(tail)
    except Exception:
        return 10 ** 9


class SceneBaseline:
    """Накопитель baseline-записей + атомарная сводка прогона."""

    def __init__(self, out_dir: str | Path, *, log=None, enabled: bool = True):
        self.out_dir = Path(out_dir)
        self.log = log or (lambda *a, **k: None)
        self.enabled = bool(enabled)
        self.jsonl_path = self.out_dir / "scenes.jsonl"
        self.summary_path = self.out_dir / "run_summary.json"
        self.records: dict[int, dict] = {}
        self._warned = False
        if self.enabled:
            self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        try:
            if not self.jsonl_path.exists():
                return
            for raw in self.jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    self.records[_scene_index(rec)] = rec
        except Exception as e:
            self._warn(f"baseline: не прочитан scenes.jsonl: {str(e)[:100]}")

    def _warn(self, msg: str) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            self.log(f"[SB] {msg}")
        except Exception:
            pass

    # ----------------------------------------------------------------- write
    def record(
        self,
        *,
        scene_index: int,
        scene_text: str,
        queries: list[str],
        candidate_ids: list[str],
        candidate_scores: list[int | None],
        selected_clip: str,
        selected_score: int | None,
        fallback_used: str,
        search_ms: float = 0.0,
        vlm_calls: int = 0,
        cache_hits: int = 0,
        scene_duration_s: float | None = None,
        fallback_reason: str = "",
        treated: bool = False,
        graphic_fallback: bool = False,
        persist: bool = True,
    ) -> dict:
        rec = {
            "scene_id": f"scene_{int(scene_index):03d}",
            "scene_text": str(scene_text or "")[:1000],
            "queries": [str(q) for q in (queries or [])],
            "candidate_ids": [str(x) for x in (candidate_ids or [])],
            "candidate_scores": [
                int(s) if isinstance(s, int) and not isinstance(s, bool) else None
                for s in (candidate_scores or [])
            ],
            "selected_clip": str(selected_clip or ""),
            "selected_score": (
                int(selected_score)
                if isinstance(selected_score, int) and not isinstance(selected_score, bool)
                else None
            ),
            "fallback_used": str(fallback_used or "none"),
            "search_ms": round(float(search_ms or 0.0), 2),
            "vlm_calls": int(vlm_calls or 0),
            "cache_hits": int(cache_hits or 0),
            "ts": _utc_now(),
        }
        # Доп. поля не обязательны для базовой схемы, но нужны отчёту/resume.
        rec["scene_index"] = int(scene_index)
        if scene_duration_s is not None:
            rec["scene_duration_s"] = round(max(0.0, float(scene_duration_s)), 2)
        if fallback_reason:
            rec["fallback_reason"] = str(fallback_reason)[:300]
        if treated:
            rec["treated"] = True
        if graphic_fallback:
            rec["graphic_fallback"] = True
        self.records[int(scene_index)] = rec
        if self.enabled and persist:
            self._append(rec)
        return rec

    def record_resume(
        self,
        *,
        scene_index: int,
        scene: dict,
        progress_rec: dict,
        downloaded: list[dict] | None = None,
    ) -> dict:
        """Восстановить baseline-запись завершённой сцены (resume/старый прогресс)."""
        if int(scene_index) in self.records:
            return self.records[int(scene_index)]
        files = [str(f) for f in (progress_rec.get("files") or []) if f]
        selected = files[0] if files else ""
        score = progress_rec.get("score")
        if not isinstance(score, int):
            score = None
        cids: list[str] = []
        cscores: list[int | None] = []
        for d in downloaded or []:
            cid = str(d.get("url") or d.get("library_file") or d.get("file") or "")
            if not cid:
                continue
            cids.append(cid)
            s = d.get("score")
            cscores.append(s if isinstance(s, int) else None)
        return self.record(
            scene_index=scene_index,
            scene_text=str(scene.get("text") or scene.get("visual") or ""),
            queries=[str(q) for q in (scene.get("queries") or [])],
            candidate_ids=cids,
            candidate_scores=cscores,
            selected_clip=selected,
            selected_score=score,
            fallback_used=str(progress_rec.get("fallback") or "legacy_resume"),
            search_ms=float(progress_rec.get("search_ms") or 0.0),
            vlm_calls=int(progress_rec.get("vlm_calls") or 0),
            cache_hits=int(progress_rec.get("cache_hits") or 0),
            scene_duration_s=_scene_duration(scene),
            fallback_reason=str(progress_rec.get("fallback_reason") or "resume"),
            treated=bool(progress_rec.get("treated")),
            graphic_fallback=bool(progress_rec.get("graphic_fallback")),
            persist=True,
        )

    def _append(self, rec: dict) -> None:
        try:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
        except Exception as e:
            self._warn(f"baseline: scenes.jsonl не пишется: {str(e)[:120]}")

    # --------------------------------------------------------------- summary
    def finalize(
        self,
        *,
        series: str,
        total_scenes: int,
        stage_times: dict[str, float] | None = None,
        vlm_calls: int = 0,
        cache_hits: int = 0,
        vlm_calls_total: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict:
        records = [self.records[k] for k in sorted(self.records)]
        scores = [
            r.get("selected_score") for r in records
            if isinstance(r.get("selected_score"), int)
        ]
        ge6 = sum(1 for s in scores if s >= 6)
        avg = round(sum(scores) / len(scores), 2) if scores else 0.0
        fallback_counts = Counter(str(r.get("fallback_used") or "none") for r in records)

        # Максимальная серия graphic_fallback (для контроля лимита 2/12с).
        max_run = 0
        max_run_dur = 0.0
        run = 0
        run_dur = 0.0
        for r in records:
            if r.get("graphic_fallback"):
                run += 1
                run_dur += float(r.get("scene_duration_s") or 0.0)
                max_run = max(max_run, run)
                max_run_dur = max(max_run_dur, run_dur)
            else:
                run = 0
                run_dur = 0.0

        times = {
            key: round(float(val or 0.0), 2)
            for key, val in (stage_times or {}).items()
        }
        calls_total = int(vlm_calls if vlm_calls_total is None else vlm_calls_total)
        summary = {
            "version": 1,
            "series": str(series or ""),
            "out_dir": str(self.out_dir),
            "scenes": int(total_scenes),
            "scenes_logged": len(records),
            "scenes_ge6": int(ge6),
            "avg_score": float(avg),
            "time_by_stage": times,
            "vlm_calls": calls_total,
            "vlm_per_scene": round(calls_total / max(1, int(total_scenes)), 3),
            "cache_hits": int(cache_hits),
            "fallback_counts": dict(sorted(fallback_counts.items())),
            "treated_scenes": sum(1 for r in records if r.get("treated")),
            "graphic_fallback": {
                "scenes": sum(1 for r in records if r.get("graphic_fallback")),
                "max_consecutive": max_run,
                "max_duration_s": round(max_run_dur, 2),
            },
            "ts": _utc_now(),
        }
        if extra:
            summary.update(extra)
        if self.enabled:
            try:
                _atomic_write_json(self.summary_path, summary)
            except Exception as e:
                self._warn(f"baseline: run_summary.json не пишется: {str(e)[:120]}")
            try:
                _atomic_write_lines(
                    self.jsonl_path,
                    (json.dumps(r, ensure_ascii=False) for r in records),
                )
            except Exception as e:
                self._warn(f"baseline: scenes.jsonl не консолидирован: {str(e)[:120]}")
        return summary


def _scene_duration(scene: dict) -> float:
    try:
        start = float(scene.get("start", 0.0) or 0.0)
        end = float(scene.get("end", start) or start)
        return max(0.0, end - start)
    except (TypeError, ValueError):
        return 0.0
