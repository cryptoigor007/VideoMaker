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
