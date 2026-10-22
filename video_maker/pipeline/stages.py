"""Стадии пайплайна — базовые классы и конкретные стадии."""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..paths import resolve_existing_path

log = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Контекст пайплайна — передаётся между стадиями."""

    # Входные данные
    audio_path: str = ""  # original source; retain for identity and metadata
    narration_path: str = ""  # trimmed/rate-adjusted voice used for rendering
    speech_metrics: dict = field(default_factory=dict)
    broll_horizontal: str = ""
    broll_vertical: str = ""
    bgm_folder: str = ""
    # r56: [{id, label, folder}, ...] — BGM/раскладка по соцсетям
    bgm_platforms: list = field(default_factory=list)
    intro_middle_outro_folder: str = ""
    vertical_background: str = ""
    cover_horizontal: str = ""
    cover_vertical: str = ""
    shorts_cover_image: str = ""
    output_folder: str = ""
    series_name: str = ""

    # Явные пути Intro/Middle/Outro из вкладки GUI (приоритет над автопоиском в папке)
    h_intro_path: str = ""
    h_mid_path: str = ""
    h_outro_path: str = ""
    v_intro_path: str = ""
    v_mid_path: str = ""
    v_outro_path: str = ""
    s_intro_path: str = ""
    s_mid_path: str = ""
    s_outro_path: str = ""

    # Длительности IMO (сек), для картинок
    h_intro_duration: float = 3.0
    h_mid_duration: float = 1.0
    h_outro_duration: float = 3.0
    v_intro_duration: float = 3.0
    v_mid_duration: float = 1.0
    v_outro_duration: float = 3.0
    s_intro_duration: float = 3.0
    s_mid_duration: float = 1.0
    s_outro_duration: float = 3.0

    # Результаты стадий
    audio_duration: float = 0.0
    transcription: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    master_horizontal: str = ""
    master_vertical: str = ""
    final_horizontal: str = ""
    final_vertical: str = ""
    # r57: аудио (BGM + loudnorm) обрабатывается одним проходом вместе с видео
    audio_normalized: bool = False
    base_bgm_folder: str = ""          # папка BGM базовой площадки (инлайн-микс)
    inline_bgm: bool = False           # ≤1 уникальная папка BGM → микшируем в энкод
    loudnorm_params: dict = field(default_factory=dict)  # измерение pass-1
    shorts: list[str] = field(default_factory=list)
    short_indices: list = field(default_factory=list)    # исходные номера клипов
    # IMO applied on horizontal
    h_did_imo: bool = False

    # Настройки
    gemini_model: str = ""
    gemini_api_key: str = ""
    gemini_api_keys: list = field(default_factory=list)
    whisper_model: str = "large-v3-turbo"
    whisperx_path: str = ""
    whisper_language: str = "ru"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    voice_enhance: bool = True
    voice_enhance_applied: bool = False  # one shared lossless narration pass before masters
    add_bgm: bool = True
    remove_long_pauses: bool = False
    align_speech_rate: bool = False   # как в прежних версиях: без трима и подтяжки темпа
    # r57-api.22: ползунок скорости речи («wpm» | «coefficient»)
    speech_rate_mode: str = "wpm"
    speech_rate_target_wpm: float = 120.0
    speech_rate_coefficient: float = 1.0
    # r57-api.24: вырезать повторы/лишние слова по тексту Gemini (corrected_text)
    cut_extra_words: bool = False
    # r57-api.25: отдельные настройки шортов.
    s_speech_rate_mode: str = "inherit"
    s_speech_rate_target_wpm: float = 120.0
    s_speech_rate_coefficient: float = 1.0
    s_cut_extra_words: bool = False
    intro_gemini: bool = True
    keep_temp_files: bool = False
    target_lufs: float = -14.0
    vstack_top_ratio: float = 0.6
    caption_style: str = "clean_pro"
    hook_style: str = "auto_aisie"
    # r51: YouTube description packaging (from GUI settings)
    packaging_opts: dict = field(default_factory=dict)

    # Cinematic Enhancement (filled when cinematic_enabled)
    cinematic_enabled: bool = False
    edit_plan: object = None          # EditPlan | None
    cinematic_settings: object = None # CinematicSettings | None
    cinematic_transcript_segments: list = field(default_factory=list)
    cinematic_output_format: str = "16:9"

    # Чекбоксы
    h_enable_intro: bool = False
    h_enable_middle: bool = False
    h_enable_outro: bool = False
    h_enable_hooks: bool = True
    h_enable_subtitles: bool = True
    h_enable_strong_words: bool = True
    v_enable_intro: bool = False
    v_enable_middle: bool = False
    v_enable_outro: bool = False
    v_enable_hooks: bool = True
    v_enable_subtitles: bool = True
    v_enable_strong_words: bool = True
    s_enable_intro: bool = False
    s_enable_middle: bool = False
    s_enable_outro: bool = False
    s_enable_hooks: bool = True
    s_enable_subtitles: bool = False   # r20: shorts default = only Hook+CTA
    s_enable_strong_words: bool = False  # r20: no karaoke by default on shorts

    # Прогресс
    progress: float = 0.0
    stage_name: str = ""
    log_callback: Any = None
    cancel_event: Any = None  # threading.Event из GUI

    def log(self, msg: str) -> None:
        """Логировать сообщение."""
        log.info(msg)
        if self.log_callback:
            self.log_callback(msg)


class Stage(ABC):
    """Базовый класс стадии пайплайна."""

    @abstractmethod
    def name(self) -> str:
        """Название стадии."""

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        """Выполнить стадию, вернуть обновлённый контекст."""


class AudioStage(Stage):
    """Шаг 0: Загрузка и валидация аудио."""

    def name(self) -> str:
        return "Загрузка аудио"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log(f"[АУДИО] Загрузка: {ctx.audio_path}")

        resolved_audio = resolve_existing_path(ctx.audio_path, kind="file")
        if resolved_audio and resolved_audio != ctx.audio_path:
            ctx.log(f"[АУДИО] путь источника нормализован: {resolved_audio}")
            ctx.audio_path = resolved_audio

        resolved_output = resolve_existing_path(ctx.output_folder, kind="dir")
        if resolved_output and resolved_output != ctx.output_folder:
            ctx.log(f"[АУДИО] путь вывода нормализован: {resolved_output}")
            ctx.output_folder = resolved_output

        from ..engines.audio import probe_duration

        ctx.audio_duration = probe_duration(ctx.audio_path)
        ctx.log(f"[АУДИО] Длительность: {ctx.audio_duration:.1f} сек")
        ctx.progress = 5.0
        return ctx


class TranscribeStage(Stage):
    """Шаг 1a: Транскрибация через Whisper."""

    def name(self) -> str:
        return "Транскрибация"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[WHISPER] Запуск транскрибации...")

        resolved_audio = resolve_existing_path(ctx.audio_path, kind="file")
        if resolved_audio and resolved_audio != ctx.audio_path:
            ctx.log(f"[WHISPER] путь источника нормализован: {resolved_audio}")
            ctx.audio_path = resolved_audio

        resolved_output = resolve_existing_path(ctx.output_folder, kind="dir")
        if resolved_output and resolved_output != ctx.output_folder:
            ctx.log(f"[WHISPER] путь вывода нормализован: {resolved_output}")
            ctx.output_folder = resolved_output

        from ..engines.transcription import transcribe

        ctx.transcription = transcribe(
            ctx.audio_path,
            model_name=ctx.whisper_model,
            whisperx_path=ctx.whisperx_path,
            language=ctx.whisper_language,
            device=ctx.whisper_device,
            compute_type=ctx.whisper_compute_type,
            log_fn=ctx.log,
        )
        ctx.log(f"[WHISPER] Готово: {len(ctx.transcription.get('segments', []))} сегментов")

        # The video is not built until trim, WPM calculation, and any time-stretch
        # are complete; subtitle/hook timestamps are shifted to this new timeline.
        from ..engines.speech_rate import prepare_narration, prepared_narration_path, speed_params_from_settings
        from ..engines.audio import probe_duration

        work_dir = os.path.join(ctx.output_folder or ".", "_tmp")
        os.makedirs(work_dir, exist_ok=True)
        ctx.voice_enhance_applied = False
        if getattr(ctx, "align_speech_rate", False):
            prepared_path = prepared_narration_path(ctx.audio_path, work_dir)
            speed_kwargs = speed_params_from_settings(
                getattr(ctx, "speech_rate_mode", "wpm"),
                getattr(ctx, "speech_rate_target_wpm", 120.0),
                getattr(ctx, "speech_rate_coefficient", 1.0),
            )
            ctx.narration_path, ctx.transcription, ctx.speech_metrics = prepare_narration(
                ctx.audio_path,
                prepared_path,
                ctx.transcription,
                remove_long_pauses=bool(getattr(ctx, "remove_long_pauses", False)),
                log_fn=ctx.log,
                **speed_kwargs,
            )
            ctx.audio_duration = probe_duration(ctx.narration_path or ctx.audio_path)
            ctx.log(
                f"[SPEECH RATE] final={ctx.speech_metrics.get('final_wpm', 0):.1f} WPM; "
                f"words={ctx.speech_metrics.get('word_count', 0)}; "
                f"pause_reduction={'on' if ctx.speech_metrics.get('pause_reduction_enabled') else 'off'}; "
                f"duration={ctx.audio_duration:.2f}s; status={ctx.speech_metrics.get('status', 'unknown')}"
            )
        else:
            # Галочка «Выравнивать темп речи» выключена: рендер идёт из исходного
            # аудио без трима тишины и time-stretch, тайминги не пересчитываются.
            ctx.narration_path = ""
            ctx.audio_duration = probe_duration(ctx.audio_path)
            ctx.speech_metrics = {"status": "disabled_by_setting"}
            ctx.log(
                "[SPEECH RATE] выравнивание темпа отключено настройкой: "
                "трим тишины и подтяжка к 120–150 WPM не применяются"
            )

        # Process the shared narration once before either visual branch. The
        # temporary WAV remains lossless until the normal master encode writes
        # AAC. Previously H and V each extracted/enhanced/re-muxed their own MP4.
        if ctx.voice_enhance:
            from ..engines.audio import voice_enhance_filter

            previous_duration = float(ctx.audio_duration or 0.0)
            prepared_name = os.path.basename(prepared_path)
            if prepared_name.startswith("narration_prepared_"):
                enhanced_name = prepared_name.replace("narration_prepared_", "narration_enhanced_", 1)
            else:
                enhanced_name = f"narration_enhanced_{os.path.splitext(prepared_name)[0]}.wav"
            enhanced_path = os.path.join(work_dir, enhanced_name)
            ctx.log("[VOICE ENHANCE] единый lossless pass для общей narration до мастеров...")
            voice_enhance_filter(ctx.narration_path or ctx.audio_path, enhanced_path, log_fn=ctx.log)
            enhanced_duration = probe_duration(enhanced_path)
            max_drift = max(0.15, previous_duration * 0.005)
            if enhanced_duration <= 0 or abs(enhanced_duration - previous_duration) > max_drift:
                raise RuntimeError(
                    "Voice enhancement changed narration duration unexpectedly: "
                    f"before={previous_duration:.3f}s after={enhanced_duration:.3f}s "
                    f"(allowed drift ±{max_drift:.3f}s); refusing to render mismatched subtitles."
                )
            ctx.narration_path = enhanced_path
            ctx.audio_duration = enhanced_duration
            ctx.voice_enhance_applied = True
            ctx.speech_metrics["voice_enhance_applied"] = True
            ctx.speech_metrics["voice_enhance_intermediate"] = "lossless_pcm_s16le_wav"
            ctx.log(
                f"[VOICE ENHANCE] applied once; duration drift="
                f"{enhanced_duration - previous_duration:+.3f}s; shared by H/V masters."
            )
        else:
            ctx.speech_metrics["voice_enhance_applied"] = False
        ctx.progress = 15.0
        return ctx


class GeminiStage(Stage):
    """Шаг 1b: Анализ через Gemini — единый вызов."""

    def name(self) -> str:
        return "Анализ Gemini"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log(f"[GEMINI] Анализ моделью {ctx.gemini_model}...")

        from ..engines.analysis import analyze

        ctx.analysis = analyze(
            ctx.transcription,
            api_key=ctx.gemini_api_key,
            api_keys=ctx.gemini_api_keys,
            model_name=ctx.gemini_model,
            intro_gemini=ctx.intro_gemini,
            series_name=ctx.series_name,
            log_fn=ctx.log,
            audio_path=getattr(ctx, "audio_path", "") or "",
            packaging_opts=getattr(ctx, "packaging_opts", None) or {},
        )
        ctx.log("[GEMINI] Пакет ANALYSIS готов")
        corrected_text = str((ctx.analysis or {}).get("corrected_text") or "")
        if corrected_text and bool(getattr(ctx, "cut_extra_words", False)):
            from ..engines.speech_rate import apply_extra_word_cuts

            apply_extra_word_cuts(ctx, corrected_text, log_fn=ctx.log)
        ctx.progress = 25.0
        return ctx