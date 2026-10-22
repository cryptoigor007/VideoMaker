"""P0-2: политика отбора клипа для сцены («2-4/10 не в кадр»).

Чистые функции + небольшие трекеры, без сети и ffmpeg — чтобы политику
можно было тестировать отдельно от пайплайна.

Таблица исходов (лучший балл VLM < 6):
* literal 5.x      → ``literal_crop`` (кроп/цвет применимы) иначе ``literal_proxy``;
* associative >=5  → ``associative`` (флаг treated=true);
* 2-4/10           → ``neutral`` если есть нейтральный кандидат
                      (город/природа/абстракция, mood >= 5),
                      иначе ``graphic`` (сцена без клипа);
* класс сцены (``scene_class`` из Gemini):
  - associative — 5.x сразу treated; при 2-4 сначала neutral (mood >= 4.5),
    затем слабый клип как ``proxy_forced`` (treated) охотнее, чем ``graphic``;
  - abstract — 5.x атмосферный/нейтральный клип → treated ``associative``;
    при 2-4 сначала neutral/атмосфера (mood >= 4.5), затем ``graphic``
    по бюджету; буквальные кандидаты не блокируются;
* score is None    → ``unscored`` (VLM без ключей: берём лучший pre-rank);
* score == -1      → кандидат НЕ проверен (``vlm_skipped``/ошибка кадров),
                     в выбор не попадает — только ``neutral``/``graphic``;
* нет кандидатов   → ``graphic`` (если позволяет бюджет), иначе ``proxy_forced``.

Повтор клипа ограничен: не более 2 появлений в любом окне 180с
(«повтор ≤1 раза за 3 минуты»). ``graphic`` — не более 2 подряд и не более
12с подряд; при превышении берётся слабый клип как ``proxy_forced``.

[B3] Diversity: клипы одной визуальной группы (cosine эмбеддингов >= 0.9
или совпадение тегов/имени) не идут более 2 подряд — похожий кандидат
уступает следующему. Шоты: длинная сцена (>6с) делится на 2-3 под-шота
по паузам word-level (>=0.25с) / границам слов.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:  # perf_history лёгкий и не тянет сеть; при сбое политика работает без него
    from . import perf_history as _perf_history
except Exception:  # pragma: no cover — защита от частичной установки
    _perf_history = None  # type: ignore[assignment]


GOOD_SCORE = 6
LITERAL_MIN = 5
ASSOCIATIVE_MIN = 5
NEUTRAL_MOOD_MIN = 5.0
NEUTRAL_MOOD_MIN_ASSOCIATIVE = 4.5
NEUTRAL_MOOD_MIN_ABSTRACT = 4.5

SCENE_CLASSES = ("literal", "associative", "abstract")
DEFAULT_SCENE_CLASS = "literal"
DEFAULT_SCENE_CONFIDENCE = 0.5

REPEAT_WINDOW_SEC = 180.0
REPEAT_MAX_IN_WINDOW = 2

# [B3] Diversity: не более 2 подряд клипов одной визуальной группы.
VISUAL_WINDOW_SEC = 180.0
VISUAL_SIM_THRESHOLD = 0.9
MAX_SAME_VISUAL_RUN = 2
_VISUAL_HISTORY_MAX = 240

# [B3] Декомпозиция длинной сцены на 2-3 шота по аудиоструктуре.
LONG_SCENE_SEC = 6.0
SHOT_PAUSE_SEC = 0.25
MAX_SHOTS_PER_SCENE = 3

GRAPHIC_MAX_CONSECUTIVE = 2
GRAPHIC_MAX_DURATION_SEC = 12.0

# Нейтральные темы: город / природа / абстракция (RU+EN).
_NEUTRAL_RE = re.compile(
    r"\b("
    r"city|urban|street|streets|skyline|traffic|building|buildings|architecture|"
    r"road|roads|bridge|metro|subway|town|downtown|"
    r"nature|forest|tree|trees|wood|woods|mountain|mountains|field|fields|"
    r"river|lake|sea|ocean|wave|waves|water|rain|snow|sky|cloud|clouds|"
    r"sunset|sunrise|beach|desert|landscape|horizon|"
    r"abstract|texture|pattern|background|gradient|light|lights|window|windows|"
    r"город|улица|улицы|здание|здания|архитектура|дорога|мост|метро|"
    r"природа|лес|дерево|деревья|гора|горы|поле|река|озеро|море|океан|"
    r"волна|волны|вода|дождь|снег|небо|облака|закат|рассвет|пляж|пустыня|"
    r"пейзаж|абстракция|текстура|узор|фон|свет|окно|окна"
    r")\b",
    re.IGNORECASE,
)

_LOW_HISTORY_BONUS = -0.9


def _as_score(value: Any) -> int | None:
    """Балл VLM 0..10; None для -1/None/мусора."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        # Не округляем вверх: 5.9 — это всё ещё «5.x», а не «6+».
        return int(value) if value >= 0 else None
    return None


def candidate_score(c: dict) -> int | None:
    return _as_score(c.get("score"))


