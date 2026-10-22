"""Надёжное разрешение существующих путей.

Пути приходят из Tk, JSON, буфера обмена и API в разной Unicode-нормализации
(NFC/NFD), иногда в кавычках или с `~`/переменными окружения. Сетевые тома
(SMB, ``/Volumes/...``) могут на первое обращение ответить «файла нет», хотя
он виден в Finder. Резолвер возвращает именно тот путь, который принимают
``os``/``ffmpeg``/``ffprobe``, либо ``None``, если пути действительно нет.
"""
from __future__ import annotations

import os
import time
import unicodedata
from urllib.parse import unquote, urlparse

__all__ = [
    "clean_path",
    "resolve_existing_path",
    "resolve_existing_file",
    "resolve_existing_dir",
]

# SMB/NFS могут «просыпаться» не мгновенно: 3 попытки с паузой ~0.6с.
_NETWORK_RETRIES = 3
_NETWORK_RETRY_DELAY = 0.6
# Защита от комбинаторного взрыва для путей с десятком не-ASCII компонентов.
_MAX_NORMALIZATION_VARIANTS = 64
_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("«", "»"),
    ("“", "”"),
    ("‘", "’"),
)
_KIND_CHECKS = {
    "file": os.path.isfile,
    "dir": os.path.isdir,
    "any": os.path.exists,
}


def clean_path(path: "str | os.PathLike[str] | None") -> str:
    """Привести пользовательский путь к виду, пригодному для os/ffmpeg.

    Убирает пробелы/переводы строк, парные кавычки, префикс ``file://``,
    раскрывает ``~`` и переменные окружения. Не меняет сам файл на диске.
    """
    if path is None:
        return ""
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    text = str(path).replace("\x00", "").strip()
    if not text:
        return ""
    if text.lower().startswith("file://"):
        parsed = urlparse(text)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            text = f"//{parsed.netloc}{unquote(parsed.path)}"
        else:
            text = unquote(parsed.path)
    # Кавычки могут быть вложены (например, "'/path with spaces'").
    for _ in range(3):
        before = text
        text = text.strip()
        for left, right in _QUOTE_PAIRS:
            if len(text) >= 2 and text.startswith(left) and text.endswith(right):
                text = text[len(left):-len(right)].strip()
                break
        if text == before:
            break
    text = os.path.expandvars(os.path.expanduser(text))
    return text


def _check(path: str, kind: str) -> bool:
    checker = _KIND_CHECKS.get(kind, os.path.isfile)
    try:
        return bool(checker(path))
    except (OSError, ValueError):
        # SMB/EIO: считаем «пока недоступно», повторим попытку.
        return False


def _component_variants(component: str) -> list[str]:
    variants: list[str] = []
    for value in (
        component,
        unicodedata.normalize("NFC", component),
        unicodedata.normalize("NFD", component),
    ):
        if value not in variants:
            variants.append(value)
    return variants


def _path_variants(path: str):
    """Все сочетания NFC/NFD по компонентам (NFD-родитель + NFC-имя и т.п.)."""
    parts = path.split(os.sep)
    combinations: list[tuple[str, ...]] = [tuple(parts)]
    for index, part in enumerate(parts):
        if not part:
            continue
        variants = _component_variants(part)
        if len(variants) == 1:
            continue
        expanded: list[tuple[str, ...]] = []
        for combination in combinations:
            for variant in variants:
                expanded.append(
                    combination[:index] + (variant,) + combination[index + 1:]
                )
        combinations = expanded[:_MAX_NORMALIZATION_VARIANTS]
    return (os.sep.join(combination) for combination in combinations)


def _matching_entries(entries: "list[str]", name: str) -> "list[str]":
    exact = [entry for entry in entries if entry == name]
    if exact:
        return exact
    target = unicodedata.normalize("NFC", name)
    normalized = [
        entry for entry in entries
        if unicodedata.normalize("NFC", entry) == target
    ]
    if normalized:
        return normalized
    # Сетевые тома бывают регистронезависимыми: последний шанс — casefold.
    folded = target.casefold()
    return [
        entry for entry in entries
        if unicodedata.normalize("NFC", entry).casefold() == folded
    ]


def _resolve_entry(parent: str, name: str, kind: str) -> "str | None":
    try:
        entries = os.listdir(parent)
    except (OSError, ValueError):
        return None
    for entry in _matching_entries(entries, name):
        candidate = os.path.join(parent, entry)
        if _check(candidate, kind):
            return candidate
    return None


def _resolve_directory(path: str) -> "str | None":
    if _check(path, "dir"):
        return path
    parent, name = os.path.split(path)
    if not name:
        return None
    resolved_parent = _resolve_directory(parent)
    if not resolved_parent:
        return None
    return _resolve_entry(resolved_parent, name, "dir")


def _resolve_via_listing(path: str, kind: str) -> "str | None":
    """Найти basename в реальном листинге родителя (NFC/NFD-эквивалентность)."""
    parent, name = os.path.split(path)
    if not name:
        return None
    resolved_parent = _resolve_directory(parent)
    if not resolved_parent:
        return None
    return _resolve_entry(resolved_parent, name, kind)


def _resolve_once(path: str, kind: str) -> "str | None":
    if _check(path, kind):
        return path
    for variant in _path_variants(path):
        if variant != path and _check(variant, kind):
            return variant
    return _resolve_via_listing(path, kind)


def _is_network_path(path: str) -> bool:
    return (
        path.startswith("/Volumes/")
        or path.startswith("/Network/")
        or path.startswith("//")
    )


def resolve_existing_path(
    path: "str | os.PathLike[str] | None",
    *,
    kind: str = "file",
    retries: "int | None" = None,
) -> "str | None":
    """Вернуть реальный существующий путь или ``None``.

    ``kind``: ``file`` (по умолчанию), ``dir`` или ``any``.
    Для сетевых томов по умолчанию 3 попытки с паузой 0.6с.
    """
    if kind not in _KIND_CHECKS:
        kind = "file"
    cleaned = clean_path(path)
    if not cleaned:
        return None
    absolute = os.path.abspath(cleaned)
    attempts = retries
    if attempts is None:
        attempts = _NETWORK_RETRIES if _is_network_path(absolute) else 1
    try:
        attempts = max(1, int(attempts))
    except (TypeError, ValueError):
        attempts = 1
    for attempt in range(attempts):
        found = _resolve_once(absolute, kind)
        if found:
            return found
        if attempt + 1 < attempts:
            time.sleep(_NETWORK_RETRY_DELAY)
    return None


def resolve_existing_file(
    path: "str | os.PathLike[str] | None",
    *,
    retries: "int | None" = None,
) -> "str | None":
    """Существующий файл (с NFC/NFD и сетевыми повторами)."""
    return resolve_existing_path(path, kind="file", retries=retries)


def resolve_existing_dir(
    path: "str | os.PathLike[str] | None",
    *,
    retries: "int | None" = None,
) -> "str | None":
    """Существующая папка (с NFC/NFD и сетевыми повторами)."""
    return resolve_existing_path(path, kind="dir", retries=retries)
