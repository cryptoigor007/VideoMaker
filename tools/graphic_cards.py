#!/usr/bin/env python3
"""Генератор графических карточек для сцен ``graphic_fallback``.

Карточка — это короткое видео (по умолчанию 4K, 30 fps, H.264) с тёмным
градиентным фоном, лёгкой текстурой/виньеткой, крупной типографикой и
мягкой анимацией: fade-in 0.4 c, медленный zoom/parallax, fade-out.

Модуль не использует PIL и другие внешние библиотеки: только ffmpeg,
ffprobe (для тестов) и стандартная библиотека Python.

Быстрый старт
-------------
    from tools.graphic_cards import make_card, make_number_card, make_quote_card

    make_card(
        "Три правила сильного текста",
        4.5,
        "output/cards/title.mp4",
        style="dark",
    )
    make_number_card("87%", "зрителей досматривают до конца", 4.0, "number.mp4")
    make_quote_card(
        "Простота — высшая степень изощрённости.",
        "Леонардо да Винчи",
        5.0,
        "quote.mp4",
    )

Разрешение задаётся параметрами ``width``/``height``:
    make_card(..., width=1920, height=1080)   # 1080p вместо 4K

Важно про ffmpeg
----------------
Обычный Homebrew-ffmpeg на этой машине собран БЕЗ фильтра ``drawtext``
(нет libfreetype). Поэтому модуль сам ищет сборку ffmpeg с drawtext:

1. переменная окружения ``GRAPHIC_CARDS_FFMPEG`` (явный путь);
2. бинарник из пакета ``imageio-ffmpeg`` (он уже есть в requirements);
3. ``ffmpeg`` из PATH;
4. ``/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`` и типовые Linux-пути.

Если нужной сборки нет — будет понятная ошибка со списком проверенных
кандидатов. Переопределить бинарник можно так:
    GRAPHIC_CARDS_FFMPEG=/path/to/ffmpeg python tools/graphic_cards_test.py

Шрифты
------
Используются системные TTF с поддержкой кириллицы (macOS):
  * заголовки/цифры — ``/System/Library/Fonts/Supplemental/Arial Bold.ttf``;
  * цитаты — ``/System/Library/Fonts/Supplemental/Georgia Italic.ttf``;
  * подписи — ``Arial.ttf``.
Есть fallback на Arial Unicode, Times New Roman Italic, а также DejaVu/
Liberation для Linux. Эмодзи не поддерживаются (нет color-emoji шрифта в
drawtext) — их лучше вырезать из текста до вызова.

Ограничения
-----------
* ``duration_s`` ограничивается сверху 12 секундами (лимит сцены);
  значения <= 0 вызывают ValueError.
* Ширина/высота должны быть чётными (yuv420p), минимум 320x180.
* Автоподбор кегля приблизительный (метрики Arial зашиты в таблицу),
  поэтому ширина текста считается с запасом; переносы — по словам.
* Один рендер 4K стоит несколько секунд CPU; для тестов используйте
  меньшее разрешение и короткую длительность.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from pathlib import Path

__all__ = [
    "MAX_DURATION_S",
    "GraphicCardError",
    "find_ffmpeg",
    "make_card",
    "make_number_card",
    "make_quote_card",
]

MAX_DURATION_S = 12.0
DEFAULT_WIDTH = 3840
DEFAULT_HEIGHT = 2160
DEFAULT_FPS = 30
BITRATE = "14M"
MAXRATE = "16M"

FFMPEG_ENV = "GRAPHIC_CARDS_FFMPEG"


class GraphicCardError(RuntimeError):
    """Ошибка генерации карточки (нет ffmpeg/drawtext, ffmpeg упал и т.п.)."""


# ---------------------------------------------------------------------------
# Шрифты
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = {
    "title": (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
    "body": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "quote": (
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    ),
}


def _pick_font(kind: str) -> str:
    for path in _FONT_CANDIDATES[kind]:
        if os.path.isfile(path):
            return path
    raise GraphicCardError(
        f"Не найден шрифт для '{kind}'. Проверены: "
        + ", ".join(_FONT_CANDIDATES[kind])
    )


# ---------------------------------------------------------------------------
# Палитры
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Palette:
    bg_top: str
    bg_mid: str
    bg_bottom: str
    text: str
    muted: str
    accent: str
    vignette: str | None
    shadow: bool


_PALETTES = {
    # Тёплый графит + латунный акцент.
    "dark": _Palette(
        bg_top="0x26344C",
        bg_mid="0x0E141E",
        bg_bottom="0x05070B",
        text="0xF6F2E9",
        muted="0xC9C2B4",
        accent="0xD9A441",
        vignette="PI/5",
        shadow=True,
    ),
    # Светлая бумага + терракота.
    "light": _Palette(
        bg_top="0xF7F2E8",
        bg_mid="0xE9E0D1",
        bg_bottom="0xD8CCB9",
        text="0x171513",
        muted="0x5E574C",
        accent="0xB4552D",
        vignette=None,
        shadow=False,
    ),
}


def _palette(style: str) -> _Palette:
    try:
        return _PALETTES[style]
    except KeyError:
        raise ValueError(
            f"Неизвестный стиль {style!r}; доступны: {', '.join(sorted(_PALETTES))}"
        ) from None


# ---------------------------------------------------------------------------
# Поиск ffmpeg с drawtext
# ---------------------------------------------------------------------------

_REQUIRED_FILTERS = ("drawtext", "gradients", "vignette", "zoompan", "noise")


@cache
def _missing_filters(binary: str) -> tuple[str, ...]:
    """Список обязательных фильтров, которых нет в данной сборке ffmpeg."""
    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _REQUIRED_FILTERS
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and set(parts[0]) <= set("TS.C"):
            found.add(parts[1])
    return tuple(name for name in _REQUIRED_FILTERS if name not in found)


@cache
def find_ffmpeg() -> str:
    """Вернуть путь к ffmpeg, в котором есть drawtext и остальные фильтры.

    Порядок: GRAPHIC_CARDS_FFMPEG → imageio-ffmpeg → PATH → ffmpeg-full.
    Результат кешируется на процесс.
    """
    explicit = os.environ.get(FFMPEG_ENV, "").strip()
    if explicit:
        if not os.path.isfile(explicit):
            raise GraphicCardError(f"{FFMPEG_ENV}={explicit!r}: файл не найден")
        missing = _missing_filters(explicit)
        if missing:
            raise GraphicCardError(
                f"{FFMPEG_ENV}={explicit!r}: нет фильтров {', '.join(missing)}"
            )
        return explicit

    candidates: list[str] = []
    # imageio-ffmpeg уже в requirements-runtime, но импорт необязателен:
    # его бинарник — самая вероятная сборка ffmpeg с drawtext на macOS.
    try:
        import imageio_ffmpeg
    except ImportError:
        imageio_ffmpeg = None  # type: ignore[assignment]
    if imageio_ffmpeg is not None:
        with suppress(OSError, RuntimeError, ValueError):
            candidates.append(imageio_ffmpeg.get_ffmpeg_exe())

    for name in ("ffmpeg", "ffmpeg-full"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    candidates.extend(
        (
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
            "/opt/local/bin/ffmpeg",
        )
    )

    seen: set[str] = set()
    checked: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        checked.append(candidate)
        if not _missing_filters(candidate):
            return candidate

    raise GraphicCardError(
        "Не найден ffmpeg с нужными фильтрами (drawtext, gradients, vignette, "
        "zoompan, noise). Проверены: "
        + (", ".join(checked) if checked else "—")
        + ".\nУстановите ffmpeg-full либо укажите сборку через "
        + f"{FFMPEG_ENV}=/path/to/ffmpeg."
    )


# ---------------------------------------------------------------------------
# Измерение и перенос текста без PIL
# ---------------------------------------------------------------------------

# Ширины глифов Arial в тысячных долях em (advance widths). Для кириллицы —
# приближённые значения Arial; этого достаточно, чтобы подобрать кегль с
# запасом и не допустить выхода текста за поля.
_GLYPH_W: dict[str, int] = {
    " ": 278,
    "\u00a0": 278,
    "\u2009": 222,
    "\u202f": 222,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
    "№": 722,
    "«": 556,
    "»": 556,
    "“": 556,
    "”": 556,
    "„": 556,
    "—": 1000,
    "–": 556,
    "…": 1000,
    "·": 278,
    "°": 400,
    "₽": 556,
    "€": 556,
    "©": 737,
    "×": 584,
    "÷": 584,
}

_GLYPH_W.update(
    {
        # заглавные латинские
        "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611,
        "G": 778, "H": 722, "I": 278, "J": 500, "K": 667, "L": 556,
        "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722,
        "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667,
        "Y": 667, "Z": 611,
        # строчные латинские
        "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278,
        "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222,
        "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333,
        "s": 500, "t": 278, "u": 556, "v": 500, "w": 722, "x": 500,
        "y": 500, "z": 500,
        # цифры
        "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
        "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
        # заглавные кириллические
        "А": 667, "Б": 667, "В": 667, "Г": 611, "Д": 722, "Е": 667,
        "Ё": 667, "Ж": 944, "З": 667, "И": 722, "Й": 722, "К": 667,
        "Л": 611, "М": 833, "Н": 722, "О": 778, "П": 667, "Р": 667,
        "С": 722, "Т": 611, "У": 667, "Ф": 722, "Х": 667, "Ц": 722,
        "Ч": 667, "Ш": 944, "Щ": 944, "Ъ": 611, "Ы": 722, "Ь": 611,
        "Э": 667, "Ю": 944, "Я": 667,
        # строчные кириллические
        "а": 556, "б": 556, "в": 556, "г": 444, "д": 556, "е": 556,
        "ё": 556, "ж": 778, "з": 500, "и": 556, "й": 556, "к": 500,
        "л": 556, "м": 833, "н": 556, "о": 556, "п": 556, "р": 556,
        "с": 500, "т": 500, "у": 500, "ф": 611, "х": 500, "ц": 556,
        "ч": 556, "ш": 778, "щ": 778, "ъ": 556, "ы": 611, "ь": 556,
        "э": 500, "ю": 722, "я": 500,
    }
)


def _text_width(text: str, size: int) -> float:
    """Приблизительная ширина строки в пикселях (Arial, с запасом)."""
    units = sum(_GLYPH_W.get(ch, 620) for ch in text)
    return units / 1000.0 * size


def _clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"text должен быть str, получено {type(value).__name__}")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        ch for ch in value if ch in "\n\t" or ord(ch) >= 32
    )
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    # схлопываем пустые строки, но сохраняем осмысленные абзацы
    cleaned: list[str] = []
    for line in lines:
        if line or (cleaned and cleaned[-1]):
            cleaned.append(line)
    result = "\n".join(cleaned).strip()
    if not result:
        raise ValueError("Пустой текст карточки")
    return result


def _wrap(text: str, size: int, max_width: float) -> list[str]:
    result: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            result.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if _text_width(trial, size) <= max_width:
                current = trial
            else:
                result.append(current)
                current = word
        result.append(current)
    return result


def _balanced_lines(
    text: str, size: int, max_width: float, line_count: int
) -> list[str] | None:
    """Разбить текст на ``line_count`` визуально ровных строк (без «сирот»).

    Динамическое программирование: минимизируем ширину самой длинной строки
    при сохранении порядка слов. Возвращает None, если ровно разбить нельзя.
    """
    if "\n" in text or line_count < 2 or line_count > 5:
        return None
    words = text.split()
    if len(words) < line_count:
        return None

    count = len(words)
    widths: dict[tuple[int, int], float] = {}
    for start in range(count):
        for end in range(start + 1, count + 1):
            widths[(start, end)] = _text_width(" ".join(words[start:end]), size)

    inf = float("inf")
    dp = [[inf] * (count + 1) for _ in range(line_count + 1)]
    back = [[0] * (count + 1) for _ in range(line_count + 1)]
    dp[0][0] = 0.0

    for line in range(1, line_count + 1):
        for end in range(1, count + 1):
            for start in range(line - 1, end):
                width = widths[(start, end)]
                if width > max_width:
                    continue
                candidate = max(dp[line - 1][start], width)
                if candidate < dp[line][end]:
                    dp[line][end] = candidate
                    back[line][end] = start

    if dp[line_count][count] == inf:
        return None

    result: list[str] = []
    end = count
    for line in range(line_count, 0, -1):
        start = back[line][end]
        result.append(" ".join(words[start:end]))
        end = start
    result.reverse()
    return result


def _fit_lines(
    text: str,
    *,
    max_width: float,
    max_height: float,
    max_size: int,
    min_size: int,
    line_spacing: float = 1.16,
    max_lines: int | None = None,
) -> tuple[list[str], int]:
    """Подобрать максимальный кегль, при котором текст влезает в блок."""
    size = max(min_size, int(max_size))
    lines = _wrap(text, size, max_width)
    while size > min_size:
        lines = _wrap(text, size, max_width)
        fits_width = all(_text_width(line, size) <= max_width for line in lines)
        fits_height = len(lines) * size * line_spacing <= max_height
        fits_lines = max_lines is None or len(lines) <= max_lines
        if fits_width and fits_height and fits_lines:
            break
        size = max(min_size, int(size * 0.94))
    if 2 <= len(lines) <= 5:
        balanced = _balanced_lines(text, size, max_width, len(lines))
        if balanced:
            lines = balanced
    return lines, size


# ---------------------------------------------------------------------------
# Построение фильтра
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _TextDraw:
    text: str
    font: str
    size: int
    color: str
    y: int
    shadow: bool = False
    alpha: float = 1.0


@dataclass(frozen=True)
class _Rule:
    y: int
    width: int
    height: int
    color: str
    alpha: float = 0.9


def _fq(value: str) -> str:
    """Заключить значение в кавычки для filtergraph."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _drawtext(item: _TextDraw, textfile: Path) -> str:
    color = item.color
    if item.alpha < 1.0:
        color = f"{color}@{item.alpha:.3f}"
    options = [
        f"fontfile={_fq(item.font)}",
        f"textfile={_fq(str(textfile))}",
        "expansion=none",
        f"fontsize={item.size}",
        f"fontcolor={color}",
        "x=(w-text_w)/2",
        f"y={int(item.y)}",
    ]
    if item.shadow:
        options += [
            "shadowcolor=black@0.35",
            "shadowx=0",
            f"shadowy={max(2, item.size // 40)}",
        ]
    return "drawtext=" + ":".join(options)


