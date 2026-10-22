"""Video vs still-image B-roll helpers."""

from __future__ import annotations

import os
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}


def is_image_path(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXT


def is_video_path(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def is_media_path(path: str | os.PathLike) -> bool:
    return is_image_path(path) or is_video_path(path)


def default_image_hold_sec(needed: float | None = None, *, cap: float = 6.0) -> float:
    """How long a still contributes when filling target duration."""
    if needed is not None and needed > 0:
        return max(0.5, min(float(needed), cap))
    return 3.0


def probe_media_duration(path: str, *, image_hold: float = 3.0) -> float:
    """
    Duration for selection accounting.
    Images: synthetic hold (can fill any slot via loop).
    Video: ffprobe; 0 if unreadable.
    """
    if is_image_path(path):
        return max(0.5, float(image_hold))
    try:
        from .vm_bridge import probe_clip_duration
        d = float(probe_clip_duration(path) or 0.0)
        return d if d > 0.05 else 0.0
    except Exception:
        return 0.0
