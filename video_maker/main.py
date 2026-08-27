"""Точка входа — main.py."""
import logging
import os
import signal
import sys
import tkinter as tk
import types

DEFAULT_LOG_FILE = os.path.expanduser("~/video_maker/videomeyker.log")


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
    log.error("╔═══════════════════════════════════════════════╗")
    log.error("║      НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ              ║")
    log.error("╚═══════════════════════════════════════════════╝")
    log.error(f"Тип: {exc_type.__name__}")
    log.error(f"Сообщение: {exc_value}")
    for line in traceback.format_tb(exc_tb):
        log.error(line.strip())

sys.excepthook = _excepthook


# Перехват SIGINT (Ctrl+C)
def _sigint_handler(signum: int, frame: types.FrameType | None) -> None:
    log.warning("Получен SIGINT (Ctrl+C) — завершение...")
    sys.exit(0)


signal.signal(signal.SIGINT, _sigint_handler)

# Перехват SIGTERM
def _sigterm_handler(signum: int, frame: types.FrameType | None) -> None:
    log.warning("Получен SIGTERM — завершение...")
    sys.exit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)


def main() -> None:
    """Запуск приложения."""
    log.info("╔════════════════════════════════════════════════════════════════════════════════════╗")
    log.info("║           ВИДЕОМЕЙКЕР — ЗАПУСК                                               ║")
    log.info("╚════════════════════════════════════════════════════════════════════════════════════╝")
    log.info(f"Python: {sys.version}")
    log.info(f"PID: {os.getpid()}")
    log.info(f"Рабочая папка: {os.getcwd()}")
    log.info(f"Лог-файл: {DEFAULT_LOG_FILE}")

    try:
        log.info("Создание корневого окна Tk()...")
        root = tk.Tk()
        log.info(f"Tk() создан: {root}")

        # Принудительно показываем окно на переднем плане (macOS)
        root.lift()
        root.attributes('-topmost', True)
        root.after_idle(root.attributes, '-topmost', False)
        root.focus_force()

        log.info("Импорт App...")
        from .gui.app import App

        log.info("Создание App(root)...")
        app = App(root)
        log.info("App создан — запуск mainloop()")

        log.info("mainloop() начат")
        root.mainloop()
        log.info("mainloop() завершён")

    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt — завершение")
    except (RuntimeError, OSError, ValueError) as e:
        log.error(f"Критическая ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        log.info("╔═══════════════════════════════════════════════════════════════════════════════════╗")
        log.info("║          ПРИЛОЖЕНИЕ ЗАВЕРШЕНО                                                ║")
        log.info("╚═══════════════════════════════════════════════════════════════════════════════╝")
        sys.exit(0)


if __name__ == "__main__":
    main()