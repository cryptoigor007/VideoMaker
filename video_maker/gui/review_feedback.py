"""Обратная связь по подбору B-roll для обучения базы.

Модуль намеренно не импортирует Tk: поиск/чтение сцен, фильтрация, запись
отзывов и экспорт — чистые файловые функции. Благодаря этому панель
«Проверка подбора» тестируется без запуска GUI.

Файлы:

* ``scenes.jsonl`` — вход, его пишет ``SceneBaseline`` (одна сцена на строку);
* ``review_feedback.jsonl`` — выход, лежит **рядом** со ``scenes.jsonl``
  (то есть в ``<output>/broll_smart/<series>/``) и дублируется в
  ``~/video_maker/review/review_feedback.jsonl``. Архивная копия переживает
  очистку папки прогона: ``smart_broll`` удаляет ``<series>/`` перед сборкой
  с нуля, а накопленные отзывы нужны для обучения/ledger.

Формат записи отзыва::

    ts, series, scene_id, scene_text, selected_clip, score, fallback,
    reason, source="ui"

где ``reason`` — одно из :data:`REASONS`.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

__all__ = [
    "FEEDBACK_FILENAME",
    "LOW_SCORE_THRESHOLD",
    "REASONS",
    "REASON_LABELS",
    "REVIEW_DIR",
    "SOURCE_UI",
    "append_feedback",
    "clip_name",
    "collect_all_feedback",
    "export_feedback",
    "feedback_index",
    "feedback_path_for",
    "filter_scene_rows",
    "find_latest_scenes",
    "find_scenes_files",
    "load_feedback",
    "load_review_config",
    "load_scene_rows",
    "load_scenes",
    "normalize_reason",
    "save_review_config",
    "scene_index",
    "scene_row",
    "series_for",
    "shorten",
]

REASONS = (
    "wrong_action",
    "wrong_atmosphere",
    "low_quality",
    "repeat/wrong_scene",
)

REASON_LABELS = {
    "wrong_action": "не то действие",
    "wrong_atmosphere": "не та атмосфера",
    "low_quality": "низкое качество",
    "repeat/wrong_scene": "повтор / не та сцена",
}

#: Синонимы, которые может прислать API/старый клиент.
_REASON_ALIASES = {
    "wrong_scene": "repeat/wrong_scene",
    "repeat": "repeat/wrong_scene",
    "repeat_wrong_scene": "repeat/wrong_scene",
    "low-quality": "low_quality",
    "wrong-action": "wrong_action",
    "wrong-atmosphere": "wrong_atmosphere",
}

FEEDBACK_FILENAME = "review_feedback.jsonl"
CONFIG_FILENAME = "config.json"
SOURCE_UI = "ui"
LOW_SCORE_THRESHOLD = 6

#: Куда кладём feedback/конфиг, пока не известен scenes.jsonl.
REVIEW_DIR = Path(os.path.expanduser("~/video_maker/review"))

_EMPTY_INDEX = 10 ** 9


# ─── низкоуровневые чтение/запись ────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: str | Path | None) -> list[dict]:
    """Прочитать JSONL, молча пропуская битые/пустые строки."""
    if not path:
        return []
    try:
        handle = Path(path).expanduser().open("r", encoding="utf-8", errors="ignore")
    except OSError:
        return []
    records: list[dict] = []
    try:
        with handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        return records
    return records


def _write_jsonl_atomic(path: Path, records: Iterable[Mapping[str, Any]]) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            for rec in records:
                handle.write(json.dumps(dict(rec), ensure_ascii=False) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(dict(data), handle, ensure_ascii=False, indent=2)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def _append_jsonl_line(path: Path, record: Mapping[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def _safe_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


# ─── сцены ───────────────────────────────────────────────────────────────

def scene_id(rec: Mapping[str, Any]) -> str:
    """Идентификатор сцены из записи (``scene_000`` и т.п.)."""
    sid = str(rec.get("scene_id") or "").strip()
    if sid:
        return sid
    idx = rec.get("scene_index")
    if isinstance(idx, int) and not isinstance(idx, bool):
        return f"scene_{idx:03d}"
    return ""


def scene_index(rec: Mapping[str, Any]) -> int:
    """Индекс сцены (0-based, как в scenes.jsonl); для сортировки."""
    value = rec.get("scene_index")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    sid = scene_id(rec)
    tail = sid.rsplit("_", 1)[-1] if sid else ""
    try:
        return int(tail)
    except (TypeError, ValueError):
        return _EMPTY_INDEX


def score_of(rec: Mapping[str, Any]) -> int | None:
    """Балл выбранного клипа; bool/строки/дроби приводим к None."""
    value = rec.get("selected_score")
    if value is None:
        value = rec.get("score")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def clip_name(value: Any) -> str:
    """Короткое имя клипа: basename пути или последний сегмент URL."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        text = unquote(parsed.path)
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = text.replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def shorten(text: Any, limit: int = 90) -> str:
    """Однострочный фрагмент фиксированной длины для строки списка."""
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, int(limit) - 1)].rstrip() + "…"


