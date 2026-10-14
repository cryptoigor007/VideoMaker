from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from video_maker.engines.speech_rate import (
    atempo_chain,
    count_words,
    count_words_in_window,
    effective_wpm,
    find_long_pause_intervals,
    find_trim_bounds,
    plan_tempo_factor,
    prepare_narration,
    retime_transcription,
)


class TestWordCount(unittest.TestCase):
    def test_punctuation_does_not_count_as_words(self):
        self.assertEqual(count_words("— «Привет,» миру! Кто-то… 123, 2026."), 5)

    def test_punctuation_only_is_zero(self):
        self.assertEqual(count_words(".,!? — …"), 0)

    def test_hyphenated_compound_is_one_word(self):
        self.assertEqual(count_words("кто-то из-за чего-либо"), 3)

    def test_trimmed_word_window_counts_only_retained_timed_words(self):
        transcript = {
            "text": "лишнее начало привет мир лишний хвост",
            "segments": [{
                "text": "лишнее начало привет мир лишний хвост",
                "words": [
                    {"word": "лишнее", "start": 0.1, "end": 0.3},
                    {"word": "начало", "start": 0.4, "end": 0.6},
                    {"word": "привет", "start": 1.0, "end": 1.2},
                    {"word": "мир!", "start": 1.4, "end": 1.6},
                    {"word": "лишний", "start": 2.0, "end": 2.2},
                    {"word": "хвост", "start": 2.3, "end": 2.5},
                ],
            }],
        }
        self.assertEqual(count_words_in_window(transcript, 0.9, 1.7), 2)

    def test_hyphen_split_tokens_count_as_one_word_in_window(self):
        """«Что» + «-то» — одно слово (как count_words), а не два: иначе WPM завышен."""
        transcript = {
            "text": "это что-то странное",
            "segments": [{"text": "это что-то странное", "words": [
                {"word": "это", "start": 0.0, "end": 0.3},
                {"word": "Что", "start": 0.3, "end": 0.6},
                {"word": "-то", "start": 0.6, "end": 0.9},
                {"word": "странное", "start": 0.9, "end": 1.5},
            ]}],
        }
        self.assertEqual(count_words_in_window(transcript, 0.0, 2.0), 3)


class TestWpmMath(unittest.TestCase):
    def test_effective_wpm_formula(self):
        self.assertAlmostEqual(effective_wpm(270, 120), 135.0)

    def test_within_band_is_not_changed(self):
        for wpm in (120, 130, 135, 150):
            self.assertEqual(plan_tempo_factor(wpm), 1.0)

    def test_outside_band_targets_lower_limit_by_default(self):
        self.assertAlmostEqual(plan_tempo_factor(100), 1.2)
        self.assertAlmostEqual(plan_tempo_factor(160), 120 / 160)

    def test_atempo_chain_product_matches_requested_speed(self):
        for value in (0.2, 0.5, 0.8, 1.0, 1.8, 2.5, 4.0):
            chain = atempo_chain(value)
            self.assertAlmostEqual(math.prod(chain), value, places=8)
            self.assertTrue(all(0.5 <= item <= 2.0 for item in chain))


