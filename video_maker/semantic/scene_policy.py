"""P0-2: политика отбора клипа для сцены («2-4/10 не в кадр»).

Чистые функции + небольшие трекеры, без сети и ffmpeg — чтобы политику
можно было тестировать отдельно от пайплайна.

Таблица исходов (лучший балл VLM < 6):
* literal 5.x      → ``literal_crop`` (кроп/цвет применимы) иначе ``literal_proxy``;
* associative >=5  → ``associative`` (флаг treated=true);
* 2-4/10           → ``neutral`` если есть нейтральный кандидат
                     (город/природа/абстракция, mood >= 5),
                     иначе ``graphic`` (сцена без клипа);
* score is None    → ``unscored`` (VLM без ключей: берём лучший pre-rank);
* score == -1      → кандидат НЕ проверен (``vlm_skipped``/ошибка кадров),
                     в выбор не попадает — только ``neutral``/``graphic``;
* нет кандидатов   → ``graphic`` (если позволяет бюджет), иначе ``proxy_forced``.

Повтор клипа ограничен: не более 2 появлений в любом окне 180с
(«повтор ≤1 раза за 3 минуты»). ``graphic`` — не более 2 подряд и не более
12с подряд; при превышении берётся слабый клип как ``proxy_forced``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

try:  # perf_history лёгкий и не тянет сеть; при сбое политика работает без него
    from . import perf_history as _perf_history
except Exception:  # pragma: no cover — защита от частичной установки
    _perf_history = None  # type: ignore[assignment]


GOOD_SCORE = 6
LITERAL_MIN = 5
ASSOCIATIVE_MIN = 5
NEUTRAL_MOOD_MIN = 5.0

REPEAT_WINDOW_SEC = 180.0
REPEAT_MAX_IN_WINDOW = 2

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


def _by_score(cands: list[dict]) -> list[dict]:
    # Порядок пула уже ранжирован VLM; сортировка стабильна и не ломает ties.
    return sorted(
        cands,
        key=lambda c: (
            -(candidate_score(c) if candidate_score(c) is not None else -1),
            0 if str(c.get("kind") or "") == "lib" else 1,
        ),
    )


def _neutral_pick(cands: list[dict]) -> dict | None:
    neutral = [
        c for c in cands
        if is_neutral(c) and candidate_mood(c) >= NEUTRAL_MOOD_MIN
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
    ordered = _by_score([c for c in (cands or []) if isinstance(c, dict)])
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
    best = usable[0] if usable else None

    def _pick(c: dict | None, fallback: str, treated: bool, reason: str,
              flags: dict | None = None) -> SceneDecision:
        picks: list[dict] = []
        if c is not None:
            picks.append(c)
            usage.record(c, at)
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
        return SceneDecision(
            candidates=picks, primary=best, fallback="none", treated=False,
            graphic=False, best_score=best_score, primary_score=candidate_score(best),
            reason="good score", flags={},
        )

    # 2) 5.x: literal → кроп/цвет или proxy; associative → treated.
    if best is not None and (candidate_score(best) or 0) >= LITERAL_MIN:
        mode = match_mode(best, scene)
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
    neutral = _neutral_pick(usable)
    if neutral is not None:
        reason = (
            f"слабый лучший {_fmt_score(best_score)}/10 — нейтральный фон "
            f"(mood {candidate_mood(neutral):.1f})"
        )
        decision = _pick(neutral, "neutral", False, reason,
                         {"neutral": True})
        decision.best_score = best_score
        return decision

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
