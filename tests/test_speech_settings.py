"""Regression tests for optional pause compression and lower-bound narration pace."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_maker.api.control import apply_settings
from video_maker.config.settings import Settings
from video_maker.pipeline.stages import PipelineContext, TranscribeStage


class FakeVar:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_pause_reduction_setting_defaults_off_and_api_sets_gui_variable():
    settings = Settings()
    assert settings.remove_long_pauses is False

    app = SimpleNamespace(settings=settings, remove_long_pauses_var=FakeVar(False))
    applied, unknown = apply_settings(app, {"remove_long_pauses": True})
    assert applied == ["remove_long_pauses"]
    assert unknown == []
    assert settings.remove_long_pauses is True
    assert app.remove_long_pauses_var.get() is True

    apply_settings(app, {"remove_long_pauses": False})
    assert settings.remove_long_pauses is False
    assert app.remove_long_pauses_var.get() is False


def test_transcribe_stage_uses_120_wpm_and_passes_pause_checkbox():
    transcription = {
        "text": "Привет, мир!",
        "segments": [{
            "start": 0.0,
            "end": 1.0,
            "text": "Привет, мир!",
            "words": [
                {"word": "Привет,", "start": 0.0, "end": 0.4},
                {"word": "мир!", "start": 0.6, "end": 1.0},
            ],
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        audio_path = str(Path(td) / "voice.wav")
        output_path = str(Path(td) / "prepared.wav")
        ctx = PipelineContext(
            audio_path=audio_path,
            output_folder=td,
            remove_long_pauses=True,
            align_speech_rate=True,
            voice_enhance=False,
            whisper_model="large-v3",
            whisper_language="ru",
            whisper_device="cpu",
            whisper_compute_type="int8",
        )
        metrics = {"final_wpm": 120, "word_count": 2, "status": "already_in_band",
                   "pause_reduction_enabled": True}
        with patch("video_maker.engines.transcription.transcribe", return_value=transcription), \
             patch("video_maker.engines.speech_rate.prepared_narration_path", return_value=output_path), \
             patch("video_maker.engines.speech_rate.prepare_narration",
                   return_value=(output_path, transcription, metrics)) as prepare, \
             patch("video_maker.engines.audio.probe_duration", return_value=1.0):
            TranscribeStage().run(ctx)

        kwargs = prepare.call_args.kwargs
        assert kwargs["min_wpm"] == 120
        assert kwargs["target_wpm"] == 120
        assert kwargs["max_wpm"] == 150
        assert kwargs["remove_long_pauses"] is True
        assert ctx.narration_path == output_path
        assert ctx.speech_metrics["pause_reduction_enabled"] is True