def candidate_skipped(c: dict) -> bool:
    """Кандидат НЕ проверен VLM: ``score == -1`` (``vlm_skipped``/ошибка кадров).

    Такой клип нельзя ставить в кадр: -1 — это не «плохая оценка», а отсутствие
    проверки. Отличается от «нет оценки вообще» (score is None), когда Vision
    недоступен и допустим выбор по pre-rank.
    """
    if c.get("vlm_skipped"):
        return True
    raw = c.get("score")
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)):
        return float(raw) < 0
    return False


def candidate_mood(c: dict) -> float:
    """Настроение/качество кандидата для нейтральной альтернативы.

    VLM-балл — релевантность сцене, поэтому для нейтрального фона он не
    годится как «mood». Приоритет: mood_score → db_score (оценка базы) →
    VLM-балл. Так нейтральный клип базы с db_score >=5 остаётся доступным,
    даже если под конкретную сцену он получил 2-4.
    """
    vals: list[float] = []
    for key in ("mood_score", "db_score", "score"):
        v = c.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and float(v) >= 0:
            vals.append(float(v))
    return max(vals) if vals else 0.0


def candidate_path(c: dict) -> str:
    """Стабильный путь для clip_usage: realpath(library_file/file) → id."""
    cached = c.get("_usage_path")
    if isinstance(cached, str) and cached:
        return cached
    key = ""
    if _perf_history is not None:
        try:
            key = _perf_history.path_of(c)
        except Exception:
            key = ""
    if not key:
        for field in ("library_file", "file"):
            raw = str(c.get(field) or "").strip()
            if raw:
                try:
                    key = os.path.realpath(raw)
                except Exception:
                    key = raw
                break
    if not key:
        key = candidate_id(c)
    if key:
        c["_usage_path"] = key
    return key


def candidate_id(c: dict) -> str:
    kind = str(c.get("kind") or "")
    if kind == "lib":
        raw = str(c.get("file") or c.get("library_file") or "")
        if raw:
            return os.path.realpath(raw) if os.path.exists(raw) else raw
    provider = str(c.get("provider") or "stock")
    cid = str(c.get("id") or "")
    if cid:
        return f"stock:{provider}:{cid}"
    url = str(c.get("url") or "")
    if url:
        return f"stock:{provider}:{url.rsplit('/', 1)[-1]}"
    raw = str(c.get("file") or "")
    return raw or "unknown"


def _candidate_text(c: dict) -> str:
    tags = c.get("tags")
    if isinstance(tags, (list, tuple, set)):
        tags = " ".join(str(x) for x in tags)
    parts = [
        str(tags or ""),
        str(c.get("description") or ""),
        str(c.get("what") or ""),
        os.path.basename(str(c.get("file") or "")),
    ]
    return " ".join(p for p in parts if p)


def is_neutral(c: dict) -> bool:
    """Нейтральная тема: город / природа / абстракция (по тегам/описанию)."""
    return bool(_NEUTRAL_RE.search(_candidate_text(c)))


# ---------------------------------------------------------------------------
# [B3] Визуальные группы: похожесть кадров без нового сетевого вызова
# ---------------------------------------------------------------------------

_VISUAL_STOP = {
    "mp4", "mov", "webm", "video", "footage", "stock", "clip", "clips",
    "pixabay", "pexels", "coverr", "videvo", "library", "broll", "b_roll",
    "full", "hd", "uhd", "4k", "1080", "720", "2160", "3840", "1920",
    "the", "and", "with", "for", "from", "this", "that", "into", "over",
}
_EMB_CACHE: dict[str, list[float]] | None = None
_EMB_MTIME: float = -1.0
_EMB_LOAD_FAILED = False


def _visual_tokens(c: dict) -> set[str]:
    """Значимые токены клипа: теги/описание/what + имя файла без мусора."""
    tags = c.get("tags")
    if isinstance(tags, (list, tuple, set)):
        tags = " ".join(str(x) for x in tags)
    raw = " ".join([
        str(tags or ""),
        str(c.get("description") or ""),
        str(c.get("what") or ""),
        Path(str(c.get("file") or c.get("library_file") or c.get("source") or "")).stem,
    ])
    out = set()
    for tok in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}", raw.lower()):
        if tok.isdigit() or tok in _VISUAL_STOP:
            continue
        # Хвосты имён файлов вида lonely_person_window_40797_pixabay
        out.add(tok)
    return out


def _embeddings() -> dict[str, list[float]]:
    """Кэш визуальных эмбеддингов базы (файл может отсутствовать)."""
    global _EMB_CACHE, _EMB_MTIME, _EMB_LOAD_FAILED
    if _EMB_LOAD_FAILED:
        return {}
    path = Path.home() / "video_maker" / "broll_embeddings.json"
    try:
        mtime = path.stat().st_mtime
    except Exception:
        _EMB_LOAD_FAILED = True
        return {}
    if _EMB_CACHE is not None and abs(mtime - _EMB_MTIME) < 1e-6:
        return _EMB_CACHE
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        _EMB_CACHE = {
            str(k): [float(x) for x in v]
            for k, v in (data or {}).items()
            if isinstance(v, list) and v
        }
        _EMB_MTIME = mtime
        return _EMB_CACHE
    except Exception:
        _EMB_LOAD_FAILED = True
        _EMB_CACHE = {}
        return {}


