"""Fail-closed validation for VideoMaker's final BGM mix output.

The validator checks the actual FFmpeg mix graph contract and probes the written
MP4. It cannot judge subjective audibility; a successful graph + decoded audio
stream is evidence that the BGM mix was rendered, and the final file should still
be listened to before publication.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import OrderedDict
from typing import Any

MIN_OUTPUT_BYTES = 1024
DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0
DEFAULT_DURATION_TOLERANCE_RATIO = 0.05
# Each platform-specific BGM deliverable reuses the same staged video source.
# Cache only source metadata keyed by real path + mtime + size; the final output
# gets its own key and is still probed after every mix. This avoids spawning the
# same ffprobe process repeatedly for the same H/V/Short source across platforms.
_PROBE_CACHE: "OrderedDict[tuple[str, str, int, int], dict[str, Any]]" = OrderedDict()
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE_MAX = 2048


def validate_bgm_filter(filter_complex: str) -> None:
    """Reject a filter graph that does not mix voice and a second BGM input."""
    graph = str(filter_complex or "")
    required_fragments = ("[0:a]", "[1:a]", "amix=inputs=2")
    missing = [fragment for fragment in required_fragments if fragment not in graph]
    if missing:
        raise RuntimeError(
            "Refusing BGM render: filter graph does not mix voice + music; "
            f"missing {', '.join(missing)}"
        )


def _probe(path: str, ffprobe_bin: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        raise RuntimeError(f"BGM output validation: media file is missing: {path!r}")
    try:
        real_path = os.path.realpath(path)
        stat = os.stat(real_path)
        size = int(stat.st_size)
    except OSError as exc:
        raise RuntimeError(f"BGM output validation: cannot stat {path!r}: {exc}") from exc
    if size < MIN_OUTPUT_BYTES:
        raise RuntimeError(
            f"BGM output validation: media file is too small ({size} bytes): {path}"
        )

    cache_key = (real_path, str(ffprobe_bin), int(stat.st_mtime_ns), size)
    with _PROBE_CACHE_LOCK:
        cached = _PROBE_CACHE.get(cache_key)
        if cached is not None:
            _PROBE_CACHE.move_to_end(cache_key)
            # Return a shallow copy so callers cannot mutate the cached record.
            return dict(cached)

    command = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,duration",
        "-of", "json",
        path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"BGM output validation: ffprobe failed for {path}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"BGM output validation: ffprobe exited {result.returncode} for {path}: "
            f"{(result.stderr or '')[-500:]}"
        )
    try:
        probe = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"BGM output validation: invalid ffprobe JSON for {path}") from exc

    streams = probe.get("streams") or []
    if not isinstance(streams, list):
        raise RuntimeError(f"BGM output validation: invalid stream list for {path}")
    stream_types = {str(item.get("codec_type") or "") for item in streams if isinstance(item, dict)}
    duration_candidates = []
    try:
        duration_candidates.append(float((probe.get("format") or {}).get("duration")))
    except (TypeError, ValueError):
        pass
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        try:
            duration_candidates.append(float(stream.get("duration")))
        except (TypeError, ValueError):
            pass
    durations = [value for value in duration_candidates if value > 0]
    duration = max(durations) if durations else 0.0
    codecs = {
        str(item.get("codec_type")): str(item.get("codec_name") or "unknown")
        for item in streams
        if isinstance(item, dict) and item.get("codec_type")
    }
    record = {
        "path": real_path,
        "size_bytes": size,
        "duration_seconds": duration,
        "stream_types": sorted(stream_types),
        "codecs": codecs,
    }
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[cache_key] = record
        _PROBE_CACHE.move_to_end(cache_key)
        while len(_PROBE_CACHE) > _PROBE_CACHE_MAX:
            _PROBE_CACHE.popitem(last=False)
    return dict(record)


def verify_bgm_output(
    source_video: str,
    output_video: str,
    *,
    ffprobe_bin: str = "ffprobe",
    duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
    duration_tolerance_ratio: float = DEFAULT_DURATION_TOLERANCE_RATIO,
) -> dict[str, Any]:
    """Probe the actual output and require video + audio with sane duration.

    The selected BGM track is supplied to the FFmpeg graph through the second
    input; ``validate_bgm_filter`` verifies the graph still contains a two-input
    voice/music amix. This output probe then ensures FFmpeg wrote a decodable MP4
    with an audio stream and a duration close to the source video.
    """
    source = _probe(source_video, ffprobe_bin)
    output = _probe(output_video, ffprobe_bin)

    if "video" not in source["stream_types"]:
        raise RuntimeError(f"BGM source has no video stream: {source_video}")
    if "audio" not in source["stream_types"]:
        raise RuntimeError(f"BGM source has no voice audio stream: {source_video}")
    if "video" not in output["stream_types"]:
        raise RuntimeError(f"BGM output has no video stream: {output_video}")
    if "audio" not in output["stream_types"]:
        raise RuntimeError(f"BGM output has no audio stream: {output_video}")
    if output["duration_seconds"] <= 0:
        raise RuntimeError(f"BGM output has no valid duration: {output_video}")

    src_duration = float(source["duration_seconds"] or 0.0)
    out_duration = float(output["duration_seconds"] or 0.0)
    if src_duration > 0:
        allowed_delta = max(
            float(duration_tolerance_seconds),
            src_duration * float(duration_tolerance_ratio),
        )
        delta = abs(src_duration - out_duration)
        if delta > allowed_delta:
            raise RuntimeError(
                "BGM output duration differs too much from source: "
                f"source={src_duration:.3f}s output={out_duration:.3f}s "
                f"delta={delta:.3f}s allowed={allowed_delta:.3f}s"
            )
    return {
        "source_duration_seconds": src_duration,
        "output_duration_seconds": out_duration,
        "output_size_bytes": output["size_bytes"],
        "video_codec": output["codecs"].get("video", "unknown"),
        "audio_codec": output["codecs"].get("audio", "unknown"),
        "audio_stream_present": True,
        "two_input_bgm_filter_verified": True,
    }
