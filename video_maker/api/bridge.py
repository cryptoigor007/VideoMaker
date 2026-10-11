# VideoMaker API | 2026.09.23-r57-api.2 | 2026-09-28
# CHANGED:
#   r57-api.2: фикс зависания при выборе аудио. _BufferHandler больше не дёргает
#              root.after() из чужого потока: _tkinter маршалит такой вызов в
#              Tcl-поток и ждёт (Tcl_ConditionWait), удерживая lock хендлера, —
#              следующий log.info главного потока залипал на этом же lock
#              навсегда. Теперь обновление статус-бара идёт через
#              TkBridge.call_async (насос главного потока).
#   r57-api: новый файл. Мост в главный поток Tk (Tkinter не потокобезопасен) +
#            сбор живого статуса: стадия, прогресс, прошло/осталось, текущее
#            действие, кольцевой буфер лога для API и веб-страницы.
#   r57-api.1: since() возвращает номер последней отданной строки — при всплеске
#              логов клиент больше не теряет строки.
# PREV: (нет файла)
# NEW FILE: video_maker/api/bridge.py

"""Мост в главный поток Tk и сбор статуса приложения.

Tkinter не потокобезопасен: читать Tk-переменные и трогать виджеты можно
только из потока, создавшего root. HTTP-сервер живёт в своём потоке, поэтому
все обращения к приложению идут через TkBridge (очередь + насос на root.after).
"""
from __future__ import annotations

import contextlib
import logging
import os
import queue
import threading
import time
import tkinter as tk
from collections import deque
from typing import ClassVar

log = logging.getLogger(__name__)

LOG_BUFFER_LINES = 1000


class TkBridge:
    """Выполняет функции в главном потоке Tk."""

    def __init__(self, root, interval_ms: int = 40):
        self.root = root
        self._interval = interval_ms
        self._q: queue.Queue = queue.Queue()
        self._alive = True
        self._pump()

    def _pump(self) -> None:
        while True:
            try:
                fn, box = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                box["result"] = fn()
                box["ok"] = True
            except BaseException as exc:  # noqa: BLE001 — пробрасываем в вызывающий поток
                box["error"] = exc
                box["ok"] = False
            finally:
                box["done"].set()
        if self._alive:
            try:
                self.root.after(self._interval, self._pump)
            except (tk.TclError, RuntimeError):  # окно закрыто — мост больше не нужен
                self._alive = False

    def call(self, fn, timeout: float = 30.0):
        """Выполнить fn() в главном потоке Tk и вернуть результат."""
        if not self._alive:
            raise RuntimeError("Tk-поток недоступен (окно закрыто)")
        box: dict = {"done": threading.Event()}
        self._q.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError(f"Tk-поток не ответил за {timeout:.0f} с")
        if not box["ok"]:
            raise box["error"]
        return box.get("result")

    def call_async(self, fn) -> None:
        """Поставить в очередь, не дожидаясь результата."""
        self._q.put((fn, {"done": threading.Event()}))

    def stop(self) -> None:
        self._alive = False


class LogBuffer:
    """Кольцевой буфер строк лога + ожидание новых (для живого потока)."""

    def __init__(self, maxlen: int = LOG_BUFFER_LINES):
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self._new = threading.Condition(self._lock)

    def add(self, line: str) -> None:
        line = (line or "").rstrip()
        if not line:
            return
        with self._new:
            if self._lines and self._lines[-1][1] == line:
                return  # дубли из logging и GUI-панели
            self._seq += 1
            self._lines.append((self._seq, line))
            self._new.notify_all()

    def tail(self, n: int = 200) -> list[str]:
        with self._lock:
            return [ln for _, ln in list(self._lines)[-max(1, n):]]

    def since(self, seq: int, limit: int = 500) -> tuple[int, list[str]]:
        """Строки после seq → (seq последней отданной строки, строки).

        Возвращаем именно seq последней отданной строки: иначе при всплеске
        логов (> limit строк) клиент перескочил бы непрочитанное.
        """
        with self._lock:
            fresh = [(s, ln) for s, ln in self._lines if s > seq]
            if not fresh:
                # строки могли вытесниться из кольца — тогда честно отдаём текущий счётчик
                return (self._seq if self._seq > seq and len(self._lines) >= self._lines.maxlen else seq), []
            chunk = fresh[:max(1, limit)]
            return chunk[-1][0], [ln for _, ln in chunk]

    def activity(self) -> str:
        with self._lock:
            return self._lines[-1][1] if self._lines else ""

    def count(self) -> int:
        with self._lock:
            return self._seq

    def wait_new(self, seq: int, timeout: float = 20.0) -> None:
        with self._new:
            if self._seq > seq:
                return
            self._new.wait(timeout)


def last_meaningful_line(text: str) -> str:
    """Последняя содержательная строка (рамки из ─ и ═ пропускаем)."""
    for candidate in reversed((text or "").splitlines()):
        line = candidate.strip()
        if line and not set(line) <= set("─═-— "):
            return line
    return ""


