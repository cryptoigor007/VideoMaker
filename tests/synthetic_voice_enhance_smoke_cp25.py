#!/usr/bin/env python3
"""Synthetic comparison: legacy H+V post-render enhancement vs shared lossless narration.

The benchmark intentionally uses an 8-second generated clip. It measures only the
voice-enhancement part (not Whisper, Gemini, master encoding, subtitle rendering,
or platform BGM). It verifies that the new path produces 48 kHz PCM WAV with an
unchanged duration and counts actual FFmpeg invocations.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print(json.dumps({"status": "SKIP", "reason": "ffmpeg/ffprobe unavailable"}, indent=2))
        return 0

    from video_maker.engines.audio import probe_duration, voice_enhance_filter
    from video_maker.pipeline.branches import FinalHorizontal, FinalVertical

    real_run = subprocess.run
    with tempfile.TemporaryDirectory(prefix="vm_cp25_voice_") as temp:
        root = Path(temp)
        horizontal = root / "horizontal.mp4"
        vertical = root / "vertical.mp4"
        narration = root / "narration.wav"
        real_run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(horizontal),
        ], check=True, capture_output=True, text=True, timeout=60)
        shutil.copy2(horizontal, vertical)
        real_run([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-c:a", "pcm_s16le", "-ar", "48000", str(narration),
        ], check=True, capture_output=True, text=True, timeout=30)

        counts = {"ffmpeg": 0, "ffprobe": 0}

        def counted_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, (list, tuple)) and cmd:
                exe = os.path.basename(str(cmd[0]))
                if exe.startswith("ffmpeg"):
                    counts["ffmpeg"] += 1
                elif exe.startswith("ffprobe"):
                    counts["ffprobe"] += 1
            return real_run(*args, **kwargs)

        ctx = SimpleNamespace(output_folder=str(root), log=lambda _message: None)
        try:
            subprocess.run = counted_run
            start = time.perf_counter()
            FinalHorizontal()._extract_enhance_replace_audio(
                str(horizontal), str(root / "voice_h.aac"), ctx
            )
            FinalVertical()._extract_enhance_replace_audio(
                str(vertical), str(root / "voice_v.aac"), ctx
            )
            legacy_seconds = time.perf_counter() - start
            legacy = dict(counts)

            counts.update(ffmpeg=0, ffprobe=0)
            start = time.perf_counter()
            shared_path = root / "narration_enhanced.wav"
            voice_enhance_filter(str(narration), str(shared_path), log_fn=lambda _message: None)
            shared_seconds = time.perf_counter() - start
            shared = dict(counts)
        finally:
            subprocess.run = real_run

        duration_before = probe_duration(str(narration))
        duration_after = probe_duration(str(shared_path))
        stream = real_run([
            ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate", "-of", "json", str(shared_path),
        ], capture_output=True, text=True, check=True, timeout=30)
        stream_info = json.loads(stream.stdout)["streams"][0]
        if stream_info.get("codec_name") != "pcm_s16le" or int(stream_info.get("sample_rate", 0)) != 48000:
            raise AssertionError(f"Expected 48 kHz lossless PCM WAV, got {stream_info}")
        if abs(duration_before - duration_after) > 0.05:
            raise AssertionError(f"Voice filter changed duration: {duration_before} -> {duration_after}")
        if legacy["ffmpeg"] != 6 or shared["ffmpeg"] != 1:
            raise AssertionError(f"Unexpected FFmpeg invocation counts: legacy={legacy}, shared={shared}")

        report = {
            "status": "PASS",
            "input_seconds": 8,
            "legacy_two_branch_voice_enhancement": {
                "ffmpeg_commands": legacy["ffmpeg"],
                "ffprobe_commands": legacy["ffprobe"],
                "wall_seconds": round(legacy_seconds, 3),
            },
            "shared_lossless_voice_enhancement": {
                "ffmpeg_commands": shared["ffmpeg"],
                "ffprobe_commands": shared["ffprobe"],
                "wall_seconds": round(shared_seconds, 3),
                "stream": stream_info,
                "duration_drift_seconds": round(duration_after - duration_before, 4),
            },
            "ffmpeg_commands_saved": legacy["ffmpeg"] - shared["ffmpeg"],
            "ffmpeg_command_reduction_percent": round((legacy["ffmpeg"] - shared["ffmpeg"]) / legacy["ffmpeg"] * 100, 1),
            "scope_limit": "Synthetic 8-second micro-benchmark for voice enhancement only; not a full-episode render benchmark.",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
