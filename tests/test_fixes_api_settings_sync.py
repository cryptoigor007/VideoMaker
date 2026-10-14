"""API-запуск (POST /api/run) обязан синхронизировать app.settings из GUI-полей.

Баг: do_run валидировал app.settings ДО синхронизации — объект содержал дефолты
(пустые broll_horizontal / vertical_background / bgm_*_folder), пока пользователь
не нажимал «Создать видео» в GUI. API-запуск с частичными оверрайдами отклонялся
(«Не выбрана папка B-roll горизонтальный» и т.п.), хотя в полях/файле настройки есть.
"""
from __future__ import annotations

from types import SimpleNamespace

_TOGGLES = (
    "h_intro", "h_middle", "h_outro", "h_hooks", "h_subs", "h_strong",
    "v_intro", "v_middle", "v_outro", "v_hooks", "v_subs", "v_strong",
    "s_intro", "s_middle", "s_outro", "s_hooks", "s_subs", "s_strong",
)


class _FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeApp(SimpleNamespace):
    """Приложение без Tk: неизвестные *_var и галочки отдают пустой FakeVar."""

    def __getattr__(self, name):
        if name.endswith("_var") or name in _TOGGLES:
            return _FakeVar("")
        raise AttributeError(name)


def _settings():
    from video_maker.config.settings import Settings

    return Settings(gemini_api_key="test-key")


def test_sync_settings_from_gui_tolerates_minimal_app():
    """Урезанный app без GUI-полей (тестовые дубли API) не должен ронять do_run."""
    from types import SimpleNamespace

    from video_maker.api.control import sync_settings_from_gui

    sync_settings_from_gui(SimpleNamespace(settings=object()))  # не должно бросить


def test_collect_settings_from_gui_fills_sources():
    """Синхронизация переносит пути из полей GUI в объект настроек."""
    from video_maker.gui.app import collect_settings_from_gui

    app = _FakeApp(settings=_settings())
    app.audio_var = _FakeVar("/tmp/a.m4a")
    app.output_var = _FakeVar("/tmp/out")
    app.broll_h_var = _FakeVar("/tmp/broll")
    app.bg_var = _FakeVar("/tmp/bg.jpg")
    app.bgm_youtube_folder_var = _FakeVar("/tmp/bgm")
    app.model_var = _FakeVar("gemini-flash-latest")

    collect_settings_from_gui(app)

    assert app.settings.audio_path == "/tmp/a.m4a"
    assert app.settings.output_folder == "/tmp/out"
    assert app.settings.broll_horizontal == "/tmp/broll"
    assert app.settings.vertical_background == "/tmp/bg.jpg"
    assert app.settings.bgm_youtube_folder == "/tmp/bgm"
    assert app.settings.gemini_model == "gemini-flash-latest"


def test_api_run_path_validates_after_gui_sync(tmp_path):
    """Полный путь API-запуска: apply_settings → sync → validate без ошибок."""
    from video_maker.api.control import apply_settings, sync_settings_from_gui, validate

    broll = tmp_path / "broll"
    broll.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    bg = tmp_path / "bg.jpg"
    bg.write_bytes(b"x")
    bgm = tmp_path / "bgm"
    bgm.mkdir()
    (bgm / "track.mp3").write_bytes(b"x")

    app = _FakeApp(settings=_settings())
    app.audio_var = _FakeVar(str(audio))
    app.output_var = _FakeVar(str(out))
    app.broll_h_var = _FakeVar(str(broll))
    app.bg_var = _FakeVar(str(bg))
    app.bgm_youtube_folder_var = _FakeVar(str(bgm))
    app.bgm_youtube_enable_var = _FakeVar(True)
    app.model_var = _FakeVar("gemini-flash-latest")
    app.v_hooks = _FakeVar(True)  # вертикаль включена → требуется vertical_background

    # как API: оверрайдов нет — всё нужное уже в полях GUI
    apply_settings(app, {})
    sync_settings_from_gui(app)
    assert validate(app) == []
