"""Точка входа — main.py."""
import logging
import os
import signal
import subprocess
import sys
import tkinter as tk
import types

DEFAULT_LOG_FILE = os.path.expanduser("~/video_maker/videomeyker.log")


def _bring_window_to_front(root: tk.Tk) -> None:
    """Агрессивно вывести окно на передний план (macOS)."""
    try:
        # Стандартные Tkinter методы
        root.lift()  # type: ignore[attr-defined]
        root.attributes('-topmost', True)  # type: ignore[attr-defined]
        root.after_idle(root.attributes, '-topmost', False)  # type: ignore[attr-defined]
        root.focus_force()  # type: ignore[attr-defined]
        root.update_idletasks()  # type: ignore[attr-defined]
        
        # macOS: используем AppleScript для принудительного вывода на передний план
        if sys.platform == "darwin":
            script = '''
            tell application "System Events"
                set frontmost of process "Python" to true
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
            
            # Альтернативный способ через Tk
            root.wm_attributes('-topmost', 1)  # type: ignore[attr-defined]
            root.after_idle(lambda: root.wm_attributes('-topmost', 0))  # type: ignore[attr-defined]
            root.update_idletasks()  # type: ignore[attr-defined]
            
    except (subprocess.SubprocessError, OSError, tk.TclError) as e:
        logging.getLogger(__name__).warning(f"Не удалось вывести окно на передний план: {e}")


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Настроить логирование. Если log_file не задан — используем дефолтный."""
    if log_file is None:
        log_file = DEFAULT_LOG_FILE

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,  # Переконфигурировать даже если уже настроено
    )
    return logging.getLogger(__name__)

# Настройка логирования — ВСЁ в файл + консоль
log = setup_logging()


# Перехват необработанных исключений
def _excepthook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    import traceback
    log.error("╔════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗")
    log.error("║      НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ              ║")
    log.error("╚════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝")
    log.error(f"Тип: {exc_type.__name__}")
    log.error(f"Сообщение: {exc_value}")
    for line in traceback.format_tb(exc_tb):
        log.error(line.strip())

sys.excepthook = _excepthook


# Перехват SIGINT (Ctrl+C)
def _sigint_handler(signum: int, frame: types.FrameType | None) -> None:
    log.warning("LIFECYCLE signal SIGINT")
    sys.exit(0)


signal.signal(signal.SIGINT, _sigint_handler)

# Перехват SIGTERM
def _sigterm_handler(signum: int, frame: types.FrameType | None) -> None:
    log.warning("LIFECYCLE signal SIGTERM")
    sys.exit(0)

signal.signal(signal.SIGTERM, _sigterm_handler)


def main() -> None:
    """Запуск приложения."""
    log.info("LIFECYCLE start pid=%s cwd=%s python=%s", os.getpid(), os.getcwd(), sys.version.split()[0])
    
    try:
        log.info("Создание корневого окна Tk()...")
        root = tk.Tk()
        log.info(f"LIFECYCLE tk_created root={root}")

        # Принудительно показываем окно на переднем плане (macOS)
        _bring_window_to_front(root)

        log.info("Импорт App...")
        from .gui.app import App

        log.info("Создание App(root)...")
        _ = App(root)
        log.info("LIFECYCLE app_created")

        # окно: размер и позиция (видно, не уехало ли за экран)
        root.update_idletasks()
        log.info(
            "LIFECYCLE window geometry=%s screen=%sx%s",
            root.geometry(),
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
        )

        log.info("LIFECYCLE mainloop_enter")
        root.mainloop()
        log.info("LIFECYCLE mainloop_exit")

    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt — завершение")
    except (RuntimeError, OSError, ValueError) as e:
        log.error(f"Критическая ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        log.info("LIFECYCLE process_end")
        sys.exit(0)


if __name__ == "__main__":
    main()