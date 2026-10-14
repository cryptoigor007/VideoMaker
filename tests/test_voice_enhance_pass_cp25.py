from pathlib import Path
from unittest.mock import patch

from video_maker.pipeline.branches import FinalHorizontal, FinalVertical
from video_maker.pipeline.stages import PipelineContext, TranscribeStage


def _transcription():
    return {
        "text": "Это проверочная фраза.",
        "segments": [{
            "start": 0.0,
            "end": 2.0,
            "text": "Это проверочная фраза.",
            "words": [
                {"word": "Это", "start": 0.1, "end": 0.4},
                {"word": "проверочная", "start": 0.5, "end": 1.2},
                {"word": "фраза.", "start": 1.3, "end": 1.9},
            ],
        }],
    }


def test_transcribe_stage_enhances_shared_narration_once_to_lossless_wav(tmp_path):
    audio_path = tmp_path / "voice.m4a"
    audio_path.write_bytes(b"fake input - intercepted by mocks")
    prepared_path = tmp_path / "_tmp" / "narration_prepared_abcd.wav"
    enhanced_path = tmp_path / "_tmp" / "narration_enhanced_abcd.wav"
    transcription = _transcription()
    metrics = {"final_wpm": 120.0, "word_count": 3, "status": "already_in_band"}

    def fake_enhance(src, dst, log_fn=None):
        assert src == str(prepared_path)
        assert dst == str(enhanced_path)
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"RIFF" + b"x" * 256)
        return str(dst)

    ctx = PipelineContext(
        audio_path=str(audio_path), output_folder=str(tmp_path), voice_enhance=True,
        align_speech_rate=True,
        whisper_model="large-v3", whisper_language="ru", whisper_device="cpu",
        whisper_compute_type="int8",
    )
    with patch("video_maker.engines.transcription.transcribe", return_value=transcription), \
         patch("video_maker.engines.speech_rate.prepared_narration_path", return_value=str(prepared_path)), \
         patch("video_maker.engines.speech_rate.prepare_narration", return_value=(str(prepared_path), transcription, metrics)), \
         patch("video_maker.engines.audio.probe_duration", return_value=2.0), \
         patch("video_maker.engines.audio.voice_enhance_filter", side_effect=fake_enhance) as enhance:
        TranscribeStage().run(ctx)

    enhance.assert_called_once()
    assert ctx.narration_path == str(enhanced_path)
    assert ctx.voice_enhance_applied is True
    assert ctx.audio_duration == 2.0
    assert ctx.speech_metrics["voice_enhance_applied"] is True
    assert ctx.speech_metrics["voice_enhance_intermediate"] == "lossless_pcm_s16le_wav"


def test_transcribe_stage_does_not_enhance_when_setting_is_off(tmp_path):
    audio_path = tmp_path / "voice.m4a"
    audio_path.write_bytes(b"fake input - intercepted by mocks")
    prepared_path = tmp_path / "prepared.wav"
    transcription = _transcription()
    metrics = {"final_wpm": 120.0, "word_count": 3, "status": "already_in_band"}
    ctx = PipelineContext(audio_path=str(audio_path), output_folder=str(tmp_path), voice_enhance=False,
                          align_speech_rate=True)

    with patch("video_maker.engines.transcription.transcribe", return_value=transcription), \
         patch("video_maker.engines.speech_rate.prepared_narration_path", return_value=str(prepared_path)), \
         patch("video_maker.engines.speech_rate.prepare_narration", return_value=(str(prepared_path), transcription, metrics)), \
         patch("video_maker.engines.audio.probe_duration", return_value=2.0), \
         patch("video_maker.engines.audio.voice_enhance_filter") as enhance:
        TranscribeStage().run(ctx)

    enhance.assert_not_called()
    assert ctx.narration_path == str(prepared_path)
    assert ctx.voice_enhance_applied is False
    assert ctx.speech_metrics["voice_enhance_applied"] is False


