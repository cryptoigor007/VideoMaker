from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_maker.pipeline.finalize import (
    _cleanup_series_staging,
    _normalize_platform_ids,
    _prepare_platform_dirs,
    _safe_name,
    resolve_series_dir,
)
from video_maker.engines import analysis, video


def test_safe_series_names_cannot_escape_output_root(tmp_path):
    base = tmp_path / "out"
    base.mkdir()
    traversal_inputs = ("..", ".", "../..", "foo/../../..", r"..\\..")
    for attack in traversal_inputs:
        series_dir, base_name = resolve_series_dir(str(base), attack)
        assert base_name == "output"
        assert Path(series_dir).parent == base
        assert Path(series_dir).name == "output"
    # A path-looking user label with a legitimate final component is reduced to
    # that component, still inside the chosen output root.
    outside_looking, safe_name = resolve_series_dir(str(base), "../../outside")
    assert safe_name == "outside"
    assert Path(outside_looking).parent == base
    ordinary, clean = resolve_series_dir(str(base), "Documentary #03")
    assert clean == "Documentary #03"
    assert Path(ordinary).parent == base


def test_series_name_keeps_dot_suffix_but_audio_fallback_strips_extension(tmp_path):
    base = tmp_path / "out"
    base.mkdir()
    series_dir, name = resolve_series_dir(str(base), "Episode.v2")
    assert name == "Episode.v2"
    assert Path(series_dir).name == "Episode.v2"
    from_audio, audio_name = resolve_series_dir(str(base), "", "/media/Episode.final.wav")
    assert audio_name == "Episode.final"
    assert Path(from_audio).name == "Episode.final"


def test_safe_name_replaces_control_characters_and_windows_separators():
    assert "\n" not in _safe_name("episode\nname")
    assert "/" not in _safe_name(r"folder\\episode")
    assert _safe_name("..") == "output"


def test_cleanup_only_removes_reserved_series_staging(tmp_path):
    series = tmp_path / "series"
    staging = series / "_tmp" / "shorts_staging"
    staging.mkdir(parents=True)
    (staging / "short.mp4").write_bytes(b"staged")
    # These are potentially valuable user or shared files and must survive cleanup.
    legacy_shorts = series / "shorts"
    legacy_shorts.mkdir()
    (legacy_shorts / "user_asset.mp4").write_bytes(b"user")
    (series / "_tmp" / "master_16x9.mp4").write_bytes(b"master")
    shared_tmp = tmp_path / "out" / "_tmp"
    shared_tmp.mkdir(parents=True)
    (shared_tmp / "another_job.bin").write_bytes(b"concurrent")

    _cleanup_series_staging(str(series), False)

    assert not staging.exists()
    assert (legacy_shorts / "user_asset.mp4").read_bytes() == b"user"
    assert (series / "_tmp" / "master_16x9.mp4").read_bytes() == b"master"
    assert (shared_tmp / "another_job.bin").read_bytes() == b"concurrent"


def test_cleanup_respects_keep_temp_files(tmp_path):
    series = tmp_path / "series"
    staging = series / "_tmp" / "shorts_staging"
    staging.mkdir(parents=True)
    sentinel = staging / "diagnostic.txt"
    sentinel.write_text("keep", encoding="utf-8")
    _cleanup_series_staging(str(series), True)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_analysis_cache_key_includes_segments_after_first_fifty(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"dummy input")
    segments = [{"text": f"segment {n}"} for n in range(55)]
    t1 = {"segments": segments}
    t2 = {"segments": [*segments[:54], {"text": "different late segment"}]}
    k1 = analysis._analysis_cache_key(str(audio), "gemini-test", False, "episode", t1)
    k2 = analysis._analysis_cache_key(str(audio), "gemini-test", False, "episode", t2)
    assert k1 != k2