class TestTrimAndTimestamps(unittest.TestCase):
    def setUp(self):
        self.transcription = {
            "text": "Привет, мир!",
            "segments": [{
                "start": 0.4,
                "end": 1.6,
                "text": "Привет, мир!",
                "words": [
                    {"word": " Привет,", "start": 0.4, "end": 0.8, "probability": 0.99},
                    {"word": " мир!", "start": 1.1, "end": 1.5, "probability": 0.98},
                ],
            }],
        }

    def test_incomplete_word_timestamps_fall_back_to_full_audio(self):
        transcript = {
            "text": "начало привет мир и ещё слова",
            "segments": [{
                "start": 0.1, "end": 2.8, "text": "начало привет мир и ещё слова",
                "words": [{"word": "привет", "start": 1.0, "end": 1.2}],
            }],
        }
        start, end, method = find_trim_bounds(transcript, 3.0)
        self.assertEqual((start, end, method), (0.0, 3.0, "incomplete_word_timestamps"))
        self.assertEqual(count_words_in_window(transcript, start, end), 6)

    def test_incomplete_word_timestamps_do_not_delete_subtitle_segment(self):
        transcript = {
            "text": "привет мир",
            "segments": [{
                "start": 0.4, "end": 1.6, "text": "привет мир",
                "words": [{"word": "привет", "start": 0.4, "end": 0.8}, {"word": "мир"}],
            }],
        }
        adjusted = retime_transcription(
            transcript, trim_start=0.0, trim_end=2.0,
            tempo_factor=1.25, final_duration=1.6,
        )
        self.assertEqual(len(adjusted["segments"]), 1)
        self.assertEqual(adjusted["segments"][0]["text"], "привет мир")
        self.assertAlmostEqual(adjusted["segments"][0]["start"], 0.32)
        self.assertAlmostEqual(adjusted["segments"][0]["end"], 1.28)
        self.assertNotIn("words", adjusted["segments"][0])

    def test_trim_bounds_keep_guard_bands(self):
        start, end, method = find_trim_bounds(self.transcription, 3.0)
        self.assertAlmostEqual(start, 0.28)
        self.assertAlmostEqual(end, 1.72)
        self.assertEqual(method, "word_timestamps")

    def test_missing_word_timestamps_fail_safe_to_full_audio(self):
        start, end, method = find_trim_bounds({"text": "Привет мир", "segments": []}, 3.0)
        self.assertEqual((start, end, method), (0.0, 3.0, "no_word_timestamps"))

    def test_retime_shifts_and_scales_words(self):
        adjusted = retime_transcription(
            self.transcription,
            trim_start=0.2,
            trim_end=1.8,
            tempo_factor=1.25,
            final_duration=1.28,
        )
        words = adjusted["segments"][0]["words"]
        self.assertAlmostEqual(words[0]["start"], 0.16)
        self.assertAlmostEqual(words[0]["end"], 0.48)
        self.assertAlmostEqual(words[1]["start"], 0.72)
        self.assertAlmostEqual(words[1]["end"], 1.04)
        self.assertAlmostEqual(adjusted["segments"][0]["start"], 0.16)
        self.assertAlmostEqual(adjusted["segments"][0]["end"], 1.04)
        self.assertEqual(adjusted["text"], "Привет, мир!")

    def test_separate_punctuation_timestamps_are_preserved(self):
        punct_transcript = {
            "text": "Привет, мир!",
            "segments": [{
                "start": 0.4, "end": 1.51, "text": "Привет, мир!",
                "words": [
                    {"word": "Привет", "start": 0.4, "end": 0.8},
                    {"word": ",", "start": 0.8, "end": 0.81},
                    {"word": "мир", "start": 1.1, "end": 1.5},
                    {"word": "!", "start": 1.5, "end": 1.51},
                ],
            }],
        }
        adjusted = retime_transcription(
            punct_transcript, trim_start=0.2, trim_end=1.8,
            tempo_factor=1.0, final_duration=1.6,
        )
        words = adjusted["segments"][0]["words"]
        self.assertEqual([w["word"] for w in words], ["Привет", ",", "мир", "!"])
        self.assertAlmostEqual(words[1]["start"], 0.6)
        self.assertAlmostEqual(words[3]["end"], 1.31)


