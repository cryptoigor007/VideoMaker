# VideoMaker API | 2026.09.23-r57-api | 2026-09-23
# CHANGED:
#   r57-api: новый файл. Публичная точка входа пульта: собирает мост, буфер
#            лога, статус, очередь и HTTP-сервер; headless-режим с заглушкой
#            модальных окон (иначе ночью прогон встанет на диалоге).
# PREV: (нет файла)
# NEW FILE: video_maker/api/__init__.py

"""HTTP-пульт VideoMaker: управление прогонами, очередь, статус, логи."""
from __future__ import annotations

import logging
import os
import time

from .bridge import AppStatus, LogBuffer, TkBridge
from .queue import SeriesQueue
from .server import CONFIG_DIR, ApiRuntime, VideoMakerAPI, load_config

log = logging.getLogger(__name__)

__all__ = ["ApiRuntime", "install_headless_dialogs", "load_config", "start_api"]


def install_headless_dialogs() -> None:
    """Заглушить модальные окна: в headless их некому закрыть, и Tk встанет.

    Патчим атрибуты модуля `tkinter.messagebox` — app.py держит ссылку на сам
    модуль, поэтому подмена действует и там.
    """
    try:
        from tkinter import messagebox
    except ImportError:  # pragma: no cover
        return

    def _log_only(kind: str):
        def _fn(title=None, message=None, **_kw):
            log.error("[API/headless] %s: %s — %s", kind, title, message)
            return "ok"
        return _fn

    messagebox.showerror = _log_only("ошибка")       # type: ignore[assignment]
    messagebox.showwarning = _log_only("предупреждение")  # type: ignore[assignment]
    messagebox.showinfo = _log_only("сообщение")     # type: ignore[assignment]
    messagebox.askyesno = lambda *a, **k: True       # type: ignore[assignment]
    log.info("[API] модальные окна заменены логом (headless)")


def start_api(app, root, headless: bool = False, port: int | None = None):
    """Поднять пульт поверх работающего приложения.

    Возвращает ApiRuntime или None, если пульт выключен/не поднялся.
    """
    conf = load_config()
    if conf.get("enabled") is not True:
        log.info("[API] пульт выключен в %s", os.path.join(CONFIG_DIR, "api.json"))
        return None
    if port:
        conf["port"] = int(port)

    if headless:
        install_headless_dialogs()

    try:
        bridge = TkBridge(root)
        logbuf = LogBuffer()
        status = AppStatus(app, bridge, logbuf, started_at=time.time())
        queue = SeriesQueue(app, bridge, status, os.path.join(CONFIG_DIR, "queue.json"))
        api = VideoMakerAPI(app, bridge, status, queue, conf)
        url = api.start()
        if not url:
            return None
        queue.start()

        # Показать адреса в окне приложения + дать галочке «доступ из сети» рычаг
        def _lan_setter(want: bool) -> dict:
            code, payload = api.do_config_set({"lan": bool(want)})
            if code != 200:
                raise RuntimeError(payload.get("error") or "не удалось переключить доступ из сети")
            return api.urls()

        if hasattr(app, "_show_api_info"):
            # Мост нужен окну: из чужого потока Tk дёргать нельзя, а галочка
            # сети переключается как раз из потока.
            app._api_bridge = bridge
            try:
                bridge.call_async(lambda: app._show_api_info(api.urls(), _lan_setter))
            except Exception:  # подсказка в окне не критична
                log.warning("[API] не удалось показать адреса в окне", exc_info=True)

        return ApiRuntime(app, bridge, status, queue, api)
    except Exception:
        log.exception("[API] не удалось поднять пульт")
        return None
