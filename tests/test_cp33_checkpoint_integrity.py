from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from video_maker.pipeline import checkpoint as cp
from video_maker.pipeline.stages import PipelineContext


def _ctx(tmp_path: Path, audio: Path, **kwargs) -> PipelineContext:
    narration = tmp_path / "narration.wav"
    if not narration.exists():
        narration.write_bytes(b"synthetic prepared narration")
    return PipelineContext(
        audio_path=str(audio), narration_path=str(narration), output_folder=str(tmp_path), series_name="series-a",
        transcription={"text": "hello", "segments": [{"text": "hello", "start": 0.0, "end": 1.0, "words": [{"word": "hello", "start": 0.0, "end": 1.0}]}]},
        analysis={"package_title": "title", "clips_for_shorts": []},
        **kwargs,
    )


def _audio(tmp_path: Path) -> Path:
    path = tmp_path / "source.m4a"
    path.write_bytes(b"synthetic audio bytes 123456789")
    return path


def test_transcription_sidecar_write_failure_does_not_mark_stage_or_replace_checkpoint(tmp_path):
    audio = _audio(tmp_path)
    ctx = _ctx(tmp_path, audio)
    cp.save_checkpoint(ctx, "AudioStage")
    main = Path(cp.checkpoint_path(str(tmp_path)))
    before = main.read_bytes()
    completed_before = list(ctx._completed_stages)
    real_write = cp._atomic_write

    def fail_transcription(path, data):
        if "checkpoint_transcription_" in Path(path).name:
            raise OSError("simulated disk full")
        return real_write(path, data)

    with patch.object(cp, "_atomic_write", side_effect=fail_transcription):
        with pytest.raises(cp.CheckpointPersistenceError, match="transcription checkpoint save failed"):
            cp.save_checkpoint(ctx, "TranscribeStage")

    assert main.read_bytes() == before
    assert ctx._completed_stages == completed_before
    assert not list((tmp_path / "_tmp").glob("checkpoint_transcription_*.json"))


def test_analysis_sidecar_failure_preserves_previous_checkpoint_and_cleans_new_transcript(tmp_path):
    audio = _audio(tmp_path)
    ctx = _ctx(tmp_path, audio)
    ctx._completed_stages = ["AudioStage", "TranscribeStage"]
    cp.save_checkpoint(ctx, "TranscribeStage")
    main = Path(cp.checkpoint_path(str(tmp_path)))
    before = main.read_bytes()
    real_write = cp._atomic_write

    def fail_analysis(path, data):
        if "checkpoint_analysis_" in Path(path).name:
            raise OSError("simulated disk full")
        return real_write(path, data)

    with patch.object(cp, "_atomic_write", side_effect=fail_analysis):
        with pytest.raises(cp.CheckpointPersistenceError, match="analysis checkpoint save failed"):
            cp.save_checkpoint(ctx, "GeminiStage")
    assert main.read_bytes() == before
    referenced = json.loads(main.read_text())
    assert Path(referenced["transcription_file"]).is_file()
    transcript_files = list((tmp_path / "_tmp").glob("checkpoint_transcription_*.json"))
    assert transcript_files == [Path(referenced["transcription_file"])]
    assert not list((tmp_path / "_tmp").glob("checkpoint_analysis_*.json"))


def test_main_checkpoint_write_failure_preserves_previous_checkpoint(tmp_path):
    audio = _audio(tmp_path)
    ctx = _ctx(tmp_path, audio)
    cp.save_checkpoint(ctx, "AudioStage")
    main = Path(cp.checkpoint_path(str(tmp_path)))
    before = main.read_bytes()
    completed_before = list(ctx._completed_stages)
    real_write = cp._atomic_write

    def fail_main(path, data):
        if Path(path).name == cp.CHECKPOINT_NAME:
            raise OSError("simulated checkpoint filesystem failure")
        return real_write(path, data)

    ctx._completed_stages = ["AudioStage"]
    with patch.object(cp, "_atomic_write", side_effect=fail_main):
        with pytest.raises(cp.CheckpointPersistenceError, match="main checkpoint save failed"):
            cp.save_checkpoint(ctx, "TranscribeStage")
    assert main.read_bytes() == before
    assert ctx._completed_stages == ["AudioStage"]
    # No newly written orphan sidecars should remain after a main checkpoint failure.
    assert not list((tmp_path / "_tmp").glob("checkpoint_transcription_*.json"))
    assert not list((tmp_path / "_tmp").glob("checkpoint_analysis_*.json"))


