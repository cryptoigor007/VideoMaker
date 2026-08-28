"""WhisperX path resolver — auto-detect, cache, fallback."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _find_whisperx() -> str | None:
    """Internal auto-detection logic."""
    candidates = []

    # 1. PATH search
    which = shutil.which("whisperx")
    if which:
        candidates.append(which)

    # 2. Project venv (relative to this file → repo root)
    try:
        # engines/whisperx_resolve.py → parents[0]=engines, [1]=video_maker, [2]=repo root
        project_root = Path(__file__).resolve().parents[2]
        candidates.append(str(Path(project_root) / ".venv" / "bin" / "whisperx"))
    except Exception:
        pass

    # User locations
    home = Path.home()
    candidates.extend([
        str(home / "video_maker" / ".venv" / "bin" / "whisperx"),
        str(home / ".local" / "bin" / "whisperx"),
        str(home / "whisperx" / "bin" / "whisperx"),
        str(home / "whisperx" / "whisperx"),
    ])

    # Homebrew / system
    if sys.platform == "darwin":
        candidates.extend([
            "/opt/homebrew/bin/whisperx",
            "/usr/local/bin/whisperx",
        ])
    elif sys.platform == "linux":
        candidates.extend([
            "/usr/local/bin/whisperx",
            "/usr/bin/whisperx",
        ])

    # Deduplicate
    seen = set()
    unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)

    # Test each
    for c in unique:
        p = Path(c)
        if p.exists() and os.access(p, os.X_OK):
            return str(Path(c).resolve())

    return None


def resolve_whisperx(explicit: str = "") -> str | None:
    """
    Public API — resolve whisperx path with auto-detection.

    Priority:
    1. Explicit path if provided and valid
    2. Auto-detection via candidates
    """
    if explicit:
        p = Path(explicit.strip())
        if p.exists() and os.access(p, os.X_OK):
            return str(Path(p).resolve())

    return _find_whisperx()