def _drawbox(rule: _Rule) -> str:
    return (
        f"drawbox=x=(iw-{rule.width})/2:y={int(rule.y)}:"
        f"w={rule.width}:h={rule.height}:"
        f"color={rule.color}@{rule.alpha:.2f}:t=fill"
    )


def _normalise_duration(duration_s: float) -> float:
    try:
        duration = float(duration_s)
    except (TypeError, ValueError):
        raise ValueError(f"duration_s должен быть числом, получено {duration_s!r}") from None
    if duration <= 0:
        raise ValueError("duration_s должен быть > 0")
    return min(duration, MAX_DURATION_S)


def _validate_size(width: int, height: int, fps: int) -> tuple[int, int, int]:
    for name, value in (("width", width), ("height", height), ("fps", fps)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} должен быть int")
    if width < 320 or height < 180:
        raise ValueError("Минимальный размер карточки — 320x180")
    if width % 2 or height % 2:
        raise ValueError("width и height должны быть чётными (yuv420p)")
    if not 1 <= fps <= 60:
        raise ValueError("fps должен быть в диапазоне 1..60")
    return width, height, fps


def _background_chain(
    palette: _Palette,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    zoom: float,
    pan: float,
) -> list[str]:
    """Фон рендерится в половинном разрешении и апскейлится — быстрее 4K."""
    bw = max(640, width // 2)
    bh = max(360, height // 2)
    frames = max(2, round(duration * fps))
    chain = [
        (
            f"gradients=s={bw}x{bh}:r={fps}:"
            f"c0={palette.bg_top}:c1={palette.bg_mid}:c2={palette.bg_bottom}:"
            f"nb_colors=3:type=linear:speed=0:seed=7:"
            f"x0=0:y0=0:x1={bw}:y1={bh}:d={duration:.3f}"
        ),
        "format=yuv420p",
        "noise=c0s=3:c0f=t+u:c1s=0:c2s=0",
    ]
    if palette.vignette:
        chain.append(f"vignette=angle={palette.vignette}")
    chain.append(
        "zoompan="
        f"z='1+{zoom}*on/{frames}':"
        f"x='iw/2-(iw/zoom/2)+{pan}*iw*on/{frames}':"
        "y='ih/2-(ih/zoom/2)':"
        f"d=1:s={bw}x{bh}:fps={fps}"
    )
    chain.append(f"scale={width}:{height}:flags=lanczos")
    return chain


def _build_filter(
    palette: _Palette,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    graphics: list[str],
    zoom: float = 0.032,
    pan: float = 0.006,
) -> str:
    chain = _background_chain(
        palette,
        width=width,
        height=height,
        fps=fps,
        duration=duration,
        zoom=zoom,
        pan=pan,
    )
    chain.extend(graphics)

    fade_in = min(0.4, duration * 0.25)
    fade_out = min(0.5, duration * 0.25)
    chain.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    chain.append(f"fade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}")
    return ",\n".join(chain) + "\n[vout]"


def _render(
    *,
    out_path: str | os.PathLike[str],
    palette: _Palette,
    texts: list[_TextDraw],
    rules: list[_Rule],
    width: int,
    height: int,
    fps: int,
    duration: float,
) -> Path:
    out = Path(out_path).expanduser()
    if not out.suffix:
        out = out.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out.stem}.", suffix=".mp4", dir=str(out.parent)
    )
    os.close(fd)
    tmp_out = Path(tmp_name)
    tmp_out.unlink(missing_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="graphic_cards_") as tmpdir:
            graphics: list[str] = []
            for index, item in enumerate(texts):
                textfile = Path(tmpdir) / f"line_{index:03d}.txt"
                textfile.write_text(item.text, encoding="utf-8")
                graphics.append(_drawtext(item, textfile))
            graphics.extend(_drawbox(rule) for rule in rules)

            graph = _build_filter(
                palette,
                width=width,
                height=height,
                fps=fps,
                duration=duration,
                graphics=graphics,
            )

            graph_file = Path(tmpdir) / "filtergraph.txt"
            graph_file.write_text(graph, encoding="utf-8")

            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-filter_complex_script",
                str(graph_file),
                "-map",
                "[vout]",
                "-t",
                f"{duration:.3f}",
                "-r",
                str(fps),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-b:v",
                BITRATE,
                "-maxrate",
                MAXRATE,
                "-bufsize",
                "28M",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-x264-params",
                "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                "-g",
                "60",
                "-movflags",
                "+faststart",
                "-an",
                "-f",
                "mp4",
                str(tmp_out),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()
                raise GraphicCardError(
                    "ffmpeg не смог собрать карточку:\n"
                    + "\n".join(stderr.splitlines()[-12:])
                )

        os.replace(tmp_out, out)
    finally:
        tmp_out.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------------------
