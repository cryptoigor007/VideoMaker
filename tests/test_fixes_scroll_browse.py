"""Регрессии: стандартная маковская прокрутка колёсиком/трекпадом (включая
зону полосы прокрутки) и открытие диалогов «Обзор» в последнем выбранном месте.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.filedialog as filedialog
from pathlib import Path
from types import SimpleNamespace

import pytest

import video_maker.gui.app as gui_app
from video_maker.gui.app import App


def _make_app(width: str = "1024x720+30+30"):
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = App(root)
    root.deiconify()
    root.geometry(width)
    root.update_idletasks()
    root.update()
    return root, app


def _wheel(app, x_root, y_root, delta):
    app._on_scroll_wheel(
        SimpleNamespace(x_root=x_root, y_root=y_root, delta=delta, num=None)
    )


def test_wheel_over_scrollbar_scrolls_the_pane():
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        host = canvas.master
        # Точка в вертикальной полосе прокрутки, правее самого canvas.
        sx = host.winfo_rootx() + host.winfo_width() - 4
        sy = host.winfo_rooty() + host.winfo_height() // 2
        before = canvas.yview()[0]
        _wheel(app, sx, sy, -3)
        after = canvas.yview()[0]
        assert after > before, "колесо над полосой прокрутки должно крутить панель"
    finally:
        root.destroy()


def test_wheel_step_scales_with_delta():
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        cx = canvas.winfo_rootx() + canvas.winfo_width() // 2
        cy = canvas.winfo_rooty() + canvas.winfo_height() // 2

        def scroll_by(delta):
            canvas.yview_moveto(0.0)
            root.update()
            _wheel(app, cx, cy, delta)
            root.update()
            return canvas.yview()[0]

        small = scroll_by(-2)
        big = scroll_by(-8)
        assert small > 0, "маленькая дельта трекпада тоже должна прокручивать"
        assert big > small * 1.5, f"шаг должен расти с дельтой: small={small} big={big}"
    finally:
        root.destroy()


def test_wheel_over_widget_scrolls_via_widget_chain():
    """Прокрутка над содержимым панели не должна зависеть от точных координат."""
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        before = canvas.yview()[0]
        widget = app.series_entry  # глубокий ребёнок внутри прокручиваемой панели
        app._on_scroll_wheel(
            SimpleNamespace(x_root=0, y_root=0, delta=-2, num=None, widget=widget)
        )
        assert canvas.yview()[0] > before, "колесо над полем должно крутить панель настроек"
    finally:
        root.destroy()


def test_browse_dir_uses_current_field_path_as_start(tmp_path, monkeypatch):
    root, app = _make_app()
    try:
        target = tmp_path / "broll"
        target.mkdir()
        app.broll_h_var.set(str(target))
        calls = []

        def fake_choose_folder(initial_dir, title, on_done):
            calls.append((initial_dir, title, on_done))

        monkeypatch.setattr(app, "_choose_folder", fake_choose_folder)
        app._browse_dir(app.broll_h_var, "broll_h_var")
        assert calls and calls[0][0] == str(target), \
            "диалог должен стартовать в папке из поля"
        calls[0][2](str(target))  # имитируем выбор пользователя
        assert app.broll_h_var.get() == str(target)
    finally:
        root.destroy()


def test_browse_file_uses_file_parent_as_start(tmp_path, monkeypatch):
    root, app = _make_app()
    try:
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"x")
        app.cover_h_var.set(str(media))
        calls = []

        def fake_choose_file(initial_dir, title, on_done, filetypes=None):
            calls.append((initial_dir, title, on_done))

        monkeypatch.setattr(app, "_choose_file", fake_choose_file)
        app._browse_file(app.cover_h_var, "cover_h_var")
        assert calls and calls[0][0] == str(tmp_path), \
            "для файла диалог стартует в его папке"
        calls[0][2](str(media))
        assert app.cover_h_var.get() == str(media)
    finally:
        root.destroy()


def test_browse_remembers_last_selected_folder(tmp_path, monkeypatch):
    root, app = _make_app()
    try:
        first = tmp_path / "one"
        first.mkdir()
        second = tmp_path / "two"
        second.mkdir()
        calls = []
        answers = [str(first), str(second)]

        def fake_choose_folder(initial_dir, title, on_done):
            calls.append(initial_dir)
            on_done(answers.pop(0))

        monkeypatch.setattr(app, "_choose_folder", fake_choose_folder)
        app.broll_h_var.set("")
        app._browse_dir(app.broll_h_var, "broll_h_var")
        assert app.broll_h_var.get() == str(first)

        app.output_var.set("")
        app._browse_dir(app.output_var, "output_var")
        assert calls[1] == str(first), "пустое поле стартует в последней выбранной папке"
        assert app.output_var.get() == str(second)
    finally:
        root.destroy()


def test_osascript_script_sets_default_location_with_escaping():
    script = App._osascript_choose_script("folder", '/tmp/Каталог "X"', 'Заголовок "T"')
    assert "choose folder" in script
    assert 'default location (POSIX file "/tmp/Каталог \\"X\\"")' in script
    assert 'with prompt "Заголовок \\"T\\""' in script
    file_script = App._osascript_choose_script("file", "", "Выберите файл")
    assert "choose file" in file_script
    assert "default location" not in file_script


class _FakeProc:
    def __init__(self, rc, out="", err=""):
        import io
        self._rc = rc
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)

    def poll(self):
        return self._rc

    def kill(self):
        pass


def _pack_touchpad(dx, dy):
    """Упаковка дельт TouchpadScroll как в Tk 9: Δx в старших 16 битах, Δy в младших."""
    return ((dx & 0xFFFF) << 16) | (dy & 0xFFFF)


def test_touchpad_scroll_binding_installed():
    """Tk 9: трекпад/Magic Mouse шлют <TouchpadScroll> — привязка обязана быть."""
    root, app = _make_app()
    try:
        try:
            script = root.bind_all("<TouchpadScroll>")
        except tk.TclError:  # pragma: no cover — Tk 8.6
            pytest.skip("Tk без TouchpadScroll")
        assert script, "нет глобальной привязки <TouchpadScroll>"
    finally:
        root.destroy()


def test_touchpad_scroll_moves_area_under_pointer():
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        canvas.yview_moveto(0.0)
        root.update()
        before = canvas.yview()[0]
        event = SimpleNamespace(
            widget=canvas,
            x_root=canvas.winfo_rootx() + 40,
            y_root=canvas.winfo_rooty() + 60,
            delta=_pack_touchpad(0, -8),
            num=None,
        )
        result = app._on_touchpad_scroll(event)
        root.update()
        assert result == "break"
        assert canvas.yview()[0] > before, "Δy<0 (жест вниз) должен прокручивать вниз"
    finally:
        root.destroy()


def test_touchpad_scroll_positive_delta_moves_up():
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        canvas.yview_moveto(0.5)
        root.update()
        before = canvas.yview()[0]
        event = SimpleNamespace(
            widget=canvas,
            x_root=canvas.winfo_rootx() + 40,
            y_root=canvas.winfo_rooty() + 60,
            delta=_pack_touchpad(0, 8),
            num=None,
        )
        result = app._on_touchpad_scroll(event)
        root.update()
        assert result == "break"
        assert canvas.yview()[0] < before, "Δy>0 (жест вверх) должен прокручивать вверх"
    finally:
        root.destroy()


def test_touchpad_horizontal_only_does_not_scroll_vertically():
    root, app = _make_app()
    try:
        canvas = app._scroll_canvases[0]
        canvas.yview_moveto(0.3)
        root.update()
        before = canvas.yview()[0]
        event = SimpleNamespace(
            widget=canvas,
            x_root=canvas.winfo_rootx() + 40,
            y_root=canvas.winfo_rooty() + 60,
            delta=_pack_touchpad(-6, 0),
            num=None,
        )
        result = app._on_touchpad_scroll(event)
        root.update()
        assert result is None
        assert canvas.yview()[0] == before
    finally:
        root.destroy()


def test_poll_osascript_delivers_path_and_handles_cancel():
    root, app = _make_app()
    try:
        results = []
        app._poll_osascript(_FakeProc(0, "/Users/x/папка/\n"), results.append)
        assert results == ["/Users/x/папка"], "папка приходит без завершающего слэша"
        app._poll_osascript(_FakeProc(1, "", "execution error: User canceled. (-128)"),
                            results.append)
        assert results[-1] == "", "отмена пользователя — пустой результат без ошибки"
    finally:
        root.destroy()
