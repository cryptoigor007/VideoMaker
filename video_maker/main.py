# DEEP AUDIT CP12 (2026-10-12): CLI validation and truthful nonzero startup failure status.
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
import argparse
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
            subprocess.run(["osascript", "-e", script], capture_output=True, check=False, timeout=15)
            
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
    """Treat Ctrl+C as an interruption, not a successful completion."""
    log.warning("LIFECYCLE signal SIGINT")
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, _sigint_handler)

# Перехват SIGTERM
def _sigterm_handler(signum: int, frame: types.FrameType | None) -> None:
    """Keep SIGTERM distinct from successful completion for supervisors."""
    log.warning("LIFECYCLE signal SIGTERM")
    raise SystemExit(143)

signal.signal(signal.SIGTERM, _sigterm_handler)


def _api_port_arg(value: str) -> int:
    """Validate a TCP port before any GUI or server resources are created."""
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("порт должен быть целым числом 1..65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("порт должен быть в диапазоне 1..65535")
    return port


def _parse_args(argv: list[str]) -> tuple[bool, int | None, bool]:
    """Разобрать CLI: (headless, порт пульта, не поднимать пульт)."""
    parser = argparse.ArgumentParser(
        prog="VideoMaker",
        description="Автоматическое создание видео и Shorts из аудио и B-roll.",
        allow_abbrev=False,
    )
    parser.add_argument("--headless", action="store_true", help="запуск без видимого окна (Tk-цикл остаётся активен)")
    parser.add_argument("--api-port", type=_api_port_arg, metavar="N", help="порт HTTP-пульта (1..65535)")
    parser.add_argument("--no-api", action="store_true", help="не запускать HTTP-пульт")
    args = parser.parse_args(argv)
    return bool(args.headless), args.api_port, bool(args.no_api)


def main() -> None:
    """Запуск приложения; failed startup must propagate a non-zero exit code."""
    headless, api_port, no_api = _parse_args(sys.argv[1:])
    exit_code = 0
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
            try:
                app._show_api_info({"enabled": False, "local": "", "network": "", "lan": False})
            except Exception:
                pass
        else:
            try:
                from .api import start_api

                runtime = start_api(app, root, headless=headless, port=api_port)
                if runtime is not None:
                    # держим ссылку: иначе пульт соберёт сборщик мусора
                    globals()["_API_RUNTIME"] = runtime
                    log.info("LIFECYCLE api_started")
                else:
                    # Пульт отключён настройкой или порт не поднялся: блок «Пульт»
                    # должен явно показывать это, галочка сети — быть недоступной.
                    try:
                        app._show_api_info({"enabled": False, "local": "", "network": "", "lan": False})
                    except Exception:
                        pass
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
        exit_code = 130
    except SystemExit as exc:
        # SystemExit приходит из обработчика SIGTERM (143) и не должен
        # записываться в журнал как успешный нулевой код.
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        raise
    except Exception as e:  # noqa: BLE001 — any startup/runtime failure must be reported
        log.error("Критическая ошибка: %s: %s", type(e).__name__, e)
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        log.info("LIFECYCLE process_end exit_code=%s", exit_code)
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()