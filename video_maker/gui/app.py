# VideoMaker FIX | 2026.09.01-r4 | 2026-09-01
# CHANGED: галочки Intro/Middle/Outro:
#   - файл нет / SSD отключён → серые (disabled), выключены
#   - файл доступен → авто-включение + active
#   - rescan каждые 5с (подключили SSD)
# PREV: cf44e6a (галочки всегда кликабельны, без проверки доступа к файлу)
# REPLACE: video_maker/gui/app.py

"""Главное окно — Tkinter GUI с тёмной темой и подробным логированием."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import tkinter as tk
import traceback
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
    "primary":      "#6366F1",
    "primary_dim":  "#4F46E5",
    "accent":       "#059669",
    "accent_dim":   "#047857",
    "text":         "#F8FAFC",
    "text_dim":     "#94A3B8",
    "text_muted":   "#64748B",
    "border":       "#334155",
    "input_bg":     "#0F172A",
    "input_fg":     "#F8FAFC",
    "selected":     "#6366F1",
    "error":        "#DC2626",
    "log_bg":       "#0D1117",
}

# ─── Settings persistence ───────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".video_maker_settings.json")

PATH_VARS = [
    "audio_var",
    "broll_h_var",
    "broll_v_var",
    "bg_var",
    "bgm_var",
    "imo_folder_var",
    "cover_h_var",
    "cover_v_var",
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
    "h_mid_position", "h_mid_custom_time",
    "h_outro_duration", "h_outro_position", "h_outro_custom_time",
    "v_intro_duration", "v_intro_position", "v_intro_custom_time",
    "v_mid_position", "v_mid_custom_time",
    "v_outro_duration", "v_outro_position", "v_outro_custom_time",
    "s_intro_duration", "s_intro_position", "s_intro_custom_time",
    "s_mid_position", "s_mid_custom_time",
    "s_outro_duration", "s_outro_position", "s_outro_custom_time",
]

BOOL_VARS = [
    "h_intro", "h_middle", "h_outro", "h_hooks", "h_subs", "h_strong",
    "v_intro", "v_middle", "v_outro", "v_hooks", "v_subs", "v_strong",
    "s_intro", "s_middle", "s_outro", "s_hooks", "s_subs", "s_strong",
    "voice_enhance_var", "add_bgm_var", "intro_gemini_var", "keep_temp_var",
    "prevent_sleep_var", "shutdown_when_done_var",
]


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
        # Полноэкранное окно по высоте
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f"960x{screen_h - 60}")
        self.root.minsize(860, 680)
        self.root.configure(bg=COLORS["bg"])
        log.info("[GUI] Окно настроено: 960x780, min 860x680")

        self.settings = Settings.from_env()
        log.info(f"[GUI] Настройки загружены: model={self.settings.gemini_model}, "
                 f"whisper={self.settings.whisper_model}")

        self.model_var = tk.StringVar(value=self.settings.gemini_model)
        self.running = False
        self.cancel_event = threading.Event()
        self.whisperx_path_var = tk.StringVar(value=self.settings.whisperx_path)
        self.whisperx_status_var = tk.StringVar(value="Автопоиск...")
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
        log.info("[GUI] _build_ui() завершён")

        # Запуск heartbeat для мониторинга состояния окна
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

    def _on_close(self) -> None:
        """Обработчик закрытия окна."""
        log.info("[GUI] WM_DELETE_WINDOW running=%s", self.running)
        log.info("[GUI] ╔══════════════════════════════════════════════╗")
        log.info("[GUI] ║ WM_DELETE_WINDOW — пользователь закрывает окно ║")
        log.info("[GUI] ╚══════════════════════════════════════════════╝")

        if self.running:
            log.info("[GUI] Пайплайн выполняется (self.running=True)")
            answer = messagebox.askyesno(
                "Подтверждение",
                "Пайплайн выполняется. Завершить?"
            )
            log.info(f"[GUI] Ответ пользователя: {answer}")
            if not answer:
                log.info("[GUI] Отмена закрытия — окно остаётся открытым")
                return
            self.cancel_event.set()
            self._kill_ffmpeg_processes()
            try:
                from ..engines.power import prevent_sleep_stop
                prevent_sleep_stop()
            except Exception:
                pass

        # Всегда: и при running=False
        self._save_settings()
        log.info("[GUI] Уничтожение корневого окна...")
        try:
            self.root.quit()
        except Exception:
            pass
        self.root.destroy()
        log.info("[GUI] root.destroy() выполнен")
        log.info("[GUI] ╔══════════════════════════════════════════════╗")
        log.info("[GUI] ║             ПРИЛОЖЕНИЕ ЗАКРЫТО               ║")
        log.info("[GUI] ╚══════════════════════════════════════════════╝")

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
            relief="solid",
            padding=6,
        )

        style.configure(
            "TButton",
            background=COLORS["card"],
            foreground=COLORS["text"],
            borderwidth=1,
            relief="solid",
            padding=(12, 6),
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
            thickness=6,
        )

        log.info("[GUI] _setup_theme(): все стили применены")

    # ─── UI ───────────────────────────────────────────────────────────────

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

        log.info("[GUI] _build_ui(): интерфейс построен")

    def _build_main_tab(self, parent) -> None:
        """Вкладка основных настроек: слева настройки, справа чёрное поле серии."""
        columns = ttk.Frame(parent)
        columns.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        columns.columnconfigure(0, weight=3)
        columns.columnconfigure(1, weight=2)
        columns.rowconfigure(0, weight=1)

        left_col = ttk.Frame(columns)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        right_col = ttk.Frame(columns)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_col.rowconfigure(0, weight=1)
        right_col.columnconfigure(0, weight=1)

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

        broll_frame = self._add_section(left_col, "B-roll видео")
        self._add_browse_row(broll_frame, "Горизонтальный:", "broll_h_var", "dir")
        self._add_browse_row(broll_frame, "Вертикальный (9:16):", "broll_v_var", "dir")
        self.imo_folder_var = tk.StringVar()
        self._add_file_section(
            left_col, "Фон для вертикального видео",
            [("Файл:", "bg_var", "file", [("Изображения/Видео", "*.jpg *.jpeg *.png *.mp4 *.mov")])],
        )
        self._add_file_section(
            left_col, "Фоновая музыка",
            [("Папка:", "bgm_var", "dir", [])],
        )
        self._add_file_section(
            left_col, "Папка вывода",
            [("Папка:", "output_var", "dir", [])],
        )

        audio_settings = self._add_section(left_col, "Настройки аудио")
        self.voice_enhance_var = tk.BooleanVar(value=True)
        self.add_bgm_var = tk.BooleanVar(value=True)
        self.intro_gemini_var = tk.BooleanVar(value=True)
        self.keep_temp_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(audio_settings, text="Усилить голос", variable=self.voice_enhance_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(audio_settings, text="Добавить BGM", variable=self.add_bgm_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(audio_settings, text="Интро: Gemini выбирает", variable=self.intro_gemini_var).pack(anchor="w", pady=2)
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
            ("Shorts", "s", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
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
        self.caption_style_var = tk.StringVar(value="auto_aisie")
        ttk.Combobox(
            row_c, textvariable=self.caption_style_var, state="readonly", width=24,
            values=[
                "auto_aisie", "hormozi", "hormozi_green", "tiktok_box",
                "clean_pro", "bold_pop", "cliffhanger",
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
        ttk.Label(lufs_frame, text="Целевой LUFS:", width=14).pack(side=tk.LEFT)
        self.target_lufs_var = tk.StringVar(value="-14.0")
        ttk.Entry(lufs_frame, textvariable=self.target_lufs_var, width=8, font=("SF Pro Text", 11)).pack(side=tk.LEFT)
        ttk.Label(lufs_frame, text="(YouTube/TikTok: -14)").pack(side=tk.LEFT, padx=(8, 0))

        # ─── Правая колонка: чёрное поле названия серии на всю высоту ───
        series_frame = self._add_section(right_col, "Название серии / папка результата", expand=True)
        series_frame.configure(padding=8)
        self.series_var = tk.StringVar()
        self.series_entry = tk.Text(
            series_frame,
            wrap=tk.WORD,
            font=("SF Pro Text", 13),
            bg="#000000",
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
        self.series_entry.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
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
        self._pipeline_t0 = 0.0

        # === Лог ===
        log_frame = self._add_section(parent, "Лог", expand=True)
        log_frame.configure(padding=4)

        self.log_text = tk.Text(
            log_frame,
            height=10,
            state=tk.DISABLED,
            wrap=tk.WORD,
            bg=COLORS["log_bg"],
            fg=COLORS["text_dim"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary"],
            font=("SF Mono", 10),
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=8,
        )
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

    def _build_imo_tab(self, parent) -> None:
        """Вкладка Intro / Middle / Outro для каждого формата."""
        scroll = ttk.Frame(parent)
        scroll.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

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

        ttk.Label(parent, text=label_text, width=14).pack(side=tk.LEFT)
        ttk.Entry(parent, textvariable=var, font=("SF Pro Text", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8)
        )

        if kind == "dir":
            cmd = lambda v=var, n=var_name: self._browse_dir(v, n)
        else:
            cmd = lambda v=var, n=var_name, ft=filetypes: self._browse_file(v, n, ft)
        ttk.Button(parent, text="Обзор...", command=cmd).pack(side=tk.LEFT)

    def _browse_dir(self, var: tk.StringVar, name: str) -> None:
        log.info(f"[GUI] Диалог выбора папки: {name}")
        path = filedialog.askdirectory()
        log.info(f"[GUI] Выбрана папка: {path or '(пусто)'}")
        if path:
            var.set(path.strip())
            log.info(f"[GUI] {name} = {path}")
            self._save_settings()

    def _browse_file(self, var: tk.StringVar, name: str, filetypes=None) -> None:
        log.info(f"[GUI] Диалог выбора файла: {name}")
        types = [("Все файлы", "*.*")]
        if filetypes:
            types = filetypes + types
        path = filedialog.askopenfilename(filetypes=types)
        log.info(f"[GUI] Выбран файл: {path or '(пусто)'}")
        if path:
            var.set(path.strip())
            log.info(f"[GUI] {name} = {path}")
            self._save_settings()
            self._sync_imo_checkboxes()

    # ─── Файловые диалоги (обратная совместимость) ────────────────────────

    def _choose_audio(self) -> None:
        """Обратная совместимость → единый диалог файла/папки."""
        self._browse_audio()

    def _browse_audio(self) -> None:
        """Одна кнопка: выбрать файл или папку. Тип определяется автоматически, инфо — в лог."""
        log.info("[GUI] Диалог выбора аудио (файл или папка)")
        path = self._ask_file_or_directory(
            title="Выберите аудиофайл или папку с аудио",
            filetypes=[("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma"), ("Все файлы", "*.*")],
        )
        if not path:
            log.info("[GUI] Выбор аудио отменён")
            return
        path = path.strip()
        self.audio_var.set(path)
        log.info(f"[GUI] audio_var = {path}")
        self._save_settings()
        self._report_audio_selection(path)

    def _ask_file_or_directory(self, title: str = "Выберите файл или папку", filetypes=None) -> str:
        """
        Диалог, позволяющий выбрать файл или папку.
        На macOS — через AppleScript (NSOpenPanel: files + directories).
        Иначе — компактное окно с двумя действиями (один раз нажать).
        """
        # --- macOS: нативный панель с canChooseFiles + canChooseDirectories ---
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
                    if panel.runModal() == NSModalResponseOK:
                        urls = panel.URLs()
                        if urls and len(urls) > 0:
                            return str(urls[0].path())
                    return ""
                except Exception:
                    pass
            except Exception as e:
                log.debug("[GUI] macOS native panel unavailable: %s", e)

        # --- Fallback: маленькое окно выбора типа (одна кнопка «Обзор» → один клик тип) ---
        result = {"path": ""}

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=COLORS["bg"])
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Что выбрать?", style="Section.TLabel").pack(anchor="w", pady=(0, 10))

        def pick_file():
            types = filetypes or [("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.wma"), ("Все файлы", "*.*")]
            p = filedialog.askopenfilename(parent=win, title="Выберите аудиофайл", filetypes=types)
            if p:
                result["path"] = p
            win.destroy()

        def pick_dir():
            p = filedialog.askdirectory(parent=win, title="Выберите папку с аудио")
            if p:
                result["path"] = p
            win.destroy()

        def cancel():
            win.destroy()

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
        return result["path"]

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
        """Открыть окно настроек (Gemini + WhisperX)."""
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        win.geometry("520x520")
        win.configure(bg=COLORS["bg"])
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Модель Gemini (по умолчанию 2.5-flash):", style="Section.TLabel").pack(anchor="w")
        ttk.Combobox(
            frame, textvariable=self.model_var,
            values=[
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash",
                "gemini-3.6-pro",
                "gemini-3.6-flash-lite",
                "gemini-1.5-flash",
            ],
            state="readonly", font=("SF Pro Text", 10),
        ).pack(fill=tk.X, pady=(4, 12))

        ttk.Label(
            frame,
            text="API ключи Gemini (каждый с новой строки или через запятую):",
            style="Section.TLabel",
        ).pack(anchor="w")
        keys_list = list(self.settings.gemini_api_keys or [])
        if not keys_list and self.settings.gemini_api_key:
            keys_list = [self.settings.gemini_api_key]
        keys_str = "\n".join(keys_list)
        keys_box = tk.Text(frame, height=5, font=("SF Pro Text", 10), wrap="word")
        keys_box.pack(fill=tk.X, pady=(4, 4))
        keys_box.insert("1.0", keys_str)
        self._settings_keys_box = keys_box
        ttk.Label(
            frame,
            text="При 429/исчерпании лимита — сразу следующий ключ. Последний рабочий запоминается.",
            font=("SF Pro Text", 9),
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(frame, text="WhisperX", style="Section.TLabel").pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(frame); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Модель:", width=14).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_model_var, values=["tiny","base","small","medium","large-v2","large-v3","large-v3-turbo"], state="readonly", width=14).pack(side=tk.LEFT)
        row = ttk.Frame(frame); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Язык:", width=14).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_lang_var, values=["ru","en","auto"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(frame); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Устройство:", width=14).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_dev_var, values=["auto","cpu","mps","cuda"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(frame); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Compute type:", width=14).pack(side=tk.LEFT)
        ttk.Combobox(row, textvariable=self.whisper_comp_var, values=["auto","int8","float16","float32"], state="readonly", width=10).pack(side=tk.LEFT)
        row = ttk.Frame(frame); row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text="Путь:", width=14).pack(side=tk.LEFT)
        ttk.Label(row, textvariable=self.whisperx_status_var).pack(side=tk.LEFT)
        ttk.Button(row, text="Найти", width=8, command=self._find_whisperx).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Вручную", width=8, command=self._browse_whisperx).pack(side=tk.LEFT)

        def save():
            self.settings.gemini_model = self.model_var.get()
            raw = self._settings_keys_box.get("1.0", "end").strip()
            if raw:
                import re as _re
                keys = [k.strip() for k in _re.split(r"[\n,;]+", raw) if k.strip()]
                self.settings.gemini_api_keys = keys
                self.settings.gemini_api_key = keys[0] if keys else ""
            self.settings.whisper_model = self.whisper_model_var.get() or "large-v3"
            self.settings.whisper_language = self.whisper_lang_var.get()
            self.settings.whisper_device = self.whisper_dev_var.get()
            self.settings.whisper_compute_type = self.whisper_comp_var.get()
            if hasattr(self, "whisperx_path_var"):
                self.settings.whisperx_path = self.whisperx_path_var.get()
            log.info(f"[SETTINGS] Gemini={self.settings.gemini_model} Whisper={self.settings.whisper_model}")
            self._save_settings()
            win.destroy()

        ttk.Button(frame, text="Сохранить", style="Accent.TButton", command=save).pack(fill=tk.X, pady=(16, 0))

    # ─── Выбор файлов ────────────────────────────────────────────────────

    def _choose_broll_h(self) -> None:
        self._browse_dir(self.broll_h_var, "broll_h_var")

    def _choose_bg(self) -> None:
        self._browse_file(self.bg_var, "bg_var", [("Изображения/Видео", "*.jpg *.jpeg *.png *.mp4 *.mov")])

    def _choose_bgm(self) -> None:
        self._browse_dir(self.bgm_var, "bgm_var")

    def _choose_output(self) -> None:
        self._browse_dir(self.output_var, "output_var")

    def _choose_imo(self) -> None:
        self._browse_dir(self.imo_folder_var, "imo_folder_var")

    def _choose_cover_h(self) -> None:
        self._browse_file(self.cover_h_var, "cover_h_var", [("Изображения", "*.jpg *.jpeg *.png")])

    def _choose_cover_v(self) -> None:
        self._browse_file(self.cover_v_var, "cover_v_var", [("Изображения", "*.jpg *.jpeg *.png")])

    # ─── Логирование ─────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """Потокобезопасная запись в лог."""
        timestamp = time.strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        log.info(msg)

        def _write():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, full_msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        try:
            self.root.after(0, _write)
        except RuntimeError:
            pass


    CAPTION_HELP = {
        "auto_aisie": "Цвет активного слова по весу AISIE (L1–L4). Karaoke 2–4 слова.",
        "hormozi": "Белые слова, активное — ярко-жёлтое, лёгкий pop. Классика Shorts.",
        "hormozi_green": "Как hormozi, акцент неон-зелёный.",
        "tiktok_box": "Текст на тёмной подложке-боксе, без scale-pop.",
        "clean_pro": "YouTube: 1 строка, 2–3 слова, тень без контура, без увеличения.",
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

    def _start(self) -> None:
        """Запустить пайплайн."""
        log.info("[GUI] ╔══════════════════════════════════════════════╗")
        log.info("[GUI] ║       КНОПКА «СОЗДАТЬ ВИДЕО» НАЖАТА         ║")
        log.info("[GUI] ╚══════════════════════════════════════════════╝")

        if self.running:
            log.warning("[GUI] Пайплайн уже выполняется — показываем предупреждение")
            messagebox.showwarning("Внимание", "Пайплайн уже запущен")
            return

        log.info("[GUI] Сбор настроек из GUI...")
        self.settings.audio_path = self.audio_var.get()
        self.settings.broll_horizontal = self.broll_h_var.get()
        self.settings.broll_vertical = self.broll_v_var.get()
        self.settings.vertical_background = self.bg_var.get()
        self.settings.bgm_folder = self.bgm_var.get()
        self.settings.intro_middle_outro_folder = self.imo_folder_var.get()
        self.settings.cover_horizontal = self.cover_h_var.get()
        self.settings.cover_vertical = self.cover_v_var.get()
        self.settings.series_name = (self.series_entry.get("1.0", "end").strip() if hasattr(self, "series_entry") else self.series_var.get())
        self.settings.gemini_model = self.model_var.get()
        self.settings.intro_gemini = self.intro_gemini_var.get()

        # WhisperX settings
        self.settings.whisperx_path = self.whisperx_path_var.get()
        self.settings.whisper_model = self.whisper_model_var.get() or "large-v3"
        self.settings.whisper_language = self.whisper_lang_var.get()
        self.settings.whisper_device = self.whisper_dev_var.get()
        self.settings.whisper_compute_type = self.whisper_comp_var.get()

        # Other settings
        self.settings.keep_temp_files = self.keep_temp_var.get()
        self.settings.prevent_sleep = self.prevent_sleep_var.get()
        self.settings.shutdown_when_done = self.shutdown_when_done_var.get()
        try:
            self.settings.target_lufs = float(self.target_lufs_var.get().strip() or -14.0)
        except (ValueError, TypeError, AttributeError):
            self.settings.target_lufs = -14.0
            log.warning("[GUI] Некорректный target_lufs, используем -14.0")

        log.info(f"[GUI] audio_path = {self.settings.audio_path}")
        log.info(f"[GUI] broll_horizontal = {self.settings.broll_horizontal}")
        log.info(f"[GUI] broll_vertical = {self.settings.broll_vertical}")
        log.info(f"[GUI] vertical_background = {self.settings.vertical_background}")
        log.info(f"[GUI] bgm_folder = {self.settings.bgm_folder}")
        log.info(f"[GUI] imo_folder = {self.settings.intro_middle_outro_folder}")
        log.info(f"[GUI] output_folder = {self.output_var.get() or '(auto)'}")
        log.info(f"[GUI] gemini_model = {self.settings.gemini_model}")
        log.info(f"[GUI] series_name = {self.settings.series_name}")
        log.info(f"[GUI] whisper_language = {self.settings.whisper_language}")
        if hasattr(self, "caption_style_var"):
            log.info(f"[GUI] caption_style = {self.caption_style_var.get()}")
        if hasattr(self, "hook_style_var"):
            log.info(f"[GUI] hook_style = {self.hook_style_var.get()}")
        log.info(f"[GUI] whisper_device = {self.settings.whisper_device}")
        log.info(f"[GUI] whisper_compute_type = {self.settings.whisper_compute_type}")
        log.info(f"[GUI] keep_temp_files = {self.settings.keep_temp_files}")
        log.info(f"[GUI] target_lufs = {self.settings.target_lufs}")

        self.settings.h_enable_intro = self.h_intro.get()
        self.settings.h_enable_middle = self.h_middle.get()
        self.settings.h_enable_outro = self.h_outro.get()
        self.settings.h_enable_hooks = self.h_hooks.get()
        self.settings.h_enable_subtitles = self.h_subs.get()
        self.settings.h_enable_strong_words = self.h_strong.get()
        self.settings.v_enable_intro = self.v_intro.get()
        self.settings.v_enable_middle = self.v_middle.get()
        self.settings.v_enable_outro = self.v_outro.get()
        self.settings.v_enable_hooks = self.v_hooks.get()
        self.settings.v_enable_subtitles = self.v_subs.get()
        self.settings.v_enable_strong_words = self.v_strong.get()
        self.settings.s_enable_intro = self.s_intro.get()
        self.settings.s_enable_middle = self.s_middle.get()
        self.settings.s_enable_outro = self.s_outro.get()
        self.settings.s_enable_hooks = self.s_hooks.get()
        self.settings.s_enable_subtitles = self.s_subs.get()
        self.settings.s_enable_strong_words = self.s_strong.get()

        self.settings.voice_enhance = self.voice_enhance_var.get()
        self.settings.add_bgm = self.add_bgm_var.get()

        log.info(f"[GUI] voice_enhance = {self.settings.voice_enhance}")
        log.info(f"[GUI] add_bgm = {self.settings.add_bgm}")

        # Папка вывода: явная, либо родитель файла, либо сама папка аудио
        out = self.output_var.get().strip()
        if not out and self.settings.audio_path:
            if os.path.isdir(self.settings.audio_path):
                out = self.settings.audio_path
            else:
                out = os.path.dirname(self.settings.audio_path)
        self.settings.output_folder = out
        log.info(f"[GUI] output_folder (final) = {self.settings.output_folder}")

        # Persist GUI state before validate / pipeline
        self._save_settings()

        # Переконфигурировать логирование в выходную папку
        if self.settings.output_folder and os.path.isdir(self.settings.output_folder):
            log_file = os.path.join(self.settings.output_folder, "videomaker.log")
            from video_maker.main import setup_logging
            setup_logging(log_file)
            log.info(f"[GUI] Логирование перенастроено в {log_file}")

        if hasattr(self, "_wake_network_paths"):
            self._wake_network_paths()
        errors = self.settings.validate()
        if errors:
            log.error(f"[GUI] Ошибки валидации: {errors}")
            messagebox.showerror("Ошибки", "\n".join(errors))
            return

        # Список аудиофайлов (один или из папки, + 1 уровень подпапок)
        self._audio_queue = Settings.collect_audio_files(self.settings.audio_path)
        log.info(f"[GUI] Аудиофайлов к обработке: {len(self._audio_queue)}")
        self._log(f"[АУДИО] К обработке: {len(self._audio_queue)} файл(ов)")
        for i, pth in enumerate(self._audio_queue, 1):
            log.info(f"[GUI]   {i}. {pth}")
            self._log(f"[АУДИО]   {i}. {os.path.basename(pth)}")

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
        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.cancel_event.clear()
        self._pipeline_t0 = time.time()
        self._start_progress_ticker()
        log.info("[GUI] self.running = True, кнопка Старт заблокирована, Отмена включена")

        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        log.info(f"[GUI] Создан поток пайплайна: {thread.name}")
        thread.start()
        log.info("[GUI] Поток запущен")

    def _run_pipeline(self) -> None:
        """Выполнить пайплайн в отдельном потоке (один файл или очередь из папки)."""
        log.info("[PIPELINE] ═══════════════════════════════════════════════")
        log.info(f"[PIPELINE] Поток пайплайна запущен: {threading.current_thread().name}")
        log.info(f"[PIPELINE] PID: {os.getpid()}")

        from ..engines.power import prevent_sleep_start, prevent_sleep_stop, shutdown_computer
        pipeline_ok = False
        if getattr(self.settings, "prevent_sleep", True):
            prevent_sleep_start(log_fn=self._log)

        audio_queue = getattr(self, "_audio_queue", None) or Settings.collect_audio_files(
            self.settings.audio_path
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

            base_series = (self.settings.series_name or "").strip()
            base_output = self.settings.output_folder

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
                    audio_path=audio_file,
                    broll_horizontal=self.settings.broll_horizontal,
                    broll_vertical=self.settings.broll_vertical,
                    bgm_folder=self.settings.bgm_folder,
                    intro_middle_outro_folder=self.settings.intro_middle_outro_folder,
                    vertical_background=self.settings.vertical_background,
                    cover_horizontal=self.settings.cover_horizontal,
                    cover_vertical=self.settings.cover_vertical,
                    output_folder=out_dir,
                    series_name=series,
                    gemini_model=self.settings.gemini_model,
                    gemini_api_key=self.settings.gemini_api_key,
                    gemini_api_keys=self.settings.gemini_api_keys,
                    whisper_model=self.settings.whisper_model,
                    whisperx_path=self.settings.whisperx_path,
                    whisper_language=self.settings.whisper_language,
                    whisper_device=self.settings.whisper_device,
                    whisper_compute_type=self.settings.whisper_compute_type,
                    voice_enhance=self.settings.voice_enhance,
                    add_bgm=self.settings.add_bgm,
                    intro_gemini=self.settings.intro_gemini,
                    keep_temp_files=self.settings.keep_temp_files,
                    caption_style=(self.caption_style_var.get() if hasattr(self, "caption_style_var") else "auto_aisie"),
                    hook_style=(self.hook_style_var.get() if hasattr(self, "hook_style_var") else "auto_aisie"),
                    target_lufs=self.settings.target_lufs,
                    vstack_top_ratio=self.settings.vstack_top_ratio,
                    h_enable_intro=self.settings.h_enable_intro,
                    h_enable_middle=self.settings.h_enable_middle,
                    h_enable_outro=self.settings.h_enable_outro,
                    h_enable_hooks=self.settings.h_enable_hooks,
                    h_enable_subtitles=self.settings.h_enable_subtitles,
                    h_enable_strong_words=self.settings.h_enable_strong_words,
                    v_enable_intro=self.settings.v_enable_intro,
                    v_enable_middle=self.settings.v_enable_middle,
                    v_enable_outro=self.settings.v_enable_outro,
                    v_enable_hooks=self.settings.v_enable_hooks,
                    v_enable_subtitles=self.settings.v_enable_subtitles,
                    v_enable_strong_words=self.settings.v_enable_strong_words,
                    s_enable_intro=self.settings.s_enable_intro,
                    s_enable_middle=self.settings.s_enable_middle,
                    s_enable_outro=self.settings.s_enable_outro,
                    s_enable_hooks=self.settings.s_enable_hooks,
                    s_enable_subtitles=self.settings.s_enable_subtitles,
                    s_enable_strong_words=self.settings.s_enable_strong_words,
                    h_intro_path=self._var_get("h_intro_path"),
                    h_mid_path=self._var_get("h_mid_path"),
                    h_outro_path=self._var_get("h_outro_path"),
                    h_intro_duration=float(self._var_get("h_intro_duration") or 3),
                    h_mid_duration=float(self._var_get("h_mid_duration") or 1),
                    h_outro_duration=float(self._var_get("h_outro_duration") or 3),
                    v_intro_duration=float(self._var_get("v_intro_duration") or 3),
                    v_mid_duration=float(self._var_get("v_mid_duration") or 1),
                    v_outro_duration=float(self._var_get("v_outro_duration") or 3),
                    s_intro_duration=float(self._var_get("s_intro_duration") or 3),
                    s_mid_duration=float(self._var_get("s_mid_duration") or 1),
                    s_outro_duration=float(self._var_get("s_outro_duration") or 3),
                    v_intro_path=self._var_get("v_intro_path"),
                    v_mid_path=self._var_get("v_mid_path"),
                    v_outro_path=self._var_get("v_outro_path"),
                    s_intro_path=self._var_get("s_intro_path"),
                    s_mid_path=self._var_get("s_mid_path"),
                    s_outro_path=self._var_get("s_outro_path"),
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
                from ..engines.power import prevent_sleep_stop, shutdown_computer
                prevent_sleep_stop(log_fn=self._log)
                if pipeline_ok and getattr(self.settings, "shutdown_when_done", False):
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
        """Heartbeat для отслеживания состояния окна (каждые 5 секунд)."""
        if not self.root.winfo_exists():
            return
        log.info(
            "LIFECYCLE heartbeat visible=%s viewable=%s geometry=%s",
            self.root.winfo_viewable(),
            self.root.winfo_ismapped(),
            self.root.geometry(),
        )
        self.root.after(5000, self._heartbeat)

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
            log.warning("[WHISPER] Not found")

    def _browse_whisperx(self) -> None:
        """Ручной выбор whisperx бинарника."""
        path = filedialog.askopenfilename(
            title="Выберите whisperx",
            filetypes=[("Исполняемые файлы", "*"), ("Все файлы", "*.*")]
        )
        if path:
            self.whisperx_path_var.set(path.strip())
            self.whisperx_status_var.set(f"✓ {path}")
            self._save_settings()
            log.info(f"[WHISPER] Manual path set: {path}")

    def _kill_ffmpeg_processes(self) -> None:
        """Убить только дочерние ffmpeg-процессы текущего процесса (не все ffmpeg в системе)."""
        try:
            import psutil
            current = psutil.Process(os.getpid())
            for child in current.children(recursive=True):
                try:
                    name = (child.name() or "").lower()
                    if "ffmpeg" in name:
                        child.kill()
                        log.info("[GUI] Убит дочерний ffmpeg pid=%s", child.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            # Без psutil — не трогаем чужие ffmpeg (pkill слишком агрессивен)
            log.warning("[GUI] psutil не установлен — пропуск убийства ffmpeg")
        except Exception as e:
            log.warning("[GUI] Не удалось убить ffmpeg: %s", e)

    def _imo_path_accessible(self, path: str) -> bool:
        """Файл реально существует и доступен (учтёт отключённый SSD)."""
        path = (path or "").strip()
        if not path:
            return False
        try:
            return os.path.isfile(path) and os.access(path, os.R_OK)
        except OSError:
            return False

    def _sync_imo_checkboxes(self) -> None:
        """Intro/Middle/Outro-галочки:
        - файл есть и доступен → галочка активна, автоматически включена;
        - файла нет / путь пустой / SSD отключён → галочка серая, выключена.
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
            path = path_var.get().strip() if path_var is not None and hasattr(path_var, "get") else ""
            ok = self._imo_path_accessible(path)
            bool_var = getattr(self, f"{prefix}_{key}", None)
            try:
                try:
                    was_disabled = str(cb.cget("state")).lower() in ("disabled", str(tk.DISABLED).lower())
                except tk.TclError:
                    was_disabled = True
                if ok:
                    if was_disabled and bool_var is not None:
                        bool_var.set(True)
                    cb.configure(state=tk.NORMAL)
                else:
                    if bool_var is not None:
                        bool_var.set(False)
                    cb.configure(state=tk.DISABLED)
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
        try:
            self._sync_imo_checkboxes()
        except Exception:
            pass
        try:
            self.root.after(5000, self._schedule_imo_rescan)
        except Exception:
            pass

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
            self._wake_network_paths()
            self._sync_imo_checkboxes()
            self._bind_imo_path_traces()
        except Exception as e:
            log.warning("[GUI] Ошибка загрузки настроек: %s", e)

    def _wake_network_paths(self) -> None:
        """Пробудить сетевые тома (/Volumes/...) после reconnect.

        macOS часто не монтирует SMB/AFP, пока папку не открыть в Finder.
        Делаем os.listdir по корню тома и exists по сохранённым путям.
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

        volumes = set()
        for p in paths:
            if p.startswith("/Volumes/"):
                parts = p.split("/")
                if len(parts) >= 3 and parts[2]:
                    volumes.add("/Volumes/" + parts[2])

        for vol in sorted(volumes):
            try:
                # Триггер automount
                if os.path.isdir(vol):
                    os.listdir(vol)
                    log.info("[GUI] Том доступен: %s", vol)
                else:
                    # Попытка «достучаться» через open -g (без окна) не всегда есть
                    # fallback: stat
                    os.stat(vol)
            except OSError as e:
                log.warning("[GUI] Том ещё не смонтирован: %s (%s)", vol, e)

        # Повторная проверка сохранённых путей
        missing = []
        for p in paths:
            if p.startswith("/Volumes/") and not os.path.exists(p):
                missing.append(p)
        if missing:
            log.warning(
                "[GUI] Пути пока недоступны (откройте том в Finder или подождите): %s",
                "; ".join(missing[:5]),
            )
            try:
                # Одна мягкая попытка: list родителя
                for p in missing[:8]:
                    parent = os.path.dirname(p)
                    try:
                        os.listdir(parent)
                    except OSError:
                        pass
            except Exception:
                pass

    def _save_settings(self) -> None:
        """Сохранить текущие настройки в JSON."""
        try:
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
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("[GUI] Настройки сохранены в %s", SETTINGS_FILE)
        except Exception as e:
            log.warning("[GUI] Ошибка сохранения настроек: %s", e)
