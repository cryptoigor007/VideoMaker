"""Стадии пайплайна — базовые классы и конкретные стадии."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Контекст пайплайна — передаётся между стадиями."""

    # Входные данные
    audio_path: str = ""
    broll_horizontal: str = ""
    broll_vertical: str = ""
    bgm_folder: str = ""
    intro_middle_outro_folder: str = ""
    vertical_background: str = ""
    cover_horizontal: str = ""
    cover_vertical: str = ""
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
    horizontal_audio_normalized: bool = False
    vertical_audio_normalized: bool = False
    shorts_audio_normalized: bool = False
    shorts: list[str] = field(default_factory=list)

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
    add_bgm: bool = True
    intro_gemini: bool = True
    keep_temp_files: bool = False
    target_lufs: float = -14.0
    vstack_top_ratio: float = 0.6
    caption_style: str = "auto_aisie"
    hook_style: str = "auto_aisie"

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
    s_enable_subtitles: bool = True
    s_enable_strong_words: bool = True

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
        )
        ctx.log("[GEMINI] Пакет ANALYSIS готов")
        ctx.progress = 25.0
        return ctx