"""Стандартные средства macOS: строка меню с «Правкой» (⌘C/V/X/A/Z) и «Окном».

На Aqua Tk у текстовых полей нет Command-привязок — копирование и вставка
работают только через меню приложения, как в системных программах.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

from video_maker.gui.app import App


def _make_app():
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


def _menu_labels(menu) -> list[str]:
    labels = []
    for i in range(menu.index("end") + 1):
        try:
            if menu.type(i) == "separator":
                continue
            labels.append(menu.entrycget(i, "label"))
        except tk.TclError:
            continue
    return labels


def _invoke(menu, label: str) -> None:
    for i in range(menu.index("end") + 1):
        try:
            if menu.type(i) != "separator" and menu.entrycget(i, "label") == label:
                menu.invoke(i)
                return
        except tk.TclError:
            continue
    raise AssertionError(f"пункт меню не найден: {label}")


def test_edit_menu_has_standard_items():
    root, app = _make_app()
    try:
        labels = _menu_labels(app._mac_menu_edit)
        for needed in ("Отменить", "Повторить", "Вырезать", "Копировать",
                       "Вставить", "Выделить всё"):
            assert needed in labels, f"в меню «Правка» нет пункта «{needed}»"
    finally:
        root.destroy()


def test_select_all_and_copy_via_edit_menu():
    root, app = _make_app()
    try:
        entry = app.series_entry
        entry.delete("1.0", "end")
        entry.insert("1.0", "ПРИВЕТ-КОПИРОВАНИЕ")
        entry.focus_force()
        root.update()

        _invoke(app._mac_menu_edit, "Выделить всё")
        root.update()
        assert entry.tag_ranges("sel"), "«Выделить всё» должно выделять текст поля"

        _invoke(app._mac_menu_edit, "Копировать")
        root.update()
        # Text всегда хранит завершающий \n; он попадает и в выделение, и в буфер.
        assert root.clipboard_get().rstrip("\n") == "ПРИВЕТ-КОПИРОВАНИЕ", \
            "«Копировать» должно копировать"
    finally:
        root.destroy()


def test_paste_via_edit_menu():
    root, app = _make_app()
    try:
        root.clipboard_clear()
        root.clipboard_append("ВСТАВЛЕННЫЙ ТЕКСТ")
        root.update()
        entry = app.series_entry
        entry.delete("1.0", "end")
        entry.focus_force()
        root.update()
        _invoke(app._mac_menu_edit, "Вставить")
        root.update()
        assert entry.get("1.0", "end-1c") == "ВСТАВЛЕННЫЙ ТЕКСТ", "«Вставить» должно вставлять"
    finally:
        root.destroy()


def test_close_window_menu_closes_focused_dialog_only():
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        entry = ttk.Entry(dialog)
        entry.pack()
        dialog.update()
        entry.focus_force()
        root.update()
        _invoke(app._mac_menu_window, "Закрыть окно")
        root.update()
        assert not dialog.winfo_exists(), "⌘W должен закрывать окно с фокусом"
        assert root.winfo_exists(), "главное окно не должно закрываться вместе с диалогом"
    finally:
        root.destroy()


def test_command_c_key_copies_from_dialog_window():
    """⌘C прямо с клавиатуры (без меню) обязан работать и в окнах-диалогах."""
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        text = tk.Text(dialog, width=30, height=4)
        text.pack()
        text.insert("1.0", "ИЗ-ДИАЛОГА")
        dialog.update()
        text.focus_force()
        root.update()
        text.tag_add("sel", "1.0", "end-1c")

        root.clipboard_clear()
        root.clipboard_append("затравка")
        root.update()
        text.event_generate("<Command-c>")
        root.update()
        assert root.clipboard_get().rstrip("\n") == "ИЗ-ДИАЛОГА", \
            "⌘C в диалоге должен копировать выделение"
    finally:
        root.destroy()


def test_command_v_key_pastes_into_dialog_window():
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        entry = ttk.Entry(dialog)
        entry.pack()
        dialog.update()
        entry.focus_force()
        root.update()
        root.clipboard_clear()
        root.clipboard_append("ВСТАВКА-ИЗ-БУФЕРА")
        root.update()

        entry.event_generate("<Command-v>")
        root.update()
        assert entry.get() == "ВСТАВКА-ИЗ-БУФЕРА", \
            "⌘V в диалоге должен вставлять из буфера"
    finally:
        root.destroy()


def test_command_a_selects_all_in_dialog_window():
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        text = tk.Text(dialog, width=30, height=4)
        text.pack()
        text.insert("1.0", "выделить всё в диалоге")
        dialog.update()
        text.focus_force()
        root.update()

        text.event_generate("<Command-a>")
        root.update()
        assert text.tag_ranges("sel"), "⌘A в диалоге должен выделять весь текст"
    finally:
        root.destroy()


def test_edit_menu_targets_dialog_widget_even_when_root_focus_is_main():
    """Реальный кейс macOS: root.focus_get() «не видит» фокус диалога, а меню
    «Правка» обязано адресовать событие последнему сфокусированному виджету."""
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        text = tk.Text(dialog, width=30, height=4)
        text.pack()
        text.insert("1.0", "ДИАЛОГ")
        dialog.update()
        text.focus_force()
        root.update()
        text.tag_add("sel", "1.0", "end-1c")

        root.clipboard_clear()
        root.clipboard_append("затравка")
        # имитируем реальный macOS: фокус формально на главном окне
        app.root.focus_set()
        root.update()

        _invoke(app._mac_menu_edit, "Копировать")
        root.update()
        assert root.clipboard_get().rstrip("\n") == "ДИАЛОГ", \
            "меню «Копировать» должно копировать из диалога, а не из главного окна"
    finally:
        root.destroy()


def test_close_window_menu_closes_dialog_even_when_root_focus_is_main():
    """⌘W при открытом диалоге должен закрывать диалог, а не главное окно."""
    root, app = _make_app()
    try:
        dialog = tk.Toplevel(root)
        entry = ttk.Entry(dialog)
        entry.pack()
        dialog.update()
        entry.focus_force()
        root.update()

        app.root.focus_set()  # фокус формально на главном окне (как на macOS)
        root.update()

        _invoke(app._mac_menu_window, "Закрыть окно")
        root.update()
        assert not dialog.winfo_exists(), "должен закрыться диалог"
        assert root.winfo_exists(), "главное окно должно остаться"
    finally:
        import contextlib
        with contextlib.suppress(tk.TclError):
            root.destroy()
