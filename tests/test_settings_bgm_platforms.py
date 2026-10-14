"""Regression tests for BGM checkbox isolation and early validation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_maker.config.settings import Settings


class TestBgmPlatformSettings(unittest.TestCase):
    def test_other_platform_bgm_does_not_reenable_youtube_bgm(self):
        with tempfile.TemporaryDirectory(prefix="vm-bgm-settings-") as temporary:
            root = Path(temporary)
            youtube = root / "youtube"
            tiktok = root / "tiktok"
            youtube.mkdir()
            tiktok.mkdir()
            (youtube / "yt.mp3").write_bytes(b"music")
            (tiktok / "tt.mp3").write_bytes(b"music")
            settings = Settings(
                bgm_youtube_enable=False,
                bgm_youtube_folder=str(youtube),
                bgm_folder=str(youtube),
                bgm_tiktok_enable=True,
                bgm_tiktok_folder=str(tiktok),
                add_bgm=True,
            )
            platforms = {item["id"]: item for item in settings.active_bgm_platforms()}
            self.assertIn("youtube", platforms)
            self.assertFalse(platforms["youtube"]["bgm_enabled"])
            self.assertEqual(platforms["youtube"]["folder"], "")
            self.assertTrue(platforms["tiktok"]["bgm_enabled"])
            self.assertEqual(platforms["tiktok"]["folder"], str(tiktok))

    def test_enabled_youtube_bgm_without_source_fails_validation_early(self):
        settings = Settings(bgm_youtube_enable=True, bgm_youtube_folder="", bgm_folder="")
        errors = settings.validate()
        self.assertTrue(any("YouTube" in error and "источник не выбран" in error for error in errors))

    def test_disabled_youtube_bgm_does_not_validate_stale_legacy_folder(self):
        settings = Settings(
            bgm_youtube_enable=False,
            bgm_youtube_folder="/path/that/no/longer/exists",
            bgm_folder="/another/stale/path",
            add_bgm=False,
        )
        errors = settings.validate()
        self.assertFalse(any("BGM YouTube" in error or "Источник BGM YouTube" in error for error in errors))

    def test_enabled_platform_with_empty_folder_fails_validation_early(self):
        settings = Settings(
            bgm_youtube_enable=False,
            bgm_tiktok_enable=True,
            bgm_tiktok_folder="",
        )
        errors = settings.validate()
        self.assertTrue(any("TikTok" in error and "источник не выбран" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
