"""Responsive Tkinter layout regression checks for CP22.

Run with a display, e.g. `xvfb-run -a pytest -q tests/test_gui_layout_cp22.py`.
The test skips cleanly in headless environments with no Tk display.
"""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path

import pytest

from video_maker.gui.app import App, COLORS


def _find_widget_by_var(root: tk.Misc, var: tk.Variable):
    wanted = str(var)
    found = []

    def walk(widget):
        try:
            if str(widget.cget("textvariable")) == wanted:
                found.append(widget)
        except (tk.TclError, TypeError):
            pass
        for child in widget.winfo_children():
            walk(child)

    walk(root)
    return found


def test_main_and_monitor_controls_fit_window_and_can_scroll():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    app = None
    modal = None
    try:
        app = App(root)
        root.deiconify()
        root.update_idletasks()
        root.update()

        assert len(app.notebook.tabs()) >= 3
        assert root.winfo_width() <= root.winfo_screenwidth()
        assert root.winfo_height() <= root.winfo_screenheight()
        assert app.start_btn.winfo_ismapped()
        assert app.cancel_btn.winfo_ismapped()
        for widget in (app.start_btn, app.cancel_btn, app.progress_bar):
            bottom = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height()
            assert bottom <= root.winfo_height(), (widget, bottom, root.winfo_height())

        # The fields that were previously packed into one line must occupy
        # separate rows and their labels must not be shortened by fixed width.
        horiz = _find_widget_by_var(root, app.broll_h_var)
        vert = _find_widget_by_var(root, app.broll_v_var)
        assert len(horiz) == len(vert) == 1
        assert horiz[0].winfo_rooty() != vert[0].winfo_rooty()
        broll_labels = [
            w.cget("text") for w in horiz[0].master.winfo_children()
            if isinstance(w, tk.Label) or w.winfo_class() == "TLabel"
        ]
        assert "Горизонтальный:" in broll_labels

        # Mouse-wheel routing is scoped to the scroll area under the pointer.
        main_canvas = app._scroll_canvases[0]
        root.update_idletasks()
        main_canvas.yview_moveto(0)
        before = main_canvas.yview()[0]
        event = type("WheelEvent", (), {
            "x_root": main_canvas.winfo_rootx() + 10,
            "y_root": main_canvas.winfo_rooty() + 10,
            "delta": -120,
            "num": None,
        })()
        app._on_scroll_wheel(event)
        after = main_canvas.yview()[0]
        assert after > before

        # Dedicated tab keeps log/API controls visible even at 1024x768.
        app.notebook.select(app.tab_monitor)
        root.update_idletasks()
        root.update()
        assert app.api_open_btn.winfo_ismapped()
        assert app.api_lan_cb.winfo_ismapped()
        for widget in (app.api_open_btn, app.api_lan_cb, app.log_text):
            bottom = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height()
            assert bottom <= root.winfo_height(), (widget, bottom, root.winfo_height())

        # Settings modal must keep its primary action in the visible bottom row.
        app._open_settings()
        root.update_idletasks()
        root.update()
        modal = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
        assert app._settings_save_btn.winfo_ismapped()
        bottom = app._settings_save_btn.winfo_rooty() - modal.winfo_rooty() + app._settings_save_btn.winfo_height()
        assert bottom <= modal.winfo_height(), (bottom, modal.winfo_height())
        assert app._settings_keys_box.cget("background") == COLORS["input_bg"]
        assert app._settings_prompt_box.cget("background") == COLORS["input_bg"]
        assert app._settings_prompt_box.cget("yscrollcommand")
    finally:
        if modal is not None:
            try:
                modal.destroy()
            except tk.TclError:
                pass
        try:
            root.destroy()
        except tk.TclError:
            pass
