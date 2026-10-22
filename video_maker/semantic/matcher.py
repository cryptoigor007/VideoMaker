"""Лексико-семантический матчер «сцена ↔ клип» («псевдо-нейролинк»).

Скор в [0,1]: unigram-пересечение (с расширением тем RU↔EN) + биграммы.
Ноль — метаданные/слова не пересеклись (вызывающий сохраняет исходный порядок).
"""

from __future__ import annotations

import re
from pathlib import Path

from .taxonomy import expand_tokens, primary_theme, queries_for_tokens, themes_for_tokens

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

# Служебные слова, не несущие смысла для матчинга
_STOP_RU = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "это", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по",
    "только", "ее", "мне", "было", "вот", "от", "меня", "еще", "нет", "о", "из",
    "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли", "если", "уже", "или",
    "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь",
    "там", "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без",
    "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто",
    "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь", "этом",
    "один", "почти", "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем",
    "всех", "никогда", "можно", "при", "наконец", "два", "об", "другой", "хоть",
    "после", "над", "больше", "тот", "через", "эти", "нас", "про", "всего", "них",
    "какая", "много", "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою",
    "этой", "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им",
    "более", "всегда", "конечно", "всю", "между",
}
_STOP_EN = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are", "was",
    "for", "with", "by", "as", "it", "its", "this", "that", "these", "those", "be",
    "from", "we", "you", "they", "he", "she", "his", "her", "their", "our", "my",
}


def scene_index_from_path(path: str) -> int | None:
    """Номер сцены из имени папки пула: 01_… / 02-fix / 13 … → 1/2/13.

    Смарт-B-roll раскладывает клипы по папкам сцен (01_, 02_, …). По этому
    номеру план жёстко привязывает клип к временному окну своей сцены.
    """
    try:
        name = Path(str(path or "")).parent.name
    except Exception:
        return None
    m = re.match(r"^(\d{1,3})(?:[_\-\s.]|$)", name)
    if not m:
        return None
    try:
        idx = int(m.group(1))
    except ValueError:
        return None
    return idx if idx > 0 else None


def tokenize(text: str) -> set[str]:
    """Токены текста: слова (RU/EN), без служебных и цифр, длина >= 3."""
    out: set[str] = set()
    for m in _TOKEN_RE.findall(str(text or "").lower()):
        if len(m) < 3 or m.isdigit():
            continue
        if m in _STOP_RU or m in _STOP_EN:
            continue
        out.add(m)
    return out


_CLIP_TOKENS_CACHE: dict[str, set[str]] = {}


def clip_tokens(path_or_name: str) -> set[str]:
    """Токены клипа: имя файла + (для клипов из базы) описание/теги Vision.

    Описание из большой базы («что реально в клипе») точнее имени файла —
    оценка соответствия сцене становится смысловой, а не только по имени.
    """
    key = str(path_or_name or "")
    cached = _CLIP_TOKENS_CACHE.get(key)
    if cached is not None:
        return cached
    toks = tokenize(Path(key).stem)
    try:  # клип из большой базы? → добавляем описание/теги/запрос
        from .library import find_by_path
        rec = find_by_path(key)
        if rec:
            toks |= tokenize(str(rec.get("description") or ""))
            toks |= tokenize(str(rec.get("tags") or "").replace('"', " "))
            toks |= tokenize(str(rec.get("query") or ""))
    except Exception:
        pass
    _CLIP_TOKENS_CACHE[key] = toks
    return toks


def _bigrams(tokens: set[str]) -> set[tuple[str, str]]:
    ordered = sorted(tokens)
    return {(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)}


def score_clip_vs_text(clip_path_or_name: str, text: str, extra_terms: str = "") -> float:
    """Скор соответствия клипа тексту сцены (0 = нет пересечения).

    Учитывает главную тему сцены: клип той же главной темы получает бонус,
    клип чужой темы — штраф (иначе «часы» побеждали сцену «про друзей»).
    """
    ct = clip_tokens(clip_path_or_name)
    tt = tokenize(text) | tokenize(extra_terms)
    if not ct or not tt:
        return 0.0
    ct_exp = expand_tokens(ct)
    tt_exp = expand_tokens(tt)
    inter = ct_exp & tt_exp
    if not inter:
        return 0.0
    uni = len(inter) / max(1, min(len(ct_exp), len(tt_exp)))
    cb, tb = _bigrams(ct), _bigrams(tt)
    bi = (len(cb & tb) / max(1, len(tb))) if tb else 0.0
    base = 0.7 * uni + 0.3 * bi
    text_theme = primary_theme(tt)
    clip_theme = primary_theme(ct)
    if text_theme and clip_theme:
        if text_theme == clip_theme:
            base += 0.25  # главная тема совпала — приоритет
        else:
            base *= 0.6   # чужая главная тема — штраф
    return min(1.0, base)


def scene_slots(segments: list[dict], target_duration: float, slot_seconds: float = 9.0) -> list[dict]:
    """Окна сцен: K слотов по времени + текст сегментов, попадающих в окно.

    Возвращает [{idx, start, end, text}]. Время сегментов и target_duration — в одной базе.
    """
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    total = float(target_duration) or (float(segs[-1].get("end", 0.0)) if segs else 0.0)
    if total <= 0:
        return []
    k = max(1, int(round(total / max(2.0, float(slot_seconds)))))
    slots: list[dict] = []
    for i in range(k):
        start = total * i / k
        end = total * (i + 1) / k
        texts: list[str] = []
        for s in segs:
            ss = float(s.get("start", 0.0))
            se = float(s.get("end", ss))
            mid = (ss + se) / 2.0
            if start <= mid < end or (i == k - 1 and mid >= start):
                t = str(s.get("text") or "").strip()
                if t:
                    texts.append(t)
        slots.append({"idx": i, "start": round(start, 2), "end": round(end, 2), "text": " ".join(texts)})
    return slots


def order_pool_for_scenes(pool: list[str], segments: list[dict], target_duration: float,
                          slot_seconds: float = 9.0) -> list[str]:
    """Смысловой порядок пула: на каждый слот — лучший неиспользованный клип, затем остальные.

    Без совпадений вовсе — исходный порядок (нулевое поведение не меняется).
    """
    slots = scene_slots(segments, target_duration, slot_seconds)
    if not slots or not pool:
        return list(pool)
    used: set[int] = set()
    picks: list[str] = []
    for slot in slots:
        text = slot.get("text") or ""
        if not text:
            continue
        best_j, best_sc = None, 0.0
        for j, path in enumerate(pool):
            if j in used:
                continue
            sc = score_clip_vs_text(path, text)
            if sc > best_sc:
                best_j, best_sc = j, sc
        if best_j is not None:
            used.add(best_j)
            picks.append(pool[best_j])
    rest = [p for j, p in enumerate(pool) if j not in used]
    return picks + rest


def queries_for_text(text: str, max_queries: int = 2, phrase_index: int = 0) -> list[str]:
    """EN-запросы для стока по русскому тексту сцены."""
    return queries_for_tokens(tokenize(text), max_queries=max_queries, phrase_index=phrase_index)


def themes_for_text(text: str) -> dict[str, int]:
    """Сработавшие темы текста (для отчётов/дебага)."""
    return themes_for_tokens(tokenize(text))
