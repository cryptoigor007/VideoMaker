"""Persistent shuffle policy for VideoMaker background music.

A directory source is shuffled per final output asset, not per episode. The selector
remembers recent tracks across application runs, avoids the last five where enough
alternatives exist, and remembers each asset key so an intentional rerender keeps
its original soundtrack. A single explicitly selected file is respected as-is; no
algorithm can avoid repeating it without silently ignoring the user's choice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time

log = logging.getLogger(__name__)

BGM_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus")
RECENT_TRACKS_TO_AVOID = 5
_MAX_CHOICES = 500
_MAX_RECENT_HISTORY = 20
_BGM_SELECTION_LOCK = threading.Lock()
_BGM_SELECTION_STATE_PATH = os.path.join(
    os.path.expanduser("~"), "video_maker", "cache", "bgm_selection.json"
)
_WARNED_SINGLE_SOURCE: set[str] = set()


def list_bgm_tracks(bgm_source: str) -> list[str]:
    """Accept either one explicitly selected audio file or a directory of tracks."""
    source = os.path.abspath(os.path.expanduser((bgm_source or "").strip())) if (bgm_source or "").strip() else ""
    if not source:
        return []
    if os.path.isfile(source):
        if os.path.basename(source).startswith(".") or not source.lower().endswith(BGM_EXTS):
            return []
        return [source]
    if not os.path.isdir(source):
        return []
    try:
        names = sorted(os.listdir(source), key=lambda name: (name.casefold(), name))
    except OSError:
        return []
    return [
        os.path.join(source, name)
        for name in names
        if not name.startswith(".")
        and name.lower().endswith(BGM_EXTS)
        and os.path.isfile(os.path.join(source, name))
    ]


def _read_state(path: str) -> dict:
    """Load history without silently forgetting it if an existing file is corrupt."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return {"version": 2, "folders": {}}
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"BGM selection history cannot be read: {path}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("folders"), dict):
        raise RuntimeError(f"BGM selection history has an invalid format: {path}")
    return value


def _write_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp_path, path)


def _source_scope(source: str) -> tuple[str, list[str], bool]:
    """Return persistent shuffle scope, tracks, and whether source is one explicit file."""
    expanded = os.path.abspath(os.path.expanduser((source or "").strip())) if (source or "").strip() else ""
    if expanded and os.path.isfile(expanded):
        tracks = list_bgm_tracks(expanded)
        scope = os.path.dirname(expanded)
        return os.path.normcase(scope), tracks, True
    if expanded and os.path.isdir(expanded):
        return os.path.normcase(expanded), list_bgm_tracks(expanded), False
    return os.path.normcase(expanded), [], False


def _remember_track(folder_state: dict, identity: str, track: str) -> None:
    recent = folder_state.get("recent_tracks")
    if not isinstance(recent, list):
        recent = []
        old_track = folder_state.get("last_track")
        if old_track:
            recent.append(old_track)
    # A replay of a cached asset is moved to the front rather than duplicated.
    recent = [item for item in recent if item != track]
    recent.insert(0, track)
    folder_state["recent_tracks"] = recent[:_MAX_RECENT_HISTORY]
    folder_state["last_seed"] = identity
    folder_state["last_track"] = track


