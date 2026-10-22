"""Проверка контента скачанных клипов через Gemini Vision.

Запросы к Pixabay хорошие, но теги врут: «office workers» может оказаться
пустым офисом. Здесь мы извлекаем кадры из клипа и спрашиваем Gemini Vision:
«насколько этот клип подходит сцене?» — клипы с низкой оценкой отбрасываются.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path

_FENCE_RE = re.compile(r"\{.*\}", re.DOTALL)
_ARRAY_FENCE_RE = re.compile(r"\[.*\]", re.DOTALL)
_ANY_FENCE_RE = re.compile(r"[\[{].*[\]}]", re.DOTALL)

# [VM-FIX] Версия промпта оценки: меняется вместе с правилами скоринга.
# Соль в ключе кэша (file + sha1(SALT|query)) — старые мягкие оценки
# (атмосфера проходила как 6-10) НЕ переиспользуются после ужесточения.
_PROMPT_SALT = "verify-v2-literal-action-2026-10-18"


def _ffmpeg_bin() -> str:
    try:
        from video_maker.engines.video import _ffmpeg_bin as _app_ffmpeg_bin
        return _app_ffmpeg_bin()
    except Exception:
        return "ffmpeg"


def extract_frames(clip_path: str, out_dir: Path, *, count: int = 2) -> list[str]:
    """Кадры из клипа — jpg для vision.

    Имена файлов уникальны для каждого клипа: иначе в общей папке партии
    кадры разных клипов перезаписывают друг друга и модель видит только
    последний клип (описания смещались).
    """
    ff = _ffmpeg_bin()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]", "_", Path(str(clip_path)).stem)[:48] or "clip"
    try:
        r = subprocess.run(
            [ff, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", clip_path],
            capture_output=True, text=True, timeout=30,
        )
        dur = float((r.stdout or "0").strip() or 0)
    except Exception:
        dur = 0.0
    if dur <= 0.5:
        # [VM-PREFILTER] ffmpeg-обёртка приложения не умеет -show_entries —
        # берём реальную длительность через ffprobe. Иначе короткие клипы
        # (1.5-4с) давали кадр за пределами файла и score=-1.
        try:
            _ok, _dur = _probe_video(clip_path)
            dur = _dur if _dur > 0.5 else 4.0
        except Exception:
            dur = 4.0
    if count <= 0:  # авто: 3-5 кадров по длине клипа (точнее выбор)
        count = 3 if dur <= 8.0 else (4 if dur <= 15.0 else 5)
    if count <= 2:
        # Короткие клипы (≤6с): достаточно ОДНОГО кадра — экономия Vision-вызовов.
        if dur <= 6.0:
            times = [dur * 0.5]
        else:
            times = [dur * 0.25, dur * 0.6]
    else:
        times = [dur * (0.1 + 0.8 * i / (count - 1)) for i in range(count)]
    frames: list[str] = []
    for i, t in enumerate(times):
        dst = out_dir / f"{stem}_f{i}.jpg"
        try:
            subprocess.run(
                [ff, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", clip_path,
                 "-frames:v", "1", "-vf", "scale=640:-2", str(dst)],
                capture_output=True, timeout=60,
            )
            if dst.exists() and dst.stat().st_size > 2000:
                frames.append(str(dst))
        except Exception:
            continue
    return frames


def _gemini_vision(prompt: str, frames: list[str], *, key: str, model: str,
                   timeout: int = 60) -> dict | None:
    """REST-вызов Gemini с изображениями. Возвращает распарсенный JSON или None."""
    import urllib.request

    parts: list[dict] = [{"text": prompt}]
    for f in frames:
        try:
            data = base64.b64encode(Path(f).read_bytes()).decode("ascii")
        except Exception:
            continue
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.1}})
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    )
    req = urllib.request.Request(
        url, data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return None
    m = _FENCE_RE.search(str(text))
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def verify_clip(clip_path: str, expected: str, queries: list[str], *,
                key: str, model: str = "gemini-flash-latest", tmp_dir: Path | None = None) -> dict:
    """Оценка клипа: {'score': 0..10, 'what': 'что на кадрах'} (score=-1 при ошибке)."""
    out_dir = (tmp_dir or Path(clip_path).parent / ".verify_frames")
    frames = extract_frames(clip_path, out_dir)
    if not frames:
        return {"score": -1, "what": "кадры не извлеклись"}
    prompt = (
        "Ты — монтажёр. Проверь стоковый видеоклип для конкретной сцены.\n"
        f"Сцена: {expected}\n"
        f"Искали по запросам: {', '.join(queries) if queries else expected}\n"
        "На изображениях — кадры из этого клипа.\n"
        "Сначала ответь на главный вопрос: «Показано ли БУКВАЛЬНО действие из сцены? (да/нет)».\n"
        "Правила оценки (жёстко):\n"
        "- Если действие НЕ показано буквально — итоговый балл НЕ выше 4, даже если кадр красивый "
        "или «подходит по атмосфере».\n"
        "- Балл 6-10 — только при буквальном совпадении действия ИЛИ точной атмосфере без противоречий; "
        "чужое действие в кадре — противоречие → не выше 4.\n"
        "- Если кадры размытые, тёмные, непонятно что происходит — оценка НЕ выше 3.\n"
        "Ответь ТОЛЬКО JSON без пояснений: "
        '{"literal_action": "да|нет", "score": <0-10>, "what": "<кратко, что реально видно на кадрах>"}'
    )
    res = _gemini_vision(prompt, frames, key=key, model=model)
    if not isinstance(res, dict):
        return {"score": -1, "what": "нет ответа vision"}
    try:
        score = int(round(float(res.get("score", -1))))
    except Exception:
        score = -1
    return {"score": max(-1, min(10, score)), "what": str(res.get("what") or "")[:200]}


# ======================================================================
# [VM-VLM3] Привязка кадров: текст кандидата → ЕГО кадры → закрывающий
# разделитель. Плоский протокол «Кадры N-M» (все тексты одним блоком, потом
# все кадры) на практике СДВИГАЕТ привязку: модель описывает клип i
# содержимым клипа i-1 (проверено 2026-10-19). Чередование обязательно.
#
# [VM-BATCH4] Один Vision-вызов на 4-6 кандидатов (вместо вызова на клип):
# модель возвращает JSON-МАССИВ [{"n":1,"score":..,"what":".."}, ...].
# Номер n проверяется; при несовпадении/битом JSON — повтор, затем дробление
# батча до 2-3, затем по одному. Кэш прежний: file + model + scene_key.
# ======================================================================
_PROMPT_SALT_V3 = "verify-v3-interleaved-2026-10-19"

_CANDIDATE_RULES = (
    "Для КАЖДОГО кандидата сначала ответь: «Показано ли БУКВАЛЬНО действие из сцены? (да/нет)».\n"
    "Правила оценки (жёстко): если действие НЕ показано буквально — итоговый балл НЕ выше 4, "
    "даже если кадр красивый. 6-10 — только при буквальном совпадении действия ИЛИ точной "
    "атмосфере без противоречий (чужое действие в кадре = противоречие → не выше 4); "
    "3-5 — частично; 0-2 — не то.\n"
    "Кадры разных кандидатов НЕ путать: кандидат N описывается только по кадрам, которые "
    "идут сразу после строки «Кандидат N» и до строки «— конец кандидата N —».\n"
    "Ответь ТОЛЬКО валидным JSON-МАССИВОМ без пояснений: "
    '[{"n":1,"literal_action":"да|нет","score":N,"what":"кратко что на кадрах"}, ...] — '
    "строго по одному элементу на кандидата, n = номер кандидата (1..N)."
)


def _scene_key_of(entry: dict) -> str:
    """Стабильный ключ сцены для кэша (visual → scene → query), нормализованный."""
    for k in ("scene_key", "visual", "scene", "query"):
        v = str(entry.get(k) or "").strip()
        if v:
            return " ".join(v.lower().split())[:300]
    return ""


def _cache_th_v3(scene_key: str, model: str = "") -> str:
    import hashlib as _hl
    # [VM-VLM3] модель — часть ключа: оценки разных моделей не смешиваются
    return _hl.sha1((_PROMPT_SALT_V3 + "|" + str(model) + "|" + scene_key).encode("utf-8")).hexdigest()[:16]


def _gemini_json(parts: list[dict], keys: list[str], model: str, *, timeout: int = 180,
                 log=print, tries: int = 3, expect: str = "dict",
                 counter: list[int] | None = None) -> dict | list | None:
    """REST-вызов Gemini с готовыми parts; ротация ключей на 401/403/429/5xx.

    expect="array" — ответ-массив (для батча кандидатов); "dict" — объект;
    "any" — любой JSON. counter[0] увеличивается на каждый HTTP-запрос.
    """
    import time as _t
    import urllib.request
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": 0.1}}).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    if expect == "array":
        rx = _ARRAY_FENCE_RE
    elif expect == "any":
        rx = _ANY_FENCE_RE
    else:
        rx = _FENCE_RE
    # [VM-FAST] ротация без штрафных пауз: мёртвые ключи (401/403) выключаем
    # на процесс, 429 — кулдаун 120с, 5xx — 15с; один запрос на ключ.
    _dead = globals().setdefault("_VLM_DEAD_KEYS", set())
    _cool = globals().setdefault("_VLM_COOLDOWN", {})
    _sticky = globals().setdefault("_VLM_STICKY_KEY", {"k": ""})
    _ord = list(keys)
    _last = _sticky.get("k") or ""
    if _last in _ord:  # старт с последнего удачного ключа
        _ix = _ord.index(_last)
        _ord = _ord[_ix:] + _ord[:_ix]
    _now = _t.time()
    _calls = 0
    for i, k in enumerate(_ord[:max(1, len(_ord))]):
        if k in _dead:
            continue
        if _cool.get(k, 0.0) > _now:
            continue
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json",
                                              "x-goog-api-key": k}, method="POST")
        if counter is not None:
            counter[0] += 1
        _calls += 1
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, 25)) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            code = getattr(e, "code", -1)
            if code in (401, 403):
                _dead.add(k)
                if log:
                    log(f"[VISION] ключ {i + 1}: {code} — мёртв (выключен)")
            elif code == 429:
                _cool[k] = _t.time() + 120.0
                if log:
                    log(f"[VISION] ключ {i + 1}: 429 — пауза 120с")
            else:
                _cool[k] = _t.time() + 15.0
                if log:
                    log(f"[VISION] ключ {i + 1}: {code or 'сеть'} — пауза 15с")
            continue
        try:
            cands = payload.get("candidates") or []
            text = "".join(str(p.get("text") or "")
                           for p in ((cands[0].get("content") or {}).get("parts") or []))
        except Exception:
            text = ""
        if not text.strip():
            _cool[k] = _t.time() + 10.0
            continue
        m = rx.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
        if expect == "array" and not isinstance(data, (list, dict)):
            return None
        if expect == "dict" and not isinstance(data, dict):
            return None
        _sticky["k"] = k  # запомним рабочий ключ — следующий вызов начнём с него
        return data
    if log and _calls:
        log(f"[VISION] все ключи не ответили ({_calls} попыток) — кандидат без оценки")
    return None


def _candidate_parts(items: list[dict], frames_by_item: list[list[str]]) -> list[dict]:
    """parts: заголовок + (текст кандидата → ЕГО кадры → конец кандидата).

    [VM-VLM3] Чередование сохраняет привязку кадров: между кадрами разных
    кандидатов всегда стоит текстовая граница с номером.
    """
    head = (
        "Ты проверяешь кадры для видеомонтажа. Ниже КАЖДЫЙ кандидат идёт со своими кадрами: "
        "сначала строка «Кандидат N. Сцена: ...», сразу после неё — кадры ЭТОГО кандидата, "
        "затем строка «— конец кандидата N —».\n"
        "Кадры разных кандидатов не путать: кандидат N описывается только по своим кадрам.\n"
        + _CANDIDATE_RULES
    )
    parts: list[dict] = [{"text": head}]
    for i, (it, frames) in enumerate(zip(items, frames_by_item), 1):
        scene = str(it.get("scene") or it.get("query") or "")
        parts.append({"text": f"Кандидат {i}. Сцена: «{scene}». Кадры кандидата {i}:"})
        for f in frames:
            try:
                data = base64.b64encode(Path(f).read_bytes()).decode("ascii")
            except Exception:
                continue
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
        parts.append({"text": f"— конец кандидата {i} —"})
    return parts


def _parse_candidates(data, n: int) -> list[dict] | None:
    """Разобрать ответ батча: массив с n=1..N (или порядок, если n нет)."""
    if isinstance(data, dict):
        if "score" in data and n == 1:
            data = [data]
        else:
            data = data.get("candidates") or data.get("clips")
    if not isinstance(data, list) or len(data) < n:
        return None
    items = data[:n]
    if not all(isinstance(c, dict) for c in items):
        return None
    has_n = ["n" in c for c in items]
    if any(has_n) and not all(has_n):
        return None
    if all(has_n):
        by: dict[int, dict] = {}
        for c in items:
            try:
                k = int(c.get("n"))
            except Exception:
                return None
            if k < 1 or k > n or k in by:
                return None
            by[k] = c
        if set(by) != set(range(1, n + 1)):
            return None
        return [by[i] for i in range(1, n + 1)]
    # старый/упрощённый ответ без n — принимаем только при точной длине
    return items if len(data) == n else None


# ======================================================================
# [VM-PREFILTER] Жёсткие предфильтры ДО Vision: дубли/близнецы, явные
# hard-теги сцены, битые/слишком короткие файлы. Экономит вызовы и не
# пропускает в отбор заведомо неподходящее.
# ======================================================================
_PROBE_CACHE: dict[tuple[str, int, int], tuple[bool, float]] = {}
_EMB_CACHE: dict | None = None
_EMB_TRIED = False


def _norm_text(text) -> str:
    t = " ".join(str(text or "").lower().replace("-", " ").split())
    for a, b in (("smart phone", "smartphone"), ("cell phone", "cellphone"),
                 ("mobile phone", "smartphone"), ("close up", "closeup"),
                 ("time lapse", "timelapse"), ("slow motion", "slowmotion"),
                 ("high five", "highfive"), ("work out", "workout")):
        t = t.replace(a, b)
    return t


def _ffprobe_bin() -> str:
    try:
        p = Path(_ffmpeg_bin())
        cand = p.with_name("ffprobe")
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    import shutil as _sh
    return _sh.which("ffprobe") or "ffprobe"


def _probe_video(path: str) -> tuple[bool, float]:
    """(есть ли валидный видеопоток, длительность). Кэш по size+mtime."""
    p = Path(str(path))
    try:
        st = p.stat()
    except Exception:
        return False, 0.0
    if st.st_size < 2000:
        return False, 0.0
    key = (str(p), int(st.st_size), int(st.st_mtime_ns))
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    try:
        r = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            res = (False, 0.0)
        else:
            d = json.loads(r.stdout or "{}")
            has_video = any(str(s.get("codec_type")) == "video" for s in (d.get("streams") or []))
            try:
                dur = float((d.get("format") or {}).get("duration") or 0)
            except Exception:
                dur = 0.0
            res = (bool(has_video and dur > 0), dur)
    except Exception:
        # ffprobe недоступен — не объявляем файл битым, решает extract_frames
        res = (st.st_size > 2000, 0.0)
    _PROBE_CACHE[key] = res
    return res


def _embeddings_cached() -> dict:
    global _EMB_CACHE, _EMB_TRIED
    if not _EMB_TRIED:
        _EMB_TRIED = True
        try:
            from .embeddings import load as _load_emb
            _EMB_CACHE = _load_emb()
        except Exception:
            _EMB_CACHE = {}
    return _EMB_CACHE or {}


def _cos(a: list[float], b: list[float]) -> float:
    try:
        s = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return s / (na * nb + 1e-9)
    except Exception:
        return 0.0


def _path_key(entry: dict) -> str:
    import os as _os
    f = str(entry.get("file") or "")
    return _os.path.realpath(f) if f else ""


def _source_id(entry: dict) -> str:
    """Pixabay/Pexels id из URL или имени файла (для точных дублей)."""
    for raw in (entry.get("source_url"), entry.get("library_file"),
                entry.get("file"), entry.get("verify_file")):
        s = str(raw or "")
        if not s:
            continue
        m = (re.search(r"(pixabay|pexels)[^0-9]{0,24}(\d{4,})", s, re.I)
             or re.search(r"_(\d{4,})_(pixabay|pexels)", s, re.I))
        if m:
            g = m.groups()
            if g[0].lower() in ("pixabay", "pexels"):
                return f"{g[0].lower()}:{g[1]}"
            return f"{g[1].lower()}:{g[0]}"
    return ""


def _hard_hit(entry: dict) -> str:
    """Явное совпадение hard/must_not-тега сцены с тегами/описанием (без догадок).

    `avoid` намеренно НЕ фильтруем: это мягкий штраф, а не жёсткий стоп.
    """
    raw: list = []
    for key in ("hard", "must_not", "scene_hard"):
        v = entry.get(key)
        if isinstance(v, str):
            raw += re.split(r"[,;]", v)
        elif isinstance(v, (list, tuple, set)):
            raw += list(v)
    terms = [_norm_text(x) for x in raw if str(x).strip()]
    if not terms:
        return ""
    hay = " " + _norm_text(" ".join(str(entry.get(k) or "") for k in
                                   ("tags", "description"))) + " "
    for t in terms:
        if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", hay):
            return t
    return ""


def prefilter_candidates(entries: list[dict], *, log=print,
                         dedup_cos: float = 0.92, min_duration: float = 1.5,
                         use_embeddings: bool = True) -> list[tuple[dict, dict]]:
    """Отсеять до Vision: дубли/близнецы, явные hard-теги, битые/короткие.

    Возвращает пары (дубль, оригинал) — оценки дубля копируются после Vision.
    Отсеянные помечаются verified=True, чтобы не попасть в очередь вызовов.
    """
    dups: list[tuple[dict, dict]] = []
    n_dup = n_hard = n_bad = 0
    need_emb = use_embeddings and any(
        e.get("library_file") or "/broll_library/" in str(e.get("file") or "")
        for e in entries)
    emb = _embeddings_cached() if need_emb else {}
    seen_path: dict[str, dict] = {}
    seen_src: dict[str, dict] = {}
    kept_vecs: list[tuple[dict, list[float]]] = []

    for e in entries:
        f = str(e.get("file") or "")
        if not f:
            continue
        pk = _path_key(e)
        sid = _source_id(e)
        lib = str(e.get("library_file") or f)
        v = emb.get(lib) if emb else None
        keeper = seen_path.get(pk) if pk else None
        if keeper is None and sid:
            keeper = seen_src.get(sid)
        if keeper is None and v:
            for cand, kv in kept_vecs:
                if _cos(v, kv) > dedup_cos:
                    keeper = cand
                    break
        if keeper is not None and keeper is not e:
            if not e.get("verified"):
                e["verified"] = True
                e["score"] = -1
                e["what"] = "предфильтр: дубль"
                e["prefilter"] = "dup"
                dups.append((e, keeper))
                n_dup += 1
            continue
        if pk:
            seen_path[pk] = e
        if sid:
            seen_src[sid] = e
        if v:
            kept_vecs.append((e, v))

    for e in entries:
        if e.get("verified") or not str(e.get("file") or ""):
            continue
        term = _hard_hit(e)
        if term:
            e["verified"] = True
            e["score"] = 0
            e["what"] = f"предфильтр: hard-тег «{term}»"
            e["prefilter"] = "hard"
            n_hard += 1

    for e in entries:
        if e.get("verified") or not str(e.get("file") or ""):
            continue
        vf = str(e.get("verify_file") or e.get("file") or "")
        try:
            dur = float(e.get("duration") or 0)
        except Exception:
            dur = 0.0
        p = Path(vf)
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        ok, probed = (size > 2000), 0.0
        if ok and dur < min_duration:
            ok, probed = _probe_video(vf)
            if ok and probed <= 0:
                probed = dur
        elif ok and dur <= 0:
            ok, probed = _probe_video(vf)
        if not ok:
            reason = "битый файл" if size <= 2000 else "нет видео"
            e["verified"] = True
            e["score"] = 0
            e["what"] = f"предфильтр: {reason}"
            e["prefilter"] = "bad"
            n_bad += 1
        elif (probed or dur) and 0 < (probed or dur) < min_duration:
            e["verified"] = True
            e["score"] = 0
            e["what"] = f"предфильтр: короткий клип {(probed or dur):.1f}с"
            e["prefilter"] = "short"
            n_bad += 1

    total = n_dup + n_hard + n_bad
    if total and log:
        log(f"[VISION] предфильтр: отсеяно {total} (дубли {n_dup}, hard {n_hard}, "
            f"битые {n_bad}) | вызовов сэкономлено: {total}")
    return dups


def _apply_twins(dups: list[tuple[dict, dict]]) -> None:
    """Скопировать оценку оригинала дублю (кадры идентичны)."""
    for e, keeper in dups:
        sc = keeper.get("score")
        if isinstance(sc, int) and keeper.get("verified"):
            e["score"] = sc
            e["what"] = str(keeper.get("what") or "")[:200]
            e["verified"] = True


def _plural_candidates(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "кандидат"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "кандидата"
    return "кандидатов"


def _plural_calls(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "вызов"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "вызова"
    return "вызовов"


def verify_candidates_gemini(entries: list[dict], *, key: str, keys: list[str] | None = None,
                             model: str = "gemini-3.1-flash-lite", batch_size: int = 5,
                             min_score: int = 5, delete_bad: bool = False, log=print,
                             use_cache: bool = True, frames_count: int = 2,
                             tmp_dir: Path | None = None, deadline: float | None = None,
                             prefilter: bool = True) -> list[dict]:
    """Оценить 4-6 кандидатов ОДНИМ Gemini-вызовом (JSON-массив с n).

    Привязка кадров — только чередованием «текст кандидата → его кадры».
    Если ответ невалиден/номера не совпали — повтор, затем дробление батча
    до 2-3, затем по одному. Кэш: (file, model, scene_key) — прежний.
    """
    import sqlite3 as _sq
    import time as _t
    _keys: list[str] = []
    for _k in [key] + list(keys or []):
        if _k and _k not in _keys:
            _keys.append(_k)
    try:
        dups = prefilter_candidates(entries, log=log) if prefilter else []
    except Exception as _pfe:
        if log:
            log(f"[VISION] предфильтр пропущен: {str(_pfe)[:100]}")
        dups = []
    cache = None
    if use_cache:
        try:
            from .library import db_path as _dbp
            cache = _sq.connect(str(_dbp()), timeout=15)
            cache.execute("CREATE TABLE IF NOT EXISTS scores (file TEXT, th TEXT, score INTEGER, "
                          "what TEXT, at TEXT, PRIMARY KEY(file, th))")
            cache.commit()
        except Exception:
            cache = None
    th_of: dict[int, str] = {}
    todo: list[dict] = []
    n_cached = 0
    for e in entries:
        f = str(e.get("file") or "")
        if not f or e.get("verified"):
            continue
        vf = str(e.get("verify_file") or f)
        if not Path(vf).exists():
            e["score"] = -1
            e["what"] = "нет файла"
            e["verified"] = True
            continue
        th = _cache_th_v3(_scene_key_of(e), model)
        th_of[id(e)] = th
        if cache is not None:
            try:
                row = cache.execute("SELECT score, what FROM scores WHERE file=? AND th=?",
                                    (f, th)).fetchone()
            except Exception:
                row = None
            if row is not None:
                e["score"] = int(row[0])
                e["what"] = str(row[1] or "")
                e["verified"] = True
                e["cached"] = True
                n_cached += 1
                continue
        todo.append(e)
    if n_cached:
        log(f"[VISION] из кэша: {n_cached}")
    # [PERF-HISTORY] негативный кэш: клип с checks>=3 и avg<2.5 (и без
    # пересечения тегов со сценой) повторно не проверяем — экономим вызовы.
    try:
        from . import perf_history
        n_hist = perf_history.apply_history_skips(entries, log=log)
        todo = [e for e in todo if not e.get("verified")]
    except Exception as _phe:
        if log:
            log(f"[VISION] история клипов недоступна: {str(_phe)[:80]}")

    frames_of: dict[int, list[str]] = {}

    def _frames(e: dict) -> list[str]:
        if id(e) not in frames_of:
            frames_of[id(e)] = extract_frames(
                str(e.get("verify_file") or e["file"]),
                (tmp_dir or Path(str(e.get("verify_file") or e["file"])).parent / ".verify_frames"),
                count=frames_count)
        return frames_of[id(e)]

    def _frames_many(items: list[dict]) -> list[list[str]]:
        """Кадры батча параллельно (4 потока): извлечение 4K-SMB — главный тормоз."""
        if len(items) <= 1:
            return [_frames(e) for e in items]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(items))) as _ex:
            return list(_ex.map(_frames, items))

    def _store(e: dict) -> None:
        f = str(e.get("file") or "")
        th = th_of.get(id(e))
        sc = e.get("score")
        if cache is not None and f and th and isinstance(sc, int) and sc >= 0:
            try:
                from datetime import datetime as _dt, timezone as _tz
                cache.execute("INSERT OR REPLACE INTO scores (file, th, score, what, at) "
                              "VALUES (?,?,?,?,?)",
                              (f, th, sc, str(e.get("what") or ""),
                               _dt.now(_tz.utc).isoformat()))
                cache.commit()
            except Exception:
                pass

    def _single(e: dict) -> None:
        frames = _frames(e)
        if not frames:
            e["score"] = -1
            e["what"] = "кадры не извлеклись"
            e["verified"] = True
            return
        d = _gemini_json(_candidate_parts([e], [frames]), _keys, model,
                         expect="array", log=log)
        arr = _parse_candidates(d, 1)
        c = arr[0] if arr else None
        try:
            sc = max(0, min(10, int(round(float((c or {}).get("score", -1))))))
        except Exception:
            sc = -1
        e["score"] = sc
        e["what"] = str((c or {}).get("what") or "")[:200]
        e["verified"] = True
        _store(e)

    bs = max(2, min(6, int(batch_size or 5)))
    chunks = [todo[i:i + bs] for i in range(0, len(todo), bs)]
    nb = len(chunks)

    def _process(chunk: list[dict], label: str) -> None:
        if deadline and _t.time() > deadline:
            for e in chunk:
                e["score"] = -1
                e["what"] = "дедлайн"
                e["verified"] = True
            return
        fr = _frames_many(chunk)
        ok_idx = [j for j, f in enumerate(fr) if f]
        for j in range(len(chunk)):
            if j not in ok_idx:
                chunk[j]["score"] = -1
                chunk[j]["what"] = "кадры не извлеклись"
                chunk[j]["verified"] = True
        items = [chunk[j] for j in ok_idx]
        if not items:
            return
        t0 = _t.time()
        counter = [0]
        parsed = _parse_candidates(
            _gemini_json(_candidate_parts(items, [fr[j] for j in ok_idx]), _keys, model,
                         expect="array", log=log, counter=counter), len(items))
        if parsed is None:
            # один повтор тем же батчем: JSON мог обрезаться
            parsed = _parse_candidates(
                _gemini_json(_candidate_parts(items, [fr[j] for j in ok_idx]), _keys, model,
                             expect="array", log=log, counter=counter), len(items))
        if parsed is None:
            if len(items) > 3:
                mid = max(2, len(items) // 2)
                log(f"[VISION] батч {label}/{nb}: ответ невалиден — дробим "
                    f"{len(items)} → {mid}+{len(items) - mid}")
                _process(items[:mid], label + "a")
                _process(items[mid:], label + "b")
            else:
                log(f"[VISION] батч {label}/{nb}: ответ невалиден — по одному")
                for e in items:
                    _single(e)
            return
        best = -1
        for e, c in zip(items, parsed):
            try:
                sc = max(0, min(10, int(round(float(c.get("score", -1))))))
            except Exception:
                sc = -1
            e["score"] = sc
            e["what"] = str(c.get("what") or "")[:200]
            e["verified"] = True
            _store(e)
            best = max(best, sc)
        dt = _t.time() - t0
        log(f"[VISION] батч {label}/{nb}: {len(items)} {_plural_candidates(len(items))} → "
            f"{counter[0]} {_plural_calls(counter[0])}, {dt:.1f}с | лучший {best}/10")
        for e in items:
            sc = e.get("score", -1)
            mark = "✓" if (isinstance(sc, int) and (sc >= min_score or sc < 0)) else "·"
            log(f"[VISION] {mark} {Path(str(e.get('file'))).name[:40]} | {sc}/10 — "
                f"{str(e.get('what'))[:60]}")

    for bi, b in enumerate(chunks, 1):
        _process(b, str(bi))

    _apply_twins(dups)
    if cache is not None:
        for e in entries:
            _store(e)
        try:
            cache.commit()
        except Exception:
            pass
    if delete_bad:
        for e in entries:
            sc = e.get("score")
            if isinstance(sc, int) and 0 <= sc < min_score:
                try:
                    Path(str(e.get("file"))).unlink()
                except Exception:
                    pass
    try:  # [PERF-HISTORY] каждая новая Vision-оценка → реестр клипов
        from . import perf_history as _ph
        _ph.record_entries(entries)
    except Exception:
        pass
    return entries


def verify_batch_gemini(entries: list[dict], *, key: str, keys: list[str] | None = None,
                        model: str = "gemini-3.1-flash-lite", batch_size: int = 5,
                        min_score: int = 5, delete_bad: bool = False, log=print,
                        use_cache: bool = True, frames_count: int = 2,
                        tmp_dir: Path | None = None, deadline: float | None = None) -> list[dict]:
    """Совместимая обёртка: батч 4-6 кандидатов за один Vision-вызов."""
    return verify_candidates_gemini(
        entries, key=key, keys=keys, model=model, batch_size=batch_size,
        min_score=min_score, delete_bad=delete_bad, log=log, use_cache=use_cache,
        frames_count=frames_count, tmp_dir=tmp_dir, deadline=deadline)

def _find_opencode() -> str:
    """Клиент OpenCode: приоритет V2 (opencode2) — у него живая консоль (Vision)."""
    import shutil as _sh
    for cand in ("opencode2", "/opt/homebrew/bin/opencode2",
                 "opencode", "/opt/homebrew/bin/opencode", "/usr/local/bin/opencode",
                 str(Path.home() / ".opencode/bin/opencode")):
        p = _sh.which(cand) if "/" not in cand else (cand if Path(cand).exists() else None)
        if p:
            return p
    return ""


def verify_batch_opencode(batch: list[dict], *, log=print,
                          model: str = "opencode/nemotron-3.5-super-vl-free") -> bool:
    """Проверка клипов бесплатной VL-моделью OpenCode — ПО ОДНОМУ кандидату.

    [VM-VLM3] CLI принимает кадры только плоским списком `-f`, без вставки
    текста между изображениями. Пакетный «Кадры N-M» на практике сдвигал
    привязку (модель описывала клип i кадрами клипа i-1), поэтому здесь
    один вызов = один кандидат: привязка кадров не может сместиться.
    Обновляет score/what/verified. True, если хоть один кандидат оценён.
    """
    bin_path = _find_opencode()
    if not bin_path:
        return False
    tmp = Path(str(batch[0].get("file") or "")).parent / ".verify_frames"
    ok = False
    for e in batch:
        if not str(e.get("file") or ""):
            continue
        frames = extract_frames(str(e.get("verify_file") or e["file"]), tmp)
        if not frames:
            continue
        prompt = (
            "Ты проверяешь кадры одного стокового клипа для сцены видеомонтажа.\n"
            f"Сцена: «{str(e.get('scene') or e.get('query') or '')}»\n"
            "Ниже приложены кадры ТОЛЬКО этого клипа, по порядку.\n"
            + _CANDIDATE_RULES
        )
        # ВАЖНО: промпт (позиционный) — ДО флагов -f, иначе массив -f съест его как файл
        args: list[str] = [bin_path, "run", "-m", model, "--format", "json", prompt]
        for f in frames:
            args += ["-f", f]
        try:
            import subprocess as _sp
            r = _sp.run(args, capture_output=True, text=True, timeout=240)
        except Exception as ex:
            log(f"[VISION] opencode вызов не удался: {ex}")
            return ok
        text = ""
        for ln in (r.stdout or "").splitlines():
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if isinstance(ev, dict) and ev.get("type") == "text":
                text += str((ev.get("part") or {}).get("text") or "")
        m = _ANY_FENCE_RE.search(text)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except Exception:
            continue
        arr = _parse_candidates(data, 1)
        c = arr[0] if arr else None
        if not isinstance(c, dict):
            continue
        try:
            e["score"] = max(0, min(10, int(round(float(c.get("score", -1))))))
        except Exception:
            e["score"] = -1
        e["what"] = str(c.get("what") or "")[:200]
        e["verified"] = True
        e["verifier"] = "opencode-vl"
        ok = True
    return ok


def verify_pool(entries: list[dict], *, key: str, model: str = "gemini-3.5-flash-lite",
                min_score: int = 5, delete_bad: bool = True, log=print,
                use_opencode: bool = True, batch_size: int = 5,
                keys: list[str] | None = None) -> list[dict]:
    """Проверить скачанные клипы; слабые (score < min_score) удалить.

    [VM-BATCH4] 4-6 кандидатов оцениваются ОДНИМ Gemini Vision-вызовом
    (чередование «Кандидат N → его кадры», ответ — JSON-массив с n).
    Предфильтры (дубли/hard/битые) отсекаются до вызовов. OpenCode-VL —
    только резерв для кандидатов без оценки (по одному, без сдвига кадров).
    """
    _dups: list[tuple[dict, dict]] = []
    try:  # hard/дубли/битые — ДО кэша, иначе кэш вернёт оценку запрещённому клипу
        _dups = prefilter_candidates(entries, log=log)
    except Exception as _pfe:
        log(f"[VISION] предфильтр пропущен: {str(_pfe)[:100]}")
    # [CACHE] старый ключ (query) — совместимость с уже накопленными оценками
    import hashlib as _hl
    import sqlite3 as _sq
    _cache = None
    _ths: dict[int, str] = {}
    try:
        from .library import db_path as _dbp
        _cache = _sq.connect(str(_dbp()), timeout=15)
        _cache.execute("CREATE TABLE IF NOT EXISTS scores (file TEXT, th TEXT, score INTEGER, "
                       "what TEXT, at TEXT, PRIMARY KEY(file, th))")
        _cache.commit()
    except Exception:
        _cache = None
    if _cache is not None:
        for _e in entries:
            _f = str(_e.get("file") or "")
            _q = str(_e.get("query") or "")
            if not _f or not _q or _e.get("verified"):
                continue
            _th = _hl.sha1((_PROMPT_SALT + "|" + _q).encode("utf-8")).hexdigest()[:16]
            _ths[id(_e)] = _th
            try:
                _row = _cache.execute("SELECT score, what FROM scores WHERE file=? AND th=?",
                                      (_f, _th)).fetchone()
            except Exception:
                _row = None
            if _row is not None:
                _e["score"] = int(_row[0])
                _e["what"] = str(_row[1] or "")
                _e["verified"] = True
                _e["cached"] = True
    # [PERF-HISTORY] негативный кэш (checks>=3, avg<2.5, теги не пересекаются)
    try:
        from . import perf_history as _ph
        _ph.apply_history_skips(entries, log=log)
    except Exception as _phe:
        log(f"[VISION] история клипов недоступна: {str(_phe)[:80]}")

    def _store_all() -> None:
        if _cache is None:
            return
        from datetime import datetime as _dt, timezone as _tz
        for _e in entries:
            _f = str(_e.get("file") or "")
            _th = _ths.get(id(_e))
            _sc = _e.get("score")
            if _th and _f and isinstance(_sc, int) and _sc >= 0:
                try:
                    _cache.execute("INSERT OR REPLACE INTO scores (file, th, score, what, at) "
                                   "VALUES (?,?,?,?,?)",
                                   (_f, _th, _sc, str(_e.get("what") or ""),
                                    _dt.now(_tz.utc).isoformat()))
                    _cache.commit()
                except Exception:
                    pass

    def _summary() -> None:
        done = [e for e in entries if e.get("verified")]
        if not done:
            return
        ok = sum(1 for e in done
                 if not isinstance(e.get("score"), int)
                 or e.get("score", -1) < 0 or e.get("score", 0) >= min_score)
        deleted = (len(done) - ok) if delete_bad else 0
        log(f"[VISION] проверено: {len(done)} | оставлено: {ok} | удалено: {deleted}")

    def _delete_bad_all() -> None:
        if not delete_bad:
            return
        for e in entries:
            sc = e.get("score")
            if isinstance(sc, int) and 0 <= sc < min_score:
                try:
                    Path(str(e.get("file"))).unlink()
                except Exception:
                    pass

    def _opencode_leftovers() -> None:
        todo = [e for e in entries
                if e.get("verified") and isinstance(e.get("score"), int)
                and e.get("score", -1) < 0
                and str(e.get("file") or "") and Path(str(e["file"])).exists()]
        for i in range(0, len(todo), max(1, batch_size)):
            b = todo[i:i + max(1, batch_size)]
            if verify_batch_opencode(b, log=log):
                for e in b:
                    if not e.get("verified"):
                        continue
                    sc = e.get("score", -1)
                    mark = "✓" if (not isinstance(sc, int) or sc < 0 or sc >= min_score) else "✗"
                    log(f"[VISION] {mark} {Path(str(e['file'])).name[:44]} | {sc}/10 — "
                        f"{str(e.get('what'))[:60]}")
                    if isinstance(sc, int) and 0 <= sc < min_score and delete_bad:
                        try:
                            Path(str(e["file"])).unlink()
                        except Exception:
                            pass
            else:
                log("[VISION] opencode-VL недоступен — пропуск")

    # [VM-BATCH4] основной путь: один Gemini-вызов на 4-6 кандидатов
    if key:
        todo = [e for e in entries
                if not e.get("verified") and str(e.get("file") or "")
                and Path(str(e["file"])).exists()]
        if todo:
            try:
                verify_candidates_gemini(
                    todo, key=key, keys=[x for x in (keys or []) if x],
                    model=model, batch_size=max(2, min(6, int(batch_size or 5))),
                    min_score=min_score, delete_bad=delete_bad, log=log)
            except Exception as _be:
                log(f"[VISION] батч не удался: {str(_be)[:120]}")
        if use_opencode:
            _apply_twins(_dups)
            _opencode_leftovers()
        _apply_twins(_dups)
        _delete_bad_all()
        _summary()
        _store_all()
        try:
            from . import perf_history as _ph
            _ph.record_entries(entries)
        except Exception:
            pass
        return entries

    # без ключей Gemini — бесплатный резерв (по одному кандидату)
    if use_opencode:
        todo = [e for e in entries
                if not e.get("verified") and str(e.get("file") or "")
                and Path(str(e["file"])).exists()]
        for i in range(0, len(todo), max(1, batch_size)):
            verify_batch_opencode(todo[i:i + max(1, batch_size)], log=log)
        _apply_twins(_dups)
        _delete_bad_all()
        _summary()
        _store_all()
    try:
        from . import perf_history as _ph
        _ph.record_entries(entries)
    except Exception:
        pass
    return entries
