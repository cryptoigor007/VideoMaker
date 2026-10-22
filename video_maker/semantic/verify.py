"""Проверка контента скачанных клипов через Gemini Vision.

Запросы к Pixabay хорошие, но теги врут: «office workers» может оказаться
пустым офисом. Здесь мы извлекаем кадры из клипа и спрашиваем Gemini Vision:
«насколько этот клип подходит сцене?» — клипы с низкой оценкой отбрасываются.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

_FENCE_RE = re.compile(r"\{.*\}", re.DOTALL)
_ARRAY_FENCE_RE = re.compile(r"\[.*\]", re.DOTALL)
_ANY_FENCE_RE = re.compile(r"[\[{].*[\]}]", re.DOTALL)

# ======================================================================
# [VM-P0-3] VLM убран с критического пути:
#   1) дешёвый префильтр (blur/тьма/статичность/точные дубли) ДО Vision;
#   2) Vision видит только ТОП-3 кандидата сцены;
#   3) режим «вето»: «действие буквально?» да/нет/сомневаюсь;
#   4) кэш (scene_hash, query, model, prompt_version) — повтор = 0 вызовов;
#   5) фолбэк по одному — не более 2 одиночных доработок на сцену.
# ======================================================================
_PROMPT_VERSION = "verify-v4-veto-2026-10-22"
_VETO_TOP = 3
_FALLBACK_SINGLES = 2
_SCENE_STATE_TTL = 900.0


def _env_float(name: str, default: float) -> float:
    """Безопасный float из env: мусор/пусто/локальная запятая не валят импорт."""
    try:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return float(default)
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float(default)


# Пороги дешёвого префильтра (можно переопределить env, без правки кода).
# blur — дисперсия Лапласа на кадре 640px: <15 — реально нечитаемо (tiny-превью
# Pixabay сжаты, поэтому порог низкий: не режем мягкие/боке-кадры зря).
_QF_BLUR_MIN = max(0.0, _env_float("VM_VISION_BLUR_MIN", 15.0))
# средняя яркость 0..255: <22 — практически чёрный кадр.
_QF_DARK_MEAN = max(0.0, _env_float("VM_VISION_DARK_MEAN", 22.0))
# средняя |Δ| между двумя кадрами 0..255: <0.08 — стоп-кадр/фриз.
_QF_MOTION_MIN = max(0.0, _env_float("VM_VISION_MOTION_MIN", 0.08))
_QF_WIDTH = 640
_SKIP_WHAT = "VLM: вне топ-3 (не оценивался)"


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


def _cache_th_for(entry: dict, model: str = "") -> str:
    """[VM-P0-3] Ключ кэша: (scene_hash, query, model, prompt_version).

    scene_hash — нормализованный текст сцены; query — запрос, которым клип
    найден (у одной сцены их несколько). Любая смена промпта/модели/запроса
    даёт новый ключ, поэтому старые мягкие оценки не переиспользуются.
    """
    scene_key = _scene_key_of(entry)
    scene_hash = hashlib.sha1(scene_key.encode("utf-8")).hexdigest()[:16]
    query = " ".join(str(entry.get("query") or "").lower().split())[:300]
    raw = f"{_PROMPT_VERSION}|{scene_hash}|{query}|{model}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


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


# ======================================================================
# [VM-P0-3] Дешёвая оценка кадров без Vision: резкость (дисперсия Лапласа),
# яркость, motion energy по двум кадрам. Один раз на файл, результат и сами
# кадры кэшируются — топ-3 потом переиспользует эти же кадры для Vision.
# ======================================================================
_QUALITY_CACHE: dict[tuple[str, int, int], dict] = {}
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _file_sig(path: str | Path) -> tuple[str, int, int] | None:
    try:
        p = Path(str(path))
        st = p.stat()
        # без realpath: на SMB он может висеть десятки секунд
        return (os.path.abspath(str(p)), int(st.st_size), int(st.st_mtime_ns))
    except Exception:
        return None


def _content_hash(path: str | Path) -> str:
    """sha1 содержимого + размер (для точных дублей).

    Файлы ≤16 МБ хэшируются целиком; крупные 4K — тремя кусками по 128 КБ
    (начало/середина/конец): этого достаточно, чтобы отличить разные клипы,
    а чтение по SMB остаётся дешёвым.
    """
    sig = _file_sig(path)
    if sig is None:
        return ""
    if sig in _HASH_CACHE:
        return _HASH_CACHE[sig]
    try:
        size = sig[1]
        h = hashlib.sha1(str(size).encode("ascii"))
        with open(sig[0], "rb") as fh:
            if size <= 16 * 1024 * 1024:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            else:
                step = 128 * 1024
                for pos in (0, max(0, size // 2 - step // 2), max(0, size - step)):
                    try:
                        fh.seek(pos)
                        h.update(fh.read(step))
                    except Exception:
                        continue
        res = h.hexdigest()
    except Exception:
        res = ""
    _HASH_CACHE[sig] = res
    return res


def _quality_tmp_dir(clip_path: str | Path, tmp_dir: Path | None = None) -> Path:
    if tmp_dir is not None:
        return Path(tmp_dir)
    return Path(str(clip_path)).parent / ".verify_frames"


def _grab_quality_frame(clip_path: str, t: float, dst: Path, timeout: int = 12) -> str:
    try:
        subprocess.run(
            [_ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}",
             "-i", str(clip_path), "-frames:v", "1",
             "-vf", f"scale={_QF_WIDTH}:-2", "-q:v", "3", str(dst)],
            capture_output=True, timeout=timeout,
        )
        if dst.exists() and dst.stat().st_size > 1000:
            return str(dst)
    except Exception:
        pass
    return ""


def _quality_stats(clip_path: str | Path, duration: float = 0.0, *,
                   tmp_dir: Path | None = None) -> dict | None:
    """{blur, bright, motion, frames} по двум кадрам; None — проверить нельзя.

    Любая ошибка/таймаут = None (кандидат НЕ отсеивается: решает Vision).
    """
    sig = _file_sig(clip_path)
    if sig is None:
        return None
    cached = _QUALITY_CACHE.get(sig)
    if cached is not None:
        frames = [f for f in (cached.get("frames") or []) if Path(f).exists()]
        return {**cached, "frames": frames}
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None
    dur = float(duration or 0)
    if dur <= 0.5:
        try:
            _ok, _dur = _probe_video(str(clip_path))
            dur = _dur if _dur > 0.5 else 0.0
        except Exception:
            dur = 0.0
    if dur <= 0.5:
        return None
    # Точки совпадают с extract_frames(count=2): для коротких клипов — один
    # кадр в середине, для длинных — 25%/60%. Тогда Vision топ-3 видит те же
    # кадры, что и раньше (сравнимость вердиктов), а motion считается по двум.
    if dur <= 6.0:
        t0 = max(0.05, dur * 0.50)
        t1 = max(t0 + 0.15, dur * 0.80)
    else:
        t0 = max(0.05, dur * 0.25)
        t1 = max(t0 + 0.15, dur * 0.60)
    if t1 >= dur - 0.05:
        t1 = max(0.10, dur - 0.10)
    out_dir = _quality_tmp_dir(clip_path, tmp_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    stem = re.sub(r"[^\w.-]", "_", Path(str(clip_path)).stem)[:40] or "clip"
    tag = hashlib.sha1(sig[0].encode("utf-8")).hexdigest()[:8]
    arrs: list = []
    frames: list[str] = []
    for i, t in enumerate((t0, t1)):
        dst = out_dir / f"q_{stem}_{tag}_{os.getpid()}_f{i}.jpg"
        f = _grab_quality_frame(str(clip_path), t, dst)
        if not f:
            if i == 0:
                break  # первый кадр не взялся — второй по SMB ждать бессмысленно
            continue
        try:
            a = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        except Exception:
            continue
        if a.ndim != 2 or min(a.shape) < 8:
            continue
        arrs.append(a)
        frames.append(f)
    if not arrs:
        return None
    lap_vars: list[float] = []
    for a in arrs:
        # дискретный лапласиан (эквивалент cv2.Laplacian во внутренней области)
        lap = (-4.0 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1]
               + a[1:-1, :-2] + a[1:-1, 2:])
        lap_vars.append(float(lap.var()))
    # Оцениваем по ЛУЧШЕМУ из двух кадров: один тёмный/смазанный кадр
    # (фейд, переход) не должен отсеивать нормальный клип.
    bright = float(max(float(a.mean()) for a in arrs))
    motion: float | None = None
    if len(arrs) >= 2 and arrs[0].shape == arrs[1].shape:
        motion = float(np.abs(arrs[1] - arrs[0]).mean())
    res = {
        "blur": float(max(lap_vars)),
        "bright": bright,
        "motion": motion,
        "frames": frames,
    }
    _QUALITY_CACHE[sig] = res
    return res


def _blur_intended(entry: dict) -> bool:
    """Сцена сама просит размытие/боке — blur-фильтр не применяем."""
    hay = " ".join(str(entry.get(k) or "") for k in
                   ("scene_key", "visual", "scene", "query", "description", "tags"))
    return bool(re.search(r"blur|bokeh|размыт|defocus|out of focus|расфокус", hay, re.I))


def _scene_idx_of(entry: dict) -> int | None:
    """Индекс сцены в пуле: ``scene_idx`` (smart_broll) или ``scene`` манифеста."""
    for k in ("scene_idx", "scene"):
        v = entry.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def _scene_text_of(entry: dict) -> str:
    """Текст сцены без учёта индекса (scene-индекс текстом не считается)."""
    for k in ("scene_key", "visual"):
        v = str(entry.get(k) or "").strip()
        if v and not v.isdigit():
            return " ".join(v.lower().split())[:300]
    sc = entry.get("scene")
    if isinstance(sc, str) and sc.strip() and not sc.strip().isdigit():
        return " ".join(sc.lower().split())[:300]
    return ""


def _group_key(entry: dict) -> str:
    """Ключ сцены для группировки/бюджета VLM.

    [VM-FIX B3] Индекс сцены входит в ключ: две РАЗНЫЕ сцены с ОДИНАКОВЫМ
    текстом (частый повтор в сценарии) не должны попадать в одну группу —
    иначе вторая сцена получает ``batch_done=True`` и все её кандидаты
    уходят в ``vlm_skipped`` (-1) без единого вызова.
    """
    text = _scene_text_of(entry)
    idx = _scene_idx_of(entry)
    if idx is not None:
        return f"idx:{idx}|{text}" if text else f"idx:{idx}"
    if text:
        return text
    return " ".join(str(entry.get("query") or "").lower().split())[:300]


def _scene_identity(grp: list[dict]) -> str:
    """Идентичность сцены для ключа ``_SCENE_STATE``.

    Индекс (production-путь smart_broll передаёт ``scene_idx``) либо хэш
    набора файлов сцены — чтобы две сцены без индекса не делили состояние.
    """
    for e in grp or []:
        idx = _scene_idx_of(e)
        if idx is not None:
            return f"i{idx}"
    files = sorted({str(e.get("file") or "") for e in (grp or []) if e.get("file")})
    if not files:
        return "i?"
    return "h" + hashlib.sha1("|".join(files).encode("utf-8")).hexdigest()[:12]


def _scene_label(entry: dict) -> str:
    for k in ("visual", "scene_key", "scene", "query"):
        v = str(entry.get(k) or "").strip()
        if v and not v.isdigit():
            return v[:60]
    return "без сцены"


def prefilter_candidates(entries: list[dict], *, log=print,
                         dedup_cos: float = 0.92, min_duration: float = 1.5,
                         use_embeddings: bool = True,
                         tmp_dir: Path | None = None) -> list[tuple[dict, dict]]:
    """Отсеять до Vision: дубли/близнецы, hard-теги, битые/короткие,
    размытые/тёмные/стоп-кадры (без единого Vision-вызова).

    Возвращает пары (дубль, оригинал) — оценки дубля копируются после Vision.
    Отсеянные помечаются verified=True, чтобы не попасть в очередь вызовов.
    """
    dups: list[tuple[dict, dict]] = []
    n_dup = n_hard = n_bad = n_short = n_blur = n_dark = n_static = 0
    need_emb = use_embeddings and any(
        e.get("library_file") or "/broll_library/" in str(e.get("file") or "")
        for e in entries)
    emb = _embeddings_cached() if need_emb else {}
    seen_path: dict[str, dict] = {}
    seen_src: dict[str, dict] = {}
    seen_hash: dict[str, dict] = {}
    kept_vecs: list[tuple[dict, list[float]]] = []
    # Хэшируем только файлы, у которых есть «напарник» по размеру: у точных
    # дублей размер совпадает всегда, а уникальные 4K по SMB не читаем зря.
    size_counts: dict[int, int] = {}
    for e in entries:
        try:
            sz = Path(str(e.get("verify_file") or e.get("file") or "")).stat().st_size
        except Exception:
            continue
        size_counts[sz] = size_counts.get(sz, 0) + 1

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
        # [VM-P0-3] точные дубли: одинаковый размер + sha1 содержимого
        h = ""
        if keeper is None:
            try:
                sz = Path(str(e.get("verify_file") or f)).stat().st_size
            except Exception:
                sz = -1
            if sz > 0 and size_counts.get(sz, 0) > 1:
                h = _content_hash(str(e.get("verify_file") or f))
        if keeper is None and h:
            keeper = seen_hash.get(h)
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
        if h:
            seen_hash[h] = e
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
            n_short += 1

    # [VM-P0-3] резкость/яркость/motion — два дешёвых кадра, без Vision.
    # Ошибка/таймаут анализа НЕ отсеивает кандидата (решит Vision).
    quality_todo = [e for e in entries
                    if not e.get("verified") and str(e.get("file") or "")]
    if quality_todo:
        def _one(e: dict):
            vf = str(e.get("verify_file") or e.get("file") or "")
            try:
                dur0 = float(e.get("duration") or 0)
            except Exception:
                dur0 = 0.0
            return e, _quality_stats(vf, dur0, tmp_dir=tmp_dir)

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(quality_todo))) as _ex:
                results = list(_ex.map(_one, quality_todo))
        except Exception:
            results = []
            for e in quality_todo:
                try:
                    results.append(_one(e))
                except Exception:
                    continue

        for e, st in results:
            if e.get("verified") or not st:
                continue
            frames = st.get("frames") or []
            if frames:  # переиспользуем для Vision топ-3 (экономия ffmpeg)
                e["_vision_frames"] = frames
            bright = st.get("bright")
            blur = st.get("blur")
            motion = st.get("motion")
            if bright is not None and bright < _QF_DARK_MEAN:
                e["verified"] = True
                e["score"] = 0
                e["what"] = f"предфильтр: тёмный кадр (яркость {bright:.0f}/255)"
                e["prefilter"] = "dark"
                n_dark += 1
                continue
            if motion is not None and motion < _QF_MOTION_MIN:
                e["verified"] = True
                e["score"] = 0
                e["what"] = f"предфильтр: стоп-кадр (motion {motion:.2f})"
                e["prefilter"] = "static"
                n_static += 1
                continue
            if (blur is not None and blur < _QF_BLUR_MIN
                    and not _blur_intended(e)):
                e["verified"] = True
                e["score"] = 0
                e["what"] = f"предфильтр: размыто (резкость {blur:.0f} < {_QF_BLUR_MIN:.0f})"
                e["prefilter"] = "blur"
                n_blur += 1

    total = n_dup + n_hard + n_bad + n_short + n_blur + n_dark + n_static
    if log and total:
        log(f"[VISION] префильтр: отсеяно {total} (blur {n_blur}, тёмные {n_dark}, "
            f"дубли {n_dup}, короткие {n_short}) | motion {n_static}, hard {n_hard}, "
            f"битые {n_bad}")
    return dups


def _apply_twins(dups: list[tuple[dict, dict]]) -> None:
    """Скопировать оценку оригинала дублю (кадры идентичны)."""
    for e, keeper in dups:
        sc = keeper.get("score")
        if isinstance(sc, int) and keeper.get("verified"):
            e["score"] = sc
            e["what"] = str(keeper.get("what") or "")[:200]
            e["verified"] = True
            if keeper.get("vlm_skipped"):
                e["vlm_skipped"] = True
                e["prefilter"] = "vlm_skip"


# ======================================================================
# [VM-P0-3] Режим «вето»: главный вопрос — «действие показано БУКВАЛЬНО?»
#   да        → берём (score поднимаем до min_score, чтобы кандидат прошёл)
#   сомневаюсь → обычный score-путь
#   нет       → не берём, смотрим следующего
# Привязка кадров — ТОЛЬКО чередованием: текст кандидата → ЕГО кадры →
# закрывающий разделитель (плоский список кадров сдвигал привязку).
# ======================================================================
_VETO_RULES = (
    "Главный вопрос по каждому кандидату: «Показано ли БУКВАЛЬНО действие из сцены?»\n"
    "literal_action:\n"
    "  «да» — действие буквально видно на кадрах;\n"
    "  «нет» — показано другое действие (даже если красиво/похоже по атмосфере);\n"
    "  «сомневаюсь» — неоднозначно, частично, кадры нечитаемы.\n"
    "Правила score (0-10): 6-10 — только при буквальном действии или точной "
    "атмосфере без противоречий; 3-5 — частично; 0-2 — не то. "
    "Если literal_action != «да» — score НЕ выше 4.\n"
    "Кадры разных кандидатов НЕ путать: кандидат N описывается только по кадрам, "
    "которые идут сразу после строки «Кандидат N» и до строки «— конец кандидата N —».\n"
    "Ответь ТОЛЬКО валидным JSON-МАССИВОМ без пояснений: "
    '[{"n":1,"literal_action":"да|нет|сомневаюсь","score":N,'
    '"what":"кратко что на кадрах"}, ...] — строго по одному элементу на кандидата.'
)

_VERDICT_YES = {"да", "yes", "y", "true", "1", "буквально"}
_VERDICT_NO = {"нет", "no", "n", "false", "0"}


def _veto_parts(items: list[dict], frames_by_item: list[list[str]]) -> list[dict]:
    """parts с сохранённой привязкой: текст кандидата → его кадры → конец."""
    head = (
        "Ты — монтажёр и проверяешь стоковые клипы для конкретных сцен.\n"
        "Ниже КАЖДЫЙ кандидат идёт со своими кадрами: строка «Кандидат N. Сцена: ...», "
        "сразу после неё — кадры ЭТОГО кандидата, затем «— конец кандидата N —».\n"
        + _VETO_RULES
    )
    parts: list[dict] = [{"text": head}]
    for i, (it, frames) in enumerate(zip(items, frames_by_item), 1):
        scene = str(it.get("visual") or it.get("scene_key") or it.get("scene")
                    or it.get("query") or "")
        parts.append({"text": f"Кандидат {i}. Сцена: «{scene}». Кадры кандидата {i}:"})
        for f in frames:
            try:
                data = base64.b64encode(Path(f).read_bytes()).decode("ascii")
            except Exception:
                continue
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
        parts.append({"text": f"— конец кандидата {i} —"})
    return parts


def _verdict_of(c: dict | None) -> str:
    """Нормализовать literal_action → да/нет/сомневаюсь."""
    if not isinstance(c, dict):
        return "сомневаюсь"
    v = str(c.get("literal_action") or c.get("literal") or c.get("action") or "").strip().lower()
    v = re.sub(r"[^0-9a-zа-яё ]+", " ", v)
    v = " ".join(v.split())
    if not v:
        return "сомневаюсь"
    if v in _VERDICT_YES or v.startswith("да ") or v == "да":
        return "да"
    if "не уверен" in v or "сомнева" in v or "частич" in v or "неоднознач" in v:
        return "сомневаюсь"
    if v in _VERDICT_NO or v.startswith("нет") or v.startswith("не показан"):
        return "нет"
    return "сомневаюсь"


def _score_of(c: dict | None) -> int:
    try:
        return max(0, min(10, int(round(float((c or {}).get("score", -1))))))
    except Exception:
        return -1


def _apply_veto(e: dict, c: dict | None, min_score: int) -> str:
    """Записать вердикт в entry. Возвращает да/нет/сомневаюсь."""
    verdict = _verdict_of(c)
    sc = _score_of(c)
    if verdict == "да":
        sc = max(sc, int(min_score))
        e["veto"] = "pass"
    elif verdict == "нет":
        sc = min(sc if sc >= 0 else 0, 4)
        e["veto"] = "reject"
    else:
        e["veto"] = "score"
    e["literal_action"] = verdict
    e["score"] = max(0, min(10, sc))
    e["what"] = str((c or {}).get("what") or "")[:200]
    e["verified"] = True
    return verdict


def _rank_todo(items: list[dict]) -> list[dict]:
    """Текущее ранжирование = порядок вызывающего (smart_broll уже сделал pre-rank).

    Здесь НЕ пересортировываем: иначе топ-3 переставал бы совпадать с тем
    ранжированием, по которому вызывающий строил пул (база+сток, RRF, история).
    """
    return list(items)


_SCENE_STATE: dict[str, dict] = {}
_SCENE_RUN_TOKEN = 0


def reset_scene_state() -> None:
    """[VM-FIX B1] Сбросить бюджет VLM на сцену для нового прогона/пула.

    `_SCENE_STATE` — процессный кэш «батч уже был»; без сброса второй эпизод
    с тем же текстом сцены получал batch_done=True и не делал ни одного вызова.
    """
    global _SCENE_RUN_TOKEN
    _SCENE_STATE.clear()
    _SCENE_RUN_TOKEN += 1


def _scene_state(key: str) -> dict:
    """Бюджет VLM на сцену: 1 батч топ-3 + ≤2 одиночных доработки."""
    now = time.time()
    st = _SCENE_STATE.get(key)
    if not st or (now - float(st.get("updated") or 0)) > _SCENE_STATE_TTL:
        st = {"batch_done": False, "singles": 0, "approved": False, "best": -1,
              "n_total": 0, "m_total": 0, "vlm_total": 0, "calls_total": 0,
              "time_total": 0.0, "updated": now}
        _SCENE_STATE[key] = st
    st["updated"] = now
    return st


def verify_candidates_gemini(entries: list[dict], *, key: str, keys: list[str] | None = None,
                             model: str = "gemini-3.1-flash-lite", batch_size: int = 5,
                             min_score: int = 5, delete_bad: bool = False, log=print,
                             use_cache: bool = True, frames_count: int = 2,
                             tmp_dir: Path | None = None, deadline: float | None = None,
                             prefilter: bool = True,
                             stats: dict | None = None) -> list[dict]:
    """[VM-P0-3] Vision только по ТОП-3 кандидатам сцены, режим «вето».

    Префильтр (дубли/hard/короткие/blur/тёмные/стоп-кадры) — без Vision.
    Топ-3 сцены оцениваются ОДНИМ вызовом (чередование кадров сохранено),
    остальные не оцениваются. Если ни один не прошёл вето и score < min_score —
    до 2 одиночных доработок на сцену. Кэш: (scene_hash, query, model,
    prompt_version); повторный прогон = 0 вызовов.

    stats (необязательно) — накопитель реальных HTTP-вызовов: ``stats["calls"]``
    увеличивается на каждый запрос (включая retry/ротацию ключей), кэш — нет.
    """
    import sqlite3 as _sq
    import time as _t
    if log is None:
        log = lambda *a, **k: None  # noqa: E731 — log=None допустим в тестах/CLI
    _keys: list[str] = []
    for _k in [key] + list(keys or []):
        if _k and _k not in _keys:
            _keys.append(_k)

    def _count_calls(n: int) -> None:
        if stats is not None and n:
            stats["calls"] = int(stats.get("calls") or 0) + int(n)

    try:
        dups = prefilter_candidates(entries, log=log, tmp_dir=tmp_dir) if prefilter else []
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
        th = _cache_th_for(e, model)
        th_of[id(e)] = th
        if cache is not None:
            try:
                row = cache.execute("SELECT score, what FROM scores WHERE file=? AND th=?",
                                    (f, th)).fetchone()
            except Exception:
                row = None
            if row is not None:
                sc = int(row[0])
                what = str(row[1] or "")
                # [VM-FIX B2] Старый кэш «пропуска» (-1, VLM:) — не оценка:
                # пул мог измениться, кандидат обязан участвовать в топ-3 заново.
                if sc < 0 and what.startswith("VLM:"):
                    row = None
            if row is not None:
                sc = int(row[0])
                e["score"] = sc
                e["what"] = str(row[1] or "")
                e["verified"] = True
                e["cached"] = True
                n_cached += 1
                continue
        todo.append(e)
    if n_cached and log:
        log(f"[VISION] из кэша: {n_cached}")
    # [PERF-HISTORY] негативный кэш: клип с checks>=3 и avg<2.5 (и без
    # пересечения тегов со сценой) повторно не проверяем — экономим вызовы.
    try:
        from . import perf_history
        perf_history.apply_history_skips(entries, log=log)
        todo = [e for e in todo if not e.get("verified")]
    except Exception as _phe:
        if log:
            log(f"[VISION] история клипов недоступна: {str(_phe)[:80]}")

    frames_of: dict[int, list[str]] = {}

    def _frames(e: dict) -> list[str]:
        if id(e) not in frames_of:
            ready = [f for f in (e.get("_vision_frames") or []) if Path(f).exists()]
            try:
                dur0 = float(e.get("duration") or 0)
            except Exception:
                dur0 = 0.0
            can_reuse = bool(ready) and int(frames_count or 0) <= 2
            if can_reuse and 0 < dur0 <= 6.0:
                ready = ready[:1]  # как extract_frames: короткий клип = 1 кадр
            elif can_reuse and len(ready) < 2 and dur0 > 6.0:
                can_reuse = False
            if can_reuse:  # [VM-P0-3] те же кадры, что извлёк дешёвый префильтр
                frames_of[id(e)] = ready
            else:
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
        # [VM-FIX B2] «Пропуск вне топ-3» — эфемерный маркер прогона, НЕ оценка.
        # В кэш пишем только реальные оценки >= 0: иначе кандидат навсегда
        # остаётся -1 и не оценивается, даже если в новом пуле стал топ-3.
        if (cache is not None and f and th and isinstance(sc, int) and sc >= 0):
            try:
                from datetime import datetime as _dt, timezone as _tz
                cache.execute("INSERT OR REPLACE INTO scores (file, th, score, what, at) "
                              "VALUES (?,?,?,?,?)",
                              (f, th, sc, str(e.get("what") or ""),
                               _dt.now(_tz.utc).isoformat()))
                cache.commit()
            except Exception:
                pass

    def _mark_skip(e: dict) -> None:
        if e.get("verified") or e.get("veto") or e.get("cached"):
            return
        e["score"] = -1
        e["what"] = _SKIP_WHAT
        e["verified"] = True
        e["vlm_skipped"] = True
        e["prefilter"] = "vlm_skip"  # не «проверен VLM»: не раздуваем статистику
        # _store намеренно не вызываем: пропуск не должен переживать прогон.

    def _mark_fail(e: dict, what: str) -> None:
        e["score"] = -1
        e["what"] = what
        e["verified"] = True
        _store(e)

    def _single(e: dict) -> tuple[str | None, int]:
        """Один кандидат — один Vision-вызов (фолбэк, ≤2 на сцену)."""
        frames = _frames(e)
        if not frames:
            _mark_fail(e, "кадры не извлеклись")
            return None, 0
        counter = [0]
        d = _gemini_json(_veto_parts([e], [frames]), _keys, model,
                         expect="array", log=log, counter=counter)
        arr = _parse_candidates(d, 1)
        if not arr:
            _mark_fail(e, "вето: нет ответа vision")
            return None, counter[0]
        v = _apply_veto(e, arr[0], min_score)
        _store(e)
        return v, counter[0]

    def _batch(items: list[dict]) -> tuple[list[dict] | None, list[dict], int]:
        """Топ-3 одним вызовом. Возвращает (ответы по items, реально оценённые, вызовы)."""
        fr = _frames_many(items)
        use: list[dict] = []
        use_fr: list[list[str]] = []
        for e, f in zip(items, fr):
            if f:
                use.append(e)
                use_fr.append(f)
            else:
                _mark_fail(e, "кадры не извлеклись")
        if not use:
            return None, [], 0
        counter = [0]
        parsed = None
        for _attempt in (1, 2):  # один повтор: JSON мог обрезаться
            d = _gemini_json(_veto_parts(use, use_fr), _keys, model,
                             expect="array", log=log, counter=counter)
            parsed = _parse_candidates(d, len(use))
            if parsed is not None:
                break
        return parsed, use, counter[0]

    def _log_candidate(e: dict) -> None:
        if log is None:
            return
        sc = e.get("score", -1)
        v = e.get("veto")
        if e.get("veto") == "pass":
            mark = "✓"
        elif isinstance(sc, int) and sc >= min_score:
            mark = "✓"
        elif e.get("vlm_skipped"):
            return
        else:
            mark = "·"
        tail = f"вето: {e.get('literal_action') or '—'}" if v else "score"
        log(f"[VISION] {mark} {Path(str(e.get('file'))).name[:40]} | {sc}/10 — "
            f"{tail} | {str(e.get('what'))[:60]}")

    # Группируем по сцене: бюджет топ-3 и фолбэка — на сцену, не на вызов.
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for e in entries:
        g = _group_key(e)
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(e)

    def _note(st: dict, e: dict) -> None:
        sc = e.get("score")
        if isinstance(sc, int):
            st["best"] = max(int(st.get("best", -1)), sc)
        if e.get("veto") == "pass":
            st["approved"] = True

    # Фаза A: план на сцену (топ-3) и общий список на батч. Батчить топы
    # РАЗНЫХ сцен вместе важно: у финального verify_pool в сцене 1-2 клипа,
    # и вызов на каждую сцену раздул бы число запросов.
    plans: list[dict] = []
    pending: list[dict] = []
    t_global = _t.time()
    for gkey in order:
        grp = groups[gkey]
        t0 = _t.time()
        label = _scene_label(grp[0])
        n_pref = sum(1 for e in grp
                     if e.get("prefilter") and e.get("prefilter") != "vlm_skip")
        n_nf = sum(1 for e in grp if str(e.get("what") or "") == "нет файла")
        m_after = len(grp) - n_pref - n_nf
        cand_todo = [e for e in grp if not e.get("verified")]
        if not cand_todo:
            if log:
                log(f"[VISION] сцена «{label}»: кандидатов {len(grp)} → префильтр {m_after} → "
                    f"VLM 0 (вето, кэш) | вызовов 0 | время {_t.time() - t0:.1f}с")
            continue
        if deadline and _t.time() > deadline:
            for e in cand_todo:
                _mark_fail(e, "дедлайн")
            continue
        # label в ключе: у манифеста scene=индекс, разные серии могут иметь
        # одинаковый индекс — запрос сцены их различает. run-token отделяет
        # прогоны: второй эпизод с тем же текстом не наследует batch_done.
        # [VM-FIX B3] idx/хэш файлов сцены разделяет ДВЕ сцены с одинаковым
        # текстом в одном прогоне: у каждой свой бюджет и свои VLM-вызовы.
        state = _scene_state(
            f"{_SCENE_RUN_TOKEN}|{gkey}|{_scene_identity(grp)}|{_scene_label(grp[0])}"
            f"|{model}|{_PROMPT_VERSION}")
        state["n_total"] += len(grp)
        state["m_total"] += max(0, m_after)
        ranked = _rank_todo(cand_todo)
        top = ranked[:_VETO_TOP]
        rest = ranked[_VETO_TOP:]
        plan = {"state": state, "label": label, "ranked": ranked, "top": top,
                "rest": rest, "t0": t0, "calls": 0, "vlm": 0,
                "first": not state.get("batch_done")}
        plans.append(plan)
        if plan["first"]:
            pending.extend(top)

    total_calls = 0
    state_of = {id(e): p["state"] for p in plans for e in p["top"]}
    bs = max(2, min(6, int(batch_size or 5)))
    for i in range(0, len(pending), bs):
        chunk = pending[i:i + bs]
        parsed, use, calls = _batch(chunk)
        total_calls += calls
        _count_calls(calls)
        chunk_ids = {id(e) for e in chunk}
        for p in plans:  # вызов обслужил и эту сцену (с ротацией ключей = calls)
            if any(id(e) in chunk_ids for e in p["top"]):
                p["calls"] += calls
        if parsed is not None:
            for e, c in zip(use, parsed):
                _apply_veto(e, c, min_score)
                _store(e)
                _note(state_of.get(id(e), {}), e)
                _log_candidate(e)
    for p in plans:
        p["vlm"] = sum(1 for e in p["top"] if e.get("veto"))

    # Фаза B: фолбэк по одному (≤2 на сцену) и пометка «вне топ-3».
    for p in plans:
        st = p["state"]
        ranked = p["ranked"]
        if p["first"]:
            st["batch_done"] = True
            # кандидаты батча без вердикта (битый JSON/нет кадров)
            for e in p["top"]:
                if e.get("veto"):
                    continue
                if int(st.get("singles", 0)) >= _FALLBACK_SINGLES:
                    break
                v, c = _single(e)
                p["calls"] += c
                total_calls += c
                _count_calls(c)
                st["singles"] = int(st.get("singles", 0)) + 1
                if v is not None:
                    p["vlm"] += 1
                    _note(st, e)
                    _log_candidate(e)
            # «вето» не пройдено и score ниже порога — добираем из остатка
            if not st.get("approved") and int(st.get("best", -1)) < min_score:
                for e in p["rest"]:
                    if int(st.get("singles", 0)) >= _FALLBACK_SINGLES:
                        break
                    v, c = _single(e)
                    p["calls"] += c
                    total_calls += c
                    _count_calls(c)
                    st["singles"] = int(st.get("singles", 0)) + 1
                    if v is not None:
                        p["vlm"] += 1
                        _note(st, e)
                        _log_candidate(e)
            for e in p["rest"]:
                _mark_skip(e)
        else:
            # следующий чанк той же сцены: только если не прошли вето/порог
            if not st.get("approved") and int(st.get("best", -1)) < min_score:
                for e in ranked:
                    if int(st.get("singles", 0)) >= _FALLBACK_SINGLES:
                        break
                    v, c = _single(e)
                    p["calls"] += c
                    total_calls += c
                    _count_calls(c)
                    st["singles"] = int(st.get("singles", 0)) + 1
                    if v is not None:
                        p["vlm"] += 1
                        _note(st, e)
                        _log_candidate(e)
            for e in ranked:
                _mark_skip(e)

        dt = _t.time() - p["t0"]
        st["vlm_total"] = int(st.get("vlm_total", 0)) + int(p["vlm"])
        st["calls_total"] = int(st.get("calls_total", 0)) + int(p["calls"])
        st["time_total"] = float(st.get("time_total", 0.0)) + dt
        if log:
            log(f"[VISION] сцена «{p['label']}»: кандидатов {st['n_total']} → "
                f"префильтр {st['m_total']} → VLM {st['vlm_total']} (вето) | "
                f"вызовов {st['calls_total']} | время {st['time_total']:.1f}с")
    if log and plans:
        log(f"[VISION] VLM-итог: сцен {len(plans)} | вызовов {total_calls} | "
            f"время {_t.time() - t_global:.1f}с")

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
                        tmp_dir: Path | None = None, deadline: float | None = None,
                        stats: dict | None = None) -> list[dict]:
    """Совместимая обёртка: батч 4-6 кандидатов за один Vision-вызов."""
    return verify_candidates_gemini(
        entries, key=key, keys=keys, model=model, batch_size=batch_size,
        min_score=min_score, delete_bad=delete_bad, log=log, use_cache=use_cache,
        frames_count=frames_count, tmp_dir=tmp_dir, deadline=deadline, stats=stats)

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
                          model: str = "opencode/nemotron-3.5-super-vl-free",
                          stats: dict | None = None) -> bool:
    """Проверка клипов бесплатной VL-моделью OpenCode — ПО ОДНОМУ кандидату.

    [VM-VLM3] CLI принимает кадры только плоским списком `-f`, без вставки
    текста между изображениями. Пакетный «Кадры N-M» на практике сдвигал
    привязку (модель описывала клип i кадрами клипа i-1), поэтому здесь
    один вызов = один кандидат: привязка кадров не может сместиться.
    Обновляет score/what/verified. True, если хоть один кандидат оценён.
    """
    if log is None:
        log = lambda *a, **k: None  # noqa: E731
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
            if stats is not None:
                stats["calls"] = int(stats.get("calls") or 0) + 1
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
                keys: list[str] | None = None,
                stats: dict | None = None) -> list[dict]:
    """Проверить скачанные клипы; слабые (score < min_score) удалить.

    [VM-P0-3] Дешёвый префильтр (дубли/hard/битые/короткие/blur/тьма/стоп-кадр)
    отсеивает кандидатов без Vision. Vision видит только топ-3 сцены в режиме
    «вето» (один вызов, чередование «Кандидат N → его кадры» сохранено), плюс
    ≤2 одиночных доработки. Кэш: (scene_hash, query, model, prompt_version) —
    повторный прогон не делает вызовов. OpenCode-VL — резерв ≤2 на сцену.

    stats — реальные вызовы Vision (``stats["calls"]``) для baseline-метрик.
    """
    # [VM-FIX B1] Новый пул/прогон — новый бюджет сцен. Иначе второй вызов
    # получал batch_done=True от предыдущего и не оценивал ни одного клипа.
    if log is None:
        log = lambda *a, **k: None  # noqa: E731
    reset_scene_state()
    _dups: list[tuple[dict, dict]] = []
    try:  # hard/дубли/битые — ДО кэша, иначе кэш вернёт оценку запрещённому клипу
        _dups = prefilter_candidates(entries, log=log)
    except Exception as _pfe:
        log(f"[VISION] предфильтр пропущен: {str(_pfe)[:100]}")
    # [CACHE] [VM-P0-3] ключ: (scene_hash, query, model, prompt_version)
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
            if not _f or _e.get("verified"):
                continue
            _th = _cache_th_for(_e, model)
            _ths[id(_e)] = _th
            try:
                _row = _cache.execute("SELECT score, what FROM scores WHERE file=? AND th=?",
                                      (_f, _th)).fetchone()
            except Exception:
                _row = None
            if _row is not None:
                _sc = int(_row[0])
                _what = str(_row[1] or "")
                # [VM-FIX B2] старый кэш пропуска (-1, VLM:) — не оценка.
                if _sc < 0 and _what.startswith("VLM:"):
                    _row = None
            if _row is not None:
                _sc = int(_row[0])
                _e["score"] = _sc
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
            if (_th and _f and isinstance(_sc, int) and _sc >= 0):
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
        # [VM-P0-3] «вне топ-3» не дооцениваем; одиночный резерв — ≤2 на сцену
        budget: dict[str, int] = {}
        todo: list[dict] = []
        for e in entries:
            if e.get("vlm_skipped"):
                continue
            if not (e.get("verified") and isinstance(e.get("score"), int)
                    and e.get("score", -1) < 0
                    and str(e.get("file") or "") and Path(str(e["file"])).exists()):
                continue
            k = _group_key(e)
            if budget.get(k, 0) >= _FALLBACK_SINGLES:
                continue
            budget[k] = budget.get(k, 0) + 1
            todo.append(e)
        for i in range(0, len(todo), max(1, batch_size)):
            b = todo[i:i + max(1, batch_size)]
            if verify_batch_opencode(b, log=log, stats=stats):
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

    # [VM-P0-3] метрика для сцен, целиком закрытых кэшем (VLM не вызывался)
    if log:
        seen_g: set[str] = set()
        for _e in entries:
            _g = _group_key(_e)
            if _g in seen_g:
                continue
            seen_g.add(_g)
            _grp = [x for x in entries if _group_key(x) == _g]
            if any(not x.get("verified") for x in _grp):
                continue
            if not any(x.get("cached") for x in _grp):
                continue
            _pref = sum(1 for x in _grp
                        if x.get("prefilter") and x.get("prefilter") != "vlm_skip")
            _nf = sum(1 for x in _grp if str(x.get("what") or "") == "нет файла")
            log(f"[VISION] сцена «{_scene_label(_e)}»: кандидатов {len(_grp)} → "
                f"префильтр {len(_grp) - _pref - _nf} → VLM 0 (вето, кэш) | "
                f"вызовов 0 | время 0.0с")

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
                    min_score=min_score, delete_bad=delete_bad, log=log,
                    prefilter=False, stats=stats)  # префильтр уже выполнен выше
            except Exception as _be:
                log(f"[VISION] батч не удался: {str(_be)[:120]}")
        if use_opencode:
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

    # без ключей Gemini — бесплатный резерв (по одному кандидату, ≤2 на сцену)
    if use_opencode:
        budget: dict[str, int] = {}
        todo: list[dict] = []
        for e in entries:
            if e.get("verified") or not str(e.get("file") or ""):
                continue
            if not Path(str(e["file"])).exists():
                continue
            k = _group_key(e)
            if budget.get(k, 0) >= _FALLBACK_SINGLES:
                continue
            budget[k] = budget.get(k, 0) + 1
            todo.append(e)
        for i in range(0, len(todo), max(1, batch_size)):
            verify_batch_opencode(todo[i:i + max(1, batch_size)], log=log, stats=stats)
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
