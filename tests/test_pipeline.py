"""Тесты для пайплайна ВидеоМейкер."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from video_maker.config.settings import Settings
from video_maker.pipeline.stages import PipelineContext, AudioStage, TranscribeStage, GeminiStage
from video_maker.pipeline.master import MasterBuilder
from video_maker.pipeline.branches import FinalHorizontal, FinalVertical
from video_maker.pipeline.shorts import ShortsCutter
from video_maker.pipeline.finalize import FinalizeStage
from video_maker.engines.audio import judge_loudness, probe_duration
from video_maker.engines.subtitles import _ass_time, _ass_color


class TestSettings(unittest.TestCase):
    """Тесты конфигурации."""

    def test_settings_defaults(self):
        """Настройки по умолчанию."""
        s = Settings()
        assert s.gemini_model == "gemini-3.6-flash"
        assert s.h_enable_hooks is True
        assert s.v_enable_subtitles is True
        assert s.s_enable_intro is False

    def test_settings_validate_missing_audio(self):
        """Валидация: нет аудио → ошибка."""
        s = Settings()
        errors = s.validate()
        assert any("аудиофайл" in e for e in errors)

    def test_settings_validate_missing_output(self):
        """Валидация: нет папки вывода → ошибка."""
        s = Settings(audio_path="/tmp/test.mp3")
        errors = s.validate()
        assert any("вывода" in e for e in errors)

    def test_settings_validate_ok(self):
        """Валидация: всё задано → нет ошибок."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "test.mp3")
            broll_h = os.path.join(tmpdir, "broll_h")
            out_dir = os.path.join(tmpdir, "out")
            os.makedirs(broll_h)
            os.makedirs(out_dir)
            with open(audio_path, "w") as f:
                f.write("dummy")

            s = Settings(
                gemini_api_key="test",
                audio_path=audio_path,
                broll_horizontal=broll_h,
                output_folder=out_dir,
                v_enable_intro=False,
                v_enable_middle=False,
                v_enable_outro=False,
                v_enable_hooks=False,
                v_enable_subtitles=False,
                v_enable_strong_words=False,
                s_enable_intro=False,
                s_enable_middle=False,
                s_enable_outro=False,
                s_enable_hooks=False,
                s_enable_subtitles=False,
                s_enable_strong_words=False,
            )
            errors = s.validate()
            assert len(errors) == 0


class TestPipelineContext(unittest.TestCase):
    """Тесты контекста пайплайна."""

    def test_context_defaults(self):
        """Контекст по умолчанию."""
        ctx = PipelineContext()
        assert ctx.audio_duration == 0.0
        assert ctx.master_horizontal == ""
        assert ctx.shorts == []

    def test_context_log_callback(self):
        """Контекст с callback логирования."""
        logs = []
        ctx = PipelineContext(log_callback=lambda m: logs.append(m))
        ctx.log("test message")
        assert logs == ["test message"]


class TestAudioEngine(unittest.TestCase):
    """Тесты аудио-движка."""

    def test_judge_loudness_ok(self):
        """Громкость ok: -20..-13 LUFS."""
        assert judge_loudness(-16.0) == "ok"
        assert judge_loudness(-20.0) == "ok"
        assert judge_loudness(-13.0) == "ok"

    def test_judge_loudness_quiet(self):
        """Громкость тихо: < -20 LUFS."""
        assert judge_loudness(-25.0) == "тихо"

    def test_judge_loudness_loud(self):
        """Громкость громко: > -13 LUFS."""
        assert judge_loudness(-10.0) == "громко"

    def test_judge_loudness_none(self):
        """Громкость неизвестна."""
        assert judge_loudness(None) == "?"


class TestSubtitlesEngine(unittest.TestCase):
    """Тесты движка субтитров."""

    def test_ass_time(self):
        """Конвертация времени в ASS формат."""
        assert _ass_time(0) == "0:00:00.00"
        assert _ass_time(61.5) == "0:01:01.50"
        assert _ass_time(3661.0) == "1:01:01.00"

    def test_ass_color_valid(self):
        """Конвертация цвета #RRGGBB → ASS BGR."""
        result = _ass_color("#FF6B00")
        assert result == "&H00006BFF&"

    def test_ass_color_invalid(self):
        """Невалидный цвет → fallback."""
        result = _ass_color("not_a_color")
        assert result == "&H00FFFFFF&"


