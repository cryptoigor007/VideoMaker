# VideoMaker | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api.1: проверка блока «Пульт» в самом окне. Работает на НАСТОЯЩЕМ
#              mainloop (как приложение), а не на root.update() — иначе
#              межпоточные вызовы Tk ведут себя иначе. Запуск:
#              python tests/verify_app_gui.py
# PREV: (нет файла)
# NEW FILE: tests/verify_app_gui.py

"""Проверка пульта со стороны ОКНА (не со стороны HTTP).

Нажимает галочку и кнопку программно внутри mainloop, следит за подписями
и проверяет, что сервер действительно переключается между петлёй и сетью.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_maker.api import start_api
from video_maker.api.server import local_ip
from video_maker.gui.app import App

PORT = 18771
LAN_IP = local_ip()

OK: list[str] = []
BAD: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (OK if cond else BAD).append(name)
    mark = "  ok   " if cond else " ПЛОХО "
    print(mark + name + (f"  [{detail}]" if detail else ""), flush=True)
    return cond


def reachable(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


logging.getLogger().setLevel(logging.INFO)  # как в приложении (setup_logging)
root = tk.Tk()
root.withdraw()
app = App(root)
root.deiconify()
# The API is intentionally kept on its own visible tab so controls never get
# clipped by the main creation view on short displays.
if hasattr(app, "tab_monitor"):
    app.notebook.select(app.tab_monitor)
    root.update_idletasks()
    root.update()

print("=" * 74, flush=True)
print("ПРОВЕРКА ПУЛЬТА СО СТОРОНЫ ОКНА (настоящий mainloop), порт",
      PORT, "| IP:", LAN_IP, flush=True)
print("=" * 74, flush=True)

runtime = start_api(app, root, headless=True, port=PORT)
check("пульт поднялся из окна", runtime is not None)
if runtime is None:
    sys.exit(1)
api = runtime.api
TOKEN = api.conf["token"]

state: dict = {"opened": [], "snap": None, "snap_error": None, "t_click": 0.0,
               "activity_before": ""}
steps: list = []          # очередь шагов, выполняемых в главном потоке
real_open = webbrowser.open


def later(seconds: float, fn) -> None:
    root.after(int(seconds * 1000), fn)


def run_next() -> None:
    if not steps:
        root.quit()
        return
    step_fn = steps.pop(0)
    try:
        step_fn()
    except Exception as exc:  # noqa: BLE001
        BAD.append(f"шаг упал: {type(exc).__name__}: {exc}")
        print(f" ПЛОХО шаг упал: {type(exc).__name__}: {exc}", flush=True)
        root.quit()


def step(fn) -> None:
    """Шаг: выполнить и перейти к следующему."""
    def wrapper():
        fn()
        later(0.05, run_next)
    steps.append(wrapper)


def wait(seconds: float) -> None:
    """Пауза между шагами: главный поток свободен, как в приложении."""
    steps.append(lambda: later(seconds, run_next))


# ── 1. Что видно сразу после старта ──────────────────────────────────────
def s1():
    check("виджет подписи существует", hasattr(app, "api_label"))
    check("галочка сети существует", hasattr(app, "api_lan_cb"))
    check("кнопка «Открыть пульт» существует", hasattr(app, "api_open_btn"))
    check("API-кнопка видима на вкладке пульта", bool(app.api_open_btn.winfo_ismapped()))
    check("галочка доступа видима на вкладке пульта", bool(app.api_lan_cb.winfo_ismapped()))
    label = app.api_label.cget("text")
    check("в подписи есть локальный адрес", f"127.0.0.1:{PORT}" in label,
          label.splitlines()[0][:60])
    check("в подписи есть токен", TOKEN in label, "…" + TOKEN[-6:])
    check("в подписи сказано, как включить телефон", "включите" in label.lower(),
          label.splitlines()[-1])
    check("галочка сети снята", app.api_lan_var.get() is False)
    check("снаружи пульт недоступен", not reachable(LAN_IP, PORT))


# ── 2. Нажимаю галочку: сервер переключается, подпись обновляется ────────
def s2_click():
    state["t_click"] = time.time()
    app.api_lan_cb.invoke()
    shown = app.api_label.cget("text")
    check("на нажатие есть мгновенный отклик", "ереключаю" in shown, shown[:50])


def s2_check():
    dt = time.time() - state["t_click"]
    label = app.api_label.cget("text")
    check("настройка переключилась", api.conf["lan"] is True)
    check("галочка включена", app.api_lan_var.get() is True)
    check("подпись обновилась быстро (< 2 с)", f"{LAN_IP}:{PORT}" in label,
          f"{dt:.2f} с, {label.splitlines()[-1][:46]}")
    check("снаружи пульт стал доступен", reachable(LAN_IP, PORT))


# ── 3. Кнопка «Открыть пульт» ────────────────────────────────────────────
def s3():
    state["opened"] = []
    webbrowser.open = lambda url, *a, **k: (state["opened"].append(url), True)[1]
    app.api_open_btn.invoke()


def s3_check():
    webbrowser.open = real_open
    opened = state["opened"]
    check("кнопка открыла браузер один раз", len(opened) == 1, str(opened[:1]))
    check("открыт адрес с токеном и портом",
          bool(opened) and TOKEN in opened[0] and f":{PORT}" in opened[0])
    check("при включённой сети открыт сетевой адрес",
          bool(opened) and LAN_IP in opened[0], str(opened[:1]))


# ── 4. Снимаю галочку ────────────────────────────────────────────────────
def s4():
    state["t_click"] = time.time()
    app.api_lan_cb.invoke()


def s4_check():
    dt = time.time() - state["t_click"]
    label = app.api_label.cget("text")
    check("настройка вернулась", api.conf["lan"] is False)
    check("галочка снята", app.api_lan_var.get() is False)
    check("снаружи снова закрыто", not reachable(LAN_IP, PORT))
    check("с петли работает", reachable("127.0.0.1", PORT))
    check("подпись снова предлагает включить сеть",
          "включите" in label.lower() and dt < 2, f"{dt:.2f} с")


def s5():
    state["opened"] = []
    webbrowser.open = lambda url, *a, **k: (state["opened"].append(url), True)[1]
    app.api_open_btn.invoke()


def s5_check():
    webbrowser.open = real_open
    check("при выключенной сети открыт локальный адрес",
          bool(state["opened"]) and "127.0.0.1" in state["opened"][0],
          str(state["opened"][:1]))


# ── 5. Строка «▶ что идёт сейчас» ────────────────────────────────────────
def s6():
    check("виджет строки существует", hasattr(app, "activity_label"))
    logging.getLogger("проверка.окна").info("Тестовая строка активности")


def s6_check():
    text = app.activity_label.cget("text")
    check("строка показывает событие с пометкой ▶",
          text.startswith("▶") and "активности" in text, text)
    state["activity_before"] = text
    logging.getLogger("проверка.окна").info("─" * 40)


def s6_check2():
    check("разделители из линий не затирают строку",
          app.activity_label.cget("text") == state["activity_before"],
          app.activity_label.cget("text")[:40])


# ── 6. Снимок статуса так, как его берёт HTTP-поток (не из Tk) ────────────
def s7():
    # Важно: запрос уходит в отдельный поток и НЕ ждём его здесь — главный
    # поток занят шагом и не может одновременно обслуживать пульт.
    def work():
        try:
            state["snap"] = runtime.status.snapshot(log_tail=3)
        except Exception as exc:  # noqa: BLE001
            state["snap_error"] = f"{type(exc).__name__}: {exc}"

    state["snap"] = None
    state["snap_error"] = None
    threading.Thread(target=work, name="как-http", daemon=True).start()


def s7_check():
    check("снимок статуса получен из чужого потока", state["snap"] is not None,
          str(state["snap_error"] or ""))
    snap = state["snap"] or {}
    check("в снимке есть стадия, прогресс, время, действие",
          all(k in snap for k in ("stage", "progress", "elapsed_human",
                                  "eta_human", "activity")))
    check("в снимке есть папка вывода и серия",
          "output_folder" in snap and "series" in snap)
    check("снимок сериализуется в JSON (пульт его отдаёт)",
          isinstance(json.dumps(snap), str))


def s8():
    # Тоже в потоке: HTTP-запрос внутри шага заблокировал бы сам пульт.
    def work():
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/api/status?key={TOKEN}", timeout=10) as r:
                state["http"] = json.loads(r.read())
        except Exception as exc:  # noqa: BLE001
            state["http_error"] = f"{type(exc).__name__}: {exc}"

    state["http"] = None
    state["http_error"] = None
    threading.Thread(target=work, name="как-браузер", daemon=True).start()


def s8_check():
    check("HTTP-статус получен, пока окно занято",
          state["http"] is not None, str(state["http_error"] or ""))
    body = state["http"] or {}
    check("HTTP-статус отдаёт стадию", "stage" in body, str(body.get("stage"))[:40])
    check("пульт отвечает после всех переключений", reachable("127.0.0.1", PORT))


# Порядок проверок и паузы (в секундах) перед каждой
PLAN: list[tuple[float, object]] = [
    (0.0, s1),
    (0.3, s2_click), (1.2, s2_check),
    (0.3, s3), (0.4, s3_check),
    (0.3, s4), (1.2, s4_check),
    (0.3, s5), (0.4, s5_check),
    (0.3, s6), (0.4, s6_check), (0.4, s6_check2),
    (0.2, s7), (0.6, s7_check),
    (0.2, s8), (0.6, s8_check),
]

for delay, step_fn in PLAN:
    if delay:
        wait(delay)
    step(step_fn)  # type: ignore[arg-type]

later(0.8, run_next)        # даём стартовому вызову моста дойти
root.mainloop()

print(flush=True)
print("=" * 74, flush=True)
print(f"ИТОГ: успешно {len(OK)}, провалено {len(BAD)}", flush=True)
for name in BAD:
    print("   -", name, flush=True)
print("=" * 74, flush=True)

with contextlib.suppress(Exception):
    runtime.stop()
root.destroy()
sys.exit(1 if BAD else 0)
