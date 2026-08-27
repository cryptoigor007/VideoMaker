"""Конфигурация приложения — dataclass с валидацией."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """Настройки приложения."""

    # API
    gemini_api_key: str = ""
    gemini_api_keys: list = field(default_factory=list)
    gemini_model: str = "gemini-3.6-flash"

    # Пути
    audio_path: str = ""
    broll_horizontal: str = ""
    broll_vertical: str = ""
    bgm_folder: str = ""
    intro_middle_outro_folder: str = ""
    vertical_background: str = ""
    cover_horizontal: str = ""
    cover_vertical: str = ""
    output_folder: str = ""

    # Название серии
    series_name: str = ""

    # Чекбоксы — горизонтальный этап
    h_enable_intro: bool = False
    h_enable_middle: bool = False
    h_enable_outro: bool = False
    h_enable_hooks: bool = True
    h_enable_subtitles: bool = True
    h_enable_strong_words: bool = True

    # Чекбоксы — вертикальный этап
    v_enable_intro: bool = False
    v_enable_middle: bool = False
    v_enable_outro: bool = False
    v_enable_hooks: bool = True
    v_enable_subtitles: bool = True
    v_enable_strong_words: bool = True

    # Чекбоксы — Shorts этап
    s_enable_intro: bool = False
    s_enable_middle: bool = False
    s_enable_outro: bool = False
    s_enable_hooks: bool = True
    s_enable_subtitles: bool = True
    s_enable_strong_words: bool = True

    # Аудио / WhisperX
    whisper_model: str = "base"
    whisperx_path: str = ""
    whisper_language: str = "ru"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    voice_enhance: bool = True
    add_bgm: bool = True
    intro_gemini: bool = True

    # Прочее
    keep_temp_files: bool = False
    target_lufs: float = -14.0
    vstack_top_ratio: float = 0.6  # P3-31: пропорция верхней части при vstack (0.0-1.0)

    @classmethod
    def from_env(cls) -> "Settings":
        """Загрузить настройки из .env."""
        keys_str = os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str else []
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_api_keys=keys,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
            broll_horizontal=os.getenv("BROLL_HORIZONTAL_FOLDER", ""),
            broll_vertical=os.getenv("BROLL_VERTICAL_FOLDER", ""),
            bgm_folder=os.getenv("BGM_FOLDER", ""),
            output_folder=os.getenv("OUTPUT_FOLDER", ""),
            whisper_model=os.getenv("WHISPER_MODEL", "base"),
            whisperx_path=os.getenv("WHISPERX_PATH", ""),
            whisper_language=os.getenv("WHISPER_LANGUAGE", "ru"),
            whisper_device=os.getenv("WHISPER_DEVICE", "auto"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "auto"),
        )

    def validate(self) -> list[str]:
        """Проверить настройки, вернуть список ошибок."""
        errors = []
        if not self.gemini_api_key:
            errors.append("Не задан Gemini API ключ")
        if not self.audio_path:
            errors.append("Не выбран аудиофайл")
        if not os.path.exists(self.audio_path):
            errors.append(f"Аудиофайл не найден: {self.audio_path}")
        if not self.broll_horizontal:
            errors.append("Не выбрана папка B-roll горизонтальный")
        if not os.path.exists(self.broll_horizontal):
            errors.append(f"Папка B-roll горизонтальный не найдена: {self.broll_horizontal}")
        if self.broll_vertical and not os.path.exists(self.broll_vertical):
            errors.append(f"Папка B-roll вертикальный не найдена: {self.broll_vertical}")
        if not self.output_folder:
            errors.append("Не выбрана папка вывода")
        if not os.path.exists(self.output_folder):
            errors.append(f"Папка вывода не найдена: {self.output_folder}")
        if self.bgm_folder and not os.path.exists(self.bgm_folder):
            errors.append(f"Папка BGM не найдена: {self.bgm_folder}")
        if self.intro_middle_outro_folder and not os.path.exists(self.intro_middle_outro_folder):
            errors.append(f"Папка Intro/Middle/Outro не найдена: {self.intro_middle_outro_folder}")
        if self.vertical_background and not os.path.exists(self.vertical_background):
            errors.append(f"Файл вертикального фона не найден: {self.vertical_background}")
        if self.whisperx_path and not os.path.exists(self.whisperx_path):
            errors.append(f"WhisperX бинарник не найден: {self.whisperx_path}")

        # Валидация вертикального фона: обязателен для вертикальных видео/Shorts если нет вертикального B-roll
        needs_vertical = (
            self.v_enable_intro
            or self.v_enable_middle
            or self.v_enable_outro
            or self.v_enable_hooks
            or self.v_enable_subtitles
            or self.v_enable_strong_words
            or self.s_enable_intro
            or self.s_enable_middle
            or self.s_enable_outro
            or self.s_enable_hooks
            or self.s_enable_subtitles
            or self.s_enable_strong_words
        )
        if needs_vertical and not self.broll_vertical and not self.vertical_background:
            errors.append(
                "Для вертикального видео или Shorts требуется вертикальный фон "
                "(vertical_background) или папка вертикального B-roll (broll_vertical)"
            )

        return errors