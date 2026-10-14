"""Ползунок скорости речи в GUI: режим «слова/мин» ↔ «коэффициент» и сбор в настройки."""
from __future__ import annotations

import tkinter as tk

import pytest


def _make_app():
    from video_maker.gui.app import App

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = App(root)
    root.deiconify()
    root.update_idletasks()
    root.update()
    return root, app


def test_speech_slider_collects_mode_and_values():
    from video_maker.gui.app import collect_settings_from_gui

    root, app = _make_app()
    try:
        app.speech_rate_mode_var.set("По коэффициенту")
        app.speech_rate_coefficient_var.set(1.2)
        collect_settings_from_gui(app)
        assert app.settings.speech_rate_mode == "coefficient"
        assert abs(app.settings.speech_rate_coefficient - 1.2) < 1e-9

        app.speech_rate_mode_var.set("По словам/мин")
        app.speech_rate_target_wpm_var.set(135)
        collect_settings_from_gui(app)
        assert app.settings.speech_rate_mode == "wpm"
        assert abs(app.settings.speech_rate_target_wpm - 135.0) < 1e-9
    finally:
        root.destroy()


def test_speech_slider_switches_range_with_mode():
    root, app = _make_app()
    try:
        app.speech_rate_mode_var.set("По коэффициенту")
        app._on_speech_rate_mode_change()
        assert abs(float(app.speech_rate_slider.cget("to")) - 1.5) < 1e-9

        app.speech_rate_mode_var.set("По словам/мин")
        app._on_speech_rate_mode_change()
        assert abs(float(app.speech_rate_slider.cget("to")) - 200.0) < 1e-9
    finally:
        root.destroy()


def test_cut_extra_words_checkbox_collects():
    """Галочка «Вырезать повторы/лишние слова» должна попадать в настройки."""
    from video_maker.gui.app import collect_settings_from_gui

    root, app = _make_app()
    try:
        app.cut_extra_words_var.set(True)
        collect_settings_from_gui(app)
        assert app.settings.cut_extra_words is True
        app.cut_extra_words_var.set(False)
        collect_settings_from_gui(app)
        assert app.settings.cut_extra_words is False
    finally:
        root.destroy()


def test_run_pipeline_passes_speech_rate_settings_into_ctx(monkeypatch, tmp_path):
    """Регрессия: поля скорости речи обязаны попадать в PipelineContext.

    Без этого ползунок менял настройки, но пайплайн получал дефолты (120 WPM).
    """
    import copy as _copy

    import video_maker.gui.app as gui_app

    root, app = _make_app()
    try:
        audio = tmp_path / "a.m4a"
        audio.write_bytes(b"x")
        out = tmp_path / "out"
        out.mkdir()
        app._audio_queue = [str(audio)]
        app.settings.audio_path = str(audio)
        app.settings.output_folder = str(out)
        app.settings.prevent_sleep = False
        app.settings.speech_rate_mode = "coefficient"
        app.settings.speech_rate_target_wpm = 130.0
        app.settings.speech_rate_coefficient = 1.15
        app._pipeline_snapshot = {
            "settings": _copy.deepcopy(app.settings),
            "packaging_opts": {},
            "caption_style": "auto_aisie",
            "hook_style": "auto_aisie",
            "imo": {},
        }

        captured = {}

        class StopCapture(Exception):
            pass

        class RecordingCtx:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                raise StopCapture()

        monkeypatch.setattr(gui_app, "PipelineContext", RecordingCtx)
        try:
            app._run_pipeline()
        except StopCapture:
            pass
        assert captured.get("speech_rate_mode") == "coefficient"
        assert captured.get("speech_rate_target_wpm") == 130.0
        assert captured.get("speech_rate_coefficient") == 1.15
    finally:
        root.destroy()
