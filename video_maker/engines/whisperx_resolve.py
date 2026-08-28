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

    which = shutil.which("whisperx")
    if which:
        candidates.append(which)

    try:
        # engines/ → video_maker/ → repo root
        project_root = Path(__file__).resolve().parents[2]
        candidates.append(str(Path(project_root) / ".venv" / "bin" / "whisperx"))
    except Exception:
        pass

    home = Path.home()
    candidates.extend([
        str(home / "video_maker" / ".venv" / "bin" / "whisperx"),
        str(home / ".local" / "bin" / "whisperx"),
        str(home / "whisperx" / "bin" / "whisperx"),
        str(home / "whisperx" / "whisperx"),
    ])

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

    seen = set()
    unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)

    for c in unique:
        p = Path(c)
        if p.exists() and os.access(p, os.X_OK):
            return str(Path(c).resolve())

    return None


def resolve_whisperx(explicit: str = "") -> str | None:
    """Resolve whisperx path: explicit → auto-detect."""
    if explicit:
        p = Path(explicit.strip())
        if p.exists() and os.access(p, os.X_OK):
            return str(Path(p).resolve())
    return _find_whisperx()
