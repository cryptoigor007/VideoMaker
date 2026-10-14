#!/usr/bin/env python3
"""Reproducible synthetic FFmpeg smoke test for speech-rate preparation.

This validates the WPM maths, outer trimming, FFmpeg execution, output probing and
subtitle timestamp retiming on a synthetic harmonic signal. It is NOT a human-voice
or perceived-timbre quality test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
if str(PACK_ROOT) not in sys.path:
    sys.path.insert(0, str(PACK_ROOT))

from video_maker.engines.speech_rate import prepare_narration  # noqa: E402


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("SKIP: ffmpeg and ffprobe are required for this smoke test.")
        return 2

    with tempfile.TemporaryDirectory(prefix="videomaker-speech-smoke-") as temp:
        work = Path(temp)
        source = work / "synthetic_source.wav"
        output = work / "synthetic_prepared.wav"
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-filter_complex", "[0:a][1:a]amix=inputs=2:weights=0.8 0.2:normalize=0[a]",
            "-map", "[a]", "-c:a", "pcm_s16le", str(source),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)

        tokens = [f"слово{i + 1}" for i in range(24)]
        words = []
        for i, token in enumerate(tokens):
            start = round(0.70 + i * 0.245, 6)
            words.append({"word": token, "start": start, "end": round(start + 0.12, 6)})
        transcription = {
            "text": " ".join(tokens),
            "segments": [{
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "text": " ".join(tokens),
                "words": words,
            }],
        }
        logs: list[str] = []
        audio_path, updated, metrics = prepare_narration(
            str(source), str(output), transcription,
            ffmpeg_bin=ffmpeg,
            ffprobe_bin=ffprobe,
            log_fn=logs.append,
        )
        if not Path(audio_path).is_file():
            raise AssertionError("prepared narration file was not created")
        if not 119.0 <= metrics["final_wpm"] <= 121.0:
            raise AssertionError(f"expected final WPM about 120, got {metrics['final_wpm']}")
        if metrics["word_count"] != 24:
            raise AssertionError(f"expected 24 lexical words, got {metrics['word_count']}")
        if metrics["trim_method"] != "word_timestamps":
            raise AssertionError(f"expected word-timestamp trim, got {metrics['trim_method']}")
        if not updated.get("segments") or not updated["segments"][0].get("words"):
            raise AssertionError("word timestamps were lost during retiming")

        result = {
            "status": "PASS",
            "input_duration_seconds": 8.0,
            "word_count": metrics["word_count"],
            "trim_start_seconds": metrics["trim_start_seconds"],
            "trim_end_seconds": metrics["trim_end_seconds"],
            "measured_wpm_before_adjustment": metrics["measured_wpm_before_adjustment"],
            "final_duration_seconds": metrics["final_duration_seconds"],
            "final_wpm": metrics["final_wpm"],
            "tempo_method": metrics["tempo_method"],
            "output_file_probeable": True,
            "subtitle_word_timestamps_preserved": True,
            "note": "Synthetic harmonic signal only; does not assess human voice timbre.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
