"""Конфигурация приложения — dataclass с валидацией."""
# VideoMaker FIX | 2026.09.16-r51-gui-packaging | 2026-09-16
# CHANGED: packaging fields for YouTube descriptions + prompt_extra
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
    bgm_folder: str = ""  # legacy → youtube
    # r56: BGM по соцсетям (папка + галочка)
    bgm_youtube_enable: bool = True
    bgm_youtube_folder: str = ""
    bgm_tiktok_enable: bool = False
    bgm_tiktok_folder: str = ""
    bgm_instagram_enable: bool = False
    bgm_instagram_folder: str = ""
    bgm_rutube_enable: bool = False
    bgm_rutube_folder: str = ""
    bgm_vk_enable: bool = False
    bgm_vk_folder: str = ""
    bgm_facebook_enable: bool = False
    bgm_facebook_folder: str = ""
    bgm_other_enable: bool = False
    bgm_other_folder: str = ""
    intro_middle_outro_folder: str = ""
    vertical_background: str = ""
    cover_horizontal: str = ""
    cover_vertical: str = ""
    shorts_cover_image: str = ""  # r53: фон обложки Shorts
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

    # r51: YouTube packaging (description .txt / GUI)
    playlist_season_url: str = "https://www.youtube.com/playlist?list=PLOsH4yc61thU"
    playlist_block_url: str = "https://www.youtube.com/playlist?list=PLfOYg9HTzi0s"
    playlist_block_label: str = "Одиночество и отношения"
    required_hashtags: str = "#ТочкаНаблюдения #ТайныйКризисЧеловечества"
    append_playlist_footer: bool = True
    prompt_extra: str = ""
    prompt_body: str = ""  # полный промпт; пусто = встроенный
    shorts_desc_max_len: int = 280
    shorts_use_template: bool = True

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
    def folder_has_bgm_audio(folder: str) -> bool:
        """В папке есть хотя бы один аудиофайл для BGM."""
        folder = (folder or "").strip()
        if not folder or not os.path.isdir(folder):
            return False
        exts = (".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg")
        try:
            for name in os.listdir(folder):
                if name.startswith("."):
                    continue
                if name.lower().endswith(exts):
                    return True
        except OSError:
            return False
        return False

    def active_bgm_platforms(self) -> list[dict]:
        """Включённые соцсети для финализации.

        YouTube — база: галочка вкл → всегда ветка youtube/ (BGM если папка с аудио).
        Остальные (TikTok, Instagram, RuTube, Другое):
        только если галочка вкл И в папке есть аудио.
        """
        yt_folder = (self.bgm_youtube_folder or "").strip() or (self.bgm_folder or "").strip()
        yt_en = bool(self.bgm_youtube_enable)
        if not yt_en and self.add_bgm and yt_folder:
            yt_en = True

        platforms = [
            ("youtube", "YouTube", yt_en, yt_folder, True),  # base: allow without audio
            ("tiktok", "TikTok", self.bgm_tiktok_enable, (self.bgm_tiktok_folder or "").strip(), False),
            ("instagram", "Instagram", self.bgm_instagram_enable, (self.bgm_instagram_folder or "").strip(), False),
            ("rutube", "RuTube", getattr(self, "bgm_rutube_enable", False), (getattr(self, "bgm_rutube_folder", "") or "").strip(), False),
            ("vk", "VK", getattr(self, "bgm_vk_enable", False), (getattr(self, "bgm_vk_folder", "") or "").strip(), False),
            ("facebook", "Facebook", getattr(self, "bgm_facebook_enable", False), (getattr(self, "bgm_facebook_folder", "") or "").strip(), False),
            ("other", "Другое", self.bgm_other_enable, (self.bgm_other_folder or "").strip(), False),
        ]
        out = []
        for pid, label, en, folder, allow_no_audio in platforms:
            if not en:
                continue
            has_audio = self.folder_has_bgm_audio(folder)
            if not allow_no_audio and not has_audio:
                continue  # доп. соцсеть без аудио в папке — пропускаем
            out.append({
                "id": pid,
                "label": label,
                "folder": folder if has_audio else "",
            })
        if not out:
            out.append({"id": "youtube", "label": "YouTube", "folder": ""})
        return out

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
        for _lab, _folder in (
            ("YouTube", self.bgm_youtube_folder),
            ("TikTok", self.bgm_tiktok_folder),
            ("Instagram", self.bgm_instagram_folder),
            ("RuTube", getattr(self, "bgm_rutube_folder", "")),
            ("VK", getattr(self, "bgm_vk_folder", "")),
            ("Facebook", getattr(self, "bgm_facebook_folder", "")),
            ("Другое", self.bgm_other_folder),
        ):
            f = (_folder or "").strip()
            if f and not os.path.exists(f):
                errors.append(f"Папка BGM {_lab} не найдена: {f}")
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