def test_source_content_change_invalidates_all_stages_even_if_size_and_mtime_are_preserved(tmp_path):
    audio = _audio(tmp_path)
    ctx1 = _ctx(tmp_path, audio)
    ctx1._completed_stages = ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder"]
    cp.save_checkpoint(ctx1, "MasterBuilder")
    old_stat = audio.stat()
    audio.write_bytes(b"different audio bytes 123456789")  # identical length
    os.utime(audio, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    ctx2 = _ctx(tmp_path, audio)
    cp.apply_checkpoint_to_ctx(ctx2, cp.load_checkpoint(str(tmp_path)), log_fn=lambda _msg: None)
    assert ctx2._completed_stages == []
    assert ctx2.transcription == {}
    assert ctx2.analysis == {}
    assert ctx2.master_horizontal == ""


def test_pause_setting_change_invalidates_transcription_and_downstream_only(tmp_path):
    audio = _audio(tmp_path)
    ctx1 = _ctx(tmp_path, audio, remove_long_pauses=False)
    ctx1._completed_stages = ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder"]
    cp.save_checkpoint(ctx1, "MasterBuilder")
    ctx2 = _ctx(tmp_path, audio, remove_long_pauses=True)
    cp.apply_checkpoint_to_ctx(ctx2, cp.load_checkpoint(str(tmp_path)), log_fn=lambda _msg: None)
    assert ctx2._completed_stages == ["AudioStage"]
    assert ctx2.transcription == {}
    assert ctx2.analysis == {}


def test_broll_inventory_change_invalidates_render_but_preserves_transcription_analysis(tmp_path):
    audio = _audio(tmp_path)
    broll = tmp_path / "broll"; broll.mkdir()
    clip = broll / "clip.mp4"; clip.write_bytes(b"clip-one")
    ctx1 = _ctx(tmp_path, audio, broll_horizontal=str(broll))
    ctx1._completed_stages = ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder"]
    cp.save_checkpoint(ctx1, "MasterBuilder")
    old_stat = clip.stat()
    clip.write_bytes(b"clip-two")  # same byte length as the previous content
    os.utime(clip, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    ctx2 = _ctx(tmp_path, audio, broll_horizontal=str(broll))
    cp.apply_checkpoint_to_ctx(ctx2, cp.load_checkpoint(str(tmp_path)), log_fn=lambda _msg: None)
    assert ctx2._completed_stages == ["AudioStage", "TranscribeStage", "GeminiStage"]
    assert ctx2.transcription.get("text") == "hello"
    assert ctx2.analysis.get("package_title") == "title"
    assert ctx2.master_horizontal == ""


def test_bgm_inventory_change_invalidates_finalization_only(tmp_path):
    audio = _audio(tmp_path)
    bgm = tmp_path / "bgm"; bgm.mkdir()
    (bgm / "track.mp3").write_bytes(b"track1")
    platforms = [{"id": "youtube", "label": "YouTube", "folder": str(bgm), "bgm_enabled": True}]
    ctx1 = _ctx(tmp_path, audio, bgm_platforms=platforms)
    ctx1._completed_stages = ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder", "ParallelFinals", "ShortsCutter"]
    cp.save_checkpoint(ctx1, "FinalizeStage")
    (bgm / "track2.mp3").write_bytes(b"track2")
    ctx2 = _ctx(tmp_path, audio, bgm_platforms=platforms)
    cp.apply_checkpoint_to_ctx(ctx2, cp.load_checkpoint(str(tmp_path)), log_fn=lambda _msg: None)
    assert ctx2._completed_stages == ["AudioStage", "TranscribeStage", "GeminiStage", "MasterBuilder", "ParallelFinals", "ShortsCutter"]
    assert ctx2.transcription.get("text") == "hello"
    assert ctx2.analysis.get("package_title") == "title"


def test_failed_sidecar_checkpoint_artifacts_use_generation_specific_names(tmp_path):
    audio = _audio(tmp_path)
    ctx = _ctx(tmp_path, audio)
    cp.save_checkpoint(ctx, "TranscribeStage")
    first = cp.load_checkpoint(str(tmp_path))["transcription_file"]
    cp.save_checkpoint(ctx, "TranscribeStage")
    second = cp.load_checkpoint(str(tmp_path))["transcription_file"]
    assert first != second
    assert Path(second).is_file()
    assert not Path(first).exists()  # Old sidecar is removed only after commit.
