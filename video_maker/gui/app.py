"""Главное окно — Tkinter GUI с тёмной темой и подробным логированием."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
import tkinter as tk
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
        self.root.geometry("960x780")
        self.root.minsize(860, 680)
        self.root.configure(bg=COLORS["bg"])
        log.info("[GUI] Окно настроено: 960x780, min 860x680")

        self.settings = Settings.from_env()
        log.info(f"[GUI] Настройки загружены: model={self.settings.gemini_model}, "
                 f"whisper={self.settings.whisper_model}")

        self.running = False
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

        log.info("[GUI] Вызов _load_settings()...")
        self._load_settings()
        log.info("[GUI] _load_settings() завершён")

        log.info("[GUI] ═══════════════════════════════════════════════")
        log.info("[GUI] App.__init__() ЗАВЕРШЁН — окно готово")
        log.info("[GUI] ═══════════════════════════════════════════════")

    def _on_close(self) -> None:
        """Обработчик закрытия окна."""
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

        log.info("[GUI] Уничтожение корневого окна...")
        self.root.destroy()
        log.info("[GUI] root.destroy() выполнен")

        log.info("[GUI] Завершение главного цикла mainloop()...")
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

        # Главный контейнер — بدون скролла, лог занимает остаток
        self.main_frame = ttk.Frame(self.root, padding=16)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # === Заголовок ===
        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="ВидеоМейкер", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Создание видео из аудио + B-roll", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        # === Колонки (фиксированная высота) ===
        columns = ttk.Frame(self.main_frame)
        columns.pack(fill=tk.X)
        columns.columnconfigure(0, weight=3)
        columns.columnconfigure(1, weight=2)

        left_col = ttk.Frame(columns)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        right_col = ttk.Frame(columns)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # ─── Левая колонка ──────────────────────────────────────────────

        self._add_file_section(
            left_col, "Аудио",
            [("Файл:", "audio_var", "file", [("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg")])],
        )

        broll_frame = self._add_section(left_col, "B-roll видео")
        row1 = ttk.Frame(broll_frame)
        row1.pack(fill=tk.X, pady=(0, 4))
        self._add_browse_row(row1, "Горизонтальный:", "broll_h_var", "dir")
        row2 = ttk.Frame(broll_frame)
        row2.pack(fill=tk.X)
        self._add_browse_row(row2, "Вертикальный:  ", "broll_v_var", "dir")

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

        self._add_file_section(
            left_col, "Intro / Middle / Outro",
            [("Папка:", "imo_folder_var", "dir", [])],
        )

        cover_frame = self._add_section(left_col, "Обложки")
        row1 = ttk.Frame(cover_frame)
        row1.pack(fill=tk.X, pady=(0, 4))
        self._add_browse_row(row1, "Горизонтальная:", "cover_h_var", "file", [("Изображения", "*.jpg *.jpeg *.png")])
        row2 = ttk.Frame(cover_frame)
        row2.pack(fill=tk.X)
        self._add_browse_row(row2, "Вертикальная:  ", "cover_v_var", "file", [("Изображения", "*.jpg *.jpeg *.png")])

        # ─── Правая колонка ─────────────────────────────────────────────

        # Название серии — ПРОСТОЙ ВВОД С ПОДСКАЗКОЙ
        series_frame = self._add_section(right_col, "Название серии")
        self.series_var = tk.StringVar()
        entry = ttk.Entry(series_frame, textvariable=self.series_var, font=("SF Pro Text", 11))
        entry.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(series_frame, text="Например: Выпуск 01 — Основы монтажа",
                  font=("SF Pro Text", 9)).pack(anchor="w", pady=(4, 0))

        # Модель Gemini
        model_frame = self._add_section(right_col, "Модель Gemini")
        self.model_var = tk.StringVar(value="gemini-3.6-flash")
        model_combo = ttk.Combobox(
            model_frame,
            textvariable=self.model_var,
            values=["gemini-3.6-flash", "gemini-3.6-pro", "gemini-3.6-flash-lite"],
            state="readonly",
            font=("SF Pro Text", 10),
        )
        model_combo.pack(fill=tk.X, pady=(4, 0))

        # Настройки аудио
        audio_settings = self._add_section(right_col, "Настройки аудио")
        self.voice_enhance_var = tk.BooleanVar(value=True)
        self.add_bgm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(audio_settings, text="Усилить голос", variable=self.voice_enhance_var).pack(anchor="w", pady=2)
        ttk.Checkbutton(audio_settings, text="Добавить BGM", variable=self.add_bgm_var).pack(anchor="w", pady=2)

        # Этапы обработки — ВЫРОВНЕНЫ С ОБЛОЖКАМИ
        checks_frame = self._add_section(right_col, "Этапы обработки")
        checks_frame.configure(padding=8)

        for label, prefix, defaults in [
            ("16:9 Гориз.", "h", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
            ("9:16 Вертик.", "v", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
            ("Shorts",      "s", {"intro": False, "middle": False, "outro": False, "hooks": True, "subs": True, "strong": True}),
        ]:
            row = ttk.Frame(checks_frame)
            row.pack(fill=tk.X, pady=(0, 6))
            ttk.Label(row, text=label, width=12, style="Section.TLabel").pack(side=tk.LEFT)

            for key, default in defaults.items():
                var = tk.BooleanVar(value=default)
                setattr(self, f"{prefix}_{key}", var)
                ttk.Checkbutton(row, text=key.capitalize(), variable=var).pack(side=tk.LEFT, padx=2)

        # === Кнопка запуска ===
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(fill=tk.X, pady=(12, 8))
        self.start_btn = ttk.Button(
            btn_frame, text="  СОЗДАТЬ ВИДЕО  ", style="Accent.TButton", command=self._start
        )
        self.start_btn.pack()

        # === Прогресс ===
        progress_frame = ttk.Frame(self.main_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 8))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100,
            style="Custom.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 12))

        self.progress_label = ttk.Label(progress_frame, text="0%", style="Progress.TLabel", width=6)
        self.progress_label.pack(side=tk.LEFT)

        self.time_label = ttk.Label(progress_frame, text="", style="Progress.TLabel", width=12)
        self.time_label.pack(side=tk.LEFT, padx=(8, 0))

        # === Лог — ЗАНИМАЕТ ВСЁ ОСТАВШЕЕСЯ МЕСТО ===
        log_frame = self._add_section(self.main_frame, "Лог")
        log_frame.configure(padding=4)
        # Лог растягивается на весь остаток высоты
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.rowconfigure(self.main_frame.grid_size()[0] - 1, weight=1)

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

        log.info(f"[GUI] _build_ui(): интерфейс построен")

    # ─── Хелперы ──────────────────────────────────────────────────────────

    def _add_section(self, parent, title: str) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=8)
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
            var.set(path)
            log.info(f"[GUI] {name} = {path}")

    def _browse_file(self, var: tk.StringVar, name: str, filetypes=None) -> None:
        log.info(f"[GUI] Диалог выбора файла: {name}")
        types = [("Все файлы", "*.*")]
        if filetypes:
            types = filetypes + types
        path = filedialog.askopenfilename(filetypes=types)
        log.info(f"[GUI] Выбран файл: {path or '(пусто)'}")
        if path:
            var.set(path)
            log.info(f"[GUI] {name} = {path}")

    # ─── Файловые диалоги (обратная совместимость) ────────────────────────

    def _choose_audio(self) -> None:
        self._browse_file(self.audio_var, "audio_var", [("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg")])

    def _choose_broll_h(self) -> None:
        self._browse_dir(self.broll_h_var, "broll_h_var")

    def _choose_broll_v(self) -> None:
        self._browse_dir(self.broll_v_var, "broll_v_var")

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

    def _set_progress(self, value: float) -> None:
        """Потокобезопасное обновление прогресса."""
        def _update():
            self.progress_var.set(value)
            self.progress_label.configure(text=f"{value:.0f}%")
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
        self.settings.series_name = self.series_var.get()
        self.settings.gemini_model = self.model_var.get()

        log.info(f"[GUI] audio_path = {self.settings.audio_path}")
        log.info(f"[GUI] broll_horizontal = {self.settings.broll_horizontal}")
        log.info(f"[GUI] broll_vertical = {self.settings.broll_vertical}")
        log.info(f"[GUI] vertical_background = {self.settings.vertical_background}")
        log.info(f"[GUI] bgm_folder = {self.settings.bgm_folder}")
        log.info(f"[GUI] imo_folder = {self.settings.intro_middle_outro_folder}")
        log.info(f"[GUI] output_folder = {self.output_var.get() or '(auto)'}")
        log.info(f"[GUI] gemini_model = {self.settings.gemini_model}")
        log.info(f"[GUI] series_name = {self.settings.series_name}")

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

        self.settings.output_folder = self.output_var.get() or (
            os.path.dirname(self.settings.audio_path) if self.settings.audio_path else ""
        )
        log.info(f"[GUI] output_folder (final) = {self.settings.output_folder}")

        errors = self.settings.validate()
        if errors:
            log.error(f"[GUI] Ошибки валидации: {errors}")
            messagebox.showerror("Ошибки", "\n".join(errors))
            return

        log.info("[GUI] Валидация пройдена — запуск пайплайна")
        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        log.info("[GUI] self.running = True, кнопка заблокирована")

        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        log.info(f"[GUI] Создан поток пайплайна: {thread.name}")
        thread.start()
        log.info("[GUI] Поток запущен")

    def _run_pipeline(self) -> None:
        """Выполнить пайплайн в отдельном потоке."""
        log.info(f"[PIPELINE] ═══════════════════════════════════════════════")
        log.info(f"[PIPELINE] Поток пайплайна запущен: {threading.current_thread().name}")
        log.info(f"[PIPELINE] PID: {os.getpid()}")

        try:
            from ..pipeline.stages import AudioStage, TranscribeStage, GeminiStage
            from ..pipeline.master import MasterBuilder
            from ..pipeline.branches import FinalHorizontal, FinalVertical
            from ..pipeline.shorts import ShortsCutter
            from ..pipeline.finalize import FinalizeStage

            log.info("[PIPELINE] Все модули пайплайна импортированы")

            ctx = PipelineContext(
                audio_path=self.settings.audio_path,
                broll_horizontal=self.settings.broll_horizontal,
                broll_vertical=self.settings.broll_vertical,
                bgm_folder=self.settings.bgm_folder,
                intro_middle_outro_folder=self.settings.intro_middle_outro_folder,
                vertical_background=self.settings.vertical_background,
                cover_horizontal=self.settings.cover_horizontal,
                cover_vertical=self.settings.cover_vertical,
                output_folder=self.settings.output_folder,
                series_name=self.settings.series_name,
                gemini_model=self.settings.gemini_model,
                gemini_api_key=self.settings.gemini_api_key,
                gemini_api_keys=self.settings.gemini_api_keys,
                whisper_model=self.settings.whisper_model,
                voice_enhance=self.settings.voice_enhance,
                add_bgm=self.settings.add_bgm,
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
                log_callback=self._log,
            )
            log.info("[PIPELINE] PipelineContext создан")

            stages = [
                ("AudioStage", AudioStage()),
                ("TranscribeStage", TranscribeStage()),
                ("GeminiStage", GeminiStage()),
                ("MasterBuilder", MasterBuilder()),
                ("FinalHorizontal", FinalHorizontal()),
                ("FinalVertical", FinalVertical()),
                ("ShortsCutter", ShortsCutter()),
                ("FinalizeStage", FinalizeStage()),
            ]

            log.info(f"[PIPELINE] {len(stages)} стадий准备就绪")

            for name, stage in stages:
                log.info(f"\n{'─'*48}")
                log.info(f"  СТАДИЯ: {name} — {stage.name()}")
                log.info(f"{'─'*48}")
                self._log(f"\n{'─'*48}")
                self._log(f"  {stage.name()}")
                self._log(f"{'─'*48}")
                ctx = stage.run(ctx)
                self._set_progress(ctx.progress)
                log.info(f"[PIPELINE] {name} завершена, progress={ctx.progress:.0f}%")

            self._log("\n" + "═"*48)
            self._log("  ГОТОВО!")
            self._log("═"*48)
            self._set_progress(100)
            log.info("[PIPELINE] ═══════════════════════════════════════════════")
            log.info("[PIPELINE] ВСЕ СТАДИИ ЗАВЕРШЕНЫ УСПЕШНО")
            log.info("[PIPELINE] ═══════════════════════════════════════════════")

        except Exception as e:
            log.error(f"[PIPELINE] ╔══════════════════════════════════════════════╗")
            log.error(f"[PIPELINE] ║            ОШИБКА В ПАЙПЛАЙНЕ                ║")
            log.error(f"[PIPELINE] ╚══════════════════════════════════════════════╝")
            log.error(f"[PIPELINE] Тип: {type(e).__name__}")
            log.error(f"[PIPELINE] Сообщение: {e}")
            log.error(f"[PIPELINE] Traceback:")
            for line in traceback.format_exc().splitlines():
                log.error(f"[PIPELINE]   {line}")
            self._log(f"\n  ОШИБКА: {e}")
        finally:
            log.info("[PIPELINE] finally: self.running = False, кнопка разблокирована")
            self.running = False
            self.root.after(0, lambda: self.start_btn.configure(state=tk.NORMAL))

    def _load_settings(self) -> None:
        """Загрузить сохранённые настройки."""
        log.info("[GUI] _load_settings(): загрузка из .env")
        pass