class TestOptionalPauseReduction(unittest.TestCase):
    def setUp(self):
        self.transcript = {
            "text": "один два, три четыре.",
            "segments": [{
                "start": 0.2, "end": 3.3, "text": "один два, три четыре.",
                "words": [
                    {"word": "один", "start": 0.2, "end": 0.45},
                    {"word": "два", "start": 1.20, "end": 1.45},
                    {"word": ",", "start": 1.45, "end": 1.47},
                    {"word": "три", "start": 1.70, "end": 1.95},
                    {"word": "четыре.", "start": 2.85, "end": 3.10},
                ],
            }],
        }

    def test_only_long_gaps_are_cut_and_punctuation_pause_is_kept_longer(self):
        intervals = find_long_pause_intervals(
            self.transcript, 0.1, 3.3,
            threshold_seconds=0.55,
            keep_gap_seconds=0.18,
            clause_keep_seconds=0.28,
            sentence_keep_seconds=0.38,
        )
        self.assertEqual(len(intervals), 2)
        # Both gaps exceed the threshold; punctuation event remains outside the cut.
        self.assertAlmostEqual(intervals[0][0], 0.63, places=2)
        self.assertAlmostEqual(intervals[0][1], 1.20, places=2)
        self.assertAlmostEqual(intervals[1][0], 2.13, places=2)
        self.assertAlmostEqual(intervals[1][1], 2.85, places=2)

    def test_comma_boundary_keeps_a_longer_pause(self):
        transcript = {
            "text": "два, три",
            "segments": [{"text": "два, три", "words": [
                {"word": "два,", "start": 0.4, "end": 0.6},
                {"word": "три", "start": 1.7, "end": 1.9},
            ]}],
        }
        intervals = find_long_pause_intervals(transcript, 0.2, 2.2)
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], 0.88, places=2)
        self.assertAlmostEqual(intervals[0][1], 1.7, places=2)

    def test_incomplete_timestamps_disable_pause_cutting(self):
        incomplete = {
            "text": "один два три",
            "segments": [{"text": "один два три", "words": [
                {"word": "один", "start": 0.1, "end": 0.3},
            ]}],
        }
        self.assertEqual(find_long_pause_intervals(incomplete, 0, 4), [])

    def test_removed_gap_is_subtracted_from_subtitle_timeline(self):
        adjusted = retime_transcription(
            self.transcript,
            trim_start=0.1,
            trim_end=3.3,
            tempo_factor=1.0,
            final_duration=2.01,
            removed_intervals=[(0.63, 1.20), (2.13, 2.85)],
        )
        words = adjusted["segments"][0]["words"]
        last_word = next(word for word in words if "четыре" in word.get("word", ""))
        self.assertAlmostEqual(last_word["start"], 1.46, places=2)
        self.assertAlmostEqual(last_word["end"], 1.71, places=2)
        self.assertTrue(any(word.get("word") == "," for word in words))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_ffmpeg_pause_reduction_shortens_audio_and_keeps_timestamps(self):
        with tempfile.TemporaryDirectory(prefix="vm-pause-test-") as tmp:
            source = Path(tmp) / "source.wav"
            output = Path(tmp) / "prepared.wav"
            subprocess.run([
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:a", "pcm_s16le", str(source),
            ], check=True, capture_output=True, text=True, timeout=30)
            transcript = {
                "text": "one two three four",
                "segments": [{"start": 0.3, "end": 3.2, "text": "one two three four", "words": [
                    {"word": "one", "start": 0.30, "end": 0.55},
                    {"word": "two", "start": 1.25, "end": 1.50},
                    {"word": "three", "start": 1.75, "end": 2.00},
                    {"word": "four", "start": 2.90, "end": 3.15},
                ]}],
            }
            output_path, adjusted, metrics = prepare_narration(
                str(source), str(output), transcript,
                min_wpm=10, max_wpm=300, target_wpm=120,
                remove_long_pauses=True,
            )
            self.assertTrue(Path(output_path).is_file())
            self.assertGreater(metrics["pause_seconds_removed"], 0.8)
            self.assertLess(metrics["final_duration_seconds"], metrics["trimmed_duration_seconds"])
            self.assertEqual(len(adjusted["segments"][0]["words"]), 4)
            self.assertTrue(metrics["pause_reduction_applied"])


class TestHyphenSplitTokensCoverage(unittest.TestCase):
    """Whisper режет дефисные слова на токены («Что» + «-то», «когда» + «-либо,»).

    Покрытие текста токенами при этом полное, и проверка не должна считать
    таймкоды неполными — иначе трим и удаление паузы молча отключаются
    (реальный баг: 74 != 76 → incomplete_word_timestamps на всей серии).
    """

    def setUp(self):
        self.transcription = {
            "text": "Что-то сломалось. Телефон полон контактов.",
            "segments": [
                {
                    "text": "Что-то сломалось.",
                    "words": [
                        {"word": "Что", "start": 0.0, "end": 0.3},
                        {"word": "-то", "start": 0.3, "end": 0.6},
                        {"word": "сломалось.", "start": 0.6, "end": 1.4},
                    ],
                },
                {
                    "text": "Телефон полон контактов.",
                    "words": [
                        {"word": "Телефон", "start": 2.4, "end": 3.0},
                        {"word": "полон", "start": 3.0, "end": 3.4},
                        {"word": "контактов.", "start": 3.4, "end": 4.0},
                    ],
                },
            ],
        }

    def test_trim_bounds_use_word_timestamps_when_hyphen_is_split(self):
        start, end, method = find_trim_bounds(self.transcription, 4.0)
        self.assertEqual(method, "word_timestamps")
        self.assertAlmostEqual(start, 0.0)
        self.assertAlmostEqual(end, 4.0)

    def test_long_pause_after_hyphen_split_sentence_is_found(self):
        intervals = find_long_pause_intervals(self.transcription, 0.0, 4.0)
        self.assertEqual(len(intervals), 1)
        self.assertAlmostEqual(intervals[0][0], 1.78, places=2)  # 1.4 + 0.38 (точка)
        self.assertAlmostEqual(intervals[0][1], 2.4, places=2)