class _BufferHandler(logging.Handler):
    """Кладёт все записи logging в буфер и обновляет статус-бар в окне."""

    def __init__(self, buf: LogBuffer, app=None, bridge=None):
        super().__init__(level=logging.INFO)
        self.buf = buf
        self.app = app
        self.bridge = bridge

    def _push_to_window(self, text: str) -> None:
        """Статус-бар в окне: «▶ что идёт сейчас» (из главного потока Tk)."""
        app = self.app
        if app is None or not text:
            return

        def _update() -> None:
            with contextlib.suppress(tk.TclError, AttributeError):
                app._activity_text = text
                app.activity_label.configure(text=f"▶ {text}")

        if self.bridge is not None:
            # Только через мост: root.after() из чужого потока _tkinter
            # маршалит в Tcl-поток и ждёт ответа (Tcl_ConditionWait), а lock
            # хендлера уже взят — главный поток встаёт на этот же lock.
            with contextlib.suppress(Exception):
                self.bridge.call_async(_update)
            return
        # Без моста (тесты) Tk не трогаем вовсе — пишем только атрибут.
        with contextlib.suppress(AttributeError):
            app._activity_text = text

    def emit(self, record: logging.LogRecord) -> None:
        # лог не должен ломать приложение ни при каких обстоятельствах
        with contextlib.suppress(Exception):
            message = record.getMessage()
            self.buf.add(f"{time.strftime('%H:%M:%S', time.localtime(record.created))} "
                         f"[{record.levelname}] {message}")
            self._push_to_window(last_meaningful_line(message))


class AppStatus:
    """Снимок состояния приложения для API."""

    #: Человеческие названия стадий (ключ — name() стадии)
    STAGE_HINTS: ClassVar[dict[str, str]] = {
        "Загрузка аудио": "чтение аудио",
        "Транскрибация": "распознавание речи (Whisper)",
        "Анализ Gemini": "разбор сцен и хуков (Gemini)",
        "Master": "склейка мастер-видео 4K",
        "Final H→V": "горизонталь + вертикаль",
        "Shorts": "нарезка шортсов",
        "Финализация": "BGM, обложки, выдача",
    }

    def __init__(self, app, bridge: TkBridge, logbuf: LogBuffer, started_at: float):
        self.app = app
        self.bridge = bridge
        self.logbuf = logbuf
        self.started_at = started_at
        self.install_log_hook()

    # ── перехват лога ────────────────────────────────────────────────────
    def install_log_hook(self) -> None:
        """Все записи logging → в буфер (в дополнение к файлу и панели GUI)."""
        root_logger = logging.getLogger()
        for h in root_logger.handlers:
            if isinstance(h, _BufferHandler):
                return
        root_logger.addHandler(_BufferHandler(self.logbuf, app=self.app, bridge=self.bridge))

    # ── чтение Tk-состояния (только из главного потока) ──────────────────
    def _read_tk(self) -> dict:
        app = self.app
        settings = getattr(app, "settings", None)

        def _text(widget_name: str) -> str:
            w = getattr(app, widget_name, None)
            with contextlib.suppress(tk.TclError, AttributeError, ValueError):
                return str(w.cget("text")) if w is not None else ""
            return ""

        progress = 0.0
        with contextlib.suppress(tk.TclError, AttributeError, ValueError, TypeError):
            progress = float(app.progress_var.get() or 0)

        cancel_set = False
        with contextlib.suppress(AttributeError, RuntimeError):
            cancel_set = bool(app.cancel_event.is_set())

        audio = getattr(settings, "audio_path", "") or ""
        return {
            "running": bool(getattr(app, "running", False)),
            "progress": progress,
            "stage_text": _text("stage_label"),
            "time_text": _text("time_label"),
            "activity_text": getattr(app, "_activity_text", "") or _text("activity_label"),
            "t0": float(getattr(app, "_pipeline_t0", 0) or 0),
            "cancel_requested": cancel_set,
            "series": getattr(settings, "series_name", "") or os.path.basename(audio),
            "audio_path": audio,
            "output_folder": getattr(settings, "output_folder", "") or "",
            "queue_files": [os.path.basename(p) for p in (getattr(app, "_audio_queue", None) or [])],
        }

    # ── публичный снимок ─────────────────────────────────────────────────
    def snapshot(self, log_tail: int = 30) -> dict:
        near = self.bridge.call(self._read_tk, timeout=10.0)

        progress = float(near["progress"])
        elapsed = (time.time() - near["t0"]) if near["t0"] else 0.0
        eta = None
        if near["running"] and progress > 3 and elapsed > 0:
            total_est = elapsed * (100.0 / max(progress, 0.1))
            eta = max(0.0, total_est - elapsed)

        stage_name, stage_idx, stage_total = self._parse_stage(near["stage_text"])

        return {
            "ok": True,
            "state": "running" if near["running"] else "idle",
            "running": near["running"],
            "cancel_requested": near["cancel_requested"],
            "series": near["series"],
            "stage": {
                "name": stage_name,
                "hint": self.STAGE_HINTS.get(stage_name, ""),
                "index": stage_idx,
                "total": stage_total,
                "raw": near["stage_text"],
            },
            "progress": round(progress, 1),
            "elapsed_sec": round(elapsed, 1),
            "elapsed_human": self._human(elapsed),
            "eta_sec": round(eta, 1) if eta is not None else None,
            "eta_human": self._human(eta) if eta is not None else None,
            "activity": self.logbuf.activity(),
            "gui_activity": near.get("activity_text", ""),
            "audio_path": near["audio_path"],
            "output_folder": near["output_folder"],
            "audio_queue": near["queue_files"],
            "log_tail": self.logbuf.tail(log_tail),
            "server": {
                "uptime_sec": round(time.time() - self.started_at, 1),
                "pid": os.getpid(),
            },
        }

    @staticmethod
    def _parse_stage(text: str) -> tuple[str, int, int]:
        """'[5/7] Final H→V' → ('Final H→V', 5, 7)."""
        name = (text or "").strip()
        idx = total = 0
        if name.startswith("[") and "]" in name:
            head, _, rest = name[1:].partition("]")
            name = rest.strip()
            if "/" in head:
                a, _, b = head.partition("/")
                try:
                    idx, total = int(a), int(b)
                except ValueError:
                    idx = total = 0
        return name, idx, total

    @staticmethod
    def _human(seconds: float | None) -> str:
        if seconds is None:
            return ""
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