def test_wav_voice_enhance_uses_lossless_pcm_codec(tmp_path):
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"source")
    output_path = tmp_path / "enhanced.wav"
    with patch("video_maker.engines.audio.probe_duration", return_value=3.0), \
         patch("video_maker.engines.audio.run_ffmpeg_atomic", return_value=str(output_path)) as runner:
        from video_maker.engines.audio import voice_enhance_filter
        voice_enhance_filter(str(audio_path), str(output_path))

    command = runner.call_args.args[0]
    assert "pcm_s16le" in command
    assert "aac" not in command
    assert command[command.index("-ar") + 1] == "48000"


def test_wav_voice_enhance_preserves_legacy_aac_for_aac_target(tmp_path):
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"source")
    output_path = tmp_path / "enhanced.aac"
    with patch("video_maker.engines.audio.probe_duration", return_value=3.0), \
         patch("video_maker.engines.audio.run_ffmpeg_atomic", return_value=str(output_path)) as runner:
        from video_maker.engines.audio import voice_enhance_filter
        voice_enhance_filter(str(audio_path), str(output_path))

    command = runner.call_args.args[0]
    assert "aac" in command
    assert "pcm_s16le" not in command


def test_horizontal_audio_post_skips_second_enhancement_when_shared_track_is_ready(tmp_path):
    ctx = PipelineContext(output_folder=str(tmp_path), voice_enhance=True, voice_enhance_applied=True)
    with patch.object(FinalHorizontal, "_extract_enhance_replace_audio") as enhance:
        result = FinalHorizontal()._apply_audio_post("final-h.mp4", ctx, "out.mp4")
    enhance.assert_not_called()
    assert result == "final-h.mp4"


def test_vertical_audio_post_skips_second_enhancement_when_shared_track_is_ready(tmp_path):
    ctx = PipelineContext(output_folder=str(tmp_path), voice_enhance=True, voice_enhance_applied=True)
    with patch.object(FinalVertical, "_extract_enhance_replace_audio") as enhance:
        result = FinalVertical()._apply_audio_post("final-v.mp4", ctx, "out.mp4")
    enhance.assert_not_called()
    assert result == "final-v.mp4"


def test_checkpoint_persists_voice_enhance_stage_state(tmp_path):
    from video_maker.pipeline.checkpoint import load_checkpoint, save_checkpoint

    ctx = PipelineContext(
        audio_path="source.m4a", output_folder=str(tmp_path),
        narration_path=str(tmp_path / "narration_enhanced.wav"),
        voice_enhance=True, voice_enhance_applied=True,
        transcription=_transcription(), speech_metrics={"voice_enhance_applied": True},
    )
    checkpoint_path = save_checkpoint(ctx, "TranscribeStage")
    data = load_checkpoint(str(tmp_path))
    assert checkpoint_path
    assert data["voice_enhance_requested"] is True
    assert data["voice_enhance_applied"] is True


def test_checkpoint_invalidates_transcription_and_downstream_when_setting_changed(tmp_path):
    from video_maker.pipeline.checkpoint import apply_checkpoint_to_ctx

    ctx = PipelineContext(
        audio_path="source.m4a", output_folder=str(tmp_path),
        narration_path="/stale/narration_enhanced.wav",
        voice_enhance=False, voice_enhance_applied=True,
        speech_metrics={"voice_enhance_applied": True},
    )
    data = {
        "completed_stages": ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder"],
        "audio_path": "source.m4a",
        "narration_path": "/stale/narration_enhanced.wav",
        "voice_enhance_requested": True,
        "voice_enhance_applied": True,
        "speech_metrics": {"voice_enhance_applied": True},
        "progress": 45.0,
    }
    apply_checkpoint_to_ctx(ctx, data, log_fn=lambda _line: None)
    assert ctx._completed_stages == ["AudioStage"]
    assert ctx.narration_path == ""
    assert ctx.voice_enhance_applied is False
    assert ctx.speech_metrics == {}
