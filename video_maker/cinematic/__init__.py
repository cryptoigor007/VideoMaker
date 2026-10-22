"""Cinematic Enhancement for VideoMaker.

Feature-flag OFF by default. Does not alter the legacy pipeline when disabled.
"""

from __future__ import annotations

__version__ = "1.9.2"

# Core API — lightweight imports only
from .config import CinematicSettings, DEFAULT_CINEMATIC_SETTINGS
from .edit_plan import EditPlan, Scene, CropRect, MotionKeyframe
from .feature_flag import (
    is_enabled,
    is_enabled_for_geometry,
    is_4k_geometry,
    should_skip_cinematic,
    load_from_project_settings,
)
from .planner import PlanBuilder
from .validator import validate_plan, assert_valid, PlanValidationError
from .kinetic_typography import write_kinetic_ass, build_kinetic_ass, kinetic_ass_filter
from .vm_bridge import settings_from_vm, plan_from_selected_clips, build_ce_filter_complex
from .roi_detect import estimate_roi, face_detector_available, detect_face_roi_from_path
from .presets import mac_air_8gb_preset, mac_pro_16gb_preset

__all__ = [
    "__version__",
    "estimate_roi",
    "face_detector_available",
    "detect_face_roi_from_path",
    "mac_air_8gb_preset",
    "mac_pro_16gb_preset",
    "CinematicSettings",
    "DEFAULT_CINEMATIC_SETTINGS",
    "EditPlan",
    "Scene",
    "CropRect",
    "MotionKeyframe",
    "is_enabled",
    "should_skip_cinematic",
    "is_enabled_for_geometry",
    "is_4k_geometry",
    "load_from_project_settings",
    "PlanBuilder",
    "validate_plan",
    "assert_valid",
    "PlanValidationError",
    "settings_from_vm",
    "plan_from_selected_clips",
    "build_ce_filter_complex",
    "write_kinetic_ass",
    "build_kinetic_ass",
    "kinetic_ass_filter",
]


def __getattr__(name: str):
    """Lazy exports for heavier submodules."""
    _lazy = {
        "get_profile": (".profiles", "get_profile"),
        "build_eq_filter": (".profiles", "build_eq_filter"),
        "PROFILES": (".profiles", "PROFILES"),
        "choose_transition": (".transitions", "choose_transition"),
        "TransitionSpec": (".transitions", "TransitionSpec"),
        "visual_cache_key": (".cache_key", "visual_cache_key"),
        "fit_crop_for_aspect": (".composition", "fit_crop_for_aspect"),
        "dual_format_plan_scenes": (".composition", "dual_format_plan_scenes"),
        "target_size": (".composition", "target_size"),
        "OUTPUT_SPECS": (".composition", "OUTPUT_SPECS"),
        "maybe_build_plan": (".pipeline_hooks", "maybe_build_plan"),
        "filter_graph_extras_for_scene": (".pipeline_hooks", "filter_graph_extras_for_scene"),
        "resolve_settings": (".pipeline_hooks", "resolve_settings"),
        "apply_rhythm": (".timeline", "apply_rhythm"),
        "apply_rhythm_to_plan": (".timeline", "apply_rhythm_to_plan"),
        "duration_error_frames": (".timeline", "duration_error_frames"),
        "TimelineResult": (".timeline", "TimelineResult"),
        "build_render_graph": (".render_graph", "build_render_graph"),
        "graph_to_ffmpeg_cmd": (".render_graph", "graph_to_ffmpeg_cmd"),
        "RenderGraph": (".render_graph", "RenderGraph"),
        "subtitle_safe_zone": (".safe_zones", "subtitle_safe_zone"),
        "recommend_subtitle_placement": (".safe_zones", "recommend_subtitle_placement"),
        "overlay_report": (".safe_zones", "overlay_report"),
        "hook_cta_zone": (".safe_zones", "hook_cta_zone"),
        "StagingDir": (".staging", "StagingDir"),
        "atomic_write_json": (".staging", "atomic_write_json"),
        "file_sha256": (".staging", "file_sha256"),
        "cache_is_sane": (".staging", "cache_is_sane"),
        "DEFAULT_POLICY": (".encode_contract", "DEFAULT_POLICY"),
        "assert_ce_host_allowed": (".encode_contract", "assert_ce_host_allowed"),
        "describe_policy": (".encode_contract", "describe_policy"),
        "save_preset": (".presets", "save_preset"),
        "load_preset": (".presets", "load_preset"),
        "builtin_preset": (".presets", "builtin_preset"),
        "init_ce_vars": (".gui_integration", "init_ce_vars"),
        "apply_ce_vars_to_settings": (".gui_integration", "apply_ce_vars_to_settings"),
        "ce_kwargs_for_pipeline_context": (".gui_integration", "ce_kwargs_for_pipeline_context"),
        "attach_ce_tab": (".gui_integration", "attach_ce_tab"),
    }
    if name not in _lazy:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = _lazy[name]
    import importlib
    mod = importlib.import_module(mod_name, __name__)
    val = getattr(mod, attr)
    globals()[name] = val
    return val
