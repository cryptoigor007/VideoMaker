"""Unit tests for BGM filter structure and ffprobe-based output verification."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from video_maker.engines.bgm_validation import validate_bgm_filter, verify_bgm_output


def probe_result(payload: dict, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)


class TestBgmFilterValidation(unittest.TestCase):
    def test_accepts_two_input_voice_plus_music_amix(self):
        validate_bgm_filter("[1:a]aloop[bg];[0:a][bg]amix=inputs=2[out]")

    def test_rejects_filter_missing_music_input(self):
        with self.assertRaisesRegex(RuntimeError, r"\[1:a\]"):
            validate_bgm_filter("[0:a]anull[out]")


class TestBgmOutputProbe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-bgm-probe-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.output = self.root / "mixed.mp4"
        self.source.write_bytes(b"s" * 4096)
        self.output.write_bytes(b"o" * 4096)
        self.src_probe = {
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.0"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "10.0"}],
        }
        self.out_probe = {
            "format": {"duration": "10.1"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.1"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "10.1"}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_decyodable_video_with_audio_and_matching_duration(self):
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(self.src_probe), probe_result(self.out_probe)
        ]):
            result = verify_bgm_output(str(self.source), str(self.output))
        self.assertTrue(result["audio_stream_present"])
        self.assertEqual(result["audio_codec"], "aac")
        self.assertAlmostEqual(result["output_duration_seconds"], 10.1)

    def test_rejects_source_without_voice_audio_stream(self):
        no_audio_source = {
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.0"}],
        }
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(no_audio_source), probe_result(self.out_probe)
        ]):
            with self.assertRaisesRegex(RuntimeError, "no voice audio stream"):
                verify_bgm_output(str(self.source), str(self.output))

    def test_rejects_output_without_audio_stream(self):
        no_audio = {
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.0"}],
        }
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(self.src_probe), probe_result(no_audio)
        ]):
            with self.assertRaisesRegex(RuntimeError, "no audio stream"):
                verify_bgm_output(str(self.source), str(self.output))

    def test_rejects_output_with_unexpected_duration(self):
        bad_duration = {
            "format": {"duration": "30.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "30.0"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "30.0"}],
        }
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(self.src_probe), probe_result(bad_duration)
        ]):
            with self.assertRaisesRegex(RuntimeError, "duration differs"):
                verify_bgm_output(str(self.source), str(self.output))

    def test_rejects_ffprobe_error(self):
        with patch("video_maker.engines.bgm_validation.subprocess.run", return_value=probe_result({}, 1, "bad media")):
            with self.assertRaisesRegex(RuntimeError, "ffprobe exited"):
                verify_bgm_output(str(self.source), str(self.output))


if __name__ == "__main__":
    unittest.main()


def test_source_metadata_probe_is_cached_across_repeated_bgm_outputs():
    """The staged source is unchanged; only each newly created output needs probing."""
    from video_maker.engines import bgm_validation

    with tempfile.TemporaryDirectory(prefix="vm-bgm-probe-cache-") as tmp:
        root = Path(tmp)
        source = root / "source.mp4"
        output_one = root / "mixed-one.mp4"
        output_two = root / "mixed-two.mp4"
        source.write_bytes(b"s" * 4096)
        output_one.write_bytes(b"o" * 4096)
        output_two.write_bytes(b"t" * 4096)
        source_probe = {
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.0"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "10.0"}],
        }
        output_probe = {
            "format": {"duration": "10.1"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.1"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "10.1"}],
        }
        with bgm_validation._PROBE_CACHE_LOCK:
            bgm_validation._PROBE_CACHE.clear()
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(source_probe), probe_result(output_probe), probe_result(output_probe),
        ]) as run:
            verify_bgm_output(str(source), str(output_one))
            verify_bgm_output(str(source), str(output_two))
        assert run.call_count == 3  # source once, plus one probe per output


def test_source_metadata_probe_cache_invalidates_when_file_changes():
    from video_maker.engines import bgm_validation

    with tempfile.TemporaryDirectory(prefix="vm-bgm-probe-invalidate-") as tmp:
        source = Path(tmp) / "source.mp4"
        source.write_bytes(b"s" * 4096)
        payload = {
            "format": {"duration": "10.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "10.0"},
                       {"codec_type": "audio", "codec_name": "aac", "duration": "10.0"}],
        }
        with bgm_validation._PROBE_CACHE_LOCK:
            bgm_validation._PROBE_CACHE.clear()
        with patch("video_maker.engines.bgm_validation.subprocess.run", side_effect=[
            probe_result(payload), probe_result(payload),
        ]) as run:
            first = bgm_validation._probe(str(source), "ffprobe")
            source.write_bytes(b"s" * 8192)  # size change must invalidate cache key
            second = bgm_validation._probe(str(source), "ffprobe")
        assert first["size_bytes"] == 4096
        assert second["size_bytes"] == 8192
        assert run.call_count == 2
