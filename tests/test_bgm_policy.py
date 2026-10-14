"""Tests for persistent per-output BGM shuffling and explicit-file behavior."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_maker.engines import bgm_policy


class TestBgmTrackPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "music"
        self.folder.mkdir()
        for name in ("01-calm.mp3", "02-atmosphere.mp3", "03-piano.mp3", "04-synth.mp3", "05-strings.mp3", "06-ambient.mp3"):
            (self.folder / name).write_bytes(b"test fixture; selector reads names only")
        self.state = self.root / "cache" / "bgm-selection.json"
        self.old_state_path = bgm_policy._BGM_SELECTION_STATE_PATH
        bgm_policy._BGM_SELECTION_STATE_PATH = str(self.state)

    def tearDown(self):
        bgm_policy._BGM_SELECTION_STATE_PATH = self.old_state_path
        bgm_policy._WARNED_SINGLE_SOURCE.clear()
        self.temp.cleanup()

    def test_each_short_gets_an_asset_specific_track_and_recent_five_do_not_repeat(self):
        selected = [
            bgm_policy.pick_bgm_track(str(self.folder), seed_key=f"episode-1|short-{i:03d}")
            for i in range(1, 25)
        ]
        for index, track in enumerate(selected):
            self.assertNotIn(track, selected[max(0, index - 5):index])
        self.assertEqual(len(set(selected[:6])), 6)

    def test_same_asset_key_is_stable_across_rerender(self):
        first = bgm_policy.pick_bgm_track(str(self.folder), seed_key="episode-001|wide")
        again = bgm_policy.pick_bgm_track(str(self.folder), seed_key="episode-001|wide")
        self.assertEqual(first, again)

    def test_cached_asset_use_is_recorded_for_the_next_selection(self):
        first = bgm_policy.pick_bgm_track(str(self.folder), seed_key="asset-1")
        self.assertEqual(first, bgm_policy.pick_bgm_track(str(self.folder), seed_key="asset-1"))
        second = bgm_policy.pick_bgm_track(str(self.folder), seed_key="asset-2")
        self.assertNotEqual(first, second)

    def test_history_persists_across_selector_state_reload(self):
        selected = [bgm_policy.pick_bgm_track(str(self.folder), seed_key=f"before-{i}") for i in range(4)]
        # The function loads state from disk each time, equivalent to a new process.
        next_track = bgm_policy.pick_bgm_track(str(self.folder), seed_key="after-restart")
        self.assertNotIn(next_track, selected)
        raw = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)

    def test_two_track_pool_alternates_without_consecutive_repeat(self):
        folder = self.root / "two"
        folder.mkdir()
        for name in ("a.mp3", "b.mp3"):
            (folder / name).write_bytes(b"x")
        sequence = [bgm_policy.pick_bgm_track(str(folder), seed_key=f"two-{i}") for i in range(20)]
        self.assertTrue(all(a != b for a, b in zip(sequence, sequence[1:])))

    def test_direct_audio_file_is_supported_and_honored(self):
        track = self.folder / "03-piano.mp3"
        expected_track = os.path.abspath(track)
        self.assertEqual(bgm_policy.list_bgm_tracks(str(track)), [expected_track])
        self.assertEqual(bgm_policy.pick_bgm_track(str(track), seed_key="short-file-1"), expected_track)
        self.assertEqual(bgm_policy.pick_bgm_track(str(track), seed_key="short-file-2"), expected_track)

    def test_empty_folder_returns_no_track(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(bgm_policy.pick_bgm_track(str(empty), seed_key="episode"), "")

    def test_opus_is_supported(self):
        opus = self.folder / "07-field-recording.opus"
        opus.write_bytes(b"test fixture")
        self.assertIn(str(opus), bgm_policy.list_bgm_tracks(str(self.folder)))

    def test_multi_track_selection_fails_closed_on_malformed_folder_history(self):
        with tempfile.TemporaryDirectory(prefix="vm-bgm-policy-malformed-") as root:
            music = Path(root) / "music"
            music.mkdir()
            (music / "one.mp3").write_bytes(b"x")
            (music / "two.mp3").write_bytes(b"x")
            state_path = Path(root) / "state.json"
            scope = os.path.normcase(os.path.abspath(music))
            folder_key = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]
            state_path.write_text(json.dumps({"version": 2, "folders": {
                folder_key: {"choices": {}, "recent_tracks": "corrupt"}
            }}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid recent_tracks"):
                bgm_policy.pick_bgm_track(str(music), "asset", state_path=str(state_path))

    def test_single_track_does_not_overwrite_corrupt_global_history(self):
        with tempfile.TemporaryDirectory(prefix="vm-bgm-policy-single-corrupt-") as root:
            music = Path(root) / "music"
            music.mkdir()
            (music / "only.mp3").write_bytes(b"x")
            state_path = Path(root) / "state.json"
            original = "{broken global history"
            state_path.write_text(original, encoding="utf-8")
            chosen = bgm_policy.pick_bgm_track(str(music), "asset", state_path=str(state_path))
            self.assertEqual(Path(chosen).name, "only.mp3")
            self.assertEqual(state_path.read_text(encoding="utf-8"), original)

    def test_multi_track_selection_fails_closed_if_persistent_history_is_corrupt(self):
        corrupt_state = self.root / "corrupt-history.json"
        corrupt_state.write_text("{not valid json", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "history is unreadable"):
            bgm_policy.pick_bgm_track(
                str(self.folder), seed_key="must-not-forget-history", state_path=str(corrupt_state)
            )

    def test_multi_track_selection_fails_if_history_cannot_be_saved(self):
        state_path = self.root / "cache" / "selection.json"
        with patch.object(bgm_policy, "_write_state", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "history could not be persisted"):
                bgm_policy.pick_bgm_track(
                    str(self.folder), seed_key="must-fail-closed", state_path=str(state_path)
                )

    def test_removed_tracks_are_ignored_in_recent_history(self):
        first = bgm_policy.pick_bgm_track(str(self.folder), seed_key="before-remove")
        Path(first).unlink()
        next_track = bgm_policy.pick_bgm_track(str(self.folder), seed_key="after-remove")
        self.assertNotEqual(first, next_track)


if __name__ == "__main__":
    unittest.main()
