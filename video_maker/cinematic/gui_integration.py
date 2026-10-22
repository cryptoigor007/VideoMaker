"""
GUI wiring helpers for VideoMaker app.py.

Does not import tkinter at module import time for the pure helpers.
Call from App.__init__ / _build_* / collect_settings_from_gui.
"""

from __future__ import annotations

from typing import Any

from .config import CinematicSettings


# Names to add into BOOL_VARS / OTHER_VARS in app.py
CE_BOOL_VARS = [
    "cinematic_enabled_var",
    "cinematic_reuse_subs_var",
    "cinematic_transition_sfx_var",
]

CE_OTHER_VARS = [
    "cinematic_profile_var",
    "cinematic_motion_var",
    "cinematic_tempo_var",
    "cinematic_color_var",
    "cinematic_transitions_var",
    "cinematic_framing_var",
    "cinematic_max_zoom_var",
    "cinematic_kinetic_var",
]


def init_ce_vars(app: Any) -> None:
    """Create tk variables on App (call once during __init__)."""
    import tkinter as tk

    app.cinematic_enabled_var = tk.BooleanVar(value=False)
    app.cinematic_profile_var = tk.StringVar(value="cinematic_documentary")
    app.cinematic_motion_var = tk.StringVar(value="low")
    app.cinematic_tempo_var = tk.StringVar(value="1.0")
    app.cinematic_color_var = tk.StringVar(value="0.5")
    app.cinematic_transitions_var = tk.StringVar(value="hard_only")
    app.cinematic_framing_var = tk.StringVar(value="center")
    app.cinematic_reuse_subs_var = tk.BooleanVar(value=True)
    app.cinematic_max_zoom_var = tk.StringVar(value="1.25")
    app.cinematic_kinetic_var = tk.StringVar(value="off")
    app.cinematic_transition_sfx_var = tk.BooleanVar(value=False)
    app.cinematic_smart_broll_var = tk.BooleanVar(value=True)
    app.cinematic_keyword_cuts_var = tk.BooleanVar(value=True)


def apply_ce_vars_to_settings(app: Any) -> None:
    """Write tk vars → Settings fields. Call from collect_settings_from_gui."""
    s = app.settings
    s.cinematic_enabled = bool(getattr(app, "cinematic_enabled_var", None) and app.cinematic_enabled_var.get())
    if hasattr(app, "cinematic_profile_var"):
        s.cinematic_profile = str(app.cinematic_profile_var.get() or "cinematic_documentary")
    if hasattr(app, "cinematic_motion_var"):
        s.cinematic_motion = str(app.cinematic_motion_var.get() or "low")
    if hasattr(app, "cinematic_transitions_var"):
        s.cinematic_transitions = str(app.cinematic_transitions_var.get() or "hard_only")
    if hasattr(app, "cinematic_framing_var"):
        s.cinematic_framing = str(app.cinematic_framing_var.get() or "center")
    if hasattr(app, "cinematic_reuse_subs_var"):
        s.cinematic_reuse_subs = bool(app.cinematic_reuse_subs_var.get())
    try:
        s.cinematic_tempo = float(app.cinematic_tempo_var.get())
    except Exception:
        s.cinematic_tempo = 1.0
    try:
        s.cinematic_color = float(app.cinematic_color_var.get())
    except Exception:
        s.cinematic_color = 0.5
    try:
        s.cinematic_max_zoom = float(app.cinematic_max_zoom_var.get())
    except Exception:
        s.cinematic_max_zoom = 1.25
    if hasattr(app, "cinematic_kinetic_var"):
        s.cinematic_kinetic = str(app.cinematic_kinetic_var.get() or "off")
    if hasattr(app, "cinematic_transition_sfx_var"):
        s.cinematic_transition_sfx = bool(app.cinematic_transition_sfx_var.get())
    if hasattr(app, "cinematic_smart_broll_var"):
        s.cinematic_smart_broll = bool(app.cinematic_smart_broll_var.get())
    if hasattr(app, "cinematic_keyword_cuts_var"):
        s.cinematic_keyword_cuts = bool(app.cinematic_keyword_cuts_var.get())