def pick_bgm_track(
    bgm_source: str,
    seed_key: str = "",
    *,
    state_path: str | None = None,
    recent_window: int = RECENT_TRACKS_TO_AVOID,
) -> str:
    """Select a track randomly, avoiding recent tracks and persisting decisions.

    `seed_key` identifies one final output asset (e.g. an episode's Short #3), not
    merely the episode. A repeated key reuses its stored track; a new key shuffles
    among tracks not used in the recent-history window. For N tracks, the effective
    exclusion window is at most N-1 so one candidate always remains.
    """
    scope, tracks, explicit_file = _source_scope(bgm_source)
    if not tracks:
        return ""

    source_label = os.path.abspath(os.path.expanduser((bgm_source or "").strip()))
    folder_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
    path = state_path or _BGM_SELECTION_STATE_PATH
    identity = str(seed_key or f"unkeyed:{time.time_ns()}:{secrets.token_hex(8)}")

    with _BGM_SELECTION_LOCK:
        try:
            state = _read_state(path)
        except RuntimeError as exc:
            log.error("[BGM] История треков повреждена/недоступна: %s", exc)
            if not explicit_file and len(tracks) > 1:
                raise RuntimeError(
                    "BGM history is unreadable; refusing a multi-track render to avoid "
                    "forgetting recent choices. Repair/remove the history file only if "
                    "you accept resetting the no-repeat history."
                ) from exc
            # With a single available/explicit track there is no rotation to promise.
            # Return it without overwriting the damaged global history for other folders.
            log.warning("[BGM] Используется единственный заданный трек без обновления повреждённой истории.")
            return tracks[0]
        folders = state.get("folders")
        if not isinstance(folders, dict):
            # _read_state validates this, but keep a defensive guard for future callers.
            folders = {}
        folder_state = folders.get(folder_key)
        if folder_state is not None and not isinstance(folder_state, dict):
            detail = f"BGM history has an invalid folder record for source {source_label}"
            if not explicit_file and len(tracks) > 1:
                raise RuntimeError(detail + "; refusing to forget recent track choices")
            log.warning("[BGM] %s; returning the only available track without rewriting history", detail)
            return tracks[0]
        if folder_state is None:
            folder_state = {"choices": {}, "last_seed": "", "last_track": "", "recent_tracks": []}
        choices = folder_state.get("choices", {})
        if not isinstance(choices, dict):
            detail = f"BGM history has invalid choices for source {source_label}"
            if not explicit_file and len(tracks) > 1:
                raise RuntimeError(detail + "; refusing to forget recent track choices")
            log.warning("[BGM] %s; returning the only available track without rewriting history", detail)
            return tracks[0]
        recent_record = folder_state.get("recent_tracks")
        if recent_record is not None and not isinstance(recent_record, list):
            detail = f"BGM history has invalid recent_tracks for source {source_label}"
            if not explicit_file and len(tracks) > 1:
                raise RuntimeError(detail + "; refusing to forget recent track choices")
            log.warning("[BGM] %s; returning the only available track without rewriting history", detail)
            return tracks[0]
        folder_state["choices"] = choices

        chosen = choices.get(identity)
        if chosen not in tracks:
            if explicit_file or len(tracks) == 1:
                chosen = tracks[0]
                if len(tracks) == 1:
                    warning_key = source_label
                    if warning_key not in _WARNED_SINGLE_SOURCE:
                        log.warning(
                            "[BGM] Указан один трек; предотвратить повторы невозможно: %s",
                            os.path.basename(chosen),
                        )
                        _WARNED_SINGLE_SOURCE.add(warning_key)
            else:
                recent = folder_state.get("recent_tracks")
                if not isinstance(recent, list):
                    recent = []
                    if folder_state.get("last_track"):
                        recent.append(folder_state["last_track"])
                recent = [track for track in recent if track in tracks]
                window = max(1, min(int(recent_window or 1), len(tracks) - 1))
                blocked = set(recent[:window])
                eligible = [track for track in tracks if track not in blocked]
                if not eligible:
                    # Defensive fallback for malformed legacy state: at minimum,
                    # avoid the last track when more than one track is available.
                    last_track = folder_state.get("last_track")
                    eligible = [track for track in tracks if track != last_track] or tracks
                chosen = secrets.choice(eligible)
            choices[identity] = chosen
            while len(choices) > _MAX_CHOICES:
                choices.pop(next(iter(choices)))
        _remember_track(folder_state, identity, chosen)
        folders[folder_key] = folder_state
        state["version"] = 2
        state["folders"] = folders
        try:
            _write_state(path, state)
        except OSError as exc:
            # A multi-track folder relies on the durable history for no-repeat
            # behavior. Do not silently continue as if the requirement were met.
            log.error("[BGM] Не удалось сохранить историю треков: %s", exc)
            if not explicit_file and len(tracks) > 1:
                raise RuntimeError(
                    "BGM history could not be persisted; refusing a multi-track "
                    "render because non-repeat selection cannot be guaranteed"
                ) from exc
        return chosen
