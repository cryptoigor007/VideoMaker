"""r57-api.26: «абсолютно всё управление» в пульте — схема настроек, мост, маски.

Проверяет:
- SETTINGS_SCHEMA покрывает каждое поле SETTINGS_VAR_MAP и все поля реальны;
- IMO-пути/длительности доступны через API (мост расширен);
- read_settings отдаёт var-поля (IMO) и маскирует секреты;
- apply_settings не перезаписывает секреты маской «***» и принимает ключи списком.
"""
from __future__ import annotations

import pytest


def test_settings_schema_covers_every_mapped_field():
    from video_maker.api.control import SETTINGS_SCHEMA, SETTINGS_VAR_MAP

    schema_fields = {
        field["name"] for section in SETTINGS_SCHEMA for field in section["fields"]
    }
    missing = set(SETTINGS_VAR_MAP) - schema_fields
    assert not missing, f"поля не попали в схему пульта: {sorted(missing)}"


def test_settings_schema_fields_are_real():
    """Каждое поле схемы существует в приложении: настройка или GUI-переменная."""
    import dataclasses

    from video_maker.api.control import SETTINGS_SCHEMA, SETTINGS_VAR_MAP
    from video_maker.config.settings import Settings

    known_settings = {f.name for f in dataclasses.fields(Settings)}
    extra_ok = {"gemini_api_keys"}
    for section in SETTINGS_SCHEMA:
        for field in section["fields"]:
            name = field["name"]
            assert (
                name in SETTINGS_VAR_MAP or name in known_settings or name in extra_ok
            ), f"поле схемы не существует: {name}"


def test_settings_schema_field_types_valid():
    from video_maker.api.control import SETTINGS_SCHEMA

    allowed = {"text", "path", "bool", "number", "select", "secret", "textarea"}
    for section in SETTINGS_SCHEMA:
        assert section.get("title")
        assert section.get("icon"), f"у секции «{section.get('title')}» нет иконки"
        for field in section["fields"]:
            assert field.get("label"), field
            assert field.get("type") in allowed, field
            if field["type"] == "select":
                assert field.get("options"), field


def test_settings_schema_includes_imo_paths_and_durations():
    from video_maker.api.control import SETTINGS_SCHEMA, SETTINGS_VAR_MAP

    schema_fields = {
        field["name"] for section in SETTINGS_SCHEMA for field in section["fields"]
    }
    for name in ("h_intro_path", "h_intro_duration", "v_mid_path", "s_outro_duration"):
        assert name in SETTINGS_VAR_MAP, f"{name} нет в мосте"
        assert name in schema_fields, f"{name} нет в схеме"


def _make_app():
    import tkinter as tk

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


def test_read_settings_includes_var_backed_imo_fields():
    from video_maker.api.control import read_settings

    root, app = _make_app()
    try:
        app.h_intro_duration.set("7")
        data = read_settings(app)
        assert str(data.get("h_intro_duration")) == "7"
        assert "h_intro_path" in data
        assert "s_outro_duration" in data
    finally:
        root.destroy()


def test_read_settings_reflects_live_gui_var_edits():
    """Панель должна видеть живое состояние GUI, а не устаревший датакласс.

    Настройки синхронизируются из полей GUI только перед запуском пайплайна;
    до первого запуска read_settings обязан читать поля GUI, иначе пульт
    показывает пустые пути и может затереть реальные значения.
    """
    from video_maker.api.control import read_settings

    root, app = _make_app()
    try:
        app.audio_var.set("/tmp/live-audio.m4a")
        app.output_var.set("/tmp/live-output")
        data = read_settings(app)
        assert data.get("audio_path") == "/tmp/live-audio.m4a"
        assert data.get("output_folder") == "/tmp/live-output"
    finally:
        root.destroy()


def test_apply_settings_updates_imo_fields_via_vars():
    from video_maker.api.control import apply_settings

    root, app = _make_app()
    try:
        applied, unknown = apply_settings(
            app, {"h_intro_duration": "7", "s_outro_path": "/tmp/x.mp4"}
        )
        assert "h_intro_duration" in applied and "s_outro_path" in applied
        assert not unknown
        assert str(app.h_intro_duration.get()) == "7"
        assert app.s_outro_path.get() == "/tmp/x.mp4"
    finally:
        root.destroy()


def test_apply_settings_skips_masked_secret():
    """«***» в секретном поле = «не менять» — не перезаписываем ключ маской."""
    from video_maker.api.control import apply_settings

    root, app = _make_app()
    try:
        app.settings.gemini_api_keys = ["key-one"]
        app.settings.gemini_api_key = "key-one"
        applied, unknown = apply_settings(app, {"gemini_api_keys": "***"})
        assert "gemini_api_keys" not in applied
        assert "gemini_api_keys" not in unknown
        assert app.settings.gemini_api_keys == ["key-one"]
        assert app.settings.gemini_api_key == "key-one"
    finally:
        root.destroy()


def test_apply_settings_sets_gemini_keys_from_lines():
    from video_maker.api.control import apply_settings

    root, app = _make_app()
    try:
        applied, unknown = apply_settings(app, {"gemini_api_keys": "k1\nk2\n\nk3"})
        assert "gemini_api_keys" in applied
        assert not unknown
        assert app.settings.gemini_api_keys == ["k1", "k2", "k3"]
        assert app.settings.gemini_api_key == "k1"
    finally:
        root.destroy()


def test_settings_schema_route_serves_full_schema():
    """GET /api/settings/schema — живой маршрут: отдаёт полную схему формы настроек."""
    import json
    import urllib.request

    from video_maker.api.server import VideoMakerAPI

    class DummyQueue:
        def __init__(self):
            self.on_finish = None

        def snapshot(self):
            return {"items": []}

    class DummyStatus:
        def snapshot(self, log_tail=0):
            return {"running": False, "log_tail": []}

    class InlineBridge:
        def call(self, fn, timeout=30):
            return fn()

    class DummyApp:
        def __init__(self):
            self.running = False
            self.settings = object()

    conf = {"enabled": True, "port": 0, "lan": False,
            "token": "panel-test-token-0123456789abcdef", "webhook_url": ""}
    api = VideoMakerAPI(DummyApp(), InlineBridge(), DummyStatus(), DummyQueue(), conf)
    try:
        assert api.start()
        port = api._httpd.server_port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/settings/schema",
            headers={"X-Api-Key": conf["token"]},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        sections = (payload.get("schema") or {}).get("sections") or []
        names = {field["name"] for section in sections for field in section["fields"]}
        assert "h_intro_path" in names
        assert "gemini_api_keys" in names
        assert "s_speech_rate_target_wpm" in names
        assert len(sections) >= 8
    finally:
        stop = getattr(api, "stop", None)
        if callable(stop):
            stop()


def test_panel_contains_full_settings_form():
    """В пульте есть карточка «Настройки»: схема, форма, сохранение, перечитать."""
    from video_maker.api import webui

    html = webui.render()
    assert "Настройки (всё управление)" in html
    assert "/api/settings/schema" in html
    assert 'data-act="saveSettings"' in html
    assert 'data-act="reloadSettings"' in html
    assert 'id="settingsSections"' in html
    assert "*** = не менять" in html  # подсказка для секретов
    # Красиво и логично: поиск по настройкам, сетка полей, подсветка изменённых.
    assert 'id="settingsSearch"' in html
    assert 'id="settingsBar"' in html
    assert "sgrid" in html
    assert "updateDirty" in html
    # Режим embed не ломается.
    embedded = webui.render(embed=True)
    assert 'id="settingsSections"' in embedded