def load_scenes(path: str | Path | None) -> list[dict]:
    """Загрузить scenes.jsonl: дедупликация по scene_id (последняя запись) + сорт."""
    records: dict[str, dict] = {}
    for line_no, rec in enumerate(_read_jsonl(path)):
        key = scene_id(rec) or f"__line_{line_no}"
        records[key] = rec
    return sorted(records.values(), key=scene_index)


def scene_row(rec: Mapping[str, Any]) -> dict:
    """Нормализованная строка для UI/фильтра."""
    idx = scene_index(rec)
    return {
        "scene_no": idx + 1 if idx != _EMPTY_INDEX else 0,
        "scene_id": scene_id(rec),
        "scene_text": str(rec.get("scene_text") or rec.get("text") or ""),
        "selected_clip": str(rec.get("selected_clip") or ""),
        "selected_clip_name": clip_name(rec.get("selected_clip")),
        "score": score_of(rec),
        "fallback": str(rec.get("fallback_used") or "none"),
        "raw": dict(rec),
    }


def load_scene_rows(path: str | Path | None) -> list[dict]:
    """scenes.jsonl → нормализованные строки, готовые для панели."""
    if not path:
        return []
    return [scene_row(rec) for rec in load_scenes(path)]


def filter_scene_rows(rows: Iterable[Mapping[str, Any]], mode: str = "all") -> list[dict]:
    """Фильтр списка: all | fallback | low_score.

    ``low_score`` включает сцены без выбранного клипа: отсутствие балла для
    обучения так же важно, как низкий балл.
    """
    items = [dict(row) for row in rows]
    key = str(mode or "all").strip().lower()
    if key in ("fallback", "fallbacks", "fallback_only"):
        return [r for r in items if str(r.get("fallback") or "none") != "none"]
    if key in ("low_score", "weak", "score_lt_6"):
        return [
            r for r in items
            if not isinstance(r.get("score"), int) or int(r["score"]) < LOW_SCORE_THRESHOLD
        ]
    return items


def find_scenes_files(search_dirs: Iterable[str | Path] = ()) -> list[Path]:
    """Найти scenes.jsonl в типовых раскладках output/broll_smart/<series>/."""
    found: dict[str, Path] = {}

    def _add(path: Path) -> None:
        try:
            if path.is_file():
                found[str(path.absolute())] = path
        except OSError:
            pass

    def _scan_root(root: Path) -> None:
        try:
            if not root.exists():
                return
        except OSError:
            return
        if root.is_file():
            if root.name == "scenes.jsonl":
                _add(root)
            return
        _add(root / "scenes.jsonl")
        # output/broll_smart/<series>/scenes.jsonl
        try:
            for child in (root / "broll_smart").glob("*/scenes.jsonl"):
                _add(child)
        except OSError:
            pass
        # Если сама папка — broll_smart.
        if root.name == "broll_smart":
            try:
                for child in root.glob("*/scenes.jsonl"):
                    _add(child)
            except OSError:
                pass
        # Пакетная обработка: output/<stem>/broll_smart/<series>/scenes.jsonl
        try:
            for child in root.glob("*/broll_smart/*/scenes.jsonl"):
                _add(child)
        except OSError:
            pass

    for value in search_dirs:
        if not value:
            continue
        try:
            _scan_root(Path(value).expanduser())
        except (OSError, ValueError):
            continue
    return [found[key] for key in sorted(found)]


