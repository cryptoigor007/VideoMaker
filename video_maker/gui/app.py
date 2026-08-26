"""Главное окно — Tkinter GUI."""
from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..config.settings import Settings
from ..pipeline.stages import PipelineContext

log = logging.getLogger(__name__)


class App:
    """Главное окно приложения ВидеоМейкер."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ВидеоМейкер")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)

        self.settings = Settings.from_env()
        self.running = False

        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        """Построить интерфейс."""
        # Главный контейнер
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # === Секция: Аудио ===
        audio_frame = ttk.LabelFrame(main_frame, text="Аудио", padding=5)
        audio_frame.pack(fill=tk.X, pady=(0, 5))

        self.audio_var = tk.StringVar()
        ttk.Label(audio_frame, text="Файл:").pack(side=tk.LEFT)
        ttk.Entry(audio_frame, textvariable=self.audio_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(audio_frame, text="Выбрать", command=self._choose_audio).pack(side=tk.LEFT)

        # === Секция: B-roll ===
        broll_frame = ttk.LabelFrame(main_frame, text="B-roll видео", padding=5)
        broll_frame.pack(fill=tk.X, pady=(0, 5))

        self.broll_h_var = tk.StringVar()
        ttk.Label(broll_frame, text="Горизонтальный:").pack(side=tk.LEFT)
        ttk.Entry(broll_frame, textvariable=self.broll_h_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(broll_frame, text="Выбрать", command=self._choose_broll_h).pack(side=tk.LEFT)

        broll_v_frame = ttk.Frame(broll_frame)
        broll_v_frame.pack(fill=tk.X, pady=(5, 0))

        self.broll_v_var = tk.StringVar()
        ttk.Label(broll_v_frame, text="Вертикальный:  ").pack(side=tk.LEFT)
        ttk.Entry(broll_v_frame, textvariable=self.broll_v_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(broll_v_frame, text="Выбрать", command=self._choose_broll_v).pack(side=tk.LEFT)

        # === Секция: Фон для вертикали ===
        bg_frame = ttk.LabelFrame(main_frame, text="Фон для вертикального видео", padding=5)
        bg_frame.pack(fill=tk.X, pady=(0, 5))

        self.bg_var = tk.StringVar()
        ttk.Label(bg_frame, text="Файл:").pack(side=tk.LEFT)
        ttk.Entry(bg_frame, textvariable=self.bg_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(bg_frame, text="Выбрать", command=self._choose_bg).pack(side=tk.LEFT)

        # === Секция: BGM ===
        bgm_frame = ttk.LabelFrame(main_frame, text="Фоновая музыка", padding=5)
        bgm_frame.pack(fill=tk.X, pady=(0, 5))

        self.bgm_var = tk.StringVar()
        ttk.Label(bgm_frame, text="Папка:").pack(side=tk.LEFT)
        ttk.Entry(bgm_frame, textvariable=self.bgm_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(bgm_frame, text="Выбрать", command=self._choose_bgm).pack(side=tk.LEFT)

        # === Секция: Intro/Middle/Outro ===
        imo_frame = ttk.LabelFrame(main_frame, text="Intro / Middle / Outro", padding=5)
        imo_frame.pack(fill=tk.X, pady=(0, 5))

        self.imo_folder_var = tk.StringVar()
        ttk.Label(imo_frame, text="Папка:").pack(side=tk.LEFT)
        ttk.Entry(imo_frame, textvariable=self.imo_folder_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(imo_frame, text="Выбрать", command=self._choose_imo).pack(side=tk.LEFT)

        # === Секция: Обложки ===
        cover_frame = ttk.LabelFrame(main_frame, text="Обложки", padding=5)
        cover_frame.pack(fill=tk.X, pady=(0, 5))

        self.cover_h_var = tk.StringVar()
        ttk.Label(cover_frame, text="Горизонтальная:").pack(side=tk.LEFT)
        ttk.Entry(cover_frame, textvariable=self.cover_h_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Button(cover_frame, text="Выбрать", command=self._choose_cover_h).pack(side=tk.LEFT)

        cover_v_frame = ttk.Frame(cover_frame)
        cover_v_frame.pack(fill=tk.X, pady=(5, 0))

        self.cover_v_var = tk.StringVar()
        ttk.Label(cover_v_frame, text="Вертикальная:  ").pack(side=tk.LEFT)
        ttk.Entry(cover_v_frame, textvariable=self.cover_v_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Button(cover_v_frame, text="Выбрать", command=self._choose_cover_v).pack(side=tk.LEFT)

        # === Секция: Настройки ===
        settings_frame = ttk.LabelFrame(main_frame, text="Настройки", padding=5)
        settings_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(settings_frame, text="Название серии:").pack(side=tk.LEFT)
        self.series_var = tk.StringVar()
        ttk.Entry(settings_frame, textvariable=self.series_var, width=30).pack(side=tk.LEFT, padx=5)

        ttk.Label(settings_frame, text="Модель Gemini:").pack(side=tk.LEFT, padx=(20, 0))
        self.model_var = tk.StringVar(value="gemini-3.6-flash")
        model_combo = ttk.Combobox(
            settings_frame,
            textvariable=self.model_var,
            values=["gemini-3.6-flash", "gemini-3.6-pro", "gemini-3.6-flash-lite"],
            state="readonly",
            width=25,
        )
        model_combo.pack(side=tk.LEFT, padx=5)

        self.voice_enhance_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="Усилить голос", variable=self.voice_enhance_var).pack(side=tk.LEFT, padx=(20, 0))

        self.add_bgm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="Добавить BGM", variable=self.add_bgm_var).pack(side=tk.LEFT, padx=5)

        # === Чекбоксы ===
        checks_frame = ttk.LabelFrame(main_frame, text="Этапы обработки", padding=5)
        checks_frame.pack(fill=tk.X, pady=(0, 5))

        # Горизонтальный
        h_frame = ttk.Frame(checks_frame)
        h_frame.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(h_frame, text="Горизонтальный:", width=15).pack(side=tk.LEFT)
        self.h_intro = tk.BooleanVar(value=False)
        self.h_middle = tk.BooleanVar(value=False)
        self.h_outro = tk.BooleanVar(value=False)
        self.h_hooks = tk.BooleanVar(value=True)
        self.h_subs = tk.BooleanVar(value=True)
        self.h_strong = tk.BooleanVar(value=True)
        ttk.Checkbutton(h_frame, text="Intro", variable=self.h_intro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(h_frame, text="Middle", variable=self.h_middle).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(h_frame, text="Outro", variable=self.h_outro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(h_frame, text="Хуки", variable=self.h_hooks).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(h_frame, text="Субтитры", variable=self.h_subs).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(h_frame, text="Сильные", variable=self.h_strong).pack(side=tk.LEFT, padx=3)

        # Вертикальный
        v_frame = ttk.Frame(checks_frame)
        v_frame.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(v_frame, text="Вертикальный:", width=15).pack(side=tk.LEFT)
        self.v_intro = tk.BooleanVar(value=False)
        self.v_middle = tk.BooleanVar(value=False)
        self.v_outro = tk.BooleanVar(value=False)
        self.v_hooks = tk.BooleanVar(value=True)
        self.v_subs = tk.BooleanVar(value=True)
        self.v_strong = tk.BooleanVar(value=True)
        ttk.Checkbutton(v_frame, text="Intro", variable=self.v_intro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(v_frame, text="Middle", variable=self.v_middle).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(v_frame, text="Outro", variable=self.v_outro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(v_frame, text="Хуки", variable=self.v_hooks).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(v_frame, text="Субтитры", variable=self.v_subs).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(v_frame, text="Сильные", variable=self.v_strong).pack(side=tk.LEFT, padx=3)

        # Shorts
        s_frame = ttk.Frame(checks_frame)
        s_frame.pack(fill=tk.X)
        ttk.Label(s_frame, text="Shorts:", width=15).pack(side=tk.LEFT)
        self.s_intro = tk.BooleanVar(value=False)
        self.s_middle = tk.BooleanVar(value=False)
        self.s_outro = tk.BooleanVar(value=False)
        self.s_hooks = tk.BooleanVar(value=True)
        self.s_subs = tk.BooleanVar(value=True)
        self.s_strong = tk.BooleanVar(value=True)
        ttk.Checkbutton(s_frame, text="Intro", variable=self.s_intro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(s_frame, text="Middle", variable=self.s_middle).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(s_frame, text="Outro", variable=self.s_outro).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(s_frame, text="Хуки", variable=self.s_hooks).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(s_frame, text="Субтитры", variable=self.s_subs).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(s_frame, text="Сильные", variable=self.s_strong).pack(side=tk.LEFT, padx=3)

        # === Кнопка запуска ===
        self.start_btn = ttk.Button(main_frame, text="СОЗДАТЬ ВИДЕО", command=self._start)
        self.start_btn.pack(pady=10)

        # === Прогресс ===
        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 10))

        self.progress_label = ttk.Label(progress_frame, text="0%", width=8)
        self.progress_label.pack(side=tk.LEFT)

        self.time_label = ttk.Label(progress_frame, text="", width=15)
        self.time_label.pack(side=tk.LEFT)

        # === Лог ===
        log_frame = ttk.LabelFrame(main_frame, text="Лог", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self.log_text = tk.Text(log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # --- Выбор файлов ---

    def _choose_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать аудиофайл",
            filetypes=[("Аудио", "*.mp3 *.wav *.flac *.m4a *.ogg"), ("Все", "*.*")],
        )
        if path:
            self.audio_var.set(path)

    def _choose_broll_h(self) -> None:
        path = filedialog.askdirectory(title="Выбрать папку B-roll горизонтальный")
        if path:
            self.broll_h_var.set(path)

    def _choose_broll_v(self) -> None:
        path = filedialog.askdirectory(title="Выбрать папку B-roll вертикальный")
        if path:
            self.broll_v_var.set(path)

    def _choose_bg(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать фон для вертикали",
            filetypes=[("Изображения/Видео", "*.jpg *.jpeg *.png *.mp4 *.mov"), ("Все", "*.*")],
        )
        if path:
            self.bg_var.set(path)

    def _choose_bgm(self) -> None:
        path = filedialog.askdirectory(title="Выбрать папку BGM")
        if path:
            self.bgm_var.set(path)

    def _choose_imo(self) -> None:
        path = filedialog.askdirectory(title="Выбрать папку Intro/Middle/Outro")
        if path:
            self.imo_folder_var.set(path)

    def _choose_cover_h(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать обложку горизонтальную",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png"), ("Все", "*.*")],
        )
        if path:
            self.cover_h_var.set(path)

    def _choose_cover_v(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбрать обложку вертикальную",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png"), ("Все", "*.*")],
        )
        if path:
            self.cover_v_var.set(path)

    # --- Логирование ---

    def _log(self, msg: str) -> None:
        """Потокобезопасная запись в лог."""
        def _write():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
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

    # --- Запуск пайплайна ---

    def _start(self) -> None:
        """Запустить пайплайн."""
        if self.running:
            messagebox.showwarning("Внимание", "Пайплайн уже запущен")
            return

        # Собираем настройки
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

        # Чекбоксы
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

        # Настройки аудио
        self.settings.voice_enhance = self.voice_enhance_var.get()
        self.settings.add_bgm = self.add_bgm_var.get()

        # Валидация
        self.settings.output_folder = os.path.dirname(self.settings.audio_path) if self.settings.audio_path else ""
        errors = self.settings.validate()
        if errors:
            messagebox.showerror("Ошибки", "\n".join(errors))
            return

        self.running = True
        self.start_btn.configure(state=tk.DISABLED)

        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        thread.start()

    def _run_pipeline(self) -> None:
        """Выполнить пайплайн в отдельном потоке."""
        try:
            from ..pipeline.stages import AudioStage, TranscribeStage, GeminiStage
            from ..pipeline.master import MasterBuilder
            from ..pipeline.branches import FinalHorizontal, FinalVertical
            from ..pipeline.shorts import ShortsCutter
            from ..pipeline.finalize import FinalizeStage

            # Создаём контекст
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

            # Запускаем стадии
            stages = [
                AudioStage(),
                TranscribeStage(),
                GeminiStage(),
                MasterBuilder(),
                FinalHorizontal(),
                FinalVertical(),
                ShortsCutter(),
                FinalizeStage(),
            ]

            for stage in stages:
                self._log(f"\n{'='*50}")
                self._log(f"Стадия: {stage.name()}")
                self._log(f"{'='*50}")
                ctx = stage.run(ctx)
                self._set_progress(ctx.progress)

            self._log("\n" + "="*50)
            self._log("ГОТОВО!")
            self._log("="*50)
            self._set_progress(100)

        except Exception as e:
            self._log(f"\nОШИБКА: {e}")
            log.exception("Pipeline error")
        finally:
            self.running = False
            self.root.after(0, lambda: self.start_btn.configure(state=tk.NORMAL))

    def _load_settings(self) -> None:
        """Загрузить сохранённые настройки."""
        # TODO: загрузка из .env/keychain
        pass
