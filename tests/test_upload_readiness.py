from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_maker.engines.upload_readiness import evaluate_upload_readiness, find_missing_short_indices


class TestUploadReadiness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vm-upload-ready-")
        self.root = Path(self.temp.name)
        self.video = self.root / "final.mp4"
        self.video.write_bytes(b"v" * 2048)
        self.title = self.root / "title.txt"
        self.description = self.root / "description.txt"
        self.hashtags = self.root / "hashtags.txt"
        self.title.write_text("A meaningful title", encoding="utf-8")
        self.description.write_text("A meaningful description", encoding="utf-8")
        self.hashtags.write_text("#topic #video", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def evaluate(self, status="mixed"):
        return evaluate_upload_readiness(
            video_path=str(self.video), title_path=str(self.title),
            description_path=str(self.description), hashtags_path=str(self.hashtags),
            bgm_status=status,
        )

    def test_complete_asset_and_confirmed_mix_is_ready(self):
        result = self.evaluate("mixed")
        self.assertTrue(result["ready_for_manual_upload"])
        self.assertTrue(result["metadata_files_exist"])
        self.assertTrue(result["metadata_content_valid"])
        self.assertEqual(result["issues"], [])

    def test_explicitly_disabled_bgm_is_accepted(self):
        self.assertTrue(self.evaluate("voice_only_no_bgm_configured")["ready_for_manual_upload"])

    def test_voice_only_fallback_after_requested_bgm_blocks_upload(self):
        result = self.evaluate("voice_only_fallback")
        self.assertFalse(result["ready_for_manual_upload"])
        self.assertTrue(any("BGM status" in issue for issue in result["issues"]))

    def test_no_final_file_after_requested_bgm_failure_blocks_upload(self):
        for status in ("mix_failed_no_final_file", "bgm_unavailable_no_final_file"):
            with self.subTest(status=status):
                result = self.evaluate(status)
                self.assertFalse(result["ready_for_manual_upload"])
                self.assertTrue(any(f"BGM status is not accepted: {status}" == issue for issue in result["issues"]))

    def test_empty_metadata_files_are_not_considered_ready(self):
        self.description.write_text("  \n", encoding="utf-8")
        result = self.evaluate()
        self.assertTrue(result["metadata_files_exist"])
        self.assertFalse(result["metadata_content_valid"])
        self.assertFalse(result["ready_for_manual_upload"])

    def test_missing_metadata_file_is_blocked(self):
        self.hashtags.unlink()
        result = self.evaluate()
        self.assertFalse(result["metadata_files_exist"])
        self.assertFalse(result["ready_for_manual_upload"])

    def test_missing_or_tiny_video_is_blocked(self):
        self.video.write_bytes(b"tiny")
        result = self.evaluate()
        self.assertTrue(result["video_exists"])
        self.assertFalse(result["video_nonempty"])
        self.assertFalse(result["ready_for_manual_upload"])
        self.video.unlink()
        result = self.evaluate()
        self.assertFalse(result["video_exists"])
        self.assertFalse(result["ready_for_manual_upload"])

    def test_missing_short_indices_are_reported_without_renumbering(self):
        self.assertEqual(find_missing_short_indices(5, [1, 2, 4, 5]), [3])
        self.assertEqual(find_missing_short_indices(4, [1, 2, 3, 4]), [])

    def test_placeholder_metadata_is_not_accepted(self):
        self.title.write_text("—", encoding="utf-8")
        self.assertFalse(self.evaluate()["ready_for_manual_upload"])


if __name__ == "__main__":
    unittest.main()
