"""Regression tests for media subprocess deadlines and atomic outputs."""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker.engines.media_process import media_timeout_for_duration, run_ffmpeg_atomic


class TestMediaTimeoutPolicy(unittest.TestCase):
    def test_scales_timeout_to_media_duration(self):
        self.assertEqual(media_timeout_for_duration(20, minimum_seconds=30), 160)

    def test_invalid_duration_uses_bounded_minimum(self):
        for value in (None, "bad", float("nan"), float("inf"), -12):
            with self.subTest(value=value):
                self.assertEqual(media_timeout_for_duration(value, minimum_seconds=45), 45)

    def test_very_long_duration_is_capped(self):
        self.assertEqual(media_timeout_for_duration(10**9, maximum_seconds=900), 900)


class TestAtomicMediaOutput(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-atomic-media-")
        self.root = Path(self.temp.name)
        self.output = self.root / "final.mp4"
        self.output.write_bytes(b"old-valid-result")

    def tearDown(self):
        self.temp.cleanup()

    def test_success_publishes_temp_output_atomically(self):
        def fake_run(command, **kwargs):
            Path(command[-1]).write_bytes(b"new-complete-result")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("video_maker.engines.media_process.subprocess.run", side_effect=fake_run):
            result = run_ffmpeg_atomic(["ffmpeg", "-y", str(self.output)], str(self.output), timeout=10)
        self.assertEqual(result, os.path.abspath(self.output))
        self.assertEqual(self.output.read_bytes(), b"new-complete-result")
        self.assertEqual(list(self.root.glob("*.vm_tmp_*")), [])
        self.assertEqual(list(self.root.glob(".*.vm_tmp_*")), [])

    def test_timeout_removes_partial_file_and_preserves_existing_output(self):
        seen_temp = []

        def fake_run(command, **kwargs):
            tmp = Path(command[-1])
            seen_temp.append(tmp)
            tmp.write_bytes(b"partial-corrupt-mp4")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with patch("video_maker.engines.media_process.subprocess.run", side_effect=fake_run):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_ffmpeg_atomic(["ffmpeg", "-y", str(self.output)], str(self.output), timeout=0.01)
        self.assertEqual(self.output.read_bytes(), b"old-valid-result")
        self.assertTrue(seen_temp)
        self.assertFalse(seen_temp[0].exists())

    def test_nonzero_exit_preserves_old_output_and_removes_partial(self):
        seen_temp = []

        def fake_run(command, **kwargs):
            tmp = Path(command[-1])
            seen_temp.append(tmp)
            tmp.write_bytes(b"partial")
            return subprocess.CompletedProcess(command, 1, "", "synthetic failure")

        with patch("video_maker.engines.media_process.subprocess.run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                run_ffmpeg_atomic(["ffmpeg", "-y", str(self.output)], str(self.output), timeout=10)
        self.assertEqual(self.output.read_bytes(), b"old-valid-result")
        self.assertFalse(seen_temp[0].exists())

    def test_requires_output_as_last_argument(self):
        with self.assertRaisesRegex(ValueError, "final command argument"):
            run_ffmpeg_atomic(["ffmpeg", str(self.output), "-f", "null", "-"], str(self.output), timeout=10)


if __name__ == "__main__":
    unittest.main()

class TestSubprocessTimeoutCoverage(unittest.TestCase):
    def test_macos_window_focus_helper_has_bounded_osascript_timeout(self):
        import inspect
        from video_maker.main import _bring_window_to_front
        source = inspect.getsource(_bring_window_to_front)
        self.assertIn('timeout=15', source)
