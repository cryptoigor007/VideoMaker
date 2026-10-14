"""r57-api.25: отдельные настройки шортов — скорость (и вырезка) независимо от длинного.

Расчёт скорости шортов: во сколько раз ускорить/замедлить готовый шорт,
чтобы он шёл с целевой скоростью шортов (wpm/коэффициент) независимо от длинного.
"""
from __future__ import annotations

import pytest


def test_normalize_short_speed_mode_recognizes_variants():
    from video_maker.engines.speech_rate import normalize_short_speed_mode

    assert normalize_short_speed_mode("Как на длинном") == "inherit"
    assert normalize_short_speed_mode("inherit") == "inherit"
    assert normalize_short_speed_mode("По словам/мин") == "wpm"
    assert normalize_short_speed_mode("wpm") == "wpm"
    assert normalize_short_speed_mode("По коэффициенту") == "coefficient"
    assert normalize_short_speed_mode("coefficient") == "coefficient"
    # Неизвестное значение = как на длинном (безопасный no-op).
    assert normalize_short_speed_mode("") == "inherit"
    assert normalize_short_speed_mode(None) == "inherit"
    assert normalize_short_speed_mode("что-то") == "inherit"


def test_short_speed_ratio_inherit_is_noop():
    from video_maker.engines.speech_rate import short_speed_ratio

    assert short_speed_ratio("inherit", 150, 1.2, metrics={"final_wpm": 120.0}) == 1.0


def test_short_speed_ratio_wpm_uses_final_wpm_of_long():
    from video_maker.engines.speech_rate import short_speed_ratio

    ratio = short_speed_ratio("wpm", 150, 1.0, metrics={"final_wpm": 120.0})
    assert abs(ratio - 1.25) < 1e-9


def test_short_speed_ratio_wpm_falls_back_to_measured_when_align_off():
    from video_maker.engines.speech_rate import short_speed_ratio

    ratio = short_speed_ratio(
        "wpm", 150, 1.0, metrics={"status": "disabled_by_setting"}, measured_wpm=111.0
    )
    assert abs(ratio - 150.0 / 111.0) < 1e-6


def test_short_speed_ratio_wpm_without_any_measure_is_noop():
    from video_maker.engines.speech_rate import short_speed_ratio

    assert short_speed_ratio("wpm", 150, 1.0, metrics=None) == 1.0
    assert short_speed_ratio("wpm", 150, 1.0, metrics={"status": "disabled_by_setting"}) == 1.0


def test_short_speed_ratio_coefficient_is_relative_to_long_tempo():
    from video_maker.engines.speech_rate import short_speed_ratio

    # Длинный шёл как есть (tempo 1.0) → шорт ×1.25.
    assert abs(short_speed_ratio("coefficient", 120, 1.25, metrics={"tempo_factor": 1.0}) - 1.25) < 1e-9
    # Длинный уже ускорен ×1.1, шорт хочет ×1.1 → ничего не меняем.
    assert short_speed_ratio("coefficient", 120, 1.1, metrics={"tempo_factor": 1.1}) == 1.0
    # Длинный ×1.1, шорт хочет ×1.32 → догоняем до ×1.2.
    assert abs(short_speed_ratio("coefficient", 120, 1.32, metrics={"tempo_factor": 1.1}) - 1.2) < 1e-9


def test_short_speed_ratio_clamps_extremes():
    from video_maker.engines.speech_rate import short_speed_ratio

    assert short_speed_ratio("wpm", 300, 1.0, metrics={"final_wpm": 60.0}) == 2.0
    assert short_speed_ratio("wpm", 60, 1.0, metrics={"final_wpm": 200.0}) == 0.5


def test_short_speed_ratio_ignores_tiny_differences():
    from video_maker.engines.speech_rate import short_speed_ratio

    # 121/120 = +0.8% — меньше порога 1% → no-op.
    assert short_speed_ratio("wpm", 121, 1.0, metrics={"final_wpm": 120.0}) == 1.0
    # 122/120 = +1.7% — заметно, применяем.
    assert abs(short_speed_ratio("wpm", 122, 1.0, metrics={"final_wpm": 120.0}) - 122.0 / 120.0) < 1e-9