class TestSpeedModes(unittest.TestCase):
    """Ползунок скорости: режимы «по словам/мин» и «по коэффициенту»."""

    def test_normalize_speed_mode_accepts_labels_and_canonical(self):
        from video_maker.engines.speech_rate import normalize_speed_mode

        self.assertEqual(normalize_speed_mode("По коэффициенту"), "coefficient")
        self.assertEqual(normalize_speed_mode("coefficient"), "coefficient")
        self.assertEqual(normalize_speed_mode("По словам/мин"), "wpm")
        self.assertEqual(normalize_speed_mode("wpm"), "wpm")
        self.assertEqual(normalize_speed_mode(""), "wpm")

    def test_speed_params_wpm_mode_uses_target_band(self):
        from video_maker.engines.speech_rate import speed_params_from_settings

        self.assertEqual(
            speed_params_from_settings("По словам/мин", 130, 1.2),
            {"min_wpm": 130.0, "max_wpm": 160.0, "target_wpm": 130.0},
        )

    def test_speed_params_coefficient_mode_clamps(self):
        from video_maker.engines.speech_rate import speed_params_from_settings

        self.assertEqual(
            speed_params_from_settings("По коэффициенту", 120, 1.15),
            {"tempo_coefficient": 1.15},
        )
        self.assertEqual(
            speed_params_from_settings("coefficient", 120, 5.0),
            {"tempo_coefficient": 2.0},
        )

    def test_prepare_narration_applies_explicit_coefficient(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from video_maker.engines import speech_rate as sr

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg недоступен")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.wav"
            output = Path(tmp) / "prepared.wav"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:a", "pcm_s16le", str(source),
            ], check=True, capture_output=True, text=True, timeout=30)
            transcript = {
                "text": "one two three four",
                "segments": [{"start": 0.3, "end": 3.2, "text": "one two three four", "words": [
                    {"word": "one", "start": 0.30, "end": 0.55},
                    {"word": "two", "start": 1.25, "end": 1.50},
                    {"word": "three", "start": 1.75, "end": 2.00},
                    {"word": "four", "start": 2.90, "end": 3.15},
                ]}],
            }
            with patch.object(sr, "SPEECH_STATE_PATH", Path(tmp) / "speech_state.json"):
                out_path, adjusted, metrics = sr.prepare_narration(
                    str(source), str(output), transcript,
                    tempo_coefficient=1.25,
                    remove_long_pauses=False,
                )
                state = sr.load_speech_state()
            self.assertTrue(Path(out_path).is_file())
            self.assertEqual(metrics["speed_mode"], "coefficient")
            self.assertAlmostEqual(metrics["tempo_factor"], 1.25, places=3)
            self.assertAlmostEqual(metrics["actual_tempo_factor"], 1.25, delta=0.05)
            self.assertEqual(state.get("word_count"), 4)
            self.assertAlmostEqual(float(state.get("tempo_factor")), 1.25, places=3)


