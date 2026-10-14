"""Регрессии r58 для GUI: переподключение живого буфера лога и состояние
блока «Пульт», когда HTTP-пульт выключен."""
from __future__ import annotations

import sys
import tkinter as tk
from types import SimpleNamespace

import pytest

from video_maker.gui.app import App


def _make_app():
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = App(root)
    return root, app


def test_reinstalls_log_hook_for_running_api():
    root, app = _make_app()
    calls = []
    fake_runtime = SimpleNamespace(status=SimpleNamespace(
        install_log_hook=lambda: calls.append(True)))
    main_module = sys.modules["__main__"]
    had_runtime = hasattr(main_module, "_API_RUNTIME")
    previous = getattr(main_module, "_API_RUNTIME", None)
    main_module._API_RUNTIME = fake_runtime
    try:
        assert hasattr(app, "_reinstall_log_hook"), "нет восстановления буфера лога"
        app._reinstall_log_hook()
        assert calls == [True], "живой буфер лога не переподключён после перенастройки logging"
        main_module._API_RUNTIME = None
        app._reinstall_log_hook()
        assert calls == [True], "без пульта восстановление ничего не делает"
    finally:
        if had_runtime:
            main_module._API_RUNTIME = previous
        else:
            delattr(main_module, "_API_RUNTIME")
        root.destroy()


def test_api_off_disables_lan_and_open_controls():
    root, app = _make_app()
    try:
        app._show_api_info({"enabled": False, "local": "", "network": "", "lan": False})
        root.update()
        assert app.api_lan_cb.instate(["disabled"]), "галочка сети должна быть недоступна"
        assert app.api_lan_cb_main.instate(["disabled"])
        assert app.api_open_btn.instate(["disabled"])
        assert app.api_open_btn_main.instate(["disabled"])
        assert "выключен" in app.api_label.cget("text").lower()

        app._show_api_info({"enabled": True, "local": "http://127.0.0.1:9/?key=k",
                            "network": "", "lan": False})
        root.update()
        assert not app.api_lan_cb.instate(["disabled"]), "включённый пульт снова доступен"
        assert not app.api_lan_cb_main.instate(["disabled"])
    finally:
        root.destroy()
