# VideoMaker | 2026.09.28 | проверка дедлока лог-хука «▶ активность»
# CHANGED:
#   r57-api.2: воспроизводит зависание при выборе аудио: фоновый поток пишет
#              в лог, _BufferHandler._push_to_window() дёргает root.after()
#              из чужого потока → _tkinter ставит вызов в очередь Tcl-потока
#              и ждёт (Tcl_ConditionWait), удерживая lock хендлера; главный
#              поток в это время заперт на том же lock → вечный дедлок.
#              Запуск: python tests/verify_log_deadlock.py
#              Живой процесс можно проверить так:
#                sample <pid> 3 -file /tmp/vm.txt   (два запертых потока)
# PREV: (нет файла)
# NEW FILE: tests/verify_log_deadlock.py

"""Сценарий как при выборе аудиодорожки: главный поток сидит в длинном
Tk-callback (имитация нативного модального диалога), фоновый поток пишет
в лог. Сломанный хендлер встаёт в Tcl_ConditionWait, удерживая RLock;
следующий log.info главного потока залипает на этом же RLock — окно
перестаёт отвечать, а поток очереди остаётся заблокированным навсегда.

Починенный хендлер прогоняет обновление статус-бара через мост TkBridge
(call_async → насос в главном потоке) и не трогает Tk из чужого потока.
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_maker.api.bridge import LogBuffer, TkBridge, _BufferHandler

HANG_TIMEOUT = 8.0  # с; если главный поток не ожил — это дедлок

logbuf = LogBuffer()
done = threading.Event()
worker_done = threading.Event()

root = tk.Tk()
root.withdraw()
label = ttk.Label(root, text="")


class FakeApp:
    """Минимум, который нужен _BufferHandler._push_to_window()."""


app = FakeApp()
app.root = root
app.activity_label = label
app._activity_text = ""

bridge = TkBridge(root)

kwargs = {"app": app}
if "bridge" in inspect.signature(_BufferHandler.__init__).parameters:
    kwargs["bridge"] = bridge  # починенная версия хендлера
handler = _BufferHandler(logbuf, **kwargs)

logger = logging.getLogger("vm.deadlock.check")
logger.handlers = [handler]
logger.propagate = False
logger.setLevel(logging.INFO)


def watchdog() -> None:
    if not done.wait(HANG_TIMEOUT):
        print(" ПЛОХО  дедлок: главный поток заперт на lock лог-хендлера", flush=True)
        os._exit(3)


def worker() -> None:
    time.sleep(0.4)
    logger.info("worker: строка из фонового потока")
    worker_done.set()


def phase_main() -> None:
    """Главный поток: долгий callback — Tcl-события не обрабатываются."""
    threading.Thread(target=worker, daemon=True).start()
    time.sleep(2.0)  # здесь поток worker ждёт ответа Tcl-потока
    logger.info("main: строка из главного потока")  # на сломанном коде — дедлок
    done.set()


def phase_check() -> None:
    text = label.cget("text") if label.winfo_exists() else ""
    buf = logbuf.tail(50)
    ok = done.is_set() and worker_done.is_set() and len(buf) >= 2
    mark = "  ok   " if ok else " ПЛОХО "
    print(mark + f"лог не повесил приложение (строк в буфере: {len(buf)})", flush=True)
    print(f"       статус-бар: {text!r}", flush=True)
    os._exit(0 if ok else 4)


def main() -> None:
    threading.Thread(target=watchdog, daemon=True).start()
    root.after(200, phase_main)
    root.after(3000, phase_check)  # насосу моста нужно пару тактов (40 мс)
    root.mainloop()
    print(" ПЛОХО  mainloop завершился до проверки", flush=True)
    os._exit(5)


if __name__ == "__main__":
    main()
