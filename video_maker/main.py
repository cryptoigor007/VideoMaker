"""Точка входа — main.py."""
import logging
import os
import signal
import sys
import tkinter as tk

# Настройка логирования — ВСЁ в файл + консоль
log_file = os.path.expanduser("~/video_maker/videomeyker.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)

log = logging.getLogger(__name__)

# Перехват необработанных исключений
def _excepthook(exc_type, exc_value, exc_tb):
    import traceback
    log.error("╔══════════════════════════════════════════════╗")
    log.error("║      НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ              ║")
    log.error("╚══════════════════════════════════════════════╝")
    log.error(f"Тип: {exc_type.__name__}")
    log.error(f"Сообщение: {exc_value}")
    for line in traceback.format_tb(exc_tb):
        log.error(line.strip())

sys.excepthook = _excepthook

# Перехват SIGINT (Ctrl+C)
def _sigint_handler(signum, frame):
    log.warning("Получен SIGINT (Ctrl+C) — завершение...")
    sys.exit(0)

signal.signal(signal.SIGINT, _sigint_handler)

# Перехват SIGTERM
def _sigterm_handler(signum, frame):
    log.warning("Получен SIGTERM — завершение...")
    sys.exit(0)

signal.signal(signal.SIGTERM, _sigterm_handler)


def main() -> None:
    """Запуск приложения."""
    log.info("╔══════════════════════════════════════════════╗")
    log.info("║           ВИДЕОМЕЙКЕР — ЗАПУСК               ║")
    log.info("╚══════════════════════════════════════════════╝")
    log.info(f"Python: {sys.version}")
    log.info(f"PID: {os.getpid()}")
    log.info(f"Рабочая папка: {os.getcwd()}")
    log.info(f"Лог-файл: {log_file}")

    try:
        log.info("Создание корневого окна Tk()...")
        root = tk.Tk()
        log.info(f"Tk() создан: {root}")

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
    except Exception as e:
        log.error(f"Критическая ошибка: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        log.info("╔══════════════════════════════════════════════╗")
        log.info("║          ПРИЛОЖЕНИЕ ЗАВЕРШЕНО                ║")
        log.info("╚══════════════════════════════════════════════╝")


if __name__ == "__main__":
    main()
