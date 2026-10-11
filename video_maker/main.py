# VideoMaker | 2026.09.23-r57-api.1 | 2026-09-23
# CHANGED:
#   r57-api: запуск HTTP-пульта (video_maker/api) поверх приложения +
#            headless-режим без окна. Новые ключи: --headless (скрыть окно),
#            --api-port N (порт пульта), --no-api (не поднимать пульт).
#            App сохраняется в переменную — пульту нужен доступ к нему.
#   r57-api.1: понятные сообщения о режимах: «[ОКНО] без окна» и «[API] пульт
#              выключен ключом --no-api».
# PREV: 2026.09.10-r43-idlefix — logging level=INFO; DEBUG по VIDEOMAKER_DEBUG
# REPLACE: video_maker/main.py

"""Точка входа — main.py."""
import logging
import os
import signal
import subprocess
import sys
import tkinter as tk
import types

DEFAULT_LOG_FILE = os.path.expanduser("~/video_maker/videomaker.log")


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

    # INFO по умолчанию (меньше disk I/O на холостом ходу).
    # DEBUG только если VIDEOMAKER_DEBUG=1
    level = logging.DEBUG if os.environ.get("VIDEOMAKER_DEBUG", "").strip() in ("1", "true", "yes") else logging.INFO

    logging.basicConfig(
        level=level,
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


def _parse_args(argv: list[str]) -> tuple[bool, int | None, bool]:
    """Разобрать ключи: (headless, порт пульта, не поднимать пульт)."""
    headless = "--headless" in argv
    no_api = "--no-api" in argv
    port: int | None = None
    if "--api-port" in argv:
        idx = argv.index("--api-port")
        if idx + 1 < len(argv):
            try:
                port = int(argv[idx + 1])
            except ValueError:
                log.warning("Некорректный --api-port %r — беру порт по умолчанию", argv[idx + 1])
    return headless, port, no_api


def main() -> None:
    """Запуск приложения."""
    headless, api_port, no_api = _parse_args(sys.argv[1:])
    log.info("LIFECYCLE start pid=%s cwd=%s python=%s headless=%s api=%s",
             os.getpid(), os.getcwd(), sys.version.split()[0], headless,
             "выключен" if no_api else "включён")
    
    try:
        log.info("Создание корневого окна Tk()...")
        root = tk.Tk()
        log.info(f"LIFECYCLE tk_created root={root}")

        if headless:
            # Окно скрыто, но Tk-цикл живёт: пайплайн работает как обычно.
            root.withdraw()
            log.info("[ОКНО] режим без окна: главное окно скрыто, приложение работает в фоне")
        else:
            # Принудительно показываем окно на переднем плане (macOS)
            _bring_window_to_front(root)

        log.info("Импорт App...")
        from .gui.app import App

        log.info("Создание App(root)...")
        app = App(root)
        log.info("LIFECYCLE app_created")

        if no_api:
            log.info("[API] пульт выключен ключом --no-api")
        else:
            try:
                from .api import start_api

                runtime = start_api(app, root, headless=headless, port=api_port)
                if runtime is not None:
                    # держим ссылку: иначе пульт соберёт сборщик мусора
                    globals()["_API_RUNTIME"] = runtime
                    log.info("LIFECYCLE api_started")
            except Exception:
                log.exception("Не удалось поднять пульт")

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