def find_latest_scenes(
    search_dirs: Iterable[str | Path] = (),
    *,
    explicit: str | Path | None = None,
    include_defaults: bool = True,
) -> Path | None:
    """Вернуть самый свежий scenes.jsonl.

    ``explicit`` (сохранённый выбор пользователя) имеет приоритет, если файл
    существует. Иначе ищем в ``search_dirs`` и в ``~/video_maker``.
    """
    if explicit:
        try:
            chosen = Path(explicit).expanduser()
        except (TypeError, ValueError):
            chosen = None
        if chosen is not None:
            if chosen.is_file() and chosen.name == "scenes.jsonl":
                return chosen
            if chosen.is_file():
                return chosen
            if chosen.is_dir():
                direct = chosen / "scenes.jsonl"
                if direct.is_file():
                    return direct
                nested = find_scenes_files([chosen])
                if nested:
                    return max(nested, key=lambda p: (_safe_mtime(p), str(p)))

    dirs = [Path(d).expanduser() for d in search_dirs if d]
    if include_defaults:
        dirs.append(REVIEW_DIR.parent)
    candidates = find_scenes_files(dirs)
    if not candidates:
        return None
    return max(candidates, key=lambda p: (_safe_mtime(p), str(p)))


def series_for(scenes_path: str | Path | None) -> str:
    """Имя серии из run_summary.json, иначе имя папки прогона."""
    if not scenes_path:
        return ""
    path = Path(scenes_path).expanduser()
    summary = path.parent / "run_summary.json"
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
        name = str(data.get("series") or "").strip() if isinstance(data, dict) else ""
        if name:
            return name
    except (OSError, ValueError, TypeError):
        pass
    return path.parent.name


# ─── обратная связь ──────────────────────────────────────────────────────

def normalize_reason(reason: Any) -> str:
    """Проверить причину и вернуть канонический ключ."""
    key = str(reason or "").strip().lower().replace(" ", "_")
    key = _REASON_ALIASES.get(key, key)
    if key not in REASONS:
        raise ValueError(
            f"unknown review reason: {reason!r}; expected one of {REASONS}"
        )
    return key


def feedback_path_for(
    scenes_path: str | Path | None,
    *,
    global_dir: str | Path | None = None,
) -> Path:
    """Путь review_feedback.jsonl: рядом со scenes.jsonl или в review-папке."""
    if scenes_path:
        return Path(scenes_path).expanduser().parent / FEEDBACK_FILENAME
    base = Path(global_dir).expanduser() if global_dir else REVIEW_DIR
    return base / FEEDBACK_FILENAME


