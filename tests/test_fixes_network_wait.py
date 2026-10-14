"""Регрессии: мягкое ожидание сетевых томов (/Volumes/...) перед стартом.

Приложение не должно ругаться сразу, если сетевой том ещё не подключён:
оно пытается его разбудить и ждёт появления путей, а «падающая» проверка
повторяется после ожидания.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import video_maker.gui.app as gui_app
from video_maker.api import control


def test_wait_returns_immediately_for_existing_paths(tmp_path):
    existing = tmp_path / "ready"
    existing.mkdir()
    started = time.monotonic()
    missing = gui_app._wait_for_paths([str(existing)], 2.0, log_fn=lambda m: None)
    assert missing == []
    assert time.monotonic() - started < 0.5


def test_wait_until_network_path_appears(tmp_path):
    target = tmp_path / "vol" / "clip.mp4"

    def create_later():
        time.sleep(0.4)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")

    worker = threading.Thread(target=create_later)
    worker.start()
    try:
        missing = gui_app._wait_for_paths([str(target)], 3.0, log_fn=lambda m: None)
    finally:
        worker.join(timeout=2)
    assert missing == [], "появившийся путь не должен считаться отсутствующим"


def test_wait_times_out_and_reports_missing(tmp_path):
    target = tmp_path / "never" / "appears.mp4"
    started = time.monotonic()
    missing = gui_app._wait_for_paths([str(target)], 1.0, log_fn=lambda m: None)
    elapsed = time.monotonic() - started
    assert missing == [str(target)]
    assert 0.9 <= elapsed < 2.5, f"ожидание должно уложиться в таймаут, вышло {elapsed:.1f}с"


def test_wait_calls_trigger_once(tmp_path):
    calls = []
    gui_app._wait_for_paths(
        [str(tmp_path / "gone")], 0.0,
        log_fn=lambda m: None, trigger=lambda: calls.append(True),
    )
    assert calls == [True]


def test_app_wake_returns_missing_list_without_crash():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        import pytest
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = None
    try:
        from video_maker.gui.app import App
        app = App(root)
        missing = app._wake_network_paths()
        assert isinstance(missing, list)
    finally:
        if app is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass


def test_wait_pokes_parent_directories_of_missing_paths(tmp_path, monkeypatch):
    seen = []
    real_listdir = gui_app.os.listdir

    def spy(path):
        seen.append(str(path))
        return real_listdir(path)

    monkeypatch.setattr(gui_app.os, "listdir", spy)
    parent = tmp_path / "share"
    parent.mkdir()
    target = parent / "deep" / "folder" / "file.mp4"
    gui_app._wait_for_paths([str(target)], 1.0, log_fn=lambda m: None)
    assert str(parent) in seen or str(tmp_path) in seen, \
        "ожидание обязано «трогать» существующих родителей, чтобы разбудить сетевую папку"


def test_deepest_existing_helper(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "file.mp4"
    (tmp_path / "a").mkdir()
    assert gui_app._deepest_existing(str(deep)) == str(tmp_path / "a")
    assert gui_app._deepest_existing(str(tmp_path / "a")) == str(tmp_path / "a")
    assert gui_app._deepest_existing("") == ""


def test_api_validate_waits_for_network_paths_before_failing():
    class FakeSettings:
        def __init__(self):
            self.calls = 0

        def validate(self):
            self.calls += 1
            if self.calls == 1:
                return ["Папка вывода не найдена: /Volumes/SSD/out"]
            return []

    class FakeApp:
        def __init__(self):
            self.settings = FakeSettings()
            self.wake_calls = []

        def _wake_network_paths(self, wait_seconds=0.0, try_open=False):
            self.wake_calls.append((wait_seconds, try_open))
            return ["/Volumes/SSD/out"]

    app = FakeApp()
    errors = control.validate(app)
    assert errors == [], "после успешного пробуждения повторная проверка должна быть чистой"
    assert app.wake_calls and app.wake_calls[-1][0] >= 10.0 and app.wake_calls[-1][1] is True
