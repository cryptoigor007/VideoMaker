# DEEP AUDIT CP14 (2026-10-12): capture GUI settings in Tk main thread before worker start.
# VideoMaker | 2026.09.23-r57-api | 2026-09-23
# CHANGED (r57-api — пульт и живой статус):
#   1) Статус-бар: строка «▶ что идёт сейчас» — последнее действие пайплайна
#      (рамки-разделители отбрасываются). Заполняется из _log.
#   2) Блок «Пульт» в главной вкладке: адрес для телефона, галочка «Доступ из
#      локальной сети», кнопка «Открыть пульт».
#   3) Новые методы: _show_api_info, _on_api_lan_toggle, _open_api_page.
# PREV: 2026.09.10-r43-idlefix — надёжность idle/закрытия: IMO rescan 60s,
#       heartbeat 30s, _on_close, log_text 3000 строк, _kill_ffmpeg.
# REPLACE: video_maker/gui/app.py

"""Главное окно — Tkinter GUI с тёмной темой и подробным логированием."""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import tkinter as tk
import traceback
import contextlib
import copy
import json
from tkinter import filedialog, messagebox, ttk

from ..config.settings import Settings
from ..pipeline.stages import PipelineContext

log = logging.getLogger(__name__)

# ─── Цвета (Design System: Soft UI Evolution Dark) ────────────────────────
COLORS = {
    "bg":           "#0F172A",
    "card":         "#1E293B",
    "card_hover":   "#273549",
    "primary":      "#818CF8",
    "primary_dim":  "#6366F1",
    "accent":       "#059669",
    "accent_dim":   "#047857",
    "text":         "#F8FAFC",
    "text_dim":     "#94A3B8",
    "text_muted":   "#8B9BB3",
    "border":       "#334155",
    "input_bg":     "#0F172A",
    "input_fg":     "#F8FAFC",
    "selected":     "#6366F1",
    "error":        "#DC2626",
    "log_bg":       "#0D1117",
}

# ─── Settings persistence ───────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".video_maker_settings.json")

# Диагностика доставки колеса мыши (включается переменной окружения).
_SCROLL_DEBUG = bool(os.environ.get("VIDEO_MAKER_SCROLL_DEBUG"))

PATH_VARS = [
    "audio_var",
    "broll_h_var",
    "broll_v_var",
    "bg_var",
    "bgm_youtube_folder_var", "bgm_tiktok_folder_var", "bgm_instagram_folder_var", "bgm_rutube_folder_var", "bgm_vk_folder_var", "bgm_facebook_folder_var", "bgm_other_folder_var",
    "imo_folder_var",
    "cover_h_var",
    "cover_v_var",
    "shorts_cover_var",
    "whisperx_path_var",
    "output_var",
    "h_intro_path", "h_mid_path", "h_outro_path",
    "v_intro_path", "v_mid_path", "v_outro_path",
    "s_intro_path", "s_mid_path", "s_outro_path",
]

OTHER_VARS = [
    "caption_style_var",
    "hook_style_var",
    "series_var",
    "model_var",
    "whisper_model_var",
    "whisper_lang_var",
    "whisper_dev_var",
    "whisper_comp_var",
    "target_lufs_var",
    "h_intro_duration", "h_intro_position", "h_intro_custom_time",
    "h_mid_duration", "h_mid_position", "h_mid_custom_time",
    "h_outro_duration", "h_outro_position", "h_outro_custom_time",
    "v_intro_duration", "v_intro_position", "v_intro_custom_time",
    "v_mid_duration", "v_mid_position", "v_mid_custom_time",
    "v_outro_duration", "v_outro_position", "v_outro_custom_time",
    "s_intro_duration", "s_intro_position", "s_intro_custom_time",
    "s_mid_duration", "s_mid_position", "s_mid_custom_time",
    "s_outro_duration", "s_outro_position", "s_outro_custom_time",
    "playlist_season_url_var", "playlist_block_url_var", "playlist_block_label_var",
    "required_hashtags_var", "shorts_desc_max_len_var",
    "speech_rate_mode_var", "speech_rate_target_wpm_var", "speech_rate_coefficient_var",
    "s_speech_rate_mode_var", "s_speech_rate_target_wpm_var", "s_speech_rate_coefficient_var",
]

BOOL_VARS = [
    "h_intro", "h_middle", "h_outro", "h_hooks", "h_subs", "h_strong",
    "v_intro", "v_middle", "v_outro", "v_hooks", "v_subs", "v_strong",
    "s_intro", "s_middle", "s_outro", "s_hooks", "s_subs", "s_strong",
    "voice_enhance_var", "intro_gemini_var", "keep_temp_var",
    "bgm_youtube_enable_var", "bgm_tiktok_enable_var",
    "bgm_instagram_enable_var", "bgm_rutube_enable_var", "bgm_vk_enable_var", "bgm_facebook_enable_var", "bgm_other_enable_var",
    "prevent_sleep_var", "shutdown_when_done_var",
    "append_playlist_footer_var", "shorts_use_template_var",
    "remove_long_pauses_var",
    "align_speech_rate_var",
    "cut_extra_words_var",
    "s_cut_extra_words_var",
]


def _deepest_existing(path: str) -> str:
    """Глубочайшая существующая часть пути (для «пробуждения» сетевых папок)."""
    if not str(path or "").strip():
        return ""
    cur = os.path.abspath(path)
    while cur and cur != os.path.dirname(cur):
        if os.path.exists(cur):
            return cur
        cur = os.path.dirname(cur)
    return ""


def _wait_for_paths(
    paths: list[str],
    wait_seconds: float,
    *,
    log_fn=None,
    progress_every: float = 4.0,
    trigger=None,
    pump=None,
) -> list[str]:
    """Ждать появления путей (сетевые тома) и вернуть всё ещё отсутствующие.

    trigger() вызывается один раз в начале ожидания, чтобы попытаться разбудить
    подключение тома; pump() — чтобы окно перерисовывалось, пока мы ждём.
    """
    _log = log_fn or log.info
    checked = [str(p).strip() for p in (paths or []) if str(p or "").strip()]
    missing = [p for p in checked if not os.path.exists(p)]
    if not missing:
        return []
    if trigger is not None:
        try:
            trigger()
        except Exception as exc:  # noqa: BLE001 — разбудить том не критично
            log.debug("[СЕТЬ] не удалось разбудить том: %s", exc)
    total = max(0.0, float(wait_seconds))
    if total > 0:
        _log(f"[СЕТЬ] жду подключения: {', '.join(missing[:4])} (до {total:.0f} с)")
    deadline = time.monotonic() + total
    last_report = time.monotonic()
    while True:
        missing = [p for p in checked if not os.path.exists(p)]
        if not missing or time.monotonic() >= deadline:
            break
        # «Будим» не только корень тома, но и существующих родителей: на SMB
        # длинный путь может отвечать «нет» , пока по папке не пройдутся.
        for p in missing:
            try:
                ancestor = _deepest_existing(os.path.dirname(p) or p)
                if ancestor and os.path.isdir(ancestor):
                    os.listdir(ancestor)
            except OSError:
                pass
        time.sleep(0.5)
        if pump is not None:
            try:
                pump()
            except Exception:  # noqa: BLE001
                pass
        now = time.monotonic()
        if now - last_report >= progress_every and total >= progress_every:
            last_report = now
            _log(f"[СЕТЬ] жду том… осталось {max(0.0, deadline - now):.0f} с")
    if checked and not missing:
        _log("[СЕТЬ] сетевые папки доступны")
    return missing


def speech_rate_mode_display(value) -> str:
    """Подпись режима скорости для GUI-комбобокса («По словам/мин»/«По коэффициенту»)."""
    from ..engines.speech_rate import speed_mode_label

    return speed_mode_label(value)


def short_speech_rate_mode_display(value) -> str:
    """Подпись режима скорости шортов («Как на длинном»/«По словам/мин»/«По коэффициенту»)."""
    from ..engines.speech_rate import short_speed_mode_label

    return short_speed_mode_label(value)


