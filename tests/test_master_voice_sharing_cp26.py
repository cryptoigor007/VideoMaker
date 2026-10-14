"""CP26: guard the one-shared-voice-track contract across H/V masters."""
from pathlib import Path
from unittest.mock import patch

from video_maker.pipeline.branches import FinalHorizontal, FinalVertical
from video_maker.pipeline.master import MasterBuilder
from video_maker.pipeline.stages import PipelineContext


def test_master_builder_feeds_same_pre_enhanced_wav_to_horizontal_and_native_vertical(tmp_path):
    enhanced = tmp_path / "_tmp" / "narration_enhanced_123.wav"
    enhanced.parent.mkdir(parents=True, exist_ok=True)
    enhanced.write_bytes(b"mock wav")
    fit_calls = []

    def collect(folder):
        return [str(Path(folder) / "clip.mp4")]

    def probe(path):
        return (720, 1280, 30.0) if "/vertical/" in path else (1920, 1080, 30.0)

    def fake_fit(**kwargs):
        fit_calls.append(kwargs)
        Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["output_path"]).write_bytes(b"mock mp4")
        return kwargs["output_path"]

    ctx = PipelineContext(
        output_folder=str(tmp_path), audio_path=str(tmp_path / "original.m4a"),
        narration_path=str(enhanced), audio_duration=8.0,
        broll_horizontal=str(tmp_path / "horizontal"),
        broll_vertical=str(tmp_path / "vertical"),
        voice_enhance=True, voice_enhance_applied=True,
    )
    with patch("video_maker.engines.video.collect_video_files", side_effect=collect), \
         patch("video_maker.engines.video._ffprobe_video_info", side_effect=probe), \
         patch("video_maker.engines.video.fit_video_to_duration", side_effect=fake_fit):
        MasterBuilder().run(ctx)

    assert len(fit_calls) == 2
    assert [call["audio_file"] for call in fit_calls] == [str(enhanced), str(enhanced)]
    assert ctx.master_horizontal.endswith("master_16x9.mp4")
    assert ctx.master_vertical.endswith("master_9x16.mp4")


def test_horizontal_final_does_not_reenhance_shared_audio(tmp_path):
    temp = tmp_path / "_tmp"
    temp.mkdir()
    master = temp / "master_16x9.mp4"
    master.write_bytes(b"master h")
    ctx = PipelineContext(
        output_folder=str(tmp_path), master_horizontal=str(master),
        voice_enhance=True, voice_enhance_applied=True, add_bgm=False,
        h_enable_intro=False, h_enable_middle=False, h_enable_outro=False,
        h_enable_hooks=False, h_enable_subtitles=False, h_enable_strong_words=False,
    )
    with patch.object(FinalHorizontal, "_apply_audio_post", wraps=FinalHorizontal()._apply_audio_post) as post:
        FinalHorizontal().run(ctx)
    post.assert_not_called()
    assert Path(ctx.final_horizontal).is_file()


def test_vertical_final_does_not_reenhance_shared_audio(tmp_path):
    temp = tmp_path / "_tmp"
    temp.mkdir()
    master = temp / "master_9x16.mp4"
    master.write_bytes(b"master v")
    ctx = PipelineContext(
        output_folder=str(tmp_path), master_vertical=str(master),
        voice_enhance=True, voice_enhance_applied=True, add_bgm=False,
        v_enable_intro=False, v_enable_middle=False, v_enable_outro=False,
        v_enable_hooks=False, v_enable_subtitles=False, v_enable_strong_words=False,
    )
    with patch.object(FinalVertical, "_apply_audio_post", wraps=FinalVertical()._apply_audio_post) as post:
        FinalVertical().run(ctx)
    post.assert_not_called()
    assert ctx.final_vertical == str(master)


def test_horizontal_final_does_not_call_obsolete_audio_post_for_deferred_bgm(tmp_path):
    temp = tmp_path / "_tmp"
    temp.mkdir()
    master = temp / "master_16x9.mp4"
    master.write_bytes(b"master h")
    ctx = PipelineContext(
        output_folder=str(tmp_path), master_horizontal=str(master),
        voice_enhance=False, voice_enhance_applied=False,
        add_bgm=True, bgm_folder=str(tmp_path / "legacy-bgm-folder"),
        h_enable_intro=False, h_enable_middle=False, h_enable_outro=False,
        h_enable_hooks=False, h_enable_subtitles=False, h_enable_strong_words=False,
    )
    with patch.object(FinalHorizontal, "_apply_audio_post", wraps=FinalHorizontal()._apply_audio_post) as post:
        FinalHorizontal().run(ctx)
    post.assert_not_called()
    assert Path(ctx.final_horizontal).is_file()