def test_map_intervals_to_window_clips_and_drops():
    from video_maker.engines.speech_rate import map_intervals_to_window

    intervals = [(5.0, 6.0), (15.0, 16.0), (30.0, 31.0)]
    assert map_intervals_to_window(intervals, 10.0, 20.0) == [(5.0, 6.0)]
    assert map_intervals_to_window(intervals, 0.0, 40.0) == [(5.0, 6.0), (15.0, 16.0), (30.0, 31.0)]
    assert map_intervals_to_window(intervals, 16.0, 30.0) == []
    # Частичное пересечение края окна обрезается по границе.
    assert map_intervals_to_window([(18.0, 22.0)], 10.0, 20.0) == [(8.0, 10.0)]


def test_scale_after_removals_shifts_then_speeds():
    from video_maker.engines.speech_rate import scale_after_removals

    assert abs(scale_after_removals(12.0, [(5.0, 6.0)], 1.25) - 8.8) < 1e-9
    assert abs(scale_after_removals(4.0, [(5.0, 6.0)], 1.25) - 3.2) < 1e-9
    # Точка внутри вырезанного интервала прилипает к стыку.
    assert abs(scale_after_removals(5.5, [(5.0, 6.0)], 1.25) - 4.0) < 1e-9
    assert abs(scale_after_removals(12.0, [], 1.0) - 12.0) < 1e-9


def test_adjust_short_clip_times_remaps_into_adjusted_short():
    from video_maker.engines.speech_rate import adjust_short_clip_times

    clip = {"start": 25.0, "end": 40.0, "hook_start": 25.0, "hook_end": 27.5,
            "cta_start": 36.0, "cta_end": 40.0, "hook": "текст"}
    # Интервалы — уже локальные (от начала окна шорта): длинный (24–25) при start=20.
    adjusted = adjust_short_clip_times(clip, window_start=20.0, intervals=[(4.0, 5.0)], ratio=1.25)
    assert abs(adjusted["start"] - 3.2) < 1e-9
    assert abs(adjusted["hook_end"] - 5.2) < 1e-9
    assert abs(adjusted["cta_start"] - 12.0) < 1e-9
    assert adjusted["hook"] == "текст"
    # Исходный клип не мутируется.
    assert clip["start"] == 25.0


def _make_test_video(ffmpeg: str, path) -> None:
    import subprocess

    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=darkred:s=320x240:r=24:d=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_retime_video_segment_removes_and_speeds(tmp_path):
    """Один проход: вырезанный интервал + ускорение — длительность как ожидается."""
    import shutil

    from video_maker.engines.audio import probe_duration
    from video_maker.engines.video import _ffmpeg_bin, retime_video_segment

    ffmpeg = shutil.which("ffmpeg") or _ffmpeg_bin()
    src = tmp_path / "in.mp4"
    _make_test_video(ffmpeg, src)

    out = retime_video_segment(
        str(src), str(tmp_path / "out.mp4"),
        speed_ratio=1.25, remove_intervals=[(1.0, 2.0)],
    )
    # (6 − 1) / 1.25 = 4.0с
    assert abs(probe_duration(out) - 4.0) < 0.3

    out2 = retime_video_segment(str(src), str(tmp_path / "out2.mp4"), speed_ratio=1.5)
    # 6 / 1.5 = 4.0с — только ускорение, без вырезок.
    assert abs(probe_duration(out2) - 4.0) < 0.3


def test_retime_video_segment_noop_returns_source(tmp_path):
    from video_maker.engines.video import retime_video_segment

    src = tmp_path / "x.mp4"
    src.write_bytes(b"x" * 4096)
    assert retime_video_segment(str(src), str(tmp_path / "o.mp4")) == str(src)


def _make_app():
    import tkinter as tk

    from video_maker.gui.app import App

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = App(root)
    root.deiconify()
    root.update_idletasks()
    root.update()
    return root, app


