"""Панель «Кинематографичный монтаж» для GUI VideoMaker.

Не импортирует tkinter на уровне модуля — удобно для unit-тестов без дисплея.
Вызывать: create_cinematic_frame(parent, get_settings=..., set_settings=...).
"""

from __future__ import annotations

from typing import Any, Callable

from .config import CinematicSettings


# Подписи на русском — в стиле остальных вкладок VideoMaker
PROFILE_LABELS = {
    "psychological_dark": "Тёмный документальный",
    "cinematic_documentary": "Кинематографичный",
    "dynamic_shorts": "Динамичные Shorts",
    "clean_explainer": "Чистый explainer",
    "custom": "Свой",
}

MOTION_LABELS = {
    "off": "Выкл",
    "low": "Слабое",
    "medium": "Среднее",
    "high": "Сильное",
}

TRANSITION_LABELS = {
    "modern_mix": "Современный микс (рекомендуется)",
    "hard_only": "Только склейки",
    "soft_dissolve": "Растворение",
    "fade_to_black": "Затемнение",
    "all_safe": "Все безопасные",
}

FRAMING_LABELS = {
    "center": "Центр",
    "safe_roi": "Безопасный ROI",
    "manual": "Вручную",
}

# Порядок в combobox
PROFILE_ORDER = list(PROFILE_LABELS.keys())
MOTION_ORDER = list(MOTION_LABELS.keys())
TRANSITION_ORDER = list(TRANSITION_LABELS.keys())
KINETIC_ORDER = ["off", "subtle", "bold", "title"]
KINETIC_LABELS = {
    "off": "Выкл",
    "subtle": "Кинетика: мягкая",
    "bold": "Кинетика: яркая",
    "title": "Кинетика: заголовки",
}

FRAMING_ORDER = list(FRAMING_LABELS.keys())


def settings_to_gui_values(s: CinematicSettings) -> dict[str, Any]:
    return {
        "enabled": s.enabled,
        "profile": s.profile,
        "motion_intensity": s.motion_intensity,
        "plan_tempo": s.plan_tempo,
        "color_intensity": s.color_intensity,
        "allowed_transitions": s.allowed_transitions,
        "framing_mode": s.framing_mode,
        "reuse_existing_subs": s.reuse_existing_subs,
        "max_zoom": s.max_zoom,
        "transition_sfx": bool(getattr(s, "transition_sfx", False)),
    }


def gui_values_to_settings(values: dict[str, Any]) -> CinematicSettings:
    return CinematicSettings.from_dict(values)


