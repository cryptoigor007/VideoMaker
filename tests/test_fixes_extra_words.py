"""Вырезание повторов/лишних слов по тексту Gemini: обвязка GeminiStage."""
from __future__ import annotations

import pytest


def test_gemini_stage_applies_extra_word_cuts_when_enabled(monkeypatch):
    import video_maker.engines.analysis as analysis_mod
    import video_maker.engines.speech_rate as sr_mod
    from video_maker.pipeline.stages import GeminiStage, PipelineContext

    calls = {}
    monkeypatch.setattr(analysis_mod, "analyze", lambda *a, **k: {"corrected_text": "тест текст"})

    def fake_apply(ctx, corrected, log_fn=None):
        calls["corrected"] = corrected
        return 1

    monkeypatch.setattr(sr_mod, "apply_extra_word_cuts", fake_apply)

    def _ctx(enabled):
        ctx = PipelineContext(audio_path="/tmp/x.m4a", output_folder="/tmp")
        ctx.cut_extra_words = enabled
        ctx.transcription = {"segments": []}
        ctx.log = lambda *a, **k: None
        return ctx

    GeminiStage().run(_ctx(True))
    assert calls.get("corrected") == "тест текст", \
        "при включённой галочке повторы должны вырезаться по corrected_text"

    calls.clear()
    GeminiStage().run(_ctx(False))
    assert not calls, "при выключенной галочке ничего не вырезается"


def test_shift_analysis_times_moves_clips_hooks_cta_and_strong_words():
    """После вырезки тайминги Gemini (клипы/хуки/CTA/сильные слова) сдвигаются."""
    from video_maker.engines.speech_rate import shift_analysis_times

    analysis = {
        "clips_for_shorts": [
            {"start": 25.0, "end": 40.0, "hook_start": 25.0, "hook_end": 27.5,
             "cta_start": 36.0, "cta_end": 40.0},
        ],
        "hooks_wide": {"text": "х", "start": 0.0, "end": 3.0},
        "cta_vertical": {"text": "ц", "start": 100.0, "end": 105.0},
        "strong_words": [{"word": "слово", "timing": 30.0, "start": 30.0, "end": 31.0}],
    }
    shift_analysis_times(analysis, [(10.0, 11.0), (20.0, 21.0)])

    clip = analysis["clips_for_shorts"][0]
    assert abs(clip["start"] - 23.0) < 1e-9
    assert abs(clip["end"] - 38.0) < 1e-9
    assert abs(clip["hook_start"] - 23.0) < 1e-9
    assert abs(clip["hook_end"] - 25.5) < 1e-9
    assert abs(clip["cta_start"] - 34.0) < 1e-9
    assert abs(clip["cta_end"] - 38.0) < 1e-9
    # Время до первой вырезки не меняется.
    assert analysis["hooks_wide"]["start"] == 0.0
    assert analysis["hooks_wide"]["end"] == 3.0
    assert abs(analysis["cta_vertical"]["start"] - 98.0) < 1e-9
    assert abs(analysis["cta_vertical"]["end"] - 103.0) < 1e-9
    sw = analysis["strong_words"][0]
    assert abs(sw["start"] - 28.0) < 1e-9
    assert abs(sw["end"] - 29.0) < 1e-9
    assert abs(sw["timing"] - 28.0) < 1e-9


def test_shift_analysis_times_handles_intro_outro_and_middle():
    from video_maker.engines.speech_rate import shift_analysis_times

    analysis = {
        "intro": {"start": 5.0, "end": 8.0},
        "outro": {"start": 30.0, "end": 33.0},
        "middle": [{"start": 12.0, "end": 14.0}],
    }
    shift_analysis_times(analysis, [(6.0, 7.0)])
    assert abs(analysis["intro"]["start"] - 5.0) < 1e-9
    assert abs(analysis["intro"]["end"] - 7.0) < 1e-9
    assert abs(analysis["outro"]["start"] - 29.0) < 1e-9
    assert abs(analysis["middle"][0]["start"] - 11.0) < 1e-9


def test_apply_extra_word_cuts_shifts_analysis_times(monkeypatch, tmp_path):
    """Регрессия: вырезанный повтор сдвигает видео — тайминги анализа обязаны следовать."""
    from pathlib import Path
    from types import SimpleNamespace

    import video_maker.engines.speech_rate as sr

    src = tmp_path / "n.wav"
    src.write_bytes(b"x" * 2048)
    words = [
        {"word": "раз", "start": 0.0, "end": 0.4},
        {"word": "два", "start": 0.5, "end": 0.9},
        {"word": "два", "start": 1.0, "end": 1.4},
        {"word": "три", "start": 1.5, "end": 1.9},
        {"word": "четыре", "start": 2.0, "end": 2.4},
        {"word": "пять", "start": 2.5, "end": 2.9},
    ]
    transcription = {"segments": [{"text": "раз два два три четыре пять", "words": words}]}
    durations = iter([3.0, 2.5])
    monkeypatch.setattr(sr, "_probe_duration", lambda *a, **k: next(durations))

    def fake_cut(source, out_path, intervals, **k):
        Path(out_path).write_bytes(b"y" * 4096)
        return out_path

    monkeypatch.setattr(sr, "cut_intervals", fake_cut)

    ctx = SimpleNamespace(
        narration_path=str(src),
        transcription=transcription,
        analysis={"clips_for_shorts": [
            {"start": 3.0, "end": 9.0, "hook_start": 3.0, "hook_end": 5.0,
             "cta_start": 8.0, "cta_end": 9.0},
        ]},
        speech_metrics={},
        audio_duration=3.0,
    )
    removed = sr.apply_extra_word_cuts(ctx, "раз два три четыре пять")
    assert removed == 1
    clip = ctx.analysis["clips_for_shorts"][0]
    # Вырезан повтор «два» ≈ 0.95–1.45с → весь дальнейший таймлайн сдвинут на −0.5с.
    assert abs(clip["start"] - 2.5) < 1e-9
    assert abs(clip["end"] - 8.5) < 1e-9
    assert abs(clip["hook_end"] - 4.5) < 1e-9
    assert abs(clip["cta_start"] - 7.5) < 1e-9