def append_feedback(
    scenes_path: str | Path | None,
    scene: Mapping[str, Any],
    *,
    reason: str,
    series: str | None = None,
    feedback_path: str | Path | None = None,
    ts: str | None = None,
) -> dict:
    """Дописать один отзыв в review_feedback.jsonl и вернуть запись."""
    rec = dict(scene or {})
    path = (
        Path(feedback_path).expanduser()
        if feedback_path
        else feedback_path_for(scenes_path)
    )
    sid = scene_id(rec)
    if not sid:
        idx = scene_index(rec)
        sid = f"scene_{idx:03d}" if idx != _EMPTY_INDEX else "scene_unknown"
    fallback = rec.get("fallback_used")
    if fallback is None:
        fallback = rec.get("fallback")
    record = {
        "ts": ts or _utc_now(),
        "series": str(series or series_for(scenes_path)),
        "scene_id": sid,
        "scene_text": str(rec.get("scene_text") or rec.get("text") or ""),
        "selected_clip": str(rec.get("selected_clip") or ""),
        "score": score_of(rec),
        "fallback": str(fallback or "none"),
        "reason": normalize_reason(reason),
        "source": SOURCE_UI,
    }
    # 1) рядом с прогоном — удобно смотреть вместе со scenes.jsonl;
    # 2) в ~/video_maker/review — архив, который не удалит следующий прогон.
    targets = [
        Path(feedback_path).expanduser()
        if feedback_path
        else feedback_path_for(scenes_path)
    ]
    archive = REVIEW_DIR / FEEDBACK_FILENAME
    if os.path.abspath(str(archive)) not in {os.path.abspath(str(p)) for p in targets}:
        targets.append(archive)
    errors: list[str] = []
    for target in targets:
        try:
            _append_jsonl_line(target, record)
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    if len(errors) == len(targets):
        raise OSError("; ".join(errors))
    return record


def load_feedback(path: str | Path | None) -> list[dict]:
    """Прочитать review_feedback.jsonl (битые строки пропускаются)."""
    return _read_jsonl(path)


def feedback_index(
    path: str | Path | None,
    *,
    series: str | None = None,
) -> dict[str, dict]:
    """Карта scene_id → последний отзыв (для отметок в списке)."""
    index: dict[str, dict] = {}
    for rec in load_feedback(path):
        if series is not None and str(rec.get("series") or "") != str(series):
            continue
        sid = str(rec.get("scene_id") or "")
        if sid:
            index[sid] = rec
    return index


def _unique_records(records: Iterable[Mapping[str, Any]]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for rec in records:
        item = dict(rec)
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda r: (
        str(r.get("ts") or ""),
        str(r.get("series") or ""),
        str(r.get("scene_id") or ""),
        str(r.get("reason") or ""),
    ))
    return unique


def collect_all_feedback(
    *,
    search_dirs: Iterable[str | Path] = (),
    current_scenes: str | Path | None = None,
    include_global: bool = True,
) -> list[dict]:
    """Собрать отзывы из текущего прогона, соседних серий и review-папки."""
    paths: list[Path] = []
    if current_scenes:
        paths.append(feedback_path_for(current_scenes))
    if include_global:
        paths.append(REVIEW_DIR / FEEDBACK_FILENAME)

    dirs = [Path(d).expanduser() for d in search_dirs if d]
    if include_global:
        dirs.append(REVIEW_DIR.parent)
    for scenes_file in find_scenes_files(dirs):
        paths.append(scenes_file.parent / FEEDBACK_FILENAME)

    seen_files: set[str] = set()
    records: list[dict] = []
    for path in paths:
        try:
            key = str(path.absolute())
            if key in seen_files or not path.is_file():
                continue
            seen_files.add(key)
            records.extend(load_feedback(path))
        except OSError:
            continue
    return _unique_records(records)


def export_feedback(
    dest: str | Path,
    records: Iterable[Mapping[str, Any]],
) -> Path:
    """Выгрузить все отзывы в один JSONL (атомарно, без дублей)."""
    return _write_jsonl_atomic(Path(dest), _unique_records(records))


# ─── настройка выбранного scenes.jsonl ───────────────────────────────────

def load_review_config(config_path: str | Path | None = None) -> dict:
    """Прочитать сохранённый выбор scenes.jsonl (необязательный override)."""
    path = (
        Path(config_path).expanduser()
        if config_path
        else REVIEW_DIR / CONFIG_FILENAME
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_review_config(
    data: Mapping[str, Any],
    config_path: str | Path | None = None,
) -> Path:
    """Сохранить настройку панели (например, выбранный scenes.jsonl)."""
    path = (
        Path(config_path).expanduser()
        if config_path
        else REVIEW_DIR / CONFIG_FILENAME
    )
    return _write_json_atomic(path, data)