def settings_to_ce_vars(app: Any) -> None:
    """Settings → tk vars (after load_settings JSON)."""
    s = app.settings
    if hasattr(app, "cinematic_enabled_var"):
        app.cinematic_enabled_var.set(bool(getattr(s, "cinematic_enabled", False)))
    if hasattr(app, "cinematic_profile_var"):
        app.cinematic_profile_var.set(str(getattr(s, "cinematic_profile", "cinematic_documentary")))
    if hasattr(app, "cinematic_motion_var"):
        app.cinematic_motion_var.set(str(getattr(s, "cinematic_motion", "low")))
    if hasattr(app, "cinematic_tempo_var"):
        app.cinematic_tempo_var.set(str(getattr(s, "cinematic_tempo", 1.0)))
    if hasattr(app, "cinematic_color_var"):
        app.cinematic_color_var.set(str(getattr(s, "cinematic_color", 0.5)))
    if hasattr(app, "cinematic_transitions_var"):
        app.cinematic_transitions_var.set(str(getattr(s, "cinematic_transitions", "hard_only")))
    if hasattr(app, "cinematic_framing_var"):
        app.cinematic_framing_var.set(str(getattr(s, "cinematic_framing", "center")))
    if hasattr(app, "cinematic_reuse_subs_var"):
        app.cinematic_reuse_subs_var.set(bool(getattr(s, "cinematic_reuse_subs", True)))
    if hasattr(app, "cinematic_max_zoom_var"):
        app.cinematic_max_zoom_var.set(str(getattr(s, "cinematic_max_zoom", 1.25)))
    if hasattr(app, "cinematic_kinetic_var"):
        app.cinematic_kinetic_var.set(str(getattr(s, "cinematic_kinetic", "off")))
    if hasattr(app, "cinematic_transition_sfx_var"):
        app.cinematic_transition_sfx_var.set(bool(getattr(s, "cinematic_transition_sfx", False)))
    if hasattr(app, "cinematic_smart_broll_var"):
        app.cinematic_smart_broll_var.set(bool(getattr(s, "cinematic_smart_broll", True)))
    if hasattr(app, "cinematic_keyword_cuts_var"):
        app.cinematic_keyword_cuts_var.set(bool(getattr(s, "cinematic_keyword_cuts", True)))


def ce_kwargs_for_pipeline_context(settings: Any) -> dict:
    """Extra kwargs for PipelineContext(...) construction.

    Passes both the flag and a full CinematicSettings block so
    settings_from_vm(ctx) does not fall back to defaults.
    """
    from .vm_bridge import settings_from_vm
    ce = settings_from_vm(settings)
    return {
        "cinematic_enabled": bool(ce.enabled),
        "cinematic_settings": ce,
    }


