from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from video_maker.api.bridge import TkBridge


class FakeRoot:
    def __init__(self):
        self.callbacks = []
        self.cancelled = []
        self.quit_called = False
        self.destroy_called = False

    def after(self, delay, fn):
        self.callbacks.append((delay, fn))
        return len(self.callbacks)

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def quit(self):
        self.quit_called = True

    def destroy(self):
        self.destroy_called = True


def _wait_for_queue(bridge: TkBridge, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    while bridge._q.empty() and time.monotonic() < deadline:
        time.sleep(0.002)
    assert not bridge._q.empty(), "bridge call was not queued"


def test_timed_out_pending_bridge_callback_never_executes_later():
    root = FakeRoot()
    bridge = TkBridge(root)
    called = []

    with pytest.raises(TimeoutError, match="не ответил"):
        bridge.call(lambda: called.append("side-effect"), timeout=0.01)

    # Process the Tk queue after the timeout. The cancelled request must be skipped.
    bridge._pump()
    assert called == []
    bridge.stop()


def test_bridge_stop_wakes_waiting_caller_and_cancels_pending_request():
    root = FakeRoot()
    bridge = TkBridge(root)
    called = []
    result = {}

    def caller():
        try:
            bridge.call(lambda: called.append(True), timeout=2.0)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=caller)
    thread.start()
    _wait_for_queue(bridge)
    bridge.stop()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert isinstance(result.get("error"), RuntimeError)
    assert called == []
    bridge._pump()
    assert called == []


def test_bridge_runs_callback_in_pump_and_propagates_result_and_exception():
    root = FakeRoot()
    bridge = TkBridge(root)
    results = {}

    def call_value():
        try:
            results["value"] = bridge.call(lambda: 42, timeout=1.0)
        except BaseException as exc:
            results["error"] = exc

    thread = threading.Thread(target=call_value)
    thread.start()
    _wait_for_queue(bridge)
    bridge._pump()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert results == {"value": 42}

    def call_exception():
        try:
            bridge.call(lambda: (_ for _ in ()).throw(ValueError("callback failure")), timeout=1.0)
        except BaseException as exc:
            results["exception"] = exc

    thread = threading.Thread(target=call_exception)
    thread.start()
    _wait_for_queue(bridge)
    bridge._pump()
    thread.join(timeout=1.0)
    assert isinstance(results.get("exception"), ValueError)
    assert str(results["exception"]) == "callback failure"
    bridge.stop()


def test_gui_close_waits_for_pipeline_worker_before_destroying_root(monkeypatch):
    from video_maker.gui import app as app_module
    from video_maker.gui.app import App
    from video_maker.engines import power

    root = FakeRoot()
    app = App.__new__(App)
    app.root = root
    app._closing = False
    app._close_pending = False
    app._close_wait_started = 0.0
    app._close_poll_after_id = None
    app._close_wait_warning_shown = False
    app._heartbeat_after_id = None
    app._imo_rescan_after_id = None
    app.running = True
    app.cancel_event = threading.Event()
    app._kill_ffmpeg_processes = lambda: None
    app._log = lambda *_args, **_kwargs: None
    app._save_settings = lambda: None
    app._api_bridge = None
    app._pipeline_thread = None

    worker_release = threading.Event()
    worker = threading.Thread(target=worker_release.wait)
    app._pipeline_thread = worker
    worker.start()

    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(power, "prevent_sleep_stop", lambda **_k: None)
    app._on_close()

    assert app.cancel_event.is_set()
    assert app._close_pending is True
    assert root.destroy_called is False

    # Simulate the worker finishing its cleanup/checkpoint work.
    app.running = False
    worker_release.set()
    worker.join(timeout=1.0)
    app._poll_close_until_idle()

    assert app._closing is True
    assert root.quit_called is True
    assert root.destroy_called is True


def test_gui_source_does_not_force_exit_process():
    from pathlib import Path
    import inspect
    from video_maker.gui.app import App

    source = inspect.getsource(App._finish_close)
    assert "os._exit" not in source
    assert "sys.exit" not in source
    assert "runtime.stop()" in source