def _float_setting(value, default: float) -> float:
    """Безопасный float из tk-переменной (пусто/мусор → default)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def collect_settings_from_gui(app) -> None:
    """Перенести значения полей GUI в app.settings (то же, что делает кнопка «Создать видео»).

    Вызывается из App._start и из API-запуска (POST /api/run): валидация и пайплайн
    не должны зависеть от того, нажимали ли кнопку в GUI после загрузки настроек.
    """
    app.settings.audio_path = app.audio_var.get()
    app.settings.broll_horizontal = app.broll_h_var.get()
    app.settings.broll_vertical = app.broll_v_var.get()
    app.settings.vertical_background = app.bg_var.get()
    # r56 platform BGM
    app.settings.bgm_youtube_folder = app.bgm_youtube_folder_var.get().strip()
    app.settings.bgm_tiktok_folder = app.bgm_tiktok_folder_var.get().strip()
    app.settings.bgm_instagram_folder = app.bgm_instagram_folder_var.get().strip()
    app.settings.bgm_rutube_folder = app.bgm_rutube_folder_var.get().strip()
    app.settings.bgm_vk_folder = app.bgm_vk_folder_var.get().strip()
    app.settings.bgm_facebook_folder = app.bgm_facebook_folder_var.get().strip()
    app.settings.bgm_other_folder = app.bgm_other_folder_var.get().strip()
    app.settings.bgm_youtube_enable = bool(app.bgm_youtube_enable_var.get())
    app.settings.bgm_tiktok_enable = bool(app.bgm_tiktok_enable_var.get())
    app.settings.bgm_instagram_enable = bool(app.bgm_instagram_enable_var.get())
    app.settings.bgm_rutube_enable = bool(app.bgm_rutube_enable_var.get())
    app.settings.bgm_vk_enable = bool(app.bgm_vk_enable_var.get())
    app.settings.bgm_facebook_enable = bool(app.bgm_facebook_enable_var.get())
    app.settings.bgm_other_enable = bool(app.bgm_other_enable_var.get())
    app.settings.bgm_folder = app.settings.bgm_youtube_folder  # legacy
    app.settings.intro_middle_outro_folder = app.imo_folder_var.get()
    app.settings.cover_horizontal = app.cover_h_var.get()
    app.settings.cover_vertical = app.cover_v_var.get()
    if hasattr(app, "shorts_cover_var"):
        app.settings.shorts_cover_image = app.shorts_cover_var.get().strip()
    app.settings.series_name = (app.series_entry.get("1.0", "end").strip() if hasattr(app, "series_entry") else app.series_var.get())
    app.settings.gemini_model = app.model_var.get()
    app.settings.intro_gemini = app.intro_gemini_var.get()

    # WhisperX settings
    app.settings.whisperx_path = app.whisperx_path_var.get()
    app.settings.whisper_model = app.whisper_model_var.get() or "large-v3"
    app.settings.whisper_language = app.whisper_lang_var.get()
    app.settings.whisper_device = app.whisper_dev_var.get()
    app.settings.whisper_compute_type = app.whisper_comp_var.get()

    # Other settings
    app.settings.keep_temp_files = app.keep_temp_var.get()
    app.settings.prevent_sleep = app.prevent_sleep_var.get()
    app.settings.shutdown_when_done = app.shutdown_when_done_var.get()
    try:
        target_lufs = float(app.target_lufs_var.get().strip() or -14.0)
        if not math.isfinite(target_lufs) or not -36.0 <= target_lufs <= -5.0:
            raise ValueError("target_lufs outside supported range [-36, -5]")
        app.settings.target_lufs = target_lufs
    except (ValueError, TypeError, AttributeError):
        app.settings.target_lufs = -14.0
        app.target_lufs_var.set("-14.0")
        log.warning("[GUI] Некорректный target_lufs; восстановлено -14.0")

    log.info(f"[GUI] audio_path = {app.settings.audio_path}")
    log.info(f"[GUI] broll_horizontal = {app.settings.broll_horizontal}")
    log.info(f"[GUI] broll_vertical = {app.settings.broll_vertical}")
    log.info(f"[GUI] vertical_background = {app.settings.vertical_background}")
    log.info(f"[GUI] bgm_folder = {app.settings.bgm_folder}")
    log.info(f"[GUI] imo_folder = {app.settings.intro_middle_outro_folder}")
    log.info(f"[GUI] output_folder = {app.output_var.get() or '(auto)'}")
    log.info(f"[GUI] gemini_model = {app.settings.gemini_model}")
    log.info(f"[GUI] series_name = {app.settings.series_name}")
    log.info(f"[GUI] whisper_language = {app.settings.whisper_language}")
    if hasattr(app, "caption_style_var"):
        log.info(f"[GUI] caption_style = {app.caption_style_var.get()}")
    if hasattr(app, "hook_style_var"):
        log.info(f"[GUI] hook_style = {app.hook_style_var.get()}")
    log.info(f"[GUI] whisper_device = {app.settings.whisper_device}")
    log.info(f"[GUI] whisper_compute_type = {app.settings.whisper_compute_type}")
    log.info(f"[GUI] keep_temp_files = {app.settings.keep_temp_files}")
    log.info(f"[GUI] target_lufs = {app.settings.target_lufs}")

    app.settings.h_enable_intro = app.h_intro.get()
    app.settings.h_enable_middle = app.h_middle.get()
    app.settings.h_enable_outro = app.h_outro.get()
    app.settings.h_enable_hooks = app.h_hooks.get()
    app.settings.h_enable_subtitles = app.h_subs.get()
    app.settings.h_enable_strong_words = app.h_strong.get()
    app.settings.v_enable_intro = app.v_intro.get()
    app.settings.v_enable_middle = app.v_middle.get()
    app.settings.v_enable_outro = app.v_outro.get()
    app.settings.v_enable_hooks = app.v_hooks.get()
    app.settings.v_enable_subtitles = app.v_subs.get()
    app.settings.v_enable_strong_words = app.v_strong.get()
    app.settings.s_enable_intro = app.s_intro.get()
    app.settings.s_enable_middle = app.s_middle.get()
    app.settings.s_enable_outro = app.s_outro.get()
    app.settings.s_enable_hooks = app.s_hooks.get()
    app.settings.s_enable_subtitles = app.s_subs.get()
    app.settings.s_enable_strong_words = app.s_strong.get()

    app.settings.voice_enhance = app.voice_enhance_var.get()
    app.settings.remove_long_pauses = bool(app.remove_long_pauses_var.get())
    app.settings.align_speech_rate = bool(app.align_speech_rate_var.get())
    from ..engines.speech_rate import normalize_short_speed_mode, normalize_speed_mode

    app.settings.speech_rate_mode = normalize_speed_mode(app.speech_rate_mode_var.get())
    app.settings.speech_rate_target_wpm = _float_setting(app.speech_rate_target_wpm_var.get(), 120.0)
    app.settings.speech_rate_coefficient = _float_setting(app.speech_rate_coefficient_var.get(), 1.0)
    app.settings.cut_extra_words = bool(app.cut_extra_words_var.get())
    # r57-api.25: отдельные настройки шортов.
    app.settings.s_speech_rate_mode = normalize_short_speed_mode(app.s_speech_rate_mode_var.get())
    app.settings.s_speech_rate_target_wpm = _float_setting(app.s_speech_rate_target_wpm_var.get(), 120.0)
    app.settings.s_speech_rate_coefficient = _float_setting(app.s_speech_rate_coefficient_var.get(), 1.0)
    app.settings.s_cut_extra_words = bool(app.s_cut_extra_words_var.get())
    app.settings.add_bgm = any([
        app.settings.bgm_youtube_enable,
        app.settings.bgm_tiktok_enable,
        app.settings.bgm_instagram_enable,
        app.settings.bgm_rutube_enable,
        app.settings.bgm_vk_enable,
        app.settings.bgm_facebook_enable,
        app.settings.bgm_other_enable,
    ])

    log.info(f"[GUI] voice_enhance = {app.settings.voice_enhance}")
    log.info(f"[GUI] remove_long_pauses = {app.settings.remove_long_pauses}")
    log.info(f"[GUI] bgm platforms = {app.settings.active_bgm_platforms()}")

    # Папка вывода: явная, либо родитель файла, либо сама папка аудио
    out = app.output_var.get().strip()
    if not out and app.settings.audio_path:
        if os.path.isdir(app.settings.audio_path):
            out = app.settings.audio_path
        else:
            out = os.path.dirname(app.settings.audio_path)
    app.settings.output_folder = out
    log.info(f"[GUI] output_folder (final) = {app.settings.output_folder}")


class App:
    """Главное окно приложения ВидеоМейкер."""

    def __init__(self, root: tk.Tk):
        log.info("[GUI] ═══════════════════════════════════════════════")
        log.info("[GUI] Инициализация App.__init__()")
        log.info(f"[GUI] Python: {sys.version}")
        log.info(f"[GUI] PID: {os.getpid()}")
        log.info(f"[GUI] Рабочая папка: {os.getcwd()}")

        self.root = root
        log.info("[GUI] Создание корневого окна Tk()")

        self.root.title("ВидеоМейкер")
        # Иконка приложения (красивая play-кнопка)
        try:
            icon_candidates = [
                os.path.join(os.path.dirname(__file__), "..", "icons", "app_icon.png"),  # video_maker/icons
                os.path.join(os.path.dirname(__file__), "..", "..", "icons", "app_icon.png"),
                os.path.expanduser("~/video_maker/icons/app_icon.png"),
                os.path.join(os.getcwd(), "icons", "app_icon.png"),
                os.path.join(os.getcwd(), "video_maker", "icons", "app_icon.png"),
            ]
            icon_path = next((p for p in icon_candidates if os.path.isfile(p)), None)
            if icon_path:
                icon_img = tk.PhotoImage(file=icon_path)
                # Tk лучше принимает несколько размеров — уменьшаем если огромная
                if icon_img.width() > 128:
                    # subsample roughly to ~64
                    factor = max(1, icon_img.width() // 64)
                    icon_img = icon_img.subsample(factor, factor)
                self.root.iconphoto(True, icon_img)
                self._icon_img = icon_img  # keep ref
            else:
                # fallback 32x32: indigo square + white play triangle
                icon_img = tk.PhotoImage(width=32, height=32)
                for y in range(32):
                    for x in range(32):
                        if 2 <= x <= 29 and 2 <= y <= 29:
                            icon_img.put("#6366F1", (x, y))
                # triangle: left edge x=11, tip at x=22, y from 8..23
                for y in range(8, 24):
                    t = (y - 8) / 15.0  # 0..1
                    # half-width grows to mid then shrinks
                    half = t * 7.5 if t <= 0.5 else (1 - t) * 7.5
                    x0 = 11
                    x1 = int(11 + 11 * (1 - abs(2 * t - 1)))  # expands toward tip
                    # classic play: width increases with distance from top/bottom
                    progress = (y - 8) if y <= 15 else (23 - y)
                    width = max(1, int(progress * 1.4))
                    for x in range(12, 12 + width):
                        if x < 32:
                            icon_img.put("#FFFFFF", (x, y))
                self.root.iconphoto(True, icon_img)
                self._icon_img = icon_img
        except Exception as _icon_err:
            log.warning("[GUI] icon load failed: %s", _icon_err)
        # Пропорциональное окно: не растягиваем низкие дисплеи по высоте и
        # центрируем главное окно в доступной области экрана.
        screen_w = max(1, int(self.root.winfo_screenwidth()))
        screen_h = max(1, int(self.root.winfo_screenheight()))
        min_w = min(860, max(480, screen_w - 24))
        min_h = min(620, max(400, screen_h - 40))
        window_w = min(1180, max(min_w, screen_w - 80))
        window_h = min(820, max(min_h, screen_h - 100))
        window_x = max(0, (screen_w - window_w) // 2)
        window_y = max(0, (screen_h - window_h) // 2)
        self.root.geometry(f"{window_w}x{window_h}+{window_x}+{window_y}")
        self.root.minsize(min_w, min_h)
        self.root.configure(bg=COLORS["bg"])
        self._scroll_canvases = []
        self._scroll_areas = []   # (host, canvas): зона колеса, включая полосу прокрутки
        self._last_browse_dir = ""  # последняя выбранная в диалогах папка
        log.info("[GUI] Окно настроено: %sx%s, min %sx%s",
                 window_w, window_h, min_w, min_h)

        self.settings = Settings.from_env()
        log.info(f"[GUI] Настройки загружены: model={self.settings.gemini_model}, "
                 f"whisper={self.settings.whisper_model}")

        self.model_var = tk.StringVar(value=self.settings.gemini_model)
        # r51 packaging
        self.playlist_season_url_var = tk.StringVar(
            value=getattr(self.settings, "playlist_season_url", "")
            or "https://www.youtube.com/playlist?list=PLOsH4yc61thU"
        )
        self.playlist_block_url_var = tk.StringVar(
            value=getattr(self.settings, "playlist_block_url", "")
            or "https://www.youtube.com/playlist?list=PLfOYg9HTzi0s"
        )
        self.playlist_block_label_var = tk.StringVar(
            value=getattr(self.settings, "playlist_block_label", "") or "Одиночество и отношения"
        )
        self.required_hashtags_var = tk.StringVar(
            value=getattr(self.settings, "required_hashtags", "")
            or "#ТочкаНаблюдения #ТайныйКризисЧеловечества"
        )
        self.append_playlist_footer_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "append_playlist_footer", True))
        )
        self.shorts_desc_max_len_var = tk.StringVar(
            value=str(getattr(self.settings, "shorts_desc_max_len", 280) or 280)
        )
        self.shorts_use_template_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "shorts_use_template", True))
        )
        self.prompt_extra_var = tk.StringVar(value="")  # full text kept in Text widget
        self._prompt_extra_default = str(getattr(self.settings, "prompt_extra", "") or "")

        self.running = False
        self.cancel_event = threading.Event()
        self.whisperx_path_var = tk.StringVar(value=self.settings.whisperx_path)
        self.whisperx_status_var = tk.StringVar(value="Автопоиск...")
        # Idle / close control
        self._closing = False
        self._close_pending = False
        self._close_wait_started = 0.0
        self._close_poll_after_id = None
        self._close_wait_warning_shown = False
        self._pipeline_thread: threading.Thread | None = None
        self._heartbeat_after_id = None
        self._imo_rescan_after_id = None
        self._imo_access_cache: dict[str, tuple[float, bool]] = {}  # path -> (ts, ok)
        log.info("[GUI] self.running = False")

        # Привязываем закрытие окна
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        log.info("[GUI] Привязан обработчик WM_DELETE_WINDOW → _on_close()")

        # Привязываем необработанные исключения
        log.info("[GUI] Привязан обработчик необработанных исключений")

        log.info("[GUI] Вызов _setup_theme()...")
        self._setup_theme()
        log.info("[GUI] _setup_theme() завершён")

        log.info("[GUI] Вызов _build_ui()...")
        self._build_ui()
        # Bind once globally; route a wheel event only to a visible scrollable
        # area under the pointer. This also works over Entry/Text/Checkbutton.
        self.root.bind_all("<MouseWheel>", self._on_scroll_wheel, add="+")
        self.root.bind_all("<Button-4>", self._on_scroll_wheel, add="+")
        self.root.bind_all("<Button-5>", self._on_scroll_wheel, add="+")
        try:
            # Tk 9: трекпад и Magic Mouse шлют <TouchpadScroll>, а не <MouseWheel>.
            self.root.bind_all("<TouchpadScroll>", self._on_touchpad_scroll, add="+")
        except tk.TclError:
            pass  # Tk < 9: жест трекпада приходит как <MouseWheel>
        if _SCROLL_DEBUG:
            def _dbg_toplevel(event):
                log.info(
                    "[SCROLL-DBG] toplevel-уровень: widget=%r x_root=%s y_root=%s delta=%r",
                    getattr(event, "widget", None), getattr(event, "x_root", None),
                    getattr(event, "y_root", None), getattr(event, "delta", None),
                )
                return None

            self.root.bind("<MouseWheel>", _dbg_toplevel, add="+")
        self._build_macos_menu()
        log.info("[GUI] _build_ui() завершён")

        # Запуск heartbeat (редко, только для диагностики)
        self._heartbeat()

        log.info("[GUI] Вызов _load_settings()...")
        self._load_settings()
        log.info("[GUI] _load_settings() завершён")
        if not getattr(self, "_imo_traces_bound", False):
            self._bind_imo_path_traces()
        self._sync_imo_checkboxes()

        # Автопоиск whisperx после загрузки настроек
        self._find_whisperx()

        log.info("[GUI] ═══════════════════════════════════════════════")
        log.info("[GUI] App.__init__() ЗАВЕРШЁН — окно готово")
        log.info("[GUI] ═══════════════════════════════════════════════")

    def _reinstall_log_hook(self) -> None:
        """Вернуть буфер лога пульта после переконфигурации logging.

        setup_logging(force=True) удаляет все обработчики корневого логгера,
        включая буфер живого журнала /api/log и SSE. Перепривязываем его.
        """
        runtime = None
        for module_name in ("__main__", "video_maker.main"):
            module = sys.modules.get(module_name)
            candidate = getattr(module, "_API_RUNTIME", None) if module else None
            if candidate is not None:
                runtime = candidate
                break
        if runtime is not None and hasattr(runtime, "status"):
            with contextlib.suppress(Exception):
                runtime.status.install_log_hook()

    def _on_close(self) -> None:
        """Ask the pipeline to cancel and wait for its worker before destroying Tk."""
        if getattr(self, "_closing", False) or getattr(self, "_close_pending", False):
            return

        if self.running:
            answer = messagebox.askyesno(
                "Подтверждение",
                "Пайплайн выполняется. Отменить его и дождаться безопасного завершения перед закрытием?",
            )
            if not answer:
                return
            self._close_pending = True
            self._close_wait_started = time.monotonic()
            self._close_wait_warning_shown = False
            log.warning("[GUI] Закрытие отложено: запрошена отмена активного пайплайна")
            self.cancel_event.set()
            self._kill_ffmpeg_processes()
            with contextlib.suppress(Exception):
                self._log("\n  ЗАКРЫТИЕ: запрошена отмена; жду окончания текущей стадии и сохранения checkpoint...")
            self._schedule_close_poll()
            return

        self._finish_close()

    def _schedule_close_poll(self, delay_ms: int = 250) -> None:
        if not getattr(self, "_close_pending", False) or getattr(self, "_closing", False):
            return
        try:
            self._close_poll_after_id = self.root.after(delay_ms, self._poll_close_until_idle)
        except (tk.TclError, RuntimeError):
            log.exception("[GUI] Не удалось ожидать завершения пайплайна через Tk")

    def _poll_close_until_idle(self) -> None:
        """Keep Tk alive until the pipeline's finally block has fully returned."""
        self._close_poll_after_id = None
        if not getattr(self, "_close_pending", False) or getattr(self, "_closing", False):
            return
        worker = getattr(self, "_pipeline_thread", None)
        active = bool(getattr(self, "running", False)) or bool(worker and worker.is_alive())
        if not active:
            self._finish_close()
            return

        elapsed = time.monotonic() - float(getattr(self, "_close_wait_started", time.monotonic()))
        if elapsed >= 120 and not getattr(self, "_close_wait_warning_shown", False):
            self._close_wait_warning_shown = True
            keep_waiting = messagebox.askyesno(
                "Завершение ещё выполняется",
                "Пайплайн пока не завершился. Продолжать ждать безопасного завершения?\n\n"
                "Если выбрать «Нет», окно останется открытым; принудительного завершения не будет.",
            )
            if not keep_waiting:
                self._close_pending = False
                self._close_wait_warning_shown = False
                self._log("\n  Закрытие отменено: пайплайн всё ещё работает; окно оставлено открытым.")
                return
            self._close_wait_started = time.monotonic()
            self._close_wait_warning_shown = False
        self._schedule_close_poll(500)

    def _finish_close(self) -> None:
        """Release background services and destroy Tk without hard process exit."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._close_pending = False

        # Cancel UI timers before destroying widgets.
        for attr in ("_heartbeat_after_id", "_imo_rescan_after_id", "_close_poll_after_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                with contextlib.suppress(Exception):
                    self.root.after_cancel(aid)
                setattr(self, attr, None)

        try:
            from ..engines.power import prevent_sleep_stop
            prevent_sleep_stop()
        except Exception:
            log.debug("[GUI] prevent_sleep_stop failed during close", exc_info=True)

        try:
            self._save_settings()
        except Exception as exc:
            log.warning("[GUI] save on close failed: %s", exc)

        # Stop the API queue/listener and reject pending TkBridge callbacks so
        # HTTP handlers are not left waiting after the main loop is destroyed.
        runtime = None
        for module_name in ("__main__", "video_maker.main"):
            module = sys.modules.get(module_name)
            candidate = getattr(module, "_API_RUNTIME", None) if module else None
            if candidate is not None:
                runtime = candidate
                break
        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime.stop()
        else:
            bridge = getattr(self, "_api_bridge", None)
            if bridge is not None:
                with contextlib.suppress(Exception):
                    bridge.stop()

        log.info("[GUI] Закрытие: сервисы остановлены, сохранение настроек завершено")
        with contextlib.suppress(tk.TclError, RuntimeError):
            self.root.quit()
        with contextlib.suppress(tk.TclError, RuntimeError):
            self.root.destroy()
        log.info("[GUI] root.destroy() выполнен; процесс завершается штатно")

    # ─── Тема ─────────────────────────────────────────────────────────────

    def _setup_theme(self) -> None:
        """Настроить ttk тему."""
        log.info("[GUI] _setup_theme(): настройка стилей...")
        style = ttk.Style()
        style.theme_use("clam")
        log.info("[GUI] _setup_theme(): theme=clam")

        style.configure(".", background=COLORS["bg"], foreground=COLORS["text"])

        style.configure(
            "Card.TLabelframe",
            background=COLORS["card"],
            foreground=COLORS["text"],
            borderwidth=1,
            relief="solid",
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=COLORS["card"],
            foreground=COLORS["primary"],
            font=("SF Pro Display", 11, "bold"),
        )

        style.configure(
            "TLabel",
            background=COLORS["card"],
            foreground=COLORS["text_dim"],
            font=("SF Pro Text", 10),
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=("SF Pro Display", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text_muted"],
            font=("SF Pro Text", 11),
        )
        style.configure(
            "Section.TLabel",
            background=COLORS["card"],
            foreground=COLORS["primary"],
            font=("SF Pro Display", 10, "bold"),
        )
        style.configure(
            "Progress.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=("SF Mono", 10),
        )

        style.configure(
            "TEntry",
            fieldbackground=COLORS["input_bg"],
            foreground=COLORS["input_fg"],
            insertcolor=COLORS["text"],
            borderwidth=1,
            relief="flat",
            padding=7,
        )

        style.configure(
            "TButton",
            background=COLORS["card"],
            foreground=COLORS["text"],
            borderwidth=0,
            relief="flat",
            padding=(12, 7),
            font=("SF Pro Text", 10),
        )
        style.map(
            "TButton",
            background=[("active", COLORS["primary"]), ("disabled", COLORS["card"])],
            foreground=[("active", "#FFFFFF"), ("disabled", COLORS["text_muted"])],
        )

        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#FFFFFF",
            borderwidth=0,
            relief="flat",
            padding=(20, 10),
            font=("SF Pro Display", 12, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", COLORS["accent_dim"]), ("disabled", COLORS["text_muted"])],
            foreground=[("disabled", COLORS["card"])],
        )

        style.configure(
            "TCheckbutton",
            background=COLORS["card"],
            foreground=COLORS["text"],
            font=("SF Pro Text", 10),
        )
        style.map(
            "TCheckbutton",
            background=[("active", COLORS["card"])],
        )

        style.configure(
            "TCombobox",
            fieldbackground=COLORS["input_bg"],
            background=COLORS["card"],
            foreground=COLORS["input_fg"],
            arrowcolor=COLORS["text_dim"],
            borderwidth=1,
            relief="solid",
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["input_bg"])],
            foreground=[("readonly", COLORS["input_fg"])],
        )

        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor=COLORS["border"],
            background=COLORS["primary"],
            borderwidth=0,
            thickness=8,
        )

        # Higher-contrast, state-aware controls. Explicit disabled colours keep
        # labels readable instead of fading into the dark card background.
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0, tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", background=COLORS["card"], foreground=COLORS["text_dim"],
                        padding=(14, 8), font=("SF Pro Text", 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["primary"]), ("active", COLORS["card_hover"])],
                  foreground=[("selected", "#FFFFFF"), ("active", COLORS["text"])])
        style.configure("TScrollbar", background=COLORS["card_hover"], troughcolor=COLORS["bg"],
                        bordercolor=COLORS["bg"], arrowcolor=COLORS["text_dim"], relief="flat")
        style.map("TScrollbar", background=[("active", COLORS["primary"]), ("pressed", COLORS["primary_dim"])])
        style.configure("TCheckbutton", indicatorcolor=COLORS["input_bg"], focuscolor=COLORS["primary"])
        style.map("TCheckbutton",
                  background=[("active", COLORS["card"])],
                  foreground=[("disabled", "#94A3B8"), ("active", "#FFFFFF")],
                  indicatorcolor=[("selected", COLORS["primary"]), ("pressed", COLORS["primary_dim"])])
        style.configure("TRadiobutton", background=COLORS["card"], foreground=COLORS["text"],
                        indicatorcolor=COLORS["input_bg"], focuscolor=COLORS["primary"])
        style.map("TRadiobutton", foreground=[("disabled", "#94A3B8"), ("active", "#FFFFFF")],
                  indicatorcolor=[("selected", COLORS["primary"]), ("pressed", COLORS["primary_dim"])])
        style.map("TButton",
                  background=[("disabled", COLORS["border"]), ("pressed", COLORS["primary_dim"]),
                              ("active", COLORS["primary"])],
                  foreground=[("disabled", "#CBD5E1"), ("!disabled", "#F8FAFC")])
        style.map("Accent.TButton",
                  background=[("disabled", COLORS["border"]), ("pressed", COLORS["accent_dim"]),
                              ("active", COLORS["accent_dim"])],
                  foreground=[("disabled", "#CBD5E1"), ("!disabled", "#FFFFFF")])

        log.info("[GUI] _setup_theme(): все стили применены")

    # ─── UI ───────────────────────────────────────────────────────────────

    def _create_scrollable_content(self, parent, *, padx=0, pady=0):
        """Создать область с вертикальной прокруткой и шириной по окну.

        Возвращает внутренний ttk.Frame. Используется для длинных настроек,
        а действия запуска остаются закреплёнными вне области прокрутки.
        """
        host = ttk.Frame(parent)
        host.pack(fill=tk.BOTH, expand=True, padx=padx, pady=pady)
        canvas = tk.Canvas(host, background=COLORS["bg"], highlightthickness=0,
                           borderwidth=0, takefocus=False, yscrollincrement=1)
        scrollbar = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.bind("<Configure>", lambda event, c=canvas, wid=window_id: c.itemconfigure(wid, width=event.width))
        if not hasattr(self, "_scroll_canvases"):
            self._scroll_canvases = []
        self._scroll_canvases.append(canvas)
        if not hasattr(self, "_scroll_areas"):
            self._scroll_areas = []
        self._scroll_areas.append((host, canvas))
        if _SCROLL_DEBUG:
            def _dbg_widget(event, _name=str(canvas)):
                log.info(
                    "[SCROLL-DBG] widget-уровень %s: widget=%r x_root=%s y_root=%s delta=%r",
                    _name, getattr(event, "widget", None), getattr(event, "x_root", None),
                    getattr(event, "y_root", None), getattr(event, "delta", None),
                )
                return None

            canvas.bind("<MouseWheel>", _dbg_widget, add="+")
            inner.bind("<MouseWheel>", _dbg_widget, add="+")
        return inner

    def _scroll_area_for_event(self, event):
        """Канва прокручиваемой области под указателем (или None).

        Сначала ищем по цепочке родителей виджета-источника, затем по координатам.
        """
        areas = getattr(self, "_scroll_areas", None)
        if not areas:
            areas = [(canvas.master, canvas) for canvas in getattr(self, "_scroll_canvases", [])]
        # 1) Виджет под курсором: поднимаемся по цепочке родителей до панели.
        widget = getattr(event, "widget", None)
        if widget is not None:
            chain = []
            walk = widget
            while walk is not None:
                chain.append(walk)
                try:
                    walk = walk.master
                except Exception:  # noqa: BLE001
                    break
            for host, canvas in reversed(areas):
                if host in chain:
                    try:
                        if canvas.winfo_ismapped():
                            if _SCROLL_DEBUG:
                                log.info("[SCROLL-DBG] → цепочка виджетов: канва %r", canvas)
                            return canvas
                    except tk.TclError:
                        pass
                    break
        # 2) Координаты указателя: полоса прокрутки и пустые зоны панели.
        for host, canvas in reversed(areas):
            try:
                if not canvas.winfo_ismapped() or not host.winfo_ismapped():
                    continue
                x0, y0 = host.winfo_rootx(), host.winfo_rooty()
                x1, y1 = x0 + host.winfo_width(), y0 + host.winfo_height()
                if not (x0 <= event.x_root <= x1 and y0 <= event.y_root <= y1):
                    continue
                if _SCROLL_DEBUG:
                    log.info("[SCROLL-DBG] → hit-test по координатам: канва %r", canvas)
                return canvas
            except tk.TclError:
                continue
        return None

    def _on_scroll_wheel(self, event):
        """Прокрутка настоящим колёсиком (MouseWheel / Button-4 / Button-5)."""
        if _SCROLL_DEBUG:
            log.info(
                "[SCROLL-DBG] вход: widget=%r x_root=%s y_root=%s delta=%r num=%r",
                getattr(event, "widget", None), getattr(event, "x_root", None),
                getattr(event, "y_root", None), getattr(event, "delta", None),
                getattr(event, "num", None),
            )
        canvas = self._scroll_area_for_event(event)
        if canvas is not None:
            return self._scroll_canvas(canvas, event)
        # Диагностика: колесо дошло до обработчика, но ни одна область не приняла
        # событие — пишем детали, чтобы видеть реальную доставку на macOS.
        try:
            log.info(
                "[SCROLL] колесо не обработано: widget=%r x_root=%s y_root=%s delta=%r num=%r",
                getattr(event, "widget", None), getattr(event, "x_root", None),
                getattr(event, "y_root", None), getattr(event, "delta", None),
                getattr(event, "num", None),
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _unpack_touchpad_delta(raw) -> tuple[int, int]:
        """Tk 9 TouchpadScroll: Δx в старших 16 битах, Δy в младших (знаковые)."""
        value = int(raw) & 0xFFFFFFFF
        dx = value >> 16
        dy = value & 0xFFFF
        if dx >= 0x8000:
            dx -= 0x10000
        if dy >= 0x8000:
            dy -= 0x10000
        return dx, dy

    def _on_touchpad_scroll(self, event):
        """Прокрутка трекпада/Magic Mouse: Tk 9 шлёт <TouchpadScroll>, не <MouseWheel>.

        Δx — старшие 16 бит event.delta, Δy — младшие. Жест генерируется ~60 раз
        в секунду, поэтому прокручиваем на сами дельты (пиксельная точность).
        """
        try:
            dx, dy = self._unpack_touchpad_delta(getattr(event, "delta", 0) or 0)
        except (TypeError, ValueError):
            return None
        if _SCROLL_DEBUG:
            log.info(
                "[SCROLL-DBG] touchpad: widget=%r x_root=%s y_root=%s dx=%s dy=%s",
                getattr(event, "widget", None), getattr(event, "x_root", None),
                getattr(event, "y_root", None), dx, dy,
            )
        if not dy:
            return None
        canvas = self._scroll_area_for_event(event)
        if canvas is None:
            return None
        pixels = max(-120, min(120, -dy))
        try:
            canvas.yview_scroll(pixels, "units")
        except tk.TclError:
            return None
        return "break"

    def _scroll_canvas(self, canvas, event):
        """Шаг прокрутки для канвы; None — событие не про прокрутку."""
        num = getattr(event, "num", None)
        if num in (4, 5):
            pixels = -60 if num == 4 else 60
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if not delta:
                return None
            if sys.platform == "darwin":
                # macOS: delta приходит «строками» (трекпад — мелкие шаги).
                pixels = max(-120, min(120, -delta * 10))
            else:
                pixels = int(-delta / 120 * 60)
                if pixels == 0:
                    pixels = -60 if delta > 0 else 60
                pixels = max(-240, min(240, pixels))
        try:
            canvas.yview_scroll(pixels, "units")
        except tk.TclError:
            return None
        return "break"

    def _build_macos_menu(self) -> None:
        """Стандартная строка меню macOS: «Правка» (⌘C/V/X/A/Z), «Окно».

        На Aqua Tk у текстовых полей нет Command-привязок: копирование, вставка
        и «выделить всё» работают только через меню приложения, как в системных
        программах. Пункты меню шлют виртуальные события текущему виджету.
        """
        if sys.platform != "darwin":
            return

        # Фокус на macOS у каждого окна свой: root.focus_get() может не видеть
        # поля диалогов (Настройки и т.п.). Запоминаем последний сфокусированный
        # виджет приложения — меню «Правка» и ⌘W адресуют действия ему.
        self._focus_widget = getattr(self, "_focus_widget", None)

        def _track_focus(event):
            widget = getattr(event, "widget", None)
            if widget is None or isinstance(widget, (tk.Tk, tk.Toplevel)):
                return
            self._focus_widget = widget

        self.root.bind_all("<FocusIn>", _track_focus, add="+")

        def _menu_virtual(name: str):
            def action():
                widget = self._edit_target_widget()
                if widget is None:
                    return
                with contextlib.suppress(tk.TclError):
                    widget.event_generate(name)
            return action

        menubar = tk.Menu(self.root)

        app_menu = tk.Menu(menubar, tearoff=0)
        app_menu.add_command(
            label="О VideoMaker",
            command=lambda: self._log("VideoMaker: окно управления сборкой видео"),
        )
        app_menu.add_separator()
        app_menu.add_command(label="Выход", accelerator="Command+Q", command=self._on_close)
        menubar.add_cascade(label="VideoMaker", menu=app_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Отменить", accelerator="Command+Z",
                              command=_menu_virtual("<<Undo>>"))
        edit_menu.add_command(label="Повторить", accelerator="Command+Shift+Z",
                              command=_menu_virtual("<<Redo>>"))
        edit_menu.add_separator()
        edit_menu.add_command(label="Вырезать", accelerator="Command+X",
                              command=_menu_virtual("<<Cut>>"))
        edit_menu.add_command(label="Копировать", accelerator="Command+C",
                              command=_menu_virtual("<<Copy>>"))
        edit_menu.add_command(label="Вставить", accelerator="Command+V",
                              command=_menu_virtual("<<Paste>>"))
        edit_menu.add_command(label="Выделить всё", accelerator="Command+A",
                              command=_menu_virtual("<<SelectAll>>"))
        menubar.add_cascade(label="Правка", menu=edit_menu)

        window_menu = tk.Menu(menubar, tearoff=0)
        window_menu.add_command(label="Свернуть", accelerator="Command+M",
                                command=self._mac_minimize)
        window_menu.add_command(label="Закрыть окно", accelerator="Command+W",
                                command=self._mac_close_focused_window)
        menubar.add_cascade(label="Окно", menu=window_menu)

        self._mac_menu_edit = edit_menu
        self._mac_menu_window = window_menu
        self.root.configure(menu=menubar)

    def _edit_target_widget(self):
        """Виджет-адресат для <<Copy>>/<<Paste>>/<<Cut>>/... — из любого окна.

        На macOS фокус у каждого toplevel свой, поэтому root.focus_get() может
        «не видеть» поля диалогов (Настройки и т.п.). Основной источник — последний
        сфокусированный виджет приложения (отслеживается через <FocusIn>).
        """
        widget = getattr(self, "_focus_widget", None)
        if widget is not None:
            try:
                if widget.winfo_exists():
                    return widget
            except tk.TclError:
                pass
        try:
            return self.root.focus_get()
        except (tk.TclError, KeyError):
            return None

    def _mac_close_focused_window(self) -> None:
        """⌘W: закрыть окно с фокусом (диалог) или главное окно."""
        widget = self._edit_target_widget()
        try:
            top = widget.winfo_toplevel() if widget is not None else self.root
        except tk.TclError:
            top = self.root
        if top is self.root:
            self._on_close()
        else:
            with contextlib.suppress(tk.TclError):
                top.destroy()

    def _mac_minimize(self) -> None:
        """⌘M: свернуть главное окно."""
        with contextlib.suppress(tk.TclError):
            self.root.iconify()

    def _build_ui(self) -> None:
        """Построить интерфейс."""
        log.info("[GUI] _build_ui(): начало построения интерфейса")

        # Главный контейнер
        self.main_frame = ttk.Frame(self.root, padding=16)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # === Заголовок ===
        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="ВидеоМейкер", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="Создание видео из аудио + B-roll", style="Subtitle.TLabel").pack(side=tk.LEFT, padx=(12, 0))
        settings_btn = ttk.Button(header, text="⚙", width=3, command=self._open_settings)
        settings_btn.pack(side=tk.RIGHT)

        # === Вкладки ===
        self.notebook = ttk.Notebook(self.main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Вкладка 1: Основные настройки
        self.tab_main = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_main, text="  Основные  ")
        self._build_main_tab(self.tab_main)

        # Вкладка 2: Intro / Middle / Outro
        self.tab_imo = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_imo, text="  Intro / Middle / Outro  ")
        self._build_imo_tab(self.tab_imo)

        self.tab_monitor = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_monitor, text="  Журнал и пульт  ")
        self._build_monitoring_tab(self.tab_monitor)

        log.info("[GUI] _build_ui(): интерфейс построен")

    def _build_main_tab(self, parent) -> None:
        """Вкладка создания: слева настройки со своим скроллом, справа пульт и журнал."""
        # Две панели: настройки (прокручиваются сами) и стационарная панель
        # пульта/журнала, растягивающаяся вместе с окном.
        self.main_panes = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        self.main_panes.pack(fill=tk.BOTH, expand=True)
        self.settings_pane = ttk.Frame(self.main_panes)
        self.monitor_pane = ttk.Frame(self.main_panes)
        self.main_panes.add(self.settings_pane, weight=3)
        self.main_panes.add(self.monitor_pane, weight=2)

        layout = ttk.Frame(self.settings_pane)
        layout.pack(fill=tk.BOTH, expand=True)
        settings_body = self._create_scrollable_content(layout, padx=8, pady=(6, 0))
        # Остальные элементы (создать/отмена, прогресс, «▶ что идёт сейчас») кладём
        # в layout, а не в canvas: основные действия остаются на экране.
        parent = layout

        # Настройки — одна вертикальная колонка: «Название серии» всегда внизу,
        # а не в отдельной правой колонке при широком окне.
        left_col = ttk.Frame(settings_body)
        left_col.pack(fill=tk.X, expand=False, padx=2, pady=6)

        # ─── Левая колонка: все настройки ───────────────────────────────
        # Аудио: одна кнопка — файл или папка (приложение определяет само)
        audio_frame = self._add_section(left_col, "Аудио (файл или папка)")
        self.audio_var = tk.StringVar()
        row_audio = ttk.Frame(audio_frame)
        row_audio.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(row_audio, text="Путь:", width=14).pack(side=tk.LEFT)
        ttk.Entry(row_audio, textvariable=self.audio_var, font=("SF Pro Text", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6)
        )
        ttk.Button(
            row_audio, text="Обзор...", width=10,
            command=self._browse_audio,
        ).pack(side=tk.LEFT)
        ttk.Label(
            audio_frame,
            text="Выберите файл или папку — приложение само определит и покажет в логе",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(2, 0))

        broll_frame = self._add_section(left_col, "B-roll видео (16:9 и 9:16)")
        broll_h_row = ttk.Frame(broll_frame)
        broll_h_row.pack(fill=tk.X, pady=(0, 4))
        self._add_browse_row(broll_h_row, "Горизонтальный:", "broll_h_var", "dir")
        broll_v_row = ttk.Frame(broll_frame)
        broll_v_row.pack(fill=tk.X)
        self._add_browse_row(broll_v_row, "Вертикальный:", "broll_v_var", "dir")
        self.imo_folder_var = tk.StringVar()
        self._add_file_section(
            left_col, "Фон для вертикального видео",
            [("Файл:", "bg_var", "file", [("Изображения/Видео", "*.jpg *.jpeg *.png *.mp4 *.mov")])],
        )
        # r56: BGM — YouTube база; остальные только с папкой, где есть аудио
        bgm_sec = self._add_section(left_col, "BGM для соцсетей")
        ttk.Label(
            bgm_sec,
            text="YouTube — всегда (BGM если в папке есть треки). Остальные: галочка + папка с аудио.",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(0, 4))
        self.bgm_youtube_enable_var = tk.BooleanVar(value=True)
        self.bgm_tiktok_enable_var = tk.BooleanVar(value=False)
        self.bgm_instagram_enable_var = tk.BooleanVar(value=False)
        self.bgm_rutube_enable_var = tk.BooleanVar(value=False)
        self.bgm_vk_enable_var = tk.BooleanVar(value=False)
        self.bgm_facebook_enable_var = tk.BooleanVar(value=False)
        self.bgm_other_enable_var = tk.BooleanVar(value=False)
        self.bgm_youtube_folder_var = tk.StringVar()
        self.bgm_tiktok_folder_var = tk.StringVar()
        self.bgm_instagram_folder_var = tk.StringVar()
        self.bgm_rutube_folder_var = tk.StringVar()
        self.bgm_vk_folder_var = tk.StringVar()
        self.bgm_facebook_folder_var = tk.StringVar()
        self.bgm_other_folder_var = tk.StringVar()
        self.bgm_var = self.bgm_youtube_folder_var  # legacy
        self._bgm_platform_rows = []
        for _en, _lab, _fvar, _is_base in (
            (self.bgm_youtube_enable_var, "BGM YouTube", self.bgm_youtube_folder_var, True),
            (self.bgm_tiktok_enable_var, "TikTok", self.bgm_tiktok_folder_var, False),
            (self.bgm_instagram_enable_var, "Instagram", self.bgm_instagram_folder_var, False),
            (self.bgm_rutube_enable_var, "RuTube", self.bgm_rutube_folder_var, False),
            (self.bgm_vk_enable_var, "VK", self.bgm_vk_folder_var, False),
            (self.bgm_facebook_enable_var, "Facebook", self.bgm_facebook_folder_var, False),
            (self.bgm_other_enable_var, "Другое", self.bgm_other_folder_var, False),
        ):
            row = ttk.Frame(bgm_sec)
            row.pack(fill=tk.X, pady=2)
            cb = ttk.Checkbutton(row, text=_lab, variable=_en, width=14)
            cb.pack(side=tk.LEFT)
            ent = ttk.Entry(row, textvariable=_fvar, font=("SF Pro Text", 10))
            ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            ttk.Button(
                row, text="…", width=3,
                command=lambda v=_fvar, e=_en, base=_is_base: self._browse_bgm_folder(v, e, base),
            ).pack(side=tk.LEFT)
            self._bgm_platform_rows.append((_en, _fvar, _is_base, cb))
        self._add_file_section(
            left_col, "Папка вывода",
            [("Папка:", "output_var", "dir", [])],
        )

        audio_settings = self._add_section(left_col, "Настройки аудио")
        self.voice_enhance_var = tk.BooleanVar(value=True)
        self.add_bgm_var = tk.BooleanVar(value=True)
        self.intro_gemini_var = tk.BooleanVar(value=True)
        self.keep_temp_var = tk.BooleanVar(value=False)
        self.remove_long_pauses_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "remove_long_pauses", False))
        )
        ttk.Checkbutton(audio_settings, text="Усилить голос", variable=self.voice_enhance_var).pack(anchor="w", pady=2)
        # r56: BGM включается галочками соцсетей выше
        ttk.Checkbutton(audio_settings, text="Интро: Gemini выбирает", variable=self.intro_gemini_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            audio_settings,
            text="Сокращать длинные паузы между словами (необязательно)",
            variable=self.remove_long_pauses_var,
        ).pack(anchor="w", pady=2)
        ttk.Label(
            audio_settings,
            text="По умолчанию выключено. Короткие паузы сохраняются; после пунктуации остаётся больше времени.",
            wraplength=410,
            justify="left",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", padx=(20, 0), pady=(0, 4))
        self.align_speech_rate_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "align_speech_rate", False))
        )
        ttk.Checkbutton(
            audio_settings,
            text="Выравнивать темп речи (скорость — ползунком ниже)",
            variable=self.align_speech_rate_var,
        ).pack(anchor="w", pady=2)
        ttk.Label(
            audio_settings,
            text="По умолчанию выключено (как в прежних версиях): аудио идёт без обрезки тишины и без ускорения/замедления. Включите, чтобы подтягивать темп речи к выбранной скорости.",
            wraplength=410,
            justify="left",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", padx=(20, 0), pady=(0, 2))

        # r57-api.22: ползунок скорости речи — цель в словах/мин или коэффициент темпа.
        self.speech_rate_mode_var = tk.StringVar(
            value=speech_rate_mode_display(getattr(self.settings, "speech_rate_mode", "wpm"))
        )
        self.speech_rate_target_wpm_var = tk.DoubleVar(
            value=_float_setting(getattr(self.settings, "speech_rate_target_wpm", 120.0), 120.0)
        )
        self.speech_rate_coefficient_var = tk.DoubleVar(
            value=_float_setting(getattr(self.settings, "speech_rate_coefficient", 1.0), 1.0)
        )
        sr_row = ttk.Frame(audio_settings)
        sr_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(sr_row, text="Скорость речи:").pack(side=tk.LEFT)
        self.speech_rate_mode_combo = ttk.Combobox(
            sr_row,
            textvariable=self.speech_rate_mode_var,
            state="readonly",
            width=18,
            values=["По словам/мин", "По коэффициенту"],
        )
        self.speech_rate_mode_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.speech_rate_slider = tk.Scale(
            audio_settings,
            orient=tk.HORIZONTAL,
            showvalue=False,
            length=380,
            from_=100, to=200, resolution=1,
            variable=self.speech_rate_target_wpm_var,
        )
        self.speech_rate_slider.pack(fill=tk.X, padx=(20, 0), pady=(2, 0))
        self.speech_rate_hint = ttk.Label(
            audio_settings,
            text="",
            wraplength=410,
            justify="left",
            font=("SF Pro Text", 9),
        )
        self.speech_rate_hint.pack(anchor="w", padx=(20, 0), pady=(0, 4))
        self.speech_rate_mode_combo.bind("<<ComboboxSelected>>", self._on_speech_rate_mode_change)
        self.speech_rate_target_wpm_var.trace_add("write", lambda *a: self._update_speech_rate_hint())
        self.speech_rate_coefficient_var.trace_add("write", lambda *a: self._update_speech_rate_hint())
        self._on_speech_rate_mode_change()

        # r57-api.24: вырезание повторов/лишних слов по итоговому тексту Gemini.
        self.cut_extra_words_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "cut_extra_words", False))
        )
        ttk.Checkbutton(
            audio_settings,
            text="Вырезать повторы и лишние слова (по тексту Gemini)",
            variable=self.cut_extra_words_var,
        ).pack(anchor="w", pady=2)
        ttk.Label(
            audio_settings,
            text="По умолчанию выключено. Сравнивает итоговый текст Gemini с расшифровкой: вырезает только слова, которых Gemini не оставил (повторы, оговорки). Исправления распознавания не трогаются.",
            wraplength=410,
            justify="left",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", padx=(20, 0), pady=(0, 4))

        # r57-api.25: отдельные настройки шортов — скорость и вырезка независимо от длинного.
        ttk.Separator(audio_settings, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(6, 4))
        ttk.Label(audio_settings, text="Шорты (отдельно от длинного):", style="Section.TLabel").pack(anchor="w")
        self.s_speech_rate_mode_var = tk.StringVar(
            value=short_speech_rate_mode_display(getattr(self.settings, "s_speech_rate_mode", "inherit"))
        )
        self.s_speech_rate_target_wpm_var = tk.DoubleVar(
            value=_float_setting(getattr(self.settings, "s_speech_rate_target_wpm", 120.0), 120.0)
        )
        self.s_speech_rate_coefficient_var = tk.DoubleVar(
            value=_float_setting(getattr(self.settings, "s_speech_rate_coefficient", 1.0), 1.0)
        )
        s_sr_row = ttk.Frame(audio_settings)
        s_sr_row.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(s_sr_row, text="Скорость шортов:").pack(side=tk.LEFT)
        self.s_speech_rate_mode_combo = ttk.Combobox(
            s_sr_row,
            textvariable=self.s_speech_rate_mode_var,
            state="readonly",
            width=18,
            values=["Как на длинном", "По словам/мин", "По коэффициенту"],
        )
        self.s_speech_rate_mode_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.s_speech_rate_slider = tk.Scale(
            audio_settings,
            orient=tk.HORIZONTAL,
            showvalue=False,
            length=380,
            from_=100, to=200, resolution=1,
            variable=self.s_speech_rate_target_wpm_var,
        )
        self.s_speech_rate_slider.pack(fill=tk.X, padx=(20, 0), pady=(2, 0))
        self.s_speech_rate_mode_combo.bind("<<ComboboxSelected>>", self._on_short_speech_mode_change)
        self._on_short_speech_mode_change()
        self.s_cut_extra_words_var = tk.BooleanVar(
            value=bool(getattr(self.settings, "s_cut_extra_words", False))
        )
        ttk.Checkbutton(
            audio_settings,
            text="Вырезать повторы и лишние слова (на шортах)",
            variable=self.s_cut_extra_words_var,
        ).pack(anchor="w", pady=2)
        ttk.Label(
            audio_settings,
            text="Если вырезка включена на длинном — шорты её наследуют; галка добавляет вырезку на шортах, когда на длинном она выключена.",
            wraplength=410,
            justify="left",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", padx=(20, 0), pady=(0, 4))
        ttk.Checkbutton(audio_settings, text="Сохранять временные файлы", variable=self.keep_temp_var).pack(anchor="w", pady=2)
        self.prevent_sleep_var = tk.BooleanVar(value=True)
        self.shutdown_when_done_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(audio_settings, text="Не засыпать во время обработки", variable=self.prevent_sleep_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(audio_settings, text="Выключить компьютер по завершению", variable=self.shutdown_when_done_var).pack(anchor="w", pady=2)

        self.whisper_model_var = tk.StringVar(value=getattr(self.settings, "whisper_model", "large-v3") or "large-v3")
        self.whisper_lang_var = tk.StringVar(value="ru")
        self.whisper_dev_var = tk.StringVar(value="cpu")
        self.whisper_comp_var = tk.StringVar(value="int8")
        self.whisperx_status_var = tk.StringVar(value="Автопоиск...")

        checks_frame = self._add_section(left_col, "Этапы обработки")
        checks_frame.configure(padding=8)
        self._imo_checkbuttons = {}
        for label, prefix, defaults in [
            ("16:9 Гориз.", "h", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
            ("9:16 Вертик.", "v", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
            ("Shorts", "s", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": False, "strong": False}),
        ]:
            row = ttk.Frame(checks_frame)
            row.pack(fill=tk.X, pady=(0, 6))
            ttk.Label(row, text=label, width=12, style="Section.TLabel").pack(side=tk.LEFT)
            for key, default in defaults.items():
                var = tk.BooleanVar(value=default)
                setattr(self, f"{prefix}_{key}", var)
                cb = ttk.Checkbutton(row, text=key.capitalize(), variable=var)
                cb.pack(side=tk.LEFT, padx=2)
                if key in ("intro", "middle", "outro"):
                    self._imo_checkbuttons[(prefix, key)] = cb
                    cb.configure(state=tk.DISABLED)

        style_frame = self._add_section(left_col, "Стили субтитров / хуков")
        style_frame.configure(padding=8)
        row_c = ttk.Frame(style_frame)
        row_c.pack(fill=tk.X, pady=2)
        ttk.Label(row_c, text="Субтитры:", width=12).pack(side=tk.LEFT)
        self.caption_style_var = tk.StringVar(value="clean_pro")
        ttk.Combobox(
            row_c, textvariable=self.caption_style_var, state="readonly", width=24,
            values=[
                "clean_pro", "auto_aisie", "hormozi", "hormozi_green",
                "tiktok_box", "bold_pop", "cliffhanger",
            ],
        ).pack(side=tk.LEFT)
        self.caption_desc_var = tk.StringVar()
        ttk.Label(
            style_frame, textvariable=self.caption_desc_var,
            font=("SF Pro Text", 9), wraplength=420, justify="left",
        ).pack(anchor="w", pady=(2, 6))
        row_h = ttk.Frame(style_frame)
        row_h.pack(fill=tk.X, pady=2)
        ttk.Label(row_h, text="Хуки:", width=12).pack(side=tk.LEFT)
        self.hook_style_var = tk.StringVar(value="auto_aisie")
        ttk.Combobox(
            row_h, textvariable=self.hook_style_var, state="readonly", width=24,
            values=[
                "auto_aisie", "marker",
            ],
        ).pack(side=tk.LEFT)
        self.hook_desc_var = tk.StringVar()
        ttk.Label(
            style_frame, textvariable=self.hook_desc_var,
            font=("SF Pro Text", 9), wraplength=420, justify="left",
        ).pack(anchor="w", pady=(2, 0))
        self.caption_style_var.trace_add("write", lambda *_: self._update_style_desc())
        self.hook_style_var.trace_add("write", lambda *_: self._update_style_desc())
        self._update_style_desc()

        lufs_frame = self._add_section(left_col, "Громкость (LUFS)")
        lufs_frame.configure(padding=8)
        ttk.Label(lufs_frame, text="Целевой LUFS для микса с BGM:").pack(anchor="w")
        lufs_row = ttk.Frame(lufs_frame)
        lufs_row.pack(fill=tk.X, pady=(3, 0))
        self.target_lufs_var = tk.StringVar(value="-14.0")
        ttk.Entry(lufs_row, textvariable=self.target_lufs_var, width=8, font=("SF Pro Text", 11)).pack(side=tk.LEFT)
        ttk.Label(lufs_row, text="диапазон −36 … −5; обычно −14").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            lufs_frame,
            text="Voice-only выход не проходит отдельную нормализацию — без дополнительного медленного прохода.",
            wraplength=410, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # ─── Поле имени серии (компактное, не растягивается на весь экран) ───
        series_frame = self._add_section(left_col, "Название серии / папка результата")
        series_frame.configure(padding=8)
        self.series_var = tk.StringVar()
        self.series_entry = tk.Text(
            series_frame,
            wrap=tk.WORD,
            font=("SF Pro Text", 13),
            height=5,
            bg=COLORS["input_bg"],
            fg="#F0F0F0",
            insertbackground="#FFFFFF",
            selectbackground="#3A7AFE",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=12,
            highlightthickness=1,
            highlightbackground="#333333",
            highlightcolor="#3A7AFE",
        )
        self.series_entry.pack(fill=tk.X, expand=False, pady=(4, 0))
        ttk.Label(
            series_frame,
            text="Имя папки результата = это название (или имя аудио)",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(6, 0))

        # === Кнопка запуска ===
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(12, 8))
        self.start_btn = ttk.Button(
            btn_frame, text="  СОЗДАТЬ ВИДЕО  ", style="Accent.TButton", command=self._start
        )
        self.start_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(
            btn_frame, text="  ОТМЕНА  ", style="TButton", command=self._cancel_pipeline, state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        # === Прогресс ===
        progress_frame = ttk.Frame(parent)
        progress_frame.pack(fill=tk.X, pady=(0, 4))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100,
            style="Custom.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill=tk.X, expand=True)

        time_row = ttk.Frame(parent)
        time_row.pack(fill=tk.X, pady=(0, 8))
        self.progress_label = ttk.Label(time_row, text="0%", style="Progress.TLabel")
        self.progress_label.pack(side=tk.LEFT)
        self.stage_label = ttk.Label(time_row, text="", style="Progress.TLabel")
        self.stage_label.pack(side=tk.LEFT, padx=(12, 0))
        self.time_label = ttk.Label(time_row, text="", style="Progress.TLabel")
        self.time_label.pack(side=tk.RIGHT)

        # Строка «что сейчас происходит» — последнее действие пайплайна
        self.activity_label = ttk.Label(
            parent, text="", style="Progress.TLabel", anchor="w", justify="left",
        )
        self.activity_label.pack(fill=tk.X, pady=(0, 6))
        self._activity_text = ""
        self._pipeline_t0 = 0.0

        # ─── Правая панель: пульт + живой журнал (без своего внешнего скролла) ───
        right = ttk.Frame(self.monitor_pane, padding=12)
        right.pack(fill=tk.BOTH, expand=True)
        self._build_api_block(
            right, side=tk.TOP, pady=(0, 10), label_attr="api_label_main",
            cb_attr="api_lan_cb_main", btn_attr="api_open_btn_main",
        )
        self._build_log_block(right, attr="log_text_main", width=46, height=16)

        # Стартовая позиция шторки ~60/40; дальше её двигает пользователь/окно.
        self._sash_placed = False
        self.root.after_idle(self._place_main_sash)

    def _build_monitoring_tab(self, parent) -> None:
        """Журнал и сетевой пульт на отдельной вкладке, без обрезания снизу."""
        layout = ttk.Frame(parent, padding=12)
        layout.pack(fill=tk.BOTH, expand=True)
        # Пульт закреплён снизу (как и раньше), журнал растягивается выше него.
        self._build_api_block(
            layout, side=tk.BOTTOM, pady=(12, 0), label_attr="api_label",
            cb_attr="api_lan_cb", btn_attr="api_open_btn",
        )
        self._build_log_block(layout, attr="log_text", height=16)

    def _place_main_sash(self) -> None:
        """Первичная позиция шторки главной вкладки (~60/40)."""
        if getattr(self, "_sash_placed", False):
            return
        try:
            width = int(self.main_panes.winfo_width())
            if width <= 200:
                self.root.after(50, self._place_main_sash)
                return
            position = min(max(420, int(width * 0.6)), max(420, width - 320))
            self.main_panes.sashpos(0, position)
            self._sash_placed = True
        except tk.TclError:
            pass

    def _build_api_block(self, parent, *, side, pady, label_attr: str, cb_attr: str, btn_attr: str):
        """Блок пульта. Копии (вкладка + правая панель) делят галочку и обработчики."""
        if not hasattr(self, "api_lan_var"):
            self.api_lan_var = tk.BooleanVar(value=False)
            self._api_labels: list[ttk.Label] = []
            self._api_urls: dict = {}
            self._api_lan_setter = None
        frame = ttk.LabelFrame(
            parent, text="Пульт (телефон / Orchestrator)",
            style="Card.TLabelframe", padding=10,
        )
        frame.pack(side=side, fill=tk.X, pady=pady)
        label = ttk.Label(
            frame, text="пульт поднимается…", style="Progress.TLabel",
            anchor="w", justify="left", wraplength=880,
        )
        label.pack(fill=tk.X)
        setattr(self, label_attr, label)
        self._api_labels.append(label)
        frame.bind(
            "<Configure>",
            lambda event, target=label: target.configure(wraplength=max(280, event.width - 28)),
            add="+",
        )
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(8, 0))
        check = ttk.Checkbutton(
            row, text="Доступ из локальной сети (телефон)",
            variable=self.api_lan_var, command=self._on_api_lan_toggle,
        )
        check.pack(side=tk.LEFT, anchor="w")
        setattr(self, cb_attr, check)
        button = ttk.Button(
            row, text="Открыть пульт", command=self._open_api_page,
            style="Accent.TButton",
        )
        button.pack(side=tk.RIGHT, padx=(12, 0))
        setattr(self, btn_attr, button)
        return frame

    def _build_log_block(self, parent, *, attr: str, height: int = 16, width: int | None = None):
        """Журнал обработки; все копии виджета получают один и тот же поток лога."""
        frame = ttk.LabelFrame(
            parent, text="Журнал обработки", style="Card.TLabelframe", padding=6,
        )
        frame.pack(fill=tk.BOTH, expand=True)
        text_kwargs: dict = dict(
            height=height, state=tk.DISABLED, wrap=tk.WORD,
            bg=COLORS["log_bg"], fg=COLORS["text_dim"],
            insertbackground=COLORS["text"], selectbackground=COLORS["primary"],
            font=("SF Mono", 10), borderwidth=0, highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
            padx=10, pady=10,
        )
        if width is not None:
            text_kwargs["width"] = width
        widget = tk.Text(frame, **text_kwargs)
        scrollbar = ttk.Scrollbar(frame, command=widget.yview)
        widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        widget.pack(fill=tk.BOTH, expand=True)
        setattr(self, attr, widget)
        if not hasattr(self, "_log_widgets"):
            self._log_widgets: list[tk.Text] = []
        self._log_widgets.append(widget)
        return frame

    # ─── Пульт: отображение адресов и переключатель сети ──────────────────
    def _show_api_info(self, urls: dict, lan_setter=None) -> None:
        """Заполнить блок пульта (вызывает video_maker.api при старте)."""
        self._api_urls = dict(urls or {})
        if lan_setter is not None:
            self._api_lan_setter = lan_setter
        local = self._api_urls.get("local", "")
        net = self._api_urls.get("network", "")
        text = f"Локально: {local}"
        if net:
            text += f"\nС телефона: {net}"
        else:
            text += "\nС телефона: включите «Доступ из локальной сети»"
        if not self._api_urls.get("enabled", True):
            text = "Пульт выключен (--no-api или enabled=false в ~/video_maker/api.json)"
            enabled = False
        else:
            enabled = True
        with contextlib.suppress(tk.TclError):
            # виджеты-копии могли быть уже уничтожены при закрытии окна
            for label in getattr(self, "_api_labels", ()):
                label.configure(text=text)
            if hasattr(self, "api_lan_var"):
                self.api_lan_var.set(bool(self._api_urls.get("lan")))
            self._set_api_controls_enabled(enabled)

    def _set_api_controls_enabled(self, enabled: bool) -> None:
        """Когда пульт выключен, галочка сети и «Открыть пульт» бессмысленны."""
        state = tk.NORMAL if enabled else tk.DISABLED
        for attr in ("api_lan_cb", "api_lan_cb_main", "api_open_btn", "api_open_btn_main"):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            with contextlib.suppress(tk.TclError):
                widget.configure(state=state)

    def _on_api_lan_toggle(self) -> None:
        """Переключить доступ из сети (сохранение + перезапуск сервера в потоке)."""
        setter = getattr(self, "_api_lan_setter", None)
        if setter is None:
            return
        want = bool(self.api_lan_var.get())
        # Сразу видно, что нажатие принято: перезапуск сервера занимает доли
        # секунды, но подпись успевает показать, что происходит.
        with contextlib.suppress(tk.TclError):
            for label in getattr(self, "_api_labels", ()):
                label.configure(
                    text=f"Переключаю доступ из сети: {'включаю' if want else 'выключаю'}…")

        def _show(urls) -> None:
            if urls:
                self._show_api_info(urls, setter)
            else:
                # не получилось — вернуть галочку в настоящее состояние сервера
                self._show_api_info(getattr(self, "_api_urls", {}), None)

        def _work():
            try:
                urls = setter(want)
            except Exception as exc:  # noqa: BLE001
                log.warning("[API] не удалось переключить доступ из сети: %s", exc)
                urls = None
            bridge = getattr(self, "_api_bridge", None)
            if bridge is not None:
                # Штатный путь: вызов исполнится в потоке Tk. Через root.after
                # из чужого потока Tk молча теряет вызов (macOS).
                with contextlib.suppress(Exception):
                    bridge.call_async(lambda: _show(urls))
                    return
            with contextlib.suppress(RuntimeError):
                self.root.after(0, lambda: _show(urls))

        threading.Thread(target=_work, daemon=True).start()

    def _open_api_page(self) -> None:
        """Открыть страницу пульта в браузере."""
        url = (self._api_urls or {}).get("network") or (self._api_urls or {}).get("local")
        if not url:
            return
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("[API] не удалось открыть браузер: %s", exc)

    def _build_imo_tab(self, parent) -> None:
        """Вкладка Intro / Middle / Outro для каждого формата с прокруткой."""
        scroll = self._create_scrollable_content(parent, padx=8, pady=8)

        for section_title, prefix in [
            ("16:9 Горизонтальное видео", "h"),
            ("9:16 Вертикальное видео", "v"),
            ("Shorts", "s"),
        ]:
            section = self._add_section(scroll, section_title)

            # Intro
            intro_frame = ttk.Frame(section)
            intro_frame.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(intro_frame, text="Intro:", style="Section.TLabel", width=8).pack(side=tk.LEFT)
            intro_path_var = tk.StringVar()
            setattr(self, f"{prefix}_intro_path", intro_path_var)
            ttk.Entry(intro_frame, textvariable=intro_path_var, font=("SF Pro Text", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ttk.Button(intro_frame, text="...", width=3, command=lambda v=intro_path_var: self._browse_file(v, "", [("Видео/картинка", "*.mp4 *.mov *.jpg *.jpeg *.png *.webp")])).pack(side=tk.LEFT)

            intro_dur_frame = ttk.Frame(section)
            intro_dur_frame.pack(fill=tk.X, pady=(0, 4))
            ttk.Label(intro_dur_frame, text="Длительность:", width=12).pack(side=tk.LEFT)
            intro_dur_var = tk.StringVar(value="3")
            setattr(self, f"{prefix}_intro_duration", intro_dur_var)
            ttk.Entry(intro_dur_frame, textvariable=intro_dur_var, width=6, font=("SF Pro Text", 10)).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(intro_dur_frame, text="сек").pack(side=tk.LEFT)

            intro_pos_var = tk.StringVar(value="start")
            setattr(self, f"{prefix}_intro_position", intro_pos_var)
            ttk.Radiobutton(intro_dur_frame, text="В начале", variable=intro_pos_var, value="start").pack(side=tk.LEFT, padx=(12, 4))
            ttk.Radiobutton(intro_dur_frame, text="Время:", variable=intro_pos_var, value="custom").pack(side=tk.LEFT, padx=(0, 4))
            intro_custom_var = tk.StringVar(value="0")
            setattr(self, f"{prefix}_intro_custom_time", intro_custom_var)
            ttk.Entry(intro_dur_frame, textvariable=intro_custom_var, width=6, font=("SF Pro Text", 10)).pack(side=tk.LEFT)
            ttk.Label(intro_dur_frame, text="сек").pack(side=tk.LEFT)

            # Middle
            mid_frame = ttk.Frame(section)
            mid_frame.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(mid_frame, text="Middle:", style="Section.TLabel", width=8).pack(side=tk.LEFT)
            mid_path_var = tk.StringVar()
            setattr(self, f"{prefix}_mid_path", mid_path_var)
            ttk.Entry(mid_frame, textvariable=mid_path_var, font=("SF Pro Text", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ttk.Button(mid_frame, text="...", width=3, command=lambda v=mid_path_var: self._browse_file(v, "", [("Видео/картинка", "*.mp4 *.mov *.jpg *.jpeg *.png *.webp")])).pack(side=tk.LEFT)

            mid_pos_var = tk.StringVar(value="middle")
            setattr(self, f"{prefix}_mid_position", mid_pos_var)
            mid_pos_frame = ttk.Frame(section)
            mid_pos_frame.pack(fill=tk.X, pady=(0, 4))
            ttk.Label(mid_pos_frame, text="Позиция:", width=12).pack(side=tk.LEFT)
            ttk.Radiobutton(mid_pos_frame, text="По центру", variable=mid_pos_var, value="middle").pack(side=tk.LEFT, padx=(0, 4))
            ttk.Radiobutton(mid_pos_frame, text="Время:", variable=mid_pos_var, value="custom").pack(side=tk.LEFT, padx=(0, 4))
            mid_custom_var = tk.StringVar(value="0")
            setattr(self, f"{prefix}_mid_custom_time", mid_custom_var)
            ttk.Entry(mid_pos_frame, textvariable=mid_custom_var, width=6, font=("SF Pro Text", 10)).pack(side=tk.LEFT)
            ttk.Label(mid_pos_frame, text="сек").pack(side=tk.LEFT)

            # Outro
            outro_frame = ttk.Frame(section)
            outro_frame.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(outro_frame, text="Outro:", style="Section.TLabel", width=8).pack(side=tk.LEFT)
            outro_path_var = tk.StringVar()
            setattr(self, f"{prefix}_outro_path", outro_path_var)
            ttk.Entry(outro_frame, textvariable=outro_path_var, font=("SF Pro Text", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
            ttk.Button(outro_frame, text="...", width=3, command=lambda v=outro_path_var: self._browse_file(v, "", [("Видео/картинка", "*.mp4 *.mov *.jpg *.jpeg *.png *.webp")])).pack(side=tk.LEFT)

            outro_dur_frame = ttk.Frame(section)
            outro_dur_frame.pack(fill=tk.X, pady=(0, 4))
            ttk.Label(outro_dur_frame, text="Длительность:", width=12).pack(side=tk.LEFT)
            outro_dur_var = tk.StringVar(value="3")
            setattr(self, f"{prefix}_outro_duration", outro_dur_var)
            ttk.Entry(outro_dur_frame, textvariable=outro_dur_var, width=6, font=("SF Pro Text", 10)).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(outro_dur_frame, text="сек").pack(side=tk.LEFT)

            outro_pos_var = tk.StringVar(value="end")
            setattr(self, f"{prefix}_outro_position", outro_pos_var)
            ttk.Radiobutton(outro_dur_frame, text="В конце", variable=outro_pos_var, value="end").pack(side=tk.LEFT, padx=(12, 4))
            ttk.Radiobutton(outro_dur_frame, text="Время:", variable=outro_pos_var, value="custom").pack(side=tk.LEFT, padx=(0, 4))
            outro_custom_var = tk.StringVar(value="0")
            setattr(self, f"{prefix}_outro_custom_time", outro_custom_var)
            ttk.Entry(outro_dur_frame, textvariable=outro_custom_var, width=6, font=("SF Pro Text", 10)).pack(side=tk.LEFT)
            ttk.Label(outro_dur_frame, text="сек").pack(side=tk.LEFT)


        covers = self._add_section(scroll, "Обложки")
        row1 = ttk.Frame(covers)
        row1.pack(fill=tk.X, pady=(0, 4))
        if not hasattr(self, "cover_h_var"):
            self.cover_h_var = tk.StringVar()
        if not hasattr(self, "cover_v_var"):
            self.cover_v_var = tk.StringVar()
        self._add_browse_row(row1, "Горизонтальная:", "cover_h_var", "file", [("Изображения", "*.jpg *.jpeg *.png")])
        row2 = ttk.Frame(covers)
        row2.pack(fill=tk.X)
        self._add_browse_row(row2, "Вертикальная:", "cover_v_var", "file", [("Изображения", "*.jpg *.jpeg *.png")])
        if not hasattr(self, "shorts_cover_var"):
            self.shorts_cover_var = tk.StringVar()
        row3 = ttk.Frame(covers)
        row3.pack(fill=tk.X, pady=(4, 0))
        self._add_browse_row(
            row3,
            "Фон Shorts:",
            "shorts_cover_var",
            "file",
            [("Изображения", "*.jpg *.jpeg *.png *.webp")],
        )
        ttk.Label(
            covers,
            text="На фон наложится hook каждого short (как в начале ролика) → short_XXX_cover.jpg",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(4, 0))

    # ─── Хелперы ──────────────────────────────────────────────────────────

    def _add_section(self, parent, title: str, expand: bool = False) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=8)
        if expand:
            frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        else:
            frame.pack(fill=tk.X, pady=(0, 8))
        return frame

    def _add_file_section(self, parent, title: str, fields: list) -> None:
        frame = self._add_section(parent, title)
        for i, (label_text, var_name, kind, filetypes) in enumerate(fields):
            row = ttk.Frame(frame)
            row.pack(fill=tk.X, pady=(0, 4) if i < len(fields) - 1 else 0)
            self._add_browse_row(row, label_text, var_name, kind, filetypes)

    def _add_browse_row(self, parent, label_text: str, var_name: str, kind: str, filetypes=None) -> None:
        var = tk.StringVar()
        setattr(self, var_name, var)

        ttk.Label(parent, text=label_text, width=18).pack(side=tk.LEFT)
        ttk.Entry(parent, textvariable=var, font=("SF Pro Text", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )

        if kind == "dir":
            cmd = lambda v=var, n=var_name: self._browse_dir(v, n)
        else:
            cmd = lambda v=var, n=var_name, ft=filetypes: self._browse_file(v, n, ft)
        ttk.Button(parent, text="Обзор...", command=cmd).pack(side=tk.LEFT)

    def _browse_initialdir(self, current_value: str = "") -> str:
        """Каталог для открытия диалога: текущее поле → его ближайшая существующая
        папка → последняя выбранная → домашний каталог."""
        def usable(path: str) -> str:
            path = (path or "").strip()
            if not path:
                return ""
            if os.path.isdir(path):
                return path
            parent = os.path.dirname(path)
            return parent if os.path.isdir(parent) else ""

        value = (current_value or "").strip()
        candidate = usable(value)
        if candidate:
            return candidate
        if value:
            base = value if os.path.isdir(value) else os.path.dirname(value)
            deepest = _deepest_existing(base or value)
            if deepest:
                return deepest
        return usable(getattr(self, "_last_browse_dir", "")) or os.path.expanduser("~")

    @staticmethod
    def _value_of(var) -> str:
        try:
            return (var.get() or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    def _remember_browse_dir(self, path: str) -> None:
        """Запомнить выбранный путь, чтобы следующий диалог открылся там же."""
        path = (path or "").strip()
        if not path:
            return
        self._last_browse_dir = path if os.path.isdir(path) else os.path.dirname(path)

    def _browse_dir(self, var: tk.StringVar, name: str) -> None:
        log.info(f"[GUI] Диалог выбора папки: {name}")
        initial_dir = self._browse_initialdir(self._value_of(var))

        def done(path: str) -> None:
            log.info(f"[GUI] Выбрана папка: {path or '(пусто)'}")
            if not path:
                return
            self._remember_browse_dir(path)
            var.set(path.strip())
            log.info(f"[GUI] {name} = {path}")
            self._save_settings()

        self._choose_folder(initial_dir, f"Выберите папку: {name}", done)

    def _browse_file(self, var: tk.StringVar, name: str, filetypes=None) -> None:
        log.info(f"[GUI] Диалог выбора файла: {name}")
        types = [("Все файлы", "*.*")]
        if filetypes:
            types = filetypes + types
        initial_dir = self._browse_initialdir(self._value_of(var))

        def done(path: str) -> None:
            log.info(f"[GUI] Выбран файл: {path or '(пусто)'}")
            if not path:
                return
            self._remember_browse_dir(path)
            var.set(path.strip())
            log.info(f"[GUI] {name} = {path}")
            # Сначала sync (галочка + bool=True), потом save —
            # иначе в JSON уходил bool=False от старого disabled.
            self._sync_imo_checkboxes()
            self._save_settings()

        self._choose_file(initial_dir, f"Выберите файл: {name}", done, filetypes=types)

    # ─── Системные диалоги выбора (macOS: choose folder/file через osascript) ──
    def _choose_folder(self, initial_dir: str, title: str, on_done) -> None:
        self._choose_path_native("folder", initial_dir, title, on_done)

    def _choose_file(self, initial_dir: str, title: str, on_done, filetypes=None) -> None:
        self._choose_path_native("file", initial_dir, title, on_done, filetypes=filetypes)

    def _choose_path_native(self, kind: str, initial_dir: str, title: str, on_done,
                            filetypes=None) -> None:
        """Системный диалог macOS со стартовой папкой.

        Tk 9 на этом Mac игнорирует initialdir у собственного диалога, поэтому
        используем родной диалог через osascript: он всегда открывается в нужной
        папке и не блокирует цикл Tk, пока открыт.
        """
        if sys.platform != "darwin":
            self._choose_path_tk(kind, initial_dir, title, on_done, filetypes=filetypes)
            return
        script = self._osascript_choose_script(kind, initial_dir, title)
        try:
            import subprocess
            proc = subprocess.Popen(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except OSError as exc:
            log.warning("[GUI] системный диалог не запустился (%s); откат на Tk", exc)
            self._choose_path_tk(kind, initial_dir, title, on_done, filetypes=filetypes)
            return
        self._poll_osascript(proc, on_done)

    @staticmethod
    def _osascript_choose_script(kind: str, initial_dir: str, title: str) -> str:
        def esc(text: str) -> str:
            return text.replace("\\", "\\\\").replace('"', '\\"')

        what = "folder" if kind == "folder" else "file"
        line = f'choose {what} with prompt "{esc(title or "Выберите путь")}"'
        if (initial_dir or "").strip():
            line += f' default location (POSIX file "{esc(initial_dir.strip())}")'
        return f"set theChoice to {line}\nreturn POSIX path of theChoice"

    def _poll_osascript(self, proc, on_done, attempts: int = 0) -> None:
        if getattr(self, "_closing", False):
            with contextlib.suppress(Exception):
                proc.kill()
            return
        rc = proc.poll()
        if rc is None:
            if attempts > 3600:  # диалог оставлен открытым надолго
                with contextlib.suppress(Exception):
                    proc.kill()
                on_done("")
                return
            with contextlib.suppress(tk.TclError, RuntimeError):
                self.root.after(200, lambda: self._poll_osascript(proc, on_done, attempts + 1))
            return
        out = ""
        err = ""
        with contextlib.suppress(Exception):
            out = (proc.stdout.read() if proc.stdout else "").strip()
            err = (proc.stderr.read() if proc.stderr else "").strip()
        path = ""
        if rc == 0 and out:
            path = out if out == "/" else out.rstrip("/")
        elif rc != 0 and "-128" not in err:
            log.warning("[GUI] диалог выбора завершился с ошибкой: %s", err[:200] or rc)
        on_done(path)

    def _choose_path_tk(self, kind: str, initial_dir: str, title: str, on_done, filetypes=None) -> None:
        """Резервный путь: встроенный диалог Tk (когда osascript недоступен)."""
        try:
            if kind == "folder":
                path = filedialog.askdirectory(initialdir=initial_dir, title=title or None)
            else:
                types = [("Все файлы", "*.*")]
                if filetypes:
                    types = list(filetypes) + types
                path = filedialog.askopenfilename(initialdir=initial_dir, title=title or None,
                                                  filetypes=types)
        except Exception as exc:  # noqa: BLE001
            log.warning("[GUI] диалог выбора не открылся: %s", exc)
            path = ""
        on_done(path or "")

    def _browse_dir_into(self, var) -> None:
        initial_dir = self._browse_initialdir(self._value_of(var))

        def done(path: str) -> None:
            if path:
                self._remember_browse_dir(path)
                var.set(path)

        self._choose_folder(initial_dir, "Выберите папку", done)

    def _browse_bgm_folder(self, folder_var, enable_var, is_youtube_base: bool = False) -> None:
        """Выбор папки BGM: если в ней есть аудио — галочка включается автоматически."""
        initial_dir = self._browse_initialdir(self._value_of(folder_var))

        def done(path: str) -> None:
            if not path:
                return
            self._remember_browse_dir(path)
            folder_var.set(path)
            from ..config.settings import Settings
            has = Settings.folder_has_bgm_audio(path)
            if has:
                enable_var.set(True)
            elif not is_youtube_base:
                # доп. соцсеть без треков — галочку лучше снять
                enable_var.set(False)

        self._choose_folder(initial_dir, "Выберите папку с фоновой музыкой", done)

    def _browse_audio(self) -> None:
        """Одна кнопка: выбрать файл или папку. Тип определяется автоматически, инфо — в лог."""
        log.info("[GUI] Диалог выбора аудио (файл или папка)")
        initial_dir = self._browse_initialdir(self._value_of(getattr(self, "audio_var", None)))

        def done(path: str) -> None:
            if not path:
                log.info("[GUI] Выбор аудио отменён")
                return
            path = path.strip()
            self._remember_browse_dir(path)
            self.audio_var.set(path)
            log.info(f"[GUI] audio_var = {path}")
            self._save_settings()
            self._report_audio_selection(path)

        self._ask_file_or_directory(
            title="Выберите аудиофайл или папку с аудио",
            on_done=done,
            filetypes=[("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma"), ("Все файлы", "*.*")],
        )

    def _ask_file_or_directory(self, title: str = "Выберите файл или папку", on_done=None,
                               filetypes=None) -> None:
        """Компактное окно «что выбрать?» → системный диалог файла или папки.

        Сам выбор идёт через _choose_file/_choose_folder (родной диалог macOS со
        стартовой папкой); результат приходит в on_done(path) позже.
        """
        # --- macOS: нативный панель с canChooseFiles + canChooseDirectories ---
        initial_dir = self._browse_initialdir(
            self.audio_var.get() if hasattr(self, "audio_var") else ""
        )
        if sys.platform == "darwin":
            try:
                import subprocess
                # AppleScript: разрешаем и файлы, и папки
                script = '''
                set theResult to choose file with prompt "%s" without invisibles
                return POSIX path of theResult
                ''' % title.replace('"', '\\"')
                # choose file OR folder requires slightly different approach:
                # use choose file name is wrong; use Finder-like via osascript with both
                script = f'''
                tell application "System Events"
                    activate
                end tell
                set theChoice to choose file with prompt "{title.replace('"', '')}" without multiple selections allowed
                return POSIX path of theChoice
                '''
                # Better: use Cocoa-style via osascript that allows folders too
                # Standard way that works on modern macOS:
                script = f'''
                set okTypes to {{"public.audio", "public.mp3", "com.microsoft.waveform-audio", "public.aiff-audio", "com.apple.m4a-audio", "org.xiph.flac", "com.microsoft.windows-media-wma"}}
                set thePanel to choose file with prompt "{title.replace(chr(34), "")}" of type okTypes without invisibles
                return POSIX path of thePanel
                '''
                # Actually pure AppleScript cannot easily do "file OR folder" in one choose.
                # Use Python + AppKit if available, else fallback UI.
                try:
                    from AppKit import NSOpenPanel, NSModalResponseOK  # type: ignore
                    panel = NSOpenPanel.openPanel()
                    panel.setCanChooseFiles_(True)
                    panel.setCanChooseDirectories_(True)
                    panel.setAllowsMultipleSelection_(False)
                    panel.setMessage_(title)
                    panel.setPrompt_("Выбрать")
                    if initial_dir:
                        try:
                            from Foundation import NSURL  # type: ignore
                            panel.setDirectoryURL_(NSURL.fileURLWithPath_(initial_dir))
                        except Exception:
                            pass
                    if panel.runModal() == NSModalResponseOK:
                        urls = panel.URLs()
                        if urls and len(urls) > 0:
                            return str(urls[0].path())
                    return ""
                except Exception:
                    pass
            except Exception as e:
                log.debug("[GUI] macOS native panel unavailable: %s", e)

        # --- Компактное окно выбора типа (одна кнопка «Обзор» → один клик тип) ---
        result = {"done": False}

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=COLORS["bg"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Что выбрать?", style="Section.TLabel").pack(anchor="w", pady=(0, 10))

        def _finish(path: str) -> None:
            if result["done"]:
                return
            result["done"] = True
            if on_done is not None:
                on_done(path or "")

        def pick_file():
            win.destroy()
            self._choose_file(
                initial_dir,
                "Выберите аудиофайл",
                _finish,
                filetypes=filetypes or [("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma"),
                                        ("Все файлы", "*.*")],
            )

        def pick_dir():
            win.destroy()
            self._choose_folder(initial_dir, "Выберите папку с аудио", _finish)

        def cancel():
            win.destroy()
            _finish("")

        ttk.Button(frame, text="📄  Аудиофайл", command=pick_file).pack(fill=tk.X, pady=3)
        ttk.Button(frame, text="📁  Папка с аудио", command=pick_dir).pack(fill=tk.X, pady=3)
        ttk.Button(frame, text="Отмена", command=cancel).pack(fill=tk.X, pady=(10, 0))

        # Центрировать относительно главного окна
        win.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

        win.wait_window()

    def _report_audio_selection(self, path: str) -> None:
        """После выбора пути: определить файл/папку, просканировать, вывести инфо в лог GUI."""
        if not path:
            return
        if not os.path.exists(path):
            self._log(f"[АУДИО] Путь не существует: {path}")
            return

        files = Settings.collect_audio_files(path)

        if os.path.isfile(path):
            if files:
                try:
                    from ..engines.audio import probe_duration
                    dur = probe_duration(path)
                    self._log(
                        f"[АУДИО] Выбран файл: {os.path.basename(path)}  "
                        f"({dur:.1f} сек)  ·  {path}"
                    )
                except Exception:
                    self._log(f"[АУДИО] Выбран файл: {os.path.basename(path)}  ·  {path}")
            else:
                self._log(
                    f"[АУДИО] Выбран файл, но формат не поддерживается: {os.path.basename(path)}"
                )
                self._log(
                    f"[АУДИО] Поддерживаются: {', '.join(Settings.AUDIO_EXTENSIONS)}"
                )
            return

        if os.path.isdir(path):
            self._log(f"[АУДИО] Выбрана папка: {path}")
            if not files:
                self._log("[АУДИО] В папке аудиофайлы не найдены")
                self._log(
                    f"[АУДИО] Ищем расширения: {', '.join(Settings.AUDIO_EXTENSIONS)}"
                )
            else:
                self._log(f"[АУДИО] Найдено аудиофайлов: {len(files)}")
                total_dur = 0.0
                for i, f in enumerate(files, 1):
                    name = os.path.basename(f)
                    try:
                        from ..engines.audio import probe_duration
                        d = probe_duration(f)
                        total_dur += d
                        self._log(f"[АУДИО]   {i}. {name}  ({d:.1f} сек)")
                    except Exception:
                        self._log(f"[АУДИО]   {i}. {name}")
                if total_dur > 0:
                    self._log(
                        f"[АУДИО] Суммарная длительность: {total_dur:.1f} сек "
                        f"({total_dur / 60:.1f} мин)  ·  будет {len(files)} прогон(ов)"
                    )
            return

        self._log(f"[АУДИО] Неизвестный тип пути: {path}")

    # ─── Настройки ───────────────────────────────────────────────────────


    def _open_settings(self) -> None:
        """Настройки: Gemini/Whisper + описания YouTube + доп. промпт."""
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        screen_w = int(win.winfo_screenwidth())
        screen_h = int(win.winfo_screenheight())
        modal_w = min(820, max(520, screen_w - 60))
        modal_h = min(760, max(500, screen_h - 100))
        modal_x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - modal_w) // 2)
        modal_y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - modal_h) // 2)
        win.geometry(f"{modal_w}x{modal_h}+{modal_x}+{modal_y}")
        win.minsize(min(520, modal_w), min(500, modal_h))
        win.configure(bg=COLORS.get("bg", "#1e1e1e"))
        win.transient(self.root)
        win.grab_set()

        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(0, weight=1)
        nb = ttk.Notebook(win)
        nb.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))

        # --- Tab: Модели ---
        tab_m = ttk.Frame(nb, padding=12)
        nb.add(tab_m, text="Модели")
        ttk.Label(tab_m, text="Модель Gemini:", style="Section.TLabel").pack(anchor="w")
        try:
            from ..engines.analysis import AVAILABLE_GEMINI_MODELS
            _model_values = list(AVAILABLE_GEMINI_MODELS)
        except Exception:
            _model_values = [
                "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                "gemini-flash-latest", "gemini-3.5-flash", "gemini-3.7-flash",
                "gemini-3.8-flash", "gemini-2.5-flash",
            ]
        cur = (self.model_var.get() or "").strip()
        if cur and cur not in _model_values:
            _model_values = [cur] + _model_values
        row_m = ttk.Frame(tab_m)
        row_m.pack(fill=tk.X, pady=(4, 4))
        model_combo = ttk.Combobox(
            row_m,
            textvariable=self.model_var,
            values=_model_values,
            state="normal",
        )
        model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._settings_model_combo = model_combo
        model_status = ttk.Label(tab_m, text="Список: локальный каталог. Нажмите «Обновить с API».")
        model_status.pack(anchor="w", pady=(0, 2))
        self._settings_model_status = model_status

        def _refresh_models_from_api():
            # ключи из поля настроек (ещё не сохранённые тоже)
            raw = ""
            if hasattr(self, "_settings_keys_box"):
                try:
                    raw = self._settings_keys_box.get("1.0", "end").strip()
                except Exception:
                    raw = ""
            keys = []
            if raw:
                import re as _re
                keys = [k.strip() for k in raw.replace(",", "\n").replace(";", "\n").splitlines() if k.strip()]
            if not keys:
                keys = list(self.settings.gemini_api_keys or [])
            if not keys and self.settings.gemini_api_key:
                keys = [self.settings.gemini_api_key]
            model_status.configure(text="Запрос списка моделей у API…")
            win.update_idletasks()

            def work():
                err = ""
                names = []
                try:
                    from ..engines.analysis import list_available_gemini_models
                    names = list_available_gemini_models(
                        api_keys=keys,
                        api_key=(keys[0] if keys else ""),
                        log_fn=lambda m: log.info(m),
                    )
                except Exception as e:
                    err = str(e)
                    names = []

                def done():
                    if not names:
                        model_status.configure(
                            text=f"Не удалось получить список ({err or 'пусто'}). Остался локальный каталог."
                        )
                        return
                    # сохранить текущий выбор если ещё актуален
                    cur_m = (self.model_var.get() or "").strip()
                    vals = list(names)
                    if cur_m and cur_m not in vals:
                        vals = [cur_m] + vals
                    model_combo.configure(values=vals)
                    if cur_m in vals:
                        self.model_var.set(cur_m)
                    elif vals:
                        self.model_var.set(vals[0])
                    model_status.configure(
                        text=f"С API: {len(names)} моделей. Выберите нужную и сохраните."
                    )
                    log.info("[SETTINGS] Gemini models from API: %s", ", ".join(names[:12]))

                try:
                    win.after(0, done)
                except Exception:
                    done()

            import threading
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(
            row_m, text="Обновить с API", width=16, command=_refresh_models_from_api
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(
            tab_m,
            text="Выбранная модель идёт первой. При 404 — fallback на другие из списка (после обновления — из API).",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(tab_m, text="API ключи Gemini (с новой строки или через запятую):", style="Section.TLabel").pack(anchor="w")
        keys_list = list(self.settings.gemini_api_keys or [])
        if not keys_list and self.settings.gemini_api_key:
            keys_list = [self.settings.gemini_api_key]
        keys_box = tk.Text(
            tab_m, height=5, wrap="word", bg=COLORS["input_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"], selectbackground=COLORS["primary"],
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
            padx=8, pady=8,
        )
        keys_box.pack(fill=tk.X, pady=(4, 8))
        keys_box.insert("1.0", "\n".join(keys_list))
        self._settings_keys_box = keys_box
        ttk.Label(tab_m, text="Whisper", style="Section.TLabel").pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(tab_m); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Модель:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_model_var, values=["tiny","base","small","medium","large-v2","large-v3","large-v3-turbo"], state="readonly", width=16).pack(side=tk.LEFT)
        row = ttk.Frame(tab_m); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Язык:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_lang_var, values=["ru","en","auto"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(tab_m); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Device:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_dev_var, values=["cpu","cuda","auto"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(tab_m); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Compute:", width=12).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_comp_var, values=["int8","float16","float32"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(tab_m); row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="WhisperX:", width=12).pack(side=tk.LEFT)
        ttk.Label(row, textvariable=self.whisperx_status_var).pack(side=tk.LEFT)
        ttk.Button(row, text="Найти", width=8, command=self._find_whisperx).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Вручную", width=8, command=self._browse_whisperx).pack(side=tk.LEFT)

        # --- Tab: Описания YouTube ---
        tab_d = ttk.Frame(nb, padding=12)
        nb.add(tab_d, text="Описания YouTube")
        ttk.Label(tab_d, text="Плейлисты (добавляются в конец description.txt):", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(tab_d); row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Весь сезон URL:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.playlist_season_url_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(tab_d); row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Блок URL:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.playlist_block_url_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(tab_d); row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Название блока:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.playlist_block_label_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(tab_d); row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Обязательные #:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.required_hashtags_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(
            tab_d, text="Всегда дописывать плейлисты в описание полного видео",
            variable=self.append_playlist_footer_var,
        ).pack(anchor="w", pady=(8, 2))
        ttk.Checkbutton(
            tab_d, text="Шаблон описания Shorts (5 блоков в промпте Gemini)",
            variable=self.shorts_use_template_var,
        ).pack(anchor="w", pady=2)
        row = ttk.Frame(tab_d); row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="Max длина Shorts desc:", width=18).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.shorts_desc_max_len_var, width=8).pack(side=tk.LEFT)
        ttk.Label(tab_d, text="Файлы: *_description.txt и short_XXX_upload.txt — для copy-paste в YouTube.", font=("SF Pro Text", 9)).pack(anchor="w", pady=(10, 0))

        # --- Tab: Промпт ---

        tab_p = ttk.Frame(nb, padding=12)
        nb.add(tab_p, text="Промпт")
        ttk.Label(
            tab_p,
            text="Полное тело промпта Gemini (можно менять целиком). Пусто при сохранении = снова встроенный.",
            style="Section.TLabel",
        ).pack(anchor="w")
        prompt_frame = ttk.Frame(tab_p)
        prompt_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 4))
        prompt_box = tk.Text(
            prompt_frame, height=12, wrap="word", bg=COLORS["input_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"], selectbackground=COLORS["primary"],
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
            padx=8, pady=8,
        )
        prompt_scrollbar = ttk.Scrollbar(prompt_frame, command=prompt_box.yview)
        prompt_box.configure(yscrollcommand=prompt_scrollbar.set)
        prompt_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        prompt_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Загрузить: сохранённое тело или встроенный промпт
        body = str(getattr(self.settings, "prompt_body", "") or "").strip()
        if not body:
            try:
                from ..engines.analysis import get_builtin_analysis_prompt
                series = ""
                if hasattr(self, "series_var"):
                    try:
                        series = (self.series_var.get() or "").strip()
                    except Exception:
                        pass
                body = get_builtin_analysis_prompt(series_name=series)
            except Exception as e:
                body = f"(не удалось загрузить встроенный промпт: {e})"
        prompt_box.delete("1.0", "end")
        prompt_box.insert("1.0", body)
        self._settings_prompt_box = prompt_box

        ttk.Label(
            tab_p,
            text="Доп. инструкции (необязательно) — дописываются ПОСЛЕ тела промпта:",
            style="Section.TLabel",
        ).pack(anchor="w", pady=(8, 0))
        extra_box = tk.Text(
            tab_p, height=4, wrap="word", bg=COLORS["input_bg"], fg=COLORS["input_fg"],
            insertbackground=COLORS["text"], selectbackground=COLORS["primary"],
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["primary"],
            padx=8, pady=8,
        )
        extra_box.pack(fill=tk.X, pady=(4, 4))
        extra_box.insert("1.0", str(getattr(self.settings, "prompt_extra", "") or ""))
        self._settings_prompt_extra_box = extra_box

        def _reset_prompt():
            try:
                from ..engines.analysis import get_builtin_analysis_prompt
                series = ""
                if hasattr(self, "series_var"):
                    try:
                        series = (self.series_var.get() or "").strip()
                    except Exception:
                        pass
                text = get_builtin_analysis_prompt(series_name=series)
            except Exception as e:
                text = f"(ошибка загрузки: {e})"
            prompt_box.delete("1.0", "end")
            prompt_box.insert("1.0", text)
            self.settings.prompt_body = ""  # пусто = builtin at runtime until save with content

        rowp = ttk.Frame(tab_p)
        rowp.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(rowp, text="Сбросить к встроенному", command=_reset_prompt).pack(side=tk.LEFT)
        ttk.Label(
            tab_p,
            text="При запуске: если тело сохранено — оно используется целиком; таймкоды транскрипта добавятся автоматически, если их ещё нет в тексте.",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(6, 0))

        def save():
            self.settings.gemini_model = self.model_var.get()
            raw = self._settings_keys_box.get("1.0", "end").strip()
            if raw:
                import re as _re
                keys = [k.strip() for k in raw.replace(",", "\n").replace(";", "\n").splitlines() if k.strip()]
                self.settings.gemini_api_keys = keys
                self.settings.gemini_api_key = keys[0] if keys else ""
            self.settings.whisper_model = self.whisper_model_var.get() or "large-v3"
            self.settings.whisper_language = self.whisper_lang_var.get()
            self.settings.whisper_device = self.whisper_dev_var.get()
            self.settings.whisper_compute_type = self.whisper_comp_var.get()
            if hasattr(self, "whisperx_path_var"):
                self.settings.whisperx_path = self.whisperx_path_var.get()
            # packaging
            self.settings.playlist_season_url = self.playlist_season_url_var.get().strip()
            self.settings.playlist_block_url = self.playlist_block_url_var.get().strip()
            self.settings.playlist_block_label = self.playlist_block_label_var.get().strip()
            self.settings.required_hashtags = self.required_hashtags_var.get().strip()
            self.settings.append_playlist_footer = bool(self.append_playlist_footer_var.get())
            self.settings.shorts_use_template = bool(self.shorts_use_template_var.get())
            try:
                self.settings.shorts_desc_max_len = int(str(self.shorts_desc_max_len_var.get()).strip() or "280")
            except ValueError:
                self.settings.shorts_desc_max_len = 280
            pb = self._settings_prompt_box.get("1.0", "end").strip()
            # Сравниваем со встроенным: если совпадает — храним пусто (builtin)
            try:
                from ..engines.analysis import get_builtin_analysis_prompt
                builtin = get_builtin_analysis_prompt().strip()
            except Exception:
                builtin = ""
            if pb and builtin and pb == builtin:
                self.settings.prompt_body = ""
            else:
                self.settings.prompt_body = pb
            pe = ""
            if hasattr(self, "_settings_prompt_extra_box"):
                pe = self._settings_prompt_extra_box.get("1.0", "end").strip()
            self.settings.prompt_extra = pe
            log.info(
                "[SETTINGS] Gemini=%s packaging footer=%s prompt_body_len=%s extra_len=%s",
                self.settings.gemini_model,
                self.settings.append_playlist_footer,
                len(getattr(self.settings, "prompt_body", "") or ""),
                len(pe),
            )
            self._save_settings()
            # also persist packaging fields not bound only via *_var lists
            self._save_packaging_settings()
            win.destroy()

        btn_row = ttk.Frame(win, padding=(0, 0, 0, 0))
        btn_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self._settings_save_btn = ttk.Button(
            btn_row, text="Сохранить", style="Accent.TButton", command=save,
        )
        self._settings_save_btn.pack(fill=tk.X, ipady=2)

    def _save_packaging_settings(self) -> None:
        """Дописать packaging-поля в JSON настроек (и prompt_extra)."""
        try:
            data = {}
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            data.update({
                "playlist_season_url": getattr(self.settings, "playlist_season_url", ""),
                "playlist_block_url": getattr(self.settings, "playlist_block_url", ""),
                "playlist_block_label": getattr(self.settings, "playlist_block_label", ""),
                "required_hashtags": getattr(self.settings, "required_hashtags", ""),
                "append_playlist_footer": bool(getattr(self.settings, "append_playlist_footer", True)),
                "shorts_use_template": bool(getattr(self.settings, "shorts_use_template", True)),
                "shorts_desc_max_len": int(getattr(self.settings, "shorts_desc_max_len", 280) or 280),
                "prompt_extra": str(getattr(self.settings, "prompt_extra", "") or ""),
                "prompt_body": str(getattr(self.settings, "prompt_body", "") or ""),
            })
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning("[GUI] packaging settings save: %s", e)

    def _packaging_opts_from_settings(self) -> dict:
        """Собрать opts для analysis/finalize из GUI settings."""
        try:
            max_len = int(str(self.shorts_desc_max_len_var.get()).strip() or "280")
        except Exception:
            max_len = int(getattr(self.settings, "shorts_desc_max_len", 280) or 280)
        # доп. инструкции
        pe = str(getattr(self.settings, "prompt_extra", "") or "")
        if hasattr(self, "_settings_prompt_extra_box"):
            try:
                pe = self._settings_prompt_extra_box.get("1.0", "end").strip()
            except Exception:
                pass
        # полное тело промпта
        pb = str(getattr(self.settings, "prompt_body", "") or "")
        if hasattr(self, "_settings_prompt_box"):
            try:
                pb_live = self._settings_prompt_box.get("1.0", "end").strip()
                if pb_live:
                    # если открыты настройки и текст изменён — берём live
                    pb = pb_live
            except Exception:
                pass
        # если тело = встроенный — не считаем кастомом
        if pb:
            try:
                from ..engines.analysis import get_builtin_analysis_prompt
                if pb.strip() == get_builtin_analysis_prompt().strip():
                    pb = ""
            except Exception:
                pass
        return {
            "playlist_season_url": (self.playlist_season_url_var.get() if hasattr(self, "playlist_season_url_var") else getattr(self.settings, "playlist_season_url", "")).strip(),
            "playlist_block_url": (self.playlist_block_url_var.get() if hasattr(self, "playlist_block_url_var") else getattr(self.settings, "playlist_block_url", "")).strip(),
            "playlist_block_label": (self.playlist_block_label_var.get() if hasattr(self, "playlist_block_label_var") else getattr(self.settings, "playlist_block_label", "")).strip(),
            "required_hashtags": (self.required_hashtags_var.get() if hasattr(self, "required_hashtags_var") else getattr(self.settings, "required_hashtags", "")).strip(),
            "append_playlist_footer": bool(self.append_playlist_footer_var.get()) if hasattr(self, "append_playlist_footer_var") else bool(getattr(self.settings, "append_playlist_footer", True)),
            "prompt_extra": pe,
            "prompt_body": pb,
            "shorts_desc_max_len": max_len,
            "shorts_use_template": bool(self.shorts_use_template_var.get()) if hasattr(self, "shorts_use_template_var") else bool(getattr(self.settings, "shorts_use_template", True)),
        }


    def _log(self, msg: str) -> None:
        """Потокобезопасная запись в лог. Ограничение ~3000 строк — нет утечки RAM."""
        timestamp = time.strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        log.info(msg)

        def _write():
            try:
                if getattr(self, "_closing", False):
                    return
                for widget in getattr(self, "_log_widgets", ()):  # вкладка + правая панель
                    try:
                        widget.configure(state=tk.NORMAL)
                        widget.insert(tk.END, full_msg + "\n")
                        # Ограничиваем рост виджета
                        line_count = int(widget.index("end-1c").split(".")[0])
                        if line_count > 3000:
                            widget.delete("1.0", f"{line_count - 2500}.0")
                        widget.see(tk.END)
                        widget.configure(state=tk.DISABLED)
                    except tk.TclError:
                        pass
                # Статус-бар: что идёт прямо сейчас (рамки-разделители пропускаем)
                line = ""
                for candidate in reversed((msg or "").splitlines()):
                    c = candidate.strip()
                    if c and not set(c) <= set("─═-— "):
                        line = c
                        break
                if line and hasattr(self, "activity_label"):
                    self._activity_text = line
                    self.activity_label.configure(text=f"▶ {line}")
            except Exception:
                pass
        try:
            self.root.after(0, _write)
        except RuntimeError:
            pass


    CAPTION_HELP = {
        "auto_aisie": "Clean Pro (parity): 2–3 слова, strong из Gemini, без pre-color neon.",
        "hormozi": "Белые слова, активное — ярко-жёлтое, лёгкий pop. Классика Shorts.",
        "hormozi_green": "Как hormozi, акцент неон-зелёный.",
        "tiktok_box": "Текст на тёмной подложке-боксе, без scale-pop.",
        "clean_pro": "Clean Pro / Shorts parity: 2–3 слова, mild yellow active, strong L2–L4 neon+scale.",
        "bold_pop": "Крупный красный акцент, сильный pop.",
        "cliffhanger": "Красный tension, холодный dim, сильный pop.",
    }
    HOOK_HELP = {
        "auto_aisie": "Стиль хука выбирает AISIE по типу (вопрос / удар / мягкий).",
        "hormozi": "Крупный жёлтый хук сверху.",
        "impact": "Оранжевый «ударный» хук.",
        "neon": "Неон CapCut: мягкий ореол свечения, без жёсткой тени.",
        "soft": "Спокойный белый хук, мягкая тень.",
        "bold": "Белый жирный хук без цвета.",
        "cliffhanger": "Красный tension-хук.",
    }

    def _update_style_desc(self) -> None:
        if hasattr(self, "caption_desc_var"):
            k = (self.caption_style_var.get() or "").strip()
            self.caption_desc_var.set(self.CAPTION_HELP.get(k, k))
        if hasattr(self, "hook_desc_var"):
            k = (self.hook_style_var.get() or "").strip()
            self.hook_desc_var.set(self.HOOK_HELP.get(k, k))

    def _format_eta(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


    def _start_progress_ticker(self) -> None:
        """Обновлять прошедшее время каждую секунду, даже между стадиями."""
        self._ticker_stop = False
        def _tick():
            if getattr(self, "_ticker_stop", True) or not getattr(self, "running", False):
                return
            t0 = getattr(self, "_pipeline_t0", 0) or 0
            if t0:
                elapsed = time.time() - t0
                value = float(self.progress_var.get() or 0)
                series = ""
                try:
                    series = (self.settings.series_name or os.path.basename(self.settings.audio_path or ""))[:40]
                except Exception:
                    pass
                if value > 3:
                    total_est = elapsed * (100.0 / max(value, 0.1))
                    remain = max(0.0, total_est - elapsed)
                    text = (
                        f"⏱ {self._format_eta(elapsed)} / ~{self._format_eta(total_est)}  "
                        f"(~{self._format_eta(remain)})  ·  {series}"
                    )
                else:
                    text = f"⏱ {self._format_eta(elapsed)}  ·  идёт… · {series}"
                try:
                    self.time_label.configure(text=text)
                except Exception:
                    pass
            try:
                self.root.after(1000, _tick)
            except Exception:
                pass
        try:
            self.root.after(200, _tick)
        except Exception:
            pass

    def _stop_progress_ticker(self) -> None:
        self._ticker_stop = True

    def _set_progress(self, value: float, stage: str = "") -> None:
        """Потокобезопасное обновление прогресса + ETA."""
        def _update():
            self.progress_var.set(value)
            self.progress_label.configure(text=f"{value:.0f}%")
            if stage and hasattr(self, "stage_label"):
                self.stage_label.configure(text=stage)
            if getattr(self, "_pipeline_t0", 0):
                elapsed = time.time() - self._pipeline_t0
                series = self.settings.series_name or os.path.basename(self.settings.audio_path or "")
                if value > 3:
                    total_est = elapsed * (100.0 / max(value, 0.1))
                    remain = max(0.0, total_est - elapsed)
                    self.time_label.configure(
                        text=f"⏱ {self._format_eta(elapsed)} / ~{self._format_eta(total_est)}  "
                             f"(~{self._format_eta(remain)})  ·  {series[:40]}"
                    )
                else:
                    self.time_label.configure(text=f"⏱ {self._format_eta(elapsed)}  ·  оценка… · {series[:40]}")
        try:
            self.root.after(0, _update)
        except RuntimeError:
            pass

    # ─── Запуск пайплайна ────────────────────────────────────────────────

    def _on_speech_rate_mode_change(self, *_args) -> None:
        """Переключить диапазон ползунка под режим: слова/мин ↔ коэффициент."""
        if not hasattr(self, "speech_rate_slider"):
            return
        from ..engines.speech_rate import normalize_speed_mode

        if normalize_speed_mode(self.speech_rate_mode_var.get()) == "coefficient":
            self.speech_rate_slider.configure(
                variable=self.speech_rate_coefficient_var,
                from_=1.0, to=1.5, resolution=0.01,
            )
        else:
            self.speech_rate_slider.configure(
                variable=self.speech_rate_target_wpm_var,
                from_=100, to=200, resolution=1,
            )
        self._update_speech_rate_hint()

    def _on_short_speech_mode_change(self, *_args) -> None:
        """Режим скорости шортов: «как на длинном» — ползунок выключен."""
        if not hasattr(self, "s_speech_rate_slider"):
            return
        from ..engines.speech_rate import normalize_short_speed_mode

        mode = normalize_short_speed_mode(self.s_speech_rate_mode_var.get())
        if mode == "coefficient":
            self.s_speech_rate_slider.configure(
                variable=self.s_speech_rate_coefficient_var,
                from_=1.0, to=1.5, resolution=0.01, state="normal",
            )
        elif mode == "wpm":
            self.s_speech_rate_slider.configure(
                variable=self.s_speech_rate_target_wpm_var,
                from_=100, to=200, resolution=1, state="normal",
            )
        else:
            self.s_speech_rate_slider.configure(state="disabled")

    def _update_speech_rate_hint(self) -> None:
        """Подсказка под ползунком: текущая цель + метрики прошлого прогона."""
        if not hasattr(self, "speech_rate_hint"):
            return
        try:
            from ..engines.speech_rate import load_speech_state, normalize_speed_mode

            state = load_speech_state()
            if normalize_speed_mode(self.speech_rate_mode_var.get()) == "coefficient":
                k = _float_setting(self.speech_rate_coefficient_var.get(), 1.0)
                first = f"Коэффициент ×{k:.2f}: итог = исходный темп × {k:.2f}"
            else:
                target = _float_setting(self.speech_rate_target_wpm_var.get(), 120.0)
                estimate = ""
                source_wpm = _float_setting(state.get("measured_wpm_before_adjustment"), 0.0)
                if source_wpm > 0:
                    if target > source_wpm:
                        estimate = f" ≈ ×{target / source_wpm:.2f} от прошлого темпа"
                    else:
                        estimate = " (прошлый темп уже не ниже цели)"
                first = f"Цель: {target:.0f} слов/мин{estimate}"
            last = ""
            if state.get("word_count"):
                last = (
                    f"Прошлый прогон: {state.get('word_count')} слов · "
                    f"{_float_setting(state.get('measured_wpm_before_adjustment'), 0.0):.1f} → "
                    f"{_float_setting(state.get('final_wpm'), 0.0):.1f} слов/мин · "
                    f"коэффициент ×{_float_setting(state.get('tempo_factor'), 1.0):.2f}"
                )
            self.speech_rate_hint.configure(text=first + (("\n" + last) if last else ""))
        except Exception:
            pass

    def _start(self) -> None:
        """Запустить пайплайн."""
        log.info("[GUI] ╔══════════════════════════════════════════════╗")
        log.info("[GUI] ║       КНОПКА «СОЗДАТЬ ВИДЕО» НАЖАТА         ║")
        log.info("[GUI] ╚══════════════════════════════════════════════╝")

        if self.running:
            log.warning("[GUI] Пайплайн уже выполняется — показываем предупреждение")
            messagebox.showwarning("Внимание", "Пайплайн уже запущен")
            return
        if getattr(self, "_starting", False):
            return

        log.info("[GUI] Сбор настроек из GUI...")
        collect_settings_from_gui(self)

        # Persist GUI state before validate / pipeline
        self._save_settings()

        # Переконфигурировать логирование в выходную папку
        if self.settings.output_folder and os.path.isdir(self.settings.output_folder):
            log_file = os.path.join(self.settings.output_folder, "videomaker.log")
            from video_maker.main import setup_logging
            setup_logging(log_file)
            self._reinstall_log_hook()
            log.info(f"[GUI] Логирование перенастроено в {log_file}")

        missing_network: list[str] = []
        if hasattr(self, "_wake_network_paths"):
            missing_network = self._wake_network_paths()
        errors = self.settings.validate()
        if errors and missing_network:
            # Том мог отключиться после сна: просим Finder подключить его и ждём —
            # вместо сухой ошибки «папка не найдена».
            self._starting = True
            try:
                self._wake_network_paths(wait_seconds=20.0, try_open=True)
            finally:
                self._starting = False
            errors = self.settings.validate()
        if errors:
            log.error(f"[GUI] Ошибки валидации: {errors}")
            hint = ""
            if any("/Volumes/" in e for e in errors):
                hint = (
                    "\n\nСетевой том ещё не подключён. Откройте его в Finder — "
                    "подключение произойдёт автоматически, затем нажмите «Создать видео» "
                    "ещё раз (приложение подождёт до 20 секунд)."
                )
            messagebox.showerror("Ошибки", "\n".join(errors) + hint)
            return

        # Список аудиофайлов (один или из папки, + 1 уровень подпапок)
        self._audio_queue = Settings.collect_audio_files(self.settings.audio_path)
        log.info(f"[GUI] Аудиофайлов к обработке: {len(self._audio_queue)}")
        self._log(f"[АУДИО] К обработке: {len(self._audio_queue)} файл(ов)")
        for i, pth in enumerate(self._audio_queue, 1):
            log.info(f"[GUI]   {i}. {pth}")
            self._log(f"[АУДИО]   {i}. {os.path.basename(pth)}")

        if not self._audio_queue:
            # Пустая папка/отсутствие поддерживаемых файлов: честно останавливаемся
            # до запуска worker, чтобы API не отвечал «started» на пустой запуск.
            messagebox.showerror(
                "Ошибки",
                f"В источнике нет аудиофайлов:\n{self.settings.audio_path}\n\n"
                "Поддерживаются m4a, mp3, wav, flac, ogg, aac (файл или папка).",
            )
            return

        # Предпросмотр B-roll (подпапки с темами)
        try:
            from ..engines.video import collect_video_files
            bh = collect_video_files(self.settings.broll_horizontal)
            self._log(f"[B-ROLL H] Найдено клипов: {len(bh)} (подпапки + корень, used исключён)")
            if not bh:
                messagebox.showerror(
                    "Ошибки",
                    f"В папке B-roll горизонтальный нет видео:\n{self.settings.broll_horizontal}\n\n"
                    "Ожидаются .mp4/.mov/.avi/.mkv/.webm в папке или в тематических подпапках.",
                )
                return
            if self.settings.broll_vertical:
                bv = collect_video_files(self.settings.broll_vertical)
                self._log(f"[B-ROLL V] Найдено клипов: {len(bv)}")
        except Exception as e:
            log.warning("[GUI] preview B-roll failed: %s", e)

        log.info("[GUI] Валидация пройдена — запуск пайплайна")
        # Freeze every setting used by the worker while still on the Tk main thread.
        # Tk StringVar/Text.get() must never be called from the pipeline thread.
        imo_fields = (
            "h_intro_path", "h_mid_path", "h_outro_path",
            "h_intro_duration", "h_mid_duration", "h_outro_duration",
            "v_intro_path", "v_mid_path", "v_outro_path",
            "v_intro_duration", "v_mid_duration", "v_outro_duration",
            "s_intro_path", "s_mid_path", "s_outro_path",
            "s_intro_duration", "s_mid_duration", "s_outro_duration",
        )
        self._pipeline_snapshot = {
            "settings": copy.deepcopy(self.settings),
            "packaging_opts": self._packaging_opts_from_settings(),
            "caption_style": self.caption_style_var.get() if hasattr(self, "caption_style_var") else "auto_aisie",
            "hook_style": self.hook_style_var.get() if hasattr(self, "hook_style_var") else "auto_aisie",
            "imo": {name: self._var_get(name) for name in imo_fields},
        }
        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.cancel_event.clear()
        self._pipeline_t0 = time.time()
        self._start_progress_ticker()
        log.info("[GUI] self.running = True, кнопка Старт заблокирована, Отмена включена")

        thread = threading.Thread(target=self._run_pipeline, name="vm-pipeline", daemon=True)
        self._pipeline_thread = thread
        log.info(f"[GUI] Создан поток пайплайна: {thread.name}")
        thread.start()
        log.info("[GUI] Поток запущен")

    def _run_pipeline(self) -> None:
        """Выполнить пайплайн в отдельном потоке (один файл или очередь из папки)."""
        snapshot = getattr(self, "_pipeline_snapshot", None) or {}
        pipeline_settings = snapshot.get("settings") or self.settings
        pipeline_packaging_opts = snapshot.get("packaging_opts")
        if not isinstance(pipeline_packaging_opts, dict):
            # Safe non-GUI fallback for programmatic callers; never access Tk here.
            pipeline_packaging_opts = {
                "playlist_season_url": getattr(pipeline_settings, "playlist_season_url", ""),
                "playlist_block_url": getattr(pipeline_settings, "playlist_block_url", ""),
                "playlist_block_label": getattr(pipeline_settings, "playlist_block_label", ""),
                "required_hashtags": getattr(pipeline_settings, "required_hashtags", ""),
                "append_playlist_footer": bool(getattr(pipeline_settings, "append_playlist_footer", True)),
                "shorts_use_template": bool(getattr(pipeline_settings, "shorts_use_template", True)),
                "shorts_desc_max_len": int(getattr(pipeline_settings, "shorts_desc_max_len", 280) or 280),
                "prompt_extra": str(getattr(pipeline_settings, "prompt_extra", "") or ""),
                "prompt_body": str(getattr(pipeline_settings, "prompt_body", "") or ""),
            }
        imo_values = snapshot.get("imo") if isinstance(snapshot.get("imo"), dict) else {}
        caption_style = str(snapshot.get("caption_style") or "auto_aisie")
        hook_style = str(snapshot.get("hook_style") or "auto_aisie")

        def _frozen_imo(name: str) -> str:
            value = imo_values.get(name, "")
            return str(value or "").strip()

        log.info("[PIPELINE] ═══════════════════════════════════════════════")
        log.info(f"[PIPELINE] Поток пайплайна запущен: {threading.current_thread().name}")
        log.info(f"[PIPELINE] PID: {os.getpid()}")

        from ..engines.power import prevent_sleep_start, prevent_sleep_stop, shutdown_computer
        pipeline_ok = False
        if getattr(pipeline_settings, "prevent_sleep", True):
            prevent_sleep_start(log_fn=self._log)

        audio_queue = getattr(self, "_audio_queue", None) or Settings.collect_audio_files(
            pipeline_settings.audio_path
        )
        total = len(audio_queue)
        if total == 0:
            self._log("[PIPELINE] Нет аудиофайлов для обработки")
            self.running = False
            self._stop_progress_ticker()
            self.root.after(0, lambda: self.start_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.cancel_btn.configure(state=tk.DISABLED))
            return

        try:
            from ..pipeline.branches import FinalHorizontal, FinalVertical
            from ..pipeline.finalize import FinalizeStage
            from ..pipeline.master import MasterBuilder
            from ..pipeline.shorts import ShortsCutter
            from ..pipeline.parallel_finals import ParallelFinals
            from ..pipeline.stages import AudioStage, GeminiStage, TranscribeStage

            log.info("[PIPELINE] Все модули пайплайна импортированы")
            log.info(f"[PIPELINE] Очередь: {total} аудиофайл(ов)")

            base_series = (pipeline_settings.series_name or "").strip()
            base_output = pipeline_settings.output_folder

            for idx, audio_file in enumerate(audio_queue, 1):
                if self.cancel_event.is_set():
                    self._log("[PIPELINE] Отмена по запросу пользователя (между файлами)")
                    break

                stem = os.path.splitext(os.path.basename(audio_file))[0]
                # Имя серии: явно заданное (для 1 файла) или имя файла
                if total == 1 and base_series:
                    series = base_series
                else:
                    series = base_series + ("_" if base_series else "") + stem if total > 1 else (base_series or stem)

                # Подпапка результата при пакетной обработке
                if total > 1:
                    out_dir = os.path.join(base_output, stem)
                    os.makedirs(out_dir, exist_ok=True)
                else:
                    out_dir = base_output

                self._log("\n" + "═" * 48)
                self._log(f"  АУДИО [{idx}/{total}]: {os.path.basename(audio_file)}")
                self._log(f"  Серия: {series}")
                self._log(f"  Вывод: {out_dir}")
                self._log("═" * 48)
                log.info(f"[PIPELINE] === Файл {idx}/{total}: {audio_file} → {out_dir} ===")

                # Прогресс: доля текущего файла в общей шкале
                progress_base = (idx - 1) / total * 100.0
                progress_span = 100.0 / total

                ctx = PipelineContext(
                    packaging_opts=pipeline_packaging_opts,
                    audio_path=audio_file,
                    broll_horizontal=pipeline_settings.broll_horizontal,
                    broll_vertical=pipeline_settings.broll_vertical,
                    bgm_folder=pipeline_settings.bgm_folder,
                    bgm_platforms=pipeline_settings.active_bgm_platforms(),
                    intro_middle_outro_folder=pipeline_settings.intro_middle_outro_folder,
                    vertical_background=pipeline_settings.vertical_background,
                    cover_horizontal=pipeline_settings.cover_horizontal,
                    cover_vertical=pipeline_settings.cover_vertical,
                    shorts_cover_image=getattr(pipeline_settings, "shorts_cover_image", "") or "",
                    output_folder=out_dir,
                    series_name=series,
                    gemini_model=pipeline_settings.gemini_model,
                    gemini_api_key=pipeline_settings.gemini_api_key,
                    gemini_api_keys=pipeline_settings.gemini_api_keys,
                    whisper_model=pipeline_settings.whisper_model,
                    whisperx_path=pipeline_settings.whisperx_path,
                    whisper_language=pipeline_settings.whisper_language,
                    whisper_device=pipeline_settings.whisper_device,
                    whisper_compute_type=pipeline_settings.whisper_compute_type,
                    voice_enhance=pipeline_settings.voice_enhance,
                    add_bgm=pipeline_settings.add_bgm,
                    remove_long_pauses=pipeline_settings.remove_long_pauses,
                    align_speech_rate=pipeline_settings.align_speech_rate,
                    # r57-api.22: ползунок скорости речи обязан попадать в контекст,
                    # иначе стадии берут дефолты (120 слов/мин) и настройка «не работает».
                    speech_rate_mode=pipeline_settings.speech_rate_mode,
                    speech_rate_target_wpm=pipeline_settings.speech_rate_target_wpm,
                    speech_rate_coefficient=pipeline_settings.speech_rate_coefficient,
                    cut_extra_words=pipeline_settings.cut_extra_words,
                    # r57-api.25: отдельные настройки шортов — скорость и вырезка.
                    s_speech_rate_mode=pipeline_settings.s_speech_rate_mode,
                    s_speech_rate_target_wpm=pipeline_settings.s_speech_rate_target_wpm,
                    s_speech_rate_coefficient=pipeline_settings.s_speech_rate_coefficient,
                    s_cut_extra_words=pipeline_settings.s_cut_extra_words,
                    intro_gemini=pipeline_settings.intro_gemini,
                    keep_temp_files=pipeline_settings.keep_temp_files,
                    caption_style=caption_style,
                    hook_style=hook_style,
                    target_lufs=pipeline_settings.target_lufs,
                    vstack_top_ratio=pipeline_settings.vstack_top_ratio,
                    h_enable_intro=pipeline_settings.h_enable_intro,
                    h_enable_middle=pipeline_settings.h_enable_middle,
                    h_enable_outro=pipeline_settings.h_enable_outro,
                    h_enable_hooks=pipeline_settings.h_enable_hooks,
                    h_enable_subtitles=pipeline_settings.h_enable_subtitles,
                    h_enable_strong_words=pipeline_settings.h_enable_strong_words,
                    v_enable_intro=pipeline_settings.v_enable_intro,
                    v_enable_middle=pipeline_settings.v_enable_middle,
                    v_enable_outro=pipeline_settings.v_enable_outro,
                    v_enable_hooks=pipeline_settings.v_enable_hooks,
                    v_enable_subtitles=pipeline_settings.v_enable_subtitles,
                    v_enable_strong_words=pipeline_settings.v_enable_strong_words,
                    s_enable_intro=pipeline_settings.s_enable_intro,
                    s_enable_middle=pipeline_settings.s_enable_middle,
                    s_enable_outro=pipeline_settings.s_enable_outro,
                    s_enable_hooks=pipeline_settings.s_enable_hooks,
                    s_enable_subtitles=pipeline_settings.s_enable_subtitles,
                    s_enable_strong_words=pipeline_settings.s_enable_strong_words,
                    h_intro_path=_frozen_imo("h_intro_path"),
                    h_mid_path=_frozen_imo("h_mid_path"),
                    h_outro_path=_frozen_imo("h_outro_path"),
                    h_intro_duration=float(_frozen_imo("h_intro_duration") or 3),
                    h_mid_duration=float(_frozen_imo("h_mid_duration") or 1),
                    h_outro_duration=float(_frozen_imo("h_outro_duration") or 3),
                    v_intro_duration=float(_frozen_imo("v_intro_duration") or 3),
                    v_mid_duration=float(_frozen_imo("v_mid_duration") or 1),
                    v_outro_duration=float(_frozen_imo("v_outro_duration") or 3),
                    s_intro_duration=float(_frozen_imo("s_intro_duration") or 3),
                    s_mid_duration=float(_frozen_imo("s_mid_duration") or 1),
                    s_outro_duration=float(_frozen_imo("s_outro_duration") or 3),
                    v_intro_path=_frozen_imo("v_intro_path"),
                    v_mid_path=_frozen_imo("v_mid_path"),
                    v_outro_path=_frozen_imo("v_outro_path"),
                    s_intro_path=_frozen_imo("s_intro_path"),
                    s_mid_path=_frozen_imo("s_mid_path"),
                    s_outro_path=_frozen_imo("s_outro_path"),
                    log_callback=self._log,
                )
                ctx.cancel_event = self.cancel_event  # для ShortsCutter / отмены внутри стадий
                log.info(f"[PIPELINE] PipelineContext создан для {os.path.basename(audio_file)}")

                stages = [
                    ("AudioStage", AudioStage()),
                    ("TranscribeStage", TranscribeStage()),
                    ("GeminiStage", GeminiStage()),
                    ("MasterBuilder", MasterBuilder()),
                    ("ParallelFinals", ParallelFinals()),
                    ("ShortsCutter", ShortsCutter()),
                    ("FinalizeStage", FinalizeStage()),
                ]

                from ..pipeline.checkpoint import (
                    apply_checkpoint_to_ctx,
                    clear_checkpoint,
                    describe_checkpoint,
                    load_checkpoint,
                    next_stage_index,
                    save_checkpoint,
                )

                start_i = 0
                ck = load_checkpoint(out_dir)
                if ck and (ck.get("audio_path") or "") == audio_file:
                    self._log("[CHECKPOINT] найден — продолжаем с места остановки")
                    for line in describe_checkpoint(ck).split("\n"):
                        self._log(f"  {line}")
                    ctx = apply_checkpoint_to_ctx(ctx, ck, log_fn=ctx.log)
                    start_i = next_stage_index(getattr(ctx, "_completed_stages", None) or ck.get("completed_stages") or [])
                    if start_i >= len(stages):
                        self._log("[CHECKPOINT] все стадии уже были выполнены")
                    else:
                        self._log(
                            f"[CHECKPOINT] старт: {stages[start_i][0]} "
                            f"(пропуск {start_i} стадий)"
                        )

                for name, stage in stages[start_i:]:
                    if self.cancel_event.is_set():
                        ctx.log("[PIPELINE] Отмена по запросу пользователя")
                        prev = (getattr(ctx, "_completed_stages", None) or [None])[-1]
                        save_checkpoint(
                            ctx, prev or "cancelled",
                            queue_index=idx, queue_total=total, log_fn=ctx.log,
                        )
                        break
                    log.info(f"\n{'─'*48}")
                    log.info(f"  [{idx}/{total}] СТАДИЯ: {name} — {stage.name()}")
                    log.info(f"{'─'*48}")
                    self._log(f"\n{'─'*48}")
                    self._log(f"  [{idx}/{total}] {stage.name()}")
                    self._log(f"{'─'*48}")
                    _t_stage = __import__("time").time()
                    log.info("[PIPELINE] >>> stage %s START", stage.name())
                    try:
                        ctx = stage.run(ctx)
                    except Exception:
                        prev = (getattr(ctx, "_completed_stages", None) or [None])[-1]
                        save_checkpoint(
                            ctx, prev or "before_fail",
                            queue_index=idx, queue_total=total, log_fn=ctx.log,
                        )
                        raise
                    log.info(
                        "[PIPELINE] <<< stage %s done in %.1fs",
                        stage.name(),
                        __import__("time").time() - _t_stage,
                    )
                    if self.cancel_event.is_set():
                        # Отмена пришла во время стадии: не помечаем её выполненной,
                        # чтобы резюме продолжилось с неё, а не с середины.
                        prev = (getattr(ctx, "_completed_stages", None) or [None])[-1]
                        save_checkpoint(
                            ctx, prev or "cancelled",
                            queue_index=idx, queue_total=total, log_fn=ctx.log,
                        )
                    else:
                        save_checkpoint(
                            ctx, name, queue_index=idx, queue_total=total, log_fn=ctx.log,
                        )
                    # Масштабируем progress стадии в общий прогресс
                    global_progress = progress_base + (ctx.progress / 100.0) * progress_span
                    self._set_progress(global_progress, stage=f"[{idx}/{total}] {stage.name()}")
                    log.info(f"[PIPELINE] {name} завершена, local={ctx.progress:.0f}% global={global_progress:.0f}%")

                if not self.cancel_event.is_set():
                    if "FinalizeStage" in (getattr(ctx, "_completed_stages", None) or []):
                        clear_checkpoint(out_dir, log_fn=ctx.log)

                if self.cancel_event.is_set():
                    break

                if self.cancel_event.is_set():
                    break

            if not self.cancel_event.is_set():
                self._log("\n" + "═" * 48)
                self._log(f"  ГОТОВО! Обработано файлов: {total}")
                self._log("═" * 48)
                self._set_progress(100)
                log.info("[PIPELINE] ══════════════════════════════════════════════")
                log.info(f"[PIPELINE] ВСЕ ФАЙЛЫ ЗАВЕРШЕНЫ УСПЕШНО ({total})")
                log.info("[PIPELINE] ═══════════════════════════════════════════════")
                pipeline_ok = True

        except Exception as e:
            log.error("[PIPELINE] ╔══════════════════════════════════════════════╗")
            log.error("[PIPELINE] ║            ОШИБКА В ПАЙПЛАЙНЕ                ║")
            log.error("[PIPELINE] ╚══════════════════════════════════════════════╝")
            log.error(f"[PIPELINE] Тип: {type(e).__name__}")
            log.error(f"[PIPELINE] Сообщение: {e}")
            log.error("[PIPELINE] Traceback:")
            for line in traceback.format_exc().splitlines():
                log.error(f"[PIPELINE]   {line}")
            self._log(f"\n  ОШИБКА: {e}")
            self.root.after(0, lambda err=e: messagebox.showerror("Ошибка пайплайна", f"{type(err).__name__}: {err}"))
        finally:
            log.info("[PIPELINE] finally: self.running = False, кнопка разблокирована")
            try:
                self.root.after(0, self._update_speech_rate_hint)
            except Exception:
                pass
            try:
                from ..engines.power import prevent_sleep_stop, shutdown_computer
                prevent_sleep_stop(log_fn=self._log)
                if pipeline_ok and getattr(pipeline_settings, "shutdown_when_done", False):
                    self._log("[POWER] Выключение компьютера по завершению...")
                    log.info("[POWER] shutdown_when_done=True — выключаем")
                    shutdown_computer(delay_sec=30, log_fn=self._log)
            except Exception as pe:
                log.warning("[POWER] %s", pe)
            self.running = False
            self._stop_progress_ticker()
            self.cancel_event.clear()
            self.root.after(0, lambda: self.start_btn.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.cancel_btn.configure(state=tk.DISABLED))

    def _cancel_pipeline(self) -> None:
        """Отменить выполнение пайплайна + убить ffmpeg процессы."""
        log.info("[GUI] Нажата кнопка ОТМЕНА — установка флага отмены + убийство ffmpeg")
        self.cancel_event.set()
        self._log("\n  ОТМЕНА: остановка после текущей стадии + убийство ffmpeg...")
        self.cancel_btn.configure(state=tk.DISABLED)
        self._kill_ffmpeg_processes()
        try:
            from ..engines.power import prevent_sleep_stop
            prevent_sleep_stop()
        except Exception:
            pass

    def _heartbeat(self) -> None:
        """Heartbeat (редко). Не грузит idle."""
        if getattr(self, "_closing", False):
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        log.debug(
            "LIFECYCLE heartbeat visible=%s viewable=%s geometry=%s",
            self.root.winfo_viewable(),
            self.root.winfo_ismapped(),
            self.root.geometry(),
        )
        try:
            self._heartbeat_after_id = self.root.after(30000, self._heartbeat)  # 30 с
        except Exception:
            self._heartbeat_after_id = None

    def _find_whisperx(self) -> None:
        """Найти whisperx и обновить статус."""
        from ..engines.whisperx_resolve import resolve_whisperx
        path = resolve_whisperx(self.whisperx_path_var.get().strip())
        if path:
            self.whisperx_path_var.set(path)
            self.whisperx_status_var.set(f"✓ {path}")
            self._save_settings()
            log.info(f"[WHISPER] Found: {path}")
        else:
            self.whisperx_status_var.set("✗ Не найден")
            # Движок использует MLX Whisper и WhisperX не вызывает — это не ошибка.
            log.debug("[WHISPER] WhisperX не найден (движку MLX он не нужен)")

    def _browse_whisperx(self) -> None:
        """Ручной выбор whisperx бинарника."""
        initial_dir = self._browse_initialdir(self._value_of(getattr(self, "whisperx_path_var", None)))

        def done(path: str) -> None:
            if not path:
                return
            self._remember_browse_dir(path)
            self.whisperx_path_var.set(path.strip())
            self.whisperx_status_var.set(f"✓ {path}")
            self._save_settings()
            log.info(f"[WHISPER] Manual path set: {path}")

        self._choose_file(initial_dir, "Выберите whisperx", done)

    def _kill_ffmpeg_processes(self) -> None:
        """Убить дочерние ffmpeg/ffprobe текущего процесса (не чужие в системе)."""
        try:
            import psutil
            current = psutil.Process(os.getpid())
            for child in current.children(recursive=True):
                try:
                    name = (child.name() or "").lower()
                    if "ffmpeg" in name or "ffprobe" in name:
                        child.kill()
                        log.info("[GUI] Убит дочерний %s pid=%s", name, child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            log.warning("[GUI] psutil не установлен — пропуск убийства ffmpeg")
        except Exception as e:
            log.warning("[GUI] Не удалось убить ffmpeg: %s", e)

    def _imo_path_accessible(self, path: str) -> bool:
        """Файл существует — условие для умной галочки Intro/Middle/Outro.

        Cache 30s: на сетевых томах isfile дорогой; не дёргаем диск каждые 5с.
        """
        path = (path or "").strip()
        if not path:
            return False
        try:
            path = os.path.expanduser(path)
        except Exception:
            return False

        # Cache
        cache = getattr(self, "_imo_access_cache", None)
        if cache is not None:
            entry = cache.get(path)
            if entry is not None:
                ts, ok = entry
                if time.time() - ts < 30.0:
                    return ok

        ok = False
        try:
            if os.path.isfile(path):
                ok = True
            else:
                try:
                    import unicodedata
                    for form in ("NFC", "NFD"):
                        norm = unicodedata.normalize(form, path)
                        if norm != path and os.path.isfile(norm):
                            ok = True
                            break
                except Exception:
                    pass
        except OSError:
            ok = False

        if cache is not None:
            cache[path] = (time.time(), ok)
            # Не раздуваем cache
            if len(cache) > 64:
                oldest = sorted(cache.items(), key=lambda x: x[1][0])[:16]
                for k, _ in oldest:
                    cache.pop(k, None)
        return ok

    def _imo_cb_set_enabled(self, cb, enabled: bool) -> None:
        """Включить/выключить ttk.Checkbutton надёжно (macOS + theme clam)."""
        try:
            if enabled:
                try:
                    cb.state(["!disabled"])
                except Exception:
                    pass
                try:
                    cb.configure(state=tk.NORMAL)
                except Exception:
                    pass
            else:
                try:
                    cb.state(["disabled"])
                except Exception:
                    pass
                try:
                    cb.configure(state=tk.DISABLED)
                except Exception:
                    pass
        except tk.TclError:
            pass

    def _imo_cb_is_disabled(self, cb) -> bool:
        """disabled ли ttk.Checkbutton."""
        try:
            if hasattr(cb, "instate"):
                return bool(cb.instate(["disabled"]))
        except Exception:
            pass
        try:
            return "disabled" in str(cb.cget("state")).lower()
        except Exception:
            return True

    def _sync_imo_checkboxes(self) -> None:
        """Intro/Middle/Outro на основной вкладке (h / v / s).

        Умная логика (исходный замысел):
        - файл ЕСТЬ → NORMAL; при переходе disabled→enabled auto True
          (пользователь может снять галочку вручную);
        - пути нет / файла нет → DISABLED + False;
        - intro/mid/outro × h/v/s независимы.
        """
        if not hasattr(self, "_imo_checkbuttons"):
            return
        for (prefix, key), cb in list(self._imo_checkbuttons.items()):
            if key == "middle":
                path_attr = f"{prefix}_mid_path"
            elif key == "intro":
                path_attr = f"{prefix}_intro_path"
            else:
                path_attr = f"{prefix}_outro_path"
            path_var = getattr(self, path_attr, None)
            path = ""
            if path_var is not None and hasattr(path_var, "get"):
                try:
                    path = (path_var.get() or "").strip()
                except Exception:
                    path = ""
            ok = self._imo_path_accessible(path)
            bool_var = getattr(self, f"{prefix}_{key}", None)
            try:
                was_disabled = self._imo_cb_is_disabled(cb)
                if ok:
                    if was_disabled and bool_var is not None:
                        bool_var.set(True)
                    self._imo_cb_set_enabled(cb, True)
                else:
                    if bool_var is not None:
                        bool_var.set(False)
                    self._imo_cb_set_enabled(cb, False)
                    if path:
                        log.debug(
                            "[GUI] IMO %s_%s path set but file missing: %r",
                            prefix, key, path,
                        )
            except tk.TclError:
                pass

    def _bind_imo_path_traces(self) -> None:
        if getattr(self, "_imo_traces_bound", False):
            return
        self._imo_traces_bound = True

        def _on_path_write(*_args):
            try:
                self._sync_imo_checkboxes()
            except Exception:
                pass

        for prefix in ("h", "v", "s"):
            for kind in ("intro_path", "mid_path", "outro_path"):
                attr = f"{prefix}_{kind}"
                var = getattr(self, attr, None)
                if var is not None and hasattr(var, "trace_add"):
                    try:
                        var.trace_add("write", _on_path_write)
                    except Exception:
                        pass
        self._schedule_imo_rescan()

    def _schedule_imo_rescan(self) -> None:
        """Редкий rescan (60 с). Traces уже обновляют галочки при изменении пути.
        Нужен только для сетевых томов, которые появились позже."""
        if getattr(self, "_closing", False):
            return
        try:
            self._sync_imo_checkboxes()
        except Exception:
            pass
        try:
            self._imo_rescan_after_id = self.root.after(60000, self._schedule_imo_rescan)  # 60 с
        except Exception:
            self._imo_rescan_after_id = None

    def _var_get(self, name: str) -> str:

        """Безопасно прочитать tk-переменную по имени."""
        obj = getattr(self, name, None)
        if obj is not None and hasattr(obj, "get"):
            val = obj.get()
            return val.strip() if isinstance(val, str) else str(val or "")
        return ""

    def _load_settings(self) -> None:
        """Загрузить настройки из JSON. Только .set() на tk-переменные."""
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("last_browse_dir"):
                self._last_browse_dir = str(data["last_browse_dir"])
            for key, value in data.items():
                if isinstance(value, str):
                    value = value.strip()
                # 1) атрибут = key (h_intro, h_intro_path, ...)
                if hasattr(self, key):
                    obj = getattr(self, key)
                    if hasattr(obj, "set"):
                        obj.set(value)
                        continue
                # 2) атрибут = key + "_var" (audio → audio_var)
                var_name = f"{key}_var"
                if hasattr(self, var_name):
                    obj = getattr(self, var_name)
                    if hasattr(obj, "set"):
                        obj.set(value)
                        continue
            log.info("[GUI] Настройки загружены из %s", SETTINGS_FILE)
            # r51 packaging fields → settings + vars
            for attr in (
                "playlist_season_url", "playlist_block_url", "playlist_block_label",
                "required_hashtags", "prompt_extra", "prompt_body",
            ):
                if attr in data and isinstance(data[attr], str):
                    setattr(self.settings, attr, data[attr])
            if "append_playlist_footer" in data:
                self.settings.append_playlist_footer = bool(data["append_playlist_footer"])
            if "shorts_use_template" in data:
                self.settings.shorts_use_template = bool(data["shorts_use_template"])
            if "shorts_desc_max_len" in data:
                try:
                    self.settings.shorts_desc_max_len = int(data["shorts_desc_max_len"])
                except (TypeError, ValueError):
                    pass
            if hasattr(self, "playlist_season_url_var"):
                self.playlist_season_url_var.set(getattr(self.settings, "playlist_season_url", "") or self.playlist_season_url_var.get())
                self.playlist_block_url_var.set(getattr(self.settings, "playlist_block_url", "") or self.playlist_block_url_var.get())
                self.playlist_block_label_var.set(getattr(self.settings, "playlist_block_label", "") or self.playlist_block_label_var.get())
                self.required_hashtags_var.set(getattr(self.settings, "required_hashtags", "") or self.required_hashtags_var.get())
                self.append_playlist_footer_var.set(bool(getattr(self.settings, "append_playlist_footer", True)))
                self.shorts_use_template_var.set(bool(getattr(self.settings, "shorts_use_template", True)))
                self.shorts_desc_max_len_var.set(str(getattr(self.settings, "shorts_desc_max_len", 280)))
            if hasattr(self, "speech_rate_mode_var"):
                # JSON может хранить каноническое «wpm»/«coefficient» или подпись —
                # в комбобоксе всегда показываем подпись.
                self.speech_rate_mode_var.set(
                    speech_rate_mode_display(self.speech_rate_mode_var.get())
                )
            self._wake_network_paths()
            self._sync_imo_checkboxes()
            self._bind_imo_path_traces()
        except Exception as e:
            log.warning("[GUI] Ошибка загрузки настроек: %s", e)

    def _wake_network_paths(self, wait_seconds: float = 0.0, *, try_open: bool = False) -> list[str]:
        """Разбудить сетевые тома (/Volumes/...) и дождаться их появления.

        macOS не монтирует SMB/AFP, пока к папке не обратятся: стучимся в корень
        тома, при необходимости просим Finder открыть его (open -g, без фокуса)
        и ждём. Возвращает пути, которые так и остались недоступны.
        """
        paths = []
        for name in PATH_VARS:
            if not hasattr(self, name):
                continue
            var = getattr(self, name)
            if not hasattr(var, "get"):
                continue
            p = (var.get() or "").strip()
            if p:
                paths.append(p)

        volumes: set[str] = set()
        for p in paths:
            if p.startswith("/Volumes/"):
                parts = p.split("/")
                if len(parts) >= 3 and parts[2]:
                    volumes.add("/Volumes/" + parts[2])

        for vol in sorted(volumes):
            if os.path.isdir(vol):
                log.info("[GUI] Том доступен: %s", vol)

        def _trigger() -> None:
            import subprocess
            opened: set[str] = set()
            for vol in sorted(volumes):
                if os.path.isdir(vol):
                    continue
                try:
                    os.listdir(vol)  # если том уже поднялся — этого достаточно
                except OSError:
                    pass
                if try_open and vol not in opened:
                    opened.add(vol)
                    try:
                        subprocess.run(["open", "-g", vol], capture_output=True,
                                       text=True, timeout=10, check=False)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("[GUI] open %s не сработал: %s", vol, exc)
            if not try_open:
                return
            # Том может быть смонтирован, а конкретная папка на нём — «спать».
            # Открываем в Finder глубочайшую существующую папку пути.
            for p in paths:
                if os.path.exists(p):
                    continue
                ancestor = _deepest_existing(os.path.dirname(p) or p)
                if not ancestor:
                    continue
                mounted_root = ""
                parts = ancestor.split("/")
                if len(parts) >= 3 and parts[1] == "Volumes":
                    mounted_root = "/".join(parts[:3])
                if not mounted_root or not os.path.isdir(mounted_root):
                    continue  # том не смонтирован — им займётся ветка выше
                if ancestor not in opened:
                    opened.add(ancestor)
                    try:
                        subprocess.run(["open", "-g", ancestor], capture_output=True,
                                       text=True, timeout=10, check=False)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("[GUI] open %s не сработал: %s", ancestor, exc)

        missing = _wait_for_paths(
            paths, wait_seconds, log_fn=self._log, trigger=_trigger,
            pump=(self.root.update_idletasks if hasattr(self, "root") else None),
        )
        volumes_missing = [p for p in missing if p.startswith("/Volumes/")]
        if volumes_missing:
            log.warning(
                "[GUI] Пути пока недоступны (откройте том в Finder или подождите): %s",
                "; ".join(volumes_missing[:5]),
            )
        other_missing = [p for p in missing if not p.startswith("/Volumes/")]
        if other_missing:
            log.debug("[GUI] Недоступные локальные пути: %s", "; ".join(other_missing[:5]))
        return missing

    def _save_settings(self) -> None:
        """Сохранить текущие настройки в JSON (merge — не затираем packaging/prompt_extra)."""
        try:
            data = {}
            if os.path.exists(SETTINGS_FILE):
                try:
                    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                except Exception:
                    data = {}
            for name in PATH_VARS + OTHER_VARS + BOOL_VARS:
                if not hasattr(self, name):
                    continue
                var = getattr(self, name)
                if not hasattr(var, "get"):
                    continue
                value = var.get()
                if isinstance(value, str):
                    value = value.strip()
                key = name[:-4] if name.endswith("_var") else name
                data[key] = value
            # r51: packaging всегда в том же файле (иначе затирается)
            data.update({
                "playlist_season_url": getattr(self.settings, "playlist_season_url", "") or (
                    self.playlist_season_url_var.get().strip() if hasattr(self, "playlist_season_url_var") else ""
                ),
                "playlist_block_url": getattr(self.settings, "playlist_block_url", "") or (
                    self.playlist_block_url_var.get().strip() if hasattr(self, "playlist_block_url_var") else ""
                ),
                "playlist_block_label": getattr(self.settings, "playlist_block_label", "") or (
                    self.playlist_block_label_var.get().strip() if hasattr(self, "playlist_block_label_var") else ""
                ),
                "required_hashtags": getattr(self.settings, "required_hashtags", "") or (
                    self.required_hashtags_var.get().strip() if hasattr(self, "required_hashtags_var") else ""
                ),
                "append_playlist_footer": bool(
                    self.append_playlist_footer_var.get()
                    if hasattr(self, "append_playlist_footer_var")
                    else getattr(self.settings, "append_playlist_footer", True)
                ),
                "shorts_use_template": bool(
                    self.shorts_use_template_var.get()
                    if hasattr(self, "shorts_use_template_var")
                    else getattr(self.settings, "shorts_use_template", True)
                ),
                "shorts_desc_max_len": int(
                    getattr(self.settings, "shorts_desc_max_len", 280) or 280
                ),
                "prompt_extra": str(getattr(self.settings, "prompt_extra", "") or ""),
                "prompt_body": str(getattr(self.settings, "prompt_body", "") or ""),
                "last_browse_dir": str(getattr(self, "_last_browse_dir", "") or ""),
            })
            try:
                data["shorts_desc_max_len"] = int(
                    str(self.shorts_desc_max_len_var.get()).strip() or "280"
                )
            except Exception:
                pass
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("[GUI] Настройки сохранены в %s", SETTINGS_FILE)
        except Exception as e:
            log.warning("[GUI] Ошибка сохранения настроек: %s", e)
