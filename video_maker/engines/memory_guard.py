# VideoMaker P0-5 | 2026.10.23
# CHANGED: memory-gate больше не блокируется хроническим swap macOS:
#   пауза/стоп только по свободной RAM; swap — WARN в лог. Критический
#   уровень → MemoryPauseError с инструкцией «закройте приложения и
#   нажмите Продолжить» (resume из манифеста чанков).
# PREV: P0-4 | 2026.10.22 — пауза/стоп по RAM + абсолютному swap.
# REPLACE: video_maker/engines/memory_guard.py
"""Безопасный 4K-рендер на Mac с 8 ГБ RAM.

Гейт читает `vm_stat` (доступная RAM = free + inactive + speculative) и
`sysctl vm.swapusage` (used / total). Перед каждым чанком:
  * RAM < 300 МБ → пауза 30с (не более 5 раз);
  * RAM < 80 МБ → MemoryPauseError (чанк не запускаем, ждём «Продолжить»).

Swap на macOS хронически заполнен и САМ ПО СЕБЕ не является причиной паузы
или стопа: при swap >= 85% пишем `[MEM] swap 90% (нормально для macOS)`.
Исключение: swap >= 99% И RAM < 150 МБ — критично (реальная нехватка).

Все пороги переопределяются env: VIDEOMAKER_MEM_MIN_FREE_MB,
VIDEOMAKER_MEM_CRITICAL_FREE_MB, VIDEOMAKER_MEM_MAX_SWAP_PCT (порог WARN),
VIDEOMAKER_MEM_CRITICAL_SWAP_PCT, VIDEOMAKER_MEM_CRITICAL_SWAP_FREE_MB;
пауза/лимит — VIDEOMAKER_MEM_PAUSE_SEC, VIDEOMAKER_MEM_MAX_PAUSES.
Нечисловое значение env → безопасный дефолт.

Дополнительно: не запускаем кодирование, пока в этой же сессии (дерево
процессов текущего Python) работает другой ffmpeg.

Тестовые хуки (только для проверки гейта, в проде не задавать):
  VIDEOMAKER_MEM_SIM_FREE_MB, VIDEOMAKER_MEM_SIM_SWAP_PCT,
  VIDEOMAKER_MEM_PAUSE_SEC, VIDEOMAKER_MEM_MAX_PAUSES.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

LogFn = Optional[Callable[[str], None]]

DEFAULT_MIN_FREE_MB = 300.0
DEFAULT_CRITICAL_FREE_MB = 80.0
# Swap — только WARN: на macOS он хронически заполнен при свободной RAM.
DEFAULT_MAX_SWAP_PCT = 85.0
DEFAULT_CRITICAL_SWAP_PCT = 99.0
DEFAULT_CRITICAL_SWAP_FREE_MB = 150.0
DEFAULT_PAUSE_SEC = 30.0
DEFAULT_MAX_PAUSES = 5
DEFAULT_FFMPEG_WAIT_SEC = 600.0

MEMORY_PAUSE_HINT = "Закройте все приложения и нажмите «Продолжить»."
MEMORY_RETRY_HINT = "закройте приложения и повторите"


class MemoryPressureError(RuntimeError):
    """Недостаточно памяти для безопасного чанка — рендер остановлен."""


class MemoryPauseError(MemoryPressureError):
    """Рендер приостановлен memory-gate; чанки/манифест сохранены для resume.

    GUI ловит это исключение отдельно от общих ошибок и показывает баннер
    с кнопкой «Продолжить», а не диалог «Ошибка пайплайна».
    """

    def __init__(self, message: str, state: Optional["MemoryState"] = None) -> None:
        super().__init__(message)
        self.state = state
        self.pause = True


class FFmpegBusyError(RuntimeError):
    """Другой ffmpeg этой сессии не завершился — кодирование не начато."""


@dataclass(frozen=True)
class MemoryState:
    """Снимок памяти в МБ."""

    free_mb: int
    swap_used_mb: int
    swap_total_mb: int
    swap_pct: float
    page_size: int = 0

    def as_text(self) -> str:
        return f"RAM {self.free_mb} МБ, swap {self.swap_pct:.0f}%"


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return float(default)
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return int(default)
        return int(float(str(raw).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return int(default)


def _to_mb(value: str, unit: str) -> float:
    """`4096,00M` / `1.5G` → МБ (локаль macOS может печатать запятую)."""
    try:
        num = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
    unit = (unit or "").strip().upper()
    if unit == "G":
        num *= 1024.0
    elif unit == "K":
        num /= 1024.0
    elif unit == "B":
        num /= 1024.0 * 1024.0
    return max(0.0, num)


def parse_vm_stat(text: str) -> tuple[int, int]:
    """Вернуть (доступная RAM МБ, размер страницы). Устойчиво к формату."""
    page_size = 16384
    m = re.search(r"page size of\s+(\d+)\s+bytes", text or "", re.IGNORECASE)
    if m:
        try:
            page_size = int(m.group(1))
        except ValueError:
            page_size = 16384

    pages: dict[str, int] = {}
    for line in (text or "").splitlines():
        # "Pages free:                               4295."
        mm = re.match(r"\s*Pages\s+([^:]+):\s*(\d+)\.?", line, re.IGNORECASE)
        if not mm:
            continue
        try:
            pages[mm.group(1).strip().lower()] = int(mm.group(2))
        except ValueError:
            continue

    # inactive/speculative — reclaimable без свопа, поэтому считаем доступными.
    available_pages = (
        pages.get("free", 0)
        + pages.get("inactive", 0)
        + pages.get("speculative", 0)
    )
    return int(available_pages * page_size / (1024 * 1024)), page_size


def parse_swapusage(text: str) -> tuple[int, int]:
    """`total = 4096,00M used = 2695,19M ...` → (used МБ, total МБ)."""
    body = text or ""
    total = re.search(r"total\s*=\s*([\d.,]+)\s*([KMGB])", body, re.IGNORECASE)
    used = re.search(r"used\s*=\s*([\d.,]+)\s*([KMGB])", body, re.IGNORECASE)
    total_mb = _to_mb(total.group(1), total.group(2)) if total else 0.0
    used_mb = _to_mb(used.group(1), used.group(2)) if used else 0.0
    return int(used_mb), int(total_mb)


def read_memory_state() -> Optional[MemoryState]:
    """Снимок RAM/swap. None → статистика недоступна (гейт не блокирует)."""
    sim_free = os.environ.get("VIDEOMAKER_MEM_SIM_FREE_MB")
    sim_swap = os.environ.get("VIDEOMAKER_MEM_SIM_SWAP_PCT")
    if sim_free is not None or sim_swap is not None:
        try:
            free_mb = int(float(str(sim_free or "0").replace(",", ".")))
        except (TypeError, ValueError):
            free_mb = 0
        try:
            swap_pct = float(str(sim_swap or "0").replace(",", "."))
        except (TypeError, ValueError):
            swap_pct = 0.0
        swap_pct = min(100.0, max(0.0, swap_pct))
        # Синтетический total=100 МБ — чтобы состояние было самосогласованным.
        return MemoryState(
            free_mb=max(0, free_mb),
            swap_used_mb=int(swap_pct),
            swap_total_mb=100,
            swap_pct=swap_pct,
            page_size=0,
        )

    try:
        vm = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=10
        )
        sw = subprocess.run(
            ["sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=10
        )
    except Exception:
        return None
    if vm.returncode != 0:
        return None

    free_mb, page_size = parse_vm_stat(vm.stdout or "")
    used_mb, total_mb = parse_swapusage(sw.stdout or "") if sw.returncode == 0 else (0, 0)
    if total_mb > 0:
        swap_pct = min(100.0, max(0.0, used_mb * 100.0 / total_mb))
    else:
        swap_pct = 0.0
    return MemoryState(
        free_mb=free_mb,
        swap_used_mb=used_mb,
        swap_total_mb=total_mb,
        swap_pct=swap_pct,
        page_size=page_size,
    )


def thresholds() -> tuple[float, float, float, float, float, int]:
    """(min_free_mb, swap_warn_pct, crit_free_mb, crit_swap_pct, pause_sec, pauses).

    swap_warn_pct — только порог предупреждения в логе, не причина паузы.
    """
    return (
        _env_float("VIDEOMAKER_MEM_MIN_FREE_MB", DEFAULT_MIN_FREE_MB),
        _env_float("VIDEOMAKER_MEM_MAX_SWAP_PCT", DEFAULT_MAX_SWAP_PCT),
        _env_float("VIDEOMAKER_MEM_CRITICAL_FREE_MB", DEFAULT_CRITICAL_FREE_MB),
        _env_float("VIDEOMAKER_MEM_CRITICAL_SWAP_PCT", DEFAULT_CRITICAL_SWAP_PCT),
        max(0.0, _env_float("VIDEOMAKER_MEM_PAUSE_SEC", DEFAULT_PAUSE_SEC)),
        max(0, _env_int("VIDEOMAKER_MEM_MAX_PAUSES", DEFAULT_MAX_PAUSES)),
    )


def critical_swap_free_mb() -> float:
    """RAM-порог, ниже которого почти полный swap считается критичным."""
    return max(
        0.0,
        _env_float("VIDEOMAKER_MEM_CRITICAL_SWAP_FREE_MB", DEFAULT_CRITICAL_SWAP_FREE_MB),
    )


def _is_critical(
    state: MemoryState,
    crit_free: float,
    crit_swap: float,
    crit_swap_free: float = DEFAULT_CRITICAL_SWAP_FREE_MB,
) -> bool:
    """Критично только при реальной нехватке RAM.

    Высокий swap сам по себе (даже 100%) на macOS — норма. Он становится
    критичным лишь вместе с почти исчерпанной RAM (< crit_swap_free).
    """
    if state.free_mb < crit_free:
        return True
    return state.swap_pct >= crit_swap and state.free_mb < crit_swap_free


def _is_low(state: MemoryState, min_free: float, _max_swap: float = 0.0) -> bool:
    """Нужна ли пауза. Только свободная RAM; swap — WARN, не критерий."""
    return state.free_mb < min_free


def memory_pause_message(state: MemoryState) -> str:
    """Сообщение критического уровня — ровно та фраза, которую ждёт GUI."""
    return (
        f"[MEM] Недостаточно памяти (RAM {state.free_mb} МБ, "
        f"swap {state.swap_pct:.0f}%). {MEMORY_PAUSE_HINT}"
    )


def memory_retry_message(state: MemoryState) -> str:
    """Повторное нажатие «Продолжить», когда память ещё не освободилась."""
    return (
        f"[MEM] Недостаточно памяти: RAM {state.free_mb} МБ, "
        f"swap {state.swap_pct:.0f}% — {MEMORY_RETRY_HINT}"
    )


def memory_ok_message(state: MemoryState) -> str:
    return f"[MEM] Память в норме (RAM {state.free_mb} МБ, swap {state.swap_pct:.0f}%) — продолжаем"


def swap_warning_message(state: MemoryState) -> str:
    return f"[MEM] swap {state.swap_pct:.0f}% (нормально для macOS)"


@dataclass(frozen=True)
class MemoryCheck:
    """Результат проверки: ok / low / critical (+ предупреждение о swap)."""

    level: str
    state: Optional[MemoryState]
    message: str
    swap_warning: str = ""

    @property
    def can_run(self) -> bool:
        return self.level == "ok"


def evaluate_memory(
    state: Optional[MemoryState] = None,
    *,
    min_free: Optional[float] = None,
    swap_warn: Optional[float] = None,
    crit_free: Optional[float] = None,
    crit_swap: Optional[float] = None,
    crit_swap_free: Optional[float] = None,
) -> MemoryCheck:
    """Классифицировать снимок памяти (без сна и без исключений)."""
    if (
        min_free is None
        or swap_warn is None
        or crit_free is None
        or crit_swap is None
    ):
        t_min, t_swap, t_crit_free, t_crit_swap, _pause, _max_pauses = thresholds()
        min_free = t_min if min_free is None else min_free
        swap_warn = t_swap if swap_warn is None else swap_warn
        crit_free = t_crit_free if crit_free is None else crit_free
        crit_swap = t_crit_swap if crit_swap is None else crit_swap
    if crit_swap_free is None:
        crit_swap_free = critical_swap_free_mb()

    if state is None:
        return MemoryCheck(
            level="unknown",
            state=None,
            message="[MEM] статистика vm_stat/swap недоступна — гейт пропущен",
        )

    warn = swap_warning_message(state) if state.swap_pct >= float(swap_warn) else ""
    if _is_critical(state, float(crit_free), float(crit_swap), float(crit_swap_free)):
        return MemoryCheck("critical", state, memory_pause_message(state), warn)
    if _is_low(state, float(min_free), float(swap_warn)):
        return MemoryCheck("low", state, memory_retry_message(state), warn)
    return MemoryCheck("ok", state, memory_ok_message(state), warn)


def can_continue(state: Optional[MemoryState] = None) -> tuple[bool, str]:
    """Можно ли нажать «Продолжить»: RAM >= min_free, swap не мешает.

    Возвращает (можно, сообщение). При нехватке — точная фраза для GUI:
    `[MEM] Недостаточно памяти: RAM X МБ, swap Y% — закройте приложения и повторите`.
    """
    if state is None:
        state = read_memory_state()
    check = evaluate_memory(state)
    if check.state is None:
        return True, check.message
    if check.level == "ok":
        return True, memory_ok_message(check.state)
    return False, memory_retry_message(check.state)


def wait_for_memory(
    log_fn: LogFn = None,
    label: str = "",
    sleep_fn: Callable[[float], None] = time.sleep,
    reader: Optional[Callable[[], Optional[MemoryState]]] = None,
) -> Optional[MemoryState]:
    """Дождаться безопасного уровня RAM перед чанком.

    Swap не блокирует рендер (на macOS он хронически заполнен): при высоком
    swap пишем WARN. Возвращает последний снимок (или None, если статистика
    недоступна). Бросает MemoryPauseError на критическом уровне — рендер
    приостанавливается с инструкцией нажать «Продолжить».
    """
    _log = log_fn or (lambda _m: None)
    if reader is None:
        reader = read_memory_state
    min_free, swap_warn, crit_free, crit_swap, pause, max_pauses = thresholds()
    crit_swap_free = critical_swap_free_mb()

    state = reader()
    if state is None:
        _log("[MEM] статистика vm_stat/swap недоступна — гейт пропущен")
        return None

    prefix = f"[MEM] {label}: " if label else "[MEM] "
    _log(f"{prefix}{state.as_text()}")
    swap_warned = False

    def _warn_swap(st: MemoryState) -> None:
        nonlocal swap_warned
        if st.swap_pct >= swap_warn:
            if not swap_warned:
                _log(swap_warning_message(st))
                swap_warned = True
        else:
            swap_warned = False

    pauses = 0
    while True:
        state = reader()
        if state is None:
            _log("[MEM] статистика пропала — гейт пропущен")
            return None
        check = evaluate_memory(
            state,
            min_free=min_free,
            swap_warn=swap_warn,
            crit_free=crit_free,
            crit_swap=crit_swap,
            crit_swap_free=crit_swap_free,
        )
        _warn_swap(state)
        if check.level == "critical":
            _log(check.message)
            raise MemoryPauseError(check.message, state=state)
        if check.level == "ok":
            if pauses:
                _log(f"[MEM] память освободилась: {state.as_text()} — продолжаем")
            return state
        if pauses >= max_pauses:
            _log(
                f"[MEM] после {pauses} пауз RAM всё ещё {state.free_mb} МБ "
                f"(< {min_free:g} МБ) — продолжаем без ожидания"
            )
            return state

        _log(
            f"[MEM] пауза {pause:g}с: RAM {state.free_mb} МБ < {min_free:g} МБ "
            f"(swap {state.swap_pct:.0f}%) — ждём освобождения"
        )
        sleep_fn(pause)
        pauses += 1


def _ps_rows() -> list[tuple[int, int, str]]:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    rows: list[tuple[int, int, str]] = []
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def _proc_name(command: str) -> str:
    """Имя исполняемого файла из командной строки (`.../ffmpeg -y ...`)."""
    try:
        return os.path.basename((command or "").strip().split()[0]).lower()
    except (IndexError, AttributeError):
        return ""


def own_ffmpeg_pids() -> list[int]:
    """PID ffmpeg-процессов, запущенных этим Python-процессом (дерево pid)."""
    rows = _ps_rows()
    if not rows:
        return []
    children: dict[int, list[int]] = {}
    comm: dict[int, str] = {}
    for pid, ppid, name in rows:
        children.setdefault(ppid, []).append(pid)
        comm[pid] = name

    own: list[int] = []
    stack = list(children.get(os.getpid(), []))
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        name = _proc_name(comm.get(pid, ""))
        if name.startswith("ffmpeg"):
            own.append(pid)
        stack.extend(children.get(pid, []))
    return sorted(own)


def wait_for_ffmpeg_idle(
    log_fn: LogFn = None,
    label: str = "",
    sleep_fn: Callable[[float], None] = time.sleep,
    timeout: Optional[float] = None,
    poll: float = 5.0,
    pids_fn: Optional[Callable[[], list[int]]] = None,
) -> None:
    """Не начинать чанк, пока другой ffmpeg этой сессии ещё работает."""
    _log = log_fn or (lambda _m: None)
    if pids_fn is None:
        pids_fn = own_ffmpeg_pids
    if timeout is None:
        timeout = max(0.0, _env_float("VIDEOMAKER_FFMPEG_WAIT_SEC", DEFAULT_FFMPEG_WAIT_SEC))
    deadline = time.monotonic() + timeout
    pids = pids_fn()
    if not pids:
        return

    where = f" ({label})" if label else ""
    _log(f"[MEM] ждём другой ffmpeg этой сессии{where}: PID {', '.join(map(str, pids))}")
    while pids:
        if time.monotonic() >= deadline:
            raise FFmpegBusyError(
                f"[MEM] другой ffmpeg этой сессии не завершился за {timeout:.0f}с "
                f"(PID {', '.join(map(str, pids))}) — чанк не запускаем"
            )
        sleep_fn(min(max(0.1, poll), max(0.1, deadline - time.monotonic())))
        pids = pids_fn()
    _log(f"[MEM] ffmpeg этой сессии завершён{where} — продолжаем")
