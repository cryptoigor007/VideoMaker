from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_final_branches_are_intentionally_sequential():
    source = (ROOT / "video_maker/pipeline/parallel_finals.py").read_text(encoding="utf-8")
    assert "FinalHorizontal().run(ctx)" in source
    assert "FinalVertical().run(ctx)" in source
    assert source.index("FinalHorizontal().run(ctx)") < source.index("FinalVertical().run(ctx)")


def test_bgm_finalization_copies_video_stream_instead_of_encoding_video():
    source = (ROOT / "video_maker/engines/audio.py").read_text(encoding="utf-8")
    function = source[source.index("def mix_bgm("):source.index("def apply_loudnorm(")]
    assert '"-c:v", "copy"' in function
    assert '"-c:a", "aac"' in function


def test_short_trim_uses_stream_copy_before_fallback_reencode():
    source = (ROOT / "video_maker/engines/video.py").read_text(encoding="utf-8")
    function = source[source.index("def cut_segment("):source.index("def add_intro_outro_mid(")]
    assert '"-c", "copy"' in function
    assert "if not force_reencode" in function
    assert "run_vt_encode" in function