def test_short_speech_controls_collect_into_settings():
    """Блок «Шорты»: режим/значение скорости и галка вырезки собираются в настройки."""
    from video_maker.gui.app import collect_settings_from_gui

    root, app = _make_app()
    try:
        app.s_speech_rate_mode_var.set("По словам/мин")
        app.s_speech_rate_target_wpm_var.set(150)
        collect_settings_from_gui(app)
        assert app.settings.s_speech_rate_mode == "wpm"
        assert abs(app.settings.s_speech_rate_target_wpm - 150.0) < 1e-9

        app.s_speech_rate_mode_var.set("Как на длинном")
        collect_settings_from_gui(app)
        assert app.settings.s_speech_rate_mode == "inherit"

        app.s_speech_rate_mode_var.set("По коэффициенту")
        app.s_speech_rate_coefficient_var.set(1.2)
        collect_settings_from_gui(app)
        assert app.settings.s_speech_rate_mode == "coefficient"
        assert abs(app.settings.s_speech_rate_coefficient - 1.2) < 1e-9

        app.s_cut_extra_words_var.set(True)
        collect_settings_from_gui(app)
        assert app.settings.s_cut_extra_words is True
    finally:
        root.destroy()


def test_short_speech_mode_disables_slider_on_inherit():
    """«Как на длинном» — ползунок шортов выключен; иначе диапазон по режиму."""
    root, app = _make_app()
    try:
        app.s_speech_rate_mode_var.set("Как на длинном")
        app._on_short_speech_mode_change()
        assert str(app.s_speech_rate_slider.cget("state")) == "disabled"

        app.s_speech_rate_mode_var.set("По словам/мин")
        app._on_short_speech_mode_change()
        assert str(app.s_speech_rate_slider.cget("state")) == "normal"
        assert abs(float(app.s_speech_rate_slider.cget("to")) - 200.0) < 1e-9

        app.s_speech_rate_mode_var.set("По коэффициенту")
        app._on_short_speech_mode_change()
        assert abs(float(app.s_speech_rate_slider.cget("to")) - 1.5) < 1e-9
    finally:
        root.destroy()


def test_run_pipeline_passes_short_speech_settings_into_ctx(monkeypatch, tmp_path):
    """Регрессия: поля шортов обязаны попадать в PipelineContext."""
    import copy as _copy

    import video_maker.gui.app as gui_app

    root, app = _make_app()
    try:
        audio = tmp_path / "a.m4a"
        audio.write_bytes(b"x")
        out = tmp_path / "out"
        out.mkdir()
        app._audio_queue = [str(audio)]
        app.settings.audio_path = str(audio)
        app.settings.output_folder = str(out)
        app.settings.prevent_sleep = False
        app.settings.s_speech_rate_mode = "wpm"
        app.settings.s_speech_rate_target_wpm = 150.0
        app.settings.s_cut_extra_words = True
        app._pipeline_snapshot = {
            "settings": _copy.deepcopy(app.settings),
            "packaging_opts": {},
            "caption_style": "auto_aisie",
            "hook_style": "auto_aisie",
            "imo": {},
        }

        captured = {}

        class StopCapture(Exception):
            pass

        class RecordingCtx:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                raise StopCapture()

        monkeypatch.setattr(gui_app, "PipelineContext", RecordingCtx)
        try:
            app._run_pipeline()
        except StopCapture:
            pass
        assert captured.get("s_speech_rate_mode") == "wpm"
        assert captured.get("s_speech_rate_target_wpm") == 150.0
        assert captured.get("s_cut_extra_words") is True
    finally:
        root.destroy()


