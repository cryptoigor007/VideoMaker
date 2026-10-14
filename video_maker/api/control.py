# VideoMaker API | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: новый файл. Управление приложением из API: применение настроек в
#            Tk-переменные (кнопка читает именно их), серия, запуск, отмена,
#            проверка перед стартом, поиск готовых файлов, чтение настроек.
#   r57-api.1: карта настроек 37 → 62 поля (галочки IMO и поля упаковки больше
#              не теряются); set_series пишет в виджет, переменную и настройки.
# PREV: (нет файла)
# NEW FILE: video_maker/api/control.py

"""Управление приложением из API.

Кнопка «Создать видео» (App._start) читает значения ИЗ TK-ПЕРЕМЕННЫХ, а не из
settings, поэтому API обязан писать именно в переменные — иначе изменения
молча потеряются. Здесь собрана карта «поле настроек → Tk-переменная».
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import logging
import math
import os
import re
import time

log = logging.getLogger(__name__)

#: поле Settings → имя Tk-переменной в App (см. App._start)
SETTINGS_VAR_MAP: dict[str, str] = {
    "audio_path": "audio_var",
    "output_folder": "output_var",
    "broll_horizontal": "broll_h_var",
    "broll_vertical": "broll_v_var",
    "vertical_background": "bg_var",
    "bgm_youtube_folder": "bgm_youtube_folder_var",
    "bgm_tiktok_folder": "bgm_tiktok_folder_var",
    "bgm_instagram_folder": "bgm_instagram_folder_var",
    "bgm_rutube_folder": "bgm_rutube_folder_var",
    "bgm_vk_folder": "bgm_vk_folder_var",
    "bgm_facebook_folder": "bgm_facebook_folder_var",
    "bgm_other_folder": "bgm_other_folder_var",
    "bgm_youtube_enable": "bgm_youtube_enable_var",
    "bgm_tiktok_enable": "bgm_tiktok_enable_var",
    "bgm_instagram_enable": "bgm_instagram_enable_var",
    "bgm_rutube_enable": "bgm_rutube_enable_var",
    "bgm_vk_enable": "bgm_vk_enable_var",
    "bgm_facebook_enable": "bgm_facebook_enable_var",
    "bgm_other_enable": "bgm_other_enable_var",
    "intro_middle_outro_folder": "imo_folder_var",
    "cover_horizontal": "cover_h_var",
    "cover_vertical": "cover_v_var",
    "shorts_cover_image": "shorts_cover_var",
    "gemini_model": "model_var",
    "intro_gemini": "intro_gemini_var",
    "whisperx_path": "whisperx_path_var",
    "whisper_model": "whisper_model_var",
    "whisper_language": "whisper_lang_var",
    "whisper_device": "whisper_dev_var",
    "whisper_compute_type": "whisper_comp_var",
    "keep_temp_files": "keep_temp_var",
    "remove_long_pauses": "remove_long_pauses_var",
    "align_speech_rate": "align_speech_rate_var",
    "speech_rate_mode": "speech_rate_mode_var",
    "speech_rate_target_wpm": "speech_rate_target_wpm_var",
    "speech_rate_coefficient": "speech_rate_coefficient_var",
    "cut_extra_words": "cut_extra_words_var",
    # r57-api.25: отдельные настройки шортов.
    "s_speech_rate_mode": "s_speech_rate_mode_var",
    "s_speech_rate_target_wpm": "s_speech_rate_target_wpm_var",
    "s_speech_rate_coefficient": "s_speech_rate_coefficient_var",
    "s_cut_extra_words": "s_cut_extra_words_var",
    "prevent_sleep": "prevent_sleep_var",
    "shutdown_when_done": "shutdown_when_done_var",
    "target_lufs": "target_lufs_var",
    "voice_enhance": "voice_enhance_var",
    "caption_style": "caption_style_var",
    "hook_style": "hook_style_var",
    # r57-api.1: галочки Intro/Middle/Outro — App._start перезаписывает из них
    # настройки, поэтому без этих строк API-правка молча терялась.
    "h_enable_intro": "h_intro",
    "h_enable_middle": "h_middle",
    "h_enable_outro": "h_outro",
    "h_enable_hooks": "h_hooks",
    "h_enable_subtitles": "h_subs",
    "h_enable_strong_words": "h_strong",
    "v_enable_intro": "v_intro",
    "v_enable_middle": "v_middle",
    "v_enable_outro": "v_outro",
    "v_enable_hooks": "v_hooks",
    "v_enable_subtitles": "v_subs",
    "v_enable_strong_words": "v_strong",
    "s_enable_intro": "s_intro",
    "s_enable_middle": "s_middle",
    "s_enable_outro": "s_outro",
    "s_enable_hooks": "s_hooks",
    "s_enable_subtitles": "s_subs",
    "s_enable_strong_words": "s_strong",
    # r57-api.26: IMO-пути и длительности — полное управление из пульта.
    "h_intro_path": "h_intro_path",
    "h_mid_path": "h_mid_path",
    "h_outro_path": "h_outro_path",
    "h_intro_duration": "h_intro_duration",
    "h_mid_duration": "h_mid_duration",
    "h_outro_duration": "h_outro_duration",
    "v_intro_path": "v_intro_path",
    "v_mid_path": "v_mid_path",
    "v_outro_path": "v_outro_path",
    "v_intro_duration": "v_intro_duration",
    "v_mid_duration": "v_mid_duration",
    "v_outro_duration": "v_outro_duration",
    "s_intro_path": "s_intro_path",
    "s_mid_path": "s_mid_path",
    "s_outro_path": "s_outro_path",
    "s_intro_duration": "s_intro_duration",
    "s_mid_duration": "s_mid_duration",
    "s_outro_duration": "s_outro_duration",
    # r57-api.1: эти App._save_settings пишет ИЗ переменных — без привязки
    # значение откатывалось при первом же сохранении.
    "playlist_season_url": "playlist_season_url_var",
    "playlist_block_url": "playlist_block_url_var",
    "playlist_block_label": "playlist_block_label_var",
    "required_hashtags": "required_hashtags_var",
    "append_playlist_footer": "append_playlist_footer_var",
    "shorts_use_template": "shorts_use_template_var",
    "shorts_desc_max_len": "shorts_desc_max_len_var",
}

#: r57-api.26: схема формы «Настройки» для пульта (единый источник правды).
_CAPTION_STYLE_OPTIONS = [
    "clean_pro", "auto_aisie", "hormozi", "hormozi_green",
    "tiktok_box", "bold_pop", "cliffhanger",
]
_HOOK_STYLE_OPTIONS = ["auto_aisie", "marker"]
_WHISPER_MODEL_OPTIONS = ["tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"]
_WHISPER_LANG_OPTIONS = ["ru", "en", "auto"]
_WHISPER_DEVICE_OPTIONS = ["cpu", "cuda", "auto"]
_WHISPER_COMPUTE_OPTIONS = ["int8", "float16", "float32"]
_SPEECH_MODE_OPTIONS = ["wpm", "coefficient"]
_SHORT_SPEECH_MODE_OPTIONS = ["inherit", "wpm", "coefficient"]
_PLATFORM_ROWS = (
    ("youtube", "YouTube"), ("tiktok", "TikTok"), ("instagram", "Instagram"),
    ("rutube", "Rutube"), ("vk", "VK"), ("facebook", "Facebook"), ("other", "Другое"),
)
_FORMAT_ROWS = (("h", "16:9"), ("v", "9:16"), ("s", "Shorts"))
_KIND_ROWS = (("intro", "Интро"), ("mid", "Середина"), ("outro", "Аутро"))


def _schema_field(name: str, label: str, type_: str = "text", **extra) -> dict:
    return {"name": name, "label": label, "type": type_, **extra}


def _build_settings_schema() -> list[dict]:
    """Секции и поля формы настроек пульта (всё, чем реально управляется пайплайн)."""
    try:
        from ..engines.analysis import AVAILABLE_GEMINI_MODELS

        gemini_options = list(AVAILABLE_GEMINI_MODELS)
    except Exception:  # noqa: BLE001
        gemini_options = []
    sections: list[dict] = [
        {"title": "Основное", "fields": [
            _schema_field("audio_path", "Аудио: файл или папка", "path"),
            _schema_field("output_folder", "Папка вывода", "path"),
            _schema_field("keep_temp_files", "Сохранять временные файлы", "bool"),
            _schema_field("voice_enhance", "Улучшение голоса", "bool"),
            _schema_field("prevent_sleep", "Не засыпать во время обработки", "bool"),
            _schema_field("shutdown_when_done", "Выключить компьютер по завершению", "bool"),
            _schema_field("target_lufs", "Целевая громкость (LUFS)", "number"),
            _schema_field("caption_style", "Стиль субтитров", "select", options=_CAPTION_STYLE_OPTIONS),
            _schema_field("hook_style", "Стиль хуков", "select", options=_HOOK_STYLE_OPTIONS),
        ]},
        {"title": "B-roll и обложки", "fields": [
            _schema_field("broll_horizontal", "B-roll 16:9 (папка)", "path"),
            _schema_field("broll_vertical", "B-roll 9:16 (папка)", "path"),
            _schema_field("vertical_background", "Фон вертикали (картинка)", "path"),
            _schema_field("cover_horizontal", "Обложка 16:9 (картинка)", "path"),
            _schema_field("cover_vertical", "Обложка 9:16 (картинка)", "path"),
            _schema_field("shorts_cover_image", "Фон обложек шортсов (картинка)", "path"),
            _schema_field("vstack_top_ratio", "Доля кадра сверху (vstack)", "number"),
        ]},
        {"title": "Интро / середина / аутро", "fields": [
            _schema_field("intro_middle_outro_folder", "Папка интро/аутро", "path"),
        ]},
        {"title": "BGM (музыка)", "fields": []},
        {"title": "Модели и распознавание", "fields": [
            _schema_field("gemini_model", "Модель Gemini", "select",
                          options=gemini_options or ["gemini-flash-latest"]),
            _schema_field("gemini_api_keys", "Ключи Gemini (по одному на строку)", "secret"),
            _schema_field("intro_gemini", "Интро-подсказки Gemini", "bool"),
            _schema_field("whisper_model", "Модель Whisper", "select", options=_WHISPER_MODEL_OPTIONS),
            _schema_field("whisper_language", "Язык", "select", options=_WHISPER_LANG_OPTIONS),
            _schema_field("whisper_device", "Устройство", "select", options=_WHISPER_DEVICE_OPTIONS),
            _schema_field("whisper_compute_type", "Тип вычислений", "select", options=_WHISPER_COMPUTE_OPTIONS),
            _schema_field("whisperx_path", "Путь whisperx", "path"),
        ]},
        {"title": "Речь — длинный ролик", "fields": [
            _schema_field("remove_long_pauses", "Сокращать длинные паузы", "bool"),
            _schema_field("align_speech_rate", "Выравнивать темп речи", "bool"),
            _schema_field("speech_rate_mode", "Режим скорости", "select", options=_SPEECH_MODE_OPTIONS),
            _schema_field("speech_rate_target_wpm", "Цель (слов/мин)", "number"),
            _schema_field("speech_rate_coefficient", "Коэффициент", "number"),
            _schema_field("cut_extra_words", "Вырезать повторы и лишние слова", "bool"),
        ]},
        {"title": "Речь — шорты", "fields": [
            _schema_field("s_speech_rate_mode", "Режим скорости шортов", "select", options=_SHORT_SPEECH_MODE_OPTIONS),
            _schema_field("s_speech_rate_target_wpm", "Цель шортов (слов/мин)", "number"),
            _schema_field("s_speech_rate_coefficient", "Коэффициент шортов", "number"),
            _schema_field("s_cut_extra_words", "Вырезать повторы на шортах", "bool"),
        ]},
        {"title": "Описание и плейлисты", "fields": [
            _schema_field("shorts_use_template", "Шаблон описания шортсов", "bool"),
            _schema_field("shorts_desc_max_len", "Макс. длина описания шортса", "number"),
            _schema_field("required_hashtags", "Обязательные хештеги", "text"),
            _schema_field("playlist_season_url", "Плейлист сезона (ссылка)", "text"),
            _schema_field("playlist_block_url", "Плейлист-блок (ссылка)", "text"),
            _schema_field("playlist_block_label", "Подпись плейлист-блока", "text"),
            _schema_field("append_playlist_footer", "Добавлять плейлисты в описание", "bool"),
        ]},
        {"title": "Промпт", "fields": [
            _schema_field("prompt_body", "Основной промпт (пусто = встроенный)", "textarea"),
            _schema_field("prompt_extra", "Дополнение к промпту", "textarea"),
        ]},
    ]
    # IMO: пути и длительности для каждого формата (intro/mid/outro).
    imo_section = next(s for s in sections if s["title"] == "Интро / середина / аутро")
    for prefix, fmt_label in _FORMAT_ROWS:
        for kind, kind_label in _KIND_ROWS:
            enable_kind = "middle" if kind == "mid" else kind
            imo_section["fields"].append(
                _schema_field(f"{prefix}_enable_{enable_kind}", f"{fmt_label} — {kind_label}: вкл", "bool")
            )
            imo_section["fields"].append(
                _schema_field(f"{prefix}_{kind}_path", f"{fmt_label} — {kind_label}: файл", "path")
            )
            imo_section["fields"].append(
                _schema_field(f"{prefix}_{kind}_duration", f"{fmt_label} — {kind_label}: секунд", "number")
            )
    # Этапы: хуки/субтитры/сильные слова (intro/mid/outro — в секции IMO).
    stages_section = {"title": "Этапы: хуки и субтитры", "fields": []}
    for prefix, fmt_label in _FORMAT_ROWS:
        stages_section["fields"].append(_schema_field(f"{prefix}_enable_hooks", f"{fmt_label} — хуки", "bool"))
        stages_section["fields"].append(_schema_field(f"{prefix}_enable_subtitles", f"{fmt_label} — субтитры", "bool"))
        stages_section["fields"].append(_schema_field(f"{prefix}_enable_strong_words", f"{fmt_label} — сильные слова", "bool"))
    sections.insert(7, stages_section)  # перед «Описанием и плейлистами»
    # BGM: папка + галочка на каждую платформу.
    bgm_section = next(s for s in sections if s["title"] == "BGM (музыка)")
    for platform, label in _PLATFORM_ROWS:
        bgm_section["fields"].append(_schema_field(f"bgm_{platform}_folder", f"{label} — папка", "path"))
        bgm_section["fields"].append(_schema_field(f"bgm_{platform}_enable", f"{label} — музыка вкл", "bool"))
    # r57-api.27: порядок — от частого к редкому; иконки для заголовков секций.
    icons = {
        "Основное": "🎛️",
        "Речь — длинный ролик": "🗣️",
        "Речь — шорты": "✂️",
        "Этапы: хуки и субтитры": "🧩",
        "B-roll и обложки": "🎬",
        "Интро / середина / аутро": "🎞️",
        "BGM (музыка)": "🎵",
        "Модели и распознавание": "🤖",
        "Описание и плейлисты": "📝",
        "Промпт": "🧠",
    }
    order = [
        "Основное",
        "Речь — длинный ролик",
        "Речь — шорты",
        "Этапы: хуки и субтитры",
        "B-roll и обложки",
        "Интро / середина / аутро",
        "BGM (музыка)",
        "Модели и распознавание",
        "Описание и плейлисты",
        "Промпт",
    ]
    by_title = {section["title"]: section for section in sections}
    for section in sections:
        section["icon"] = icons.get(section["title"], "⚙️")
    return [by_title[title] for title in order if title in by_title]


SETTINGS_SCHEMA: list[dict] = _build_settings_schema()


def get_settings_schema() -> dict:
    """Схема формы настроек для пульта: секции → поля (тип, подпись, опции)."""
    return {"sections": copy.deepcopy(SETTINGS_SCHEMA)}


#: поля, которые нельзя отдавать наружу
SECRET_HINTS = ("key", "token", "secret", "password")

#: поля, которые приложение пересчитывает при старте — API их принимает,
#: но на результат они не влияют (источник истины — bgm_*_enable).
DERIVED_FIELDS = ("add_bgm",)

#: человеческие подписи для ошибок валидации
PLATFORM_FIELDS = [
    "bgm_youtube_enable", "bgm_tiktok_enable", "bgm_instagram_enable",
    "bgm_rutube_enable", "bgm_vk_enable", "bgm_facebook_enable", "bgm_other_enable",
]


def set_series(app, name: str) -> None:
    """Название серии: в приложении это Text-виджет, а не переменная.

    Пишем во все три места сразу (виджет, переменная, настройки) — иначе
    значение «теряется» в зависимости от того, кто его потом читает.
    """
    value = str(name or "")
    entry = getattr(app, "series_entry", None)
    if entry is not None:
        try:
            entry.delete("1.0", "end")
            entry.insert("1.0", value)
        except Exception as exc:  # noqa: BLE001
            log.warning("[API] не удалось задать серию в виджете: %s", exc)
    var = getattr(app, "series_var", None)
    if var is not None:
        with contextlib.suppress(Exception):
            var.set(value)
    settings = getattr(app, "settings", None)
    if settings is not None:
        with contextlib.suppress(AttributeError, ValueError, TypeError):
            settings.series_name = value


def apply_settings(app, data: dict) -> tuple[list[str], list[str]]:
    """Применить настройки (выполнять ТОЛЬКО в главном потоке Tk).

    Возвращает (применённые, неизвестные поля).
    """
    applied: list[str] = []
    unknown: list[str] = []
    settings = getattr(app, "settings", None)
    if settings is None:
        return applied, list(data.keys())

    for field, value in (data or {}).items():
        if (
            isinstance(value, str)
            and value.strip() == "***"
            and any(hint in field.lower() for hint in SECRET_HINTS)
        ):
            # Маска секрета из read_settings: «***» = не менять значение.
            continue
        if field == "gemini_api_keys":
            raw = value if isinstance(value, list) else re.split(r"[\n,]+", str(value))
            keys = [str(k).strip() for k in raw if str(k).strip()]
            settings.gemini_api_keys = keys
            settings.gemini_api_key = keys[0] if keys else ""
            applied.append(field)
            continue
        if field == "target_lufs":
            try:
                numeric_lufs = float(value)
            except (TypeError, ValueError):
                log.warning("[API] отклонён target_lufs=%r: ожидается число [-36, -5]", value)
                unknown.append(field)
                continue
            if not math.isfinite(numeric_lufs) or not -36.0 <= numeric_lufs <= -5.0:
                log.warning("[API] отклонён target_lufs=%r: вне диапазона [-36, -5]", value)
                unknown.append(field)
                continue
            value = numeric_lufs
        if field == "series_name":
            set_series(app, str(value))  # виджет + переменная + настройки
            applied.append(field)
            continue

        known = hasattr(settings, field)
        var_name = SETTINGS_VAR_MAP.get(field)
        var = getattr(app, var_name, None) if var_name else None
        if not known and var is None:
            unknown.append(field)
            continue

        # Reject string/number booleans rather than relying on bool("false").
        # API JSON values for boolean settings must be true/false literals.
        current = getattr(settings, field) if known else None
        var_current = None
        if var is not None:
            try:
                var_current = var.get()
            except Exception:
                pass
        expects_bool = isinstance(current, bool) or isinstance(var_current, bool)
        if expects_bool and type(value) is not bool:
            log.warning("[API] настройка %s отклонена: ожидается boolean, получено %r", field, value)
            unknown.append(field)
            continue

        if var is not None:
            try:
                var.set(value if expects_bool else ("" if value is None else str(value)))
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] переменная %s не приняла %r: %s", var_name, value, exc)
                unknown.append(field)
                continue
        if known:
            try:
                if isinstance(current, float) and not isinstance(value, bool):
                    value = float(value)
                elif isinstance(current, int) and not isinstance(current, bool) and not isinstance(value, bool):
                    value = int(value)
                setattr(settings, field, value)
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] настройка %s не приняла %r: %s", field, value, exc)
                unknown.append(field)
                continue
        if field in DERIVED_FIELDS:
            log.info("[API] поле %s приложение пересчитывает само (bgm_*_enable) — "
                     "значение будет перезаписано при старте", field)
        applied.append(field)

    with contextlib.suppress(Exception):
        if hasattr(app, "_update_style_desc"):
            app._update_style_desc()
    return applied, unknown


def sync_settings_from_gui(app) -> None:
    """app.settings ← поля GUI (то же, что делает App._start перед валидацией).

    API-запуск обязан синхронизировать настройки ДО validate(): иначе app.settings
    содержит дефолты, пока в GUI не нажимали «Создать видео», и запуск отклоняется
    («Не выбрана папка B-roll горизонтальный» и т.п.), хотя поля заполнены.
    """
    try:
        from ..gui.app import collect_settings_from_gui
    except Exception as exc:  # noqa: BLE001
        log.warning("[API] sync_settings_from_gui: %s", exc)
        return
    try:
        collect_settings_from_gui(app)
    except AttributeError as exc:
        # Урезанный/тестовый app без GUI-полей — синхронизировать нечего.
        log.debug("[API] sync_settings_from_gui: %s", exc)


def read_settings(app, mask_secrets: bool = True) -> dict:
    """Текущие настройки приложения (в главном потоке Tk)."""
    settings = getattr(app, "settings", None)
    if settings is None:
        return {}
    # r57-api.27: настройки синхронизируются из полей GUI только перед запуском;
    # для чтения (пульт) подтягиваем живое состояние полей — иначе до первого
    # запуска отдаются устаревшие значения датакласса (пустые пути).
    sync_settings_from_gui(app)
    if dataclasses.is_dataclass(settings):
        data = dataclasses.asdict(settings)
    else:
        data = {k: v for k, v in vars(settings).items() if not k.startswith("_")}

    # r57-api.26: поля из GUI-переменных (IMO-пути/длительности) — их нет в Settings.
    for field, var_name in SETTINGS_VAR_MAP.items():
        if field in data:
            continue
        var = getattr(app, var_name, None)
        if var is None or not hasattr(var, "get"):
            continue
        with contextlib.suppress(Exception):
            data[field] = var.get()

    if mask_secrets:
        for k in list(data.keys()):
            low = k.lower()
            if any(h in low for h in SECRET_HINTS):
                val = data.get(k)
                data[k] = ("***" if val else "")
    return data


def validate(app) -> list[str]:
    """Проверка настроек до старта — чтобы не поймать модальное окно в headless."""
    try:
        errors = list(app.settings.validate())
        if (errors and hasattr(app, "_wake_network_paths")
                and any("/Volumes/" in e for e in errors)):
            # Сетевой том мог ещё не подключиться: будим и ждём до 10 секунд,
            # затем честно повторяем проверку.
            log.info("[API] жду подключения сетевых папок перед стартом…")
            app._wake_network_paths(wait_seconds=10.0, try_open=True)
            errors = list(app.settings.validate())
        return errors
    except Exception as exc:  # noqa: BLE001
        log.warning("[API] validate() упал: %s", exc)
        return [f"Не удалось проверить настройки: {exc}"]


def start_pipeline(app) -> None:
    """Нажать «Создать видео» (в главном потоке Tk)."""
    app._start()


def cancel_pipeline(app) -> None:
    """Нажать «Отмена» (в главном потоке Tk)."""
    app._cancel_pipeline()


def collect_audio(path: str) -> list[str]:
    """Сколько аудиофайлов возьмёт пайплайн по этому пути."""
    try:
        from ..config.settings import Settings
        return list(Settings.collect_audio_files(path))
    except Exception as exc:  # noqa: BLE001
        log.warning("[API] collect_audio(%s): %s", path, exc)
        return []


def find_series_dir(output: str, series: str, since: float = 0.0) -> str:
    """Папка серии в выдаче (по имени или самая свежая с info_metadata.txt)."""
    if not output or not os.path.isdir(output):
        return ""
    candidates: list[tuple[float, str]] = []
    try:
        entries = os.listdir(output)
    except OSError:
        return ""
    for name in entries:
        full = os.path.join(output, name)
        if not os.path.isdir(full):
            continue
        meta = os.path.join(full, "info_metadata.txt")
        if os.path.isfile(meta):
            try:
                mtime = os.path.getmtime(meta)
            except OSError:
                continue
            if mtime >= since - 5:
                candidates.append((mtime, full))
    if series:
        for _, full in candidates:
            if os.path.basename(full) == series:
                return full
    if candidates:
        return max(candidates)[1]
    return ""


def find_results(output: str, series: str = "", since: float = 0.0) -> dict:
    """Что уже готово в папке серии: файлы по платформам и видам."""
    series_dir = find_series_dir(output, series, since)
    result: dict = {
        "output_folder": output,
        "series": series,
        "series_dir": series_dir,
        # A directory or metadata sidecar alone is not a completed video.
        "ready": False,
        "video_files": [],
        "files": [],
        "metadata": "",
        "total_bytes": 0,
    }
    if not series_dir:
        return result

    meta = os.path.join(series_dir, "info_metadata.txt")
    if os.path.isfile(meta):
        result["metadata"] = meta

    limit = time.time() - 86400 * 30
    for root, _dirs, files in os.walk(series_dir):
        for fn in files:
            if fn.endswith((".tmp", ".part", ".log")):
                continue
            full = os.path.join(root, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if st.st_mtime < limit:
                continue
            rel = os.path.relpath(full, series_dir)
            entry = {"path": full, "rel": rel, "size": st.st_size, "mtime": st.st_mtime}
            result["files"].append(entry)
            result["total_bytes"] += st.st_size
            if st.st_size >= 1024 and fn.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")):
                result["video_files"].append(entry)
    result["files"].sort(key=lambda f: f["rel"])
    result["video_files"].sort(key=lambda f: f["rel"])
    result["ready"] = bool(result["video_files"])
    return result
