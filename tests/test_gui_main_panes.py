"""Проверки двухпанельной главной вкладки: настройки слева, пульт+журнал справа.

Левая панель — со своим ползунком прокрутки (существующий scroll-canvas),
правая — стационарная, растягивается по высоте. Дубли вкладки «Журнал и пульт»
делят состояние: одна галочка сети, одна команда кнопок, один поток лога.
"""
from __future__ import annotations

import tkinter as tk

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


def test_main_tab_has_settings_and_monitor_panes():
    root, app = _make_app()
    try:
        assert hasattr(app, "main_panes"), "главная вкладка не содержит панельного разделителя"
        panes = [str(pane) for pane in app.main_panes.panes()]
        assert len(panes) == 2, f"ожидалось две панели, получено: {panes}"
        assert str(app.settings_pane) in panes, "левая панель настроек отсутствует"
        assert str(app.monitor_pane) in panes, "правая панель пульта/журнала отсутствует"
        # Левая панель остаётся прокручиваемой, правая — нет.
        assert app._scroll_canvases, "левая панель потеряла прокрутку настроек"
        assert str(app.settings_pane) in str(app._scroll_canvases[0])
    finally:
        root.destroy()


def test_log_goes_to_both_main_and_monitor_widgets():
    root, app = _make_app()
    try:
        assert hasattr(app, "log_text_main"), "в правой панели нет виджета журнала"
        assert app.log_text is not app.log_text_main
        app._log("ПРОВЕРКА-СПЛИТ-123")
        root.update()
        for name, widget in (("правая панель", app.log_text_main), ("вкладка", app.log_text)):
            assert "ПРОВЕРКА-СПЛИТ-123" in widget.get("1.0", "end"), f"лог не дошёл: {name}"
    finally:
        root.destroy()


def test_api_controls_and_label_are_shared_between_panes():
    root, app = _make_app()
    try:
        app._show_api_info({
            "local": "http://127.0.0.1:9999/?key=тест",
            "network": "",
            "lan": False,
            "enabled": True,
        })
        root.update()
        assert "127.0.0.1:9999" in app.api_label.cget("text"), "вкладка не обновила адрес"
        assert "127.0.0.1:9999" in app.api_label_main.cget("text"), "правая панель не обновила адрес"
        # Обе галочки привязаны к одной переменной — состояние не разъедется.
        assert str(app.api_lan_cb.cget("variable")) == str(app.api_lan_var)
        assert str(app.api_lan_cb_main.cget("variable")) == str(app.api_lan_var)
        assert hasattr(app, "api_open_btn_main"), "в правой панели нет кнопки «Открыть пульт»"
    finally:
        root.destroy()


def test_monitoring_tab_is_still_present():
    root, app = _make_app()
    try:
        assert str(app.tab_monitor) in [str(tab) for tab in app.notebook.tabs()]
    finally:
        root.destroy()


def test_series_field_stays_in_left_column_when_window_widens():
    """Широкое окно не должно переносить «Название серии» в правую колонку."""
    root, app = _make_app()
    try:
        root.deiconify()
        root.geometry("1900x900+20+20")
        root.update_idletasks()
        root.update()
        canvas = app._scroll_canvases[0]
        offset = app.series_entry.winfo_rootx() - canvas.winfo_rootx()
        width_ratio = app.series_entry.winfo_width() / max(1, canvas.winfo_width())
        assert 0 <= offset < 80, f"поле серии уехало в правую колонку (offset={offset})"
        assert width_ratio > 0.5, f"поле серии сжалось в правую колонку (width={width_ratio:.2f})"
    finally:
        root.destroy()
