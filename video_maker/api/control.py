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
import dataclasses
import logging
import os
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

        if var is not None:
            try:
                if isinstance(value, bool):
                    var.set(bool(value))
                else:
                    var.set("" if value is None else str(value))
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] переменная %s не приняла %r: %s", var_name, value, exc)
        if known:
            try:
                current = getattr(settings, field)
                if isinstance(current, bool):
                    value = bool(value)
                elif isinstance(current, float) and not isinstance(value, bool):
                    value = float(value)
                elif isinstance(current, int) and not isinstance(value, bool):
                    value = int(value)
                setattr(settings, field, value)
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] настройка %s не приняла %r: %s", field, value, exc)
        if field in DERIVED_FIELDS:
            log.info("[API] поле %s приложение пересчитывает само (bgm_*_enable) — "
                     "значение будет перезаписано при старте", field)
        applied.append(field)

    with contextlib.suppress(Exception):
        if hasattr(app, "_update_style_desc"):
            app._update_style_desc()
    return applied, unknown


def read_settings(app, mask_secrets: bool = True) -> dict:
    """Текущие настройки приложения (в главном потоке Tk)."""
    settings = getattr(app, "settings", None)
    if settings is None:
        return {}
    if dataclasses.is_dataclass(settings):
        data = dataclasses.asdict(settings)
    else:
        data = {k: v for k, v in vars(settings).items() if not k.startswith("_")}

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
        return list(app.settings.validate())
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
        "ready": bool(series_dir),
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
            result["files"].append({
                "path": full,
                "rel": rel,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
            result["total_bytes"] += st.st_size
    result["files"].sort(key=lambda f: f["rel"])
    return result