def test_gemini_error_redactor_handles_raw_and_encoded_keys():
    from urllib.parse import quote, quote_plus
    secret = "AbC+/=secret"
    text = f"url?key={secret} path={quote(secret, safe='')} plus={quote_plus(secret, safe='')}"
    redacted = analysis._redact_secret(text, secret)
    assert secret not in redacted
    assert quote(secret, safe="") not in redacted
    assert quote_plus(secret, safe="") not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_video_meta_cache_write_is_atomic_and_cleans_temp(tmp_path, monkeypatch):
    cache_path = tmp_path / "probe.json"
    cache_path.write_text('{"old": {"width": 1}}', encoding="utf-8")
    monkeypatch.setattr(video, "_VIDEO_META_FILE", str(cache_path))
    monkeypatch.setattr(video, "_VIDEO_META_CACHE", {"new": {"width": 1920, "height": 1080}})
    monkeypatch.setattr(video, "_VIDEO_META_DIRTY", True)
    real_replace = os.replace

    def fail_replace(src, dst):
        if str(dst) == str(cache_path):
            raise OSError("simulated crash before atomic replace")
        return real_replace(src, dst)

    monkeypatch.setattr(video.os, "replace", fail_replace)
    video.save_video_meta_cache()
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"old": {"width": 1}}
    assert list(tmp_path.glob("*.tmp")) == []
    assert video._VIDEO_META_DIRTY is True


def test_background_cache_only_publishes_valid_png_and_cleans_partial_on_timeout(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "bg.jpg"
    source.write_bytes(b"source")
    monkeypatch.setattr(video, "_ffmpeg_bin", lambda: "ffmpeg-test")
    real_run = video.subprocess.run
    calls = {"count": 0}

    def successful_run(cmd, **kwargs):
        calls["count"] += 1
        out = Path(cmd[-1])
        Image.new("RGB", (16, 8), (20, 30, 40)).save(out, format="PNG")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video.subprocess, "run", successful_run)
    cache = video._cached_bg_half(str(source), 16, 8)
    assert Path(cache).is_file()
    with Image.open(cache) as image:
        assert image.size == (16, 8)
    # Valid entry is reused without rerunning ffmpeg.
    assert video._cached_bg_half(str(source), 16, 8) == cache
    assert calls["count"] == 1

    # Corrupt a cache hit; the next call must regenerate, not reuse it.
    Path(cache).write_bytes(b"truncated")
    assert video._cached_bg_half(str(source), 16, 8) == cache
    assert calls["count"] == 2

    def timeout_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, 120)

    # Changing source stats creates a new key and forces another encode attempt.
    source.write_bytes(b"source changed")
    monkeypatch.setattr(video.subprocess, "run", timeout_run)
    fallback = video._cached_bg_half(str(source), 16, 8)
    assert fallback == str(source)
    cache_dir = tmp_path / "video_maker" / "cache" / "bg"
    assert not list(cache_dir.glob("*.tmp.png"))
    assert not list(cache_dir.glob(".*.tmp.png"))


def test_cleanup_refuses_symlinked_tmp_parent(tmp_path):
    series = tmp_path / "series"
    series.mkdir()
    outside = tmp_path / "outside"
    (outside / "shorts_staging").mkdir(parents=True)
    sentinel = outside / "shorts_staging" / "valuable.bin"
    sentinel.write_bytes(b"do not delete")
    try:
        (series / "_tmp").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")
    _cleanup_series_staging(str(series), False)
    assert sentinel.read_bytes() == b"do not delete"


def test_platform_ids_are_sanitized_and_duplicate_ids_skipped():
    logs = []
    normalized = _normalize_platform_ids(
        [
            {"id": "youtube", "label": "YouTube"},
            {"id": "../youtube", "label": "duplicate after sanitizing"},
            {"id": "../../evil", "label": "Evil"},
            {"id": "tiktok", "label": "TikTok"},
        ],
        logs.append,
    )
    assert [item["id"] for item in normalized] == ["youtube", "evil", "tiktok"]
    assert any("duplicate/sanitized platform id skipped" in entry for entry in logs)
    assert any("platform id sanitized" in entry for entry in logs)


def test_output_directories_refuse_symlinked_platform_folder(tmp_path):
    series = tmp_path / "series"
    series.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (series / "youtube").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this environment: {exc}")
    with pytest.raises(RuntimeError, match="symlink"):
        _prepare_platform_dirs(str(series), "youtube")
    assert list(outside.iterdir()) == []