def _short_ctx(**overrides):
    from types import SimpleNamespace

    words = [{"word": f"с{i}", "start": i * 0.5, "end": i * 0.5 + 0.1} for i in range(20)]
    ctx = SimpleNamespace(
        s_speech_rate_mode="inherit",
        s_speech_rate_target_wpm=120.0,
        s_speech_rate_coefficient=1.0,
        s_cut_extra_words=False,
        speech_metrics={"final_wpm": 120.0},
        transcription={"segments": [{"text": " ".join(w["word"] for w in words), "words": words}]},
        analysis={},
        audio_duration=10.0,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def test_plan_short_adjustments_inherit_is_noop():
    from video_maker.engines.speech_rate import plan_short_adjustments

    plan = plan_short_adjustments(_short_ctx(), window_start=2.0, window_end=8.0)
    assert plan == {"ratio": 1.0, "intervals": []}


def test_plan_short_adjustments_wpm_ratio():
    from video_maker.engines.speech_rate import plan_short_adjustments

    plan = plan_short_adjustments(
        _short_ctx(s_speech_rate_mode="wpm", s_speech_rate_target_wpm=150.0),
        window_start=0.0,
        window_end=10.0,
    )
    assert abs(plan["ratio"] - 1.25) < 1e-9
    assert plan["intervals"] == []


def test_plan_short_adjustments_measures_wpm_when_long_align_off():
    from video_maker.engines.speech_rate import plan_short_adjustments

    ctx = _short_ctx(
        s_speech_rate_mode="wpm",
        s_speech_rate_target_wpm=150.0,
        speech_metrics={"status": "disabled_by_setting"},
    )
    plan = plan_short_adjustments(ctx, window_start=0.0, window_end=10.0)
    # 20 слов за 10с = 120 wpm → 150/120 = 1.25
    assert abs(plan["ratio"] - 1.25) < 1e-6


def test_plan_short_adjustments_cut_intervals_within_window():
    from video_maker.engines.speech_rate import plan_short_adjustments

    words = [{"word": f"с{i}", "start": i * 0.5, "end": i * 0.5 + 0.1} for i in range(20)]
    words[8]["word"] = words[7]["word"]  # повтор
    text = " ".join(w["word"] for w in words)
    corrected = " ".join(w["word"] for i, w in enumerate(words) if i != 8)
    ctx = _short_ctx(
        s_cut_extra_words=True,
        transcription={"segments": [{"text": text, "words": words}]},
        analysis={"corrected_text": corrected},
    )
    plan = plan_short_adjustments(ctx, window_start=3.0, window_end=6.0)
    assert len(plan["intervals"]) == 1
    local_start, local_end = plan["intervals"][0]
    # Повтор около 3.5–4.2с длинного → локально 0.4–1.2с окна; длина ≈ 0.2с.
    assert 0.3 <= local_start <= 1.1
    assert abs((local_end - local_start) - 0.2) < 0.03


def test_plan_short_adjustments_skips_cut_when_long_already_cut():
    from video_maker.engines.speech_rate import plan_short_adjustments

    ctx = _short_ctx(
        s_cut_extra_words=True,
        speech_metrics={"extra_words_applied": True},
        analysis={"corrected_text": "с0 с1"},
    )
    plan = plan_short_adjustments(ctx, window_start=0.0, window_end=10.0)
    assert plan["intervals"] == []


def _make_short_wiring(monkeypatch, tmp_path, *, transcription, corrected="", overrides=None):
    from pathlib import Path
    from types import SimpleNamespace

    import video_maker.engines.audio as audio_mod
    import video_maker.engines.subtitles as subs_mod
    import video_maker.engines.video as video_mod

    src = tmp_path / "final.mp4"
    src.write_bytes(b"v" * 4096)
    calls = {}

    monkeypatch.setattr(
        audio_mod, "probe_duration",
        lambda p, **k: 15.4 if str(p).endswith("cut.mp4") else 20.0,
    )

    def fake_cut(video_path, start, duration, output_path, **k):
        Path(output_path).write_bytes(b"c" * 2048)
        return output_path

    monkeypatch.setattr(video_mod, "cut_segment", fake_cut)

    def fake_retime(video_path, output_path, **k):
        calls["retime"] = k
        Path(output_path).write_bytes(b"r" * 2048)
        return output_path

    monkeypatch.setattr(video_mod, "retime_video_segment", fake_retime)

    def fake_burn(video_path, analysis, clip=None, output_path="", **k):
        calls["burn_clip"] = dict(clip or {})
        Path(output_path).write_bytes(b"b" * 2048)
        return output_path

    monkeypatch.setattr(subs_mod, "burn_subtitles", fake_burn)

    ctx = SimpleNamespace(
        final_vertical=str(src),
        master_vertical="",
        transcription=transcription,
        analysis={"corrected_text": corrected},
        speech_metrics={"final_wpm": 120.0},
        audio_duration=15.4,
        output_folder=str(tmp_path),
        series_name="тест",
        cancel_event=None,
        log=lambda *a, **k: None,
        keep_temp_files=False,
        s_speech_rate_mode="wpm",
        s_speech_rate_target_wpm=150.0,
        s_speech_rate_coefficient=1.0,
        s_cut_extra_words=False,
        s_enable_hooks=True,
        s_enable_subtitles=False,
        s_enable_strong_words=False,
        s_enable_intro=False,
        s_enable_middle=False,
        s_enable_outro=False,
        shorts_cover_image="",
        packaging_opts=None,
        bgm_mixed=False,
    )
    for key, value in (overrides or {}).items():
        setattr(ctx, key, value)
    return ctx, calls


def test_create_short_applies_short_speed(monkeypatch, tmp_path):
    """Шорт с отдельной скоростью: проход retime + пересчёт хук/CTA."""
    from pathlib import Path

    from video_maker.pipeline.shorts import ShortsCutter

    words = [{"word": "а", "start": 0.0, "end": 0.4}, {"word": "б", "start": 14.5, "end": 14.9}]
    transcription = {"segments": [{"text": "а б", "words": words}]}
    ctx, calls = _make_short_wiring(monkeypatch, tmp_path, transcription=transcription)
    clip = {"start": 0.0, "end": 15.0, "hook": "Х", "hook_start": 0.0, "hook_end": 2.5,
            "cta": "Ц", "cta_start": 13.0, "cta_end": 15.0, "title": "т",
            "description": "о", "hashtags": "#ТочкаНаблюдения"}
    path = ShortsCutter()._create_short(ctx, clip, 1, str(tmp_path / "out"))
    assert path and Path(path).exists()
    assert abs(calls["retime"]["speed_ratio"] - 1.25) < 1e-9
    assert calls["retime"]["remove_intervals"] == []
    # Хук пересчитан в новый таймлайн: 2.5 / 1.25 = 2.0
    assert abs(calls["burn_clip"]["hook_end"] - 2.0) < 1e-9


def test_create_short_applies_short_cut_intervals(monkeypatch, tmp_path):
    """Шорт с галкой вырезки (на длинном выключено): интервалы уходят в проход."""
    from pathlib import Path

    from video_maker.pipeline.shorts import ShortsCutter

    words = [{"word": f"с{i}", "start": i * 0.5, "end": i * 0.5 + 0.1} for i in range(20)]
    words[8]["word"] = words[7]["word"]
    text = " ".join(w["word"] for w in words)
    corrected = " ".join(w["word"] for i, w in enumerate(words) if i != 8)
    ctx, calls = _make_short_wiring(
        monkeypatch, tmp_path,
        transcription={"segments": [{"text": text, "words": words}]},
        corrected=corrected,
        overrides={"s_speech_rate_mode": "inherit", "s_cut_extra_words": True},
    )
    clip = {"start": 0.0, "end": 9.9, "hook": "Х", "hook_start": 0.0, "hook_end": 2.5,
            "cta": "Ц", "cta_start": 8.0, "cta_end": 9.9, "title": "т",
            "description": "о", "hashtags": "#ТочкаНаблюдения"}
    path = ShortsCutter()._create_short(ctx, clip, 1, str(tmp_path / "out"))
    assert path and Path(path).exists()
    assert len(calls["retime"]["remove_intervals"]) == 1
    assert calls["retime"]["speed_ratio"] == 1.0


def test_analysis_prompt_shorts_length_from_15s():
    """Промпт нарезок: «от 15 секунд», без верхней границы (регрессия r57-api.25)."""
    from video_maker.engines.analysis import _build_analysis_prompt

    prompt = _build_analysis_prompt(
        "тестовый текст", [{"start": 0.0, "end": 5.0, "text": "тестовый текст"}]
    )
    assert "от 15 секунд" in prompt
    assert "без верхней границы" in prompt
    assert "15–60" not in prompt