class TestExtraWords(unittest.TestCase):
    """Повторы/оговорки: слова Whisper, которых нет в тексте Gemini (corrected_text)."""

    def _transcript(self, pairs):
        words = [{"word": w, "start": s, "end": e} for w, s, e in pairs]
        text = " ".join(w for w, _, _ in pairs)
        return {"text": text, "segments": [{"text": text, "words": words}]}

    def test_detects_repeated_phrase_deleted_by_gemini(self):
        from video_maker.engines.speech_rate import find_extra_word_intervals

        tr = self._transcript([
            ("тебе", 10.0, 10.3), ("что", 10.3, 10.5), ("именно", 10.5, 11.0),
            ("ты", 11.0, 11.2), ("знаешь", 11.2, 11.6),   # первый заход
            ("ты", 12.0, 12.2), ("знаешь", 12.2, 12.6),   # повтор — его Gemini оставил
            ("этих", 12.6, 13.0),
        ])
        intervals = find_extra_word_intervals(tr, "тебе что именно ты знаешь этих")
        self.assertEqual(len(intervals), 1)
        s, e = intervals[0]
        # При одинаковых повторах вырезается один из двух (звучит идентично);
        # при отличающихся заходах дифф режет именно ошибочный (проверено на данных).
        first_ok = abs(s - 10.95) < 0.02 and abs(e - 11.65) < 0.02
        second_ok = abs(s - 11.95) < 0.02 and abs(e - 12.65) < 0.02
        self.assertTrue(first_ok or second_ok,
                        f"должен вырезаться ровно один из повторов, получили ({s:.2f}, {e:.2f})")

    def test_hyphen_merge_is_not_a_deletion(self):
        from video_maker.engines.speech_rate import find_extra_word_intervals

        tr = self._transcript([
            ("миллионы", 0.0, 0.6), ("людей", 0.6, 1.0), ("потеряли", 1.0, 1.6),
            ("кое", 1.6, 1.8), ("-что", 1.8, 2.1), ("важное", 2.1, 2.6),
        ])
        self.assertEqual(
            find_extra_word_intervals(tr, "миллионы людей потеряли кое-что важное"), []
        )

    def test_replacement_is_not_a_deletion(self):
        from video_maker.engines.speech_rate import find_extra_word_intervals

        tr = self._transcript([
            ("но", 0.0, 0.2), ("есть", 0.2, 0.5), ("вопрос", 0.5, 0.9),
            ("постражнее", 0.9, 1.6), ("если", 1.6, 1.9),
        ])
        # Gemini исправил распознавание (постражнее → пострашнее) — НЕ режем
        self.assertEqual(
            find_extra_word_intervals(tr, "но есть вопрос пострашнее если"), []
        )

    def test_long_deleted_run_is_skipped(self):
        from video_maker.engines.speech_rate import find_extra_word_intervals

        pairs = [(f"слово{i}", float(i), i + 0.4) for i in range(14)]
        tr = self._transcript(pairs)
        # Gemini «потерял» кусок из 10 слов — защита не даёт резать живое
        corrected = "слово0 слово1 слово2 слово3 слово13"
        self.assertEqual(find_extra_word_intervals(tr, corrected), [])

    def test_cut_intervals_removes_audio(self):
        import subprocess
        import tempfile
        from pathlib import Path

        from video_maker.engines.audio import probe_duration
        from video_maker.engines.speech_rate import cut_intervals

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg недоступен")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.wav"
            out = Path(tmp) / "out.wav"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:a", "pcm_s16le", str(src),
            ], check=True, capture_output=True, text=True, timeout=30)
            res = cut_intervals(str(src), str(out), [(1.0, 1.5)], ffmpeg_bin=ffmpeg)
            self.assertTrue(Path(res).is_file())
            self.assertAlmostEqual(probe_duration(str(out)), 3.5, delta=0.15)

    def test_apply_extra_word_cuts_updates_ctx(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from video_maker.engines.audio import probe_duration
        from video_maker.engines.speech_rate import apply_extra_word_cuts

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg недоступен")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "narration.wav"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
                "-c:a", "pcm_s16le", str(src),
            ], check=True, capture_output=True, text=True, timeout=30)
            tr = self._transcript([
                ("раз", 0.5, 0.8), ("два", 0.8, 1.1),
                ("раз", 2.0, 2.3), ("два", 2.3, 2.6), ("три", 2.6, 3.0),
            ])
            ctx = SimpleNamespace(
                narration_path=str(src), audio_path="", transcription=tr,
                speech_metrics={}, audio_duration=6.0,
            )
            count = apply_extra_word_cuts(ctx, "раз два три", log_fn=lambda m: None)
            self.assertEqual(count, 1)
            self.assertNotEqual(ctx.narration_path, str(src))
            new_dur = probe_duration(ctx.narration_path)
            self.assertLess(new_dur, 6.0)
            self.assertGreater(new_dur, 5.0)
            self.assertEqual(ctx.speech_metrics.get("extra_words_intervals_removed"), 1)
            last = ctx.transcription["segments"][0]["words"][-1]
            self.assertLess(float(last["start"]), 2.6)  # таймкоды пересчитаны
            tokens = [
                str(w.get("word") or "").strip("-").lower()
                for seg in ctx.transcription.get("segments") or []
                for w in seg.get("words") or []
            ]
            self.assertEqual(tokens.count("раз"), 1, "вырезанный повтор не должен остаться в таймкодах")
            self.assertEqual(tokens.count("два"), 1, "вырезанный повтор не должен остаться в таймкодах")


if __name__ == "__main__":
    unittest.main()
