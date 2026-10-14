"""Общая изоляция тестов.

Исторический баг: GUI-тесты создают настоящий App(), который читает и пишет
реальный ~/.video_maker_settings.json; «browse»-тесты оставляли в нём
pytest-пути (output, broll_h, cover_h, last_browse_dir) — каждый прогон
тестов портил пользовательские настройки.

Перенаправляем SETTINGS_FILE в tmp_path на время каждого теста: ни один тест
не должен читать или писать реальные пользовательские настройки.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_settings(tmp_path, monkeypatch):
    import video_maker.gui.app as gui_app

    monkeypatch.setattr(gui_app, "SETTINGS_FILE", str(tmp_path / "settings.json"))
