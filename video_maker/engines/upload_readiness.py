"""Fail-closed readiness checks for finalized YouTube assets.

This does not upload files or claim to measure whether background music is
subjectively audible. It prevents incomplete metadata, tiny/missing outputs, and
known BGM fallback statuses from being reported as ready.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ACCEPTED_BGM_STATUSES = {"mixed", "voice_only_no_bgm_configured"}
MIN_VIDEO_BYTES = 1024


def _meaningful_text_file(path: str) -> bool:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return False
    return bool(value) and value.casefold() not in {"-", "—", "n/a", "na", "none", "null"}



def find_missing_short_indices(expected_clip_count: int, available_indices: list[int]) -> list[int]:
    """List intended 1-based Short indexes that have no surviving output file."""
    try:
        count = max(0, int(expected_clip_count))
    except (TypeError, ValueError):
        count = 0
    available: set[int] = set()
    for value in available_indices or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index > 0:
            available.add(index)
    return [index for index in range(1, count + 1) if index not in available]

def evaluate_upload_readiness(
    *, video_path: str, title_path: str, description_path: str,
    hashtags_path: str, bgm_status: str,
) -> dict[str, Any]:
    """Return evidence and blocking reasons for one manual-upload asset."""
    video_exists = bool(video_path and os.path.isfile(video_path))
    try:
        video_nonempty = video_exists and os.path.getsize(video_path) >= MIN_VIDEO_BYTES
    except OSError:
        video_nonempty = False

    metadata_paths = {"title": title_path, "description": description_path, "hashtags": hashtags_path}
    metadata_files_exist = all(os.path.isfile(path) for path in metadata_paths.values())
    metadata_content = {name: _meaningful_text_file(path) for name, path in metadata_paths.items()}
    metadata_content_valid = all(metadata_content.values())
    status = str(bgm_status or "missing")
    bgm_status_accepted = status in ACCEPTED_BGM_STATUSES

    issues: list[str] = []
    if not video_exists:
        issues.append("final video file is missing")
    elif not video_nonempty:
        issues.append(f"final video is smaller than {MIN_VIDEO_BYTES} bytes")
    if not metadata_files_exist:
        missing = [name for name, path in metadata_paths.items() if not os.path.isfile(path)]
        issues.append("metadata files missing: " + ", ".join(missing))
    empty = [name for name, valid in metadata_content.items() if not valid]
    if empty:
        issues.append("metadata values empty/placeholder: " + ", ".join(empty))
    if not bgm_status_accepted:
        issues.append("BGM status is not accepted: " + status)

    ready = bool(video_nonempty and metadata_content_valid and bgm_status_accepted)
    return {
        "video_exists": video_exists,
        "video_nonempty": bool(video_nonempty),
        "metadata_files_exist": metadata_files_exist,
        "metadata_content_valid": metadata_content_valid,
        "metadata_content": metadata_content,
        "bgm_status_accepted": bgm_status_accepted,
        "ready_for_manual_upload": ready,
        "issues": issues,
    }
