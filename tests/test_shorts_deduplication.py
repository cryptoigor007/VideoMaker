"""Regression tests for duplicate Shorts and repeated delivery outputs."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker.pipeline.shorts import dedupe_short_clips, _ensure_hashtags, _required_hashtags
from video_maker.pipeline.finalize import FinalizeStage
from video_maker.pipeline.stages import PipelineContext


class TestConfiguredShortsHashtags(unittest.TestCase):
    def test_custom_required_hashtags_override_brand_defaults(self):
        required = _required_hashtags({"required_hashtags": "#MyChannel #MySeries"})
        output = _ensure_hashtags("#Topic #MyChannel", required)
        self.assertEqual(required, ("#MyChannel", "#MySeries"))
        self.assertTrue(output.startswith("#MyChannel #MySeries"))
        self.assertIn("#Topic", output)
        self.assertNotIn("#ТочкаНаблюдения", output)
        self.assertNotIn("#ТайныйКризисЧеловечества", output)

    def test_empty_custom_setting_uses_project_defaults(self):
        required = _required_hashtags({"required_hashtags": ""})
        self.assertEqual(required, ("#ТочкаНаблюдения", "#ТайныйКризисЧеловечества"))

    def test_sidecar_and_upload_text_use_custom_required_hashtags(self):
        with tempfile.TemporaryDirectory(prefix="videomaker-custom-tags-") as temporary:
            cutter = __import__("video_maker.pipeline.shorts", fromlist=["ShortsCutter"]).ShortsCutter()
            clip = {"title": "Title", "description": "Description", "hook": "Hook", "hashtags": "#Topic"}
            required = _required_hashtags({"required_hashtags": "#MyChannel"})
            cutter._write_sidecar_files(clip, temporary, 1, required_tags=required)
            tags = (Path(temporary) / "short_001_hashtags.txt").read_text(encoding="utf-8")
            upload = (Path(temporary) / "short_001_upload.txt").read_text(encoding="utf-8")
            self.assertIn("#MyChannel", tags)
            self.assertNotIn("#ТочкаНаблюдения", tags)
            self.assertIn("#MyChannel", upload)


class TestClipWindowDeduplication(unittest.TestCase):
    def test_near_identical_time_window_is_dropped_and_reported(self):
        clips = [
            {"start": 10.0, "end": 25.0, "title": "Первый вариант"},
            {"start": 10.12, "end": 25.18, "title": "Повтор"},
            {"start": 10.8, "end": 27.0, "title": "Другой фрагмент"},
        ]
        unique, dropped = dedupe_short_clips(clips)
        self.assertEqual(len(unique), 2)
        self.assertEqual(unique[0]["title"], "Первый вариант")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["dropped_index"], 2)
        self.assertEqual(dropped[0]["kept_index"], 1)

    def test_distinct_windows_are_preserved(self):
        unique, dropped = dedupe_short_clips([
            {"start": 1, "end": 12},
            {"start": 12.5, "end": 24},
            {"start": 1.0, "end": 8.0},
        ])
        self.assertEqual(len(unique), 3)
        self.assertEqual(dropped, [])

    def test_invalid_window_does_not_crash_deduplication(self):
        clips = [{"start": "bad", "end": 4}, {"start": 5, "end": 4}]
        unique, dropped = dedupe_short_clips(clips)
        self.assertEqual(unique, clips)
        self.assertEqual(dropped, [])


class TestCrossPlatformIdenticalDelivery(unittest.TestCase):

    def _run_platform_bgm_case(self, mirror_track_bytes: bytes, temporary: str):
        root = Path(temporary)
        source = root / "short-source.mp4"
        source.write_bytes(b"synthetic test media" * 500)
        bgm_youtube = root / "bgm-youtube"
        bgm_mirror = root / "bgm-mirror"
        bgm_youtube.mkdir()
        bgm_mirror.mkdir()
        (bgm_youtube / "track.mp3").write_bytes(b"identical-track-content" * 100)
        (bgm_mirror / "track.mp3").write_bytes(mirror_track_bytes)

        ctx = PipelineContext(
            audio_path=str(source),
            output_folder=str(root),
            series_name="Series A",
            final_horizontal=str(source),
            final_vertical=str(source),
            shorts=[str(source)],
            short_indices=[1],
            keep_temp_files=True,
            add_bgm=False,
            bgm_platforms=[
                {"id": "youtube", "label": "YouTube", "folder": str(bgm_youtube),
                 "bgm_enabled": True, "bgm_folder_configured": True, "bgm_available": True},
                {"id": "mirror", "label": "Mirror", "folder": str(bgm_mirror),
                 "bgm_enabled": True, "bgm_folder_configured": True, "bgm_available": True},
            ],
            analysis={
                "clips_for_shorts": [{
                    "start": 1, "end": 3, "title": "Title",
                    "description": "A valid short description",
                    "hook": "Hook", "hashtags": "#TestTag",
                }],
                "package_title": "Long video title",
                "package_description": "Long description with details",
                "package_hook": "Hook",
                "package_hashtags": "#TestTag",
            },
        )
        calls = []

        def fake_pick(folder: str, seed_key: str = "") -> str:
            return str(Path(folder) / "track.mp3")

        def fake_mix(src: str, folder: str, dst: str, **kwargs) -> str:
            calls.append(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            Path(dst).write_bytes(b"synthetic mixed video" * 100)
            return dst

        with patch("video_maker.engines.audio.pick_bgm_track", side_effect=fake_pick), \
             patch("video_maker.engines.audio.mix_bgm", side_effect=fake_mix):
            FinalizeStage().run(ctx)

        series_dir = root / "Series A"
        report = json.loads((series_dir / "bgm_report.json").read_text(encoding="utf-8"))
        short_mix_calls = [call for call in calls if os.sep + "shorts" + os.sep in call]
        duplicate = series_dir / "mirror" / "shorts" / "short_001" / "short_001.mp4"
        canonical = series_dir / "youtube" / "shorts" / "short_001" / "short_001.mp4"
        return short_mix_calls, report, canonical, duplicate

    def test_identical_bgm_short_is_copied_without_second_mix(self):
        same_track = b"identical-track-content" * 100
        with tempfile.TemporaryDirectory(prefix="videomaker-bgm-content-dedupe-") as temporary:
            calls, report, canonical, duplicate = self._run_platform_bgm_case(same_track, temporary)
            self.assertEqual(len(calls), 1, "identical Short/BGM content should be mixed only once")
            self.assertTrue(canonical.is_file())
            self.assertTrue(duplicate.is_file(), "вторая площадка обязана получить файл шорта")
            self.assertEqual(duplicate.read_bytes(), canonical.read_bytes())
            mirror_dir = duplicate.parent
            self.assertTrue((mirror_dir / "short_001_title.txt").is_file(),
                            "тексты шорта тоже должны быть у второй площадки")
            statuses = {(item.get("platform"), item.get("asset")): item.get("status")
                        for item in report["items"]}
            self.assertEqual(statuses[("Mirror", "shorts/short_001")], "deduplicated")

    def test_different_bgm_content_is_kept_as_a_distinct_variant(self):
        with tempfile.TemporaryDirectory(prefix="videomaker-bgm-distinct-") as temporary:
            calls, report, canonical, duplicate = self._run_platform_bgm_case(
                b"a-different-track-content" * 100, temporary
            )
            self.assertEqual(len(calls), 2, "different soundtrack content must remain a distinct variant")
            self.assertTrue(canonical.is_file())
            self.assertTrue(duplicate.is_file())
            statuses = {(item.get("platform"), item.get("asset")): item.get("status")
                        for item in report["items"]}
            self.assertEqual(statuses[("Mirror", "shorts/short_001")], "mixed")

    def test_identical_voice_only_short_is_available_on_every_platform(self):
        with tempfile.TemporaryDirectory(prefix="videomaker-dedupe-") as temporary:
            root = Path(temporary)
            source = root / "short-source.mp4"
            source.write_bytes(b"synthetic test media" * 500)
            ctx = PipelineContext(
                audio_path=str(source),
                output_folder=str(root),
                series_name="Series A",
                final_horizontal=str(source),
                final_vertical=str(source),
                shorts=[str(source)],
                short_indices=[1],
                keep_temp_files=True,
                add_bgm=False,
                bgm_platforms=[
                    {"id": "youtube", "label": "YouTube", "folder": "", "bgm_enabled": False},
                    {"id": "mirror", "label": "Mirror", "folder": "", "bgm_enabled": False},
                ],
                analysis={
                    "clips_for_shorts": [{
                        "start": 1, "end": 3, "title": "Title",
                        "description": "A valid short description",
                        "hook": "Hook", "hashtags": "#TestTag",
                    }],
                    "package_title": "Long video title",
                    "package_description": "Long description with details",
                    "package_hook": "Hook",
                    "package_hashtags": "#TestTag",
                },
            )
            staging = root / "Series A" / "_tmp" / "shorts_staging"
            staging.mkdir(parents=True, exist_ok=True)
            staging_sentinel = staging / "keep-temp-sentinel.bin"
            staging_sentinel.write_bytes(b"keep_temp_files must preserve this")
            FinalizeStage().run(ctx)
            self.assertTrue(staging_sentinel.exists(), "keep_temp_files must preserve hidden Shorts staging")
            canonical = root / "Series A" / "youtube" / "shorts" / "short_001" / "short_001.mp4"
            duplicate = root / "Series A" / "mirror" / "shorts" / "short_001" / "short_001.mp4"
            report = json.loads((root / "Series A" / "bgm_report.json").read_text(encoding="utf-8"))
            self.assertTrue(canonical.is_file())
            self.assertTrue(duplicate.is_file(), "вторая площадка обязана получить файл шорта")
            self.assertEqual(duplicate.read_bytes(), canonical.read_bytes())
            deduped = [item for item in report["items"] if item.get("status") == "deduplicated"]
            self.assertTrue(deduped)
            self.assertEqual(deduped[0]["canonical_destination"], str(canonical))


    def test_master_files_are_kept_only_on_the_canonical_platform(self):
        with tempfile.TemporaryDirectory(prefix="videomaker-master-platform-copy-") as temporary:
            root = Path(temporary)
            master_h = root / "master-source-h.mp4"
            master_h.write_bytes(b"master media h" * 400)
            master_v = root / "master-source-v.mp4"
            master_v.write_bytes(b"master media v" * 400)
            final = root / "final-source.mp4"
            final.write_bytes(b"final media" * 500)
            ctx = PipelineContext(
                audio_path=str(master_h),
                output_folder=str(root),
                series_name="Series Masters",
                master_horizontal=str(master_h),
                master_vertical=str(master_v),
                final_horizontal=str(final),
                final_vertical=str(final),
                keep_temp_files=False,
                bgm_platforms=[
                    {"id": "youtube", "label": "YouTube", "folder": "", "bgm_enabled": False},
                    {"id": "mirror", "label": "Mirror", "folder": "", "bgm_enabled": False},
                ],
                analysis={},
            )
            FinalizeStage().run(ctx)
            series = root / "Series Masters"
            # Мастера — служебные файлы: остаются только у каноничной площадки.
            self.assertTrue((series / "youtube" / "wide" / "master_16x9.mp4").is_file())
            self.assertTrue((series / "youtube" / "vertical" / "master_9x16.mp4").is_file())
            self.assertFalse((series / "mirror" / "wide" / "master_16x9.mp4").exists())
            self.assertFalse((series / "mirror" / "vertical" / "master_9x16.mp4").exists())
            # Финалы при этом доезжают до каждой площадки.
            for platform in ("youtube", "mirror"):
                self.assertTrue((series / platform / "wide" / "final_16x9.mp4").is_file())
                self.assertTrue((series / platform / "vertical" / "final_9x16.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
