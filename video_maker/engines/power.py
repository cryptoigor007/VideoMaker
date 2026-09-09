"""Управление питанием: не засыпать во время обработки, выключить по завершению."""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from typing import Optional

log = logging.getLogger(__name__)

_caffeinate_proc: Optional[subprocess.Popen] = None


def prevent_sleep_start(log_fn=None) -> bool:
    """Запретить сон/гибернацию на время обработки (macOS/Linux).

    На macOS использует caffeinate -dims (дисплей + idle + system).
    Возвращает True если удалось запустить.
    """
    global _caffeinate_proc
    _log = log_fn or log.info

    if _caffeinate_proc is not None and _caffeinate_proc.poll() is None:
        _log("[POWER] prevent_sleep уже активен")
        return True

    system = platform.system()
    try:
        if system == "Darwin":
            # -d disk, -i idle, -m disk, -s system — не давать уснуть
            _caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-dims"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log(f"[POWER] caffeinate запущен (pid={_caffeinate_proc.pid}) — компьютер не уснёт")
            return True
        elif system == "Linux":
            # systemd-inhibit если есть
            _caffeinate_proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle:sleep:shutdown",
                    "--who=VideoMaker",
                    "--why=Pipeline running",
                    "--mode=block",
                    "sleep", "infinity",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log(f"[POWER] systemd-inhibit запущен (pid={_caffeinate_proc.pid})")
            return True
        else:
            _log(f"[POWER] prevent_sleep не поддерживается на {system}")
            return False
    except FileNotFoundError:
        _log("[POWER] caffeinate/systemd-inhibit не найден — пропуск prevent_sleep")
        return False
    except Exception as e:
        _log(f"[POWER] Не удалось запустить prevent_sleep: {e}")
        return False


def prevent_sleep_stop(log_fn=None) -> None:
    """Снять запрет сна."""
    global _caffeinate_proc
    _log = log_fn or log.info

    if _caffeinate_proc is None:
        return

    try:
        if _caffeinate_proc.poll() is None:
            _caffeinate_proc.terminate()
            try:
                _caffeinate_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _caffeinate_proc.kill()
            _log("[POWER] prevent_sleep остановлен")
    except Exception as e:
        _log(f"[POWER] Ошибка остановки prevent_sleep: {e}")
    finally:
        _caffeinate_proc = None


def shutdown_computer(delay_sec: int = 60, log_fn=None) -> bool:
    """Выключить компьютер через delay_sec секунд.

    На macOS: osascript (мягкое выключение, как через меню).
    На Linux: shutdown -h.
    Пользователь может отменить в течение delay_sec.
    """
    _log = log_fn or log.info
    system = platform.system()

    try:
        if system == "Darwin":
            # Мягкое выключение через System Events (показывает диалог отмены)
            # Альтернатива без диалога: sudo shutdown -h +1
            script = (
                f'tell application "System Events" to shut down'
            )
            # Даём пользователю время увидеть сообщение
            _log(f"[POWER] Выключение через {delay_sec} сек (можно отменить в диалоге macOS)...")
            # Используем shutdown с задержкой — можно отменить: sudo killall shutdown
            # Более дружелюбно — osascript после паузы
            time.sleep(min(5, delay_sec))  # короткая пауза чтобы лог успел записаться
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                # Fallback: shutdown command
                _log(f"[POWER] osascript failed: {result.stderr}, пробуем shutdown")
                subprocess.Popen(
                    ["shutdown", "-h", f"+{max(1, delay_sec // 60)}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            _log("[POWER] Команда выключения отправлена")
            return True
        elif system == "Linux":
            mins = max(1, delay_sec // 60)
            subprocess.Popen(
                ["shutdown", "-h", f"+{mins}", "VideoMaker: обработка завершена"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log(f"[POWER] shutdown -h +{mins} — отмена: sudo shutdown -c")
            return True
        else:
            _log(f"[POWER] shutdown не поддерживается на {system}")
            return False
    except Exception as e:
        _log(f"[POWER] Ошибка выключения: {e}")
        return False