def _clip_embedding(c: dict) -> list[float] | None:
    """Вектор клипа по пути (учитываем symlink/локальную копию пула)."""
    cached = c.get("_visual_emb")
    if isinstance(cached, list) or cached is None and "_visual_emb" in c:
        return cached if isinstance(cached, list) else None
    # Планировщик часто передаёт только путь — не грузим 60МБ эмбеддингов
    # ради сравнения имён файлов (токенов достаточно).
    if not (c.get("description") or c.get("tags") or c.get("what")):
        c["_visual_emb"] = None
        return None
    emb = _embeddings()
    if not emb:
        c["_visual_emb"] = None
        return None
    vec: list[float] | None = None
    for field in ("library_file", "file", "source", "verify_file"):
        raw = str(c.get(field) or "").strip()
        if not raw:
            continue
        variants = [raw]
        try:
            variants.append(os.path.realpath(raw))
        except Exception:
            pass
        for key in variants:
            v = emb.get(key)
            if isinstance(v, list) and v:
                vec = v
                break
        if vec:
            break
    c["_visual_emb"] = vec
    return vec


def visual_similarity(a: dict, b: dict) -> float:
    """Похожесть кадров 0..1: эмбеддинги (cosine) + совпадение тегов/имени.

    Эмбеддинги текстовые (описание/теги клипа) — дёшево и без Vision-вызова;
    если вектора нет, работает Jaccard значимых токенов.
    """
    pa = candidate_path(a)
    pb = candidate_path(b)
    if pa and pa == pb:
        return 1.0
    sa = str(a.get("source_url") or "")
    sb = str(b.get("source_url") or "")
    if sa and sa == sb:
        return 1.0
    va = _clip_embedding(a)
    vb = _clip_embedding(b)
    cos = 0.0
    if va and vb and len(va) == len(vb):
        try:
            dot = sum(x * y for x, y in zip(va, vb))
            na = math.sqrt(sum(x * x for x in va))
            nb = math.sqrt(sum(y * y for y in vb))
            cos = dot / (na * nb + 1e-9)
        except Exception:
            cos = 0.0
    ta = _visual_tokens(a)
    tb = _visual_tokens(b)
    if ta and tb:
        jac = len(ta & tb) / max(1, len(ta | tb))
    else:
        jac = 0.0
    return max(0.0, min(1.0, max(cos, jac)))


def same_visual_group(a: dict, b: dict,
                      threshold: float = VISUAL_SIM_THRESHOLD) -> bool:
    """Одна визуальная группа: явная метка, общий источник или closeness."""
    ga = str(a.get("visual_group") or a.get("visual_family") or "")
    gb = str(b.get("visual_group") or b.get("visual_family") or "")
    if ga and gb and ga == gb:
        return True
    return visual_similarity(a, b) >= float(threshold)


def _visual_memory(c: dict) -> dict:
    """Компактная копия кандидата для истории (без тяжёлых полей)."""
    keep = ("tags", "description", "what", "file", "library_file", "source",
            "source_url", "url", "visual_group", "visual_family", "provider", "id")
    out = {k: c.get(k) for k in keep if c.get(k) is not None}
    if "_visual_emb" in c:
        out["_visual_emb"] = c.get("_visual_emb")
    return out


class VisualDiversityTracker:
    """История визуальных групп: не более ``max_run`` похожих подряд.

    Дополнительно к жёсткому правилу «2 подряд» считает число похожих
    появлений в окне 3 минуты — это мягкий штраф для ранжирования.
    """

    def __init__(self, window: float = VISUAL_WINDOW_SEC,
                 max_run: int = MAX_SAME_VISUAL_RUN,
                 threshold: float = VISUAL_SIM_THRESHOLD):
        self.window = float(window)
        self.max_run = max(1, int(max_run))
        self.threshold = float(threshold)
        self.entries: list[dict] = []

    def _recent(self, at: float) -> list[dict]:
        lo = float(at) - self.window
        return [e for e in self.entries if float(e.get("at", 0.0)) >= lo]

    def run_length(self, c: dict, at: float) -> int:
        """Сколько похожих клипов уже идёт подряд перед этим кандидатом."""
        run = 0
        for e in reversed(self._recent(at)):
            if same_visual_group(c, e["cand"], self.threshold):
                run += 1
            else:
                break
        return run

    def count_in_window(self, c: dict, at: float) -> int:
        return sum(
            1 for e in self._recent(at)
            if same_visual_group(c, e["cand"], self.threshold)
        )

    def would_violate(self, c: dict, at: float) -> bool:
        return self.run_length(c, at) >= self.max_run

    def penalty(self, c: dict, at: float) -> float:
        """Мягкий штраф 0..1 за частый визуальный тип в окне 3 минуты."""
        recent = self._recent(at)
        if not recent:
            return 0.0
        same = sum(1 for e in recent if same_visual_group(c, e["cand"], self.threshold))
        return min(1.0, same / max(1.0, float(self.max_run) * 2.0))

    def record(self, c: dict, at: float) -> None:
        if not isinstance(c, dict):
            return
        self.entries.append({"at": float(at), "cand": _visual_memory(c)})
        if len(self.entries) > _VISUAL_HISTORY_MAX:
            self.entries = self.entries[-_VISUAL_HISTORY_MAX:]
        lo = float(at) - self.window * 1.5
        self.entries = [e for e in self.entries if float(e.get("at", 0.0)) >= lo]

    def record_gap(self, at: float) -> None:
        """Сбросить серию похожих (graphic/пустая сцена между клипами)."""
        self.entries.append({"at": float(at), "cand": {"visual_group": "__gap__"}})
        if len(self.entries) > _VISUAL_HISTORY_MAX:
            self.entries = self.entries[-_VISUAL_HISTORY_MAX:]

    def reset(self) -> None:
        self.entries = []


