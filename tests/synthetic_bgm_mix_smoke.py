"""End-to-end synthetic FFmpeg test for a voice + BGM mix.

This verifies the two-input filter graph, invokes real ffmpeg/ffprobe, and checks
that the written output has decodable video/audio streams and sane duration. It is
not a subjective loudness or human-voice listening test.
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

from video_maker.engines.bgm_validation import validate_bgm_filter, verify_bgm_output  # noqa: E402


def run(command: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"STDERR:\n{(result.stderr or '')[-3000:]}"
        )
    return result


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print(json.dumps({"status": "SKIP", "reason": "ffmpeg/ffprobe not installed"}, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="videomaker-bgm-smoke-") as temp:
        root = Path(temp)
        source = root / "source_voice.mp4"
        bgm = root / "bgm.wav"
        output = root / "mixed.mp4"

        # Synthetic 4-second video with a 440 Hz speech stand-in.
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(source),
        ])
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1",
            "-c:a", "pcm_s16le", str(bgm),
        ])

        # Same two-input ducking/mix contract as VideoMaker's build_bgm_filter.
        graph = (
            "[1:a]aloop=loop=-1:size=200000,"
            "loudnorm=I=-18:TP=-3:LRA=11,"
            "afade=t=in:st=0:d=0.1,atrim=0:4.000,asetpts=N/SR/TB[bg];"
            "[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[bgd];"
            "[0:a][bgd]amix=inputs=2:duration=first:weights=1 0.9:dropout_transition=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11[out]"
        )
        validate_bgm_filter(graph)
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-i", str(bgm),
            "-filter_complex", graph,
            "-map", "0:v:0", "-map", "[out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            str(output),
        ], timeout=120)
        verified = verify_bgm_output(str(source), str(output))

        # Decode the mixed audio fully; ffprobe alone would not catch all decode errors.
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
            "-map", "0:a:0", "-f", "null", "-",
        ], timeout=60)

        result = {
            "status": "PASS",
            "test": "synthetic voice + BGM FFmpeg render",
            "source_duration_seconds": verified["source_duration_seconds"],
            "output_duration_seconds": verified["output_duration_seconds"],
            "output_size_bytes": verified["output_size_bytes"],
            "video_codec": verified["video_codec"],
            "audio_codec": verified["audio_codec"],
            "audio_stream_present": verified["audio_stream_present"],
            "full_audio_decode": "PASS",
            "limitation": "synthetic tones only; does not measure subjective music audibility or voice timbre",
        }
        print(json.dumps(result, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