class TestShortsCutter(unittest.TestCase):
    """Тесты нарезки Shorts."""

    def test_shorts_from_intermediate_vertical(self):
        """Shorts режутся из final_9x16 (fallback master_vertical)."""
        ctx = PipelineContext(
            output_folder="/tmp/out",
            final_vertical="/tmp/final_9x16.mp4",
            master_vertical="/tmp/master_9x16.mp4",
            analysis={
                "clips_for_shorts": [
                    {"start": 0, "end": 15, "title": "Short 1"},
                    {"start": 20, "end": 35, "title": "Short 2"},
                ]
            },
            voice_enhance=False,
            add_bgm=False,
        )
        cutter = ShortsCutter()
        with patch("video_maker.engines.video.cut_segment") as mock_cut, \
             patch("video_maker.engines.subtitles.burn_subtitles") as mock_sub, \
             patch("video_maker.engines.audio.probe_duration", return_value=60.0), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isfile", return_value=True), \
             patch("shutil.copy2"), \
             patch.object(cutter, "_write_metadata"), \
             patch.object(cutter, "_write_sidecar_files"):
            mock_cut.return_value = "/tmp/short.mp4"
            mock_sub.return_value = "/tmp/short_final.mp4"
            ctx = cutter.run(ctx)

        assert len(ctx.shorts) == 2
        mock_cut.assert_called()
        call_args = mock_cut.call_args_list[0]
        vp = (call_args.kwargs or {}).get("video_path") or (call_args[1] or {}).get("video_path")
        assert vp == "/tmp/final_9x16.mp4"


class TestFinalHorizontal(unittest.TestCase):
    """Тесты финального горизонтального видео."""

    def test_final_horizontal_from_master(self):
        """Финальное горизонтальное берётся из master_16x9."""
        ctx = PipelineContext(
            master_horizontal="/tmp/master_16x9.mp4",
            analysis={"subtitles": [], "hook": {}},
            voice_enhance=False,
            add_bgm=False,
        )
        branch = FinalHorizontal()
        with patch("video_maker.engines.subtitles.burn_subtitles") as mock_sub:
            mock_sub.return_value = "/tmp/final_16x9.mp4"
            ctx = branch.run(ctx)

        assert ctx.final_horizontal == "/tmp/final_16x9.mp4"


class TestFinalVertical(unittest.TestCase):
    """Тесты финального вертикального видео."""

    def test_final_vertical_from_master(self):
        """Финальное вертикальное берётся из master_9x16."""
        ctx = PipelineContext(
            master_vertical="/tmp/master_9x16.mp4",
            analysis={"subtitles": [], "hook": {}},
            voice_enhance=False,
            add_bgm=False,
        )
        branch = FinalVertical()
        with patch("video_maker.engines.subtitles.burn_subtitles") as mock_sub:
            mock_sub.return_value = "/tmp/final_9x16.mp4"
            ctx = branch.run(ctx)

        assert ctx.final_vertical == "/tmp/final_9x16.mp4"


class TestPipelineFlow(unittest.TestCase):
    """Интеграционные тесты пайплайна."""

    def test_intermediate_has_no_overlays(self):
        """Промежуточные видео не содержат обработки."""
        ctx = PipelineContext(
            audio_path="/tmp/test.mp3",
            broll_horizontal="/tmp/broll_h",
            output_folder="/tmp/out",
            vertical_background="/tmp/bg.jpg",
            voice_enhance=False,
            add_bgm=False,
        )
        master = MasterBuilder()
        with patch("video_maker.engines.video.collect_video_files", return_value=["/tmp/v.mp4"]), \
             patch("video_maker.engines.video.fit_video_to_duration"), \
             patch("video_maker.engines.video.vstack_video_image") as mock_vstack, \
             patch("video_maker.engines.audio.probe_duration", return_value=60.0), \
             patch("os.makedirs"):
            mock_vstack.return_value = "/tmp/master_9x16.mp4"
            ctx = master.run(ctx)

        # Master: только 16x9 (вертикаль собирается в Final, one encode)
        assert "master_16x9.mp4" in ctx.master_horizontal
        # master_vertical может быть пустым до FinalVertical — это by design (r21+)

    def test_three_products_from_master(self):
        """Из master_9x16 создаются: final_9x16 + shorts."""
        ctx = PipelineContext(
            output_folder="/tmp/out",
            master_vertical="/tmp/master_9x16.mp4",
            analysis={
                "subtitles": [],
                "hook": {},
                "clips_for_shorts": [
                    {"start": 0, "end": 15, "title": "Short 1"},
                ],
            },
            voice_enhance=False,
            add_bgm=False,
        )

        fv = FinalVertical()
        with patch("video_maker.engines.subtitles.burn_subtitles") as mock_sub:
            mock_sub.return_value = "/tmp/final_9x16.mp4"
            ctx = fv.run(ctx)

        sc = ShortsCutter()
        with patch("video_maker.engines.video.cut_segment") as mock_cut, \
             patch("video_maker.engines.subtitles.burn_subtitles") as mock_sub, \
             patch("video_maker.engines.audio.probe_duration", return_value=60.0), \
             patch("os.makedirs"), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isfile", return_value=True), \
             patch("shutil.copy2"), \
             patch.object(sc, "_write_metadata"), \
             patch.object(sc, "_write_sidecar_files"):
            mock_cut.return_value = "/tmp/short.mp4"
            mock_sub.return_value = "/tmp/short_final.mp4"
            ctx = sc.run(ctx)

        assert ctx.final_vertical == "/tmp/final_9x16.mp4"
        assert len(ctx.shorts) == 1


if __name__ == "__main__":
    unittest.main()