def diversify_candidates(
    cands: Sequence[dict],
    *,
    at: float = 0.0,
    tracker: VisualDiversityTracker | None = None,
    log=None,
    scene_idx: int | None = None,
    keep_good_floor: bool = False,
) -> list[dict]:
    """Переставить кандидатов так, чтобы не было 3 похожих подряд.

    Блокированные кандидаты не выбрасываются — уходят в конец (лучше повтор,
    чем дыра в кадре). ``keep_good_floor`` не даёт заменить хороший клип
    слабым: если среди альтернатив нет ни одного >= GOOD_SCORE, порядок
    сохраняется. Лог пишется только если заменён ЛУЧШИЙ кандидат.
    """
    items = list(cands or [])
    if not items or tracker is None:
        return items
    if len(tracker.entries) < tracker.max_run:
        return items
    out: list[dict] = []
    deferred: list[dict] = []
    first_blocked = -1
    for i, c in enumerate(items):
        if tracker.would_violate(c, at):
            deferred.append(c)
            if first_blocked < 0:
                first_blocked = i
        else:
            out.append(c)
    if not deferred:
        return items
    if keep_good_floor:
        def _good(c: dict) -> bool:
            sc = candidate_score(c)
            return sc is not None and sc >= GOOD_SCORE
        if any(_good(c) for c in deferred) and not any(_good(c) for c in out):
            return items
    if out and first_blocked == 0 and log is not None:
        try:
            n = f" — сцена {int(scene_idx) + 1}" if scene_idx is not None else ""
            log(f"[SB] diversity: заменён клип (похож на предыдущий){n}")
        except Exception:
            pass
    return out + deferred


# ---------------------------------------------------------------------------
# [B3] Шоты сцены по аудиоструктуре (паузы / границы слов)
# ---------------------------------------------------------------------------

def transcript_units(segments: Sequence[dict] | None) -> list[dict]:
    """Слова (или сегменты, если word-level нет) как единый таймлайн."""
    units: list[dict] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        words = seg.get("words")
        if isinstance(words, list) and words:
            for w in words:
                if not isinstance(w, dict):
                    continue
                try:
                    ws = float(w.get("start", seg.get("start", 0.0)) or 0.0)
                    we = float(w.get("end", ws + 0.25) or (ws + 0.25))
                except (TypeError, ValueError):
                    continue
                text = str(w.get("word") or w.get("text") or "").strip()
                if we <= ws:
                    we = ws + 0.15
                units.append({"start": ws, "end": we, "text": text})
        else:
            try:
                ws = float(seg.get("start", 0.0) or 0.0)
                we = float(seg.get("end", ws) or ws)
            except (TypeError, ValueError):
                continue
            if we <= ws:
                we = ws + 0.2
            units.append({"start": ws, "end": we,
                          "text": str(seg.get("text") or "").strip()})
    units.sort(key=lambda u: u["start"])
    return units


def units_in_window(units: Sequence[dict], start: float, end: float) -> list[dict]:
    return [
        u for u in (units or [])
        if float(u.get("end", 0.0)) > float(start) + 1e-3
        and float(u.get("start", 0.0)) < float(end) - 1e-3
    ]


def _shot_cut_candidates(units: Sequence[dict], start: float, end: float,
                         min_shot_sec: float,
                         pause_sec: float = SHOT_PAUSE_SEC) -> list[dict]:
    """Границы резов: пауза >=0.25с приоритетнее, затем пунктуация/акцент."""
    cuts: list[dict] = []
    for i in range(1, len(units)):
        prev, cur = units[i - 1], units[i]
        try:
            pos = float(cur.get("start", 0.0))
            gap = pos - float(prev.get("end", pos))
        except (TypeError, ValueError):
            continue
        if pos <= start + min_shot_sec - 1e-6 or pos >= end - min_shot_sec + 1e-6:
            continue
        prev_text = str(prev.get("text") or "").strip()
        sentence = prev_text.endswith((".", "!", "?", "…"))
        word_len = len(re.sub(r"[^a-zA-Zа-яА-ЯёЁ]", "", str(cur.get("text") or "")))
        if gap >= float(pause_sec):
            kind, bonus = "pause", 0.55
        elif sentence and gap >= 0.04:
            kind, bonus = "sentence", 0.35
        elif word_len >= 6 and gap >= 0.08:
            kind, bonus = "accent", 0.18
        else:
            # Сплошная речь без пауз: рез допустим по началу слова
            # (ниже паузы по приоритету), но не посреди слова.
            kind, bonus = "word", 0.0
        cuts.append({"pos": pos, "kind": kind, "gap": max(0.0, gap), "bonus": bonus})
    cuts.sort(key=lambda c: c["pos"])
    deduped: list[dict] = []
    for c in cuts:
        if deduped and abs(c["pos"] - deduped[-1]["pos"]) < 0.05:
            if c["bonus"] > deduped[-1]["bonus"]:
                deduped[-1] = c
            continue
        deduped.append(c)
    return deduped