def create_cinematic_frame(
    parent: Any,
    *,
    get_settings: Callable[[], CinematicSettings],
    set_settings: Callable[[CinematicSettings], None],
    on_preview: Callable[[], None] | None = None,
) -> Any:
    """
    Собрать ttk.Labelframe с элементами управления.
    Только в GUI-потоке. Тяжёлые операции — в worker VideoMaker.
    """
    import tkinter as tk
    from tkinter import ttk

    outer = ttk.Frame(parent, padding=4)
    frame = ttk.Labelframe(outer, text="  Кинематографичный монтаж  ", padding=12)
    frame.pack(fill="both", expand=True)

    s = get_settings()
    enabled_var = tk.BooleanVar(value=s.enabled)
    profile_var = tk.StringVar(value=s.profile)
    motion_var = tk.StringVar(value=s.motion_intensity)
    tempo_var = tk.DoubleVar(value=float(s.plan_tempo))
    color_var = tk.DoubleVar(value=float(s.color_intensity))
    zoom_var = tk.DoubleVar(value=float(s.max_zoom))
    trans_var = tk.StringVar(value=s.allowed_transitions)
    kinetic_var = tk.StringVar(value=getattr(s, "kinetic_typography", "off") or "off")
    framing_var = tk.StringVar(value=s.framing_mode)
    subs_var = tk.BooleanVar(value=s.reuse_existing_subs)
    sfx_var = tk.BooleanVar(value=bool(getattr(s, 'transition_sfx', False)))
    smart_var = tk.BooleanVar(value=bool(getattr(s, "smart_broll", True)))
    kw_var = tk.BooleanVar(value=bool(getattr(s, "keyword_cuts", True)))
    tempo_label = tk.StringVar(value=f"{s.plan_tempo:.1f}")
    color_label = tk.StringVar(value=f"{s.color_intensity:.0%}")
    zoom_label = tk.StringVar(value=f"{s.max_zoom:.2f}×")

    def _apply(*_args: Any) -> None:
        try:
            tempo = float(tempo_var.get())
        except (tk.TclError, ValueError, TypeError):
            tempo = 1.0
        try:
            color = float(color_var.get())
        except (tk.TclError, ValueError, TypeError):
            color = 0.5
        try:
            zoom = float(zoom_var.get())
        except (tk.TclError, ValueError, TypeError):
            zoom = 1.25
        tempo_label.set(f"{tempo:.1f}")
        color_label.set(f"{color:.0%}")
        zoom_label.set(f"{zoom:.2f}×")
        new = CinematicSettings(
            enabled=bool(enabled_var.get()),
            profile=str(profile_var.get()),  # type: ignore[arg-type]
            motion_intensity=str(motion_var.get()),  # type: ignore[arg-type]
            plan_tempo=tempo,
            color_intensity=color,
            allowed_transitions=str(trans_var.get()),  # type: ignore[arg-type]
            framing_mode=str(framing_var.get()),  # type: ignore[arg-type]
            reuse_existing_subs=bool(subs_var.get()),
            max_zoom=zoom,
            kinetic_typography=str(kinetic_var.get() or "off"),
            seed=s.seed,
            transition_sfx=bool(sfx_var.get()),
            smart_broll=bool(smart_var.get()),
            keyword_cuts=bool(kw_var.get()),
        )
        if not new.validate():
            set_settings(new)

    # --- row 0: master switch ---
    ttk.Checkbutton(
        frame,
        text="Включить кинематографичный монтаж (только 4K)",
        variable=enabled_var,
        command=_apply,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

    ttk.Label(
        frame,
        text="Master: 3840×2160 (H) или 2160×3840 (V). Другие размеры — обычная склейка.",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

    ttk.Separator(frame, orient="horizontal").grid(
        row=3, column=0, columnspan=3, sticky="ew", pady=(0, 10)
    )

    def _row_label(r: int, text: str) -> None:
        ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=4)

    def _labeled_combo(
        r: int,
        key_var: tk.StringVar,
        order: list[str],
        labels: dict[str, str],
    ) -> None:
        """Combobox shows Russian labels; stores stable English keys in key_var."""
        label_to_key = {labels[k]: k for k in order}
        display_var = tk.StringVar(value=labels.get(key_var.get(), key_var.get()))

        def _on_select(_event=None) -> None:
            chosen = display_var.get()
            key = label_to_key.get(chosen, chosen)
            if key != key_var.get():
                key_var.set(key)
            _apply()

        cb = ttk.Combobox(
            frame,
            textvariable=display_var,
            values=[labels[k] for k in order],
            state="readonly",
            width=28,
        )
        cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        cb.bind("<<ComboboxSelected>>", _on_select)

    _row_label(3, "Профиль")
    _labeled_combo(3, profile_var, PROFILE_ORDER, PROFILE_LABELS)

    _row_label(4, "Движение камеры")
    _labeled_combo(4, motion_var, MOTION_ORDER, MOTION_LABELS)

    _row_label(5, "Темп планов")
    ttk.Scale(
        frame, from_=0.5, to=2.0, variable=tempo_var, command=lambda _: _apply()
    ).grid(row=5, column=1, sticky="ew", pady=4)
    ttk.Label(frame, textvariable=tempo_label, width=6).grid(row=5, column=2, sticky="e")

    _row_label(6, "Интенсивность цвета")
    ttk.Scale(
        frame, from_=0.0, to=1.0, variable=color_var, command=lambda _: _apply()
    ).grid(row=6, column=1, sticky="ew", pady=4)
    ttk.Label(frame, textvariable=color_label, width=6).grid(row=6, column=2, sticky="e")

    _row_label(7, "Переходы")
    _labeled_combo(7, trans_var, TRANSITION_ORDER, TRANSITION_LABELS)

    _row_label(8, "Кадрирование")
    _labeled_combo(8, framing_var, FRAMING_ORDER, FRAMING_LABELS)

    _row_label(9, "Типографика")
    _labeled_combo(9, kinetic_var, KINETIC_ORDER, KINETIC_LABELS)

    _row_label(10, "Макс. zoom")
    ttk.Scale(
        frame, from_=1.0, to=1.5, variable=zoom_var, command=lambda _: _apply()
    ).grid(row=10, column=1, sticky="ew", pady=4)
    ttk.Label(frame, textvariable=zoom_label, width=6).grid(row=10, column=2, sticky="e")

    ttk.Checkbutton(
        frame,
        text="Использовать существующие субтитры и графику (без дублирования)",
        variable=subs_var,
        command=_apply,
    ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 4))

    ttk.Checkbutton(
        frame,
        text="Умный B-roll: подбор клипов по смыслу сцен (псевдо-нейролинк)",
        variable=smart_var,
        command=_apply,
    ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(4, 0))

    ttk.Checkbutton(
        frame,
        text="Частая смена кадров под ключевые слова озвучки (пословный монтаж)",
        variable=kw_var,
        command=_apply,
    ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(4, 0))

    ttk.Checkbutton(
        frame,
        text="SFX на переходах (whoosh / soft impact, тихо)",
        variable=sfx_var,
        command=_apply,
    ).grid(row=14, column=0, columnspan=3, sticky="w", pady=(4, 4))

    # Mac presets
    preset_row = ttk.Frame(frame)
    preset_row.grid(row=15, column=0, columnspan=3, sticky="ew", pady=(8, 4))

    def _apply_mac_air() -> None:
        from .presets import mac_air_8gb_preset
        ps = mac_air_8gb_preset()
        enabled_var.set(True)
        profile_var.set(ps.profile)
        motion_var.set(ps.motion_intensity)
        tempo_var.set(float(ps.plan_tempo))
        color_var.set(float(ps.color_intensity))
        zoom_var.set(float(ps.max_zoom))
        trans_var.set(ps.allowed_transitions)
        framing_var.set(ps.framing_mode)
        kinetic_var.set(getattr(ps, "kinetic_typography", "off") or "off")
        sfx_var.set(False)
        _apply()

    def _apply_mac_pro() -> None:
        from .presets import mac_pro_16gb_preset
        ps = mac_pro_16gb_preset()
        enabled_var.set(True)
        profile_var.set(ps.profile)
        motion_var.set(ps.motion_intensity)
        tempo_var.set(float(ps.plan_tempo))
        color_var.set(float(ps.color_intensity))
        zoom_var.set(float(ps.max_zoom))
        trans_var.set(ps.allowed_transitions)
        framing_var.set(ps.framing_mode)
        kinetic_var.set(getattr(ps, "kinetic_typography", "off") or "off")
        sfx_var.set(False)
        _apply()

    ttk.Label(preset_row, text="Пресет Mac:").pack(side="left", padx=(0, 8))
    ttk.Button(preset_row, text="Air 8 GB", command=_apply_mac_air).pack(side="left", padx=2)
    ttk.Button(preset_row, text="Pro 16 GB", command=_apply_mac_pro).pack(side="left", padx=2)

    if on_preview:
        ttk.Button(frame, text="Preview фрагмента", command=on_preview).grid(
            row=16, column=0, columnspan=3, sticky="ew", pady=(12, 0)
        )

    hint = ttk.Label(
        frame,
        text="По умолчанию выключено. Эффекты в encode master (без повторного Whisper). "
        "MacBook Air 8 GB → пресет «Air 8 GB». "
        "Лицо: Haar ROI. Переходы 0.3–0.55s. Motion: 9 паттернов + smootherstep.",
        wraplength=420,
        justify="left",
    )
    hint.grid(row=17, column=0, columnspan=3, sticky="w", pady=(14, 0))

    frame.columnconfigure(1, weight=1)
    return outer
