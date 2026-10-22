"""Feature-flag helpers. Module is OFF unless explicitly enabled in settings.

CE production path is restricted to 4K geometry only:
  - horizontal: 3840×2160
  - vertical:   2160×3840
Any other size → legacy filter even if cinematic_enabled=True.
"""

from __future__ import annotations

from .config import CinematicSettings, DEFAULT_CINEMATIC_SETTINGS

# Exact UHD frames used by VideoMaker master encode
UHD_HORIZONTAL = (3840, 2160)
UHD_VERTICAL = (2160, 3840)
_ALLOWED_4K = {UHD_HORIZONTAL, UHD_VERTICAL}


def is_4k_geometry(width: int, height: int) -> bool:
    """True only for VideoMaker 4K master sizes (16:9 or 9:16 UHD)."""
    try:
        return (int(width), int(height)) in _ALLOWED_4K
    except (TypeError, ValueError):
        return False


def is_enabled(settings: CinematicSettings | None) -> bool:
    if settings is None:
        return False
    return bool(settings.enabled)


def is_enabled_for_geometry(
    settings: CinematicSettings | None,
    width: int,
    height: int,
) -> bool:
    """CE active only when flag ON and encode is 4K UHD."""
    return is_enabled(settings) and is_4k_geometry(width, height)


def should_skip_cinematic(
    settings: CinematicSettings | None,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """True → caller must take the exact legacy path."""
    if not is_enabled(settings):
        return True
    if width is not None and height is not None and not is_4k_geometry(width, height):
        return True
    return False


def load_from_project_settings(project_settings: dict) -> CinematicSettings:
    """Extract cinematic block from existing VideoMaker settings dict."""
    block = project_settings.get("cinematic") or project_settings.get("cinematic_enhancement")
    if not isinstance(block, dict):
        return DEFAULT_CINEMATIC_SETTINGS
    return CinematicSettings.from_dict(block)