# Публичные конструкторы
# ---------------------------------------------------------------------------

def make_card(
    text_ru: str,
    duration_s: float,
    out_path: str | os.PathLike[str],
    style: str = "dark",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Карточка-заголовок: крупный текст по центру.

    Args:
        text_ru: текст (кириллица/латиница), можно с переносами ``\\n``.
        duration_s: длительность, > 0..12 c (больше — обрезается до 12).
        out_path: путь к mp4; родительские папки создаются.
        style: ``"dark"`` (по умолчанию) или ``"light"``.
        width, height: 3840x2160 по умолчанию, можно 1920x1080.
        fps: 30 по умолчанию.

    Returns:
        Путь к готовому файлу.
    """
    text = _clean_text(text_ru)
    duration = _normalise_duration(duration_s)
    width, height, fps = _validate_size(width, height, fps)
    palette = _palette(style)

    margin_x = int(width * 0.09)
    max_w = width - 2 * margin_x
    lines, size = _fit_lines(
        text,
        max_width=max_w,
        max_height=height * 0.56,
        max_size=int(height * 0.14),
        min_size=max(28, int(height * 0.028)),
        max_lines=6,
    )
    step = round(size * 1.16)
    block_h = step * len(lines)
    y0 = (height - block_h) // 2

    title_font = _pick_font("title")
    texts = [
        _TextDraw(
            line,
            title_font,
            size,
            palette.text,
            y0 + index * step,
            shadow=palette.shadow,
        )
        for index, line in enumerate(lines)
    ]

    rule = _Rule(
        y=max(int(height * 0.06), y0 - int(height * 0.065)),
        width=max(96, width // 28),
        height=max(4, height // 340),
        color=palette.accent,
    )
    return _render(
        out_path=out_path,
        palette=palette,
        texts=texts,
        rules=[rule],
        width=width,
        height=height,
        fps=fps,
        duration=duration,
    )


def make_number_card(
    value: str,
    label: str,
    duration_s: float,
    out_path: str | os.PathLike[str],
    style: str = "dark",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Карточка с большой цифрой и подписью под ней.

    ``value`` — строка, а не число, чтобы поддержать "87%", "1 200", "+34%".
    """
    value_text = _clean_text(value)
    label_text = _clean_text(label).upper() if label else ""
    duration = _normalise_duration(duration_s)
    width, height, fps = _validate_size(width, height, fps)
    palette = _palette(style)

    title_font = _pick_font("title")
    body_font = _pick_font("body")

    value_lines, value_size = _fit_lines(
        value_text,
        max_width=width * 0.84,
        max_height=height * 0.40,
        max_size=int(height * 0.38),
        min_size=max(40, int(height * 0.05)),
        max_lines=1,
        line_spacing=1.0,
    )
    value_line = value_lines[0]
    value_h = int(value_size * 0.98)

    label_lines: list[str] = []
    label_size = 0
    if label_text:
        label_lines, label_size = _fit_lines(
            label_text,
            max_width=width * 0.70,
            max_height=height * 0.11,
            max_size=int(height * 0.052),
            min_size=max(24, int(height * 0.018)),
            max_lines=2,
            line_spacing=1.22,
        )
    label_step = round(label_size * 1.22) if label_size else 0
    label_block = label_step * len(label_lines)

    gap_value = int(height * 0.045)
    rule_h = max(4, height // 380)
    gap_label = int(height * 0.032)
    total = value_h + gap_value + rule_h + gap_label + label_block
    y0 = max(int(height * 0.10), (height - total) // 2)

    texts = [
        _TextDraw(
            value_line,
            title_font,
            value_size,
            palette.text,
            y0,
            shadow=palette.shadow,
        )
    ]
    rule_y = y0 + value_h + gap_value
    if label_lines:
        label_y = rule_y + rule_h + gap_label
        texts.extend(
            _TextDraw(
                line,
                body_font,
                label_size,
                palette.muted,
                label_y + index * label_step,
                shadow=palette.shadow,
            )
            for index, line in enumerate(label_lines)
        )

    rule = _Rule(
        y=rule_y,
        width=max(120, width // 18),
        height=rule_h,
        color=palette.accent,
    )
    return _render(
        out_path=out_path,
        palette=palette,
        texts=texts,
        rules=[rule],
        width=width,
        height=height,
        fps=fps,
        duration=duration,
    )


def make_quote_card(
    text: str,
    author: str,
    duration_s: float,
    out_path: str | os.PathLike[str],
    style: str = "dark",
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Карточка-цитата: курсивный текст и подпись автора."""
    quote_text = _clean_text(text)
    author_text = author.strip() if isinstance(author, str) else ""
    duration = _normalise_duration(duration_s)
    width, height, fps = _validate_size(width, height, fps)
    palette = _palette(style)

    quote_font = _pick_font("quote")
    body_font = _pick_font("body")

    quote_lines, quote_size = _fit_lines(
        quote_text,
        max_width=width * 0.74,
        max_height=height * 0.50,
        max_size=int(height * 0.095),
        min_size=max(30, int(height * 0.03)),
        max_lines=8,
        line_spacing=1.24,
    )
    quote_step = round(quote_size * 1.24)
    quote_block = quote_step * len(quote_lines)

    author_lines: list[str] = []
    author_size = 0
    if author_text:
        author_lines, author_size = _fit_lines(
            f"— {author_text}",
            max_width=width * 0.62,
            max_height=height * 0.06,
            max_size=int(height * 0.038),
            min_size=max(22, int(height * 0.016)),
            max_lines=1,
            line_spacing=1.2,
        )
    author_step = round(author_size * 1.2) if author_size else 0
    author_block = author_step * len(author_lines)

    rule_h = max(4, height // 380)
    gap_rule = int(height * 0.045)
    gap_author = int(height * 0.05)
    total = rule_h + gap_rule + quote_block + gap_author + author_block
    y0 = max(int(height * 0.11), (height - total) // 2)

    texts = [
        _TextDraw(
            line,
            quote_font,
            quote_size,
            palette.text,
            y0 + rule_h + gap_rule + index * quote_step,
            shadow=palette.shadow,
        )
        for index, line in enumerate(quote_lines)
    ]
    if author_lines:
        author_y = y0 + rule_h + gap_rule + quote_block + gap_author
        texts.extend(
            _TextDraw(
                line,
                body_font,
                author_size,
                palette.accent,
                author_y + index * author_step,
                shadow=palette.shadow,
            )
            for index, line in enumerate(author_lines)
        )

    rule = _Rule(
        y=y0,
        width=max(90, width // 34),
        height=rule_h,
        color=palette.accent,
    )
    return _render(
        out_path=out_path,
        palette=palette,
        texts=texts,
        rules=[rule],
        width=width,
        height=height,
        fps=fps,
        duration=duration,
    )
