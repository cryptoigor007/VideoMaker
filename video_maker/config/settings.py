"""Конфигурация приложения — dataclass с валидацией."""
# VideoMaker FIX | 2026.09.04-r27 | 2026-09-04
# CHANGED: validate() — убрана блокировка по whisperx_path (движок = MLX Whisper)
# REPLACE: video_maker/config/settings.py
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma")


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

    # Чекбоксы — Shorts этап (r20: по умолчанию только Hook+CTA)
    s_enable_intro: bool = False
    s_enable_middle: bool = False
    s_enable_outro: bool = False
    s_enable_hooks: bool = True
    s_enable_subtitles: bool = False
    s_enable_strong_words: bool = False

    # Аудио / WhisperX — дефолты по требованию пользователя
    whisper_model: str = "large-v3"
    whisperx_path: str = ""
    whisper_language: str = "ru"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    voice_enhance: bool = True
    add_bgm: bool = True
    intro_gemini: bool = True

    # Питание
    prevent_sleep: bool = True          # не засыпать во время обработки
    shutdown_when_done: bool = False    # выключить компьютер после всех файлов

    # Прочее
    keep_temp_files: bool = False
    target_lufs: float = -14.0
    vstack_top_ratio: float = 0.6

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
            whisper_model=os.getenv("WHISPER_MODEL", "large-v3"),
            whisperx_path=os.getenv("WHISPERX_PATH", ""),
            whisper_language=os.getenv("WHISPER_LANGUAGE", "ru"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        )

    @staticmethod
    def collect_audio_files(path: str) -> list[str]:
        """Собрать аудиофайлы: один файл или все из папки (+ 1 уровень подпапок).

        Поддерживаемые расширения: mp3, wav, flac, m4a, ogg, aac, wma.
        Игнорирует скрытые файлы и папки.
        """
        if not path or not os.path.exists(path):
            return []

        p = Path(path)
        result: list[str] = []

        def _is_audio(name: str) -> bool:
            return name.lower().endswith(_AUDIO_EXTS) and not name.startswith(".")

        if p.is_file():
            if _is_audio(p.name):
                return [str(p.resolve())]
            return []

        try:
            for entry in sorted(p.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_file() and _is_audio(entry.name):
                    result.append(str(entry.resolve()))
                elif entry.is_dir():
                    try:
                        for sub in sorted(entry.iterdir()):
                            if sub.is_file() and _is_audio(sub.name):
                                result.append(str(sub.resolve()))
                    except OSError:
                        continue
        except OSError:
            return []

        return result

    def validate(self) -> list[str]:
        """Проверить настройки, вернуть список ошибок."""
        errors = []
        if not self.gemini_api_key and not self.gemini_api_keys:
            errors.append("Не задан Gemini API ключ")
        if not self.audio_path:
            errors.append("Не выбран аудиофайл")
        elif not os.path.exists(self.audio_path):
            errors.append(f"Аудиофайл не найден: {self.audio_path}")
        if not self.broll_horizontal:
            errors.append("Не выбрана папка B-roll горизонтальный")
        elif not os.path.exists(self.broll_horizontal):
            errors.append(f"Папка B-roll горизонтальный не найдена: {self.broll_horizontal}")
        if self.broll_vertical and not os.path.exists(self.broll_vertical):
            errors.append(f"Папка B-roll вертикальный не найдена: {self.broll_vertical}")
        if not self.output_folder:
            errors.append("Не выбрана папка вывода")
        elif not os.path.exists(self.output_folder):
            errors.append(f"Папка вывода не найдена: {self.output_folder}")
        if self.bgm_folder and not os.path.exists(self.bgm_folder):
            errors.append(f"Папка BGM не найдена: {self.bgm_folder}")
        if self.intro_middle_outro_folder and not os.path.exists(self.intro_middle_outro_folder):
            errors.append(f"Папка Intro/Middle/Outro не найдена: {self.intro_middle_outro_folder}")
        if self.vertical_background and not os.path.exists(self.vertical_background):
            errors.append(f"Файл вертикального фона не найден: {self.vertical_background}")
        # WhisperX больше не используется (транскрипция = MLX Whisper).
        # Старый whisperx_path в настройках не блокирует запуск.

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
