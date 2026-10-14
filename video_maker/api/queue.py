# VideoMaker API | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: новый файл. Очередь серий: постановка, последовательный запуск,
#            сохранение в JSON (перезапуск не теряет), повтор после падения,
#            пауза, определение успеха/ошибки по выдаче и логу.
#   r57-api.1: пачка возвращает rejected; завершение серии берёт папку и серию
#              из настроек приложения; битый файл очереди не роняет пульт.
# PREV: (нет файла)
# NEW FILE: video_maker/api/queue.py

"""Очередь серий для пакетной обработки.

Пайплайн тяжёлый (8 ГБ памяти, диск 40 МБ/с), поэтому очередь строго
последовательная: следующая серия стартует только когда приложение свободно.
Состояние лежит в JSON, так что перезапуск приложения очередь не теряет.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid

from .control import apply_settings, find_results, start_pipeline, validate

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, FAILED, CANCELED = "pending", "running", "done", "failed", "canceled"
TERMINAL = (DONE, FAILED, CANCELED)


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class SeriesQueue:
    """Последовательная очередь серий поверх запуска приложения."""

    def __init__(self, app, bridge, status, path: str):
        self.app = app
        self.bridge = bridge
        self.status = status
        self.path = path

        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._paused = False
        self._cancel_pending = False
        self._cancel_target: str | None = None
        self._last_tick_warning = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: вызывается при завершении серии (вебхук) — задаёт сервер
        self.on_finish = None
        self._load()

    # ── жизненный цикл ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="vm-queue", daemon=True)
        self._thread.start()
        log.info("[ОЧЕРЕДЬ] поток запущен, в очереди %d", len(self._items))

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — очередь не должна умирать
                # Пока открыт нативный диалог, Tk не обслуживает таймеры — тот же
                # сбой повторяется каждые ~12 с. Пишем предупреждение не чаще
                # раза в минуту, остальное — в DEBUG.
                now = time.monotonic()
                if now - self._last_tick_warning >= 60.0:
                    self._last_tick_warning = now
                    log.warning("[ОЧЕРЕДЬ] сбой такта: %s", exc)
                else:
                    log.debug("[ОЧЕРЕДЬ] сбой такта (повтор, подавлено): %s", exc)

    # ── операции ─────────────────────────────────────────────────────────
    def add(self, audio: str, output: str = "", series: str = "",
            note: str = "", overrides: dict | None = None,
            allow_duplicate: bool = False) -> dict:
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("overrides должен быть JSON-объектом")
        audio = (audio or "").strip()
        if not audio:
            raise ValueError("не указан аудиофайл (audio)")
        item = {
            "id": uuid.uuid4().hex[:12],
            "audio": audio,
            "output": (output or "").strip(),
            "series": (series or "").strip(),
            "note": (note or "").strip(),
            "overrides": dict(overrides or {}),
            "status": PENDING,
            "attempts": 0,
            "added_at": _now(),
            "added_human": _iso(_now()),
            "started_at": 0.0,
            "finished_at": 0.0,
            "error": "",
        }
        with self._lock:
            if not allow_duplicate:
                for it in self._items:
                    if it["audio"] == audio and it["status"] in (PENDING, RUNNING):
                        return it
            self._items.append(item)
            self._save()
        log.info("[ОЧЕРЕДЬ] + %s → %s", os.path.basename(audio), item["output"] or "(по настройкам)")
        return item

    def add_many(self, entries: list[dict]) -> tuple[list[dict], list[str]]:
        """Добавить пачку. Возвращает (добавленные, причины отказов)."""
        items: list[dict] = []
        rejected: list[str] = []
        for entry in (entries or []):
            entry = entry if isinstance(entry, dict) else {}
            try:
                items.append(self.add(
                    audio=entry.get("audio", ""),
                    output=entry.get("output", ""),
                    series=entry.get("series", ""),
                    note=entry.get("note", ""),
                    overrides=entry.get("overrides") or {
                        k: v for k, v in entry.items()
                        if k not in ("audio", "output", "series", "note", "overrides")},
                ))
            except ValueError as exc:
                rejected.append(f"{entry.get('audio') or '(без аудио)'}: {exc}")
        if rejected:
            log.warning("[ОЧЕРЕДЬ] пропущено записей: %d (%s)", len(rejected), "; ".join(rejected[:3]))
        return items, rejected

    def snapshot(self) -> dict:
        with self._lock:
            cur = self._current()
            pending = [i for i in self._items if i["status"] == PENDING]
            return {
                "paused": self._paused,
                "total": len(self._items),
                "pending": len(pending),
                "done": sum(1 for i in self._items if i["status"] == DONE),
                "failed": sum(1 for i in self._items if i["status"] == FAILED),
                "current": self._public(cur) if cur else None,
                "items": [self._public(i) for i in self._items],
            }

    def remove(self, item_id: str) -> bool:
        with self._lock:
            for i, it in enumerate(self._items):
                if it["id"] == item_id:
                    if it["status"] == RUNNING:
                        raise ValueError("нельзя удалить выполняющуюся серию — сначала отмена")
                    self._items.pop(i)
                    self._save()
                    return True
        return False

    def retry(self, item_id: str) -> bool:
        with self._lock:
            for it in self._items:
                if it["id"] == item_id and it["status"] in TERMINAL:
                    it["status"] = PENDING
                    it["error"] = ""
                    it["finished_at"] = 0.0
                    self._save()
                    return True
        return False

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i["status"] not in TERMINAL]
            self._save()
            return before - len(self._items)

    def pause(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)
            self._save()

    def note_cancel(self) -> None:
        """Отмена адресуется текущей серии очереди, чтобы не помечать чужие сбои."""
        with self._lock:
            current = self._current()
            self._cancel_pending = True
            self._cancel_target = current["id"] if current else None

    # ── внутреннее ───────────────────────────────────────────────────────
    def _public(self, item: dict | None) -> dict | None:
        if not item:
            return None
        out = dict(item)
        if item["status"] == RUNNING and item["started_at"]:
            out["running_sec"] = round(_now() - item["started_at"], 1)
        if item["status"] in TERMINAL and item["started_at"] and item["finished_at"]:
            out["took_sec"] = round(item["finished_at"] - item["started_at"], 1)
        out["started_human"] = _iso(item["started_at"])
        out["finished_human"] = _iso(item["finished_at"])
        return out

    def _current(self) -> dict | None:
        for it in self._items:
            if it["status"] == RUNNING:
                return it
        return None

    def _next_pending(self) -> dict | None:
        with self._lock:
            for it in self._items:
                if it["status"] == PENDING:
                    return it
            return None

    def _tick(self) -> None:
        snap = self.status.snapshot(log_tail=0)
        running = bool(snap.get("running"))
        with self._lock:
            cur = self._current()
        if cur is not None:
            if not running:
                self._finish(cur, snap)
            return
        if running or self._paused:
            return
        nxt = self._next_pending()
        if nxt is not None:
            self._launch(nxt)

    def _launch(self, item: dict) -> None:
        settings = {
            "audio_path": item["audio"],
        }
        if item.get("output"):
            settings["output_folder"] = item["output"]
        if item.get("series"):
            settings["series_name"] = item["series"]
        settings.update({k: v for k, v in (item.get("overrides") or {}).items()
                         if k not in ("audio", "output")})

        def _go():
            apply_settings(self.app, settings)
            errors = validate(self.app)
            if errors:
                item["_precheck"] = errors
                log.error("[ОЧЕРЕДЬ] серия %s не прошла проверку: %s",
                          os.path.basename(item["audio"]), "; ".join(errors))
                return
            start_pipeline(self.app)
            if not bool(getattr(self.app, "running", False)):
                item["_precheck"] = ["приложение не запустило обработку; проверьте аудио и B-roll"]

        try:
            self.bridge.call(_go, timeout=90)
        except Exception as exc:  # noqa: BLE001
            self._mark(item, FAILED, f"не удалось запустить: {exc}")
            return

        if item.get("_precheck"):
            self._mark(item, FAILED, "; ".join(item.pop("_precheck")))
            return

        with self._lock:
            item["status"] = RUNNING
            item["started_at"] = _now()
            item["attempts"] += 1
            item["error"] = ""
            self._save()
        log.info("[ОЧЕРЕДЬ] ▶ старт: %s (попытка %d)",
                 os.path.basename(item["audio"]), item["attempts"])

    def _finish(self, item: dict, snap: dict) -> None:
        since = item.get("started_at") or 0
        # Запись могла не указывать папку/серию — тогда истина в настройках
        # приложения (snap собран из окна тем же тактом).
        output = item.get("output") or snap.get("output_folder") or ""
        series = item.get("series") or snap.get("series") or ""
        result = find_results(output, series, since)

        # Отмена одноразовая и адресная: потребляем её на первом же завершении,
        # но учитываем только если она была про этот выпуск (или про запуск из GUI).
        pending = bool(self._cancel_pending)
        target = self._cancel_target
        self._cancel_pending = False
        self._cancel_target = None

        if result.get("ready") and result.get("total_bytes"):
            self._mark(item, DONE, "", result=result)
            return
        if (pending and target == item["id"]) or snap.get("cancel_requested"):
            self._mark(item, CANCELED, "отменено пользователем")
            return
        reason = snap.get("activity") or "пайплайн завершился без выдачи"
        self._mark(item, FAILED, reason)

    def _mark(self, item: dict, status: str, error: str = "", result: dict | None = None) -> None:
        with self._lock:
            item["status"] = status
            item["error"] = error
            item["finished_at"] = _now()
            if result:
                item["result"] = {
                    "series_dir": result.get("series_dir", ""),
                    "files": len(result.get("files") or []),
                    "total_bytes": result.get("total_bytes", 0),
                    "metadata": result.get("metadata", ""),
                }
            self._save()
        mark = {DONE: "✔", FAILED: "✖", CANCELED: "■"}.get(status, "?")
        log.info("[ОЧЕРЕДЬ] %s %s → %s %s", mark, os.path.basename(item["audio"]), status, error)
        # уведомить внешние системы (вебхук Orchestrator)
        if callable(self.on_finish):
            try:
                self.on_finish(dict(item))
            except Exception:  # вебхук не должен ломать очередь
                log.warning("[ОЧЕРЕДЬ] обработчик завершения упал", exc_info=True)

    # ── хранение ─────────────────────────────────────────────────────────
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            data = {"paused": self._paused, "items": self._items}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("[ОЧЕРЕДЬ] не удалось сохранить: %s", exc)

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                items = data.get("items", [])
                paused = data.get("paused", False)
                self._paused = paused if type(paused) is bool else False
                if type(paused) is not bool:
                    log.warning("[ОЧЕРЕДЬ] paused должен быть boolean; очередь восстановлена без паузы")
            elif isinstance(data, list):
                # Legacy format: queue file used to be a plain list of items.
                items = data
                self._paused = False
            else:
                log.warning("[ОЧЕРЕДЬ] корень файла должен быть объектом или legacy-списком; файл проигнорирован")
                return
            if not isinstance(items, list):
                log.warning("[ОЧЕРЕДЬ] поле items должно быть списком; файл проигнорирован")
                self._paused = False
                return
            for source in items:
                if not isinstance(source, dict):
                    continue
                audio = source.get("audio")
                if not isinstance(audio, str) or not audio.strip():
                    continue
                it = dict(source)
                if not isinstance(it.get("overrides"), dict):
                    it["overrides"] = {}
                it.setdefault("id", uuid.uuid4().hex[:12])
                for key, default in (("output", ""), ("series", ""), ("note", ""),
                                     ("attempts", 0), ("added_at", _now()),
                                     ("started_at", 0.0), ("finished_at", 0.0), ("error", "")):
                    it.setdefault(key, default)
                it.setdefault("added_human", _iso(it["added_at"]))
                if it.get("status") not in (PENDING, RUNNING, DONE, FAILED, CANCELED):
                    it["status"] = PENDING
                if it.get("status") == RUNNING:
                    it["status"] = PENDING
                    it["error"] = "прервано перезапуском приложения"
                self._items.append(it)
            log.info("[ОЧЕРЕДЬ] восстановлено %d записей", len(self._items))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("[ОЧЕРЕДЬ] не удалось прочитать %s: %s", self.path, exc)

