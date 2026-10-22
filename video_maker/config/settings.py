"""Конфигурация приложения — dataclass с валидацией."""
# VideoMaker FIX | 2026.09.16-r51-gui-packaging | 2026-09-16
# CHANGED: packaging fields for YouTube descriptions + prompt_extra
# REPLACE: video_maker/config/settings.py
import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from ..paths import resolve_existing_path

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
    # Optional, conservative compression of excessive silence between words.
    # Disabled by default to preserve the narrator's original intonation.
    remove_long_pauses: bool = False
    # Выравнивание темпа речи. По умолчанию ВЫКЛЮЧЕНО — как в прежних версиях:
    # аудио идёт в видео без обрезки тишины и без ускорения/замедления.
    align_speech_rate: bool = False
    # r57-api.22: ползунок скорости речи — «wpm» (слова/мин) или «coefficient» (множитель)
    speech_rate_mode: str = "wpm"
    speech_rate_target_wpm: float = 120.0
    speech_rate_coefficient: float = 1.0
    # r57-api.24: вырезать повторы/лишние слова (по тексту Gemini), по умолчанию выкл.
    cut_extra_words: bool = False
    # r57-api.25: отдельные настройки шортов — скорость и вырезка независимо от длинного.
    s_speech_rate_mode: str = "inherit"   # inherit | wpm | coefficient
    s_speech_rate_target_wpm: float = 120.0
    s_speech_rate_coefficient: float = 1.0
    s_cut_extra_words: bool = False

    # Питание
    prevent_sleep: bool = True          # не засыпать во время обработки
    shutdown_when_done: bool = False    # выключить компьютер после всех файлов

    # Прочее
    keep_temp_files: bool = False
    # Target only applies to final BGM mixes; voice-only output avoids an extra encode pass.
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


    # --- Cinematic Enhancement (OFF by default) ---
    cinematic_enabled: bool = False
    cinematic_profile: str = "cinematic_documentary"  # psychological_dark|cinematic_documentary|dynamic_shorts|clean_explainer|custom
    cinematic_motion: str = "low"       # off|low|medium|high
    cinematic_tempo: float = 1.0        # 0.5..2.0
    cinematic_color: float = 0.5        # 0..1
    cinematic_transitions: str = "modern_mix"  # hard_only|soft_dissolve|fade_to_black|all_safe
    cinematic_framing: str = "center"   # center|safe_roi|manual
    cinematic_reuse_subs: bool = True
    cinematic_kinetic: str = "off"
    cinematic_max_zoom: float = 1.25
    cinematic_seed: int = 42
    cinematic_transition_sfx: bool = False  # whoosh under soft dissolves
    cinematic_smart_broll: bool = True      # умный подбор B-roll по смыслу (псевдо-нейролинк)
    cinematic_keyword_cuts: bool = True     # смена кадра под ключевые слова озвучки
    cinematic_min_shot: float = 1.3         # минимальная длина плана при пословном монтаже, сек

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
        """Поддерживает выбранный музыкальный файл или папку с треками."""
        source = (folder or "").strip()
        if not source:
            return False
        source = resolve_existing_path(source, kind="any") or source
        exts = (".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus")
        if os.path.isfile(source):
            return not os.path.basename(source).startswith(".") and source.lower().endswith(exts)
        if not os.path.isdir(source):
            return False
        try:
            for name in os.listdir(source):
                candidate = os.path.join(source, name)
                if not name.startswith(".") and name.lower().endswith(exts) and os.path.isfile(candidate):
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
        # YouTube is always an output branch. Its checkbox controls BGM only; it
        # must not disappear merely because another platform's music is enabled.
        # The YouTube BGM checkbox is authoritative. `add_bgm` is a legacy aggregate
        # of all social checkboxes and must not turn YouTube music back on when a
        # different platform has BGM enabled.
        yt_bgm_enabled = bool(self.bgm_youtube_enable)
        yt_bgm_folder = yt_folder if yt_bgm_enabled else ""

        platforms = [
            ("youtube", "YouTube", True, yt_bgm_folder, True),  # always publish branch; BGM is optional
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
                "bgm_enabled": bool(self.bgm_youtube_enable) if pid == "youtube" else bool(en),
                "bgm_folder_configured": bool(folder),
                "bgm_available": bool(has_audio),
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
        if not path:
            return []
        resolved_path = resolve_existing_path(path, kind="any")
        if not resolved_path:
            return []

        p = Path(resolved_path)
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
        # Unicode NFC/NFD + сетевые тома: фиксируем реальные пути один раз,
        # чтобы дальше пайплайн работал с тем же написанием, что и файловая система.
        for attr, kind in (
            ("audio_path", "file"),
            ("broll_horizontal", "dir"),
            ("broll_vertical", "dir"),
            ("output_folder", "dir"),
            ("intro_middle_outro_folder", "dir"),
            ("vertical_background", "file"),
        ):
            value = getattr(self, attr, "") or ""
            resolved = resolve_existing_path(value, kind=kind)
            if resolved:
                setattr(self, attr, resolved)
        try:
            target_lufs = float(self.target_lufs)
        except (TypeError, ValueError):
            target_lufs = float("nan")
        if not math.isfinite(target_lufs) or not -36.0 <= target_lufs <= -5.0:
            errors.append("Целевой LUFS должен быть конечным числом в диапазоне от -36 до -5; применяется только к миксу с BGM")
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
        # Validate every explicitly enabled BGM source before expensive rendering.
        # Otherwise FinalizeStage would only discover a missing/empty track folder
        # after all video stages have run and then refuse to publish the batch.
        active_bgm_sources = [
            ("YouTube", bool(self.bgm_youtube_enable),
             (self.bgm_youtube_folder or "").strip() or (self.bgm_folder or "").strip()),
            ("TikTok", bool(self.bgm_tiktok_enable), (self.bgm_tiktok_folder or "").strip()),
            ("Instagram", bool(self.bgm_instagram_enable), (self.bgm_instagram_folder or "").strip()),
            ("RuTube", bool(getattr(self, "bgm_rutube_enable", False)),
             (getattr(self, "bgm_rutube_folder", "") or "").strip()),
            ("VK", bool(getattr(self, "bgm_vk_enable", False)),
             (getattr(self, "bgm_vk_folder", "") or "").strip()),
            ("Facebook", bool(getattr(self, "bgm_facebook_enable", False)),
             (getattr(self, "bgm_facebook_folder", "") or "").strip()),
            ("Другое", bool(self.bgm_other_enable), (self.bgm_other_folder or "").strip()),
        ]
        for label, enabled, source in active_bgm_sources:
            if not enabled:
                continue
            if not source:
                errors.append(
                    f"Фоновая музыка {label} включена, но источник не выбран. "
                    "Выберите аудиофайл/папку или отключите BGM для этой площадки."
                )
            elif not resolve_existing_path(source, kind="any"):
                errors.append(f"Источник BGM {label} не найден: {source}")
            elif not self.folder_has_bgm_audio(source):
                errors.append(
                    f"Источник BGM {label} не содержит поддерживаемых аудиофайлов: {source}"
                )
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
        if needs_vertical and not self.vertical_background:
            if not self.broll_vertical:
                errors.append(
                    "Для вертикального видео или Shorts требуется вертикальный фон "
                    "(vertical_background) или папка вертикального B-roll (broll_vertical)"
                )
            elif os.path.isdir(self.broll_vertical):
                # Catch an empty selected folder before Whisper/Gemini/render work.
                try:
                    from ..engines.video import collect_video_files
                    if not collect_video_files(self.broll_vertical):
                        errors.append(
                            f"Папка вертикального B-roll не содержит видео: {self.broll_vertical}. "
                            "Добавьте вертикальные клипы или выберите вертикальный фон."
                        )
                except Exception as exc:  # fail closed rather than fail after expensive stages
                    errors.append(f"Не удалось проверить вертикальный B-roll: {exc}")

        return errors