def attach_ce_tab(app: Any, notebook: Any) -> Any:
    """
    Add «Кинематографичный монтаж» tab to existing ttk.Notebook.
    Returns the frame.
    """
    from .gui_panel import create_cinematic_frame

    import tkinter as tk
    from tkinter import ttk

    tab = ttk.Frame(notebook)
    notebook.add(tab, text="  Кинематограф  ")

    def get_settings() -> CinematicSettings:
        apply_ce_vars_to_settings(app)
        from .vm_bridge import settings_from_vm
        return settings_from_vm(app.settings)

    def set_settings(s: CinematicSettings) -> None:
        app.settings.cinematic_enabled = s.enabled
        app.settings.cinematic_profile = s.profile
        app.settings.cinematic_motion = s.motion_intensity
        app.settings.cinematic_tempo = s.plan_tempo
        app.settings.cinematic_color = s.color_intensity
        app.settings.cinematic_transitions = s.allowed_transitions
        app.settings.cinematic_framing = s.framing_mode
        app.settings.cinematic_reuse_subs = s.reuse_existing_subs
        app.settings.cinematic_max_zoom = s.max_zoom
        app.settings.cinematic_smart_broll = bool(getattr(s, "smart_broll", True))
        app.settings.cinematic_keyword_cuts = bool(getattr(s, "keyword_cuts", True))
        settings_to_ce_vars(app)

    def _preview_worker() -> None:
        """Background CE preview — never block the GUI thread."""
        import logging
        import os
        import subprocess
        import tempfile
        import threading
        from pathlib import Path

        log = logging.getLogger(__name__)

        def _ui(msg: str) -> None:
            try:
                if hasattr(app, "_log"):
                    app.root.after(0, lambda: app._log(msg))
                else:
                    log.info(msg)
            except Exception:
                log.info(msg)

        def _run() -> None:
            try:
                ce = get_settings()
                if not ce.enabled:
                    ce = ce.__class__(**{**ce.to_dict(), "enabled": True})
                # Pick first video from horizontal B-roll folder if set
                broll = ""
                for attr in ("broll_horizontal", "broll_vertical"):
                    broll = str(getattr(app.settings, attr, "") or "").strip()
                    if broll and os.path.isdir(broll):
                        break
                src = None
                if broll and os.path.isdir(broll):
                    from video_maker.cinematic.media_assets import is_media_path

                    def _scan(root: str, *, skip_used: bool = True, one_per_dir: bool = False) -> list[str]:
                        found: list[str] = []
                        try:
                            names = sorted(os.listdir(root))
                        except OSError:
                            return found
                        for name in names:
                            if name.startswith(".") or name.startswith("._"):
                                continue
                            full = os.path.join(root, name)
                            if os.path.isfile(full) and is_media_path(full):
                                found.append(full)
                            elif os.path.isdir(full):
                                if skip_used and name.lower() in ("used", "использованная"):
                                    continue
                                try:
                                    for n2 in sorted(os.listdir(full)):
                                        f2 = os.path.join(full, n2)
                                        if os.path.isfile(f2) and is_media_path(f2):
                                            found.append(f2)
                                            if one_per_dir:
                                                break
                                except OSError:
                                    pass
                        return found

                    candidates = _scan(broll)
                    if not candidates:
                        # Пул израсходован (всё в used/) — берём образец оттуда
                        candidates = _scan(os.path.join(broll, "used"), skip_used=False, one_per_dir=True)
                        if not candidates:
                            for _d in sorted(os.listdir(broll)) if os.path.isdir(broll) else []:
                                _sub = os.path.join(broll, _d, "used")
                                if os.path.isdir(_sub):
                                    candidates = _scan(_sub, skip_used=False, one_per_dir=True)
                                    if candidates:
                                        break
                    src = candidates[0] if candidates else None
                if not src or not os.path.isfile(src):
                    _ui("[CE Preview] Нет B-roll клипа — укажите папку B-roll H/V")
                    return
                from .edit_plan import Scene, MotionKeyframe
                from .preview import render_scene_preview
                scene = Scene(
                    scene_id="preview",
                    asset_id="preview",
                    source_path=src,
                    source_in=0.0,
                    source_out=min(6.0, float(ce.preview_range_sec or 6.0)),
                    timeline_start=0.0,
                    timeline_end=min(6.0, float(ce.preview_range_sec or 6.0)),
                    color_profile=ce.profile,
                    motion=[
                        MotionKeyframe(t=0.0, scale=1.0),
                        MotionKeyframe(t=1.0, scale=min(ce.max_zoom, 1.15)),
                    ],
                )
                out_dir = Path(tempfile.gettempdir()) / "ce_preview"
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / "ce_gui_preview.mp4"
                _ui(f"[CE Preview] рендер {os.path.basename(src)} …")
                result = render_scene_preview(
                    scene, settings=ce, output_path=out, out_w=960, out_h=540, fps=30
                )
                _ui(f"[CE Preview] готово: {result}")
                # Best-effort open on macOS / linux
                try:
                    if sys_platform_is_darwin():
                        subprocess.Popen(["open", str(result)])
                    else:
                        subprocess.Popen(["xdg-open", str(result)])
                except Exception:
                    pass
            except Exception as e:
                _ui(f"[CE Preview] ошибка: {e}")
                log.exception("CE preview failed")

        def sys_platform_is_darwin() -> bool:
            import sys
            return sys.platform == "darwin"

        if getattr(app, "_ce_preview_busy", False):
            _ui("[CE Preview] уже выполняется…")
            return
        app._ce_preview_busy = True

        def _wrap() -> None:
            try:
                _run()
            finally:
                app._ce_preview_busy = False

        threading.Thread(target=_wrap, daemon=True, name="ce-preview").start()

    def _make_frame(parent):
        return create_cinematic_frame(
            parent,
            get_settings=get_settings,
            set_settings=set_settings,
            on_preview=_preview_worker,
        )

    frame = _make_frame(tab)
    frame.pack(fill="both", expand=True, padx=12, pady=12)
    app._ce_tab = tab
    app._ce_tab_frame = frame
    app._ce_get_settings = get_settings
    app._ce_set_settings = set_settings

    def refresh_ce_tab() -> None:
        """Rebuild panel controls after settings JSON load."""
        try:
            for child in tab.winfo_children():
                child.destroy()
            new_frame = _make_frame(tab)
            new_frame.pack(fill="both", expand=True, padx=12, pady=12)
            app._ce_tab_frame = new_frame
        except Exception:
            pass

    app.refresh_ce_tab = refresh_ce_tab
    return tab


# --- Copy-paste guide for app.py ---
APP_PY_HOOKS = '''
# 1) BOOL_VARS += CE_BOOL_VARS names (or import CE_BOOL_VARS)
# 2) OTHER_VARS += CE_OTHER_VARS names
# 3) In App.__init__ after other vars:
#       from video_maker.cinematic.gui_integration import init_ce_vars, attach_ce_tab
#       init_ce_vars(self)
# 4) After notebook tabs (Журнал и пульт):
#       attach_ce_tab(self, self.notebook)
# 5) End of collect_settings_from_gui(app):
#       from video_maker.cinematic.gui_integration import apply_ce_vars_to_settings
#       apply_ce_vars_to_settings(app)
# 6) End of _load_settings after applying JSON to settings:
#       from video_maker.cinematic.gui_integration import settings_to_ce_vars
#       settings_to_ce_vars(self)
# 7) PipelineContext(...):
#       **ce_kwargs_for_pipeline_context(pipeline_settings)
#    or explicit:
#       cinematic_enabled=getattr(pipeline_settings, "cinematic_enabled", False),
'''
