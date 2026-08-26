"""Точка входа — main.py."""
import logging
import sys
import tkinter as tk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

log = logging.getLogger(__name__)


def main() -> None:
    """Запуск приложения."""
    log.info("=== ВидеоМейкер ===")
    root = tk.Tk()

    from .gui.app import App
    app = App(root)

    root.mainloop()


if __name__ == "__main__":
    main()