def _place_shot_cuts(cuts: Sequence[dict], start: float, end: float,
                     n_shots: int, min_shot_sec: float) -> list[dict]:
    """Жадно выбрать n-1 резов около идеальных долей, уважая min_shot_sec."""
    dur = max(0.05, end - start)
    chosen: list[dict] = []
    for j in range(1, n_shots):
        lo = (chosen[-1]["pos"] if chosen else start) + min_shot_sec
        hi = end - min_shot_sec * (n_shots - j)
        if hi < lo - 1e-6:
            return []
        ideal = start + dur * j / n_shots
        pool = [
            c for c in cuts
            if lo - 1e-6 <= c["pos"] <= hi + 1e-6
            and all(abs(c["pos"] - x["pos"]) > 0.05 for x in chosen)
        ]
        if not pool:
            return []
        best = max(
            pool,
            key=lambda c: (
                -abs(c["pos"] - ideal) / max(1.0, dur) * 2.6
                + min(0.8, float(c["gap"])) * 0.5
                + float(c["bonus"])
            ),
        )
        chosen.append(best)
    chosen.sort(key=lambda c: c["pos"])
    return chosen


def split_scene_into_shots(
    start: float,
    end: float,
    units: Sequence[dict] | None = None,
    *,
    max_shots: int = MAX_SHOTS_PER_SCENE,
    min_shot_sec: float = 2.0,
    long_scene_sec: float = LONG_SCENE_SEC,
    pause_sec: float = SHOT_PAUSE_SEC,
) -> list[dict]:
    """2-3 шота для длинной сцены; короткая фраза остаётся одним шотом.

    Резы — по паузам word-level (>= ``pause_sec``), затем по границам
    предложений/ударным словам; при отсутствии пауз — по началу слова
    около идеальной доли (кадр не рвётся посреди слова).
    """
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return [{"start": 0.0, "end": 0.05, "index": 0}]
    if end <= start + 0.05:
        return [{"start": start, "end": max(start + 0.05, end), "index": 0}]
    duration = end - start
    if duration <= float(long_scene_sec) + 1e-6:
        return [{"start": start, "end": end, "index": 0}]
    hard_min = max(1.0, min(6.0, float(min_shot_sec or 2.0)))
    n_cap = min(int(max_shots or MAX_SHOTS_PER_SCENE),
                max(1, int(duration // hard_min)))
    if n_cap < 2:
        return [{"start": start, "end": end, "index": 0}]
    cuts = _shot_cut_candidates(units or [], start, end, hard_min, pause_sec)
    fallback: list[dict] | None = None
    for n in range(n_cap, 1, -1):
        placed = _place_shot_cuts(cuts, start, end, n, hard_min)
        if len(placed) != n - 1:
            continue
        bounds = [start] + [c["pos"] for c in placed] + [end]
        shots = [
            {"start": round(bounds[i], 3), "end": round(bounds[i + 1], 3),
             "index": i}
            for i in range(n)
        ]
        if not all(s["end"] - s["start"] >= hard_min - 1e-6 for s in shots):
            continue
        # Паузы/границы предложений важнее «любого слова»: если на n шотов
        # пауз не хватает, лучше 2 шота по паузам, чем 3 с резом вслепую.
        if all(c["kind"] in ("pause", "sentence") for c in placed):
            return shots
        if fallback is None:
            fallback = shots
    if fallback is not None:
        return fallback
    return [{"start": start, "end": end, "index": 0}]


def scene_shot_windows(
    windows: Sequence[tuple[float, float]],
    segments: Sequence[dict] | None,
    *,
    max_shots: int = MAX_SHOTS_PER_SCENE,
    min_shot_sec: float = 2.0,
    long_scene_sec: float = LONG_SCENE_SEC,
) -> list[list[dict]]:
    """Окон раз сцен → список шотов каждой сцены (по общему транскрипту)."""
    units = transcript_units(segments)
    out: list[list[dict]] = []
    for ws, we in windows or []:
        out.append(split_scene_into_shots(
            ws, we, units_in_window(units, ws, we),
            max_shots=max_shots, min_shot_sec=min_shot_sec,
            long_scene_sec=long_scene_sec,
        ))
    return out


def scene_class_of(scene: dict) -> str:
    """Класс сцены: literal | associative | abstract (дефолт literal)."""
    s = str((scene or {}).get("scene_class") or "").strip().lower()
    return s if s in SCENE_CLASSES else DEFAULT_SCENE_CLASS


def scene_confidence_of(scene: dict) -> float:
    """Confidence сцены 0..1 (дефолт 0.5); в политике пока не участвует."""
    raw = (scene or {}).get("confidence")
    if isinstance(raw, bool) or raw is None:
        return DEFAULT_SCENE_CONFIDENCE
    try:
        v = float(str(raw).replace(",", ".").strip())
    except (TypeError, ValueError):
        return DEFAULT_SCENE_CONFIDENCE
    if v != v or v in (float("inf"), float("-inf")):
        return DEFAULT_SCENE_CONFIDENCE
    if v > 1.0:
        v = v / 100.0
    return min(1.0, max(0.0, v))


def match_mode(c: dict, scene: dict) -> str:
    """``literal`` или ``associative`` — по лексическому совпадению тегов.

    VLM возвращает literal_action, но smart_broll его не сохраняет (verify.py
    вне зоны патча). Поэтому используем консервативное приближение: если в
    тегах/описании клипа встретилось >=2 значимых слова запроса — literal,
    иначе считаем ассоциативным.
    """
    queries = " ".join(str(q) for q in (scene.get("queries") or []) if str(q).strip())
    if not queries:
        return "associative"
    try:
        from .stock import tag_relevance

        hits = int(tag_relevance(_candidate_text(c), queries))
    except Exception:
        hits = 0
    return "literal" if hits >= 2 else "associative"


def crop_color_applicable(c: dict, orientation: str = "wide") -> bool:
    """Можно ли вылечить кропом/цветом без апскейла.

    Требуем реальные размеры и достаточный запас по целевой ориентации.
    Нет размеров (или клип меньше 720p) → кроп не применяем, идём в proxy.
    """
    try:
        w = float(c.get("width") or 0)
        h = float(c.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if w < 720 or h < 720:
        return False
    if str(orientation).lower() in ("wide", "16:9", "horizontal"):
        return w >= 1280 and h >= 720 and (w / max(1.0, h)) >= 1.5
    return h >= 1280 and w >= 720 and (h / max(1.0, w)) >= 1.5


def _history_low(c: dict) -> bool:
    """Негативный clip_perf: pre-rank уже посчитал бонус истории."""
    try:
        return float(c.get("_pre_hist") or 0.0) <= _LOW_HISTORY_BONUS
    except (TypeError, ValueError):
        return False


@dataclass
class SceneDecision:
    candidates: list[dict] = field(default_factory=list)
    primary: dict | None = None
    fallback: str = "none"
    treated: bool = False
    graphic: bool = False
    best_score: int | None = None
    primary_score: int | None = None
    reason: str = ""
    flags: dict[str, Any] = field(default_factory=dict)


class ClipUsageTracker:
    """clip_usage: не более ``max_in_window`` появлений клипа за ``window`` секунд."""

    def __init__(self, window: float = REPEAT_WINDOW_SEC,
                 max_in_window: int = REPEAT_MAX_IN_WINDOW):
        self.window = float(window)
        self.max_in_window = max(1, int(max_in_window))
        self._uses: dict[str, list[float]] = {}

    def _recent(self, key: str, at: float) -> list[float]:
        vals = self._uses.get(key) or []
        lo = float(at) - self.window
        return [t for t in vals if t >= lo]

    def blocked(self, c: dict, at: float) -> bool:
        key = candidate_path(c)
        if not key:
            return False
        return len(self._recent(key, at)) >= self.max_in_window

    def record(self, c: dict, at: float) -> None:
        key = candidate_path(c)
        if not key:
            return
        vals = self._recent(key, at)
        vals.append(float(at))
        vals.sort()
        self._uses[key] = vals[-self.max_in_window * 4:]

    def count(self, c: dict, at: float) -> int:
        key = candidate_path(c)
        return len(self._recent(key, at)) if key else 0


class GraphicFallbackBudget:
    """``graphic_fallback`` ≤2 подряд и ≤12с подряд."""

    def __init__(self, max_consecutive: int = GRAPHIC_MAX_CONSECUTIVE,
                 max_duration: float = GRAPHIC_MAX_DURATION_SEC):
        self.max_consecutive = max(1, int(max_consecutive))
        self.max_duration = float(max_duration)
        self.consecutive = 0
        self.duration = 0.0

    def can_use(self, duration: float) -> bool:
        d = max(0.0, float(duration or 0.0))
        return (
            self.consecutive < self.max_consecutive
            and (self.duration + d) <= self.max_duration + 1e-6
        )

    def record(self, duration: float) -> None:
        self.consecutive += 1
        self.duration += max(0.0, float(duration or 0.0))

    def reset(self) -> None:
        self.consecutive = 0
        self.duration = 0.0


def _scene_start(scene: dict) -> float:
    try:
        return float(scene.get("start", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _scene_duration(scene: dict) -> float:
    try:
        start = float(scene.get("start", 0.0) or 0.0)
        end = float(scene.get("end", start) or start)
        return max(0.0, end - start)
    except (TypeError, ValueError):
        return 0.0


def _fmt_score(score: int | None) -> str:
    return str(score) if isinstance(score, int) else "-"


def _by_score(cands: list[dict], *, at: float | None = None,
              diversity: VisualDiversityTracker | None = None) -> list[dict]:
    """Порядок пула: VLM-балл, затем источник.

    [B3] Мягкий штраф diversity (до 0.25 балла) за частый визуальный тип
    в окне 3 минуты — не меняет пороги 5/6, только порядок равных/близких.
    """
    penalties: dict[int, float] = {}
    if diversity is not None and at is not None:
        for c in cands:
            try:
                penalties[id(c)] = diversity.penalty(c, float(at))
            except Exception:
                penalties[id(c)] = 0.0

    def _key(c: dict) -> tuple:
        raw = candidate_score(c)
        sc = raw if raw is not None else -1
        return (
            -(float(sc) - 0.25 * float(penalties.get(id(c), 0.0))),
            0 if str(c.get("kind") or "") == "lib" else 1,
        )

    # Порядок пула уже ранжирован VLM; сортировка стабильна и не ломает ties.
    return sorted(cands, key=_key)


def _neutral_pick(cands: list[dict], min_mood: float = NEUTRAL_MOOD_MIN) -> dict | None:
    neutral = [
        c for c in cands
        if is_neutral(c) and candidate_mood(c) >= min_mood
    ]
    if not neutral:
        return None
    neutral.sort(
        key=lambda c: (
            -(candidate_score(c) if candidate_score(c) is not None else -1),
            -candidate_mood(c),
            0 if str(c.get("kind") or "") == "lib" else 1,
        )
    )
    return neutral[0]


def select_scene(
    cands: list[dict],
    scene: dict,
    *,
    per_scene: int = 1,
    orientation: str = "wide",
    usage: ClipUsageTracker | None = None,
    graphic_budget: GraphicFallbackBudget | None = None,
    diversity: VisualDiversityTracker | None = None,
    log=None,
) -> SceneDecision:
    """Выбрать клип(ы) сцены по таблице fallback-политики.

    Возвращает решение; ``candidates`` — то, что нужно скачать/связать
    (primary первым, затем безопасные доборы при per_scene>1).
    """
    usage = usage or ClipUsageTracker()
    budget = graphic_budget or GraphicFallbackBudget()
    at = _scene_start(scene)
    duration = _scene_duration(scene)
    ordered = _by_score(
        [c for c in (cands or []) if isinstance(c, dict)],
        at=at, diversity=diversity,
    )
    # score == -1 — «не проверен» (vlm_skipped/ошибка), а не оценка:
    # такой кандидат не пригоден ни для primary, ни для доборов/proxy.
    evaluated = [c for c in ordered if not candidate_skipped(c)]

    scores = [candidate_score(c) for c in evaluated]
    best_score = max((s for s in scores if s is not None), default=None)

    # Повторы и негативная история: если есть альтернативы — отбрасываем.
    usable = [c for c in evaluated if not usage.blocked(c, at)]
    not_low = [c for c in usable if not _history_low(c)]
    if not_low:
        usable = not_low
    # [B3] Diversity: похожий на два предыдущих клип уходит в конец.
    usable = diversify_candidates(
        usable, at=at, tracker=diversity, log=log,
        scene_idx=scene.get("idx") if isinstance(scene, dict) else None,
        keep_good_floor=True,
    )
    best = usable[0] if usable else None

    def _pick(c: dict | None, fallback: str, treated: bool, reason: str,
              flags: dict | None = None) -> SceneDecision:
        picks: list[dict] = []
        if c is not None:
            picks.append(c)
            usage.record(c, at)
            if diversity is not None:
                diversity.record(c, at)
            budget.reset()
        primary_score = candidate_score(c) if c is not None else None
        return SceneDecision(
            candidates=picks, primary=c, fallback=fallback, treated=bool(treated),
            graphic=False, best_score=best_score, primary_score=primary_score,
            reason=reason, flags=dict(flags or {}),
        )

    # 1) Хороший клип: 6+ — берём как есть (и добираем хорошие при per_scene>1).
    if best is not None and (candidate_score(best) or 0) >= GOOD_SCORE:
        picks = [best]
        usage.record(best, at)
        if diversity is not None:
            diversity.record(best, at)
        budget.reset()
        for c in usable:
            if len(picks) >= max(1, int(per_scene)):
                break
            if c is best:
                continue
            sc = candidate_score(c)
            if sc is not None and sc >= GOOD_SCORE:
                picks.append(c)
                usage.record(c, at)
                if diversity is not None:
                    diversity.record(c, at)
        return SceneDecision(
            candidates=picks, primary=best, fallback="none", treated=False,
            graphic=False, best_score=best_score, primary_score=candidate_score(best),
            reason="good score", flags={},
        )

    _cls = scene_class_of(scene)
    _neutral_min = {
        "associative": NEUTRAL_MOOD_MIN_ASSOCIATIVE,
        "abstract": NEUTRAL_MOOD_MIN_ABSTRACT,
    }.get(_cls, NEUTRAL_MOOD_MIN)

    # 2) 5.x: literal → кроп/цвет или proxy; associative → treated.
    if best is not None and (candidate_score(best) or 0) >= LITERAL_MIN:
        mode = match_mode(best, scene)
        # [A2] Класс сцены важнее лексического совпадения: ассоциативная сцена
        # с «буквальным» клипом всё равно требует mood-обработки, а abstract
        # предпочитает атмосферу (буквальный клип при этом не блокируется).
        if _cls == "associative":
            return _pick(best, "associative", True,
                         "associative-сцена >=5: mood/лёгкая обработка",
                         {"treated": True, "light_treatment": "associative",
                          "scene_class": _cls})
        if _cls == "abstract" and is_neutral(best):
            return _pick(best, "associative", True,
                         "abstract >=5: атмосферный клип",
                         {"treated": True, "light_treatment": "associative",
                          "atmosphere": True, "scene_class": _cls})
        if mode == "associative":
            return _pick(best, "associative", True,
                         "associative >=5: лёгкая обработка",
                         {"treated": True, "light_treatment": "associative"})
        if crop_color_applicable(best, orientation):
            return _pick(best, "literal_crop", True,
                         "literal 5.x: кроп/цвет применимы",
                         {"treated": True, "crop": True, "color": True})
        return _pick(best, "literal_proxy", True,
                     "literal 5.x: кроп неприменим → proxy",
                     {"treated": True, "proxy": True})

    # 3) 2-4 или ниже: сначала нейтральная альтернатива, затем graphic.
    if best is not None and best_score is None:
        # Vision без ключей/оценок: не блокируем B-roll, берём лучший pre-rank.
        return _pick(best, "unscored", False,
                     "VLM недоступен — выбран лучший по pre-rank", {})
    neutral = _neutral_pick(usable, min_mood=_neutral_min)
    if neutral is not None:
        reason = (
            f"слабый лучший {_fmt_score(best_score)}/10 — нейтральный фон "
            f"(mood {candidate_mood(neutral):.1f})"
        )
        decision = _pick(neutral, "neutral", False, reason,
                         {"neutral": True})
        decision.best_score = best_score
        return decision

    # [A2] Ассоциативная сцена: слабый клип под mood лучше дыры/graphic —
    # отдаём его treated (proxy), как при исчерпанном graphic-бюджете.
    if _cls == "associative" and best is not None:
        return _pick(best, "proxy_forced", True,
                     f"associative-сцена: слабый {_fmt_score(best_score)}/10 — "
                     f"treated вместо graphic",
                     {"treated": True, "proxy": True, "scene_class": _cls})

    if best is None:
        # Все кандидаты отсеяны повторами/историей: если это хороший клип,
        # лучше показать его повторно, чем оставить дыру; иначе — graphic.
        top = evaluated[0] if evaluated else None
        skipped_only = bool(ordered) and not evaluated
        top_score = candidate_score(top) if top is not None else None
        if top is not None and top_score is not None and top_score >= GOOD_SCORE:
            return _pick(top, "repeat_forced", True,
                         "все альтернативы отсеяны повтором/историей",
                         {"treated": True, "repeat_forced": True})
        if budget.can_use(duration):
            budget.record(duration)
            if diversity is not None:
                diversity.record_gap(at)
            return SceneDecision(
                candidates=[], primary=None, fallback="graphic", treated=False,
                graphic=True, best_score=best_score, primary_score=None,
                reason=("все кандидаты не проверены VLM (skipped) — нет пригодного клипа"
                        if skipped_only else "нет доступных кандидатов"),
                flags={"graphic_fallback": True,
                       **({"vlm_skipped_only": True} if skipped_only else {})},
            )
        if top is not None:
            return _pick(top, "proxy_forced", True,
                         "graphic-лимит исчерпан: слабый клип как proxy",
                         {"treated": True, "proxy": True, "graphic_limit": True})
        budget.record(duration)  # выбора нет — фиксируем нарушение бюджета в логе
        if diversity is not None:
            diversity.record_gap(at)
        return SceneDecision(
            candidates=[], primary=None, fallback="graphic", treated=False,
            graphic=True, best_score=best_score, primary_score=None,
            reason=("нет кандидатов (все skipped, graphic-лимит превышен)"
                    if skipped_only else "нет кандидатов (graphic-лимит превышен)"),
            flags={"graphic_fallback": True, "graphic_limit_exceeded": True,
                   **({"vlm_skipped_only": True} if skipped_only else {})},
        )

    if budget.can_use(duration):
        budget.record(duration)
        if diversity is not None:
            diversity.record_gap(at)
        return SceneDecision(
            candidates=[], primary=None, fallback="graphic", treated=False,
            graphic=True, best_score=best_score, primary_score=None,
            reason=f"лучший {_fmt_score(best_score)}/10 — нет пригодного клипа",
            flags={"graphic_fallback": True},
        )

    # Бюджет graphic исчерпан: не ставим 2-4 полным кадром, но и дыру не
    # оставляем — слабый клип идёт как proxy (помечается treated).
    return _pick(best, "proxy_forced", True,
                 f"graphic-лимит исчерпан (лучший {_fmt_score(best_score)}/10)",
                 {"treated": True, "proxy": True, "graphic_limit": True})


def log_decision(log, scene: dict, decision: SceneDecision) -> None:
    """Единый формат P0-2: ``[SB] сцена N: fallback=<тип> (лучший X/10)``."""
    if log is None:
        return
    try:
        n = int(scene.get("idx", 0)) + 1
        log(f"[SB] сцена {n}: fallback={decision.fallback} "
            f"(лучший {_fmt_score(decision.best_score)}/10)")
        if decision.flags.get("graphic_limit_exceeded"):
            log(f"[SB] сцена {n}: graphic_fallback превысил лимит "
                f"(≤{GRAPHIC_MAX_CONSECUTIVE} подряд / ≤{GRAPHIC_MAX_DURATION_SEC:.0f}с) — "
                f"принудительный {decision.fallback}")
    except Exception:
        pass
